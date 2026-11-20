# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import shutil

import pytest
from journey_state_support import ethos_ready, expansion_payload, setup_ready
from test_expansion_progress import FIXTURES, author, begin, progress, save
from test_expansion_scope import modules as modules
from test_first_eval_state import evidence

REVIEW_QUESTION = "Approve both created refund tasks?"
RUN_QUESTION = "Run both tasks twice with the displayed settings?"


def render(modules, saved):
    return modules[2].render({**expansion_payload(modules, saved), "stage": "expansion_review"})


def bound(modules, repo, saved, checkpoint, source):
    current = progress(modules, repo, saved)[checkpoint]
    return {"digest": current["digest" if checkpoint == "review" else "selection_digest"], "source": source}


def ready(modules, repo, statuses=("passed", "passed"), *, setup="gym", same_title=False):
    saved, value = begin(modules, repo)
    if setup == "harbor":
        setup_ready(modules[0], repo, journey="expand", setup=setup)
        saved = ethos_ready(modules[0], repo)
        value = modules[1].new_scope(
            saved, path=value["path"], plan=value["plan"], question=None, source="Harbor task plan"
        )
        value["decision"] = {"kind": "approved", "source": "User approves Harbor task plan"}
        saved = save(modules, repo, saved, value)
    if same_title:
        value["plan"]["tasks"][1].update(
            task=value["plan"]["tasks"][0]["task"],
            scenario="Repeat the same refund request",
            outcome="Keep the remaining balance unchanged",
        )
        value["decision"]["source"] = "User approves distinct same-title cases"
        saved = save(modules, repo, saved, value)
    for index, status in enumerate(statuses):
        if status != "queued":
            evidence(repo, f".eval-author/tasks/refund-{index}/README.md", f"Review refund task {index}")
            author(modules, repo, value, index, status)
    value["review"] = {
        "artifacts": None,
        "presented": None,
        "decision": None,
        "pending": {"question": REVIEW_QUESTION, "source": "Complete task review"},
    }
    saved = save(modules, repo, saved, value)
    value["review"]["artifacts"] = {
        **bound(modules, repo, saved, "review", "Refreshed authored-task reports"),
        "evidence": [
            evidence(repo, path, "Refund task inventory")
            for path in (".eval-author/report.html", ".eval-author/report_markdown/tasks.md")
        ],
    }
    return save(modules, repo, saved, value), value


def present_review(modules, repo, saved, value):
    value["review"]["presented"] = bound(modules, repo, saved, "review", "Delivered task review")
    return save(modules, repo, saved, value)


def approve(modules, repo, saved, value):
    value["review"]["decision"] = {
        **bound(modules, repo, saved, "review", "User approves both created tasks"),
        "decision": "approved",
    }
    value["review"]["pending"] = None
    return save(modules, repo, saved, value)


def execution(modules, repo, saved, value, *, present=True):
    value["execution"] = {
        "execution_settings": {
            "agent_model": "refund-agent / configured-target",
            "attempts": "Two attempts per task",
            "validation": "The verifier controls passed. I’ll check the agent’s refund amount and remaining balance in each attempt.",
            "limits": "Four attempts; no automatic retries; at most six steps per attempt; synthetic orders only",
            "open_questions": [],
        },
        "selection": {
            "agent": "refund-agent",
            "model": "configured-target",
            "source": "Verified native configuration",
            "config": evidence(repo, ".eval-author/.working/agent.yaml", "agent: refund-agent"),
            "inputs": [
                evidence(
                    repo, ".eval-author/.working/refunds.jsonl", '{"refund": "partial"}\n{"refund": "duplicate"}\n'
                )
            ],
            "cases": [
                {
                    "task_id": modules[1].task_key(task),
                    "example_ids": [f"refund-{index}"],
                    "attempts": 2,
                    "rows": [index + 1],
                }
                for index, task in enumerate(value["plan"]["tasks"])
            ],
        },
        "execution_settings_presented": None,
        "declined": None,
        "pending": {"question": RUN_QUESTION, "source": "Actual-agent run decision"},
    }
    saved = save(modules, repo, saved, value)
    if present:
        value["execution"]["execution_settings_presented"] = bound(
            modules, repo, saved, "execution", "Delivered execution settings"
        )
        saved = save(modules, repo, saved, value)
    return saved


def authorized(modules, repo, *, setup="gym", same_title=False):
    saved, value = ready(modules, repo, setup=setup, same_title=same_title)
    saved = approve(modules, repo, present_review(modules, repo, saved, value), value)
    saved = execution(modules, repo, saved, value)
    value["execution"]["selection"]["authorization"] = bound(
        modules, repo, saved, "execution", "User authorizes four attempts"
    )
    value["execution"]["pending"] = None
    return save(modules, repo, saved, value), value


