# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render audit checkpoints from saved decisions and caller-observed evidence."""

from __future__ import annotations

from pathlib import PurePosixPath

import audit_state
import conversation_templates as shared
import journey_state
from conversation_formatting import link, literal, mapping, text

from . import audit_templates as copy
from . import onboarding

JOURNEYS = ("audit",)
PREVIEWS: set[str] = set()
PHASES = {
    "suite_selection": "suites",
    "trace_selection": "traces",
    "checks_review": "checks",
    "measurement": "measurement",
    "findings_review": "findings",
    "next_action": "next_action",
    "complete": "next_action",
}
STAGES = {"audit_" + stage for stage in {*PHASES.values(), "blockers", "resume"}}
COMMON_FACTS = {"audit_observations", "links", "blockers"}
STAGE_FACTS = {
    "suites": {"suites"},
    "traces": set(),
    "checks": {"checks", "validation", "report_action", "limits", "changes"},
    "measurement": {"progress", "report_action"},
    "findings": {"conclusion", "findings", "limits", "recommendations", "report_action"},
    "next_action": {"recommendations"},
    "blockers": {"report_action"},
}


def rows(value: object, label: str, *, maximum: int = 100) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a bounded list")
    return value


def recommendations(facts: dict) -> list[dict]:
    result = rows(facts.get("recommendations", []), "recommendations", maximum=3)
    labels = set()
    for item in result:
        mapping(item, "recommendation", {"kind", "label", "reason"})
        if item.get("kind") not in audit_state.ACTIONS - {"finish"}:
            raise ValueError("Recommendations require a supported next action")
        label = text(item.get("label"), "recommendation label")
        text(item.get("reason"), "recommendation reason")
        if label in labels:
            raise ValueError("Recommendation labels must be distinct")
        labels.add(label)
    return result


def decision_question(stage: str, lifecycle: dict, facts: dict) -> str:
    """Build the canonical question to save before presenting a decision."""
    stage = stage.removeprefix("audit_")
    if stage == "suites":
        return copy.SUITES_QUESTION if lifecycle["suites"]["candidates"] else copy.SUITE_LOCATION_QUESTION
    if stage == "traces":
        return copy.TRACE_QUESTION
    if stage == "checks":
        return copy.CHECKS_QUESTION
    if stage == "findings":
        items = recommendations(facts)
        if not items:
            return copy.NO_CHANGE_QUESTION
        if any(item["kind"] != "improve" for item in items):
            return copy.OTHER_ACTION_QUESTION
        labels = [
            copy.IMPROVEMENT_CHOICE.format(label=literal(item["label"], "recommendation label")) for item in items
        ]
        choices = (
            labels[0]
            if len(labels) == 1
            else (copy.TWO_IMPROVEMENTS if len(labels) == 2 else copy.MULTIPLE_IMPROVEMENTS).format(
                choices=", ".join(labels)
            )
        )
        return copy.FINDINGS_QUESTION.format(choices=choices)
    raise ValueError("This audit stage has no canonical decision question")


def local_target(state: dict, path: str) -> str:
    return str(PurePosixPath(state["repository"]) / path)


def source_link(state: dict, path: str, label: str | None = None) -> str:
    return link(local_target(state, path), label or path)


def report_link(state: dict, lifecycle: dict, facts: dict, key: str = "report") -> str:
    report = lifecycle["findings"]
    if not report or facts["audit_observations"]["files"].get(report["report"]["path"]) != report["report"]["digest"]:
        raise ValueError("Record the current Audit coverage report and its observed digest before linking it")
    item = mapping(facts.get("links", {}).get(key), f"links.{key}", {"target", "fragment"})
    if item.get("target") != local_target(state, report["report"]["path"]):
        raise ValueError("Audit report links must match the saved report")
    if key != "report" and not item.get("fragment"):
        raise ValueError("Supply a verified report section fragment")
    return link(
        item["target"],
        copy.REPORT_LABEL if key == "report" else copy.SECTION_LABELS[key],
        fragment=item.get("fragment"),
    )


def report_notice(state: dict, lifecycle: dict, facts: dict) -> str:
    action = facts.get("report_action")
    if not isinstance(action, str) or action not in copy.REPORT_ACTIONS:
        raise ValueError("Supply the actual report action: created, updated, or unchanged")
    return copy.REPORT_ACTIONS[action].format(report=report_link(state, lifecycle, facts))


def scope(state: dict, lifecycle: dict) -> str:
    lines = [copy.SCOPE_HEADING]
    exclusions = []
    for key, label in copy.SCOPE_LABELS.items():
        selected = lifecycle[key]
        content = ", ".join(source_link(state, path) for path in selected["paths"])
        if selected["status"] == "pending":
            content = copy.SCOPE_PENDING
        elif not content:
            content = copy.SCOPE_NONE[key]
        lines.append(f"- **{label}:** {content}")
        for item in selected["exclusions"]:
            exclusions.append(
                copy.EXCLUSION.format(
                    path=source_link(state, item["path"]), reason=literal(item["reason"], "exclusion reason")
                )
            )
    lines.extend(["", copy.EXCLUSIONS, *exclusions] if exclusions else ["", copy.NO_EXCLUSIONS])
    return "\n".join(lines)


