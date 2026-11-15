"""Reproduce the SQLite checkpoint repair experiment."""

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import gzip
from hashlib import sha256
from itertools import combinations, product
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_archive_transfer import oracle  # noqa: E402
from resumecert.archive_repair import METHODS, checkpoint, create_repair_schema, resume  # noqa: E402
from resumecert.archive_transfer import CONTRACT, FIELDS, change, connect, read_fields, seed  # noqa: E402
from resumecert.records import Audit, Context, Decision  # noqa: E402
from resumecert.verifier import verify  # noqa: E402

SUMMARY = "archive_repair_v1.json"
TRACES = "archive_repair_v1_runs.jsonl.gz"
BUDGETS = (0, 1, 2, 3, 4)
INITIAL_BUDGET = 4


def design():
    regular = tuple((group,) for n in (0, 1, 2) for group in combinations(FIELDS, n))
    restored = tuple(((field,), (field,)) for field in FIELDS)
    for request_id, (before, schedule, budget, method) in enumerate(
        product(product((0, 1), repeat=len(FIELDS)), regular + restored, BUDGETS, METHODS), 1
    ):
        yield request_id, before, schedule, budget, method


def mutation_result(before, schedule):
    changes = Counter(field for step in schedule for field in step)
    return tuple(b ^ (changes[f] % 2) for f, b in zip(FIELDS, before))


def run(path):
    cells = tuple(design())
    issued = {}
    with closing(connect(path)) as initial:
        initial.execute("PRAGMA journal_mode=WAL")
        create_repair_schema(initial)
        for request_id, before, _, _, _ in cells:
            seed(initial, request_id, zip(FIELDS, before))
            issued[request_id] = checkpoint(initial, request_id, INITIAL_BUDGET)

    with closing(connect(path)) as writer:
        for request_id, _, schedule, _, _ in cells:
            for step in schedule:
                change(writer, request_id, step)

    # A new connection uses only the durable checkpoint. The observer reads
    # actual state separately and supplies the independent verifier downstream.
    with closing(connect(path)) as actor, closing(connect(path)) as observer:
        for request_id, before, schedule, budget, method in cells:
            actual = tuple(read_fields(observer, request_id, FIELDS).values())
            if mutation_result(before, schedule) != actual:
                raise RuntimeError("scheduled mutation differs from stored state")
            prior = issued[request_id]
            attempted = resume(actor, request_id, method, budget)
            receipt = observer.execute("SELECT 1 FROM archive_receipts WHERE request_id = ?",
                                       (request_id,)).fetchone() is not None
            if receipt != (attempted.status == "committed") or receipt and not oracle(actual):
                raise RuntimeError("invalid or inconsistent persisted receipt")
            cert = attempted.certificate
            if cert is not None:
                mutations = Counter(field for step in schedule for field in step)
                original = {field: value for read in prior.reads for field, value in read.values}
                expects_reuse = not method.startswith("restart_")
                expected_epochs = tuple((f, 0, mutations[f]) for f in FIELDS
                                        if expects_reuse and f in original)
                expected_retained = tuple((f, original[f]) for f, _, current in expected_epochs
                                          if current == 0)
                if (attempted.checked_epochs != expected_epochs
                        or cert.initial_facts != expected_retained):
                    raise RuntimeError("retained evidence disagrees with scheduled field changes")
                ctx = Context(CONTRACT, cert.scope, budget, cert.initial_facts, "", "")
                audit = Audit(ctx, tuple(zip(FIELDS, actual)), attempted.trace,
                              sum(event.cost for event in attempted.trace))
                if not verify(audit, cert).accepted:
                    raise RuntimeError("repair certificate failed independent verification")
            if (prior.decision is Decision.ALLOW) != (cert is not None):
                raise RuntimeError("missing repair decision or unexpected authorization")
            yield {
                "request_id": request_id, "method": method, "budget": budget,
                "case_group": "value_restored" if len(schedule) == 2 else "single_commit",
                "before": before, "after": actual, "writer_steps": schedule,
                "allowed_at_commit": oracle(actual),
                "prior_decision": prior.decision.value,
                "prior_reads": [{"query": read.query, "values": read.values} for read in prior.reads],
                "attempt": asdict(attempted), "receipt": receipt,
            }


