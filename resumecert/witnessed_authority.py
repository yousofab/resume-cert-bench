"""Witness the exact state of a provider authority outside its backup domain."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from . import provider_failover as routing, retention, revocable_delivery as delivery, terminal_evidence
from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict, digest, encode
from .provider_authority import AuthorityUnavailable, ProviderAuthority, _refresh_projection


class StateMismatch(AuthorityUnavailable):
    """The candidate authority does not match the witnessed state."""


TABLES = (
    "provider_identity", "authority_identity", "handoff_authority", "handoff_effects",
    "handoff_fences", "permanent_retirements",
)


def _commitment(db):
    schema = db.execute("""
        SELECT type, name, tbl_name, sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name
    """).fetchall()
    tables = {name: sorted(encode(row) for row in db.execute(f"SELECT * FROM {name}"))
              for name in TABLES}
    return digest(encode(["resumecert-authority-state/v1", schema, tables]))


def create_witness(connection):
    connection.executescript("""
        CREATE TABLE authority_heads (
            provider_id TEXT, store_id TEXT, revision INTEGER NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY (provider_id, store_id)
        ) WITHOUT ROWID;
        CREATE TRIGGER authority_head_forward_only BEFORE UPDATE ON authority_heads
        WHEN OLD.provider_id != NEW.provider_id OR OLD.store_id != NEW.store_id
             OR NEW.revision != OLD.revision + 1
        BEGIN SELECT RAISE(ABORT, 'authority head must advance once'); END;
        CREATE TRIGGER authority_head_retained BEFORE DELETE ON authority_heads
        BEGIN SELECT RAISE(ABORT, 'authority head is retained'); END;
    """)


def enroll_fresh(authority, witness_path):
    """Enroll an unused provider once. Never enroll a candidate recovery image."""
    service = WitnessedAuthority(authority, witness_path)
    with authority._connect() as db, write_transaction(db), service._witness() as witness:
        if any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in TABLES[2:]):
            raise AuthorityUnavailable("enrollment requires unused provider state")
        db.execute("""CREATE TABLE authority_revision (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1), revision INTEGER NOT NULL
        )""")
        db.execute("INSERT INTO authority_revision VALUES (1, 0)")
        with write_transaction(witness):
            witness.execute("INSERT INTO authority_heads VALUES (?, ?, 0, ?)",
                            (*service._identity, _commitment(db)))
    return service


@dataclass(frozen=True, slots=True)
class WitnessedAuthority:
    authority: ProviderAuthority
    witness_path: Path

    @property
    def _identity(self):
        return self.authority.provider_id, self.authority.store_id

    @contextmanager
    def _witness(self):
        path = Path(self.witness_path).resolve()
        if path == Path(self.authority.path).resolve():
            raise AuthorityUnavailable("witness must be a separate store")
        try:
            db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, autocommit=True, timeout=5)
        except sqlite3.Error as error:
            raise AuthorityUnavailable("independent witness unavailable") from error
        with closing(db):
            db.execute("PRAGMA synchronous = FULL")
            yield db

    def _head(self, witness):
        try:
            head = witness.execute("""SELECT revision, digest FROM authority_heads
                                      WHERE provider_id=? AND store_id=?""", self._identity).fetchone()
        except sqlite3.Error as error:
            raise AuthorityUnavailable("independent witness head cannot be read") from error
        if head is None:
            raise AuthorityUnavailable("provider has no independently witnessed state")
        return head

    def _matches(self, actual, expected):
        return actual == expected

    def _revision(self, db):
        try:
            row = db.execute("SELECT revision FROM authority_revision WHERE singleton=1").fetchone()
        except sqlite3.Error as error:
            raise StateMismatch("authority lacks its witnessed revision") from error
        if row is None:
            raise StateMismatch("authority lacks its witnessed revision")
        return row[0]

    def _advance(self, witness, head, candidate, transition):
        updated = witness.execute("""
            UPDATE authority_heads SET revision=revision+1, digest=?
            WHERE provider_id=? AND store_id=? AND revision=? AND digest=?
        """, (candidate, *self._identity, *head))
        if updated.rowcount != 1:
            raise StateMismatch("another authority update advanced the independent witness")

    @contextmanager
    def _session(self, transition=None):
        # Provider write lock serializes calls on one image. CAS also prevents a
        # second same-head image from committing a conflicting transition.
        with self.authority._connect() as db, write_transaction(db), self._witness() as witness:
            revision = self._revision(db)
            previous = _commitment(db)
            head = self._head(witness)
            if not self._matches((revision, previous), head):
                raise StateMismatch("authority revision or content differs from the independent witness")
            yield db, revision
            candidate = _commitment(db)
            if previous != candidate:
                with write_transaction(witness):
                    self._advance(witness, head, candidate, transition)
                # If the process exits here, serving the predecessor image is
                # unsafe: it could also be a restore after a successful commit.
                db.execute("UPDATE authority_revision SET revision=? WHERE singleton=1", (head[0] + 1,))

    def verify_current(self):
        """Check an offline-restored candidate before enabling traffic."""
        with self._session() as (_, revision):
            return revision

    def reserve(self, source, guard, request_id, stream_id):
        provider_id = self.authority.provider_id
        message = terminal_evidence._reservation_message(source, request_id, provider_id, stream_id)
        with self._session(("register", message.notice, None)) as (db, _):
            epoch = delivery._register_in_transaction(db, message.notice)
            if delivery.authority(db, message.notice) != (epoch, True):
                raise delivery.DispatchDenied("provider operation is closed")
        return terminal_evidence._claim_dispatch(guard, provider_id, message, epoch)

    def apply(self, dispatch):
        if dispatch.provider_id != self.authority.provider_id:
            raise DeliveryConflict("dispatch belongs to another provider")
        retention._integer(dispatch.epoch, 1)
        with self._session(("apply", dispatch.notice, dispatch.epoch)) as (db, _):
            return delivery._apply_in_transaction(db, dispatch.notice, dispatch.epoch)

    def issue(self, dispatch, key, *, terminal=False):
        transition = ("retire", dispatch.notice, dispatch.epoch) if terminal else None
        with self._session(transition) as (db, _):
            terminal_evidence._validate_issuer(db, dispatch, key)
            effect = (routing._retire_in_transaction(db, dispatch.notice, dispatch.epoch).effect if terminal
                      else delivery.reconcile(db, dispatch.notice))
            return terminal_evidence._attest(dispatch, key, effect, terminal)

    def refresh_projection(self, projection, dispatch):
        with self._session() as (db, _):
            return _refresh_projection(db, projection, dispatch)
