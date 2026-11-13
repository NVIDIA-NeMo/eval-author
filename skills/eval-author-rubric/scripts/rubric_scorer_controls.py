# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bound the fixed refund panel to the existing canonical anti-cheat rubric."""

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from eval_author_shared.task_evidence import loads

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = Path(__file__).resolve().parents[1] / "references/anti-cheat.md"
ANCHORS = {
    "AC-01": "protected-information-stays-outside-the-solver-boundary",
    "AC-02": "shortcuts-do-not-earn-forbidden-credit",
    "AC-03": "credit-comes-from-current-trusted-results",
    "AC-04": "prior-trials-do-not-leak-answers-or-success",
}


def _runner():
    # This opt-in fixture integration requires the checkout, not an installed skill alone.
    if not (ROOT / "tools/run_scorer_controls.py").is_file():
        raise ValueError("refund controls require the repository fixture")
    sys.path.insert(0, str(ROOT / "tools"))
    import run_scorer_controls

    return run_scorer_controls


def project(root, selection):
    runner = _runner()
    selected = selection["scorer_controls"]
    if not isinstance(selected, dict) or set(selected) != {"evidence_root", "mode", "arm"}:
        raise ValueError("invalid scorer control selection")
    if selected["arm"] not in ("baseline", "reviewed") or selected["mode"] not in ("fixture", "native"):
        raise ValueError("unsupported scorer control selection")
    relative = selected["evidence_root"]
    if not isinstance(relative, str) or relative != ".":
        raise ValueError("select the exact evidence root")
    root = Path(root)
    retained = loads(runner.source_bytes(root / "selection.json", root=root))
    # Require the outer identity selection to name the exact retained inputs.
    outer = deepcopy(retained)
    for record in outer["artifacts"]:
        record["path"] = "inputs/" + record["path"]
        for ref in record["evidence"]:
            ref["path"] = "inputs/" + ref["path"]
    if any(selection.get(key) != value for key, value in outer.items()):
        raise ValueError("scorer controls are not the selected subject")
    native_report = None
    panel: dict[str, Any]
    if selected["mode"] == "fixture":
        panel = runner.run_panel(root, retained)
        saved = loads(runner.source_bytes(root / "panel.json", root=root))
        if runner.encoded(saved) != runner.encoded(panel):
            raise ValueError("retained panel differs from replay")
    else:
        import scorer_native

        native_report = scorer_native.summarize(root, 0)
        if native_report["status"] != "passed":
            raise ValueError("native evidence is incomplete or disagrees")
        panel = {
            "identity": native_report["identity"],
            "cases": [
                {
                    "id": check["id"],
                    "role": check["role"],
                    "expected": check["expected"],
                    "observed": check["results"],
                    "matched": check["status"] == "passed",
                }
                for check in native_report["checks"]
            ],
        }
    metadata_bytes = runner.source_bytes(root / "inputs/applicability.json", root=root)
    if metadata_bytes != runner.source_bytes(runner.FIXTURES / "applicability.json"):
        raise ValueError("applicability differs from fixed panel")
    metadata = loads(metadata_bytes)
    arm = selected["arm"]
    controls: list[dict[str, Any]] = []
    for case in panel["cases"]:
        observed = case["observed"][arm]
        intended = case["expected"]["reviewed"]
        operational = case["role"] == "operational"
        # Each arm has its own scale. Compare common checks, not vector shape.
        forbidden_checks = [
            key
            for key, check in observed["checks"].items()
            if key in intended["checks"] and check > intended["checks"][key]
        ]
        forbidden = not operational and (
            bool(forbidden_checks)
            or (intended["total_credit"] == 0 and observed["total_credit"] > 0)
            or (intended["accepted"] is False and observed["accepted"] is True)
        )
        healthy = observed["health"] == "ok"
        valid_accepted = intended["accepted"] is not True or observed["accepted"] is True
        controls.append(
            {
                "id": case["id"],
                "role": case["role"],
                "expected": case["expected"][arm],
                "observed": observed,
                "contract_intended": intended,
                "forbidden_credit": forbidden,
                "forbidden_checks": forbidden_checks,
                "defense_pass": None
                if operational
                else healthy and not forbidden and valid_accepted and case["matched"],
            }
        )
    scorer_digest = next(row["digest"] for row in retained["artifacts"] if row["id"] == "scorer")
    binding = {"scorer_sha256": scorer_digest, "grader": arm}
    indexed = {row["id"]: row for row in controls}
    declared = set(metadata["required_valid_controls"])
    declared.update(ident for ids in metadata["attack_classes"].values() for ident in ids)
    if declared != {row["id"] for row in controls if row["role"] != "operational"}:
        raise ValueError("applicability inventory does not cover the fixed controls")
    class_counts = {
        name: {
            "applicable": len(ids),
            "healthy": sum(indexed[ident]["observed"]["health"] == "ok" for ident in ids),
            "intended": sum(indexed[ident]["defense_pass"] is True for ident in ids),
        }
        for name, ids in metadata["attack_classes"].items()
    }
    return {
        "status": "pass",
        "mode": selected["mode"],
        "arm": arm,
        "native": native_report,
        "subject_revision": "sha256:" + hashlib.sha256(runner.encoded(binding).encode()).hexdigest(),
        "binding": binding,
        "controls": controls,
        "applicability": metadata,
        "applicability_digest": "sha256:" + hashlib.sha256(metadata_bytes).hexdigest(),
        "source_identity": panel["identity"]["measurements"]["identity"],
        "counts": {
            "applicable": sum(row["role"] != "operational" for row in controls),
            "healthy": sum(row["role"] != "operational" and row["observed"]["health"] == "ok" for row in controls),
            "intended": sum(row["defense_pass"] is True for row in controls),
            "operational": sum(row["role"] == "operational" for row in controls),
        },
        "attack_class_counts": class_counts,
        "required_valid_controls_passed": all(
            indexed[ident]["defense_pass"] is True for ident in metadata["required_valid_controls"]
        ),
    }


