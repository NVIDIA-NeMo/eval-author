# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical copy for audit decisions and findings review."""

MODE_QUESTION = "How would you like to audit your evaluations?"
MODE_CHOICES = (
    (
        "Review tasks only –",
        "Review scenarios, fixtures, and verifiers against your agent’s intended behavior (ETHOS.md).",
    ),
    (
        "Full review with measured evaluation runs –",
        "Review tasks, then analyze saved agent runs to validate that intended behaviors and tools are covered. "
        "Uses additional tokens.",
    ),
)
MODE_LABELS = {"task_review": "Review tasks only", "measured": "Full review with measured evaluation runs"}
MODE_SELECTED = "**Audit approach:** {mode}"
TASK_REVIEW_UNMEASURED = "This audit reviews existing tasks. Run coverage remains unmeasured."
TASK_REVIEW_NEXT = "Once approved, I’ll review the selected tasks and bring back the findings."
TASK_REVIEW_NO_TASKS = "The findings review is complete. No evaluation tasks were available to audit."


def mode_prompt() -> str:
    choices = "\n\n".join(
        f"{index}. **{label}** {description}" for index, (label, description) in enumerate(MODE_CHOICES, 1)
    )
    return MODE_QUESTION + "\n\n" + choices


SUITES_INTRO = "I found these evaluation suites."
SUITES_TABLE = "| Suite | What it tests |\n|---|---|"
SUITES_QUESTION = "Are these the evaluation suites you want to audit, or should we add or exclude any?"
SUITE_LOCATION = "The evaluation suite location is still unresolved."
SUITE_LOCATION_QUESTION = "Where are the evaluation suites you want to audit?"
TRACE_INTRO = (
    "Next, we need **traces**: records of what the agent actually did during each run, "
    "including its tool calls and responses."
)
TRACE_PURPOSE = (
    "Test definitions and pass/fail scores alone don’t show which tools and behaviors were actually exercised."
)
TRACE_LIMIT = "Without usable traces, we can still review the intended checks, but coverage will remain **unmeasured**."
TRACE_QUESTION = "Which traces should this audit use, and where are they saved?"
SCOPE_HEADING = "**Selected scope**"
SCOPE_LABELS = {"suites": "Evaluation suites", "traces": "Traces"}
SCOPE_NONE = {
    "suites": "You confirmed that no evaluation suites exist.",
    "traces": "Traces are unavailable for this audit.",
}
SCOPE_PENDING = "Selection is still unresolved."
EXCLUSIONS = "**Exclusions**"
NO_EXCLUSIONS = "No exclusions."
EXCLUSION = "- {path}: {reason}"
CHECKS_INTRO = (
    "I’ve added the proposed checks to **Coverage report** in the **Audit coverage report**, "
    "including the evidence each check needs and any limitations."
)
CHECKS_SAVED = (
    "The proposed checks and evidence requirements are in **Coverage report** in the **Audit coverage report**."
)
REPORT_ACTIONS = {"created": "I created {report}.", "updated": "I updated {report}.", "unchanged": "Review {report}."}
REPORT_LABEL = "Audit coverage report"
CANONICAL_REPORT = ".eval-author/report_markdown/audits.md"
HTML_REPORT_PATH = ".eval-author/report.html"
HTML_REPORT_LABEL = "HTML report’s Audit tab"
HTML_REPORT = "You can also read it in the {html}."
SECTION_LABELS = {
    "intended_coverage": "Audit coverage report: Capabilities audited",
    "findings": "Audit coverage report: Key findings",
}
CHECKS_TABLE = "| Area | What we’ll check | Evidence needed |\n|---|---|---|"
CHECK_KINDS = {
    "tools": "Tools",
    "capabilities": "Capabilities",
    "failure_cases": "Failure cases",
    "grading": "Verifier",
}
NO_CHECKS = "No checks proposed in this area."
NO_CHECK_EVIDENCE = "Not applicable."
CHANGES_HEADING = "**Changes since the previous review**"
CHECKS_NEXT = "Once approved, I’ll perform the audit and bring back the findings."
CHECKS_LIMITED_NEXT = "Once approved, I’ll continue the agreed work, keeping unavailable measurement explicit."
CHECKS_QUESTION = "Please review **Coverage report**. Are these the right checks and evidence requirements, or should anything change?"
MEASUREMENT_PROGRESS = "The selected traces still need measurement against the approved checks."
MEASUREMENT_UNAVAILABLE = "Coverage cannot be measured until usable traces are selected."
MEASUREMENT_COMPLETE = "Measurement is complete for the selected scope."
MEASUREMENT_PARTIAL = "Measurement is partial; the successful subset does not complete the agreed scope."
MEASUREMENT_DEFERRED = "Measurement is deferred by your decision; the full measured audit remains incomplete."
MEASUREMENT_PENDING = "Coverage is unmeasured until the agreed measurement is complete."
MEASUREMENT_STALE = (
    "Saved measurements are missing, changed, or no longer applicable; they do not establish current coverage."
)
TASKS_CHANGED = (
    "Tasks recorded in the findings changed after approval: {tasks}. "
    "The approved checks and measurement no longer apply until you review the checks again."
)
FINDINGS_TABLE = "| Finding | Evidence and significance |\n|---|---|"
FINDING_KINDS = {
    "strength": "Strength",
    "missing_scenario": "Missing scenario",
    "grading_weakness": "Verifier weakness",
    "agent_failure": "Observed agent failure",
    "insufficient_evidence": "Insufficient evidence",
}
LIMITS_HEADING = "**Evidence limits**"
RECOMMENDATIONS_HEADING = "**Recommended next actions**"
NEXT_OFFER = "You can finish here or choose a recommended next action."
REVIEW_ACCEPTED = "The audit findings are recorded."
AUDIT_COMPLETE = "The audit is complete for the reviewed scope."
AUDIT_LIMITED = "The limited findings review is complete; the full measured audit remains incomplete."
NEXT_ACTIONS = {
    "finish": "You chose to finish here.",
    "improve": "You chose to plan evaluation improvements. The next step is to review their scope and grading before implementation.",
    "create": "You chose to plan your first evaluations from the reviewed findings.",
    "collect_evidence": "You chose to gather the missing evidence. The selected source and any new runs still need their applicable authorization.",
    "fix_agent": "You chose to address the observed agent behavior before changing the evaluations.",
}
STAGE_LABELS = {
    "suites": "Select evaluations",
    "traces": "Select traces",
    "checks": "Review proposed checks",
    "measurement": "Perform the audit",
    "findings": "Summarize findings",
    "next_action": "Findings recorded",
}
CURRENT_MILESTONE = "**Current audit step:** {stage}"
