# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creation approvals, current evidence, and native run selection stay independent."""

import copy
import hashlib
import importlib
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


def approve(modules, repo, value):
    value["plan_approval"] = {"digest": facts(modules, repo)["plan_digest"], "source": "User approves this task design"}
    return save(modules, repo, value)


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
    review_first(modules, repo, value)
    check(modules, repo, value, "refund", "validation")
    for planned in value["plan"]["tasks"][1:]:
        author(modules, repo, value, planned["id"])
        check(modules, repo, value, planned["id"], "environment")
        check(modules, repo, value, planned["id"], "validation")
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
    assert facts(modules, tmp_path)["next_stage"] == "review_first"
    with pytest.raises(ValueError, match="first task before"):
        author(modules, tmp_path, value, "policy")
    del value["tasks"]["policy"]
    review_first(modules, tmp_path, value)
    assert facts(modules, tmp_path)["first_task_approved"]
    author(modules, tmp_path, value, "policy")
    assert not facts(modules, tmp_path)["execution_authorized"]


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


@pytest.mark.parametrize("decision", ["accepted", "declined"])
def test_audit_decision_preserves_complete_creation_and_missing_audit_evidence_blocks_only_audit(
    modules, tmp_path, decision
):
    value = complete(modules, tmp_path)
    value["audit_invitation"] = {"status": decision, "source": "Actual user audit decision"}
    saved = save(modules, tmp_path, value)
    assert facts(modules, tmp_path)["complete"]
    assert saved["next_handoff"] == ("eval-author-audit" if decision == "accepted" else None)
    if decision == "accepted":
        audit = modules[0].initialize(tmp_path, journey="audit")
        modules[0].update(
            tmp_path, {"blockers": [{"id": "data", "phase": "audit", "message": "No audit traces selected"}]}
        )
        assert audit["ethos"] == saved["ethos"]
        assert audit["scopes"]["create"] == saved["scopes"]["create"]
        assert audit["journey_status"]["create"] == "complete"
        assert facts(modules, tmp_path)["complete"]
    else:
        assert saved["journey"] == "create"


def test_revision_checks_history_and_current_ethos_review_cannot_be_bypassed(modules, tmp_path):
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
    assert saved["scopes"]["create"]["first_eval"]["history"]


def test_gym_harbor_requires_current_bridge_evidence_not_just_converted_tasks(modules, tmp_path):
    value = ready(modules, tmp_path)
    value["plan"]["execution_path"] = "gym_harbor"
    save(modules, tmp_path, value)
    approve(modules, tmp_path, value)
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
    with pytest.raises(ValueError, match="Approve the first task"):
        save(modules, tmp_path, value)
    value = complete(modules, tmp_path)
    state = modules[0].load_state(tmp_path)
    observed = modules[1].observe(tmp_path, state)
    state["scopes"]["create"]["first_eval"].pop("execution_settings_presented")
    derived = modules[1].derive(state, observed)
    assert not derived["execution_authorized"]
    assert derived["next_stage"] == "execution_settings"


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
def test_saved_design_question_preserves_history_but_stops_automatic_authoring(modules, tmp_path, prepared):
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
    assert retained["history"][-1]["record"]["plan_approval"] == prior["plan_approval"]
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
    retained.pop("history", None)
    retained["plan"]["trace_choice"] = "none"
    save(modules, tmp_path, retained)
    approve(modules, tmp_path, retained)
    save(modules, tmp_path, retained)
    assert facts(modules, tmp_path)["scope_approved"]


@pytest.mark.parametrize("decision", ["revise", "new"])
@pytest.mark.parametrize("audit_status", ["pending", "accepted", "declined"])
def test_replacing_saved_design_retires_prior_audit_invitation_without_losing_history(
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
    assert retained["history"][-1]["record"]["audit_invitation"] == previous_invitation
    retained.pop("history")
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
