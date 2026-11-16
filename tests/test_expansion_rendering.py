# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import importlib
from pathlib import Path

import pytest
from journey_state_support import expansion_scope_approve as approve
from journey_state_support import expansion_scope_decide as decide
from journey_state_support import expansion_scope_payload as payload
from journey_state_support import expansion_scope_present as present
from journey_state_support import expansion_scope_progress as progress
from journey_state_support import expansion_scope_replace as replace_scope
from journey_state_support import expansion_scope_start as start

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return tuple(
        importlib.import_module(name) for name in ("journey_state", "expansion_state", "conversation_renderer")
    )


def test_shared_recovery_question_takes_priority_over_retained_scope(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path)
    original = copy.deepcopy(expansion.record(saved))
    question = "Which account recovery actions require approval?"
    paused = state.update(
        tmp_path,
        {"ethos": {"stage": "review", "remaining_questions": [question]}, "pending_question": question},
        expected_revision=saved["revision"],
    )
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["pending_question"] == question
    assert expansion.record(resumed) == original
    text = renderer.render(payload(modules, resumed))
    assert text.endswith(question + "\n")
    assert not progress(modules, tmp_path, paused)["construction_ready"]


@pytest.mark.parametrize("claim", [{"scope_approval": True}, {"execution_approval": True}, {"complete": True}])
def test_display_facts_cannot_grant_scope_or_execution_permission(modules, tmp_path, claim):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path)
    data = payload(modules, saved)
    data["facts"].update(claim)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        renderer.render(data)
    assert state.state_path(tmp_path).read_bytes() == before
    assert expansion.record(state.load_state(tmp_path))["decision"] is None


def test_scope_renderer_uses_supplied_observations_without_io_or_mutation(modules, tmp_path, monkeypatch):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path)
    data = payload(modules, saved)
    expected = renderer.render(data)
    original = copy.deepcopy(data)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering must not inspect or change repository files")

    for name in ("read_bytes", "read_text", "write_bytes", "write_text"):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(expansion, "observe", forbidden)
    monkeypatch.setattr(state, "load_state", forbidden)
    monkeypatch.setattr(state, "save", forbidden)
    assert renderer.render(data) == renderer.render(data) == expected
    assert data == original


@pytest.mark.parametrize(
    "variant", ["pending", "approved", "declined", "revision", "missing-evidence", "trace-only", "empty-plan"]
)
def test_scope_conversation_matches_its_fixture(modules, tmp_path, variant):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, path="recorded_interaction" if variant == "trace-only" else "audit_tool_gap")
    if variant in {"approved", "missing-evidence"}:
        saved = approve(modules, tmp_path, saved)
    elif variant in {"declined", "revision"}:
        saved = decide(
            modules, tmp_path, present(modules, tmp_path, saved), "revise" if variant == "revision" else variant
        )
    if variant == "missing-evidence":
        (tmp_path / expansion.record(saved)["plan"]["coverage_report"]).unlink()
        saved = state.load_state(tmp_path)
    if variant == "empty-plan":
        value = copy.deepcopy(expansion.record(saved))
        question = "Which supported task should we construct?"
        value["plan"].update(tasks=[], selected_task_ids=[], unresolved_inputs=[question])
        value["pending"] = {"question": question, "source": "User has not selected a construction target"}
        saved = replace_scope(modules, tmp_path, saved, value)
    expected = (
        (Path(__file__).parent / "fixtures/expansion-conversations" / f"scope-{variant}.md")
        .read_text()
        .split("\n\n", 1)[1]
    )
    rendered = renderer.render(payload(modules, saved))
    assert rendered == expected
    if variant == "empty-plan":
        assert "No tasks are proposed for construction yet." in rendered
        assert "No supported tasks" not in rendered
        assert rendered.endswith("Which supported task should we construct?\n")
        assert not progress(modules, tmp_path, saved)["construction_ready"]


