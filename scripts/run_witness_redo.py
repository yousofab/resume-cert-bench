"""Compare quarantine and one-transition redo at the provider/witness commit boundary."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_authority_rollback import AbortRevisionWrite
from run_provider_authority import KEYS, STREAM, prepare, snapshot, restore
from resumecert import authority_redo as redo, provider_failover as routing
from resumecert import revocable_delivery as delivery, terminal_evidence as evidence
from resumecert import witnessed_authority as witnessed
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import encode
from resumecert.provider_authority import AuthorityUnavailable

POLICIES = {"digest_only": witnessed.WitnessedAuthority, "one_transition_redo": redo.RecoverableAuthority}
SCENARIOS = {
    "normal_effect": "apply recover attest",
    "gap_after_effect_intent": "abort_apply recover attest",
    "gap_after_retirement_intent": "abort_retire recover retire late transfer",
    "gap_after_registration_intent": "abort_register recover register apply attest",
    "restore_after_acknowledged_effect": "save apply attest restore recover attest",
    "restore_after_secondary_effect": "save retire transfer restore recover late attest_secondary",
    "repeat_recovery_and_delivery": "abort_apply recover recover apply attest",
    "second_crash_during_recovery": "abort_apply abort_recover recover attest",
    "two_revisions_behind": "save register apply restore recover",
    "divergent_predecessor_content": "abort_apply diverge recover",
    "changed_redo_command": "abort_apply change_command recover",
    "witness_unavailable": "abort_apply isolate_witness recover",
}

class Run:
    def __init__(self, root, policy):
        self.root = root
        self.witness = root / "witness.sqlite3"
        self.cls = POLICIES[policy]
        self.policy = policy
        self.request_id = 1
        with closing(connect(self.witness)) as db:
            witnessed.create_witness(db)
            if policy == "one_transition_redo":
                redo.create_redo_log(db)

        def enroll(authority):
            witnessed.enroll_fresh(authority, self.witness)
            return self.cls(authority, self.witness)

        services, self.dispatch = prepare(root, "retained_authority", authority_factory=enroll)
        self.primary, self.secondary = services["primary"], services["secondary"]
        self.source_path = root / "source.sqlite3"
        self.guard_path = root / "guard.sqlite3"
        self.path = self.primary.authority.path
        self.secondary_proof = None

    def action(self, step, source, guard, event):
        target = self.primary
        fault = step.startswith("abort_")
        if fault:
            target = self.cls(AbortRevisionWrite(self.path, "primary", target.authority.store_id), self.witness)
            step = step.removeprefix("abort_")
        if step == "save":
            snapshot(self.path)
        elif step == "restore":
            restore(self.path)
        elif step == "recover":
            result = (target.recover() if self.policy == "one_transition_redo"
                      else redo.Recovery(target.verify_current(), False))
            event.update(status="repaired" if result.repaired else "current", returned_revision=result.revision)
        elif step == "diverge":
            # A same-revision image whose registered payload differs.
            with closing(connect(self.path)) as db:
                db.execute("UPDATE handoff_authority SET payload_digest = ?", ("f" * 64,))
        elif step == "change_command":
            if self.policy == "one_transition_redo":
                with closing(connect(self.witness)) as db:
                    db.execute("UPDATE authority_redo SET command=? WHERE provider_id='primary'",
                               (encode(["resume-cert-redo/v1", "retire", self.dispatch.notice.operation_key,
                                        self.dispatch.notice.payload, self.dispatch.epoch]),))
        elif step == "isolate_witness":
            self.witness.rename(self.root / "witness-offline.sqlite3")
        elif step == "register":
            self.request_id = 2
            self.dispatch = target.reserve(source, guard, self.request_id, STREAM)
        elif step in ("apply", "late"):
            event["receipt"] = target.apply(self.dispatch).receipt.effect_id
        elif step == "retire":
            proof = target.issue(self.dispatch, KEYS["primary"], terminal=True)
            event["source_result"] = evidence.accept(source, guard, self.request_id, proof, KEYS)
        elif step == "transfer":
            if routing._route(source, self.request_id)[4] != "secondary":
                event["status"] = "not_authorized"
                return
            attempt = self.secondary.reserve(source, guard, self.request_id, STREAM)
            self.secondary.apply(attempt)
            self.secondary_proof = self.secondary.issue(attempt, KEYS["secondary"])
            event["source_result"] = evidence.accept(source, guard, self.request_id, self.secondary_proof, KEYS)
        elif step in ("attest", "attest_secondary"):
            proof = self.secondary_proof if step == "attest_secondary" else target.issue(self.dispatch, KEYS["primary"])
            event["receipt"] = evidence.verify(proof, KEYS)["effect_id"]
            event["source_result"] = evidence.accept(source, guard, self.request_id, proof, KEYS)
        else:
            raise ValueError(f"unknown schedule step: {step}")
        if fault:
            raise AssertionError("expected injected revision-write failure")

    def inspect(self, notice_key):
        counts = []
        for provider in (self.primary, self.secondary):
            with provider.authority._connect() as db:
                counts.append(db.execute("SELECT count(*) FROM handoff_effects WHERE operation_key=?",
                                         (notice_key,)).fetchone()[0])
        with self.primary.authority._connect() as db:
            revision = self.primary._revision(db)
            digest = witnessed._commitment(db)
            retired = db.execute("SELECT 1 FROM permanent_retirements WHERE operation_key=?",
                                 (notice_key,)).fetchone() is not None
        head = None
        if self.witness.exists():
            with self.primary._witness() as db:
                head = self.primary._head(db)
        return {"effect_rows": counts, "revision": revision, "witness_revision": head[0] if head else None,
                "primary_retired": retired, "authority_current": (revision, digest) == head}

    def execute(self, scenario):
        rows = []
        with closing(connect(self.source_path)) as source, closing(connect(self.guard_path)) as guard:
            for step in SCENARIOS[scenario].split():
                event = {"step": step}
                try:
                    self.action(step, source, guard, event)
                except witnessed.StateMismatch:
                    event["status"] = "quarantined"
                except AuthorityUnavailable:
                    event["status"] = "witness_unavailable"
                except delivery.DispatchDenied:
                    event["status"] = "delivery_denied"
                except sqlite3.DatabaseError as error:
                    if not step.startswith("abort_") or "not authorized" not in str(error):
                        raise
                    event["status"] = "injected_provider_rollback"
                notice, _, _, _, _, settled = routing._route(source, self.request_id)
                event.update(self.inspect(notice.operation_key))
                event["settled_provider"] = settled
                rows.append(event)
        last = rows[-1]
        confirmed = last["settled_provider"] is not None and sum(last["effect_rows"]) == 1
        return {"scenario": scenario, "policy": self.policy, "trace": rows, "confirmed": confirmed}


def result():
    runs = []
    summary = {name: Counter(confirmed=0, unresolved=0, restored_transitions=0, current_authority=0)
               for name in POLICIES}
    with TemporaryDirectory() as temp:
        for index, (scenario, policy) in enumerate((s, p) for s in SCENARIOS for p in POLICIES):
            root = Path(temp) / str(index)
            root.mkdir()
            row = Run(root, policy).execute(scenario)
            runs.append(row)
            summary[policy]["confirmed" if row["confirmed"] else "unresolved"] += 1
            summary[policy]["restored_transitions"] += sum(event.get("status") == "repaired" for event in row["trace"])
            summary[policy]["current_authority"] += row["trace"][-1]["authority_current"]
    return {"schema": "resumecert-witness-redo-v1", "runs": runs,
            "summary": {k: dict(v) for k, v in summary.items()},
            "external_effect_control": external_effect_control()}


def external_effect_control():
    """A separate SQLite sink models an opaque, non-idempotent service."""
    rows = []
    with TemporaryDirectory() as temp:
        for prior_effect in (False, True):
            root = Path(temp) / str(int(prior_effect))
            root.mkdir()
            run = Run(root, "one_transition_redo")
            with closing(connect(root / "opaque-sink.sqlite3")) as sink:
                sink.execute("CREATE TABLE effects (operation_key TEXT NOT NULL)")
                key = run.dispatch.notice.operation_key
                if prior_effect:
                    sink.execute("INSERT INTO effects VALUES (?)", (key,))
                schedule = run.execute("gap_after_effect_intent")
                # Local reconstruction does not use the sink. These are the
                # two counts if an operator withholds or blindly retries.
                before = sink.execute("SELECT count(*) FROM effects").fetchone()[0]
                with run.primary._witness() as db:
                    head = run.primary._head(db)
                sink.execute("INSERT INTO effects VALUES (?)", (key,))
                after = sink.execute("SELECT count(*) FROM effects").fetchone()[0]
            rows.append({"local_witness_head": list(head), "local_before_repair": schedule["trace"][0],
                         "withhold_send": before,
                         "blind_resend": after})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Witness redo results differ\n")
    else:
        print(f"Reproduced {len(data['runs'])} witness redo runs")


if __name__ == "__main__":
    main()
