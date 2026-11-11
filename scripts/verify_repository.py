"""Reproduce the toy witness and check the bytes of the review artifacts."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "ARTIFACTS.sha256"
EXPECTED = {
    "docs/proposal/ResumeCert_Research_Proposal_FA.pdf",
    "docs/proposal/ResumeCert_Research_Proposal_FA.docx",
    "assets/institutions/fardiat-logo.png",
    "assets/institutions/islamic-azad-university-logo.png",
    "proof/witness_result.json",
}


def verify():
    entries = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if name not in EXPECTED or name in entries:
            raise ValueError(f"Unexpected or duplicate artifact: {name}")
        entries[name] = digest
    if entries.keys() != EXPECTED:
        raise ValueError("Missing artifact in the manifest")
    for name, digest in entries.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"SHA-256 mismatch: {name}")

    witness = ROOT / "proof" / "witness.py"
    output = subprocess.check_output([sys.executable, str(witness)], cwd=ROOT)
    observed = json.loads(output)
    expected = json.loads((ROOT / "proof" / "witness_result.json").read_bytes())
    if observed != expected:
        raise ValueError("The witness did not reproduce the recorded result")

    print(json.dumps({
        "status": "pass",
        "verified_artifacts": len(entries),
        "witness_worlds": observed["possible_worlds"],
        "scope": "constructed witness; no benchmark or model evaluation",
    }))


if __name__ == "__main__":
    verify()
