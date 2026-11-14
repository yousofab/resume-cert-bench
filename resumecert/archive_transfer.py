"""SQLite test fixture for a local, inert research archive workflow."""

from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations
import sqlite3

from .contract import ActionContract, Query
from .pilot.policies import decide
from .records import Audit, Certificate, Decision
from .simulator import Simulation
from .verifier import verify

RELEVANT = ("manifest_verified", "curator_approved", "dataset_restricted", "restricted_lane_open")
FIELDS = (*RELEVANT, "annotation_revision")
METHODS = ("snapshot_only", "revision", "all_evidence", "sufficient_evidence", "full_predicate")


def eq_one(field):
    return ("eq", ("field", field), ("literal", 1))


CONTRACT = ActionContract(
    "archive_dataset", "transfer-v1",
    tuple((field, (0, 1)) for field in FIELDS),
    tuple(Query(field, (field,)) for field in FIELDS),
    ("all", eq_one(RELEVANT[0]),
     ("any", eq_one(RELEVANT[1]), ("all", eq_one(RELEVANT[2]), eq_one(RELEVANT[3])))),
)


@dataclass(frozen=True, slots=True)
class Grant:
    """Actor-issued evidence and its trusted audit, scoped to one fixture row."""

    request_id: int
    revision: int
    audit: Audit
    certificate: Certificate


@dataclass(frozen=True, slots=True)
class Receipt:
    status: str
    checked_fields: tuple[str, ...] = ()


@contextmanager
def write_transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def create_schema(connection):
    connection.executescript("""
        CREATE TABLE datasets (
            id INTEGER PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 0,
            manifest_verified INTEGER NOT NULL CHECK (manifest_verified IN (0, 1)),
            curator_approved INTEGER NOT NULL CHECK (curator_approved IN (0, 1)),
            dataset_restricted INTEGER NOT NULL CHECK (dataset_restricted IN (0, 1)),
            restricted_lane_open INTEGER NOT NULL CHECK (restricted_lane_open IN (0, 1)),
            annotation_revision INTEGER NOT NULL CHECK (annotation_revision IN (0, 1))
        );
        CREATE TABLE archive_receipts (request_id INTEGER PRIMARY KEY);
    """)


def seed(connection, request_id, facts):
    values = dict(CONTRACT.facts(facts))
    connection.execute(
        f"INSERT INTO datasets (id, {', '.join(FIELDS)}) VALUES (?, ?, ?, ?, ?, ?)",
        (request_id, *(values[field] for field in FIELDS)),
    )


def change(connection, request_id, fields):
    """Commit the scheduled changes through a separate writer connection."""
    if len(set(fields)) != len(fields) or any(field not in FIELDS for field in fields):
        raise ValueError("invalid fixture change")
    if not fields:
        return
    toggles = ", ".join(f"{field} = 1 - {field}" for field in fields)
    connection.execute(f"UPDATE datasets SET revision = revision + 1, {toggles} WHERE id = ?",
                       (request_id,))


def read_fields(connection, request_id, fields):
    allowed_columns = (*FIELDS, "revision")
    if not fields or any(field not in allowed_columns for field in fields):
        raise ValueError("invalid column selection")
    row = connection.execute(
        f"SELECT {', '.join(fields)} FROM datasets WHERE id = ?", (request_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown fixture request")
    return dict(zip(fields, row))


def authorize(connection, request_id, budget):
    snapshot = read_fields(connection, request_id, ("revision", *FIELDS))
    revision = snapshot.pop("revision")
    sim = Simulation(CONTRACT, snapshot, snapshot_id=f"{request_id}@{revision}",
                     run_id=f"authorize:{request_id}", budget=budget)
    certificate = decide("adaptive", sim.context, sim.read)
    return Grant(request_id, revision, sim.audit(), certificate)


def sufficient_subproofs(contract, observed):
    """Enumerate inclusion-minimal sufficient subsets of acquired field values."""
    accepted = []
    worlds = tuple(contract.worlds())
    for size in range(len(observed) + 1):
        for subset in combinations(observed, size):
            if any(set(proof).issubset(subset) for proof in accepted):
                continue
            compatible = [world for world in worlds if all(world[f] == v for f, v in subset)]
            if compatible and all(contract.permits(world) for world in compatible):
                accepted.append(subset)
    return tuple(accepted)


def matches(current, proof):
    return all(current[field] == value for field, value in proof)


def archive(connection, request_id, grant, method):
    if method not in METHODS:
        raise ValueError("unknown commit method")
    scope = grant.certificate.scope
    if (request_id != grant.request_id or scope.snapshot_id != f"{request_id}@{grant.revision}"
            or scope.run_id != f"authorize:{request_id}" or grant.audit.context.contract != CONTRACT
            or not verify(grant.audit, grant.certificate).accepted):
        return Receipt("invalid_certificate")
    if grant.certificate.decision is not Decision.ALLOW:
        return Receipt("no_allow_certificate")

    observations = dict(grant.certificate.initial_facts)
    for read in grant.certificate.reads:
        observations.update(read.values)
    observed = CONTRACT.facts(observations)
    proofs = ()
    fields = ()
    if method == "revision":
        fields = ("revision",)
    elif method == "all_evidence":
        proofs = (observed,)
        fields = tuple(field for field, _ in observed)
    elif method == "sufficient_evidence":
        proofs = sufficient_subproofs(CONTRACT, observed)
        if not proofs:
            raise RuntimeError("verified allow has no sufficient subproof")
        used_fields = {field for proof in proofs for field, _ in proof}
        fields = tuple(field for field in FIELDS if field in used_fields)
    elif method == "full_predicate":
        fields = RELEVANT

    # All target guards and the receipt insertion share a single write transaction.
    with write_transaction(connection):
        if connection.execute("SELECT 1 FROM archive_receipts WHERE request_id = ?",
                              (request_id,)).fetchone():
            return Receipt("already_committed")
        if fields:
            current = read_fields(connection, request_id, fields)
            if method == "revision" and current["revision"] != grant.revision:
                return Receipt("guard_rejected", fields)
            if method in ("all_evidence", "sufficient_evidence") and not any(
                    matches(current, proof) for proof in proofs):
                return Receipt("guard_rejected", fields)
            if method == "full_predicate" and not CONTRACT.permits(current):
                return Receipt("guard_rejected", fields)
        connection.execute("INSERT INTO archive_receipts VALUES (?)", (request_id,))
    return Receipt("committed", fields)


def connect(path):
    # Explicit transaction control, and no nondeterministic waiting for test locks.
    return sqlite3.connect(path, autocommit=True, timeout=0)
