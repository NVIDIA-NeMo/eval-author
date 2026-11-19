# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import shutil
from pathlib import Path

import pytest
from journey_state_support import expansion_payload, setup_ready
from test_expansion_scope import QUESTION, start
from test_expansion_scope import modules as modules
from test_first_eval_state import evidence

FIXTURES = Path(__file__).parent / "fixtures/expansion-conversations"


def save(modules, repo, saved, value):
    question = modules[1].pending(value)
    return modules[0].update(
        repo,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": question["question"] if question else None},
        expected_revision=saved["revision"],
    )


def begin(modules, repo, path="starter_suite"):
    saved = start(modules, repo, path)
    value = copy.deepcopy(modules[1].record(saved))
    value["plan"]["tasks"].append({**value["plan"]["tasks"][0], "task": "Duplicate refund request"})
    value.update(decision={"kind": "approved", "source": "User agrees to both tasks"}, pending=None)
    saved = save(modules, repo, saved, value)
    value["creation"] = {"tasks": {}, "active": None, "pending": None}
    return save(modules, repo, saved, value), value


def author(modules, repo, value, index, status="passed"):
    path = f".eval-author/tasks/refund-{index}"
    evidence(repo, f"{path}/instruction.md", f"Refund scenario {index}")
    task = {"path": path, "sha256": modules[0].task_digest(repo, path), "validation": None}
    if status:
        task["validation"] = {
            "sha256": task["sha256"],
            "status": status,
            "source": "The verifier accepted a second refund."
            if status == "failed"
            else "Owning workflow checked native results",
            "evidence": [evidence(repo, f".eval-author/.working/refund-{index}-{status}.json", status)],
        }
    value["creation"]["tasks"][modules[1].task_key(value["plan"]["tasks"][index])] = task
    return task


def progress(modules, repo, saved):
    return modules[1].derive(saved, modules[1].observe(repo, saved))


def render(modules, saved):
    return modules[2].render({**expansion_payload(modules, saved), "stage": "expansion_progress"})


@pytest.mark.parametrize("path", ["starter_suite", "audit_tool_gap", "recorded_interaction"])
def test_starting_activity_is_visible_before_task_files_and_survives_resume(modules, tmp_path, path):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path, path)
    detail = "Preparing the partial-refund environment and starting data."
    value["creation"]["active"] = {
        "task": expansion.task_key(value["plan"]["tasks"][0]),
        "status": "creating",
        "detail": detail,
    }
    saved = save(modules, tmp_path, saved, value)
    message = render(modules, saved)
    assert "| Partial refund | Creating |" in message
    assert "| Duplicate refund request | Not created |" in message
    assert detail in message
    assert not value["creation"]["tasks"]
    assert not progress(modules, tmp_path, saved)["creation"]["ready_for_review"]
    assert not (tmp_path / ".eval-author/tasks").exists()
    resumed = state.initialize(tmp_path, journey="expand")
    assert render(modules, resumed) == message
    assert resumed["journey_status"].get("expand") != "complete"
    assert resumed["pending_question"] is None


def test_two_task_creation_failure_repair_and_resume_match_fixtures(modules, tmp_path):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path)
    opening = render(modules, saved)
    assert "The next steps are to reuse or prepare the test setup" in opening
    assert "your permission to run the evaluation" in opening
    assert not value["creation"]["tasks"]
    author(modules, tmp_path, value, 0)
    saved = save(modules, tmp_path, saved, value)
    current = progress(modules, tmp_path, saved)
    assert [row["status"] for row in current["creation"]["rows"]] == ["passed", "queued"]
    assert not current["creation"]["ready_for_review"]
    failed = author(modules, tmp_path, value, 1, "failed")
    saved = save(modules, tmp_path, saved, value)
    current = progress(modules, tmp_path, saved)["creation"]
    assert [row["status"] for row in current["rows"]] == ["passed", "failed"]
    assert failed["validation"]["source"] in current["detail"]
    value["creation"]["active"] = {
        "task": expansion.task_key(value["plan"]["tasks"][1]),
        "status": "repairing",
        "detail": "The duplicate-request verifier accepted a second refund. I’m correcting that check.",
    }
    saved = save(modules, tmp_path, saved, value)
    for fixture in ("progress-repair.md", "progress-ready.md"):
        expected = (FIXTURES / fixture).read_text().split("\n\n", 1)[1]
        before = state.state_path(tmp_path).read_bytes()
        assert render(modules, saved) == render(modules, saved) == expected
        assert state.state_path(tmp_path).read_bytes() == before
        resumed = state.initialize(tmp_path, journey="expand")
        assert render(modules, resumed) == expected
        assert resumed["journey_status"].get("expand") != "complete"
        if fixture == "progress-repair.md":
            evidence(tmp_path, f"{failed['path']}/verifier.py", "Reject a second refund")
            assert author(modules, tmp_path, value, 1)["sha256"] != failed["sha256"]
            value["creation"]["active"] = None
            saved = save(modules, tmp_path, resumed, value)
    assert progress(modules, tmp_path, saved)["creation"]["ready_for_review"]
    assert (tmp_path / ".eval-author/.working/refund-1-failed.json").read_text() == "failed"


