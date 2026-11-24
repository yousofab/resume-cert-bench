"""Compare local state, copied evidence, and retained provider authority on restore."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_terminal_evidence import KEYS
from resumecert import provider_authority as durable, provider_failover as routing
from resumecert import terminal_evidence as evidence, revocable_delivery as delivery
from resumecert.archive_repair import checkpoint, create_repair_schema, resume
from resumecert.archive_transfer import FIELDS, connect, seed
from resumecert.effect_delivery import create_source_schema, prepare_notice

POLICIES = ("provider_image", "copy_after_commit", "retained_authority")
STREAM = "provider-authority-study"
SCENARIOS = {
    "ordinary_ack": "apply_a read_a accept_a",
    "ordinary_failover": "close_a accept_a apply_b read_b accept_b late_a",
    "retirement_then_projection_restore": "snapshot close_a accept_a apply_b read_b accept_b restore late_a",
    "retirement_retrieved_after_restore": "snapshot close_lost restore close_a accept_a apply_b read_b accept_b late_a",
    "ack_then_projection_restore": "snapshot apply_a read_a accept_a restore late_a read_a accept_a",
    "lost_reply_before_receipt_copy": "snapshot apply_lost restore late_a read_a accept_a",
    "repeated_restore_after_lost_reply": "snapshot apply_lost restore apply_lost restore late_a read_a accept_a",
    "lost_copy_then_try_to_terminalize": "snapshot apply_lost restore close_a accept_a apply_b read_b accept_b late_a",
    "historical_receipt_retrieved_after_restore": "snapshot apply_a restore read_a accept_a",
    "source_and_projection_restore": "snapshot close_a accept_a restore_source restore saved_a apply_b read_b accept_b late_a",
    "secondary_projection_restore": "snapshot close_a accept_a reserve_b snapshot_b apply_b_lost restore_b late_b read_b accept_b",
    "unrelated_authorized_work_after_restore": "snapshot close_a accept_a restore late_a next apply_b read_b accept_b",
    "projection_unavailable": "projection_offline apply_a read_a accept_a",
    "stale_projection_can_be_rebuilt": "refresh_a snapshot apply_a refresh_a restore inspect_a refresh_a read_a accept_a",
    "authority_unavailable_then_recovers": "snapshot apply_a offline apply_lost read_a accept_a online read_a accept_a",
    "authority_remains_unavailable": "apply_lost offline read_a accept_a",
    "shared_restore_domain_control": "snapshot close_a accept_a apply_b read_b accept_b restore_all late_a",
}

def prepare(directory, policy):
    with closing(connect(directory / "source.sqlite3")) as source, closing(
            connect(directory / "guard.sqlite3")) as guard:
        create_repair_schema(source)
        create_source_schema(source)
        routing.create_routes(source)
        evidence.create_guard(guard)
        for number in (1, 2):
            seed(source, number, zip(FIELDS, (1, 1, 0, 0, 0)))
            prepare_notice(source, STREAM, number, f"Synthetic handoff {number}")
            checkpoint(source, number, 4)
            if resume(source, number, "reuse_only", 0).status != "committed":
                raise RuntimeError("source authorization failed")
            routing.enroll(source, number, "primary", "secondary")
        providers = {}
        for name in KEYS:
            image = directory / f"{name}.sqlite3"
            authority = directory / f"{name}-authority.sqlite3"
            store_id = f"fixture:{name}"
            if policy == "retained_authority":
                with closing(connect(authority)) as db:
                    durable.create_authority(db, name, store_id)
            with closing(connect(image)) as db:
                durable.create_projection(db)
                if policy != "retained_authority":
                    routing.create_provider(db, name)
            guard.execute("INSERT INTO retention_anchors VALUES (?, ?, 0)", (name, STREAM))
            providers[name] = durable.ProviderAuthority(authority, name, store_id)
        if policy == "retained_authority":
            dispatch = providers["primary"].reserve(source, guard, 1, STREAM)
        else:
            with closing(connect(directory / "primary.sqlite3")) as primary:
                dispatch = evidence.reserve(source, guard, 1, primary, STREAM)
    with closing(connect(directory / "copy.sqlite3")) as db:
        db.execute("""CREATE TABLE copies (
            provider_id TEXT, operation_key TEXT, payload TEXT, mac TEXT,
            PRIMARY KEY (provider_id, operation_key))""")
    with closing(connect(directory / "history.sqlite3")) as db:
        db.execute("CREATE TABLE creations (provider_id TEXT, operation_key TEXT, effect_id INTEGER)")
    return providers, dispatch

def snapshot(path):
    with closing(connect(path)) as db, closing(connect(path.with_suffix(".backup"))) as backup:
        db.backup(backup)

def restore(path):
    shutil.copyfile(path.with_suffix(".backup"), path)

class Case:
    def __init__(self, directory, policy):
        self.directory = directory
        self.policy = policy
        self.providers, first = prepare(directory, policy)
        self.dispatch = {"primary": first}
        self.proofs = {}
        self.offline = False

    def path(self, name):
        return self.directory / f"{name}.sqlite3"

    def actual(self, name):
        return self.providers[name].path if self.policy == "retained_authority" else self.path(name)

    def copy(self, dispatch):
        with closing(connect(self.path("copy"))) as db:
            row = db.execute("SELECT payload, mac FROM copies WHERE provider_id = ? AND operation_key = ?",
                (dispatch.provider_id, dispatch.notice.operation_key)).fetchone()
        return evidence.Evidence(*row) if row else None

    def issue(self, dispatch, *, terminal=False, publish=True):
        name = dispatch.provider_id
        if self.offline:
            raise durable.AuthorityUnavailable("injected primary authority outage")
        if self.policy == "retained_authority":
            return self.providers[name].issue(dispatch, KEYS[name], terminal=terminal)
        if self.policy == "copy_after_commit":
            previous = self.copy(dispatch)
            if previous is not None:
                return previous
        if not self.path(name).exists():
            raise durable.AuthorityUnavailable("provider image unavailable")
        with closing(connect(self.path(name))) as db:
            proof = evidence.issue(db, dispatch, KEYS[name], terminal=terminal)
        if (self.policy == "copy_after_commit" and publish
                and evidence.verify(proof, KEYS)["kind"] != "lookup_empty"):
            with closing(connect(self.path("copy"))) as db:
                db.execute("INSERT INTO copies VALUES (?, ?, ?, ?)",
                           (name, dispatch.notice.operation_key, proof.payload, proof.mac))
        return proof

    def apply(self, dispatch, event, *, publish):
        if self.offline:
            raise durable.AuthorityUnavailable("injected primary authority outage")
        name, notice = dispatch.provider_id, dispatch.notice
        if self.policy == "copy_after_commit":
            proof = self.copy(dispatch)
            if proof is not None:
                body = evidence.verify(proof, KEYS)
                if body["kind"] == "retired_empty":
                    raise delivery.DispatchDenied("independent copy remembers terminal retirement")
                event["receipt_id"] = body["effect_id"]
                event["copied_receipt"] = True
                return
        if not self.actual(name).exists():
            raise durable.AuthorityUnavailable("provider store unavailable")
        with closing(connect(self.actual(name))) as db:
            prior = db.execute("SELECT effect_id FROM handoff_effects WHERE operation_key = ?",
                               (notice.operation_key,)).fetchone()
        if self.policy == "retained_authority":
            effect = self.providers[name].apply(dispatch)
        else:
            with closing(connect(self.path(name))) as db:
                effect = routing.apply_dispatch(db, dispatch)
        event["receipt_id"] = effect.receipt.effect_id
        if prior is None:
            with closing(connect(self.path("history"))) as history:
                history.execute("INSERT INTO creations VALUES (?, ?, ?)", (name, notice.operation_key, effect.receipt.effect_id))
            event["created"] = [name, notice.operation_key, effect.receipt.effect_id]
        if self.policy == "copy_after_commit" and publish:
            self.issue(dispatch)

    def reserve(self, source, guard, number, name):
        if self.policy == "retained_authority":
            return self.providers[name].reserve(source, guard, number, STREAM)
        with closing(connect(self.path(name))) as db:
            return evidence.reserve(source, guard, number, db, STREAM)

    def step(self, step, source, guard, event):
        first = self.dispatch["primary"]
        if step == "snapshot":
            for name in ("primary", "source", "secondary"):
                snapshot(self.path(name))
            if self.policy == "retained_authority":
                snapshot(self.actual("primary"))
            snapshot(self.path("copy"))
        elif step == "snapshot_b":
            snapshot(self.path("secondary"))
        elif step == "offline" or step == "online":
            self.offline = step == "offline"
        elif step == "projection_offline":
            # An inaccessible projection must stay outside the authority path.
            self.path("primary").rename(self.path("primary").with_suffix(".offline"))
        elif step == "restore_all":
            restore(self.path("primary"))
            restore(self.path("copy"))
            if self.policy == "retained_authority":
                restore(self.actual("primary"))
        elif step in ("restore", "restore_b"):
            restore(self.path("primary" if step == "restore" else "secondary"))
        elif step == "refresh_a":
            if self.policy == "retained_authority":
                with closing(connect(self.path("primary"))) as projection:
                    event["status"] = self.providers["primary"].refresh_projection(projection, first)
        elif step == "inspect_a":
            with closing(connect(self.path("primary"))) as projection:
                row = projection.execute("SELECT status FROM provider_projection").fetchone()
            event["status"] = row[0] if row else "uncached"
        elif step == "next":
            dispatch = self.reserve(source, guard, 2, "primary")
            self.apply(dispatch, event, publish=True)
            result = self.issue(dispatch)
            event["next_status"] = evidence.accept(source, guard, 2, result, KEYS)
        elif step in ("apply_a", "apply_lost", "late_a"):
            self.apply(first, event, publish=step != "apply_lost")
        elif step in ("close_a", "close_lost", "read_a"):
            proof = self.issue(first, terminal=step != "read_a", publish=step != "close_lost")
            event["evidence_kind"] = evidence.verify(proof, KEYS)["kind"]
            if step != "close_lost":
                self.proofs["primary"] = proof
        elif step in ("accept_a", "accept_b"):
            proof = self.proofs.get("primary" if step == "accept_a" else "secondary")
            event["status"] = evidence.accept(source, guard, 1, proof, KEYS) if proof else "unknown"
        elif step == "saved_a":
            event["status"] = evidence.recover_saved(source, guard, 1, "primary", KEYS)
        elif step in ("reserve_b", "apply_b", "apply_b_lost", "late_b", "read_b"):
            if "secondary" not in self.dispatch:
                _, status, _, _, active, _ = routing._route(source, 1)
                if status == "pending" and active == "secondary":
                    self.dispatch["secondary"] = self.reserve(source, guard, 1, "secondary")
            dispatch = self.dispatch.get("secondary")
            if dispatch is None:
                event["status"] = "no_secondary_route"
            elif step == "read_b":
                self.proofs["secondary"] = self.issue(dispatch)
                event["evidence_kind"] = evidence.verify(self.proofs["secondary"], KEYS)["kind"]
            elif step != "reserve_b":
                self.apply(dispatch, event, publish=step != "apply_b_lost")

    def run(self, name):
        events = []
        for step in SCENARIOS[name].split():
            if step == "restore_source":
                restore(self.path("source"))
            event = {"step": step}
            with closing(connect(self.path("source"))) as source, closing(connect(self.path("guard"))) as guard:
                try:
                    if step != "restore_source":
                        self.step(step, source, guard, event)
                except evidence.InsufficientEvidence:
                    event["status"] = "unknown"
                except delivery.DispatchDenied:
                    event["status"] = "dispatch_denied"
                except durable.AuthorityUnavailable:
                    event["status"] = "authority_unavailable"
                except evidence.EvidenceError as error:
                    if str(error) != "provider evidence conflicts with the accepted terminal decision":
                        raise
                    event["status"] = "conflicting_evidence"
                _, status, _, _, active, settled = routing._route(source, 1)
                event.update(source_status=status, active_provider=active, settled_provider=settled)
            events.append(event)
        key = self.dispatch["primary"].notice.operation_key
        with closing(connect(self.path("history"))) as db:
            history = [list(row) for row in db.execute("SELECT * FROM creations ORDER BY rowid")]
        primary = sum(row[0] == "primary" and row[1] == key for row in history)
        secondary = sum(row[0] == "secondary" and row[1] == key for row in history)
        return {"scenario": name, "policy": self.policy, "operation_key": key, "events": events, "history": history,
                "effects_by_provider": [primary, secondary], "settled_provider": events[-1]["settled_provider"],
                "boundary_control": name == "shared_restore_domain_control"}

def result():
    runs = []
    summary = {p: Counter(confirmed_single=0, duplicate_runs=0, unresolved=0, boundary_duplicates=0) for p in POLICIES}
    with TemporaryDirectory() as root:
        for index, (scenario, policy) in enumerate((s, p) for s in SCENARIOS for p in POLICIES):
            case_dir = Path(root) / str(index)
            case_dir.mkdir()
            row = Case(case_dir, policy).run(scenario)
            count = sum(row["effects_by_provider"])
            if row["boundary_control"]:
                summary[policy]["boundary_duplicates"] += count > 1
            else:
                summary[policy].update(confirmed_single=int(count == 1 and row["settled_provider"] is not None),
                    duplicate_runs=int(count > 1), unresolved=int(row["settled_provider"] is None))
                if policy == "retained_authority" and count > 1:
                    raise RuntimeError("authority allowed duplicate historical effects")
            runs.append(row)
    return {"schema": "resumecert-provider-authority-v1", "runs": runs, "summary": {p: dict(v) for p, v in summary.items()}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    data = result()
    if args.check is None:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif data != json.loads(args.check.read_bytes()):
        parser.exit(1, "Provider-authority results differ\n")
    else:
        print(f"Reproduced {len(data['runs'])} provider-authority runs")

if __name__ == "__main__":
    main()
