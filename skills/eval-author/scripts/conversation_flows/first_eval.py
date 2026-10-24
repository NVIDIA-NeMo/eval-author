# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Present creation decisions from supplied state and caller-observed artifacts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import PurePosixPath

import conversation_templates as shared
import first_eval_state
import journey_state
from conversation_formatting import link, literal, mapping, text

from . import first_eval_templates as copy

JOURNEYS = ("create",)
PREVIEWS: set[str] = set()
STAGES = {
    "first_eval_" + stage
    for stage in (
        "traces",
        "saved_design",
        "proposal",
        "execution_settings",
        "prepare_first",
        "build_progress",
        "review_first",
        "prepare_remaining",
        "remaining_progress",
        "ready_to_run",
        "results",
        "resume",
        "blockers",
    )
}
CONTENT_FIELDS = {
    "input",
    "expected_result",
    "grading",
    "grading_limitation",
    "observed",
    "grade_explanation",
    "quality_failures",
    "identity_limitations",
    "reconciliation",
    "missing_execution_question",
}
LINK_FIELDS = {*copy.LINK_LABELS, "run_evidence", "attempt_evidence"}


def target_link(value: object, label: str, *, fragment: bool = False) -> str:
    value = mapping(value, "verified link", {"target", "fragment"})
    anchor = value.get("fragment")
    if fragment and not anchor:
        raise ValueError("Navigation requires a caller-verified fragment")
    return link(value.get("target"), label, fragment=anchor)


def named_link(facts: dict, key: str, *, fragment: bool = False, label: str | None = None) -> str:
    links = mapping(facts.get("links", {}), "links", LINK_FIELDS)
    return target_link(links.get(key), label or copy.LINK_LABELS[key], fragment=fragment)


def reports(facts: dict) -> str:
    return shared.REPORTS.format(report=named_link(facts, "report"), readme=named_link(facts, "readme"))


def bound_links(state: dict, facts: dict) -> None:
    links = mapping(facts.get("links", {}), "links", LINK_FIELDS)
    paths = {
        "report": ".eval-author/report.html",
        "readme": ".eval-author/README.md",
        "rerun": ".eval-author/README.md",
        "tasks_view": ".eval-author/report.html",
    }
    record = first_eval_state.record(state)
    if record and record["first_task"] in record["tasks"]:
        paths["task_readme"] = record["tasks"][record["first_task"]]["path"] + "/README.md"
    if record and record["run_review"]:
        reviewed = record["run_review"]
        run = next((item for item in record["runs"] if item["id"] == reviewed["run_id"]), None)
        attempt = (
            next((item for item in run["attempts"] if item["id"] == reviewed["attempt_id"]), None) if run else None
        )
        if attempt and attempt["task_id"] in record["tasks"]:
            paths["task_rerun"] = record["tasks"][attempt["task_id"]]["path"] + "/README.md"
    for key, path in paths.items():
        if key in links:
            target = mapping(links[key], "verified link", {"target", "fragment"})
            if target.get("target") != str(PurePosixPath(state["repository"]) / path):
                raise ValueError("Artifact navigation must target the actual saved repository artifact")
    if "tasks_view" in links and links["tasks_view"].get("fragment") != "cases":
        raise ValueError("Tasks view requires the actual report navigation fragment")
    for key in ("run_evidence", "attempt_evidence"):
        if key not in links:
            continue
        if not isinstance(links[key], dict):
            raise ValueError("Evidence links must map exact retained paths to destinations")
        for path, target in links[key].items():
            target = mapping(target, "verified evidence link", {"target", "fragment"})
            if target.get("target") != str(PurePosixPath(state["repository"]) / path):
                raise ValueError("Evidence navigation must target its exact retained repository file")


def applicable_blockers(state: dict) -> dict:
    return {item["id"]: item for item in state["blockers"] if item["phase"] in {"setup", "ethos", state["journey"]}}