def assert_unchanged_error(modules, repo, saved, value):
    before = modules[0].state_path(repo).read_bytes()
    with pytest.raises(ValueError):
        save(modules, repo, saved, value)
    assert modules[0].state_path(repo).read_bytes() == before


@pytest.mark.parametrize("decision", ["authorize", "decline"])
def test_review_and_distinct_run_decision_resume_deterministically(modules, tmp_path, decision):
    saved, value = ready(modules, tmp_path)
    if decision == "authorize":
        expected = (FIXTURES / "review-ready.md").read_text().split("\n\n", 1)[1]
        assert render(modules, saved).replace(str(tmp_path), "/repo") == expected
    saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
    assert progress(modules, tmp_path, saved)["review"]["approved"]
    saved = execution(modules, tmp_path, saved, value)
    assert not progress(modules, tmp_path, saved)["execution"]["authorized"]
    plan = render(modules, saved)
    assert plan.count(RUN_QUESTION) == 1
    assert "Running your agent will test its performance" in plan
    assert plan.index("**Run limits:**") < plan.index("**Run configuration:**") < plan.index(RUN_QUESTION)
    target = value["execution"]["selection"] if decision == "authorize" else value["execution"]
    target["authorization" if decision == "authorize" else "declined"] = bound(
        modules, tmp_path, saved, "execution", f"User reply: {decision}"
    )
    value["execution"]["pending"] = None
    saved = save(modules, tmp_path, saved, value)
    before = modules[0].state_path(tmp_path).read_bytes()
    output = render(modules, saved)
    assert output == render(modules, saved)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    resumed = modules[0].initialize(tmp_path, journey="expand")
    assert render(modules, resumed) == output and resumed["pending_question"] is None
    assert not progress(modules, tmp_path, resumed)["complete"] and RUN_QUESTION not in output
    if decision == "authorize":
        expected = (FIXTURES / "execution-authorized.md").read_text().split("\n\n", 1)[1]
        assert output.replace(str(tmp_path), "/repo") == expected
    else:
        assert progress(modules, tmp_path, resumed)["execution"]["declined"]
        assert not progress(modules, tmp_path, resumed)["execution"]["authorized"]


@pytest.mark.parametrize("status", ["queued", "failed", "blocked", "missing", "stale"])
def test_incomplete_review_shows_all_tasks_but_cannot_approve(modules, tmp_path, status):
    saved, value = ready(
        modules, tmp_path, ("passed", status if status in {"queued", "failed", "blocked"} else "passed")
    )
    if status in {"missing", "stale"}:
        target = (
            tmp_path
            / value["creation"]["tasks"][modules[1].task_key(value["plan"]["tasks"][1])]["path"]
            / "instruction.md"
        )
        shutil.rmtree(target.parent) if status == "missing" else target.write_text("Changed instruction")
        saved = modules[0].initialize(tmp_path, journey="expand")
    output = render(modules, saved)
    assert all(task["task"] in output for task in value["plan"]["tasks"])
    assert not progress(modules, tmp_path, saved)["review"]["ready"]
    saved = present_review(modules, tmp_path, saved, value)
    value["review"]["decision"] = {**bound(modules, tmp_path, saved, "review", "User approves"), "decision": "approved"}
    value["review"]["pending"] = None
    assert_unchanged_error(modules, tmp_path, saved, value)


def test_requested_draft_changes_revalidate_only_affected_task_and_require_review_again(modules, tmp_path):
    saved, value = ready(modules, tmp_path)
    saved = present_review(modules, tmp_path, saved, value)
    value["review"]["decision"] = {
        **bound(modules, tmp_path, saved, "review", "User requests duplicate-refund correction"),
        "decision": "changes_requested",
    }
    value["review"]["pending"] = None
    saved = save(modules, tmp_path, saved, value)
    unchanged = copy.deepcopy(value["creation"]["tasks"][modules[1].task_key(value["plan"]["tasks"][0])])
    evidence(tmp_path, ".eval-author/tasks/refund-1/verifier.py", "Reject duplicate refunds")
    assert not progress(modules, tmp_path, saved)["review"]["approved"]
    author(modules, tmp_path, value, 1)
    saved = save(modules, tmp_path, saved, value)
    assert value["creation"]["tasks"][modules[1].task_key(value["plan"]["tasks"][0])] == unchanged
    assert not progress(modules, tmp_path, saved)["review"]["ready"]
    value["review"]["artifacts"]["digest"] = bound(modules, tmp_path, saved, "review", "Refresh")["digest"]
    saved = save(modules, tmp_path, saved, value)
    updated = render(modules, saved)
    assert "The updated tasks have passed their checks and need your approval" in updated
    assert "The previous task review no longer applies" not in updated
    saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
    assert progress(modules, tmp_path, saved)["review"]["approved"]


