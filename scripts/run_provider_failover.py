"""Evaluate failover policies with two independent persisted destination ledgers."""

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resumecert import provider_failover as routing, revocable_delivery as delivery  # noqa: E402
from resumecert.archive_repair import checkpoint, create_repair_schema, resume  # noqa: E402
from resumecert.archive_transfer import FIELDS, connect, seed  # noqa: E402
from resumecert.effect_delivery import acknowledge, create_source_schema, prepare_notice  # noqa: E402

SCENARIOS = {
    "primary_confirmed": "a_arrive a_confirm recover b_arrive",
    "primary_reply_lost": "a_arrive recover b_arrive",
    "primary_arrives_after_switch": "recover b_arrive a_arrive",
    "primary_unreachable_with_late_arrival": "offline recover b_arrive a_arrive",
    "primary_unreachable_without_effect": "offline recover b_arrive",
    "ordinary_fence_blocks_late_arrival": "fence recover a_arrive b_arrive",
    "ordinary_fence_then_regrant": "fence recover regrant b_arrive a_fresh_arrival",
    "permanent_retirement_blocks_late_arrival": "retire recover b_arrive a_arrive",
    "preexisting_effect_at_retirement": "a_arrive retire recover b_arrive",
    "retirement_response_lost": "retire restart recover b_arrive a_arrive",
    "retirement_hidden_then_recovered": "retire offline recover b_arrive online recover b_arrive",
    "restart_after_route_commit": "retire recover restart offline recover b_arrive a_arrive",
    "regrant_after_permanent_retirement": "retire recover regrant b_arrive a_fresh_arrival",
    "repeated_recovery_and_secondary_retry": "retire recover recover b_arrive b_arrive a_arrive",
    "secondary_commit_reply_lost": "retire recover b_silent restart offline recover b_confirm a_arrive",
    "ordinary_fence_upgraded_to_retirement": "fence retire recover b_arrive a_arrive",
}
POLICIES = ("timeout_switch", "lookup_switch", "generation_fence_switch", "permanent_retirement_switch")


