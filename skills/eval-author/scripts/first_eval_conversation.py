#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the first-evaluation conversation from saved state and inspected facts.

This is a read-only presentation boundary, not a source of approvals or proof.
Provider observations must be recorded through journey_state before rendering.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import first_eval_state
import journey_state
from markdown_it import MarkdownIt

TRACE_INTRO = (
    "We’ll use your confirmed `ETHOS.md` to propose your first evaluation tasks. "
    "Existing traces can help us use realistic requests, but they aren’t required."
)
TRACE_QUESTION = "Would you like to use existing traces, or start from `ETHOS.md`?"
TRACE_CHOICES = ("Start from ETHOS.md", "Use existing traces")
PROPOSAL_QUESTION = (
    "Do these tasks make sense for your agent? Are we testing the right behaviors, "
    "with the right expected results and grading? Tell me what you’d change, add, "
    "or prioritize—or approve the proposal as written."
)
FIRST_PROGRESS = (
    "I’m preparing the first task’s starting data and checking that its environment works. "
    "Then I’ll add the [reference solution](https://docs.harborframework.com/tasks/solution) "
    "and [grader](https://docs.harborframework.com/tasks/verifier)."
)
GYM_PROGRESS = (
    "I’m preparing the first task’s starting data and checking that its environment works. "
    "Then I’ll add the reference fixtures and native Gym verifier."
)
FIRST_QUESTION = (
    "Does this represent the behavior you want to test, and would its grading judge the agent fairly? "
    "Approve the first task or tell me what to change before I build the remaining tasks."
)
REMAINDER_PROGRESS = (
    "I’ll use the approved pattern to build the remaining tasks, then check that their graders "
    "accept reference results and reject relevant incorrect results."
)
CHECKS_PASSED = (
    "All selected tasks passed their reference checks. Those checks establish that the tasks work; "
    "they don’t measure your agent’s performance."
)
RUN_PROGRESS = "I’ll now run the approved tasks with the agreed settings."
RUN_QUESTION = "May I run the selected tasks with the displayed settings and limits?"
AUDIT_QUESTION = (
    "Would you like to see how you could improve your suite further? We can audit its coverage "
    "and grading against the behavior described in `ETHOS.md` to identify gaps and worthwhile improvements."
)
STAGES = {
    "traces",
    "proposal",
    "execution_settings",
    "prepare_first",
    "review_first",
    "prepare_remaining",
    "ready_to_run",
    "results",
    "resume",
    "blockers",
}


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value.strip()


def cell(value: object, label: str) -> str:
    """Keep inspected source content within its own Markdown table cell."""
    return text(value, label).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def line(value: object, label: str) -> str:
    return text(value, label).replace("\r", "").replace("\n", " ")


class HTMLIds(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.ids.update(value for key, value in attrs if key == "id" and value is not None)


def heading_ids(source: str) -> set[str]:
    """GitHub-style heading slugs, including duplicate-heading suffixes."""
    tokens = MarkdownIt().parse(source)
    result = set()
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        children = tokens[index + 1].children or []
        title = "".join(child.content for child in children if child.type in {"text", "code_inline"})
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        candidate = slug
        suffix = 0
        while candidate in result:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        result.add(candidate)
    return result


def artifact(repo: Path, value: object, label: str, *, fragment: bool = False) -> str:
    """Resolve an existing local target, verifying any actual navigation anchor."""
    value = text(value, label)
    relative, _, anchor = value.partition("#")
    path = first_eval_state._path(repo, relative)
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {relative}")
    if fragment and not anchor:
        raise ValueError(f"{label} requires a verified heading or view anchor")
    if anchor:
        content = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".html", ".htm"}:
            parser = HTMLIds()
            parser.feed(content)
            anchors = parser.ids
        elif path.suffix.lower() == ".md":
            anchors = heading_ids(content)
        else:
            raise ValueError(f"{label} does not support fragment navigation")
        if anchor not in anchors:
            raise ValueError(f"{label} anchor is absent: {anchor}")
    target = str(path) + (f"#{anchor}" if anchor else "")
    # Angle-bracket targets preserve paths containing spaces and parentheses.
    return f"[{label}](<{target}>)"


def blockers(entries: object) -> str:
    if not isinstance(entries, list) or not entries:
        raise ValueError("Blockers need at least one blocked step, cause, and needed action")
    lines = ["### Blockers", ""]
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"step", "reason", "needed"}:
            raise ValueError("Each blocker needs step, reason, and needed")
        lines.append(
            f"- **{line(entry['step'], 'blocked step')}:** {line(entry['reason'], 'blocker reason')} "
            f"**Needed:** {line(entry['needed'], 'needed action')}"
        )
    return "\n".join(lines)


