"""Evaluate queued archive handoff against a destination-owned revocation fence."""

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resumecert import revocable_delivery as guarded  # noqa: E402
from resumecert.archive_repair import checkpoint, create_repair_schema, resume  # noqa: E402
from resumecert.archive_transfer import FIELDS, connect, seed  # noqa: E402
from resumecert.effect_delivery import (  # noqa: E402
    acknowledge, create_inbox_schema, create_source_schema, lookup_receipt, prepare_notice, receive,
)

SCHEDULES = {
    "healthy": "queue arrive_ack",
    "cut_before_queue": "request fence queue arrive recover",
    "sender_check_race": "queue request fence arrive recover",
    "pre_cut_commit_ack_lost": "queue arrive request fence recover",
    "cut_propagation_window": "queue request arrive fence recover",
    "fence_control_not_delivered": "queue request recover arrive recover",
    "no_dispatch_during_propagation": "request queue arrive recover",
    "regrant_old_arrives_first": "queue request fence renew queue arrive arrive_ack recover",
    "regrant_new_arrives_first": "queue request fence renew queue arrive_newest_ack arrive recover",
    "closed_empty": "request fence recover",
    "active_ack_lost": "queue arrive recover",
    "redelivery_after_fence": "queue arrive request fence replay_old arrive recover",
    "old_cut_replay_after_regrant": "queue request fence renew replay_fence queue arrive arrive_ack recover",
    "reconcile_before_late_arrival": "queue request fence recover arrive recover",
}
POLICIES = ("stable_key", "sender_recheck", "fence_coupled_lookup", "fence_separate_lookup")


