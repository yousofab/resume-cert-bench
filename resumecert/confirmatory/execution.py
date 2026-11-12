"""Execute and audit the registered confirmatory grid without changing the freeze."""

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product

from ..contract import Facts, same
from ..pilot.policies import POLICIES, decide, planner
from ..records import Decision
from ..simulator import Simulation
from ..verifier import verify
from .design import build_design, contract_variant, digest, oracle, stable
from .scoring import PrimaryRecord, score_primary

DEFINITIVE = (Decision.ALLOW, Decision.BLOCK)
READ_POLICIES = frozenset(POLICIES) - {"checkpoint", "defer"} | {"full"}


@dataclass(frozen=True, slots=True)
class Case:
    family: str
    key: str
    arm: str
    surface: str | None
    snapshot_id: str
    live: Facts
    initial: Facts
    checkpoint: str
    message: str


def cases_for(pairs, controls):
    paired = tuple(
        Case(pair.family, pair.key, arm, pair.surface, sid, live, (),
             pair.checkpoint, pair.resume_message)
        for pair in pairs
        for arm, sid, live in (("pivotal", pair.pivotal_id, pair.pivotal),
                               ("benign", pair.benign_id, pair.benign))
    )
    control = tuple(Case(c.family, c.key, c.kind, None, c.snapshot_id, c.facts,
                         c.initial_facts, c.checkpoint, c.resume_message) for c in controls)
    return paired, control


def schedule(plan, paired, control, contracts):
    secondary = plan["secondary"]
    policies = tuple(secondary["policies"])
    if policies != POLICIES:
        raise ValueError("registered policies disagree with pinned implementation")
    for case, budget, policy in product(paired, secondary["budgets"], policies):
        yield "primary_regime_grid", case, budget, policy
    for case, budget, policy in product(control, secondary["controls_budgets"], policies):
        yield "controls", case, budget, policy
    for mode in secondary["sensitivity"]:
        for case, policy in product(paired, mode["policies"]):
            yield mode["name"], case, mode["budget"], policy
    for case in paired:
        yield "full_read", case, sum(q.cost for q in contracts[case.family, "primary"].queries), "full"


def compatible_counts(audit):
    """Count compatible worlds after each prefix, including initial facts."""
    remaining = tuple(audit.context.contract.worlds())
    counts = []
    for facts in (audit.context.initial_facts, *(read.values for read in audit.reads)):
        remaining = tuple(w for w in remaining if all(same(w[f], v) for f, v in facts))
        counts.append(len(remaining))
    return counts


def run_cell(seed, mode, case, budget, policy, contract, floors):
    scope_key = (mode, case.snapshot_id, budget, policy)
    sim = Simulation(contract, case.live, snapshot_id=case.snapshot_id,
                     run_id=digest((seed, "confirmatory-run", scope_key)), budget=budget,
                     initial_facts=case.initial, checkpoint=case.checkpoint,
                     resume_message=case.message)
    cert = decide(policy, sim.context, sim.read)
    audit = sim.audit()
    verdict = verify(audit, cert)
    truth = Decision.ALLOW if oracle(case.family, dict(case.live)) else Decision.BLOCK
    floor_key = (contract.digest, case.initial, case.live)
    if floor_key not in floors:
        floors[floor_key] = planner(contract, case.initial).witness_cost(dict(case.live))

    return {
        "mode": mode, "family": case.family, "key": case.key, "arm": case.arm,
        "surface": case.surface, "snapshot_id": case.snapshot_id, "policy": policy,
        "budget": budget, "truth": truth, "claimed": cert.decision, "verified": verdict.accepted,
        "verification_reason": verdict.reason, "spent": audit.spent,
        "certified": verdict.accepted and cert.decision is truth,
        "hindsight_floor": floors[floor_key], "compatible_worlds_by_prefix": compatible_counts(audit),
        "context": {"scope": asdict(sim.context.scope), "budget": sim.context.budget,
                    "initial_facts": sim.context.initial_facts, "checkpoint": sim.context.checkpoint,
                    "resume_message": sim.context.resume_message},
        "audit": {"live": audit.live, "reads": [asdict(read) for read in audit.reads],
                  "spent": audit.spent},
        "certificate": asdict(cert),
    }


