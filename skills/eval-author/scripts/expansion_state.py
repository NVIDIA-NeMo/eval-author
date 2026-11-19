# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Expansion entry decisions bound to provider, confirmed intent, and retained audit context."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, NotRequired, TypedDict, cast


class EntryChoice(TypedDict):
    kind: Literal["starter", "trace"]
    source: str


def validate_entry_choice(value: object) -> EntryChoice:
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "source"}
        or value["kind"] not in ("starter", "trace")
        or not isinstance(value["source"], str)
        or not value["source"].strip()
        or len(value["source"]) > 4000
    ):
        raise ValueError("Expansion entry_choice requires a supported kind and bounded nonempty source")
    return cast(EntryChoice, value)


class PendingDecision(TypedDict):
    question: str
    source: str


class EntryRecord(TypedDict):
    schema_version: Literal[1]
    setup: NotRequired[Literal["gym", "harbor"]]
    ethos_digest: str
    handoff_digest: str | None
    pending: PendingDecision


class ScopeTask(TypedDict):
    task: str
    scenario: str
    outcome: str
    verification: str


class ScopePlan(TypedDict):
    summary: str
    tasks: list[ScopeTask]
    breadth: str
    limits: str
    deferred: list[str]


class ScopeDecision(TypedDict):
    kind: Literal["approved", "declined", "revise"]
    source: str


class ScopeRecord(TypedDict):
    schema_version: Literal[2]
    setup: Literal["gym", "harbor"]
    ethos_digest: str | None
    handoff_digest: str | None
    pending: PendingDecision | None
    path: Literal["starter_suite", "audit_tool_gap", "recorded_interaction"]
    plan: ScopePlan
    decision: ScopeDecision | None


def record(state: dict) -> EntryRecord | ScopeRecord | None:
    scope = state["scopes"].get("expand", {})
    return cast(EntryRecord | ScopeRecord | None, scope.get("lifecycle")) if isinstance(scope, dict) else None


def handoff_digest(state: dict) -> str | None:
    scope = state["scopes"].get("expand", {})
    handoff = scope.get("audit_handoff") if isinstance(scope, dict) else None
    if handoff is None:
        return None
    return hashlib.sha256(json.dumps(handoff, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("Expansion requires a saved checkpoint")
    scope = value.get("schema_version") == 2
    fields = {
        "schema_version",
        "ethos_digest",
        "handoff_digest",
        "pending",
    }
    if scope:
        fields |= {"setup", "path", "plan", "decision"}
    if (set(value) if scope else set(value) - {"setup"}) != fields:
        raise ValueError("Expansion entry requires versioned context bindings and a pending decision")
    if type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2):
        raise ValueError("Unsupported expansion lifecycle version")
    if "setup" in value and value["setup"] not in ("gym", "harbor"):
        raise ValueError("Expansion entry requires a supported provider")
    for field in ("ethos_digest", "handoff_digest"):
        digest = value[field]
        if digest is None and (field == "handoff_digest" or scope and value["path"] == "recorded_interaction"):
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"Invalid expansion {field}")
    pending = value["pending"]
    if scope:
        _validate_plan(value)
        if pending is None:
            return
    if not isinstance(pending, dict) or set(pending) != {"question", "source"}:
        raise ValueError("Expansion entry requires the unresolved question and its source")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in pending.values()):
        raise ValueError("Expansion question and source must be bounded nonempty text")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4000