def setup(source_path, sink_path, protected):
    with closing(connect(source_path)) as source:
        create_repair_schema(source)
        create_source_schema(source)
        seed(source, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
        notice = prepare_notice(source, "revocation-study", 1, "Synthetic archive handoff")
        checkpoint(source, 1, 4)
        if resume(source, 1, "reuse_only", 0).status != "committed":
            raise RuntimeError("source did not authorize the handoff fixture")
    with closing(connect(sink_path)) as sink:
        guarded.create_destination(sink)
        if not protected:
            create_inbox_schema(sink)
        guarded.register(sink, notice)
    return notice


def run_case(directory, case, policy):
    protected = policy in ("fence_coupled_lookup", "fence_separate_lookup")
    source_path = directory / "source.sqlite3"
    sink_path = directory / "destination.sqlite3"
    notice = setup(source_path, sink_path, protected)
    queue = []
    events = []
    cut_epoch = None
    last_fence = None
    original_epoch = None
    settlement = "unknown"

    for step in SCHEDULES[case].split():
        with closing(connect(source_path)) as source, closing(connect(sink_path)) as sink:
            epoch, active = guarded.authority(sink, notice)
            event = {"step": step, "destination_epoch": epoch, "destination_active": active}
            if step == "queue":
                allowed = policy == "stable_key" or (cut_epoch is None and active)
                if allowed:
                    queue.append(epoch)
                    if original_epoch is None:
                        original_epoch = epoch
                event["status"] = "queued" if allowed else "sender_blocked"
            elif step == "request":
                if not active or cut_epoch is not None:
                    raise RuntimeError("invalid cancellation fixture")
                cut_epoch = epoch
                event["status"] = "requested"
            elif step in ("fence", "replay_fence"):
                target = cut_epoch if step == "fence" else last_fence.retired_epoch
                last_fence = guarded.revoke(sink, notice, target)
                if protected:
                    event["fence"] = asdict(last_fence)
                event["status"] = "applied" if step == "fence" else "replayed"
            elif step == "renew":
                event["new_epoch"] = guarded.regrant(sink, notice, last_fence.closed_epoch)
                cut_epoch = None
                if settlement == "fenced_no_effect":
                    settlement = "unknown"
            elif step == "replay_old":
                queue.append(original_epoch)
            elif step.startswith("arrive"):
                if not queue:
                    event["status"] = "no_delivery"
                else:
                    carried = queue.pop(-1 if step == "arrive_newest_ack" else 0)
                    previous = (guarded.reconcile(sink, notice) if protected
                                else lookup_receipt(sink, notice))
                    event["carried_epoch"] = carried
                    try:
                        receipt = (guarded.apply(sink, notice, carried).receipt if protected
                                   else receive(sink, notice))
                    except guarded.DispatchDenied:
                        event["status"] = "destination_denied"
                    else:
                        event["status"] = "applied" if previous is None else "same_receipt"
                        event["receipt_id"] = receipt.effect_id
                        if previous is None:
                            event["invalid_commit"] = not active or epoch != carried
                            event["during_propagation"] = (cut_epoch == carried and active
                                                           and epoch == carried)
                        if step.endswith("_ack"):
                            acknowledge(source, 1, receipt)
                            settlement = "effect_confirmed"
            elif step == "recover":
                if policy == "fence_coupled_lookup" and cut_epoch is not None:
                    event["status"] = "lookup_blocked"
                else:
                    record = guarded.reconcile(sink, notice) if protected else None
                    receipt = (record.receipt if record else None) if protected else lookup_receipt(sink, notice)
                    if receipt is not None:
                        acknowledge(source, 1, receipt)
                        settlement = "effect_confirmed"
                    elif policy == "fence_separate_lookup" and cut_epoch is not None:
                        fence = guarded.fence_status(sink, notice, cut_epoch)
                        if fence is not None and fence.effect is None:
                            settlement = "fenced_no_effect"
                    event["status"] = settlement
            else:
                raise RuntimeError(f"unknown study event: {step}")
            events.append(event)

    if queue:
        raise RuntimeError("study ended with undelivered queued attempts")
    with closing(connect(source_path)) as source, closing(connect(sink_path)) as sink:
        recorded = (guarded.reconcile(sink, notice) if protected else lookup_receipt(sink, notice))
        source_status, effect_id = source.execute("SELECT status, effect_id FROM notice_outbox").fetchone()
        table = "handoff_effects" if protected else "inbox_effects"
        effect_count = sink.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if (effect_count != int(recorded is not None)
                or source_status == "sent" and
                (recorded is None or effect_id != (recorded.receipt if protected else recorded).effect_id)):
            raise RuntimeError("source and destination receipts disagree")
    return {
        "scenario": case, "policy": policy, "effect_count": effect_count,
        "invalid_effects": sum(e.get("invalid_commit", False) for e in events),
        "propagation_effects": sum(e.get("during_propagation", False) for e in events),
        "denied_arrivals": sum(e.get("status") == "destination_denied" for e in events),
        "source_status": source_status, "settlement": settlement, "events": events,
    }


def result():
    rows = []
    for case in SCHEDULES:
        for policy in POLICIES:
            with TemporaryDirectory(prefix="resumecert-fence-") as directory:
                rows.append(run_case(Path(directory), case, policy))
    totals = {}
    for policy in POLICIES:
        counts = Counter()
        for row in (r for r in rows if r["policy"] == policy):
            counts["runs"] += 1
            counts["invalid_effects"] += row["invalid_effects"]
            counts["propagation_effects"] += row["propagation_effects"]
            counts["confirmed"] += row["settlement"] == "effect_confirmed"
            counts["fenced_no_effect"] += row["settlement"] == "fenced_no_effect"
            counts["unknown"] += row["settlement"] == "unknown"
            counts["recorded_but_unconfirmed"] += row["effect_count"] and row["source_status"] != "sent"
        totals[policy] = dict(counts)
    return {"schema": "resumecert-destination-revocation-v1", "summary": totals, "runs": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Destination revocation traces differ from recorded output\n")
    else:
        print(f"Reproduced {len(data['runs'])} protected and baseline SQLite runs")


if __name__ == "__main__":
    main()
