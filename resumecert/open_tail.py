"""Provider-qualified write-ahead claims for recovery images with an open tail.

An effect INSERT must win a durable independent claim first. A lost claim result
cannot authorize another INSERT. Positive receipts can be recovered or sealed
after the provider transaction commits. Unresolved claims remain unresolved.
"""

from contextlib import closing, contextmanager
from dataclasses import dataclass
import sqlite3
from urllib.parse import quote

from . import provider_failover as routing, retention, revocable_delivery as delivery
from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict, Notice


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    provider_id: str
    effect: delivery.RecordedEffect | None = None


def create_guard(connection):
    retention.create_guard(connection)
    connection.executescript("""
        CREATE TABLE open_tail_claims (
            provider_id TEXT NOT NULL,
            operation_key TEXT NOT NULL,
            stream_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (typeof(sequence) = 'integer' AND sequence > 0),
            payload_digest TEXT NOT NULL,
            accepted_epoch INTEGER NOT NULL CHECK (typeof(accepted_epoch) = 'integer' AND accepted_epoch > 0),
            effect_id INTEGER CHECK (effect_id IS NULL OR (typeof(effect_id) = 'integer' AND effect_id > 0)),
            PRIMARY KEY (provider_id, operation_key),
            UNIQUE (provider_id, stream_id, sequence)
        );
        CREATE TRIGGER claim_identity_cannot_change BEFORE UPDATE ON open_tail_claims
        WHEN NEW.provider_id != OLD.provider_id OR NEW.operation_key != OLD.operation_key
          OR NEW.stream_id != OLD.stream_id OR NEW.sequence != OLD.sequence
          OR NEW.payload_digest != OLD.payload_digest OR NEW.accepted_epoch != OLD.accepted_epoch
          OR (OLD.effect_id IS NOT NULL AND NEW.effect_id IS NOT OLD.effect_id)
        BEGIN SELECT RAISE(ABORT, 'claim identity and recorded receipt cannot change'); END;
        CREATE TRIGGER claim_delete_requires_closed_prefix BEFORE DELETE ON open_tail_claims
        WHEN NOT EXISTS (
            SELECT 1 FROM retention_anchors WHERE provider_id = OLD.provider_id
              AND stream_id = OLD.stream_id AND closed_through >= OLD.sequence
        )
        BEGIN SELECT RAISE(ABORT, 'open claim cannot be deleted'); END;
    """)


def create_provider(connection, provider_id):
    retention.create_provider(connection, provider_id)
    connection.executescript("""
        CREATE TRIGGER effect_requires_independent_claim BEFORE INSERT ON handoff_effects
        WHEN claim_new_effect(NEW.operation_key, NEW.payload, NEW.accepted_epoch,
             (SELECT stream_id FROM retention_bindings WHERE operation_key = NEW.operation_key),
             (SELECT sequence FROM retention_bindings WHERE operation_key = NEW.operation_key)) != 1
        BEGIN SELECT RAISE(ABORT, 'open-tail claim already exists'); END;
    """)


@contextmanager
def _fresh_guard(guard, *, write=False):
    if guard is None or guard.in_transaction:
        raise retention.GuardUnavailable("fresh independent claim storage is required")
    path = next(row[2] for row in guard.execute("PRAGMA database_list") if row[1] == "main")
    if not path:
        raise retention.GuardUnavailable("claims require independent durable storage")
    mode = "rw" if write else "ro"
    with closing(sqlite3.connect(f"file:{quote(path, safe='/')}?mode={mode}", uri=True,
                                 autocommit=True, timeout=5)) as current:
        yield current


def _floor(current, provider_id, message):
    row = current.execute("""
        SELECT closed_through FROM retention_anchors WHERE provider_id = ? AND stream_id = ?
    """, (provider_id, message.stream_id)).fetchone()
    if row is None:
        raise retention.GuardUnavailable("provider stream has no independent anchor")
    return row[0]


def _claim(current, provider_id, message):
    row = current.execute("""
        SELECT stream_id, sequence, payload_digest, accepted_epoch, effect_id
        FROM open_tail_claims WHERE provider_id = ? AND operation_key = ?
    """, (provider_id, message.notice.operation_key)).fetchone()
    if row is not None and row[:3] != (message.stream_id, message.sequence, message.notice.payload_digest):
        raise DeliveryConflict("independent claim binds another notice")
    return row


