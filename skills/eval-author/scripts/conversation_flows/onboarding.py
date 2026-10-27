# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render orientation, prerequisites, and Ethos without advancing their workflow.

The caller supplies state and evidence; audit_welcome and prerequisite_results
accept state whose artifact checks are still deferred.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import conversation_templates as shared
import first_eval_state
import journey_state
from conversation_formatting import checklist as format_checklist
from conversation_formatting import link, literal, mapping, text

from . import first_eval, first_eval_templates
from . import onboarding_templates as copy

JOURNEYS = (None, *shared.JOURNEYS)
PREVIEWS = {"orientation", "goal_selected", "prerequisites"}
STAGES = PREVIEWS | {
    "audit_welcome",
    "audit_entry_failure",
    "prerequisite_failure",
    "prerequisite_results",
    "results",
    "ethos",
    "confirmed",
    "resume",
}
MAX_STATE_ERROR_CHARS = 2000
ROW_PREREQUISITES = {
    "skills": ("skills", "ethos_skills"),
    "access": ("code_access", "file_writes", "command_execution"),
    "runtime": ("python", "runtime"),
    "environment": ("environment",),
    "services_data": ("services", "data"),
    "agent_access": ("agent_connection", "model_credentials"),
    "traces": ("data",),
    "tooling": ("python",),
}


def checklist(journey: str, state: dict | None = None, *, state_only: bool = False) -> str:
    rows = (
        journey_state.checklist(
            {**state, "blockers": active_blockers(state, state_only=state_only)}, state_only=state_only
        )
        if state is not None
        else [
            {"label": label, "complete": False, "current": index == 0}
            for index, label in enumerate((*shared.CHECKLIST_LABELS, shared.JOURNEYS[journey]))
        ]
    )
    return format_checklist(rows)


def recorded_status(state: dict, key: str) -> str:
    """Summarize a composite display row without replacing checklist readiness rules."""
    statuses = {state["prerequisites"].get(item, {}).get("status", "unknown") for item in ROW_PREREQUISITES[key]}
    for status in ("missing", "unknown", "confirmed", "verified"):
        if status in statuses:
            return status
    return "not_required"


def canonical_blocker(state: dict, blocker: dict) -> dict | None:
    variants = copy.BLOCKER_TEMPLATES.get((state["journey"], blocker["phase"], blocker["id"]), {})
    status = state["prerequisites"].get(blocker["id"], {}).get("status", "unknown")
    return variants.get(status, variants.get("*"))


def prerequisite_table(journey: str, facts: dict, *, state: dict | None = None, checking: bool = False) -> str:
    rows = copy.AUDIT_ROWS if journey == "audit" else copy.AUTHORING_ROWS
    supplied = mapping(facts.get("rows", {}), "rows", {key for key, _ in rows})
    labels = {**shared.STATUS_LABELS, "checking": "Checking"} if checking else shared.STATUS_LABELS
    lines = [copy.TABLE_HEADER]
    for key, label in rows:
        default = {"status": "unknown", "findings": copy.TRACE_FINDING} if checking and key == "traces" else {}
        record = mapping(supplied.get(key, default), f"rows.{key}", {"status", "findings", "source", "links"})
        selected = record.get("status", "checking" if checking else "unknown")
        trace_copy = None
        if key == "traces" and state is not None:
            trace_copy = next(
                (canonical_blocker(state, item) for item in state["blockers"] if item["id"] == "data"), None
            )
            if trace_copy:
                selected = recorded_status(state, key)
                if "status" in record and record["status"] != selected:
                    raise ValueError("rows.traces.status conflicts with the saved trace availability blocker")
        if selected not in labels:
            raise ValueError(f"Unknown prerequisite status: {selected}")
        if selected == "not_required" and key not in {"environment", "services_data", "agent_access"}:
            raise ValueError(f"{key} is required for this journey")
        if selected not in {"unknown", "checking"}:
            saved_source = (
                state["prerequisites"].get("data", {}).get("source") if trace_copy and state is not None else None
            )
            text(record.get("source", saved_source), f"rows.{key}.source")
            if not trace_copy:
                text(record.get("findings"), f"rows.{key}.findings")
            if state is not None and key in ROW_PREREQUISITES and selected != recorded_status(state, key):
                raise ValueError(f"rows.{key}.status conflicts with saved prerequisites; record the findings first")
            if (
                state is not None
                and key in {"agent_repository", "evaluation_framework"}
                and selected == "missing"
                and not any(item["phase"] == "setup" for item in state["blockers"])
            ):
                raise ValueError(f"A missing {key} requires its saved setup blocker")
        status = labels[selected]
        if checking and key == "agent_access" and key not in supplied:
            status = "Needed before agent runs"
        finding = trace_copy["finding"] if trace_copy else record.get("findings")
        finding = literal(finding, "findings", table=True) if finding else "—"
        links = record.get("links", [])
        if not isinstance(links, list):
            raise ValueError("Row links must be a list")
        for item in links:
            item = mapping(item, "row link", {"label", "target"})
            finding += "<br>" + link(item.get("target"), text(item.get("label"), "link label"))
        lines.append(f"| {label} | {status} | {finding} |")
    return "\n".join(lines)


