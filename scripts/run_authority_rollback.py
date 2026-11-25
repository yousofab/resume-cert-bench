"""Exercise restores at the provider boundary while independently retaining its witness."""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_provider_authority import KEYS, STREAM, prepare, snapshot, restore
from resumecert import provider_failover as routing, revocable_delivery as delivery
from resumecert import terminal_evidence as evidence, witnessed_authority as protected
from resumecert.archive_transfer import connect
from resumecert.provider_authority import AuthorityUnavailable, ProviderAuthority


class IdentityOnly(protected.WitnessedAuthority):
    def _matches(self, actual, expected):
        return True


class RevisionOnly(protected.WitnessedAuthority):
    def _matches(self, actual, expected):
        return actual[0] == expected[0]


class AbortRevisionWrite(ProviderAuthority):
    @contextmanager
    def _connect(self):
        with super()._connect() as db:
            def authorize(action, arg1, arg2, database, trigger):
                if action == sqlite3.SQLITE_UPDATE and arg1 == "authority_revision":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            yield db


POLICIES = {"identity_only": IdentityOnly, "revision_only": RevisionOnly,
            "revision_and_commitment": protected.WitnessedAuthority}

SCENARIOS = {
    "normal_commit": "apply_a attest accept",
    "normal_transfer": "retire accept apply_b late_a",
    "older_image_after_transfer": "baseline retire accept apply_b restore_old verify late_a",
    "older_image_after_commit": "baseline apply_a attest accept restore_old verify late_a",
    "older_image_cannot_issue_terminal_absence": "baseline apply_a restore_old verify retire accept apply_b",
    "same_revision_sibling_after_transfer": "baseline sibling retire accept apply_b restore_sibling verify late_a",
    "same_revision_sibling_after_commit": "baseline sibling apply_a attest accept restore_sibling verify late_a",
    "current_backup_after_transfer": "retire accept apply_b save_current restore_current verify late_a",
    "current_backup_after_commit": "apply_a save_current restore_current verify attest accept",
    "quarantine_then_restore_current": "baseline apply_a save_current restore_old verify late_a restore_current verify attest accept",
    "witness_outage_then_recovers": "witness_offline verify apply_a witness_online apply_a attest accept",
    "witness_ahead_after_failed_commit": "abort_apply verify late_a",
    "witness_and_authority_restored_together": "baseline retire accept apply_b restore_both verify late_a",
}

