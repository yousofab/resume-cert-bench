"""Durable handoff of an authorized archive notice to a separate SQLite inbox."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
import json

from .archive_transfer import write_transaction


class DeliveryConflict(ValueError):
    """A stable operation identity was reused with a different payload or receipt."""


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Notice:
    operation_key: str
    payload: str

    @property
    def payload_digest(self):
        return digest(self.payload)


@dataclass(frozen=True, slots=True)
class InboxReceipt:
    operation_key: str
    payload_digest: str
    effect_id: int


def create_source_schema(connection):
    """Add the handoff contract to an archive database with no prior effects enrolled."""
    connection.executescript("""
        CREATE TABLE notice_outbox (
            request_id INTEGER PRIMARY KEY,
            operation_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('prepared', 'pending', 'sent')),
            effect_id INTEGER,
            CHECK ((status = 'sent') = (effect_id IS NOT NULL))
        );
        CREATE TRIGGER archive_receipt_enqueues_notice AFTER INSERT ON archive_receipts
        BEGIN
            UPDATE notice_outbox SET status = 'pending'
            WHERE request_id = NEW.request_id AND status = 'prepared';
            SELECT CASE WHEN changes() != 1
                THEN RAISE(ABORT, 'archive notice must be prepared before authorization')
            END;
        END;
    """)


def prepare_notice(connection, source_id, request_id, label):
    """Freeze one canonical payload for the logical archive request before commit."""
    if type(source_id) is not str or not source_id.strip():
        raise ValueError("source_id must be a stable nonempty namespace")
    if type(request_id) is not int or request_id < 1:
        raise ValueError("invalid request id")
    if type(label) is not str or not label.strip():
        raise ValueError("label must be nonempty")
    notice = Notice(digest(encode(["archive-notice/v1", source_id, request_id])),
                    encode({"archive_id": request_id, "kind": "archive-notice/v1", "label": label}))
    with write_transaction(connection):
        row = connection.execute(
            "SELECT operation_key, payload FROM notice_outbox WHERE request_id = ?", (request_id,),
        ).fetchone()
        if row is not None:
            if Notice(*row) != notice:
                raise DeliveryConflict("prepared operation cannot change")
            return notice
        available = connection.execute("""
            SELECT 1 FROM datasets WHERE id = ?
            AND NOT EXISTS (SELECT 1 FROM archive_receipts WHERE request_id = ?)
        """, (request_id, request_id)).fetchone()
        if available is None:
            raise ValueError("request must exist and be awaiting authorization")
        connection.execute("INSERT INTO notice_outbox VALUES (?, ?, ?, 'prepared', NULL)",
                           (request_id, notice.operation_key, notice.payload))
    return notice


def pending_notice(connection, request_id):
    row = connection.execute("""
        SELECT operation_key, payload FROM notice_outbox WHERE request_id = ? AND status = 'pending'
    """, (request_id,)).fetchone()
    return Notice(*row) if row is not None else None


def acknowledge(connection, request_id, receipt):
    """Move to sent only for a trusted receipt bound to this request and payload."""
    if type(receipt) is not InboxReceipt or type(receipt.effect_id) is not int or receipt.effect_id < 1:
        raise DeliveryConflict("invalid inbox receipt")
    with write_transaction(connection):
        row = connection.execute("""
            SELECT operation_key, payload, status, effect_id FROM notice_outbox WHERE request_id = ?
        """, (request_id,)).fetchone()
        if row is None:
            raise DeliveryConflict("unknown request")
        key, payload, status, effect_id = row
        if (key != receipt.operation_key or digest(payload) != receipt.payload_digest
                or status == "prepared" or (status == "sent" and effect_id != receipt.effect_id)):
            raise DeliveryConflict("receipt does not match the authorized notice")
        if status == "pending":
            connection.execute("UPDATE notice_outbox SET status = 'sent', effect_id = ? WHERE request_id = ?",
                               (receipt.effect_id, request_id))


def deliver_once(connection, request_id, send: Callable[[Notice], InboxReceipt]):
    """Attempt delivery of a pending notice. A transport exception leaves it pending."""
    notice = pending_notice(connection, request_id)
    if notice is None:
        return None
    receipt = send(notice)
    acknowledge(connection, request_id, receipt)
    return receipt


def create_inbox_schema(connection):
    connection.executescript("""
        CREATE TABLE inbox_effects (
            effect_id INTEGER PRIMARY KEY,
            operation_key TEXT NOT NULL,
            idempotency_key TEXT UNIQUE,
            payload TEXT NOT NULL
        );
        CREATE INDEX inbox_by_operation ON inbox_effects(operation_key);
    """)


def receive(connection, notice, *, idempotent=True):
    """Apply the inbox effect and bind its receipt to the key in one transaction."""
    with write_transaction(connection):
        if idempotent:
            row = connection.execute(
                "SELECT effect_id, payload FROM inbox_effects WHERE idempotency_key = ?",
                (notice.operation_key,),
            ).fetchone()
            if row is not None:
                effect_id, payload = row
                if payload != notice.payload:
                    raise DeliveryConflict("destination key was already bound to another payload")
                return InboxReceipt(notice.operation_key, notice.payload_digest, effect_id)
        inserted = connection.execute("""
            INSERT INTO inbox_effects (operation_key, idempotency_key, payload) VALUES (?, ?, ?)
        """, (notice.operation_key, notice.operation_key if idempotent else None, notice.payload))
        effect_id = inserted.lastrowid
    return InboxReceipt(notice.operation_key, notice.payload_digest, effect_id)


def lookup_receipt(connection, notice):
    row = connection.execute("""
        SELECT effect_id, payload FROM inbox_effects
        WHERE operation_key = ? ORDER BY effect_id LIMIT 1
    """, (notice.operation_key,)).fetchone()
    if row is None:
        return None
    effect_id, payload = row
    if payload != notice.payload:
        raise DeliveryConflict("lookup returned a receipt for a different payload")
    return InboxReceipt(notice.operation_key, notice.payload_digest, effect_id)
