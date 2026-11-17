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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rubric_demo_common import ArtifactRecord, artifact_record, run_measure  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
sys.path.insert(0, str(SCRIPTS))
from rubric_evidence_scoring import canonical_definitions, definitions_digest, value_digest  # noqa: E402
from rubric_identity import read_selected  # noqa: E402

# Authored synthetic fixtures shared by the demo and tests, independent of scorer predicates.
VECTORS = {
    "provenance.task-artifact-access": (
        "task_artifacts",
        {"id": "a", "access": "accessible", "revision": "v1", "inspection_effect": "none"},
        {"access": "missing", "inspection_effect": "blocks"},
        {"revision": None},
    ),
    "provenance.grading-path": (
        "grading_routes",
        {"id": "a", "declared_scorer": "s", "selected_scorer": "s", "configuration": "v1", "branch": "resolved"},
        {"selected_scorer": "other"},
        {"branch": "ambiguous"},
    ),
    "provenance.grading-dependency-access": (
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
    "provenance.declared-configuration": (
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
    "provenance.observed-configuration": (
        "run_settings",
        {"id": "model", "declared": "m1", "observed": "m1", "run_identity": "run1", "disposition": "agrees"},
        {"observed": "m2", "disposition": "invalidates"},
        {"observed": None, "disposition": "unresolved"},
    ),
    "provenance.runtime-route": (
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
    "provenance.comparison-fairness": (
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
    "provenance.solver-tool-access": (
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
    "provenance.dependency-substitution": (
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
    "provenance.external-state": (
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
    "provenance.evidence-revision-consistency": (
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
    "task-contract.clear-contract": (
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
    "task-contract.discoverable-requirements": (
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
    "task-contract.scoring-authority": (
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
    if variant == 1 and ident in (
        "provenance.observed-configuration",
        "provenance.external-state",
        "task-contract.scoring-authority",
    ):
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
    "provenance.task-artifact-access": "inventory_reconstruction",
    "provenance.grading-path": "route_reconstruction",
    "provenance.grading-dependency-access": "dependency_reconstruction",
    "provenance.declared-configuration": "manifest_reconstruction",
    "provenance.observed-configuration": "configuration_repeat",
    "provenance.runtime-route": "compatibility_controls",
    "provenance.comparison-fairness": "matched_sensitivity",
    "provenance.solver-tool-access": "clean_access_controls",
    "provenance.dependency-substitution": "provisioning_reconciliation",
    "provenance.external-state": "replay_drift",
    "provenance.evidence-revision-consistency": "change_injection",
    "task-contract.clear-contract": "boundary_solutions",
    "task-contract.discoverable-requirements": "blind_discovery",
    "task-contract.scoring-authority": "authority_corroboration",
    "AC-01": "heldout_access",
    "AC-02": "heldout_credit",
    "AC-03": "consumer_boundary",
    "AC-04": "trial_order_controls",
}


def robustness_for(ident: str) -> dict[str, Any]:
    # All requested control roles must be present, not merely one green receipt.
    roles = {
        "provenance.task-artifact-access": ["inventory"],
        "provenance.grading-path": ["selection"],
        "provenance.grading-dependency-access": ["coverage", "access"],
        "provenance.declared-configuration": ["manifest", "limitations"],
        "provenance.observed-configuration": ["configuration", "control"],
        "provenance.runtime-route": ["clean_setup", "invalid_manifest"],
        "provenance.comparison-fairness": ["matched_setup", "sensitivity"],
        "provenance.solver-tool-access": ["reset", "error", "boundary"],
        "provenance.dependency-substitution": ["provisioning", "approval"],
        "provenance.external-state": ["replay", "deliberate_drift"],
        "provenance.evidence-revision-consistency": ["changed_invalidated", "unchanged_preserved", "lineage_preserved"],
        "task-contract.clear-contract": ["independent_solution", "valid_boundary"],
        "task-contract.discoverable-requirements": ["blind_discovery", "solver_access"],
        "task-contract.scoring-authority": ["reference", "scoring_contract"],
        "AC-01": ["independent_path", "heldout", "permitted_neighbor"],
        "AC-02": ["heldout_attack", "valid_alternative"],
        "AC-03": ["invalid_rejection", "valid_acceptance"],
        "AC-04": ["order", "session"],
    }[ident]
    outcomes = {
        "provenance.task-artifact-access": ["inventory_reconstructed"],
        "provenance.grading-path": ["route_reconstructed"],
        "provenance.grading-dependency-access": ["dependencies_complete", "dependencies_accessible"],
        "provenance.declared-configuration": ["manifest_reconstructed", "limitations_preserved"],
        "provenance.observed-configuration": ["configuration_reproduced", "configuration_control_agrees"],
        "provenance.runtime-route": ["clean_setup_accepted", "invalid_manifest_rejected"],
        "provenance.comparison-fairness": ["matched_setup_comparable", "sensitivity_bounded"],
        "provenance.solver-tool-access": [
            "reset_preserved_access",
            "error_contract_preserved",
            "access_boundary_preserved",
        ],
        "provenance.dependency-substitution": ["provisioning_reconciled", "substitutions_authorized"],
        "provenance.external-state": ["replay_interpretable", "drift_detected"],
        "provenance.evidence-revision-consistency": [
            "changed_result_invalidated",
            "unchanged_result_preserved",
            "lineage_preserved",
        ],
        "task-contract.clear-contract": ["public_solution_accepted", "valid_boundary_accepted"],
        "task-contract.discoverable-requirements": ["conditions_discovered", "authority_accessible"],
        "task-contract.scoring-authority": ["reference_agrees", "scoring_contract_agrees"],
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


def artifact(directory: Path, ident: str, document: Any, role: str = "evidence") -> ArtifactRecord:
    path = directory / f"{ident}.json"
    write_json(path, document)
    return artifact_record(
        directory, ident, path.name, role, revision="synthetic-demo-v1", source="authored-synthetic-scoring-demo"
    )


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
    log = prefix + ("-verify" if verify else "")
    report, _ = run_measure(
        directory,
        prefix=prefix,
        selection_name="baseline-selection.json" if baseline else "selection.json",
        verify=verify,
        record_invocation=True,
        sort_keys=True,
        error_message=f"measurement rejected evidence: {directory.name}/{log}",
    )
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
            or row["evidence_strength"] != (None if expected is None else "E1")
            or row["native_execution_verified"] is not False
            or row["reviewer_authenticated"] is not False
            or row["execution_state"] != "NOT_RUN"
            or row["score_provenance"]["kind"] != "synthetic-anchor-application"
            or row["status"] != ("NOT_ASSESSED" if expected is None else "ASSESSED")
        ):
            raise ValueError("unexpected synthetic anchor or evidence claim")


REJECTION_CONTROLS = (
    "digest-tamper",
    "stale-definitions",
    "stale-subject",
    "promote-synthetic",
    "missing-report-rows",
    "alter-report-score",
)
CONTROL_FILES = (
    "task.json",
    "facts.json",
    "packet.json",
    "selection.json",
    "baseline-selection.json",
    "measure-report.json",
    "baseline-report.json",
)


def mutate_control(directory: Path, name: str) -> None:
    if name == "digest-tamper":
        source = read_json(directory / "facts.json")
        source["provenance"]["actor_id"] = "altered-actor"
        write_json(directory / "facts.json", source)
    elif name in ("stale-definitions", "stale-subject", "promote-synthetic"):
        packet = read_json(directory / "packet.json")
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
    elif name in ("missing-report-rows", "alter-report-score"):
        report = read_json(directory / "measure-report.json")
        if name == "missing-report-rows":
            del report["criterion_demonstration"]
        else:
            report["criterion_demonstration"]["AC-01"]["score"] = 2
        write_json(directory / "measure-report.json", report)
    else:
        raise ValueError("unknown rejection control")


def validate_control(directory: Path, expected: Path) -> None:
    # These are fixed generated fixtures, not arbitrary equivalent JSON. Bound
    # each no-follow read by the trusted bytes; never trust a retained manifest.
    for filename in CONTROL_FILES:
        wanted = (expected / filename).read_bytes()
        actual, reason = read_selected(directory, filename, limit=len(wanted))
        if reason or actual != wanted:
            raise ValueError(f"rejection control fixture mismatch: {directory.name}/{filename}")


def rejection_controls(output: Path, *, verify: bool) -> dict[str, str]:
    results = {}
    with tempfile.TemporaryDirectory(prefix="criterion-controls-") as temporary:
        root = Path(temporary).resolve()
        trusted = root / "score-3"
        prepare(trusted, 3)
        measure(trusted)
        measure(trusted, baseline=True)
        for name in REJECTION_CONTROLS:
            expected = root / name
            shutil.copytree(trusted, expected)
            mutate_control(expected, name)
            directory = output / "rejection-controls" / name
            if not verify:
                shutil.copytree(output / "run-a/score-3", directory)
                mutate_control(directory, name)
            # An unrelated selection.invalid (including absent evidence) is not
            # proof that the named mutation reached the measurement boundary.
            validate_control(directory, expected)
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
    if verify:
        # Preserve no-follow admission; copied symlinks must not redirect log writes.
        if output.is_symlink() or any(path.is_symlink() for path in output.rglob("*")):
            raise ValueError("retained evidence contains a symlink")
        # The owning CLI retains invocation logs, so replay only a private copy.
        with tempfile.TemporaryDirectory(prefix="criterion-verify-") as temporary:
            root = Path(temporary).resolve()
            copied = root / "evidence"
            shutil.copytree(output, copied, symlinks=True)
            for path in (copied, *copied.rglob("*")):
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o200)
            return _run(copied, verify=True)
    return _run(output, verify=False)


def _run(output: Path, *, verify: bool) -> dict[str, Any]:
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
        "schema": "criterion-scoring-demo-v2",
        "status": "passed",
        "assessment_scope": "synthetic-scoring-demonstration",
        "task_qualification": False,
        "human_review": "NOT_RUN",
        "native_execution": "NOT_RUN",
        "limitations": [
            "Synthetic scores NEVER qualify a task.",
            "This demo does not perform human judgments, run native controls, establish independence or measure solver performance.",
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
                    "schema": "criterion-scoring-demo-v2",
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