def reports(repo: Path, facts: dict) -> str:
    return "\n\n".join(
        [
            artifact(repo, facts.get("report", ".eval-author/report.html"), "Report"),
            artifact(repo, facts.get("markdown", ".eval-author/README.md"), "Markdown summary"),
        ]
    )


def proposal(record: dict) -> str:
    plan = record["plan"]
    presentation = plan.get("presentation", {})
    if not isinstance(presentation, dict):
        raise ValueError("Proposal presentation must be an object")
    rows = ["| Task | Input | Behavior under test | Expected result | Grading |", "|---|---|---|---|---|"]
    for task in plan["tasks"]:
        rows.append(
            "| "
            + " | ".join(
                cell(task.get(key), key) for key in ("title", "input", "behavior", "expected_result", "grading")
            )
            + " |"
        )
    first = next(task for task in plan["tasks"] if task["id"] == record["first_task"])
    paragraphs = [
        "Here are the proposed evaluation tasks.",
        "\n".join(rows),
        text(presentation.get("coverage_note"), "coverage_note"),
        f"I’ll build **{line(first.get('title'), 'first task title')}** first.",
        PROPOSAL_QUESTION,
    ]
    return "\n\n".join(paragraphs)


def execution_settings(record: dict) -> str:
    settings = first_eval_state.execution_settings(record)
    questions = settings.get("open_questions")
    if not isinstance(questions, list):
        raise ValueError("Execution settings need open_questions, including an empty list when resolved")
    tasks = record["plan"]["tasks"]
    examples = len({example for task in tasks for example in task["example_ids"]})
    bullets = [
        f"- **Agent/model:** {line(settings.get('agent_model'), 'agent_model')}",
        f"- **Tasks and examples:** {len(tasks)} tasks; {examples} distinct input examples.",
        f"- **Attempts:** {line(settings.get('attempts'), 'attempts')}",
        f"- **Validation:** {line(settings.get('validation'), 'validation')}",
        f"- **Time/cost limits:** {line(settings.get('limits'), 'limits')}",
        "- **Open questions:** " + ("; ".join(line(item, "open question") for item in questions) or "None."),
    ]
    return "\n\n".join(
        [
            "Here are the settings for running the approved tasks.",
            "\n".join(bullets),
            "Agent runs and paid grading require authorization within these limits.",
        ]
    )


def first_review(repo: Path, record: dict, progress: dict, facts: dict) -> str:
    task_id = record["first_task"]
    status = progress["tasks"][task_id]
    if not progress["scope_approved"] or not status["created"] or not status.get("environment_proven"):
        raise ValueError("First-task review requires saved scope approval, current task files, and environment proof")
    task = record["tasks"][task_id]
    # Actual report currently names the Tasks navigation target 'cases'.
    tasks_view = artifact(repo, facts.get("tasks_view", ".eval-author/report.html#cases"), "Tasks view", fragment=True)
    readme = artifact(repo, f"{task['path']}/README.md", "task README")
    footer = reports(repo, facts)  # Both artifacts must exist before asking for review.
    validation = "passed" if status["validated"] else "pending or requiring attention"
    execution = "completed" if status["executed"] else "not completed for the agreed settings"
    return "\n\n".join(
        [
            f"- **Input:** {line(facts.get('input'), 'input')}\n"
            f"- **Expected result:** {line(facts.get('expected_result'), 'expected_result')}\n"
            f"- **Grading:** {line(facts.get('grading'), 'grading')} "
            f"Limitation: {line(facts.get('grading_limitation'), 'grading_limitation')}\n"
            f"- **Status:** Environment proof: passed. Grading validation: {validation}. Agent execution: {execution}.",
            f"{tasks_view} · {readme}",
            FIRST_QUESTION,
            footer,
        ]
    )