def blocker_section(state: dict, facts: dict, stage: str) -> str | None:
    entries = facts.get("blockers", [])
    if not isinstance(entries, list):
        raise ValueError("Blockers must be a list")
    saved = applicable_blockers(state)
    explained = {}
    for entry in entries:
        entry = mapping(entry, "blocker", {"id", "step", "reason", "needed"})
        key = text(entry.get("id"), "blocker id")
        if key in explained or key not in saved:
            raise ValueError("Each blocker must explain a unique saved blocker")
        explained[key] = entry
    if stage in {"execution_settings", "ready_to_run", "results", "blockers"} and set(explained) != set(saved):
        raise ValueError("Saved blockers must be explained with a specific needed action")
    if not explained:
        if stage == "blockers":
            raise ValueError("Save at least one blocker before rendering Blockers")
        return None
    lines = [shared.HANDOFF_BLOCKERS, ""]
    lines.extend(
        copy.BLOCKER.format(**{key: literal(entry.get(key), key) for key in ("step", "reason", "needed")})
        for _, entry in sorted(explained.items())
    )
    return "\n".join(lines)


def proposal(record: dict) -> str:
    plan = record["plan"]
    tasks = plan["tasks"]
    breadth_request = plan.get("breadth_request")
    if breadth_request is not None:
        breadth_request = mapping(breadth_request, "broader proposal request", {"source"})
        text(breadth_request.get("source"), "explicit broader proposal request source")
    elif not 1 <= len(tasks) <= 3 or any(len(task["example_ids"]) != 1 for task in tasks):
        raise ValueError(
            "Propose 1–3 tasks with one distinct input example per task unless the user explicitly requested "
            "a broader scope; retain that request in plan.breadth_request.source"
        )
    presentation = plan.get("presentation", {})
    if not isinstance(presentation, dict):
        raise ValueError("Proposal presentation must be an object")
    rows = [copy.PROPOSAL_TABLE]
    for task in tasks:
        rows.append(
            "| "
            + " | ".join(
                literal(task.get(key), key, table=True)
                for key in ("title", "input", "behavior", "expected_result", "grading")
            )
            + " |"
        )
    first = next(task for task in tasks if task["id"] == record["first_task"])
    examples = len({example for task in tasks for example in task["example_ids"]})
    return "\n\n".join(
        [
            copy.PROPOSAL_INTRO,
            "\n".join(rows),
            literal(presentation.get("coverage_note"), "coverage_note"),
            copy.FIRST_CHOICE.format(title=literal(first.get("title"), "first task title")),
            copy.PROPOSAL_PROCESS.format(tasks=len(tasks), examples=examples),
            copy.PROPOSAL_QUESTION,
        ]
    )


def saved_design(state: dict, record: dict, observed: dict) -> str:
    historical = deepcopy(state)
    prior_record = first_eval_state.record(historical)
    assert prior_record is not None
    prior_record.pop("saved_design_review", None)
    prior = first_eval_state.derive(historical, observed)
    assert prior is not None
    tasks = record["plan"]["tasks"]
    rows = [copy.SAVED_DESIGN_TABLE]
    rows.extend(
        "| "
        + literal(task.get("title") or task["id"], "saved task title", table=True)
        + " | "
        + literal(task.get("behavior") or copy.SAVED_BEHAVIOR_UNKNOWN, "saved task behavior", table=True)
        + " |"
        for task in tasks
    )
    choice = first_eval_state.trace_choice(state)
    source = (
        copy.SAVED_SOURCE_ETHOS
        if choice == "none"
        else copy.SAVED_SOURCE_TRACES
        if choice
        else copy.SAVED_SOURCE_UNKNOWN
    )
    approval = record["plan_approval"]
    approval_status = (
        copy.SAVED_APPROVAL_CURRENT
        if approval and approval["digest"] == prior["plan_digest"]
        else copy.SAVED_APPROVAL_STALE
        if approval
        else copy.SAVED_APPROVAL_UNKNOWN
    )
    return "\n\n".join(
        [
            copy.SAVED_DESIGN_INTRO,
            copy.SAVED_DESIGN_COUNTS.format(
                tasks=len(tasks), examples=len({example for task in tasks for example in task["example_ids"]})
            ),
            "\n".join(rows),
            copy.SAVED_DESIGN_SOURCE.format(source=source),
            copy.SAVED_DESIGN_APPROVAL.format(approval=approval_status),
            copy.SAVED_DESIGN_PROGRESS.format(
                tasks=len(tasks),
                **{
                    key: sum(task[key] for task in prior["tasks"].values())
                    for key in ("created", "validated", "executed")
                },
            ),
            "\n".join("- " + choice for choice in copy.SAVED_DESIGN_CHOICES),
            copy.SAVED_DESIGN_QUESTION,
        ]
    )


