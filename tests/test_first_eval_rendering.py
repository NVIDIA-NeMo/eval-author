# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creation presentations derive gates and exact evidence navigation from supplied state."""

import importlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
SHA = "a" * 64


@pytest.fixture
def creation(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    state_helper = importlib.import_module("journey_state")
    helper = importlib.import_module("first_eval_state")
    renderer = importlib.import_module("conversation_renderer")
    templates = importlib.import_module("conversation_flows.first_eval_templates")
    state = state_helper.new_state(tmp_path)
    state.update(journey="create", setup="harbor", revision=4, agent="support")
    state["prerequisites"] = {
        key: {"status": "verified", "source": "Observed check"} for key in state_helper.PREREQUISITES
    }
    state["ethos"].update(
        stage="complete", confirmed_digest=SHA, confirmation_source="User reviewed", validation="Passed"
    )
    record = {
        "schema_version": 1,
        "provider": "harbor",
        "ethos_digest": SHA,
        "plan": {
            "trace_choice": "none",
            "tasks": [
                {
                    "id": task_id,
                    "example_ids": [task_id + "-example"],
                    "title": task_id,
                    "input": "Request | with newline\ncontinued",
                    "behavior": "Respect policy",
                    "expected_result": "Correct disposition",
                    "grading": "Check state and response",
                }
                for task_id in ("refund", "reject")
            ],
            "presentation": {"coverage_note": "These check refunds and rejection. Other regions remain untested."},
        },
        "plan_approval": None,
        "first_task": "refund",
        "tasks": {},
        "selection": None,
        "runs": [],
        "run_review": None,
        "audit_invitation": None,
        "execution_settings": {
            "agent_model": "support / model; health unknown",
            "attempts": "1 per task, 2 total attempts",
            "validation": "Reference and relevant incorrect-result controls",
            "limits": "10 minutes, at most $1",
            "open_questions": [],
        },
    }
    state["scopes"]["create"] = {"first_eval": record}
    return state, record, helper, renderer, templates


def observations(record):
    files = {}
    for task in record["tasks"].values():
        for field in ("environment", "validation"):
            files.update({ref["path"]: ref["digest"] for ref in (task.get(field) or {}).get("evidence", [])})
    if record["selection"]:
        files.update(
            {
                ref["path"]: ref["digest"]
                for ref in [record["selection"]["config"], *record["selection"].get("inputs", [])]
            }
        )
    for run in record["runs"]:
        files.update({ref["path"]: ref["digest"] for ref in run["evidence"]})
        for attempt in run["attempts"]:
            files.update({ref["path"]: ref["digest"] for ref in attempt["evidence"]})
    summary = (record["run_review"] or {}).get("summary")
    if summary:
        files[summary["path"]] = summary["digest"]
    return {"tasks": {key: task["sha256"] for key, task in record["tasks"].items()}, "files": files}


def payload(creation, stage, **facts):
    state, record, *_ = creation
    return {
        "stage": "first_eval_" + stage,
        "state": state,
        "revision": state["revision"],
        "facts": {"observations": observations(record), **facts},
    }


def approved(creation):
    state, record, helper, *_ = creation
    record["plan_approval"] = {
        "digest": helper.derive(state, observations(record))["plan_digest"],
        "source": "User approves design",
    }


def built(creation, *, all_tasks=False, reviewed=False, validated=False):
    _, record, *_ = creation
    approved(creation)
    for task_id in ("refund", "reject") if all_tasks else ("refund",):
        check = {
            "sha256": SHA,
            "status": "passed",
            "source": "Observed reference result",
            "evidence": [{"path": f"checks/{task_id}.json", "digest": SHA}],
        }
        record["tasks"][task_id] = {
            "path": f".eval-author/tasks/{task_id}",
            "sha256": SHA,
            "review": {"sha256": SHA, "decision": "approved", "source": "User approves first task"}
            if reviewed
            else None,
            "environment": check,
            "validation": deepcopy(check) if validated else None,
            "limitations": ["Other regions untested"],
        }


def selected(creation):
    state, record, helper, *_ = creation
    built(creation, all_tasks=True, reviewed=True, validated=True)
    derived = helper.derive(state, observations(record))
    record["execution_settings_presented"] = {
        "digest": derived["settings_digest"],
        "source": "Delivered settings message",
    }
    record["selection"] = {
        "config": {"path": ".eval-author/run.yaml", "digest": SHA},
        "cases": [{"task_id": key, "example_ids": [key + "-example"], "attempts": 1} for key in record["tasks"]],
        "agent": "support",
        "model": "configured-model",
        "source": "Explicit run selection",
        "settings_digest": derived["settings_digest"],
    }
    digest = helper.derive(state, observations(record))["selection_digest"]
    record["selection"]["authorization"] = {"digest": digest, "source": "User approved bounded run"}


def finished(creation):
    state, record, helper, *_ = creation
    selected(creation)
    record["runs"] = [
        {
            "id": "baseline-job",
            "role": "agent",
            "status": "completed",
            "selection_digest": helper.derive(state, observations(record))["selection_digest"],
            "task_revisions": {key: SHA for key in record["tasks"]},
            "source": "Observed native job",
            "evidence": [{"path": ".eval-author/jobs/baseline/result.json", "digest": SHA}],
            "attempts": [
                {
                    "id": key + "-1",
                    "task_id": key,
                    "example_ids": [key + "-example"],
                    "native_id": "native-" + key,
                    "status": "completed",
                    "rewards": {"pass": 0},
                    "evidence": [{"path": f".eval-author/jobs/baseline/{key}-1/result.json", "digest": SHA}],
                }
                for key in record["tasks"]
            ],
        }
    ]
    record["run_review"] = {
        "run_id": "baseline-job",
        "attempt_id": "refund-1",
        "digest": helper.fingerprint(record["runs"][0]),
        "source": "Inspected real attempt",
        "limitations": ["Configured identity only; model health unknown"],
        "summary": {
            "path": ".eval-author/task-measurements/baseline-review.md",
            "digest": SHA,
            "run_id": "baseline-job",
            "attempt_id": "refund-1",
        },
    }
    record["selected_run_ids"] = ["baseline-job"]
    record["audit_invitation"] = {"status": "pending", "source": None}


def links(creation):
    state, record, *_ = creation
    root = state["repository"]
    result = {
        "report": {"target": root + "/.eval-author/report.html"},
        "readme": {"target": root + "/.eval-author/README.md"},
        "tasks_view": {"target": root + "/.eval-author/report.html", "fragment": "cases"},
        "task_readme": {"target": root + "/.eval-author/tasks/refund/README.md"},
        "rerun": {"target": root + "/.eval-author/README.md", "fragment": "rerun-evaluations"},
    }
    if record["runs"]:
        run = record["runs"][0]
        for key, refs in (
            ("run_evidence", run["evidence"]),
            ("attempt_evidence", run["attempts"][0]["evidence"] if run["attempts"] else []),
        ):
            result[key] = {ref["path"]: {"target": root + "/" + ref["path"]} for ref in refs}
    return result


def result_facts(creation):
    return {
        "links": links(creation),
        "input": "Refund request",
        "observed": "Denied eligible refund",
        "grade_explanation": "Wrong disposition",
        "quality_failures": "Two incorrect dispositions under the binary grader.",
        "identity_limitations": "Configured identity only; actual model health is unknown.",
        "reconciliation": "Delivered two tasks and two attempts; other regions remain untested.",
    }


def test_proposal_is_concise_deterministic_and_read_only(creation):
    state, record, _, renderer, copy = creation
    state["blockers"] = [{"id": "credentials", "phase": "create", "message": "No live-run key"}]
    before = json.dumps(state, sort_keys=True)
    output = renderer.render(payload(creation, "proposal"))
    assert output == (
        "Here are the proposed evaluation tasks.\n\n"
        "| Task | Input | Behavior under test | Expected result | Grading |\n|---|---|---|---|---|\n"
        "| refund | Request &#124; with newline<br>continued | Respect policy | Correct disposition | Check state and response |\n"
        "| reject | Request &#124; with newline<br>continued | Respect policy | Correct disposition | Check state and response |\n\n"
        "These check refunds and rejection. Other regions remain untested.\n\n"
        "I’ll build **refund** first.\n\n"
        "Proposed tasks: 2; distinct input examples: 2. After you approve the proposal, "
        "I’ll build only that first task and pause for your review before building any remaining tasks.\n\n"
        "Do these tasks make sense for your agent? Are we testing the right behaviors, "
        "with the right expected results and grading? Tell me what you’d change, add, "
        "or prioritize—or approve the proposal as written.\n"
    )
    assert output == renderer.render(payload(creation, "resume"))
    assert copy.PROPOSAL_TABLE in output
    assert "Request &#124; with newline<br>continued" in output
    assert output.endswith(copy.PROPOSAL_QUESTION + "\n")
    assert output.index("Other regions remain untested") < output.index("I’ll build **refund** first.")
    assert "- **Agent/model:" not in output and "Blockers" not in output and "SKILL.md" not in output
    assert json.dumps(state, sort_keys=True) == before
    record.pop("execution_settings")
    assert renderer.render(payload(creation, "proposal")) == output


@pytest.mark.parametrize("task_count", [1, 3])
def test_default_proposal_stays_small_and_explains_the_next_review(creation, task_count):
    _, record, _, renderer, copy = creation
    template = record["plan"]["tasks"][0]
    record["plan"]["tasks"] = [
        {**template, "id": f"task-{number}", "example_ids": [f"example-{number}"]} for number in range(task_count)
    ]
    record["first_task"] = "task-0"
    output = renderer.render(payload(creation, "proposal"))
    assert copy.PROPOSAL_PROCESS.format(tasks=task_count, examples=task_count) in output
    assert output.endswith(copy.PROPOSAL_QUESTION + "\n")
    assert "Time/cost limits" not in output


@pytest.mark.parametrize("broader_scope", ["more_tasks", "more_examples"])
def test_broader_proposal_requires_an_explicit_user_request(creation, broader_scope):
    state, record, helper, renderer, copy = creation
    if broader_scope == "more_tasks":
        template = record["plan"]["tasks"][0]
        record["plan"]["tasks"].extend(
            {**template, "id": f"extra-{number}", "example_ids": [f"extra-example-{number}"]} for number in range(2)
        )
    else:
        record["plan"]["tasks"][0]["example_ids"].append("second-refund-example")
    helper.validate(record)
    with pytest.raises(ValueError, match="1–3 tasks with one distinct input example"):
        renderer.render(payload(creation, "proposal"))
    record["plan"]["breadth_request"] = {"source": "User message: include the broader routing scope"}
    output = renderer.render(payload(creation, "proposal"))
    assert output.endswith(copy.PROPOSAL_QUESTION + "\n")
    record["plan"]["breadth_request"]["source"] = " "
    with pytest.raises(ValueError):
        renderer.render(payload(creation, "proposal"))
    record["plan"].pop("breadth_request")
    approved(creation)
    assert renderer.render(payload(creation, "resume")) == copy.FIRST_PROGRESS + "\n"
    assert helper.derive(state, observations(record))["scope_approved"]


def test_fixed_trace_options_and_saved_selection(creation):
    _, record, _, renderer, copy = creation
    record["plan"].pop("trace_choice")
    output = renderer.render(payload(creation, "traces"))
    assert output == (
        "We’ll use your confirmed `ETHOS.md` to propose your first evaluation tasks. "
        "Existing traces can help us use realistic requests, but they aren’t required.\n\n"
        "Would you like to use existing traces, or start from `ETHOS.md`?\n\n"
        "- Start from ETHOS.md\n- Use existing traces\n"
    )
    assert "synthetic" not in output
    record["plan"]["trace_choice"] = "none"
    assert renderer.render(payload(creation, "traces")) == copy.TRACE_INTRO + "\n"


def test_source_answer_is_required_before_any_proposal(creation):
    _, record, _, renderer, copy = creation
    record["plan"].pop("trace_choice")
    with pytest.raises(ValueError, match="choice of existing traces or ETHOS.md"):
        renderer.render(payload(creation, "proposal"))
    output = renderer.render(payload(creation, "resume"))
    assert copy.TRACE_QUESTION in output
    assert copy.PROPOSAL_TABLE not in output and copy.FIRST_PROGRESS not in output


def test_resume_without_design_asks_for_sources_without_requiring_file_observations(creation):
    state, _, _, renderer, copy = creation
    state["scopes"]["create"] = {}
    request = {"stage": "first_eval_resume", "state": state, "revision": state["revision"]}
    assert renderer.render(request) == renderer.render({**request, "stage": "first_eval_traces"})
    assert copy.TRACE_QUESTION in renderer.render(request)
    request["revision"] -= 1
    with pytest.raises(ValueError, match="revision differs"):
        renderer.render(request)


@pytest.mark.parametrize("choice", ["none", {"path": "traces/selected.json", "runs": ["chosen-run"]}])
def test_saved_source_is_reused_and_does_not_create_a_proposal_or_approval(creation, choice):
    state, record, helper, renderer, copy = creation
    record["plan"].pop("trace_choice")
    state["scopes"]["create"]["trace_choice"] = choice
    with pytest.raises(ValueError, match="Copy the saved source choice into the proposal"):
        renderer.render(payload(creation, "resume"))
    record["plan"]["trace_choice"] = choice
    output = renderer.render(payload(creation, "resume"))
    assert copy.TRACE_QUESTION not in output and copy.PROPOSAL_QUESTION in output
    assert not helper.derive(state, observations(record))["scope_approved"]
    state["scopes"]["create"].pop("first_eval")
    assert renderer.render(payload(creation, "traces")) == copy.TRACE_INTRO + "\n"
    with pytest.raises(ValueError, match="Save a small, concrete first-evaluation proposal"):
        renderer.render(payload(creation, "resume"))


def test_saved_design_approval_leads_directly_to_first_authoring(creation):
    _, _, _, renderer, copy = creation
    with pytest.raises(ValueError, match="scope approval"):
        renderer.render(payload(creation, "prepare_first"))
    with pytest.raises(ValueError, match="first-task approval"):
        renderer.render(payload(creation, "prepare_remaining"))
    approved(creation)
    assert renderer.render(payload(creation, "resume")) == copy.FIRST_PROGRESS + "\n"
    assert "[reference solution](https://docs.harborframework.com/tasks/solution)" in copy.FIRST_PROGRESS
    assert "[grader](https://docs.harborframework.com/tasks/verifier)" in copy.FIRST_PROGRESS
    with pytest.raises(ValueError, match="Review and approve the first task"):
        renderer.render(payload(creation, "execution_settings"))


def test_review_separates_proof_validation_execution_and_requires_links(creation):
    _, _, _, renderer, copy = creation
    built(creation)
    facts = {
        "links": links(creation),
        "input": "Refund request",
        "expected_result": "Refund allowed",
        "grading": "Inspect state",
        "grading_limitation": "Other regions untested",
    }
    output = renderer.render(payload(creation, "review_first", **facts))
    assert output == renderer.render(payload(creation, "resume", **facts))
    assert "Environment proof: passed. Grading validation: pending" in output
    assert "Agent execution: not completed" in output and copy.FIRST_QUESTION in output
    assert "report.html#cases>)" in output and "/tasks/refund/README.md>)" in output
    assert output.endswith("/README.md>).\n")
    assert "- **Agent/model:" not in output
    changed = payload(creation, "review_first", **facts)
    changed["facts"]["observations"]["tasks"]["refund"] = "b" * 64
    with pytest.raises(ValueError, match="current task files"):
        renderer.render(changed)
    facts["links"]["task_readme"]["target"] = "/unrelated/README.md"
    with pytest.raises(ValueError, match="actual saved repository artifact"):
        renderer.render(payload(creation, "review_first", **facts))


def test_late_settings_and_explicit_execution_authorization(creation):
    _, record, helper, renderer, copy = creation
    built(creation, all_tasks=True, reviewed=True, validated=True)
    output = renderer.render(payload(creation, "resume"))
    assert output.startswith(copy.SETTINGS_INTRO)
    assert [row.split("**")[1].rstrip(":") for row in output.splitlines() if row.startswith("- **")] == list(
        copy.SETTINGS_LABELS
    )
    assert not helper.derive(creation[0], observations(record))["execution_authorized"]
    with pytest.raises(ValueError, match="Display and save execution settings"):
        renderer.render(payload(creation, "ready_to_run", access_ready=True))
    selected(creation)
    output = renderer.render(payload(creation, "ready_to_run", access_ready=True))
    assert copy.CHECKS_PASSED in output and copy.RUN_PROGRESS in output
    record["selection"].pop("authorization")
    assert copy.RUN_QUESTION in renderer.render(payload(creation, "ready_to_run", access_ready=True))
    assert copy.RUN_PROGRESS not in renderer.render(
        payload(creation, "ready_to_run", missing_execution_question="Which model credential should this run use?")
    )


@pytest.mark.parametrize("dependency", ["unrelated_blocker", "later_environment", "open_question"])
def test_run_readiness_checks_only_current_dependencies_and_resolved_settings(creation, dependency):
    state, record, helper, renderer, copy = creation
    selected(creation)
    if dependency == "unrelated_blocker":
        state["blockers"] = [{"id": "audit-traces", "phase": "audit", "message": "Audit evidence missing"}]
        assert copy.RUN_PROGRESS in renderer.render(payload(creation, "ready_to_run", access_ready=True))
    elif dependency == "later_environment":
        record["tasks"]["reject"]["environment"]["status"] = "failed"
        with pytest.raises(ValueError, match="current environment proof"):
            renderer.render(payload(creation, "ready_to_run", access_ready=True))
    else:
        question = "Which maximum dollar amount should this run use?"
        record["execution_settings"]["open_questions"] = [question]
        record["execution_settings_presented"]["digest"] = helper.derive(state, observations(record))["settings_digest"]
        output = renderer.render(payload(creation, "ready_to_run", access_ready=True))
        assert question in output and copy.RUN_PROGRESS not in output and copy.RUN_QUESTION not in output


def test_blockers_are_saved_and_only_interrupt_dependent_steps(creation):
    state, _, _, renderer, copy = creation
    state["blockers"] = [{"id": "credentials", "phase": "create", "message": "Missing credential"}]
    approved(creation)
    assert renderer.render(payload(creation, "resume")) == copy.FIRST_PROGRESS + "\n"
    built(creation, all_tasks=True, reviewed=True, validated=True)
    with pytest.raises(ValueError, match="Saved blockers"):
        renderer.render(payload(creation, "execution_settings"))
    entries = [
        {
            "id": "credentials",
            "step": "Run tasks",
            "reason": "Credential missing.",
            "needed": "Configure the selected credential.",
        }
    ]
    output = renderer.render(payload(creation, "execution_settings", blockers=entries))
    assert "### Blockers" in output and "**Needed:** Configure the selected credential." in output


@pytest.mark.parametrize("decision", ["pending", "declined", "accepted"])
def test_results_preserve_exact_attempt_rerun_and_final_audit_invitation(creation, decision):
    _, record, _, renderer, copy = creation
    finished(creation)
    record["audit_invitation"] = {"status": decision, "source": None if decision == "pending" else "User decision"}
    output = renderer.render(payload(creation, "results", **result_facts(creation)))
    assert "Reviewed run: **baseline-job**. Attempt: **refund-1** (native identity: native-refund)" in output
    assert "2/2 completed attempts with grades; 0 execution failures" in output
    assert "Agent-quality failures: Two incorrect dispositions" in output
    assert "/refund-1/result.json>)" in output and "README.md#rerun-evaluations>)" in output
    assert "unknown" in output
    assert (copy.AUDIT_QUESTION in output) == (decision == "pending")
    assert output.endswith("/README.md>).\n")
    assert output.count("[the report]") == output.count("[the suite README]") == 1
    if decision == "pending":
        assert output.split(copy.AUDIT_QUESTION + "\n\n")[1].startswith("Review your evaluations in")


def test_missing_evidence_is_incomplete_and_later_runs_do_not_replace_review(creation):
    _, record, _, renderer, copy = creation
    finished(creation)
    later = deepcopy(record["runs"][0])
    later["id"] = "later-job"
    later["attempts"][0]["id"] = "later-attempt"
    record["runs"].append(later)
    record["selected_run_ids"] = ["baseline-job"]
    request = payload(creation, "results", **result_facts(creation))
    request["facts"]["observations"]["files"][record["runs"][0]["attempts"][1]["evidence"][0]["path"]] = None
    output = renderer.render(request)
    assert output.startswith("Your first evaluations remain incomplete.")
    assert "1 retained attempts with stale or unavailable evidence" in output
    assert "later-attempt" not in output and copy.AUDIT_QUESTION not in output and "[x]" not in output
    request["facts"]["observations"]["files"][record["runs"][0]["attempts"][0]["evidence"][0]["path"]] = None
    output = renderer.render(request)
    assert "Agent performance has not been established" in output and "### Blockers" in output
    assert "Saved review identity: run **baseline-job**; attempt **refund-1**" in output
    assert "Reviewed run:" not in output and copy.AUDIT_QUESTION not in output


@pytest.mark.parametrize("outcome", ["controls", "no_attempts", "missing_summary", "missing_selection"])
def test_missing_agent_review_renders_incomplete_inventory_without_success(creation, outcome):
    _, record, _, renderer, copy = creation
    finished(creation)
    if outcome == "controls":
        record["runs"][0]["role"] = "control"
    elif outcome == "no_attempts":
        record["runs"][0]["attempts"] = []
    elif outcome == "missing_selection":
        record["selected_run_ids"] = []
    request = payload(creation, "results", **result_facts(creation))
    if outcome == "missing_summary":
        request["facts"]["observations"]["files"][record["run_review"]["summary"]["path"]] = None
    output = renderer.render(request)
    assert output.startswith("Your first evaluations remain incomplete.")
    assert "### Blockers" in output and "Agent performance has not been established" in output
    assert "[x]" not in output and copy.AUDIT_QUESTION not in output and "Reviewed run:" not in output


def test_execution_failures_and_missing_attempts_are_distinct_from_agent_quality(creation):
    _, record, helper, renderer, copy = creation
    finished(creation)
    run = record["runs"][0]
    run["status"] = "infrastructure_error"
    run["attempts"][1]["status"] = "infrastructure_error"
    run["attempts"][1]["rewards"] = {}
    record["run_review"]["digest"] = helper.fingerprint(run)
    output = renderer.render(payload(creation, "results", **result_facts(creation)))
    assert "1/2 completed attempts with grades; 1 execution failures" in output
    assert "0 missing attempts" in output and copy.AUDIT_QUESTION not in output
    run["attempts"].pop()
    record["run_review"]["digest"] = helper.fingerprint(run)
    output = renderer.render(payload(creation, "results", **result_facts(creation)))
    assert "1 missing attempts" in output and "0 execution failures" in output


def test_native_gym_author_progress_and_rows_are_retained(creation):
    state, record, _, renderer, copy = creation
    state["setup"] = record["provider"] = "gym"
    approved(creation)
    assert renderer.render(payload(creation, "resume")) == copy.GYM_PROGRESS + "\n"
    assert "native Gym verifier" in copy.GYM_PROGRESS and "harborframework" not in copy.GYM_PROGRESS


def test_completed_evidence_cannot_override_saved_blockers_or_shared_readiness(creation):
    state, _, _, renderer, copy = creation
    finished(creation)
    state["blockers"] = [{"id": "run-access", "phase": "create", "message": "Access remains unresolved"}]
    facts = result_facts(creation)
    facts["blockers"] = [
        {
            "id": "run-access",
            "step": "Finish evaluations",
            "reason": "Access remains unresolved.",
            "needed": "Reconcile the saved access finding.",
        }
    ]
    output = renderer.render(payload(creation, "results", **facts))
    assert output.startswith("Your first evaluations remain incomplete.")
    assert "### Blockers" in output and "[x]" not in output and copy.AUDIT_QUESTION not in output
    state["agent"] = None
    with pytest.raises(ValueError, match="shared prerequisites"):
        renderer.render(payload(creation, "results", **facts))
    assert "### Blockers" in renderer.render(payload(creation, "blockers", blockers=facts["blockers"]))


def test_unconfirmed_ethos_and_other_journeys_cannot_use_creation(creation):
    state, _, _, renderer, _ = creation
    state["ethos"]["stage"] = "review"
    with pytest.raises(ValueError, match="Confirm ETHOS.md"):
        renderer.render(payload(creation, "traces"))
    state["ethos"]["stage"] = "complete"
    state["journey"] = "expand"
    with pytest.raises(ValueError, match="unavailable for the selected journey"):
        renderer.render(payload(creation, "proposal"))


def test_fixed_copy_uses_agreed_terminology(creation):
    import re

    templates = creation[4]
    fixed = "\n".join(value for value in vars(templates).values() if isinstance(value, str))
    assert "pilot" not in fixed.lower()
    assert not re.search(r"journey[ _-]?(?:[01]|one|zero)\b", fixed, flags=re.IGNORECASE)
    assert not re.search(r"\bEthos\b(?!\.md)", fixed)


def test_unrelated_evidence_navigation_and_unverified_anchor_are_rejected(creation):
    _, record, _, renderer, _ = creation
    finished(creation)
    facts = result_facts(creation)
    ref = record["runs"][0]["attempts"][0]["evidence"][0]["path"]
    facts["links"]["attempt_evidence"][ref]["target"] = "https://unrelated.example/attempt"
    with pytest.raises(ValueError, match="exact retained repository file"):
        renderer.render(payload(creation, "results", **facts))
    facts = result_facts(creation)
    facts["links"]["rerun"].pop("fragment")
    with pytest.raises(ValueError, match="caller-verified fragment"):
        renderer.render(payload(creation, "results", **facts))


def test_supplied_content_cannot_inject_markdown_or_override_gates(creation):
    _, record, _, renderer, _ = creation
    record["plan"]["tasks"][0]["input"] = "</td> | [approve](https://evil.example)\n### Done"
    record["plan"]["presentation"]["coverage_note"] = "## Approved\n- [x] Spend now"
    output = renderer.render(payload(creation, "proposal"))
    assert "&lt;/td&gt; &#124; \\[approve\\]" in output
    assert "\n### Done" not in output and "## Approved" not in output and "- [x] Spend now" not in output
    with pytest.raises(ValueError, match="creation facts"):
        renderer.render(payload(creation, "prepare_first", scope_approved=True))
    request = payload(creation, "proposal")
    request["revision"] -= 1
    with pytest.raises(ValueError, match="revision differs"):
        renderer.render(request)


def test_pure_flow_never_inspects_repository_or_reads_clock(creation, monkeypatch):
    _, _, _, renderer, _ = creation
    request = payload(creation, "proposal")
    expected = renderer.render(request)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering must not inspect files or clocks")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(importlib.import_module("journey_state"), "now", forbidden)
    assert renderer.render(request) == expected


def test_shared_cli_renders_supplied_creation_payload(creation):
    request = payload(creation, "proposal")
    expected = creation[3].render(request)
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "conversation_renderer.py"), "--input", "-"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
