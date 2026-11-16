# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render expansion entry and scope decisions from supplied current state."""

import conversation_templates as shared
import expansion_state
import journey_state
from conversation_formatting import literal, mapping

from . import expansion_templates as copy
from . import onboarding

JOURNEYS = ("expand",)
STAGES = {"expansion_continue", "expansion_scope"}
PREVIEWS: set[str] = set()


def _inputs(values: list[str]) -> str:
    return "; ".join(literal(value, "unresolved input", table=True) for value in values) or copy.NONE


def _table(plan: dict) -> str:
    if not plan["tasks"]:
        return copy.NO_TASKS
    rows = [copy.SCOPE_TABLE]
    selected = set(plan["selected_task_ids"])
    for task in plan["tasks"]:
        grading = copy.GRADING_VALIDATION.format(
            grading=literal(task["grading"], "grading rule", table=True),
            validation=literal(task["validation"], "validation plan", table=True),
        )
        cells = (
            literal(task["id"], "task id", table=True),
            copy.YES if task["id"] in selected else copy.NO,
            literal(task["scenario"], "task scenario", table=True),
            literal(task["expected_outcome"], "expected outcome", table=True),
            grading,
            _inputs(task["unresolved_inputs"]),
        )
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _scope(state: dict, progress: dict) -> str:
    value = expansion_state.record(state)
    assert value is not None
    if value["schema_version"] != 2:
        raise ValueError("Scope rendering requires a saved expansion scope")
    if expansion_state.scope_ready(state) and state["pending_question"] != progress["pending_question"]:
        raise ValueError("Render only the saved expansion scope question")
    plan = value["plan"]
    if value["path"] == "recorded_interaction":
        parts = [copy.SCOPE_HEADING]
    else:
        parts = [shared.NEXT_STEPS, onboarding.checklist("expand", state)]
    parts.extend(
        (
            copy.SCOPE_REQUEST.format(outcome=literal(value["request"]["outcome"], "requested outcome")),
            copy.SCOPE_PATH.format(path=copy.PATHS[value["path"]]),
            _table(plan),
        )
    )
    selected = [task for task in plan["tasks"] if task["id"] in plan["selected_task_ids"]]
    parts.append(
        copy.COUNTS.format(
            tasks=len(selected), examples=len({item for task in selected for item in task["example_ids"]})
        )
    )
    if selected:
        attempts = [copy.ATTEMPTS_HEADING]
        attempts.extend(
            copy.ATTEMPTS.format(
                task=literal(task["id"], "task id"),
                example_count=len(task["example_ids"]),
                examples=", ".join(f"**{literal(item, 'input example id')}**" for item in task["example_ids"])
                or copy.NONE,
                attempts=task["attempts"],
                unit=copy.ATTEMPT_UNITS[plan["provider"]],
            )
            for task in selected
        )
        parts.extend((attempts[0] + "\n\n" + "\n".join(attempts[1:]), copy.ATTEMPTS_NOTE))
    parts.append(copy.SOURCES_HEADING)
    parts.append(
        "\n".join(
            copy.SOURCE.format(
                path=literal(source["path"], "selected source path"),
                kind=copy.SOURCE_KINDS[source["kind"]],
            )
            for source in plan["sources"]
        )
        or copy.SOURCES_EMPTY
    )
    limits = plan["limits"]
    uncapped = copy.UNSETTLED if limits["unresolved_inputs"] else copy.NO_CAP
    minutes = copy.MINUTES.format(value=limits["max_minutes"]) if limits["max_minutes"] is not None else uncapped
    cost = copy.DOLLARS.format(value=limits["max_cost_usd"]) if limits["max_cost_usd"] is not None else uncapped
    parts.extend(
        (
            copy.LIMITS_HEADING,
            copy.LIMITS.format(attempts=limits["max_construction_attempts"], minutes=minutes, cost=cost),
        )
    )
    if limits["unresolved_inputs"]:
        parts.append(copy.LIMITS_QUESTIONS.format(inputs=_inputs(limits["unresolved_inputs"])))
    if plan["unresolved_inputs"]:
        parts.append(copy.UNRESOLVED.format(inputs=_inputs(plan["unresolved_inputs"])))
    parts.append(copy.EXCLUSIONS.format(exclusions=_inputs(plan["exclusions"])))
    if plan["deferred"]:
        deferred = [copy.DEFERRED_HEADING]
        deferred.extend(
            copy.DEFERRED.format(
                improvement=literal(item["improvement"], "deferred improvement"),
                reason=literal(item["reason"], "implementation limit"),
            )
            for item in plan["deferred"]
        )
        parts.append(deferred[0] + "\n\n" + "\n".join(deferred[1:]))
    if progress["blockers"]:
        parts.append(
            copy.BLOCKERS_HEADING
            + "\n\n"
            + "\n".join(
                copy.BLOCKER.format(reason=literal(reason, "construction blocker")) for reason in progress["blockers"]
            )
        )
    if progress["scope_approved"]:
        parts.extend(
            (
                copy.APPROVED,
                copy.CONSTRUCTION_READY if progress["construction_ready"] else copy.CONSTRUCTION_BLOCKED,
                copy.EXECUTION_SEPARATE,
            )
        )
    elif progress["stage"] == "scope_declined":
        parts.append(copy.DECLINED)
    elif progress["stage"] == "scope_revision":
        parts.append(copy.REVISION)
    elif not progress["context_current"]:
        parts.append(copy.STALE_DECISION)
    elif not selected:
        parts.append(copy.NO_SELECTION)
    else:
        parts.append(copy.PENDING)
    question = progress["pending_question"] if expansion_state.scope_ready(state) else state["pending_question"]
    if question is not None:
        parts.append(literal(question, "expansion scope question"))
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
    lifecycle = expansion_state.record(state)
    if payload["stage"] == "expansion_continue" and lifecycle is not None and lifecycle["schema_version"] == 2:
        raise ValueError("Use expansion_scope to render structured expansion scopes")
    facts = mapping(payload.get("facts", {}), "expansion facts", {"expansion_observations"})
    progress = expansion_state.derive(state, facts.get("expansion_observations"))
    if progress is None:
        raise ValueError("Save a structured expansion lifecycle before continuation")
    if payload["stage"] == "expansion_scope":
        return _scope(state, progress)
    if not progress["context_current"]:
        raise ValueError("Expansion context changed; reconcile intent and handoff before continuation")
    if not expansion_state.entry_ready(state):
        raise ValueError(
            "Resolve shared prerequisites, intent questions, and saved blockers before expansion continuation"
        )
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Render only the saved unresolved expansion question")
    parts = [shared.NEXT_STEPS, onboarding.checklist("expand", state)]
    parts.append(literal(progress["pending_question"], "expansion question"))
    return "\n\n".join(parts) + "\n"
