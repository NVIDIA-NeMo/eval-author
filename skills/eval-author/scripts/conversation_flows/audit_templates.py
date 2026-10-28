# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical copy for audit decisions and findings review."""

SUITES_INTRO = "I found these evaluation suites."
SUITES_TABLE = "| Suite | What it tests |\n|---|---|"
SUITES_QUESTION = "Are these the evaluation suites you want to audit, or should we add or exclude any?"
SUITE_LOCATION = "The evaluation suite location is still unresolved."
SUITE_LOCATION_QUESTION = "Where are the evaluation suites you want to audit?"
TRACE_INTRO = (
    "Next, we need **traces** from those suites: records of what the agent actually did during each evaluation run, "
    "including its tool calls and responses."
)
TRACE_PURPOSE = "Test definitions and pass/fail scores alone don’t establish that coverage."
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
    "I’ve added the proposed checks to **Intended coverage** in the **Audit coverage report**, "
    "including the evidence each check needs and any limitations."
)
CHECKS_SAVED = (
    "The proposed checks and evidence requirements are in **Intended coverage** in the **Audit coverage report**."
)
REPORT_ACTIONS = {"created": "I created {report}.", "updated": "I updated {report}.", "unchanged": "Review {report}."}
REPORT_LABEL = "Audit coverage report"
SECTION_LABELS = {
    "intended_coverage": "Audit coverage report — Intended coverage",
    "findings": "Audit coverage report — Findings",
}
CHECKS_TABLE = "| Area | What we’ll check | Evidence needed |\n|---|---|---|"
CHECK_KINDS = {"tools": "Tools", "capabilities": "Capabilities", "failure_cases": "Failure cases", "grading": "Grading"}
NO_CHECKS = "No checks proposed in this area."
NO_CHECK_EVIDENCE = "Not applicable."
CHANGES_HEADING = "**Changes since the previous review**"
CHECKS_NEXT = "Once approved, I’ll perform the audit and bring back the findings."
CHECKS_LIMITED_NEXT = "Once approved, I’ll continue the agreed work, keeping unavailable measurement explicit."
CHECKS_QUESTION = "Please review **Intended coverage**. Are these the right checks and evidence requirements, or should anything change?"
MEASUREMENT_PROGRESS = (
    "I’m inspecting the selected evaluations and measuring the selected traces against the approved checks."
)
MEASUREMENT_COMPLETE = "Measurement is complete for the selected scope."
MEASUREMENT_PARTIAL = "Measurement is partial; the successful subset does not complete the agreed scope."
MEASUREMENT_DEFERRED = "Measurement is deferred by your decision; the full measured audit remains incomplete."
MEASUREMENT_PENDING = "Coverage is unmeasured until the agreed measurement is complete."
MEASUREMENT_STALE = (
    "Saved measurements are missing, changed, or no longer applicable; they do not establish current coverage."
)
FINDINGS_TABLE = "| Finding | Evidence and significance |\n|---|---|"
FINDING_KINDS = {
    "strength": "Strength",
    "missing_scenario": "Missing scenario",
    "grading_weakness": "Grading weakness",
    "agent_failure": "Observed agent failure",
    "insufficient_evidence": "Insufficient evidence",
}
LIMITS_HEADING = "**Evidence limits**"
RECOMMENDATIONS_HEADING = "**Recommended next actions**"
FINDINGS_QUESTION = "Do these findings look right? If so, would you like to finish the audit here or plan {choices}?"
IMPROVEMENT_CHOICE = "the {label}"
TWO_IMPROVEMENTS = "{choices}, or both"
MULTIPLE_IMPROVEMENTS = "{choices}, or any combination"
NO_CHANGE_QUESTION = "Do these findings look right? You can finish the audit here or tell me what needs correcting."
OTHER_ACTION_QUESTION = (
    "Do these findings look right? If so, would you like to finish the audit here or choose a recommended next action?"
)
REVIEW_ACCEPTED = "You’ve accepted the audit findings."
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
    "findings": "Review findings",
    "next_action": "Findings accepted",
}
CURRENT_MILESTONE = "**Current audit step:** {stage}"
