# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))


def runner():
    assert (SCRIPTS / "rubric_mutation_runner.py").is_file(), "mutation runner not implemented"
    return importlib.import_module("rubric_mutation_runner")


def test_real_missing_input_assessment_repair_and_external_expectation(tmp_path):
    module = runner()
    result = module.run_source_case(tmp_path / "case", "missing-contract")
    ident = "provenance.task-artifact-access"
    assert result["observed"][ident]["score"] == 0
    assert result["expected"][ident] == 0
    assert result["comparison"][ident] == "matched"
    assert result["repair"]["matches_baseline"] is True
    assert result["repair"]["fresh_assessment"] is True
    assert result["base_qualified"] is False
    assert result["invariants"]["definitions_unchanged"] is True
    assert result["invariants"]["request_unchanged"] is True
    assert result["invariants"]["original_preserved"] is True
    assert result["assessment"]["replay_exit"] == result["assessment"]["exit"] == 1
    saved = json.loads(Path(result["assessment"]["report"]).read_text())
    assert "expected" not in saved["selection"]
    assert "criterion_evidence" not in saved["selection"]
    assert "private" not in json.dumps(saved["selection"])
    assert module.compare_expectations({ident: 3}, result["observed"])[ident] == "mismatch"
    assert module.compare_expectations({ident: None}, result["observed"])[ident] == "mismatch"


def test_withheld_selection_keeps_all_criteria_unknown(tmp_path):
    result = runner().run_source_case(tmp_path / "missing", "withhold-assessment-evidence")
    assert set(result["observed"]) == set(runner().canonical_definitions())
    assert len(result["observed"]) == 23
    assert sum(len(row["checks"]) for row in result["observed"].values()) == 76
    assert all(row["score"] is None for row in result["observed"].values())
    assert all(value == "matched" for value in result["comparison"].values())
    assert result["repair"]["matches_baseline"]
    assert result["freshness"]["task_sha256"] == result["freshness"]["baseline_sha256"]


def test_config_scope_and_partial_inventory_are_real_assessments(tmp_path):
    module = runner()
    for case, ident, score in (
        ("unselected-contract", "provenance.task-artifact-access", 1),
        ("missing-grading-input", "provenance.grading-dependency-access", 0),
        ("declared-scripted", "provenance.declared-configuration", 2),
        ("declared-model-unconfigured", "provenance.declared-configuration", 1),
    ):
        result = module.run_source_case(tmp_path / case, case)
        assert result["observed"][ident]["score"] == score
        assert result["repair"]["matches_baseline"]


def test_runner_reports_admitted_semantic_controls(tmp_path):
    module = runner()
    result = module.run_source_case(tmp_path / "semantic", "ledger-omission")
    ident = "task-contract.scoring-authority"
    assert result["observed"][ident]["score"] == 0
    assert result["comparison"][ident] == "matched"
    assert result["semantic_controls"]["assessment_consumed"] is True
    assert result["semantic_controls"]["repair_matches_baseline"] is True
    assert result["semantic_controls"]["unchanged_neighbors"]["reference"] is True
    assert result["repair"]["matches_baseline"]
    assert result["baseline_observed"][ident]["score"] is None
    module.validate_coverage(module.reconcile([result]))


def test_local_defect_uses_owning_source_evidence_not_optional_subprocess_results(tmp_path):
    result = runner().run_source_case(tmp_path / "completion", "completion-only", local_controls=True)
    assert result["controls"]["changed"]["cases"]["no-work"]["result"]["total_credit"] == 1
    assert result["controls"]["changed"]["cases"]["no-work"]["result"]["max_credit"] == 1
    assert result["controls"]["baseline"]["cases"]["no-work"]["result"]["total_credit"] == 0
    assert result["controls"]["repair_matches_baseline"]
    assert result["observed"]["AUDIT-03"]["score"] == 0
    assert result["comparison"]["AUDIT-03"] == "matched"
    assert result["semantic_controls"]["assessment_consumed"] is True
    assert result["controls"]["assessment_consumed"] is False
    assert result["controls"]["unchanged_neighbors"]["reference"] is False
    assert result["controls"]["changed"]["cases"]["reference"]["result"]["accepted"] is True


