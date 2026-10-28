# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
from pathlib import Path

import pytest
from test_audit_state import reviewed

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return (
        importlib.import_module("journey_state"),
        importlib.import_module("audit_state"),
        importlib.import_module("conversation_renderer"),
    )


def presentation(modules, *, complete=False, stage="resume"):
    state_helper, audit, _ = modules
    state = state_helper.new_state(Path("/synthetic/audit-repository"))
    state.update(agent="support", journey="audit", revision=4)
    state["prerequisites"] = {
        key: {"status": "verified", "source": "Observed audit prerequisite"}
        for key in state_helper.COMMON_PREREQUISITES | {"data"}
    }
    state["ethos"].update(
        stage="complete",
        confirmed_digest=audit.fingerprint("intent"),
        confirmation_source="User confirmed saved intent",
        validation="Ethos validation passed",
    )
    if complete:
        domain, lifecycle, observed = reviewed(audit)
        state["scopes"] = domain["scopes"]
        state["journey_status"] = {**domain["journey_status"], "audit": "complete"}
    else:
        lifecycle = audit.new_record()
        lifecycle["suites"]["candidates"] = ["suites/refunds"]
        state["scopes"]["audit"] = {"lifecycle": lifecycle}
        state["journey_status"]["audit"] = "in_progress"
        observed = {"files": {}}
        lifecycle["pending"] = {
            "stage": "suite_selection",
            "digest": audit.derive(state, observed)["checkpoint_digests"]["suite_selection"],
            "question": "Should this audit include the refunds evaluation suite?",
            "source": "Presented the bounded evaluation inventory",
        }
    state_helper.validate(state)
    return {"stage": stage, "state": state, "revision": state["revision"], "facts": {"audit_observations": observed}}


def forbid_io(*_args, **_kwargs):
    raise AssertionError("Rendering must derive only from supplied state and observations")


@pytest.mark.parametrize("context", ["ethos_review", "legacy_audit"])
def test_resume_forwards_view_observations_without_audit_lifecycle(modules, monkeypatch, context):
    state_helper, audit, renderer = modules
    payload = presentation(modules)
    state = payload["state"]
    if context == "ethos_review":
        state["scopes"] = {}
        state["journey_status"] = {}
        state["ethos"]["stage"] = "review"
        question = "Does this describe the intended support behavior?"
        payload["facts"]["ethos"] = {
            "status": "existing",
            "link": state["repository"] + "/" + state["ethos"]["path"],
            "summary": "The support agent answers refund questions.",
        }
    else:
        state["scopes"]["audit"] = {"confirmed": True, "traces": ["runs/old.json"]}
        state["journey_status"]["audit"] = "complete"
        question = "Which evaluation suites should this audit include?"
    state["pending_question"] = question
    saved = copy.deepcopy(state)
    viewed = state_helper.view(state)
    assert viewed["audit_observations"] == {"files": {}}
    assert viewed["audit"] is None
    assert not viewed["checklist"][2]["complete"]
    assert state == saved
    payload["state"] = viewed["state"]
    payload["facts"]["audit_observations"] = viewed["audit_observations"]
    original = copy.deepcopy(payload)
    monkeypatch.setattr(Path, "open", forbid_io)
    monkeypatch.setattr(Path, "read_bytes", forbid_io)
    monkeypatch.setattr(Path, "read_text", forbid_io)
    monkeypatch.setattr(state_helper, "now", forbid_io)
    monkeypatch.setattr(audit, "observe", forbid_io)
    output = renderer.render(payload)
    assert question in output
    assert f"- [ ] **{state_helper.JOURNEYS['audit']}**" in output
    assert output.count("- [x]") == (1 if context == "ethos_review" else 2)
    assert payload == original


@pytest.mark.parametrize("observed", [{"files": {"runs/old.json": "a" * 64}}, {"files": []}])
def test_observations_without_audit_lifecycle_must_be_empty(modules, observed):
    _, _, renderer = modules
    payload = presentation(modules)
    payload["state"]["scopes"] = {}
    payload["facts"]["audit_observations"] = observed
    with pytest.raises(ValueError, match="require a saved audit lifecycle"):
        renderer.render(payload)


@pytest.mark.parametrize("stage", ["resume", "results"])
def test_audit_resume_recovers_domain_pending_after_switch_without_io(modules, monkeypatch, stage):
    state_helper, audit, renderer = modules
    payload = presentation(modules, stage=stage)
    pending = audit.record(payload["state"])["pending"]
    assert payload["state"]["pending_question"] is None
    original = copy.deepcopy(payload)
    monkeypatch.setattr(Path, "open", forbid_io)
    monkeypatch.setattr(Path, "read_bytes", forbid_io)
    monkeypatch.setattr(Path, "read_text", forbid_io)
    monkeypatch.setattr(state_helper, "now", forbid_io)
    monkeypatch.setattr(audit, "observe", forbid_io)
    output = renderer.render(payload)
    assert output.rstrip().endswith(pending["question"])
    assert output.count("- [x]") == 2 and output.count("- [ ]") == 1
    assert "We're here" in output
    assert payload == original


