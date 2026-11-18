# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Current-scope integration; execution deliberately deferred by delivery order."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))
import rubric_leakage_current as current  # noqa: E402
import rubric_table  # noqa: E402


def test_current_matrix_enumerates_all_cells_without_legacy_scores(monkeypatch):
    import rubric_mutation_runner as runner

    rows = {
        ident: {
            "score": None,
            "checks": {c["id"]: "NOT_RUN" for c in definition["checks"]},
            "evidence_strength": None,
            "remaining_gaps": [],
        }
        for ident, definition in current.canonical_definitions().items()
    }
    monkeypatch.setattr(runner, "observed_rows", lambda record: deepcopy(rows))
    result = runner.matrix_summary({"report": "unused"}, [], {"report": "unused"})
    assert len(result["cells"]) == 84
    assert len(result["missing_evidence_cells"]) == 21
    assert result["counts"]["unsupported"] == 84
    assert not result["base_qualified"]
    assert current.current_expectations({"AC-02": 0, "AC-03": 0, "AC-01": 0}) == {"AUDIT-03": 0}


def test_legacy_only_measurements_do_not_expand_selected_scope():
    report = {"items": {"AC-01": {"score": None}}, "measurements": {}}
    assert current.assess_current(Path("."), {"scorer_controls": {}}, report) is report


def test_inventory_replaces_overlapping_legacy_rows():
    inventory = current.inventory()
    assert inventory["criterion_count"] == 21
    assert inventory["check_count"] == 70
    assert inventory["numeric_cells"] == 84
    assert inventory["missing_evidence_cells"] == 21
    assert inventory["undefined"] == ["METHOD-J1"]
    assert not set(current.LEGACY_CROSSWALK) & current.canonical_definitions().keys()
    assert current.LEGACY_CROSSWALK["AC-02"] == current.LEGACY_CROSSWALK["AC-03"] == ["AUDIT-03"]


def test_native_scores_are_not_canonical_approvals():
    report = {"items": {key: {"score": 3} for key in current.LEGACY_CROSSWALK}, "measurements": {}}
    original = deepcopy(report)
    result = current.reconcile(report, {})
    assert report == original
    assert all(row["score"] is None for row in result["items"].values())
    assert result["legacy_evidence_items"] == original["items"]
    assert result["undefined_criteria"]["METHOD-J1"]["score"] is None


def test_partial_native_attack_evidence_does_not_approve_human_check():
    control = {
        "case": "nop",
        "status": "passed",
        "checks": {"health": "ok", "total_credit": 0, "max_credit": 5, "checks": {"work": 0}},
        "expected": {"total_credit": 0, "max_credit": 5, "checks": {"work": 0}},
    }
    panel = {
        "scope": "eval-author.refund-attack-panel.v1",
        "task_freshness": "current",
        "native_execution": True,
        "index_digest": "bound-by-owning-validator",
        "controls": [control],
    }
    report = {"items": {}, "measurements": {"example_assessment": {"native": panel}}}
    result = current.reconcile(report, {})
    row = result["items"]["AUDIT-03"]
    assert row["score"] == 1
    assert row["check_results"][0]["outcome"] == "NOT_RUN"
    assert row["check_results"][1]["outcome"] == "PARTIAL"
    assert len(result["measurements"]["leakage_current"]["shared_controls"]) == 1


def test_error_cannot_establish_forbidden_credit():
    control = {"case": "nop", "status": "infrastructure_error", "reward": 1, "expected": {"total_credit": 0}}
    panel = {
        "scope": "eval-author.refund-attack-panel.v1",
        "task_freshness": "current",
        "native_execution": True,
        "controls": [control],
    }
    result = current.reconcile({"items": {}, "measurements": {"example_assessment": {"native": panel}}}, {})
    assert result["items"]["AUDIT-03"]["score"] is None
    assert result["measurements"]["leakage_current"]["operational_errors"] == 1