def execution_settings(record: dict) -> str:
    settings = first_eval_state.execution_settings(record)
    questions = settings.get("open_questions")
    if not isinstance(questions, list):
        raise ValueError("Execution settings need open_questions, including an empty list when resolved")
    tasks = record["plan"]["tasks"]
    examples = len({example for task in tasks for example in task["example_ids"]})
    values = (
        literal(settings.get("agent_model"), "agent_model"),
        copy.TASK_COUNTS.format(tasks=len(tasks), examples=examples),
        literal(settings.get("attempts"), "attempts"),
        literal(settings.get("validation"), "validation"),
        literal(settings.get("limits"), "limits"),
        "; ".join(literal(item, "open question") for item in questions) or copy.NO_OPEN_QUESTIONS,
    )
    bullets = "\n".join(f"- **{label}:** {value}" for label, value in zip(copy.SETTINGS_LABELS, values, strict=True))
    return "\n\n".join([copy.SETTINGS_INTRO, bullets, copy.SETTINGS_NOTICE])


def build_rows(labels: dict, steps: dict, facts: dict, *, counts: dict | None = None) -> str:
    blocked = None
    if facts.get("blockers"):
        blocked = next((step for step in labels if steps[step] == "in_progress"), None)
        blocked = blocked or next((step for step in labels if steps[step] != "complete"), None)
    rows = []
    for key, label in labels.items():
        status = "blocked" if key == blocked else steps[key]
        if counts and key in counts:
            label = copy.BUILD_TASK_COUNTS.format(label=label, **counts[key])
        rows.append(
            copy.BUILD_ROW.format(
                checked="x" if status == "complete" else " ",
                label=label,
                skill=copy.BUILD_SKILLS[key],
                status=copy.BUILD_STATUSES[status],
            )
        )
    return "\n".join(rows)


def build_checklist(record: dict, progress: dict, facts: dict) -> str:
    first = next(task for task in record["plan"]["tasks"] if task["id"] == record["first_task"])
    labels = dict(copy.BUILD_STEPS)
    if record["provider"] == "gym":
        labels["grading"] = copy.GYM_BUILD_GRADING
    return (
        copy.BUILD_HEADING.format(title=literal(first.get("title") or first["id"], "first task title"))
        + "\n\n"
        + build_rows(labels, progress["first_task_build"]["steps"], facts)
    )


def remaining_checklist(progress: dict, facts: dict) -> str:
    build = progress["remaining_build"]
    parts = [copy.REMAINING_HEADING]
    if not build["task_ids"]:
        parts.append(copy.NO_REMAINING_TASKS)
    parts.append(build_rows(copy.REMAINING_STEPS, build["steps"], facts, counts=build["counts"]))
    return "\n\n".join(parts)


def first_review(record: dict, progress: dict, facts: dict) -> str:
    status = progress["tasks"][record["first_task"]]
    if not progress["scope_approved"] or not status["created"] or not status["environment_proven"]:
        raise ValueError("First-task review requires saved scope approval, current task files, and environment proof")
    if (
        record.get("first_task_build")
        and not progress["first_task_approved"]
        and not (progress["first_task_build"]["current"] and progress["first_task_build"]["complete"])
    ):
        raise ValueError("Complete the current first-task build checklist before requesting first-task review")
    values = {
        key: literal(facts.get(key), key) for key in ("input", "expected_result", "grading", "grading_limitation")
    }
    values.update(
        validation=copy.VALIDATION_PASSED if status["validated"] else copy.VALIDATION_PENDING,
        execution=copy.EXECUTION_COMPLETED if status["executed"] else copy.EXECUTION_PENDING,
    )
    first = next(task for task in record["plan"]["tasks"] if task["id"] == record["first_task"])
    return "\n\n".join(
        [
            copy.FIRST_REVIEW_INTRO.format(title=literal(first.get("title") or first["id"], "first task title")),
            copy.FIRST_REVIEW_ACTION.format(
                report=named_link(facts, "tasks_view", fragment=True, label=copy.FIRST_REVIEW_REPORT_LABEL),
                readme=named_link(facts, "readme", label=copy.FIRST_REVIEW_README_LABEL),
            ),
            copy.FIRST_REVIEW.format(**values),
            named_link(facts, "task_readme"),
            copy.FIRST_QUESTION,
        ]
    )


def evidence_links(facts: dict, key: str, references: list[dict], identity: str) -> str:
    links = mapping(facts.get("links", {}), "links", LINK_FIELDS)
    targets = links.get(key)
    if not isinstance(targets, dict) or set(targets) != {ref["path"] for ref in references}:
        raise ValueError("Evidence navigation must identify the exact retained run or attempt files")
    return " · ".join(
        target_link(targets[ref["path"]], identity + ": " + PurePosixPath(ref["path"]).name) for ref in references
    )


