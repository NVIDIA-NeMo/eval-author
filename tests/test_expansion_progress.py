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


def begin(modules, repo):
    saved = start(modules, repo)
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
            "source": "Owning workflow checked native results",
            "evidence": [evidence(repo, f".eval-author/.working/refund-{index}-{status}.json", status)],
        }
    value["creation"]["tasks"][modules[1].task_key(value["plan"]["tasks"][index])] = task
    return task


def progress(modules, repo, saved):
    return modules[1].derive(saved, modules[1].observe(repo, saved))


def render(modules, saved):
    return modules[2].render({**expansion_payload(modules, saved), "stage": "expansion_progress"})


def test_two_task_creation_failure_repair_and_resume_match_fixtures(modules, tmp_path):
    state, expansion, _ = modules
    saved, value = begin(modules, tmp_path)
    opening = render(modules, saved)
    assert "The next steps are to reuse or prepare the test setup" in opening
    assert "get your permission before running your agent" in opening
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
        "detail": "The duplicate-request grader accepted a second refund. I’m correcting that check.",
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
            evidence(tmp_path, f"{failed['path']}/grader.py", "Reject a second refund")
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
