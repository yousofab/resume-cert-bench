"""Measure the exact backup lag admitted by single and bounded witnessed redo."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_provider_authority import KEYS, STREAM, prepare
from resumecert import authority_redo as redo, authority_redo_chain as chain
from resumecert import revocable_delivery as delivery, terminal_evidence as evidence
from resumecert import witnessed_authority as witnessed
from resumecert.archive_transfer import connect
from resumecert.effect_delivery import encode

CAPACITIES = (1, 2, 4)
LAST_REVISION = 6


def local_state(service):
    with service.authority._connect() as db:
        return {
            "revision": service._revision(db),
            "digest": witnessed._commitment(db),
            "effects": db.execute("SELECT COUNT(*) FROM handoff_effects").fetchone()[0],
            "retirements": db.execute("SELECT COUNT(*) FROM permanent_retirements").fetchone()[0],
        }


class Fixture:
    def __init__(self, root, capacity, lag):
        self.witness = root / "witness.sqlite3"
        self.backup = root / "provider.backup"
        self.source = root / "source.sqlite3"
        self.guard = root / "guard.sqlite3"
        self.backup_revision = LAST_REVISION - lag
        self.table = "authority_redo" if capacity == 1 else "authority_redo_chain"
        with closing(connect(self.witness)) as db:
            witnessed.create_witness(db)
            if capacity == 1:
                redo.create_redo_log(db)
            else:
                chain.create_redo_chain(db)

        def enroll(authority):
            witnessed.enroll_fresh(authority, self.witness)
            if capacity == 1:
                service = redo.RecoverableAuthority(authority, self.witness)
            else:
                service = chain.BoundedRedoAuthority(authority, self.witness, capacity)
            if authority.provider_id == "primary" and self.backup_revision == 0:
                self.save(service)
            return service

        services, first = prepare(root, "retained_authority", authority_factory=enroll)
        self.primary = services["primary"]
        with closing(connect(self.source)) as source, closing(connect(self.guard)) as guard:
            self.save_if_selected(1)
            self.primary.apply(first)
            self.save_if_selected(2)
            first_proof = self.primary.issue(first, KEYS["primary"], terminal=True)
            self.save_if_selected(3)
            self.second = self.primary.reserve(source, guard, 2, STREAM)
            self.save_if_selected(4)
            self.primary.apply(self.second)
            self.save_if_selected(5)
            second_proof = self.primary.issue(self.second, KEYS["primary"], terminal=True)
            self.save_if_selected(6)
        self.original_proofs = (first_proof, second_proof)
        self.dispatches = (first, self.second)
        self.expected = local_state(self.primary)
        assert self.expected["revision"] == LAST_REVISION
        shutil.copyfile(self.backup, self.primary.authority.path)
        self.before = local_state(self.primary)
        assert self.before["revision"] == self.backup_revision

    def save(self, service):
        with service.authority._connect() as db, closing(connect(self.backup)) as backup:
            db.backup(backup)

    def save_if_selected(self, revision):
        if revision == self.backup_revision:
            self.save(self.primary)

    def recover(self):
        outcome = self.primary.recover()
        if isinstance(outcome, redo.Recovery):
            return int(outcome.repaired)
        return outcome.replayed

    def confirm(self):
        with closing(connect(self.source)) as source, closing(connect(self.guard)) as guard:
            outcomes = []
            for request, dispatch in enumerate(self.dispatches, 1):
                proof = self.primary.issue(dispatch, KEYS["primary"], terminal=True)
                assert proof == self.original_proofs[request - 1]
                outcomes.append(evidence.accept(source, guard, request, proof, KEYS))
                try:
                    self.primary.apply(dispatch)
                except delivery.DispatchDenied:
                    pass
                else:
                    raise AssertionError("a retired dispatch could create an effect")
        assert outcomes == ["confirmed", "confirmed"]
        return outcomes

    def retained(self):
        with closing(connect(self.witness)) as db:
            rows = db.execute(f"""
                SELECT revision, length(CAST(command AS BLOB)) FROM {self.table}
                WHERE provider_id=? AND store_id=? ORDER BY revision
            """, self.primary._identity).fetchall()
        return {"revisions": [row[0] for row in rows], "command_bytes": sum(row[1] for row in rows)}

    def crash(self):
        script = """
import os
from pathlib import Path
import sys
from unittest.mock import patch
from resumecert import authority_redo_chain as chain
from resumecert.provider_authority import ProviderAuthority

