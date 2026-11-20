# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the existing expansion entry and task-planning checkpoints."""

from collections import Counter
from typing import Literal, NamedTuple, TypedDict, cast

import conversation_templates as shared
import expansion_state
import journey_state
from conversation_formatting import link, literal, mapping, text

from . import expansion_templates as copy
from . import onboarding

JOURNEYS = ("expand",)
STAGES = {
    "expansion_audit_opening",
    "expansion_continue",
    "expansion_scope",
    "expansion_progress",
    "expansion_review",
    "expansion_results",
}
PREVIEWS: set[str] = set()


class AttemptKey(NamedTuple):
    task_id: str
    example_id: str | None
    attempt: int


class AttemptResult(TypedDict):
    task_id: str
    example_id: str | None
    attempt: int
    status: Literal["completed", "error", "incomplete"]
    score: str | None
    error: str | None
    output: str | None
    trace: str | None
    verification: str | None


def _audit_opening(state: dict, facts: dict) -> str:
    scope = state["scopes"].get("expand")
    handoff = scope.get("audit_handoff") if isinstance(scope, dict) else None
    if handoff is None:
        raise ValueError("Retain the selected audit improvements before rendering their opening")
    selected = handoff["recommendation_ids"]
    labels = mapping(facts.get("selected_improvements"), "selected improvements", set(selected))
    if set(labels) != set(selected):
        raise ValueError("Supply labels for exactly the retained audit improvement selection")
    all_unsupported = facts.get("all_selected_unsupported", False)
    if type(all_unsupported) is not bool:
        raise ValueError("all_selected_unsupported must be a boolean")
    if all_unsupported and "limits" not in facts:
        raise ValueError("Explain the known support limits when all selected improvements are unsupported")
    count = "one" if len(selected) == 1 else "two" if len(selected) == 2 else "many"
    parts = [
        copy.AUDIT_SELECTION_ACKNOWLEDGMENTS[count],
        copy.AUDIT_SELECTIONS.format(
            value="; ".join(literal(labels[key], "selected improvement label") for key in selected)
        ),
    ]
    if not all_unsupported:
        parts.append(copy.AUDIT_NEXT_STEPS)
    if "limits" in facts:
        parts.append(copy.AUDIT_LIMITS.format(value=literal(facts["limits"], "known support limits")))
    return "\n\n".join(parts) + "\n"


def _checkpoint(
    state: dict, progress: expansion_state.ExpansionProgress, *, reviewing: bool = False
) -> expansion_state.ScopeRecord:
    value = expansion_state.record(state)
    if value is None or value["checkpoint"] != "scope":
        raise ValueError("Save the existing authoring workflow's planning checkpoint first")
    ready = expansion_state.question_ready(state, progress) if reviewing else expansion_state.checkpoint_ready(state)
    if not ready:
        raise ValueError("Continue the saved prerequisite or intent question through shared onboarding")
    if not progress["context_current"] or state["scopes"]["expand"].get("needs_review"):
        raise ValueError("Expansion context changed; reconcile it through the selected authoring workflow")
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Continue the current question through its owning authoring workflow")
    return value


def _scope(state: dict, progress: expansion_state.ExpansionProgress) -> str:
    value = _checkpoint(state, progress)
    plan = value["plan"]
    parts = (
        [copy.SCOPE_HEADING]
        if value["path"] == "recorded_interaction"
        else [shared.NEXT_STEPS, onboarding.checklist("expand", state), copy.SCOPE_HEADING]
    )
    parts.append(copy.SCOPE_FIELDS["summary"].format(value=literal(plan["summary"], "scope summary")))
    rows = [copy.SCOPE_TABLE]
    for task in plan["tasks"]:
        cells = (
            literal(task[key], f"scope {key}", table=True) for key in ("task", "scenario", "outcome", "verification")
        )
        rows.append("| " + " | ".join(cells) + " |")
    parts.append("\n".join(rows) if plan["tasks"] else copy.SCOPE_EMPTY)
    parts.append(copy.SCOPE_FIELDS["limits"].format(value=literal(plan["limits"], "scope limits")))
    if plan["deferred"]:
        parts.append(
            copy.SCOPE_DEFERRED + "\n\n" + "\n".join("- " + literal(item, "deferred work") for item in plan["deferred"])
        )
    if status := copy.SCOPE_STATUS[progress["stage"]]:
        parts.append(status)
    if progress["stage"] == "scope_approved":
        parts.append(copy.CREATION_PLAN)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "scope question"))
    return "\n\n".join(parts) + "\n"


