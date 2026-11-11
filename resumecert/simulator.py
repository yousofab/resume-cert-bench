"""Trusted fixture runner with a bounded, scoped read capability."""

from .contract import ActionContract, same
from .records import Audit, Context, Read, Scope


class BudgetExceeded(ValueError):
    pass


class Simulation:
    def __init__(self, contract: ActionContract, live, *, snapshot_id: str,
                 run_id: str, budget: int, initial_facts=(), checkpoint: str = "",
                 resume_message: str = ""):
        if type(budget) is not int or budget < 0:
            raise ValueError("budget must be a nonnegative integer")
        if any(type(s) is not str or not s.strip() for s in (snapshot_id, run_id)):
            raise ValueError("snapshot and run identifiers must be nonempty")
        if type(checkpoint) is not str or type(resume_message) is not str:
            raise ValueError("historical context must be text")
        self._live = contract.facts(live)
        live_map = dict(self._live)
        if (len(live_map) != len(contract.domains)
                or not any(all(same(w[f], v) for f, v in self._live)
                           for w in contract.worlds())):
            raise ValueError("live snapshot is outside the admissible state space")
        initial = contract.facts(initial_facts)
        if not all(same(live_map[f], v) for f, v in initial):
            raise ValueError("initial current facts disagree with snapshot")
        scope = Scope(contract.action_id, contract.digest, snapshot_id, run_id)
        self._context = Context(contract, scope, budget, initial, checkpoint, resume_message)
        self._reads: list[Read] = []
        self._spent = 0

    @property
    def context(self) -> Context:
        return self._context

    def read(self, name: str) -> Read:
        """The callable given to a policy. Denied reads do not change the ledger."""
        query = self._context.contract.query(name)
        if self._spent + query.cost > self._context.budget:
            raise BudgetExceeded(f"query {name!r} exceeds remaining budget")
        live = dict(self._live)
        event = Read(self._context.scope, len(self._reads) + 1, name,
                     tuple((f, live[f]) for f in query.fields), query.cost)
        self._reads.append(event)
        self._spent += query.cost
        return event

    def audit(self) -> Audit:
        return Audit(self._context, self._live, tuple(self._reads), self._spent)
