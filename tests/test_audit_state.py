# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("audit_state")


def selection(paths, *, source="User selected these exact sources", status="selected"):
    return {"status": status, "paths": paths, "candidates": [], "exclusions": [], "source": source}


def selected(audit):
    value = audit.new_record()
    value.update(
        suites=selection(["suites/refund"]),
        traces=selection(["runs"]),
        suite_files=["suites/refund/cases.json", "suites/refund/grader.py"],
        trace_files=["runs/refund.json"],
        specification=".eval-author/audit.md",
    )
    state = {
        "repository": "/synthetic/audit-repository",
        "agent": "support",
        "journey": "audit",
        "ethos": {"path": "ETHOS.md", "stage": "complete", "confirmed_digest": audit.fingerprint("intent")},
        "scopes": {"audit": {"lifecycle": value}},
        "journey_status": {},
    }
    paths = value["suite_files"] + value["trace_files"] + [value["specification"]]
    return state, value, {"files": {path: audit.fingerprint(path) for path in paths}}


def approved(audit):
    state, value, observed = selected(audit)
    value["checks_approval"] = {
        "digest": audit.derive(state, observed)["checks_digest"],
        "source": "User approved the checks",
    }
    return state, value, observed


def measured(audit):
    state, value, observed = approved(audit)
    artifact = {"path": ".eval-author/audit-coverage-report.json", "digest": audit.fingerprint("measurement")}
    value["measurement"] = {
        "checks_digest": value["checks_approval"]["digest"],
        "status": "complete",
        "evidence": [artifact],
        "source": "Measurement and native report verification completed for every selected input and method",
    }
    report = {"path": ".eval-author/audit-coverage-report.md", "digest": audit.fingerprint("findings")}
    value["findings"] = {"report": report, "source": "Presented strengths, gaps, and limits"}
    observed["files"].update({ref["path"]: ref["digest"] for ref in (artifact, report)})
    return state, value, observed


def reviewed(audit):
    state, value, observed = measured(audit)
    value["findings_review"] = {
        "digest": audit.derive(state, observed)["findings_digest"],
        "source": "User accepts findings only",
    }
    return state, value, observed


@pytest.mark.parametrize(
    "stage", ["suite_selection", "trace_selection", "checks_review", "findings_review", "next_action"]
)
def test_restart_resumes_each_saved_decision(audit, monkeypatch, stage):
    state, value, observed = reviewed(audit)
    if stage == "suite_selection":
        value["suites"] = audit.new_record()["suites"]
    elif stage == "trace_selection":
        value["traces"] = audit.new_record()["traces"]
    elif stage == "checks_review":
        value["checks_approval"] = None
    elif stage == "findings_review":
        value["findings_review"] = None
    previous = copy.deepcopy(state)
    value["pending"] = {
        "stage": stage,
        "digest": audit.derive(state, observed)["checkpoint_digests"][stage],
        "question": f"Please resolve {stage}",
        "source": "Question presented to user",
    }
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    audit.transition(Path("/synthetic"), previous, state)
    resumed = audit.derive(json.loads(json.dumps(state)), observed)
    assert resumed["stage"] == stage
    assert resumed["pending_checkpoint"] == value["pending"]


@pytest.mark.parametrize("change", ["agent", "ethos", "specification", "suite", "trace", "selection", "exclusion"])
def test_input_changes_reopen_entire_audit_without_erasing_decisions(audit, change):
    state, value, observed = reviewed(audit)
    decisions = copy.deepcopy(value)
    if change == "agent":
        state["agent"] = "other-agent"
    elif change == "ethos":
        state["ethos"]["confirmed_digest"] = audit.fingerprint("changed intent")
    elif change == "selection":
        value["traces"]["paths"] = ["runs/refund.json"]
    elif change == "exclusion":
        value["traces"]["exclusions"] = [{"path": "runs/other.json", "reason": "Out of scope", "source": "User answer"}]
    else:
        path = {
            "specification": value["specification"],
            "suite": value["suite_files"][1],
            "trace": value["trace_files"][0],
        }[change]
        observed["files"][path] = audit.fingerprint("changed bytes")
    result = audit.derive(state, observed)
    assert result["stage"] == "checks_review"
    assert not any(result[key] for key in ("checks_approved", "measurement_complete", "findings_reviewed", "complete"))
    for key in ("checks_approval", "measurement", "findings_review"):
        assert value[key] == decisions[key]


