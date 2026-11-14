# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise all canonical anchors with synthetic data, never task qualification.

Run with --output NEW_DIRECTORY; --verify-existing DIRECTORY replays the real
measure.py CLI against retained packets, fact sources and reports. No human
review, native environment, model, or provider is executed by this demonstration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
sys.path.insert(0, str(SCRIPTS))
from rubric_evidence_scoring import canonical_definitions, definitions_digest, value_digest  # noqa: E402

# Authored synthetic observations, independent of scorer predicates and tests.
VECTORS = {
    "DISC-01-A": (
        "task_artifacts",
        {"id": "a", "access": "accessible", "revision": "v1", "inspection_effect": "none"},
        {"access": "missing", "inspection_effect": "blocks"},
        {"revision": None},
    ),
    "DISC-01-B": (
        "grading_routes",
        {"id": "a", "declared_scorer": "s", "selected_scorer": "s", "configuration": "v1", "branch": "resolved"},
        {"selected_scorer": "other"},
        {"branch": "ambiguous"},
    ),
    "DISC-01-C": (
        "grading_dependencies",
        {
            "id": "a",
            "access": "accessible",
            "revision": "v1",
            "inspection_effect": "none",
            "reference_review": "resolved",
        },
        {"access": "denied", "inspection_effect": "blocks"},
        {"reference_review": "unresolved"},
    ),
    "DISC-02-A": (
        "configuration_fields",
        {
            "id": "model",
            "value": "model-v1",
            "source_identity": "config-v1",
            "availability": "sourced",
            "claim_limit": "none",
            "reported_value": "model-v1",
        },
        {"reported_value": "wrong", "claim_limit": "uninterpretable"},
        {"source_identity": None},
    ),
    "DISC-02-B": (
        "run_settings",
        {"id": "model", "declared": "m1", "observed": "m1", "run_identity": "run1", "disposition": "agrees"},
        {"observed": "m2", "disposition": "invalidates"},
        {"observed": None, "disposition": "unresolved"},
    ),
    "DISC-03-A": (
        "provider_validation",
        {
            "id": "route",
            "provider": "harbor",
            "adapter_revision": "v1",
            "manifest_identity": "m1",
            "validation": "accepted",
            "adapter": "compatible",
        },
        {"validation": "incompatible"},
        {"adapter": "unresolved"},
    ),
    "AUD-02": (
        "setup_differences",
        {
            "id": "budget",
            "left": "100",
            "right": "200",
            "rationale": "Scope permits accommodations",
            "disposition": "justified",
            "advantage": "none",
        },
        {"disposition": "unjustified", "advantage": "demonstrated"},
        {"disposition": "disputed"},
    ),
    "ENV-01": (
        "tool_probes",
        {
            "id": "read",
            "action": "read",
            "solver_identity": "solver",
            "expected": "allowed",
            "actual": "allowed",
            "probe": "call",
            "execution": "completed",
        },
        {"actual": "denied"},
        {"probe": "schema", "execution": "partial"},
    ),
    "ENV-02-A": (
        "components",
        {
            "id": "tool",
            "form": "real",
            "observed_revision": "v1",
            "approved_revision": None,
            "authorization": "not_required",
            "availability": "available",
        },
        {"availability": "missing"},
        {"availability": "partly_reconciled"},
    ),
    "ENV-03": (
        "external_state",
        {
            "id": "dataset",
            "captured_identity": "sha256-v1",
            "mode": "pinned",
            "live_bound": None,
            "observation": "within_bound",
            "outcome_relation": "interpretable",
        },
        {"observation": "drifted", "outcome_relation": "invalidated"},
        {"captured_identity": None, "observation": "capture_gap"},
    ),
    "CREATE-07": (
        "result_revisions",
        {
            "id": "result",
            "recorded_revision": "v1",
            "current_revision": "v1",
            "change": "unchanged",
            "disposition": "current",
            "lineage": "original",
        },
        {"current_revision": "v2", "change": "material", "disposition": "combined"},
        {"current_revision": "v2", "change": "material", "disposition": "stale"},
    ),
    "CREATE-03": (
        "contract_requirements",
        {
            "id": "format",
            "public_requirement": "JSON",
            "scorer_requirement": "JSON",
            "interpretation": "public",
            "resolution": "resolved",
        },
        {"scorer_requirement": "CSV", "resolution": "prevents_grading"},
        {"resolution": "ambiguous"},
    ),
    "CREATE-04": (
        "scored_conditions",
        {
            "id": "format",
            "condition": "JSON",
            "authority": "JSON",
            "solver_access": "accessible",
            "mapping": "supported",
        },
        {"authority": None, "solver_access": "denied", "mapping": "hidden"},
        {"mapping": "disputed"},
    ),
    "CREATE-05-A": (
        "scoring_authorities",
        {
            "id": "answer",
            "expected": "42",
            "authoritative": "42",
            "obligation": "exact result",
            "semantics": "resolved",
        },
        {"authoritative": "43", "semantics": "contradicted"},
        {"semantics": "disputed"},
    ),
    "AC-01": (
        "information_surfaces",
        {
            "id": "answer",
            "contract_role": "protected",
            "solver_read": "denied",
            "permitted_neighbor": "readable",
            "exposure": "none",
            "review": "complete",
        },
        {"solver_read": "readable", "exposure": "unintended"},
        {"review": "partial"},
    ),
    "AC-02": (
        "credit_controls",
        {
            "id": "shortcut",
            "class": "attack",
            "expected_credit": 0,
            "actual_credit": 0,
            "health": "healthy",
            "reach": "reached",
            "applicability": "applicable",
        },
        {"actual_credit": 1},
        {"reach": "unresolved"},
    ),
    "AC-03": (
        "receipt_controls",
        {
            "id": "stale",
            "receipt_kind": "stale",
            "consumer": "rejected",
            "native_validation": "invalid",
            "health": "healthy",
            "reach": "reached",
        },
        {"consumer": "credited"},
        {"reach": "unresolved"},
    ),
    "AC-04": (
        "reset_surfaces",
        {
            "id": "cache",
            "sharing": "isolated",
            "expected_state": "empty",
            "reset_state": "empty",
            "target_alone": "0",
            "target_after": "0",
            "consequence": "none",
            "inspection": "complete",
        },
        {"reset_state": "answer", "consequence": "answer_exposure"},
        {"inspection": "partial"},
    ),
}


