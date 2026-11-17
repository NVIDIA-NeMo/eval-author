# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise task mutations through the ordinary assessment without native runs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import example_fixtures as fixtures

# Import the owning entrypoint first so its shared runtime paths are installed.
import rubric_table
from rubric_evidence_scoring import canonical_definitions, definitions_digest

SCRIPTS = Path(__file__).resolve().parent
TASK = "assessment/task/task.toml"


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def invoke(argv: list[str], directory: Path, label: str) -> dict[str, Any]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-B", *argv]
    completed = subprocess.run(command, capture_output=True, timeout=180, env=env)
    (directory / (label + ".stdout")).write_bytes(completed.stdout)
    (directory / (label + ".stderr")).write_bytes(completed.stderr)
    result = {"command": command, "exit": completed.returncode}
    write_json(directory / (label + ".command.json"), result)
    return result


def assess(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    selection_path = root / "selection.json"
    write_json(selection_path, selection)
    report = root / "report"
    result = invoke(
        [
            str(SCRIPTS / "rubric_table.py"),
            "--root",
            str(root),
            "--selection",
            str(selection_path),
            "--output",
            str(report),
        ],
        root,
        "assess",
    )
    result["report"] = str(report / "assessment.json")
    result["replay_exit"] = None
    if result["exit"] not in (0, 1) or not (report / "assessment.json").is_file():
        result["state"] = "blocked"
        return result
    replay = invoke(
        [str(SCRIPTS / "rubric_table.py"), "--root", str(root), "--verify-existing", str(report)], root, "replay"
    )
    result["replay_exit"] = replay["exit"]
    result["state"] = "assessed" if replay["exit"] == result["exit"] else "invalid_replay"
    return result


def observed_rows(assessment: dict[str, Any]) -> dict[str, Any]:
    if assessment["state"] != "assessed":
        return {}
    report = json.loads(Path(assessment["report"]).read_text())["report"]
    definitions = canonical_definitions()
    if report["items"].keys() != definitions.keys():
        raise ValueError("incomplete criterion inventory")
    result = {}
    for ident, definition in definitions.items():
        row = report["items"][ident]
        checks = row["check_results"]
        if [check.get("check_id", check["id"]) for check in checks] != [check["id"] for check in definition["checks"]]:
            raise ValueError("incomplete check inventory")
        score = row["score"]
        if score is not None and (type(score) is not int or score not in range(4)):
            raise ValueError("invalid observed score")
        result[ident] = {
            "score": score,
            "checks": {check["id"]: check["outcome"] for check in checks},
            "evidence_strength": row.get("evidence_strength"),
            "remaining_gaps": row.get("remaining_gaps", []),
        }
    return result


def comparison_projection(rows: dict[str, Any]) -> dict[str, Any]:
    return {ident: {key: row[key] for key in ("score", "checks", "evidence_strength")} for ident, row in rows.items()}


def compare_expectations(expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, str]:
    result = {}
    for ident, target in expected.items():
        if ident not in observed:
            result[ident] = "blocked"
        elif type(observed[ident]["score"]) is type(target) and observed[ident]["score"] == target:
            result[ident] = "matched"
        elif observed[ident]["score"] is None:
            result[ident] = "unassessed"
        else:
            result[ident] = "mismatch"
    return result


EXTRA_CASES = {
    "source-inspection": ("provenance.task-artifact-access", 2),
    "withhold-reference": ("AC-02", None),
    "withhold-assessment-evidence": (None, None),
    "unselected-contract": ("provenance.task-artifact-access", 1),
    "missing-grading-input": ("provenance.grading-dependency-access", 0),
    "declared-scripted": ("provenance.declared-configuration", 2),
    "declared-model-unconfigured": ("provenance.declared-configuration", 1),
}


def expectation(mutation: str) -> dict[str, Any]:
    if mutation == "source-inspection":
        return {
            "id": mutation,
            "target_criteria": [
                "provenance.task-artifact-access",
                "provenance.grading-path",
                "provenance.grading-dependency-access",
            ],
            "intended_score": None,
            "expected_scores": {
                "provenance.task-artifact-access": 2,
                "provenance.grading-path": 1,
                "provenance.grading-dependency-access": 1,
            },
            "collateral_criteria": [],
            "rerun_controls": ["ordinary_source_assessment"],
            "unchanged_neighbors": [],
            "category": "source_only_evidence_scope",
        }
    if mutation == "withhold-reference":
        return json.loads((fixtures.EXAMPLE / "expectations/withhold-reference.json").read_text())
    if mutation not in EXTRA_CASES:
        if mutation not in {path.stem for path in (fixtures.EXAMPLE / "mutations").glob("*.json")}:
            raise ValueError("unknown mutation")
        return json.loads((fixtures.EXAMPLE / "expectations" / (mutation + ".json")).read_text())
    ident, score = EXTRA_CASES[mutation]
    return {
        "id": mutation,
        "target_criteria": [ident] if ident else list(canonical_definitions()),
        "intended_score": score,
        "collateral_criteria": [],
        "rerun_controls": ["ordinary_source_assessment"],
        "unchanged_neighbors": ["fixed_assessment_request"],
        "category": "missing_evidence" if ident is None else "source_or_selection_change",
    }


def prepare_selection(root: Path, mutation: str) -> dict[str, Any]:
    if mutation == "missing-grading-input":
        path = root / "assessment/task/tests/test.sh"
        original = path.read_text()
        if "--contract /app/contract.json" not in original:
            raise ValueError("unsupported verifier launch")
        path.write_text(original.replace("--contract /app/contract.json", "--contract /tests/absent-contract.json"))
    selected = rubric_table.select_task(root, TASK)
    if mutation == "withhold-assessment-evidence":
        # Keep the subject identity, but deny every selected read. No artifact is deleted.
        for row in selected["artifacts"]:
            row["access"] = "unresolved"
    elif mutation == "unselected-contract":
        selected["artifacts"] = [
            row for row in selected["artifacts"] if not row["path"].endswith("/environment/contract.json")
        ]
    elif mutation in ("declared-scripted", "declared-model-unconfigured"):
        source = root / "source-selection.json"
        write_json(source, selected)
        prepared = invoke(
            [
                str(SCRIPTS / "rubric_example_assessment.py"),
                "--root",
                str(root),
                "--selection",
                str(source),
                "--agent",
                "nop" if mutation == "declared-scripted" else "model",
                "--output",
                str(root / "configuration"),
            ],
            root,
            "configuration",
        )
        if prepared["exit"]:
            raise ValueError("configuration preparation blocked")
        selected = json.loads((root / "configuration/selection.json").read_text())
    return selected


def verify_case_input(root: Path, case: str) -> None:
    files, _ = fixtures._selected("base" if case in EXTRA_CASES else case)
    if case == "missing-grading-input":
        content, mode = files["tests/test.sh"]
        files["tests/test.sh"] = (
            content.replace(b"--contract /app/contract.json", b"--contract /tests/absent-contract.json"),
            mode,
        )
    if fixtures._snapshot(root / "assessment/task") != files:
        raise ValueError("retained mutation differs from trusted recipe")
    if (root / "assessment/request.md").read_text() != fixtures.ASSESSMENT_REQUEST:
        raise ValueError("assessment request changed")
    selected = json.loads((root / "selection.json").read_text())
    if case not in {"declared-scripted", "declared-model-unconfigured"}:
        wanted = rubric_table.select_task(root, TASK)
        if case == "withhold-assessment-evidence":
            for row in wanted["artifacts"]:
                row["access"] = "unresolved"
        elif case == "unselected-contract":
            wanted["artifacts"] = [
                row for row in wanted["artifacts"] if not row["path"].endswith("/environment/contract.json")
            ]
        if selected != wanted:
            raise ValueError("retained evidence scope differs from recipe")
    if case == "withhold-reference":
        withheld = root.parent / "withheld/assessment/local-results.json"
        if withheld.exists() and "reference" in json.loads(withheld.read_text())["cases"]:
            raise ValueError("withheld reference was restored")


def run_source_case(output: Path, mutation: str, *, local_controls: bool = False) -> dict[str, Any]:
    output = fixtures._separate_output(output)
    definition_digest = definitions_digest()
    original = fixtures.tree_identity(fixtures.BASE)
    catalog = expectation(mutation)
    expected = catalog.get("expected_scores", dict.fromkeys(catalog["target_criteria"], catalog["intended_score"]))
    output.mkdir(mode=0o700)
    (output / "private").mkdir(mode=0o700)
    write_json(output / "private/expectation.json", catalog)
    baseline = output / "baseline"
    changed = output / "changed"
    repair = output / "repair"
    fixtures.materialize(baseline)
    manifest = fixtures.materialize(changed, "base" if mutation in EXTRA_CASES else mutation)
    fixtures.restore_to_base(changed, repair)
    selections = {
        name: prepare_selection(root, mutation if name == "changed" else "base")
        for name, root in (("baseline", baseline), ("changed", changed), ("repair", repair))
    }
    manifest["task_sha256"] = fixtures.tree_identity(changed / "assessment/task")
    if mutation == "missing-grading-input":
        manifest["changed_paths"] = ["tests/test.sh"]
    assessments = {
        name: assess(root, selections[name])
        for name, root in (("baseline", baseline), ("changed", changed), ("repair", repair))
    }
    controls: dict[str, Any] = {"state": "not_run", "assessment_consumed": False}
    if local_controls and mutation not in EXTRA_CASES and mutation != "missing-contract":
        for name, root in (("baseline", baseline), ("changed", changed), ("repair", repair)):
            controls[name] = fixtures.run_local_controls(root, output / (name + "-controls"))
        controls["state"] = "executed_local_not_native"
        controls["repair_matches_baseline"] = controls["baseline"] == controls["repair"]
        controls["unchanged_neighbors"] = {
            name: controls["baseline"]["cases"][name] == controls["changed"]["cases"].get(name)
            for name in catalog["unchanged_neighbors"]
            if name in controls["baseline"]["cases"]
        }
    if local_controls and mutation == "withhold-reference":
        controls["baseline"] = fixtures.run_local_controls(baseline, output / "baseline-controls")
        controls["withheld"] = fixtures.mutate_evidence(
            changed, output / "baseline-controls", output / "withheld", "withhold-reference"
        )
        controls["repair"] = fixtures.run_local_controls(repair, output / "repair-controls")
        controls["state"] = "executed_local_not_native"
        controls["repair_matches_baseline"] = controls["baseline"] == controls["repair"]
    semantic: dict[str, Any] = {"assessment_consumed": False, "state": "not_admitted"}
    inspections = {}
    for name, assessment in assessments.items():
        if assessment["state"] == "assessed":
            report = json.loads(Path(assessment["report"]).read_text())["report"]
            admitted = report["measurements"].get("refund_contract")
            if admitted:
                inspections[name] = admitted["task-contract.scoring-authority"]["source_inspection"]
    if set(inspections) == {"baseline", "changed", "repair"}:
        semantic = {
            "assessment_consumed": True,
            "state": "executed_local_not_native",
            "variant": inspections["changed"]["variant"],
            "controls": inspections["changed"]["controls"],
            "repair_matches_baseline": inspections["baseline"]["controls"] == inspections["repair"]["controls"],
            "unchanged_neighbors": {
                name: control == inspections["changed"]["controls"][name]
                for name, control in inspections["baseline"]["controls"].items()
            },
        }
    rows = {name: observed_rows(value) for name, value in assessments.items()}
    before, after = comparison_projection(rows["baseline"]), comparison_projection(rows["changed"])
    changed_ids = [ident for ident in before if before[ident] != after.get(ident)]
    result = {
        "semantic_controls": semantic,
        "controls": controls,
        "selection_changes": {"baseline": selections["baseline"], "changed": selections["changed"]},
        "case": mutation,
        "scope": "prequalification_source_assessment",
        "base_qualified": bool(rows["baseline"]) and all(row["score"] == 3 for row in rows["baseline"].values()),
        "expected": expected,
        "observed": rows["changed"],
        "baseline_observed": rows["baseline"],
        "comparison": compare_expectations(expected, rows["changed"]),
        "assessment": assessments["changed"],
        "baseline_assessment": assessments["baseline"],
        "intended_collateral": catalog["collateral_criteria"],
        "actual_collateral": [ident for ident in changed_ids if ident not in expected],
        "unchanged_criteria": [ident for ident in before if ident in after and before[ident] == after[ident]],
        "comparison_scope": "Score, check outcomes and evidence strength. Evidence references remain in full reports.",
        "changed_paths": manifest["changed_paths"],
        "rerun_controls": catalog["rerun_controls"],
        "repair": {
            "assessment": assessments["repair"],
            "fresh_assessment": True,
            "matches_baseline": bool(before) and before == comparison_projection(rows["repair"]),
            "native_regrading": False,
        },
        "invariants": {
            "definitions_unchanged": definition_digest == definitions_digest(),
            "definitions_sha256": definition_digest,
            "request_unchanged": all(
                (root / "assessment/request.md").read_text() == fixtures.ASSESSMENT_REQUEST
                for root in (baseline, changed, repair)
            ),
            "request_sha256": hashlib.sha256(fixtures.ASSESSMENT_REQUEST.encode()).hexdigest(),
            "original_preserved": original == fixtures.tree_identity(fixtures.BASE),
        },
        "freshness": {
            "task_sha256": manifest["task_sha256"],
            "baseline_sha256": original,
            "native_evidence": "not_selected",
            "repair_sha256": fixtures.tree_identity(repair / "assessment/task"),
        },
        "blockers": [
            "The base is not qualified. These are preparation runs, not mutations of a qualified base.",
            "Only admitted refund source controls feed semantic defect scores. Optional subprocess controls remain separate. No new native execution runs here.",
        ],
    }
    result["result_path"] = str(output / "result.json")
    write_json(output / "result.json", result)
    return result


def run_audit_cases(report_path: Path, output: Path) -> dict[str, Any]:
    from copy import deepcopy

    from rubric_audit_companion import _copy_selection, exercise

    output = fixtures._separate_output(output, report_path)
    output.mkdir(mode=0o700)
    result: dict[str, Any] = {
        "case": "audit-scope-and-invalidation",
        "state": "blocked",
        "observed": {},
        "expected": {"provenance.evidence-revision-consistency": 3},
        "base_qualified": False,
        "source_report": str(report_path),
        "scenarios": [],
        "native_regrading": False,
    }
    try:
        saved = json.loads(report_path.read_text())
        root = Path(saved["root"])
        selected = deepcopy(saved["selection"])
        selected.pop("audit_companion", None)
        isolated = output / "selected"
        _copy_selection(root, selected, isolated)
        baseline_selection = deepcopy(selected)
        baseline_selection["audit_companion"] = {"mode": "inspect"}
        baseline = assess(isolated, baseline_selection)
        result["baseline_assessment"] = baseline
        if baseline["state"] != "assessed":
            raise ValueError("current native source admission failed; no new native run attempted")
        evidence = exercise(isolated, selected, output / "invalidation")
        result["invalidation_tests"] = evidence["invalidation_tests"]
        result["consumer_rejections"] = []
        for test in evidence["invalidation_tests"]:
            rejected_root = output / "invalidation" / test["id"]
            rejected = assess(rejected_root, baseline_selection)
            restored_root = output / "invalidation" / (test["id"] + "-fresh-counterpart")
            restored = assess(restored_root, baseline_selection)
            if rejected["exit"] != 2 or restored["state"] != "assessed":
                raise ValueError("ordinary stale rejection or recovery failed")
            result["consumer_rejections"].append(
                {
                    "case": test["id"],
                    "rejected": rejected,
                    "repair": restored,
                    "observed_score": None,
                    "rejection_kind": "invalid_evidence_not_criterion_zero",
                }
            )
        for name, audit_selection, expected in (
            ("inspect", {"mode": "inspect"}, 2),
            ("exercise", {"mode": "exercise", "path": "audit"}, 3),
            ("without-audit", None, None),
        ):
            target = output / name
            _copy_selection(root, selected, target)
            selection = deepcopy(selected)
            if audit_selection:
                selection["audit_companion"] = audit_selection
                if name == "exercise":
                    exercise(target, selected, target / "audit")
            assessment = assess(target, selection)
            rows = observed_rows(assessment)
            wanted = {"provenance.evidence-revision-consistency": expected}
            result["scenarios"].append(
                {
                    "case": "audit-" + name,
                    "expected": wanted,
                    "observed": rows,
                    "assessment": assessment,
                    "comparison": compare_expectations(wanted, rows),
                    "base_qualified": False,
                }
            )
        result["state"] = "assessed"
        result["observed"] = result["scenarios"][1]["observed"]
        result["comparison"] = compare_expectations(result["expected"], result["observed"])
        result["repair"] = {
            "fresh_assessment": True,
            "native_regrading": False,
            "matches_baseline": all(
                test["restored_replay_equal"] and test["original_preserved"] and test["neighbors_equal"]
                for test in evidence["invalidation_tests"]
            ),
        }
    except (ValueError, OSError, KeyError, TypeError) as error:
        result["blocker"] = {"type": type(error).__name__, "message": str(error)}
    result["result_path"] = str(output / "result.json")
    write_json(output / "result.json", result)
    return result


def adapter_requirement(ident: str, scenario: str) -> str:
    if scenario == "3":
        return "Qualify the highest-anchor base with current owning evidence and required independent or owner review."
    if ident in {"provenance.task-artifact-access", "provenance.grading-dependency-access"}:
        return "Use the selected-source inventory consumer. Current runtime origins are needed for complete dependency closure."
    if ident == "provenance.grading-path":
        return "Add a source-bound mismatch predicate for 0; current consumer emits 1/2 or null, never 0. Complete closure needs current native origins."
    if ident == "provenance.declared-configuration":
        return "Add a material false-declaration predicate for 0. Existing declaration companion supports 1/2, not 0."
    if ident == "provenance.evidence-revision-consistency":
        return "Admit settled panels for inspect=2 and exercise=3; 0 needs demonstrated unsafe reuse, 1 needs typed incomplete revision bindings."
    if ident == "provenance.comparison-fairness":
        return "Consume scoped owner justification and matched sensitivity evidence. Configuration differences alone do not establish unfairness."
    if ident == "task-contract.scoring-authority" and scenario == "0":
        return "The ordinary refund adapter admits four fixed source defects at 0/E1. Other source families need their own admitted controls."
    if ident.startswith("task-contract."):
        return "Local condition evidence is available for the admitted refund family. Positive anchors still require scoped human decisions and independent evidence."
    if ident.startswith("AC-"):
        return "Admit changed-task native panels and criterion-specific partial scopes. Local scorer results are not native boundary evidence."
    return "Extend the owning native consumer to changed task/configuration and partial control scope; preserve unavailable evidence as null."


def reconcile(results: list[dict[str, Any]]) -> dict[str, Any]:
    definitions = canonical_definitions()
    if len(definitions) != 18 or sum(len(row["checks"]) for row in definitions.values()) != 59:
        raise ValueError("canonical inventory changed")
    runs = []
    for result in results:
        if "invariants" in result and not all(
            result["invariants"][name] for name in ("definitions_unchanged", "request_unchanged", "original_preserved")
        ):
            raise ValueError("run invariants failed")
        runs.extend(result.get("scenarios", []))
        if "assessment" in result:
            runs.append(result)
    cells = []
    for ident, definition in definitions.items():
        for scenario in ("0", "1", "2", "3", "missing"):
            expected = None if scenario == "missing" else int(scenario)
            attempts = []
            for run in runs:
                if (
                    ident not in run["expected"]
                    or type(run["expected"][ident]) is not type(expected)
                    or run["expected"][ident] != expected
                ):
                    continue
                rows = observed_rows(run["assessment"])
                if rows != run["observed"]:
                    raise ValueError("stored observations differ from ordinary assessment")
                attempts.append(
                    {
                        "case": run["case"],
                        "assessment": run["assessment"],
                        "observed_score": rows.get(ident, {}).get("score"),
                        "comparison": compare_expectations({ident: expected}, rows)[ident],
                    }
                )
            matched = scenario != "missing" and any(attempt["comparison"] == "matched" for attempt in attempts)
            cells.append(
                {
                    "criterion": ident,
                    "scenario": scenario,
                    "expected": expected,
                    "anchor": definition["scoring"][scenario]
                    if scenario != "missing"
                    else "Unassessed: required evidence withheld, not a zero.",
                    "check_ids": [check["id"] for check in definition["checks"]],
                    "status": "actual_prequalification" if matched else "pending",
                    "implementation": "exercised" if attempts else "not_implemented_or_not_run",
                    "attempts": attempts,
                    "blocker": "Qualified-base rerun required." if matched else adapter_requirement(ident, scenario),
                }
            )
    coverage = {
        "schema": "rubric-mutation-coverage-v1",
        "definitions_sha256": definitions_digest(),
        "request_sha256": hashlib.sha256(fixtures.ASSESSMENT_REQUEST.encode()).hexdigest(),
        "criterion_count": len(definitions),
        "check_count": 59,
        "cells": cells,
        "qualified_mutation_cells": 0,
        "counts": {
            state: sum(row["status"] == state for row in cells) for state in ("actual_prequalification", "pending")
        },
        "limits": [
            "No highest-anchor base qualification is inferred.",
            "Broad withholding and preexisting nulls do not establish selective missing-evidence drops. Use the selective evidence companion.",
            "Numeric observations outside each declared target are collateral, not extra matched mutation cells.",
            "Expectations are external comparisons and never assessment inputs.",
        ],
    }
    validate_coverage(coverage)
    return coverage


def validate_coverage(coverage: dict[str, Any]) -> None:
    definitions = canonical_definitions()
    expected_keys = {(ident, scenario) for ident in definitions for scenario in ("0", "1", "2", "3", "missing")}
    cells = coverage["cells"]
    if len(cells) != len(expected_keys) or {(row["criterion"], row["scenario"]) for row in cells} != expected_keys:
        raise ValueError("invalid 18 by 5 inventory")
    if coverage["definitions_sha256"] != definitions_digest() or coverage["qualified_mutation_cells"] != 0:
        raise ValueError("unsupported qualification or stale definitions")
    checked_reports: set[str] = set()
    for row in cells:
        definition = definitions[row["criterion"]]
        scenario = row["scenario"]
        anchor = (
            definition["scoring"][scenario]
            if scenario != "missing"
            else "Unassessed: required evidence withheld, not a zero."
        )
        if row["check_ids"] != [check["id"] for check in definition["checks"]] or row["anchor"] != anchor:
            raise ValueError("canonical metadata changed")
        expected = None if row["scenario"] == "missing" else int(row["scenario"])
        if type(row["expected"]) is not type(expected) or row["expected"] != expected:
            raise ValueError("invalid target")
        matched = scenario != "missing" and any(attempt["comparison"] == "matched" for attempt in row["attempts"])
        if (row["status"] == "actual_prequalification") != matched:
            raise ValueError("unsupported coverage claim")
        for attempt in row["attempts"]:
            case = attempt["case"]
            if case in {"audit-inspect", "audit-exercise", "audit-without-audit"}:
                targets = {
                    "provenance.evidence-revision-consistency": {
                        "audit-inspect": 2,
                        "audit-exercise": 3,
                        "audit-without-audit": None,
                    }[case]
                }
            else:
                catalog = expectation(case)
                targets = catalog.get(
                    "expected_scores", dict.fromkeys(catalog["target_criteria"], catalog["intended_score"])
                )
            if (
                row["criterion"] not in targets
                or type(targets[row["criterion"]]) is not type(expected)
                or targets[row["criterion"]] != expected
            ):
                raise ValueError("expectation differs from catalog")
            assessment = attempt["assessment"]
            if assessment["state"] == "assessed" and assessment["report"] not in checked_reports:
                path = Path(assessment["report"])
                saved = json.loads(rubric_table.read_private(path))
                if not case.startswith("audit-"):
                    verify_case_input(Path(saved["root"]), case)
                command = [
                    sys.executable,
                    "-B",
                    str(SCRIPTS / "rubric_table.py"),
                    "--root",
                    saved["root"],
                    "--verify-existing",
                    str(path.parent),
                ]
                replay = subprocess.run(
                    command, capture_output=True, timeout=180, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
                )
                if replay.returncode != assessment["exit"] or replay.returncode not in (0, 1):
                    raise ValueError("ordinary assessment replay failed")
                checked_reports.add(assessment["report"])
            rows = observed_rows(assessment)
            score = rows.get(row["criterion"], {}).get("score")
            if type(attempt["observed_score"]) is not type(score) or attempt["observed_score"] != score:
                raise ValueError("observed score differs from report")
            if attempt["comparison"] != compare_expectations({row["criterion"]: expected}, rows)[row["criterion"]]:
                raise ValueError("unsupported comparison")
    counts = {state: sum(row["status"] == state for row in cells) for state in ("actual_prequalification", "pending")}
    if coverage["counts"] != counts:
        raise ValueError("coverage totals disagree")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument(
        "--local-controls", action="store_true", help="Run local verifier subprocess controls, never native or models"
    )
    parser.add_argument(
        "--audit-report", type=Path, help="Existing admitted assessment.json; stale evidence remains blocked"
    )
    parser.add_argument("--registry", action="append", type=Path, default=[])
    parser.add_argument("--verify-coverage", type=Path)
    parser.add_argument(
        "--evidence-scope-report", type=Path, help="Run selective evidence removals from a current admitted assessment"
    )
    parser.add_argument("--verify-evidence-scope", type=Path, help="Replay the selective evidence reconciliation")
    parser.add_argument("--base-report", type=Path, help="Consolidated base, source and numeric scope matrix")
    parser.add_argument("--extend-matrix", type=Path, help="Reuse current verified base cases; execute missing native/semantic report orchestration")
    parser.add_argument("--native-numeric-bundle", type=Path, help="Existing owning-admitted native mutation bundle; never recollected")
    parser.add_argument("--native-numeric-registry", type=Path)
    parser.add_argument("--native-numeric-reports", type=Path, help="Optional unchanged emitted native reports; owning validation and replay still required")
    parser.add_argument("--verify-matrix", type=Path, help="Read-only current matrix verification")
    for flag in ("source-review-operator-store", "source-review-operator-trust", "source-review-session-store", "source-review-signed-store", "upper-environment-bundle", "upper-anticheat-bundle", "upper-anticheat-registry"):
        parser.add_argument("--" + flag)
    args = parser.parse_args(argv)
    if args.base_report or args.verify_matrix or args.extend_matrix:
        if args.case or args.local_controls or args.audit_report or args.verify_coverage or args.evidence_scope_report or args.verify_evidence_scope:
            parser.error("matrix execution cannot be mixed with another runner mode")
        if args.verify_matrix and (args.base_report or args.output):
            parser.error("matrix replay is read-only")
        options = {flag: getattr(args, flag.replace("-", "_")) for flag in ("source-review-operator-store", "source-review-operator-trust", "source-review-session-store", "source-review-signed-store", "upper-environment-bundle", "upper-anticheat-bundle", "upper-anticheat-registry") if getattr(args, flag.replace("-", "_"))}
        options["registry"] = [str(p.absolute()) for p in args.registry]
        if args.extend_matrix:
            from rubric_numeric_matrix_extensions import complete
            if args.base_report or args.verify_matrix or not all((args.output, args.native_numeric_bundle, args.native_numeric_registry)):
                parser.error("extension needs output, native bundle and registry, without another matrix mode")
            result = complete(args.extend_matrix.absolute(), args.output.absolute(), options,
                              args.native_numeric_bundle.absolute(), args.native_numeric_registry.absolute(),
                              args.native_numeric_reports.absolute() if args.native_numeric_reports else None)
            print(json.dumps({"summary": str(args.output / "summary.md"), "counts": result["counts"], "verification": result["verification"]}))
            return 0
        if args.verify_matrix:
            if "original_matrix" in json.loads(args.verify_matrix.read_text()):
                from rubric_numeric_matrix_extensions import verify
                verify(args.verify_matrix, options)
            else:
                verify_matrix(args.verify_matrix, options)
            print(json.dumps({"verified": True, "matrix": str(args.verify_matrix)}))
        else:
            if args.output is None:
                parser.error("--output is required")
            if args.native_numeric_bundle:
                from rubric_numeric_matrix_extensions import complete
                if not args.native_numeric_registry:
                    parser.error("native numeric bundle requires explicit registry")
                base_output = args.output.with_name(args.output.name + "-base")
                run_matrix(args.base_report.absolute(), base_output.absolute(), options)
                result = complete(base_output.absolute() / "matrix.json", args.output.absolute(), options,
                                  args.native_numeric_bundle.absolute(), args.native_numeric_registry.absolute(),
                                  args.native_numeric_reports.absolute() if args.native_numeric_reports else None)
            else:
                result = run_matrix(args.base_report.absolute(), args.output.absolute(), options)
            print(json.dumps({"summary": str(args.output / "summary.md"), "counts": result["counts"], "verification": result["verification"]}))
        return 0
    if args.evidence_scope_report or args.verify_evidence_scope:
        from rubric_evidence_scope_mutations import main as evidence_main

        if args.case or args.local_controls or args.audit_report or args.verify_coverage:
            parser.error("selective evidence controls cannot be combined with source mutation options")
        forwarded = [part for registry in args.registry for part in ("--registry", str(registry))]
        if args.verify_evidence_scope:
            if args.evidence_scope_report or args.output:
                parser.error("selective replay is read-only")
            return evidence_main([*forwarded, "--verify", str(args.verify_evidence_scope)])
        if args.output is None:
            parser.error("--output is required")
        return evidence_main(
            [*forwarded, "--source-report", str(args.evidence_scope_report), "--output", str(args.output)]
        )
    if args.registry:
        os.environ["EVAL_AUTHOR_NATIVE_REGISTRIES"] = os.pathsep.join(str(path.absolute()) for path in args.registry)
    if args.verify_coverage:
        validate_coverage(json.loads(args.verify_coverage.read_text()))
        print(json.dumps({"coverage": "verified", "qualified_mutation_cells": 0}))
        return 0
    if args.output is None:
        parser.error("--output is required")
    output = fixtures._separate_output(args.output)
    output.mkdir(mode=0o700)
    cases = args.case or sorted(
        {path.stem for path in (fixtures.EXAMPLE / "mutations").glob("*.json")} | EXTRA_CASES.keys()
    )
    results = []
    for case in cases:
        try:
            result = run_source_case(output / case, case, local_controls=args.local_controls)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
            result = {
                "case": case,
                "state": "blocked",
                "blocker": {"type": type(error).__name__, "message": str(error)},
                "observed": {},
            }
        results.append(result)
        write_json(output / "runs.json", results)
    if args.audit_report:
        results.append(run_audit_cases(args.audit_report, output / "audit"))
        write_json(output / "runs.json", results)
    coverage = reconcile(json.loads((output / "runs.json").read_text()))
    write_json(output / "coverage.json", coverage)
    print(
        json.dumps(
            {"coverage": str(output / "coverage.json"), "counts": coverage["counts"], "qualified_mutation_cells": 0}
        )
    )
    return 0


def matrix_flags(options: dict[str, Any]) -> list[str]:
    return [part for key, value in options.items() for item in (value if isinstance(value, list) else [value])
            for part in ("--" + key, str(item))]


def matrix_assess(root: Path, selection: dict[str, Any], output: Path, options: dict[str, Any]) -> dict[str, Any]:
    output.mkdir(mode=0o700, parents=True)
    write_json(output / "selection.json", selection)
    result = invoke([str(SCRIPTS / "rubric_base_assessment.py"), "--root", str(root),
                     "--selection", str(output / "selection.json"), "--output", str(output / "ordinary"),
                     "--defer-replay", *matrix_flags(options)], output, "assessment")
    result.update(root=str(root), options=options, report=str(output / "ordinary/report/assessment.json"),
                  state="assessed" if result["exit"] in (0, 1) else "blocked")
    return result


def matrix_replay(record: dict[str, Any], *, log: bool = True) -> bool:
    argv = [str(SCRIPTS / "rubric_base_assessment.py"), "--root", record["root"],
            "--verify-existing", str(Path(record["report"]).parent), *matrix_flags(record["options"])]
    command = [sys.executable, "-B", *argv]
    run = subprocess.run(command, capture_output=True, timeout=600, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if log:
        parent = Path(record["report"]).parents[2]
        (parent / "replay.stdout").write_bytes(run.stdout)
        (parent / "replay.stderr").write_bytes(run.stderr)
        write_json(parent / "replay.command.json", {"command": command, "exit": run.returncode})
    return run.returncode == record["exit"] and run.returncode in (0, 1) and json.loads(run.stdout)["replay"] == "verified"


def matrix_specs() -> dict[str, dict[str, Any]]:
    p = "provenance."
    return {
        "without-upper-environment": {p + n: 2 for n in ("observed-configuration", "runtime-route", "solver-tool-access", "dependency-substitution", "external-state", "comparison-fairness")},
        "without-upper-anticheat": {"AC-03": 2, "AC-04": 2, "task-contract.clear-contract": 2},
        "without-source-review": {p + n: 2 for n in ("task-artifact-access", "grading-path", "grading-dependency-access", "declared-configuration")},
        "audit-inspect": {p + "evidence-revision-consistency": 2},
        "without-audit": {p + "evidence-revision-consistency": None, p + "comparison-fairness": None},
        "without-configuration": {p + "declared-configuration": 1},
        "without-access": {"AC-01": 1},
        "without-attacks": {"AC-02": 1},
        "without-boundaries": {"AC-03": 1, "AC-04": 1},
        "without-runtime": {p + "observed-configuration": None, p + "runtime-route": None, p + "solver-tool-access": None, p + "dependency-substitution": None, p + "external-state": None, p + "grading-path": 1, p + "grading-dependency-access": 1},
        "partial-runtime-observations": {p + n: 1 for n in ("observed-configuration", "solver-tool-access", "dependency-substitution", "external-state")},
        "wrong-inventory": {p + "grading-path": 0, p + "declared-configuration": 0},
    }


def matrix_selection(original: dict[str, Any], case: str, options: dict[str, Any], scopes: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from copy import deepcopy
    selected, configured = deepcopy(original), deepcopy(options)
    selector = selected["example_assessment"]
    if case == "without-upper-environment":
        configured.pop("upper-environment-bundle", None)
    elif case == "without-upper-anticheat":
        for key in ("upper-anticheat-bundle", "upper-anticheat-registry"):
            configured.pop(key, None)
    elif case == "without-source-review":
        selector.pop("review_ids", None)
    elif case == "audit-inspect":
        selected["audit_companion"] = {"mode": "inspect"}
    elif case == "without-audit":
        selected.pop("audit_companion", None)
    elif case == "without-configuration":
        selector.pop("configuration_id", None)
        selector.pop("review_ids", None)
        selected.pop("audit_companion", None)
    elif case.startswith("without-"):
        criterion = {"without-access": "AC-01", "without-attacks": "AC-02", "without-boundaries": "AC-03", "without-runtime": "provenance.observed-configuration"}[case]
        from rubric_evidence_scope_mutations import withhold
        selected, _ = withhold(selected, criterion, scopes)
        if case == "without-runtime":
            configured.pop("upper-environment-bundle", None)
        if case in {"without-boundaries", "without-attacks", "without-access"}:
            for key in ("upper-anticheat-bundle", "upper-anticheat-registry"):
                configured.pop(key, None)
    elif case == "partial-runtime-observations":
        selected["evidence_scope"] = {"configuration_fields": ["environment.cpus", "environment.memory_mb"],
                                      "actions": ["read", "error"], "components": ["contract", "ledger"], "state_cases": ["nop"]}
    elif case == "wrong-inventory":
        selector.pop("review_ids", None)
        selected.pop("audit_companion", None)
    else:
        raise ValueError("unsupported matrix case")
    return selected, configured


def validate_matrix_expectation(run: dict[str, Any]) -> None:
    if run.get("extension_kind"):
        from rubric_numeric_matrix_extensions import extension_specs
        expected = extension_specs(run["extension_kind"])[run["case"]]
    elif run["case"] in matrix_specs():
        expected = matrix_specs()[run["case"]]
    elif run["case"] == "invalid-provider-manifest":
        expected = {"provenance.runtime-route": 0}
    else:
        catalog = expectation(run["case"])
        expected = catalog.get("expected_scores", dict.fromkeys(catalog["target_criteria"], catalog["intended_score"]))
    if rubric_table.encoded(expected) != rubric_table.encoded(run["expected"]):
        raise ValueError("external expectation differs from trusted recipe")


def matrix_summary(base: dict[str, Any], runs: list[dict[str, Any]], repair: dict[str, Any]) -> dict[str, Any]:
    from copy import deepcopy
    runs = deepcopy(runs)
    before = observed_rows(base)
    repaired = observed_rows(repair)
    if comparison_projection(before) != comparison_projection(repaired):
        raise ValueError("final full-base repair differs")
    definitions = canonical_definitions()
    rows = []
    for run in runs:
        validate_matrix_expectation(run)
        actual = observed_rows(run["assessment"])
        changes = {k: {"base": before[k], "actual": v} for k, v in actual.items()
                   if comparison_projection({k: before[k]}) != comparison_projection({k: v})}
        run["actual"] = actual
        run["changes"] = changes
        run["collateral"] = sorted(set(changes) - set(run["expected"]))
        run["unchanged"] = sorted(set(before) - set(changes)) if actual else []
        for ident, target in run["expected"].items():
            value = actual.get(ident, {}).get("score")
            matched = bool(actual) and type(value) is type(target) and value == target
            rows.append({"case": run["case"], "criterion": ident, "base": before[ident]["score"],
                         "expected": target, "actual": value, "executed": bool(actual), "matched": matched,
                         "numeric_drop": matched and type(target) is int and type(before[ident]["score"]) is int and target < before[ident]["score"],
                         "reason": (actual.get(ident, {}).get("remaining_gaps", [run["reason"]]) or [run["reason"]])[0], "repair": repaired[ident]["score"], "report": run["assessment"]["report"]})
    cells = []
    for ident, definition in definitions.items():
        for number in range(4):
            attempts = [r for r in rows if r["criterion"] == ident and type(r["expected"]) is int and r["expected"] == number]
            matched = [r for r in attempts if r["matched"]]
            base_match = before[ident]["score"] == number
            status = "actual_transition" if any(r["numeric_drop"] for r in matched) else "actual_base" if base_match else "actual_control" if matched else "unsupported"
            cells.append({"criterion": ident, "expected": number, "anchor": definition["scoring"][str(number)],
                          "base_actual": before[ident]["score"], "status": status,
                          "attempts": [{"case": r["case"], "actual": r["actual"], "matched": r["matched"], "report": r["report"]} for r in attempts],
                          "remaining": None if status != "unsupported" else adapter_requirement(ident, str(number))})
    if len(cells) != 72 or len({(r["criterion"], r["expected"]) for r in cells}) != 72:
        raise ValueError("canonical matrix incomplete")
    return {"schema": "rubric-consolidated-matrix-v1", "definitions_sha256": definitions_digest(),
            "base_qualified": all(r["score"] == 3 for r in before.values()),
            "base": base, "base_scores": {k: v["score"] for k, v in before.items()}, "repair": repair,
            "runs": runs, "rows": rows, "cells": cells,
            "counts": {state: sum(c["status"] == state for c in cells) for state in ("actual_transition", "actual_base", "actual_control", "unsupported")}}


def matrix_remaining(criterion: str, number: int) -> str:
    if number == 3:
        return "Deferred independent clean/held-out review gate; existing owner decisions establish 2, not 3. No model or token follow-up was run."
    requirements = {
        ("provenance.observed-configuration", 0): "Missing changed-configuration native admission proving a material mismatch invalidates this comparison; retained matching receipts cannot prove it.",
        ("provenance.runtime-route", 1): "Missing a typed, materially incomplete route/adapter fixture and consumer; invalid manifest demonstrates 0, not partial compatibility.",
        ("provenance.comparison-fairness", 0): "Missing demonstrated model-specific advantage plus scoped finding that it is unjustified; disclosed differences alone do not establish this.",
        ("provenance.comparison-fairness", 1): "Missing a bounded comparison with known inadequately justified/tested differences and its owning assessment; current retained intent remains valid.",
        ("provenance.solver-tool-access", 0): "Missing changed-task native solver-UID failed-required-action evidence and admission; current actions pass.",
        ("provenance.dependency-substitution", 0): "Missing observed required missing component or active unapproved replacement plus its scoped authorization reconciliation.",
        ("provenance.external-state", 0): "Missing admitted changed-state drift that materially changes expected outcomes; successful deliberate-drift detection is not invalidation.",
        ("provenance.evidence-revision-consistency", 0): "Missing demonstrated silent incompatible-revision reuse. Current rejection of stale evidence is a successful defense, not 0.",
        ("provenance.evidence-revision-consistency", 1): "Missing typed incomplete version metadata or retained stale review admitted as partial; removing Audit gives Unassessed, not 1.",
        ("task-contract.clear-contract", 0): "Missing full-closure admitted material contradiction/underspecification preventing justified grading; current scorer contradictions do not by themselves prove this contract predicate.",
        ("task-contract.clear-contract", 1): "Missing scoped material ambiguity and partial authority mapping; withholding owner review alone is not demonstrated ambiguity.",
        ("task-contract.discoverable-requirements", 0): "Executed hidden-completion remains Unassessed: verifier source is allowed authority, so the mutation does not establish a hidden requirement. Needs a genuinely inaccessible material condition and admitted authority-surface adapter.",
        ("task-contract.discoverable-requirements", 1): "Missing an admitted incomplete/disputed condition-to-authority mapping; removing evidence is not proof of a dispute.",
        ("task-contract.scoring-authority", 1): "Missing a mixed justified/unresolved material scoring-authority fixture and scoped dispute evidence; current exact contradictions establish 0 instead.",
        ("AC-01", 0): "Missing changed-task solver-role protected-information exposure and owning admission; current protected record is unreadable.",
        ("AC-04", 0): "Missing changed-task predecessor/order evidence proving unintended carryover changes credit or leaks answers; current reset controls pass.",
    }
    return requirements.get((criterion, number), adapter_requirement(criterion, str(number)))


def render_matrix(result: dict[str, Any], output: Path) -> str:
    import shlex
    lines = ["# Current base and numeric mutations", "",
             "Base is **not qualified**: four independent upper gates remain at 2. Controls continue; no all-3 claim.",
             "Expected values are external inputs; actual values below come from ordinary CLI reports. Unassessed is not 0.",
             "Exact task rollback and one final full-selection repair reuse unchanged admitted native evidence; no new native/model execution.", "",
             "| Case | Criterion | Base | Expected | Actual | Reason / scope | Repair |",
             "| --- | --- | ---: | ---: | ---: | --- | ---: |"]
    score = lambda v: "Unassessed" if v is None else str(v)
    for ident, value in result["base_scores"].items():
        lines.append(f"| [base](baseline/ordinary/report/results.md) | {ident} | {value} | 3 | {value} | {'Current upper gate deferred' if value != 3 else 'Current owning evidence and accepted scope'} | {value} |")
    for row in result["rows"]:
        link = os.path.relpath(Path(row["report"]).with_name("results.md"), output)
        lines.append(f"| [{row['case']}]({link}) | {row['criterion']} | {score(row['base'])} | {score(row['expected'])} | {score(row['actual']) if row['executed'] else 'BLOCKED'} | {rubric_table.cell(row['reason'])} | {score(row['repair'])} |")
    lines += ["", "## Remaining numeric cells", ""]
    for cell in result["cells"]:
        if cell["status"] == "unsupported":
            lines.append(f"- **{cell['criterion']} / {cell['expected']}**: " + matrix_remaining(cell["criterion"], cell["expected"]))
    lines += ["", "## Commands and exact repairs", "", "Every command below uses real selected evidence; expectations are never selected. Shared panel cases appear once. Full collateral score/check/evidence-strength changes and unchanged neighbors are in [matrix.json](matrix.json).", ""]
    for run in result["runs"]:
        record = run["assessment"]
        replay = [sys.executable, "-B", str(SCRIPTS / "rubric_base_assessment.py"), "--root", record["root"], "--verify-existing", str(Path(record["report"]).parent), *matrix_flags(record["options"])]
        lines += ["### " + run["case"], "", run["repair_description"], "", "Executed generation (use a new output directory to repeat):", "```sh", shlex.join(record["command"]), "```", "Read-only current replay:", "```sh", shlex.join(replay), "```", ""]
    replay = [sys.executable, "-B", str(SCRIPTS / "rubric_mutation_runner.py"), "--verify-matrix", str(output / "matrix.json"), *matrix_flags(result["options"])]
    lines += ["## Final verification", "", "```sh", shlex.join(replay), "```", "", json.dumps(result.get("verification", {}), sort_keys=True), "",
              "[Full-base repair](repair/ordinary/report/results.md). Source bytes, native receipts and original decisions remain unchanged. Positive source-only neighbors are not highest-anchor qualification."]
    return "\n".join(lines) + "\n"


def run_matrix(source_report: Path, output: Path, options: dict[str, Any]) -> dict[str, Any]:
    from copy import deepcopy
    from rubric_evidence_scope_mutations import source_inventory, selected_scopes, panel_comparison
    saved = json.loads(rubric_table.read_private(source_report))
    root, original = Path(saved["root"]), saved["selection"]
    output = fixtures._separate_output(output, source_report)
    output.mkdir(mode=0o700)
    frozen = source_inventory(root, original)
    write_json(output / "frozen-source-inventory.json", frozen)
    base_identity = fixtures.tree_identity(fixtures.BASE)
    baseline = matrix_assess(root, original, output / "baseline", options)
    if baseline["state"] != "assessed":
        raise ValueError("current combined base failed")
    if {k: r["score"] for k, r in observed_rows(baseline).items()} != {k: r["score"] for k, r in saved["report"]["items"].items()}:
        raise ValueError("current base differs from supplied milestone")
    scopes = selected_scopes(root, original)
    runs = []
    for case, expected in matrix_specs().items():
        selected, configured = matrix_selection(original, case, options, scopes)
        work = output / case
        if case == "wrong-inventory":
            work.mkdir(mode=0o700)
            task = next(a for a in original["artifacts"] if a["id"] == original["example_assessment"]["task_config_id"])
            claim = {"schema": "rubric-assessment-inventory-v1", "task_config_id": task["id"],
                     "scorer": str(Path(task["path"]).parent / "solution/reference.py"),
                     "agent": {"name": "unconfigured-model", "kind": "model"}}
            path = work / "inventory.json"
            write_json(path, claim)
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            selected["artifacts"].append({"id": "assessment-inventory:" + digest, "path": relative,
                "role": "assessment_inventory", "digest": digest, "revision": None, "access": "available",
                "selection_origin": "explicit claimed assessment inventory", "source": relative,
                "evidence": [{"path": relative, "digest": digest}]})
        record = matrix_assess(root, selected, work / "changed", configured)
        neighbors = panel_comparison(saved["report"], json.loads(Path(record["report"]).read_text())["report"]) if record["state"] == "assessed" else None
        if neighbors and not neighbors["retained_panels_equal"]:
            raise ValueError("retained native neighbor changed")
        runs.append({"case": case, "expected": expected, "assessment": record,
                     "reason": "Explicit " + case.replace("-", " ") + "; retained native panels are validated whole, not rewritten.",
                     "repair_description": "Restore the original selection/companion flags. The shared final repair reassesses that exact original selection.",
                     "native_neighbors": neighbors, "task_bytes_changed": False})
        write_json(output / "runs.json", runs)
        print(json.dumps({"case": case, "exit": record["exit"]}), flush=True)
    source_cases = ["source-inspection", "missing-contract", "unselected-contract", "missing-grading-input", "declared-scripted", "declared-model-unconfigured", "completion-only", "approval-after-effect", "ledger-omission", "hidden-completion", "disabled-ledger", "tax-drift", "withhold-assessment-evidence", "invalid-provider-manifest"]
    for case in source_cases:
        work = output / case
        mutation = "base" if case in EXTRA_CASES or case == "invalid-provider-manifest" else case
        fixtures.materialize(work, mutation)
        fixtures.materialize(work / "exact-repair")
        if case == "invalid-provider-manifest":
            path = work / "assessment/task/task.toml"
            text = path.read_text()
            if text.count('network_mode = "no-network"') < 1:
                raise ValueError("provider mutation precondition")
            path.write_text(text.replace('network_mode = "no-network"', 'network_mode = "invalid-route"', 1))
            expected = {"provenance.runtime-route": 0}
            selected = rubric_table.select_task(work, TASK)
        else:
            selected = prepare_selection(work, case)
            catalog = expectation(case)
            expected = catalog.get("expected_scores", dict.fromkeys(catalog["target_criteria"], catalog["intended_score"]))
        record = matrix_assess(work, selected, work / "changed", {k: v for k, v in options.items() if not k.startswith("upper-")})
        runs.append({"case": case, "expected": expected, "assessment": record,
                     "reason": "Fresh " + case.replace("-", " ") + " source/configuration assessment. Source-bound native panels and exact source-review packet are not transferable to changed bytes.",
                     "repair_description": "Byte/mode-exact restored task is retained at exact-repair/assessment/task. Shared final full-base repair restores the original selection and unchanged native evidence; this is not native regrading of mutant bytes.",
                     "source_input": str(work), "input_identity": fixtures.tree_identity(work / "assessment/task"),
                     "exact_repair": str(work / "exact-repair/assessment/task"),
                     "task_bytes_changed": fixtures.tree_identity(work / "assessment/task") != base_identity})
        write_json(output / "runs.json", runs)
        print(json.dumps({"case": case, "exit": record["exit"]}), flush=True)
    repair = matrix_assess(root, original, output / "repair", options)
    runs = json.loads((output / "runs.json").read_text())
    result = matrix_summary(baseline, runs, repair)
    result.update(source_report=str(source_report), options=options, frozen_source_inventory=str(output / "frozen-source-inventory.json"),
                  base_task_identity=base_identity)
    write_json(output / "matrix.json", result)
    verification = verify_matrix(output / "matrix.json", options, write_logs=True)
    result["verification"] = verification
    write_json(output / "matrix.json", result)
    (output / "summary.md").write_text(render_matrix(result, output))
    return result


def matrix_source_files(case: str) -> dict[str, tuple[bytes, int]]:
    files, _ = fixtures._selected("base" if case in EXTRA_CASES or case == "invalid-provider-manifest" else case)
    if case == "missing-grading-input":
        raw, mode = files["tests/test.sh"]
        files["tests/test.sh"] = (raw.replace(b"--contract /app/contract.json", b"--contract /tests/absent-contract.json"), mode)
    if case == "invalid-provider-manifest":
        raw, mode = files["task.toml"]
        files["task.toml"] = (raw.replace(b'network_mode = "no-network"', b'network_mode = "invalid-route"', 1), mode)
    return files


def verify_matrix(path: Path, options: dict[str, Any], *, write_logs: bool = False) -> dict[str, Any]:
    from rubric_evidence_scope_mutations import source_inventory, selected_scopes, panel_comparison
    result = json.loads(path.read_text())
    if result["options"] != options:
        raise ValueError("explicit matrix trust/companion arguments differ")
    base = json.loads(rubric_table.read_private(Path(result["base"]["report"])))
    original = base["selection"]
    if source_inventory(Path(base["root"]), original) != json.loads(Path(result["frozen_source_inventory"]).read_text()):
        raise ValueError("frozen source inventory changed")
    if fixtures.tree_identity(fixtures.BASE) != result["base_task_identity"]:
        raise ValueError("canonical task changed")
    repair_saved = json.loads(rubric_table.read_private(Path(result["repair"]["report"])))
    if repair_saved["selection"] != original or repair_saved["root"] != base["root"]:
        raise ValueError("repair did not restore original selection/root")
    reports = [result["base"], result["repair"]]
    for run in result["runs"]:
        record = run["assessment"]
        if record["state"] == "assessed":
            reports.append(record)
        if "source_input" in run:
            work = Path(run["source_input"])
            if fixtures.tree_identity(work / "assessment/task") != run["input_identity"] or fixtures.tree_identity(Path(run["exact_repair"])) != result["base_task_identity"]:
                raise ValueError("mutation or exact repair changed")
            if fixtures._snapshot(work / "assessment/task") != matrix_source_files(run["case"]):
                raise ValueError("retained task no longer has the trusted mutation")
            if (work / "assessment/request.md").read_text() != fixtures.ASSESSMENT_REQUEST:
                raise ValueError("fixed assessment request changed")
            selected = json.loads((work / "changed/selection.json").read_text())
            if record["state"] == "assessed" and selected != json.loads(rubric_table.read_private(Path(record["report"])))["selection"]:
                raise ValueError("source selection changed")
        else:
            selected, configured = matrix_selection(original, run["case"], options, selected_scopes(Path(base["root"]), original))
            retained_selection = json.loads(Path(record["report"]).parents[2].joinpath("selection.json").read_text())
            if run["case"] == "wrong-inventory":
                claims = [a for a in retained_selection["artifacts"] if a["role"] == "assessment_inventory"]
                if len(claims) != 1:
                    raise ValueError("inventory recipe missing")
                task = next(a for a in original["artifacts"] if a["id"] == original["example_assessment"]["task_config_id"])
                wanted = {"schema": "rubric-assessment-inventory-v1", "task_config_id": task["id"],
                          "scorer": str(Path(task["path"]).parent / "solution/reference.py"),
                          "agent": {"name": "unconfigured-model", "kind": "model"}}
                if json.loads((Path(base["root"]) / claims[0]["path"]).read_text()) != wanted:
                    raise ValueError("wrong-inventory mutation changed")
                selected["artifacts"].append(claims[0])
            if configured != record["options"] or selected != retained_selection:
                raise ValueError("scope recipe changed")
        if run.get("native_neighbors"):
            after = json.loads(rubric_table.read_private(Path(record["report"])))["report"]
            if panel_comparison(base["report"], after) != run["native_neighbors"]:
                raise ValueError("native neighbor projection changed")
    from concurrent.futures import ThreadPoolExecutor
    # Each subprocess owns its measurement hooks; immutable panels are shared.
    # Verify distinct physical reports, never once per criterion/state cell.
    with ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(pool.map(lambda record: matrix_replay(record, log=write_logs), reports))
    for record, valid in zip(reports, verified):
        if not valid:
            raise ValueError("ordinary retained replay failed: " + record["report"])
    rebuilt = matrix_summary(result["base"], result["runs"], result["repair"])
    for key in rebuilt:
        if result[key] != rebuilt[key]:
            raise ValueError("retained matrix derivation differs: " + key)
    return {"distinct_reports_replayed": len(reports), "all_replayed": True, "frozen_sources_preserved": True,
            "exact_repairs": sum("exact_repair" in r for r in result["runs"]), "full_base_repair_equal": True,
            "numeric_cells": len(result["cells"]), "blocked_cases": sum(r["assessment"]["state"] != "assessed" for r in result["runs"])}


if __name__ == "__main__":
    raise SystemExit(main())
