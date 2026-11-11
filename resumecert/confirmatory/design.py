"""Generate the locked cases without importing policies, the simulator, or the verifier."""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from itertools import combinations, product
import json
from pathlib import Path

from ..contract import ActionContract, Facts, Query

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "study/confirmatory-v1/plan.json"
NUISANCE = "annotation_revision"
SURFACES = {
    "low": "Return to the same pending step.",
    "high": "The discussion has moved to training and staffing. Return to the same pending step.",
}
VOTES = ("vote_a", "vote_b", "vote_c", "vote_d")


@dataclass(frozen=True, slots=True)
class Family:
    contract: ActionContract
    bundle: tuple[str, ...]
    expensive: str

    @property
    def name(self):
        return self.contract.action_id


@dataclass(frozen=True, slots=True)
class Pair:
    family: str
    key: str
    before: Facts
    previous_permitted: bool
    surface: str
    checkpoint: str
    resume_message: str
    changed_field: str
    pivotal: Facts
    benign: Facts
    pivotal_id: str
    benign_id: str


@dataclass(frozen=True, slots=True)
class Control:
    family: str
    kind: str
    key: str
    snapshot_id: str
    facts: Facts
    initial_facts: Facts
    checkpoint: str
    resume_message: str


def load_plan():
    return json.loads(PLAN_PATH.read_bytes())


