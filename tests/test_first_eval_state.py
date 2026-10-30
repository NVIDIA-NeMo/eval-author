# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creation approvals, current evidence, and native run selection stay independent."""

import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("journey_state"), importlib.import_module("first_eval_state")


def evidence(repo, path, content="retained native evidence"):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"path": path, "digest": hashlib.sha256(target.read_bytes()).hexdigest()}


def save(modules, repo, value, **changes):
    state, _ = modules
    return state.update(
        repo,
        {"scopes": {"create": {"first_eval": value}}, **changes},
        expected_revision=state.load_state(repo)["revision"],
    )


def facts(modules, repo):
    state, creation = modules
    return creation.progress(repo, state.load_state(repo))


def assert_current_record_only(repo, value):
    assert "history" not in value and "history_head" not in value
    assert not (repo / ".eval-author/.working/history").exists()
    assert not (repo / ".eval-author/.working/archives").exists()


def start(modules, repo, provider="harbor", examples=None):
    state, _ = modules
    state.initialize(repo, agent="support", journey="create", setup=provider)
    state.update(
        repo, {"prerequisites": {key: {"status": "confirmed", "source": "user reply"} for key in state.PREREQUISITES}}
    )
    (repo / "ETHOS.md").write_text("# Support agent behavior\n")
    state.confirm_ethos(
        repo, reviewed_digest=state.ethos_digest(repo), source="Current intent approval", validation="valid"
    )
    value = {
        "provider": provider,
        "ethos_digest": state.ethos_digest(repo),
        "plan": {
            "tasks": examples
            or [{"id": "refund", "example_ids": ["refund-1"]}, {"id": "policy", "example_ids": ["policy-1"]}],
            "trace_choice": "none",
            "presentation": {"coverage_note": "Refunds and policy refusals; other regions untested"},
        },
        "first_task": "refund",
        "plan_approval": None,
        "tasks": {},
        "selection": None,
        "runs": [],
        "run_review": None,
        "audit_invitation": None,
    }
    save(modules, repo, value)
    return value


def approve(modules, repo, value, *, setup=True):
    digest = facts(modules, repo)["plan_digest"]
    value["plan_approval"] = {"digest": digest, "source": "User approves this task design"}
    approved_state = save(modules, repo, value)
    if setup and facts(modules, repo)["scope_approved"]:
        value["validation_setup"] = {
            "plan_digest": digest,
            "mode": "deterministic",
            "requirements": ["Local correct-result and incorrect-result controls; no model requests"],
            "open_questions": [],
            "source": "Inspected the agreed deterministic grading prerequisites",
        }
        return save(modules, repo, value)
    return approved_state


def test_retargeting_same_content_ethos_does_not_reuse_creation_approval(modules, tmp_path):
    state, creation = modules
    value = start(modules, tmp_path)
    approved = approve(modules, tmp_path, value)
    relative = "agents/support/ETHOS.md"
    document = tmp_path / relative
    document.parent.mkdir(parents=True)
    document.write_bytes((tmp_path / "ETHOS.md").read_bytes())
    selected = state.update(tmp_path, {"ethos": {"path": relative}}, expected_revision=approved["revision"])
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path, relative),
        source="User confirms selected agent intent",
        validation="Selected document passed validation",
        ethos_path=relative,
        expected_revision=selected["revision"],
    )
    assert confirmed["scopes"]["create"]["first_eval"] == approved["scopes"]["create"]["first_eval"]
    progress = creation.progress(tmp_path, confirmed)
    assert not progress["scope_approved"]
    assert confirmed["scopes"]["create"]["needs_review"]


def author(modules, repo, value, task_id):
    path = f".eval-author/tasks/{task_id}"
    evidence(repo, f"{path}/instruction.md", f"Request for {task_id}")
    evidence(repo, f"{path}/README.md", "# Task\n\n## Rerun task\n")
    value["tasks"][task_id] = {
        "path": path,
        "sha256": modules[0].task_digest(repo, path),
        "review": None,
        "environment": None,
        "validation": None,
        "limitations": ["Synthetic starting state"],
    }
    return save(modules, repo, value)


def check(modules, repo, value, task_id, field):
    task = value["tasks"][task_id]
    task[field] = {
        "sha256": task["sha256"],
        "status": "passed",
        "source": f"Inspected native {field} check",
        "evidence": [evidence(repo, f".eval-author/.working/{task_id}-{field}.json")],
    }
    return save(modules, repo, value)


def review_first(modules, repo, value):
    task = value["tasks"][value["first_task"]]
    task["review"] = {
        "sha256": task["sha256"],
        "decision": "approved",
        "source": "User reviewed task README and report",
    }
    return save(modules, repo, value)


def ready(modules, repo, provider="harbor", examples=None):
    value = start(modules, repo, provider, examples)
    approve(modules, repo, value)
    author(modules, repo, value, "refund")
    check(modules, repo, value, "refund", "environment")
    check(modules, repo, value, "refund", "validation")
    review_first(modules, repo, value)
    for planned in value["plan"]["tasks"][1:]:
        author(modules, repo, value, planned["id"])
        check(modules, repo, value, planned["id"], "environment")
        check(modules, repo, value, planned["id"], "validation")
    approve_suite(modules, repo, value)
    return value


def select(modules, repo, value, repeats=1):
    value["execution_settings"] = {
        "agent_model": "actual-agent / configured-model",
        "attempts": str(repeats),
        "validation": "Native controls",
        "limits": "180 seconds; no retries; $1 maximum",
        "open_questions": [],
    }
    save(modules, repo, value)
    value["execution_settings_presented"] = {
        "digest": facts(modules, repo)["settings_digest"],
        "source": "Displayed the concrete execution limits",
    }
    save(modules, repo, value)
    value["selection"] = {
        "config": evidence(repo, ".eval-author/run.yaml", "explicit task selection"),
        "cases": [
            {"task_id": task["id"], "example_ids": task["example_ids"], "attempts": repeats}
            for task in value["plan"]["tasks"]
        ],
        "agent": "actual-agent",
        "model": "configured-model",
        "source": "Inspected provider-resolved explicit selection",
        "settings_digest": facts(modules, repo)["settings_digest"],
    }
    if value["provider"] == "gym":
        value["selection"]["inputs"] = [evidence(repo, ".eval-author/selected.jsonl", "complete selected rows")]
        for case in value["selection"]["cases"]:
            case["rows"] = list(range(1, len(case["example_ids"]) + 1))
    save(modules, repo, value)
    value["selection"]["authorization"] = {
        "digest": facts(modules, repo)["selection_digest"],
        "source": "User authorizes this exact bounded run",
    }
    save(modules, repo, value)


def run(modules, repo, value, run_id="baseline"):
    attempts = []
    for case in value["selection"]["cases"]:
        units = [[example] for example in case["example_ids"]] if value["provider"] == "gym" else [case["example_ids"]]
        for examples in units:
            for repeat in range(case["attempts"]):
                attempt_id = f"{case['task_id']}-{examples[0]}-{repeat}"
                attempts.append(
                    {
                        "id": attempt_id,
                        "native_id": f"{run_id}/{attempt_id}",
                        "task_id": case["task_id"],
                        "example_ids": examples,
                        "status": "completed",
                        "rewards": {"reward": 0},
                        "evidence": [evidence(repo, f".eval-author/jobs/{run_id}/{attempt_id}/result.json")],
                    }
                )
    value["runs"].append(
        {
            "id": run_id,
            "role": "agent",
            "status": "completed",
            "selection_digest": facts(modules, repo)["selection_digest"],
            "task_revisions": {key: item["sha256"] for key, item in value["tasks"].items()},
            "source": "Inspected native target-agent job and attempts",
            "evidence": [evidence(repo, f".eval-author/jobs/{run_id}/result.json")],
            "attempts": attempts,
        }
    )
    save(modules, repo, value)


def review_run(modules, repo, value, run_id="baseline"):
    native = next(item for item in value["runs"] if item["id"] == run_id)
    attempt = native["attempts"][0]
    value["selected_run_ids"] = [run_id]
    summary = {
        **evidence(repo, f".eval-author/reviews/{run_id}.md", f"Run {run_id}; attempt {attempt['id']}"),
        "run_id": run_id,
        "attempt_id": attempt["id"],
    }
    value["run_review"] = {
        "run_id": run_id,
        "attempt_id": attempt["id"],
        "digest": modules[1].fingerprint(native),
        "source": "Reviewed this actual native attempt",
        "summary": summary,
        "limitations": ["No native ATIF available"],
    }
    return save(modules, repo, value)


def complete(modules, repo, provider="harbor", examples=None, repeats=1):
    value = ready(modules, repo, provider, examples)
    select(modules, repo, value, repeats)
    run(modules, repo, value)
    review_run(modules, repo, value)
    save(modules, repo, value, journey_status={"create": "complete"})
    return value


def start_build(modules, repo, value):
    value["first_task_build"] = {
        "plan_digest": facts(modules, repo)["plan_digest"],
        "task_id": value["first_task"],
        "active_step": "environment",
        "source": "Started the first task environment",
        "receipts": {},
    }
    return save(modules, repo, value)


def finish_build_step(modules, repo, value, step):
    steps = modules[1].BUILD_STEPS
    build = value["first_task_build"]
    build["receipts"][step] = {
        "source": f"Inspected completed {step} work",
        "evidence": [evidence(repo, f".eval-author/.working/build-{step}.txt", f"Completed {step}")],
    }
    build["source"] = f"Finished {step} and started the next step"
    build["active_step"] = steps[steps.index(step) + 1] if step != steps[-1] else None
    return save(modules, repo, value)


def start_remaining(modules, repo, value):
    progress = facts(modules, repo)
    value["remaining_build"] = {
        "plan_digest": progress["plan_digest"],
        "first_task_sha256": value["tasks"][value["first_task"]]["sha256"],
        "active_step": next(
            (key for key, status in progress["remaining_build"]["steps"].items() if status != "complete"), None
        ),
        "source": "Started remaining tasks after the first-task review",
        "artifacts": None,
    }
    return save(modules, repo, value)


def refresh_remaining_artifacts(modules, repo, value):
    value["remaining_build"]["artifacts"] = {
        "digest": facts(modules, repo)["remaining_build_digest"],
        "source": "Refreshed and inspected the suite report and Markdown mirror",
        "evidence": [
            evidence(repo, ".eval-author/report.html", "Current task report"),
            evidence(repo, ".eval-author/README.md", "Current Markdown mirror"),
        ],
    }
    value["remaining_build"]["active_step"] = None
    return save(modules, repo, value)


def present_suite(modules, repo, value):
    value["suite_review_presented"] = {
        "digest": facts(modules, repo)["suite_digest"],
        "source": "Displayed all current tasks with report and README links",
    }
    return save(modules, repo, value)


def approve_suite(modules, repo, value):
    if not facts(modules, repo)["remaining_build"]["current"]:
        start_remaining(modules, repo, value)
    refresh_remaining_artifacts(modules, repo, value)
    present_suite(modules, repo, value)
    value["suite_approval"] = {
        "digest": facts(modules, repo)["suite_digest"],
        "source": "User approved all current evaluation tasks",
    }
    return save(modules, repo, value)


