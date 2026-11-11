"""Trusted runner, matching checks, and development-only results."""

from collections import Counter
from dataclasses import asdict, replace
from hashlib import sha256
import json
from random import Random

from ..contract import Query
from ..records import Decision
from ..simulator import Simulation
from ..verifier import verify
from .fixtures import FAMILIES, controls, matched_cases, validate
from .policies import POLICIES, decide, planner

BUDGETS = (0, 1, 2, 3, 4)


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _variant(case, mode):
    contract = case.family.contract
    if mode == "cheap_bundle":
        contract = replace(contract, version="pilot-v1-bundle",
                           queries=contract.queries + (Query("read_bundle", case.family.cheap_bundle),))
    return contract


def _run(cases, budgets, policies, mode, rng, witness_cache):
    output = []
    for case in cases:
        contract = _variant(case, mode)
        p = planner(contract, case.initial)
        key = (contract.digest, case.initial, case.live)
        if key not in witness_cache:
            witness_cache[key] = p.witness_cost(dict(case.live))
        for budget in budgets:
            for policy in policies:
                limit = sum(q.cost for q in contract.queries) if policy == "full" else budget
                sim = Simulation(contract, case.live, snapshot_id=case.snapshot_id,
                                 run_id=f"{rng.getrandbits(128):032x}", budget=limit,
                                 checkpoint=case.checkpoint, resume_message=case.message,
                                 initial_facts=case.initial)
                cert = decide(policy, sim.context, sim.read)
                audit = sim.audit()
                verification = verify(audit, cert)
                truth = Decision.ALLOW if contract.permits(dict(case.live)) else Decision.BLOCK
                decisive = cert.decision in (Decision.ALLOW, Decision.BLOCK)
                certified = verification.accepted and decisive
                if (certified and cert.decision is not truth) or (policy not in ("checkpoint", "defer") and not verification.accepted):
                    raise RuntimeError(f"unexpected verifier verdict: {policy}, {verification}")
                if certified and witness_cache[key] > audit.spent:
                    raise RuntimeError("certificate beat the per-world witness bound")
                output.append({
                    "mode": mode, "case": case.snapshot_id, "pair": case.public_key,
                    "family": case.family.name, "arm": case.arm, "surface": case.surface,
                    "policy": policy, "budget": limit, "truth": truth.value,
                    "claimed": cert.decision.value, "verified": verification.accepted,
                    "verification_reason": verification.reason,
                    "certified": certified, "spent": audit.spent,
                    "compatible_worlds": cert.compatible_worlds,
                    "minimum_witness_cost": witness_cache[key],
                    "queries": [r.query for r in audit.reads],
                    "observations": [r.values for r in audit.reads],
                })
    return output


def summarize(rows, paired=True):
    cnt = Counter(row["pair"] for row in rows if row["certified"])
    return {
        "n": len(rows),
        "certified_allow": sum(r["certified"] and r["truth"] == "allow" for r in rows),
        "certified_block": sum(r["certified"] and r["truth"] == "block" for r in rows),
        "pairs_both_certified": sum(n == 2 for n in cnt.values()) if paired else None,
        "rejected_claims": sum(not r["verified"] for r in rows),
        "raw_false_allow": sum(r["claimed"] == "allow" and r["truth"] == "block" for r in rows),
        "raw_false_block": sum(r["claimed"] == "block" and r["truth"] == "allow" for r in rows),
        "verified_deferrals": sum(r["verified"] and r["claimed"] == "defer" for r in rows),
        "verified_deferral_of_allowed": sum(r["verified"] and r["claimed"] == "defer"
                                            and r["truth"] == "allow" for r in rows),
        "total_read_cost": sum(r["spent"] for r in rows),
        "within_budget_witness": sum(r["minimum_witness_cost"] <= r["budget"] for r in rows),
        "within_budget_but_uncertified": sum(
            not r["certified"] and r["minimum_witness_cost"] <= r["budget"] for r in rows),
    }


def fixture_payload(cases, separate):
    def row(c):
        return {
            "pair_or_control": c.public_key, "arm": c.arm, "family": c.family.name,
            "snapshot_id": c.snapshot_id, "surface": c.surface,
            "checkpoint": c.checkpoint, "resume_message": c.message,
            "before": dict(c.before), "live": dict(c.live),
            "initial_current_facts": dict(c.initial),
        }
    return {
        "schema": "resumecert-pilot-fixtures-v1",
        "contracts": {f.name: asdict(f.contract) for f in FAMILIES},
        "matched_cases": [row(c) for c in cases],
        "independent_controls": [row(c) for c in separate],
    }


def study():
    cases, separate = matched_cases(), controls()
    groups = validate(cases)
    rng = Random(2026111104)
    cache = {}
    primary = _run(cases, BUDGETS, POLICIES, "primary", rng, cache)
    sensitivity = _run(cases, (2,), ("fixed", "adaptive", "optimal_tree"), "cheap_bundle", rng, cache)
    controls_rows = _run(separate, (0, 2), POLICIES, "controls", rng, cache)
    full = _run(cases, (0,), ("full",), "full", rng, cache)
    rows = primary + sensitivity + controls_rows + full
    fixtures = fixture_payload(cases, separate)

    # A matched pair is indistinguishable without current reads (opaque IDs
    # excluded); this guard tests every policy rather than inspecting text only.
    blind = {(r["pair"], r["arm"], r["policy"]): r["claimed"]
             for r in primary if r["budget"] == 0}
    if any(blind[k, "intervention", p] != blind[k, "benign", p]
           for k in groups for p in POLICIES):
        raise RuntimeError("a policy distinguishes paired outcomes without reads")

    def summary(data, **filters):
        return summarize([r for r in data if all(r[k] == v for k, v in filters.items())],
                         paired=data is not controls_rows and "arm" not in filters)

    results = {
        "schema": "resumecert-matched-development-pilot-v1",
        "scope": "synthetic development pilot; no LLM, people, or confirmatory test",
        "design": {"matched_pairs": len(groups), "matched_cases": len(cases),
                   "families": [f.name for f in FAMILIES], "controls": len(separate),
                   "budgets": BUDGETS, "runs": len(rows),
                   "primary_runs": len(primary),
                   "fixtures_sha256": sha256(stable(fixtures).encode()).hexdigest(),
                   "runs_sha256": sha256(stable(rows).encode()).hexdigest()},
        "primary": {str(b): {p: summary(primary, budget=b, policy=p) for p in POLICIES}
                    for b in BUDGETS},
        "budget_2_by_family": {f.name: {p: summary(primary, budget=2, policy=p, family=f.name)
                                        for p in POLICIES} for f in FAMILIES},
        "budget_2_by_surface": {s: {p: summary(primary, budget=2, policy=p, surface=s) for p in POLICIES}
                                 for s in ("low", "high")},
        "budget_2_by_arm": {a: {p: summary(primary, budget=2, policy=p, arm=a)
                               for p in POLICIES} for a in ("intervention", "benign")},
        "budget_2_cheap_bundle": {p: summary(sensitivity, policy=p)
                                 for p in ("fixed", "adaptive", "optimal_tree")},
        "controls": {a: {str(b): {p: summary(controls_rows, arm=a, budget=b, policy=p)
                                 for p in POLICIES} for b in (0, 2)}
                     for a in ("interrupted_unchanged", "uninterrupted_current")},
        "full_read": summary(full),
    }
    return results, fixtures, rows
