# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
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


def measured(tmp_path):
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
def test_bound_aggregate_rejects_every_upstream_mutation(tmp_path, mutation):
    audit, trace, judgments, report = measured(tmp_path)
    if mutation == "trace":
        trace.write_text(trace.read_text() + "\n")
    elif mutation == "audit":
        audit.write_text(audit.read_text().replace("example-agent", "changed-agent"))
    elif mutation == "source":
        (tmp_path / "ethos.md").write_text("Changed source")
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