@pytest.mark.parametrize("stage", ["resume", "results"])
def test_current_observations_allow_completed_audit_checklist_without_io(modules, monkeypatch, stage):
    state_helper, audit, renderer = modules
    payload = presentation(modules, complete=True, stage=stage)
    original = copy.deepcopy(payload)
    monkeypatch.setattr(Path, "open", forbid_io)
    monkeypatch.setattr(state_helper, "now", forbid_io)
    monkeypatch.setattr(audit, "observe", forbid_io)
    output = renderer.render(payload)
    assert output.count("- [x]") == 3
    assert "- [ ]" not in output and "We're here" not in output
    assert payload == original


@pytest.mark.parametrize("observation", ["missing", "stale"])
def test_completed_audit_without_current_observations_stays_unchecked(modules, observation):
    _, audit, renderer = modules
    payload = presentation(modules, complete=True)
    if observation == "missing":
        payload["facts"].pop("audit_observations")
    else:
        evidence = audit.record(payload["state"])["measurement"]["evidence"][0]
        payload["facts"]["audit_observations"]["files"][evidence["path"]] = None
    output = renderer.render(payload)
    assert output.count("- [x]") == 2 and output.count("- [ ]") == 1
    assert "We're here" in output


def test_stale_audit_checkpoint_cannot_reappear_through_global_question_or_fact(modules):
    _, audit, renderer = modules
    payload = presentation(modules)
    lifecycle = audit.record(payload["state"])
    question = lifecycle["pending"]["question"]
    payload["state"]["pending_question"] = question
    lifecycle["suites"]["candidates"].append("suites/policy")
    original = copy.deepcopy(payload)
    assert question not in renderer.render(payload)
    assert payload == original
    payload["facts"]["question"] = question
    with pytest.raises(ValueError, match="checkpoint is stale"):
        renderer.render(payload)


@pytest.mark.parametrize("phase", ["setup", "ethos"])
def test_audit_pending_does_not_replace_shared_blocker_question(modules, phase):
    state_helper, audit, renderer = modules
    payload = presentation(modules)
    shared_question = "Which source resolves the prerequisite uncertainty?"
    payload["state"]["pending_question"] = shared_question
    audit.record(payload["state"])["pending"]["digest"] = "0" * 64
    payload["facts"]["question"] = shared_question
    payload["state"]["blockers"] = [{"id": "shared-review", "phase": phase, "message": "Review source"}]
    payload["facts"]["blockers"] = [
        {"id": "shared-review", "step": "Shared review", "reason": "Source remains unresolved", "needed": "User answer"}
    ]
    output = renderer.render(payload)
    assert shared_question in output
    assert audit.record(payload["state"])["pending"]["question"] not in output
    derived = audit.derive(payload["state"], payload["facts"]["audit_observations"])
    assert state_helper.audit_presentation_state(payload["state"], derived) is payload["state"]


@pytest.mark.parametrize(
    "journey,stage",
    [("create", "resume"), ("expand", "results"), ("audit", "prerequisite_results"), ("audit", "ethos")],
)
def test_audit_observations_are_rejected_outside_normal_audit_continuation(modules, journey, stage):
    _, _, renderer = modules
    payload = presentation(modules, stage=stage)
    payload["state"]["journey"] = journey
    with pytest.raises(ValueError, match="facts must be an object"):
        renderer.render(payload)


@pytest.mark.parametrize("override", ["derived_fact", "observation_flag"])
def test_renderer_rejects_derived_audit_completion_override(modules, override):
    _, _, renderer = modules
    payload = presentation(modules)
    if override == "derived_fact":
        payload["facts"]["audit"] = {"complete": True}
    else:
        payload["facts"]["audit_observations"]["complete"] = True
    with pytest.raises(ValueError):
        renderer.render(payload)


@pytest.mark.parametrize("stale", [False, True])
def test_view_projects_audit_question_without_mutating_saved_state_or_state_only_output(modules, monkeypatch, stale):
    state_helper, audit, _ = modules
    payload = presentation(modules)
    state = payload["state"]
    lifecycle = audit.record(state)
    question = lifecycle["pending"]["question"]
    if stale:
        state["pending_question"] = question
        lifecycle["suites"]["candidates"].append("suites/policy")
    original = copy.deepcopy(state)
    monkeypatch.setattr(audit, "observe", lambda *_: payload["facts"]["audit_observations"])
    output = state_helper.view(state)
    assert output["state"]["pending_question"] == (None if stale else question)
    assert state == original
    deferred = state_helper.view(state, state_only=True)
    assert deferred["state"] == original
    assert deferred["audit"] is None and deferred["audit_observations"] is None
