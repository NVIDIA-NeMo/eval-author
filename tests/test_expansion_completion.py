# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest
from journey_state_support import expansion_payload
from test_expansion_progress import FIXTURES, save
from test_expansion_review import authorized, bound
from test_expansion_scope import modules as modules
from test_first_eval_state import evidence


def accepted(modules, repo, *, path="starter_suite"):
    from conversation_flows.expansion_templates import RESULTS_QUESTION

    saved, value = authorized(modules, repo, path=path)
    value["execution"]["pending"] = {"question": RESULTS_QUESTION, "source": "Review the observed run results"}
    saved = save(modules, repo, saved, value)
    for key, task in value["creation"]["tasks"].items():
        result = evidence(repo, f".eval-author/.working/results-{key}.json", '{"score": 1}')
        saved = modules[0].record_task(
            repo,
            task=task["path"],
            status="accepted",
            source="User accepts this task after reviewing results",
            evidence=[result["path"]],
        )
    value["execution"]["pending"] = None
    value["execution"]["results_review"] = bound(
        modules, repo, saved, "execution", "User accepts the complete current result set"
    )
    return save(modules, repo, saved, value), value


def render(modules, repo, saved, value, remaining=None):
    from conversation_flows import expansion_templates as copy

    remaining = remaining or {}
    question = copy.COMPLETION_WITH_IMPROVEMENTS if remaining else copy.COMPLETION_QUESTION
    value["execution"]["pending"] = {"question": question, "source": "Post-acceptance next action"}
    saved = save(modules, repo, saved, value)
    payload = expansion_payload(modules, saved)
    payload["stage"] = "expansion_completion"
    payload["facts"]["remaining_improvements"] = remaining
    return saved, payload, modules[2].render(payload)


def test_completion_offers_audit_or_finish_and_resumes_without_starting_work(modules, tmp_path):
    saved, value = accepted(modules, tmp_path)
    saved, payload, output = render(modules, tmp_path, saved, value)
    expected = (FIXTURES / "completion.md").read_text().split("\n\n", 1)[1]
    assert output == expected
    before = modules[0].state_path(tmp_path).read_bytes()
    assert modules[2].render(payload) == output
    assert modules[0].state_path(tmp_path).read_bytes() == before
    resumed = modules[0].initialize(tmp_path, journey="expand")
    assert modules[0].view(resumed)["expansion"]["complete"]
    assert resumed["pending_question"] == saved["pending_question"]
    assert resumed["next_handoff"] is None
    assert_completion_resume(modules, resumed, output)
    assert "Other supported" not in output
    value["execution"]["pending"] = None
    saved = save(modules, tmp_path, resumed, value)
    finished = modules[0].update(
        tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"]
    )
    assert modules[0].view(finished)["checklist"][2]["complete"]
    resumed = modules[0].initialize(tmp_path, journey="expand")
    from conversation_flows import expansion_templates as templates

    expected_finished = (
        "\n\n".join(
            [templates.COMPLETION_FINISHED_HEADING, templates.COMPLETION_ACCEPTED, templates.COMPLETION_FINISHED]
        )
        + "\n"
    )
    assert_completion_resume(modules, resumed, expected_finished)
    assert "Would you like" not in expected_finished


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize(
    "change", ["draft", "task", "missing_task", "evidence", "missing", "agent", "scope", "settings", "rerun"]
)
def test_incomplete_or_changed_work_cannot_offer_completion_or_mark_complete(modules, tmp_path, change, finished):
    saved, value = accepted(modules, tmp_path)
    if finished:
        saved = modules[0].update(
            tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"]
        )
    task = next(iter(value["creation"]["tasks"].values()))
    if change == "draft":
        saved = modules[0].record_task(tmp_path, task=task["path"], status="draft", source="Not accepted")
    elif change == "task":
        (tmp_path / task["path"] / "instruction.md").write_text("Changed task")
    elif change == "missing_task":
        (tmp_path / task["path"] / "instruction.md").unlink()
    elif change in {"evidence", "missing"}:
        path = tmp_path / saved["tasks"][task["path"]]["evidence"][0]["path"]
        path.unlink() if change == "missing" else path.write_text("Changed evidence")
    elif change == "agent":
        saved["agent"] = "different-agent"
        modules[0].save(tmp_path, saved)
    elif change == "settings":
        value["execution"]["results_review"] = None
        value["execution"]["selection"]["cases"][0]["attempts"] = 3
        saved = save(modules, tmp_path, saved, value)
        value["execution"]["execution_settings_presented"] = bound(
            modules, tmp_path, saved, "execution", "Displayed changed settings"
        )
        saved = save(modules, tmp_path, saved, value)
        value["execution"]["selection"]["authorization"] = bound(
            modules, tmp_path, saved, "execution", "User authorizes changed settings"
        )
        saved = save(modules, tmp_path, saved, value)
    elif change == "rerun":
        value["execution"]["results_review"] = None
        saved = save(modules, tmp_path, saved, value)
    else:
        value["execution"]["results_review"] = None
        value["plan"]["tasks"].append(copy.deepcopy(value["plan"]["tasks"][0]) | {"task": "Another task"})
        value["decision"] = None
        saved = save(modules, tmp_path, saved, value)
    saved = modules[0].initialize(tmp_path, journey="expand")
    assert not modules[0].view(saved)["expansion"]["complete"]
    checkpoint = "expansion_scope" if change == "scope" else "expansion_review"
    for stage in ("resume", "expansion_continue", "expansion_completion"):
        data = expansion_payload(modules, saved)
        data["stage"] = stage
        with pytest.raises(ValueError, match=f"Use {checkpoint}"):
            modules[2].render(data)
    recovery = expansion_payload(modules, saved)
    recovery["stage"] = checkpoint
    assert modules[2].render(recovery)
    payload = expansion_payload(modules, saved)
    payload["stage"] = "expansion_completion"
    payload["facts"]["remaining_improvements"] = {}
    with pytest.raises(ValueError):
        modules[2].render(payload)
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        modules[0].update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"])
    assert modules[0].state_path(tmp_path).read_bytes() == before


