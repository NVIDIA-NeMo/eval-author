# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
from pathlib import Path

import pytest
from journey_state_support import expansion_start, setup_ready

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
QUESTION = "Create this partial-refund task?"
PLAN = {
    "summary": "Add a partial-refund case",
    "tasks": [
        {
            "task": "Partial refund",
            "scenario": "A customer requests part of an eligible order's price",
            "outcome": "Refund the requested amount and retain the remaining balance",
            "verification": "Check the saved refund amount and order balance",
        }
    ],
    "breadth": "One task using the existing task-creation workflow",
    "limits": "At most two construction attempts; running the agent requires separate approval",
    "deferred": ["Repairing an unrelated existing verifier"],
}


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return tuple(
        importlib.import_module(name) for name in ("journey_state", "expansion_state", "conversation_renderer")
    )


def save_scope(modules, repo, saved, value):
    return modules[0].update(
        repo,
        {
            "scopes": {"expand": {"lifecycle": value}},
            "pending_question": value["pending"]["question"] if value["pending"] else None,
        },
        expected_revision=saved["revision"],
    )


def start(modules, repo, path="starter_suite"):
    state, expansion, _ = modules
    saved = (
        setup_ready(state, repo, journey="expand")
        if path == "recorded_interaction"
        else expansion_start(modules, repo, handoff=path == "audit_tool_gap")
    )
    value = expansion.new_scope(saved, path=path, plan=copy.deepcopy(PLAN), question=QUESTION, source="User request")
    return save_scope(modules, repo, saved, value)


@pytest.mark.parametrize(
    ("path", "existing_ethos"),
    [
        ("starter_suite", False),
        ("audit_tool_gap", False),
        ("recorded_interaction", False),
        ("recorded_interaction", True),
    ],
)
def test_scope_checkpoint_survives_resume_without_replacing_existing_context(modules, tmp_path, path, existing_ethos):
    state, expansion, _ = modules
    if existing_ethos:
        (tmp_path / "ETHOS.md").write_text("# Existing unconfirmed intent\n")
    saved = start(modules, tmp_path, path)
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["scopes"] == saved["scopes"]
    assert resumed["tasks"] == saved["tasks"]
    assert resumed["pending_question"] == QUESTION
    progress = expansion.derive(resumed, expansion.observe(tmp_path, resumed))
    assert progress["stage"] == "scope"
    assert progress["context_current"]
    assert not progress["complete"]
    assert expansion.record(resumed)["plan"] == PLAN
    output = modules[2].render({"stage": "resume", "state": resumed, "revision": resumed["revision"]})
    assert QUESTION in output
    if path == "recorded_interaction":
        assert resumed["ethos"]["confirmed_digest"] is None
        assert not resumed["scopes"]["expand"]["needs_review"]


@pytest.mark.parametrize("kind", ["approved", "declined", "revise"])
def test_saved_decision_resumes_at_its_checkpoint_without_completing_the_journey(modules, tmp_path, kind):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"kind": kind, "source": f"User reply: {kind}"}
    value["pending"] = (
        {"question": "Which refund scenario instead?", "source": "User requests revision"} if kind == "revise" else None
    )
    saved = save_scope(modules, tmp_path, saved, value)
    resumed = state.initialize(tmp_path, journey="expand")
    progress = expansion.derive(resumed, expansion.observe(tmp_path, resumed))
    assert (
        progress["stage"]
        == {"approved": "scope_approved", "declined": "scope_declined", "revise": "scope_revision"}[kind]
    )
    assert resumed["pending_question"] == (value["pending"]["question"] if value["pending"] else None)
    assert expansion.record(resumed) == expansion.record(saved)
    assert not progress["complete"]
    assert resumed["journey_status"].get("expand") != "complete"
    output = modules[2].render({"stage": "resume", "state": resumed, "revision": resumed["revision"]})
    if kind == "revise":
        assert value["pending"]["question"] in output
    else:
        assert QUESTION not in output
    if kind != "revise":
        question = "Which fixture account should the task use?"
        saved = state.update(tmp_path, {"pending_question": question}, expected_revision=resumed["revision"])
        assert state.initialize(tmp_path, journey="expand")["pending_question"] == question
        output = modules[2].render({"stage": "resume", "state": saved, "revision": saved["revision"]})
        assert question in output and QUESTION not in output


@pytest.mark.parametrize("kind", ["approved", "declined"])
@pytest.mark.parametrize("next_question", [None, "Which fixture account should the task use?"])
def test_answered_scope_question_must_be_cleared_or_replaced(modules, tmp_path, kind, next_question):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"kind": kind, "source": f"User reply: {kind}"}
    value["pending"] = None
    patch = {"scopes": {"expand": {"lifecycle": value}}}
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="answered scope question"):
        state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before
    patch["pending_question"] = next_question
    updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert updated["pending_question"] == next_question


@pytest.mark.parametrize("path", ["starter_suite", "recorded_interaction"])
@pytest.mark.parametrize("blocker_id", ["environment", "services", "data", "model_credentials"])
def test_scope_planning_defers_execution_blockers(modules, tmp_path, path, blocker_id):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path)
    blocked = state.update(
        tmp_path,
        {"blockers": [{"id": blocker_id, "phase": "expand", "message": "Needed before execution"}]},
        expected_revision=saved["revision"],
    )
    assert expansion.checkpoint_ready(blocked)
    value = copy.deepcopy(expansion.record(blocked))
    value["decision"] = {"kind": "approved", "source": "User approves planning only"}
    value["pending"] = None
    approved = save_scope(modules, tmp_path, blocked, value)
    assert approved["scopes"]["expand"]["confirmed"]
    assert approved["blockers"] == blocked["blockers"]


