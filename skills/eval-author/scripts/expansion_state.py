# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Expansion entry decisions bound to provider, confirmed intent, and retained audit context."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import ModuleType

PATHS = {
    "starter_suite": "eval-author-first-eval",
    "audit_tool_gap": "eval-author-task-create",
    "recorded_interaction": "eval-author-task-create",
}
MAX_ITEMS = 1000


def _fields(value, names: str) -> None:
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("Expansion scope requires its explicit fields")


def _text(value) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError("Expansion scope text must be bounded and nonempty")


def _list(value) -> None:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError("Expansion scope collections must be bounded lists")


def _strings(value) -> None:
    _list(value)
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise ValueError("Expansion scope identities and inputs must be unique")


def _digest(value, *, nullable=False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Expansion bindings require lowercase SHA-256 digests")


def _path(value) -> None:
    _text(value)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value == "." or "\\" in value:
        raise ValueError("Expansion sources must be canonical repository-relative paths")


def _receipt(value, *, decision=False) -> None:
    if value is None:
        return
    _fields(value, "digest kind source" if decision else "digest source")
    _digest(value["digest"])
    _text(value["source"])
    if decision and value["kind"] not in {"approved", "declined", "revise"}:
        raise ValueError("Unknown expansion scope decision")


def _validate_scope(value: dict) -> None:
    _fields(
        value,
        "schema_version ethos_digest handoff_digest pending request path plan presented decision history",
    )
    if not isinstance(value["path"], str) or value["path"] not in PATHS:
        raise ValueError("Choose an existing supported expansion path")
    _digest(value["ethos_digest"], nullable=value["path"] == "recorded_interaction")
    _digest(value["handoff_digest"], nullable=True)
    if value["pending"] is not None:
        _fields(value["pending"], "question source")
        for item in value["pending"].values():
            _text(item)
    _fields(value["request"], "outcome source")
    for item in value["request"].values():
        _text(item)
    plan = value["plan"]
    _fields(
        plan,
        "provider tasks selected_task_ids sources coverage_report deferred exclusions limits unresolved_inputs source",
    )
    if plan["provider"] not in {"gym", "harbor"}:
        raise ValueError("Expansion scope requires NeMo Gym or Harbor")
    _text(plan["source"])
    _list(plan["tasks"])
    identities = []
    for task in plan["tasks"]:
        _fields(
            task,
            "id scenario expected_outcome grading validation example_ids attempts unresolved_inputs recommendation_id target_tool",
        )
        for key in ("id", "scenario", "expected_outcome", "grading", "validation"):
            _text(task[key])
        identities.append(task["id"])
        _strings(task["example_ids"])
        _strings(task["unresolved_inputs"])
        if type(task["attempts"]) is not int or task["attempts"] < 1:
            raise ValueError("Planned task attempts must be positive integers")
        for key in ("recommendation_id", "target_tool"):
            if task[key] is not None:
                _text(task[key])
        if task["recommendation_id"] is not None and not re.fullmatch(r"R[1-9][0-9]*", task["recommendation_id"]):
            raise ValueError("Use report recommendation identities such as R1")
    if len(set(identities)) != len(identities):
        raise ValueError("Proposed expansion task identities must be unique")
    _strings(plan["selected_task_ids"])
    if not set(plan["selected_task_ids"]) <= set(identities):
        raise ValueError("Select tasks from the proposed expansion scope")
    _list(plan["sources"])
    paths = []
    for source in plan["sources"]:
        _fields(source, "path digest kind")
        _path(source["path"])
        _digest(source["digest"])
        if source["kind"] not in {"file", "task"}:
            raise ValueError("Expansion sources must identify a file or task tree")
        paths.append(source["path"])
    if len(set(paths)) != len(paths):
        raise ValueError("Expansion source paths must be unique")
    if plan["coverage_report"] is not None:
        _path(plan["coverage_report"])
    _list(plan["deferred"])
    deferred_ids = []
    for improvement in plan["deferred"]:
        _fields(improvement, "id improvement reason")
        for item in improvement.values():
            _text(item)
        deferred_ids.append(improvement["id"])
    if len(set(deferred_ids)) != len(deferred_ids):
        raise ValueError("Deferred improvement identities must be unique")
    for key in ("exclusions", "unresolved_inputs"):
        _strings(plan[key])
    limits = plan["limits"]
    _fields(limits, "max_construction_attempts max_minutes max_cost_usd source unresolved_inputs")
    _text(limits["source"])
    _strings(limits["unresolved_inputs"])
    if type(limits["max_construction_attempts"]) is not int or not 1 <= limits["max_construction_attempts"] <= 4:
        raise ValueError("Construction permits an initial attempt and at most three repairs")
    for key in ("max_minutes", "max_cost_usd"):
        number = limits[key]
        if number is not None and (
            type(number) not in (int, float)
            or not math.isfinite(number)
            or number < 0
            or key == "max_minutes"
            and number == 0
        ):
            raise ValueError("Expansion time and cost limits must be finite valid numbers or explicit no cap")
    _receipt(value["presented"])
    _receipt(value["decision"], decision=True)
    _list(value["history"])
    for decision in value["history"]:
        if decision is None:
            raise ValueError("Retained scope decisions must be explicit receipts")
        _receipt(decision, decision=True)


def record(state: dict) -> dict | None:
    scope = state["scopes"].get("expand", {})
    return scope.get("lifecycle") if isinstance(scope, dict) else None


def handoff_digest(state: dict) -> str | None:
    scope = state["scopes"].get("expand", {})
    handoff = scope.get("audit_handoff") if isinstance(scope, dict) else None
    if handoff is None:
        return None
    return hashlib.sha256(json.dumps(handoff, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(value: dict) -> None:
    if isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 2:
        try:
            _validate_scope(value)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Malformed structured expansion scope") from exc
        return
    if not isinstance(value, dict) or set(value) - {"setup"} != {
        "schema_version",
        "ethos_digest",
        "handoff_digest",
        "pending",
    }:
        raise ValueError("Expansion entry requires versioned context bindings and a pending decision")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported expansion lifecycle version")
    if "setup" in value and value["setup"] not in ("gym", "harbor"):
        raise ValueError("Expansion entry requires a supported provider")
    for field in ("ethos_digest", "handoff_digest"):
        digest = value[field]
        if digest is None and field == "handoff_digest":
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"Invalid expansion {field}")
    pending = value["pending"]
    if not isinstance(pending, dict) or set(pending) != {"question", "source"}:
        raise ValueError("Expansion entry requires the unresolved question and its source")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in pending.values()):
        raise ValueError("Expansion question and source must be bounded nonempty text")


def new_record(state: dict, *, question: str, source: str) -> dict:
    value = {
        "schema_version": 1,
        "setup": state["setup"],
        "ethos_digest": state["ethos"]["confirmed_digest"],
        "handoff_digest": handoff_digest(state),
        "pending": {"question": question, "source": source},
    }
    validate(value)
    return value


def new_scope(state: dict, *, path: str, request: dict, plan: dict, question: str | None, source: str) -> dict:
    value = {
        "schema_version": 2,
        "ethos_digest": state["ethos"]["confirmed_digest"],
        "handoff_digest": handoff_digest(state),
        "pending": {"question": question, "source": source} if question is not None else None,
        "request": copy.deepcopy(request),
        "path": path,
        "plan": copy.deepcopy(plan),
        "presented": None,
        "decision": None,
        "history": [],
    }
    _text(source)
    validate(value)
    return value


def scope_digest(state: dict) -> str:
    value = record(state)
    if value is None or value["schema_version"] != 2:
        raise ValueError("Save the expansion scope before binding its agreement")
    return hashlib.sha256(
        json.dumps(
            {key: value[key] for key in ("path", "plan", "ethos_digest", "handoff_digest")}
            | {
                "outcome": value["request"]["outcome"],
                "repository": state["repository"],
                "agent": state["agent"],
                "ethos_path": state["ethos"]["path"] if value["ethos_digest"] is not None else None,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


@lru_cache(maxsize=1)
def _task_pipeline() -> ModuleType:
    location = Path(__file__).resolve().parents[2] / "eval-author-task-create" / "scripts" / "task_pipeline.py"
    spec = importlib.util.spec_from_file_location("_expansion_task_pipeline", location)
    if spec is None or spec.loader is None:
        raise ValueError("The existing task-gap selector is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit_observation(repo: Path, state: dict, sources: dict) -> dict:
    import audit_state
    from journey_state import _task_local_path

    value = record(state)
    assert value is not None
    handoff = state["scopes"]["expand"].get("audit_handoff")
    result = {
        "handoff_current": not bool(handoff),
        "coverage_current": not bool(handoff),
        "recommendations": [],
        "actionable_tools": [],
    }
    report = value["plan"]["coverage_report"]
    report_ref = next(
        (
            {"path": source["path"], "digest": source["digest"]}
            for source in value["plan"]["sources"]
            if source["path"] == report and source["kind"] == "file"
        ),
        None,
    )
    if handoff:
        origin = audit_state.record(state)
        progress = audit_state.progress(repo, state)
        result["handoff_current"] = bool(
            origin
            and progress
            and progress["next_action_current"]
            and progress["findings_digest"] == handoff["findings_digest"]
            and all((origin["findings"] or {}).get(key) == handoff[key] for key in ("report", "content"))
            and (origin["next_action"] or {}).get("kind") == "improve"
            and (origin["next_action"] or {}).get("findings_digest") == handoff["findings_digest"]
            and (origin["next_action"] or {}).get("recommendation_ids") == handoff["recommendation_ids"]
        )
        result["coverage_current"] = bool(origin and report_ref in (origin["measurement"] or {}).get("evidence", []))
        if result["handoff_current"] and origin is not None:
            try:
                result["recommendations"] = [
                    {key: item[key] for key in ("id", "action", "items")}
                    for item in audit_state.selected_recommendations(repo, origin)
                ]
                _list(result["recommendations"])
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                result["handoff_current"] = False
                result["recommendations"] = []
    if report_ref and sources.get(report) == report_ref["digest"]:
        try:
            selected = _task_pipeline()._select(_task_local_path(repo, report), None)
            result["actionable_tools"] = [
                {"name": item["name"], "task_slug": item["task_slug"]} for item in selected["actionable_tools"]
            ]
            _list(result["actionable_tools"])
            for item in result["actionable_tools"]:
                _text(item["name"])
                _text(item["task_slug"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            result["actionable_tools"] = []
    return result


def observe(repo, state: dict) -> dict:
    from journey_state import MAX_BYTES, _file_digest, _task_local_path, ethos_digest, task_digest, task_lifecycle

    digest = None
    if record(state) is not None:
        try:
            digest = ethos_digest(repo, state["ethos"]["path"])
        except (OSError, ValueError):
            pass
    result = {"repository": state["repository"], "revision": state["revision"], "ethos_digest": digest}
    value = record(state)
    if value is None or value["schema_version"] == 1:
        return result
    validate(value)
    source_limit = MAX_BYTES
    if value["path"] == "recorded_interaction":
        from trace_loading import MAX_RECORD_BYTES

        source_limit = MAX_RECORD_BYTES
    sources, patterns = {}, []
    creation = state["scopes"].get("create", {})
    first_eval = creation.get("first_eval", {}) if isinstance(creation, dict) else {}
    for source in value["plan"]["sources"]:
        path = source["path"]
        try:
            if source["kind"] == "task":
                sources[path] = task_digest(repo, path)
                reviewed = any(
                    task["path"] == path
                    and task["sha256"] == sources[path]
                    and (task.get("review") or {}).get("sha256") == sources[path]
                    and (task.get("review") or {}).get("decision") == "approved"
                    for task in first_eval.get("tasks", {}).values()
                )
                if task_lifecycle(repo, state, path)["status"] == "accepted" or reviewed:
                    patterns.append(path)
            else:
                sources[path], _ = _file_digest(_task_local_path(repo, path), source_limit)
        except (OSError, ValueError):
            sources[path] = None
    result.update(
        sources=sources,
        starter_patterns=patterns,
        audit=_audit_observation(repo, state, sources) if value["path"] == "audit_tool_gap" else None,
    )
    return result


def derive(state: dict, observed: object) -> dict | None:
    value = record(state)
    if value is None:
        return None
    validate(value)
    expected = {"repository", "revision", "ethos_digest"}
    if value["schema_version"] == 2:
        expected |= {"sources", "audit", "starter_patterns"}
    if (
        not isinstance(observed, dict)
        or set(observed) != expected
        or observed["repository"] != state["repository"]
        or type(observed["revision"]) is not int
        or observed["revision"] != state["revision"]
    ):
        raise ValueError("Supply expansion observations from the current repository and revision")
    if value["schema_version"] == 2:
        return _derive_scope(state, observed)
    current = (
        state["ethos"]["stage"] == "complete"
        and "setup" in value
        and value["setup"] == state["setup"]
        and value["ethos_digest"] == state["ethos"]["confirmed_digest"] == observed["ethos_digest"]
        and value["handoff_digest"] == handoff_digest(state)
    )
    return {
        "stage": "entry",
        "context_current": current,
        "pending_question": value["pending"]["question"] if current else None,
        "complete": False,
    }


def scope_ready(state: dict) -> bool:
    value = record(state)
    if value is not None and value.get("schema_version") == 2 and value["path"] == "recorded_interaction":
        return (
            state["journey"] == "expand"
            and state["setup"] == value["plan"]["provider"]
            and all(
                state["prerequisites"].get(key, {}).get("status") in {"verified", "confirmed"}
                for key in ("python", "runtime")
            )
            and not any(item["phase"] in {"setup", "expand"} for item in state["blockers"])
        )
    return entry_ready(state)


def _validate_observation(value: dict, observed: dict) -> None:
    _digest(observed["ethos_digest"], nullable=True)
    paths = {source["path"] for source in value["plan"]["sources"]}
    if not isinstance(observed["sources"], dict) or set(observed["sources"]) != paths:
        raise ValueError("Observe exactly the expansion scope's retained sources")
    for checksum in observed["sources"].values():
        _digest(checksum, nullable=True)
    _strings(observed["starter_patterns"])
    if not set(observed["starter_patterns"]) <= {
        source["path"] for source in value["plan"]["sources"] if source["kind"] == "task"
    }:
        raise ValueError("Reviewed starter patterns must belong to the selected source tasks")
    audit = observed["audit"]
    if value["path"] != "audit_tool_gap":
        if audit is not None:
            raise ValueError("Only audit-driven creation consumes audit eligibility observations")
        return
    _fields(audit, "handoff_current coverage_current recommendations actionable_tools")
    if any(type(audit[key]) is not bool for key in ("handoff_current", "coverage_current")):
        raise ValueError("Observe the originating audit's selection and measured report binding")
    for key in ("recommendations", "actionable_tools"):
        _list(audit[key])
    for row in audit["recommendations"]:
        _fields(row, "id action items")
        _text(row["id"])
        _text(row["action"])
        _strings(row["items"])
    for row in audit["actionable_tools"]:
        _fields(row, "name task_slug")
        _text(row["name"])
        _text(row["task_slug"])


def _derive_scope(state: dict, observed: dict) -> dict:
    value = record(state)
    assert value is not None
    _validate_observation(value, observed)
    plan = value["plan"]
    trace = value["path"] == "recorded_interaction"
    intent_current = value["ethos_digest"] == state["ethos"]["confirmed_digest"] and (
        trace
        and value["ethos_digest"] is None
        or state["ethos"]["stage"] == "complete"
        and not state["ethos"]["remaining_questions"]
        and value["ethos_digest"] == observed["ethos_digest"]
    )
    blockers = []
    if not intent_current:
        blockers.append("Review the changed or unresolved agent intent before agreeing to construction")
    handoff_current = value["handoff_digest"] == handoff_digest(state)
    if not handoff_current:
        blockers.append("Reconcile the changed audit selection before agreeing to construction")
    sources_current = True
    for source in plan["sources"]:
        if observed["sources"][source["path"]] != source["digest"]:
            blockers.append(f"Restore or review changed source: {source['path']}")
            sources_current = False
    if not scope_ready(state):
        blockers.append("Resolve the applicable prerequisites and saved blockers before construction")
    provider_current = state["setup"] == plan["provider"]
    if not provider_current:
        blockers.append("Reconcile the proposed provider with the existing suite setup")
    selected = [task for task in plan["tasks"] if task["id"] in plan["selected_task_ids"]]
    if not selected:
        blockers.append("Select supported proposed tasks before agreeing to construction")
    blockers.extend(plan["unresolved_inputs"])
    blockers.extend(plan["limits"]["unresolved_inputs"])
    for task in selected:
        blockers.extend(task["unresolved_inputs"])
        if not task["example_ids"]:
            blockers.append(f"Select the distinct input examples for {task['id']}")
    if value["path"] == "starter_suite" and not observed["starter_patterns"]:
        blockers.append("Select a current reviewed or accepted starter task as the expansion pattern")
    if trace and not any(source["kind"] == "file" for source in plan["sources"]):
        blockers.append("Select the recorded interaction before constructing its task candidate")
    if value["path"] == "audit_tool_gap":
        audit = observed["audit"]
        assert audit is not None
        report = plan["coverage_report"]
        if not report or not any(source["path"] == report and source["kind"] == "file" for source in plan["sources"]):
            blockers.append("Select and bind the measured coverage report for tool-gap creation")
        handoff = state["scopes"]["expand"].get("audit_handoff")
        if handoff:
            if not audit["handoff_current"]:
                blockers.append("Review changed audit inputs before relying on the selected findings for construction")
                sources_current = False
            if not audit["coverage_current"]:
                blockers.append(
                    "Reconcile the selected coverage report with the originating reviewed audit measurements"
                )
        recommendations = {row["id"]: row for row in audit["recommendations"]}
        tools = {row["name"]: row["task_slug"] for row in audit["actionable_tools"]}
        for task in selected:
            tool = task["target_tool"]
            if tool not in tools or tools[tool] != task["id"]:
                blockers.append(f"{task['id']} does not match an eligible measured tool gap and its task identity")
            if handoff and audit["handoff_current"]:
                recommendation = recommendations.get(task["recommendation_id"])
                if (
                    task["recommendation_id"] not in handoff["recommendation_ids"]
                    or recommendation is None
                    or recommendation["action"] != "new_task"
                    or tool not in recommendation["items"]
                ):
                    blockers.append(f"{task['id']} does not implement a selected eligible audit recommendation")
        if handoff:
            represented = {task["recommendation_id"] for task in selected} | {item["id"] for item in plan["deferred"]}
            if not set(handoff["recommendation_ids"]) <= represented:
                blockers.append("Keep every selected audit improvement visible as planned or deferred")
    digest = scope_digest(state)
    decision = value["decision"]
    current = bool(intent_current and handoff_current and sources_current and provider_current)
    approved = bool(current and decision and decision["digest"] == digest and decision["kind"] == "approved")
    decision_kind = decision["kind"] if current and decision and decision["digest"] == digest else None
    stage = {"approved": "scope_approved", "declined": "scope_declined", "revise": "scope_revision"}.get(
        decision_kind, "scope"
    )
    return {
        "stage": stage,
        "context_current": current,
        "pending_question": value["pending"]["question"] if value["pending"] else None,
        "complete": False,
        "scope_digest": digest,
        "scope_approved": approved,
        "construction_ready": approved and not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "handoff": PATHS[value["path"]] if approved and not blockers else None,
    }


def entry_ready(state: dict) -> bool:
    from journey_state import active_blockers, checklist

    return (
        state["journey"] == "expand"
        and not state["ethos"]["remaining_questions"]
        and not active_blockers(state)
        and all(row["complete"] for row in checklist(state)[:2])
    )


def reconcile_question(repo, state: dict, *, answered_ethos_review: bool = False) -> None:
    if (
        record(state) is None
        or (state["pending_question"] is not None and not answered_ethos_review)
        or not scope_ready(state)
    ):
        return
    progress = derive(state, observe(repo, state))
    if progress is not None and progress["context_current"]:
        state["pending_question"] = progress["pending_question"]


def _transition_scope(repo, previous: dict, updated: dict) -> None:
    before, after = record(previous), record(updated)
    assert after is not None
    previous_scope = previous["scopes"].get("expand", {})
    scope = updated["scopes"]["expand"]
    retained_entries = previous_scope.get("entry_history", []) if isinstance(previous_scope, dict) else []
    if scope.get("entry_history", []) != retained_entries:
        raise ValueError("Retain the previous expansion entry records unchanged")
    if before is not None and before["schema_version"] == 1:
        scope["entry_history"] = [*copy.deepcopy(retained_entries), copy.deepcopy(before)]
    prior = before if before is not None and before["schema_version"] == 2 else None
    if after["history"] != (prior["history"] if prior else []):
        raise ValueError("Scope decision history is retained automatically; do not alter it")
    old_decision = prior["decision"] if prior else None
    if old_decision is not None and after["decision"] is None:
        raise ValueError("Retain the previous scope decision as history when revising the plan")
    progress = derive(updated, observe(repo, updated))
    assert progress is not None
    digest = progress["scope_digest"]
    old = derive(previous, observe(repo, previous)) if prior else None
    if after["presented"] != (prior["presented"] if prior else None) and after["presented"] is not None:
        if not old or old["scope_digest"] != digest or after["presented"]["digest"] != digest:
            raise ValueError("Save the current expansion scope before recording its presentation")
    decision = after["decision"]
    if decision != old_decision and decision is not None:
        if not (
            prior
            and old
            and old["scope_digest"] == digest
            and prior["presented"]
            and prior["presented"]["digest"] == digest
            and decision["digest"] == digest
        ):
            raise ValueError("Present the exact current scope before recording its user decision")
        sources = {
            prior["presented"]["source"],
            after["request"]["source"],
            after["plan"]["source"],
            (prior["pending"] or {}).get("source"),
            scope.get("audit_handoff", {}).get("source"),
            (old_decision or {}).get("source"),
        }
        if decision["source"] in sources:
            raise ValueError("Record the actual scope answer, not the request, audit selection, or presentation")
        if decision["kind"] == "approved" and (not progress["construction_ready"] or progress["blockers"]):
            raise ValueError("Resolve current scope inputs, evidence, and limits before approving construction")
        if old_decision is not None:
            after["history"].append(copy.deepcopy(old_decision))
    if before != after:
        scope.update(confirmed=progress["scope_approved"], needs_review=not progress["scope_approved"])
    if scope_ready(updated):
        if updated["pending_question"] != progress["pending_question"]:
            if not scope_ready(previous) and updated["pending_question"] is None:
                reconcile_question(repo, updated)
            else:
                raise ValueError("Save the actual unresolved scope question with its lifecycle")
    if decision is not None and decision["digest"] == digest and decision["kind"] in {"approved", "declined"}:
        if after["pending"] is not None:
            raise ValueError("An answered scope decision cannot retain its approval question")


def construction_guard(repo, state: dict, *, task_id: str, expected_revision: int) -> dict:
    from journey_state import load_state

    current = load_state(repo)
    if type(expected_revision) is not int or expected_revision != state["revision"]:
        raise ValueError("State changed; reload before constructing the agreed expansion task")
    if current != state:
        raise ValueError("State changed; reload before constructing the agreed expansion task")
    value = record(state)
    if value is None or value["schema_version"] != 2:
        raise ValueError("Save and approve the expansion scope before construction")
    progress = derive(state, observe(repo, state))
    assert progress is not None
    if not progress["construction_ready"]:
        raise ValueError("Construction requires current explicit scope approval and resolved inputs and limits")
    if task_id not in value["plan"]["selected_task_ids"]:
        raise ValueError("Construct only tasks selected in the agreed expansion scope")
    task = next(task for task in value["plan"]["tasks"] if task["id"] == task_id)
    return {
        "revision": state["revision"],
        "scope_digest": progress["scope_digest"],
        "task": copy.deepcopy(task),
        "limits": copy.deepcopy(value["plan"]["limits"]),
        "handoff": progress["handoff"],
        "execution_authorized": False,
    }


def transition(repo, previous: dict, updated: dict) -> None:
    before, after = record(previous), record(updated)
    if before is not None and after is None:
        raise ValueError("Retain structured expansion progress when updating its scope")
    if after is None:
        return
    if before is not None and before["schema_version"] == 2 and after["schema_version"] != 2:
        raise ValueError("Retain the versioned expansion scope and its decisions")
    if after["schema_version"] == 2:
        _transition_scope(repo, previous, updated)
        if updated["journey_status"].get("expand") == "complete":
            raise ValueError("Scope agreement cannot establish expansion completion")
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