def recommendations(facts: dict) -> str | None:
    entries = facts.get("recommendations", [])
    if not isinstance(entries, list):
        raise ValueError("Recommendations must be a list")
    if not entries:
        return None
    lines = [shared.RECOMMENDATIONS, ""]
    for item in entries:
        item = mapping(item, "recommendation", {"text", "link"})
        label = text(item.get("text"), "recommendation text")
        lines.append("- " + (link(item["link"], label) if "link" in item else literal(label, "recommendation")))
    return "\n".join(lines)


def active_blockers(state: dict, *, state_only: bool = False) -> list[dict]:
    """Present dependencies at the action they block, retaining all saved evidence."""
    shared_done = all(row["complete"] for row in journey_state.checklist(state, state_only=state_only)[:2])
    started = state["journey_status"].get(state["journey"]) in {"in_progress", "complete"}
    later_requirements = {"environment", "services", "data", *journey_state.DEFERRED}
    return [
        item
        for item in state["blockers"]
        if item["phase"] in {"setup", "ethos"}
        or (shared_done and item["phase"] == state["journey"] and (started or item["id"] not in later_requirements))
    ]


def blocker_section(state: dict, facts: dict, *, confirmed: bool, state_only: bool = False) -> str | None:
    active = active_blockers(state, state_only=state_only)
    explanations = facts.get("blockers", [])
    if not isinstance(explanations, list):
        raise ValueError("Blockers must be a list")
    by_id = {}
    for item in explanations:
        item = mapping(item, "blocker", {"id", "step", "reason", "needed", "skill_link"})
        key = text(item.get("id"), "blocker id")
        if key in by_id:
            raise ValueError("Duplicate blocker explanation")
        by_id[key] = item
    if set(by_id) != {item["id"] for item in active}:
        raise ValueError("Supply an explanation for every relevant saved blocker, and no others")
    if not active:
        return None
    lines = [shared.HANDOFF_BLOCKERS if confirmed else shared.BLOCKERS, ""]
    templates = {item["id"]: canonical_blocker(state, item) for item in active}
    pauses = []
    # IDs provide a stable order independent of JSON mapping or state-list insertion order.
    for key in sorted(by_id):
        item = templates[key] or by_id[key]
        lines.append(
            f"- **{literal(item.get('step'), 'blocked step')}:** {literal(item.get('reason'), 'blocker reason')} "
            f"**Needed:** {literal(item.get('needed'), 'needed action')}"
        )
        if templates[key]:
            target = text(by_id[key].get("skill_link"), f"blocker {key} skill_link")
            if not urlsplit(target).path.endswith("/SKILL.md"):
                raise ValueError("A canonical blocker's skill_link must identify the actual loaded SKILL.md")
            pauses.append(shared.BLOCKER_PAUSE.format(skill=link(target, item["skill_label"]), quote=item["quote"]))
    if state["pending_question"] and not state_only and not any(templates.values()):
        # An old continuation CTA cannot escape a newly recorded blocker.
        if state["pending_question"] not in {*copy.CONTINUATION_JOURNEYS, first_eval_templates.TRACE_QUESTION}:
            lines.extend(["", literal(state["pending_question"], "pending question")])
    if state["journey"] == "expand" and any(
        item["id"] == "evaluation_format" and item["phase"] == "setup" for item in active
    ):
        lines.extend(["", copy.CONVERSION_HANDOFF])
    for pause in dict.fromkeys(pauses):
        lines.extend(["", pause])
    return "\n".join(lines)


