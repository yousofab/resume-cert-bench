"""Primary outcome reducer. This module cannot run a policy."""

from collections import Counter
from dataclasses import dataclass

from ..records import Decision
from .design import oracle


@dataclass(frozen=True, slots=True)
class PrimaryRecord:
    snapshot_id: str
    policy: str
    budget: int
    claimed: Decision
    verified: bool
    spent: int


def score_primary(plan, pairs, rows):
    """Require one verified primary record per case and frozen policy.

    The caller must obtain verified directly from the independent verifier.
    The truth check here uses the arithmetic specification, not the runner.
    """
    spec = plan["primary"]
    a, f = spec["policy_a"], spec["policy_f"]
    budget = spec["budget"]
    facts = {sid: (p.family, world) for p in pairs
             for sid, world in ((p.pivotal_id, p.pivotal), (p.benign_id, p.benign))}
    if (len(facts) != spec["denominator"] or len(facts) != 2 * len(pairs)
            or set(family for family, _ in facts.values()) != set(spec["strata"])):
        raise ValueError("wrong cases, family set, or denominator")
    seen = {}
    for row in rows:
        key = (row.snapshot_id, row.policy)
        if row.snapshot_id not in facts or row.policy not in (a, f) or key in seen:
            raise ValueError("unexpected or duplicate primary cell")
        if (type(row.budget) is not int or row.budget != budget or type(row.spent) is not int
                or not 0 <= row.spent <= budget or row.verified is not True):
            raise ValueError("primary cell failed budget or verifier gate")
        family, world = facts[row.snapshot_id]
        truth = Decision.ALLOW if oracle(family, dict(world)) else Decision.BLOCK
        if row.claimed is not truth and row.claimed is not Decision.DEFER:
            raise ValueError("invalid or incorrect definitive decision")
        seen[key] = row
    if len(seen) != len(facts) * 2:
        raise ValueError("missing primary cell")

    def summarize(subset):
        if not subset:
            raise ValueError("empty primary stratum")
        counts = Counter()
        costs = Counter()
        for pair in subset:
            completed = Counter()
            for sid in (pair.pivotal_id, pair.benign_id):
                ca, cf = (seen[sid, p] for p in (a, f))
                sa, sf = ca.claimed is not Decision.DEFER, cf.claimed is not Decision.DEFER
                counts["both" if sa and sf else "a_only" if sa else "f_only" if sf else "neither"] += 1
                for row, certified in ((ca, sa), (cf, sf)):
                    costs[row.policy] += row.spent
                    completed[row.policy] += certified
                    if certified:
                        counts[f"{row.policy}_{row.claimed.value}"] += 1
            for p in (a, f):
                counts[f"{p}_both_arms"] += completed[p] == 2
        return {
            "pairs": len(subset), "cases": 2 * len(subset),
            "discordance": {key: counts[key] for key in ("both", "a_only", "f_only", "neither")},
            "delta_numerator": counts["a_only"] - counts["f_only"],
            "delta_denominator": 2 * len(subset),
            "certified": {p: {label: counts[f"{p}_{label}"] for label in ("allow", "block")}
                          for p in (a, f)},
            "pairs_fully_certified": {p: counts[f"{p}_both_arms"] for p in (a, f)},
            "read_units": {p: costs[p] for p in (a, f)},
        }

    pooled = summarize(pairs)
    strata = {f: summarize([p for p in pairs if p.family == f]) for f in spec["strata"]}
    outcome = ("consistent_support" if all(m["delta_numerator"] > 0 for m in strata.values())
               else "mixed" if pooled["delta_numerator"] > 0 else "no_pooled_gain")
    return {"pooled": pooled, "families": strata, "interpretation": outcome}