def progress(state: dict, audit: dict, stage: str) -> str:
    parts = [shared.NEXT_STEPS, onboarding.checklist("audit", state, audit=audit)]
    if not audit["findings_reviewed"]:
        parts.append(copy.CURRENT_MILESTONE.format(stage=copy.STAGE_LABELS[stage]))
    return "\n\n".join(parts)


def bullet_section(heading: str, value: object, label: str) -> str | None:
    items = rows(value, label)
    return heading + "\n\n" + "\n".join("- " + literal(item, label) for item in items) if items else None


def measurement_status(lifecycle: dict, audit: dict) -> str:
    measurement = lifecycle["measurement"]
    if audit["measurement_complete"]:
        return copy.MEASUREMENT_COMPLETE
    if measurement:
        if not audit["checks_approved"] or measurement["checks_digest"] != audit["checks_digest"]:
            return copy.MEASUREMENT_STALE
        if measurement["status"] == "deferred":
            return copy.MEASUREMENT_DEFERRED + " " + literal(measurement["source"], "deferral reason")
        if measurement["status"] == "partial":
            return copy.MEASUREMENT_PARTIAL
        return copy.MEASUREMENT_STALE
    return copy.MEASUREMENT_PENDING


def render_suites(state: dict, lifecycle: dict, facts: dict) -> list[str]:
    supplied = rows(facts.get("suites", []), "suite candidates", maximum=1000)
    paths = []
    lines = [copy.SUITES_TABLE]
    for item in supplied:
        mapping(item, "suite candidate", {"path", "label", "description"})
        path = text(item.get("path"), "suite path")
        paths.append(path)
        lines.append(
            f"| {source_link(state, path, text(item.get('label'), 'suite label'))} | {literal(item.get('description'), 'suite description', table=True)} |"
        )
    if len(set(paths)) != len(paths) or set(paths) != set(lifecycle["suites"]["candidates"]):
        raise ValueError("Describe exactly the saved suite candidates")
    return [copy.SUITES_INTRO, "\n".join(lines)] if supplied else [copy.SUITE_LOCATION]


def render_checks(state: dict, lifecycle: dict, audit: dict, facts: dict) -> list[str]:
    if facts.get("validation") != "passed":
        raise ValueError("Proposed checks require successful specification validation")
    checks = mapping(facts.get("checks"), "proposed checks", set(copy.CHECK_KINDS))
    if set(checks) != set(copy.CHECK_KINDS):
        raise ValueError("Account for tools, capabilities, failure cases, and grading in the check preview")
    lines = [copy.CHECKS_TABLE]
    for kind, label in copy.CHECK_KINDS.items():
        items = rows(checks[kind], "check preview")
        if not items:
            lines.append(f"| {label} | {copy.NO_CHECKS} | {copy.NO_CHECK_EVIDENCE} |")
        for item in items:
            mapping(item, "proposed check", {"check", "evidence"})
            lines.append(
                f"| {label} | {literal(item.get('check'), 'check', table=True)} | {literal(item.get('evidence'), 'required evidence', table=True)} |"
            )
    parts = [
        copy.CHECKS_SAVED if facts.get("report_action") == "unchanged" else copy.CHECKS_INTRO,
        report_notice(state, lifecycle, facts),
        scope(state, lifecycle),
    ]
    if changes := bullet_section(copy.CHANGES_HEADING, facts.get("changes", []), "changes"):
        parts.append(changes)
    parts.extend(
        [
            "\n".join(lines),
            report_link(state, lifecycle, facts, "intended_coverage"),
            measurement_status(lifecycle, audit),
        ]
    )
    if limits := bullet_section(copy.LIMITS_HEADING, facts.get("limits"), "evidence limits"):
        parts.append(limits)
    parts.append(copy.CHECKS_NEXT if lifecycle["traces"]["status"] == "selected" else copy.CHECKS_LIMITED_NEXT)
    return parts


def recommendation_section(facts: dict) -> str | None:
    items = recommendations(facts)
    return (
        copy.RECOMMENDATIONS_HEADING
        + "\n\n"
        + "\n".join(
            f"{index}. **{literal(item['label'], 'recommendation label')}:** {literal(item['reason'], 'recommendation reason')}"
            for index, item in enumerate(items, 1)
        )
        if items
        else None
    )


def render_findings(state: dict, lifecycle: dict, audit: dict, facts: dict) -> list[str]:
    lines = [copy.FINDINGS_TABLE]
    for item in rows(facts.get("findings"), "findings"):
        mapping(item, "finding", {"kind", "label", "evidence"})
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in copy.FINDING_KINDS:
            raise ValueError("Findings must distinguish strengths, gaps, failures, and evidence limits")
        lines.append(
            f"| **{copy.FINDING_KINDS[kind]}: {literal(item.get('label'), 'finding', table=True)}** | {literal(item.get('evidence'), 'finding evidence', table=True)} |"
        )
    parts = [
        literal(facts.get("conclusion"), "audit conclusion"),
        report_notice(state, lifecycle, facts),
        "\n".join(lines),
    ]
    if "findings" in facts.get("links", {}):
        parts.append(report_link(state, lifecycle, facts, "findings"))
    parts.extend([scope(state, lifecycle), measurement_status(lifecycle, audit)])
    if limits := bullet_section(copy.LIMITS_HEADING, facts.get("limits"), "evidence limits"):
        parts.append(limits)
    if recommended := recommendation_section(facts):
        parts.append(recommended)
    return parts