def test_plan_revision_requires_a_new_decision(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"kind": "approved", "source": "User approves the partial-refund plan"}
    value["pending"] = None
    saved = save_scope(modules, tmp_path, saved, value)
    value["plan"]["tasks"][0]["scenario"] = "A customer requests a full refund"
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        save_scope(modules, tmp_path, saved, value)
    assert state.state_path(tmp_path).read_bytes() == before
    value["decision"] = None
    value["pending"] = {"question": "Create the full-refund task instead?", "source": "User changes the scenario"}
    revised = save_scope(modules, tmp_path, saved, value)
    assert state.initialize(tmp_path, journey="expand")["pending_question"] == value["pending"]["question"]
    assert expansion.record(revised)["decision"] is None


@pytest.mark.parametrize("change", ["provider", "ethos", "handoff"])
def test_changed_context_does_not_reuse_the_saved_scope_decision(modules, tmp_path, change):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"kind": "approved", "source": "User approves the partial-refund plan"}
    value["pending"] = None
    saved = save_scope(modules, tmp_path, saved, value)
    if change == "provider":
        state.initialize(tmp_path, setup="harbor")
    elif change == "ethos":
        (tmp_path / "ETHOS.md").write_text("# Revised agent intent\n")
    else:
        saved["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = ["R2"]
        state.save(tmp_path, saved)
    current = state.load_state(tmp_path)
    progress = expansion.derive(current, expansion.observe(tmp_path, current))
    assert expansion.record(current) == value
    assert not progress["context_current"]
    assert progress["stage"] == "scope"
    if change == "provider":
        restored = setup_ready(state, tmp_path, journey="expand")
        progress = expansion.derive(restored, expansion.observe(tmp_path, restored))
        assert progress["context_current"]
        assert progress["stage"] == "scope"
        assert expansion.record(restored) == value


def test_incomplete_plan_is_saveable_but_cannot_be_approved(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["tasks"] = []
    value["pending"] = {"question": "Which recorded interaction should the task use?", "source": "Missing input"}
    saved = save_scope(modules, tmp_path, saved, value)
    saved = state.initialize(tmp_path, journey="expand")
    assert saved["pending_question"] == value["pending"]["question"]
    value["decision"] = {"kind": "approved", "source": "User says proceed"}
    value["pending"] = None
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        save_scope(modules, tmp_path, saved, value)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("path", ["starter_suite", "recorded_interaction"])
@pytest.mark.parametrize("prerequisite", ["runtime", "code_access", "file_writes", "command_execution", "skills"])
def test_scope_question_returns_after_shared_prerequisite_recovery(modules, tmp_path, path, prerequisite):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path)
    original = copy.deepcopy(expansion.record(saved))
    recovery_question = "Restore the missing evaluation runtime."
    blocked = state.update(
        tmp_path,
        {
            "prerequisites": {prerequisite: {"status": "missing", "source": "Prerequisite probe failed"}},
            "pending_question": recovery_question,
        },
        expected_revision=saved["revision"],
    )
    blocked = state.initialize(tmp_path, journey="expand")
    assert blocked["pending_question"] == recovery_question
    repaired = state.update(
        tmp_path,
        {
            "prerequisites": {prerequisite: {"status": "verified", "source": "Prerequisite probe passed"}},
            "pending_question": None,
        },
        expected_revision=blocked["revision"],
    )
    assert repaired["pending_question"] == QUESTION
    assert expansion.record(repaired) == original


@pytest.mark.parametrize("approved", [False, True])
def test_scope_cannot_establish_journey_completion(modules, tmp_path, approved):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    if approved:
        value = copy.deepcopy(expansion.record(saved))
        value["decision"] = {"kind": "approved", "source": "User approves"}
        value["pending"] = None
        saved = save_scope(modules, tmp_path, saved, value)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="cannot establish completion"):
        state.update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("path", ["starter_suite", "recorded_interaction"])
@pytest.mark.parametrize("change", ["blocked", "stale"])
def test_scope_changes_require_ready_current_context(modules, tmp_path, path, change):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path)
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"kind": "approved", "source": "User approves"}
    value["pending"] = None
    patch = {"scopes": {"expand": {"lifecycle": value}}, "pending_question": None}
    if change == "blocked":
        patch["prerequisites"] = {"runtime": {"status": "missing", "source": "Runtime probe failed"}}
    else:
        value["handoff_digest"] = "a" * 64
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="shared prerequisites|current provider"):
        state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


def test_stale_scope_allows_a_context_review_question(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    value = copy.deepcopy(expansion.record(saved))
    saved["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = ["R2"]
    state.save(tmp_path, saved)
    question = "Review the changed audit selection?"
    updated = state.update(tmp_path, {"pending_question": question}, expected_revision=saved["revision"])
    assert updated["pending_question"] == question
    assert expansion.record(updated) == value
    assert not expansion.derive(updated, expansion.observe(tmp_path, updated))["context_current"]


def test_scope_update_requires_current_revision_without_writes(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    for revision in (None, saved["revision"] - 1):
        with pytest.raises(ValueError):
            state.update(
                tmp_path,
                {"scopes": {"expand": {"lifecycle": expansion.record(saved)}}},
                expected_revision=revision,
            )
        assert state.state_path(tmp_path).read_bytes() == before
