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


def test_qualification_without_legacy_skill_or_workspace_orchestration(tmp_path):
    """Copied core validates the same retained proof without loading the CLI layer."""
    import subprocess

    helpers = load_helpers()
    root, manifest = helpers.workspace(tmp_path)
    jobs = helpers.proof(root, manifest)
    expected = helpers.validate(root, jobs)
    installed = tmp_path / "installed"
    shutil.copytree(ROOT / "skills/eval-author/scripts/eval_author_shared", installed / "eval_author_shared")
    script = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from eval_author_shared import task_evidence as evidence
root = Path(sys.argv[2])
jobs = {key: [Path(p) for p in paths] for key, paths in json.loads(sys.argv[3]).items()}
result = evidence._validation_from_jobs(
    root, jobs['nop'], jobs['oracle'], jobs['negative'], 'synthetic',
    alternative_job_dirs=jobs['alternative'], bypass_job_dirs=jobs['bypass'])
contract = {
    'task_id': root.name, 'provider': 'harbor', 'native_run_id': '/rollout_id',
    'alternative_not_applicable': 'Single numeric synthetic output.',
    'side_effect_not_applicable': 'Read-only synthetic task.', 'cases': {}}
for kind, reward in [('reference', 1), ('incorrect', 0), ('empty', 0), ('agent', 1)]:
    contract['cases'][kind] = {
        'kind': kind, 'requirement': 'Return the total.',
        'health': [{'pointer': '/error', 'equals': None}],
        'checks': [{'pointer': '/reward', 'min': reward, 'max': reward}]}
contract_path = root.parent / 'native-contract.json'
contract_path.write_text(json.dumps(contract))
evidence.prepare(root / 'task', contract_path, root.parent / 'native-manifest.json')
assert 'eval_author_shared.trace_workspace' not in sys.modules
print(json.dumps(result, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed), str(root), json.dumps(jobs, default=str)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == expected
    # The same isolated implementation rejects altered retained proof.
    receipt = jobs["oracle"][0] / "task__trial/result.json"
    payload = json.loads(receipt.read_text())
    payload["task_checksum"] = "changed"
    receipt.write_text(json.dumps(payload))
    changed = subprocess.run(result.args, capture_output=True, text=True)
    assert changed.returncode != 0
    assert "checksum" in changed.stderr


def test_shared_runtime_assets_preserve_standalone_compatibility():
    from eval_author_shared.runtime_assets import ASSETS, load

    for name in ("qualification_runtime", "review_runtime", "replay_mcp_server"):
        assert (ASSETS / f"{name}.py").read_bytes() == (
            ROOT / "skills/eval-author-trace-environment/scripts" / f"{name}.py"
        ).read_bytes()
    assert Path(load("qualification_runtime").__file__).parent == ASSETS
    assert Path(load("review_runtime").__file__).parent == ASSETS


def test_repair_reports_pending_activation_and_recovers_without_manual_deletion(tmp_path):
    import argparse

    from eval_author_shared import commands, trace_workspace

    helpers, root, _ = fresh_workspace(tmp_path)
    value = construction.from_trace(root)
    for requirement in value["requirements"]:
        requirement["authority"] = {"basis": "specification", "reference": "Synthetic public requirement"}
    first = root / "private/first.json"
    helpers.save(first, value)
    construction.activate(root, first)
    repair = task_evidence._record_repair(
        argparse.Namespace(task_dir=root, reason_code="verifier_defect", note="Fix a public requirement check")
    )
    assert repair["next"].startswith("activate the repaired contract")
    assert commands.main("environment", ["record-reproducibility", "--task-dir", str(root)]) == 1
    assert not (root / "reproducibility.json").exists()
    checked = trace_workspace._check(argparse.Namespace(task_dir=root))
    assert not checked["valid"]
    assert any("activate the repaired" in error for error in checked["errors"])
    summary = json.loads((root / "summary.json").read_text())
    summary["status"] = "candidate"
    helpers.save(root / "summary.json", summary)
    manifest = root.parent / "batch.json"
    helpers.save(
        manifest,
        {
            "schema": trace_workspace.BATCH_SCHEMA,
            "members": [
                {"task_id": root.name, "atif": "unused.json", "source_kind": "atif"},
                {"task_id": "missing-member", "atif": "unused.json", "source_kind": "atif"},
            ],
        },
    )
    batch = trace_workspace._batch_status(argparse.Namespace(root=root.parent, manifest=manifest))
    assert batch["counts"] == {"invalid": 1, "missing": 1}
    second = root / "private/second.json"
    helpers.save(second, value)
    construction.activate(root, second)
    assert commands.main("environment", ["record-reproducibility", "--task-dir", str(root)]) == 0
    args = trace_workspace._parser().parse_args(
        ["record-run-inputs", "--task-dir", str(root), "--arm", "nop", "--job-dir", "private/jobs/repaired"]
    )
    receipt = task_evidence._record_run_inputs(args)
    assert json.loads(Path(receipt["run_inputs"]).read_text())["construction_path"] == "private/second.json"


def test_activation_recovers_previously_recorded_post_repair_reproducibility(tmp_path):
    import argparse

    from eval_author_shared import trace_workspace

    helpers, root, _ = fresh_workspace(tmp_path)
    value = construction.from_trace(root)
    for requirement in value["requirements"]:
        requirement["authority"] = {"basis": "specification", "reference": "Synthetic public requirement"}
    first = root / "private/first.json"
    helpers.save(first, value)
    construction.activate(root, first)
    task_evidence._record_repair(
        argparse.Namespace(task_dir=root, reason_code="verifier_defect", note="Repair a check")
    )
    # Reproduce the previous CLI's ordering; this report describes the new revision.
    helpers.save(root / "reproducibility.json", trace_workspace._derive_reproducibility(root))
    second = root / "private/second.json"
    helpers.save(second, value)
    construction.activate(root, second)
    assert construction.active_path(root) == second


def test_canonical_authority_error_survives_workspace_boundary(tmp_path):
    helpers, root, _ = fresh_workspace(tmp_path)
    value = construction.from_trace(root)
    helpers.save(root / "private/construction.json", value)
    with pytest.raises(ValueError, match="resolve grading authority before construction"):
        task_evidence._construction_context(root)


def test_historical_trace_size_bound_and_cli_validation(tmp_path):
    import subprocess

    from eval_author_shared import legacy_construction

    helpers, root, value = fresh_workspace(tmp_path)
    value["review"] = {"reviewer_kind": "agent", "note": "Reviewed source. " * 80000}
    path = root / "private/construction.json"
    helpers.save(path, value)
    assert legacy_plan.MAX_BYTES < path.stat().st_size < legacy_construction.MAX_BYTES
    expected = legacy_construction.validate(root)
    assert construction.validate(root)["sha256"] == expected["sha256"]
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills/eval-author/scripts/construction_contract.py"),
            "--plan",
            str(path),
            "--mode",
            "draft",
            "--workspace",
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value["schema"] = construction.SCHEMA
    helpers.save(path, value)
    with pytest.raises(construction.PlanError, match="size limit"):
        construction.load(path)
