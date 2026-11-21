"""Close a source prefix before reclaiming recovery metadata at a provider.

The independent guard must survive restoration of the provider's recovery image.
Trusted administrators enroll streams and authorize irreversible prefix closure.
All mutations serialize on one active provider database, including after restore.
"""

from contextlib import closing
from dataclasses import dataclass
import sqlite3
from urllib.parse import quote

from . import provider_failover as routing, revocable_delivery as delivery
from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict, Notice, digest, encode


class PrefixClosed(delivery.DispatchDenied):
    """This sequence has permanently lost permission to create another effect."""


class GuardUnavailable(RuntimeError):
    """The independent closure boundary cannot be established."""


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError("invalid stream sequence")


@dataclass(frozen=True, slots=True)
class StreamNotice:
    stream_id: str
    sequence: int
    notice: Notice

    def __post_init__(self):
        if type(self.stream_id) is not str or not self.stream_id.strip():
            raise ValueError("a stable source namespace is required")
        _integer(self.sequence, 1)
        if type(self.notice) is not Notice or self.notice.operation_key != digest(
                encode(["archive-notice/v1", self.stream_id, self.sequence])):
            raise DeliveryConflict("notice identity does not bind this source and sequence")


@dataclass(frozen=True, slots=True)
class Resolution:
    status: str
    effect: delivery.RecordedEffect | None = None


def create_guard(connection):
    connection.executescript("""
        CREATE TABLE retention_anchors (
            provider_id TEXT NOT NULL,
            stream_id TEXT NOT NULL,
            closed_through INTEGER NOT NULL CHECK (typeof(closed_through) = 'integer' AND closed_through >= 0),
            PRIMARY KEY (provider_id, stream_id)
        );
        CREATE TRIGGER anchor_cannot_rewind BEFORE UPDATE ON retention_anchors
        WHEN NEW.provider_id != OLD.provider_id OR NEW.stream_id != OLD.stream_id
             OR NEW.closed_through < OLD.closed_through
        BEGIN SELECT RAISE(ABORT, 'closure anchor cannot rewind'); END;
        CREATE TRIGGER anchor_cannot_delete BEFORE DELETE ON retention_anchors
        BEGIN SELECT RAISE(ABORT, 'closure anchor cannot be deleted'); END;
    """)


def create_provider(connection, provider_id):
    routing.create_provider(connection, provider_id)
    connection.executescript("""
        CREATE TABLE retention_streams (
            stream_id TEXT PRIMARY KEY,
            closed_through INTEGER NOT NULL CHECK (typeof(closed_through) = 'integer' AND closed_through >= 0)
        );
        CREATE TABLE retention_bindings (
            operation_key TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL REFERENCES retention_streams(stream_id),
            sequence INTEGER NOT NULL CHECK (typeof(sequence) = 'integer' AND sequence > 0),
            UNIQUE (stream_id, sequence)
        );
        CREATE TRIGGER local_floor_cannot_rewind BEFORE UPDATE ON retention_streams
        WHEN NEW.stream_id != OLD.stream_id OR NEW.closed_through < OLD.closed_through
        BEGIN SELECT RAISE(ABORT, 'local closure floor cannot rewind'); END;
        CREATE TRIGGER local_floor_cannot_delete BEFORE DELETE ON retention_streams
        BEGIN SELECT RAISE(ABORT, 'local closure floor cannot be deleted'); END;
        CREATE TRIGGER binding_cannot_change BEFORE UPDATE ON retention_bindings
        BEGIN SELECT RAISE(ABORT, 'operation sequence cannot change'); END;
    """)
    # An unconfigured connection lacks retention_guard_floor and cannot write effects.
    # All three original mutation paths remain guarded even if called directly.
    for table, event in (("handoff_authority", "INSERT"), ("handoff_authority", "UPDATE"),
                         ("handoff_effects", "INSERT")):
        connection.executescript(f"""
            CREATE TRIGGER retention_{table}_{event.lower()} BEFORE {event} ON {table}
            WHEN NOT EXISTS (
                SELECT 1 FROM retention_bindings b JOIN retention_streams s USING (stream_id)
                WHERE b.operation_key = NEW.operation_key
                  AND b.sequence > max(s.closed_through, retention_guard_floor(b.stream_id))
            )
            BEGIN SELECT RAISE(ABORT, 'operation prefix is closed or unbound'); END;
        """)


def _anchor(guard, provider_id, stream_id):
    if guard is None:
        raise GuardUnavailable("independent guard is required")
    # A caller-owned read transaction could pin an obsolete anchor snapshot.
    if guard.in_transaction:
        raise GuardUnavailable("guard must not hold an earlier read transaction")
    # A pending SELECT can retain a WAL snapshot even when in_transaction is false.
    # Use a fresh read-only connection so neither kind of old snapshot is trusted.
    path = next(row[2] for row in guard.execute("PRAGMA database_list") if row[1] == "main")
    if not path:
        raise GuardUnavailable("guard must have independent durable storage")
    try:
        with closing(sqlite3.connect(f"file:{quote(path, safe='/')}?mode=ro", uri=True,
                                     autocommit=True, timeout=5)) as current:
            row = current.execute("""
                SELECT closed_through FROM retention_anchors WHERE provider_id = ? AND stream_id = ?
            """, (provider_id, stream_id)).fetchone()
    except sqlite3.Error as error:
        raise GuardUnavailable("independent guard cannot be read") from error
    if row is None:
        raise GuardUnavailable("provider stream has no independent closure anchor")
    return row[0]