@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("validation", ["missing", "failed", "stale"])
def test_full_suite_review_requires_validation_before_later_execution_settings(modules, tmp_path, provider, validation):
    value = start(modules, tmp_path, provider)
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    check(modules, tmp_path, value, "refund", "validation")
    review_first(modules, tmp_path, value)
    author(modules, tmp_path, value, "policy")
    check(modules, tmp_path, value, "policy", "environment")
    if validation != "missing":
        check(modules, tmp_path, value, "policy", "validation")
        if validation == "failed":
            value["tasks"]["policy"]["validation"]["status"] = "failed"
            save(modules, tmp_path, value)
        else:
            (tmp_path / value["tasks"]["policy"]["validation"]["evidence"][0]["path"]).unlink()
    start_remaining(modules, tmp_path, value)
    refresh_remaining_artifacts(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert not progress["suite_review_ready"] and not progress["suite_approved"]
    assert progress["next_stage"] == "remaining_progress"
    assert progress["remaining_build"]["counts"]["validation"] == {"completed": 1, "total": 2}
    with pytest.raises(ValueError, match="before recording review"):
        present_suite(modules, tmp_path, value)
    value.pop("suite_review_presented")
    value["execution_settings_presented"] = {
        "digest": progress["settings_digest"],
        "source": "Premature run settings before all-task approval",
    }
    with pytest.raises(ValueError, match="Approve the full suite"):
        save(modules, tmp_path, value)
    value.pop("execution_settings_presented")
    check(modules, tmp_path, value, "policy", "validation")
    approve_suite(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["suite_approved"]
    assert progress["next_stage"] == "execution_settings"
    assert not progress["execution_authorized"]
    assert all(task["validated"] for task in progress["tasks"].values())
    assert not any(task["executed"] for task in progress["tasks"].values())


@pytest.mark.parametrize("source", ["design", "first", "presentation"])
def test_suite_approval_requires_its_actual_answer_after_a_separately_saved_review(modules, tmp_path, source):
    value = ready(modules, tmp_path)
    value.pop("suite_approval")
    value.pop("suite_review_presented")
    save(modules, tmp_path, value)
    value["suite_approval"] = {"digest": facts(modules, tmp_path)["suite_digest"], "source": "Unpresented approval"}
    with pytest.raises(ValueError, match="Present the current full suite"):
        save(modules, tmp_path, value)
    value["suite_review_presented"] = {
        "digest": facts(modules, tmp_path)["suite_digest"],
        "source": "Review and answer in one unsaved update",
    }
    with pytest.raises(ValueError, match="Present the current full suite"):
        save(modules, tmp_path, value)
    value.pop("suite_approval")
    present_suite(modules, tmp_path, value)
    sources = {
        "design": value["plan_approval"]["source"],
        "first": value["tasks"]["refund"]["review"]["source"],
        "presentation": value["suite_review_presented"]["source"],
    }
    value["suite_approval"] = {"digest": facts(modules, tmp_path)["suite_digest"], "source": sources[source]}
    with pytest.raises(ValueError, match="actual suite approval"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("already_approved", [False, True])
def test_changed_suite_needs_a_separately_saved_current_presentation(modules, tmp_path, provider, already_approved):
    value = ready(modules, tmp_path, provider)
    if not already_approved:
        value.pop("suite_approval")
        save(modules, tmp_path, value)
    state = modules[0].load_state(tmp_path)
    prior = facts(modules, tmp_path)
    before = modules[0].state_path(tmp_path).read_bytes()
    saved_value = state["scopes"]["create"]["first_eval"]
    assert_current_record_only(tmp_path, saved_value)
    assert prior["suite_review_ready"] and prior["suite_review_presented"]
    assert prior["suite_approved"] is already_approved

    def revised_progress():
        candidate = copy.deepcopy(state)
        candidate["scopes"]["create"]["first_eval"] = copy.deepcopy(value)
        return modules[1].derive(candidate, modules[1].observe(tmp_path, candidate))

    value["plan"]["tasks"].pop()
    revised = revised_progress()
    value["plan_approval"] = {"digest": revised["plan_digest"], "source": "User approves the smaller task design"}
    value["validation_setup"]["plan_digest"] = revised["plan_digest"]
    value["remaining_build"]["plan_digest"] = revised["plan_digest"]
    revised = revised_progress()
    value["remaining_build"]["artifacts"]["digest"] = revised["remaining_build_digest"]
    value["suite_review_presented"] = {
        "digest": revised["suite_digest"],
        "source": "Displayed the revised one-task suite",
    }
    value["suite_approval"] = {"digest": revised["suite_digest"], "source": "User approves the revised one-task suite"}
    revised = revised_progress()
    assert revised["suite_digest"] != prior["suite_digest"]
    assert revised["suite_review_ready"] and revised["suite_review_presented"] and revised["suite_approved"]
    with pytest.raises(ValueError, match="Present the current full suite"):
        save(modules, tmp_path, value)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert modules[0].load_state(tmp_path)["scopes"]["create"]["first_eval"] == saved_value
    assert facts(modules, tmp_path)["suite_approved"] is already_approved

    approval = value.pop("suite_approval")
    save(modules, tmp_path, value)
    displayed = facts(modules, tmp_path)
    assert displayed["suite_review_ready"] and displayed["suite_review_presented"] and not displayed["suite_approved"]
    value["suite_approval"] = approval
    saved = save(modules, tmp_path, value)
    retained = saved["scopes"]["create"]["first_eval"]
    assert facts(modules, tmp_path)["suite_approved"]
    assert facts(modules, tmp_path)["suite_digest"] == revised["suite_digest"]
    assert retained["tasks"] == saved_value["tasks"]
    assert retained["plan"] == value["plan"]
    assert_current_record_only(tmp_path, retained)


def test_suite_approval_is_revision_bound_but_survives_validation_and_report_refresh(modules, tmp_path):
    value = ready(modules, tmp_path)
    digest = facts(modules, tmp_path)["suite_digest"]
    value["tasks"]["policy"]["validation"]["status"] = "failed"
    save(modules, tmp_path, value)
    (tmp_path / ".eval-author/report.html").write_text("Later validation results")
    progress = facts(modules, tmp_path)
    assert progress["suite_digest"] == digest
    assert progress["suite_approved"] and not progress["suite_review_ready"]
    assert not progress["tasks"]["policy"]["validated"]
    (tmp_path / ".eval-author/tasks/policy/README.md").write_text("Revised task grading")
    progress = facts(modules, tmp_path)
    assert not progress["suite_approved"]
    assert progress["first_task_approved"]
    assert progress["next_stage"] == "remaining_progress"


def test_suite_review_approval_needs_the_current_report_and_markdown_mirror(modules, tmp_path):
    value = ready(modules, tmp_path)
    value["suite_approval"] = None
    save(modules, tmp_path, value)
    (tmp_path / ".eval-author/README.md").write_text("Changed after the review request")
    value["suite_approval"] = {"digest": facts(modules, tmp_path)["suite_digest"], "source": "New actual task approval"}
    with pytest.raises(ValueError, match="Present the current full suite"):
        save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["suite_approved"]


def test_legacy_execution_evidence_is_inspectable_without_inferring_suite_approval(modules, tmp_path):
    complete(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    value = state["scopes"]["create"]["first_eval"]
    value.pop("suite_approval")
    value.pop("suite_review_presented")
    observed = modules[1].observe(tmp_path, state)
    original = copy.deepcopy(state)
    progress = modules[1].derive(state, observed)
    assert not progress["suite_approved"] and not progress["execution_authorized"]
    assert not progress["execution_settings_presented"]
    assert progress["runs"]["baseline"]["reviewable"] and progress["reviewed_run_current"]
    assert not progress["complete"]
    assert state == original


def test_remaining_build_progress_uses_actual_counts_and_retains_resume_without_authority_gain(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    check(modules, tmp_path, value, "refund", "validation")
    review_first(modules, tmp_path, value)
    start_remaining(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["next_stage"] == "remaining_progress"
    assert progress["remaining_build"]["task_ids"] == ["policy"]
    assert progress["remaining_build"]["counts"] == {
        "tasks": {"completed": 0, "total": 1},
        "environment_check": {"completed": 0, "total": 1},
        "validation": {"completed": 1, "total": 2},
    }
    assert progress["remaining_build"]["steps"]["tasks"] == "in_progress"
    saved_bytes = modules[0].state_path(tmp_path).read_bytes()
    assert facts(modules, tmp_path) == progress
    assert modules[0].state_path(tmp_path).read_bytes() == saved_bytes
    author(modules, tmp_path, value, "policy")
    value["remaining_build"]["active_step"] = "environment_check"
    save(modules, tmp_path, value)
    check(modules, tmp_path, value, "policy", "environment")
    check(modules, tmp_path, value, "refund", "validation")
    assert facts(modules, tmp_path)["remaining_build"]["counts"]["validation"] == {"completed": 1, "total": 2}
    check(modules, tmp_path, value, "policy", "validation")
    refresh_remaining_artifacts(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["remaining_build"]["complete"]
    assert progress["remaining_build"]["artifacts_current"]
    assert progress["next_stage"] == "review_suite"
    assert progress["suite_review_ready"] and not progress["suite_approved"]
    assert not progress["execution_authorized"] and not progress["complete"]
    assert not any(task["executed"] for task in progress["tasks"].values())


def test_remaining_build_empty_set_retains_first_task_validation(modules, tmp_path):
    value = start(modules, tmp_path, examples=[{"id": "refund", "example_ids": ["refund-1"]}])
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    check(modules, tmp_path, value, "refund", "validation")
    review_first(modules, tmp_path, value)
    start_remaining(modules, tmp_path, value)
    progress = facts(modules, tmp_path)["remaining_build"]
    assert progress["task_ids"] == []
    assert progress["counts"]["tasks"] == progress["counts"]["environment_check"] == {"completed": 0, "total": 0}
    assert progress["steps"] == {
        "tasks": "complete",
        "environment_check": "complete",
        "validation": "complete",
        "artifacts": "in_progress",
    }
    refresh_remaining_artifacts(modules, tmp_path, value)
    assert facts(modules, tmp_path)["suite_review_ready"]
    assert facts(modules, tmp_path)["next_stage"] == "review_suite"
    assert facts(modules, tmp_path)["remaining_build"]["complete"]


def test_remaining_build_requires_prior_first_task_approval_and_never_creates_it(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    with pytest.raises(ValueError, match="Save first-task approval"):
        start_remaining(modules, tmp_path, value)
    value["tasks"]["refund"]["review"] = {
        "sha256": value["tasks"]["refund"]["sha256"],
        "decision": "approved",
        "source": "Task approval in the same unsaved update",
    }
    with pytest.raises(ValueError, match="Save first-task approval"):
        save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["first_task_approved"]


def test_remaining_build_allows_interleaving_and_run_credentials_do_not_block_local_work(modules, tmp_path):
    value = start(
        modules,
        tmp_path,
        examples=[{"id": key, "example_ids": [f"{key}-1"]} for key in ("refund", "policy", "billing")],
    )
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    check(modules, tmp_path, value, "refund", "validation")
    review_first(modules, tmp_path, value)
    start_remaining(modules, tmp_path, value)
    modules[0].update(
        tmp_path, {"blockers": [{"id": "credentials", "phase": "create", "message": "Agent API key missing"}]}
    )
    author(modules, tmp_path, value, "policy")
    value["remaining_build"]["active_step"] = "environment_check"
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["remaining_build"]["counts"]["tasks"] == {"completed": 1, "total": 2}
    assert progress["remaining_build"]["steps"]["environment_check"] == "in_progress"
    check(modules, tmp_path, value, "policy", "environment")
    author(modules, tmp_path, value, "billing")
    assert facts(modules, tmp_path)["remaining_build"]["counts"]["tasks"]["completed"] == 2
    assert not facts(modules, tmp_path)["execution_authorized"]


def test_remaining_artifacts_bind_validation_and_report_changes_without_restarting_execution(modules, tmp_path):
    value = ready(modules, tmp_path)
    start_remaining(modules, tmp_path, value)
    refresh_remaining_artifacts(modules, tmp_path, value)
    prior_digest = facts(modules, tmp_path)["remaining_build_digest"]
    value["tasks"]["policy"]["validation"]["source"] = "Inspected fresh passing controls"
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["remaining_build_digest"] != prior_digest
    assert not progress["remaining_build"]["artifacts_current"]
    assert progress["next_stage"] == "execution_settings"
    select(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "remaining_progress"
    refresh_remaining_artifacts(modules, tmp_path, value)
    (tmp_path / ".eval-author/report.html").write_text("Later agent results report")
    progress = facts(modules, tmp_path)
    assert not progress["remaining_build"]["artifacts_current"]
    assert progress["next_stage"] == "ready_to_run"
    value["tasks"]["policy"]["validation"]["status"] = "failed"
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["next_stage"] == "remaining_progress"
    assert progress["remaining_build"]["counts"]["validation"]["completed"] == 1


def test_remaining_build_plan_changes_and_first_task_changes_reopen_the_right_stage(modules, tmp_path):
    value = ready(modules, tmp_path)
    start_remaining(modules, tmp_path, value)
    refresh_remaining_artifacts(modules, tmp_path, value)
    value["plan"]["presentation"]["coverage_note"] = "Revised agreement"
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "proposal"
    approve(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert not progress["remaining_build"]["current"]
    assert progress["next_stage"] == "prepare_remaining"
    start_remaining(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "remaining_progress"
    (tmp_path / ".eval-author/tasks/refund/README.md").write_text("Changed first task")
    progress = facts(modules, tmp_path)
    assert not progress["first_task_approved"] and not progress["remaining_build"]["current"]
    assert progress["next_stage"] == "prepare_first"


@pytest.mark.parametrize("missing", [".eval-author/report.html", ".eval-author/README.md"])
def test_remaining_artifact_receipt_requires_both_current_review_files(modules, tmp_path, missing):
    value = ready(modules, tmp_path)
    start_remaining(modules, tmp_path, value)
    refresh_remaining_artifacts(modules, tmp_path, value)
    (tmp_path / missing).unlink()
    assert not facts(modules, tmp_path)["remaining_build"]["artifacts_current"]
    assert facts(modules, tmp_path)["next_stage"] == "execution_settings"
    value["remaining_build"]["artifacts"]["evidence"] = [
        ref for ref in value["remaining_build"]["artifacts"]["evidence"] if ref["path"] != missing
    ]
    with pytest.raises(ValueError, match="actual suite report and its Markdown mirror"):
        save(modules, tmp_path, value)


def test_first_task_build_progress_is_optional_and_starting_does_not_complete_work(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    before = facts(modules, tmp_path)
    assert before["first_task_build"] == {
        "current": False,
        "steps": dict.fromkeys(modules[1].BUILD_STEPS, "pending"),
        "complete": False,
    }
    assert before["next_stage"] == "prepare_first"
    start_build(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["next_stage"] == "build_progress"
    assert progress["first_task_build"]["steps"] == {
        "environment": "in_progress",
        "environment_check": "pending",
        "grading": "pending",
        "artifacts": "pending",
    }
    assert not progress["first_task_build"]["complete"]
    saved = modules[0].load_state(tmp_path)
    original = copy.deepcopy(saved)
    assert modules[1].derive(saved, modules[1].observe(tmp_path, saved)) == progress
    assert saved == original


def test_first_task_build_checkpoints_progress_without_granting_other_claims(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    for index, step in enumerate(modules[1].BUILD_STEPS):
        finish_build_step(modules, tmp_path, value, step)
        progress = facts(modules, tmp_path)
        assert all(
            progress["first_task_build"]["steps"][prior] == "complete" for prior in modules[1].BUILD_STEPS[: index + 1]
        )
        assert not progress["tasks"]["refund"]["environment_proven"]
        assert not progress["tasks"]["refund"]["validated"]
        assert not progress["first_task_approved"]
        assert not progress["execution_authorized"]
        assert not progress["complete"]
    assert progress["first_task_build"]["complete"]
    assert progress["next_stage"] == "build_progress"
    assert value["first_task_build"]["active_step"] is None
    retained = modules[0].load_state(tmp_path)["scopes"]["create"]["first_eval"]
    assert retained["first_task_build"] == value["first_task_build"]
    assert_current_record_only(tmp_path, retained)


def test_first_task_build_retains_scoped_evidence_when_grader_changes(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    finish_build_step(modules, tmp_path, value, "environment")
    finish_build_step(modules, tmp_path, value, "environment_check")
    author(modules, tmp_path, value, "refund")
    evidence(tmp_path, ".eval-author/tasks/refund/verifier.py", "new grader")
    progress = facts(modules, tmp_path)
    assert progress["first_task_build"]["steps"]["environment_check"] == "complete"
    assert not progress["tasks"]["refund"]["created"]
    assert progress["next_stage"] == "build_progress"


def test_changed_or_missing_build_evidence_reopens_affected_and_later_steps(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    for step in modules[1].BUILD_STEPS:
        finish_build_step(modules, tmp_path, value, step)
    (tmp_path / ".eval-author/.working/build-grading.txt").unlink()
    progress = facts(modules, tmp_path)
    assert progress["first_task_build"]["steps"] == {
        "environment": "complete",
        "environment_check": "complete",
        "grading": "pending",
        "artifacts": "pending",
    }
    assert progress["next_stage"] == "build_progress"
    (tmp_path / ".eval-author/.working/build-environment.txt").write_text("changed starting data")
    assert set(facts(modules, tmp_path)["first_task_build"]["steps"].values()) == {"pending"}


def test_replacing_earlier_build_receipt_requires_rebuilding_dependent_checkpoints(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    for step in modules[1].BUILD_STEPS:
        finish_build_step(modules, tmp_path, value, step)
    build = value["first_task_build"]
    build["receipts"]["environment"]["evidence"] = [
        evidence(tmp_path, ".eval-author/.working/repaired-environment.txt", "repaired starting data")
    ]
    with pytest.raises(ValueError, match="Reopen later first-task build steps"):
        save(modules, tmp_path, value)
    build["receipts"] = {"environment": build["receipts"]["environment"]}
    build["active_step"] = "environment_check"
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["first_task_build"]["steps"] == {
        "environment": "complete",
        "environment_check": "in_progress",
        "grading": "pending",
        "artifacts": "pending",
    }


@pytest.mark.parametrize("change", ["plan", "first_task"])
def test_stale_build_design_cannot_reuse_receipts_or_skip_to_old_review(modules, tmp_path, change):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    for step in modules[1].BUILD_STEPS:
        finish_build_step(modules, tmp_path, value, step)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    if change == "plan":
        value["plan"]["presentation"]["coverage_note"] = "Changed intended coverage"
    else:
        value["first_task"] = "policy"
    save(modules, tmp_path, value)
    approve(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert not progress["first_task_build"]["current"]
    assert set(progress["first_task_build"]["steps"].values()) == {"pending"}
    assert progress["next_stage"] == "prepare_first"
    value["first_task_build"]["plan_digest"] = progress["plan_digest"]
    value["first_task_build"]["task_id"] = value["first_task"]
    with pytest.raises(ValueError, match="Reopen first-task build receipts"):
        save(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    assert facts(modules, tmp_path)["first_task_build"]["steps"]["environment"] == "in_progress"


def test_build_receipts_require_saved_approval_and_earlier_saved_checkpoints(modules, tmp_path):
    value = start(modules, tmp_path)
    with pytest.raises(ValueError, match="Save task-design approval"):
        start_build(modules, tmp_path, value)
    value.pop("first_task_build")
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    with pytest.raises(ValueError, match="earlier first-task build steps"):
        finish_build_step(modules, tmp_path, value, "environment_check")
    value["first_task_build"]["receipts"]["environment"] = {
        "source": "Completed environment in the same unsaved update",
        "evidence": [evidence(tmp_path, ".eval-author/.working/environment.txt")],
    }
    with pytest.raises(ValueError, match="earlier first-task build steps"):
        save(modules, tmp_path, value)
    value["first_task_build"]["receipts"] = {}
    value["first_task_build"]["active_step"] = "grading"
    with pytest.raises(ValueError, match="earliest incomplete"):
        save(modules, tmp_path, value)


def test_failed_checks_do_not_complete_build_and_later_report_refresh_preserves_approved_stage(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    start_build(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    value["tasks"]["refund"]["environment"]["status"] = "failed"
    save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["tasks"]["refund"]["environment_proven"]
    assert not facts(modules, tmp_path)["first_task_build"]["complete"]
    for step in modules[1].BUILD_STEPS:
        finish_build_step(modules, tmp_path, value, step)
    check(modules, tmp_path, value, "refund", "environment")
    assert facts(modules, tmp_path)["next_stage"] == "build_progress"
    check(modules, tmp_path, value, "refund", "validation")
    assert facts(modules, tmp_path)["next_stage"] == "review_first"
    review_first(modules, tmp_path, value)
    (tmp_path / ".eval-author/.working/build-artifacts.txt").write_text("refreshed report for later work")
    progress = facts(modules, tmp_path)
    assert not progress["first_task_build"]["complete"]
    assert progress["first_task_approved"]
    assert progress["next_stage"] == "prepare_remaining"


@pytest.mark.parametrize("choice", ["none", {"paths": ["selected-run.json"], "exclusions": ["all other runs"]}])
def test_interrupted_source_selection_resumes_before_proposal_approval(modules, tmp_path, choice):
    value = start(modules, tmp_path)
    value["plan"].pop("trace_choice")
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "traces"
    approve(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["scope_approved"]
    with pytest.raises(ValueError, match="task-design approval"):
        author(modules, tmp_path, value, "refund")
    value["tasks"] = {}
    value["plan"]["trace_choice"] = choice
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "proposal"
    assert not facts(modules, tmp_path)["scope_approved"]
    approve(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "prepare_first"


@pytest.mark.parametrize("plan_choice", [None, "none", {"paths": ["selected-run.json"]}])
def test_saved_source_selection_is_reused_without_implicitly_approving_design(modules, tmp_path, plan_choice):
    value = start(modules, tmp_path)
    value["plan"]["trace_choice"] = plan_choice
    saved = modules[0].update(
        tmp_path,
        {"scopes": {"create": {"trace_choice": "none", "first_eval": value}}},
        expected_revision=modules[0].load_state(tmp_path)["revision"],
    )
    assert modules[1].trace_choice(saved) == (plan_choice or "none")
    assert facts(modules, tmp_path)["next_stage"] == "proposal"
    assert not facts(modules, tmp_path)["scope_approved"]
    approve(modules, tmp_path, value)
    assert facts(modules, tmp_path)["scope_approved"] is (plan_choice is not None)
    if plan_choice is None:
        assert facts(modules, tmp_path)["next_stage"] == "proposal"


def test_source_choice_before_any_proposal_is_read_only_and_does_not_invent_design(modules, tmp_path):
    start(modules, tmp_path)
    saved = modules[0].load_state(tmp_path)
    saved["scopes"]["create"].pop("first_eval")
    original = copy.deepcopy(saved)
    assert modules[1].trace_choice(saved) is None
    assert modules[1].derive(saved, {"tasks": {}, "files": {}}) is None
    assert saved == original
    saved["scopes"]["create"]["trace_choice"] = {"paths": ["selected.json"], "runs": ["run-1"]}
    assert modules[1].trace_choice(saved) == {"paths": ["selected.json"], "runs": ["run-1"]}
    assert modules[1].derive(saved, {"tasks": {}, "files": {}}) is None


@pytest.mark.parametrize("breadth", [{}, {"source": " "}, "user wants many tasks"])
def test_broader_proposal_request_requires_saved_user_provenance(modules, tmp_path, breadth):
    value = start(modules, tmp_path)
    value["plan"]["breadth_request"] = breadth
    with pytest.raises(ValueError, match="provenance"):
        save(modules, tmp_path, value)


def test_saved_scope_and_first_task_reviews_gate_generation_without_execution_settings(modules, tmp_path):
    value = start(modules, tmp_path)
    with pytest.raises(ValueError, match="task-design approval"):
        author(modules, tmp_path, value, "refund")
    value["tasks"] = {}
    approve(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "prepare_first"
    author(modules, tmp_path, value, "refund")
    with pytest.raises(ValueError, match="proven environment"):
        review_first(modules, tmp_path, value)
    value["tasks"]["refund"]["review"] = None
    check(modules, tmp_path, value, "refund", "environment")
    assert facts(modules, tmp_path)["next_stage"] == "prepare_first"
    with pytest.raises(ValueError, match="validation"):
        review_first(modules, tmp_path, value)
    value["tasks"]["refund"]["review"] = None
    check(modules, tmp_path, value, "refund", "validation")
    assert facts(modules, tmp_path)["next_stage"] == "review_first"
    with pytest.raises(ValueError, match="first task before"):
        author(modules, tmp_path, value, "policy")
    del value["tasks"]["policy"]
    review_first(modules, tmp_path, value)
    assert facts(modules, tmp_path)["first_task_approved"]
    author(modules, tmp_path, value, "policy")
    assert not facts(modules, tmp_path)["execution_authorized"]


@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("validation", ["missing", "failed", "stale_task", "stale_evidence", "missing_evidence"])
def test_first_task_approval_requires_saved_current_grader_validation(modules, tmp_path, provider, validation):
    value = start(modules, tmp_path, provider)
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    if validation != "missing":
        check(modules, tmp_path, value, "refund", "validation")
        task = value["tasks"]["refund"]
        if validation == "failed":
            task["validation"]["status"] = "failed"
            save(modules, tmp_path, value)
        elif validation == "stale_task":
            task["validation"]["sha256"] = "b" * 64
            save(modules, tmp_path, value)
        elif validation == "stale_evidence":
            (tmp_path / task["validation"]["evidence"][0]["path"]).write_text("Changed grader control result")
        else:
            (tmp_path / task["validation"]["evidence"][0]["path"]).unlink()
    before = modules[0].state_path(tmp_path).read_bytes()
    assert facts(modules, tmp_path)["next_stage"] == "prepare_first"
    with pytest.raises(ValueError, match="validation"):
        review_first(modules, tmp_path, value)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert not facts(modules, tmp_path)["first_task_approved"]
    value["tasks"]["refund"]["review"] = None
    check(modules, tmp_path, value, "refund", "validation")
    assert facts(modules, tmp_path)["next_stage"] == "review_first"
    review_first(modules, tmp_path, value)
    assert facts(modules, tmp_path)["first_task_approved"]


def test_same_update_grader_validation_does_not_retroactively_authorize_first_task_review(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    author(modules, tmp_path, value, "refund")
    check(modules, tmp_path, value, "refund", "environment")
    task = value["tasks"]["refund"]
    task["validation"] = {
        "sha256": task["sha256"],
        "status": "passed",
        "source": "New passing grader result in the same update",
        "evidence": [evidence(tmp_path, ".eval-author/.working/new-validation.json")],
    }
    with pytest.raises(ValueError, match="validation"):
        review_first(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["first_task_approved"]
    assert not facts(modules, tmp_path)["tasks"]["refund"]["validated"]


@pytest.mark.parametrize("task_id", ["refund", "policy"])
def test_validation_evidence_loss_retains_task_approval_but_requires_revalidation(modules, tmp_path, task_id):
    value = ready(modules, tmp_path)
    first_review = copy.deepcopy(value["tasks"]["refund"]["review"])
    suite_approval = copy.deepcopy(value["suite_approval"])
    (tmp_path / value["tasks"][task_id]["validation"]["evidence"][0]["path"]).unlink()
    progress = facts(modules, tmp_path)
    assert progress["first_task_approved"] and progress["suite_approved"]
    assert not progress["suite_review_ready"] and not progress["tasks"][task_id]["validated"]
    assert not progress["execution_authorized"] and not progress["complete"]
    assert progress["next_stage"] not in {"review_first", "review_suite"}
    check(modules, tmp_path, value, task_id, "validation")
    assert value["tasks"]["refund"]["review"] == first_review
    assert value["suite_approval"] == suite_approval
    assert facts(modules, tmp_path)["first_task_approved"] and facts(modules, tmp_path)["suite_approved"]


@pytest.mark.parametrize("provider", ["harbor", "gym"])
def test_zero_reward_baseline_is_complete_and_show_retains_exact_run_attempt(modules, tmp_path, provider):
    value = complete(modules, tmp_path, provider)
    before = modules[0].state_path(tmp_path).read_bytes()
    view = modules[0].view(modules[0].load_state(tmp_path))
    assert view["first_eval"]["complete"] and view["checklist"][2]["complete"]
    assert modules[1].derive(view["state"], view["first_eval_observations"]) == view["first_eval"]
    original = copy.deepcopy(value["run_review"])
    run(modules, tmp_path, value, "later")
    assert facts(modules, tmp_path)["complete"]
    assert value["run_review"] == original
    assert facts(modules, tmp_path)["suite"]["selected_run_ids"] == ["baseline"]
    assert modules[0].state_path(tmp_path).read_bytes() != before


def test_actual_runs_are_never_selected_implicitly(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    run(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["complete"]
    assert facts(modules, tmp_path)["suite"]["selected_run_ids"] == []
    review_run(modules, tmp_path, value)
    assert facts(modules, tmp_path)["complete"]


@pytest.mark.parametrize("field", ["task", "validation", "environment", "config", "summary", "result"])
def test_changed_or_missing_bytes_reopen_only_affected_claims_without_writing(modules, tmp_path, field):
    value = complete(modules, tmp_path)
    paths = {
        "task": f"{value['tasks']['refund']['path']}/instruction.md",
        "validation": value["tasks"]["refund"]["validation"]["evidence"][0]["path"],
        "environment": value["tasks"]["policy"]["environment"]["evidence"][0]["path"],
        "config": value["selection"]["config"]["path"],
        "summary": value["run_review"]["summary"]["path"],
        "result": value["runs"][0]["attempts"][0]["evidence"][0]["path"],
    }
    (tmp_path / paths[field]).write_text("changed bytes")
    before = modules[0].state_path(tmp_path).read_bytes()
    loaded = modules[0].load_state(tmp_path)
    progress = modules[0].view(loaded)["first_eval"]
    assert not progress["complete"] and loaded["journey_status"]["create"] == "in_progress"
    assert progress["tasks"]["policy"]["validated"]
    if field in {"config", "summary", "result"}:
        assert progress["first_task_approved"]
    assert modules[0].state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "change", ["controls", "infrastructure", "partial", "ungraded", "missing_summary", "wrong_summary_identity"]
)
def test_incomplete_evidence_is_never_an_agent_baseline(modules, tmp_path, change):
    value = complete(modules, tmp_path)
    if change == "controls":
        value["runs"][0]["role"] = "control"
    elif change == "infrastructure":
        value["runs"][0]["status"] = "infrastructure_error"
    elif change == "partial":
        value["runs"][0]["attempts"].pop()
    elif change == "ungraded":
        value["runs"][0]["attempts"][0]["rewards"] = {}
    elif change == "missing_summary":
        value["run_review"].pop("summary")
    else:
        value["run_review"]["summary"]["attempt_id"] = "different-attempt"
    value["run_review"]["digest"] = modules[1].fingerprint(value["runs"][0])
    save(modules, tmp_path, value, journey_status={"create": "in_progress"})
    assert not facts(modules, tmp_path)["complete"]
    with pytest.raises(ValueError, match="completion requires"):
        save(modules, tmp_path, value, journey_status={"create": "complete"})


def test_native_gym_rows_repeats_and_missing_distinct_examples_are_separate(modules, tmp_path):
    examples = [{"id": "refund", "example_ids": ["first", "second"]}]
    value = complete(modules, tmp_path, "gym", examples, repeats=2)
    assert facts(modules, tmp_path)["suite"]["planned_attempts"] == 4
    value["runs"][0]["attempts"][2]["example_ids"] = ["first"]
    save(modules, tmp_path, value, journey_status={"create": "in_progress"})
    assert not facts(modules, tmp_path)["complete"]
    value["selection"]["cases"][0]["rows"] = [1, 1]
    with pytest.raises(ValueError, match="Gym requires"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("provider,planned_attempts", [("harbor", 4), ("gym", 6)])
def test_example_ids_are_scoped_to_tasks_across_repeated_attempts(modules, tmp_path, provider, planned_attempts):
    examples = [
        {"id": "refund", "example_ids": ["example-1", "example-2"]},
        {"id": "policy", "example_ids": ["example-1"]},
    ]
    value = ready(modules, tmp_path, provider, examples)
    select(modules, tmp_path, value, repeats=2)
    suite = facts(modules, tmp_path)["suite"]
    assert suite["planned_examples"] == 3
    assert suite["planned_attempts"] == planned_attempts
    assert suite["executed_examples"] == suite["completed_attempts"] == 0

    run(modules, tmp_path, value)
    native = value["runs"][0]
    attempts = native["attempts"]
    value["selected_run_ids"] = [native["id"]]
    native["attempts"] = [attempt for attempt in attempts if attempt["task_id"] == "refund"]
    save(modules, tmp_path, value)
    suite = facts(modules, tmp_path)["suite"]
    assert suite["planned_examples"] == 3
    assert suite["executed_examples"] == 2
    assert suite["completed_attempts"] == planned_attempts - 2
    assert not suite["complete"]

    native["attempts"].append(next(attempt for attempt in attempts if attempt["task_id"] == "policy"))
    save(modules, tmp_path, value)
    suite = facts(modules, tmp_path)["suite"]
    assert suite["executed_examples"] == suite["planned_examples"] == 3
    assert suite["completed_attempts"] == planned_attempts - 1
    assert not suite["complete"]

    native["attempts"] = attempts
    save(modules, tmp_path, value)
    suite = facts(modules, tmp_path)["suite"]
    assert suite["executed_examples"] == suite["planned_examples"] == 3
    assert suite["completed_attempts"] == planned_attempts
    assert suite["complete"]


def test_settings_and_selection_changes_reopen_execution_without_reapproving_design(modules, tmp_path):
    value = complete(modules, tmp_path)
    prior = facts(modules, tmp_path)
    value["execution_settings"]["limits"] = "90 seconds and $0.50 maximum"
    save(modules, tmp_path, value, journey_status={"create": "in_progress"})
    current = facts(modules, tmp_path)
    assert current["scope_approved"] and current["first_task_approved"]
    assert current["plan_digest"] == prior["plan_digest"]
    assert not current["selection_current"] and not current["execution_authorized"]
    assert current["next_stage"] == "execution_settings"
    value["selection"]["cases"].append({"task_id": "unrelated", "example_ids": ["other"], "attempts": 1})
    save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["selection_current"]


def test_proposal_sources_and_first_task_revisions_invalidate_their_approvals(modules, tmp_path):
    value = ready(modules, tmp_path)
    value["plan"]["trace_choice"] = {"paths": ["selected-run.json"], "exclusions": ["other runs"]}
    save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["scope_approved"]
    approve(modules, tmp_path, value)
    (tmp_path / value["tasks"]["refund"]["path"] / "instruction.md").write_text("Changed behavior")
    assert not facts(modules, tmp_path)["first_task_approved"]


def test_derive_is_pure_and_observations_cannot_override_approval(modules, tmp_path, monkeypatch):
    value = complete(modules, tmp_path)
    state, helper = modules
    saved = state.load_state(tmp_path)
    observed = helper.observe(tmp_path, saved)
    expected = helper.derive(saved, observed)
    monkeypatch.setattr(state, "task_digest", lambda *_: pytest.fail("derive read a task"))
    monkeypatch.setattr(state, "_file_digest", lambda *_: pytest.fail("derive read evidence"))
    assert helper.derive(saved, observed) == expected
    assert not helper.derive(saved, {"tasks": {}, "files": {}})["complete"]
    with pytest.raises(ValueError, match="approval overrides"):
        helper.derive(saved, {**observed, "scope_approved": True})
    saved["scopes"]["create"]["first_eval"]["plan_approval"] = None
    assert not helper.derive(saved, observed)["scope_approved"]
    assert value["plan_approval"] is not None


def test_observe_never_searches_selected_trace_paths_plan_or_history(modules, tmp_path, monkeypatch):
    value = ready(modules, tmp_path)
    value["plan"]["trace_choice"] = {"paths": ["not-inspected.json"]}
    save(modules, tmp_path, value)
    read = []
    original = modules[0]._file_digest
    monkeypatch.setattr(
        modules[0],
        "_file_digest",
        lambda path, limit: read.append(str(path.relative_to(tmp_path))) or original(path, limit),
    )
    modules[1].observe(tmp_path, modules[0].load_state(tmp_path))
    assert read and "not-inspected.json" not in read


def test_state_only_resume_never_observes_creation_or_infers_fresh_evidence(modules, tmp_path, monkeypatch):
    complete(modules, tmp_path)
    before = modules[0].state_path(tmp_path).read_bytes()
    monkeypatch.setattr(modules[1], "observe", lambda *_: pytest.fail("state-only inspected creation"))
    loaded = modules[0].load_state(tmp_path, state_only=True)
    view = modules[0].view(loaded, state_only=True)
    assert view["first_eval"] is None and view["first_eval_observations"] is None
    assert not view["checklist"][2]["complete"]
    assert modules[0].state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("decision", ["pending", "accepted", "declined"])
def test_audit_decision_preserves_complete_creation_and_missing_audit_evidence_blocks_only_audit(
    modules, tmp_path, decision
):
    value = complete(modules, tmp_path)
    value["audit_invitation"] = {"status": decision, "source": "Actual user audit decision"}
    saved = save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["complete"]
    assert saved["next_handoff"] == ("eval-author-audit" if decision == "accepted" else None)
    if decision == "accepted":
        modules[0].initialize(tmp_path, journey="audit", state_only=True)
        audit = modules[0].update(
            tmp_path, {"blockers": [{"id": "data", "phase": "audit", "message": "No audit traces selected"}]}
        )
        for field in ("ethos", "journey_status"):
            assert audit[field] == saved[field]
        assert audit["scopes"] == {
            **saved["scopes"],
            "create": {
                **saved["scopes"]["create"],
                "audit_entry": {
                    "agent": saved["agent"],
                    "revision": saved["revision"],
                    "pending_question": saved["pending_question"],
                    "next_handoff": saved["next_handoff"],
                },
            },
        }
        assert facts(modules, tmp_path)["complete"]
    else:
        assert saved["journey"] == "create"
    assert facts(modules, tmp_path)["audit_decision"] == decision


def forbid_audit_creation_reads(modules, repo, monkeypatch):
    state, creation = modules
    metadata = {repo / "ETHOS.md", state.state_path(repo), repo / ".eval-author/.working/journey-state.lock"}
    artifacts = {path for path in repo.rglob("*") if path.is_file()} - metadata

    def fail(*args, **kwargs):
        pytest.fail("Audit metadata reconciliation must not inspect creation or trace evidence")

    for name in ("observe", "progress", "transition"):
        monkeypatch.setattr(creation, name, fail)
    for name in ("task_lifecycle", "task_digest"):
        monkeypatch.setattr(state, name, fail)
    file_digest = state._file_digest

    def guard_digest(path, remaining):
        if path != repo / "ETHOS.md":
            fail()
        return file_digest(path, remaining)

    monkeypatch.setattr(state, "_file_digest", guard_digest)
    for name in ("open", "stat", "lstat"):
        original = getattr(Path, name)

        def guard(path, *args, _original=original, **kwargs):
            if path in artifacts:
                fail()
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guard)
    for name in ("glob", "rglob", "iterdir"):
        original = getattr(Path, name)

        def guard_scan(path, *args, _original=original, **kwargs):
            if path == repo or path.is_relative_to(repo):
                fail()
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guard_scan)


@pytest.mark.parametrize("selection", ["candidates", "selected_subset"])
@pytest.mark.parametrize("ethos_change", [None, "edit", "missing"])
@pytest.mark.parametrize("origin", ["create", "expand"])
def test_normal_audit_metadata_operations_preserve_creation_without_reading_its_evidence(
    modules, tmp_path, monkeypatch, selection, ethos_change, origin
):
    complete(modules, tmp_path)
    state, _ = modules
    state.initialize(tmp_path, journey=origin, state_only=True)
    original = state.load_state(tmp_path)
    candidates = [evidence(tmp_path, f"runs/{name}.json")["path"] for name in ("selected", "other")]
    if ethos_change == "edit":
        (tmp_path / "ETHOS.md").write_text("# Revised behavior\n")
    elif ethos_change == "missing":
        (tmp_path / "ETHOS.md").unlink()
    audit_scope = {"trace_candidates": candidates}
    if selection == "selected_subset":
        audit_scope.update(traces=candidates[:1], exclusions=candidates[1:], selection_source="Actual source choice")

    forbid_audit_creation_reads(modules, tmp_path, monkeypatch)
    switched = state.initialize(tmp_path, journey="audit", state_only=True)
    assert switched["scopes"]["create"] == original["scopes"]["create"]
    assert switched["journey_status"]["create"] == "complete"
    assert switched["ethos"] == original["ethos"]
    state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "confirmed", "source": "Current audit prerequisite evidence"}
                for key in ("skills", "python", "data")
            }
        },
        state_only=True,
    )
    reconciled = state.load_state(tmp_path)
    question = reconciled["pending_question"] if ethos_change else "Which evaluation suites should this audit include?"
    updated = state.update(
        tmp_path,
        {"scopes": {"audit": audit_scope}, "pending_question": question},
        expected_revision=reconciled["revision"],
    )
    before = state.state_path(tmp_path).read_bytes()
    output = state.view(state.load_state(tmp_path))
    assert output["first_eval"] is None and output["first_eval_observations"] is None
    assert output["task_readiness"] is None and output["artifact_checks_deferred"] is False
    assert updated["scopes"]["create"]["first_eval"] == original["scopes"]["create"]["first_eval"]
    assert updated["scopes"]["audit"] == audit_scope and updated["pending_question"] == question
    if ethos_change:
        assert updated["ethos"]["stage"] == "review" and updated["ethos"]["confirmed_digest"] is None
        assert updated["next_handoff"] == "ethos-orchestrate"
        assert question == "ETHOS.md changed or is unavailable; review its intended behavior again."
        assert not updated["journey_status"]
    else:
        assert updated["ethos"] == original["ethos"]
        assert updated["journey_status"]["create"] == "complete"
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("changed", ["task", "run_evidence"])
def test_leaving_audit_reconciles_stale_creation_evidence(modules, tmp_path, journey, changed):
    complete(modules, tmp_path)
    state, _ = modules
    value = state.load_state(tmp_path, state_only=True)["scopes"]["create"]["first_eval"]
    state.initialize(tmp_path, journey="audit", state_only=True)
    path = (
        tmp_path / value["tasks"]["refund"]["path"] / "instruction.md"
        if changed == "task"
        else tmp_path / value["runs"][0]["evidence"][0]["path"]
    )
    path.write_text("Changed after the authoring review\n")
    audit = state.load_state(tmp_path)
    assert audit["journey_status"]["create"] == "complete"
    assert audit["scopes"]["create"]["first_eval"] == value
    assert state.view(audit)["first_eval"] is None

    state.initialize(tmp_path, journey=journey, state_only=True)
    current = state.load_state(tmp_path)
    output = state.view(current)
    assert current["journey_status"]["create"] == "in_progress"
    assert output["first_eval"] is not None and not output["first_eval"]["complete"]
    assert output["first_eval_observations"] is not None and output["task_readiness"] is not None
    assert current["scopes"]["create"]["first_eval"] == value


@pytest.mark.parametrize("change", ["typed_scope", "clear_scope", "completion"])
def test_active_audit_cannot_modify_creation_scope_or_completion(modules, tmp_path, monkeypatch, change):
    value = complete(modules, tmp_path)
    state, _ = modules
    audit = state.initialize(tmp_path, journey="audit", state_only=True)
    patch = {
        "typed_scope": {"scopes": {"create": {"first_eval": value}}},
        "clear_scope": {"scopes": {"create": {}}},
        "completion": {"journey_status": {"create": "complete"}},
    }[change]
    before = state.state_path(tmp_path).read_bytes()
    forbid_audit_creation_reads(modules, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="[Aa]uthoring|[Cc]reation"):
        state.update(tmp_path, patch, expected_revision=audit["revision"])
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path, state_only=True) == audit


def test_revision_checks_and_current_ethos_review_cannot_be_bypassed(modules, tmp_path):
    value = ready(modules, tmp_path)
    saved = modules[0].load_state(tmp_path)
    with pytest.raises(ValueError, match="expected-revision"):
        modules[0].update(tmp_path, {"scopes": {"create": {"first_eval": value}}})
    with pytest.raises(ValueError, match="State changed"):
        modules[0].update(tmp_path, {"scopes": {"create": {"first_eval": value}}}, expected_revision=0)
    modules[0].update(tmp_path, {"ethos": {"stage": "review"}})
    assert modules[0].load_state(tmp_path)["ethos"]["confirmed_digest"] == saved["ethos"]["confirmed_digest"]
    assert not facts(modules, tmp_path)["scope_approved"]
    value["plan"]["breadth"] = "Changed design"
    with pytest.raises(ValueError, match="Confirm ETHOS.md"):
        save(modules, tmp_path, value)
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])


@pytest.mark.parametrize("execution_path", ["harbor", "gym_harbor"])
def test_null_bridge_saves_and_observes_as_absent_without_mutating_state(modules, tmp_path, execution_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    value["selection"].pop("authorization")
    value["selection"]["bridge"] = None
    if execution_path == "gym_harbor":
        value["plan"]["execution_path"] = execution_path
    save(modules, tmp_path, value)
    if execution_path == "gym_harbor":
        approve(modules, tmp_path, value)
        approve_suite(modules, tmp_path, value)

    state, creation = modules
    saved = state.load_state(tmp_path, state_only=True)
    before = state.state_path(tmp_path).read_bytes()
    observed = creation.observe(tmp_path, saved)
    current = creation.derive(saved, observed)

    assert saved["scopes"]["create"]["first_eval"]["selection"]["bridge"] is None
    assert current["scope_approved"] and current["suite_approved"]
    assert not current["bridge_current"]
    assert current["selection_current"] == (execution_path == "harbor")
    assert not current["execution_authorized"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_gym_harbor_requires_current_bridge_evidence_not_just_converted_tasks(modules, tmp_path):
    value = ready(modules, tmp_path)
    value["plan"]["execution_path"] = "gym_harbor"
    save(modules, tmp_path, value)
    approve(modules, tmp_path, value)
    approve_suite(modules, tmp_path, value)
    with pytest.raises(ValueError, match="execution authorization"):
        select(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["bridge_current"]
    assert not facts(modules, tmp_path)["selection_current"]
    value["selection"].pop("authorization")
    bridge = evidence(
        tmp_path, ".eval-author/.working/bridge-proof.json", "Selected agent executed through configured bridge"
    )
    value["selection"]["bridge"] = {"source": "Inspected configured bridge's native proof", "evidence": [bridge]}
    save(modules, tmp_path, value)
    value["selection"]["authorization"] = {
        "digest": facts(modules, tmp_path)["selection_digest"],
        "source": "Approved bounded bridge execution",
    }
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["bridge_current"] and facts(modules, tmp_path)["execution_authorized"]
    (tmp_path / bridge["path"]).write_text("Changed bridge configuration")
    assert not facts(modules, tmp_path)["selection_current"]


def test_display_and_spending_authority_require_reviewed_task_and_current_receipt(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    value["execution_settings_presented"] = {
        "digest": facts(modules, tmp_path)["settings_digest"],
        "source": "Premature display",
    }
    with pytest.raises(ValueError, match="Approve the full suite"):
        save(modules, tmp_path, value)
    value = complete(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    observed = modules[1].observe(tmp_path, state)
    state["scopes"]["create"]["first_eval"].pop("execution_settings_presented")
    derived = modules[1].derive(state, observed)
    assert not derived["execution_authorized"]
    assert derived["next_stage"] == "execution_settings"


@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("already_authorized", [False, True])
def test_changed_execution_limits_need_a_separately_saved_current_presentation(
    modules, tmp_path, provider, already_authorized
):
    value = ready(modules, tmp_path, provider)
    select(modules, tmp_path, value)
    if not already_authorized:
        value["selection"].pop("authorization")
        save(modules, tmp_path, value)
    state = modules[0].load_state(tmp_path)
    prior = facts(modules, tmp_path)
    before = modules[0].state_path(tmp_path).read_bytes()
    assert prior["execution_settings_presented"]
    assert prior["execution_authorized"] is already_authorized

    value["execution_settings"]["limits"] = "180 seconds; no retries; $100 maximum"
    candidate = copy.deepcopy(state)
    candidate["scopes"]["create"]["first_eval"] = copy.deepcopy(value)
    observed = modules[1].observe(tmp_path, candidate)
    revised = modules[1].derive(candidate, observed)
    value["execution_settings_presented"] = {
        "digest": revised["settings_digest"],
        "source": "Displayed the revised $100 limit",
    }
    value["selection"]["settings_digest"] = revised["settings_digest"]
    candidate["scopes"]["create"]["first_eval"] = copy.deepcopy(value)
    value["selection"]["authorization"] = {
        "digest": modules[1].derive(candidate, observed)["selection_digest"],
        "source": "User approves the revised $100 limit",
    }
    with pytest.raises(ValueError, match="Display current settings"):
        save(modules, tmp_path, value)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert facts(modules, tmp_path)["settings_digest"] == prior["settings_digest"]
    assert facts(modules, tmp_path)["execution_authorized"] is already_authorized

    authorization = value["selection"].pop("authorization")
    save(modules, tmp_path, value)
    displayed = facts(modules, tmp_path)
    assert displayed["execution_settings_presented"] and not displayed["execution_authorized"]
    value["selection"]["authorization"] = authorization
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["execution_authorized"]


@pytest.mark.parametrize(
    "invalid", [{}, {"agent_model": "unknown"}, {"open_questions": "none"}, {"open_questions": [None]}, {"limits": " "}]
)
def test_explicit_execution_settings_must_be_complete_and_typed(modules, tmp_path, invalid):
    value = ready(modules, tmp_path)
    value["execution_settings"] = {
        "agent_model": "actual-agent / model",
        "attempts": "1 per task",
        "validation": "Native controls",
        "limits": "$1 maximum",
        "open_questions": [],
    }
    if invalid:
        value["execution_settings"].update(invalid)
    else:
        value["execution_settings"] = {}
    if invalid == {"agent_model": "unknown"}:
        value["execution_settings"].pop("attempts")
    with pytest.raises(ValueError, match="Execution settings require"):
        save(modules, tmp_path, value)


def test_open_execution_questions_cannot_authorize_runs_and_fallback_fields_stay_supported(modules, tmp_path):
    value = complete(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    observed = modules[1].observe(tmp_path, state)
    current = modules[1].derive(state, observed)
    assert current["execution_settings_resolved"] and current["execution_authorized"]
    value["execution_settings"]["open_questions"] = ["Which model should execute the selected tasks?"]
    save(modules, tmp_path, value, journey_status={"create": "in_progress"})
    value["execution_settings_presented"] = {
        "digest": facts(modules, tmp_path)["settings_digest"],
        "source": "Displayed unresolved execution choices",
    }
    save(modules, tmp_path, value)
    value["selection"]["settings_digest"] = facts(modules, tmp_path)["settings_digest"]
    save(modules, tmp_path, value)
    value["selection"]["authorization"] = {
        "digest": facts(modules, tmp_path)["selection_digest"],
        "source": "Attempt to authorize unresolved choices",
    }
    with pytest.raises(ValueError, match="execution authorization"):
        save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["execution_settings_resolved"]
    assert not facts(modules, tmp_path)["execution_authorized"]

    legacy = state["scopes"]["create"]["first_eval"]
    settings = legacy.pop("execution_settings")
    assert not modules[1].derive(state, observed)["execution_settings_resolved"]
    assert not modules[1].derive(state, observed)["execution_authorized"]
    legacy["plan"]["presentation"].update(settings)
    assert modules[1].derive(state, observed)["execution_settings_resolved"]


def saved_design_question(modules, repo, value, request="Fresh first-evaluations request"):
    value["saved_design_review"] = {
        "digest": facts(modules, repo)["plan_digest"],
        "decision": "pending",
        "request_source": request,
        "source": None,
    }
    return save(modules, repo, value)


@pytest.mark.parametrize("prepared", [False, True])
def test_saved_design_question_preserves_current_progress_but_stops_automatic_authoring(modules, tmp_path, prepared):
    value = ready(modules, tmp_path) if prepared else start(modules, tmp_path)
    if not prepared:
        approve(modules, tmp_path, value)
    prior = copy.deepcopy(value)
    saved = saved_design_question(modules, tmp_path, value)
    derived = facts(modules, tmp_path)
    assert derived["next_stage"] == "saved_design" and derived["saved_design_review_needed"]
    assert not any(derived[key] for key in ("scope_approved", "first_task_approved", "execution_authorized"))
    retained = saved["scopes"]["create"]["first_eval"]
    assert all(retained[key] == prior[key] for key in ("plan_approval", "tasks", "runs"))
    assert_current_record_only(tmp_path, retained)
    with pytest.raises(ValueError, match="saved-design choice"):
        author(modules, tmp_path, value, "refund")


@pytest.mark.parametrize("already_authorized", [False, True])
def test_continue_saved_design_retains_only_existing_authority_and_does_not_ask_again(
    modules, tmp_path, already_authorized
):
    value = ready(modules, tmp_path)
    if already_authorized:
        select(modules, tmp_path, value)
    prior = facts(modules, tmp_path)
    selection = copy.deepcopy(value["selection"])
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision="continue", source="User chooses to continue the saved design")
    save(modules, tmp_path, value)
    current = facts(modules, tmp_path)
    assert not current["saved_design_review_needed"]
    assert current["next_stage"] == prior["next_stage"] and current["first_task_approved"]
    assert current["execution_authorized"] is already_authorized
    assert value["selection"] == selection
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == current["next_stage"]
    value["saved_design_review"].update(decision="pending", source=None)
    with pytest.raises(ValueError, match="already answered"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("source", ["Fresh first-evaluations request", "Current intent approval", " "])
def test_saved_design_continue_requires_its_own_actual_user_answer(modules, tmp_path, source):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision="continue", source=source)
    with pytest.raises(ValueError, match="actual saved-design answer|nonempty text"):
        save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "saved_design"


def test_saved_design_confirmation_cannot_be_invented_or_removed(modules, tmp_path):
    value = start(modules, tmp_path)
    value["saved_design_review"] = {
        "digest": facts(modules, tmp_path)["plan_digest"],
        "decision": "continue",
        "request_source": "Fresh request",
        "source": "Assumed from ETHOS.md confirmation",
    }
    with pytest.raises(ValueError, match="Ask about the saved design"):
        save(modules, tmp_path, value)
    saved_design_question(modules, tmp_path, value)
    del value["saved_design_review"]
    with pytest.raises(ValueError, match="Retain the saved-design review"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("decision", ["revise", "new"])
def test_saved_design_revise_or_new_preserves_artifacts_runs_and_reopens_the_right_choices(modules, tmp_path, decision):
    value = complete(modules, tmp_path)
    modules[0].update(
        tmp_path,
        {"scopes": {"create": {"trace_choice": "none"}}},
        expected_revision=modules[0].load_state(tmp_path)["revision"],
    )
    prior = copy.deepcopy(value)
    task_bytes = {
        key: (tmp_path / task["path"] / "instruction.md").read_bytes() for key, task in value["tasks"].items()
    }
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision=decision, source=f"User chooses {decision}")
    updated = save(modules, tmp_path, value)
    retained = updated["scopes"]["create"]["first_eval"]
    assert retained["plan_approval"] is None
    assert all(retained[key] == prior[key] for key in ("tasks", "runs", "selection", "run_review"))
    assert all(
        (tmp_path / prior["tasks"][key]["path"] / "instruction.md").read_bytes() == payload
        for key, payload in task_bytes.items()
    )
    assert facts(modules, tmp_path)["next_stage"] == ("traces" if decision == "new" else "proposal")
    assert not facts(modules, tmp_path)["execution_authorized"]
    assert (modules[1].trace_choice(updated) is None) is (decision == "new")
    retained["plan"]["trace_choice"] = "none"
    save(modules, tmp_path, retained)
    approve(modules, tmp_path, retained)
    save(modules, tmp_path, retained)
    assert facts(modules, tmp_path)["scope_approved"]


@pytest.mark.parametrize("decision", ["revise", "new"])
@pytest.mark.parametrize("audit_status", ["pending", "accepted", "declined"])
def test_replacing_saved_design_retires_prior_audit_invitation_and_retains_current_work(
    modules, tmp_path, decision, audit_status
):
    value = complete(modules, tmp_path)
    previous_invitation = {"status": audit_status, "source": "Previous suite audit answer"}
    value["audit_invitation"] = previous_invitation
    save(modules, tmp_path, value)
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision=decision, source=f"User chooses {decision}")
    updated = save(modules, tmp_path, value)
    retained = updated["scopes"]["create"]["first_eval"]
    assert retained["audit_invitation"] is None and updated["next_handoff"] is None
    assert facts(modules, tmp_path)["audit_decision"] is None
    assert retained["tasks"] == value["tasks"] and retained["runs"] == value["runs"]
    assert_current_record_only(tmp_path, retained)
    retained["plan"]["trace_choice"] = "none"
    save(modules, tmp_path, retained)
    approve(modules, tmp_path, retained)
    retained["audit_invitation"] = {"status": "pending"}
    updated = save(modules, tmp_path, retained)
    assert facts(modules, tmp_path)["audit_decision"] == "pending" and updated["next_handoff"] is None


def test_changed_saved_design_must_be_presented_again_before_continue(modules, tmp_path):
    value = ready(modules, tmp_path)
    saved_design_question(modules, tmp_path, value)
    value["plan"]["tasks"][0]["example_ids"] = ["revised-input"]
    save(modules, tmp_path, value)
    value["saved_design_review"].update(decision="continue", source="User chooses the earlier displayed design")
    with pytest.raises(ValueError, match="saved design changed"):
        save(modules, tmp_path, value)
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision="continue", source="User chooses the newly displayed revision")
    save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["scope_approved"]
    assert facts(modules, tmp_path)["next_stage"] == "proposal"


def test_continuing_unapproved_design_does_not_supply_proposal_approval(modules, tmp_path):
    value = start(modules, tmp_path)
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision="continue", source="User chooses the saved design")
    save(modules, tmp_path, value)
    assert not facts(modules, tmp_path)["scope_approved"]
    assert facts(modules, tmp_path)["next_stage"] == "proposal"


def test_design_changes_after_continue_reopen_proposal_without_repeating_resume_question(modules, tmp_path):
    value = ready(modules, tmp_path)
    saved_design_question(modules, tmp_path, value)
    value["saved_design_review"].update(decision="continue", source="User chooses the saved design")
    save(modules, tmp_path, value)
    value["plan"]["tasks"][0]["example_ids"] = ["new-input"]
    save(modules, tmp_path, value)
    current = facts(modules, tmp_path)
    assert not current["saved_design_review_needed"] and not current["scope_approved"]
    assert current["next_stage"] == "proposal"


def native_activity(modules, repo, value, *, run_id="active-baseline", status="running", tasks=None):
    value["run_progress"] = {
        "selection_digest": facts(modules, repo)["selection_digest"] or "0" * 64,
        "run_id": run_id,
        "status": status,
        "active_task_ids": (["refund"] if status == "running" else []) if tasks is None else tasks,
        "source": "Inspected the selected provider job status",
        "evidence": [evidence(repo, f".eval-author/jobs/{run_id}/status-1.json", f"Native job status: {status}")],
    }
    return value["run_progress"]


@pytest.mark.parametrize("status,active", [("running", ["refund"]), ("running", []), ("waiting", [])])
def test_observed_native_activity_resumes_without_creating_terminal_attempts(modules, tmp_path, status, active):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    assert facts(modules, tmp_path)["run_progress"] == {"current": False}
    assert facts(modules, tmp_path)["next_stage"] == "ready_to_run"
    native_activity(modules, tmp_path, value, status=status, tasks=active)
    saved = save(modules, tmp_path, value)
    before = (tmp_path / ".eval-author/.working/journey-state.json").read_bytes()
    progress = facts(modules, tmp_path)
    assert progress["run_progress"] == {
        "current": True,
        "run_id": "active-baseline",
        "status": status,
        "active_task_ids": active,
    }
    assert progress["next_stage"] == "run_progress"
    assert progress["suite"]["planned_attempts"] == 2
    assert progress["suite"]["completed_attempts"] == 0
    assert not progress["complete"] and not saved["scopes"]["create"]["first_eval"]["runs"]
    assert (tmp_path / ".eval-author/.working/journey-state.json").read_bytes() == before
    assert modules[0].load_state(tmp_path)["revision"] == saved["revision"]


@pytest.mark.parametrize("missing", ["selection", "authorization", "suite_approval", "validation"])
def test_native_activity_requires_saved_authorization_and_current_checks(modules, tmp_path, missing):
    value = ready(modules, tmp_path)
    if missing != "selection":
        select(modules, tmp_path, value)
    if missing == "authorization":
        value["selection"].pop("authorization")
    elif missing == "suite_approval":
        value.pop("suite_approval")
    elif missing == "validation":
        value["tasks"]["refund"]["validation"]["status"] = "blocked"
    save(modules, tmp_path, value)
    native_activity(modules, tmp_path, value)
    with pytest.raises(ValueError, match="execution authorization and task checks"):
        save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["run_progress"] == {"current": False}


def test_native_activity_cannot_be_added_with_its_first_authorization(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    authorization = value["selection"].pop("authorization")
    save(modules, tmp_path, value)
    value["selection"]["authorization"] = authorization
    native_activity(modules, tmp_path, value)
    with pytest.raises(ValueError, match="execution authorization and task checks"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "completed"},
        {"active_task_ids": ["refund", "refund"]},
        {"active_task_ids": "refund"},
        {"status": "waiting", "active_task_ids": ["refund"]},
        {"evidence": []},
        {"source": " "},
        {"completed_attempts": 1},
    ],
)
def test_native_activity_accepts_only_observed_job_status_shape(modules, tmp_path, change):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    native_activity(modules, tmp_path, value).update(change)
    with pytest.raises(ValueError):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("tasks", [["unselected"], ["refund", "unselected"]])
def test_native_activity_cannot_identify_unselected_tasks(modules, tmp_path, tasks):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    native_activity(modules, tmp_path, value, tasks=tasks)
    with pytest.raises(ValueError, match="execution authorization and task checks"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("changed", ["task", "selection", "validation", "snapshot"])
def test_changed_runtime_or_revision_evidence_reopens_activity_without_erasing_it(modules, tmp_path, changed):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    activity = native_activity(modules, tmp_path, value)
    save(modules, tmp_path, value)
    paths = {
        "task": ".eval-author/tasks/refund/instruction.md",
        "selection": value["selection"]["config"]["path"],
        "validation": value["tasks"]["refund"]["validation"]["evidence"][0]["path"],
        "snapshot": activity["evidence"][0]["path"],
    }
    (tmp_path / paths[changed]).write_text("Changed after the retained observation")
    progress = facts(modules, tmp_path)
    assert progress["run_progress"] == {"current": False}
    assert progress["next_stage"] != "run_progress"
    saved = save(modules, tmp_path, value)
    assert saved["scopes"]["create"]["first_eval"]["run_progress"] == activity
    assert progress["suite"]["completed_attempts"] == 0


@pytest.mark.parametrize(
    "role,status", [("agent", "completed"), ("agent", "infrastructure_error"), ("control", "completed")]
)
def test_terminal_or_control_native_job_supersedes_live_activity(modules, tmp_path, role, status):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    native_activity(modules, tmp_path, value)
    save(modules, tmp_path, value)
    run(modules, tmp_path, value, run_id="active-baseline")
    value["runs"][0].update(role=role, status=status)
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["run_progress"] == {"current": False}
    assert facts(modules, tmp_path)["next_stage"] == "ready_to_run"
    value["run_progress"]["source"] = "Attempted to treat a terminal or control job as active"
    with pytest.raises(ValueError, match="execution authorization and task checks"):
        save(modules, tmp_path, value)


def test_partial_selected_results_keep_current_job_progress_and_completed_attempts(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    run(modules, tmp_path, value, run_id="partial")
    value["runs"][0]["attempts"] = value["runs"][0]["attempts"][:1]
    value["selected_run_ids"] = ["partial"]
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "results"
    native_activity(modules, tmp_path, value, tasks=["policy"])
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["next_stage"] == "run_progress"
    assert progress["suite"]["completed_attempts"] == 1
    assert not progress["suite"]["complete"] and not progress["complete"]
    value["run_progress"]["status"] = "waiting"
    value["run_progress"]["active_task_ids"] = []
    value["run_progress"]["evidence"] = [
        evidence(tmp_path, ".eval-author/jobs/active-baseline/status-2.json", "Native job awaiting progress")
    ]
    updated = save(modules, tmp_path, value)
    retained = updated["scopes"]["create"]["first_eval"]
    assert retained["run_progress"] == value["run_progress"]
    assert retained["runs"][0]["id"] == "partial"
    assert len(retained["runs"][0]["attempts"]) == 1
    assert_current_record_only(tmp_path, retained)
    assert facts(modules, tmp_path)["run_progress"]["status"] == "waiting"


def test_native_gym_progress_preserves_selected_row_repeat_denominator(modules, tmp_path):
    value = ready(
        modules,
        tmp_path,
        "gym",
        examples=[{"id": "refund", "example_ids": ["refund-1", "refund-2"]}],
    )
    select(modules, tmp_path, value, repeats=2)
    native_activity(modules, tmp_path, value)
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["run_progress"]["current"]
    assert progress["suite"]["planned_attempts"] == 4
    assert progress["suite"]["completed_attempts"] == 0


def test_native_status_updates_replace_one_current_recovery_file_under_revision_check(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    first = copy.deepcopy(native_activity(modules, tmp_path, value))
    original = save(modules, tmp_path, value)
    current_path = first["evidence"][0]["path"]
    value["run_progress"]["evidence"] = [evidence(tmp_path, current_path, "Native job is waiting for capacity")]
    value["run_progress"].update(status="waiting", active_task_ids=[])
    saved = save(modules, tmp_path, value)
    assert saved["scopes"]["create"]["first_eval"]["run_progress"] == value["run_progress"]
    assert facts(modules, tmp_path)["run_progress"]["status"] == "waiting"
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="State changed"):
        modules[0].update(
            tmp_path,
            {"scopes": {"create": {"first_eval": value}}},
            expected_revision=original["revision"],
        )
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])


def active_native_run(modules, repo, value, *, run_id="active-baseline", attempt_tasks=None):
    tasks = ["refund"] if attempt_tasks is None else attempt_tasks
    native = {
        "id": run_id,
        "role": "agent",
        "status": "running",
        "selection_digest": facts(modules, repo)["selection_digest"],
        "task_revisions": {key: task["sha256"] for key, task in value["tasks"].items()},
        "source": "Inspected native target-agent job still running",
        "evidence": [evidence(repo, f".eval-author/jobs/{run_id}/partial-1.json")],
        "attempts": [
            {
                "id": f"{task_id}-1",
                "native_id": f"{run_id}/{task_id}-1",
                "task_id": task_id,
                "example_ids": next(
                    case["example_ids"] for case in value["selection"]["cases"] if case["task_id"] == task_id
                ),
                "status": "completed",
                "rewards": {"reward": 0},
                "evidence": [evidence(repo, f".eval-author/jobs/{run_id}/{task_id}-1/result.json")],
            }
            for task_id in tasks
        ],
    }
    value["runs"].append(native)
    value["selected_run_ids"] = [*(value.get("selected_run_ids") or []), run_id]
    return native


def test_same_native_job_can_report_completed_attempts_while_remaining_work_runs(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    native = active_native_run(modules, tmp_path, value)
    native_activity(modules, tmp_path, value, tasks=["policy"])
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["run_progress"]["current"]
    assert progress["suite"]["selected_run_ids"] == [native["id"]]
    assert progress["suite"]["completed_attempts"] == 1
    assert progress["suite"]["planned_attempts"] == 2
    assert not progress["suite"]["complete"] and progress["next_stage"] == "run_progress"
    native["status"] = "completed"
    native["evidence"] = [evidence(tmp_path, ".eval-author/jobs/active-baseline/final.json")]
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["run_progress"] == {"current": False}
    assert progress["suite"]["completed_attempts"] == 1
    assert progress["next_stage"] == "results" and not progress["complete"]


def test_selected_running_job_prevents_multirun_completion_even_when_every_attempt_is_graded(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    run(modules, tmp_path, value, run_id="finished-part")
    value["runs"][0]["attempts"] = value["runs"][0]["attempts"][:1]
    value["selected_run_ids"] = ["finished-part"]
    active_native_run(modules, tmp_path, value, attempt_tasks=["policy"])
    native_activity(modules, tmp_path, value, tasks=[])
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["suite"]["completed_attempts"] == progress["suite"]["planned_attempts"] == 2
    assert all(task["executed"] for task in progress["tasks"].values())
    assert not progress["suite"]["complete"] and not progress["complete"]
    assert progress["next_stage"] == "run_progress"


@pytest.mark.parametrize("missing", ["authorization", "validation", "selected_run", "evidence", "control"])
def test_running_native_records_require_prior_authority_current_checks_and_selection(modules, tmp_path, missing):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    if missing == "authorization":
        value["selection"].pop("authorization")
    elif missing == "validation":
        value["tasks"]["refund"]["validation"]["status"] = "blocked"
    save(modules, tmp_path, value)
    native = active_native_run(modules, tmp_path, value)
    if missing == "selected_run":
        value["selected_run_ids"] = []
    elif missing == "evidence":
        (tmp_path / native["evidence"][0]["path"]).unlink()
    elif missing == "control":
        native["role"] = "control"
    with pytest.raises(ValueError, match="authorization, task checks, and explicit selection"):
        save(modules, tmp_path, value)


def test_existing_running_record_stays_in_current_inventory_but_cannot_update_without_authority(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    native = active_native_run(modules, tmp_path, value)
    save(modules, tmp_path, value)
    value["selection"].pop("authorization")
    save(modules, tmp_path, value)
    native["source"] = "A new live observation without execution authorization"
    with pytest.raises(ValueError, match="authorization, task checks, and explicit selection"):
        save(modules, tmp_path, value)


def test_terminal_results_can_be_imported_without_live_authority_but_cannot_reopen_as_running(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    value["selection"].pop("authorization")
    save(modules, tmp_path, value)
    run(modules, tmp_path, value)
    assert len(modules[0].load_state(tmp_path)["scopes"]["create"]["first_eval"]["runs"]) == 1
    value["runs"][0]["status"] = "running"
    value["selected_run_ids"] = ["baseline"]
    with pytest.raises(ValueError, match="terminal native job"):
        save(modules, tmp_path, value)


@pytest.mark.parametrize("changed", ["evidence", "selected_run"])
def test_matching_running_native_job_must_remain_current_and_explicitly_selected(modules, tmp_path, changed):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    native = active_native_run(modules, tmp_path, value)
    native_activity(modules, tmp_path, value, tasks=["policy"])
    save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["run_progress"]["current"]
    if changed == "evidence":
        (tmp_path / native["evidence"][0]["path"]).write_text("Native observation changed after the snapshot")
    else:
        value["selected_run_ids"] = []
        save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["run_progress"] == {"current": False}
    assert progress["next_stage"] != "run_progress"


def legacy_history(modules, repo, *, count=3, padding=0):
    value = start(modules, repo)
    state = modules[0].load_state(repo)
    history = []
    for index in range(count):
        previous = copy.deepcopy(value)
        previous["plan"]["presentation"]["coverage_note"] = f"Prior design {index}: " + "x" * padding
        history.append(
            {"revision": state["revision"] + index, "digest": modules[1].fingerprint(previous), "record": previous}
        )
    state["revision"] += count
    state["scopes"]["create"]["first_eval"]["history"] = history
    modules[0].state_path(repo).write_text(json.dumps(state))
    return value, state


def test_legacy_history_reads_and_renders_passively_then_is_discarded_without_archiving(modules, tmp_path):
    value, original = legacy_history(modules, tmp_path)
    path = modules[0].state_path(tmp_path)
    before = path.read_bytes()
    loaded = modules[0].load_state(tmp_path)
    assert modules[0].view(loaded)["state"]["scopes"] == original["scopes"]
    renderer = importlib.import_module("conversation_renderer")
    rendered = renderer.render(
        {
            "stage": "first_eval_traces",
            "state": loaded,
            "revision": loaded["revision"],
            "facts": {"observations": modules[1].observe(tmp_path, loaded)},
        }
    )
    assert rendered and not facts(modules, tmp_path)["scope_approved"]
    assert path.read_bytes() == before
    assert not (tmp_path / ".eval-author/.working/history").exists()
    saved = save(modules, tmp_path, value)
    assert saved["schema_version"] == original["schema_version"] == 1
    assert saved["revision"] == original["revision"] + 1
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])
    updated = save(modules, tmp_path, value, pending_question="Unrelated explicit update")
    assert updated["pending_question"] == "Unrelated explicit update"
    assert_current_record_only(tmp_path, updated["scopes"]["create"]["first_eval"])


def test_legacy_history_near_state_limit_is_discarded_before_saving_current_recovery(modules, tmp_path):
    value, _ = legacy_history(modules, tmp_path, count=18, padding=110000)
    state = modules[0]
    assert state.state_path(tmp_path).stat().st_size > state.MAX_BYTES * 0.9
    value["plan"]["presentation"]["coverage_note"] = "Updated design: " + "y" * 180000
    saved = save(modules, tmp_path, value)
    retained = saved["scopes"]["create"]["first_eval"]
    assert state.state_path(tmp_path).stat().st_size < state.MAX_BYTES // 4
    assert retained["plan"]["presentation"]["coverage_note"] == value["plan"]["presentation"]["coverage_note"]
    assert_current_record_only(tmp_path, retained)


def test_hundreds_of_progress_updates_keep_only_current_recovery_before_terminal_review_and_audit(modules, tmp_path):
    value = ready(modules, tmp_path)
    select(modules, tmp_path, value)
    activity = native_activity(modules, tmp_path, value)
    files = {path for path in tmp_path.rglob("*") if path.is_file()}
    for index in range(250):
        activity["source"] = f"Inspected native status update {index}: " + "Current native status detail. " * 70
        activity["evidence"] = [
            evidence(tmp_path, activity["evidence"][0]["path"], f"Current provider status update {index}")
        ]
        saved = save(modules, tmp_path, value)
    retained = saved["scopes"]["create"]["first_eval"]
    assert_current_record_only(tmp_path, retained)
    assert retained["run_progress"] == activity
    assert {path for path in tmp_path.rglob("*") if path.is_file()} == files
    assert modules[0].state_path(tmp_path).stat().st_size < modules[0].MAX_BYTES // 10
    run(modules, tmp_path, value, run_id="active-baseline")
    review_run(modules, tmp_path, value, run_id="active-baseline")
    save(modules, tmp_path, value, journey_status={"create": "complete"})
    value["audit_invitation"] = {"status": "accepted", "source": "User accepts the audit invitation"}
    saved = save(modules, tmp_path, value)
    assert saved["next_handoff"] == "eval-author-audit" and facts(modules, tmp_path)["complete"]
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])


@pytest.mark.parametrize(
    "metadata",
    [
        {"history": [{"record": {"plan_approval": {"digest": "a" * 64, "source": "Old approval"}}}]},
        {"history_head": {"path": ".eval-author/.working/history/create/missing.json", "digest": "a" * 64}},
        {"history": "Obsolete metadata", "history_head": {"path": "../unselected/unknown.json"}},
    ],
    ids=["inline-record", "missing-snapshot", "opaque-fields"],
)
def test_old_history_metadata_never_reads_previous_files_or_grants_authority(modules, tmp_path, monkeypatch, metadata):
    value = start(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    state["scopes"]["create"]["first_eval"].update(copy.deepcopy(metadata))
    path = modules[0].state_path(tmp_path)
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    original_open = Path.open

    def no_old_files(target, *args, **kwargs):
        assert ".working/history" not in target.as_posix()
        assert "unselected" not in target.parts
        return original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_old_files)
    loaded = modules[0].load_state(tmp_path)
    assert all(loaded["scopes"]["create"]["first_eval"][key] == item for key, item in metadata.items())
    assert not facts(modules, tmp_path)["scope_approved"]
    assert path.read_bytes() == before
    saved = save(modules, tmp_path, value)
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])
    assert not facts(modules, tmp_path)["scope_approved"]


@pytest.mark.parametrize("field", ["history", "history_head"])
def test_updates_discard_supplied_obsolete_history_fields(modules, tmp_path, field):
    value = start(modules, tmp_path)
    value[field] = {"path": ".eval-author/.working/history/create/missing.json", "record": "Ignored legacy input"}
    saved = save(modules, tmp_path, value)
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])


@pytest.mark.parametrize("field", ["history", "history_head"])
def test_unrelated_update_can_drop_old_history_during_ethos_review_without_changing_creation_authority(
    modules, tmp_path, field
):
    value = start(modules, tmp_path)
    state, creation = modules
    previous = state.load_state(tmp_path)
    previous["ethos"]["stage"] = "review"
    previous["scopes"]["create"]["first_eval"][field] = (
        [{"record": copy.deepcopy(value)}]
        if field == "history"
        else {"path": ".eval-author/.working/history/create/unavailable.json", "digest": "a" * 64}
    )
    path = state.state_path(tmp_path)
    path.write_text(json.dumps(previous))
    original = copy.deepcopy(previous)
    before = path.read_bytes()
    proposed = copy.deepcopy(previous)
    proposed["pending_question"] = "Review the current ETHOS.md"
    creation.transition(tmp_path, previous, proposed)
    assert previous == original and path.read_bytes() == before
    assert_current_record_only(tmp_path, proposed["scopes"]["create"]["first_eval"])
    changed = copy.deepcopy(value)
    changed["plan"]["presentation"]["coverage_note"] = "Different task design while intent is unconfirmed"
    with pytest.raises(ValueError, match="Confirm ETHOS.md"):
        save(modules, tmp_path, changed)
    assert path.read_bytes() == before
    updated = state.update(
        tmp_path,
        {"pending_question": proposed["pending_question"]},
        expected_revision=previous["revision"],
    )
    assert updated["ethos"]["stage"] == "review"
    assert updated["pending_question"] == proposed["pending_question"]
    assert_current_record_only(tmp_path, updated["scopes"]["create"]["first_eval"])
    assert not facts(modules, tmp_path)["scope_approved"]


def test_interrupted_current_recovery_save_preserves_state_and_retry_creates_no_history(modules, tmp_path, monkeypatch):
    value, original = legacy_history(modules, tmp_path)
    before = modules[0].state_path(tmp_path).read_bytes()
    value["plan"]["presentation"]["coverage_note"] = "Revised design"
    paths = importlib.import_module("working_paths")
    replace = paths.os.replace

    def failed_replace(*_):
        raise OSError("Interrupted current recovery save")

    monkeypatch.setattr(paths.os, "replace", failed_replace)
    with pytest.raises(OSError, match="Interrupted current recovery"):
        save(modules, tmp_path, value)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert not (tmp_path / ".eval-author/.working/history").exists()
    assert not list(modules[0].state_path(tmp_path).parent.glob(".journey-state.json-*"))
    monkeypatch.setattr(paths.os, "replace", replace)
    saved = save(modules, tmp_path, value)
    assert saved["revision"] == original["revision"] + 1
    assert saved["scopes"]["create"]["first_eval"]["plan"] == value["plan"]
    assert_current_record_only(tmp_path, saved["scopes"]["create"]["first_eval"])


def test_revision_or_lock_conflict_preserves_current_recovery_without_archiving(modules, tmp_path):
    value = start(modules, tmp_path)
    before = modules[0].state_path(tmp_path).read_bytes()
    value["plan"]["presentation"]["coverage_note"] = "Revised design"
    with pytest.raises(ValueError, match="State changed"):
        modules[0].update(tmp_path, {"scopes": {"create": {"first_eval": value}}}, expected_revision=0)
    paths = importlib.import_module("working_paths")
    with paths.working_lock(paths.working_path(tmp_path, "journey-state.lock")), pytest.raises(OSError):
        save(modules, tmp_path, value)
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert_current_record_only(tmp_path, modules[0].load_state(tmp_path)["scopes"]["create"]["first_eval"])


def pending_validation_setup(modules, repo, value):
    value["validation_setup"] = {
        "plan_digest": facts(modules, repo)["plan_digest"],
        "mode": "model",
        "requirements": ["Use the configured judge to score correct and incorrect control responses"],
        "open_questions": ["Which judge configuration should these grading checks use?"],
        "source": "Inspected grading prerequisites after task-design approval",
    }
    return save(modules, repo, value)


@pytest.mark.parametrize("setup_status", ["missing", "pending", "stale"])
@pytest.mark.parametrize("operation", ["build", "task"])
def test_validation_inputs_must_be_saved_before_authoring(modules, tmp_path, setup_status, operation):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value, setup=False)
    if setup_status != "missing":
        pending_validation_setup(modules, tmp_path, value)
        if setup_status == "stale":
            value["validation_setup"]["open_questions"] = []
            save(modules, tmp_path, value)
            value["plan"]["presentation"]["coverage_note"] = "Revised grading scope"
            save(modules, tmp_path, value)
            approve(modules, tmp_path, value, setup=False)
    before = modules[0].load_state(tmp_path)
    progress = facts(modules, tmp_path)
    assert progress["scope_approved"]
    assert not progress["validation_setup_ready"]
    assert progress["validation_setup_current"] is (setup_status == "pending")
    assert progress["next_stage"] == "validation_setup"
    with pytest.raises(ValueError, match="(?i)validation.*setup|setup.*validation"):
        if operation == "build":
            start_build(modules, tmp_path, value)
        else:
            author(modules, tmp_path, value, "refund")
    assert modules[0].load_state(tmp_path) == before


@pytest.mark.parametrize("operation", ["build", "task"])
def test_validation_setup_cannot_be_resolved_in_the_same_update_as_authoring(modules, tmp_path, operation):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value, setup=False)
    pending_validation_setup(modules, tmp_path, value)
    value["validation_setup"]["open_questions"] = []
    value["validation_setup"]["source"] = "User provided the judge and authorized bounded grading checks"
    with pytest.raises(ValueError, match="(?i)validation.*setup|setup.*validation"):
        if operation == "build":
            start_build(modules, tmp_path, value)
        else:
            author(modules, tmp_path, value, "refund")
    assert facts(modules, tmp_path)["next_stage"] == "validation_setup"
    assert not modules[0].load_state(tmp_path)["scopes"]["create"]["first_eval"]["tasks"]


def test_resolving_validation_inputs_preserves_design_and_does_not_authorize_agent_runs(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value, setup=False)
    prior = copy.deepcopy(value["plan_approval"])
    pending_validation_setup(modules, tmp_path, value)
    value["validation_setup"]["requirements"] = [
        "Configured judge: existing local config; access verified without exposing credentials",
        "At most 6 judge calls, 60 seconds per call, $1 maximum, no retries",
    ]
    value["validation_setup"]["open_questions"] = []
    value["validation_setup"]["source"] = "User selected the judge and authorized these grading checks"
    save(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["validation_setup_current"] and progress["validation_setup_ready"]
    assert progress["plan_digest"] == prior["digest"] and value["plan_approval"] == prior
    assert progress["next_stage"] == "prepare_first"
    assert not progress["execution_authorized"] and not progress["execution_settings_presented"]
    assert not progress["suite_approved"] and value["selection"] is None
    author(modules, tmp_path, value, "refund")
    assert facts(modules, tmp_path)["tasks"]["refund"]["created"]


def test_deterministic_validation_setup_skips_an_additional_user_checkpoint(modules, tmp_path):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    progress = facts(modules, tmp_path)
    assert progress["validation_setup_ready"] and progress["next_stage"] == "prepare_first"
    assert value["validation_setup"]["mode"] == "deterministic"
    start_build(modules, tmp_path, value)
    assert facts(modules, tmp_path)["next_stage"] == "build_progress"


@pytest.mark.parametrize("operation", ["task_revision", "validation", "remaining", "receipt"])
def test_pending_validation_setup_blocks_continued_dependent_work(modules, tmp_path, operation):
    value = ready(modules, tmp_path)
    if operation == "receipt":
        start_build(modules, tmp_path, value)
    value["tasks"]["policy"]["validation"] = None
    save(modules, tmp_path, value)
    pending_validation_setup(modules, tmp_path, value)
    before = modules[0].load_state(tmp_path)
    with pytest.raises(ValueError, match="(?i)validation.*setup|setup.*validation"):
        if operation == "task_revision":
            task = value["tasks"]["policy"]
            evidence(tmp_path, task["path"] + "/new-input.txt", "Revised input")
            task["sha256"] = modules[0].task_digest(tmp_path, task["path"])
            save(modules, tmp_path, value)
        elif operation == "validation":
            check(modules, tmp_path, value, "policy", "validation")
        elif operation == "receipt":
            finish_build_step(modules, tmp_path, value, "environment")
        else:
            value["remaining_build"]["source"] = "Started additional task authoring"
            save(modules, tmp_path, value)
    assert modules[0].load_state(tmp_path) == before


@pytest.mark.parametrize("field", ["plan_digest", "mode", "requirements", "open_questions", "source"])
def test_validation_setup_requires_explicit_well_formed_findings(modules, tmp_path, field):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    value["validation_setup"].pop(field)
    with pytest.raises(ValueError):
        modules[1].validate(value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("mode", "infer from prose"),
        ("requirements", []),
        ("requirements", [" "]),
        ("requirements", "Configured judge"),
        ("open_questions", [""]),
        ("open_questions", "Which judge?"),
        ("source", " "),
    ],
)
def test_validation_setup_rejects_implicit_or_malformed_requirements(modules, tmp_path, field, invalid):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    value["validation_setup"][field] = invalid
    with pytest.raises(ValueError):
        modules[1].validate(value)


def test_legacy_completed_creation_does_not_reopen_validation_input_questions(modules, tmp_path):
    complete(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    value = state["scopes"]["create"]["first_eval"]
    observed = modules[1].observe(tmp_path, state)
    before = modules[1].derive(state, observed)
    value.pop("validation_setup")
    after = modules[1].derive(state, observed)
    assert not after["validation_setup_ready"]
    assert after["complete"] and after["next_stage"] == before["next_stage"] == "results"
    assert after["first_task_approved"] and after["suite_approved"]
    assert after["tasks"] == before["tasks"] and after["reviewed_run_current"]


@pytest.mark.parametrize("scope_changes", [False, True])
@pytest.mark.parametrize("operation", ["build", "task", "validation"])
def test_replacing_ready_validation_setup_requires_separate_save_before_dependent_work(
    modules, tmp_path, scope_changes, operation
):
    value = start(modules, tmp_path)
    approve(modules, tmp_path, value)
    if operation == "validation":
        author(modules, tmp_path, value, "refund")
        check(modules, tmp_path, value, "refund", "environment")
    value["validation_setup"]["requirements"] = ["Revised configured grading controls and applicable limits"]
    value["validation_setup"]["source"] = "User confirmed the revised grading configuration"
    if scope_changes:
        value["plan"]["presentation"]["coverage_note"] = "Updated grading design and scope"
        proposal = modules[0].load_state(tmp_path)
        proposal["scopes"]["create"]["first_eval"] = value
        digest = modules[1].derive(proposal, modules[1].observe(tmp_path, proposal))["plan_digest"]
        value["plan_approval"] = {"digest": digest, "source": "User approved the revised proposal"}
        value["validation_setup"]["plan_digest"] = digest
    setup_update = copy.deepcopy(value)
    before = modules[0].load_state(tmp_path)
    with pytest.raises(ValueError, match="(?i)validation.*setup|setup.*validation"):
        if operation == "build":
            start_build(modules, tmp_path, value)
        elif operation == "task":
            author(modules, tmp_path, value, "refund")
        else:
            check(modules, tmp_path, value, "refund", "validation")
    assert modules[0].load_state(tmp_path) == before
    save(modules, tmp_path, setup_update)
    assert facts(modules, tmp_path)["validation_setup_ready"]
    if operation == "build":
        start_build(modules, tmp_path, setup_update)
        assert facts(modules, tmp_path)["first_task_build"]["current"]
    elif operation == "task":
        author(modules, tmp_path, setup_update, "refund")
        assert facts(modules, tmp_path)["tasks"]["refund"]["created"]
    else:
        check(modules, tmp_path, setup_update, "refund", "validation")
        assert facts(modules, tmp_path)["tasks"]["refund"]["validated"]


def legacy_validated_tasks(modules, repo, *, first_build=False):
    value = ready(modules, repo)
    if first_build:
        start_build(modules, repo, value)
        for step in modules[1].BUILD_STEPS[:-1]:
            finish_build_step(modules, repo, value, step)
    state = modules[0].load_state(repo)
    value = state["scopes"]["create"]["first_eval"]
    value.pop("validation_setup")
    value["suite_review_presented"] = None
    value["suite_approval"] = None
    value["remaining_build"]["artifacts"] = None
    modules[0].save(repo, state)
    return value


@pytest.mark.parametrize("build_phase", ["first", "remaining"])
def test_validated_legacy_suite_can_finish_report_bookkeeping_and_review_without_setup(modules, tmp_path, build_phase):
    value = legacy_validated_tasks(modules, tmp_path, first_build=build_phase == "first")
    task_evidence = copy.deepcopy(value["tasks"])
    progress = facts(modules, tmp_path)
    assert not progress["validation_setup_ready"] and not progress["validation_setup_needed"]
    assert progress["first_task_approved"]
    if build_phase == "first":
        finish_build_step(modules, tmp_path, value, "artifacts")
    start_remaining(modules, tmp_path, value)
    refresh_remaining_artifacts(modules, tmp_path, value)
    assert facts(modules, tmp_path)["suite_review_ready"]
    assert facts(modules, tmp_path)["next_stage"] == "review_suite"
    approve_suite(modules, tmp_path, value)
    assert facts(modules, tmp_path)["suite_approved"]
    assert facts(modules, tmp_path)["next_stage"] == "execution_settings"
    assert value["tasks"] == task_evidence and "validation_setup" not in value


@pytest.mark.parametrize("operation", ["task_revision", "validation"])
def test_validated_legacy_suite_still_needs_setup_before_new_bytes_or_grading_checks(modules, tmp_path, operation):
    value = legacy_validated_tasks(modules, tmp_path)
    assert not facts(modules, tmp_path)["validation_setup_needed"]
    before = modules[0].load_state(tmp_path)
    with pytest.raises(ValueError, match="(?i)validation.*setup|setup.*validation"):
        if operation == "task_revision":
            task = value["tasks"]["policy"]
            evidence(tmp_path, task["path"] + "/changed-input.txt", "Changed task input")
            task["sha256"] = modules[0].task_digest(tmp_path, task["path"])
            save(modules, tmp_path, value)
        else:
            value["tasks"]["policy"]["validation"]["source"] = "New model grading controls"
            save(modules, tmp_path, value)
    assert modules[0].load_state(tmp_path) == before


@pytest.mark.parametrize("operation", ["nonartifact_receipt", "new_build", "remaining_authoring"])
def test_legacy_reporting_exemption_does_not_allow_restarting_task_construction(modules, tmp_path, operation):
    value = legacy_validated_tasks(modules, tmp_path, first_build=operation == "nonartifact_receipt")
    before = modules[0].load_state(tmp_path)
    with pytest.raises(ValueError, match="(?i)validation.*setup|setup.*validation"):
        if operation == "nonartifact_receipt":
            value["first_task_build"]["receipts"]["environment"]["source"] = "New environment construction"
            save(modules, tmp_path, value)
        elif operation == "new_build":
            start_build(modules, tmp_path, value)
        else:
            value["remaining_build"]["active_step"] = "tasks"
            value["remaining_build"]["source"] = "Restart task authoring"
            save(modules, tmp_path, value)
    assert modules[0].load_state(tmp_path) == before