def results(repo: Path, record: dict, progress: dict, facts: dict) -> str:
    review = record["run_review"]
    if not review or not progress["reviewed_run_current"]:
        raise ValueError("Results review requires an exact retained current agent run and attempt review")
    run = next(item for item in record["runs"] if item["id"] == review["run_id"])
    attempt = next(item for item in run["attempts"] if item["id"] == review["attempt_id"])
    selected = record["selection"]
    planned = sum(
        case["attempts"] * (len(case["example_ids"]) if record["provider"] == "gym" else 1)
        for case in selected["cases"]
        if case["task_id"] in run["task_revisions"]
    )
    attempt_progress = progress["runs"][run["id"]]["attempts"]
    completed = sum(item["completed"] for item in attempt_progress.values())
    infrastructure = sum(
        item["status"] == "infrastructure_error" and attempt_progress[item["id"]]["reviewable"]
        for item in run["attempts"]
    )
    ungraded = sum(
        item["status"] == "completed" and not item["rewards"] and attempt_progress[item["id"]]["reviewable"]
        for item in run["attempts"]
    )
    unavailable = sum(not item["reviewable"] for item in attempt_progress.values())
    quality = text(facts.get("quality_failures"), "quality_failures (interpret the actual grading criteria)")
    parts = []
    if not progress["complete"]:
        pending = [
            key
            for key, status in progress["tasks"].items()
            if not status["created"] or not status["validated"] or not status["executed"]
        ]
        parts.append(
            "Your first evaluations remain incomplete. Tasks with unfinished work: "
            + (", ".join(pending) or "run completion")
            + "."
        )
        parts.append(
            "\n".join(
                f"- **{task_id}:** Created: {'yes' if status['created'] else 'no'}; "
                f"validated: {'yes' if status['validated'] else 'no'}; "
                f"agreed executions complete: {'yes' if status['executed'] else 'no'}."
                for task_id, status in progress["tasks"].items()
            )
        )
    suite = progress["suite"]
    parts.append(
        f"Selected suite: {suite['completed_attempts']}/{suite['planned_attempts']} completed attempts "
        f"with current evidence across {len(suite['selected_run_ids'])} selected runs "
        f"({suite['observed_attempts']} observed attempts). "
        f"Selected run IDs: {', '.join(suite['selected_run_ids']) or 'none'}."
    )
    if suite.get("job_errors"):
        parts.append(
            "Retained jobs with execution errors: "
            + ", ".join(suite["job_errors"])
            + ". Successful attempts from these jobs count only where their evidence is current. "
            + (
                "The selected recovery runs account for the remaining work."
                if suite["complete"]
                else "Run completion still requires attention."
            )
        )
    parts.extend(
        [
            f"Run **{line(run['id'], 'run id')}**: {completed}/{planned} completed attempts with grades; "
            f"{infrastructure} execution failures; {ungraded} completed attempts without grades; "
            f"{max(0, planned - len(run['attempts']))} missing attempts; "
            f"{unavailable} retained attempts with stale or unavailable evidence.\n\nAgent-quality failures: {quality}",
            f"Reviewed run: **{line(run['id'], 'run id')}**. Attempt: **{line(attempt['id'], 'attempt id')}** "
            f"(native identity: {line(attempt['native_id'], 'native identity')}).\n\n"
            f"- **Input:** {line(facts.get('input'), 'input')}\n"
            f"- **Observed response/actions:** {line(facts.get('observed'), 'observed')}\n"
            f"- **Grade:** {json.dumps(attempt['rewards'], sort_keys=True, ensure_ascii=False)}. "
            f"{line(facts.get('grade_explanation'), 'grade_explanation')}\n"
            f"- **Agent/model:** Configured identity: {line(selected['agent'], 'agent')}; {line(selected['model'], 'model')}. "
            f"{line(facts.get('identity_limitations'), 'identity_limitations')}",
            "Run evidence: "
            + " · ".join(
                artifact(repo, ref["path"], f"{run['id']}: {Path(ref['path']).name}") for ref in run["evidence"]
            ),
            "Attempt evidence: "
            + " · ".join(
                artifact(repo, ref["path"], f"{attempt['id']}: {Path(ref['path']).name}") for ref in attempt["evidence"]
            ),
        ]
    )
    limitations = review["limitations"]
    if not limitations:
        raise ValueError("Run review must state material evidence limitations, including unknown model health")
    parts.append("Limitations: " + "; ".join(line(item, "limitation") for item in limitations))
    rerun = artifact(
        repo, facts.get("rerun", ".eval-author/README.md#rerun-evaluations"), "saved run instructions", fragment=True
    )
    parts.append(f"You can rerun the full suite or just this task using the **{rerun}**.")
    if facts.get("task_rerun"):
        parts.append(artifact(repo, facts["task_rerun"], "Task-specific run instructions", fragment=True))
    parts.append(text(facts.get("reconciliation"), "agreed versus delivered reconciliation"))
    if progress["complete"]:
        if progress["audit_decision"] is None:
            raise ValueError("Save the pending audit invitation before presenting the completed evaluation handoff")
        parts.append("- [x] Confirm prerequisites\n- [x] Confirm `ETHOS.md`\n- [x] Create your first evaluations")
        if progress["audit_decision"] == "pending":
            parts.append(AUDIT_QUESTION)
    if facts.get("blockers"):
        parts.append(blockers(facts["blockers"]))
    parts.append(reports(repo, facts))
    return "\n\n".join(parts)