@pytest.mark.parametrize("change", ["task_edit", "evidence_edit", "missing_task", "missing_evidence"])
def test_changed_or_missing_artifacts_invalidate_only_the_affected_task(modules, tmp_path, change):
    saved, value = begin(modules, tmp_path)
    author(modules, tmp_path, value, 0)
    task = author(modules, tmp_path, value, 1)
    saved = save(modules, tmp_path, saved, value)
    receipt = tmp_path / task["validation"]["evidence"][0]["path"]
    if change == "task_edit":
        (tmp_path / task["path"] / "instruction.md").write_text("Changed task")
    elif change == "evidence_edit":
        receipt.write_text("Changed native result")
    elif change == "missing_task":
        shutil.rmtree(tmp_path / task["path"])
    else:
        receipt.unlink()
    resumed = modules[0].initialize(tmp_path, journey="expand")
    current = progress(modules, tmp_path, resumed)["creation"]
    assert [row["status"] for row in current["rows"]] == ["passed", "missing" if change == "missing_task" else "stale"]
    assert not current["ready_for_review"]
    assert modules[1].record(resumed) == value


def test_blocked_question_survives_shared_recovery_without_repeating_scope_approval(modules, tmp_path):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path)
    author(modules, tmp_path, value, 0)
    question = "Which refund policy should the duplicate-request task use?"
    value["creation"]["pending"] = {"question": question, "source": "Missing policy decision"}
    value["creation"]["active"] = {
        "task": expansion.task_key(value["plan"]["tasks"][1]),
        "status": "blocked",
        "detail": "The refund policy is missing.",
    }
    saved = save(modules, tmp_path, saved, value)
    before = render(modules, saved)
    assert before.count(question) == 1 and QUESTION not in before
    blocked = state.update(
        tmp_path,
        {
            "prerequisites": {"runtime": {"status": "missing", "source": "Runtime probe failed"}},
            "blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}],
            "pending_question": "Restore the runtime.",
        },
        expected_revision=saved["revision"],
    )
    with pytest.raises(ValueError, match="shared onboarding"):
        render(modules, blocked)
    repaired = state.update(
        tmp_path,
        {
            "prerequisites": {"runtime": {"status": "verified", "source": "Runtime probe passed"}},
            "blockers": [],
            "pending_question": None,
        },
        expected_revision=blocked["revision"],
    )
    assert repaired["pending_question"] == question
    assert render(modules, state.initialize(tmp_path, journey="expand")) == before


def test_scope_revision_retains_artifacts_and_unchanged_validation(modules, tmp_path):
    expansion = modules[1]
    saved, value = begin(modules, tmp_path)
    first = author(modules, tmp_path, value, 0)
    second = author(modules, tmp_path, value, 1)
    value["creation"]["active"] = {
        "task": expansion.task_key(value["plan"]["tasks"][1]),
        "status": "validating",
        "detail": "Checking the second task.",
    }
    saved = save(modules, tmp_path, saved, value)
    plan = copy.deepcopy(value["plan"])
    plan["tasks"][1]["scenario"] = "Duplicate refund request after the refund window closes"
    revised = expansion.new_scope(
        saved,
        path=value["path"],
        plan=plan,
        question="Use the revised duplicate-request scenario?",
        source="Scope revision",
    )
    assert revised["creation"]["tasks"] == value["creation"]["tasks"]
    assert revised["creation"]["active"] is None
    value = revised
    saved = save(modules, tmp_path, saved, value)
    assert progress(modules, tmp_path, saved)["stage"] == "scope"
    value.update(decision={"kind": "approved", "source": "User agrees to the revised scenario"}, pending=None)
    saved = save(modules, tmp_path, saved, value)
    assert [row["status"] for row in progress(modules, tmp_path, saved)["creation"]["rows"]] == ["passed", "queued"]
    assert all(modules[0].task_digest(tmp_path, task["path"]) == task["sha256"] for task in (first, second))


