# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Record native audit results, resume reviewed findings, and invalidate changed inputs."""

import copy
import hashlib
import importlib
from pathlib import Path

from test_skill_contract import _AUDIT_MEASURE, _AUDIT_REPORT, _run_json_script, _write_atif_trace, _write_audit

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


def ref(path):
    return {"path": str(path), "digest": hashlib.sha256(path.read_bytes()).hexdigest()}


def selection(path):
    return {
        "status": "selected",
        "paths": [str(path)],
        "candidates": [],
        "exclusions": [],
        "source": "User selected this source",
    }


def test_native_results_resume_and_changed_trace_reopens_audit(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    state = importlib.import_module("journey_state")
    audit = importlib.import_module("audit_state")
    specification = _write_audit(tmp_path)
    state.initialize(tmp_path, agent="example-agent", journey="audit")
    state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Observed local audit prerequisite"}
                for key in state.COMMON_PREREQUISITES | {"data"}
            }
        },
    )
    state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User confirmed intent",
        validation="Valid fixture",
    )
    suite = tmp_path / "evals"
    suite.mkdir()
    cases = suite / "cases.json"
    cases.write_text('{"cases": ["lookup"]}')
    trace = tmp_path / "trajectory.json"
    _write_atif_trace(trace, tool_calls=["customer.lookup"])
    value = audit.new_record()
    value.update(
        suites=selection(suite),
        traces=selection(trace),
        suite_files=[str(cases)],
        trace_files=[str(trace)],
        specification=str(specification),
    )

    def save(**extra):
        return state.update(
            tmp_path,
            {"scopes": {"audit": {"lifecycle": copy.deepcopy(value)}}, **extra},
            expected_revision=state.load_state(tmp_path)["revision"],
        )

    def progress():
        return audit.progress(tmp_path, state.load_state(tmp_path))

    save()
    value["checks_approval"] = {"digest": progress()["checks_digest"], "source": "User approved these checks"}
    save()
    assert progress()["stage"] == "measurement"

    output = tmp_path / "measurements"
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE,
        "--audit",
        str(specification),
        "--trace",
        str(trace),
        "--task-id",
        "lookup",
        "--run-id",
        "selected-run",
        "--out-dir",
        str(output),
        "--measure",
        "tool_calls,capabilities,failure_cases",
        "--evidence-class",
        "agent_under_test",
    )
    assert code == 0, stderr or result
    coverage = sorted(output.rglob("coverage.json"))
    assert len(coverage) == 3
    aggregate = tmp_path / ".eval-author/audit-coverage-report.json"
    command = ["--audit", str(specification), "--out", str(aggregate)]
    for path in coverage:
        command.extend(["--coverage", str(path)])
    code, result, stderr = _run_json_script(_AUDIT_REPORT, *command)
    assert code == 0, stderr or result
    code, result, stderr = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(aggregate))
    assert code == 0 and result["valid"], stderr or result
    artifacts = [*sorted(output.rglob("*.json")), aggregate]
    value["measurement"] = {
        "checks_digest": value["checks_approval"]["digest"],
        "status": "complete",
        "evidence": [ref(path) for path in artifacts],
        "source": "measure.py and report.py succeeded; report.py --verify-existing returned valid=true",
    }
    save()
    assert progress()["measurement_complete"]

    report = tmp_path / ".eval-author/audit-coverage-report.md"
    report.write_text("# Audit coverage report\n\nLookup is exercised; missing intent judgments limit the findings.\n")
    value["findings"] = {"report": ref(report), "source": "Presented findings and evidence limits"}
    save()
    value["findings_review"] = {"digest": progress()["findings_digest"], "source": "User accepted the findings only"}
    save(journey_status={"audit": "complete"})
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    resumed = state.view(state.load_state(tmp_path))
    assert resumed["audit"]["complete"] and resumed["checklist"][2]["complete"]
    assert resumed["audit"]["stage"] == "next_action"
    assert not resumed["audit"]["next_action_current"]
    assert all(path.read_bytes() == contents for path, contents in before.items())

    value["next_action"] = {
        "kind": "improve",
        "findings_digest": resumed["audit"]["findings_digest"],
        "source": "User explicitly requests improvement proposals",
    }
    save()
    assert progress()["next_action_current"]
    assert not (tmp_path / ".eval-author/proposals").exists()

    trace.write_text(trace.read_text() + "\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    reopened = state.view(state.load_state(tmp_path))
    assert reopened["audit"]["stage"] == "checks_review"
    for field in ("checks_approved", "measurement_complete", "findings_reviewed", "next_action_current", "complete"):
        assert not reopened["audit"][field]
    assert not reopened["checklist"][2]["complete"]
    assert all(path.read_bytes() == contents for path, contents in before.items())