def test_remaining_improvements_require_audit_context_and_exclude_selected_ids(modules, tmp_path):
    saved, value = accepted(modules, tmp_path)
    with pytest.raises(ValueError, match="originating audit"):
        render(modules, tmp_path, saved, value, {"R2": "Add an inspection task"})
    saved = modules[0].initialize(tmp_path, journey="expand")
    audit_repo = tmp_path / "audit"
    audit_repo.mkdir()
    saved, value = accepted(modules, audit_repo, path="audit_tool_gap")
    saved, payload, output = render(modules, audit_repo, saved, value, {"R2": "Add an inspection task"})
    assert "| R2 | Add an inspection task |" in output
    assert "work on one of these improvements" in output
    payload["facts"]["remaining_improvements"] = {"R1": "Already selected"}
    with pytest.raises(ValueError, match="unselected"):
        modules[2].render(payload)
    payload["facts"]["remaining_improvements"] = {}
    with pytest.raises(ValueError, match="actual completion question"):
        modules[2].render(payload)


def assert_completion_resume(modules, saved, expected, remaining=None):
    for stage in ("resume", "expansion_continue", "expansion_review", "expansion_results"):
        payload = expansion_payload(modules, saved)
        payload["stage"] = stage
        with pytest.raises(ValueError, match="Use expansion_completion"):
            modules[2].render(payload)
    payload = expansion_payload(modules, saved)
    payload["stage"] = "expansion_completion"
    if remaining is not None or saved["journey_status"].get("expand") != "complete":
        payload["facts"]["remaining_improvements"] = remaining or {}
    assert modules[2].render(payload) == expected
    assert "next step is to run" not in expected


def test_completion_resume_retains_remaining_improvements(modules, tmp_path):
    saved, value = accepted(modules, tmp_path, path="audit_tool_gap")
    remaining = {"R2": "Add an inspection task"}
    _, _, output = render(modules, tmp_path, saved, value, remaining)
    resumed = modules[0].initialize(tmp_path, journey="expand")
    assert_completion_resume(modules, resumed, output, remaining)


@pytest.mark.parametrize("change", ["task", "evidence"])
def test_explicit_recompletion_rejects_stale_acceptance(modules, tmp_path, change):
    saved, value = accepted(modules, tmp_path)
    saved = modules[0].update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"])
    task = next(iter(value["creation"]["tasks"].values()))
    path = (
        tmp_path / task["path"] / "instruction.md"
        if change == "task"
        else tmp_path / saved["tasks"][task["path"]]["evidence"][0]["path"]
    )
    path.write_text("Changed since acceptance")
    resumed = modules[0].initialize(tmp_path, journey="expand")
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Expansion completion requires current user acceptance"):
        modules[0].update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=resumed["revision"])
    assert modules[0].state_path(tmp_path).read_bytes() == before
    demoted = modules[0].update(tmp_path, {}, expected_revision=resumed["revision"])
    assert demoted["journey_status"]["expand"] == "in_progress"


@pytest.mark.parametrize("ids", [["R0"], ["R01"], ["arbitrary"], list(range(1001))])
def test_remaining_improvement_ids_use_audit_validation(modules, tmp_path, ids):
    saved, value = accepted(modules, tmp_path, path="audit_tool_gap")
    _, payload, _ = render(modules, tmp_path, saved, value, {"R2": "Supported task"})
    payload["facts"]["remaining_improvements"] = dict.fromkeys(ids, "Unsupported ID")
    with pytest.raises(ValueError, match="unique report recommendation IDs"):
        modules[2].render(payload)


