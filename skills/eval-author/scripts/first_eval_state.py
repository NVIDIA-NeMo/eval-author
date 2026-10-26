# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bind creation decisions to saved design and observed bytes, without provider calls."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from itertools import chain
from pathlib import PurePosixPath

import first_eval_history

BUILD_STEPS = ("environment", "environment_check", "grading", "artifacts")
REMAINING_STEPS = ("tasks", "environment_check", "validation", "artifacts")


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _text(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Creation decisions require nonempty text and provenance")


def _digest(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Creation evidence requires a lowercase SHA-256 digest")


def _path(value: str) -> None:
    _text(value)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value == ".":
        raise ValueError("Creation artifact paths must be canonical and repository-relative")


def _strings(values: list) -> None:
    if not isinstance(values, list) or not values or len(set(values)) != len(values):
        raise ValueError("Creation task/example/run identities must be nonempty and unique")
    for value in values:
        _text(value)


def _decision(value: dict | None, digest_key: str = "digest") -> None:
    if value is not None:
        _digest(value[digest_key])
        _text(value["source"])


def _evidence(refs: list) -> None:
    if not isinstance(refs, list) or not refs:
        raise ValueError("Creation observations require retained evidence references")
    for ref in refs:
        _path(ref["path"])
        _digest(ref["digest"])


def _settings_fields(settings: object) -> bool:
    return bool(
        isinstance(settings, dict)
        and all(
            isinstance(settings.get(key), str) and settings[key].strip()
            for key in ("agent_model", "attempts", "validation", "limits")
        )
        and isinstance(settings.get("open_questions"), list)
        and all(isinstance(item, str) and item.strip() for item in settings["open_questions"])
    )


def record(state: dict) -> dict | None:
    scope = state["scopes"].get("create", {})
    return scope.get("first_eval") if isinstance(scope, dict) else None


def trace_choice(state: dict) -> str | dict | None:
    scope = state["scopes"].get("create", {})
    if not isinstance(scope, dict):
        return None
    plan = (record(state) or {}).get("plan", {})
    return plan.get("trace_choice") or scope.get("trace_choice")


def validate(value: dict) -> None:
    """Validate the optional domain record; unstructured legacy scopes stay readable."""
    try:
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version", 1)) is not int
            or value.get("schema_version", 1) != 1
            or value["provider"] not in {"harbor", "gym"}
        ):
            raise ValueError("Invalid creation record or provider")
        _digest(value["ethos_digest"])
        planned = value["plan"]["tasks"]
        if not isinstance(planned, list) or not isinstance(value["tasks"], dict) or not isinstance(value["runs"], list):
            raise ValueError("Creation tasks, task observations, and native runs require explicit collections")
        _strings([task["id"] for task in planned])
        for task in planned:
            _strings(task["example_ids"])
        if value["first_task"] not in {task["id"] for task in planned}:
            raise ValueError("Choose the first task from the agreed design")
        choice = value["plan"].get("trace_choice")
        if choice is not None and choice != "none" and (not isinstance(choice, dict) or not choice):
            raise ValueError("Trace selection must be none or an explicit bounded source selection")
        breadth = value["plan"].get("breadth_request")
        if breadth is not None:
            if not isinstance(breadth, dict):
                raise ValueError("A broader proposal requires the user's request provenance")
            _text(breadth.get("source"))
        _decision(value["plan_approval"])
        saved_review = value.get("saved_design_review")
        if saved_review is not None:
            _digest(saved_review["digest"])
            _text(saved_review["request_source"])
            if saved_review["decision"] not in {"pending", "continue", "revise", "new"}:
                raise ValueError("Unknown saved-design review decision")
            if saved_review["decision"] == "pending":
                if saved_review.get("source") is not None:
                    raise ValueError("Pending saved-design review cannot imply a user answer")
            else:
                _text(saved_review["source"])
        build = value.get("first_task_build")
        if build is not None:
            if not isinstance(build, dict) or set(build) != {
                "plan_digest",
                "task_id",
                "active_step",
                "source",
                "receipts",
            }:
                raise ValueError("First-task build progress requires its design, activity, and retained receipts")
            _digest(build["plan_digest"])
            _text(build["task_id"])
            _text(build["source"])
            if build["active_step"] is not None and build["active_step"] not in BUILD_STEPS:
                raise ValueError("Unknown first-task build step")
            if not isinstance(build["receipts"], dict) or set(build["receipts"]) - set(BUILD_STEPS):
                raise ValueError("First-task build receipts require fixed step identities")
            for receipt in build["receipts"].values():
                if not isinstance(receipt, dict) or set(receipt) != {"source", "evidence"}:
                    raise ValueError("First-task build receipts require provenance and retained evidence")
                _text(receipt["source"])
                _evidence(receipt["evidence"])
        remaining = value.get("remaining_build")
        if remaining is not None:
            if not isinstance(remaining, dict) or set(remaining) != {
                "plan_digest",
                "first_task_sha256",
                "active_step",
                "source",
                "artifacts",
            }:
                raise ValueError("Remaining-task progress requires its approved design, activity, and review artifacts")
            _digest(remaining["plan_digest"])
            _digest(remaining["first_task_sha256"])
            _text(remaining["source"])
            if remaining["active_step"] is not None and remaining["active_step"] not in REMAINING_STEPS:
                raise ValueError("Unknown remaining-task build step")
            artifacts = remaining["artifacts"]
            if artifacts is not None:
                if not isinstance(artifacts, dict) or set(artifacts) != {"digest", "source", "evidence"}:
                    raise ValueError("Remaining-task artifact refresh requires provenance and retained evidence")
                _decision(artifacts)
                _evidence(artifacts["evidence"])
                if len(artifacts["evidence"]) != 2 or {ref["path"] for ref in artifacts["evidence"]} != {
                    ".eval-author/report.html",
                    ".eval-author/README.md",
                }:
                    raise ValueError("Retain both the actual suite report and its Markdown mirror")
        for key, task in value["tasks"].items():
            _text(key)
            _path(task["path"])
            if (
                PurePosixPath(task["path"]).parts[:2] != (".eval-author", "tasks")
                or len(PurePosixPath(task["path"]).parts) != 3
            ):
                raise ValueError("Keep created tasks in .eval-author/tasks/<slug>")
            _digest(task["sha256"])
            _decision(task.get("review"), "sha256")
            if task.get("review") and task["review"]["decision"] not in {"approved", "changes_requested"}:
                raise ValueError("Unknown task review decision")
            for field in ("environment", "validation"):
                check = task.get(field)
                if check:
                    _decision(check, "sha256")
                    _evidence(check["evidence"])
                    if check["status"] not in {"passed", "failed", "blocked"}:
                        raise ValueError("Unknown task check status")
        selection = value["selection"]
        if selection is not None:
            _evidence([selection["config"], *selection.get("inputs", [])])
            for key in ("agent", "model", "source"):
                _text(selection[key])
            _decision(selection.get("authorization"))
            if "settings_digest" in selection:
                _digest(selection["settings_digest"])
            if selection.get("bridge"):
                _text(selection["bridge"]["source"])
                _evidence(selection["bridge"]["evidence"])
            _strings([case["task_id"] for case in selection["cases"]])
            for case in selection["cases"]:
                _strings(case["example_ids"])
                if type(case["attempts"]) is not int or case["attempts"] < 1:
                    raise ValueError("Attempts must be positive integers")
                if value["provider"] == "gym":
                    rows = case.get("rows", [])
                    if (
                        not selection.get("inputs")
                        or len(rows) != len(case["example_ids"])
                        or len(set(rows)) != len(rows)
                    ):
                        raise ValueError("Gym requires selected complete input rows and their retained inputs")
                    if any(type(row) is not int or row < 1 for row in rows):
                        raise ValueError("Gym rows must be unique one-based row numbers")
        runs = value["runs"]
        if runs:
            _strings([run["id"] for run in runs])
        for run in runs:
            _digest(run["selection_digest"])
            _text(run["source"])
            _evidence(run["evidence"])
            if run["role"] not in {"agent", "control"} or run["status"] not in {
                "running",
                "completed",
                "infrastructure_error",
            }:
                raise ValueError("Unknown native run role/status")
            for checksum in run["task_revisions"].values():
                _digest(checksum)
            if run["attempts"]:
                _strings([attempt["id"] for attempt in run["attempts"]])
                _strings([attempt["native_id"] for attempt in run["attempts"]])
            for attempt in run["attempts"]:
                _text(attempt["task_id"])
                _strings(attempt["example_ids"])
                _evidence(attempt["evidence"])
                if attempt["status"] not in {"completed", "infrastructure_error"}:
                    raise ValueError("Unknown native attempt status")
                if not isinstance(attempt["rewards"], dict) or any(
                    type(reward) not in (int, float) or not math.isfinite(reward)
                    for reward in attempt["rewards"].values()
                ):
                    raise ValueError("Native rewards must be finite numbers")
        activity = value.get("run_progress")
        if activity is not None:
            if not isinstance(activity, dict) or set(activity) != {
                "selection_digest",
                "run_id",
                "status",
                "active_task_ids",
                "source",
                "evidence",
            }:
                raise ValueError("Run progress requires a selected native job observation and retained evidence")
            _digest(activity["selection_digest"])
            _text(activity["run_id"])
            _text(activity["source"])
            _evidence(activity["evidence"])
            if activity["status"] not in {"running", "waiting"}:
                raise ValueError("Unknown observed native job status")
            if not isinstance(activity["active_task_ids"], list):
                raise ValueError("Active native tasks must be an explicit list")
            if activity["active_task_ids"]:
                _strings(activity["active_task_ids"])
            if activity["status"] == "waiting" and activity["active_task_ids"]:
                raise ValueError("Waiting for native progress cannot identify active tasks")
        _decision(value["run_review"])
        if value["run_review"]:
            _text(value["run_review"]["run_id"])
            _text(value["run_review"]["attempt_id"])
            if value["run_review"].get("summary"):
                summary = value["run_review"]["summary"]
                _evidence([summary])
                _text(summary["run_id"])
                _text(summary["attempt_id"])
        invitation = value["audit_invitation"]
        if invitation:
            if invitation["status"] not in {"pending", "accepted", "declined"}:
                raise ValueError("Unknown audit invitation decision")
            if invitation["status"] != "pending":
                _text(invitation["source"])
        if "selected_run_ids" in value:
            if not isinstance(value["selected_run_ids"], list):
                raise ValueError("Explicit native run selection must be a list")
            if value["selected_run_ids"]:
                _strings(value["selected_run_ids"])
            if not set(value["selected_run_ids"]) <= {run["id"] for run in runs}:
                raise ValueError("Select retained native run identities")
        _decision(value.get("execution_settings_presented"))
        _decision(value.get("suite_review_presented"))
        _decision(value.get("suite_approval"))
        if "execution_settings" in value and not _settings_fields(value["execution_settings"]):
            raise ValueError(
                "Execution settings require agent_model, attempts, validation, limits, and a list of open_questions"
            )
        if not isinstance(value.get("history", []), list):
            raise ValueError("Creation history must retain its ordered snapshots")
        for item in value.get("history", []):
            if (
                type(item["revision"]) is not int
                or item["revision"] < 0
                or fingerprint(item["record"]) != item["digest"]
            ):
                raise ValueError("Creation history cannot be rewritten")
        if value.get("history_head") is not None:
            first_eval_history.validate_reference(value["history_head"])
            if value.get("history"):
                raise ValueError("Creation history cannot mix inline snapshots and an archived chain")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed structured creation record") from exc


def _sources(value: dict) -> tuple[dict, set]:
    tasks, files = {}, set()
    for receipt in (value.get("first_task_build") or {}).get("receipts", {}).values():
        files.update(ref["path"] for ref in receipt["evidence"])
    artifacts = (value.get("remaining_build") or {}).get("artifacts")
    if artifacts:
        files.update(ref["path"] for ref in artifacts["evidence"])
    for task_id, task in value["tasks"].items():
        tasks[task_id] = task["path"]
        for field in ("environment", "validation"):
            files.update(ref["path"] for ref in (task.get(field) or {}).get("evidence", []))
    selection = value["selection"]
    if selection:
        files.update(ref["path"] for ref in [selection["config"], *selection.get("inputs", [])])
        files.update(ref["path"] for ref in selection.get("bridge", {}).get("evidence", []))
    for run in value["runs"]:
        files.update(ref["path"] for ref in run["evidence"])
        for attempt in run["attempts"]:
            files.update(ref["path"] for ref in attempt["evidence"])
    files.update(ref["path"] for ref in (value.get("run_progress") or {}).get("evidence", []))
    summary = (value["run_review"] or {}).get("summary")
    if summary:
        files.add(summary["path"])
    return tasks, files


def observe(repo, state: dict) -> dict:
    """Read only declared task trees/evidence using the existing bounded lifecycle helpers."""
    from journey_state import MAX_TASK_BYTES, _file_digest, _task_local_path, task_digest

    value = record(state)
    observed = {"tasks": {}, "files": {}}
    if value is None:
        return observed
    validate(value)
    tasks, files = _sources(value)
    for key, path in tasks.items():
        try:
            observed["tasks"][key] = task_digest(repo, path)
        except (OSError, ValueError):
            observed["tasks"][key] = None
    for path in sorted(files):
        try:
            observed["files"][path] = _file_digest(_task_local_path(repo, path), MAX_TASK_BYTES)[0]
        except (OSError, ValueError):
            observed["files"][path] = None
    return observed


def execution_settings(value: dict) -> dict:
    return value.get("execution_settings", value["plan"].get("presentation", {}))


def _build_progress(value: dict, observed: dict, plan_digest: str) -> dict:
    build = value.get("first_task_build")
    current = bool(build and build["plan_digest"] == plan_digest and build["task_id"] == value["first_task"])
    steps = dict.fromkeys(BUILD_STEPS, "pending")
    if current:
        prior_complete = True
        for step in BUILD_STEPS:
            receipt = build["receipts"].get(step)
            completed = bool(
                prior_complete
                and receipt
                and all(observed["files"].get(ref["path"]) == ref["digest"] for ref in receipt["evidence"])
            )
            if completed:
                steps[step] = "complete"
            elif prior_complete and build["active_step"] == step:
                steps[step] = "in_progress"
            prior_complete = completed
    return {"current": current, "steps": steps, "complete": all(status == "complete" for status in steps.values())}


def _remaining_progress(
    value: dict, observed: dict, plan_digest: str, tasks: dict, first_approved: bool
) -> tuple[dict, str]:
    build = value.get("remaining_build")
    remaining = [key for key in tasks if key != value["first_task"]]
    first_digest = value["tasks"].get(value["first_task"], {}).get("sha256")
    digest = fingerprint(
        {
            "plan_digest": plan_digest,
            "first_task_sha256": first_digest,
            "tasks": {
                key: {
                    "record": {
                        field: value["tasks"].get(key, {}).get(field)
                        for field in ("path", "sha256", "environment", "validation")
                    },
                    "current": {field: task[field] for field in ("created", "environment_proven", "validated")},
                }
                for key, task in tasks.items()
            },
        }
    )
    current = bool(
        build and first_approved and build["plan_digest"] == plan_digest and build["first_task_sha256"] == first_digest
    )
    counts = {
        "tasks": {"completed": sum(tasks[key]["created"] for key in remaining), "total": len(remaining)},
        "environment_check": {
            "completed": sum(tasks[key]["environment_proven"] for key in remaining),
            "total": len(remaining),
        },
        "validation": {
            "completed": sum(task["validated"] and task["environment_proven"] for task in tasks.values()),
            "total": len(tasks),
        },
    }
    artifacts = (build or {}).get("artifacts")
    artifacts_current = bool(
        current
        and artifacts
        and artifacts["digest"] == digest
        and all(observed["files"].get(ref["path"]) == ref["digest"] for ref in artifacts["evidence"])
    )
    steps = {key: "complete" if count["completed"] == count["total"] else "pending" for key, count in counts.items()}
    steps["artifacts"] = "complete" if artifacts_current else "pending"
    if current and build["active_step"] is not None and steps[build["active_step"]] != "complete":
        steps[build["active_step"]] = "in_progress"
    return {
        "current": current,
        "task_ids": remaining,
        "steps": steps,
        "counts": counts,
        "complete": current and all(status == "complete" for status in steps.values()),
        "artifacts_current": artifacts_current,
    }, digest


def derive(state: dict, observed: object) -> dict | None:
    """Pure applicability: byte observations never supply approval or native result claims."""
    value = record(state)
    if value is None:
        return None
    validate(value)
    declared_tasks, declared_files = _sources(value)
    if not isinstance(observed, dict) or set(observed) != {"tasks", "files"}:
        raise ValueError("Supply task/file byte observations, never derived approval overrides")
    for field, allowed in (("tasks", declared_tasks), ("files", declared_files)):
        entries = observed[field]
        if not isinstance(entries, dict) or set(entries) - set(allowed):
            raise ValueError("Observe declared creation evidence only")
        for checksum in entries.values():
            if checksum is not None:
                _digest(checksum)

    def current(ref: dict) -> bool:
        return observed["files"].get(ref["path"]) == ref["digest"]

    planned = {task["id"]: task["example_ids"] for task in value["plan"]["tasks"]}
    plan_digest = fingerprint({key: value[key] for key in ("plan", "first_task", "provider", "ethos_digest")})
    build = _build_progress(value, observed, plan_digest)
    saved_review = value.get("saved_design_review")
    saved_review_needed = bool(saved_review and saved_review["decision"] == "pending")
    approved = bool(
        state["ethos"]["stage"] == "complete"
        and state["ethos"]["confirmed_digest"] == value["ethos_digest"]
        and state["setup"] == value["provider"]
        and not state["scopes"]["create"].get("needs_review")
        and value["plan"].get("trace_choice")
        and value["plan_approval"]
        and value["plan_approval"]["digest"] == plan_digest
        and not saved_review_needed
    )
    tasks = {}
    for key in planned:
        task = value["tasks"].get(key, {})
        created = bool(task and observed["tasks"].get(key) == task["sha256"])
        facts = {
            "created": created,
            "reviewed": False,
            "validated": False,
            "environment_proven": False,
            "executed": False,
        }
        review = task.get("review")
        facts["reviewed"] = bool(
            approved and created and review and review["sha256"] == task["sha256"] and review["decision"] == "approved"
        )
        for field, target in (("environment", "environment_proven"), ("validation", "validated")):
            check = task.get(field)
            facts[target] = bool(
                approved
                and created
                and check
                and check["sha256"] == task["sha256"]
                and check["status"] == "passed"
                and all(current(ref) for ref in check["evidence"])
            )
        facts["stale"] = (
            ["Task files or evidence changed, are missing, or need review"]
            if task and not all(facts[field] for field in ("created", "validated", "environment_proven"))
            else []
        )
        tasks[key] = facts
    first_approved = bool(tasks[value["first_task"]]["reviewed"] and tasks[value["first_task"]]["environment_proven"])
    remaining, remaining_digest = _remaining_progress(value, observed, plan_digest, tasks, first_approved)
    suite_digest = fingerprint(
        {
            "plan_digest": plan_digest,
            "tasks": {key: value["tasks"].get(key, {}).get("sha256") for key in planned},
        }
    )
    suite_files_current = bool(
        first_approved and all(task["created"] and task["environment_proven"] for task in tasks.values())
    )
    suite_review_ready = suite_files_current and remaining["artifacts_current"]
    suite_presented = bool(
        suite_files_current
        and value.get("suite_review_presented")
        and value["suite_review_presented"]["digest"] == suite_digest
    )
    suite_approved = bool(
        suite_presented and value.get("suite_approval") and value["suite_approval"]["digest"] == suite_digest
    )
    settings_digest = fingerprint({"plan_digest": plan_digest, "settings": execution_settings(value)})
    settings = execution_settings(value)
    settings_resolved = bool(_settings_fields(settings) and not settings["open_questions"])
    settings_shown = bool(
        suite_approved
        and value.get("execution_settings_presented")
        and value["execution_settings_presented"]["digest"] == settings_digest
    )
    selection = value["selection"]
    selection_digest = (
        fingerprint(
            {
                "selection": {key: item for key, item in selection.items() if key != "authorization"},
                "plan_digest": plan_digest,
                "tasks": {key: value["tasks"].get(key, {}).get("sha256") for key in planned},
            }
        )
        if selection
        else None
    )
    selected = {case["task_id"]: case for case in selection["cases"]} if selection else {}
    bridge = selection.get("bridge") if selection else None
    bridge_current = bool(bridge and all(current(ref) for ref in bridge["evidence"]))
    selection_current = bool(
        approved
        and selection
        and set(selected) == set(planned)
        and all(set(selected[key]["example_ids"]) == set(examples) for key, examples in planned.items())
        and all(task["created"] for task in tasks.values())
        and all(current(ref) for ref in [selection["config"], *selection.get("inputs", [])])
        and ("execution_settings" not in value or selection.get("settings_digest") == settings_digest)
        and (value["plan"].get("execution_path") != "gym_harbor" or bridge_current)
    )
    authorized = bool(
        suite_approved
        and selection_current
        and settings_shown
        and settings_resolved
        and selection.get("authorization")
        and selection["authorization"]["digest"] == selection_digest
    )
    runs, run_digests = {}, {}
    for run in value["runs"]:
        run_digests[run["id"]] = fingerprint(run)
        applicable = bool(
            selection_current
            and run["role"] == "agent"
            and run["selection_digest"] == selection_digest
            and set(run["task_revisions"]) <= set(planned)
            and all(
                tasks[key]["created"] and checksum == value["tasks"][key]["sha256"]
                for key, checksum in run["task_revisions"].items()
            )
            and all(current(ref) for ref in run["evidence"])
        )
        attempts, native_counts, run_errors = {}, Counter(), []
        for attempt in run["attempts"]:
            key = attempt["task_id"]
            inspectable = bool(
                applicable
                and key in run["task_revisions"]
                and key in planned
                and set(attempt["example_ids"]) <= set(planned[key])
                and all(current(ref) for ref in attempt["evidence"])
            )
            packaged = inspectable and (
                len(attempt["example_ids"]) == 1
                if value["provider"] == "gym"
                else set(attempt["example_ids"]) == set(planned.get(key, []))
            )
            attempts[attempt["id"]] = {
                "reviewable": inspectable,
                "completed": bool(packaged and attempt["status"] == "completed" and attempt["rewards"]),
            }
            if attempts[attempt["id"]]["completed"]:
                native_counts.update((key, example) for example in attempt["example_ids"])
            elif attempt["status"] == "completed":
                run_errors.append(f"Attempt {attempt['id']} has missing, stale, ungraded, or incompatible evidence")
        native_complete = bool(
            applicable
            and run["status"] == "completed"
            and not run_errors
            and all(item["completed"] for item in attempts.values())
            and all(task["validated"] and task["environment_proven"] for task in tasks.values())
            and all(
                native_counts[key, example] == selected[key]["attempts"]
                for key, examples in planned.items()
                for example in examples
            )
        )
        runs[run["id"]] = {
            "reviewable": applicable,
            "attempts": attempts,
            "complete": native_complete,
            "errors": run_errors,
        }
    review = value["run_review"]
    summary = (review or {}).get("summary")
    reviewed = bool(
        review
        and summary
        and summary["run_id"] == review["run_id"]
        and summary["attempt_id"] == review["attempt_id"]
        and current(summary)
        and run_digests.get(review["run_id"]) == review["digest"]
        and runs.get(review["run_id"], {}).get("attempts", {}).get(review["attempt_id"], {}).get("reviewable")
    )
    selected_runs = value.get("selected_run_ids", [])
    counts, identities, errors, job_errors = Counter(), set(), [], []
    completed_attempts = observed_attempts = 0
    for run in value["runs"]:
        if run["id"] not in selected_runs:
            continue
        if not runs[run["id"]]["reviewable"]:
            errors.append(f"Run {run['id']} is a control, stale, or missing evidence")
        errors.extend(runs[run["id"]]["errors"])
        if run["status"] == "infrastructure_error":
            job_errors.append(run["id"])
        for attempt in run["attempts"]:
            observed_attempts += 1
            if attempt["native_id"] in identities:
                errors.append(f"Reused native attempt: {attempt['native_id']}")
            elif runs[run["id"]]["attempts"][attempt["id"]]["completed"]:
                completed_attempts += 1
                counts.update((attempt["task_id"], example) for example in attempt["example_ids"])
            identities.add(attempt["native_id"])
    for key, examples in planned.items():
        tasks[key]["executed"] = bool(
            selection_current and all(counts[key, example] == selected[key]["attempts"] for example in examples)
        )
    suite_complete = bool(
        selected_runs
        and selection_current
        and not errors
        and not any(run["id"] in selected_runs and run["status"] == "running" for run in value["runs"])
        and all(task["executed"] for task in tasks.values())
        and (len(selected_runs) > 1 or not job_errors)
        and (len(selected_runs) != 1 or runs[selected_runs[0]]["complete"])
    )
    suite = {
        "selected_run_ids": selected_runs,
        "planned_attempts": sum(
            case["attempts"] * (len(case["example_ids"]) if value["provider"] == "gym" else 1)
            for case in selected.values()
        ),
        "completed_attempts": completed_attempts,
        "observed_attempts": observed_attempts,
        "planned_examples": len({example for examples in planned.values() for example in examples}),
        "executed_examples": sum(count > 0 for count in counts.values()),
        "complete": suite_complete,
        "errors": errors,
        "job_errors": job_errors,
    }
    validated = all(task["validated"] and task["environment_proven"] for task in tasks.values())
    activity = value.get("run_progress")
    run_progress = {"current": False}
    if (
        activity
        and authorized
        and validated
        and activity["selection_digest"] == selection_digest
        and set(activity["active_task_ids"]) <= set(selected)
        and all(current(ref) for ref in activity["evidence"])
        and not any(
            run["id"] == activity["run_id"]
            and (
                run["role"] != "agent"
                or run["status"] != "running"
                or run["id"] not in selected_runs
                or not runs[run["id"]]["reviewable"]
            )
            for run in value["runs"]
        )
    ):
        run_progress = {
            "current": True,
            **{key: activity[key] for key in ("run_id", "status", "active_task_ids")},
        }
    complete = bool(suite_approved and validated and suite_complete and reviewed and review["run_id"] in selected_runs)
    artifact_receipt = (value.get("remaining_build") or {}).get("artifacts")
    remaining_work = bool(
        remaining["current"]
        and not remaining["complete"]
        and (
            any(remaining["steps"][step] != "complete" for step in REMAINING_STEPS[:-1])
            or not artifact_receipt
            or artifact_receipt["digest"] != remaining_digest
            or not (settings_shown or selected_runs)
        )
    )
    first = tasks[value["first_task"]]
    stage = (
        "saved_design"
        if saved_review_needed
        else "traces"
        if not trace_choice(state)
        else "proposal"
        if not approved
        else "prepare_first"
        if value.get("first_task_build") and not first_approved and not build["current"]
        else "build_progress"
        if value.get("first_task_build") and not first_approved and not build["complete"]
        else "prepare_first"
        if not first["created"] or not first["environment_proven"]
        else "review_first"
        if not first_approved
        else "prepare_remaining"
        if value.get("remaining_build") and not remaining["current"]
        else "review_suite"
        if suite_review_ready and not suite_approved
        else "remaining_progress"
        if value.get("remaining_build") and not suite_approved
        else "prepare_remaining"
        if not suite_approved
        else "execution_settings"
        if not settings_shown
        else "remaining_progress"
        if remaining_work
        else "prepare_remaining"
        if not validated
        else "run_progress"
        if run_progress["current"] and not suite_complete
        else "results"
        if selected_runs
        else "ready_to_run"
    )
    return {
        "scope_approved": approved,
        "plan_approved": approved,
        "plan_digest": plan_digest,
        "saved_design_review_needed": saved_review_needed,
        "first_task_build": build,
        "remaining_build": remaining,
        "remaining_build_digest": remaining_digest,
        "suite_digest": suite_digest,
        "suite_review_ready": suite_review_ready,
        "suite_review_presented": suite_presented,
        "suite_approved": suite_approved,
        "settings_digest": settings_digest,
        "execution_settings_presented": settings_shown,
        "execution_settings_resolved": settings_resolved,
        "first_task_approved": first_approved,
        "tasks": tasks,
        "selection_digest": selection_digest,
        "selection_current": selection_current,
        "execution_authorized": authorized,
        "bridge_current": bridge_current,
        "run_digests": run_digests,
        "runs": runs,
        "run_progress": run_progress,
        "suite": suite,
        "reviewed_run_current": reviewed,
        "complete": complete,
        "audit_decision": (value["audit_invitation"] or {}).get("status"),
        "next_stage": stage,
    }


def progress(repo, state: dict) -> dict | None:
    return derive(state, observe(repo, state))


def transition(repo, previous: dict, updated: dict) -> None:
    """Enforce saved checkpoints and retain superseded records before the shared atomic save."""
    before, after = record(previous), record(updated)
    if before is not None and after is None:
        raise ValueError("Retain structured creation progress and its evidence")
    if after is None:
        return
    first_eval_history.inherit(before, after)
    saved_review = after.get("saved_design_review")
    prior_review = (before or {}).get("saved_design_review")
    replacing_design = False
    if prior_review is not None and saved_review is None:
        raise ValueError("Retain the saved-design review and its user decision")
    if saved_review and saved_review != prior_review:
        design_digest = fingerprint({key: after[key] for key in ("plan", "first_task", "provider", "ethos_digest")})
        if saved_review["decision"] == "pending":
            if saved_review["digest"] != design_digest:
                raise ValueError("Present the current saved design before asking to resume it")
            if (
                prior_review
                and prior_review["decision"] != "pending"
                and prior_review["request_source"] == saved_review["request_source"]
                and prior_review["digest"] == saved_review["digest"]
            ):
                raise ValueError("The saved-design choice for this request is already answered")
        else:
            if not (
                prior_review
                and prior_review["decision"] == "pending"
                and prior_review["request_source"] == saved_review["request_source"]
                and prior_review["digest"] == saved_review["digest"]
            ):
                raise ValueError("Ask about the saved design before recording its user decision")
            if saved_review["source"] in {
                saved_review["request_source"],
                updated["ethos"].get("confirmation_source"),
            }:
                raise ValueError("Record the actual saved-design answer, not the request or ETHOS.md confirmation")
            if saved_review["decision"] == "continue" and saved_review["digest"] != design_digest:
                raise ValueError("The saved design changed; review its current revision before continuing")
            if saved_review["decision"] in {"revise", "new"}:
                replacing_design = True
                after["plan_approval"] = None
                after["audit_invitation"] = None
                if updated["next_handoff"] == "eval-author-audit":
                    updated["next_handoff"] = None
            if saved_review["decision"] == "new":
                after["plan"].pop("trace_choice", None)
                updated["scopes"]["create"].pop("trace_choice", None)
    old = progress(repo, previous) if before else None
    now = progress(repo, updated)
    assert now is not None
    build = after.get("first_task_build")
    prior_build = (before or {}).get("first_task_build")
    if prior_build is not None and build is None:
        raise ValueError("Retain first-task build progress and its evidence")
    if build != prior_build:
        assert build is not None
        if not (old and old["scope_approved"] and now["scope_approved"]):
            raise ValueError("Save task-design approval before recording first-task build progress")
        if not now["first_task_build"]["current"]:
            raise ValueError("Bind first-task build progress to the current design and first task")
        if (
            prior_build
            and any(build[key] != prior_build[key] for key in ("plan_digest", "task_id"))
            and build["receipts"]
        ):
            raise ValueError("Reopen first-task build receipts when binding a changed design or first task")
        for index, step in enumerate(BUILD_STEPS):
            receipt = build["receipts"].get(step)
            prior_receipt = (prior_build or {}).get("receipts", {}).get(step)
            if receipt != prior_receipt and prior_receipt:
                if any(later in build["receipts"] for later in BUILD_STEPS[index + 1 :]):
                    raise ValueError("Reopen later first-task build steps when replacing earlier evidence")
            if receipt != prior_receipt and receipt:
                if any(
                    old["first_task_build"]["steps"][prior] != "complete"
                    or now["first_task_build"]["steps"][prior] != "complete"
                    for prior in BUILD_STEPS[:index]
                ):
                    raise ValueError("Save completed earlier first-task build steps before recording later receipts")
                if now["first_task_build"]["steps"][step] != "complete":
                    raise ValueError("Save current artifact evidence for a completed first-task build step")
        active = build["active_step"]
        if active is not None and now["first_task_build"]["steps"][active] != "in_progress":
            raise ValueError("Only the earliest incomplete first-task build step may be active")
    remaining = after.get("remaining_build")
    prior_remaining = (before or {}).get("remaining_build")
    if prior_remaining is not None and remaining is None:
        raise ValueError("Retain remaining-task build progress and its evidence")
    if remaining != prior_remaining:
        assert remaining is not None
        if not (old and old["first_task_approved"] and now["first_task_approved"]):
            raise ValueError("Save first-task approval before recording remaining-task build progress")
        if not now["remaining_build"]["current"]:
            raise ValueError("Bind remaining-task progress to the current design and approved first task")
        artifacts = remaining["artifacts"]
        if artifacts and artifacts != (prior_remaining or {}).get("artifacts"):
            if not now["remaining_build"]["artifacts_current"] or any(
                now["remaining_build"]["steps"][step] != "complete" for step in REMAINING_STEPS[:2]
            ):
                raise ValueError("Refresh current suite artifacts after authoring and environment proof")
        active = remaining["active_step"]
        if active is not None and now["remaining_build"]["steps"][active] != "in_progress":
            raise ValueError("Only an incomplete remaining-task build step may be active")
    if before and "execution_settings" in before and "execution_settings" not in after:
        raise ValueError("Retain adopted execution settings; revise them instead of removing them")
    if before != after and updated["ethos"]["stage"] != "complete":
        raise ValueError("Confirm ETHOS.md before changing creation decisions or evidence")
    if (
        now["saved_design_review_needed"]
        and after["plan_approval"]
        and after["plan_approval"] != (before or {}).get("plan_approval")
    ):
        raise ValueError("Confirm the saved-design choice before recording task-design approval")
    if now["saved_design_review_needed"] and any(
        after[key] != (before or {}).get(key, {} if key == "tasks" else []) for key in ("tasks", "runs")
    ):
        raise ValueError("Confirm the saved-design choice before changing tasks or adding run evidence")
    prior_tasks = (before or {}).get("tasks", {})
    if not prior_tasks.keys() <= after["tasks"].keys() or not {run["id"] for run in (before or {}).get("runs", [])} <= {
        run["id"] for run in after["runs"]
    }:
        raise ValueError("Retain prior tasks and native runs; retire tasks in the plan instead")
    for key in prior_tasks:
        if prior_tasks[key]["path"] != after["tasks"][key]["path"]:
            raise ValueError("Keep task directories stable through review and handoff")
    first = after["first_task"]
    first_review = after["tasks"].get(first, {}).get("review")
    if (
        first_review
        and first_review.get("decision") == "approved"
        and first_review != prior_tasks.get(first, {}).get("review")
    ):
        if not (
            old
            and old["tasks"].get(first, {}).get("environment_proven")
            and now["tasks"][first]["reviewed"]
            and now["tasks"][first]["environment_proven"]
        ):
            raise ValueError("Save the first task and its proven environment before recording its review approval")
    presentation = after.get("suite_review_presented")
    if presentation and presentation != (before or {}).get("suite_review_presented"):
        if not (old and old["suite_review_ready"] and now["suite_review_ready"] and now["suite_review_presented"]):
            raise ValueError(
                "Save current suite files, environment proof, and review artifacts before recording review"
            )
    suite_approval = after.get("suite_approval")
    if suite_approval and suite_approval != (before or {}).get("suite_approval"):
        if not (
            old
            and old["suite_review_ready"]
            and old["suite_review_presented"]
            and now["suite_review_ready"]
            and now["suite_approved"]
        ):
            raise ValueError("Present the current full suite before recording its approval")
        if suite_approval["source"] in {
            (after["plan_approval"] or {}).get("source"),
            (first_review or {}).get("source"),
            (presentation or {}).get("source"),
        }:
            raise ValueError(
                "Record the actual suite approval, not task-design, first-task, or presentation provenance"
            )
    shown = after.get("execution_settings_presented")
    if shown and shown != (before or {}).get("execution_settings_presented"):
        if not (old and old["suite_approved"] and now["suite_approved"] and now["execution_settings_presented"]):
            raise ValueError("Approve the full suite before recording current execution settings display")
    authorization = (after["selection"] or {}).get("authorization")
    if authorization and authorization != ((before or {}).get("selection") or {}).get("authorization"):
        if not (
            old
            and old["suite_approved"]
            and now["suite_approved"]
            and old["execution_settings_presented"]
            and now["execution_settings_presented"]
            and now["execution_authorized"]
        ):
            raise ValueError(
                "Display current settings after first-task approval before recording execution authorization"
            )
    activity = after.get("run_progress")
    if activity and activity != (before or {}).get("run_progress"):
        fresh = {ref["path"]: ref["digest"] for ref in activity["evidence"]}
        for item in chain((before,), first_eval_history.snapshots(repo, before)):
            for ref in ((item or {}).get("run_progress") or {}).get("evidence", []):
                if ref["path"] in fresh and ref["digest"] != fresh[ref["path"]]:
                    raise ValueError("Preserve native progress snapshots with fresh evidence paths")
        if not (
            old
            and old["execution_authorized"]
            and all(task["validated"] and task["environment_proven"] for task in old["tasks"].values())
            and now["run_progress"]["current"]
        ):
            raise ValueError(
                "Save current execution authorization and task checks before recording native run progress"
            )
    prior_runs = {run["id"]: run for run in (before or {}).get("runs", [])}
    for run in after["runs"]:
        if run["status"] == "running" and run != prior_runs.get(run["id"]):
            prior_run = prior_runs.get(run["id"])
            if prior_run and prior_run["status"] != "running":
                raise ValueError("A terminal native job cannot resume under the same run identity")
            if not (
                old
                and old["execution_authorized"]
                and now["execution_authorized"]
                and all(task["validated"] and task["environment_proven"] for task in old["tasks"].values())
                and all(task["validated"] and task["environment_proven"] for task in now["tasks"].values())
                and run["role"] == "agent"
                and run["id"] in now["suite"]["selected_run_ids"]
                and now["runs"][run["id"]]["reviewable"]
            ):
                raise ValueError(
                    "Save authorization, task checks, and explicit selection before recording a running native job"
                )
    added = after["tasks"].keys() - prior_tasks.keys()
    if added and not (old and old["scope_approved"] and now["scope_approved"]):
        raise ValueError("Save task-design approval before creating tasks")
    if added - {after["first_task"]} and not (old and old["first_task_approved"] and now["first_task_approved"]):
        raise ValueError("Approve the first task before creating the remainder")
    if (
        after["audit_invitation"] != (before or {}).get("audit_invitation")
        and not now["complete"]
        and not replacing_design
    ):
        raise ValueError("Complete creation before offering or answering its audit invitation")
    if after["audit_invitation"] != (before or {}).get("audit_invitation"):
        if after["audit_invitation"] and after["audit_invitation"]["status"] == "accepted":
            updated["next_handoff"] = "eval-author-audit"
        elif updated["next_handoff"] == "eval-author-audit":
            updated["next_handoff"] = None
    first_eval_history.retain(repo, before, after, previous["revision"])