def test_per_check_forbidden_credit_despite_equal_total():
    control = {
        "case": "nop",
        "status": "behavior_failed",
        "checks": {"health": "ok", "total_credit": 1, "max_credit": 5, "checks": {"work": 1, "other": 0}},
        "expected": {"total_credit": 1, "max_credit": 5, "checks": {"work": 0, "other": 1}},
    }
    panel = {
        "scope": "eval-author.refund-attack-panel.v1",
        "task_freshness": "current",
        "native_execution": True,
        "index_digest": "owning",
        "controls": [control],
    }
    result = current.reconcile({"items": {}, "measurements": {"example_assessment": {"native": panel}}}, {})
    assert result["items"]["AUDIT-03"]["score"] == 0
    assert result["items"]["AUDIT-04-B"]["score"] is None


def test_measurement_findings_are_not_semantic_zero():
    result = current.reconcile(
        {"items": {}, "measurements": {}},
        {
            "leakage": {
                "status": "fail",
                "findings": [{"kind": "full", "allowed": False}],
                "counts": {"public_files": 1, "private_files": 1},
            }
        },
    )
    assert result["items"]["AUDIT-05-B"]["score"] == 1
    assert result["items"]["CREATE-02"]["score"] is None
    assert result["items"]["AUDIT-03"]["score"] is None


def test_recipe_selection_has_no_expected_scores(tmp_path):
    import rubric_leakage_recipes as recipes

    selected = recipes.materialize(tmp_path / "case", "private-copy")
    assert "expected" not in json.dumps(selected)
    assert not any(a["role"] == "criterion_evidence" for a in selected["artifacts"])
    report = rubric_table.assess_table(tmp_path / "case", selected, None)
    assert set(report["items"]) == set(current.canonical_definitions())
    assert report["items"]["AUDIT-03"]["score"] is None
    assert report["items"]["AUDIT-05-B"]["score"] == 1
    assert report["undefined_criteria"]["METHOD-J1"]["score"] is None
    text = rubric_table.render(report)
    assert "Anticheat" in text and "METHOD-J1" in text
    assert "AC-01" not in text.split("## Evidence details")[0]


@pytest.mark.parametrize(
    "case",
    ["inventory", "private-copy", "answer-filename", "split-duplicate", "prompt-reuse", "withhold-source-inventory"],
)
def test_every_source_recipe_reaches_ordinary_observation_and_exact_repair(tmp_path, case):
    import rubric_leakage_recipes as recipes

    root = tmp_path / "case"
    selected = recipes.materialize(root, case)
    recipes.verify_input(root, case, selected)
    report = rubric_table.assess_table(root, selected, None)
    recipes.verify_observation(report, case)
    for ident, expected in recipes.expectations(case).items():
        assert report["items"][ident]["score"] == expected
    repair = tmp_path / "repair"
    repaired_selection = recipes.materialize(repair, "inventory")
    repaired = rubric_table.assess_table(repair, repaired_selection, None)
    assert repaired["items"]["AUDIT-05-B"]["score"] == 1
    assert repaired["items"]["CREATE-02"]["score"] is None


def test_removed_mutation_and_missing_observation_are_rejected(tmp_path):
    import rubric_leakage_recipes as recipes

    root = tmp_path / "case"
    selected = recipes.materialize(root, "private-copy")
    (root / "task/environment/reference-copy.py").write_text("Mutation removed")
    with pytest.raises(ValueError):
        recipes.verify_input(root, "private-copy", selected)
    with pytest.raises(ValueError):
        recipes.verify_observation(
            {"measurements": {"leakage_current": {"sources": {"leakage": {"findings": []}}}}}, "private-copy"
        )


def test_allowed_overlap_and_unknown_status_cannot_clear_cues():
    result = current.reconcile(
        {"items": {}, "measurements": {}},
        {
            "leakage": {
                "status": "unknown",
                "findings": [{"kind": "full", "allowed": True}],
                "counts": {"public_files": 1},
            }
        },
    )
    assert result["items"]["CREATE-02"]["score"] is None
    assert result["items"]["AUDIT-03"]["score"] is None
