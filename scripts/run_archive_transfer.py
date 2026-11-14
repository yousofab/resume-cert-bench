"""Reproduce the exploratory SQLite archive commit-guard experiment."""

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

from resumecert.archive_transfer import (  # noqa: E402
    FIELDS, METHODS, archive, authorize, change, connect, create_schema, read_fields, seed,
)
from resumecert.records import Decision  # noqa: E402

SUMMARY = "archive_transfer_v1.json"
TRACES = "archive_transfer_v1_runs.jsonl.gz"
BUDGETS = (2, 3, 4)


def oracle(bits):
    """Arithmetic check independent of the action contract and commit guard."""
    manifest, curator, restricted, lane, _ = bits
    return manifest == 1 and (curator + restricted * lane >= 1)


def transition(before, after, changed):
    if not changed:
        return "stable"
    if changed == ("annotation_revision",):
        return "metadata_only"
    if before and not after:
        return "permission_revoked"
    if not before and after:
        return "permission_gained"
    return "relevant_change_preserves_decision"


def run(path):
    changes = tuple(group for size in (0, 1, 2) for group in combinations(FIELDS, size))
    with closing(connect(path)) as actor, closing(connect(path)) as writer:
        create_schema(actor)
        for request_id, (budget, schedule, method) in enumerate(
            product(BUDGETS, product(product((0, 1), repeat=len(FIELDS)), changes), METHODS), 1
        ):
            before, changed = schedule
            seed(actor, request_id, zip(FIELDS, before))
            grant = authorize(actor, request_id, budget)
            change(writer, request_id, changed)
            after = tuple(read_fields(writer, request_id, FIELDS).values())
            if after != tuple(b ^ int(f in changed) for f, b in zip(FIELDS, before)):
                raise RuntimeError("scheduled mutation did not persist")
            result = archive(actor, request_id, grant, method)
            durable = writer.execute(
                "SELECT 1 FROM archive_receipts WHERE request_id = ?", (request_id,),
            ).fetchone() is not None
            if durable != (result.status == "committed"):
                raise RuntimeError("reported outcome disagrees with persisted receipt")
            then_allowed, now_allowed = oracle(before), oracle(after)
            decision = grant.certificate.decision
            if ((decision is Decision.ALLOW and not then_allowed)
                    or (decision is Decision.BLOCK and then_allowed)
                    or result.status == "invalid_certificate"):
                raise RuntimeError("snapshot certificate disagrees with audit")
            if durable and not now_allowed and method != "snapshot_only":
                raise RuntimeError("atomic guard produced an invalid receipt")
            yield {
                "request_id": request_id, "budget": budget, "method": method,
                "before_bits": before, "after_bits": after, "changed_fields": changed,
                "transition": transition(then_allowed, now_allowed, changed),
                "initial_permission": then_allowed, "commit_permission": now_allowed,
                "certificate": asdict(grant.certificate),
                "receipt": asdict(result), "receipt_persisted": durable,
            }


def summarize(rows):
    counts = Counter()
    for row in rows:
        cert = row["certificate"]
        authorized = cert["decision"] == Decision.ALLOW
        current, persisted = row["commit_permission"], row["receipt_persisted"]
        counts["trials"] += 1
        counts["initial_allows"] += row["initial_permission"]
        counts["certified_allows"] += authorized
        counts["certified_blocks"] += cert["decision"] == Decision.BLOCK
        counts["deferred"] += cert["decision"] == Decision.DEFER
        counts["still_allowed_with_allow_certificate"] += current and authorized
        counts["revoked_with_allow_certificate"] += not current and authorized
        counts["safe_commits"] += current and persisted
        counts["invalid_commits"] += not current and persisted
        counts["guard_rejected_eligible"] += current and authorized and not persisted
        counts["eligible_without_allow_certificate"] += current and not authorized
        counts["receipt_guard_fields"] += len(row["receipt"]["checked_fields"])
        counts["observation_units"] += cert["spent"]
    return dict(sorted(counts.items()))


def generate(directory):
    with TemporaryDirectory(prefix="resumecert-archive-") as scratch:
        rows = list(run(Path(scratch) / "archive.sqlite3"))
    schedules = {(row["before_bits"], row["changed_fields"]) for row in rows}
    keys = {(row["budget"], row["method"], row["before_bits"], row["changed_fields"]) for row in rows}
    if len(schedules) != 512 or len(rows) != 7680 or len(keys) != len(rows):
        raise RuntimeError("incomplete or duplicate experimental grid")
    trace = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                    for row in rows).encode("ascii")
    summary = {
        "schema": "resumecert-archive-transfer-v1",
        "scope": "exploratory, deterministic SQLite archive receipts",
        "state_field_order": FIELDS,
        "state_count": 32,
        "schedules": len(schedules),
        "trials": len(rows),
        "same_snapshot_policy": "adaptive",
        "trace_sha256_uncompressed": sha256(trace).hexdigest(),
        "by_budget": {},
    }
    for budget in BUDGETS:
        summary["by_budget"][str(budget)] = {}
        for method in METHODS:
            selected = [r for r in rows if r["budget"] == budget and r["method"] == method]
            summary["by_budget"][str(budget)][method] = {
                "overall": summarize(selected),
                "transitions": {
                    label: summarize([row for row in selected if row["transition"] == label])
                    for label in sorted({r["transition"] for r in selected})
                },
            }
    (directory / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                     encoding="ascii", newline="\n")
    (directory / TRACES).write_bytes(gzip.compress(trace, mtime=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", type=Path)
    mode.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.record is not None:
        args.record.mkdir()
        generate(args.record)
        print(f"Recorded archive transfer artifacts in {args.record}")
    else:
        with TemporaryDirectory(prefix="resumecert-transfer-check-") as scratch:
            actual = Path(scratch)
            generate(actual)
            if (actual / SUMMARY).read_bytes() != (args.check / SUMMARY).read_bytes():
                parser.exit(1, "Archive transfer summary differs from recorded output\n")
            if gzip.decompress((actual / TRACES).read_bytes()) != gzip.decompress((args.check / TRACES).read_bytes()):
                parser.exit(1, "Archive transfer trace differs from recorded output\n")
        print("Archive transfer: summary and all 7680 traces match")


if __name__ == "__main__":
    main()
