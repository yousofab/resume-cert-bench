"""Verify the source and case commitments without executing a held-out policy."""

from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resumecert.confirmatory.design import audit_design, build_design, load_plan  # noqa: E402

MANIFEST = ROOT / "study/confirmatory-v1/manifest.json"
TRACKED = (
    "study/confirmatory-v1/plan.json",
    "docs/research/CONFIRMATORY_PROTOCOL_V1.md",
    "docs/research/CONFIRMATORY_PROTOCOL_V1_FA.md",
    "resumecert/__init__.py",
    "resumecert/confirmatory/__init__.py",
    "resumecert/confirmatory/design.py",
    "resumecert/confirmatory/scoring.py",
    "resumecert/contract.py",
    "resumecert/inference.py",
    "resumecert/records.py",
    "resumecert/simulator.py",
    "resumecert/verifier.py",
    "resumecert/pilot/__init__.py",
    "resumecert/pilot/policies.py",
    "results/matched_pilot_fixtures_v1.json",
    "results/matched_pilot_v1.json",
    "scripts/verify_freeze.py",
)


def verify(manifest):
    if manifest["schema"] != "resumecert-confirmatory-freeze-v1":
        raise ValueError("unexpected manifest schema")
    pins = manifest["sha256"]
    if set(pins) != set(TRACKED):
        raise ValueError("incomplete source commitment")
    for path, expected in pins.items():
        if sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise ValueError(f"frozen source hash mismatch: {path}")
    plan = load_plan()
    if manifest["development_base_commit"] != plan["design_seed"].rsplit(":", 1)[1]:
        raise ValueError("development base and deterministic seed disagree")
    audit = audit_design(plan, *build_design(plan))
    if audit != manifest["design_audit"]:
        raise ValueError("contract, fixture, or design-audit commitment changed")
    return {
        "status": "pass", "pinned_artifacts": len(pins),
        "held_out_pairs": audit["primary_cases"] // 2,
        "held_out_cases": audit["primary_cases"],
        "controls": audit["controls"],
        "fixture_sha256": audit["fixture_sha256"],
        "held_out_policy_runs": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify(json.loads(MANIFEST.read_bytes())), sort_keys=True))