@pytest.mark.parametrize("change", ["task", "control", "agent", "settings", "config", "inputs", "report"])
def test_current_bytes_invalidate_only_dependent_decisions(modules, tmp_path, change):
    saved, value = authorized(modules, tmp_path)
    if change in {"task", "control", "config", "inputs", "report"}:
        path = {
            "task": ".eval-author/tasks/refund-1/instruction.md",
            "control": ".eval-author/.working/refund-1-passed.json",
            "config": ".eval-author/.working/agent.yaml",
            "inputs": ".eval-author/.working/refunds.jsonl",
            "report": ".eval-author/report.html",
        }[change]
        (tmp_path / path).write_text("Changed current bytes")
    elif change == "settings":
        value["execution"]["execution_settings"]["limits"] = "Four attempts; changed time limit"
        saved = save(modules, tmp_path, saved, value)
    else:
        value["execution"]["selection"]["agent"] = "another-agent"
        saved = save(modules, tmp_path, saved, value)
    current = progress(modules, tmp_path, modules[0].initialize(tmp_path, journey="expand"))
    assert current["review"]["approved"] == (change not in {"task", "control"})
    assert current["execution"]["authorized"] == (change == "report")
    assert current["creation"]["rows"][0]["status"] == "passed"
    if change == "report":
        assert not current["review"]["artifacts_current"]
        value["review"]["decision"]["source"] = "User renews task approval"
        assert_unchanged_error(modules, tmp_path, modules[0].load_state(tmp_path), value)


@pytest.mark.parametrize(
    "invalid",
    [
        "review_without_display",
        "combined_review",
        "run_without_display",
        "combined_run",
        "subset",
        "stale_revision",
        "run_uses_task_approval",
    ],
)
def test_checkpoint_gates_reject_invalid_updates_without_writes(modules, tmp_path, invalid):
    saved, value = ready(modules, tmp_path)
    if invalid in {"review_without_display", "combined_review"}:
        if invalid == "combined_review":
            value["review"]["presented"] = bound(modules, tmp_path, saved, "review", "Delivered review")
        value["review"]["decision"] = {
            **bound(modules, tmp_path, saved, "review", "User approves"),
            "decision": "approved",
        }
        value["review"]["pending"] = None
    else:
        saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
        saved = execution(
            modules, tmp_path, saved, value, present=invalid in {"subset", "stale_revision", "run_uses_task_approval"}
        )
        if invalid == "combined_run":
            value["execution"]["execution_settings_presented"] = bound(
                modules, tmp_path, saved, "execution", "Delivered settings"
            )
        if invalid == "subset":
            value["execution"]["selection"]["cases"].pop()
            saved = save(modules, tmp_path, saved, value)
            value["execution"]["execution_settings_presented"] = bound(
                modules, tmp_path, saved, "execution", "Delivered subset"
            )
            saved = save(modules, tmp_path, saved, value)
        value["execution"]["selection"]["authorization"] = bound(
            modules, tmp_path, saved, "execution", "User authorizes"
        )
        if invalid == "run_uses_task_approval":
            value["execution"]["selection"]["authorization"]["source"] = value["review"]["decision"]["source"]
        value["execution"]["pending"] = None
        if invalid == "stale_revision":
            saved = {**saved, "revision": saved["revision"] - 1}
    assert_unchanged_error(modules, tmp_path, saved, value)


def test_review_and_execution_questions_survive_shared_prerequisite_recovery(modules, tmp_path):
    saved, value = ready(modules, tmp_path)
    for question in (REVIEW_QUESTION, RUN_QUESTION):
        assert saved["pending_question"] == question
        saved = modules[0].initialize(tmp_path, journey="expand")
        assert saved["pending_question"] == question
        blocked = modules[0].update(
            tmp_path,
            {
                "prerequisites": {"runtime": {"status": "missing", "source": "Runtime unavailable"}},
                "blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}],
                "pending_question": "Restore the runtime.",
            },
            expected_revision=saved["revision"],
        )
        with pytest.raises(ValueError, match="shared onboarding"):
            render(modules, blocked)
        saved = modules[0].update(
            tmp_path,
            {
                "prerequisites": {"runtime": {"status": "verified", "source": "Runtime restored"}},
                "blockers": [],
                "pending_question": None,
            },
            expected_revision=blocked["revision"],
        )
        assert saved["pending_question"] == question and render(modules, saved).count(question) == 1
        if question == REVIEW_QUESTION:
            saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
            saved = execution(modules, tmp_path, saved, value)


def test_withdrawing_review_display_invalidates_approval_and_run_permission(modules, tmp_path):
    saved, value = authorized(modules, tmp_path)
    value["review"]["presented"] = None
    current = progress(modules, tmp_path, save(modules, tmp_path, saved, value))
    assert not current["review"]["approved"] and not current["execution"]["authorized"]


