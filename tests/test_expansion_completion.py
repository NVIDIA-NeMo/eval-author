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
    saved, value = authorized(modules, repo, path=path)
    for key, task in value["creation"]["tasks"].items():
        result = evidence(repo, f".eval-author/.working/results-{key}.json", '{"score": 1}')
        saved = modules[0].record_task(
            repo,
            task=task["path"],
            status="accepted",
            source="User accepts this task after reviewing results",
            evidence=[result["path"]],
        )
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
        "\n\n".join([templates.COMPLETION_HEADING, templates.COMPLETION_ACCEPTED, templates.COMPLETION_FINISHED]) + "\n"
    )
    assert_completion_resume(modules, resumed, expected_finished)
    assert "Would you like" not in expected_finished


@pytest.mark.parametrize("change", ["draft", "task", "evidence", "missing", "agent", "scope", "settings", "rerun"])
def test_incomplete_or_changed_work_cannot_offer_completion_or_mark_complete(modules, tmp_path, change):
    saved, value = accepted(modules, tmp_path)
    task = next(iter(value["creation"]["tasks"].values()))
    if change == "draft":
        saved = modules[0].record_task(tmp_path, task=task["path"], status="draft", source="Not accepted")
    elif change == "task":
        (tmp_path / task["path"] / "instruction.md").write_text("Changed task")
    elif change in {"evidence", "missing"}:
        path = tmp_path / saved["tasks"][task["path"]]["evidence"][0]["path"]
        path.unlink() if change == "missing" else path.write_text("Changed evidence")
    elif change == "agent":
        saved["agent"] = "different-agent"
        modules[0].save(tmp_path, saved)
    elif change == "settings":
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
        value["plan"]["tasks"].append(copy.deepcopy(value["plan"]["tasks"][0]) | {"task": "Another task"})
        value["decision"] = None
        saved = save(modules, tmp_path, saved, value)
    saved = modules[0].initialize(tmp_path, journey="expand")
    assert not modules[0].view(saved)["expansion"]["complete"]
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
    for stage in ("resume", "expansion_continue", "expansion_review"):
        payload = expansion_payload(modules, saved)
        payload["stage"] = stage
        if stage == "resume":
            payload.pop("facts")
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
