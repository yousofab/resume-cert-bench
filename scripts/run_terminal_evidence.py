"""Evaluate authenticated observations against purpose-bound terminal evidence."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resumecert import open_tail, provider_failover as routing, retention, revocable_delivery as delivery
from resumecert import terminal_evidence as evidence
from resumecert.archive_repair import checkpoint, create_repair_schema, resume
from resumecert.archive_transfer import FIELDS, connect, seed
from resumecert.effect_delivery import create_source_schema, prepare_notice

STREAM = "terminal-evidence-study"
# Public, deterministic test keys. They are not deployment credentials.
KEYS = {name: hashlib.sha256(f"PUBLIC ResumeCert fixture key ONLY {name}".encode()).digest()
        for name in ("primary", "secondary")}
POLICIES = ("hold_negative", "authenticated_lookup_release", "bound_terminal_release")
SCENARIOS = {
    "committed_receipt_missing": "a_apply lookup consume b_apply a_late",
    "late_after_negative_lookup": "lookup consume b_apply a_late",
    "terminal_empty": "terminal consume b_apply a_late",
    "committed_before_terminal": "a_apply terminal consume b_apply a_late",
    "negative_lookup_then_commit": "lookup a_apply consume b_apply",
    "terminal_proof_then_issuer_offline": "terminal offline consume b_apply a_late",
    "no_provider_evidence": "offline lookup consume b_apply a_late",
    "saved_empty_after_source_restore": "snapshot terminal consume restore_source saved b_apply a_late",
    "saved_positive_after_source_restore": "snapshot a_apply lookup consume restore_source saved b_apply",
    "secondary_reply_lost": "terminal consume b_silent b_lookup consume",
    "repeated_consumption_and_delivery": "terminal consume consume b_apply b_apply a_late",
    "tampered_mac": "terminal tamper consume b_apply a_late",
    "valid_mac_wrong_operation": "terminal wrong_operation consume b_apply a_late",
    "valid_mac_wrong_payload": "terminal wrong_payload consume b_apply a_late",
    "valid_mac_wrong_generation": "terminal wrong_epoch consume b_apply a_late",
    "valid_mac_wrong_provider": "terminal wrong_provider consume b_apply a_late",
    "valid_mac_wrong_purpose": "terminal wrong_purpose consume b_apply a_late",
    "valid_mac_wrong_closed_generation": "terminal wrong_closed consume b_apply a_late",
    "issuer_channel_recovers": "offline lookup consume online terminal consume b_apply a_late",
    "provider_rollback_boundary_control": "snapshot terminal consume b_apply restore_backend a_late",
}

def setup(directory):
    with closing(connect(directory / "source.sqlite3")) as source:
        create_repair_schema(source)
        create_source_schema(source)
        routing.create_routes(source)
        seed(source, 1, zip(FIELDS, (1, 1, 0, 0, 0)))
        notice = prepare_notice(source, STREAM, 1, "Synthetic evidence-backed handoff")
        checkpoint(source, 1, 4)
        if resume(source, 1, "reuse_only", 0).status != "committed":
            raise RuntimeError("source authorization failed")
        routing.enroll(source, 1, "primary", "secondary")
    message = retention.StreamNotice(STREAM, 1, notice)
    for name in KEYS:
        with closing(connect(directory / f"{name}.sqlite3")) as backend:
            routing.create_provider(backend, name)
    with closing(connect(directory / "guard.sqlite3")) as guard, closing(
            connect(directory / "projection.sqlite3")) as projection, closing(
            connect(directory / "source.sqlite3")) as source, closing(
            connect(directory / "primary.sqlite3")) as backend:
        evidence.create_guard(guard)
        open_tail.create_provider(projection, "primary")
        retention.enroll_stream(projection, guard, STREAM)
        retention.register(projection, guard, message)
        # Trusted fixture enrollment of the source-approved secondary namespace.
        guard.execute("INSERT INTO retention_anchors VALUES ('secondary', ?, 0)", (STREAM,))
        dispatch = evidence.reserve(source, guard, 1, backend, STREAM)
    return message, dispatch

def consume(source, guard, attestation, policy):
    if attestation is None:
        return "unknown"
    if policy == "hold_negative":
        body = evidence.verify(attestation, KEYS)
        evidence._context(source, guard, 1, body)
        return evidence.accept(source, guard, 1, attestation, KEYS) if body["kind"] == "recorded" else "unknown"
    if policy == "authenticated_lookup_release":
        return evidence._accept(source, guard, 1, attestation, KEYS, require_terminal=False)
    return evidence.accept(source, guard, 1, attestation, KEYS)

def backup(directory):
    for name in ("source", "primary"):
        with closing(connect(directory / f"{name}.sqlite3")) as live, closing(
                connect(directory / f"{name}.backup")) as saved:
            live.backup(saved)

def run_case(directory, name, policy):
    message, original = setup(directory)
    attestation = None
    secondary_dispatch = None
    online = True
    events = []
    witness = None
    for step in SCENARIOS[name].split():
        if step == "snapshot":
            backup(directory)
        if step == "restore_source":
            shutil.copyfile(directory / "source.backup", directory / "source.sqlite3")
        if step == "restore_backend":
            shutil.copyfile(directory / "primary.backup", directory / "primary.sqlite3")
        if step in ("online", "offline"):
            online = step == "online"
        event = {"step": step}
        with closing(connect(directory / "source.sqlite3")) as source, closing(
                connect(directory / "guard.sqlite3")) as guard, closing(
                connect(directory / "primary.sqlite3")) as a, closing(
                connect(directory / "secondary.sqlite3")) as b, closing(
                connect(directory / "projection.sqlite3")) as projection:
            try:
                if step in ("a_apply", "a_late"):
                    event["receipt_id"] = routing.apply_dispatch(a, original).receipt.effect_id
                elif step in ("lookup", "terminal", "b_lookup"):
                    if not online:
                        attestation = None
                        event["status"] = "issuer_channel_unavailable"
                    elif step == "b_lookup" and secondary_dispatch is None:
                        attestation = None
                        event["status"] = "no_secondary_attempt"
                    else:
                        target, dispatch = (b, secondary_dispatch) if step == "b_lookup" else (a, original)
                        attestation = evidence.issue(target, dispatch, KEYS[dispatch.provider_id], terminal=step == "terminal")
                        event["evidence_kind"] = json.loads(attestation.payload)["kind"]
                        if policy == "bound_terminal_release" and name in ("terminal_empty", "committed_before_terminal"):
                            old = open_tail.recover(projection, guard, message)
                            witness = {
                                "legacy_outcome": old.status,
                                "legacy_claims": [list(row) for row in guard.execute("SELECT * FROM open_tail_claims")],
                                "legacy_projection": [list(row) for row in projection.execute("SELECT * FROM handoff_authority")],
                                "legacy_effects": [list(row) for row in projection.execute("SELECT * FROM handoff_effects")],
                                "legacy_source": [list(row) for row in source.execute("SELECT * FROM notice_outbox")],
                                "new_evidence_kind": event["evidence_kind"],
                            }
                elif step == "consume":
                    if attestation is not None:
                        event["evidence"] = {"payload": attestation.payload, "mac": attestation.mac}
                    event["status"] = consume(source, guard, attestation, policy)
                elif step == "saved":
                    row = guard.execute("""SELECT payload, mac FROM accepted_provider_evidence
                        WHERE provider_id = 'primary' AND operation_key = ?""", (message.notice.operation_key,)).fetchone()
                    if row:
                        event["evidence"] = {"payload": row[0], "mac": row[1]}
                    event["status"] = consume(source, guard, evidence.Evidence(*row) if row else None, policy)
                elif step in ("b_apply", "b_silent"):
                    if secondary_dispatch is None:
                        _, status, _, _, active, _ = routing._route(source, 1)
                        if status == "pending" and active == "secondary":
                            secondary_dispatch = evidence.reserve(source, guard, 1, b, STREAM)
                    if secondary_dispatch is None:
                        event["status"] = "no_secondary_route"
                    else:
                        event["receipt_id"] = routing.apply_dispatch(b, secondary_dispatch).receipt.effect_id
                        if step == "b_apply":
                            positive = evidence.issue(b, secondary_dispatch, KEYS["secondary"])
                            event["evidence"] = {"payload": positive.payload, "mac": positive.mac}
                            event["status"] = consume(source, guard, positive, policy)
                elif step == "tamper":
                    attestation = evidence.Evidence(attestation.payload,
                        ("1" if attestation.mac[0] == "0" else "0") + attestation.mac[1:])
                elif step.startswith("wrong_"):
                    body = json.loads(attestation.payload)
                    field, value = {
                        "wrong_operation": ("operation_key", "f" * 64),
                        "wrong_payload": ("payload_digest", "e" * 64),
                        "wrong_epoch": ("epoch", 3),
                        "wrong_provider": ("provider_id", "secondary"),
                        "wrong_purpose": ("purpose", "observation"),
                        "wrong_closed": ("closed_epoch", 8),
                    }[step]
                    body[field] = value
                    if step == "wrong_epoch":
                        body["closed_epoch"] = 4
                    # Controlled valid-MAC fixtures test scope and purpose, beyond integrity.
                    attestation = evidence._sign(body, KEYS[body["provider_id"]])
            except evidence.InsufficientEvidence:
                event["status"] = "insufficient_terminal_evidence"
            except evidence.EvidenceError:
                event["status"] = "evidence_rejected"
            except delivery.DispatchDenied:
                event["status"] = "dispatch_denied"
            _, status, _, _, active, settled = routing._route(source, 1)
            event["source_status"] = status
            event["active_provider"] = active
            event["settled_provider"] = settled
            event["effects_by_provider"] = [node.execute("SELECT count(*) FROM handoff_effects").fetchone()[0] for node in (a, b)]
            events.append(event)
    count = sum(events[-1]["effects_by_provider"])
    confirmed = events[-1]["source_status"] == "sent"
    row = {"scenario": name, "policy": policy, "events": events,
           "effects_by_provider": events[-1]["effects_by_provider"],
           "confirmed_provider": events[-1]["settled_provider"],
           "clean_confirmed": count == 1 and confirmed, "duplicate": count > 1,
           "unresolved": not confirmed, "boundary_control": name == "provider_rollback_boundary_control"}
    if witness is not None:
        row["additional_evidence_witness"] = witness
    return row

def result():
    totals = {p: Counter(runs=0, clean_confirmed=0, duplicates=0, supported_duplicates=0,
                          unresolved=0, unresolved_empty=0, unresolved_with_effect=0) for p in POLICIES}
    runs = []
    with TemporaryDirectory() as root:
        for i, (name, policy) in enumerate((s, p) for s in SCENARIOS for p in POLICIES):
            directory = Path(root) / str(i)
            directory.mkdir()
            row = run_case(directory, name, policy)
            runs.append(row)
            count = sum(row["effects_by_provider"])
            totals[policy].update(runs=1, clean_confirmed=int(row["clean_confirmed"]), duplicates=int(row["duplicate"]),
                supported_duplicates=int(row["duplicate"] and not row["boundary_control"]),
                unresolved=int(row["unresolved"]), unresolved_empty=int(row["unresolved"] and count == 0),
                unresolved_with_effect=int(row["unresolved"] and count > 0))
            if policy == "bound_terminal_release" and not row["boundary_control"] and row["duplicate"]:
                raise RuntimeError("terminal-evidence recovery produced duplicate effects")
    return {"schema": "resumecert-terminal-evidence-v1", "summary": {p: dict(c) for p, c in totals.items()}, "runs": runs}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Terminal-evidence results differ\n")
    else:
        print(f"Reproduced {len(data['runs'])} terminal-evidence runs")

if __name__ == "__main__":
    main()