def test_ethos_confirmation_does_not_clear_review_dependency_recovery(modules, tmp_path):
    saved, _ = ready(modules, tmp_path)
    question = "Restore the verifier dependency before continuing."
    modules[0].update(
        tmp_path,
        {
            "ethos": {"stage": "review"},
            "blockers": [{"id": "verifier", "phase": "expand", "message": question}],
            "pending_question": question,
        },
        expected_revision=saved["revision"],
        state_only=True,
    )
    confirmed = modules[0].confirm_ethos(
        tmp_path,
        reviewed_digest=modules[0].ethos_digest(tmp_path),
        source="User confirms intent while dependency remains unavailable",
        validation="Saved-file validation passed",
    )
    assert confirmed["pending_question"] == question
    output = modules[2].render(
        {
            "stage": "resume",
            "state": confirmed,
            "revision": confirmed["revision"],
            "facts": {
                "blockers": [{"id": "verifier", "step": "Task review", "reason": question, "needed": "Restore access"}]
            },
        }
    )
    assert question in output and "Restore access" in output


def test_scope_revision_retains_history_and_clears_later_questions(modules, tmp_path):
    saved, value = ready(modules, tmp_path)
    saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
    saved = execution(modules, tmp_path, saved, value)
    plan = {**value["plan"], "summary": "Revised refund scope"}
    revised = modules[1].new_scope(
        saved, path=value["path"], plan=plan, question="Agree to this revised plan?", source="User revises scope"
    )
    saved = save(modules, tmp_path, saved, revised)
    assert revised["creation"]["tasks"] == value["creation"]["tasks"]
    assert revised["review"]["decision"] == value["review"]["decision"]
    assert revised["review"]["pending"] is revised["execution"]["pending"] is None
    with pytest.raises(ValueError, match="Use expansion_scope"):
        modules[2].render({"stage": "resume", "state": saved, "revision": saved["revision"]})
    revised.update(decision={"kind": "approved", "source": "User agrees to revised scope"}, pending=None)
    saved = save(modules, tmp_path, saved, revised)
    assert saved["pending_question"] is None and RUN_QUESTION not in render(modules, saved)


@pytest.mark.parametrize("change", ["task", "control", "config", "missing_model"])
def test_stale_approval_questions_are_suppressed_but_missing_inputs_remain_visible(modules, tmp_path, change):
    saved, value = ready(modules, tmp_path)
    question = REVIEW_QUESTION
    if change in {"config", "missing_model"}:
        saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
        saved = execution(modules, tmp_path, saved, value)
        question = RUN_QUESTION
    if change == "missing_model":
        question = "Which agent model should run these tasks?"
        value["execution"].update(selection=None, pending={"question": question, "source": "Model not selected"})
        value["execution"]["execution_settings"]["open_questions"] = [question]
        saved = save(modules, tmp_path, saved, value)
    else:
        path = {
            "task": ".eval-author/tasks/refund-1/instruction.md",
            "control": ".eval-author/.working/refund-1-passed.json",
            "config": ".eval-author/.working/agent.yaml",
        }[change]
        (tmp_path / path).write_text("Changed bytes invalidate the pending approval")
    payload: dict = {**expansion_payload(modules, saved), "stage": "expansion_review"}
    expected_question = question if change == "missing_model" else None
    assert payload["state"]["pending_question"] == expected_question
    assert modules[2].render(payload).count(question) == (1 if change == "missing_model" else 0)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    assert resumed["pending_question"] == expected_question
    assert expansion_payload(modules, resumed)["state"]["pending_question"] == resumed["pending_question"]
    assert render(modules, resumed).count(question) == (1 if change == "missing_model" else 0)


def test_harbor_ignores_non_gym_row_metadata_when_rendering(modules, tmp_path):
    saved, value = ready(modules, tmp_path, setup="harbor")
    saved = approve(modules, tmp_path, present_review(modules, tmp_path, saved, value), value)
    saved = execution(modules, tmp_path, saved, value)
    for case in value["execution"]["selection"]["cases"]:
        case["rows"] = ["ROW_METADATA | forged row\n### Forged heading"]
    output = render(modules, save(modules, tmp_path, saved, value))
    assert "ROW_METADATA" not in output and output.count("Not applicable") == 2
    assert "Attempts per task" in output


