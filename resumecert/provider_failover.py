"""Transfer one authorized, inert handoff between independent SQLite providers."""

from dataclasses import dataclass

from . import revocable_delivery as destination
from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict, Notice, _acknowledge_in_transaction


@dataclass(frozen=True, slots=True)
class Dispatch:
    provider_id: str
    notice: Notice
    epoch: int


def create_provider(connection, provider_id):
    if type(provider_id) is not str or not provider_id.strip():
        raise ValueError("provider identity must be nonempty")
    destination.create_destination(connection)
    connection.executescript("""
        CREATE TABLE provider_identity (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            provider_id TEXT NOT NULL
        );
        CREATE TABLE permanent_retirements (
            operation_key TEXT PRIMARY KEY,
            retired_epoch INTEGER NOT NULL
        );
        CREATE TRIGGER retired_authority_cannot_reopen BEFORE UPDATE ON handoff_authority
        WHEN EXISTS (SELECT 1 FROM permanent_retirements WHERE operation_key = OLD.operation_key)
        BEGIN
            SELECT RAISE(ABORT, 'retired provider operation cannot reopen');
        END;
        CREATE TRIGGER retired_operation_cannot_write BEFORE INSERT ON handoff_effects
        WHEN EXISTS (SELECT 1 FROM permanent_retirements WHERE operation_key = NEW.operation_key)
        BEGIN
            SELECT RAISE(ABORT, 'retired provider operation cannot write');
        END;
    """)
    connection.execute("INSERT INTO provider_identity VALUES (1, ?)", (provider_id,))


def identity(connection):
    return connection.execute("SELECT provider_id FROM provider_identity WHERE singleton = 1").fetchone()[0]


def retirement(connection, notice):
    row = connection.execute("""
        SELECT retired_epoch FROM permanent_retirements WHERE operation_key = ?
    """, (notice.operation_key,)).fetchone()
    if row is None:
        return None
    fence = destination.fence_status(connection, notice, row[0])
    if fence is None or destination.authority(connection, notice) != (fence.closed_epoch, False):
        raise RuntimeError("provider has an inconsistent retirement")
    return fence


def retire(connection, notice, expected_epoch):
    """Atomically close dispatch and permanently retire this operation at this provider."""
    if type(expected_epoch) is not int or expected_epoch < 1:
        raise ValueError("invalid expected epoch")
    with write_transaction(connection):
        return _retire_in_transaction(connection, notice, expected_epoch)


def _retire_in_transaction(connection, notice, expected_epoch):
    previous = retirement(connection, notice)
    if previous is not None:
        if previous.retired_epoch != expected_epoch:
            raise destination.DispatchDenied("operation retired under another epoch")
        return previous
    fence = destination._revoke_in_transaction(connection, notice, expected_epoch)
    if destination.authority(connection, notice) != (fence.closed_epoch, False):
        raise destination.DispatchDenied("ordinary fence was superseded by a new grant")
    connection.execute("INSERT INTO permanent_retirements VALUES (?, ?)",
                       (notice.operation_key, expected_epoch))
    return fence


def create_routes(connection):
    connection.executescript("""
        CREATE TABLE failover_routes (
            request_id INTEGER PRIMARY KEY,
            operation_key TEXT NOT NULL UNIQUE,
            payload_digest TEXT NOT NULL,
            primary_id TEXT NOT NULL,
            secondary_id TEXT NOT NULL CHECK (primary_id != secondary_id),
            active_provider TEXT NOT NULL CHECK (active_provider IN (primary_id, secondary_id)),
            retired_epoch INTEGER,
            settled_provider TEXT,
            CHECK ((active_provider = secondary_id) = (retired_epoch IS NOT NULL)),
            CHECK (settled_provider IS NULL OR settled_provider = active_provider)
        );
    """)


def _route(connection, request_id):
    row = connection.execute("""
        SELECT o.operation_key, o.payload, o.status, r.primary_id, r.secondary_id,
               r.active_provider, r.settled_provider, r.payload_digest
        FROM failover_routes r JOIN notice_outbox o USING (request_id)
        WHERE r.request_id = ? AND o.operation_key = r.operation_key
    """, (request_id,)).fetchone()
    if row is None:
        raise DeliveryConflict("request has no authorized failover route")
    key, payload, status, primary, secondary, active, settled, stored = row
    notice = Notice(key, payload)
    if stored != notice.payload_digest:
        raise DeliveryConflict("route payload differs from the authorized notice")
    return notice, status, primary, secondary, active, settled


