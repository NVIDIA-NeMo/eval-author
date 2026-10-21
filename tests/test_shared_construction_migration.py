# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Migration regressions; source receipts and runtime producers remain immutable."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from eval_author_shared import construction, legacy_plan, task_evidence  # noqa: E402


def load_helpers():
    path = ROOT / "skills/eval-author-trace-environment/evals/tests/test_construction.py"
    spec = importlib.util.spec_from_file_location("shared_migration_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fresh_workspace(tmp_path):
    helpers = load_helpers()
    original, manifest = helpers.workspace(tmp_path)
    fresh = original.parent / "fresh-workspace" / original.name
    shutil.copytree(
        original, fresh, ignore=shutil.ignore_patterns("jobs", "run-inputs", "probes", "repairs", "validation.json")
    )
    return helpers, fresh, manifest


def test_incomplete_proposal_migrates_without_inventing_checks(tmp_path):
    root = tmp_path / ".eval-author"
    root.mkdir()
    source = root / "old.json"
    original = {
        "schema": legacy_plan.SCHEMA,
        "task_id": "example",
        "provider": "gym",
        "requirements": [
            {
                "id": "total",
                "statement": "Calculate a total",
                "authority": {"basis": "unresolved", "reference": "Need the aggregation rule"},
            }
        ],
        "inputs": [],
        "checks": [],
        "cases": [],
        "prerequisites": [],
    }
    source.write_text(json.dumps(original))
    original_bytes = source.read_bytes()
    destination = root / "new.json"
    construction.migrate(source, destination)
    migrated, _ = construction.load(destination)
    result = construction.assess(migrated)
    assert result["status"] == "incomplete"
    assert result["unmapped_requirements"] == ["total"]
    assert result["unresolved_requirements"] == ["total"]
    assert result["execution"] == "unmeasured"
    assert source.read_bytes() == original_bytes
    source.write_text(json.dumps(original, indent=2))
    with pytest.raises(construction.PlanError, match="historical construction source changed"):
        construction.assess(migrated)


def test_trace_import_retains_all_controls_and_requires_authority_review(tmp_path):
    helpers, root, original = fresh_workspace(tmp_path)
    source = root / "private/construction.json"
    before = source.read_bytes()
    destination = root / "private/construction-v2.json"
    construction.migrate(source, destination, workspace=root)
    migrated, _ = construction.load(destination)
    assert construction._trace_projection(migrated) == original
    assert source.read_bytes() == before
    with pytest.raises(construction.PlanError, match="resolve grading authority"):
        construction.activate(root, destination)
    for requirement in migrated["requirements"]:
        requirement["authority"] = {"basis": "specification", "reference": "Synthetic fixture requirement"}
    destination.write_text(json.dumps(migrated))
    construction.activate(root, destination)
    context = construction.validate(root)
    assert context["canonical_path"] == "private/construction-v2.json"
    assert context["sha256"] == task_evidence.digest(destination)
    assert source.read_bytes() == before


def test_legacy_control_names_do_not_collide_with_builtin_cases(tmp_path):
    helpers, root, original = fresh_workspace(tmp_path)
    negative = next(c for c in original["controls"] if c["arm"] == "negative")
    negative["id"] = "builtin-nop"
    helpers.save(root / "private/construction.json", original)
    migrated = construction.from_trace(root)
    names = [case["case"] for case in migrated["cases"]]
    assert len(names) == len(set(names))
    assert construction._trace_projection(migrated) == original


def test_stale_active_contract_does_not_move_reports_during_repair(tmp_path):
    import argparse

    helpers, root, _ = fresh_workspace(tmp_path)
    migrated = construction.from_trace(root)
    for requirement in migrated["requirements"]:
        requirement["authority"] = {"basis": "specification", "reference": "Synthetic fixture requirement"}
    destination = root / "private/construction-v2.json"
    helpers.save(destination, migrated)
    construction.activate(root, destination)
    report = root / "reproducibility.json"
    before = report.read_bytes()
    destination.write_text(destination.read_text() + "\n")
    with pytest.raises(construction.PlanError, match="active construction changed"):
        task_evidence._record_repair(
            argparse.Namespace(task_dir=root, reason_code="verifier_defect", note="Repair a declared check")
        )
    assert report.read_bytes() == before
    assert not (root / "private/repairs/repair-1.json").exists()


def test_canonical_repair_preserves_sources_and_requires_fresh_selection(tmp_path):
    import argparse

    helpers, root, _ = fresh_workspace(tmp_path)
    original = root / "private/construction.json"
    original_bytes = original.read_bytes()
    value = construction.from_trace(root)
    for requirement in value["requirements"]:
        requirement["authority"] = {"basis": "specification", "reference": "Synthetic fixture requirement"}
    first = root / "private/construction-v2.json"
    helpers.save(first, value)
    construction.activate(root, first)
    marker = (root / "private/active-construction.json").read_bytes()
    args = helpers.driver._parser().parse_args(
        [
            "record-run-inputs",
            "--task-dir",
            str(root),
            "--arm",
            "nop",
            "--job-dir",
            "private/jobs/new-nop",
        ]
    )
    registration = task_evidence._record_run_inputs(args)
    inputs = json.loads(Path(registration["run_inputs"]).read_text())
    assert inputs["schema"] == helpers.driver.CANONICAL_RUN_INPUT_SCHEMA
    assert inputs["construction_path"] == "private/construction-v2.json"
    assert inputs["construction_sha256"] == task_evidence.digest(first)
    task_evidence._record_repair(
        argparse.Namespace(
            task_dir=root, reason_code="verifier_defect", note="Correct a declared check before another attempt"
        )
    )
    archive = root / "private/repairs/repair-1"
    assert (archive / "construction.json").read_bytes() == first.read_bytes()
    assert (archive / "active-construction.json").read_bytes() == marker
    assert original.read_bytes() == original_bytes
    with pytest.raises(construction.PlanError, match="activate the repaired"):
        construction.active_path(root)
    second = root / "private/construction-v2-repaired.json"
    helpers.save(second, value)
    construction.activate(root, second)
    assert construction.active_path(root) == second
    assert task_evidence._repairs(root)[0]["index"] == 1


def test_admission_requires_privacy_and_rechecks_reviewed_findings(tmp_path):
    from eval_author_shared import admission

    helpers = load_helpers().helpers
    root, _ = helpers._workspace(tmp_path)
    helpers._candidate(root)
    with pytest.raises(admission.workspace.ContractError, match="contextual privacy review"):
        admission.assess(root)
    helpers._review_privacy(root)
    result = admission.assess(root)
    assert result["admitted"] is True
    assert result["readiness"] == "unproven"
    assert result["execution_verified"] is False
    audit_path = root / "private/privacy-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["character_count"] += 1
    helpers._write_json(audit_path, audit)
    with pytest.raises(admission.workspace.ContractError, match="no longer matches"):
        admission.assess(root)


def test_admission_keeps_rejected_sources_without_task_or_execution(tmp_path):
    from eval_author_shared import admission

    helpers = load_helpers().helpers
    root, _ = helpers._workspace(tmp_path)
    helpers._candidate(root, status="no_candidate")
    result = admission.assess(root)
    assert result["admitted"] is False
    assert result["disposition"] == "no_candidate"
    assert result["readiness"] == "not_attempted"
    assert not (root / "task").exists()