def result_payload(modules, repo, *, setup="gym", same_title=False):
    state, _, _ = modules
    saved, value = authorized(modules, repo, setup=setup, same_title=same_title)
    for task in value["creation"]["tasks"].values():
        saved = state.record_task(repo, task=task["path"], status="draft", source="New authored task")
    value["execution"]["pending"] = {
        "question": "Which tasks do you approve, or what would you like to change?",
        "source": "Post-run task acceptance decision",
    }
    saved = save(modules, repo, saved, value)
    paths = {}
    for field, name in (("output", "interaction.json"), ("trace", "trajectory.json"), ("verification", "checks.json")):
        path = f".eval-author/jobs/refund-0/repeat-1/{name}"
        evidence(repo, path, "Retained native attempt evidence")
        paths[field] = str(repo / path)
    cases = value["execution"]["selection"]["cases"]
    attempts = [
        {
            "task_id": case["task_id"],
            "example_id": case["example_ids"][0] if setup == "gym" else None,
            "attempt": attempt,
            "status": "completed",
            "score": "1" if attempt == 1 else "0",
            "error": None,
            **(paths if index == 0 and attempt == 1 else dict.fromkeys(paths)),
        }
        for index, case in enumerate(cases)
        for attempt in (1, 2)
    ]
    attempts[2].update(status="error", score=None, error="Provider timeout | retry required")
    return (
        saved,
        value,
        {
            **expansion_payload(modules, saved),
            "stage": "expansion_results",
            "facts": {
                **expansion_payload(modules, saved)["facts"],
                "attempts": attempts[:-1],
            },
        },
    )


@pytest.mark.parametrize("setup", ["gym", "harbor"])
def test_results_show_every_authorized_attempt_with_evidence_and_leave_tasks_draft(modules, tmp_path, setup):
    state, _, renderer = modules
    saved, value, data = result_payload(modules, tmp_path, setup=setup)
    before = state.state_path(tmp_path).read_bytes()
    output = renderer.render(data)
    assert output == renderer.render(data)
    assert state.state_path(tmp_path).read_bytes() == before
    assert output.count("; no execution error") == 2
    assert "Completed; score 0; no execution error" in output
    assert "Execution error; score not available; Provider timeout &#124; retry required" in output
    assert "Missing result | Not available | Not available | Not available" in output
    assert "interaction.json" in output and "trajectory.json" in output and "checks.json" in output
    assert output.endswith(value["execution"]["pending"]["question"] + "\n")
    assert all(
        state.task_lifecycle(tmp_path, saved, task["path"])["status"] == "draft"
        for task in value["creation"]["tasks"].values()
    )
    attempts = data["facts"]["attempts"]
    resumed = state.initialize(tmp_path, journey="expand")
    data.update(expansion_payload(modules, resumed))
    data["stage"] = "expansion_results"
    data["facts"]["attempts"] = attempts
    assert renderer.render(data) == output
    if setup == "gym":
        fixture = FIXTURES / "results-review.md"
        assert output.replace(str(tmp_path), "/repo") == fixture.read_text().split("\n\n", 1)[1]


@pytest.mark.parametrize("change", ["duplicate", "unselected", "bad_link", "stale_task", "stale_config", "no_question"])
def test_results_reject_ambiguous_scope_stale_authorization_and_missing_review_question(modules, tmp_path, change):
    state, _, renderer = modules
    saved, value, data = result_payload(modules, tmp_path)
    if change == "duplicate":
        data["facts"]["attempts"][1] = data["facts"]["attempts"][0]
    elif change == "unselected":
        data["facts"]["attempts"][0]["attempt"] = 3
    elif change == "bad_link":
        data["facts"]["attempts"][0]["output"] = str(tmp_path / ".eval-author/../private.json")
    elif change == "no_question":
        value["execution"]["pending"] = None
        saved = save(modules, tmp_path, saved, value)
    else:
        path = (
            ".eval-author/tasks/refund-0/instruction.md"
            if change == "stale_task"
            else ".eval-author/.working/agent.yaml"
        )
        (tmp_path / path).write_text("Changed content")
        saved = state.initialize(tmp_path, journey="expand")
    attempts = data["facts"]["attempts"]
    data.update(expansion_payload(modules, saved))
    data["stage"] = "expansion_results"
    data["facts"]["attempts"] = attempts
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        renderer.render(data)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("change", ["authorization_source", "no_question", "pending_question", "question_source"])
def test_post_run_review_requires_a_distinct_answer_to_the_saved_question(modules, tmp_path, change):
    if change == "no_question":
        saved, value = authorized(modules, tmp_path)
    else:
        saved, value, _ = result_payload(modules, tmp_path)
    execution = value["execution"]
    source = "User accepts the displayed actual-agent results"
    if change == "authorization_source":
        source = execution["selection"]["authorization"]["source"]
    elif change == "question_source":
        source = execution["pending"]["source"]
    if change != "pending_question":
        execution["pending"] = None
    execution["results_review"] = bound(modules, tmp_path, saved, "execution", source)
    assert_unchanged_error(modules, tmp_path, saved, value)