def setup(source_path, primary_path, secondary_path):
    with closing(connect(source_path)) as source:
        create_repair_schema(source)
        create_source_schema(source)
        routing.create_routes(source)
        seed(source, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
        notice = prepare_notice(source, "failover-study", 1, "Synthetic handoff packet")
        checkpoint(source, 1, 4)
        if resume(source, 1, "reuse_only", 0).status != "committed":
            raise RuntimeError("source did not authorize the handoff")
        routing.enroll(source, 1, "primary", "secondary")
    for path, name in ((primary_path, "primary"), (secondary_path, "secondary")):
        with closing(connect(path)) as node:
            routing.create_provider(node, name)
    with closing(connect(source_path)) as source, closing(connect(primary_path)) as primary:
        return routing.begin_dispatch(source, 1, primary)


def baseline_recover(source, primary, notice, policy, online, route, first_epoch):
    if route != "primary":
        return route
    if policy == "timeout_switch":
        return "secondary"
    if not online:
        return "primary"
    found = delivery.reconcile(primary, notice)
    if found is not None:
        acknowledge(source, 1, found.receipt)
        return "primary_confirmed"
    if policy == "lookup_switch" or delivery.fence_status(primary, notice, first_epoch) is not None:
        return "secondary"
    return "primary"


def run_case(directory, name, policy):
    paths = [directory / f"{store}.sqlite3" for store in ("source", "primary", "secondary")]
    dispatch = setup(*paths)
    notice, first_epoch = dispatch.notice, dispatch.epoch
    strict = policy == "permanent_retirement_switch"
    route = "primary"
    online = True
    fresh_epoch = None
    follow_up = None
    confirmed_provider = None
    events = []
    for step in SCENARIOS[name].split():
        # All application connections are reconstructed for each event.
        with closing(connect(paths[0])) as source, closing(connect(paths[1])) as primary, closing(
                connect(paths[2])) as secondary:
            row = {"step": step}
            if step in ("offline", "online"):
                online = step == "online"
            elif step == "fence":
                row["epoch"] = delivery.revoke(primary, notice, first_epoch).closed_epoch
            elif step == "retire":
                fence = routing.retire(primary, notice, first_epoch)
                row["had_prior_effect"] = fence.effect is not None
            elif step == "regrant":
                try:
                    fresh_epoch = delivery.regrant(primary, notice, first_epoch + 1)
                except sqlite3.IntegrityError as error:
                    if "retired provider operation" not in str(error):
                        raise
                    row["status"] = "permanently_blocked"
                else:
                    row["epoch"] = fresh_epoch
            elif step in ("a_arrive", "a_fresh_arrival"):
                attempt = dispatch if step == "a_arrive" else (
                    routing.Dispatch("primary", notice, fresh_epoch) if fresh_epoch else None)
                if attempt is not None:
                    try:
                        row["effect_id"] = routing.apply_dispatch(primary, attempt).receipt.effect_id
                    except delivery.DispatchDenied:
                        row["status"] = "denied"
                else:
                    row["status"] = "no_dispatch"
            elif step == "a_confirm":
                if not routing.confirm(source, 1, primary):
                    raise RuntimeError("missing expected primary receipt")
                confirmed_provider = "primary"
                route = "primary_confirmed"
            elif step == "recover":
                if strict:
                    state = routing.recover_failover(source, 1, primary if online else None)
                    if state in ("primary_confirmed", "already_confirmed"):
                        confirmed_provider = source.execute(
                            "SELECT settled_provider FROM failover_routes").fetchone()[0]
                    route = "primary" if state == "unknown" else state
                    row["status"] = state
                else:
                    route = baseline_recover(source, primary, notice, policy, online, route, first_epoch)
                    if route == "primary_confirmed":
                        confirmed_provider = "primary"
                    row["status"] = route
            elif step in ("b_arrive", "b_silent", "b_confirm") and route == "secondary":
                if step != "b_confirm":
                    if follow_up is None:
                        follow_up = routing.begin_dispatch(source, 1, secondary) if strict else routing.Dispatch(
                            "secondary", notice, delivery.register(secondary, notice))
                    effect = routing.apply_dispatch(secondary, follow_up)
                    row["effect_id"] = effect.receipt.effect_id
                if step != "b_silent":
                    if strict:
                        if not routing.confirm(source, 1, secondary):
                            raise RuntimeError("missing expected alternative receipt")
                    else:
                        effect = delivery.reconcile(secondary, notice)
                        acknowledge(source, 1, effect.receipt)
                    confirmed_provider = "secondary"
            elif step not in ("restart", "b_arrive", "b_silent", "b_confirm"):
                raise RuntimeError(f"unknown fixture event: {step}")
            events.append(row)

    with closing(connect(paths[0])) as source, closing(connect(paths[1])) as primary, closing(
            connect(paths[2])) as secondary:
        ledgers = {routing.identity(node): node.execute("SELECT effect_id FROM handoff_effects").fetchall()
                   for node in (primary, secondary)}
        source_status, effect_id = source.execute("SELECT status, effect_id FROM notice_outbox").fetchone()
        if source_status != ("sent" if confirmed_provider is not None else "pending"):
            raise RuntimeError("source confirmation does not match the workflow")
        if confirmed_provider is not None and (effect_id,) not in ledgers[confirmed_provider]:
            raise RuntimeError("receipt is absent from the selected provider's ledger")
        if strict and source.execute("SELECT settled_provider FROM failover_routes").fetchone()[0] != confirmed_provider:
            raise RuntimeError("durable source route does not match the provider-qualified result")
    counts = [len(ledgers[provider]) for provider in ("primary", "secondary")]
    return {
        "scenario": name, "policy": policy, "effects_by_provider": counts,
        "confirmed_provider": confirmed_provider, "events": events,
    }


def result():
    runs = []
    totals = {policy: Counter() for policy in POLICIES}
    for name in SCENARIOS:
        for policy in POLICIES:
            with TemporaryDirectory(prefix="resumecert-provider-") as root:
                row = run_case(Path(root), name, policy)
            runs.append(row)
            counts = totals[policy]
            effects = sum(row["effects_by_provider"])
            confirmed = row["confirmed_provider"] is not None
            counts["duplicate"] += effects > 1
            counts["clean_confirmed"] += effects == 1 and confirmed
            counts["unresolved"] += not confirmed
            counts["secondary_used"] += row["effects_by_provider"][1]
            if policy == "permanent_retirement_switch" and effects > 1:
                raise RuntimeError("protected workflow produced cross-provider duplicates")
    return {"schema": "resumecert-provider-failover-v1", "summary": {p: dict(c) for p, c in totals.items()},
            "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Provider failover traces differ from recorded output\n")
    else:
        print(f"Reproduced {len(data['runs'])} two-provider SQLite runs")


if __name__ == "__main__":
    main()