def test_report_changes_reopen_findings_only(audit):
    state, value, observed = reviewed(audit)
    report = value["findings"]["report"]
    report["digest"] = observed["files"][report["path"]] = audit.fingerprint("revised findings")
    result = audit.derive(state, observed)
    assert result["checks_approved"] and result["measurement_complete"]
    assert not result["findings_reviewed"]
    assert result["stage"] == "findings_review"


def test_missing_evidence_reopens_measurement(audit):
    state, value, observed = reviewed(audit)
    observed["files"][value["measurement"]["evidence"][0]["path"]] = None
    result = audit.derive(state, observed)
    assert result["checks_approved"] and not result["complete"]
    assert result["stage"] == "measurement"


@pytest.mark.parametrize("together", [False, True])
def test_measurement_requires_previously_saved_approval(audit, monkeypatch, together):
    previous, _, observed = selected(audit)
    updated, value, measured_observations = measured(audit)
    if not together:
        value["checks_approval"] = None
    observed["files"].update(measured_observations["files"])
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    with pytest.raises(ValueError, match="Save current check approval"):
        audit.transition(Path("/synthetic"), previous, updated)


@pytest.mark.parametrize("status", ["partial", "deferred"])
def test_partial_or_deferred_measurement_cannot_complete_audit(audit, status):
    state, value, observed = measured(audit)
    value["measurement"]["status"] = status
    value["measurement"]["source"] = "User defers measurement" if status == "deferred" else "One method failed"
    value["findings_review"] = {
        "digest": audit.derive(state, observed)["findings_digest"],
        "source": "User accepts limitations",
    }
    result = audit.derive(state, observed)
    assert not result["measurement_complete"] and not result["complete"]
    assert result["findings_reviewed"] == (status == "deferred")


def test_improvement_requires_its_own_explicit_current_decision(audit, monkeypatch):
    previous, _, observed = reviewed(audit)
    updated = copy.deepcopy(previous)
    value = audit.record(updated)
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    result = audit.derive(updated, observed)
    assert value["next_action"] is None and not result["next_action_current"]
    value["next_action"] = {
        "kind": "improve",
        "findings_digest": result["findings_digest"],
        "source": "User requests improvements",
    }
    audit.transition(Path("/synthetic"), previous, updated)
    assert audit.derive(updated, observed)["next_action_current"]
    value["next_action"]["source"] = ""
    with pytest.raises(ValueError, match="provenance"):
        audit.validate(value)


@pytest.mark.parametrize("change", ["checks_approval", "findings_review", "next_action", "pending"])
def test_stale_decisions_are_rejected(audit, monkeypatch, change):
    previous, _, observed = reviewed(audit)
    updated = copy.deepcopy(previous)
    value = audit.record(updated)
    stale = audit.fingerprint("stale decision")
    value[change] = {"digest": stale, "source": "User answer"}
    if change == "next_action":
        value[change] = {"kind": "improve", "findings_digest": stale, "source": "User answer"}
    elif change == "pending":
        value[change].update(stage="next_action", question="What next?")
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    with pytest.raises(ValueError):
        audit.transition(Path("/synthetic"), previous, updated)


@pytest.mark.parametrize("block", ["suite_pending", "trace_pending", "excluded", "candidate"])
def test_observation_never_reads_unselected_traces(audit, monkeypatch, tmp_path, block):
    state, value, _ = selected(audit)
    state["repository"] = str(tmp_path)
    if block == "suite_pending":
        value["suites"] = audit.new_record()["suites"]
    elif block == "trace_pending":
        value["traces"] = audit.new_record()["traces"]
    elif block == "excluded":
        value["traces"]["exclusions"] = [{"path": "runs", "reason": "Exclude these runs", "source": "User answer"}]
    else:
        value["traces"]["paths"] = ["selected-runs"]
        value["traces"]["candidates"] = ["runs"]
    reads = []

    def read(path, remaining):
        reads.append(path.relative_to(tmp_path).as_posix())
        assert "runs" not in path.parts
        return audit.fingerprint(str(path)), 10

    monkeypatch.setattr(importlib.import_module("journey_state"), "_file_digest", read)
    audit.observe(tmp_path, state)
    assert "runs/refund.json" not in reads


def test_observation_hashes_only_declared_files_without_writes(audit, tmp_path):
    state, value, _ = selected(audit)
    state["repository"] = str(tmp_path)
    paths = value["suite_files"] + value["trace_files"] + [value["specification"]]
    for name in paths + ["unrelated-authoring-evidence.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture")
    original = {name: (tmp_path / name).read_bytes() for name in paths}
    observed = audit.observe(tmp_path, state)
    assert set(observed["files"]) == set(paths)
    assert all(observed["files"].values())
    assert all((tmp_path / name).read_bytes() == data for name, data in original.items())