@pytest.mark.parametrize("change", ["rerun", "selection", "settings"])
def test_post_run_review_must_be_cleared_on_reauthorization_or_changed_settings(modules, tmp_path, change):
    saved, value = reviewed(modules, tmp_path)
    execution = value["execution"]
    assert saved["pending_question"] is None
    assert progress(modules, tmp_path, saved)["execution"]["results_reviewed"]
    resumed = modules[0].initialize(tmp_path, journey="expand")
    assert progress(modules, tmp_path, resumed)["execution"]["results_reviewed"]
    original = copy.deepcopy(execution)
    if change == "rerun":
        execution["selection"]["authorization"]["source"] = "User authorizes a rerun"
    elif change == "selection":
        execution["selection"]["cases"][0]["attempts"] = 3
    else:
        execution["execution_settings"]["attempts"] = "Three attempts per task"
    assert_unchanged_error(modules, tmp_path, resumed, value)
    execution["results_review"] = None
    saved = save(modules, tmp_path, resumed, value)
    assert not progress(modules, tmp_path, saved)["execution"]["results_reviewed"]
    if change != "rerun":
        # Restoring the original configuration must not resurrect its old review.
        value["execution"] = {**original, "results_review": None}
        saved = save(modules, tmp_path, saved, value)
        assert progress(modules, tmp_path, saved)["execution"]["authorized"]
        assert not progress(modules, tmp_path, saved)["execution"]["results_reviewed"]
    value["execution"]["pending"] = {"question": "Approve the rerun results?", "source": "New post-run review question"}
    saved = save(modules, tmp_path, saved, value)
    value["execution"]["pending"] = None
    value["execution"]["results_review"] = bound(modules, tmp_path, saved, "execution", "User accepts the new results")
    saved = save(modules, tmp_path, saved, value)
    assert progress(modules, tmp_path, saved)["execution"]["results_reviewed"]


@pytest.mark.parametrize(
    "stage", ["resume", "expansion_continue", "expansion_scope", "expansion_progress", "expansion_review"]
)
def test_pending_post_run_review_resume_routes_to_results(modules, tmp_path, stage):
    saved, _, data = result_payload(modules, tmp_path)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    payload = {"stage": stage, "state": resumed, "revision": resumed["revision"]}
    payload = {**expansion_payload(modules, resumed), "stage": stage}
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Use expansion_results"):
        modules[2].render(payload)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    if stage == "resume":
        with pytest.raises(ValueError, match="Use expansion_review"):
            modules[2].render({"stage": "resume", "state": resumed, "revision": resumed["revision"]})
        with pytest.raises(ValueError, match="Use expansion_results"):
            modules[2].render({**expansion_payload(modules, resumed), "stage": "expansion_review"})
    attempts = data["facts"]["attempts"]
    data.update(expansion_payload(modules, resumed))
    data["stage"] = "expansion_results"
    data["facts"]["attempts"] = attempts
    output = modules[2].render(data)
    assert "Missing result" in output
    assert "The next step is to run the selected tasks" not in output
    assert output.endswith(resumed["pending_question"] + "\n")


@pytest.mark.parametrize("stage", ["resume", "expansion_continue", "expansion_review"])
def test_changed_settings_resume_returns_to_run_permission(modules, tmp_path, stage):
    saved, value = authorized(modules, tmp_path)
    value["execution"]["execution_settings"]["limits"] = "Changed time limit; no automatic retries"
    value["execution"]["pending"] = {"question": RUN_QUESTION, "source": "Renew permission for changed settings"}
    saved = save(modules, tmp_path, saved, value)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    if stage == "resume":
        payload = {"stage": stage, "state": resumed, "revision": resumed["revision"]}
    else:
        payload = {**expansion_payload(modules, resumed), "stage": stage}
    if stage == "expansion_review":
        output = modules[2].render(payload)
        assert "Changed time limit" in output and output.endswith(RUN_QUESTION + "\n")
        assert "You’ve authorized this run" not in output
    else:
        with pytest.raises(ValueError, match="Use expansion_review"):
            modules[2].render(payload)


def reviewed(modules, repo):
    saved, value, _ = result_payload(modules, repo)
    value["execution"]["pending"] = None
    value["execution"]["results_review"] = bound(
        modules, repo, saved, "execution", "User accepts the displayed actual-agent results"
    )
    return save(modules, repo, saved, value), value


@pytest.mark.parametrize("change", ["config", "inputs", "task", "validation", "presentation", "declined"])
def test_stale_or_declined_post_run_review_returns_to_repair(modules, tmp_path, change):
    saved, value, _ = result_payload(modules, tmp_path)
    if change in {"config", "inputs", "task", "validation"}:
        path = {
            "config": ".eval-author/.working/agent.yaml",
            "inputs": ".eval-author/.working/refunds.jsonl",
            "task": ".eval-author/tasks/refund-0/instruction.md",
            "validation": ".eval-author/.working/refund-0-passed.json",
        }[change]
        (tmp_path / path).write_text("Changed current evidence")
    elif change == "presentation":
        value["execution"]["execution_settings_presented"] = None
        saved = save(modules, tmp_path, saved, value)
    else:
        question = value["execution"]["pending"]
        value["execution"]["pending"] = None
        value["execution"]["declined"] = bound(modules, tmp_path, saved, "execution", "User declines further execution")
        saved = save(modules, tmp_path, saved, value)
        value["execution"]["pending"] = question
        saved = save(modules, tmp_path, saved, value)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    payload = expansion_payload(modules, resumed)
    current = progress(modules, tmp_path, resumed)
    assert not current["execution"]["authorized"]
    assert not modules[1].results_pending(current)
    before = modules[0].state_path(tmp_path).read_bytes()
    for stage in ("resume", "expansion_continue"):
        for observed in (False, True):
            routing = {**payload, "stage": stage}
            if not observed:
                routing.pop("facts")
            with pytest.raises(ValueError, match="Use expansion_review"):
                modules[2].render(routing)
    output = modules[2].render({**payload, "stage": "expansion_review"})
    assert "The next step is to run the selected tasks" not in output
    assert "final acceptance requires your approval" not in output
    assert modules[0].state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("kind", ["task", "scope"])