def configure(connection, guard):
    """Install a fresh independent guard read at each SQL mutation boundary."""
    provider_id = routing.identity(connection)
    connection.create_function("retention_guard_floor", 1,
                               lambda stream: _anchor(guard, provider_id, stream))


def enroll_stream(connection, guard, stream_id):
    """Trusted first-time enrollment; an existing stream cannot recreate a lost guard."""
    if type(stream_id) is not str or not stream_id.strip():
        raise ValueError("a stable source namespace is required")
    if guard is None or guard.in_transaction:
        raise GuardUnavailable("fresh independent guard access is required")
    provider_id = routing.identity(connection)
    with write_transaction(connection):
        existing = connection.execute("SELECT 1 FROM retention_streams WHERE stream_id = ?",
                                      (stream_id,)).fetchone()
        if existing is not None:
            _anchor(guard, provider_id, stream_id)
        else:
            with write_transaction(guard):
                guard.execute("INSERT INTO retention_anchors VALUES (?, ?, 0) ON CONFLICT DO NOTHING",
                              (provider_id, stream_id))
            connection.execute("INSERT INTO retention_streams VALUES (?, ?)",
                               (stream_id, _anchor(guard, provider_id, stream_id)))
    configure(connection, guard)


def closed_through(connection, guard, stream_id):
    row = connection.execute("SELECT closed_through FROM retention_streams WHERE stream_id = ?",
                             (stream_id,)).fetchone()
    if row is None:
        raise GuardUnavailable("source stream has not been enrolled")
    return max(row[0], _anchor(guard, routing.identity(connection), stream_id))


def _check_open(connection, guard, message):
    if message.sequence <= closed_through(connection, guard, message.stream_id):
        raise PrefixClosed("operation belongs to an irreversibly closed prefix")


def bind(connection, guard, message):
    """Bind a trusted authorized notice before using the existing provider API."""
    configure(connection, guard)
    with write_transaction(connection):
        _check_open(connection, guard, message)
        connection.execute("INSERT INTO retention_bindings VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                           (message.notice.operation_key, message.stream_id, message.sequence))
        stored = connection.execute("""
            SELECT stream_id, sequence FROM retention_bindings WHERE operation_key = ?
        """, (message.notice.operation_key,)).fetchone()
        if stored != (message.stream_id, message.sequence):
            raise DeliveryConflict("stored operation sequence conflicts with the notice")


def register(connection, guard, message):
    bind(connection, guard, message)
    with write_transaction(connection):
        _check_open(connection, guard, message)
        epoch = delivery._register_in_transaction(connection, message.notice)
        if delivery.authority(connection, message.notice) != (epoch, True):
            raise delivery.DispatchDenied("provider operation is closed")
        return routing.Dispatch(routing.identity(connection), message.notice, epoch)


def apply(connection, guard, message, dispatch):
    if message.notice != dispatch.notice or routing.identity(connection) != dispatch.provider_id:
        raise DeliveryConflict("dispatch does not match the provider and bound notice")
    _integer(dispatch.epoch, 1)
    configure(connection, guard)
    with write_transaction(connection):
        _check_open(connection, guard, message)
        return delivery._apply_in_transaction(connection, message.notice, dispatch.epoch)


def lookup(connection, guard, message):
    """A closed prefix erases outcome knowledge and never asserts absence of an effect."""
    if message.sequence <= closed_through(connection, guard, message.stream_id):
        return Resolution("forgotten")
    try:
        effect = delivery.reconcile(connection, message.notice)
    except delivery.DispatchDenied:
        return Resolution("unregistered")
    return Resolution("recorded", effect) if effect is not None else Resolution("retained_empty")


def close_prefix(connection, guard, stream_id, through):
    """Irreversibly stop all <= through, including unseen work, then reclaim its metadata.

    The guard commits first while the provider writer is locked. If the provider
    rolls back or crashes afterwards, every new admission reads the advanced guard.
    A single active provider image and an independently retained guard are required.
    """
    _integer(through)
    provider_id = routing.identity(connection)
    with write_transaction(connection):
        current = closed_through(connection, guard, stream_id)
        if through < current:
            raise ValueError("closure boundary cannot rewind")
        with write_transaction(guard):
            guard.execute("""
                UPDATE retention_anchors SET closed_through = ? WHERE provider_id = ? AND stream_id = ?
            """, (through, provider_id, stream_id))
        connection.execute("UPDATE retention_streams SET closed_through = ? WHERE stream_id = ?",
                           (through, stream_id))
        # Actual downstream effects are represented by a separate evaluation oracle.
        # These rows are the provider's recovery projection and terminal fences.
        deleted = 0
        for table in ("handoff_fences", "permanent_retirements", "handoff_effects", "handoff_authority"):
            deleted += connection.execute(f"""
                DELETE FROM {table} WHERE operation_key IN (
                    SELECT operation_key FROM retention_bindings WHERE stream_id = ? AND sequence <= ?
                )
            """, (stream_id, through)).rowcount
        deleted += connection.execute("DELETE FROM retention_bindings WHERE stream_id = ? AND sequence <= ?",
                                      (stream_id, through)).rowcount
        return deleted
