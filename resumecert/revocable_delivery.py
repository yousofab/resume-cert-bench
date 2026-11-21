"""Destination-owned dispatch authority for an inert archive handoff."""

from dataclasses import dataclass

from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict, InboxReceipt


class DispatchDenied(ValueError):
    """The supplied generation cannot create a destination effect."""


@dataclass(frozen=True, slots=True)
class RecordedEffect:
    receipt: InboxReceipt
    accepted_epoch: int


@dataclass(frozen=True, slots=True)
class Fence:
    operation_key: str
    payload_digest: str
    retired_epoch: int
    closed_epoch: int
    effect: RecordedEffect | None


def create_destination(connection):
    connection.executescript("""
        CREATE TABLE handoff_authority (
            operation_key TEXT PRIMARY KEY,
            payload_digest TEXT NOT NULL,
            epoch INTEGER NOT NULL CHECK (typeof(epoch) = 'integer' AND epoch > 0),
            active INTEGER NOT NULL CHECK (active IN (0, 1))
        );
        CREATE TABLE handoff_effects (
            effect_id INTEGER PRIMARY KEY,
            operation_key TEXT NOT NULL UNIQUE,
            payload_digest TEXT NOT NULL,
            payload TEXT NOT NULL,
            accepted_epoch INTEGER NOT NULL
        );
        CREATE TRIGGER handoff_require_current_authority BEFORE INSERT ON handoff_effects
        WHEN NOT EXISTS (
            SELECT 1 FROM handoff_authority WHERE operation_key = NEW.operation_key
                AND payload_digest = NEW.payload_digest AND epoch = NEW.accepted_epoch AND active = 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'handoff authority is stale or closed');
        END;
        CREATE TABLE handoff_fences (
            operation_key TEXT NOT NULL,
            retired_epoch INTEGER NOT NULL,
            closed_epoch INTEGER NOT NULL,
            effect_id INTEGER,
            effect_epoch INTEGER,
            PRIMARY KEY (operation_key, retired_epoch),
            CHECK (closed_epoch = retired_epoch + 1),
            CHECK ((effect_id IS NULL) = (effect_epoch IS NULL))
        );
    """)


def register(connection, notice):
    """Bind a stable key to one payload and initialize the first authorization."""
    with write_transaction(connection):
        return _register_in_transaction(connection, notice)


def _register_in_transaction(connection, notice):
    connection.execute("""
        INSERT INTO handoff_authority VALUES (?, ?, 1, 1)
        ON CONFLICT(operation_key) DO NOTHING
    """, (notice.operation_key, notice.payload_digest))
    return authority(connection, notice)[0]


def authority(connection, notice):
    row = connection.execute("""
        SELECT payload_digest, epoch, active FROM handoff_authority WHERE operation_key = ?
    """, (notice.operation_key,)).fetchone()
    if row is None:
        raise DispatchDenied("destination has no matching authority")
    if row[0] != notice.payload_digest:
        raise DeliveryConflict("destination authority is bound to another payload")
    return row[1], bool(row[2])


def _recorded(notice, effect_id, epoch):
    if effect_id is None:
        return None
    return RecordedEffect(InboxReceipt(notice.operation_key, notice.payload_digest, effect_id), epoch)


def reconcile(connection, notice):
    """Read under separate trusted reconciliation authority, including after revocation."""
    authority(connection, notice)
    row = connection.execute("""
        SELECT effect_id, accepted_epoch FROM handoff_effects WHERE operation_key = ?
    """, (notice.operation_key,)).fetchone()
    return _recorded(notice, *row) if row is not None else None


def fence_status(connection, notice, retired_epoch):
    """Read the immutable observation captured at this generation's fence."""
    authority(connection, notice)
    row = connection.execute("""
        SELECT closed_epoch, effect_id, effect_epoch FROM handoff_fences
        WHERE operation_key = ? AND retired_epoch = ?
    """, (notice.operation_key, retired_epoch)).fetchone()
    if row is None:
        return None
    return Fence(notice.operation_key, notice.payload_digest, retired_epoch, row[0],
                 _recorded(notice, row[1], row[2]))


def revoke(connection, notice, expected_epoch):
    """Close one generation and record whether a receipt already exists in the same transaction."""
    if type(expected_epoch) is not int or expected_epoch < 1:
        raise ValueError("invalid expected epoch")
    with write_transaction(connection):
        return _revoke_in_transaction(connection, notice, expected_epoch)


def _revoke_in_transaction(connection, notice, expected_epoch):
    previous = fence_status(connection, notice, expected_epoch)
    if previous is not None:
        return previous
    if authority(connection, notice) != (expected_epoch, True):
        raise DispatchDenied("revocation does not match the active generation")
    effect = reconcile(connection, notice)
    closed_epoch = expected_epoch + 1
    connection.execute("UPDATE handoff_authority SET epoch = ?, active = 0 WHERE operation_key = ?",
                       (closed_epoch, notice.operation_key))
    connection.execute("INSERT INTO handoff_fences VALUES (?, ?, ?, ?, ?)",
                       (notice.operation_key, expected_epoch, closed_epoch,
                        effect.receipt.effect_id if effect else None,
                        effect.accepted_epoch if effect else None))
    return Fence(notice.operation_key, notice.payload_digest, expected_epoch, closed_epoch, effect)


def regrant(connection, notice, closed_epoch):
    """An explicit new approval creates a fresh generation for the same operation and content."""
    if type(closed_epoch) is not int or closed_epoch < 2:
        raise ValueError("invalid closed epoch")
    with write_transaction(connection):
        current = authority(connection, notice)
        if current == (closed_epoch + 1, True):
            return current[0]
        if current != (closed_epoch, False):
            raise DispatchDenied("new approval does not match the destination fence")
        new_epoch = closed_epoch + 1
        connection.execute("UPDATE handoff_authority SET epoch = ?, active = 1 WHERE operation_key = ?",
                           (new_epoch, notice.operation_key))
        return new_epoch


def apply(connection, notice, epoch):
    """Create an effect only under the current epoch, or return its existing receipt."""
    if type(epoch) is not int or epoch < 1:
        raise ValueError("invalid dispatch epoch")
    with write_transaction(connection):
        return _apply_in_transaction(connection, notice, epoch)


def _apply_in_transaction(connection, notice, epoch):
    if authority(connection, notice) != (epoch, True):
        raise DispatchDenied("dispatch authority is stale or closed")
    effect = reconcile(connection, notice)
    if effect is not None:
        return effect
    row = connection.execute("""
        INSERT INTO handoff_effects (operation_key, payload_digest, payload, accepted_epoch)
        VALUES (?, ?, ?, ?)
    """, (notice.operation_key, notice.payload_digest, notice.payload, epoch))
    return _recorded(notice, row.lastrowid, epoch)