def facts_for(ident: str, variant: int) -> dict[str, Any]:
    name, base, defect, partial = VECTORS[ident]
    row = {**base, **({0: defect, 1: partial}.get(variant, {}))}
    rows = [row]
    if variant == 1 and ident in ("DISC-02-B", "ENV-03", "CREATE-05-A"):
        rows.append({**base, "id": "established-neighbor"})
    if ident == "AC-02":
        rows.extend(
            {**base, "id": kind, "class": kind, "expected_credit": credit, "actual_credit": credit}
            for kind, credit in (("reference", 1), ("incorrect", 0), ("valid_alternative", 1))
        )
    if ident == "AC-03":
        rows.extend(
            [
                {**base, "id": "forged", "receipt_kind": "forged"},
                {
                    **base,
                    "id": "valid",
                    "receipt_kind": "current",
                    "consumer": "credited",
                    "native_validation": "valid",
                },
            ]
        )
    return {name: rows}


ROBUSTNESS = {
    "DISC-01-A": "inventory_reconstruction",
    "DISC-01-B": "route_reconstruction",
    "DISC-01-C": "dependency_reconstruction",
    "DISC-02-A": "manifest_reconstruction",
    "DISC-02-B": "configuration_repeat",
    "DISC-03-A": "compatibility_controls",
    "AUD-02": "matched_sensitivity",
    "ENV-01": "clean_access_controls",
    "ENV-02-A": "provisioning_reconciliation",
    "ENV-03": "replay_drift",
    "CREATE-07": "change_injection",
    "CREATE-03": "boundary_solutions",
    "CREATE-04": "blind_discovery",
    "CREATE-05-A": "authority_corroboration",
    "AC-01": "heldout_access",
    "AC-02": "heldout_credit",
    "AC-03": "consumer_boundary",
    "AC-04": "trial_order_controls",
}


