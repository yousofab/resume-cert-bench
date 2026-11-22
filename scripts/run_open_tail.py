"""Compare open-tail replay with a postcommit cache and a write-ahead claim."""

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

from resumecert import open_tail as tail, provider_failover as routing, retention, revocable_delivery as delivery  # noqa: E402
from resumecert.archive_repair import checkpoint, create_repair_schema, resume  # noqa: E402
from resumecert.archive_transfer import FIELDS, connect, seed  # noqa: E402
from resumecert.effect_delivery import DeliveryConflict, acknowledge, create_source_schema, prepare_notice  # noqa: E402

POLICIES = ("prefix_floor_only", "postcommit_cache", "write_ahead_claim")
SCENARIOS = {
    "retained_retry": "apply retry recover",
    "restore_completed": "snapshot apply restore retry recover",
    "repeated_restore_completed": "snapshot apply restore retry restore retry recover",
    "unsealed_retained": "unsealed retry recover",
    "unsealed_restored": "snapshot unsealed restore retry recover",
    "repeated_unsealed_restored": "snapshot unsealed restore retry restore retry recover",
    "failed_effect_retained": "fail_effect retry recover",
    "failed_effect_restored": "snapshot fail_effect restore retry recover",
    "unavailable_guard_then_resume": "offline retry online retry recover",
    "unavailable_guard_after_effect_restore": "snapshot unsealed offline restore retry online retry recover",
    "seal_failure_retained": "fail_seal retry recover",
    "seal_failure_restored": "snapshot fail_seal restore retry recover",
    "pristine_restore": "snapshot restore retry recover",
    "unknown_does_not_block_next": "snapshot unsealed restore retry next recover",
    "historical_receipt_after_id_reuse": "snapshot apply restore next retry recover",
    "raw_replay_completed": "snapshot apply restore raw_arrival recover",
    "raw_replay_unsealed": "snapshot unsealed restore raw_arrival recover",
    "compact_completed": "apply compact retry recover",
    "compact_unknown": "unsealed compact retry recover",
    "revoked_with_retained_receipt": "unsealed revoke recover retry",
    "guard_rollback_boundary_control": "snapshot apply restore_all retry recover",
}
STREAM = "open-tail-study"


def setup(directory):
    messages = []
    with closing(connect(directory / "source.sqlite3")) as source:
        create_repair_schema(source)
        create_source_schema(source)
        for sequence in (1, 2):
            seed(source, sequence, zip(FIELDS, (1, 1, 0, 0, 0)))
            notice = prepare_notice(source, STREAM, sequence, "Synthetic open-tail packet")
            checkpoint(source, sequence, 4)
            if resume(source, sequence, "reuse_only", 0).status != "committed":
                raise RuntimeError("source did not authorize notice")
            messages.append(retention.StreamNotice(STREAM, sequence, notice))
    with closing(connect(directory / "provider.sqlite3")) as provider, closing(
            connect(directory / "guard.sqlite3")) as guard:
        tail.create_guard(guard)
        guard.execute("""
            CREATE TABLE fixture_postcommit_cache (
                operation_key TEXT PRIMARY KEY, payload_digest TEXT NOT NULL,
                effect_id INTEGER NOT NULL, accepted_epoch INTEGER NOT NULL
            )
        """)
        tail.create_provider(provider, "primary")
        retention.enroll_stream(provider, guard, STREAM)
        dispatches = [retention.register(provider, guard, item) for item in messages]
    with closing(connect(directory / "oracle.sqlite3")) as oracle:
        oracle.execute("""
            CREATE TABLE observations (
                event_id INTEGER PRIMARY KEY, operation_key TEXT NOT NULL,
                payload_digest TEXT NOT NULL, historical_receipt_id INTEGER NOT NULL
            )
        """)
    return messages, dispatches


def configure(provider, guard, policy):
    tail.configure(provider, guard)
    if policy != "write_ahead_claim":
        # Explicit ablation: effect creation no longer requires a durable claim.
        provider.create_function("claim_new_effect", 5, lambda *args: 1)


def cache_lookup(guard, message):
    with tail._fresh_guard(guard) as current:
        row = current.execute("""
            SELECT payload_digest, effect_id, accepted_epoch FROM fixture_postcommit_cache
            WHERE operation_key = ?
        """, (message.notice.operation_key,)).fetchone()
    if row is not None and row[0] != message.notice.payload_digest:
        raise DeliveryConflict("postcommit cache binds another payload")
    return delivery._recorded(message.notice, row[1], row[2]) if row else None


