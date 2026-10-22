# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise saved first-evaluation decisions independently of provider credentials."""

import copy
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("journey_state"), importlib.import_module("first_eval_state")


def reference(repo, path, contents="retained native evidence"):
    import hashlib

    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents)
    return {"path": path, "digest": hashlib.sha256(target.read_bytes()).hexdigest()}


def save_record(state, repo, record, **fields):
    current = state.load_state(repo)
    return state.update(
        repo,
        {"scopes": {"create": {"first_eval": record}}, **fields},
        expected_revision=current["revision"],
    )


def start(modules, repo, provider="harbor", examples=None, *, with_settings=False):
    state, helper = modules
    state.initialize(repo, agent="support", journey="create", setup=provider)
    state.update(
        repo, {"prerequisites": {key: {"status": "confirmed", "source": "user reply"} for key in state.PREREQUISITES}}
    )
    (repo / "ETHOS.md").write_text("# Support agent intent\n")
    state.confirm_ethos(
        repo, reviewed_digest=state.ethos_digest(repo), source="reviewed saved Ethos", validation="valid"
    )
    record = {
        "schema_version": 1,
        "provider": provider,
        "ethos_digest": state.ethos_digest(repo),
        "plan": {
            "tasks": examples
            or [{"id": "refund", "example_ids": ["refund-1"]}, {"id": "policy", "example_ids": ["policy-1"]}],
            "breadth": "pilot",
            "exclusions": ["other regions"],
        },
        "plan_approval": None,
        "first_task": "refund",
        "tasks": {},
        "selection": None,
        "runs": [],
        "run_review": None,
        "audit_invitation": None,
    }
    if with_settings:
        adopt_settings(record)
    save_record(state, repo, record, journey_status={"create": "in_progress"})
    return record


def approve_plan(modules, repo, record):
    state, helper = modules
    record["plan_approval"] = {
        "digest": helper.progress(repo, state.load_state(repo))["plan_digest"],
        "source": "user agrees to the displayed pilot",
    }
    return save_record(state, repo, record)


def present_settings(modules, repo, record):
    state, helper = modules
    record["execution_settings_presented"] = {
        "digest": helper.progress(repo, state.load_state(repo))["settings_digest"],
        "source": "Displayed execution settings after first-task approval",
    }
    return save_record(state, repo, record)


def adopt_settings(record):
    record["execution_settings"] = {
        "agent_model": "Actual support agent; configured model",
        "attempts": "One attempt per task",
        "validation": "Reference and relevant incorrect-result controls",
        "limits": "180 seconds per attempt; no paid grading or retries",
        "open_questions": [],
    }


def task(modules, repo, task_id):
    _, helper = modules
    path = f".eval-author/tasks/{task_id}"
    reference(repo, f"{path}/instruction.md", f"{task_id} customer instruction")
    return {
        "path": path,
        "sha256": helper.task_digest(repo, path),
        "review": None,
        "validation": None,
        "limitations": ["Narrow fixtures"],
    }


def validate_and_review(repo, task_id, item):
    item["environment"] = {
        "sha256": item["sha256"],
        "status": "passed",
        "source": "Observed the native environment smoke task",
        "evidence": [reference(repo, f".eval-author/.working/environments/{task_id}/smoke.json")],
    }
    item["validation"] = {
        "sha256": item["sha256"],
        "status": "passed",
        "source": "Observed all declared native controls",
        "evidence": [reference(repo, f".eval-author/task-measurements/{task_id}/controls.json")],
    }
    item["review"] = {"sha256": item["sha256"], "decision": "approved", "source": "user reviewed this task revision"}


def cases_ready(modules, repo, provider="harbor", examples=None, *, with_settings=False):
    state, helper = modules
    record = start(modules, repo, provider, examples, with_settings=with_settings)
    approve_plan(modules, repo, record)
    record["tasks"]["refund"] = task(modules, repo, "refund")
    save_record(state, repo, record)
    validate_and_review(repo, "refund", record["tasks"]["refund"])
    save_record(state, repo, record)
    for planned in record["plan"]["tasks"][1:]:
        item = task(modules, repo, planned["id"])
        validate_and_review(repo, planned["id"], item)
        record["tasks"][planned["id"]] = item
    save_record(state, repo, record)
    return record