def assess_controls(measurements, selection, root):
    observed = project(root, selection)
    if json.dumps(observed, sort_keys=True) != json.dumps(measurements.get("scorer_controls"), sort_keys=True):
        raise ValueError("scorer control measurement failed revalidation")
    reference = REFERENCE.read_text()
    rows = {}
    for ident, anchor in ANCHORS.items():
        definition_ref = f"anti-cheat.md#{ident.lower()}-{anchor}"
        section = reference.split(f"## {ident}:", 1)[1].split("\n## ", 1)[0]
        scoring = section.split("**Scoring:**", 1)[1].split("\n\n", 1)[0].strip()
        rows[ident] = {
            "item_id": ident,
            "pillar": "validity",
            "status": "NOT_ASSESSED",
            "assessment_state": "NOT_ASSESSED",
            "score": None,
            "evidence_strength": None,
            "definition_ref": definition_ref,
            "canonical_scoring": scoring,
            "subject_id": observed["arm"],
            "subject_revision": observed["subject_revision"],
            "evidence_refs": [],
            "check_results": [],
            "remaining_gaps": ["No criterion-specific evidence assessment was selected."],
        }
    rows["AC-03"]["remaining_gaps"] = [
        "AC-03 was not selected in this bounded assessment. The baseline solver-authored "
        "success defect can also support AC-03=0 under the canonical shared-finding rule; "
        "this null does not mean evidence is absent."
    ]
    row = rows["AC-02"]
    defect = any(control["forbidden_credit"] for control in observed["controls"])
    native = observed["native"] is not None
    gaps = (
        ([] if native else observed["applicability"]["fixture_gaps"])
        + list(observed["applicability"]["unresolved_classes"].values())
        + observed["applicability"]["remaining_gaps"]
    )
    row.update(
        status="ASSESSED",
        assessment_state="ASSESSED",
        # This fixed panel has unresolved applicable controls even with native receipts.
        score=0 if defect else 1,
        evidence_strength="E2",
        evidence_refs=["#/measurements/scorer_controls"],
        remaining_gaps=gaps,
        check_results=[
            {
                "method": "review",
                "outcome": "PASS",
                "reviewer_kind": "agent",
                "definition_ref": row["definition_ref"],
                "evidence_refs": ["#/measurements/scorer_controls/applicability"],
            },
            {
                "method": "probe",
                "outcome": "PASS" if native else "NOT_RUN",
                "definition_ref": row["definition_ref"],
                "remaining_gap": None if native else gaps[0],
            },
            {
                "method": "deterministic",
                "outcome": "FAIL" if defect else "PASS",
                "definition_ref": row["definition_ref"],
                "evidence_refs": ["#/measurements/scorer_controls/controls"],
            },
        ],
    )
    return rows
