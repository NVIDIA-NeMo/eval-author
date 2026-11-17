# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))


def module():
    assert (SCRIPTS / "rubric_semantic_numeric_mutations.py").is_file(), "semantic subject adapter missing"
    return importlib.import_module("rubric_semantic_numeric_mutations")


def test_contradictory_public_contract_ordinary_table(tmp_path):
    m = module()
    import rubric_table

    selection = m.materialize(tmp_path / "case", "contradictory-contract")
    result = rubric_table.measure(tmp_path / "case", selection)
    row = result["items"]["task-contract.clear-contract"]
    assert row["score"] == 0
    assert row["evidence_strength"] == "E1"
    assert "semantic_numeric" in result["measurements"]
    assert (
        result["measurements"]["semantic_numeric"]["observations"]["controls"]["reference"]["selected"]["health"]
        == "ok"
    )
    assert all("expected" not in key for key in selection)
    assert row["check_results"][1]["outcome"] != "PASS"


def test_resealed_tampering_is_not_a_numeric_subject(tmp_path):
    m = module()
    import rubric_table

    root = tmp_path / "case"
    selection = m.materialize(root, "combined-revisions")
    original = m.project(root, selection)
    assert original["findings"]["provenance.evidence-revision-consistency"]["score"] == 0
    path = root / "task" / m.SUBJECT
    value = json.loads(path.read_text())
    value["records"][0]["result"]["total_credit"] = 12345
    path.write_text(json.dumps(value))
    selection = rubric_table.select_task(root, m.TASK)
    assert m.project(root, selection) is None
    report = rubric_table.measure(root, selection)
    assert report["items"]["provenance.evidence-revision-consistency"]["score"] is None
    assert "semantic_numeric" not in report["measurements"]


def test_fresh_repair_and_late_change(tmp_path, monkeypatch):
    m = module()
    import pytest
    import rubric_table

    root = tmp_path / "case"
    selection = m.materialize(root, "contradictory-contract")
    original_apply = m.apply

    def change(report, projection):
        original_apply(report, projection)
        path = root / "task/instruction.md"
        path.write_text(path.read_text() + "\nAltered after assessment\n")

    monkeypatch.setattr(m, "apply", change)
    with pytest.raises(ValueError):
        rubric_table.measure(root, selection)
    repair = tmp_path / "repair"
    restored = m.materialize(repair, "repair")
    report = rubric_table.measure(repair, restored)
    assert report["items"]["task-contract.clear-contract"]["score"] is None
    assert all((repair / "task" / name).read_bytes() == raw for name, raw in m.source._current().items())


def test_missing_subject_evidence_preserves_ordinary_missingness(tmp_path):
    m = module()
    import rubric_table

    root = tmp_path / "case"
    selection = m.materialize(root, "contradictory-contract")
    selection["artifacts"][0]["access"] = "denied"
    report = rubric_table.measure(root, selection)
    assert "semantic_numeric" not in report["measurements"]
    assert report["items"]["task-contract.clear-contract"]["score"] is None


def test_batch_has_normal_table_repair_and_final_replay(tmp_path):
    m = module()
    assert callable(getattr(m, "run_batch", None)), "reusable ordinary batch missing"
    expected = {"contradictory-contract": {"task-contract.clear-contract": 0}}
    summary = m.run_batch(tmp_path / "batch", expected)
    assert summary["verified"] is True
    assert summary["matched_cells"] == 1
    assert summary["cases"]["contradictory-contract"]["repair_equal"] is True


def test_remaining_numeric_subjects_and_explicit_owner_gap(tmp_path, monkeypatch):
    # This test specifically exercises absence, independent of operator host state.
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", raising=False)
    m = module()
    import rubric_table

    expected = {
        "ambiguous-contract": ("task-contract.clear-contract", 1),
        "hidden-authority": ("task-contract.discoverable-requirements", 0),
        "disputed-authority": ("task-contract.discoverable-requirements", 1),
        "disputed-scoring": ("task-contract.scoring-authority", 1),
        "combined-revisions": ("provenance.evidence-revision-consistency", 0),
        "partial-revisions": ("provenance.evidence-revision-consistency", 1),
        "untested-comparison": ("provenance.comparison-fairness", 1),
        "advantaged-comparison": ("provenance.comparison-fairness", 1),
    }
    for case, (ident, score) in expected.items():
        root = tmp_path / case
        selection = m.materialize(root, case)
        report = rubric_table.measure(root, selection)
        assert report["items"][ident]["score"] == score, case
        proof = report["measurements"]["semantic_numeric"]
        assert proof["observations"]["human_adjudication"] is False
        if case == "advantaged-comparison":
            assert proof["observations"]["comparison"]["advantage_demonstrated"] is True
            assert proof["observations"]["comparison"]["owner_decision"] is None
        if case == "hidden-authority":
            assert proof["observations"]["controls"]["missing-completion"]["selected"]["accepted"] is False
            assert proof["observations"]["controls"]["missing-completion"]["counterpart"]["accepted"] is True
        if case == "combined-revisions":
            assert proof["observations"]["revision_review"]["incompatible_combined"] is True
        if case == "partial-revisions":
            assert proof["observations"]["revision_review"]["missing_bindings"]
