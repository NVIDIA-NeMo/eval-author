# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))

import rubric_create02_subject as subject  # noqa: E402
import rubric_table  # noqa: E402
from rubric_leakage_presentation import presentation  # noqa: E402


def test_new_subject_observation_and_no_approval_transfer(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    root = tmp_path / "subject"
    selected = subject.build(root)
    report = rubric_table.assess_table(root, selected, None)
    observation = report["measurements"]["create02_subject"]
    assert observation["material_bypass_observed"]
    assert observation["reviews"] == {"owner": None, "agent": None}
    assert report["items"]["CREATE-02"]["score"] is None
    assert len(observation["pairs"]) == 14
    for pair in observation["pairs"]:
        for arm in (pair["baseline"], pair["intervention"]):
            assert arm["feasible"]
            assert arm["scorer_repair_rescore"] == arm["evidence_oracle"]
        if pair["class"] == "misleading-cue" and pair["policy"] == "cue-only":
            assert pair["published_transition"] == [0, 1]
            assert pair["evidence_transition"] == [1, 0]
            assert [entry["path"] for entry in pair["intervention"]["access_trace"]] == ["presentation.json"]
            assert "evidence.json" in pair["intervention"]["input_inventory"]
        if pair["class"] == "misleading-cue" and pair["policy"] == "evidence-first":
            assert pair["evidence_transition"] == [1, 1]
            assert pair["baseline"]["answer"] == pair["intervention"]["answer"]
    assert "Exact new-subject owner and agent qualification required" in rubric_table.render(report)


def test_resealed_changed_claim_rejected(tmp_path):
    root = tmp_path / "subject"
    selected = subject.build(root)
    changed = json.loads((root / "subject.json").read_text())
    changed["claim"] = "Fixed actions only"
    (root / "subject.json").write_text(json.dumps(changed))
    import hashlib

    selected["artifacts"][0]["digest"] = hashlib.sha256((root / "subject.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        subject.collect(root, selected)


def test_subject_cannot_overlay_approved_base(tmp_path):
    root = tmp_path / "subject"
    selected = subject.build(root)
    selected["example_assessment"] = {"task_config_id": "prior-task"}
    with pytest.raises(ValueError):
        subject.collect(root, selected)


def test_display_preserves_original_pending_text_and_decisions():
    row = {
        "item_id": "CREATE-02",
        "score": 2,
        "check_results": [{"outcome": "PASS", "method": "llm_as_judge", "remaining_gap": "Fresh review required"}],
    }
    review = {"revoked": False, "event": {"decisions": {"CREATE-02": {"decision": "approve"}}}}
    report = {
        "items": {"CREATE-02": row},
        "measurements": {
            "leakage_bounded": {
                "criterion": "CREATE-02",
                "reviews": {"owner": review, "agent": review},
                "observations": {"qualification": "Pending owner"},
            }
        },
    }
    before = deepcopy(report)
    displayed = presentation(report)
    assert report == before
    assert displayed["items"]["CREATE-02"]["check_results"][0]["historical_predecision_gap"] == "Fresh review required"
    assert (
        displayed["measurements"]["leakage_bounded"]["observations"]["historical_predecision_qualification"]
        == "Pending owner"
    )