def enroll(connection, request_id, primary_id, secondary_id):
    """Persist the exact authorized pair before dispatching to either provider."""
    if (any(type(name) is not str or not name.strip() for name in (primary_id, secondary_id))
            or primary_id == secondary_id):
        raise ValueError("distinct named providers are required")
    with write_transaction(connection):
        row = connection.execute("""
            SELECT operation_key, payload, status FROM notice_outbox WHERE request_id = ?
        """, (request_id,)).fetchone()
        if row is None or row[2] == "prepared":
            raise DeliveryConflict("source authorization is required")
        notice = Notice(*row[:2])
        existing = connection.execute("""
            SELECT primary_id, secondary_id FROM failover_routes WHERE request_id = ?
        """, (request_id,)).fetchone()
        if existing is not None:
            _route(connection, request_id)
            if existing != (primary_id, secondary_id):
                raise DeliveryConflict("provider pair cannot change")
            return
        if row[2] != "pending":
            raise DeliveryConflict("the operation already has an effect")
        connection.execute("""
            INSERT INTO failover_routes VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
        """, (request_id, notice.operation_key, notice.payload_digest, primary_id, secondary_id, primary_id))


def begin_dispatch(connection, request_id, provider):
    notice, status, _, _, active, _ = _route(connection, request_id)
    provider_id = identity(provider)
    if status != "pending" or active != provider_id:
        raise DeliveryConflict("provider has no active dispatch route")
    epoch = destination.register(provider, notice)
    if destination.authority(provider, notice) != (epoch, True):
        raise destination.DispatchDenied("selected provider operation is closed")
    return Dispatch(provider_id, notice, epoch)


def apply_dispatch(provider, dispatch):
    if identity(provider) != dispatch.provider_id:
        raise DeliveryConflict("dispatch is bound to another provider")
    return destination.apply(provider, dispatch.notice, dispatch.epoch)


def _settle(connection, request_id, provider_id, effect):
    _acknowledge_in_transaction(connection, request_id, effect.receipt)
    connection.execute("UPDATE failover_routes SET settled_provider = ? WHERE request_id = ?",
                       (provider_id, request_id))


def recover_failover(connection, request_id, primary_provider):
    """Recover a prior receipt or switch after a durable empty primary retirement."""
    notice, status, primary, secondary, active, _ = _route(connection, request_id)
    if status == "sent":
        return "already_confirmed"
    if active == secondary:
        return "secondary"
    if primary_provider is None:
        return "unknown"
    if identity(primary_provider) != primary:
        raise DeliveryConflict("retirement must come from the enrolled primary")
    fence = retirement(primary_provider, notice)
    try:
        effect = fence.effect if fence is not None else destination.reconcile(primary_provider, notice)
    except destination.DispatchDenied:
        return "unknown"
    if fence is None and effect is None:
        return "unknown"
    with write_transaction(connection):
        _, status, _, secondary, active, _ = _route(connection, request_id)
        if status == "sent":
            return "already_confirmed"
        if active == secondary:
            return "secondary"
        if effect is not None:
            _settle(connection, request_id, primary, effect)
            return "primary_confirmed"
        connection.execute("""
            UPDATE failover_routes SET active_provider = ?, retired_epoch = ? WHERE request_id = ?
        """, (secondary, fence.retired_epoch, request_id))
        return "secondary"


def confirm(connection, request_id, provider):
    """Accept only a matching receipt read from the currently selected provider."""
    provider_id = identity(provider)
    with write_transaction(connection):
        notice, status, _, _, active, settled = _route(connection, request_id)
        if active != provider_id:
            raise DeliveryConflict("receipt is from the wrong provider")
        if status == "sent":
            if settled != provider_id:
                raise DeliveryConflict("stored receipt has no matching provider")
            return True
        effect = destination.reconcile(provider, notice)
        if effect is None:
            return False
        _settle(connection, request_id, provider_id, effect)
        return True
