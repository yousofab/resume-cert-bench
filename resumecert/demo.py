"""Engineering fixtures; this module does not run the matched study."""

from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
from random import Random
from uuid import UUID

from .contract import ActionContract, Query
from .inference import infer
from .records import Decision
from .simulator import Simulation
from .verifier import verify

FIELDS = ("route", "normal_approval", "exception_approval")

ROUTED_APPROVAL = ActionContract(
    "submit_request", "1", tuple((f, (0, 1)) for f in FIELDS),
    tuple(Query(f, (f,)) for f in FIELDS),
    ("any",
     ("all", ("eq", ("field", "route"), ("literal", 0)),
      ("eq", ("field", "normal_approval"), ("literal", 1))),
     ("all", ("eq", ("field", "route"), ("literal", 1)),
      ("eq", ("field", "exception_approval"), ("literal", 1)))),
)

DOCUMENT_RELEASE = ActionContract(
    "release_document", "1",
    (("version", (1, 2)), ("approved_version", (1, 2))),
    (Query("version", ("version",)), Query("approval", ("approved_version",)),
     Query("version_and_approval", ("version", "approved_version"), 2)),
    ("eq", ("field", "version"), ("field", "approved_version")),
)


def _policy(context, read, mode):
    # The first observation picks the second query only in adaptive mode.
    events = ()
    order = list(FIELDS)
    certificate = infer(context)
    while order and certificate.decision is Decision.DEFER and certificate.spent < context.budget:
        events += (read(order.pop(0)),)
        if mode == "adaptive" and len(events) == 1:
            route = dict(events[0].values)["route"]
            order = [FIELDS[1 + route]]
        certificate = infer(context, events)
    return certificate


def _check(audit, certificate):
    checked = verify(audit, certificate)
    if not checked.accepted:
        raise RuntimeError(f"reference certificate failed independent verification: {checked.reason}")
    return checked


def _fixture_id(rng):
    return UUID(int=rng.getrandbits(128), version=4).hex


def witness_rows():
    worlds = tuple(ROUTED_APPROVAL.worlds())
    rows = []
    rng = Random(831758)
    snapshots = tuple(_fixture_id(rng) for _ in worlds)
    for budget in range(4):
        row = {"unit_read_budget": budget}
        for mode in ("fixed", "adaptive"):
            counts = Counter()
            total_cost = 0
            for live, snapshot_id in zip(worlds, snapshots):
                sim = Simulation(ROUTED_APPROVAL, live, snapshot_id=snapshot_id,
                                 run_id=_fixture_id(rng), budget=budget)
                certificate = _policy(sim.context, sim.read, mode)
                _check(sim.audit(), certificate)
                counts[certificate.decision] += 1
                total_cost += certificate.spent
            row[mode] = {
                "allow": counts[Decision.ALLOW], "block": counts[Decision.BLOCK],
                "defer": counts[Decision.DEFER], "total_cost": total_cost,
            }
        rows.append(row)
    return rows


def _document_fixtures():
    cases = {}
    rng = Random(416738)
    for name, version, budget in (("matching_version", 1, 2),
                                  ("stale_approval", 2, 2),
                                  ("insufficient_budget", 2, 1)):
        sim = Simulation(DOCUMENT_RELEASE, {"version": version, "approved_version": 1},
                         snapshot_id=_fixture_id(rng), run_id=_fixture_id(rng), budget=budget,
                         checkpoint="Version 1 was approved before interruption.",
                         resume_message="Resume the pending release.")
        events = (sim.read("version_and_approval" if budget == 2 else "version"),)
        certificate = infer(sim.context, events)
        checked = _check(sim.audit(), certificate)
        cases[name] = {"decision": certificate.decision, "spent": certificate.spent,
                       "compatible_worlds": certificate.compatible_worlds,
                       "verified": checked.accepted}
    sim = Simulation(DOCUMENT_RELEASE, {"version": 2, "approved_version": 1},
                     snapshot_id=_fixture_id(rng), run_id=_fixture_id(rng), budget=2)
    first, second = sim.read("version"), sim.read("version")
    conflicting = replace(second, values=(("version", 1),))
    certificate = infer(sim.context, (first, conflicting))
    against_trusted = verify(sim.audit(), certificate)
    against_corrupted = verify(replace(sim.audit(), reads=(first, conflicting)), certificate)
    if certificate.decision is not Decision.INCONSISTENT or (
        against_trusted.accepted or against_corrupted.accepted
    ):
        raise RuntimeError("contradictory evidence fixture failed closed-loop check")
    cases["contradictory_observations"] = {
        "decision": certificate.decision, "spent": certificate.spent,
        "compatible_worlds": certificate.compatible_worlds,
        "against_trusted_audit": asdict(against_trusted),
        "against_corrupted_audit": asdict(against_corrupted),
    }
    return cases


def report():
    rows = witness_rows()
    proof = Path(__file__).resolve().parents[1] / "proof" / "witness_result.json"
    baseline = json.loads(proof.read_text(encoding="utf-8"))
    observed = [(r["unit_read_budget"], r["fixed"]["allow"] + r["fixed"]["block"],
                 r["adaptive"]["allow"] + r["adaptive"]["block"]) for r in rows]
    expected = [(r["unit_read_budget"], r["best_fixed_certified"],
                 r["best_adaptive_certified"]) for r in baseline["table"]]
    if baseline["possible_worlds"] != len(tuple(ROUTED_APPROVAL.worlds())) or observed != expected:
        raise RuntimeError("read interface did not reproduce standalone witness counts")
    return {
        "schema": "resumecert-simulator-smoke-v1",
        "scope": "constructed engineering fixtures; no matched pilot, model, or real workflow",
        "witness": {
            "possible_worlds": baseline["possible_worlds"],
            "verified_runs": baseline["possible_worlds"] * len(rows) * 2,
            "matches_standalone_proof": True,
            "rows": rows,
        },
        "document_release": _document_fixtures(),
    }


def report_json():
    return json.dumps(report(), indent=2, sort_keys=True) + "\n"
