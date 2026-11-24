"""Keep provider effects and retirement in a store outside the projection's restore domain."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from . import provider_failover as routing, revocable_delivery as delivery, terminal_evidence
from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict


class AuthorityUnavailable(RuntimeError):
    """The configured provider authority cannot be established."""


def create_authority(connection, provider_id, store_id):
    """Provision an empty provider system of record in its own restore domain."""
    if type(store_id) is not str or not store_id.strip():
        raise ValueError("store identity must be nonempty")
    routing.create_provider(connection, provider_id)
    connection.execute("""CREATE TABLE authority_identity (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1), store_id TEXT NOT NULL
    )""")
    connection.execute("INSERT INTO authority_identity VALUES (1, ?)", (store_id,))


def create_projection(connection):
    connection.execute("""CREATE TABLE provider_projection (
        provider_id TEXT NOT NULL, operation_key TEXT NOT NULL, payload_digest TEXT NOT NULL,
        epoch INTEGER NOT NULL, status TEXT NOT NULL, effect_id INTEGER,
        PRIMARY KEY (provider_id, operation_key)
    )""")


@dataclass(frozen=True, slots=True)
class ProviderAuthority:
    """Trusted service configuration, held separately from restorable projection data."""

    path: Path
    provider_id: str
    store_id: str

    @contextmanager
    def _connect(self):
        uri = Path(self.path).resolve().as_uri() + "?mode=rw"
        try:
            connection = sqlite3.connect(uri, uri=True, autocommit=True, timeout=5)
        except sqlite3.Error as error:
            raise AuthorityUnavailable("provider authority cannot be opened") from error
        with closing(connection):
            try:
                provider = routing.identity(connection)
                row = connection.execute("SELECT store_id FROM authority_identity WHERE singleton = 1").fetchone()
            except (sqlite3.Error, TypeError) as error:
                raise AuthorityUnavailable("provider authority identity cannot be read") from error
            if provider != self.provider_id or row != (self.store_id,):
                raise AuthorityUnavailable("provider authority differs from the pinned identity")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection

    def reserve(self, source, guard, request_id, stream_id):
        with self._connect() as authority:
            return terminal_evidence.reserve(source, guard, request_id, authority, stream_id)

    def apply(self, dispatch):
        """The retained SQLite effect row is the actual inert effect and its receipt."""
        with self._connect() as authority:
            return routing.apply_dispatch(authority, dispatch)

    def issue(self, dispatch, key, *, terminal=False):
        """Only the retained provider authority can issue an outcome or retirement."""
        with self._connect() as authority:
            return terminal_evidence.issue(authority, dispatch, key, terminal=terminal)

    def refresh_projection(self, projection, dispatch):
        """Rebuild disposable status from the provider, without creating an effect."""
        if dispatch.provider_id != self.provider_id:
            raise DeliveryConflict("projection request is bound to another provider")
        with self._connect() as authority, write_transaction(authority):
            notice = dispatch.notice
            effect = delivery.reconcile(authority, notice)
            fence = routing.retirement(authority, notice)
            if effect is not None and effect.accepted_epoch != dispatch.epoch:
                raise DeliveryConflict("receipt belongs to another dispatch generation")
            if fence is not None and fence.retired_epoch != dispatch.epoch:
                raise DeliveryConflict("retirement belongs to another dispatch generation")
            status = "recorded" if effect is not None else "retired_empty" if fence is not None else "unresolved"
            # Serialize materialization in the same order as authority reads. The
            # projection is never an input to dispatch or evidence issuance.
            with write_transaction(projection):
                projection.execute("""
                    INSERT INTO provider_projection VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id, operation_key) DO UPDATE SET
                        payload_digest=excluded.payload_digest, epoch=excluded.epoch,
                        status=excluded.status, effect_id=excluded.effect_id
                """, (self.provider_id, notice.operation_key, notice.payload_digest, dispatch.epoch,
                      status, effect.receipt.effect_id if effect is not None else None))
        return status
