# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Expansion entry decisions bound to provider, confirmed intent, and retained audit context."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
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


class EvidenceReference(TypedDict):
    path: str
    digest: str


class ValidationResult(TypedDict):
    sha256: str
    status: Literal["passed", "failed", "blocked"]
    source: str
    evidence: list[EvidenceReference]


class DraftProgress(TypedDict):
    path: str
    sha256: str
    validation: ValidationResult | None


class CreationActivity(TypedDict):
    task: str
    status: Literal["creating", "validating", "repairing", "blocked"]
    detail: str


class CreationRecord(TypedDict):
    tasks: dict[str, DraftProgress]
    active: CreationActivity | None
    pending: PendingDecision | None


class ScopeRecord(TypedDict):
    schema_version: Literal[2]
    setup: Literal["gym", "harbor"]
    ethos_digest: str | None
    handoff_digest: str | None
    pending: PendingDecision | None
    path: Literal["starter_suite", "audit_tool_gap", "recorded_interaction"]
    plan: ScopePlan
    decision: ScopeDecision | None
    creation: NotRequired[CreationRecord]


def task_key(task: ScopeTask) -> str:
    return hashlib.sha256(json.dumps(task, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pending(value: EntryRecord | ScopeRecord) -> PendingDecision | None:
    if value["pending"] is not None:
        return value["pending"]
    if value["schema_version"] == 2 and value["decision"] and value["decision"]["kind"] == "approved":
        return (value.get("creation") or {}).get("pending")
    return None


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
    if (set(value) - {"creation"} if scope else set(value) - {"setup"}) != fields:
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
        if "creation" in value:
            _validate_creation(value)
        if pending is None:
            return
    if not isinstance(pending, dict) or set(pending) != {"question", "source"}:
        raise ValueError("Expansion entry requires the unresolved question and its source")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in pending.values()):
        raise ValueError("Expansion question and source must be bounded nonempty text")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4000


def _validate_creation(value: dict) -> None:
    from journey_state import MAX_TASK_EVIDENCE, _relative_task_path

    creation = value["creation"]
    if not isinstance(creation, dict) or set(creation) != {"tasks", "active", "pending"}:
        raise ValueError("Save creation artifacts, current activity, and the actual pending question")
    tasks = creation["tasks"]
    if not isinstance(tasks, dict) or len(tasks) > 1000:
        raise ValueError("Creation task references must be a bounded mapping")
    for key, task in tasks.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Bind creation artifacts to their agreed task row")
        if not isinstance(task, dict) or set(task) != {"path", "sha256", "validation"}:
            raise ValueError("Save each draft's path, revision, and optional validation result")
        _relative_task_path(task["path"])
        check = task["validation"]
        digests = [task["sha256"]]
        if check is not None:
            if (
                not isinstance(check, dict)
                or set(check) != {"sha256", "status", "source", "evidence"}
                or check["status"] not in ("passed", "failed", "blocked")
                or not _text(check["source"])
                or not isinstance(check["evidence"], list)
                or not 0 < len(check["evidence"]) <= MAX_TASK_EVIDENCE
            ):
                raise ValueError("Record the owning workflow's validation result and retained evidence")
            digests.append(check["sha256"])
            for ref in check["evidence"]:
                if not isinstance(ref, dict) or set(ref) != {"path", "digest"}:
                    raise ValueError("Validation evidence requires a path and digest")
                _relative_task_path(ref["path"])
                digests.append(ref["digest"])
        if any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item) for item in digests):
            raise ValueError("Bind draft and evidence revisions using SHA-256 digests")
    planned = {task_key(task) for task in value["plan"]["tasks"]}
    paths = [task["path"] for key, task in tasks.items() if key in planned]
    if len(set(paths)) != len(paths):
        raise ValueError("Each agreed task needs its own draft")
    active = creation["active"]
    if active is not None and (
        not isinstance(active, dict)
        or set(active) != {"task", "status", "detail"}
        or not isinstance(active["task"], str)
        or active["task"] not in planned
        or active["status"] not in ("creating", "validating", "repairing", "blocked")
        or not _text(active["detail"])
    ):
        raise ValueError("Bind current activity to an agreed task and describe the actual work or blocker")
    question = creation["pending"]
    if question is not None and (
        not isinstance(question, dict)
        or set(question) != {"question", "source"}
        or not all(_text(item) for item in question.values())
    ):
        raise ValueError("Save only the actual unresolved creation question and its source")


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
    if "creation" in value and len({task_key(task) for task in plan["tasks"]}) != len(plan["tasks"]):
        raise ValueError("Distinguish agreed task scenarios before recording creation progress")
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
    previous = record(state)
    if previous and previous["schema_version"] == 2 and previous.get("creation") is not None:
        value["creation"] = {"tasks": copy.deepcopy(previous["creation"]["tasks"]), "active": None, "pending": None}
    validate(value)
    return cast(ScopeRecord, value)


