"""Reference certificate producer using only the policy's visible evidence."""

from .contract import same
from .records import Certificate, Context, Decision, Read


def infer(context: Context, reads: tuple[Read, ...] = ()) -> Certificate:
    facts = list(context.initial_facts)
    spent = 0
    for ordinal, event in enumerate(reads, 1):
        query = context.contract.query(event.query)
        if (event.scope != context.scope or type(event.ordinal) is not int
                or event.ordinal != ordinal or type(event.cost) is not int
                or event.cost != query.cost or type(event.values) is not tuple
                or any(type(pair) is not tuple or len(pair) != 2 for pair in event.values)
                or tuple(f for f, _ in event.values) != query.fields
                or not all(context.contract.valid_value(f, v) for f, v in event.values)):
            raise ValueError("invalid read envelope")
        facts.extend(event.values)
        spent += query.cost
    if spent > context.budget:
        raise ValueError("evidence exceeds budget")
    outcomes = [context.contract.permits(world) for world in context.contract.worlds()
                if all(same(world[f], v) for f, v in facts)]
    if not outcomes:
        decision = Decision.INCONSISTENT
    elif all(outcomes):
        decision = Decision.ALLOW
    elif not any(outcomes):
        decision = Decision.BLOCK
    else:
        decision = Decision.DEFER
    return Certificate(context.scope, context.budget, context.initial_facts, tuple(reads),
                       decision, len(outcomes), spent)