def _validate_plan(value: dict) -> None:
    if value["path"] not in ("starter_suite", "audit_tool_gap", "recorded_interaction"):
        raise ValueError("Use the existing supported authoring workflow")
    plan = value["plan"]
    if not isinstance(plan, dict) or set(plan) != {"summary", "tasks", "breadth", "limits", "deferred"}:
        raise ValueError("Save the existing coverage-planning checkpoint's presentation fields")
    if not all(_text(plan[key]) for key in ("summary", "breadth", "limits")):
        raise ValueError("Scope summary, breadth and limits require bounded nonempty text")
    for key in ("tasks", "deferred"):
        if not isinstance(plan[key], list) or len(plan[key]) > 1000:
            raise ValueError("Scope presentation lists must be bounded")
    if not all(_text(item) for item in plan["deferred"]):
        raise ValueError("Retain deferred work as bounded nonempty text")
    for task in plan["tasks"]:
        if not isinstance(task, dict) or set(task) != {"task", "scenario", "outcome", "verification"}:
            raise ValueError("Scope task rows require the task, scenario, outcome and verification")
        if not all(_text(item) for item in task.values()):
            raise ValueError("Scope task cells require bounded nonempty text")
    decision = value["decision"]
    if decision is not None:
        if (
            not isinstance(decision, dict)
            or set(decision) != {"kind", "source"}
            or decision["kind"] not in ("approved", "declined", "revise")
            or not _text(decision["source"])
        ):
            raise ValueError("Save the actual scope decision and its source")
        if decision["kind"] in ("approved", "declined") and value["pending"] is not None:
            raise ValueError("Clear the answered scope question")
        if decision["kind"] == "revise" and value["pending"] is None:
            raise ValueError("Save the actual unresolved scope revision question")
        if decision["kind"] == "approved" and not plan["tasks"]:
            raise ValueError("Resolve supported scope before recording its agreement")


def new_record(state: dict, *, question: str, source: str) -> EntryRecord:
    value: EntryRecord = {
        "schema_version": 1,
        "setup": state["setup"],
        "ethos_digest": state["ethos"]["confirmed_digest"],
        "handoff_digest": handoff_digest(state),
        "pending": {"question": question, "source": source},
    }
    validate(value)
    return value


def new_scope(state: dict, *, path: str, plan: dict, question: str | None, source: str) -> ScopeRecord:
    import copy

    value = {
        "schema_version": 2,
        "setup": state["setup"],
        "ethos_digest": state["ethos"]["confirmed_digest"],
        "handoff_digest": handoff_digest(state),
        "path": path,
        "plan": copy.deepcopy(plan),
        "decision": None,
        "pending": {"question": question, "source": source} if question is not None else None,
    }
    validate(value)
    return cast(ScopeRecord, value)


def observe(repo, state: dict) -> dict:
    from journey_state import ethos_digest

    digest = None
    if record(state) is not None:
        try:
            digest = ethos_digest(repo, state["ethos"]["path"])
        except (OSError, ValueError):
            pass
    return {"repository": state["repository"], "revision": state["revision"], "ethos_digest": digest}


def derive(state: dict, observed: object) -> dict | None:
    value = record(state)
    if value is None:
        return None
    validate(value)
    if (
        not isinstance(observed, dict)
        or set(observed) != {"repository", "revision", "ethos_digest"}
        or observed["repository"] != state["repository"]
        or type(observed["revision"]) is not int
        or observed["revision"] != state["revision"]
    ):
        raise ValueError("Supply expansion observations from the current repository and revision")
    scoped_trace = (
        value["schema_version"] == 2 and value["path"] == "recorded_interaction" and value["ethos_digest"] is None
    )
    current = (
        "setup" in value
        and value["setup"] == state["setup"]
        and value["ethos_digest"] == state["ethos"]["confirmed_digest"]
        and value["handoff_digest"] == handoff_digest(state)
        and (
            scoped_trace or state["ethos"]["stage"] == "complete" and value["ethos_digest"] == observed["ethos_digest"]
        )
    )
    scope = state["scopes"]["expand"]
    decision = value["decision"] if value["schema_version"] == 2 and current and not scope.get("needs_review") else None
    return {
        "stage": "entry"
        if value["schema_version"] == 1
        else {"approved": "scope_approved", "declined": "scope_declined", "revise": "scope_revision"}.get(
            decision["kind"] if decision else None, "scope"
        ),
        "context_current": current,
        "pending_question": value["pending"]["question"] if current and value["pending"] else None,
        "complete": False,
    }


def entry_ready(state: dict) -> bool:
    from journey_state import active_blockers, checklist

    return (
        state["journey"] == "expand"
        and not state["ethos"]["remaining_questions"]
        and not active_blockers(state)
        and all(row["complete"] for row in checklist(state)[:2])
    )


def trace_scope(state: dict) -> bool:
    value = record(state)
    return (
        state["journey"] == "expand"
        and value is not None
        and value["schema_version"] == 2
        and value["path"] == "recorded_interaction"
    )