def selection_ready(modules, repo, provider="harbor", examples=None, *, with_settings=False):
    state, helper = modules
    record = cases_ready(modules, repo, provider, examples, with_settings=with_settings)
    present_settings(modules, repo, record)
    cases: list[dict[str, Any]] = [
        {"task_id": item["id"], "example_ids": item["example_ids"], "attempts": 1} for item in record["plan"]["tasks"]
    ]
    selection = {
        "config": reference(repo, ".eval-author/run.yaml", "native selected configuration"),
        "cases": cases,
        "agent": "actual-support-agent",
        "model": "configured-model",
        "source": "Validated native selection and connection configuration",
    }
    if "execution_settings" in record:
        selection["settings_digest"] = helper.progress(repo, state.load_state(repo))["settings_digest"]
    if provider == "gym":
        selection["inputs"] = [reference(repo, ".eval-author/dataset.jsonl", "selected native rows")]
        for case in cases:
            case["rows"] = list(range(1, len(case["example_ids"]) + 1))
    record["selection"] = selection
    saved = save_record(state, repo, record)
    selection["authorization"] = {
        "digest": helper.progress(repo, saved)["selection_digest"],
        "source": "User authorizes this bounded baseline and spend",
    }
    save_record(state, repo, record)
    return record


def finished(modules, repo, provider="harbor", examples=None, *, with_settings=False):
    state, helper = modules
    record = selection_ready(modules, repo, provider, examples, with_settings=with_settings)
    selected = helper.progress(repo, state.load_state(repo))["selection_digest"]
    attempts = []
    for case in record["selection"]["cases"]:
        units = [[example] for example in case["example_ids"]] if provider == "gym" else [case["example_ids"]]
        for i, unit in enumerate(units):
            name = f"{case['task_id']}-{i + 1}"
            attempts.append(
                {
                    "id": name,
                    "native_id": f"native-{name}",
                    "task_id": case["task_id"],
                    "example_ids": unit,
                    "status": "completed",
                    "rewards": {"reward": 0},
                    "evidence": [reference(repo, f".eval-author/jobs/baseline/{name}/result.json")],
                }
            )
    run = {
        "id": "baseline-job",
        "role": "agent",
        "status": "completed",
        "selection_digest": selected,
        "task_revisions": {key: item["sha256"] for key, item in record["tasks"].items()},
        "source": "Observed actual-agent native run and inspected every selected result",
        "evidence": [reference(repo, ".eval-author/jobs/baseline/result.json")],
        "attempts": attempts,
    }
    record["runs"] = [run]
    saved = save_record(state, repo, record)
    record["run_review"] = {
        "run_id": run["id"],
        "attempt_id": attempts[0]["id"],
        "digest": helper.progress(repo, saved)["run_digests"][run["id"]],
        "source": "Reviewed this exact saved attempt with the user",
        "limitations": ["Native ATIF unavailable", "Report feedback is an external dependency"],
    }
    save_record(state, repo, record, journey_status={"create": "complete"})
    return record


def test_scope_approval_and_first_task_require_separate_saved_checkpoints(modules, tmp_path):
    state, helper = modules
    record = start(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    record["tasks"]["refund"] = task(modules, tmp_path, "refund")
    with pytest.raises(ValueError, match="scope approval"):
        save_record(state, tmp_path, record)
    assert state.state_path(tmp_path).read_bytes() == before
    record["tasks"] = {}
    approve_plan(modules, tmp_path, record)
    record["tasks"]["refund"] = task(modules, tmp_path, "refund")
    save_record(state, tmp_path, record)
    validate_and_review(tmp_path, "refund", record["tasks"]["refund"])
    record["tasks"]["policy"] = task(modules, tmp_path, "policy")
    with pytest.raises(ValueError, match="first task"):
        save_record(state, tmp_path, record)
    del record["tasks"]["policy"]
    save_record(state, tmp_path, record)
    record["tasks"]["policy"] = task(modules, tmp_path, "policy")
    result = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, result)["first_task_approved"]