def stable(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def digest(value):
    return sha256(stable(value).encode("ascii")).hexdigest()


def _eq(field, value):
    return ("eq", ("field", field), ("literal", value))


def _any(expressions):
    return ("any", *expressions)


def _all(*expressions):
    return ("all", *expressions)


def _contract(name, domains, precondition, admissibility=None):
    all_domains = domains + ((NUISANCE, (0, 1)),)
    return ActionContract(name, "confirmatory-v1", all_domains,
                          tuple(Query(f, (f,)) for f, _ in all_domains),
                          precondition, admissibility)


def families():
    quorum = _any(
        _all(_eq("required_yes", k), _any(_all(*(_eq(v, 1) for v in subset))
                                         for subset in combinations(VOTES, k)))
        for k in (1, 2, 3, 4))
    capacity = _any(
        _all(_eq("capacity", cap), _eq("committed", used), _eq("request_size", req))
        for cap, used, req in product(range(5), range(4), (1, 2, 3)) if cap - used >= req)
    admissible = _any(
        _all(_eq("capacity", cap), _eq("committed", used))
        for cap, used in product(range(5), range(4)) if used <= cap)
    return (
        Family(_contract("quorum_release", tuple((v, (0, 1)) for v in VOTES)
                         + (("required_yes", (1, 2, 3, 4)),), quorum),
               ("vote_a", "vote_b"), "required_yes"),
        Family(_contract("capacity_reservation", (("capacity", tuple(range(5))),
                                                  ("committed", tuple(range(4))),
                                                  ("request_size", (1, 2, 3)),
                                                  ("service_open", (0, 1))),
                         _all(_eq("service_open", 1), capacity), admissible),
               ("capacity", "committed"), "capacity"),
    )


def contract_variant(family, name):
    contract = family.contract
    if name == "primary":
        return contract
    if name == "grouped":
        return replace(contract, version="confirmatory-v1-grouped",
                       queries=contract.queries + (Query("read_bundle", family.bundle, 1),))
    if name == "unequal":
        return replace(contract, version="confirmatory-v1-unequal", queries=tuple(
            replace(q, cost=2) if q.name == family.expensive else q for q in contract.queries))
    raise ValueError(f"unknown cost regime: {name}")


def oracle(family, values):
    """Independent arithmetic specification used to check scenario construction."""
    if family == "quorum_release":
        return sum(values[v] for v in VOTES) >= values["required_yes"]
    if family == "capacity_reservation":
        return values["service_open"] == 1 and (
            values["capacity"] - values["committed"] >= values["request_size"])
    raise ValueError(f"unknown family: {family}")


def _admissible(family, values):
    return family != "capacity_reservation" or values["committed"] <= values["capacity"]


def _checkpoint(name, permitted):
    return f"Before the boundary, {name} was {'permitted' if permitted else 'blocked'}."


def _structure(facts):
    return tuple((f, v) for f, v in facts if f != NUISANCE)


def _rank(seed, *parts):
    return digest((seed, *parts))


def _facts(structural, noise):
    return structural + ((NUISANCE, noise),)


def _candidates(family):
    contract = family.contract
    worlds = {tuple(w.items()): oracle(family.name, w) for w in contract.worlds()
              if w[NUISANCE] == 0}
    for state, truth in worlds.items():
        values = dict(state)
        for field, domain in contract.domains[:-1]:
            for offset in (-1, 1):
                new = values | {field: values[field] + offset}
                if new[field] not in domain:
                    continue
                after = tuple(new.items())
                if after in worlds and truth != worlds[after]:
                    yield field, truth, _structure(state), _structure(after)


def _pair_design(family, seed, per_cell):
    candidates = sorted(_candidates(family), key=lambda x: _rank(seed, family.name, x))
    used_before, used_edges = set(), set()
    field_counts = Counter()
    pairs = []
    for n, (previous, surface) in enumerate(
        ((truth, s) for _ in range(per_cell) for truth in (False, True) for s in SURFACES), 1
    ):
        choices = [(field, before, after) for field, truth, before, after in candidates
                   if truth == previous and before not in used_before
                   and frozenset((before, after)) not in used_edges]
        if not choices:
            raise ValueError(f"cannot meet locked quota: {family.name} pair {n}")
        field, before, after = min(choices, key=lambda x: field_counts[x[0]])
        used_before.add(before)
        used_edges.add(frozenset((before, after)))
        field_counts[field] += 1
        noise = int(_rank(seed, family.name, "annotation", n)[0], 16) % 2
        ids = tuple(_rank(seed, "snapshot", family.name, 2 * n + i)[:32] for i in (0, 1))
        if int(_rank(seed, family.name, "shuffle", n)[0], 16) % 2:
            ids = ids[::-1]
        pairs.append(Pair(family.name, f"{family.name}-{n:02d}", _facts(before, noise),
                          previous, surface, _checkpoint(family.name, previous), SURFACES[surface],
                          field, _facts(after, noise), _facts(before, 1 - noise), *ids))
    return tuple(pairs)


def _controls(family, seed):
    worlds = tuple(family.contract.worlds())
    result = []
    for kind in ("interrupted_unchanged", "uninterrupted_current"):
        for truth in (False, True):
            possible = [tuple(w.items()) for w in worlds if oracle(family.name, w) == truth]
            facts = min(possible, key=lambda s: _rank(seed, family.name, "control", kind, s))
            key = f"{family.name}-{kind}-{int(truth)}"
            initial = facts if kind == "uninterrupted_current" else ()
            result.append(Control(family.name, kind, key, _rank(seed, "control-id", key)[:32],
                                  facts, initial, _checkpoint(family.name, truth), SURFACES["low"]))
    return tuple(result)


def build_design(plan):
    seed = plan["design_seed"]
    n = plan["split"]["pairs_per_surface_and_previous_truth_cell"]
    specs = families()
    pairs = tuple(p for f in specs for p in _pair_design(f, seed, n))
    controls = tuple(c for f in specs for c in _controls(f, seed))
    return specs, pairs, controls


def audit_design(plan, specs, pairs, controls):
    split = plan["split"]
    family_names = [f.name for f in specs]
    if (family_names != split["held_out"] or set(family_names) & set(split["development"])
            or len(set(family_names)) != len(family_names)):
        raise ValueError("held-out split mismatch")
    if len({p.key for p in pairs}) != len(pairs) or len({c.key for c in controls}) != len(controls):
        raise ValueError("duplicated fixture key")
    ids = [x for p in pairs for x in (p.pivotal_id, p.benign_id)] + [c.snapshot_id for c in controls]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicated snapshot ID")
    expected_cases = 2 * len(specs) * split["pairs_per_family"]
    if len(pairs) * 2 != expected_cases or plan["primary"]["denominator"] != expected_cases:
        raise ValueError("primary denominator mismatch")
    report = {}
    for family in specs:
        contract = family.contract
        domains = [dict(zip((f for f, _ in contract.domains), values))
                   for values in product(*(d for _, d in contract.domains))]
        actual_worlds = {tuple(w.items()) for w in contract.worlds()}
        intended = {tuple(w.items()) for w in domains if _admissible(family.name, w)}
        if actual_worlds != intended or any(contract.permits(dict(s)) != oracle(family.name, dict(s))
                                           for s in actual_worlds):
            raise ValueError("finite contract diverges from independent arithmetic rules")
        subset = [p for p in pairs if p.family == family.name]
        hold = [c for c in controls if c.family == family.name]
        tally = Counter((p.surface, p.previous_permitted) for p in subset)
        if tally != Counter({(s, t): split["pairs_per_surface_and_previous_truth_cell"]
                             for s in SURFACES for t in (False, True)}):
            raise ValueError("unbalanced family strata")
        if len(subset) != split["pairs_per_family"] or len(hold) != split["controls_per_family"]:
            raise ValueError("family size mismatch")
        if (len({_structure(p.before) for p in subset}) != len(subset)
                or len({frozenset((_structure(p.before), _structure(p.pivotal)))
                        for p in subset}) != len(subset)):
            raise ValueError("duplicated starting structure or pivotal edge")
        for p in subset:
            if any(x not in actual_worlds for x in (p.before, p.pivotal, p.benign)):
                raise ValueError("inadmissible pair member")
            b = dict(p.before)
            if (oracle(p.family, b) != p.previous_permitted
                    or oracle(p.family, dict(p.pivotal)) == p.previous_permitted
                    or oracle(p.family, dict(p.benign)) != p.previous_permitted
                    or p.checkpoint != _checkpoint(p.family, p.previous_permitted)
                    or p.resume_message != SURFACES[p.surface] or p.changed_field == NUISANCE):
                raise ValueError("pair labels or shared public context inconsistent")
            for facts, field in ((p.pivotal, p.changed_field), (p.benign, NUISANCE)):
                after = dict(facts)
                if ({f for f in b if b[f] != after[f]} != {field}
                        or abs(b[field] - after[field]) != 1):
                    raise ValueError("unmatched transition magnitude")
        control_cells = Counter()
        for c in hold:
            truth = oracle(c.family, dict(c.facts))
            control_cells[c.kind, truth] += 1
            initial = c.facts if c.kind == "uninterrupted_current" else ()
            if (c.facts not in actual_worlds or c.initial_facts != initial
                    or c.checkpoint != _checkpoint(c.family, truth)
                    or c.resume_message != SURFACES["low"]):
                raise ValueError("invalid control facts or context")
        if control_cells != Counter({(k, t): 1 for k in (
                "interrupted_unchanged", "uninterrupted_current") for t in (False, True)}):
            raise ValueError("invalid control strata")
        report[family.name] = {
            "admissible_worlds_checked": len(actual_worlds), "pairs": len(subset),
            "controls": len(hold),
            "previous_truth_and_surface": {f"{s}:{int(t)}": tally[s, t]
                                           for s in SURFACES for t in (False, True)},
            "pivotal_field_counts": dict(sorted(Counter(p.changed_field for p in subset).items())),
        }
    if sum(r["controls"] for r in report.values()) != len(controls):
        raise ValueError("unexpected control family")
    payload = {"pairs": [asdict(p) for p in pairs], "controls": [asdict(c) for c in controls]}
    return {
        "fixture_sha256": digest(payload),
        "contracts": {f.name: {v: contract_variant(f, v).digest
                              for v in ("primary", "grouped", "unequal")} for f in specs},
        "families": report,
        "primary_cases": len(pairs) * 2,
        "controls": len(controls),
    }