def incomplete_inventory(progress: dict) -> list[str]:
    pending = [
        key
        for key, status in progress["tasks"].items()
        if not all(status[field] for field in ("created", "validated", "executed"))
    ]
    return [
        copy.INCOMPLETE.format(tasks=literal(", ".join(pending) or copy.PENDING_RUN, "unfinished tasks")),
        "\n".join(
            copy.TASK_STATUS.format(
                task=literal(task_id, "task id"),
                **{key: copy.YES if status[key] else copy.NO for key in ("created", "validated", "executed")},
            )
            for task_id, status in progress["tasks"].items()
        ),
    ]


def suite_counts(progress: dict) -> str:
    suite = progress["suite"]
    return copy.SUITE_COUNTS.format(
        completed=suite["completed_attempts"],
        planned=suite["planned_attempts"],
        runs=len(suite["selected_run_ids"]),
        observed=suite["observed_attempts"],
        run_ids=literal(", ".join(suite["selected_run_ids"]) or copy.NO_RUNS, "run ids"),
    )


def attempt_counts(record: dict, progress: dict, run: dict) -> dict:
    attempts = progress["runs"][run["id"]]["attempts"]
    planned = sum(
        case["attempts"] * (len(case["example_ids"]) if record["provider"] == "gym" else 1)
        for case in (record["selection"] or {}).get("cases", [])
        if case["task_id"] in run["task_revisions"]
    )
    return {
        "run": literal(run["id"], "run id"),
        "planned": planned,
        "completed": sum(item["completed"] for item in attempts.values()),
        "infrastructure": sum(
            item["status"] == "infrastructure_error" and attempts[item["id"]]["reviewable"] for item in run["attempts"]
        ),
        "ungraded": sum(
            item["status"] == "completed" and not item["rewards"] and attempts[item["id"]]["reviewable"]
            for item in run["attempts"]
        ),
        "missing": max(0, planned - len(run["attempts"])),
        "unavailable": sum(not item["reviewable"] for item in attempts.values()),
    }


def unavailable_results(record: dict, progress: dict, facts: dict) -> str:
    parts = [*incomplete_inventory(progress), suite_counts(progress), copy.REVIEW_UNAVAILABLE]
    if record["run_review"]:
        parts.append(
            copy.HISTORICAL_REVIEW.format(
                run=literal(record["run_review"]["run_id"], "saved run id"),
                attempt=literal(record["run_review"]["attempt_id"], "saved attempt id"),
            )
        )
    for run in record["runs"]:
        if run["id"] in progress["suite"]["selected_run_ids"]:
            parts.append(
                copy.RUN_INVENTORY.format(
                    role=literal(run["role"], "run role"), **attempt_counts(record, progress, run)
                )
            )
    if facts.get("links", {}).get("rerun"):
        parts.append(copy.RERUN.format(instructions=named_link(facts, "rerun", fragment=True)))
    parts.append(literal(facts.get("reconciliation"), "agreed versus delivered reconciliation"))
    if not facts.get("blockers"):
        parts.extend([shared.HANDOFF_BLOCKERS, copy.MISSING_REVIEW_BLOCKER])
    return "\n\n".join(parts)