@pytest.mark.parametrize("change", ["declined", "revoked", "provider_round_trip"])
def test_scope_agreement_cannot_be_reactivated_by_recording_creation_activity(modules, tmp_path, change):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path)
    for index in range(2):
        author(modules, tmp_path, value, index)
    saved = save(modules, tmp_path, saved, value)
    if change == "provider_round_trip":
        state.initialize(tmp_path, setup="harbor")
        saved = setup_ready(state, tmp_path, journey="expand")
        with pytest.raises(ValueError, match="Use expansion_scope"):
            modules[2].render({"stage": "resume", "state": saved, "revision": saved["revision"]})
    else:
        value["decision"] = {"kind": "declined", "source": "User declines"} if change == "declined" else None
        saved = save(modules, tmp_path, saved, value)
    current = progress(modules, tmp_path, saved)
    assert current["stage"] != "creation"
    assert [row["status"] for row in current["creation"]["rows"]] == ["passed", "passed"]
    assert not current["creation"]["ready_for_review"]
    value = copy.deepcopy(expansion.record(saved))
    value["creation"]["active"] = {
        "task": expansion.task_key(value["plan"]["tasks"][0]),
        "status": "validating",
        "detail": "Rechecking the first task.",
    }
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Agree the current task scope"):
        save(modules, tmp_path, saved, value)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "invalid",
    ["stale_revision", "missing_task", "missing_evidence", "wrong_revision", "no_agreement", "entry_downgrade"],
)
def test_invalid_creation_updates_leave_saved_state_unchanged(modules, tmp_path, invalid):
    state, expansion, _ = modules
    if invalid == "no_agreement":
        saved = start(modules, tmp_path)
        value = copy.deepcopy(expansion.record(saved))
        value["creation"] = {"tasks": {}, "active": None, "pending": None}
    else:
        saved, value = begin(modules, tmp_path)
    task = author(modules, tmp_path, value, 0)
    if invalid == "stale_revision":
        saved = {**saved, "revision": saved["revision"] - 1}
    elif invalid == "missing_task":
        shutil.rmtree(tmp_path / task["path"])
    elif invalid == "missing_evidence":
        (tmp_path / task["validation"]["evidence"][0]["path"]).unlink()
    elif invalid == "wrong_revision":
        task["validation"]["sha256"] = "b" * 64
    elif invalid == "entry_downgrade":
        saved = save(modules, tmp_path, saved, value)
        value = expansion.new_record(saved, question="Choose another improvement?", source="New entry request")
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        save(modules, tmp_path, saved, value)
    assert state.state_path(tmp_path).read_bytes() == before


def test_provider_round_trip_does_not_require_a_hidden_creation_question(modules, tmp_path):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path)
    author(modules, tmp_path, value, 0)
    value["creation"]["pending"] = {"question": "Which refund policy applies?", "source": "Missing policy"}
    saved = save(modules, tmp_path, saved, value)
    state.initialize(tmp_path, setup="harbor")
    saved = setup_ready(state, tmp_path, journey="expand")
    assert saved["pending_question"] is None
    saved = state.update(tmp_path, {"next_handoff": "eval-author-task-create"}, expected_revision=saved["revision"])
    assert progress(modules, tmp_path, saved)["stage"] == "scope"
    assert expansion.record(saved)["creation"]["pending"] == value["creation"]["pending"]
    value = copy.deepcopy(expansion.record(saved))
    value["decision"] = {"kind": "approved", "source": "User reconfirms scope after provider recovery"}
    saved = save(modules, tmp_path, saved, value)
    assert saved["pending_question"] == value["creation"]["pending"]["question"]
    assert progress(modules, tmp_path, saved)["stage"] == "creation"


@pytest.mark.parametrize("decision", [None, "approved", "declined", "revise"])
def test_continue_redirects_to_the_renderable_checkpoint(modules, tmp_path, decision):
    saved, value = begin(modules, tmp_path)
    value["decision"] = {"kind": decision, "source": "User's scope answer"} if decision else None
    value["pending"] = (
        {"question": "Which scenario should change?", "source": "User requests revision"}
        if decision == "revise"
        else None
    )
    saved = save(modules, tmp_path, saved, value)
    payload = expansion_payload(modules, saved)
    stage = "expansion_progress" if decision == "approved" else "expansion_scope"
    with pytest.raises(ValueError, match=f"Use {stage} for the saved expansion checkpoint"):
        modules[2].render({**payload, "stage": "expansion_continue"})
    assert modules[2].render({**payload, "stage": stage})


def test_shared_historical_draft_path_has_a_recoverable_scope_revision(modules, tmp_path):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path)
    author(modules, tmp_path, value, 1)
    saved = save(modules, tmp_path, saved, value)
    original = copy.deepcopy(value["plan"]["tasks"][1])
    plan = copy.deepcopy(value["plan"])
    plan["tasks"][1]["scenario"] = "Duplicate request after the refund window closes"
    value = expansion.new_scope(saved, path=value["path"], plan=plan, question=None, source="User revises scenario")
    value["decision"] = {"kind": "approved", "source": "User agrees to revision"}
    saved = save(modules, tmp_path, saved, value)
    task = author(modules, tmp_path, value, 1)
    saved = save(modules, tmp_path, saved, value)
    both = copy.deepcopy(plan)
    both["tasks"].append(original)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="before revising scope, move the current draft"):
        expansion.new_scope(saved, path=value["path"], plan=both, question=None, source="User wants both")
    assert state.state_path(tmp_path).read_bytes() == before
    moved = ".eval-author/tasks/revised-refund"
    shutil.move(tmp_path / task["path"], tmp_path / moved)
    task["path"] = moved
    task["sha256"] = state.task_digest(tmp_path, moved)
    task["validation"] = None
    saved = save(modules, tmp_path, saved, value)
    value = expansion.new_scope(saved, path=value["path"], plan=both, question=None, source="User wants both")
    value["decision"] = {"kind": "approved", "source": "User agrees to both scenarios"}
    saved = save(modules, tmp_path, saved, value)
    statuses = [row["status"] for row in progress(modules, tmp_path, saved)["creation"]["rows"]]
    assert statuses == ["queued", "draft", "missing"]
    assert not progress(modules, tmp_path, saved)["creation"]["ready_for_review"]
