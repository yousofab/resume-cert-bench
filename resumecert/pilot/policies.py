"""Policies inspect public context and acquired observations only."""

from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from functools import lru_cache
from hashlib import sha256
from itertools import combinations, permutations
from random import Random

from ..contract import same
from ..inference import infer
from ..records import Decision

POLICIES = ("checkpoint", "defer", "random", "fixed", "adaptive", "optimal_tree")


class Planner:
    """Exact small-model planning over a uniform prior on admissible worlds."""

    def __init__(self, contract, initial):
        self.worlds = tuple(w for w in contract.worlds()
                            if all(same(w[f], v) for f, v in initial))
        self.labels = tuple(contract.permits(w) for w in self.worlds)
        self.start = tuple(range(len(self.worlds)))
        self.queries = contract.queries
        self.responses = tuple(tuple(tuple((type(w[f]), w[f]) for f in q.fields)
                                     for w in self.worlds) for q in self.queries)

    def partition(self, worlds, q):
        parts = defaultdict(list)
        for w in worlds:
            parts[self.responses[q][w]].append(w)
        return tuple(tuple(p) for p in parts.values())

    def pure(self, worlds):
        return all(self.labels[w] == self.labels[worlds[0]] for w in worlds)

    @staticmethod
    def _risk(labels):
        return Fraction(labels.count(True) * labels.count(False), len(labels))

    def greedy(self, worlds, available, budget):
        base = self._risk([self.labels[w] for w in worlds])
        scores = []
        for q in available:
            cost = self.queries[q].cost
            if cost <= budget:
                parts = self.partition(worlds, q)
                if len(parts) < 2:
                    continue
                gain = base - sum(self._risk([self.labels[w] for w in p]) for p in parts)
                scores.append((gain / cost, -cost, -q))
        return -max(scores)[2] if scores else None

    @lru_cache(maxsize=16384)
    def tree(self, worlds, available, budget):
        if self.pure(worlds):
            return len(worlds), 0, None
        best = (0, 0, None)
        for q in available:
            cost = self.queries[q].cost
            if cost > budget:
                continue
            parts = self.partition(worlds, q)
            if len(parts) < 2:
                continue
            rest = tuple(i for i in available if i != q)
            plans = [self.tree(p, rest, budget - cost) for p in parts]
            candidate = (sum(p[0] for p in plans),
                         sum(p[1] for p in plans) - len(worlds) * cost, q)
            if candidate[:2] > best[:2]:
                best = candidate
        return best

    @lru_cache(maxsize=1024)
    def fixed(self, budget):
        """Choose one precommitted order; stop on certification or budget."""
        def value(worlds, order, remaining):
            if self.pure(worlds):
                return len(worlds), 0
            if not order or self.queries[order[0]].cost > remaining:
                return 0, 0
            q = order[0]
            cost = self.queries[q].cost
            results = [value(p, order[1:], remaining - cost)
                       for p in self.partition(worlds, q)]
            return sum(r[0] for r in results), sum(r[1] for r in results) - len(worlds) * cost

        return max(permutations(range(len(self.queries))),
                   key=lambda order: value(self.start, order, budget))

    def observe(self, worlds, q, values):
        observed = tuple((type(v), v) for _, v in values)
        return tuple(w for w in worlds if self.responses[q][w] == observed)

    def witness_cost(self, live):
        """Private diagnostic: minimum cost of a proof tailored to one world."""
        def consistent(w, group):
            return all(same(self.worlds[w][f], live[f])
                       for q in group for f in self.queries[q].fields)

        query_ids = range(len(self.queries))
        return min(sum(self.queries[q].cost for q in subset)
                   for size in range(len(self.queries) + 1)
                   for subset in combinations(query_ids, size)
                   if self.pure(tuple(w for w in self.start if consistent(w, subset))))


@lru_cache(maxsize=256)
def planner(contract, initial):
    return Planner(contract, initial)


def decide(name, context, read):
    cert = infer(context)
    if name == "checkpoint":
        claim = Decision.ALLOW if context.checkpoint.endswith("was permitted.") else Decision.BLOCK
        return replace(cert, decision=claim)
    if name == "defer":
        return replace(cert, decision=Decision.DEFER)
    if name == "full":
        events = tuple(read(q.name) for q in context.contract.queries)
        return infer(context, events)

    p = planner(context.contract, context.initial_facts)
    worlds = p.start
    available = tuple(range(len(p.queries)))
    if name not in POLICIES:
        raise ValueError(f"unknown pilot policy: {name}")
    if name == "fixed":
        available = p.fixed(context.budget)
    elif name == "random":
        # Identical randomization in each pair; no run or snapshot ID is used.
        text = "\n".join((context.contract.digest, context.checkpoint, context.resume_message))
        random = Random(int(sha256(text.encode()).hexdigest(), 16))
        available = tuple(random.sample(available, len(available)))
    events = ()
    while available and cert.decision is Decision.DEFER:
        remaining = context.budget - cert.spent
        if name == "adaptive":
            q = p.greedy(worlds, available, remaining)
        elif name == "optimal_tree":
            q = p.tree(worlds, available, remaining)[2]
        else:
            q = available[0]
        if q is None or p.queries[q].cost > remaining:
            break
        event = read(p.queries[q].name)
        available = tuple(i for i in available if i != q)
        worlds = p.observe(worlds, q, event.values)
        events += (event,)
        cert = infer(context, events)
    return cert