def robustness_for(ident: str) -> dict[str, Any]:
    # All requested control roles must be present, not merely one green receipt.
    roles = {
        "DISC-01-A": ["inventory"],
        "DISC-01-B": ["selection"],
        "DISC-01-C": ["coverage", "access"],
        "DISC-02-A": ["manifest", "limitations"],
        "DISC-02-B": ["configuration", "control"],
        "DISC-03-A": ["clean_setup", "invalid_manifest"],
        "AUD-02": ["matched_setup", "sensitivity"],
        "ENV-01": ["reset", "error", "boundary"],
        "ENV-02-A": ["provisioning", "approval"],
        "ENV-03": ["replay", "deliberate_drift"],
        "CREATE-07": ["changed_invalidated", "unchanged_preserved", "lineage_preserved"],
        "CREATE-03": ["independent_solution", "valid_boundary"],
        "CREATE-04": ["blind_discovery", "solver_access"],
        "CREATE-05-A": ["reference", "scoring_contract"],
        "AC-01": ["independent_path", "heldout", "permitted_neighbor"],
        "AC-02": ["heldout_attack", "valid_alternative"],
        "AC-03": ["invalid_rejection", "valid_acceptance"],
        "AC-04": ["order", "session"],
    }[ident]
    outcomes = {
        "DISC-01-A": ["inventory_reconstructed"],
        "DISC-01-B": ["route_reconstructed"],
        "DISC-01-C": ["dependencies_complete", "dependencies_accessible"],
        "DISC-02-A": ["manifest_reconstructed", "limitations_preserved"],
        "DISC-02-B": ["configuration_reproduced", "configuration_control_agrees"],
        "DISC-03-A": ["clean_setup_accepted", "invalid_manifest_rejected"],
        "AUD-02": ["matched_setup_comparable", "sensitivity_bounded"],
        "ENV-01": ["reset_preserved_access", "error_contract_preserved", "access_boundary_preserved"],
        "ENV-02-A": ["provisioning_reconciled", "substitutions_authorized"],
        "ENV-03": ["replay_interpretable", "drift_detected"],
        "CREATE-07": ["changed_result_invalidated", "unchanged_result_preserved", "lineage_preserved"],
        "CREATE-03": ["public_solution_accepted", "valid_boundary_accepted"],
        "CREATE-04": ["conditions_discovered", "authority_accessible"],
        "CREATE-05-A": ["reference_agrees", "scoring_contract_agrees"],
        "AC-01": ["protected_read_denied", "heldout_read_denied", "permitted_read_allowed"],
        "AC-02": ["forbidden_credit_withheld", "valid_alternative_credited"],
        "AC-03": ["invalid_receipt_rejected", "valid_receipt_credited"],
        "AC-04": ["order_isolation_preserved", "session_isolation_preserved"],
    }[ident]
    return {
        ROBUSTNESS[ident]: [
            {
                "id": role,
                "role": role,
                "expected": outcome,
                "observed": outcome,
                "original_actor": "author-a",
                "corroborating_actor": "reviewer-b",
                "source_identity": f"independent-{role}",
                "execution": "completed",
                "scope": "same-task-revision",
            }
            for role, outcome in zip(roles, outcomes, strict=True)
        ]
    }


VARIANTS = {"score-0": 0, "score-1": 1, "score-2": 2, "score-3": 3, "missing": None}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def artifact(directory: Path, ident: str, document: Any, role: str = "evidence") -> dict[str, Any]:
    path = directory / f"{ident}.json"
    write_json(path, document)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "id": ident,
        "path": path.name,
        "digest": digest,
        "access": "available",
        "role": role,
        "revision": "synthetic-demo-v1",
        "source": "authored-synthetic-scoring-demo",
        "selection_origin": "explicit",
        "evidence": [{"path": path.name, "digest": digest}],
    }


