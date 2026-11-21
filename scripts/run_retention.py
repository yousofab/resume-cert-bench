"""Compare metadata deletion, co-restored closure floors, and independent floors."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resumecert import provider_failover as routing, retention, revocable_delivery as delivery  # noqa: E402
from resumecert.archive_repair import checkpoint, create_repair_schema, resume  # noqa: E402
from resumecert.archive_transfer import FIELDS, connect, seed, write_transaction  # noqa: E402
from resumecert.effect_delivery import DeliveryConflict, create_source_schema, prepare_notice  # noqa: E402

POLICIES = ("delete_only", "co_restored_floor", "independent_floor")
SCENARIOS = {
    "retained_retry": "a_apply a_retry lookup",
    "retained_receipt_recovery": "a_apply a_confirm a_retry lookup",
    "replay_after_cleanup": "a_apply compact a_retry lookup",
    "unseen_sequence_after_close": "compact a_retry lookup",
    "next_sequence_after_close": "a_apply compact next_apply a_retry lookup",
    "repeated_cleanup_and_retry": "a_apply compact compact a_retry a_retry lookup",
    "recorded_outcome_forgotten": "a_apply compact lookup",
    "empty_outcome_forgotten": "compact lookup",
    "provider_restore_after_close": "snapshot a_apply compact restore a_retry lookup",
    "repeated_provider_restore_after_close": "snapshot a_apply compact restore a_retry restore a_retry lookup",
    "source_restore_after_close": "snapshot a_apply a_confirm compact source_restore a_retry lookup",
    "retirement_restore_after_failover": "snapshot failover compact restore a_retry lookup",
    "crash_after_anchor_before_cleanup": "snapshot a_apply close_fail restore a_retry lookup",
    "guard_unavailable_after_cleanup": "a_apply compact guard_offline a_retry lookup",
    "changed_sequence_rejected": "a_apply compact rebind lookup",
    "independent_stream_open": "a_apply compact other_apply a_retry lookup",
    "unclosed_provider_restore_control": "snapshot a_apply restore a_retry lookup",
    "unclosed_source_restore_control": "snapshot a_apply a_confirm source_restore a_retry lookup",
}
TABLES = ("handoff_fences", "permanent_retirements", "handoff_effects", "handoff_authority",
          "retention_bindings")
STREAM = "retention-study"


def _authorized(source, stream, sequence):
    seed(source, sequence, zip(FIELDS, (1, 1, 0, 0, 0)))
    notice = prepare_notice(source, stream, sequence, "Synthetic retained handoff")
    checkpoint(source, sequence, 4)
    if resume(source, sequence, "reuse_only", 0).status != "committed":
        raise RuntimeError("source authorization failed")
    routing.enroll(source, sequence, "primary", "secondary")
    return retention.StreamNotice(stream, sequence, notice)


def setup(directory):
    with closing(connect(directory / "guard.sqlite3")) as guard:
        retention.create_guard(guard)
        for name in ("primary", "secondary"):
            with closing(connect(directory / f"{name}.sqlite3")) as provider:
                retention.create_provider(provider, name)
                retention.enroll_stream(provider, guard, STREAM)
                retention.enroll_stream(provider, guard, "other-source")
    with closing(connect(directory / "source.sqlite3")) as source:
        create_repair_schema(source)
        create_source_schema(source)
        routing.create_routes(source)
        messages = [_authorized(source, STREAM, i) for i in (1, 2)]
    with closing(connect(directory / "oracle.sqlite3")) as oracle:
        oracle.execute("""
            CREATE TABLE observations (
                observation_id INTEGER PRIMARY KEY, provider_id TEXT NOT NULL,
                operation_key TEXT NOT NULL, payload_digest TEXT NOT NULL
            )
        """)
    return messages


def observe_apply(provider, guard, oracle, message, dispatch):
    # The evaluator observes committed INSERTs independently of returned receipt IDs.
    # Its separate database survives replacement of a provider recovery image.
    before = provider.execute("SELECT 1 FROM handoff_effects WHERE operation_key = ?",
                              (message.notice.operation_key,)).fetchone()
    effect = retention.apply(provider, guard, message, dispatch)
    after = provider.execute("SELECT payload_digest FROM handoff_effects WHERE operation_key = ?",
                             (message.notice.operation_key,)).fetchone()
    created = before is None and after is not None
    if created:
        oracle.execute("INSERT INTO observations VALUES (NULL, ?, ?, ?)",
                       (routing.identity(provider), message.notice.operation_key, after[0]))
    return {"created": created, "receipt_id": effect.receipt.effect_id,
            "operation_key": message.notice.operation_key, "provider": routing.identity(provider)}


def erase_only(provider, message):
    with write_transaction(provider):
        deleted = sum(provider.execute(f"DELETE FROM {table} WHERE operation_key = ?",
                                       (message.notice.operation_key,)).rowcount for table in TABLES)
    return deleted


def backup(path):
    with closing(connect(path)) as source, closing(connect(path.with_suffix(".backup"))) as target:
        source.backup(target)


def run_case(directory, name, policy):
    first, next_message = setup(directory)
    online = True
    events = []
    for step in SCENARIOS[name].split():
        if step == "snapshot":
            for store in ("primary", "guard", "source"):
                backup(directory / f"{store}.sqlite3")
        elif step == "restore":
            shutil.copyfile(directory / "primary.backup", directory / "primary.sqlite3")
            if policy == "co_restored_floor":
                shutil.copyfile(directory / "guard.backup", directory / "guard.sqlite3")
        elif step == "source_restore":
            shutil.copyfile(directory / "source.backup", directory / "source.sqlite3")
        elif step == "guard_offline":
            online = False
        event = {"step": step}
        with closing(connect(directory / "source.sqlite3")) as source, closing(
                connect(directory / "primary.sqlite3")) as a, closing(
                connect(directory / "secondary.sqlite3")) as b, closing(
                connect(directory / "guard.sqlite3")) as actual_guard, closing(
                connect(directory / "oracle.sqlite3")) as oracle:
            guard = actual_guard if online else None
            retention.configure(a, guard)
            retention.configure(b, guard)
            try:
                if step in ("a_apply", "a_retry", "next_apply"):
                    message = next_message if step == "next_apply" else first
                    dispatch = retention.register(a, guard, message)
                    event.update(observe_apply(a, guard, oracle, message, dispatch))
                elif step == "a_confirm":
                    if not routing.confirm(source, 1, a):
                        raise RuntimeError("missing effect for confirmation")
                elif step in ("compact", "close_fail"):
                    if policy == "delete_only":
                        event["deleted_rows"] = erase_only(a, first)
                    else:
                        if step == "close_fail":
                            a.executescript("""
                                CREATE TRIGGER fixture_fail_cleanup BEFORE UPDATE ON retention_streams
                                BEGIN SELECT RAISE(ABORT, 'injected cleanup failure'); END;
                            """)
                        try:
                            event["deleted_rows"] = retention.close_prefix(a, guard, STREAM, 1)
                        except sqlite3.IntegrityError as error:
                            if str(error) != "injected cleanup failure":
                                raise
                            event["status"] = "provider_rolled_back_after_anchor_commit"
                        finally:
                            a.execute("DROP TRIGGER IF EXISTS fixture_fail_cleanup")
                elif step == "lookup":
                    result = retention.lookup(a, guard, first)
                    event["resolution"] = result.status
                elif step == "failover":
                    old = retention.register(a, guard, first)
                    routing.retire(a, first.notice, old.epoch)
                    if routing.recover_failover(source, 1, a) != "secondary":
                        raise RuntimeError("expected durable secondary route")
                    retention.bind(b, guard, first)
                    new = routing.begin_dispatch(source, 1, b)
                    event.update(observe_apply(b, guard, oracle, first, new))
                    routing.confirm(source, 1, b)
                elif step == "rebind":
                    retention.StreamNotice(STREAM, 2, first.notice)
                    raise RuntimeError("an old operation was rebound to a new sequence")
                elif step == "other_apply":
                    # A separately authorized source with its own namespace and sequence.
                    message = _authorized(source, "other-source", 3)
                    dispatch = retention.register(a, guard, message)
                    event.update(observe_apply(a, guard, oracle, message, dispatch))
            except retention.PrefixClosed:
                event["status"] = "closed"
            except retention.GuardUnavailable:
                event["status"] = "guard_unavailable"
            except DeliveryConflict:
                if step != "rebind":
                    raise
                event["status"] = "binding_rejected"
            event["retained_operation_rows"] = sum(a.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                                     for table in TABLES)
            event["oracle_effects"] = oracle.execute("SELECT count(*) FROM observations").fetchone()[0]
        events.append(event)
    with closing(connect(directory / "oracle.sqlite3")) as oracle:
        count = oracle.execute("SELECT count(*) FROM observations WHERE operation_key = ?",
                               (first.notice.operation_key,)).fetchone()[0]
        observed = [list(row) for row in oracle.execute(
            "SELECT provider_id, operation_key FROM observations ORDER BY observation_id")]
    return {"scenario": name, "policy": policy, "first_operation_effects": count,
            "duplicate": count > 1, "events": events, "observed_effects": observed,
            "closed_prefix_case": any(step in SCENARIOS[name].split() for step in ("compact", "close_fail"))}


def result():
    runs = []
    totals = {policy: Counter(runs=0, duplicate_runs=0, closed_prefix_duplicate_runs=0,
                              forgotten_lookups=0, blocked_attempts=0) for policy in POLICIES}
    with TemporaryDirectory() as root:
        for index, (name, policy) in enumerate((s, p) for s in SCENARIOS for p in POLICIES):
            directory = Path(root) / str(index)
            directory.mkdir()
            row = run_case(directory, name, policy)
            runs.append(row)
            totals[policy].update(runs=1, duplicate_runs=int(row["duplicate"]),
                                  closed_prefix_duplicate_runs=int(row["duplicate"] and row["closed_prefix_case"]),
                                  forgotten_lookups=sum(e.get("resolution") == "forgotten" for e in row["events"]),
                                  blocked_attempts=sum(e.get("status") == "closed" for e in row["events"]))
            if policy == "independent_floor" and row["closed_prefix_case"] and row["duplicate"]:
                raise RuntimeError("independent closure permitted a duplicate in a closed prefix")
    return {"schema": "resumecert-retention-v1", "summary": {p: dict(c) for p, c in totals.items()},
            "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Retention traces differ from recorded output\n")
    else:
        print(f"Reproduced {len(data['runs'])} retention and restore runs")


if __name__ == "__main__":
    main()
