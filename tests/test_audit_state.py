# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
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
        suite_files=["suites/refund/cases.json", "suites/refund/verifier.py"],
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
    "stage,partial",
    [
        (stage, False)
        for stage in ("suite_selection", "trace_selection", "checks_review", "findings_review", "next_action")
    ]
    + [("findings_review", True), ("next_action", True)],
)
def test_restart_resumes_each_saved_decision(audit, monkeypatch, stage, partial):
    state, value, observed = reviewed(audit)
    if partial:
        value["measurement"]["status"] = "partial"
        value["findings_review"]["digest"] = audit.derive(state, observed)["findings_digest"]
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
    if partial:
        assert not resumed["measurement_complete"] and not resumed["complete"]
        previous = copy.deepcopy(state)
        if stage == "findings_review":
            value["findings_review"] = {"digest": resumed["findings_digest"], "source": "User accepts partial findings"}
        else:
            value["next_action"] = {
                "kind": "improve",
                "findings_digest": resumed["findings_digest"],
                "source": "User requests improvements",
            }
        audit.transition(Path("/synthetic"), previous, state)
        answered = audit.derive(state, observed)
        assert answered["stage"] == "measurement" and answered["pending_checkpoint"] is None
        assert answered["findings_reviewed"] and answered["next_action_current"] == (stage == "next_action")
        assert not answered["measurement_complete"] and not answered["complete"]


@pytest.mark.parametrize(
    "stage,change",
    [
        ("findings_review", "evidence"),
        ("findings_review", "report"),
        ("next_action", "evidence"),
        ("next_action", "report"),
        ("next_action", "approval"),
    ],
)
def test_partial_checkpoint_requires_current_findings_and_applicable_decision(audit, monkeypatch, stage, change):
    state, value, observed = measured(audit)
    value["measurement"]["status"] = "partial"
    digest = audit.derive(state, observed)["findings_digest"]
    if stage == "next_action":
        value["findings_review"] = {"digest": digest, "source": "User accepts partial findings"}
    previous = copy.deepcopy(state)
    value["pending"] = {"stage": stage, "digest": digest, "question": "Continue?", "source": "Presented findings"}
    assert audit.derive(state, observed)["pending_checkpoint"] == value["pending"]
    if change == "approval":
        value["findings_review"] = None
    else:
        ref = value["findings"]["report"] if change == "report" else value["measurement"]["evidence"][0]
        observed["files"][ref["path"]] = audit.fingerprint("changed bytes")
        if change == "evidence":
            value["pending"]["digest"] = audit.derive(state, observed)["findings_digest"]
    result = audit.derive(state, observed)
    assert result["stage"] == "measurement" and result["pending_checkpoint"] is None
    assert not result["measurement_complete"] and not result["complete"]
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    with pytest.raises(ValueError, match="Pending audit checkpoint"):
        audit.transition(Path("/synthetic"), previous, state)


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
def test_partial_or_deferred_findings_acceptance_preserves_unfinished_measurement(audit, monkeypatch, status):
    state, value, observed = measured(audit)
    value["measurement"]["status"] = status
    value["measurement"]["source"] = "User defers measurement" if status == "deferred" else "One method failed"
    previous = copy.deepcopy(state)
    value["findings_review"] = {
        "digest": audit.derive(state, observed)["findings_digest"],
        "source": "User accepts limitations",
    }
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    audit.transition(Path("/synthetic"), previous, state)
    resumed = json.loads(json.dumps(state))
    assert audit.record(resumed)["findings_review"] == value["findings_review"]
    result = audit.derive(resumed, observed)
    assert result["findings_reviewed"]
    assert not result["measurement_complete"] and not result["complete"]
    assert result["stage"] == ("next_action" if status == "deferred" else "measurement")
    assert value["next_action"] is None and not result["next_action_current"]