@pytest.mark.parametrize("kind", ["approved", "declined"])
def test_answered_scope_can_resume_shared_recovery_without_a_scope_question(modules, tmp_path, kind):
    state, expansion, renderer = modules
    saved = decide(modules, tmp_path, present(modules, tmp_path, start(modules, tmp_path)), kind)
    question = "Is this still the correct agent intent?"
    paused = state.update(
        tmp_path,
        {"ethos": {"stage": "review"}, "pending_question": question},
        expected_revision=saved["revision"],
        state_only=True,
    )
    assert expansion.record(paused)["pending"] is None
    rendered = renderer.render(
        {
            "stage": "resume",
            "state": paused,
            "revision": paused["revision"],
            "facts": {
                "ethos": {
                    "status": "existing",
                    "link": str(tmp_path / "ETHOS.md"),
                    "summary": "The saved document describes the agent's intended behavior.",
                }
            },
        }
    )
    assert question in rendered
    assert expansion.record(state.load_state(tmp_path))["decision"]["kind"] == kind


@pytest.mark.parametrize("changed", ["example", "source"])
def test_scope_displays_changed_identity_with_unchanged_count_or_bytes(modules, tmp_path, changed):
    _, expansion, renderer = modules
    saved = start(modules, tmp_path, path="recorded_interaction")
    before = renderer.render(payload(modules, saved))
    value = copy.deepcopy(expansion.record(saved))
    if changed == "example":
        old, new = "partial-refund", "partial-refund-eur"
        examples = value["plan"]["tasks"][0]["example_ids"]
        assert examples == [old]
        examples[0] = new
    else:
        source = value["plan"]["sources"][0]
        old, new = source["path"], "traces/reviewed-refund.json"
        (tmp_path / new).write_bytes((tmp_path / old).read_bytes())
        source["path"] = new
        value["plan"]["source"] = "User selected the other recorded interaction with matching bytes"
        assert hashlib.sha256((tmp_path / new).read_bytes()).hexdigest() == source["digest"]
    revised = replace_scope(modules, tmp_path, saved, value)
    after = renderer.render(payload(modules, revised))
    assert old in before and new not in before
    assert new in after
    assert "**distinct input examples:** 1" in before and "**distinct input examples:** 1" in after
    assert before != after


@pytest.mark.parametrize("kind", ["declined", "revise"])
def test_scope_decision_rendering_is_stable_on_resume(modules, tmp_path, kind):
    state, _, renderer = modules
    saved = decide(modules, tmp_path, present(modules, tmp_path, start(modules, tmp_path)), kind)
    rendered = renderer.render(payload(modules, saved))
    resumed = state.initialize(tmp_path, journey="expand")
    assert renderer.render(payload(modules, resumed)) == rendered


def test_scope_shows_unresolved_privacy_and_admission_questions(modules, tmp_path):
    _, expansion, renderer = modules
    saved = start(modules, tmp_path, path="recorded_interaction")
    value = copy.deepcopy(expansion.record(saved))
    questions = [
        "Review the selected interaction for privacy",
        "Confirm task-candidate admission from the recorded tools",
    ]
    value["plan"]["tasks"][0]["unresolved_inputs"] = questions
    saved = replace_scope(modules, tmp_path, saved, value)
    assert all(question in renderer.render(payload(modules, saved)) for question in questions)


def test_scope_shows_selected_unselected_and_deferred_improvements(modules, tmp_path):
    _, expansion, renderer = modules
    saved = start(modules, tmp_path, extra_gap=True)
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["selected_task_ids"] = ["cover-refund"]
    reason = "User chose to keep this eligible improvement for later"
    value["plan"]["deferred"].append({"id": "R3", "improvement": "Order cancellation coverage", "reason": reason})
    saved = replace_scope(modules, tmp_path, saved, value)
    rendered = renderer.render(payload(modules, saved))
    assert "cover-refund" in rendered and "cover-cancel-order" in rendered
    assert reason in rendered
    assert "Repair the existing order grader" in rendered