@pytest.mark.parametrize("edit_settings", [False, True])
def test_repairs_can_clear_obsolete_results_without_granting_other_execution_edits(
    modules, tmp_path, kind, edit_settings
):
    saved, value = reviewed(modules, tmp_path)
    if kind == "task":
        evidence(tmp_path, ".eval-author/tasks/refund-1/verifier.py", "Reject duplicate refunds")
        author(modules, tmp_path, value, 1)
    else:
        value = modules[1].new_scope(
            saved,
            path=value["path"],
            plan={**value["plan"], "summary": "Revised refund scope"},
            question="Agree to the repaired scope?",
            source="User requests scope repair",
        )
    assert_unchanged_error(modules, tmp_path, saved, value)
    value["execution"]["results_review"] = None
    value["execution"]["pending"] = None
    if edit_settings:
        value["execution"]["execution_settings"]["limits"] = "Unapproved new limits"
        assert_unchanged_error(modules, tmp_path, saved, value)
    else:
        saved = save(modules, tmp_path, saved, value)
        current = progress(modules, tmp_path, saved)
        assert not current["execution"]["results_reviewed"]
        assert not current["execution"]["authorized"]
        assert not current["review"]["approved"]


def test_same_settings_rerun_has_separate_permission_and_results_questions(modules, tmp_path):
    saved, value = reviewed(modules, tmp_path)
    execution = value["execution"]
    execution["results_review"] = None
    execution["selection"]["authorization"] = None
    question = "Rerun both tasks twice with the same settings?"
    execution["pending"] = {"question": question, "source": "Rerun permission request"}
    saved = save(modules, tmp_path, saved, value)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    output = render(modules, resumed)
    assert output.endswith(question + "\n")
    assert "final acceptance requires your approval" not in output
    assert not progress(modules, tmp_path, resumed)["execution"]["authorized"]
    execution["pending"] = None
    execution["selection"]["authorization"] = bound(
        modules, tmp_path, resumed, "execution", "User authorizes the same-settings rerun"
    )
    saved = save(modules, tmp_path, resumed, value)
    assert progress(modules, tmp_path, saved)["execution"]["authorized"]
    assert not progress(modules, tmp_path, saved)["execution"]["results_reviewed"]
    execution["pending"] = {"question": "Approve the new results?", "source": "Rerun results question"}
    saved = save(modules, tmp_path, saved, value)
    payload = {
        **expansion_payload(modules, saved),
        "stage": "expansion_results",
        "facts": {**expansion_payload(modules, saved)["facts"], "attempts": []},
    }
    output = modules[2].render(payload)
    assert "Missing result" in output and output.endswith("Approve the new results?\n")


def test_reviewed_run_resumes_without_pre_run_copy_or_settled_questions(modules, tmp_path):
    saved, _ = reviewed(modules, tmp_path)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    payload = expansion_payload(modules, resumed)
    for stage in ("resume", "expansion_continue"):
        with pytest.raises(ValueError, match="Use expansion_review"):
            modules[2].render({**payload, "stage": stage})
    before = modules[0].state_path(tmp_path).read_bytes()
    output = modules[2].render({**payload, "stage": "expansion_review"})
    assert output == modules[2].render({**payload, "stage": "expansion_review"})
    assert modules[0].state_path(tmp_path).read_bytes() == before
    expected = (FIXTURES / "results-reviewed.md").read_text().split("\n\n", 1)[1]
    assert output.replace(str(tmp_path), "/repo") == expected
    assert "The next step is to run the selected tasks" not in output
    assert resumed["pending_question"] is None and RUN_QUESTION not in output


