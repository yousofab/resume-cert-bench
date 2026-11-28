"""Restore a witnessed provider by replaying a retained suffix in one transaction."""
from dataclasses import dataclass
import sqlite3

from .archive_transfer import write_transaction
from .authority_redo import _encode_transition, _replay_transition
from .witnessed_authority import StateMismatch, WitnessedAuthority, _commitment


def create_redo_chain(witness):
    witness.execute("""
        CREATE TABLE authority_redo_chain (
            provider_id TEXT NOT NULL, store_id TEXT NOT NULL, revision INTEGER NOT NULL,
            predecessor_digest TEXT NOT NULL, successor_digest TEXT NOT NULL, command TEXT NOT NULL,
            PRIMARY KEY (provider_id, store_id, revision)
        ) WITHOUT ROWID
    """)


@dataclass(frozen=True, slots=True)
class ChainRecovery:
    revision: int
    replayed: int


@dataclass(frozen=True)
class BoundedRedoAuthority(WitnessedAuthority):
    """Keep up to capacity changes per provider, with their exact state commitments."""

    capacity: int

    def __post_init__(self):
        if type(self.capacity) is not int or not 1 <= self.capacity <= 2**63 - 1:
            raise ValueError("redo capacity must be a positive SQLite integer")

    def _advance(self, witness, head, candidate, transition):
        command = _encode_transition(transition)
        super()._advance(witness, head, candidate, transition)
        revision = head[0] + 1
        witness.execute("INSERT INTO authority_redo_chain VALUES (?, ?, ?, ?, ?, ?)",
                        (*self._identity, revision, head[1], candidate, command))
        witness.execute("""
            DELETE FROM authority_redo_chain
            WHERE provider_id=? AND store_id=? AND revision<=?
        """, (*self._identity, revision - self.capacity))

    def recover(self):
        """Reconstruct the observed witness head or leave the candidate as it was."""
        with self.authority._connect() as db, write_transaction(db), self._witness() as witness:
            revision, previous = self._revision(db), _commitment(db)
            witness.execute("BEGIN")
            try:
                head = self._head(witness)
                if type(revision) is not int or revision < 0 or type(head[0]) is not int or head[0] < 0:
                    raise StateMismatch("invalid authority or witness revision")
                if (revision, previous) == head:
                    return ChainRecovery(revision, 0)
                gap = head[0] - revision
                if not 1 <= gap <= self.capacity:
                    raise StateMismatch("candidate is outside the redo window or has divergent content")
                links = witness.execute("""
                    SELECT revision, predecessor_digest, successor_digest, command
                    FROM authority_redo_chain
                    WHERE provider_id=? AND store_id=? AND revision>? AND revision<=?
                    ORDER BY revision
                """, (*self._identity, revision, head[0])).fetchall()
            except sqlite3.Error as error:
                raise StateMismatch("redo chain unavailable") from error
            finally:
                witness.execute("ROLLBACK")

            if len(links) != gap:
                raise StateMismatch("redo chain is incomplete")
            for expected, (number, predecessor, successor, _) in enumerate(links, revision + 1):
                if (number, predecessor) != (expected, previous):
                    raise StateMismatch("redo chain is discontinuous")
                previous = successor
            if previous != head[1]:
                raise StateMismatch("redo chain does not reach the witnessed head")

            for _, _, successor, command in links:
                _replay_transition(db, command, successor)
            db.execute("UPDATE authority_revision SET revision=? WHERE singleton=1", (head[0],))
            return ChainRecovery(head[0], gap)
