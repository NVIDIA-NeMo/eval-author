# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render expansion entry, planning, and creation checkpoints."""

import conversation_templates as shared
import expansion_state
import journey_state
from conversation_formatting import link, literal, mapping

from . import expansion_templates as copy
from . import onboarding

JOURNEYS = ("expand",)
STAGES = {"expansion_continue", "expansion_scope", "expansion_progress", "expansion_review"}
PREVIEWS: set[str] = set()


def _checkpoint(state: dict, progress: dict, *, reviewing: bool = False) -> expansion_state.ScopeRecord:
    value = expansion_state.record(state)
    if value is None or value["schema_version"] != 2:
        raise ValueError("Save the existing authoring workflow's planning checkpoint first")
    if not progress["context_current"] or state["scopes"]["expand"].get("needs_review"):
        raise ValueError("Expansion context changed; reconcile it through the selected authoring workflow")
    ready = expansion_state.review_ready(state) if reviewing else expansion_state.checkpoint_ready(state)
    if not ready:
        raise ValueError("Continue the saved prerequisite or intent question through shared onboarding")
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Continue the current question through its owning authoring workflow")
    return value


def _scope(state: dict, progress: dict) -> str:
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


def _progress(state: dict, progress: dict) -> str:
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


def _artifact(state: dict, path: str, label: str, *, fragment: str | None = None) -> str:
    return link(state["repository"].rstrip("/") + "/" + path, label, fragment=fragment)


def _review(state: dict, progress: dict, observed: dict) -> str:
    value = _checkpoint(state, progress, reviewing=True)
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
                report=_artifact(state, ".eval-author/report.html", copy.REVIEW_REPORT_LABEL, fragment="cases"),
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
    if not review["ready"]:
        parts.append(copy.REVIEW_PARTIAL)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "task review question"))
    return "\n\n".join(parts) + "\n"


def _execution(state: dict, value: expansion_state.ScopeRecord, progress: dict, observed: dict) -> str:
    saved = value.get("execution")
    execution = progress.get("execution")
    if saved is None or execution is None or not progress["review"]["approved"]:
        raise ValueError("Approve the reviewed task set before presenting execution settings")
    settings = saved["execution_settings"]
    selection = saved["selection"]
    parts = [copy.EXECUTION_HEADING, copy.REVIEW_APPROVED]
    if selection:
        config = selection["config"]
        config_link = literal(config["path"], "execution configuration")
        if observed["artifacts"]["files"].get(config["path"]) == config["digest"]:
            config_link = _artifact(state, config["path"], config["path"])
        parts.append(
            copy.EXECUTION_IDENTITY.format(
                agent=literal(selection["agent"], "selected agent"),
                model=literal(selection["model"], "selected model"),
                config=config_link,
            )
        )
        cases = {case["task_id"]: case for case in selection["cases"]}
        rows = [copy.EXECUTION_TABLE]
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
                f"| {literal(planned['task'], 'selected task', table=True)} | {examples} | {attempts} | {dataset_rows} |"
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
    questions = [item for item in settings["open_questions"] if item != progress["pending_question"]]
    if questions or progress["pending_question"] is None:
        parts.append(
            copy.EXECUTION_QUESTIONS.format(
                questions="; ".join(literal(item, "open execution question") for item in questions)
                or copy.EXECUTION_NO_QUESTIONS
            )
        )
    parts.append(
        copy.EXECUTION_AUTHORIZED
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
    value = expansion_state.record(state)
    if payload["stage"] == "expansion_continue" and value is not None and value["schema_version"] == 2:
        checkpoint = (
            "expansion_review"
            if value.get("review") is not None
            else "expansion_progress"
            if value.get("creation") is not None
            else "expansion_scope"
        )
        raise ValueError(f"Use {checkpoint} for the saved expansion checkpoint")
    facts = mapping(payload.get("facts", {}), "expansion facts", {"expansion_observations"})
    progress = expansion_state.derive(state, facts.get("expansion_observations"))
    if progress is None:
        raise ValueError("Save a structured expansion lifecycle before continuation")
    if payload["stage"] == "expansion_review":
        return _review(state, progress, facts["expansion_observations"])
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
