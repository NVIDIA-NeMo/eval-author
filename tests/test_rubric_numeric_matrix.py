# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"))
import rubric_evidence_scope_mutations as scope


def test_unknown_scope_rejected_without_native():
    import pytest

    with pytest.raises(ValueError):
        scope.project_numeric(Path.cwd(), {"evidence_scope": {"score": 1}}, {"measurements": {}})


def test_numeric_projection_keeps_native_probe_unrun_for_static_zero():
    report = {
        "measurements": {},
        "items": {
            "AC-02": {
                "score": None,
                "remaining_gaps": [],
                "findings": [],
                "check_results": [
                    {"method": "Probe", "outcome": "NOT_RUN"},
                    {"method": "Deterministic", "outcome": "NOT_RUN"},
                ],
            }
        },
    }
    scope.apply_numeric(report, {"AC-02": {"score": 0, "strength": "E1", "reason": "source-confirmed"}})
    assert report["items"]["AC-02"]["score"] == 0
    assert report["items"]["AC-02"]["check_results"][0]["outcome"] == "NOT_RUN"
    assert report["items"]["AC-02"]["check_results"][1]["outcome"] == "FAIL"


def test_partial_scope_never_erases_existing_failure():
    report = {
        "measurements": {},
        "items": {
            "AC-01": {
                "score": 0,
                "remaining_gaps": ["defect"],
                "findings": [],
                "check_results": [{"method": "Probe", "outcome": "FAIL"}],
            }
        },
    }
    original = copy.deepcopy(report["items"])
    scope.apply_numeric(report, {"AC-01": {"score": 1, "strength": "E2", "reason": "partial"}})
    assert report["items"] == original


def test_matrix_reconciliation_does_not_trust_retained_expectation():
    runner = importlib.import_module("rubric_mutation_runner")
    altered = {"case": "wrong-inventory", "expected": {"AC-01": 3}}
    import pytest

    with pytest.raises(ValueError):
        runner.validate_matrix_expectation(altered)