def summarize(rows):
    counts = Counter({k: 0 for k in ("trials", "prior_allows", "eligible", "revoked",
                   "valid_receipts", "invalid_receipts", "block", "defer",
                   "fresh_values", "epoch_checks", "retained_values", "no_prior_allow")})
    for row in rows:
        attempt = row["attempt"]
        eligible = row["prior_decision"] == "allow" and row["allowed_at_commit"]
        counts["trials"] += 1
        counts["prior_allows"] += row["prior_decision"] == "allow"
        counts["eligible"] += eligible
        counts["revoked"] += row["prior_decision"] == "allow" and not row["allowed_at_commit"]
        counts["valid_receipts"] += eligible and row["receipt"]
        counts["invalid_receipts"] += not row["allowed_at_commit"] and row["receipt"]
        counts["block"] += attempt["status"] == "block"
        counts["defer"] += attempt["status"] == "defer"
        counts["no_prior_allow"] += attempt["status"] == "no_prior_allow"
        counts["epoch_checks"] += len(attempt["checked_epochs"])
        cert = attempt["certificate"]
        if cert is not None:
            counts["fresh_values"] += cert["spent"]
            counts["retained_values"] += len(cert["initial_facts"])
    return dict(counts)


def generate(directory):
    with TemporaryDirectory(prefix="resumecert-repair-") as root:
        rows = list(run(Path(root) / "archive.sqlite3"))
    keys = {(r["before"], r["writer_steps"], r["budget"], r["method"]) for r in rows}
    if len(rows) != 20160 or len(keys) != len(rows):
        raise RuntimeError("incomplete or duplicate repair grid")
    prior_by_case = {}
    for row in rows:
        key = (row["before"], row["writer_steps"])
        baseline = (row["prior_decision"], row["prior_reads"])
        if prior_by_case.setdefault(key, baseline) != baseline:
            raise RuntimeError("methods or budgets did not receive the same initial evidence")
    traces = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                     for row in rows).encode("ascii")
    summary = {"schema": "resumecert-archive-repair-v1", "field_order": FIELDS,
               "initial_budget": INITIAL_BUDGET, "trials": len(rows),
               "trace_sha256_uncompressed": sha256(traces).hexdigest(), "groups": {}}
    for group in ("single_commit", "value_restored"):
        summary["groups"][group] = {
            str(budget): {
                method: summarize([r for r in rows if r["case_group"] == group
                                   and r["budget"] == budget and r["method"] == method])
                for method in METHODS
            } for budget in BUDGETS
        }
    (directory / SUMMARY).write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n",
                                     encoding="ascii", newline="\n")
    (directory / TRACES).write_bytes(gzip.compress(traces, mtime=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", type=Path)
    mode.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.record:
        args.record.mkdir()
        generate(args.record)
        print(f"Recorded archive repair artifacts in {args.record}")
    else:
        with TemporaryDirectory(prefix="resumecert-repair-check-") as root:
            path = Path(root)
            generate(path)
            if (path / SUMMARY).read_bytes() != (args.check / SUMMARY).read_bytes():
                parser.exit(1, "Archive repair summary differs\n")
            with gzip.open(path / TRACES, "rb") as observed, gzip.open(args.check / TRACES, "rb") as expected:
                while block := observed.read(65536):
                    if block != expected.read(65536):
                        parser.exit(1, "Archive repair trace differs\n")
                if expected.read(1):
                    parser.exit(1, "Archive repair trace contains extra bytes\n")
        print("Archive repair: summary and all 20160 traces match")


if __name__ == "__main__":
    main()