def _claim_new_effect(guard, provider_id, key, payload, epoch, stream, sequence):
    message = retention.StreamNotice(stream, sequence, Notice(key, payload))
    retention._integer(epoch, 1)
    with _fresh_guard(guard, write=True) as current, write_transaction(current):
        if sequence <= _floor(current, provider_id, message):
            raise retention.PrefixClosed("claim belongs to a closed prefix")
        if _claim(current, provider_id, message) is not None:
            return 0
        current.execute("INSERT INTO open_tail_claims VALUES (?, ?, ?, ?, ?, ?, NULL)",
                        (provider_id, key, stream, sequence, message.notice.payload_digest, epoch))
        return 1


def configure(connection, guard):
    """Protect new effect INSERTs, including direct calls through the older adapter."""
    retention.configure(connection, guard)
    provider_id = routing.identity(connection)
    connection.create_function("claim_new_effect", 5,
                               lambda *args: _claim_new_effect(guard, provider_id, *args))


def _recorded(provider_id, message, row):
    return Outcome("recorded", provider_id, delivery._recorded(message.notice, row[4], row[3]))


def _state(connection, guard, message):
    provider_id = routing.identity(connection)
    if message.sequence <= retention.closed_through(connection, guard, message.stream_id):
        return Outcome("forgotten", provider_id)
    with _fresh_guard(guard) as current:
        row = _claim(current, provider_id, message)
    if row is None:
        return Outcome("unattempted", provider_id)
    if row[4] is not None:
        return _recorded(provider_id, message, row)
    try:
        effect = delivery.reconcile(connection, message.notice)
    except delivery.DispatchDenied:
        effect = None
    if effect is None:
        return Outcome("unknown", provider_id)
    if effect.accepted_epoch != row[3]:
        raise DeliveryConflict("provider receipt differs from the independent claim")
    return Outcome("needs_seal", provider_id, effect)


def _seal(guard, provider_id, message, effect):
    """Record a matching historical receipt only after the provider COMMIT succeeded."""
    with _fresh_guard(guard, write=True) as current, write_transaction(current):
        if message.sequence <= _floor(current, provider_id, message):
            return Outcome("forgotten", provider_id)
        row = _claim(current, provider_id, message)
        if (row is None or row[3] != effect.accepted_epoch
                or effect.receipt.operation_key != message.notice.operation_key
                or effect.receipt.payload_digest != message.notice.payload_digest
                or (row[4] is not None and row[4] != effect.receipt.effect_id)):
            raise DeliveryConflict("committed receipt does not match the independent claim")
        current.execute("""
            UPDATE open_tail_claims SET effect_id = ? WHERE provider_id = ? AND operation_key = ?
        """, (effect.receipt.effect_id, provider_id, message.notice.operation_key))
        return Outcome("recorded", provider_id, effect)


def recover(connection, guard, message):
    """Recover positive evidence; a claim with no surviving receipt remains unknown."""
    configure(connection, guard)
    with write_transaction(connection):
        state = _state(connection, guard, message)
    if state.status == "needs_seal":
        return _seal(guard, state.provider_id, message, state.effect)
    return state


def apply(connection, guard, message, dispatch):
    """Try once through an independent claim or recover an existing recorded result."""
    provider_id = routing.identity(connection)
    if dispatch.provider_id != provider_id or dispatch.notice != message.notice:
        raise DeliveryConflict("dispatch does not match this provider and notice")
    retention._integer(dispatch.epoch, 1)
    configure(connection, guard)
    try:
        with write_transaction(connection):
            state = _state(connection, guard, message)
            if state.status == "forgotten":
                raise retention.PrefixClosed("operation belongs to a closed prefix")
            if state.status in ("recorded", "unknown"):
                return state
            effect = state.effect if state.status == "needs_seal" else delivery._apply_in_transaction(
                connection, message.notice, dispatch.epoch)
    except sqlite3.IntegrityError as error:
        if str(error) != "open-tail claim already exists":
            raise
        return recover(connection, guard, message)
    # The target's COMMIT precedes receipt sealing. A crash here leaves a claim.
    return _seal(guard, provider_id, message, effect)


def close_prefix(connection, guard, stream_id, through):
    """Compose existing irreversible closure with independent claim reclamation."""
    deleted = retention.close_prefix(connection, guard, stream_id, through)
    provider_id = routing.identity(connection)
    with _fresh_guard(guard, write=True) as current, write_transaction(current):
        deleted += current.execute("""
            DELETE FROM open_tail_claims WHERE provider_id = ? AND stream_id = ? AND sequence <= ?
        """, (provider_id, stream_id, through)).rowcount
    return deleted