def _progress(state: dict, progress: expansion_state.ExpansionProgress) -> str:
    value = _checkpoint(state, progress)
    creation = progress.get("creation")
    if progress["stage"] != "creation" or creation is None:
        raise ValueError("Save creation progress for the agreed task plan first")
    rows = [copy.CREATION_TABLE]
    for task in creation["rows"]:
        rows.append(f"| {literal(task['task'], 'task title', table=True)} | {copy.CREATION_STATUS[task['status']]} |")
    parts = [copy.CREATION_HEADING]
    queued = all(row["status"] == "queued" for row in creation["rows"])
    if queued:
        parts.append(copy.CREATION_PLAN)
    parts.append("\n".join(rows))
    if creation["detail"] is not None:
        parts.append(literal(creation["detail"], "creation detail"))
    parts.append(copy.SCOPE_FIELDS["limits"].format(value=literal(value["plan"]["limits"], "scope limits")))
    if creation["ready_for_review"]:
        parts.append(copy.CREATION_READY)
    elif not queued:
        parts.append(copy.CREATION_NEXT)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "creation question"))
    return "\n\n".join(parts) + "\n"


def _artifact(state: dict, path: str, label: str) -> str:
    return link(state["repository"].rstrip("/") + "/" + path, label)


def _review(state: dict, progress: expansion_state.ExpansionProgress, observed: dict) -> str:
    value = _checkpoint(state, progress, reviewing=True)
    if expansion_state.results_pending(progress):
        raise ValueError("Use expansion_results for the saved post-run results review")
    review = progress.get("review")
    if review is None or progress["stage"] not in {"task_review", "execution"}:
        raise ValueError("Save the generated-task review checkpoint first")
    if progress["stage"] == "execution":
        return _execution(state, value, progress, observed)
    files = observed["artifacts"]["files"]
    parts = [copy.REVIEW_HEADING]
    if review["ready"]:
        parts.append(copy.REVIEW_READY)
    if review["artifacts_current"]:
        parts.append(
            copy.REVIEW_ACTION.format(
                report=_artifact(state, ".eval-author/report.html", copy.REVIEW_REPORT_LABEL),
                markdown=_artifact(state, ".eval-author/report_markdown/tasks.md", copy.REVIEW_MARKDOWN_LABEL),
            )
        )
    else:
        parts.append(copy.REVIEW_ARTIFACTS_PENDING)
    parts.append(copy.REVIEW_DETAILS)
    creation = progress.get("creation")
    statuses = creation["rows"] if creation else [{"status": "queued"} for task in value["plan"]["tasks"]]
    rows = [copy.REVIEW_TABLE]
    for planned, status in zip(value["plan"]["tasks"], statuses, strict=True):
        task = value.get("creation", {}).get("tasks", {}).get(expansion_state.task_key(planned))
        title = literal(planned["task"], "task title", table=True)
        evidence = []
        if task:
            readme = task["path"] + "/README.md"
            if files.get(readme) is not None:
                title = _artifact(state, readme, planned["task"])
            for index, ref in enumerate((task["validation"] or {}).get("evidence", []), 1):
                if files.get(ref["path"]) == ref["digest"]:
                    evidence.append(_artifact(state, ref["path"], copy.REVIEW_EVIDENCE_LABEL.format(index=index)))
        rows.append(
            f"| {title} | {copy.CREATION_STATUS[status['status']]} | {'; '.join(evidence) or copy.REVIEW_NO_EVIDENCE} |"
        )
    parts.extend(["\n".join(rows), copy.REVIEW_CONTROLS])
    if creation and creation["detail"]:
        parts.append(literal(creation["detail"], "creation detail"))
    if files.get(".eval-author/README.md") is not None:
        parts.append(_artifact(state, ".eval-author/README.md", copy.REVIEW_README_LABEL))
    parts.append(copy.SCOPE_FIELDS["limits"].format(value=literal(value["plan"]["limits"], "scope limits")))
    if review["decision"] == "approved" and not review["approved"]:
        parts.append(copy.REVIEW_UPDATED if review["ready"] else copy.REVIEW_STALE)
    elif review["decision"]:
        parts.append(copy.REVIEW_STATUS[review["decision"]])
    elif value.get("review", {}).get("decision"):
        parts.append(copy.REVIEW_UPDATED if review["ready"] else copy.REVIEW_STALE)
    elif review["ready"]:
        parts.append(copy.REVIEW_NEEDED)
    if not review["ready"] and not (review["approved"] or review["decision"] == "declined"):
        parts.append(copy.REVIEW_PARTIAL)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "task review question"))
    return "\n\n".join(parts) + "\n"