def canonical_question(question: str | None) -> str | None:
    if question is not None and question in copy.CONTINUATION_JOURNEYS:
        return copy.CONTINUATION[copy.CONTINUATION_JOURNEYS[question]]
    return question


def pending_question(state: dict, facts: dict) -> str | None:
    saved = canonical_question(state["pending_question"])
    explicit = facts.get("question")
    remaining = [canonical_question(question) for question in state["ethos"]["remaining_questions"]]
    if explicit is not None:
        explicit = canonical_question(text(explicit, "question"))
    if saved and explicit and text(saved, "pending question") != explicit:
        raise ValueError("The supplied question conflicts with the saved pending question")
    if not saved and remaining and explicit and text(remaining[0], "remaining question") != explicit:
        raise ValueError("The supplied question conflicts with the saved unresolved intent question")
    return saved or (remaining[0] if remaining else explicit)


def ethos_context(state: dict, facts: dict) -> tuple[str, str | None, str | None]:
    ethos = mapping(facts.get("ethos", {}), "ethos facts", {"status", "link", "summary"})
    status = ethos.get("status", "unknown")
    if status not in {"existing", "absent", "unknown"}:
        raise ValueError("Ethos status must be existing, absent, or unknown")
    if status != "existing":
        if "link" in ethos or "summary" in ethos:
            raise ValueError("Only an inspected existing ETHOS.md can have a link or summary")
        return status, None, None
    target = text(ethos.get("link"), "ETHOS.md link")
    if target != state["repository"].rstrip("/") + "/" + state["ethos"]["path"]:
        raise ValueError("ETHOS.md link must match the selected document in state")
    summary = literal(ethos["summary"], "ETHOS.md summary") if ethos.get("summary") else None
    return status, link(target, "ETHOS.md"), summary


