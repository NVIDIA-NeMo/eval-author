# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A successful process or skipped suite must never become measured compatibility."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location(
    "gym_evidence", Path(__file__).resolve().parents[1] / "tools/collect_gym_evidence.py"
)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def evidence(root, marks=("", ""), route="audit"):
    nodes = "".join(
        f'<testcase name="{name}[{route}]">{mark}</testcase>' for name, mark in zip(collector.NATIVE_TESTS, marks)
    )
    (root / "junit.xml").write_text(f"<testsuites><testsuite>{nodes}</testsuite></testsuites>")
    native = root / "native" / route
    native.mkdir(parents=True)
    (native / "summary.json").write_text(json.dumps({**collector.EXPECTED, "model_performance_measured": False}))
    (native / "quality_summary.json").write_text(
        json.dumps(
            {
                "run": {
                    "verdicts": {"healthy": 0, "unhealthy": 0, "unobserved": 4},
                    "ignored_checks": [],
                    "artifacts": {"records": 4},
                }
            }
        )
    )


@pytest.mark.parametrize("route", collector.ROUTES)
def test_valid_controls_preserve_unobserved_health(tmp_path, route):
    evidence(tmp_path, route=route)
    result = collector.summarize(tmp_path, 0, route)
    assert result["status"] == "passed"
    assert result["rollout_health"]["unobserved"] == 4


@pytest.mark.parametrize(
    "marks,status",
    [(("<skipped/>", ""), "incomplete"), (("", "<failure/>"), "failed"), (("<error/>", ""), "incomplete")],
)
def test_missing_execution_is_not_a_pass(tmp_path, marks, status):
    evidence(tmp_path, marks)
    assert collector.summarize(tmp_path, 0)["status"] == status


@pytest.mark.parametrize("route", collector.ROUTES)
def test_preparation_regressions_are_failures_in_real_junit(tmp_path, route):
    # Exercise the actual harness's fixture/test boundary without needing NeMo Gym.
    harness = tmp_path / "test_gym_native.py"
    harness.write_bytes((collector.ROOT / "tests/test_gym_native.py").read_bytes())
    (tmp_path / "conftest.py").write_text(
        "def broken_builder(tmp_path):\n"
        "    raise AssertionError('synthetic preparation regression')\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        item.callspec.params['draft_builder'] = broken_builder\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *(f"{harness}::{name}[{route}]" for name in collector.NATIVE_TESTS),
            f"--junitxml={tmp_path / 'junit.xml'}",
        ],
        cwd=tmp_path,
        env={**os.environ, "EVAL_AUTHOR_GYM_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 failed" in result.stdout
    assert "synthetic preparation regression" in result.stdout
    summary = collector.summarize(tmp_path, result.returncode, route)
    assert summary["status"] == "failed"
    assert set(summary["checks"].values()) == {"failed"}


def test_successful_exit_needs_native_evidence(tmp_path):
    evidence(tmp_path)
    (tmp_path / "native/audit/summary.json").unlink()
    assert collector.summarize(tmp_path, 0)["status"] == "incomplete"


@pytest.mark.parametrize(
    "xml",
    [
        "broken",
        "<testsuites/>",
        '<testsuite><testcase name="other"/></testsuite>',
        '<testsuite><testcase name="test_native_http_execution_controls"/>'
        '<testcase name="test_native_http_execution_controls"/></testsuite>',
    ],
)
def test_incomplete_or_changed_panel_does_not_pass(tmp_path, xml):
    evidence(tmp_path)
    (tmp_path / "junit.xml").write_text(xml)
    assert collector.summarize(tmp_path, 0)["status"] == "incomplete"


def test_exit_failure_cannot_be_hidden_by_old_success(tmp_path):
    evidence(tmp_path)
    assert collector.summarize(tmp_path, 1)["status"] == "incomplete"


@pytest.mark.parametrize("route", collector.ROUTES)
def test_runtime_failure_still_writes_summary(tmp_path, route):
    output = tmp_path / "run"
    code = collector.run(output, "/nonexistent/python", tmp_path, "a" * 40, 1, route=route)
    report = json.loads((output / "gym-summary.json").read_text())
    assert code == 1
    assert report["status"] == "incomplete" and report["metrics"] is None
    assert report["finished_at"] is not None
    assert report["source_route"] == route
    assert report["protocol"] == ("gym-ledger-controls-v1" if route == "audit" else f"gym-{route}-ledger-controls-v1")
    with pytest.raises(FileExistsError):
        collector.run(output, "/nonexistent/python", tmp_path, "a" * 40, 1, route=route)


def test_socket_restriction_is_infrastructure_not_a_control_failure(tmp_path, monkeypatch):
    def blocked():
        raise PermissionError("socket unavailable")

    monkeypatch.setattr(collector, "check_local_http", blocked)
    output = tmp_path / "restricted"
    assert collector.run(output, "/unused/python", tmp_path, "a" * 40, 1) == 1
    report = json.loads((output / "gym-summary.json").read_text())
    assert report["status"] == "incomplete"
    assert report["reason"] == "runtime_or_test_execution_error"
    assert set(report["checks"].values()) == {"missing"}


@pytest.mark.parametrize("route", ["trace", "ng-trajectory"])
def test_audit_evidence_cannot_pass_as_trace_evidence(tmp_path, route):
    evidence(tmp_path)
    assert collector.summarize(tmp_path, 0, route)["status"] == "incomplete"
    nodes = "".join(f'<testcase name="{name}[{route}]"/>' for name in collector.NATIVE_TESTS)
    (tmp_path / "junit.xml").write_text(f"<testsuite>{nodes}</testsuite>")
    # Even passing trace test rows require native evidence from that same route.
    assert collector.summarize(tmp_path, 0, route)["status"] == "incomplete"


def test_ci_retains_every_route_independently_even_when_another_fails():
    workflow = yaml.safe_load((collector.ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["gym-compatibility"]
    assert job["strategy"]["fail-fast"] is False
    entries = job["strategy"]["matrix"]["include"]
    assert {row["route"] for row in entries} == set(collector.ROUTES)
    assert len({row["artifact"] for row in entries}) == len(entries)
    assert next(row for row in entries if row["route"] == "audit")["artifact"] == "gym"
    assert "secrets." not in json.dumps(job)
    uploads = [step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
    public = next(step for step in uploads if step["with"]["path"].endswith("/gym-summary.json"))
    assert public["if"] == "${{ !cancelled() }}"
    assert public["with"]["if-no-files-found"] == "error"
    assert "matrix.artifact" in public["with"]["name"]
    private = next(step for step in uploads if step != public)
    assert private["if"] == "${{ !cancelled() && github.event.repository.private == true }}"
    assert private["with"]["include-hidden-files"] is True
    execute = next(step for step in job["steps"] if "collect_gym_evidence.py" in step.get("run", ""))
    assert execute["if"] == "${{ !cancelled() }}"
    assert '--route "${{ matrix.route }}"' in execute["run"]
