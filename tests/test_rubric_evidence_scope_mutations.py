# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))


def module():
    assert (SCRIPTS / "rubric_evidence_scope_mutations.py").is_file(), "selective evidence helper missing"
    return importlib.import_module("rubric_evidence_scope_mutations")


def test_access_scope_removal_preserves_sources_and_other_selectors():
    helper = module()
    selection = {
        "artifacts": [
            {"id": "access", "path": "access/native-index.json"},
            {"id": "attack", "path": "attack/native-index.json"},
        ],
        "example_assessment": {
            "task_config_id": "task",
            "native_bundle_ids": ["access", "attack"],
            "configuration_id": "config",
        },
        "audit_companion": {"mode": "exercise", "path": "audit"},
    }
    original = json.loads(json.dumps(selection))
    changed, removed = helper.withhold(
        selection,
        "AC-01",
        {"access": "eval-author.refund-access-panel.v1", "attack": "eval-author.refund-attack-panel.v1"},
    )
    assert selection == original
    assert changed["artifacts"] == original["artifacts"]
    assert changed["example_assessment"] == {**original["example_assessment"], "native_bundle_ids": ["attack"]}
    assert removed == ["access"]
    assert "audit_companion" not in changed  # Its exact selected-panel binding must be rebuilt.
    assert "expected" not in json.dumps(changed)


def test_ordinary_source_assessment_partial_removal_and_fresh_repair(tmp_path):
    helper = module()
    import example_fixtures
    import rubric_example_assessment
    import rubric_table

    example_fixtures.materialize(tmp_path / "subject", "ledger-omission")
    root = tmp_path / "subject"
    source = rubric_table.select_task(root, "assessment/task/task.toml")
    path = root / "source.json"
    path.write_text(json.dumps(source))
    assert (
        rubric_example_assessment.configuration_main(
            ["--root", str(root), "--selection", str(path), "--agent", "nop", "--output", str(root / "configuration")]
        )
        == 0
    )
    selection = json.loads((root / "configuration/selection.json").read_text())
    baseline = helper.assess(root, selection, tmp_path / "baseline")
    result = helper.run_case(root, selection, baseline, "provenance.declared-configuration", {}, tmp_path / "case")
    assert result["baseline_score"] == 1  # The source defect also prevents a complete declaration.
    assert result["observed_score"] == 1
    assert result["status"] == "partial_evidence_retained"
    assert result["fresh_repair_matches"] is True
    assert result["assessment"]["replay_verified"] is True
    assert result["unchanged_criteria"]
    assert result["preserved_defects"] is True
    assert result["native_neighbors"]["retained_panels_equal"] is True
    assert result["native_neighbors"]["withheld_panel_digests"] == []
    assert helper.rows(result["assessment"])["task-contract.scoring-authority"]["score"] == 0
    assert "task-contract.scoring-authority" in result["unchanged_criteria"]
    assert result["selection_artifacts_unchanged"] is True
    assert "provenance.declared-configuration" not in json.dumps(
        json.loads(Path(result["assessment"]["report"]).read_text())["selection"]
    )
    helper.verify_case(result)
    original_scopes = result["panel_scopes"]
    result["panel_scopes"] = {"invented": "eval-author.refund-access-panel.v1"}
    import pytest

    with pytest.raises(ValueError, match="panel scope"):
        helper.verify_case(result)
    result["panel_scopes"] = original_scopes
    from copy import deepcopy

    import pytest

    for field, wrong in (
        ("status", "selective_missing"),
        ("unchanged_criteria", []),
        ("collateral_criteria", ["AC-01"]),
        ("fresh_repair_matches", False),
        ("native_neighbors", {}),
        ("native_rerun", True),
        ("selection_artifacts_unchanged", False),
        ("base_qualified", True),
    ):
        tampered = deepcopy(result)
        tampered[field] = wrong
        with pytest.raises(ValueError, match="observation"):
            helper.verify_case(tampered)