path, witness, store_id = sys.argv[1:]
service = chain.BoundedRedoAuthority(ProviderAuthority(Path(path), "primary", store_id),
                                     Path(witness), 4)
original = chain._replay_transition
completed = 0

def exit_during_replay(*args):
    global completed
    original(*args)
    completed += 1
    if completed == 2:
        os._exit(71)

with patch.object(chain, "_replay_transition", exit_during_replay):
    service.recover()
"""
        child = subprocess.run(
            [sys.executable, "-c", script, str(self.primary.authority.path),
             str(self.witness), self.primary.authority.store_id],
            cwd=Path(__file__).resolve().parents[1], check=False, timeout=15,
        )
        assert child.returncode == 71
        return child.returncode


def matched_case(root, capacity, lag):
    fixture = Fixture(root, capacity, lag)
    try:
        replayed = fixture.recover()
    except witnessed.StateMismatch:
        assert lag > capacity
        status, replayed, confirmations = "quarantined", 0, []
        assert local_state(fixture.primary) == fixture.before
    else:
        assert lag <= capacity and replayed == lag
        assert local_state(fixture.primary) == fixture.expected
        assert fixture.recover() == 0
        status = "repaired" if replayed else "current"
        confirmations = fixture.confirm()
    retained = fixture.retained()
    assert retained["revisions"] == list(range(LAST_REVISION - capacity + 1, LAST_REVISION + 1))
    return {
        "capacity": capacity, "lag": lag, "status": status, "replayed": replayed,
        "before": fixture.before, "after": local_state(fixture.primary),
        "expected_head": fixture.expected, "retained": retained, "source_confirmations": confirmations,
    }


def interrupted_case(root, fault):
    fixture = Fixture(root, 4, 3)
    notice = fixture.second.notice
    with closing(connect(fixture.witness)) as db:
        saved = db.execute("SELECT * FROM authority_redo_chain WHERE revision=5").fetchone()
        if fault == "missing_middle_record":
            db.execute("DELETE FROM authority_redo_chain WHERE revision=5")
            expected = "redo chain is incomplete"
        elif fault == "broken_middle_digest":
            db.execute("UPDATE authority_redo_chain SET predecessor_digest=? WHERE revision=5", ("0" * 64,))
            expected = "redo chain is discontinuous"
        elif fault == "wrong_middle_command":
            db.execute("UPDATE authority_redo_chain SET command=? WHERE revision=5", (
                encode(["resume-cert-redo/v1", "retire", notice.operation_key, notice.payload, 1]),
            ))
            expected = "reconstructed state differs from witnessed successor"
    if fault == "process_exit_after_two_replays":
        interruption = fixture.crash()
    else:
        try:
            fixture.recover()
        except witnessed.StateMismatch as error:
            interruption = str(error)
            assert interruption == expected
        else:
            raise AssertionError("damaged history was admitted")
    after_interruption = local_state(fixture.primary)
    assert after_interruption == fixture.before
    if fault != "process_exit_after_two_replays":
        with closing(connect(fixture.witness)) as db:
            db.execute("INSERT OR REPLACE INTO authority_redo_chain VALUES (?, ?, ?, ?, ?, ?)", saved)
    replayed = fixture.recover()
    assert replayed == 3 and fixture.recover() == 0
    assert local_state(fixture.primary) == fixture.expected
    return {
        "fault": fault, "interruption": interruption, "before": fixture.before,
        "after_interruption": after_interruption, "after_retry": local_state(fixture.primary),
        "replayed_on_retry": replayed, "source_confirmations": fixture.confirm(),
    }


def result():
    matched, faults = [], []
    for capacity in CAPACITIES:
        for lag in range(LAST_REVISION + 1):
            with TemporaryDirectory() as directory:
                matched.append(matched_case(Path(directory), capacity, lag))
    for fault in ("missing_middle_record", "broken_middle_digest", "wrong_middle_command",
                  "process_exit_after_two_replays"):
        with TemporaryDirectory() as directory:
            faults.append(interrupted_case(Path(directory), fault))
    return {"schema": "resumecert-bounded-redo-chain-v1", "matched_runs": matched, "fault_runs": faults}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    output = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.check is None:
        print(output, end="")
    elif args.check.read_text() != output:
        parser.exit(1, "Bounded redo chain results differ\n")
    else:
        print(f"Reproduced {len(data['matched_runs'])} matched and {len(data['fault_runs'])} fault runs")


if __name__ == "__main__":
    main()
