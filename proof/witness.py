"""Exhaustive, offline sanity check. The eight worlds are not empirical data."""

from functools import lru_cache
from itertools import combinations, product
import json

FIELDS = ("route", "normal_approval", "exception_approval")
WORLDS = tuple(product((0, 1), repeat=3))
ALL = tuple(range(len(WORLDS)))


def permit(world):
    route, normal, exception = world
    return exception if route else normal


def certified(indices):
    return len({permit(WORLDS[i]) for i in indices}) == 1


def partition(indices, field):
    return tuple(
        group
        for value in (0, 1)
        if (group := tuple(i for i in indices if WORLDS[i][field] == value))
    )


def fixed_score(fields):
    cells = {}
    for i, world in enumerate(WORLDS):
        cells.setdefault(tuple(world[f] for f in fields), []).append(i)
    return sum(len(cell) for cell in cells.values() if certified(cell))


@lru_cache(maxsize=None)
def optimal_adaptive(indices, budget):
    if certified(indices):
        return len(indices), None
    if not budget:
        return 0, None
    best = (0, None)
    for field in range(len(FIELDS)):
        groups = partition(indices, field)
        if len(groups) == 1:
            continue
        score = sum(optimal_adaptive(group, budget - 1)[0] for group in groups)
        if score > best[0]:
            best = (score, FIELDS[field])
    return best


def route_first(world, budget):
    if budget < 2:
        return None
    route = world[0]
    fields = (0, 2 if route else 1)
    group = tuple(i for i in ALL if all(WORLDS[i][f] == world[f] for f in fields))
    assert certified(group), "certificate must cover every consistent world"
    return permit(WORLDS[group[0]])


def main():
    table = []
    for budget in range(4):
        fixed = max(
            fixed_score(fields)
            for n in range(budget + 1)
            for fields in combinations(range(len(FIELDS)), n)
        )
        adaptive, first = optimal_adaptive(ALL, budget)
        table.append({"unit_read_budget": budget, "best_fixed_certified": fixed,
                      "best_adaptive_certified": adaptive, "adaptive_first_read": first})
    assert [(r["best_fixed_certified"], r["best_adaptive_certified"]) for r in table] == [
        (0, 0), (0, 0), (4, 8), (8, 8)
    ]
    assert table[2]["adaptive_first_read"] == "route"
    assert all(route_first(world, 2) == permit(world) for world in WORLDS)

    left, right = (0, 1, 0), (1, 1, 0)
    assert left[1:] == right[1:] and permit(left) != permit(right)
    print(json.dumps({
        "scope": "exhaustive constructed counterexample; no model or real-world data",
        "possible_worlds": len(WORLDS),
        "unit_reads_per_field": 1,
        "table": table,
        "twin_worlds": {"fields": FIELDS, "a": left, "b": right,
                        "visible_if_only_approvals_read": left[1:],
                        "permit_a": permit(left), "permit_b": permit(right)}
    }, indent=2))


if __name__ == "__main__":
    main()