def next_steps(state: dict, facts: dict, stage: str) -> str:
    progress = journey_state.checklist(state)
    if not progress[0]["complete"]:
        raise ValueError("Prerequisites are incomplete; record the unresolved blocker and supply its explanation")
    question = pending_question(state, facts)
    journey = state["journey"]
    started = state["journey_status"].get(journey) in {"in_progress", "complete"}
    shared_continued = any(status in {"in_progress", "complete"} for status in state["journey_status"].values())
    confirmed = progress[1]["complete"]
    source_question = question == first_eval_templates.TRACE_QUESTION
    initial_sources = (
        journey == "create" and first_eval_state.record(state) is None and not first_eval_state.trace_choice(state)
    )
    confirmation_questions = {None, copy.CONTINUATION[journey]}
    if initial_sources:
        confirmation_questions.add(first_eval_templates.TRACE_QUESTION)
    if stage == "confirmed" and (not confirmed or started or state["ethos"]["remaining_questions"]):
        raise ValueError("Confirmation pause requires confirmed ETHOS.md and no started downstream work or intent gaps")
    if stage == "confirmed" and question not in confirmation_questions:
        raise ValueError("Clear the answered review question before presenting the confirmation pause")
    paragraphs = [shared.NEXT_STEPS, checklist(journey, state)]
    if confirmed:
        if state["ethos"]["remaining_questions"]:
            raise ValueError("Resolve the saved intent questions before presenting confirmed ETHOS.md")
        if question in copy.CONTINUATION.values() and (question != copy.CONTINUATION[journey] or started):
            raise ValueError("Saved continuation question is stale for the current journey progress")
        if source_question and (not initial_sources or started):
            raise ValueError("Saved source question is stale for the current creation progress")
        if not started and (
            question == copy.CONTINUATION[journey] or source_question or (question is None and not shared_continued)
        ):
            status, document, _ = ethos_context(state, facts)
            if status != "existing":
                raise ValueError("Confirmation requires the supplied verified link to saved ETHOS.md")
            # Report the saved confirmation without implying any runtime or evaluation success.
            paragraphs.insert(0, copy.ETHOS_CONFIRMED.format(document=document))
            paragraphs.append(first_eval.source_prompt(state) if initial_sources else copy.CONTINUATION[journey])
        elif question:
            paragraphs.append(literal(question, "pending question"))
        return "\n\n".join(paragraphs)

    if question in copy.CONTINUATION.values() or source_question:
        raise ValueError("ETHOS.md review is unfinished; a continuation question cannot bypass it")
    status, document, summary = ethos_context(state, facts)
    if state["ethos"]["stage"] in {"draft", "write"} and state["ethos"]["draft"] is not None:
        draft = mapping(facts.get("draft"), "draft facts", {"link", "digest"})
        recorded = state["ethos"]["draft"]
        if draft.get("digest") != recorded["digest"]:
            raise ValueError("Supply the verified draft matching the saved content digest")
        if draft.get("link") != state["repository"].rstrip("/") + "/" + recorded["path"]:
            raise ValueError("Draft link must match the exact saved working draft path")
        paragraphs.append(copy.ETHOS_DRAFT.format(document=link(draft["link"], "ETHOS.md draft")))
    elif status == "existing" and state["ethos"]["stage"] == "review":
        if not summary:
            raise ValueError("Existing ETHOS.md review requires an inspected summary")
        paragraphs.extend([copy.ETHOS_FOUND.format(document=document), summary])
        question = question or copy.ETHOS_REVIEW.format(agent=text(state["agent"], "agent"))
    elif status == "absent":
        paragraphs.append(copy.ETHOS_ABSENT)
    elif status == "unknown":
        paragraphs.append(copy.ETHOS_UNKNOWN)
    # Exploration/interview/draft/writing remain owned by ethos-orchestrate.
    if not question:
        raise ValueError("Supply or save the orchestrator's unresolved intent question or next action")
    rendered_question = literal(question, "pending question")
    if status == "existing" and state["ethos"]["stage"] == "review":
        rendered_question = f"**{rendered_question}**"
    paragraphs.append(rendered_question)
    return "\n\n".join(paragraphs)


def state_error(facts: dict) -> str:
    error = text(facts.get("state_error"), "state_error")
    if len(error) > MAX_STATE_ERROR_CHARS:
        raise ValueError(f"state_error exceeds the {MAX_STATE_ERROR_CHARS}-character limit")
    return error


def audit_entry_failure(payload: dict) -> str:
    payload = mapping(payload, "audit entry failure", {"stage", "journey", "facts"})
    if payload.get("journey") != "audit":
        raise ValueError("Audit entry failure requires journey audit")
    facts = mapping(payload.get("facts"), "audit entry failure facts", {"state_error"})
    paragraphs = [
        shared.BLOCKERS,
        copy.AUDIT_ENTRY_STATE_FAILURE.format(error=literal(state_error(facts), "state_error")),
    ]
    return "\n\n".join(paragraphs) + "\n"


def prerequisite_failure(payload: dict) -> str:
    payload = mapping(payload, "prerequisite failure", {"stage", "journey", "ready", "facts"})
    journey = payload.get("journey")
    if not isinstance(journey, str) or journey not in shared.JOURNEYS:
        raise ValueError("Select create, audit, or expand")
    if payload.get("ready") is not True:
        raise ValueError("Wait for the user's readiness reply before checking prerequisites")
    facts = mapping(payload.get("facts"), "prerequisite failure facts", {"rows", "state_error"})
    error = state_error(facts)
    paragraphs = [
        copy.PREREQUISITE_RESULTS_INTRO,
        prerequisite_table(journey, facts),
        shared.BLOCKERS,
        copy.PREREQUISITE_STATE_FAILURE.format(error=literal(error, "state_error")),
        copy.PREREQUISITE_UNSAVED_NOTICE,
    ]
    return "\n\n".join(paragraphs) + "\n"