def validate_cell(row, contract):
    if row["verified"]:
        if row["claimed"] not in (Decision.DEFER, row["truth"]):
            raise ValueError("verifier accepted an incorrect decision")
        if row["compatible_worlds_by_prefix"][-1] != row["certificate"]["compatible_worlds"]:
            raise ValueError("compatible-world replay disagrees with certificate")
    elif row["policy"] in READ_POLICIES or row["verification_reason"] != "invalid_claim":
        raise ValueError(f"invalid audit: {row['verification_reason']}")
    if row["certified"] and row["hindsight_floor"] > row["spent"]:
        raise ValueError("verified certificate falls below the hindsight floor")
    if row["mode"] == "full_read" and (
        not row["certified"]
        or [r["query"] for r in row["audit"]["reads"]] != [q.name for q in contract.queries]
    ):
        raise ValueError("full-read reference did not establish the oracle decision")


def summarize(rows, paired=False):
    counts = Counter()
    certified_by_pair = Counter()
    for row in rows:
        truth, claim = row["truth"], row["claimed"]
        counts["allowed" if truth is Decision.ALLOW else "blocked"] += 1
        counts["certified_allow"] += row["certified"] and truth is Decision.ALLOW
        counts["certified_block"] += row["certified"] and truth is Decision.BLOCK
        counts["raw_false_allow"] += claim is Decision.ALLOW and truth is Decision.BLOCK
        counts["raw_false_block"] += claim is Decision.BLOCK and truth is Decision.ALLOW
        counts["rejected"] += not row["verified"]
        deferred = row["verified"] and claim is Decision.DEFER
        counts["verified_defer"] += deferred
        counts["verified_defer_allowed"] += deferred and truth is Decision.ALLOW
        counts["spent"] += row["spent"]
        prefixes = row["compatible_worlds_by_prefix"]
        counts["reads"] += len(prefixes) - 1
        counts["noninformative"] += sum(a == b for a, b in zip(prefixes, prefixes[1:]))
        exceeds = row["hindsight_floor"] > row["budget"]
        counts["floor_above_budget"] += exceeds
        counts["affordable_but_uncertified"] += not exceeds and not row["certified"]
        counts["affordable_verified_defer"] += not exceeds and deferred
        if row["certified"]:
            certified_by_pair[row["key"]] += 1

    def ratio(n, d):
        return {"numerator": n, "denominator": d} if d else None

    n = len(rows)
    return {
        "n": n, "actually_allowed": counts["allowed"], "actually_blocked": counts["blocked"],
        "certified_allow": counts["certified_allow"], "certified_block": counts["certified_block"],
        "pairs_fully_certified": sum(v == 2 for v in certified_by_pair.values()) if paired else None,
        "raw_false_allow": counts["raw_false_allow"], "raw_false_block": counts["raw_false_block"],
        "raw_false_allow_per_blocked": ratio(counts["raw_false_allow"], counts["blocked"]),
        "raw_false_block_per_allowed": ratio(counts["raw_false_block"], counts["allowed"]),
        "raw_false_allow_per_case": ratio(counts["raw_false_allow"], n),
        "raw_false_block_per_case": ratio(counts["raw_false_block"], n),
        "rejected_claims": counts["rejected"],
        "rejected_per_submitted": ratio(counts["rejected"], n),
        "verified_deferrals": counts["verified_defer"],
        "verified_deferral_of_allowed": counts["verified_defer_allowed"],
        "verified_deferral_per_allowed": ratio(counts["verified_defer_allowed"], counts["allowed"]),
        "spent_units": counts["spent"], "spent_units_per_case": ratio(counts["spent"], n),
        "reads": counts["reads"], "noninformative_reads": counts["noninformative"],
        "floor_above_budget": counts["floor_above_budget"],
        "affordable_but_uncertified": counts["affordable_but_uncertified"],
        "affordable_verified_deferrals": counts["affordable_verified_defer"],
        "hindsight_floor_histogram": dict(sorted(Counter(row["hindsight_floor"] for row in rows).items())),
    }