def prepare(directory: Path, expected: int | None) -> None:
    directory.mkdir(parents=True)
    task = artifact(directory, "task", {"requirements": ["output exact JSON"]}, "task")
    subject = {"artifact_id": "task", "digest": task["digest"]}
    facts: dict[str, Any] = {}
    selected = []
    for ident in VECTORS:
        values = {VECTORS[ident][0]: None} if expected is None else facts_for(ident, min(expected, 2))
        if expected == 3:
            values.update(robustness_for(ident))
        if facts.keys() & values.keys():
            raise ValueError("demo fact names must be distinct")
        facts.update(values)
        selected.append(
            {
                "item_id": ident,
                "facts": {
                    name: {
                        "basis": "recorded_judgment",
                        "source": {"artifact_id": "facts", "pointer": f"/facts/{name}", "digest": value_digest(value)},
                        "support": [
                            {
                                "artifact_id": "task",
                                "pointer": "/requirements",
                                "digest": value_digest(["output exact JSON"]),
                            }
                        ],
                    }
                    for name, value in values.items()
                },
            }
        )
    source = artifact(
        directory,
        "facts",
        {
            "schema": "rubric-fact-source-v1",
            "subject": subject,
            "definitions_digest": definitions_digest(),
            "provenance": {
                "kind": "synthetic",
                "actor_id": "demo-fixture-author",
                "activity": "review",
                "limitations": [
                    "Authored synthetic observations; human review and native evidence NOT_RUN.",
                    "Synthetic scores NEVER qualify a task.",
                ],
            },
            "facts": facts,
        },
    )
    packet = artifact(
        directory,
        "packet",
        {
            "schema": "rubric-criterion-evidence-v1",
            "subject": subject,
            "definitions_digest": definitions_digest(),
            "assessment_scope": "synthetic-scoring-demonstration",
            "items": selected,
        },
    )
    selection = {"artifacts": [task, source, packet], "criterion_evidence": {"artifact_id": "packet"}}
    write_json(directory / "selection.json", selection)
    write_json(directory / "baseline-selection.json", {"artifacts": selection["artifacts"]})


def measure(directory: Path, *, baseline: bool = False, verify: bool = False) -> dict[str, Any]:
    prefix = "baseline" if baseline else "measure"
    selection = "baseline-selection.json" if baseline else "selection.json"
    command = [
        sys.executable,
        "-B",
        str(SCRIPTS / "measure.py"),
        "--root",
        str(directory),
        "--selection",
        str(directory / selection),
    ]
    if verify:
        command += ["--verify-existing", str(directory / f"{prefix}-report.json")]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    )
    log = prefix + ("-verify" if verify else "")
    (directory / f"{log}.stdout.json").write_text(result.stdout, encoding="utf-8")
    (directory / f"{log}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    write_json(
        directory / f"{log}-invocation.json",
        {
            "command": command,
            "returncode": result.returncode,
            "engine": "actual-measure.py-cli",
        },
    )
    report = json.loads(result.stdout)
    if result.returncode not in (0, 1) or report.get("schema") != "rubric-measurements-v2":
        raise ValueError(f"measurement rejected evidence: {directory.name}/{log}")
    if not verify:
        write_json(directory / f"{prefix}-report.json", report)
    return report


def validate_report(report: dict[str, Any], baseline: dict[str, Any], expected: int | None) -> None:
    rows = report.get("criterion_demonstration", {})
    if set(rows) != set(VECTORS) or report["items"] != baseline["items"]:
        raise ValueError("missing demonstration or synthetic evidence changed task qualification")
    for row in rows.values():
        if (
            row["score"] != expected
            or (expected is not None and type(row["score"]) is not int)
            or row["assessment_scope"] != "synthetic-scoring-demonstration"
            or row["evidence_strength"] != "E1"
            or row["native_execution_verified"] is not False
            or row["reviewer_authenticated"] is not False
            or row["execution_state"] != "NOT_RUN"
            or row["score_provenance"]["kind"] != "synthetic-anchor-application"
            or row["status"] != ("NOT_ASSESSED" if expected is None else "ASSESSED")
        ):
            raise ValueError("unexpected synthetic anchor or evidence claim")