def render(payload: dict) -> str:
    """Pure rendering: no clocks, filesystem access, state writes, or workflow execution."""
    stage = payload["stage"]
    if stage == "audit_entry_failure":
        return audit_entry_failure(payload)
    if stage == "prerequisite_failure":
        return prerequisite_failure(payload)
    if stage in PREVIEWS:
        allowed = {"stage", "journey", "ready"} | ({"facts"} if stage == "prerequisites" else set())
        if set(payload) - allowed:
            raise ValueError("Only prerequisite previews accept row facts; previews never accept saved state")
        journey = payload.get("journey")
        if journey not in shared.JOURNEYS and not (stage == "orientation" and journey is None):
            raise ValueError("Select create, audit, or expand")
        if stage == "prerequisites":
            assert isinstance(journey, str)
            if payload.get("ready") is not True:
                raise ValueError("Wait for the user's readiness reply before checking prerequisites")
            facts = mapping(payload.get("facts", {}), "prerequisite preview facts", {"rows"})
            paragraphs = [copy.PREREQUISITE_PROGRESS_INTRO, prerequisite_table(journey, facts, checking=True)]
        elif journey is None:
            paragraphs = [copy.WELCOME, copy.GOAL_QUESTION, "\n".join(f"- {choice}" for choice in copy.GOAL_CHOICES)]
        else:
            paragraphs = [
                copy.WELCOME_WITH_CHECKLIST if stage == "orientation" else copy.GOAL_INTROS[journey],
                checklist(journey),
                copy.READY,
            ]
        return "\n\n".join(paragraphs) + "\n"

    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("Supply reconciled journey state")
    journey_state.validate(state)
    if type(payload.get("revision")) is not int or payload["revision"] != state["revision"]:
        raise ValueError("State revision differs; reload state and rebuild the presentation input")
    if not state["journey"]:
        raise ValueError("Select the journey through the state helper before rendering progress")
    if "journey" in payload or "ready" in payload:
        raise ValueError("Saved state owns the selected journey; readiness is not a saved-state override")
    if stage == "audit_welcome":
        mapping(payload, "audit welcome", {"stage", "state", "revision"})
        if state["journey"] != "audit":
            raise ValueError("Audit welcome requires saved journey audit")
        return (
            "\n\n".join(
                [copy.WELCOME_WITH_CHECKLIST, checklist("audit", state, state_only=True), copy.AUDIT_REUSE_NOTICE]
            )
            + "\n"
        )
    state_only = stage == "prerequisite_results"
    fact_keys = {"rows", "recommendations", "blockers"}
    if not state_only:
        fact_keys |= {"ethos", "question", "draft"}
    facts = mapping(payload.get("facts", {}), "facts", fact_keys)
    if not state_only:
        pending_question(state, facts)  # Reject contradictory questions even on blocked turns.
    paragraphs = []
    if stage in {"prerequisite_results", "results"}:
        paragraphs.extend([copy.PREREQUISITE_RESULTS_INTRO, prerequisite_table(state["journey"], facts, state=state)])
    if (optional := recommendations(facts)) and stage != "ethos":
        paragraphs.append(optional)
    blocked = blocker_section(
        state, facts, confirmed=not state_only and state["ethos"]["stage"] == "complete", state_only=state_only
    )
    if blocked:
        paragraphs.append(blocked)
    elif state_only:
        if not journey_state.checklist(state, state_only=True)[0]["complete"]:
            raise ValueError("Prerequisites are incomplete; record the unresolved blocker and supply its explanation")
        paragraphs.append(copy.PREREQUISITES_COMPLETE)
    else:
        paragraphs.append(next_steps(state, facts, stage))
    return "\n\n".join(paragraphs) + "\n"