def cache_store(guard, effect):
    with tail._fresh_guard(guard, write=True) as current:
        current.execute("INSERT INTO fixture_postcommit_cache VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        (effect.receipt.operation_key, effect.receipt.payload_digest,
                         effect.receipt.effect_id, effect.accepted_epoch))


def baseline_apply(provider, guard, message, dispatch, policy):
    retention._check_open(provider, guard, message)
    if policy == "postcommit_cache":
        old = cache_lookup(guard, message)
        if old is not None:
            return tail.Outcome("recorded", "primary", old)
    effect = retention.apply(provider, guard, message, dispatch)
    if policy == "postcommit_cache":
        cache_store(guard, effect)
    return tail.Outcome("recorded", "primary", effect)


def recover(provider, guard, message, policy):
    if policy == "write_ahead_claim":
        return tail.recover(provider, guard, message)
    if message.sequence <= retention.closed_through(provider, guard, message.stream_id):
        return tail.Outcome("forgotten", "primary")
    if policy == "postcommit_cache":
        old = cache_lookup(guard, message)
        if old is not None:
            return tail.Outcome("recorded", "primary", old)
    found = retention.lookup(provider, guard, message)
    if found.effect is None:
        return tail.Outcome("unattempted", "primary")
    if policy == "postcommit_cache":
        cache_store(guard, found.effect)
    return tail.Outcome("recorded", "primary", found.effect)


def backup(directory):
    for name in ("provider", "guard"):
        with closing(connect(directory / f"{name}.sqlite3")) as live, closing(
                connect(directory / f"{name}.backup")) as saved:
            live.backup(saved)