def results(record: dict, progress: dict, facts: dict, journey: str) -> str:
    review = record["run_review"]
    if (
        not review
        or not progress["reviewed_run_current"]
        or review["run_id"] not in progress["suite"]["selected_run_ids"]
    ):
        return unavailable_results(record, progress, facts)
    run = next(item for item in record["runs"] if item["id"] == review["run_id"])
    attempt = next(item for item in run["attempts"] if item["id"] == review["attempt_id"])
    selected = record["selection"]
    parts = [] if progress["complete"] else incomplete_inventory(progress)
    suite = progress["suite"]
    parts.append(suite_counts(progress))
    if suite.get("job_errors"):
        parts.append(
            copy.JOB_ERRORS.format(
                runs=literal(", ".join(suite["job_errors"]), "failed jobs"),
                recovery=copy.RECOVERY_COMPLETE if suite["complete"] else copy.RECOVERY_PENDING,
            )
        )
    parts.append(
        copy.RUN_COUNTS.format(
            quality=literal(facts.get("quality_failures"), "quality_failures"), **attempt_counts(record, progress, run)
        )
    )
    parts.append(
        copy.ATTEMPT_REVIEW.format(
            run=literal(run["id"], "run id"),
            attempt=literal(attempt["id"], "attempt id"),
            native=literal(attempt["native_id"], "native identity"),
            rewards=literal(json.dumps(attempt["rewards"], sort_keys=True, ensure_ascii=False), "grades"),
            agent=literal(selected["agent"], "agent"),
            model=literal(selected["model"], "model"),
            **{
                key: literal(facts.get(key), key)
                for key in ("input", "observed", "grade_explanation", "identity_limitations")
            },
        )
    )
    parts.extend(
        [
            copy.RUN_EVIDENCE.format(links=evidence_links(facts, "run_evidence", run["evidence"], run["id"])),
            copy.ATTEMPT_EVIDENCE.format(
                links=evidence_links(facts, "attempt_evidence", attempt["evidence"], attempt["id"])
            ),
        ]
    )
    if not review["limitations"]:
        raise ValueError("Run review must state material evidence limitations, including unknown model health")
    parts.append(
        copy.LIMITATIONS.format(limitations="; ".join(literal(item, "limitation") for item in review["limitations"]))
    )
    parts.append(copy.RERUN.format(instructions=named_link(facts, "rerun", fragment=True)))
    if facts.get("links", {}).get("task_rerun"):
        parts.append(named_link(facts, "task_rerun", fragment=True))
    parts.append(literal(facts.get("reconciliation"), "agreed versus delivered reconciliation"))
    if progress["complete"]:
        if progress["audit_decision"] is None:
            raise ValueError("Save the pending audit invitation before presenting the completed evaluation handoff")
        parts.append("\n".join(f"- [x] {label}" for label in (*shared.CHECKLIST_LABELS, shared.JOURNEYS[journey])))
        if progress["audit_decision"] == "pending":
            parts.append(copy.AUDIT_QUESTION)
    elif not facts.get("blockers"):
        parts.extend([shared.HANDOFF_BLOCKERS, copy.INCOMPLETE_EXECUTION_BLOCKER])
    return "\n\n".join(parts)