def trace_ethos_optional(state: dict) -> bool:
    return (
        trace_scope(state)
        and state["ethos"]["stage"] not in ("review", "interview", "draft", "write")
        and not state["ethos"]["remaining_questions"]
    )


def checkpoint_ready(state: dict) -> bool:
    from journey_state import active_blockers, checklist

    if not trace_scope(state):
        return entry_ready(state)
    return (
        trace_ethos_optional(state)
        and checklist(state)[0]["complete"]
        and not active_blockers(state, ethos_optional=True)
    )


def reconcile_question(repo, state: dict, *, answered_ethos_review: bool = False) -> None:
    value = record(state)
    if value is not None and value["schema_version"] == 2:
        from journey_state import active_blockers, checklist

        scope_question = value["pending"]["question"] if value["pending"] else None
        ethos_question = (
            state["next_handoff"] == "ethos-orchestrate"
            and checklist(state)[0]["complete"]
            and not any(blocker["phase"] in {"setup", "ethos"} for blocker in active_blockers(state))
        )
        if answered_ethos_review and (
            ethos_question or state["pending_question"] == scope_question and checkpoint_ready(state)
        ):
            state["pending_question"] = None
        answered_ethos_review = False
    if (
        value is None
        or not value["pending"]
        or (state["pending_question"] is not None and not answered_ethos_review)
        or not checkpoint_ready(state)
        or value["schema_version"] == 2
        and state["scopes"]["expand"].get("needs_review")
    ):
        return
    progress = derive(state, observe(repo, state))
    if progress is not None and progress["context_current"]:
        state["pending_question"] = progress["pending_question"]


def transition(repo, previous: dict, updated: dict) -> None:
    before, after = record(previous), record(updated)
    if before is not None and after is None:
        raise ValueError("Retain structured expansion progress when updating its scope")
    if after is None:
        return
    if updated["journey_status"].get("expand") == "complete":
        raise ValueError("An unresolved expansion entry decision cannot establish completion")
    if after["schema_version"] == 2:
        if before is not None and before["schema_version"] == 2:
            fields = ("plan", "path", "setup", "ethos_digest", "handoff_digest")
            if any(before[key] != after[key] for key in fields) and after["decision"] is not None:
                if after["decision"] == before["decision"]:
                    raise ValueError("Record the user's decision on the changed scope, or leave it unresolved")
        progress = derive(updated, observe(repo, updated))
        assert progress is not None
        if before != after:
            if not checkpoint_ready(updated):
                raise ValueError("Expansion scope requires its journey and shared prerequisites")
            if not progress["context_current"]:
                raise ValueError("Bind the expansion scope to the current provider, intent, and handoff")
            if (
                before is not None
                and before["pending"] is not None
                and after["pending"] is None
                and updated["pending_question"] == before["pending"]["question"]
            ):
                raise ValueError("Clear or replace the answered scope question")
            scope = updated["scopes"]["expand"]
            current = progress["context_current"]
            scope["confirmed"] = bool(current and after["decision"] and after["decision"]["kind"] == "approved")
            scope["needs_review"] = not current
        if (
            after["pending"] is not None
            and progress["context_current"]
            and not updated["scopes"]["expand"].get("needs_review")
            and checkpoint_ready(updated)
        ):
            if updated["pending_question"] != after["pending"]["question"]:
                if not checkpoint_ready(previous) and updated["pending_question"] is None:
                    reconcile_question(repo, updated)
                else:
                    raise ValueError("Save the actual unresolved scope question")
        return
    progress = derive(updated, observe(repo, updated))
    assert progress is not None
    if before != after:
        if not entry_ready(updated):
            raise ValueError("Expansion entry requires its journey, shared prerequisites, and confirmed Ethos")
        if not progress["context_current"]:
            raise ValueError("Bind the expansion question to the current provider, confirmed intent, and handoff")
        if updated["pending_question"] != progress["pending_question"]:
            raise ValueError("Save the expansion entry question as the pending question")
    if (
        entry_ready(updated)
        and progress["context_current"]
        and updated["pending_question"] != progress["pending_question"]
    ):
        if not entry_ready(previous) and updated["pending_question"] is None:
            reconcile_question(repo, updated)
        else:
            raise ValueError("Retain the unresolved expansion question until its lifecycle decision changes")
