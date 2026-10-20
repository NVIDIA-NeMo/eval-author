# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_skill_contract import (
    _AUDIT_MEASURE,
    _AUDIT_REPORT,
    _run_json_script,
    _write_atif_trace,
    _write_audit,
    _write_capability_judgments,
)


def measured(tmp_path, evidence_class="agent_under_test"):
    audit = _write_audit(tmp_path)
    trace = tmp_path / "trace.json"
    _write_atif_trace(trace, tool_calls=["customer.lookup"])
    judgments = tmp_path / "judgments.json"
    _write_capability_judgments(judgments, trace=trace)
    out = tmp_path / "measurements"
    command = [
        "--audit",
        str(audit),
        "--trace",
        str(trace),
        "--task-id",
        "fixture",
        "--out-dir",
        str(out),
        "--measure",
        "tool_calls,capabilities",
        "--capability-judgments",
        str(judgments),
    ]
    if evidence_class is None:
        command.append("--skip-evidence-class")
    else:
        command.extend(["--evidence-class", evidence_class])
    for _ in range(2):
        code, result, stderr = _run_json_script(_AUDIT_MEASURE, *command)
        assert code == 0, stderr or result
        contents = {path.relative_to(out): path.read_bytes() for path in out.rglob("*.json")}
        if _ == 0:
            original = contents
        else:
            assert contents == original
    report = tmp_path / "aggregate.json"
    command = ["--audit", str(audit), "--coverage-dir", str(out), "--out", str(report)]
    code, result, stderr = _run_json_script(_AUDIT_REPORT, *command)
    assert code == 0, stderr or result
    before = report.read_bytes()
    code, _, _ = _run_json_script(_AUDIT_REPORT, *command)
    assert code == 0 and report.read_bytes() == before
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(report))
    assert code == 0, result
    return audit, trace, judgments, report


@pytest.mark.parametrize("mutation", ["trace", "audit", "source", "measurement", "aggregate", "judgment"])
@pytest.mark.parametrize("evidence_class", ["agent_under_test", "reference_solution", None])
def test_bound_aggregate_rejects_every_upstream_mutation(tmp_path, mutation, evidence_class):
    audit, trace, judgments, report = measured(tmp_path, evidence_class)
    if mutation == "trace":
        trace.write_text(trace.read_text() + "\n")
    elif mutation == "audit":
        audit.write_text(audit.read_text().replace("example-agent", "changed-agent"))
    elif mutation == "source":
        (tmp_path / "ETHOS.md").write_text("Changed source")
    elif mutation == "measurement":
        source = Path(json.loads(report.read_text())["binding"]["inputs"][0]["path"])
        source.write_text(source.read_text() + "\n")
    elif mutation == "aggregate":
        payload = json.loads(report.read_text())
        payload["coverage"]["overall"]["covered_count"] = 999
        report.write_text(json.dumps(payload))
    else:
        payload = json.loads(judgments.read_text())
        payload["judged_by"] = "edited-review"
        judgments.write_text(json.dumps(payload))
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(report))
    assert code == 1 and not result["written"], result


def test_copied_measurement_with_new_path_cannot_inflate_trials(tmp_path):
    audit, _, _, report = measured(tmp_path)
    source = Path(json.loads(report.read_text())["binding"]["inputs"][0]["path"])
    copied = tmp_path / "copy.json"
    copied.write_bytes(source.read_bytes())
    code, result, _ = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage",
        str(source),
        "--coverage",
        str(copied),
        "--out",
        str(tmp_path / "duplicate.json"),
    )
    assert code == 1 and "duplicate run" in result["error"]


def test_bound_aggregate_keeps_reference_coverage_separate(tmp_path):
    audit, trace, _, report = measured(tmp_path, "reference_solution")
    agent_trace = tmp_path / "agent.json"
    _write_atif_trace(agent_trace, tool_calls=[])
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE,
        "--audit",
        str(audit),
        "--trace",
        str(agent_trace),
        "--task-id",
        "fixture",
        "--run-id",
        "agent-run",
        "--evidence-class",
        "agent_under_test",
        "--out-dir",
        str(tmp_path / "measurements"),
    )
    assert code == 0, stderr or result
    code, result, stderr = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage-dir",
        str(tmp_path / "measurements"),
        "--out",
        str(report),
    )
    assert code == 0, stderr or result
    payload = json.loads(report.read_text())
    by_class = payload["coverage_by_evidence_class"]
    assert "customer.lookup" not in payload["covered"]
    assert "customer.lookup" in by_class["reference_solution"]["covered"]
    assert payload["covered"] == by_class["agent_under_test"]["covered"]
    assert len(payload["binding"]["inputs"]) == 3
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(report))
    assert code == 0, result
    trace.write_text(trace.read_text() + "\n")
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(report))
    assert code == 1 and not result["written"], result


def test_relative_unclassified_inputs_can_be_reverified(tmp_path):
    audit, _, _, _ = measured(tmp_path, None)
    result = subprocess.run(
        [
            sys.executable,
            str(_AUDIT_REPORT),
            "--audit",
            str(audit),
            "--coverage-dir",
            "measurements",
            "--out",
            "relative.json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(tmp_path / "relative.json"))
    assert code == 0, result


@pytest.mark.parametrize("evidence_class", ["agent_under_test", None])
def test_aggregate_survives_workspace_relocation(tmp_path, evidence_class):
    workspace = tmp_path / "original"
    workspace.mkdir()
    _, _, _, report = measured(workspace, evidence_class)
    retained = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    relocated = tmp_path / "retained"
    workspace.rename(relocated)
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(relocated / report.name))
    assert code == 0, result
    assert retained == {p.relative_to(relocated): p.read_bytes() for p in relocated.rglob("*") if p.is_file()}
    (relocated / "trace.json").write_text("{}")
    code, result, _ = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(relocated / report.name))
    assert code == 1 and result["valid"] is False


@pytest.mark.parametrize("contents", [None, b"{", b"\xff"])
def test_missing_or_corrupt_judgments_produce_structured_errors(tmp_path, contents):
    audit, _, judgments, _ = measured(tmp_path)
    if contents is None:
        judgments.unlink()
    else:
        judgments.write_bytes(contents)
    code, result, stderr = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage-dir",
        str(tmp_path / "measurements"),
        "--out",
        str(tmp_path / "broken.json"),
    )
    assert code == 1 and result["error_type"] == "coverage_input"
    assert str(judgments) in result["error"]
    assert "Traceback" not in stderr
