"""Reconstruct a witnessed local SQLite transition from its exact predecessor."""
from dataclasses import dataclass
import json
import sqlite3

from . import provider_failover as routing, revocable_delivery as delivery
from .archive_transfer import write_transaction
from .effect_delivery import Notice, encode
from .witnessed_authority import StateMismatch, WitnessedAuthority, _commitment


def create_redo_log(witness):
    """Provision once on the trusted witness. Existing heads gain redo on their next change."""
    witness.execute("""
        CREATE TABLE authority_redo (
            provider_id TEXT NOT NULL, store_id TEXT NOT NULL, revision INTEGER NOT NULL,
            predecessor_digest TEXT NOT NULL, successor_digest TEXT NOT NULL, command TEXT NOT NULL,
            PRIMARY KEY (provider_id, store_id)
        ) WITHOUT ROWID
    """)


@dataclass(frozen=True, slots=True)
class Recovery:
    revision: int
    repaired: bool


def _decode(command):
    try:
        version, kind, key, payload, epoch = json.loads(command)
        if (version != "resume-cert-redo/v1" or type(key) is not str or type(payload) is not str
                or type(kind) is not str
                or command != encode([version, kind, key, payload, epoch])):
            raise ValueError
        if kind == "register" and epoch is None:
            return kind, Notice(key, payload), epoch
        if kind in ("apply", "retire") and type(epoch) is int and 1 <= epoch <= 2**63 - 2:
            return kind, Notice(key, payload), epoch
    except (TypeError, ValueError):
        pass
    raise StateMismatch("invalid or unsupported redo command")


def _encode_transition(transition):
    if transition is None:
        raise StateMismatch("state-changing session has no redo command")
    kind, notice, epoch = transition
    command = encode(["resume-cert-redo/v1", kind, notice.operation_key, notice.payload, epoch])
    _decode(command)
    return command


def _replay_transition(db, command, successor_digest):
    kind, notice, epoch = _decode(command)
    try:
        if kind == "register":
            delivery._register_in_transaction(db, notice)
        elif kind == "apply":
            delivery._apply_in_transaction(db, notice, epoch)
        else:
            routing._retire_in_transaction(db, notice, epoch)
    except (ValueError, sqlite3.Error) as error:
        raise StateMismatch("stored transition cannot be reconstructed") from error
    if _commitment(db) != successor_digest:
        raise StateMismatch("reconstructed state differs from witnessed successor")


class RecoverableAuthority(WitnessedAuthority):
    """Retain the latest typed redo command atomically with the witness head."""

    def _advance(self, witness, head, candidate, transition):
        command = _encode_transition(transition)
        super()._advance(witness, head, candidate, transition)
        witness.execute("""
            INSERT INTO authority_redo VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, store_id) DO UPDATE SET
                revision=excluded.revision, predecessor_digest=excluded.predecessor_digest,
                successor_digest=excluded.successor_digest, command=excluded.command
        """, (*self._identity, head[0] + 1, head[1], candidate, command))

    def recover(self):
        """Materialize an already witnessed transition with local SQL only."""
        with self.authority._connect() as db, write_transaction(db), self._witness() as witness:
            # Head and recipe must come from the same witness snapshot. The
            # provider lock is acquired first, as it is on the normal path.
            witness.execute("BEGIN")
            try:
                head = self._head(witness)
                recipe = None
                revision, state = self._revision(db), _commitment(db)
                if (revision, state) != head:
                    recipe = witness.execute("""
                        SELECT revision, predecessor_digest, successor_digest, command
                        FROM authority_redo WHERE provider_id=? AND store_id=?
                    """, self._identity).fetchone()
            except sqlite3.Error as error:
                raise StateMismatch("redo record unavailable") from error
            finally:
                witness.execute("ROLLBACK")
            if (revision, state) == head:
                return Recovery(revision, False)
            if (recipe is None or head[0] != revision + 1
                    or recipe[:3] != (head[0], state, head[1])):
                raise StateMismatch("the exact witnessed predecessor and redo record are required")
            _replay_transition(db, recipe[3], head[1])
            db.execute("UPDATE authority_revision SET revision=? WHERE singleton=1", (head[0],))
            return Recovery(head[0], True)