def test_reconciles_current_numeric_and_missing_without_claiming_pending_matches(tmp_path):
    module = runner()
    result = module.run_source_case(tmp_path / "missing", "withhold-assessment-evidence")
    coverage = module.reconcile([result])
    assert coverage["criterion_count"] == 23
    assert coverage["check_count"] == 76
    assert sum(row["scenario"] != "missing" for row in coverage["cells"]) == 92
    assert sum(row["scenario"] == "missing" for row in coverage["cells"]) == 23
    assert "METHOD-J1" not in {row["criterion"] for row in coverage["cells"]}
    assert coverage["counts"]["actual_prequalification"] == 0
    assert coverage["counts"]["pending"] == 115
    assert coverage["qualified_mutation_cells"] == 0
    module.validate_coverage(coverage)
    coverage["cells"][0]["criterion"] = "invented"
    import pytest

    with pytest.raises(ValueError):
        module.validate_coverage(coverage)


def test_audit_failure_is_retained_without_manufactured_score(tmp_path):
    module = runner()
    source = tmp_path / "invalid.json"
    source.write_text("{}")
    result = module.run_audit_cases(source, tmp_path / "audit")
    assert result["state"] == "blocked"
    assert result["observed"] == {}
    assert (tmp_path / "audit/result.json").is_file()


def test_coverage_rejects_tampered_observation_check_and_expectation(tmp_path):
    from copy import deepcopy

    import pytest

    module = runner()
    result = module.run_source_case(tmp_path / "case", "missing-contract")
    coverage = module.reconcile([result])
    for field, value in (("observed_score", 3), ("case", "forged-case")):
        altered = deepcopy(coverage)
        cell = next(row for row in altered["cells"] if row["attempts"])
        cell["attempts"][0][field] = value
        with pytest.raises(ValueError):
            module.validate_coverage(altered)
    altered = deepcopy(coverage)
    altered["cells"][0]["check_ids"] = []
    with pytest.raises(ValueError):
        module.validate_coverage(altered)
    saved = Path(result["assessment"]["report"])
    data = json.loads(saved.read_text())
    data["report"]["items"]["provenance.task-artifact-access"]["score"] = 3
    saved.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        module.validate_coverage(coverage)


def test_source_scope_and_withheld_reference_are_distinct(tmp_path):
    module = runner()
    source = module.run_source_case(tmp_path / "source", "source-inspection")
    assert all(value == "matched" for value in source["comparison"].values())
    missing = module.run_source_case(tmp_path / "missing", "withhold-reference", local_controls=True)
    assert "reference" not in missing["controls"]["withheld"]["cases"]
    assert "reference" in missing["controls"]["baseline"]["cases"]
    assert missing["observed"]["AUDIT-03"]["score"] is None
    assert missing["controls"]["assessment_consumed"] is False


def test_missing_mutation_cannot_claim_catalog_coverage(tmp_path):
    import pytest

    module = runner()
    result = module.run_source_case(tmp_path / "case", "missing-contract")
    root = tmp_path / "case/changed"
    assert module.verify_case_input(root, "missing-contract") is None
    target = root / "assessment/task/environment/contract.json"
    target.write_bytes((module.fixtures.BASE / "environment/contract.json").read_bytes())
    with pytest.raises(ValueError):
        module.verify_case_input(root, "missing-contract")
    result["invariants"]["original_preserved"] = False
    with pytest.raises(ValueError):
        module.reconcile([result])


def test_audit_retained_real_panels(tmp_path):
    import os

    import pytest

    source = os.environ.get("EVAL_AUTHOR_MUTATION_AUDIT_REPORT")
    if not source:
        pytest.skip("requires current admitted native receipts; no native runs")
    result = runner().run_audit_cases(Path(source), tmp_path / "audit")
    assert result["state"] == "assessed", result.get("blocker")
    assert len(result["invalidation_tests"]) == 3
    assert all(row["stale_reuse_rejected"] and row["neighbors_equal"] for row in result["invalidation_tests"])
    assert result["repair"]["matches_baseline"]
    scores = [case["observed"]["provenance.evidence-revision-consistency"]["score"] for case in result["scenarios"][:3]]
    assert scores == [2, 3, None]