def results(plan, pairs, rows):
    primary = plan["primary"]
    policies = plan["secondary"]["policies"]
    grid = [r for r in rows if r["mode"] == "primary_regime_grid"]
    at_primary_budget = [r for r in grid if r["budget"] == primary["budget"]]
    primary_rows = [PrimaryRecord(r["snapshot_id"], r["policy"], r["budget"], r["claimed"],
                                  r["verified"], r["spent"]) for r in at_primary_budget
                    if r["policy"] in (primary["policy_a"], primary["policy_f"])]
    if len(primary_rows) != plan["expected_cells"]["primary_contrast"]:
        raise ValueError("wrong number of primary contrast cells")

    zero = {(r["key"], r["arm"], r["policy"]): r["claimed"] for r in grid if r["budget"] == 0}
    if any(zero[p.key, "pivotal", policy] != zero[p.key, "benign", policy]
           for p, policy in product(pairs, policies)):
        raise ValueError("a policy distinguished paired arms at zero reads")

    def by_policy(source, names, paired=True, **filters):
        return {
            policy: summarize([r for r in source if r["policy"] == policy
                               and all(r[k] == v for k, v in filters.items())], paired)
            for policy in names
        }

    controls = [r for r in rows if r["mode"] == "controls"]
    full = [r for r in rows if r["mode"] == "full_read"]
    return {
        "primary": score_primary(plan, pairs, primary_rows),
        "budget_grid": {str(b): by_policy(grid, policies, budget=b)
                        for b in plan["secondary"]["budgets"]},
        "primary_budget_by_family": {f: by_policy(at_primary_budget, policies, family=f)
                                     for f in primary["strata"]},
        "primary_budget_by_arm": {a: by_policy(at_primary_budget, policies, paired=False, arm=a)
                                  for a in plan["split"]["arms"]},
        "primary_budget_by_surface": {s: by_policy(at_primary_budget, policies, surface=s)
                                      for s in sorted({p.surface for p in pairs})},
        "controls": {
            arm: {str(b): by_policy(controls, policies, paired=False, arm=arm, budget=b)
                  for b in plan["secondary"]["controls_budgets"]}
            for arm in sorted({r["arm"] for r in controls})
        },
        "sensitivity": {
            mode["name"]: {
                label: by_policy(rows, mode["policies"], mode=mode["name"], **filters)
                for label, filters in [("pooled", {}), *((f, {"family": f}) for f in primary["strata"])]
            }
            for mode in plan["secondary"]["sensitivity"]
        },
        "full_read": {"pooled": summarize(full, True),
                      "families": {f: summarize([r for r in full if r["family"] == f], True)
                                   for f in primary["strata"]}},
    }


def execute(plan, sink):
    specs, pairs, controls = build_design(plan)
    paired, control = cases_for(pairs, controls)
    contracts = {(spec.name, mode): contract_variant(spec, mode)
                 for spec, mode in product(specs, ("primary", "grouped", "unequal"))}
    expected = plan["expected_cells"]
    cells = tuple(schedule(plan, paired, control, contracts))
    counts = Counter(mode for mode, *_ in cells)
    keys = {(mode, c.snapshot_id, b, p) for mode, c, b, p in cells}
    if (len(keys) != len(cells) or len(cells) != expected["total"]
            or counts != {k: n for k, n in expected.items() if k not in ("total", "primary_contrast")}):
        raise ValueError("missing, unexpected, or duplicate execution cell")

    trace_hash = sha256()
    rows = []
    floors = {}
    for mode, case, budget, policy in cells:
        cost_mode = mode if mode in ("grouped", "unequal") else "primary"
        contract = contracts[case.family, cost_mode]
        try:
            row = run_cell(plan["design_seed"], mode, case, budget, policy, contract, floors)
            line = (stable(row) + "\n").encode("ascii")
            sink.write(line)
            trace_hash.update(line)
            validate_cell(row, contract)
        except Exception as exc:
            exc.add_note(f"cell={mode}/{case.snapshot_id}/{budget}/{policy}")
            raise
        rows.append(row)
    return {
        "schema": "resumecert-confirmatory-results-v1",
        "design": {"pairs": len(pairs), "snapshots": len(paired), "controls": len(control),
                   "cells": {"total": len(rows), "primary_contrast": expected["primary_contrast"],
                             **dict(counts)}, "trace_sha256_uncompressed": trace_hash.hexdigest()},
        **results(plan, pairs, rows),
    }