@pytest.mark.parametrize("with_settings", [False, True])
def test_design_approval_authors_and_reviews_tasks_before_execution_settings(modules, tmp_path, with_settings):
    state, helper = modules
    record = start(modules, tmp_path, with_settings=with_settings)
    record["plan_approval"] = {
        "digest": helper.progress(tmp_path, state.load_state(tmp_path))["plan_digest"],
        "source": "User approved the proposed task design",
    }
    saved = save_record(state, tmp_path, record)
    facts = helper.progress(tmp_path, saved)
    assert facts["scope_approved"]
    assert not facts["execution_settings_presented"]
    assert facts["next_stage"] == "prepare_first"
    before = state.state_path(tmp_path).read_bytes()
    assert helper.progress(tmp_path, state.load_state(tmp_path)) == facts
    assert state.state_path(tmp_path).read_bytes() == before

    record["tasks"]["refund"] = task(modules, tmp_path, "refund")
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "prepare_first"
    first = record["tasks"]["refund"]
    validate_and_review(tmp_path, "refund", first)
    first["review"] = None
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "review_first"
    assert not helper.progress(tmp_path, saved)["execution_settings_presented"]
    first["review"] = {"sha256": first["sha256"], "decision": "approved", "source": "User approves first task"}
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "prepare_remaining"
    record["tasks"]["policy"] = task(modules, tmp_path, "policy")
    save_record(state, tmp_path, record)
    validate_and_review(tmp_path, "policy", record["tasks"]["policy"])
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "execution_settings"
    assert not helper.progress(tmp_path, saved)["execution_settings_presented"]
    saved = present_settings(modules, tmp_path, record)
    shown = helper.progress(tmp_path, saved)
    assert shown["next_stage"] == "ready_to_run"
    assert shown["scope_approved"] and shown["execution_settings_presented"]
    assert not shown["execution_authorized"]


def test_settings_only_changes_do_not_interrupt_first_task_authoring_or_review(modules, tmp_path):
    state, helper = modules
    record = start(modules, tmp_path, with_settings=True)
    saved = approve_plan(modules, tmp_path, record)
    approved_digest = helper.progress(tmp_path, saved)["plan_digest"]
    record["execution_settings"]["limits"] = "Run budget unresolved; no paid work authorized"
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "prepare_first"
    record["tasks"]["refund"] = task(modules, tmp_path, "refund")
    save_record(state, tmp_path, record)
    first = record["tasks"]["refund"]
    validate_and_review(tmp_path, "refund", first)
    first["review"] = None
    record["execution_settings"]["agent_model"] = "Agent and judge models remain unresolved"
    saved = save_record(state, tmp_path, record)
    facts = helper.progress(tmp_path, saved)
    assert facts["scope_approved"] and facts["plan_digest"] == approved_digest
    assert facts["tasks"]["refund"]["validated"] and facts["next_stage"] == "review_first"
    first["review"] = {"sha256": first["sha256"], "decision": "approved", "source": "User approves first task"}
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "prepare_remaining"
    assert not helper.progress(tmp_path, saved)["execution_settings_presented"]


def test_settings_presented_for_paid_validation_are_reused_before_running(modules, tmp_path):
    state, helper = modules
    record = cases_ready(modules, tmp_path, with_settings=True)
    validation = record["tasks"]["refund"]["validation"]
    record["tasks"]["refund"]["validation"] = None
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["first_task_approved"]
    assert helper.progress(tmp_path, saved)["next_stage"] == "prepare_remaining"
    saved = present_settings(modules, tmp_path, record)
    assert helper.progress(tmp_path, saved)["execution_settings_presented"]
    assert helper.progress(tmp_path, saved)["next_stage"] == "prepare_remaining"
    record["tasks"]["refund"]["validation"] = validation
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "ready_to_run"
    assert not helper.progress(tmp_path, saved)["execution_authorized"]


def test_current_execution_authorization_does_not_imply_settings_were_displayed(modules, tmp_path):
    state, helper = modules
    record = selection_ready(modules, tmp_path, with_settings=True)
    record.pop("execution_settings_presented")
    saved = save_record(state, tmp_path, record)
    facts = helper.progress(tmp_path, saved)
    assert facts["execution_authorized"]
    assert not facts["execution_settings_presented"]
    assert facts["next_stage"] == "execution_settings"


def test_new_unbound_selection_requires_previously_saved_settings_display(modules, tmp_path):
    state, helper = modules
    record = cases_ready(modules, tmp_path)
    selection = {
        "config": reference(tmp_path, ".eval-author/run.yaml", "native selected configuration"),
        "cases": [
            {"task_id": item["id"], "example_ids": item["example_ids"], "attempts": 1}
            for item in record["plan"]["tasks"]
        ],
        "agent": "actual-support-agent",
        "model": "configured-model",
        "source": "Inspected concrete native selection",
    }
    record["selection"] = selection
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="before introducing an unbound execution selection"):
        save_record(state, tmp_path, record)
    assert state.state_path(tmp_path).read_bytes() == before

    record["execution_settings_presented"] = {
        "digest": helper.progress(tmp_path, state.load_state(tmp_path))["settings_digest"],
        "source": "Delivered settings checkpoint",
    }
    with pytest.raises(ValueError, match="before introducing an unbound execution selection"):
        save_record(state, tmp_path, record)
    record["selection"] = None
    present_settings(modules, tmp_path, record)
    record["selection"] = selection
    saved = save_record(state, tmp_path, record)
    current = helper.progress(tmp_path, saved)
    assert current["selection_current"]
    record.pop("execution_settings_presented")
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["selection_digest"] == current["selection_digest"]
    assert helper.progress(tmp_path, saved)["selection_current"]