def _execution(
    state: dict, value: expansion_state.ScopeRecord, progress: expansion_state.ExpansionProgress, observed: dict
) -> str:
    saved = value.get("execution")
    execution = progress.get("execution")
    if saved is None or execution is None or progress["review"] is None or not progress["review"]["approved"]:
        raise ValueError("Approve the reviewed task set before presenting execution settings")
    settings = saved["execution_settings"]
    selection = saved["selection"] if saved.get("provider", value["setup"]) == value["setup"] else None
    parts = [copy.EXECUTION_HEADING, copy.REVIEW_APPROVED, copy.EXECUTION_PURPOSE]
    config_link = None
    if selection:
        config = selection["config"]
        config_link = literal(config["path"], "execution configuration")
        if observed["artifacts"]["files"].get(config["path"]) == config["digest"]:
            config_link = _artifact(state, config["path"], config["path"])
        parts.append(
            copy.EXECUTION_IDENTITY.format(
                agent=literal(selection["agent"], "selected agent"),
                model=literal(selection["model"], "selected model"),
            )
        )
        cases = {case["task_id"]: case for case in selection["cases"]}
        rows = [copy.EXECUTION_TABLE.format(unit=copy.EXECUTION_ATTEMPT_UNIT[value["setup"]])]
        for planned in value["plan"]["tasks"]:
            case = cases.get(expansion_state.task_key(planned))
            examples = (
                ", ".join(literal(item, "selected example", table=True) for item in case["example_ids"])
                if case
                else copy.EXECUTION_UNSELECTED
            )
            attempts = str(case["attempts"]) if case else copy.EXECUTION_UNSELECTED
            dataset_rows = (
                (", ".join(str(row) for row in case["rows"]) if case else copy.EXECUTION_UNSELECTED)
                if value["setup"] == "gym"
                else copy.EXECUTION_NO_ROWS
            )
            rows.append(
                f"| {literal(planned['task'], 'selected task', table=True)} "
                f"| {literal(planned['scenario'], 'selected scenario', table=True)} "
                f"| {literal(planned['outcome'], 'expected result', table=True)} "
                f"| {examples} | {attempts} | {dataset_rows} |"
            )
        parts.append("\n".join(rows))
    else:
        parts.append(
            copy.EXECUTION_SAVED.format(
                agent_model=literal(settings["agent_model"], "saved agent/model"),
                attempts=literal(settings["attempts"], "saved attempts"),
            )
        )
    parts.append(
        copy.EXECUTION_SETTINGS.format(
            validation=literal(settings["validation"], "validation work"),
            limits=literal(settings["limits"], "execution limits"),
        )
    )
    if config_link is not None:
        parts.append(copy.EXECUTION_CONFIG.format(config=config_link))
    questions = [item for item in settings["open_questions"] if item != progress["pending_question"]]
    if questions or progress["pending_question"] is None:
        parts.append(
            copy.EXECUTION_QUESTIONS.format(
                questions="; ".join(literal(item, "open execution question") for item in questions)
                or copy.EXECUTION_NO_QUESTIONS
            )
        )
    parts.append(
        copy.RESULTS_REVIEWED
        if execution["results_reviewed"]
        else copy.EXECUTION_AUTHORIZED
        if execution["authorized"]
        else copy.EXECUTION_DECLINED
        if execution["declined"]
        else copy.EXECUTION_STALE
        if not execution["selection_current"]
        else copy.EXECUTION_PENDING
    )
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "execution question"))
    return "\n\n".join(parts) + "\n"