@pytest.mark.parametrize("setup", ["gym", "harbor"])
@pytest.mark.parametrize("question_first", [False, True])
def test_execution_blocker_keeps_partial_results_visible_without_permitting_advancement(
    modules, tmp_path, setup, question_first
):
    state, _, renderer = modules
    saved, value, data = result_payload(modules, tmp_path, setup=setup)
    question = value["execution"]["pending"]
    if not question_first:
        value["execution"]["pending"] = None
        saved = save(modules, tmp_path, saved, value)
    saved = state.update(
        tmp_path,
        {"blockers": [{"id": "services", "phase": "expand", "message": "Refund service unavailable"}]},
        expected_revision=saved["revision"],
    )
    if not question_first:
        value["execution"]["pending"] = question
        saved = save(modules, tmp_path, saved, value)
    resumed = state.initialize(tmp_path, journey="expand")
    current = progress(modules, tmp_path, resumed)
    assert current["execution"]["results_inspectable"]
    assert not current["execution"]["authorized"] and not current["execution"]["results_reviewed"]
    assert not current["review"]["approved"] and not current["complete"]
    payload = expansion_payload(modules, resumed)
    for stage in ("resume", "expansion_continue", "expansion_review"):
        with pytest.raises(ValueError, match="Use expansion_results"):
            renderer.render({**payload, "stage": stage})
    payload["stage"] = "expansion_results"
    payload["facts"]["attempts"] = data["facts"]["attempts"]
    before = state.state_path(tmp_path).read_bytes()
    output = renderer.render(payload)
    assert output == renderer.render(payload)
    assert state.state_path(tmp_path).read_bytes() == before
    assert "Completed; score 1" in output and "Execution error; score not available" in output
    assert "Provider timeout" in output and "Missing result" in output
    assert "### Blockers" in output and "Refund service unavailable" in output
    assert "Resolve the blockers before accepting tasks or running the agent again" in output
    assert all(
        state.task_lifecycle(tmp_path, resumed, task["path"])["status"] == "draft"
        for task in value["creation"]["tasks"].values()
    )
    if setup == "gym":
        expected = (FIXTURES / "results-blocked.md").read_text().split("\n\n", 1)[1]
        assert output.replace(str(tmp_path), "/repo") == expected
    for decision in ("results_review", "authorization", "settings"):
        attempted = copy.deepcopy(value)
        attempted["execution"]["pending"] = None
        if decision == "settings":
            attempted["execution"]["execution_settings"]["limits"] = "Unapproved new limits"
        else:
            target = attempted["execution"]["selection"] if decision == "authorization" else attempted["execution"]
            target[decision] = bound(modules, tmp_path, resumed, "execution", "User requests advancement while blocked")
        assert_unchanged_error(modules, tmp_path, resumed, attempted)
    recovered = state.update(tmp_path, {"blockers": []}, expected_revision=resumed["revision"])
    assert progress(modules, tmp_path, recovered)["execution"]["authorized"]
    assert not progress(modules, tmp_path, recovered)["execution"]["results_reviewed"]


def test_same_title_harbor_tasks_have_distinct_labels_even_without_results(modules, tmp_path):
    saved, value, data = result_payload(modules, tmp_path, setup="harbor", same_title=True)
    data["facts"]["attempts"] = []
    output = modules[2].render(data)
    assert len({case["task_id"] for case in value["execution"]["selection"]["cases"]}) == 2
    for task in value["creation"]["tasks"].values():
        assert output.count("Partial refund (" + task["path"] + ") — attempt") == 2
    assert output.count("Missing result") == 4
    assert output.endswith(saved["pending_question"] + "\n")


@pytest.mark.parametrize("stage", ["expansion_review", "expansion_progress", "expansion_scope", "expansion_results"])
def test_saved_lifecycle_requires_observations_instead_of_another_state_save(modules, tmp_path, stage):
    saved, _ = reviewed(modules, tmp_path)
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Supply expansion observations from the current repository and revision"):
        modules[2].render({"stage": stage, "state": saved, "revision": saved["revision"]})
    assert modules[0].state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("change", ["task", "validation", "config"])
def test_execution_blocker_does_not_allow_displaying_results_with_stale_bindings(modules, tmp_path, change):
    saved, _, data = result_payload(modules, tmp_path)
    saved = modules[0].update(
        tmp_path,
        {"blockers": [{"id": "services", "phase": "expand", "message": "Refund service unavailable"}]},
        expected_revision=saved["revision"],
    )
    path = {
        "task": ".eval-author/tasks/refund-0/instruction.md",
        "validation": ".eval-author/.working/refund-0-passed.json",
        "config": ".eval-author/.working/agent.yaml",
    }[change]
    (tmp_path / path).write_text("Changed evidence after the run")
    saved = modules[0].initialize(tmp_path, journey="expand")
    assert not progress(modules, tmp_path, saved)["execution"]["results_inspectable"]
    attempts = data["facts"]["attempts"]
    data.update(expansion_payload(modules, saved))
    data["stage"] = "expansion_results"
    data["facts"]["attempts"] = attempts
    with pytest.raises(ValueError, match="Reconcile the current tasks, settings, and run authorization"):
        modules[2].render(data)