@pytest.mark.parametrize("change", ["report", "measurement", "changed_evidence", "missing_evidence"])
def test_partial_findings_acceptance_requires_current_measurement_and_evidence(audit, monkeypatch, change):
    state, value, observed = measured(audit)
    value["measurement"]["status"] = "partial"
    value["findings_review"] = {
        "digest": audit.derive(state, observed)["findings_digest"],
        "source": "User accepts partial findings",
    }
    assert audit.derive(state, observed)["findings_reviewed"]
    previous = copy.deepcopy(state)
    if change == "report":
        report = value["findings"]["report"]
        report["digest"] = observed["files"][report["path"]] = audit.fingerprint("revised findings")
    elif change == "measurement":
        value["measurement"]["status"] = "complete"
    else:
        evidence = value["measurement"]["evidence"][0]
        observed["files"][evidence["path"]] = (
            None if change == "missing_evidence" else audit.fingerprint("changed evidence")
        )
    result = audit.derive(state, observed)
    assert result["checks_approved"] and not result["findings_reviewed"] and not result["complete"]
    if change in {"changed_evidence", "missing_evidence"}:
        assert result["findings_digest"] is not None
        value["findings_review"]["digest"] = result["findings_digest"]
        monkeypatch.setattr(audit, "observe", lambda *_: observed)
        with pytest.raises(ValueError, match="Review current findings"):
            audit.transition(Path("/synthetic"), previous, state)


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
    value["selection_manifest"] = "runs/selection.json"
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
    value["selection_manifest"] = ".eval-author/selection.json"
    paths = value["suite_files"] + value["trace_files"] + [value["specification"], value["selection_manifest"]]
    for name in paths + ["unrelated-authoring-evidence.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture")
    original = {name: (tmp_path / name).read_bytes() for name in paths}
    observed = audit.observe(tmp_path, state)
    assert set(observed["files"]) == set(paths)
    assert all(observed["files"].values())
    assert all((tmp_path / name).read_bytes() == data for name, data in original.items())


@pytest.mark.parametrize(
    "trace_mib,prepared_name", [(40, "a-prepared.json"), (40, "z-prepared.json"), (128, "a-prepared.json")]
)
def test_retained_prepared_trace_cannot_exhaust_approved_input_hashing(audit, tmp_path, trace_mib, prepared_name):
    state, value, _ = selected(audit)
    state["repository"] = str(tmp_path)
    for name in value["suite_files"] + [value["specification"]]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("selected fixture")
    trace = tmp_path / value["trace_files"][0]
    trace.parent.mkdir(parents=True)
    with trace.open("wb") as stream:
        stream.truncate(trace_mib * 1024 * 1024)
    observed = audit.observe(tmp_path, state)
    assert all(observed["files"].values())
    checks_digest = audit.derive(state, observed)["checks_digest"]
    value["checks_approval"] = {"digest": checks_digest, "source": "User approved the selected inputs"}
    assert audit.derive(state, observed)["checks_approved"]
    previous = copy.deepcopy(state)

    prepared = trace.with_name(prepared_name)
    with prepared.open("wb") as stream:
        stream.truncate(25 * 1024 * 1024)
    with prepared.open("rb") as stream:
        prepared_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    value["measurement"] = {
        "checks_digest": checks_digest,
        "status": "complete",
        "evidence": [{"path": prepared.relative_to(tmp_path).as_posix(), "digest": prepared_digest}],
        "source": "Measurement retained the prepared trace",
    }
    retained = audit.observe(tmp_path, state)
    assert {name: retained["files"][name] for name in observed["files"]} == observed["files"]
    audit.transition(tmp_path, previous, state)
    result = audit.derive(state, retained)
    assert result["checks_digest"] == checks_digest and result["checks_approved"] and result["measurement_complete"]


def test_legacy_lifecycle_without_selection_manifest_validates(audit):
    value = audit.new_record()
    value.pop("selection_manifest", None)
    audit.validate(value)
