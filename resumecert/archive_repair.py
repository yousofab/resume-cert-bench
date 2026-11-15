"""Durable checkpoints and bounded, transactional repair for the SQLite archive fixture."""

from dataclasses import dataclass
import json

from .archive_transfer import CONTRACT, FIELDS, authorize, create_schema, read_fields, write_transaction
from .contract import Facts
from .inference import infer
from .pilot.policies import decide
from .records import Certificate, Context, Decision, Read, Scope
from .simulator import BudgetExceeded
from .verifier import verify

METHODS = {
    "reuse_only": (True, None),
    "restart_fixed": (False, "fixed"),
    "restart_exact": (False, "optimal_tree"),
    "reuse_fixed": (True, "fixed"),
    "reuse_greedy": (True, "adaptive"),
    "reuse_exact": (True, "optimal_tree"),
}


@dataclass(frozen=True, slots=True)
class Attempt:
    status: str
    certificate: Certificate | None = None
    checked_epochs: tuple[tuple[str, int, int], ...] = ()
    trace: tuple[Read, ...] = ()


def create_repair_schema(connection):
    create_schema(connection)
    connection.executescript("""
        CREATE TABLE repair_checkpoints (
            request_id INTEGER PRIMARY KEY,
            contract_digest TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('allow', 'block', 'defer')),
            evidence TEXT NOT NULL
        );
        CREATE TABLE repair_epochs (
            request_id INTEGER NOT NULL,
            field TEXT NOT NULL,
            epoch INTEGER NOT NULL CHECK (typeof(epoch) = 'integer' AND epoch >= 0),
            PRIMARY KEY (request_id, field)
        ) WITHOUT ROWID;
        CREATE TRIGGER repair_prevent_reused_identity BEFORE INSERT ON datasets
        WHEN EXISTS (SELECT 1 FROM repair_epochs WHERE request_id = NEW.id)
        BEGIN
            SELECT RAISE(ABORT, 'dataset identity cannot be reused');
        END;
        CREATE TRIGGER repair_prevent_id_change BEFORE UPDATE OF id ON datasets
        WHEN NEW.id != OLD.id
        BEGIN
            SELECT RAISE(ABORT, 'dataset identity cannot change');
        END;
        CREATE TRIGGER repair_seed_epochs AFTER INSERT ON datasets
        BEGIN
    """ + "\n".join(f"INSERT INTO repair_epochs VALUES (NEW.id, '{field}', 0);"
                   for field in FIELDS) + "\nEND;")
    for field in FIELDS:
        connection.executescript(f"""
            CREATE TRIGGER repair_epoch_{field} AFTER UPDATE OF {field} ON datasets
            WHEN OLD.{field} != NEW.{field}
            BEGIN
                UPDATE repair_epochs SET epoch = epoch + 1
                WHERE request_id = NEW.id AND field = '{field}';
            END;
        """)


def read_epochs(connection, request_id, fields):
    if not fields:
        return {}
    positions = ", ".join("?" for _ in fields)
    rows = connection.execute(
        f"SELECT field, epoch FROM repair_epochs WHERE request_id = ? AND field IN ({positions})",
        (request_id, *fields),
    )
    return dict(rows)


def checkpoint(connection, request_id, initial_budget):
    """Persist only independently verified observations and their authoritative epochs."""
    with write_transaction(connection):
        grant = authorize(connection, request_id, initial_budget)
        cert = grant.certificate
        if not verify(grant.audit, cert).accepted:
            raise RuntimeError("unverified initial certificate")
        known = dict(cert.initial_facts)
        for read in cert.reads:
            known.update(read.values)
        epochs = read_epochs(connection, request_id, tuple(known))
        evidence = [(field, value, epochs[field]) for field, value in CONTRACT.facts(known)]
        connection.execute(
            "INSERT INTO repair_checkpoints VALUES (?, ?, ?, ?)",
            (request_id, CONTRACT.digest, cert.decision.value, json.dumps(evidence)),
        )
    return cert


def resume(connection, request_id, method, budget):
    """Refresh bounded evidence and persist at most one receipt inside the same lock."""
    if method not in METHODS:
        raise ValueError("unknown repair method")
    if type(budget) is not int or budget < 0:
        raise ValueError("invalid repair budget")
    reuse, policy = METHODS[method]
    with write_transaction(connection):
        row = connection.execute(
            "SELECT contract_digest, decision, evidence FROM repair_checkpoints WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row[0] != CONTRACT.digest:
            return Attempt("missing_or_incompatible_checkpoint")
        if connection.execute("SELECT 1 FROM archive_receipts WHERE request_id = ?",
                              (request_id,)).fetchone():
            return Attempt("already_committed")
        if row[1] != Decision.ALLOW:
            return Attempt("no_prior_allow")
        current = connection.execute("SELECT revision FROM datasets WHERE id = ?",
                                     (request_id,)).fetchone()
        if current is None:
            return Attempt("dataset_missing")

        evidence = json.loads(row[2]) if reuse else ()
        epochs = read_epochs(connection, request_id, tuple(field for field, _, _ in evidence))
        checked = tuple((field, old, epochs[field]) for field, _, old in evidence)
        retained: Facts = CONTRACT.facts((f, value) for f, value, old in evidence if epochs[f] == old)
        scope = Scope(CONTRACT.action_id, CONTRACT.digest, f"{request_id}@{current[0]}",
                      f"repair:{request_id}:{method}:{budget}")
        context = Context(CONTRACT, scope, budget, retained, "", "")
        ledger = []
        spent = 0

        def read(name):
            nonlocal spent
            query = CONTRACT.query(name)
            if spent + query.cost > budget:
                raise BudgetExceeded("repair read exceeds remaining budget")
            values = CONTRACT.facts(read_fields(connection, request_id, query.fields))
            event = Read(scope, len(ledger) + 1, name, values, query.cost)
            ledger.append(event)
            spent += query.cost
            return event

        if policy is None:
            certificate = infer(context)
        else:
            certificate = decide(policy, context, read)
        if certificate != infer(context, tuple(ledger)) or certificate.decision is Decision.INCONSISTENT:
            raise RuntimeError("policy decision disagrees with recorded repair evidence")
        if certificate.decision is Decision.ALLOW:
            connection.execute("INSERT INTO archive_receipts VALUES (?)", (request_id,))
            return Attempt("committed", certificate, checked, tuple(ledger))
        return Attempt(certificate.decision.value, certificate, checked, tuple(ledger))
