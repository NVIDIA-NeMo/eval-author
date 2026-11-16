# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from test_rubric_measurements import rubric_panel as rubric_panel
from test_rubric_measurements import rubric_panel_runs as rubric_panel_runs


def test_one_command_package_acceptance_is_separate(rubric_panel):
    report = rubric_panel
    assert report["counts"] == {"cases": 14, "assessments": 5, "evidence_cases": 13}
    package = report["package_acceptance"]
    assert package["status"] == "passed"
    assert package["runtime_acceptance"] == "not_measured"
    assert package["merit_acceptance"] == "not_assessed"
    rows = {row["id"]: row for row in package["cases"]}
    assert package["counts"] == {"cases": len(rows), "matched": len(rows)}
    assert {
        "shared-valid",
        "shared-missing",
        "shared-denied",
        "shared-unresolved",
        "shared-unknown-route",
        "retained-approved",
        "retained-rejected",
        "config-bound-equal",
        "config-run-mismatch",
        "config-task-mismatch",
        "config-digest-mismatch",
    } <= rows.keys()
    assert all(row["matches_expectations"] and row["inputs_unchanged"] for row in rows.values())
    for name in ("valid", "missing", "denied", "unresolved", "unknown-route"):
        observed = rows["shared-" + name]["observed"]
        assert observed["same_identity_records"]
        assert observed["retained_credit"]["identity"] == observed["coverage"]["identity"]
        assert observed["coverage"]["preflight_success"] is False
        assert observed["retained_credit"]["preflight_success"] is (name == "valid")
        assert observed["coverage"]["measurement_allowed"] is (name == "valid")
    assert rows["shared-valid"]["observed"]["coverage"]["route"]["bound_dependency"] is True
    for name in ("unbound-route", "dependency-mismatch"):
        observed = rows["shared-" + name]["observed"]
        assert observed["retained_credit"]["preflight_success"] is False
        assert observed["coverage"]["measurement_allowed"] is False
        assert observed["coverage"]["route"]["bound_dependency"] is False
    assert rows["retained-approved"]["observed"]["status"] == "pass"
    assert rows["retained-rejected"]["observed"]["status"] == "fail"
    for name in ("run", "task", "digest"):
        config = rows[f"config-{name}-mismatch"]["observed"]
        assert config["comparison_status"] == "equal"
        assert config["binding_accepted"] is False