def run(directory, policy, scenario):
    witness = directory / "witness.sqlite3"
    with closing(connect(witness)) as db:
        protected.create_witness(db)
    cls = POLICIES[policy]

    def enroll(authority):
        protected.enroll_fresh(authority, witness)
        return cls(authority, witness)

    services, dispatch = prepare(directory, "retained_authority", authority_factory=enroll)
    primary, secondary = services["primary"], services["secondary"]
    authority_path = primary.authority.path
    base_file = directory / "baseline.sqlite3"
    current_file = directory / "current.sqlite3"
    sibling_file = directory / "sibling.sqlite3"
    source_path = directory / "source.sqlite3"
    guard_path = directory / "guard.sqlite3"
    unavailable_witness = directory / "witness-offline.sqlite3"
    history = []
    events = []
    attestation = None
    for step in SCENARIOS[scenario].split():
        event = {"step": step}
        with closing(connect(source_path)) as source, closing(connect(guard_path)) as guard:
            try:
                if step == "baseline":
                    snapshot(authority_path)
                    shutil.copyfile(authority_path.with_suffix(".backup"), base_file)
                    snapshot(witness)
                elif step == "sibling":
                    shutil.copyfile(base_file, sibling_file)
                    branch_witness = directory / "sibling-witness.sqlite3"
                    shutil.copyfile(witness.with_suffix(".backup"), branch_witness)
                    branch = protected.WitnessedAuthority(
                        ProviderAuthority(sibling_file, "primary", primary.authority.store_id), branch_witness)
                    # The sibling advances once by registering another legitimate notice.
                    with branch._session() as (db, _):
                        delivery._register_in_transaction(db, routing._route(source, 2)[0])
                elif step == "save_current":
                    snapshot(authority_path)
                    shutil.copyfile(authority_path.with_suffix(".backup"), current_file)
                elif step in ("restore_old", "restore_sibling", "restore_current", "restore_both"):
                    saved = {"restore_old": base_file, "restore_sibling": sibling_file,
                             "restore_current": current_file, "restore_both": base_file}[step]
                    shutil.copyfile(saved, authority_path)
                    if step == "restore_both":
                        restore(witness)
                elif step == "witness_offline":
                    witness.rename(unavailable_witness)
                elif step == "witness_online":
                    unavailable_witness.rename(witness)
                elif step == "verify":
                    if witness.exists():
                        with primary.authority._connect() as db, primary._witness() as observer:
                            event["candidate_revision"] = db.execute("SELECT revision FROM authority_revision").fetchone()[0]
                            version, checksum = primary._head(observer)
                            event["witness_revision"] = version
                            event["candidate_digest"] = protected._commitment(db)
                            event["witness_digest"] = checksum
                            event["matching_content"] = event["candidate_digest"] == checksum
                    event["revision"] = primary.verify_current()
                    event["status"] = "admitted"
                elif step in ("retire", "attest"):
                    attestation = None
                    attestation = primary.issue(dispatch, KEYS["primary"], terminal=step == "retire")
                    event["kind"] = evidence.verify(attestation, KEYS)["kind"]
                elif step == "accept":
                    event["status"] = evidence.accept(source, guard, 1, attestation, KEYS) if attestation else "unknown"
                elif step == "abort_apply":
                    failing = cls(AbortRevisionWrite(authority_path, "primary", primary.authority.store_id), witness)
                    try:
                        failing.apply(dispatch)
                    except sqlite3.DatabaseError as error:
                        if "not authorized" not in str(error):
                            raise
                        event["status"] = "aborted_after_witness_commit"
                    else:
                        raise RuntimeError("expected injected write failure")
                elif step in ("apply_a", "late_a", "apply_b"):
                    service, attempt = primary, dispatch
                    if step == "apply_b" and routing._route(source, 1)[4] != "secondary":
                        event["status"] = "no_secondary_route"
                    else:
                        if step == "apply_b":
                            service = secondary
                            attempt = service.reserve(source, guard, 1, STREAM)
                        with service.authority._connect() as db:
                            prior = db.execute("SELECT effect_id FROM handoff_effects WHERE operation_key=?",
                                               (attempt.notice.operation_key,)).fetchone()
                        result = service.apply(attempt)
                        if prior is None:
                            creation = [attempt.provider_id, result.receipt.effect_id]
                            history.append(creation)
                            event["created"] = creation
                        if step == "apply_b":
                            proof = secondary.issue(attempt, KEYS["secondary"])
                            event["status"] = evidence.accept(source, guard, 1, proof, KEYS)
            except protected.StateMismatch:
                event["status"] = "quarantined"
            except AuthorityUnavailable:
                event["status"] = "unavailable"
            except delivery.DispatchDenied:
                event["status"] = "delivery_denied"
            _, status, _, _, active, settled = routing._route(source, 1)
            event.update(source_status=status, active_provider=active, settled_provider=settled)
        events.append(event)
    return {"scenario": scenario, "policy": policy, "events": events,
            "history": history, "settled_provider": events[-1]["settled_provider"],
            "boundary": scenario == "witness_and_authority_restored_together"}


def result():
    runs = []
    summary = {name: Counter(single_confirmed=0, duplicate=0, unresolved=0, boundary_duplicates=0)
               for name in POLICIES}
    with TemporaryDirectory() as tmp:
        for number, (scenario, policy) in enumerate((s, p) for s in SCENARIOS for p in POLICIES):
            directory = Path(tmp) / str(number)
            directory.mkdir()
            row = run(directory, policy, scenario)
            runs.append(row)
            count = len(row["history"])
            if row["boundary"]:
                summary[policy]["boundary_duplicates"] += int(count > 1)
            elif count > 1:
                summary[policy]["duplicate"] += 1
            elif count == 1 and row["settled_provider"] is not None:
                summary[policy]["single_confirmed"] += 1
            else:
                summary[policy]["unresolved"] += 1
    return {"schema": "resumecert-authority-rollback-v1", "runs": runs,
            "summary": {key: dict(value) for key, value in summary.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Authority rollback results differ\n")
    else:
        print(f"Reproduced {len(data['runs'])} authority rollback runs")


if __name__ == "__main__":
    main()
