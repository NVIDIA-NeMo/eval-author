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
        "scopes": {"audit": {"mode": {"kind": "measured", "source": "User chose measured audit"}, "lifecycle": value}},
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
    """Recorded current findings are reviewed without any user decision."""
    return measured(audit)


@pytest.mark.parametrize("path", ["C:/runs/a.json", "/etc/passwd", ".", "../runs/a.json", "runs/./a.json"])
def test_audit_paths_must_be_canonical_and_repository_relative(audit, path):
    _, value, _ = selected(audit)
    value["traces"]["paths"] = value["trace_files"] = [path]
    with pytest.raises(ValueError, match="canonical"):
        audit.validate(value)


def test_timestamped_trace_names_can_be_hashed_and_approved(audit, tmp_path):
    state, value, _ = selected(audit)
    state["repository"] = str(tmp_path)
    value["trace_files"] = ["runs/2026-10-28T15:31:27.json"]
    paths = value["suite_files"] + value["trace_files"] + [value["specification"]]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("selected evidence")
    previous = copy.deepcopy(state)
    current = audit.progress(tmp_path, state)
    assert current["checks_digest"] is not None
    value["checks_approval"] = {"digest": current["checks_digest"], "source": "User approved timestamped traces"}
    audit.transition(tmp_path, previous, state)
    assert audit.progress(tmp_path, state)["checks_approved"]


@pytest.mark.parametrize("key,field", [("suites", "suite_files"), ("traces", "trace_files")])
def test_selected_files_can_match_exact_file_bounds(audit, key, field):
    _, value, _ = selected(audit)
    value[key]["paths"] = value[field].copy()
    audit.validate(value)


@pytest.mark.parametrize(
    "stage,partial",
    [(stage, False) for stage in ("suite_selection", "trace_selection", "checks_review", "next_action")]
    + [("next_action", True)],
)
def test_restart_resumes_each_saved_decision(audit, monkeypatch, stage, partial):
    state, value, observed = reviewed(audit)
    if partial:
        value["measurement"]["status"] = "partial"
    if stage == "suite_selection":
        value["suites"] = audit.new_record()["suites"]
        value["suite_files"] = []
    elif stage == "trace_selection":
        value["traces"] = audit.new_record()["traces"]
        value["trace_files"] = []
    elif stage == "checks_review":
        value["checks_approval"] = None
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
        value["next_action"] = {
            "kind": "improve",
            "findings_digest": resumed["findings_digest"],
            "source": "User requests improvements",
        }
        audit.transition(Path("/synthetic"), previous, state)
        answered = audit.derive(state, observed)
        assert answered["stage"] == "measurement" and answered["pending_checkpoint"] is None
        assert answered["findings_reviewed"] and answered["next_action_current"]
        assert not answered["measurement_complete"] and not answered["complete"]


@pytest.mark.parametrize(
    "stage,change",
    [
        ("next_action", "evidence"),
        ("next_action", "report"),
    ],
)
def test_partial_checkpoint_requires_current_findings_and_applicable_decision(audit, monkeypatch, stage, change):
    state, value, observed = measured(audit)
    value["measurement"]["status"] = "partial"
    digest = audit.derive(state, observed)["findings_digest"]
    previous = copy.deepcopy(state)
    value["pending"] = {"stage": stage, "digest": digest, "question": "Continue?", "source": "Presented findings"}
    assert audit.derive(state, observed)["pending_checkpoint"] == value["pending"]
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


def test_recorded_findings_are_reviewed_without_a_user_decision(audit):
    state, value, observed = measured(audit)
    assert value["findings_review"] is None
    result = audit.derive(state, observed)
    assert result["findings_reviewed"] and result["complete"] and result["stage"] == "next_action"
    assert result["pending_checkpoint"] is None and not result["next_action_current"]


def test_legacy_findings_review_record_is_still_accepted(audit, monkeypatch):
    previous, _, observed = measured(audit)
    updated = copy.deepcopy(previous)
    value = audit.record(updated)
    value["findings_review"] = {
        "digest": audit.derive(updated, observed)["findings_digest"],
        "source": "User accepts findings only",
    }
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    audit.transition(Path("/synthetic"), previous, updated)
    assert audit.derive(updated, observed)["findings_reviewed"]


def test_report_bytes_that_differ_from_the_recorded_binding_reopen_findings(audit):
    state, value, observed = reviewed(audit)
    observed["files"][value["findings"]["report"]["path"]] = audit.fingerprint("edited after recording")
    result = audit.derive(state, observed)
    assert result["checks_approved"] and result["measurement_complete"]
    assert not result["findings_reviewed"] and result["findings_digest"] is None
    assert result["stage"] == "findings_review"