def run_case(directory, name, policy):
    messages, dispatches = setup(directory)
    online = True
    events = []
    final = "unattempted"
    for step in SCENARIOS[name].split():
        if step == "snapshot":
            backup(directory)
        if step in ("restore", "restore_all"):
            shutil.copyfile(directory / "provider.backup", directory / "provider.sqlite3")
            if step == "restore_all":
                shutil.copyfile(directory / "guard.backup", directory / "guard.sqlite3")
        if step in ("online", "offline"):
            online = step == "online"
        event = {"step": step}
        with closing(connect(directory / "provider.sqlite3")) as provider, closing(
                connect(directory / "guard.sqlite3")) as actual_guard, closing(
                connect(directory / "oracle.sqlite3")) as oracle, closing(
                connect(directory / "source.sqlite3")) as source:
            guard = actual_guard if online else None
            configure(provider, guard, policy)
            before = {row[0] for row in provider.execute("SELECT operation_key FROM handoff_effects")}
            try:
                if step in ("apply", "retry", "next", "fail_effect", "fail_seal"):
                    index = int(step == "next")
                    item, dispatch = messages[index], dispatches[index]
                    if step == "fail_effect":
                        provider.executescript("""
                            CREATE TRIGGER fixture_fail_effect AFTER INSERT ON handoff_effects
                            BEGIN SELECT RAISE(ABORT, 'injected target write failure'); END;
                        """)
                    if step == "fail_seal":
                        actual_guard.executescript("""
                            CREATE TRIGGER fixture_fail_seal BEFORE UPDATE ON open_tail_claims
                            BEGIN SELECT RAISE(ABORT, 'injected seal failure'); END;
                            CREATE TRIGGER fixture_fail_cache BEFORE INSERT ON fixture_postcommit_cache
                            BEGIN SELECT RAISE(ABORT, 'injected seal failure'); END;
                        """)
                    try:
                        outcome = tail.apply(provider, guard, item, dispatch) if policy == "write_ahead_claim" else (
                            baseline_apply(provider, guard, item, dispatch, policy))
                        event["outcome"] = outcome.status
                    finally:
                        provider.execute("DROP TRIGGER IF EXISTS fixture_fail_effect")
                        actual_guard.execute("DROP TRIGGER IF EXISTS fixture_fail_seal")
                        actual_guard.execute("DROP TRIGGER IF EXISTS fixture_fail_cache")
                elif step in ("unsealed", "raw_arrival"):
                    effect = retention.apply(provider, guard, messages[0], dispatches[0])
                    event["outcome"] = "reply_unsealed"
                    event["receipt_id"] = effect.receipt.effect_id
                elif step == "recover":
                    outcome = recover(provider, guard, messages[0], policy)
                    event["outcome"] = final = outcome.status
                    if outcome.effect is not None:
                        receipt = outcome.effect.receipt
                        acknowledge(source, 1, receipt)
                        event["receipt_id"] = receipt.effect_id
                        event["receipt_operation_key"] = receipt.operation_key
                elif step == "compact":
                    event["deleted_rows"] = tail.close_prefix(provider, guard, STREAM, 1)
                    actual_guard.execute("DELETE FROM fixture_postcommit_cache WHERE operation_key = ?",
                                         (messages[0].notice.operation_key,))
                elif step == "revoke":
                    delivery.revoke(provider, messages[0].notice, dispatches[0].epoch)
            except retention.GuardUnavailable:
                event["outcome"] = "guard_unavailable"
            except retention.PrefixClosed:
                event["outcome"] = "closed"
            except delivery.DispatchDenied:
                event["outcome"] = "dispatch_denied"
            except sqlite3.IntegrityError as error:
                if str(error) not in ("injected target write failure", "injected seal failure", "open-tail claim already exists"):
                    raise
                event["outcome"] = str(error)
            event["created"] = []
            # Persist newly committed target rows even if positive receipt sealing failed.
            for key, payload_digest, effect_id in provider.execute(
                    "SELECT operation_key, payload_digest, effect_id FROM handoff_effects ORDER BY effect_id"):
                if key not in before:
                    oracle.execute("INSERT INTO observations VALUES (NULL, ?, ?, ?)", (key, payload_digest, effect_id))
                    event["created"].append({"operation_key": key, "receipt_id": effect_id})
            event["oracle_effects"] = oracle.execute("SELECT count(*) FROM observations").fetchone()[0]
            event["claim_rows"] = actual_guard.execute("SELECT count(*) FROM open_tail_claims").fetchone()[0]
            events.append(event)
    with closing(connect(directory / "oracle.sqlite3")) as oracle, closing(
            connect(directory / "source.sqlite3")) as source:
        effects = [oracle.execute("SELECT count(*) FROM observations WHERE operation_key = ?",
                                  (m.notice.operation_key,)).fetchone()[0] for m in messages]
        source_status = source.execute("SELECT status FROM notice_outbox WHERE request_id = 1").fetchone()[0]
    row = {"scenario": name, "policy": policy, "events": events, "effects_by_operation": effects,
            "operation_keys": [m.notice.operation_key for m in messages],
            "final_recovery": final, "source_status": source_status,
            "boundary_control": name == "guard_rollback_boundary_control"}
    if policy == "write_ahead_claim" and name in ("failed_effect_restored", "unsealed_restored"):
        # Complete logical view used by recovery, excluding the evaluator's private history.
        view = {}
        tables = {
            "provider": ("provider_identity", "retention_streams", "retention_bindings",
                         "handoff_authority", "handoff_effects", "handoff_fences", "permanent_retirements"),
            "guard": ("retention_anchors", "open_tail_claims"),
            "source": ("notice_outbox",),
        }
        for store, names in tables.items():
            with closing(connect(directory / f"{store}.sqlite3")) as connection:
                for table in names:
                    view[f"{store}.{table}"] = [list(record) for record in connection.execute(
                        f"SELECT * FROM {table} ORDER BY 1, 2")]
        row["recovery_view"] = view
    return row


def result():
    totals = {p: Counter(runs=0, duplicate_runs=0, supported_duplicate_runs=0, known_single=0,
                          unknown_with_effect=0, unknown_without_effect=0, forgotten=0) for p in POLICIES}
    runs = []
    with TemporaryDirectory() as root:
        for i, (name, policy) in enumerate((s, p) for s in SCENARIOS for p in POLICIES):
            directory = Path(root) / str(i)
            directory.mkdir()
            row = run_case(directory, name, policy)
            runs.append(row)
            first = row["effects_by_operation"][0]
            state = row["final_recovery"]
            totals[policy].update(runs=1, duplicate_runs=int(first > 1),
                                  supported_duplicate_runs=int(first > 1 and not row["boundary_control"]),
                                  known_single=int(first == 1 and state == "recorded"),
                                  unknown_with_effect=int(first == 1 and state == "unknown"),
                                  unknown_without_effect=int(first == 0 and state == "unknown"),
                                  forgotten=int(state == "forgotten"))
            if policy == "write_ahead_claim" and not row["boundary_control"] and first > 1:
                raise RuntimeError("independent open-tail claim allowed a duplicate")
    return {"schema": "resumecert-open-tail-v1", "summary": {p: dict(c) for p, c in totals.items()}, "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Open-tail recovery differs from recorded output\n")
    else:
        print(f"Reproduced {len(data['runs'])} open-tail recovery runs")


if __name__ == "__main__":
    main()