def _sources(value: EntryRecord | ScopeRecord) -> tuple[dict[str, str], set[str]]:
    tasks = (value.get("creation") or {}).get("tasks", {})
    return (
        {key: task["path"] for key, task in tasks.items()},
        {ref["path"] for task in tasks.values() for ref in (task["validation"] or {}).get("evidence", [])},
    )


def observe(repo: Path, state: dict) -> dict:
    from journey_state import ethos_digest, observe_artifacts

    digest = None
    if record(state) is not None:
        try:
            digest = ethos_digest(repo, state["ethos"]["path"])
        except (OSError, ValueError):
            pass
    observed = {"repository": state["repository"], "revision": state["revision"], "ethos_digest": digest}
    value = record(state)
    if value and "creation" in value:
        observed["artifacts"] = observe_artifacts(repo, *_sources(value))
    return observed


def _creation(value: ScopeRecord, observed: object) -> dict:
    tasks, files = _sources(value)
    if not isinstance(observed, dict) or set(observed) != {"tasks", "files"}:
        raise ValueError("Supply current creation task and evidence observations")
    for field, allowed in (("tasks", tasks), ("files", files)):
        if not isinstance(observed[field], dict) or set(observed[field]) != set(allowed):
            raise ValueError("Observe exactly the declared creation artifacts")
        if any(
            item is not None and (not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item))
            for item in observed[field].values()
        ):
            raise ValueError("Creation observations require SHA-256 digests or missing evidence")
    creation = value["creation"]
    active = creation["active"]
    rows = []
    failures = []
    for planned in value["plan"]["tasks"]:
        key = task_key(planned)
        task = creation["tasks"].get(key)
        status = "queued"
        if task:
            check = task["validation"]
            if observed["tasks"][key] is None:
                status = "missing"
            elif observed["tasks"][key] != task["sha256"]:
                status = "stale"
            elif check is None:
                status = "draft"
            elif check["sha256"] != task["sha256"] or any(
                observed["files"][ref["path"]] != ref["digest"] for ref in check["evidence"]
            ):
                status = "stale"
            else:
                status = check["status"]
                if status in ("failed", "blocked"):
                    failures.append(f"{planned['task']}: {check['source']}")
        if active and active["task"] == key:
            status = active["status"]
        rows.append({"task": planned["task"], "status": status})
    return {
        "rows": rows,
        "detail": active["detail"] if active else " ".join(failures) or None,
        "ready_for_review": bool(rows) and all(row["status"] == "passed" for row in rows) and not creation["pending"],
    }