@pytest.mark.parametrize("stage", ["resume", "expansion_continue"])
def test_results_review_resume_requires_current_observations(modules, tmp_path, stage):
    saved, value = accepted(modules, tmp_path)
    saved, _, _ = render(modules, tmp_path, saved, value)
    payload = expansion_payload(modules, saved)
    payload["stage"] = stage
    observed = payload["facts"].pop("expansion_observations")
    with pytest.raises(ValueError, match="Supply current expansion_observations"):
        modules[2].render(payload)
    payload["facts"]["expansion_observations"] = observed
    observed["revision"] -= 1
    with pytest.raises(ValueError, match="current repository and revision"):
        modules[2].render(payload)


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize(
    ("recovery", "stage"),
    [("ethos", stage) for stage in ("results", "ethos", "resume")]
    + [("setup", stage) for stage in ("results", "ethos", "confirmed", "resume")],
)
def test_shared_recovery_forwards_observations_after_results_acceptance(modules, tmp_path, finished, recovery, stage):
    state, expansion, renderer = modules
    saved, value = accepted(modules, tmp_path)
    saved, _, _ = render(modules, tmp_path, saved, value)
    if finished:
        value["execution"]["pending"] = None
        saved = save(modules, tmp_path, saved, value)
        saved = state.update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"])
    if recovery == "ethos":
        (tmp_path / "ETHOS.md").write_text("# Changed intended behavior\n")
    else:
        saved = state.update(
            tmp_path,
            {
                "prerequisites": {"runtime": {"status": "missing", "source": "Runtime probe failed"}},
                "blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}],
                "pending_question": "Restore the runtime.",
            },
            expected_revision=saved["revision"],
        )
    saved = state.initialize(tmp_path, journey="expand")
    snapshot = state.view(saved)
    assert expansion.record(saved)["execution"]["results_review"] is not None
    facts = {
        "expansion_observations": snapshot["expansion_observations"],
        "ethos": {"status": "existing", "link": str(tmp_path / "ETHOS.md"), "summary": "Changed intended behavior"},
    }
    if recovery == "setup":
        facts["blockers"] = [
            {"id": "runtime", "step": "Setup", "reason": "Runtime unavailable", "needed": "Restore the runtime"}
        ]
    before = state.state_path(tmp_path).read_bytes()
    output = renderer.render(
        {"stage": stage, "state": snapshot["state"], "revision": saved["revision"], "facts": facts}
    )
    assert ("ETHOS.md" if recovery == "ethos" else "Restore the runtime") in output
    assert "Choose what to do next" not in output
    assert "Expansion complete" not in output
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("improvements", [False, True])
@pytest.mark.parametrize("change", ["draft", "task", "missing", "evidence", "agent"])
def test_saved_completion_question_must_be_cleared_after_acceptance_changes(modules, tmp_path, improvements, change):
    state, expansion, renderer = modules
    saved, value = accepted(modules, tmp_path, path="audit_tool_gap" if improvements else "starter_suite")
    remaining = {"R2": "Add an inspection task"} if improvements else {}
    saved, _, _ = render(modules, tmp_path, saved, value, remaining)
    task = next(iter(value["creation"]["tasks"].values()))
    if change == "draft":
        state.record_task(tmp_path, task=task["path"], status="draft", source="User withdraws acceptance")
    elif change == "task":
        (tmp_path / task["path"] / "instruction.md").write_text("Changed task")
    elif change in {"evidence", "missing"}:
        path = tmp_path / saved["tasks"][task["path"]]["evidence"][0]["path"]
        path.unlink() if change == "missing" else path.write_text("Changed acceptance evidence")
    else:
        saved["agent"] = "different-agent"
        state.save(tmp_path, saved)
    resumed = state.initialize(tmp_path, journey="expand")
    before = state.state_path(tmp_path).read_bytes()
    for stage in ("expansion_review", "expansion_results", "expansion_completion"):
        payload = expansion_payload(modules, resumed)
        payload["stage"] = stage
        with pytest.raises(ValueError, match="Clear the stale completion question"):
            renderer.render(payload)
    assert state.state_path(tmp_path).read_bytes() == before
    value = copy.deepcopy(expansion.record(resumed))
    value["execution"]["pending"] = None
    repaired = save(modules, tmp_path, resumed, value)
    assert repaired["pending_question"] is None
    output = renderer.render({**expansion_payload(modules, repaired), "stage": "expansion_review"})
    assert "finish here" not in output
    assert "work on one of these improvements" not in output
    assert "final acceptance requires your approval of these results" not in output
    with pytest.raises(ValueError, match="Expansion completion requires current user acceptance"):
        state.update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=repaired["revision"])


def test_already_reviewed_results_with_unaccepted_task_return_to_task_review(modules, tmp_path):
    state, _, renderer = modules
    saved, value = accepted(modules, tmp_path)
    task = next(iter(value["creation"]["tasks"].values()))
    saved = state.record_task(tmp_path, task=task["path"], status="draft", source="User withdraws task acceptance")
    payload: dict = {**expansion_payload(modules, saved), "stage": "expansion_results"}
    payload["facts"]["attempts"] = []
    with pytest.raises(ValueError, match="Use expansion_review for already-reviewed results"):
        renderer.render(payload)
    output = renderer.render({**expansion_payload(modules, saved), "stage": "expansion_review"})
    assert "final acceptance requires your approval of these results" not in output