def test_settings_display_requires_previously_saved_design_approval(modules, tmp_path):
    state, helper = modules
    record = start(modules, tmp_path, with_settings=True)
    facts = helper.progress(tmp_path, state.load_state(tmp_path))
    record["plan_approval"] = {"digest": facts["plan_digest"], "source": "User approves task design"}
    record["execution_settings_presented"] = {
        "digest": facts["settings_digest"],
        "source": "Delivered execution settings",
    }
    with pytest.raises(ValueError, match="Save scope approval before recording displayed execution settings"):
        save_record(state, tmp_path, record)


def test_legacy_proposal_settings_are_available_without_changing_approved_plan(modules, tmp_path):
    state, helper = modules
    record = start(modules, tmp_path)
    settings = {}
    adopt_settings(settings)
    record["plan"]["presentation"] = {"coverage_note": "Useful task coverage", **settings["execution_settings"]}
    saved = save_record(state, tmp_path, record)
    plan_digest = helper.progress(tmp_path, saved)["plan_digest"]
    approve_plan(modules, tmp_path, record)
    before = state.state_path(tmp_path).read_bytes()
    assert helper.execution_settings(record) == settings["execution_settings"]
    facts = helper.progress(tmp_path, state.load_state(tmp_path))
    assert facts["scope_approved"] and facts["plan_digest"] == plan_digest
    assert facts["next_stage"] == "prepare_first"
    assert state.state_path(tmp_path).read_bytes() == before


def test_settings_changes_preserve_design_but_reopen_execution_and_retain_history(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path, with_settings=True)
    prior = helper.progress(tmp_path, state.load_state(tmp_path))
    prior_settings = copy.deepcopy(record["execution_settings"])
    prior_runs = copy.deepcopy(record["runs"])
    record["execution_settings"]["limits"] = "120 seconds per attempt; no paid grading or retries"
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    facts = helper.progress(tmp_path, saved)
    assert facts["scope_approved"] and facts["first_task_approved"]
    assert facts["plan_digest"] == prior["plan_digest"]
    assert all(task["validated"] for task in facts["tasks"].values())
    assert not facts["selection_current"] and not facts["execution_authorized"]
    assert not facts["complete"] and not facts["reviewed_run_current"]
    assert facts["next_stage"] == "execution_settings"
    assert saved["scopes"]["create"]["first_eval"]["runs"] == prior_runs
    assert any(
        item["kind"] == "execution_settings" and item["record"] == prior_settings
        for item in saved["scopes"]["create"]["first_eval"]["history"]
    )

    previous_display = copy.deepcopy(record["execution_settings_presented"])
    record["execution_settings_presented"] = {
        "digest": facts["settings_digest"],
        "source": "Displayed revised limits without requesting design approval again",
    }
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["next_stage"] == "ready_to_run"
    assert any(
        item["kind"] == "execution_settings_presented" and item["record"] == previous_display
        for item in saved["scopes"]["create"]["first_eval"]["history"]
    )
    record["selection"]["settings_digest"] = facts["settings_digest"]
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["selection_current"]
    assert not helper.progress(tmp_path, saved)["execution_authorized"]
    assert not helper.progress(tmp_path, saved)["runs"]["baseline-job"]["reviewable"]


def test_adopted_settings_cannot_be_removed_to_restore_execution(modules, tmp_path):
    state, _ = modules
    record = finished(modules, tmp_path, with_settings=True)
    before = state.state_path(tmp_path).read_bytes()
    record.pop("execution_settings")
    record.pop("execution_settings_presented")
    record["selection"].pop("settings_digest")
    with pytest.raises(ValueError, match="Retain adopted execution settings"):
        save_record(state, tmp_path, record)
    assert state.state_path(tmp_path).read_bytes() == before


