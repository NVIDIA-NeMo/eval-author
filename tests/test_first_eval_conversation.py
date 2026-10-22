# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conversation gates and navigation use real saved state, not prompt snapshots."""

import copy
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_first_eval_state import (
    approve_plan,
    finished,
    reference,
    save_record,
    selection_ready,
    start,
    task,
    validate_and_review,
)
from test_journey_state import ethos_ready, setup_ready

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def context(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    state = importlib.import_module("journey_state")
    helper = importlib.import_module("first_eval_state")
    conversation = importlib.import_module("first_eval_conversation")
    return (state, helper), conversation, tmp_path


def render(context, stage, **facts):
    (state, _), conversation, repo = context
    return conversation.render(repo, {"revision": state.load_state(repo)["revision"], "stage": stage, "facts": facts})


def proposal(context):
    modules, _, repo = context
    record = start(modules, repo)
    for item in record["plan"]["tasks"]:
        item.update(
            title=item["id"],
            input="Request | with newline\ncontinued",
            behavior="Respect policy",
            expected_result="Correct disposition",
            grading="Check state and response",
        )
    record["plan"]["presentation"] = {
        "agent_model": "support / model (health unknown)",
        "attempts": "2 per task; 4 total executions",
        "validation": "Reference checks and incorrect-result controls; no paid grading",
        "limits": "10 minutes; at most $1",
        "open_questions": [],
        "coverage_note": "These test refunds and policy. Other regions remain untested.",
        "request_execution_authorization": False,
    }
    save_record(modules[0], repo, record)
    return record


def artifacts(repo):
    reference(repo, ".eval-author/report.html", '<html><div id="cases">Tasks</div></html>')
    reference(repo, ".eval-author/README.md", "# Summary\n\n## Rerun evaluations\n\nExplicit saved commands.\n")


def review_facts():
    return {
        "input": "Refund request",
        "expected_result": "Refund allowed",
        "grading": "Inspect final state",
        "grading_limitation": "Does not cover other regions",
    }


def result_facts():
    return {
        "input": "Refund request",
        "observed": "Denied eligible refund",
        "grade_explanation": "Incorrect disposition",
        "quality_failures": "Two incorrect dispositions under the binary fixture grader.",
        "identity_limitations": "Identity is the recorded configuration; model health remains unknown.",
        "reconciliation": "Delivered two tasks and two attempts; additional regions remain out of scope.",
    }


def test_proposal_shape_and_fixed_feedback_are_repeatable_and_read_only(context):
    modules, conversation, repo = context
    proposal(context)
    before = modules[0].state_path(repo).read_bytes()
    output = render(context, "proposal")
    assert output == render(context, "resume") == render(context, "proposal")
    assert "| Task | Input | Behavior under test | Expected result | Grading |" in output
    assert "Request \\| with newline<br>continued" in output
    labels = ["Agent/model", "Tasks and examples", "Attempts", "Validation", "Time/cost limits", "Open questions"]
    assert [row.split("**")[1].rstrip(":") for row in output.splitlines() if row.startswith("- **")] == labels
    assert "| Check state and response |\n\n- **Agent/model:" in output
    assert conversation.PROPOSAL_QUESTION in output
    assert "This approval covers the task design" in output
    assert modules[0].state_path(repo).read_bytes() == before


def test_trace_choice_has_fixed_options_before_a_proposal_and_is_read_only(context):
    modules, conversation, repo = context
    state = modules[0]
    setup_ready(state, repo)
    with pytest.raises(ValueError, match="Confirm ETHOS.md"):
        render(context, "traces")
    ethos_ready(state, repo)
    assert "first_eval" not in state.load_state(repo)["scopes"].get("create", {})
    before = state.state_path(repo).read_bytes()

    output = render(context, "traces")

    assert output == (
        conversation.TRACE_INTRO
        + "\n\n"
        + conversation.TRACE_QUESTION
        + "\n\n- Start from ETHOS.md\n- Use existing traces\n"
    )
    assert output == render(context, "traces")
    assert state.state_path(repo).read_bytes() == before


@pytest.mark.parametrize("location", ["scope", "plan"])
@pytest.mark.parametrize("choice", ["none", {"locations": ["traces/selected"], "runs": ["run-1"], "exclusions": []}])
def test_trace_question_and_options_are_skipped_for_saved_source_choices(context, location, choice):
    modules, conversation, repo = context
    record = proposal(context)
    state = modules[0]
    if location == "scope":
        state.update(
            repo,
            {"scopes": {"create": {"trace_choice": choice}}},
            expected_revision=state.load_state(repo)["revision"],
        )
    else:
        record["plan"]["trace_choice"] = choice
        save_record(state, repo, record)
    before = state.state_path(repo).read_bytes()

    assert render(context, "traces") == conversation.TRACE_INTRO + "\n"
    assert state.state_path(repo).read_bytes() == before


def test_combined_offer_is_not_execution_authorization(context):
    modules, _, repo = context
    record = proposal(context)
    record["plan"]["presentation"]["request_execution_authorization"] = "false"
    save_record(modules[0], repo, record)
    with pytest.raises(ValueError, match="must be true or false"):
        render(context, "proposal")
    record["plan"]["presentation"]["request_execution_authorization"] = True
    save_record(modules[0], repo, record)
    assert "approve building and running within the displayed settings and limits" in render(context, "proposal")
    approve_plan(modules, repo, record)
    assert not modules[1].progress(repo, modules[0].load_state(repo))["execution_authorized"]
    assert "reference solution](https://docs.harborframework.com/tasks/solution)" in render(context, "prepare_first")
    assert "grader](https://docs.harborframework.com/tasks/verifier)" in render(context, "prepare_first")


def test_no_preapproval_generation_messages_or_stale_revision(context):
    modules, conversation, repo = context
    proposal(context)
    with pytest.raises(ValueError, match="scope approval"):
        render(context, "prepare_first")
    with pytest.raises(ValueError, match="first-task approval"):
        render(context, "prepare_remaining")
    with pytest.raises(ValueError, match="stale"):
        conversation.render(repo, {"stage": "proposal", "revision": 0})


def test_first_review_requires_environment_and_artifacts_but_not_formal_controls(context):
    modules, conversation, repo = context
    record = proposal(context)
    approve_plan(modules, repo, record)
    item = task(modules, repo, "refund")
    reference(repo, f"{item['path']}/README.md", "# Refund\n\n## Rerun task\n")
    item["sha256"] = modules[1].task_digest(repo, item["path"])
    record["tasks"]["refund"] = item
    save_record(modules[0], repo, record)
    with pytest.raises(ValueError, match="environment proof"):
        render(context, "review_first", **review_facts())
    validate_and_review(repo, "refund", item)
    item["review"] = None
    item["validation"] = None
    save_record(modules[0], repo, record)
    with pytest.raises(ValueError, match="does not exist"):
        render(context, "review_first", **review_facts())
    artifacts(repo)
    output = render(context, "review_first", **review_facts())
    assert conversation.FIRST_QUESTION in output
    assert "Environment proof: passed. Grading validation: pending" in output
    assert "Agent execution: not completed" in output
    assert "#cases>" in output and "/tasks/refund/README.md>" in output
    with pytest.raises(ValueError, match="anchor is absent"):
        render(context, "review_first", **review_facts(), tasks_view=".eval-author/report.html#tasks")
    (repo / item["path"] / "instruction.md").write_text("edited behavior")
    with pytest.raises(ValueError, match="current task files"):
        render(context, "review_first", **review_facts())


def test_run_announcement_requires_authorization_credentials_and_no_blocker(context):
    modules, conversation, repo = context
    record = selection_ready(modules, repo)
    question = "Which credential should this approved run use?"
    output = render(context, "ready_to_run", missing_execution_question=question)
    assert conversation.CHECKS_PASSED in output and conversation.RUN_PROGRESS not in output
    assert question in output
    assert conversation.RUN_PROGRESS in render(context, "ready_to_run", access_ready=True)
    record["selection"].pop("authorization")
    save_record(modules[0], repo, record)
    assert conversation.RUN_PROGRESS not in render(
        context, "ready_to_run", access_ready=True, missing_execution_question="May I run within these limits?"
    )


def test_blockers_are_structured_and_saved_blockers_cannot_disappear(context):
    modules, _, repo = context
    proposal(context)
    modules[0].update(repo, {"blockers": [{"id": "credential", "phase": "create", "message": "Credential missing"}]})
    with pytest.raises(ValueError, match="Saved blockers"):
        render(context, "proposal")
    entries = [{"step": "Run tasks", "reason": "Credential missing.", "needed": "Configure MODEL_KEY locally."}]
    output = render(context, "proposal", blockers=entries)
    assert "### Blockers\n\n- **Run tasks:** Credential missing. **Needed:** Configure MODEL_KEY locally." in output
    assert render(context, "blockers", blockers=entries).startswith("### Blockers")


@pytest.mark.parametrize("decision", ["pending", "declined", "accepted"])
def test_results_link_exact_attempt_and_verified_reruns_and_only_invite_when_pending(context, decision):
    modules, conversation, repo = context
    record = finished(modules, repo)
    artifacts(repo)
    record["audit_invitation"] = {"status": "pending", "source": None}
    save_record(modules[0], repo, record)
    if decision != "pending":
        record["audit_invitation"] = {"status": decision, "source": "user reply"}
        save_record(modules[0], repo, record)
    output = render(context, "results", **result_facts())
    assert "Reviewed run: **baseline-job**. Attempt: **refund-1**" in output
    assert "/jobs/baseline/refund-1/result.json>" in output
    assert "**[saved run instructions](<" in output and "README.md#rerun-evaluations>" in output
    assert (conversation.AUDIT_QUESTION in output) == (decision == "pending")
    assert output.endswith(f"[Markdown summary](<{repo}/.eval-author/README.md>)\n")
    if decision == "pending":
        assert output.index(conversation.AUDIT_QUESTION) < output.index("[Report]")
    (repo / ".eval-author/README.md").write_text("# Summary without run instructions\n")
    with pytest.raises(ValueError, match="anchor is absent"):
        render(context, "results", **result_facts())


def test_later_runs_do_not_silently_replace_reviewed_attempt(context):
    modules, _, repo = context
    record = finished(modules, repo)
    artifacts(repo)
    later = copy.deepcopy(record["runs"][0])
    later["id"] = "later-job"
    later["attempts"][0]["id"] = "later-attempt"
    record["runs"].append(later)
    record["audit_invitation"] = {"status": "pending", "source": None}
    save_record(modules[0], repo, record)
    output = render(context, "results", **result_facts())
    assert "baseline-job" in output and "later-attempt" not in output


def test_completed_handoff_requires_recording_the_audit_offer(context):
    modules, _, repo = context
    finished(modules, repo)
    artifacts(repo)
    with pytest.raises(ValueError, match="pending audit invitation"):
        render(context, "results", **result_facts())


def test_multiple_selected_jobs_show_suite_totals_separately_from_reviewed_subset(context):
    modules, _, repo = context
    record = finished(modules, repo)
    artifacts(repo)
    original = record["runs"][0]
    runs = []
    for index, attempt in enumerate(original["attempts"]):
        run = copy.deepcopy(original)
        run["id"] = f"subset-{index}"
        run["attempts"] = [attempt]
        run["task_revisions"] = {attempt["task_id"]: original["task_revisions"][attempt["task_id"]]}
        runs.append(run)
    record["runs"] = [original, *runs]
    record["selected_run_ids"] = [run["id"] for run in runs]
    record["run_review"].update(run_id=runs[0]["id"], digest=modules[1].fingerprint(runs[0]))
    record["audit_invitation"] = {"status": "pending", "source": None}
    save_record(modules[0], repo, record)
    output = render(context, "results", **result_facts())
    assert "Selected suite: 2/2 completed attempts with current evidence across 2 selected runs" in output
    assert "Run **subset-0**: 1/1 completed attempts with grades" in output
    assert "1 missing attempts" not in output


def test_shared_input_is_one_distinct_example(context):
    modules, _, repo = context
    record = proposal(context)
    for proposed in record["plan"]["tasks"]:
        proposed["example_ids"] = ["shared-request"]
    save_record(modules[0], repo, record)
    assert "2 tasks; 1 distinct input examples" in render(context, "proposal")


def test_native_gym_progress_uses_reference_fixtures(context):
    modules, conversation, repo = context
    record = start(modules, repo, "gym")
    approve_plan(modules, repo, record)
    output = render(context, "prepare_first")
    assert output == conversation.GYM_PROGRESS + "\n"
    assert "native Gym verifier" in output and "harborframework" not in output


def test_harbor_workflow_retains_explicit_run_yaml_name():
    root = SCRIPTS.parents[2]
    for relative in (
        "skills/eval-author-first-eval/SKILL.md",
        "skills/eval-author-first-eval/references/harbor-first-eval.md",
    ):
        source = (root / relative).read_text()
        assert ".eval-author/run.yaml" in source
        # Migration guidance may name the old file; executable examples may not.
        for command_block in source.split("```bash")[1:]:
            assert "first-eval.yaml" not in command_block.split("```", 1)[0]


def test_workflow_uses_behavioral_names_without_numbered_journey_labels():
    root = SCRIPTS.parents[2]
    numbered = re.compile(r"\bjourney[ _-]?(?:[01]|zero|one)\b", re.IGNORECASE)
    for folder in ("skills", "docs", "tests/scenarios"):
        for path in (root / folder).rglob("*"):
            if path.is_file() and path.suffix in {".md", ".py", ".json"}:
                assert not numbered.search(path.read_text()), f"Numbered workflow label in {path.relative_to(root)}"


def test_partial_agent_results_explain_unfinished_work_without_audit(context):
    modules, conversation, repo = context
    record = finished(modules, repo)
    artifacts(repo)
    run = record["runs"][0]
    run["status"] = "infrastructure_error"
    run["attempts"].pop()
    record["run_review"]["digest"] = modules[1].fingerprint(run)
    save_record(modules[0], repo, record, journey_status={"create": "in_progress"})
    output = render(context, "results", **result_facts())
    assert output.startswith("Your first evaluations remain incomplete.")
    assert "1/2 completed attempts" in output and "1 missing attempts" in output
    assert conversation.AUDIT_QUESTION not in output and "[x]" not in output


def test_control_only_results_cannot_be_presented_as_agent_results(context):
    modules, _, repo = context
    record = finished(modules, repo)
    record["runs"][0]["role"] = "control"
    record["run_review"]["digest"] = modules[1].fingerprint(record["runs"][0])
    save_record(modules[0], repo, record, journey_status={"create": "in_progress"})
    with pytest.raises(ValueError, match="exact retained current agent"):
        render(context, "results", **result_facts())


def test_unreviewed_attempt_with_missing_evidence_is_not_counted_as_completed(context):
    modules, _, repo = context
    record = finished(modules, repo)
    artifacts(repo)
    (repo / record["runs"][0]["attempts"][1]["evidence"][0]["path"]).unlink()
    output = render(context, "results", **result_facts())
    assert "1/2 completed attempts with grades" in output
    assert "1 retained attempts with stale or unavailable evidence" in output
    assert "Your first evaluations remain incomplete" in output


def test_navigation_rejects_missing_anchors_and_symlinks(context):
    _, conversation, repo = context
    artifacts(repo)
    assert conversation.heading_ids("# Rerun evaluations\n# Rerun evaluations\n") == {
        "rerun-evaluations",
        "rerun-evaluations-1",
    }
    with pytest.raises(ValueError, match="requires a verified"):
        conversation.artifact(repo, ".eval-author/README.md", "saved run instructions", fragment=True)
    (repo / "shortcut.md").symlink_to(repo / ".eval-author/README.md")
    with pytest.raises(ValueError, match="symlink"):
        conversation.artifact(repo, "shortcut.md", "unsafe link")
    with pytest.raises(ValueError, match="cannot escape"):
        conversation.artifact(repo, "../elsewhere.md", "unsafe link")


def test_cli_reports_invalid_input_without_mutation(context):
    modules, _, repo = context
    proposal(context)
    before = modules[0].state_path(repo).read_bytes()
    input_path = repo / "conversation.json"
    input_path.write_text(json.dumps({"stage": "proposal", "revision": 0}))
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "first_eval_conversation.py"), "--repo", str(repo), "--input", str(input_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "stale" in result.stderr and not result.stdout
    assert modules[0].state_path(repo).read_bytes() == before