def rejection_controls(output: Path, *, verify: bool) -> dict[str, str]:
    results = {}
    names = (
        "digest-tamper",
        "stale-definitions",
        "stale-subject",
        "promote-synthetic",
        "missing-report-rows",
        "alter-report-score",
    )
    for name in names:
        directory = output / "rejection-controls" / name
        if not verify:
            shutil.copytree(output / "run-a/score-3", directory)
            packet = read_json(directory / "packet.json")
            if name == "digest-tamper":
                source = read_json(directory / "facts.json")
                source["provenance"]["actor_id"] = "altered-actor"
                write_json(directory / "facts.json", source)
            elif name in ("stale-definitions", "stale-subject", "promote-synthetic"):
                if name == "stale-definitions":
                    packet["definitions_digest"] = "0" * 64
                elif name == "stale-subject":
                    packet["subject"]["digest"] = "0" * 64
                else:
                    packet["assessment_scope"] = "recorded-evidence-assessment"
                write_json(directory / "packet.json", packet)
                selection = read_json(directory / "selection.json")
                record = next(row for row in selection["artifacts"] if row["id"] == "packet")
                record["digest"] = hashlib.sha256((directory / "packet.json").read_bytes()).hexdigest()
                record["evidence"][0]["digest"] = record["digest"]
                write_json(directory / "selection.json", selection)
            else:
                report = read_json(directory / "measure-report.json")
                if name == "missing-report-rows":
                    del report["criterion_demonstration"]
                else:
                    report["criterion_demonstration"]["AC-01"]["score"] = 2
                write_json(directory / "measure-report.json", report)
        modes = (True,) if name in ("missing-report-rows", "alter-report-score") else (False, True)
        for replay in modes:
            try:
                measure(directory, verify=replay)
            except ValueError:
                prefix = "measure-verify" if replay else "measure"
                invocation = read_json(directory / f"{prefix}-invocation.json")
                report = read_json(directory / f"{prefix}.stdout.json")
                if (
                    invocation["returncode"] != 2
                    or report.get("code") != "selection.invalid"
                    or report.get("preflight_success") is not False
                ):
                    raise ValueError("rejection control did not fail at the measurement boundary") from None
            else:
                raise ValueError(f"invalid evidence was accepted: {name}")
        results[name] = "rejected"
    return results


def run(output: Path, *, verify: bool = False) -> dict[str, Any]:
    output = output.absolute()
    if set(canonical_definitions()) != set(VECTORS) or len(VECTORS) != 18:
        raise ValueError("demo does not cover the canonical criterion inventory")
    if not verify:
        output.mkdir(parents=True, exist_ok=False)
    projections = []
    for name in ("run-a", "run-b"):
        reports = {}
        for variant, expected in VARIANTS.items():
            directory = output / name / variant
            if not verify:
                prepare(directory, expected)
            report = measure(directory, verify=verify)
            baseline = measure(directory, baseline=True, verify=verify)
            validate_report(report, baseline, expected)
            reports[variant] = report
        projections.append(reports)
    if projections[0] != projections[1]:
        raise ValueError("fresh output roots produced different semantic reports")
    summary = {
        "schema": "criterion-scoring-demo-v1",
        "status": "passed",
        "assessment_scope": "synthetic-scoring-demonstration",
        "task_qualification": False,
        "human_review": "NOT_RUN",
        "native_execution": "NOT_RUN",
        "limitations": [
            "Synthetic scores NEVER qualify a task.",
            "Human judgments, native controls, independence and solver performance were not executed.",
        ],
        "engine": "actual-measure.py-cli",
        "criterion_count": len(VECTORS),
        "vector_count_per_root": len(VECTORS) * len(VARIANTS),
        "rejection_controls": rejection_controls(output, verify=verify),
        "definitions_digest": definitions_digest(),
        "semantic_results_equal": True,
        "results": {
            variant: {ident: row["score"] for ident, row in report["criterion_demonstration"].items()}
            for variant, report in projections[0].items()
        },
        "report_paths": [
            f"{name}/{variant}/measure-report.json" for name in ("run-a", "run-b") for variant in VARIANTS
        ],
    }
    if verify:
        if read_json(output / "summary.json") != summary:
            raise ValueError("retained summary disagrees with current evidence")
    else:
        write_json(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="new evidence directory (never overwritten)")
    mode.add_argument("--verify-existing", type=Path, help="replay retained evidence and compare reports")
    args = parser.parse_args()
    try:
        summary = run(args.verify_existing or args.output, verify=args.verify_existing is not None)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "schema": "criterion-scoring-demo-v1",
                    "status": "invalid",
                    "reason": str(error),
                    "task_qualification": False,
                }
            )
        )
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
