# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-group integrity scores are not canonical rubric merit."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def scoring(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return importlib.import_module("skills.eval-author.scripts.verification_scoring")


@pytest.mark.parametrize("passed,failed,score", [(0, 4, 0), (1, 3, 1), (2, 2, 1), (3, 1, 2), (4, 0, 3)])
def test_complete_group_anchors(scoring, passed, failed, score):
    result = scoring.score_check_group(passed=passed, failed=failed)
    assert result["score"] == score
    assert result["outcome"] == "assessed"
    assert result["applicable"] == result["assessed"] == passed + failed
    assert result["passed"] == passed
    assert result["failed"] == failed
    assert result["unknown"] == result["excluded"] == 0
    assert result["coverage"] == 1.0
    assert result["schema"] == "verification-check-group-v1"
    assert result["next_action"]


@pytest.mark.parametrize("passed,failed,unknown,excluded", [(0, 0, 0, 0), (0, 0, 0, 4), (4, 0, 1, 2), (0, 4, 1, 2)])
def test_incomplete_group_has_no_score(scoring, passed, failed, unknown, excluded):
    result = scoring.score_check_group(passed=passed, failed=failed, unknown=unknown, excluded=excluded)
    assert result["score"] is None
    assert result["outcome"] == "unassessed"
    assert result["applicable"] == passed + failed + unknown
    assert result["assessed"] == passed + failed
    assert result["excluded"] == excluded
    assert result["coverage"] == ((passed + failed) / result["applicable"] if result["applicable"] else None)


@pytest.mark.parametrize("field", ["passed", "failed", "unknown", "excluded"])
@pytest.mark.parametrize("invalid", [-1, True, False, 1.0, "1", None])
def test_invalid_counts_are_rejected(scoring, field, invalid):
    counts = dict(passed=1, failed=1, unknown=0, excluded=0)
    counts[field] = invalid
    with pytest.raises(ValueError, match=field):
        scoring.score_check_group(**counts)


def test_excluded_checks_do_not_change_score_or_denominator(scoring):
    result = scoring.score_check_group(passed=3, failed=1, excluded=100)
    assert result["score"] == 2
    assert result["applicable"] == result["assessed"] == 4
    assert result["coverage"] == 1.0


@pytest.mark.parametrize("freshness", ["unknown", "stale", "current"])
def test_native_groups_separate_consistency_from_current_verification(scoring, freshness):
    native = {
        "controls": [
            {"receipt": "a", "integrity": {"status_claim": "consistent"}},
            {"receipt": "b", "integrity": {"status_claim": "contradiction"}},
        ],
        "required_verification": {
            "cases": [{"state": "pass", "freshness": freshness}, {"state": "fail", "freshness": freshness}]
        },
    }
    groups = {row["id"]: row for row in scoring.score_integrity_groups(native)}
    assert set(groups) == {"receipt_consistency", "required_verification"}
    assert groups["receipt_consistency"]["score"] == 1
    required = groups["required_verification"]
    assert required["score"] == (1 if freshness == "current" else None)
    assert required["unknown"] == (0 if freshness == "current" else 2)
    for group in groups.values():
        assert group["scope"] == "verification_integrity"
        assert group["name"]
        assert group["evidence_refs"]


@pytest.mark.parametrize(
    "state", ["missing", "unknown", "unassessed", "stale", "execution_error", "mismatched", "nonsense"]
)
def test_nonverdict_required_states_are_unknown(scoring, state):
    group = scoring.score_integrity_groups(
        {"required_verification": {"cases": [{"state": state, "freshness": "current"}]}}
    )[1]
    assert group["score"] is None
    assert group["unknown"] == 1
    assert group["failed"] == 0


@pytest.mark.parametrize(
    "native",
    [
        {},
        {"controls": None, "required_verification": None},
        {"controls": [None], "required_verification": {"cases": [None]}},
    ],
)
def test_missing_or_malformed_native_groups_stay_unknown(scoring, native):
    for group in scoring.score_integrity_groups(native):
        assert group["score"] is None
        assert group["unknown"] == 1


def test_empty_native_groups_are_unassessed(scoring):
    for group in scoring.score_integrity_groups({"controls": [], "required_verification": {"cases": []}}):
        assert group["score"] is None
        assert group["applicable"] == 0
        assert group["coverage"] is None


def test_review_candidates_and_nonclaims_are_excluded_not_failures(scoring):
    native = {
        "controls": [
            {"receipt": "candidate", "integrity": {"status_claim": "contradiction"}},
            {"receipt": "baseline", "integrity": {"status_claim": "excluded"}},
            {"receipt": "negative", "integrity": {"status_claim": "not_applicable"}},
            {"receipt": "current", "integrity": {"status_claim": "consistent"}},
        ],
        "superseded_references": {"findings": [{"receipt": "candidate", "status": "review_candidate"}]},
        "required_verification": {"cases": [{"state": "review_candidate", "freshness": "current"}]},
    }
    receipt, required = scoring.score_integrity_groups(native)
    assert receipt["score"] == 3
    assert receipt["excluded"] == 3
    assert receipt["applicable"] == 1
    assert receipt["failed"] == required["failed"] == 0
    assert required["score"] is None
    assert required["excluded"] == 1
