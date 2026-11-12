"""Record or reproduce the complete ResumeCert confirmatory v1 sweep."""

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from traceback import format_exc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from verify_freeze import MANIFEST, verify  # noqa: E402
from resumecert.confirmatory.design import load_plan  # noqa: E402
from resumecert.confirmatory.execution import execute  # noqa: E402

FREEZE_COMMIT = "09b77a40880045f6c8f85d0ddf90c241635d7a61"
MANIFEST_HASH = "e2337c52ffb0aa7f2b7703ea22fe8b3baa385db163846aaa69e8616ead012a4c"
SUMMARY = "confirmatory_v1.json"
TRACES = "confirmatory_v1_runs.jsonl.gz"


def verify_commitment():
    contents = MANIFEST.read_bytes()
    if sha256(contents).hexdigest() != MANIFEST_HASH:
        raise ValueError("manifest differs from the registered Git commit")
    return verify(json.loads(contents))


def record(directory):
    directory.mkdir()
    partial = directory / "partial.jsonl"
    try:
        before = verify_commitment()
        with partial.open("wb") as sink:
            summary = execute(load_plan(), sink)
        if verify_commitment() != before:
            raise ValueError("source or case commitment changed during the sweep")
        summary["freeze"] = {"commit": FREEZE_COMMIT, "manifest_sha256": MANIFEST_HASH,
                             "pinned_artifacts": before["pinned_artifacts"],
                             "fixture_sha256": before["fixture_sha256"]}
        with (directory / TRACES).open("wb") as target:
            with gzip.GzipFile(fileobj=target, filename="", mode="wb", mtime=0) as compressed:
                with partial.open("rb") as source:
                    shutil.copyfileobj(source, compressed)
        (directory / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                         encoding="ascii", newline="\n")
    except Exception:
        (directory / "failure.txt").write_text(format_exc(), encoding="utf-8")
        raise
    partial.unlink()


def check(expected):
    with TemporaryDirectory(prefix="resumecert-confirmatory-") as scratch:
        actual = Path(scratch) / "run"
        record(actual)
        if (actual / SUMMARY).read_bytes() != (expected / SUMMARY).read_bytes():
            raise ValueError("summary differs from the published record")
        with gzip.open(actual / TRACES, "rb") as left, gzip.open(expected / TRACES, "rb") as right:
            while block := left.read(65536):
                if block != right.read(65536):
                    raise ValueError("trace differs from the published record")
            if right.read(1):
                raise ValueError("published trace has extra bytes")
    print("Confirmatory v1: summary and all uncompressed run traces match")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--record", type=Path, help="write one sweep to a new directory")
    action.add_argument("--check", type=Path, help="compare a reproduced sweep to this directory")
    options = parser.parse_args()
    if options.check is not None:
        check(options.check)
    else:
        record(options.record)
        print(f"Confirmatory v1: audited results written to {options.record}")


if __name__ == "__main__":
    main()