def test_all_criteria_have_evidence_specific_recipes_or_explicit_gaps():
    helper = module()
    definitions = helper.canonical_definitions()
    catalog = helper.catalog()
    assert catalog.keys() == definitions.keys()
    assert len(catalog) == 18
    assert all(row["checks"] == definitions[ident]["checks"] for ident, row in catalog.items())
    assert catalog["provenance.grading-path"]["supported"] is False
    assert catalog["task-contract.clear-contract"]["supported"] is False
    assert catalog["provenance.declared-configuration"]["supported"] is True
    scopes = {
        "runtime": "eval-author.refund-runtime-observation.v1",
        "attack": "eval-author.refund-attack-panel.v1",
        "boundary": "eval-author.refund-boundary-panel.v1",
    }
    selection = {
        "artifacts": [],
        "example_assessment": {"native_bundle_ids": list(scopes)},
        "audit_companion": {"mode": "inspect"},
    }
    for ident in (
        "provenance.observed-configuration",
        "provenance.runtime-route",
        "provenance.solver-tool-access",
        "provenance.dependency-substitution",
        "provenance.external-state",
    ):
        changed, removed = helper.withhold(selection, ident, scopes)
        assert removed == ["runtime"]
        assert changed["example_assessment"]["native_bundle_ids"] == ["attack", "boundary"]
    for ident in ("provenance.evidence-revision-consistency", "provenance.comparison-fairness"):
        changed, removed = helper.withhold(selection, ident, scopes)
        assert changed == {k: v for k, v in selection.items() if k != "audit_companion"}
        assert removed == ["audit_companion"]


def test_reconcile_rejects_changed_observation_and_accounts_for_all_18(tmp_path):
    helper = module()
    import example_fixtures
    import rubric_table

    root = tmp_path / "subject"
    example_fixtures.materialize(root)
    selection = rubric_table.select_task(root, "assessment/task/task.toml")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"root": str(root), "selection": selection}))
    result = helper.run(source, tmp_path / "batch")
    assert len(result["criteria"]) == 18
    assert all(row["status"] in {"unsupported", "not_selected"} for row in result["criteria"])
    assert result["counts"]["selective_missing"] == 0
    assert result["baseline"]["replay_verified"]
    assert result["repair"]["replay_verified"]
    assert result["source_bytes_preserved"]
    snapshot = {p: p.read_bytes() for p in (tmp_path / "batch").rglob("*") if p.is_file()}
    helper.verify(tmp_path / "batch/reconciliation.json")
    assert {p: p.read_bytes() for p in (tmp_path / "batch").rglob("*") if p.is_file()} == snapshot
    result["distinct_removals"] = 99
    (tmp_path / "batch/reconciliation.json").write_text(json.dumps(result))
    import pytest

    with pytest.raises(ValueError, match="reconciliation"):
        helper.verify(tmp_path / "batch/reconciliation.json")
    result["distinct_removals"] = 0
    result["criteria"][0]["baseline_score"] = 999
    (tmp_path / "batch/reconciliation.json").write_text(json.dumps(result))
    import pytest

    with pytest.raises(ValueError, match="reconciliation"):
        helper.verify(tmp_path / "batch/reconciliation.json")


def test_panel_comparison_preserves_full_neighbor_and_error_accounting():
    helper = module()
    original = {
        "measurements": {
            "example_assessment": {
                "native": {
                    "panels": [
                        {"index_digest": "a", "scope": "access", "counts": {"infrastructure_errors": 0}},
                        {"index_digest": "b", "scope": "boundary", "counts": {"infrastructure_errors": 1}},
                    ]
                }
            }
        }
    }
    changed = {
        "measurements": {
            "example_assessment": {"native": original["measurements"]["example_assessment"]["native"]["panels"][1]}
        }
    }
    assert helper.panel_comparison(original, changed) == {
        "retained_panels_equal": True,
        "retained_panel_digests": ["b"],
        "withheld_panel_digests": ["a"],
        "baseline_operational_errors": 1,
        "retained_operational_errors": 1,
    }


def test_preexisting_null_is_not_a_demonstrated_drop():
    helper = module()
    row = {"score": None, "evidence_strength": None, "check_results": [], "findings": []}
    result = helper.observations({"criterion": row}, {"criterion": row}, {"criterion": row}, "criterion")
    assert result["status"] == "preexisting_null"
    assert result["demonstrated_selective_drop"] is False


def test_mutation_runner_selective_entrypoint_runs_ordinary_tables(tmp_path):
    import example_fixtures
    import rubric_mutation_runner
    import rubric_table

    root = tmp_path / "subject"
    example_fixtures.materialize(root)
    selection = rubric_table.select_task(root, "assessment/task/task.toml")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"root": str(root), "selection": selection}))
    assert rubric_mutation_runner.main(["--evidence-scope-report", str(source), "--output", str(tmp_path / "run")]) == 0
    assert (tmp_path / "run/baseline/report/results.md").is_file()
    assert rubric_mutation_runner.main(["--verify-evidence-scope", str(tmp_path / "run/reconciliation.json")]) == 0
