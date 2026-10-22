#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Revision applicability for first-evaluation observations, not an execution attestation.

The caller records actual checks and user decisions with their provenance. This
module retains them and derives current applicability without running providers,
inferring user approval, or modifying state or evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"[0-9a-f]{64}")


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")


def _digest(value: object) -> None:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError("First-eval digests must be lowercase SHA-256")


def _strings(value: object, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{label} must be a list")
    for item in value:
        _text(item, label)
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must not contain duplicates")


def _mapping(value: object, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - (optional or set()):
        raise ValueError("Invalid first-eval record fields")


def _relative(value: str) -> None:
    _text(value, "artifact path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError("First-eval paths must be repository-relative and cannot escape the repository")


def _reference(value: dict) -> None:
    _mapping(value, {"path", "digest"})
    _relative(value["path"])
    _digest(value["digest"])


def _evidence(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError("First-eval observations require retained evidence")
    for ref in value:
        _reference(ref)


def validate(record: dict) -> None:
    """Validate only the opt-in first-evaluation record; historical scopes stay readable."""
    _mapping(
        record,
        {
            "schema_version",
            "provider",
            "ethos_digest",
            "plan",
            "plan_approval",
            "first_task",
            "tasks",
            "selection",
            "runs",
            "run_review",
            "audit_invitation",
        },
        {"selected_run_ids", "history", "execution_settings", "execution_settings_presented"},
    )
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("Unsupported first-eval state schema")
    if record["provider"] not in {"harbor", "gym"}:
        raise ValueError("Unknown first-eval provider")
    _digest(record["ethos_digest"])
    plan = record["plan"]
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        raise ValueError("First-eval plan needs the proposed tasks and examples")
    ids = []
    examples = {}
    for task in plan["tasks"]:
        if not isinstance(task, dict) or not {"id", "example_ids"} <= task.keys():
            raise ValueError("Planned tasks need stable IDs and example IDs")
        _text(task["id"], "task id")
        _strings(task["example_ids"], "planned examples", nonempty=True)
        ids.append(task["id"])
        examples[task["id"]] = task["example_ids"]
    if len(set(ids)) != len(ids) or record["first_task"] not in ids:
        raise ValueError("Select exactly one first task from the unique planned task IDs")
    if record["plan_approval"] is not None:
        _mapping(record["plan_approval"], {"digest", "source"})
        _digest(record["plan_approval"]["digest"])
        _text(record["plan_approval"]["source"], "scope approval source")
    if "execution_settings" in record:
        settings = record["execution_settings"]
        _mapping(settings, {"agent_model", "attempts", "validation", "limits", "open_questions"})
        for field in ("agent_model", "attempts", "validation", "limits"):
            _text(settings[field], f"execution settings {field}")
        _strings(settings["open_questions"], "execution settings open questions")
    if record.get("execution_settings_presented") is not None:
        _mapping(record["execution_settings_presented"], {"digest", "source"})
        _digest(record["execution_settings_presented"]["digest"])
        _text(record["execution_settings_presented"]["source"], "execution settings presentation source")
    if not isinstance(record["tasks"], dict):
        raise ValueError("First-eval tasks must be a mapping")
    # Retired tasks remain available after a scope change, but never count toward
    # the new selected set or establish its completion.
    for task_id, task in record["tasks"].items():
        _text(task_id, "task id")
        _mapping(task, {"path", "sha256", "review", "validation", "limitations"}, {"environment"})
        _relative(task["path"])
        if Path(task["path"]).parts[:2] != (".eval-author", "tasks") or len(Path(task["path"]).parts) != 3:
            raise ValueError("New first-evaluation tasks use stable .eval-author/tasks/<slug> paths")
        _digest(task["sha256"])
        _strings(task["limitations"], "task limitations")
        if task["review"] is not None:
            review = task["review"]
            _mapping(review, {"sha256", "decision", "source"})
            _digest(review["sha256"])
            if review["decision"] not in {"approved", "changes_requested"}:
                raise ValueError("Unknown task review decision")
            _text(review["source"], "task review source")
        for field in ("validation", "environment"):
            validation = task.get(field)
            if validation is None:
                continue
            _mapping(validation, {"sha256", "status", "source", "evidence"})
            _digest(validation["sha256"])
            if validation["status"] not in {"passed", "failed", "blocked"}:
                raise ValueError("Unknown task validation status")
            _text(validation["source"], "observed validation source")
            _evidence(validation["evidence"])
    selection = record["selection"]
    if selection is not None:
        _mapping(
            selection,
            {"config", "cases", "agent", "model", "source"},
            {"inputs", "authorization", "settings_digest"},
        )
        if "settings_digest" in selection:
            _digest(selection["settings_digest"])
        _reference(selection["config"])
        if "inputs" in selection:
            _evidence(selection["inputs"])
        if selection.get("authorization") is not None:
            _mapping(selection["authorization"], {"digest", "source"})
            _digest(selection["authorization"]["digest"])
            _text(selection["authorization"]["source"], "execution authorization source")
        for field in ("agent", "model", "source"):
            _text(selection[field], field)
        if not isinstance(selection["cases"], list) or not selection["cases"]:
            raise ValueError("Run selection needs explicitly selected tasks")
        selected = []
        for case in selection["cases"]:
            _mapping(case, {"task_id", "example_ids", "attempts"}, {"rows"})
            _text(case["task_id"], "selected task id")
            _strings(case["example_ids"], "selected examples", nonempty=True)
            selected.append(case["task_id"])
            if type(case["attempts"]) is not int or case["attempts"] < 1:
                raise ValueError("Selected attempts must be positive integers")
            if record["provider"] == "gym":
                rows = case.get("rows")
                if (
                    not isinstance(rows, list)
                    or len(rows) != len(case["example_ids"])
                    or any(type(row) is not int or row < 1 for row in rows)
                    or len(set(rows)) != len(rows)
                ):
                    raise ValueError("NeMo Gym requires a unique one-based row for each selected example")
                if not selection.get("inputs"):
                    raise ValueError("NeMo Gym selection requires retained dataset, runtime, and reset inputs")
            elif "rows" in case:
                raise ValueError("Harbor selections use task IDs, not dataset rows")
        if len(set(selected)) != len(selected):
            raise ValueError("Run selection must not repeat a task")
    if not isinstance(record["runs"], list):
        raise ValueError("First-eval runs must be a list")
    run_ids = []
    for run in record["runs"]:
        _mapping(run, {"id", "role", "status", "selection_digest", "task_revisions", "source", "evidence", "attempts"})
        _text(run["id"], "native run id")
        run_ids.append(run["id"])
        if run["role"] not in {"agent", "control"} or run["status"] not in {"completed", "infrastructure_error"}:
            raise ValueError("Unknown native run role or status")
        _digest(run["selection_digest"])
        if not isinstance(run["task_revisions"], dict):
            raise ValueError("Native run needs its executed task revisions")
        for task_id, checksum in run["task_revisions"].items():
            _text(task_id, "executed task id")
            _digest(checksum)
        _text(run["source"], "observed native run source")
        _evidence(run["evidence"])
        if not isinstance(run["attempts"], list):
            raise ValueError("Native attempts must be a list")
        attempts = []
        native_ids = []
        for attempt in run["attempts"]:
            _mapping(attempt, {"id", "task_id", "example_ids", "native_id", "status", "rewards", "evidence"})
            for field in ("id", "task_id", "native_id"):
                _text(attempt[field], f"attempt {field}")
            attempts.append(attempt["id"])
            native_ids.append(attempt["native_id"])
            _strings(attempt["example_ids"], "executed examples", nonempty=True)
            if attempt["status"] not in {"completed", "infrastructure_error"}:
                raise ValueError("Unknown native attempt status")
            rewards = attempt["rewards"]
            if not isinstance(rewards, dict) or any(
                not isinstance(key, str) or not key or type(value) not in (int, float) or not math.isfinite(value)
                for key, value in rewards.items()
            ):
                raise ValueError("Recorded rewards must be finite named numbers")
            _evidence(attempt["evidence"])
        if len(set(attempts)) != len(attempts) or len(set(native_ids)) != len(native_ids):
            raise ValueError("Native attempts must have distinct identities within a run")
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("Retained run IDs must be unique")
    if "selected_run_ids" in record:
        _strings(record["selected_run_ids"], "selected native run IDs")
        if not set(record["selected_run_ids"]) <= set(run_ids):
            raise ValueError("Selected runs must identify retained native runs")
    if record["run_review"] is not None:
        review = record["run_review"]
        _mapping(review, {"run_id", "attempt_id", "digest", "source", "limitations"})
        for field in ("run_id", "attempt_id", "source"):
            _text(review[field], f"run review {field}")
        _digest(review["digest"])
        _strings(review["limitations"], "run review limitations")
    if record["audit_invitation"] is not None:
        invitation = record["audit_invitation"]
        _mapping(invitation, {"status", "source"})
        if invitation["status"] not in {"pending", "accepted", "declined"}:
            raise ValueError("Unknown audit invitation decision")
        if invitation["status"] != "pending" or invitation["source"] is not None:
            _text(invitation["source"], "audit decision source")
    if "history" in record:
        if not isinstance(record["history"], list):
            raise ValueError("First-eval history must be a list")
        for item in record["history"]:
            _mapping(item, {"kind", "key", "revision", "digest", "record"})
            if item["kind"] not in {
                "plan",
                "task",
                "selection",
                "run",
                "run_review",
                "audit_invitation",
                "selected_runs",
                "execution_settings",
                "execution_settings_presented",
            }:
                raise ValueError("Unknown first-eval history kind")
            _text(item["key"], "history key")
            if type(item["revision"]) is not int or item["revision"] < 0 or not isinstance(item["record"], dict):
                raise ValueError("Invalid first-eval history snapshot")
            _digest(item["digest"])
            if fingerprint(item["record"]) != item["digest"]:
                raise ValueError("First-eval history snapshot differs from its digest")


def _path(repo: Path, value: str) -> Path:
    _relative(value)
    path = repo / value
    for parent in (path, *path.parents):
        if parent == repo:
            break
        if parent.is_symlink():
            raise ValueError("First-eval evidence must not traverse a symlink")
    return path


def _current(repo: Path, reference: dict) -> bool:
    try:
        path = _path(repo, reference["path"])
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == reference["digest"]
    except (OSError, ValueError):
        return False


def task_digest(repo: Path, relative: str) -> str:
    """Use the evidence recorder's exact task revision rules without provider imports."""
    source = Path(__file__).resolve().parents[2] / "eval-author-task-create/scripts/task_evidence.py"
    spec = importlib.util.spec_from_file_location("_first_eval_task_evidence", source)
    if spec is None or spec.loader is None:
        raise ValueError("The shared task-evidence helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.task_digest(_path(repo, relative))


def execution_settings(record: dict) -> dict:
    """Keep execution details independent of task design without rewriting old plans."""
    if "execution_settings" in record:
        return record["execution_settings"]
    presentation = record["plan"].get("presentation", {})
    if not isinstance(presentation, dict):
        return {}
    return {
        field: presentation[field]
        for field in ("agent_model", "attempts", "validation", "limits", "open_questions")
        if field in presentation
    }


def progress(repo: Path, state: dict) -> dict | None:
    scope = state["scopes"].get("create", {})
    if not isinstance(scope, dict) or "first_eval" not in scope:
        return None
    record = scope["first_eval"]
    validate(record)
    planned = {task["id"]: task["example_ids"] for task in record["plan"]["tasks"]}
    plan_digest = fingerprint(
        {
            "plan": record["plan"],
            "first_task": record["first_task"],
            "provider": record["provider"],
            "ethos_digest": record["ethos_digest"],
        }
    )
    context_current = (
        record["provider"] == state["setup"]
        and record["ethos_digest"] == state["ethos"]["confirmed_digest"]
        and not scope.get("needs_review", False)
    )
    approval = record["plan_approval"]
    approved = bool(context_current and approval and approval["digest"] == plan_digest)
    settings_digest = fingerprint({"plan_digest": plan_digest, "settings": execution_settings(record)})
    presented = record.get("execution_settings_presented")
    settings_presented = bool(approved and presented and presented["digest"] == settings_digest)
    # Existing task observations predate this presentation checkpoint. Keep
    # them usable without claiming that missing presentation evidence exists.
    legacy_started = "execution_settings" not in record and record["first_task"] in record["tasks"]
    tasks = {}
    for task_id in planned:
        task = record["tasks"].get(task_id)
        stale = []
        current = False
        if task:
            try:
                current = task_digest(repo, task["path"]) == task["sha256"]
            except (OSError, ValueError, ImportError):
                pass
            if not current:
                stale.append("Task files changed or are unavailable")
        review = task and task["review"]
        validation = task and task["validation"]
        environment = task and task.get("environment")
        reviewed = bool(
            approved and current and review and review["decision"] == "approved" and review["sha256"] == task["sha256"]
        )
        validated = bool(
            approved
            and current
            and validation
            and validation["status"] == "passed"
            and validation["sha256"] == task["sha256"]
            and all(_current(repo, ref) for ref in validation["evidence"])
        )
        environment_proven = bool(
            approved
            and current
            and environment
            and environment["status"] == "passed"
            and environment["sha256"] == task["sha256"]
            and all(_current(repo, ref) for ref in environment["evidence"])
        )
        if validation and not validated:
            stale.append("Validation is failed, blocked, stale, or not applicable to the agreed scope")
        if review and not reviewed:
            stale.append("Task approval is pending changes, stale, or not applicable to the agreed scope")
        tasks[task_id] = {
            "created": bool(task and current),
            "reviewed": reviewed,
            "validated": validated,
            "environment_proven": environment_proven,
            "executed": False,
            "stale": stale,
        }
    first = tasks[record["first_task"]]
    first_approved = first["reviewed"] and first["environment_proven"]
    selection = record["selection"]
    selection_digest = (
        fingerprint(
            {
                "selection": {key: value for key, value in selection.items() if key != "authorization"},
                "plan_digest": plan_digest,
                "task_revisions": {key: record["tasks"].get(key, {}).get("sha256") for key in planned},
            }
        )
        if selection
        else None
    )
    selected = {case["task_id"]: case for case in selection["cases"]} if selection else {}
    selected_matches = set(selected) == set(planned) and all(
        set(case["example_ids"]) == set(planned[task_id]) for task_id, case in selected.items() if task_id in planned
    )
    selection_current = bool(
        approved
        and selection
        and (
            selection.get("settings_digest") == settings_digest
            if "execution_settings" in record or "settings_digest" in selection
            else True
        )
        and selected_matches
        and all(task["created"] for task in tasks.values())
        and _current(repo, selection["config"])
        and all(_current(repo, ref) for ref in selection.get("inputs", []))
    )
    authorization = selection.get("authorization") if selection else None
    execution_authorized = bool(selection_current and authorization and authorization["digest"] == selection_digest)
    current_runs = []
    run_digests = {}
    run_progress: dict[str, dict[str, Any]] = {}
    for run in record["runs"]:
        run_digests[run["id"]] = fingerprint(run)
        current = bool(
            selection_current
            and run["role"] == "agent"
            and run["selection_digest"] == selection_digest
            and set(run["task_revisions"]) <= set(planned)
            and all(
                tasks[key]["created"] and run["task_revisions"][key] == record["tasks"][key]["sha256"]
                for key in run["task_revisions"]
            )
            and all(_current(repo, ref) for ref in run["evidence"])
        )
        run_progress[run["id"]] = {"reviewable": current, "complete": False, "attempts": {}, "errors": []}
        if not current:
            continue
        counts = {task_id: {example: 0 for example in planned[task_id]} for task_id in planned}
        valid = True
        for attempt in run["attempts"]:
            task_id = attempt["task_id"]
            inspectable = bool(
                task_id in run["task_revisions"]
                and set(attempt["example_ids"]) <= set(planned[task_id])
                and all(_current(repo, ref) for ref in attempt["evidence"])
            )
            packaged = bool(
                inspectable
                and (
                    record["provider"] == "gym"
                    and len(attempt["example_ids"]) == 1
                    or record["provider"] == "harbor"
                    and set(attempt["example_ids"]) == set(planned[task_id])
                )
            )
            completed = bool(packaged and attempt["status"] == "completed" and attempt["rewards"])
            if not packaged or attempt["status"] == "completed" and not attempt["rewards"]:
                run_progress[run["id"]]["errors"].append(
                    f"Attempt {attempt['id']} has stale, missing, or incompatible selected evidence"
                )
            run_progress[run["id"]]["attempts"][attempt["id"]] = {"reviewable": inspectable, "completed": completed}
            if not completed:
                valid = False
                continue
            for example in attempt["example_ids"]:
                counts[task_id][example] += 1
        for task_id, examples in counts.items():
            if all(count == selected[task_id]["attempts"] for count in examples.values()):
                tasks[task_id]["executed"] = True
        if (
            valid
            and run["status"] == "completed"
            and all(task["validated"] for task in tasks.values())
            and all(
                all(count == selected[task_id]["attempts"] for count in examples.values())
                for task_id, examples in counts.items()
            )
        ):
            current_runs.append(run)
            run_progress[run["id"]]["complete"] = True
    review = record["run_review"]
    reviewed_run = bool(
        review
        and review["digest"] == run_digests.get(review["run_id"])
        and run_progress.get(review["run_id"], {})
        .get("attempts", {})
        .get(review["attempt_id"], {})
        .get("reviewable", False)
    )
    # Multiple native jobs are reconciled only when explicitly selected. Keep
    # the original single-run behavior when that selection has not been made.
    if "selected_run_ids" in record:
        selected_runs = record["selected_run_ids"]
    elif review and run_progress.get(review["run_id"], {}).get("complete"):
        selected_runs = [review["run_id"]]
    elif current_runs:
        selected_runs = [current_runs[-1]["id"]]
    else:
        selected_runs = [run["id"] for run in record["runs"] if run_progress[run["id"]]["reviewable"]][-1:]
    run_by_id = {run["id"]: run for run in record["runs"]}
    counts = {key: {example: 0 for example in examples} for key, examples in planned.items()}
    seen_native = set()
    completed_attempts = 0
    observed_attempts = 0
    errors = []
    job_errors = []
    for run_id in selected_runs:
        run = run_by_id[run_id]
        derived = run_progress[run_id]
        if not derived["reviewable"]:
            errors.append(f"Selected run {run_id} is a control, stale, or not applicable")
            continue
        errors.extend(derived["errors"])
        if run["status"] == "infrastructure_error":
            job_errors.append(run_id)
        for attempt in run["attempts"]:
            observed_attempts += 1
            if attempt["native_id"] in seen_native:
                errors.append(f"Native attempt identity is reused: {attempt['native_id']}")
                continue
            seen_native.add(attempt["native_id"])
            if not derived["attempts"].get(attempt["id"], {}).get("completed"):
                continue
            completed_attempts += 1
            for example in attempt["example_ids"]:
                counts[attempt["task_id"]][example] += 1
    suite_complete = bool(
        selection_current
        and selected_runs
        and not errors
        and all(
            all(count == selected[key]["attempts"] for count in examples.values()) for key, examples in counts.items()
        )
    )
    if "selected_run_ids" not in record or len(selected_runs) == 1:
        suite_complete = bool(suite_complete and run_progress[selected_runs[0]]["complete"])
    for task_id, examples in counts.items():
        if selection_current and all(count == selected[task_id]["attempts"] for count in examples.values()):
            tasks[task_id]["executed"] = True
    suite = {
        "selected_run_ids": selected_runs,
        "planned_attempts": sum(
            case["attempts"] * (len(case["example_ids"]) if record["provider"] == "gym" else 1)
            for case in selected.values()
        ),
        "completed_attempts": completed_attempts,
        "observed_attempts": observed_attempts,
        "planned_examples": len({example for examples in planned.values() for example in examples}),
        "executed_examples": len(
            {example for examples in counts.values() for example, count in examples.items() if count}
        ),
        "complete": suite_complete,
        "errors": errors,
        "job_errors": job_errors,
    }
    complete = bool(
        first_approved
        and all(task["validated"] for task in tasks.values())
        and reviewed_run
        and review["run_id"] in selected_runs
        and suite_complete
    )
    invitation = record["audit_invitation"]
    audit_decision = invitation["status"] if invitation else None
    if not approved:
        next_stage = "proposal"
    elif not settings_presented and not legacy_started:
        next_stage = "execution_settings"
    elif not first["created"] or not first["environment_proven"]:
        next_stage = "prepare_first"
    elif not first_approved:
        next_stage = "review_first"
    elif not all(task["validated"] for task in tasks.values()):
        next_stage = "prepare_remaining"
    elif not selection_current or not any(run["reviewable"] for run in run_progress.values()):
        next_stage = "ready_to_run"
    else:
        next_stage = "results"
    return {
        "scope_approved": approved,
        "plan_approved": approved,
        "plan_digest": plan_digest,
        "settings_digest": settings_digest,
        "execution_settings_presented": settings_presented,
        "first_task_approved": first_approved,
        "tasks": tasks,
        "selection_digest": selection_digest,
        "selection_current": selection_current,
        "run_digests": run_digests,
        "runs": run_progress,
        "suite": suite,
        "execution_authorized": execution_authorized,
        "reviewed_run_current": reviewed_run,
        "complete": complete,
        "audit_decision": audit_decision,
        "next_stage": next_stage,
    }


def transition(repo: Path, previous: dict, updated: dict) -> None:
    """Check creation order against previously saved approvals, never the same patch."""
    before_scope = previous["scopes"].get("create", {})
    after_scope = updated["scopes"].get("create", {})
    before = before_scope.get("first_eval") if isinstance(before_scope, dict) else None
    after = after_scope.get("first_eval") if isinstance(after_scope, dict) else None
    if after is None:
        return
    now = progress(repo, updated)
    if now is None:
        raise ValueError("The structured first-eval record is unavailable")
    old = progress(repo, previous) if before else None
    if before and "execution_settings" in before and "execution_settings" not in after:
        raise ValueError("Retain adopted execution settings; revise them instead of removing them")
    presented = after.get("execution_settings_presented")
    if presented is not None and presented != (before or {}).get("execution_settings_presented"):
        if not old or not old["scope_approved"] or not now["scope_approved"]:
            raise ValueError("Save scope approval before recording displayed execution settings")
    previous_tasks = before["tasks"] if before else {}
    if not set(previous_tasks) <= set(after["tasks"]):
        raise ValueError("Retain prior task observations; remove retired tasks from the plan instead")
    added = set(after["tasks"]) - set(previous_tasks)
    for task_id in set(after["tasks"]) & set(previous_tasks):
        if after["tasks"][task_id]["path"] != previous_tasks[task_id]["path"]:
            raise ValueError("Keep task paths stable across review, validation, and execution")
    if added:
        if not old or not old["scope_approved"] or not now["scope_approved"]:
            raise ValueError("Save scope approval before recording created tasks")
        if after["first_task"] in added and not (
            old["execution_settings_presented"] and now["execution_settings_presented"]
        ):
            raise ValueError("Display and save execution settings before recording the first created task")
        if added - {after["first_task"]} and not (old["first_task_approved"] and now["first_task_approved"]):
            raise ValueError(
                "Prove the environment and approve exactly the first task before creating the remaining tasks"
            )
    invitation = after["audit_invitation"]
    if invitation and invitation != (before or {}).get("audit_invitation") and not now["complete"]:
        raise ValueError("Offer or answer the audit invitation only after the first-eval run review is complete")
    prior_runs = {run["id"]: run for run in before["runs"]} if before else {}
    next_runs = {run["id"]: run for run in after["runs"]}
    if not prior_runs.keys() <= next_runs.keys():
        raise ValueError("Retain prior native runs; select the applicable runs instead of deleting evidence")
    history = (before or {}).get("history", [])
    if "history" in after and after["history"] != history:
        raise ValueError("First-eval history is retained automatically and cannot be rewritten")
    history = deepcopy(history)

    def retain(kind: str, key: str, old: dict | None, new: dict | None) -> None:
        if old is not None and old != new:
            history.append(
                {
                    "kind": kind,
                    "key": key,
                    "revision": previous["revision"],
                    "digest": fingerprint(old),
                    "record": deepcopy(old),
                }
            )

    if before:
        plan_keys = ("plan", "plan_approval", "first_task", "provider", "ethos_digest")
        old_plan = {key: before[key] for key in plan_keys}
        new_plan = {key: after[key] for key in plan_keys}
        if before["plan_approval"] or any(before[key] != after[key] for key in plan_keys if key != "plan_approval"):
            retain("plan", "plan", old_plan, new_plan)
        for key, old_task in previous_tasks.items():
            retain("task", key, old_task, after["tasks"][key])
        for key, old_run in prior_runs.items():
            retain("run", key, old_run, next_runs[key])
        for field in ("selection", "run_review", "audit_invitation"):
            retain(field, field, before[field], after[field])
        for field in ("execution_settings", "execution_settings_presented"):
            retain(field, field, before.get(field), after.get(field))
        if before.get("selected_run_ids") != after.get("selected_run_ids"):
            retain(
                "selected_runs",
                "selected_run_ids",
                {"ids": before.get("selected_run_ids")},
                {"ids": after.get("selected_run_ids")},
            )
    if history:
        after["history"] = history