def derive(state: dict, observed: object) -> dict | None:
    value = record(state)
    if value is None:
        return None
    validate(value)
    if (
        not isinstance(observed, dict)
        or set(observed)
        != {"repository", "revision", "ethos_digest"} | ({"artifacts"} if "creation" in value else set())
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
    creation = _creation(value, observed["artifacts"]) if value["schema_version"] == 2 and "creation" in value else None
    creating = bool(creation and decision and decision["kind"] == "approved")
    if creation is not None:
        creation["ready_for_review"] = creation["ready_for_review"] and creating and checkpoint_ready(state)
    question = pending(value) if creating else value["pending"]
    return {
        "stage": "creation"
        if creating
        else "entry"
        if value["schema_version"] == 1
        else {"approved": "scope_approved", "declined": "scope_declined", "revise": "scope_revision"}.get(
            decision["kind"] if decision else None, "scope"
        ),
        "context_current": current,
        "pending_question": question["question"] if current and question else None,
        "creation": creation,
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


def checkpoint_ready(state: dict) -> bool:
    value = record(state)
    if value is None or value["schema_version"] == 1 or value["path"] != "recorded_interaction":
        return entry_ready(state)
    return (
        state["journey"] == "expand"
        and all(
            state["prerequisites"].get(key, {}).get("status") in ("verified", "confirmed")
            for key in ("python", "runtime")
        )
        and not any(row["phase"] in ("setup", "ethos", "expand") for row in state["blockers"])
        and state["ethos"]["stage"] not in ("review", "interview", "draft", "write")
        and not state["ethos"]["remaining_questions"]
    )


def reconcile_question(repo, state: dict, *, answered_ethos_review: bool = False) -> None:
    value = record(state)
    if (
        value is None
        or not pending(value)
        or (state["pending_question"] is not None and not answered_ethos_review)
        or not checkpoint_ready(state)
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
    if before and before.get("creation") is not None and after["schema_version"] != 2:
        raise ValueError("Retain creation progress when returning to scope decisions")
    if after["schema_version"] == 2:
        prior_creation = before.get("creation") if before else None
        creation = after.get("creation")
        observed = observe(repo, updated)
        if prior_creation and (not creation or set(prior_creation["tasks"]) - set(creation["tasks"])):
            raise ValueError("Retain partial creation artifacts when revising scope")
        if before is not None and before["schema_version"] == 2:
            fields = ("plan", "path", "setup", "ethos_digest", "handoff_digest")
            if any(before[key] != after[key] for key in fields) and after["decision"] is not None:
                if after["decision"] == before["decision"]:
                    raise ValueError("Record the user's decision on the changed scope, or leave it unresolved")
        if before is None or any(before.get(key) != after.get(key) for key in after if key != "creation"):
            scope = updated["scopes"]["expand"]
            progress = derive(updated, observed)
            assert progress is not None
            current = progress["context_current"]
            scope["confirmed"] = bool(current and after["decision"] and after["decision"]["kind"] == "approved")
            scope["needs_review"] = not current
        if creation is not None and creation != prior_creation:
            progress = derive(updated, observed)
            assert progress is not None
            if progress["stage"] != "creation" and not (
                prior_creation is not None
                and creation["tasks"] == prior_creation["tasks"]
                and creation["active"] is None
                and creation["pending"] is None
            ):
                raise ValueError("Agree the current task scope before recording creation progress")
            previous_tasks = prior_creation["tasks"] if prior_creation else {}
            planned = {task_key(task) for task in after["plan"]["tasks"]}
            for key, task in creation["tasks"].items():
                if key not in planned and task != previous_tasks.get(key):
                    raise ValueError("Create only tasks from the agreed scope; retain other artifacts unchanged")
                check = task["validation"]
                if check and check["status"] == "passed" and task != previous_tasks.get(key):
                    observations = observed["artifacts"]
                    if (
                        not checkpoint_ready(updated)
                        or check["sha256"] != task["sha256"]
                        or observations["tasks"][key] != task["sha256"]
                        or any(observations["files"][ref["path"]] != ref["digest"] for ref in check["evidence"])
                    ):
                        raise ValueError("Record passing checks only for the current draft and retained evidence")
        question = pending(after)
        if question is not None and checkpoint_ready(updated):
            if updated["pending_question"] != question["question"]:
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
    if updated["journey_status"].get("expand") == "complete":
        raise ValueError("An unresolved expansion entry decision cannot establish completion")