def render(repo: Path, payload: dict) -> str:
    repo = repo.resolve()
    state = journey_state.load_state(repo)
    if type(payload.get("revision")) is not int or payload["revision"] != state["revision"]:
        raise ValueError("Conversation input revision is stale; inspect journey_state show before rendering")
    stage = payload.get("stage")
    if stage not in STAGES:
        raise ValueError("Unknown first-evaluation conversation stage")
    facts = payload.get("facts", {})
    if not isinstance(facts, dict):
        raise ValueError("Conversation facts must be an object")
    if stage == "blockers":
        return blockers(facts.get("blockers")) + "\n"
    if stage == "traces":
        if state["ethos"]["stage"] != "complete":
            raise ValueError("Confirm ETHOS.md before proposing the trace choice")
        scope = state["scopes"].get("create", {})
        choice = scope.get("trace_choice") or scope.get("first_eval", {}).get("plan", {}).get("trace_choice")
        result = TRACE_INTRO
        if not choice:
            result += "\n\n" + TRACE_QUESTION + "\n\n" + "\n".join(f"- {label}" for label in TRACE_CHOICES)
        if facts.get("blockers"):
            result += "\n\n" + blockers(facts["blockers"])
        if state["blockers"] and not facts.get("blockers"):
            raise ValueError("Saved blockers must be explained with a specific needed action")
        return result + "\n"
    progress = first_eval_state.progress(repo, state)
    if progress is None:
        raise ValueError("Save a first-evaluation proposal record before rendering")
    record = state["scopes"]["create"]["first_eval"]
    if stage == "resume":
        stage = progress["next_stage"]
    if stage == "proposal":
        if progress["scope_approved"]:
            raise ValueError("Scope is already approved; resume the saved next stage")
        # Run blockers remain saved and are presented with the execution settings
        # when execution is ready. Design blockers use the separate blockers stage.
        return proposal(record) + "\n"
    elif stage == "execution_settings":
        if not progress["scope_approved"]:
            raise ValueError("Save scope approval before displaying execution settings")
        if not progress["first_task_approved"]:
            raise ValueError("Review and approve the first task before displaying execution settings")
        result = execution_settings(record)
    elif stage == "prepare_first":
        if not progress["scope_approved"]:
            raise ValueError("Save scope approval before preparing the first task")
        result = FIRST_PROGRESS if record["provider"] == "harbor" else GYM_PROGRESS
    elif stage == "review_first":
        result = first_review(repo, record, progress, facts)
    elif stage == "prepare_remaining":
        if not progress["first_task_approved"]:
            raise ValueError("Save first-task approval before preparing remaining tasks")
        result = REMAINDER_PROGRESS
    elif stage == "ready_to_run":
        if progress["next_stage"] == "execution_settings":
            raise ValueError("Display and save execution settings before announcing execution")
        if not progress["first_task_approved"] or not all(item["validated"] for item in progress["tasks"].values()):
            raise ValueError("All selected tasks need current grading validation before announcing reference checks")
        result = CHECKS_PASSED
        if (
            progress["execution_authorized"]
            and facts.get("access_ready") is True
            and not state["blockers"]
            and not facts.get("blockers")
        ):
            result += "\n\n" + RUN_PROGRESS
        elif (
            progress["selection_current"]
            and facts.get("access_ready") is True
            and not state["blockers"]
            and not facts.get("blockers")
        ):
            result += "\n\n" + RUN_QUESTION
        else:
            result += "\n\n" + text(facts.get("missing_execution_question"), "one focused missing execution question")
    else:
        result = results(repo, record, progress, facts)
    if stage != "results" and facts.get("blockers"):
        result += "\n\n" + blockers(facts["blockers"])
    if stage in {"execution_settings", "ready_to_run", "results"} and state["blockers"] and not facts.get("blockers"):
        raise ValueError("Saved blockers must be explained with the Blockers heading and a specific needed action")
    return result + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = journey_state.read_json(args.input)
        print(render(args.repo, payload), end="")
    except (OSError, ValueError, KeyError, StopIteration) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