def render(payload: dict) -> str:
    stage = payload["stage"]
    if stage not in STAGES or "journey" in payload or "ready" in payload:
        raise ValueError("Audit stages require saved state without journey or readiness overrides")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("Supply reconciled journey state")
    journey_state.validate(state)
    if type(payload.get("revision")) is not int or payload["revision"] != state["revision"]:
        raise ValueError("State revision differs; reload state and rebuild the presentation input")
    if state["journey"] != "audit":
        raise ValueError("Audit conversations require the saved audit journey")
    lifecycle = audit_state.record(state)
    if lifecycle is None:
        raise ValueError("Save an audit lifecycle before rendering audit checkpoints")
    facts = mapping(payload.get("facts", {}), "audit facts", COMMON_FACTS | set().union(*STAGE_FACTS.values()))
    observed = facts.get("audit_observations")
    if not isinstance(observed, dict):
        raise ValueError("Supply fresh audit_observations from normal journey-state inspection")
    audit = audit_state.derive(state, observed)
    assert audit is not None
    state = journey_state.audit_presentation_state(state, audit)
    stage = stage.removeprefix("audit_")
    active = onboarding.active_blockers(state)
    if stage == "resume":
        stage = "blockers" if active else "next_action" if audit["findings_reviewed"] else PHASES[audit["stage"]]
    facts = mapping(facts, "audit stage facts", COMMON_FACTS | STAGE_FACTS[stage])
    links = mapping(facts.get("links", {}), "audit links", {"report", *copy.SECTION_LABELS})
    for key in links:
        report_link(state, lifecycle, facts, key)
    if stage == "blockers":
        if not active:
            raise ValueError("Audit blocker rendering requires a saved relevant blocker")
        parts = [scope(state, lifecycle)] if not any(item["phase"] in {"setup", "ethos"} for item in active) else []
        if "report_action" in facts:
            parts.append(report_notice(state, lifecycle, facts))
        parts.append(onboarding.blocker_section(state, facts, confirmed=True) or "")
        return "\n\n".join(parts) + "\n"
    if active or facts.get("blockers"):
        raise ValueError("Resolve saved blockers or render audit_blockers before continuing")
    if not all(row["complete"] for row in journey_state.checklist(state)[:2]):
        raise ValueError("Resolve shared prerequisites and confirm ETHOS.md before audit checkpoints")
    if stage != PHASES[audit["stage"]] and not (stage == "next_action" and audit["findings_reviewed"]):
        raise ValueError("Audit stage differs from the current unresolved milestone; resume current state")
    pending = audit["pending_checkpoint"]
    question = None
    if stage in {"suites", "traces", "checks", "findings"}:
        question = decision_question(stage, lifecycle, facts)
        if not pending or pending["question"] != question:
            raise ValueError(
                "Save the current canonical audit decision question and checkpoint digest before rendering"
            )
    elif stage == "next_action":
        if pending:
            question = literal(pending["question"], "pending next action")
        elif state["pending_question"] is not None:
            raise ValueError("Clear the answered findings question before acknowledging acceptance")
    elif state["pending_question"] is not None:
        raise ValueError("Resolve the pending measurement question through a saved blocker")
    if stage == "suites":
        parts = render_suites(state, lifecycle, facts)
    elif stage == "traces":
        parts = [scope(state, lifecycle), copy.TRACE_INTRO, copy.TRACE_PURPOSE, copy.TRACE_LIMIT]
    elif stage == "checks":
        parts = render_checks(state, lifecycle, audit, facts)
    elif stage == "findings":
        parts = render_findings(state, lifecycle, audit, facts)
    elif stage == "measurement":
        parts = [copy.MEASUREMENT_PROGRESS, scope(state, lifecycle), measurement_status(lifecycle, audit)]
        if facts.get("progress"):
            parts.append(literal(facts["progress"], "measurement progress"))
        if "report_action" in facts:
            parts.append(report_notice(state, lifecycle, facts))
    else:
        parts = [
            copy.REVIEW_ACCEPTED,
            copy.AUDIT_COMPLETE if audit["complete"] else copy.AUDIT_LIMITED,
            report_link(state, lifecycle, facts),
        ]
        if not audit["complete"]:
            parts.append(measurement_status(lifecycle, audit))
        if audit["next_action_current"]:
            parts.append(copy.NEXT_ACTIONS[lifecycle["next_action"]["kind"]])
        elif pending and (recommended := recommendation_section(facts)):
            parts.append(recommended)
    parts.append(progress(state, audit, stage))
    if question:
        parts.append(question)
    return "\n\n".join(parts) + "\n"
