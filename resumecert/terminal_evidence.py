"""Consume purpose-bound provider evidence without consulting a restored image.

HMAC keys are pinned by provider. The provider's durable terminalization contract
and truthful issuer are trusted. This fixture defines no public signature scheme.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json

from . import open_tail, provider_failover as routing, retention, revocable_delivery as delivery
from .archive_transfer import write_transaction
from .effect_delivery import DeliveryConflict, encode


class EvidenceError(ValueError):
    """Authentication, type, scope, or purpose does not justify this transition."""


class InsufficientEvidence(EvidenceError):
    """An authenticated observation does not establish terminal absence."""


@dataclass(frozen=True, slots=True)
class Evidence:
    payload: str
    mac: str


FIELDS = {"version", "provider_id", "operation_key", "payload_digest", "epoch",
          "kind", "purpose", "effect_id", "closed_epoch"}


def create_guard(connection):
    open_tail.create_guard(connection)
    connection.executescript("""
        CREATE TABLE accepted_provider_evidence (
            provider_id TEXT NOT NULL, operation_key TEXT NOT NULL, epoch INTEGER NOT NULL,
            payload TEXT NOT NULL, mac TEXT NOT NULL,
            PRIMARY KEY (provider_id, operation_key, epoch)
        );
        CREATE TRIGGER accepted_evidence_cannot_change BEFORE UPDATE ON accepted_provider_evidence
        BEGIN SELECT RAISE(ABORT, 'accepted provider evidence is immutable'); END;
        CREATE TRIGGER accepted_evidence_cannot_delete BEFORE DELETE ON accepted_provider_evidence
        BEGIN SELECT RAISE(ABORT, 'accepted provider evidence is retained'); END;
    """)


def _sign(body, key):
    if type(key) is not bytes or len(key) < 32:
        raise ValueError("a provider-pinned MAC key of at least 32 bytes is required")
    payload = encode(body)
    return Evidence(payload, hmac.new(key, payload.encode(), hashlib.sha256).hexdigest())


def issue(provider, dispatch, key, *, terminal=False):
    """Attest an observation, or atomically retire this operation and attest its outcome."""
    _validate_issuer(provider, dispatch, key)
    effect = (routing.retire(provider, dispatch.notice, dispatch.epoch).effect if terminal
              else delivery.reconcile(provider, dispatch.notice))
    return _attest(dispatch, key, effect, terminal)


def _validate_issuer(provider, dispatch, key):
    if type(key) is not bytes or len(key) < 32:
        raise ValueError("a provider-pinned MAC key of at least 32 bytes is required")
    if routing.identity(provider) != dispatch.provider_id:
        raise DeliveryConflict("dispatch is bound to another provider")
    retention._integer(dispatch.epoch, 1)


def _attest(dispatch, key, effect, terminal):
    if effect is not None and effect.accepted_epoch != dispatch.epoch:
        raise EvidenceError("observed receipt belongs to another dispatch generation")
    kind = "recorded" if effect is not None else ("retired_empty" if terminal else "lookup_empty")
    return _sign({"version": "resumecert-provider-evidence/v1", "provider_id": dispatch.provider_id,
                  "operation_key": dispatch.notice.operation_key, "payload_digest": dispatch.notice.payload_digest,
                  "epoch": dispatch.epoch, "kind": kind,
                  "purpose": {"recorded": "outcome", "retired_empty": "terminal", "lookup_empty": "observation"}[kind],
                  "effect_id": effect.receipt.effect_id if effect is not None else None,
                  "closed_epoch": dispatch.epoch + 1 if kind == "retired_empty" else None}, key)


def verify(evidence, trusted_keys):
    """Authenticate a canonical, typed object; caller must still bind it to its claim."""
    if type(evidence) is not Evidence or type(evidence.payload) is not str or type(evidence.mac) is not str:
        raise EvidenceError("invalid evidence envelope")
    try:
        body = json.loads(evidence.payload)
    except (ValueError, TypeError) as error:
        raise EvidenceError("invalid evidence encoding") from error
    if type(body) is not dict or set(body) != FIELDS or encode(body) != evidence.payload:
        raise EvidenceError("noncanonical or unexpected evidence fields")
    provider_id = body["provider_id"]
    if type(provider_id) is not str or provider_id not in trusted_keys:
        raise EvidenceError("provider is not pinned by the relying party")
    expected = _sign(body, trusted_keys[provider_id]).mac
    if (len(evidence.mac) != 64 or any(c not in "0123456789abcdef" for c in evidence.mac)
            or not hmac.compare_digest(expected, evidence.mac)):
        raise EvidenceError("provider evidence authentication failed")
    if (body["version"] != "resumecert-provider-evidence/v1"
            or type(body["epoch"]) is not int or not 1 <= body["epoch"] < 2**63 - 1
            or any(type(body[field]) is not str for field in ("operation_key", "payload_digest", "kind", "purpose"))):
        raise EvidenceError("invalid evidence scope or version")
    kind = body["kind"]
    expected_purpose = {"recorded": "outcome", "retired_empty": "terminal", "lookup_empty": "observation"}.get(kind)
    if expected_purpose is None or body["purpose"] != expected_purpose:
        raise EvidenceError("evidence kind does not affirm its declared purpose")
    if kind == "recorded":
        if type(body["effect_id"]) is not int or body["effect_id"] < 1 or body["closed_epoch"] is not None:
            raise EvidenceError("invalid positive receipt")
    elif body["effect_id"] is not None:
        raise EvidenceError("negative evidence contains a positive receipt")
    elif kind == "retired_empty":
        if type(body["closed_epoch"]) is not int or body["closed_epoch"] != body["epoch"] + 1:
            raise EvidenceError("terminal evidence has no matching closed generation")
    elif body["closed_epoch"] is not None:
        raise EvidenceError("lookup evidence cannot assert a closed generation")
    return body


def reserve(source, guard, request_id, provider, stream_id):
    """Reserve one source-authorized attempt before dispatch to the enrolled active provider."""
    provider_id = routing.identity(provider)
    message = _reservation_message(source, request_id, provider_id, stream_id)
    epoch = delivery.register(provider, message.notice)
    if delivery.authority(provider, message.notice) != (epoch, True):
        raise delivery.DispatchDenied("provider operation is closed")
    return _claim_dispatch(guard, provider_id, message, epoch)


def _reservation_message(source, request_id, provider_id, stream_id):
    notice, status, _, _, active, _ = routing._route(source, request_id)
    if status != "pending" or provider_id != active:
        raise DeliveryConflict("provider has no active authorized source route")
    return retention.StreamNotice(stream_id, request_id, notice)


def _claim_dispatch(guard, provider_id, message, epoch):
    notice = message.notice
    if open_tail._claim_new_effect(guard, provider_id, notice.operation_key, notice.payload,
                                   epoch, message.stream_id, message.sequence) != 1:
        raise delivery.DispatchDenied("a prior attempt remains reserved; reconcile its evidence")
    return routing.Dispatch(provider_id, notice, epoch)


def _context(source, guard, request_id, body):
    notice, _, primary, secondary, active, _ = routing._route(source, request_id)
    if (body["provider_id"] not in (primary, active)
            or body["operation_key"] != notice.operation_key or body["payload_digest"] != notice.payload_digest):
        raise EvidenceError("provider evidence does not match this source route and notice")
    with open_tail._fresh_guard(guard) as current:
        row = current.execute("""
            SELECT stream_id, sequence FROM open_tail_claims WHERE provider_id = ? AND operation_key = ?
        """, (body["provider_id"], notice.operation_key)).fetchone()
        if row is None or row[1] != request_id:
            raise EvidenceError("source has no matching independent attempt claim")
        message = retention.StreamNotice(row[0], row[1], notice)
        claim = open_tail._claim(current, body["provider_id"], message)
        if claim[3] != body["epoch"]:
            raise EvidenceError("provider evidence belongs to another attempt generation")
    return message


def _remember(guard, message, body, evidence):
    provider_id = body["provider_id"]
    with open_tail._fresh_guard(guard, write=True) as current, write_transaction(current):
        if message.sequence <= open_tail._floor(current, provider_id, message):
            raise retention.PrefixClosed("evidence belongs to a forgotten operation")
        claim = open_tail._claim(current, provider_id, message)
        if claim is None or claim[3] != body["epoch"]:
            raise EvidenceError("independent attempt changed")
        if body["kind"] != "recorded" and claim[4] is not None:
            raise EvidenceError("negative evidence conflicts with a retained positive receipt")
        old = current.execute("""
            SELECT payload, mac FROM accepted_provider_evidence
            WHERE provider_id = ? AND operation_key = ? AND epoch = ?
        """, (provider_id, message.notice.operation_key, body["epoch"])).fetchone()
        if old is not None and old != (evidence.payload, evidence.mac):
            raise EvidenceError("provider evidence conflicts with the accepted terminal decision")
        current.execute("INSERT INTO accepted_provider_evidence VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        (provider_id, message.notice.operation_key, body["epoch"], evidence.payload, evidence.mac))
        if body["kind"] == "recorded":
            current.execute("UPDATE open_tail_claims SET effect_id = ? WHERE provider_id = ? AND operation_key = ?",
                            (body["effect_id"], provider_id, message.notice.operation_key))


def _accept(source, guard, request_id, evidence, trusted_keys, *, require_terminal=True):
    body = verify(evidence, trusted_keys)
    message = _context(source, guard, request_id, body)
    if body["kind"] == "lookup_empty" and require_terminal:
        raise InsufficientEvidence("authenticated absence now does not prevent a late effect")
    _remember(guard, message, body, evidence)
    with write_transaction(source):
        notice, status, primary, secondary, active, settled = routing._route(source, request_id)
        if notice != message.notice:
            raise EvidenceError("source notice changed")
        if status == "sent":
            if body["kind"] == "recorded" and settled != body["provider_id"]:
                raise EvidenceError("source was settled by another provider")
            return "already_confirmed"
        if body["kind"] == "recorded":
            if active != body["provider_id"]:
                raise EvidenceError("receipt is not from the active provider")
            routing._settle(source, request_id, active,
                            delivery._recorded(notice, body["effect_id"], body["epoch"]))
            return "confirmed"
        if body["provider_id"] != primary:
            return "stopped_empty"
        if active == secondary:
            return "secondary"
        source.execute("UPDATE failover_routes SET active_provider = ?, retired_epoch = ? WHERE request_id = ?",
                       (secondary, body["epoch"], request_id))
        return "secondary"


def accept(source, guard, request_id, evidence, trusted_keys):
    """Only a matching positive result or permanent empty retirement changes recovery."""
    return _accept(source, guard, request_id, evidence, trusted_keys)


def recover_saved(source, guard, request_id, provider_id, trusted_keys):
    notice, _, _, _, _, _ = routing._route(source, request_id)
    with open_tail._fresh_guard(guard) as current:
        rows = current.execute("""
            SELECT payload, mac FROM accepted_provider_evidence WHERE provider_id = ? AND operation_key = ?
        """, (provider_id, notice.operation_key)).fetchall()
    if not rows:
        return "unknown"
    if len(rows) != 1:
        raise EvidenceError("multiple accepted generations require explicit reconciliation")
    return accept(source, guard, request_id, Evidence(*rows[0]), trusted_keys)