def test_rebinding_a_revised_report_keeps_findings_reviewed_but_reopens_the_next_action(audit):
    state, value, observed = reviewed(audit)
    value["next_action"] = {
        "kind": "improve",
        "findings_digest": audit.derive(state, observed)["findings_digest"],
        "source": "User requests improvements",
    }
    assert audit.derive(state, observed)["next_action_current"]
    report = value["findings"]["report"]
    report["digest"] = observed["files"][report["path"]] = audit.fingerprint("revised findings")
    result = audit.derive(state, observed)
    assert result["findings_reviewed"] and not result["next_action_current"]


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


@pytest.mark.parametrize("change", ["report", "changed_evidence", "missing_evidence"])
def test_partial_findings_require_current_measurement_and_evidence(audit, monkeypatch, change):
    state, value, observed = measured(audit)
    value["measurement"]["status"] = "partial"
    assert audit.derive(state, observed)["findings_reviewed"]
    previous = copy.deepcopy(state)
    if change == "report":
        observed["files"][value["findings"]["report"]["path"]] = audit.fingerprint("edited after recording")
    else:
        evidence = value["measurement"]["evidence"][0]
        observed["files"][evidence["path"]] = (
            None if change == "missing_evidence" else audit.fingerprint("changed evidence")
        )
    result = audit.derive(state, observed)
    assert result["checks_approved"] and not result["findings_reviewed"] and not result["complete"]
    if change in {"changed_evidence", "missing_evidence"}:
        assert result["findings_digest"] is not None
        value["findings_review"] = {"digest": result["findings_digest"], "source": "Legacy findings acceptance"}
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


@pytest.mark.parametrize("change", ["checks_approval", "next_action", "pending"])
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


@pytest.mark.parametrize("changed", ["content", "source"])
def test_structured_findings_bind_content_and_sources_without_reopening_measurement(audit, changed):
    state, value, observed = measured(audit)
    content = {"path": ".eval-author/.working/audit/report-content.json", "digest": audit.fingerprint("content")}
    source = {"path": "selected/record.json", "digest": audit.fingerprint("source"), "kind": "file"}
    value["findings"].update(content=content, inputs=[source])
    observed["files"].update({content["path"]: content["digest"], source["path"]: source["digest"]})
    current = audit.derive(state, observed)
    value["findings_review"] = {"digest": current["findings_digest"], "source": "User accepted the generated report"}
    assert audit.derive(state, observed)["findings_reviewed"]
    observed["files"][content["path"] if changed == "content" else source["path"]] = audit.fingerprint("changed")
    current = audit.derive(state, observed)
    assert current["measurement_complete"]
    assert not current["findings_reviewed"]
    assert current["findings_digest"] is None


