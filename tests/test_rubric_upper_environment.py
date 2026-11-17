# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("rubric_upper_environment", SCRIPTS / "rubric_upper_environment.py")
assert spec and spec.loader
upper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upper)


def fixture_rows():
    return {
        criterion: {
            "score": 2,
            "check_results": [
                {"id": criterion + ".deterministic", "method": "deterministic", "outcome": "PASS"},
                {"id": criterion + ".probe", "method": "probe", "outcome": "PASS"},
            ],
            "remaining_gaps": [],
            "limitations": [],
            "evidence_refs": [],
        }
        for criterion in upper.CRITERIA
    }


def fixture_projection():
    return {
        "index_digest": "synthetic-test-only",
        "ready": dict.fromkeys(upper.CRITERIA, True),
        "matched_sensitivity": [],
    }


def test_provider_real_rejection_and_recovery():
    result = upper.provider_controls(upper.collector.BASE)
    assert [r["errors"][0]["type"] for r in result["controls"]] == ["enum", "int_parsing", "int_parsing"]
    assert all(r["recovery"] == "accepted" for r in result["controls"])


def test_exact_duplicate_drift_and_repeat_recipes():
    recipes = upper.recipes()
    assert len(recipes) == 7
    assert recipes["reference-repeat"] == recipes["reference-batched-after"]
    assert recipes["duplicate-drift"]["requests"].count(recipes["reference-batched-after"]["requests"][1]) == 2
    assert recipes["reset"]["requests"] == []
    assert all(len(r["errors"]) == 4 for r in recipes.values())


def test_full_anchor_projection_is_not_a_universal_e3_floor():
    rows = fixture_rows()
    upper.apply(rows, fixture_projection())
    assert all(r["score"] == 3 for r in rows.values())
    assert rows["provenance.comparison-fairness"]["evidence_strength"] == "E2"
    assert rows["provenance.observed-configuration"]["evidence_strength"] == "E3"
    assert all(r["upper_environment"]["independent_human"] is False for r in rows.values())


@pytest.mark.parametrize("outcome", ["FAIL", "ERROR", "BLOCKED", "PARTIAL"])
def test_failed_or_unrelated_partial_check_is_not_erased(outcome):
    rows = fixture_rows()
    row = rows["provenance.runtime-route"]
    row["check_results"][0]["outcome"] = outcome
    upper.apply(rows, fixture_projection())
    assert row["score"] == 2
    assert row["check_results"][0]["outcome"] == outcome


def test_exact_owned_missing_repeat_is_resolved_only():
    rows = fixture_rows()
    check = rows["provenance.observed-configuration"]["check_results"][-1]
    check.update(outcome="PARTIAL", remaining_gap="Repeated controls share one collector; independent repeat absent.")
    upper.apply(rows, fixture_projection())
    assert rows["provenance.observed-configuration"]["score"] == 3
    assert check["outcome"] == "PASS"


def test_prior_decision_missing_cannot_promote_and_unknown_gap_preserved():
    rows = fixture_rows()
    projection = fixture_projection()
    projection["ready"]["provenance.comparison-fairness"] = False
    rows["provenance.runtime-route"]["remaining_gaps"] = ["Unrelated unresolved finding"]
    upper.apply(rows, projection)
    assert rows["provenance.comparison-fairness"]["score"] == 2
    assert rows["provenance.runtime-route"]["remaining_gaps"] == ["Unrelated unresolved finding"]


def test_no_companion_no_changes():
    rows = fixture_rows()
    before = deepcopy(rows)
    upper.apply(rows, None)
    assert rows == before