def _results(state: dict, progress: expansion_state.ExpansionProgress, facts: dict) -> str:
    value = _checkpoint(state, progress, reviewing=True)
    if not progress["execution"] or not progress["execution"]["results_inspectable"]:
        raise ValueError("Reconcile the current tasks, settings, and run authorization before presenting results")
    if progress["pending_question"] is None:
        raise ValueError("Save the post-run results review question before presenting results")
    selection = value["execution"]["selection"]
    assert selection is not None
    titles = {expansion_state.task_key(task): task["task"] for task in value["plan"]["tasks"]}
    expected = [
        AttemptKey(case["task_id"], example, attempt)
        for case in selection["cases"]
        for example in (case["example_ids"] if state["setup"] == "gym" else [None])
        for attempt in range(1, case["attempts"] + 1)
    ]
    supplied = facts.get("attempts")
    if not isinstance(supplied, list) or len(supplied) > len(expected):
        raise ValueError("Supply only inspected attempts from the authorized selection")
    expected_keys = set(expected)
    attempts: dict[AttemptKey, AttemptResult] = {}
    fields = set(AttemptResult.__annotations__)
    for item in supplied:
        item = mapping(item, "attempt result", fields)
        if set(item) != fields:
            raise ValueError("Record each attempt's result and available evidence explicitly")
        if (
            not isinstance(item["task_id"], str)
            or item["example_id"] is not None
            and not isinstance(item["example_id"], str)
        ):
            raise ValueError("Attempt results require the selected task and example identities")
        key = AttemptKey(item["task_id"], item["example_id"], item["attempt"])
        if type(item["attempt"]) is not int or key not in expected_keys or key in attempts:
            raise ValueError("Attempt results must uniquely identify an authorized task, example, and attempt")
        if (
            not isinstance(item["status"], str)
            or item["status"] not in copy.RESULT_STATUS
            or item["status"] == "error"
            and not item["error"]
        ):
            raise ValueError("Record the native attempt status and any execution error")
        for field in ("score", "error"):
            if item[field] is not None:
                text(item[field], field)
        if item["status"] == "completed" and item["error"] is not None:
            raise ValueError("An execution error cannot be reported as completed")
        attempts[key] = cast(AttemptResult, item)
    title_counts = Counter(titles.values())
    rows = [copy.RESULTS_TABLE]
    for key in expected:
        label = titles[key.task_id]
        if title_counts[label] > 1:
            label = copy.RESULT_TASK_PATH.format(task=label, path=value["creation"]["tasks"][key.task_id]["path"])
        if key.example_id is not None:
            label += f" / {key.example_id}"
        label = literal(copy.RESULT_ATTEMPT.format(task=label, number=key.attempt), "attempt", table=True)
        item = attempts.get(key)
        result = copy.RESULT_MISSING
        links = [copy.RESULT_UNAVAILABLE] * 3
        if item:
            result = copy.RESULT_STATUS[item["status"]]
            if item["score"] is not None:
                result += copy.RESULT_SCORE.format(value=literal(item["score"], "score", table=True))
            else:
                result += copy.RESULT_SCORE_UNAVAILABLE
            if item["error"] is not None:
                result += copy.RESULT_ERROR.format(value=literal(item["error"], "execution error", table=True))
            elif item["status"] == "completed":
                result += copy.RESULT_NO_ERROR
            for index, (field, target) in enumerate(
                (("output", item["output"]), ("trace", item["trace"]), ("verification", item["verification"]))
            ):
                if target is not None:
                    path = text(target, field)
                    prefix = state["repository"].rstrip("/") + "/.eval-author/"
                    if not path.startswith(prefix):
                        raise ValueError("Attempt evidence must link to its retained repository artifact")
                    journey_state._relative_task_path(path[len(state["repository"].rstrip("/")) + 1 :])
                    links[index] = link(path, copy.RESULT_LINK_LABELS[field])
        rows.append("| " + " | ".join((label, result, *links)) + " |")
    parts = [copy.RESULTS_HEADING, "\n".join(rows)]
    blockers = expansion_state.execution_blockers(state)
    if blockers:
        parts.extend(
            [
                shared.BLOCKERS,
                "\n".join("- " + literal(item["message"], "execution blocker") for item in blockers),
                copy.RESULTS_BLOCKED,
            ]
        )
    else:
        parts.append(copy.RESULTS_REVIEW)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "results review question"))
    return "\n\n".join(parts) + "\n"