def test_structured_task_inputs_reopen_scope_when_the_task_tree_changes(audit, tmp_path):
    state, value, _ = measured(audit)
    state["repository"] = str(tmp_path)
    for name in (
        value["suite_files"]
        + value["trace_files"]
        + [value["specification"], value["findings"]["report"]["path"], value["measurement"]["evidence"][0]["path"]]
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Selected source bytes.\n")
    content_path = tmp_path / ".eval-author/.working/audit/report-content.json"
    content_path.parent.mkdir(parents=True)
    content_path.write_text("{}\n")
    from eval_author_shared.task_evidence import task_digest

    value["checks_approval"]["digest"] = audit.derive(state, audit.observe(tmp_path, state))["checks_digest"]
    value["measurement"]["checks_digest"] = value["checks_approval"]["digest"]
    for ref in [value["findings"]["report"], *value["measurement"]["evidence"]]:
        ref["digest"] = hashlib.sha256((tmp_path / ref["path"]).read_bytes()).hexdigest()
    approved_digest = audit.derive(state, audit.observe(tmp_path, state))["checks_digest"]
    # Record the findings after approval and measurement, without recomputing either.
    value["findings"].update(
        content={
            "path": content_path.relative_to(tmp_path).as_posix(),
            "digest": hashlib.sha256(content_path.read_bytes()).hexdigest(),
        },
        inputs=[{"path": "suites/refund", "digest": task_digest(tmp_path / "suites/refund"), "kind": "task"}],
    )
    current = audit.derive(state, audit.observe(tmp_path, state))
    assert current["checks_digest"] == approved_digest
    assert current["checks_approved"] and current["measurement_complete"]
    assert current["findings_digest"] and not current["changed_task_bindings"]
    value["findings_review"] = {"digest": current["findings_digest"], "source": "User accepted current report"}
    assert audit.derive(state, audit.observe(tmp_path, state))["findings_reviewed"]
    (tmp_path / "suites/refund/extra.txt").write_text("Task changed.\n")
    current = audit.derive(state, audit.observe(tmp_path, state))
    assert not current["checks_approved"]
    assert not current["measurement_complete"]
    assert not current["findings_reviewed"]
    assert current["changed_task_bindings"] == ["suites/refund"]


def test_recording_task_bound_findings_keeps_approval_and_measurement(audit):
    state, value, observed = measured(audit)
    before = audit.derive(state, observed)
    task = {"path": "suites/refund", "digest": audit.fingerprint("task tree"), "kind": "task"}
    content = {"path": ".eval-author/.working/audit/report-content.json", "digest": audit.fingerprint("content")}
    value["findings"].update(content=content, inputs=[task])
    observed["files"].update({task["path"]: task["digest"], content["path"]: content["digest"]})
    after = audit.derive(state, observed)
    assert after["checks_digest"] == before["checks_digest"]
    assert after["checks_approved"] and after["measurement_complete"]
    assert after["findings_digest"] and after["changed_task_bindings"] == []


def test_a_changed_task_binding_revokes_approval_and_blocks_progress(audit):
    state, value, observed = measured(audit)
    task = {"path": "suites/refund", "digest": audit.fingerprint("task tree"), "kind": "task"}
    content = {"path": ".eval-author/.working/audit/report-content.json", "digest": audit.fingerprint("content")}
    value["findings"].update(content=content, inputs=[task])
    observed["files"].update({task["path"]: audit.fingerprint("edited"), content["path"]: content["digest"]})
    current = audit.derive(state, observed)
    assert current["changed_task_bindings"] == ["suites/refund"]
    assert not current["checks_approved"] and not current["measurement_complete"]


def test_transition_names_the_changed_tasks_before_recording_more_progress(audit, monkeypatch):
    state, value, observed = measured(audit)
    task = {"path": "suites/refund", "digest": audit.fingerprint("task tree"), "kind": "task"}
    content = {"path": ".eval-author/.working/audit/report-content.json", "digest": audit.fingerprint("content")}
    value["findings"].update(content=content, inputs=[task])
    observed["files"].update({task["path"]: audit.fingerprint("edited"), content["path"]: content["digest"]})
    previous = copy.deepcopy(state)
    value["findings_review"] = {"digest": audit.fingerprint("review"), "source": "User accepted findings"}
    monkeypatch.setattr(audit, "observe", lambda *_: observed)
    with pytest.raises(ValueError, match="Tasks recorded in the findings changed after approval: suites/refund"):
        audit.transition(Path("/synthetic"), previous, state)


def task_review(audit):
    state, value, observed = selected(audit)
    state["scopes"]["audit"]["mode"] = {"kind": "task_review", "source": "User selected task review"}
    value["traces"] = audit.new_record()["traces"]
    value["trace_files"] = []
    return state, value, observed


def test_task_review_finishes_without_traces_or_deferred_measurement(audit):
    state, value, observed = task_review(audit)
    assert audit.derive(state, observed)["stage"] == "checks_review"
    value["checks_approval"] = {
        "digest": audit.derive(state, observed)["checks_digest"],
        "source": "User approved task-review checks",
    }
    report = {"path": ".eval-author/audit-coverage-report.md", "digest": audit.fingerprint("Task findings")}
    value["findings"] = {"report": report, "source": "Presented inspected task findings"}
    observed["files"][report["path"]] = report["digest"]
    progress = audit.derive(state, observed)
    assert progress["stage"] == "next_action" and progress["complete"] and progress["findings_reviewed"]
    resumed = audit.derive(json.loads(json.dumps(state)), observed)
    assert resumed["complete"] and resumed["findings_reviewed"]
    assert not resumed["measurement_complete"] and value["measurement"] is None
    assert resumed["stage"] == "next_action" and value["next_action"] is None


@pytest.mark.parametrize("change", ["suite", "specification", "ethos", "mode"])
def test_task_review_changes_reopen_check_approval(audit, change):
    state, value, observed = task_review(audit)
    value["checks_approval"] = {"digest": audit.derive(state, observed)["checks_digest"], "source": "User approval"}
    if change == "mode":
        state["scopes"]["audit"]["mode"]["kind"] = "measured"
    elif change == "ethos":
        state["ethos"]["confirmed_digest"] = audit.fingerprint("New intent")
    else:
        path = value["suite_files"][0] if change == "suite" else value["specification"]
        observed["files"][path] = audit.fingerprint("Edited input")
    progress = audit.derive(state, observed)
    assert not progress["checks_approved"] and not progress["complete"]


def test_task_review_never_reads_historical_run_inputs(audit, monkeypatch, tmp_path):
    state, value, _ = measured(audit)
    state["scopes"]["audit"]["mode"] = {"kind": "task_review", "source": "User changed approach"}
    value["selection_manifest"] = ".eval-author/selection.json"
    forbidden_paths = {value["selection_manifest"], *value["trace_files"]}
    forbidden_paths.update(ref["path"] for ref in value["measurement"]["evidence"])
    reads = []

    def read(path, _remaining):
        name = path.relative_to(tmp_path).as_posix()
        assert name not in forbidden_paths
        reads.append(name)
        return audit.fingerprint(name), 10

    monkeypatch.setattr(importlib.import_module("journey_state"), "_file_digest", read)
    observed = audit.observe(tmp_path, state)
    assert set(reads) == {*value["suite_files"], value["specification"], value["findings"]["report"]["path"]}
    assert not audit.derive(state, observed)["measurement_complete"]


def test_task_review_ignores_historical_run_bindings_but_keeps_reused_evidence_fresh(audit, monkeypatch, tmp_path):
    state, value, _ = measured(audit)
    state["scopes"]["audit"]["mode"] = {"kind": "task_review", "source": "User changed approach"}
    content = {"path": ".eval-author/.working/audit/report-content.json", "digest": audit.fingerprint("content")}
    native = value["measurement"]["evidence"][0]
    inputs = [
        {"path": path, "digest": audit.fingerprint(path), "kind": "file"}
        for path in (*value["trace_files"], native["path"])
    ]
    value["findings"].update(content=content, inputs=inputs)
    report = value["findings"]["report"]
    digests = {path: audit.fingerprint(path) for path in (*value["suite_files"], value["specification"])}
    digests.update({report["path"]: report["digest"], content["path"]: content["digest"]})
    digests[native["path"]] = inputs[1]["digest"]
    reads = []

    def read(path, _remaining):
        name = path.relative_to(tmp_path).as_posix()
        assert name not in value["trace_files"]
        reads.append(name)
        return digests[name], 10

    monkeypatch.setattr(importlib.import_module("journey_state"), "_file_digest", read)
    observed = audit.observe(tmp_path, state)
    assert native["path"] in reads and not set(value["trace_files"]) & set(observed["files"])
    value["checks_approval"] = {"digest": audit.derive(state, observed)["checks_digest"], "source": "User approval"}
    assert audit.derive(state, observed)["findings_digest"]
    observed["files"][native["path"]] = audit.fingerprint("edited native result")
    assert audit.derive(state, observed)["findings_digest"] is None


def test_task_review_keeps_controls_bound_under_a_retained_trace_directory(audit, monkeypatch, tmp_path):
    state, value, _ = measured(audit)
    state["scopes"]["audit"]["mode"] = {"kind": "task_review", "source": "User changed approach"}
    value["traces"]["paths"] = ["runs"]
    content = {"path": ".eval-author/.working/audit/report-content.json", "digest": audit.fingerprint("content")}
    control = {"path": "runs/verifier-controls/result.json", "digest": audit.fingerprint("control"), "kind": "file"}
    value["findings"].update(content=content, inputs=[control])
    report = value["findings"]["report"]
    digests = {path: audit.fingerprint(path) for path in (*value["suite_files"], value["specification"])}
    digests.update({report["path"]: report["digest"], content["path"]: content["digest"]})
    digests[control["path"]] = control["digest"]
    monkeypatch.setattr(
        importlib.import_module("journey_state"),
        "_file_digest",
        lambda path, _remaining: (digests[path.relative_to(tmp_path).as_posix()], 10),
    )
    observed = audit.observe(tmp_path, state)
    assert control["path"] in observed["files"]
    value["checks_approval"] = {"digest": audit.derive(state, observed)["checks_digest"], "source": "User approval"}
    assert audit.derive(state, observed)["findings_digest"]
    observed["files"][control["path"]] = audit.fingerprint("edited control")
    assert audit.derive(state, observed)["findings_digest"] is None
    del observed["files"][control["path"]]
    assert audit.derive(state, observed)["findings_digest"] is None


def test_saved_audit_without_mode_defaults_to_unmeasured_task_review(audit):
    state, _, observed = reviewed(audit)
    del state["scopes"]["audit"]["mode"]
    before = copy.deepcopy(state)
    observed["files"] = {path: digest for path, digest in observed["files"].items() if not path.startswith("runs/")}
    progress = audit.derive(state, observed)
    assert progress["mode"] == "task_review"
    assert not progress["measurement_complete"] and not progress["checks_approved"]
    assert not progress["complete"]
    assert audit.traces_unused(state)
    assert state == before
