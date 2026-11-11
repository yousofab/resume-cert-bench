"""Audit a submitted certificate independently of the producer and read runner."""

from .contract import Facts, same
from .records import Audit, Certificate, Decision, Verification


def _facts_equal(left: Facts, right: Facts) -> bool:
    return (type(left) is tuple and type(right) is tuple and len(left) == len(right)
            and all(type(a) is tuple and type(b) is tuple and len(a) == len(b) == 2
                    and a[0] == b[0] and same(a[1], b[1]) for a, b in zip(left, right)))


def verify(audit: Audit, certificate: Certificate) -> Verification:
    context = audit.context
    contract = context.contract
    scope = context.scope
    if (scope.action_id != contract.action_id or scope.contract_digest != contract.digest
            or any(type(s) is not str or not s.strip() for s in (scope.snapshot_id, scope.run_id))
            or certificate.scope != scope):
        return Verification(False, "scope_mismatch")
    if (type(context.budget) is not int or context.budget < 0
            or type(certificate.budget) is not int or certificate.budget != context.budget
            or not _facts_equal(certificate.initial_facts, context.initial_facts)):
        return Verification(False, "context_mismatch")
    if (type(certificate.reads) is not tuple or type(audit.reads) is not tuple
            or len(certificate.reads) != len(audit.reads)):
        return Verification(False, "transcript_mismatch")
    try:
        live = contract.facts(audit.live)
        initial = contract.facts(context.initial_facts)
    except (TypeError, ValueError):
        return Verification(False, "invalid_snapshot_or_initial_facts")
    live_map = dict(live)
    if (not _facts_equal(initial, context.initial_facts)
            or not _facts_equal(live, audit.live)
            or len(live) != len(contract.domains)
            or not any(_facts_equal(live, tuple(w.items())) for w in contract.worlds())
            or not all(same(live_map[f], v) for f, v in initial)):
        return Verification(False, "invalid_snapshot_or_initial_facts")
    # Check observations against the private snapshot, but never include its
    # unobserved fields as evidence when deciding the claim below.
    knowledge = dict(initial)
    cost = 0
    for index, (actual, claimed) in enumerate(zip(audit.reads, certificate.reads), 1):
        try:
            query = contract.query(actual.query)
        except ValueError:
            return Verification(False, "invalid_audit_read")
        expected = tuple((f, live_map[f]) for f in query.fields)
        if (actual.scope != scope or type(actual.ordinal) is not int or actual.ordinal != index
                or type(actual.cost) is not int or actual.cost != query.cost
                or not _facts_equal(actual.values, expected)):
            return Verification(False, "invalid_audit_read")
        if (claimed.scope != scope or type(claimed.ordinal) is not int or claimed.ordinal != index
                or type(claimed.cost) is not int or claimed.cost != query.cost
                or claimed.query != actual.query or not _facts_equal(claimed.values, expected)):
            return Verification(False, "transcript_mismatch")
        knowledge.update(expected)
        cost += query.cost
    if (type(audit.spent) is not int or type(certificate.spent) is not int
            or cost > context.budget or cost != audit.spent or cost != certificate.spent):
        return Verification(False, "cost_mismatch")
    yes = no = 0
    for candidate in contract.worlds():
        if any(not same(candidate[f], v) for f, v in knowledge.items()):
            continue
        if contract.permits(candidate):
            yes += 1
        else:
            no += 1
    if not (yes or no):
        return Verification(False, "inconsistent_audit")
    expected_decision = Decision.DEFER if yes and no else Decision.ALLOW if yes else Decision.BLOCK
    if (certificate.decision is not expected_decision
            or type(certificate.compatible_worlds) is not int
            or certificate.compatible_worlds != yes + no):
        return Verification(False, "invalid_claim")
    return Verification(True, "verified")