def render(payload: dict) -> str:
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("Supply the complete saved expansion state")
    journey_state.validate(state)
    if payload.get("stage") not in STAGES or state["journey"] != "expand":
        raise ValueError("Expansion continuation requires the saved expansion journey")
    if set(payload) - {"stage", "state", "revision", "facts"}:
        raise ValueError("Expansion continuation requires stateful input only")
    if type(payload.get("revision")) is not int or payload["revision"] != state["revision"]:
        raise ValueError("State revision differs; reload before rendering expansion")
    if payload["stage"] == "expansion_audit_opening":
        facts = mapping(
            payload.get("facts", {}),
            "audit opening facts",
            {"selected_improvements", "limits", "all_selected_unsupported"},
        )
        return _audit_opening(state, facts)
    value = expansion_state.record(state)
    facts = mapping(
        payload.get("facts", {}),
        "expansion facts",
        {"expansion_observations", "attempts"}
        if payload["stage"] == "expansion_results"
        else {"expansion_observations"},
    )
    progress = (
        expansion_state.derive(state, facts["expansion_observations"]) if "expansion_observations" in facts else None
    )
    if payload["stage"] == "expansion_continue" and value is not None and value["checkpoint"] == "scope":
        checkpoint = (
            "expansion_results"
            if expansion_state.results_pending(progress)
            else "expansion_review"
            if (progress and progress["stage"] in {"task_review", "execution"})
            or progress is None
            and value.get("review") is not None
            else "expansion_progress"
            if (progress and progress["stage"] == "creation") or progress is None and value.get("creation") is not None
            else "expansion_scope"
        )
        raise ValueError(f"Use {checkpoint} for the saved expansion checkpoint")
    if progress is None:
        progress = expansion_state.derive(state, facts.get("expansion_observations"))
    if progress is None:
        raise ValueError("Save a structured expansion lifecycle before continuation")
    if payload["stage"] == "expansion_results":
        return _results(state, progress, facts)
    if payload["stage"] == "expansion_review":
        return _review(state, progress, facts["expansion_observations"])
    if value is not None and expansion_state.results_pending(progress):
        raise ValueError("Use expansion_results for the saved post-run results review")
    if progress["stage"] in {"task_review", "execution"}:
        raise ValueError("Use expansion_review for saved task review and execution settings")
    if payload["stage"] == "expansion_progress":
        return _progress(state, progress)
    if payload["stage"] == "expansion_scope":
        if progress["stage"] == "creation":
            raise ValueError("Use expansion_progress for saved creation and validation progress")
        return _scope(state, progress)
    if not progress["context_current"]:
        raise ValueError("Expansion context changed; reconcile provider, intent, and handoff before continuation")
    if not expansion_state.entry_ready(state):
        raise ValueError(
            "Resolve shared prerequisites, intent questions, and saved blockers before expansion continuation"
        )
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Render only the saved unresolved expansion question")
    parts = [shared.NEXT_STEPS, onboarding.checklist("expand", state)]
    parts.append(literal(progress["pending_question"], "expansion question"))
    return "\n\n".join(parts) + "\n"