def render(payload: dict) -> str:
    stage = payload["stage"]
    if stage not in STAGES or "journey" in payload or "ready" in payload:
        raise ValueError("Creation stages require saved state without journey or readiness overrides")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("Supply reconciled journey state")
    journey_state.validate(state)
    if type(payload.get("revision")) is not int or payload["revision"] != state["revision"]:
        raise ValueError("State revision differs; reload state and rebuild the presentation input")
    if state["journey"] not in JOURNEYS:
        raise ValueError("Creation conversations require the saved create flow")
    if stage != "first_eval_blockers" and state["ethos"]["stage"] != "complete":
        raise ValueError("Confirm ETHOS.md before creating evaluation tasks")
    if stage != "first_eval_blockers" and not all(row["complete"] for row in journey_state.checklist(state)[:2]):
        raise ValueError("Resolve shared prerequisites and confirm ETHOS.md before creating evaluation tasks")
    facts = mapping(
        payload.get("facts", {}),
        "creation facts",
        CONTENT_FIELDS | {"observations", "links", "blockers", "access_ready"},
    )
    bound_links(state, facts)
    if "access_ready" in facts and type(facts["access_ready"]) is not bool:
        raise ValueError("access_ready must be an observed boolean")
    stage = stage.removeprefix("first_eval_")
    record = first_eval_state.record(state)
    progress = None
    if record and (record.get("saved_design_review") or stage == "saved_design") and stage != "blockers":
        observed = facts.get("observations")
        if not isinstance(observed, dict):
            raise ValueError("Supply freshly observed task and evidence digests")
        progress = first_eval_state.derive(state, observed)
        assert progress is not None
        if progress["saved_design_review_needed"]:
            if stage == "resume":
                stage = "saved_design"
            elif stage != "saved_design":
                raise ValueError("Confirm how to proceed with the saved design before continuing")
    if stage == "saved_design":
        if record is None:
            raise ValueError("Save the discovered task design before asking whether to resume it")
        observed = facts.get("observations")
        if not isinstance(observed, dict):
            raise ValueError("Supply freshly observed task and evidence digests")
        return saved_design(state, record, observed) + "\n"
    choice = first_eval_state.trace_choice(state)
    if stage == "resume" and not choice:
        stage = "traces"
    if stage == "traces":
        result = copy.TRACE_INTRO
        if not choice:
            result += "\n\n" + copy.TRACE_QUESTION + "\n\n" + "\n".join(f"- {label}" for label in copy.TRACE_CHOICES)
    elif stage == "blockers":
        return (blocker_section(state, facts, stage) or "") + "\n"
    else:
        if stage == "proposal" and not choice:
            raise ValueError("Save the user’s choice of existing traces or ETHOS.md before presenting a proposal")
        if stage == "resume" and first_eval_state.record(state) is None:
            raise ValueError("Save a small, concrete first-evaluation proposal before resuming the selected source")
        observed = facts.get("observations")
        if not isinstance(observed, dict):
            raise ValueError("Supply freshly observed task and evidence digests")
        progress = progress or first_eval_state.derive(state, observed)
        if progress is None:
            raise ValueError("Save a first-evaluation proposal record before rendering")
        record = first_eval_state.record(state)
        assert record is not None
        if stage == "resume":
            stage = progress["next_stage"]
        if stage == "proposal":
            if progress["scope_approved"]:
                raise ValueError("Scope is already approved; resume the saved next stage")
            if not record["plan"].get("trace_choice"):
                raise ValueError("Copy the saved source choice into the proposal before presenting its design")
            return proposal(record) + "\n"
        if stage == "execution_settings":
            if not progress["scope_approved"]:
                raise ValueError("Save scope approval before displaying execution settings")
            if not progress["first_task_approved"]:
                raise ValueError("Review and approve the first task before displaying execution settings")
            result = execution_settings(record)
        elif stage == "prepare_first":
            if not progress["scope_approved"]:
                raise ValueError("Save scope approval before preparing the first task")
            result = copy.FIRST_PROGRESS if record["provider"] == "harbor" else copy.GYM_PROGRESS
            result += "\n\n" + build_checklist(record, progress, facts)
        elif stage == "build_progress":
            if not progress["scope_approved"]:
                raise ValueError("Save scope approval before reporting first-task build progress")
            if not record.get("first_task_build") or not progress["first_task_build"]["current"]:
                raise ValueError("Save build progress for the current first task and approved design")
            result = build_checklist(record, progress, facts)
        elif stage == "review_first":
            result = first_review(record, progress, facts)
        elif stage == "prepare_remaining":
            if not progress["first_task_approved"]:
                raise ValueError("Save first-task approval before preparing remaining tasks")
            result = copy.REMAINDER_PROGRESS if progress["remaining_build"]["task_ids"] else copy.NO_REMAINDER_PROGRESS
            result += "\n\n" + remaining_checklist(progress, facts)
        elif stage == "remaining_progress":
            if not progress["first_task_approved"]:
                raise ValueError("Save first-task approval before reporting remaining-task build progress")
            if not record.get("remaining_build") or not progress["remaining_build"]["current"]:
                raise ValueError("Save build progress for the current remaining tasks and approved first task")
            result = remaining_checklist(progress, facts)
        elif stage == "ready_to_run":
            if progress["next_stage"] == "execution_settings":
                raise ValueError("Display and save execution settings before announcing execution")
            if not progress["first_task_approved"] or not all(
                item["validated"] and item["environment_proven"] for item in progress["tasks"].values()
            ):
                raise ValueError(
                    "All selected tasks need current environment proof and grading validation before announcing reference checks"
                )
            result = copy.CHECKS_PASSED
            if (
                facts.get("access_ready")
                and not applicable_blockers(state)
                and not facts.get("blockers")
                and progress["execution_authorized"]
            ):
                result += "\n\n" + copy.RUN_PROGRESS
            elif (
                facts.get("access_ready")
                and not applicable_blockers(state)
                and not facts.get("blockers")
                and progress["selection_current"]
                and progress["execution_settings_resolved"]
            ):
                result += "\n\n" + copy.RUN_QUESTION
            else:
                questions = first_eval_state.execution_settings(record).get("open_questions", [])
                result += "\n\n" + literal(
                    facts.get("missing_execution_question") or (questions[0] if questions else None),
                    "one focused missing execution question",
                )
        else:
            blocked = bool(applicable_blockers(state))
            result = results(
                record, {**progress, "complete": progress["complete"] and not blocked}, facts, state["journey"]
            )
    if blocked := blocker_section(state, facts, stage):
        result += "\n\n" + blocked
    if stage == "results":
        result += "\n\n" + reports(facts)
    return result + "\n"
