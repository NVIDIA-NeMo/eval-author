# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_one_command_package_acceptance_is_repeatable_and_separate():
    command = [sys.executable, "-B", str(ROOT / "tools/run_rubric_measurements.py")]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["counts"] == {"cases": 14, "assessments": 5, "evidence_cases": 8}
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
        assert observed["w01"]["identity"] == observed["w05"]["identity"]
        assert observed["w05"]["preflight_success"] is False
        assert observed["w01"]["preflight_success"] is (name == "valid")
        assert observed["w05"]["measurement_allowed"] is (name == "valid")
    assert rows["shared-valid"]["observed"]["w05"]["route"]["bound_dependency"] is True
    for name in ("unbound-route", "dependency-mismatch"):
        observed = rows["shared-" + name]["observed"]
        assert observed["w01"]["preflight_success"] is False
        assert observed["w05"]["measurement_allowed"] is False
        assert observed["w05"]["route"]["bound_dependency"] is False
    assert rows["retained-approved"]["observed"]["status"] == "pass"
    assert rows["retained-rejected"]["observed"]["status"] == "fail"
    for name in ("run", "task", "digest"):
        config = rows[f"config-{name}-mismatch"]["observed"]
        assert config["comparison_status"] == "equal"
        assert config["binding_accepted"] is False