def test_legacy_completed_records_do_not_require_retroactive_settings_display(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    prior = helper.progress(tmp_path, state.load_state(tmp_path))
    record.pop("execution_settings_presented")
    save_record(state, tmp_path, record)
    before = state.state_path(tmp_path).read_bytes()
    loaded = state.load_state(tmp_path)
    facts = helper.progress(tmp_path, loaded)
    assert facts["complete"] and facts["scope_approved"] and facts["execution_authorized"]
    assert not facts["execution_settings_presented"]
    assert facts["next_stage"] == "results"
    assert facts["plan_digest"] == prior["plan_digest"]
    assert facts["selection_digest"] == prior["selection_digest"]
    assert facts["run_digests"] == prior["run_digests"]
    assert "execution_settings" not in loaded["scopes"]["create"]["first_eval"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_missing_credentials_retains_cases_controls_and_pending_question(modules, tmp_path):
    state, helper = modules
    record = cases_ready(modules, tmp_path)
    saved = state.update(
        tmp_path,
        {
            "prerequisites": {"model_credentials": {"status": "missing", "source": "MODEL_KEY absent"}},
            "blockers": [{"id": "model_credentials", "phase": "create", "message": "Set MODEL_KEY"}],
            "pending_question": "Configure MODEL_KEY in the execution environment",
            "next_handoff": "eval-author-first-eval",
        },
    )
    resumed = state.initialize(tmp_path, agent="support", journey="create", setup="harbor")
    assert resumed["scopes"]["create"]["first_eval"] == saved["scopes"]["create"]["first_eval"]
    assert resumed["scopes"]["create"]["first_eval"]["tasks"] == record["tasks"]
    assert resumed["pending_question"] == saved["pending_question"]
    assert state.checklist(resumed)[0]["complete"]
    facts = helper.progress(tmp_path, resumed)
    assert all(item["validated"] for item in facts["tasks"].values())
    assert not facts["complete"]


@pytest.mark.parametrize("provider", ["harbor", "gym"])
def test_zero_reward_actual_run_completes_without_atif_and_retains_exact_review(modules, tmp_path, provider):
    state, helper = modules
    record = finished(modules, tmp_path, provider)
    before = state.state_path(tmp_path).read_bytes()
    loaded = state.load_state(tmp_path)
    view = state.view(loaded)
    assert view["first_eval"]["complete"]
    assert view["first_eval"]["reviewed_run_current"]
    assert view["checklist"][2]["complete"]
    assert record["run_review"]["attempt_id"] == "refund-1"
    assert all(attempt["rewards"]["reward"] == 0 for attempt in record["runs"][0]["attempts"])
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("change", ["control", "empty_rewards", "error", "missing_attempt", "wrong_review"])
def test_incomplete_or_control_only_runs_cannot_complete(modules, tmp_path, change):
    state, helper = modules
    record = finished(modules, tmp_path)
    if change == "control":
        record["runs"][0]["role"] = "control"
    elif change == "empty_rewards":
        record["runs"][0]["attempts"][0]["rewards"] = {}
    elif change == "error":
        record["runs"][0]["attempts"][0]["status"] = "infrastructure_error"
    elif change == "missing_attempt":
        record["runs"][0]["attempts"].pop()
    else:
        record["run_review"]["attempt_id"] = "a-control-attempt"
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    assert not helper.progress(tmp_path, saved)["complete"]
    with pytest.raises(ValueError, match="completion requires"):
        state.update(tmp_path, {"journey_status": {"create": "complete"}})


@pytest.mark.parametrize("changed", ["task", "config", "control", "result", "ethos", "input"])
def test_show_derives_staleness_without_rewriting_evidence_or_state(modules, tmp_path, changed):
    state, helper = modules
    record = finished(modules, tmp_path, "gym")
    prior_record = copy.deepcopy(state.load_state(tmp_path)["scopes"]["create"]["first_eval"])
    paths = {
        "task": ".eval-author/tasks/refund/instruction.md",
        "config": record["selection"]["config"]["path"],
        "control": record["tasks"]["refund"]["validation"]["evidence"][0]["path"],
        "result": record["runs"][0]["attempts"][0]["evidence"][0]["path"],
        "ethos": "ETHOS.md",
        "input": record["selection"]["inputs"][0]["path"],
    }
    (tmp_path / paths[changed]).write_text("changed bytes")
    before = state.state_path(tmp_path).read_bytes()
    loaded = state.load_state(tmp_path)
    assert loaded["journey_status"].get("create") != "complete"
    facts = helper.progress(tmp_path, loaded)
    assert not facts["complete"]
    if changed == "task":
        assert not facts["tasks"]["refund"]["validated"]
        assert facts["tasks"]["policy"]["validated"]
    assert loaded["scopes"]["create"]["first_eval"] == prior_record
    assert state.state_path(tmp_path).read_bytes() == before


def test_gym_rows_are_explicit_and_repeats_do_not_replace_missing_examples(modules, tmp_path):
    state, helper = modules
    examples = [{"id": "refund", "example_ids": ["refund-1", "refund-2"]}]
    record = finished(modules, tmp_path, "gym", examples)
    assert helper.progress(tmp_path, state.load_state(tmp_path))["complete"]
    record["runs"][0]["attempts"][1]["example_ids"] = ["refund-1"]
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    assert not helper.progress(tmp_path, saved)["complete"]
    record["selection"]["cases"][0]["rows"] = [1, 1]
    with pytest.raises(ValueError, match="one-based row"):
        save_record(state, tmp_path, record)


def test_changed_plan_retains_old_artifacts_but_requires_fresh_agreement(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    record["plan"]["breadth"] = "expanded"
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    facts = helper.progress(tmp_path, saved)
    assert not facts["scope_approved"]
    assert facts["next_stage"] == "proposal"
    assert saved["scopes"]["create"]["first_eval"]["runs"] == record["runs"]


def test_selection_authorization_is_bound_and_cannot_be_inferred_from_scope(modules, tmp_path):
    state, helper = modules
    record = selection_ready(modules, tmp_path)
    selected_digest = helper.progress(tmp_path, state.load_state(tmp_path))["selection_digest"]
    record["selection"].pop("authorization")
    saved = save_record(state, tmp_path, record)
    facts = helper.progress(tmp_path, saved)
    assert facts["scope_approved"] and facts["selection_current"]
    assert not facts["execution_authorized"]
    record["selection"]["authorization"] = {"digest": selected_digest, "source": "User authorizes selected run"}
    record["selection"]["cases"][0]["attempts"] = 2
    saved = save_record(state, tmp_path, record)
    assert not helper.progress(tmp_path, saved)["execution_authorized"]


@pytest.mark.parametrize("decision", ["pending", "declined", "accepted"])
def test_audit_decision_is_saved_without_automatic_transition(modules, tmp_path, decision):
    state, helper = modules
    record = finished(modules, tmp_path)
    record["audit_invitation"] = {"status": decision, "source": None if decision == "pending" else "user reply"}
    saved = save_record(state, tmp_path, record)
    assert saved["journey"] == "create"
    assert helper.progress(tmp_path, saved)["audit_decision"] == decision
    if decision == "accepted":
        continued = state.initialize(tmp_path, journey="audit")
        assert continued["ethos"] == saved["ethos"]
        assert continued["journey_status"]["create"] == "complete"
        assert continued["scopes"]["create"] == saved["scopes"]["create"]
        assert continued["prerequisites"]["runtime"]["status"] == "unknown"


def test_scope_updates_require_revision_and_preserve_unrelated_context(modules, tmp_path):
    state, helper = modules
    record = start(modules, tmp_path)
    saved = state.update(tmp_path, {"scopes": {"audit": {"traces": ["selected.json"]}}})
    state.update(tmp_path, {"scopes": {"create": {"corpus": "no traces"}}}, expected_revision=saved["revision"])
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="expected-revision"):
        state.update(tmp_path, {"scopes": {"create": {"first_eval": record}}})
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, {"scopes": {"create": {"first_eval": record}}}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before
    approved = approve_plan(modules, tmp_path, record)
    assert approved["scopes"]["create"]["corpus"] == "no traces"
    assert approved["scopes"]["audit"] == {"traces": ["selected.json"]}


def test_legacy_scope_is_readable_but_cannot_establish_creation_completion(modules, tmp_path):
    state, helper = modules
    state.initialize(tmp_path, journey="create")
    state.update(
        tmp_path,
        {"scopes": {"create": {"cases": ["refund"], "confirmed": True}}, "journey_status": {"create": "complete"}},
    )
    before = state.state_path(tmp_path).read_bytes()
    loaded = state.load_state(tmp_path)
    assert helper.progress(tmp_path, loaded) is None
    assert not state.checklist(loaded)[2]["complete"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_invalid_scope_update_and_symlink_preserve_prior_state(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    bad = copy.deepcopy(record)
    bad["tasks"]["refund"]["path"] = "../escape"
    with pytest.raises(ValueError, match="repository-relative"):
        save_record(state, tmp_path, bad)
    assert state.state_path(tmp_path).read_bytes() == before
    path = tmp_path / record["runs"][0]["evidence"][0]["path"]
    retained = path.with_name("retained.json")
    path.rename(retained)
    path.symlink_to(retained)
    assert not helper.progress(tmp_path, state.load_state(tmp_path))["complete"]


def test_cli_show_exposes_derived_progress_and_does_not_write(modules, tmp_path):
    import subprocess
    import sys

    state, _ = modules
    finished(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    shown = subprocess.run(
        [sys.executable, str(SCRIPTS / "journey_state.py"), "--repo", str(tmp_path), "show"],
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["first_eval"]["complete"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_first_task_environment_and_approval_allow_remainder_before_formal_controls(modules, tmp_path):
    state, helper = modules
    record = start(modules, tmp_path)
    approve_plan(modules, tmp_path, record)
    record["tasks"]["refund"] = task(modules, tmp_path, "refund")
    save_record(state, tmp_path, record)
    validate_and_review(tmp_path, "refund", record["tasks"]["refund"])
    record["tasks"]["refund"]["validation"] = None
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["first_task_approved"]
    assert not helper.progress(tmp_path, saved)["tasks"]["refund"]["validated"]
    record["tasks"]["policy"] = task(modules, tmp_path, "policy")
    save_record(state, tmp_path, record)


def test_later_tasks_do_not_require_individual_human_approval(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    record["tasks"]["policy"]["review"] = None
    saved = save_record(state, tmp_path, record)
    assert not helper.progress(tmp_path, saved)["tasks"]["policy"]["reviewed"]
    assert helper.progress(tmp_path, saved)["complete"]


def test_partial_error_run_can_be_reviewed_and_retains_successful_task(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    run = record["runs"][0]
    run["status"] = "infrastructure_error"
    run["attempts"][1]["status"] = "infrastructure_error"
    run["attempts"][1]["rewards"] = {}
    record["run_review"]["digest"] = helper.fingerprint(run)
    record["run_review"]["attempt_id"] = run["attempts"][1]["id"]
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    facts = helper.progress(tmp_path, saved)
    assert facts["reviewed_run_current"]
    assert facts["tasks"]["refund"]["executed"]
    assert not facts["tasks"]["policy"]["executed"]
    assert not facts["complete"]


def test_environment_proof_is_required_and_bound_to_retained_evidence(modules, tmp_path):
    state, helper = modules
    record = cases_ready(modules, tmp_path)
    environment = record["tasks"]["refund"].pop("environment")
    saved = save_record(state, tmp_path, record)
    assert not helper.progress(tmp_path, saved)["first_task_approved"]
    record["tasks"]["refund"]["environment"] = environment
    save_record(state, tmp_path, record)
    (tmp_path / environment["evidence"][0]["path"]).write_text("changed environment proof")
    assert not helper.progress(tmp_path, state.load_state(tmp_path))["first_task_approved"]


def test_task_path_cannot_move_on_acceptance(modules, tmp_path):
    state, helper = modules
    record = cases_ready(modules, tmp_path)
    record["tasks"]["refund"]["path"] = ".eval-author/tasks/accepted-refund"
    with pytest.raises(ValueError, match="Keep task paths stable"):
        save_record(state, tmp_path, record)


@pytest.mark.parametrize("provider", ["harbor", "gym"])
def test_explicit_recovery_jobs_complete_without_rerunning_successful_cases(modules, tmp_path, provider):
    state, helper = modules
    record = finished(modules, tmp_path, provider)
    original = record["runs"][0]
    retried = copy.deepcopy(original["attempts"][1])
    original["status"] = "infrastructure_error"
    original["attempts"][1]["status"] = "infrastructure_error"
    original["attempts"][1]["rewards"] = {}
    retried["id"] = "policy-retry"
    retried["native_id"] = "native-policy-retry"
    retried["evidence"] = [reference(tmp_path, ".eval-author/jobs/retry/policy/result.json")]
    retry = {
        "id": "retry-job",
        "role": "agent",
        "status": "completed",
        "selection_digest": original["selection_digest"],
        "task_revisions": {"policy": record["tasks"]["policy"]["sha256"]},
        "source": "Observed retry of the failed task only with unchanged agent/model and selected inputs",
        "evidence": [reference(tmp_path, ".eval-author/jobs/retry/result.json")],
        "attempts": [retried],
    }
    record["runs"].append(retry)
    record["run_review"] = {
        "run_id": retry["id"],
        "attempt_id": retried["id"],
        "digest": helper.fingerprint(retry),
        "source": "Reviewed native retry and initial successful task",
        "limitations": [],
    }
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    assert not helper.progress(tmp_path, saved)["complete"]
    record["selected_run_ids"] = [original["id"], retry["id"]]
    saved = save_record(state, tmp_path, record, journey_status={"create": "complete"})
    facts = helper.progress(tmp_path, saved)
    assert facts["complete"]
    assert facts["suite"]["completed_attempts"] == facts["suite"]["planned_attempts"] == 2
    assert facts["suite"]["observed_attempts"] == 3
    assert facts["suite"]["executed_examples"] == facts["suite"]["planned_examples"] == 2
    assert not facts["runs"][original["id"]]["complete"]
    assert not facts["runs"][retry["id"]]["complete"]
    assert all(task["executed"] for task in facts["tasks"].values())


@pytest.mark.parametrize("change", ["duplicate_native", "control", "stale"])
def test_selected_recovery_rejects_reused_identity_controls_and_stale_inputs(modules, tmp_path, change):
    state, helper = modules
    record = finished(modules, tmp_path)
    other = copy.deepcopy(record["runs"][0])
    other["id"] = "other-job"
    if change == "control":
        other["role"] = "control"
    elif change == "stale":
        other["selection_digest"] = "0" * 64
    record["runs"].append(other)
    record["selected_run_ids"] = ["baseline-job", "other-job"]
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    facts = helper.progress(tmp_path, saved)
    assert not facts["complete"]
    assert facts["suite"]["errors"]


def test_task_revision_and_plan_revision_reopen_execution_authorization(modules, tmp_path):
    state, helper = modules
    record = selection_ready(modules, tmp_path)
    original = helper.progress(tmp_path, state.load_state(tmp_path))["selection_digest"]
    (tmp_path / record["tasks"]["refund"]["path"] / "instruction.md").write_text("Revised task behavior")
    assert not helper.progress(tmp_path, state.load_state(tmp_path))["execution_authorized"]
    record["tasks"]["refund"]["sha256"] = helper.task_digest(tmp_path, record["tasks"]["refund"]["path"])
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["selection_digest"] != original
    assert not helper.progress(tmp_path, saved)["execution_authorized"]
    revised_task_digest = helper.progress(tmp_path, saved)["selection_digest"]
    record["plan"]["exclusions"].append("Newly excluded region")
    saved = save_record(state, tmp_path, record)
    assert helper.progress(tmp_path, saved)["selection_digest"] != revised_task_digest


def test_explicit_single_run_does_not_hide_job_infrastructure_error(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    record["runs"][0]["status"] = "infrastructure_error"
    record["run_review"]["digest"] = helper.fingerprint(record["runs"][0])
    record["selected_run_ids"] = ["baseline-job"]
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    facts = helper.progress(tmp_path, saved)
    assert facts["suite"]["job_errors"] == ["baseline-job"]
    assert not facts["suite"]["complete"]
    assert not facts["complete"]


def test_revisions_retain_approved_task_and_corrected_native_run_history(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    original_task = copy.deepcopy(record["tasks"]["refund"])
    original_run = copy.deepcopy(record["runs"][0])
    (tmp_path / original_task["path"] / "instruction.md").write_text("Revised request")
    record["tasks"]["refund"]["sha256"] = helper.task_digest(tmp_path, original_task["path"])
    record["tasks"]["refund"]["review"] = None
    record["tasks"]["refund"]["validation"] = None
    record["runs"][0]["source"] = "Corrected native execution observation source"
    saved = save_record(state, tmp_path, record, journey_status={"create": "in_progress"})
    history = saved["scopes"]["create"]["first_eval"]["history"]
    assert any(item["kind"] == "task" and item["record"] == original_task for item in history)
    assert any(item["kind"] == "run" and item["record"] == original_run for item in history)
    assert all(helper.fingerprint(item["record"]) == item["digest"] for item in history)
    assert not helper.progress(tmp_path, saved)["reviewed_run_current"]
    # The caller can omit generated history; the helper carries it forward.
    resumed = save_record(state, tmp_path, record)
    assert resumed["scopes"]["create"]["first_eval"]["history"] == history


def test_prior_task_run_and_generated_history_cannot_be_dropped(modules, tmp_path):
    state, helper = modules
    record = finished(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    for field in ("tasks", "runs"):
        changed = copy.deepcopy(record)
        changed[field] = {} if field == "tasks" else []
        with pytest.raises(ValueError, match="Retain prior"):
            save_record(state, tmp_path, changed)
    changed = copy.deepcopy(record)
    changed["history"] = []
    with pytest.raises(ValueError, match="cannot be rewritten"):
        save_record(state, tmp_path, changed)
    current = state.load_state(tmp_path)
    with pytest.raises(ValueError, match="Retain the structured"):
        state.update(tmp_path, {"scopes": {"create": None}}, expected_revision=current["revision"])
    assert state.state_path(tmp_path).read_bytes() == before
