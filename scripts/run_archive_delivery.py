"""Run controlled archive-notice failures against two on-disk SQLite databases."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resumecert.archive_repair import checkpoint, create_repair_schema, resume  # noqa: E402
from resumecert.archive_transfer import FIELDS, connect, seed  # noqa: E402
from resumecert.effect_delivery import (  # noqa: E402
    acknowledge, create_inbox_schema, create_source_schema, deliver_once,
    lookup_receipt, pending_notice, prepare_notice, receive,
)

FAULTS = {
    "healthy": ("ok",),
    "lost_before_apply": ("drop", "ok", "ok"),
    "lost_ack": ("ack_lost", "ok", "ok"),
    "late_commit": ("hold", "ok", "ok"),
    "transport_redelivery": ("double",),
    "stale_lookup": ("ack_lost", "ok", "ok"),
    "outage": ("drop", "drop", "drop"),
    "two_lost_acks": ("ack_lost", "ack_lost", "ok"),
}
POLICIES = ("stop_on_unknown", "blind_retry", "lookup_then_retry", "stable_key")
BUDGETS = (0, 1, 2)


class Transport:
    def __init__(self, sink_path, fault, idempotent):
        self.sink_path = sink_path
        self.fault = fault
        self.idempotent = idempotent
        self.events = []
        self.held = []
        self.sends = 0
        self.lookups = 0
        self.invocations = 0

    def apply(self, notice, phase):
        self.invocations += 1
        with closing(connect(self.sink_path)) as sink:
            receipt = receive(sink, notice, idempotent=self.idempotent)
        self.events.append({"event": phase, "receipt_id": receipt.effect_id})
        return receipt

    def send(self, notice):
        instruction = FAULTS[self.fault][self.sends]
        self.sends += 1
        self.events.append({"event": "send", "mode": instruction})
        if instruction == "drop":
            raise TimeoutError("request did not reach the destination")
        if instruction == "hold":
            self.held.append(notice)
            raise TimeoutError("request still in flight")
        receipt = self.apply(notice, "destination_return")
        if instruction == "double":
            self.apply(notice, "transport_redelivery")
        if instruction == "ack_lost":
            raise TimeoutError("destination committed but acknowledgement was lost")
        return receipt

    def lookup(self, notice):
        self.lookups += 1
        with closing(connect(self.sink_path)) as sink:
            receipt = None if self.fault == "stale_lookup" else lookup_receipt(sink, notice)
        self.events.append({"event": "lookup", "receipt_id": receipt.effect_id if receipt else None})
        return receipt

    def settle(self):
        for notice in self.held:
            self.apply(notice, "late_destination_return")
        self.held.clear()


def setup(source_path, sink_path):
    with closing(connect(source_path)) as source:
        create_repair_schema(source)
        create_source_schema(source)
        seed(source, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
        prepare_notice(source, "study-example", 1, "Archive receipt ready")
        checkpoint(source, 1, 4)
        decision = resume(source, 1, "reuse_only", 0)
        if decision.status != "committed" or pending_notice(source, 1) is None:
            raise RuntimeError("fixture did not atomically authorize and enqueue the notice")
    with closing(connect(sink_path)) as sink:
        create_inbox_schema(sink)


def run_case(directory, fault, policy, retry_budget):
    source_path = directory / "source.sqlite3"
    sink_path = directory / "destination.sqlite3"
    setup(source_path, sink_path)
    transport = Transport(sink_path, fault, policy == "stable_key")
    for cycle in range(retry_budget + 1):
        if cycle and policy == "stop_on_unknown":
            break
        with closing(connect(source_path)) as source:
            if cycle and policy == "lookup_then_retry":
                receipt = transport.lookup(pending_notice(source, 1))
                if receipt is not None:
                    acknowledge(source, 1, receipt)
                    break
            try:
                deliver_once(source, 1, transport.send)
            except TimeoutError:
                transport.events.append({"event": "source_timeout"})
            else:
                break
    transport.settle()
    with closing(connect(source_path)) as source, closing(connect(sink_path)) as sink:
        status, confirmed_id, key, payload = source.execute("""
            SELECT status, effect_id, operation_key, payload FROM notice_outbox WHERE request_id = 1
        """).fetchone()
        effects = sink.execute("""
            SELECT effect_id FROM inbox_effects WHERE operation_key = ? AND payload = ? ORDER BY effect_id
        """, (key, payload)).fetchall()
        wrong_effects = sink.execute("SELECT count(*) FROM inbox_effects").fetchone()[0] - len(effects)
    if wrong_effects or (status == "sent" and (confirmed_id,) not in effects):
        raise RuntimeError("persisted effect or acknowledgement does not match the authorized notice")
    return {
        "fault": fault, "policy": policy, "retry_budget": retry_budget,
        "effect_count": len(effects), "status": status, "sends": transport.sends,
        "lookups": transport.lookups, "sink_invocations": transport.invocations,
        "events": transport.events,
    }


def run():
    rows = []
    for budget in BUDGETS:
        for fault in FAULTS:
            for policy in POLICIES:
                with TemporaryDirectory(prefix="resumecert-delivery-") as directory:
                    rows.append(run_case(Path(directory), fault, policy, budget))
    return rows


def summarize(rows):
    results = {}
    for budget in BUDGETS:
        results[str(budget)] = {}
        for policy in POLICIES:
            counts = Counter(exactly_one_effect=0, duplicate=0, missing=0, source_pending=0)
            for row in rows:
                if row["retry_budget"] != budget or row["policy"] != policy:
                    continue
                count = row["effect_count"]
                counts["exactly_one_effect" if count == 1 else "duplicate" if count > 1 else "missing"] += 1
                counts["source_pending"] += row["status"] == "pending"
            results[str(budget)][policy] = dict(counts)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, help="check the frozen outcomes")
    parser.add_argument("--traces", action="store_true", help="emit complete event traces")
    args = parser.parse_args()
    rows = run()
    summary = summarize(rows)
    if args.check is not None:
        compact = [{k: v for k, v in row.items() if k != "events"} for row in rows]
        expected = json.loads(args.check.read_bytes())
        if compact != expected["cases"] or summary != expected["by_retry_budget"]:
            for actual, reference in zip(compact, expected["cases"]):
                if actual != reference:
                    parser.exit(1, f"Delivery mismatch: {actual} != {reference}\n")
            parser.exit(1, "Delivery outcome counts differ from the reference\n")
        print(f"Verified {len(rows)} SQLite delivery cases and persisted effects")
        print(json.dumps(summary, sort_keys=True))
    else:
        print(json.dumps(rows if args.traces else summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
