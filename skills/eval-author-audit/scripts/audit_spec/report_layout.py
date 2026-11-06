# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

SECTIONS = (
    (2, "Summary"),
    (3, "Evaluation coverage"),
    (2, "Key findings"),
    (2, "Recommended Tasks by Capability & Tool"),
    (2, "Task catalog"),
    (2, "Coverage report"),
    (2, "Verifier cheat screening"),
    (2, "Scope and Execution Evidence"),
    (2, "Definitions"),
)
EVAL_AUTHOR_URL = "https://github.com/NVIDIA-NeMo/eval-author"
SUBHEADINGS = {"advisory": "ADVISORY", "next_actions": "Next actions"}
ACTIONS = {
    "new_task": "Add a task",
    "revise_task": "Revise a task",
    "collect_evidence": "Collect more evidence",
    "fix_agent": "Fix the agent",
    "review_scope": "Review the audit scope",
    "repair_verifier": "Repair the verifier",
}
PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
TABLES = {
    "recommended": ("Functionality", "Proposed task"),
    "tasks": ("Task", "Behavior", "Capabilities and tools", "Findings"),
    "coverage": ("Functionality", "Intended check and required evidence", "Tasks", "Run evidence"),
    "current": ("", "Current audit"),
}
KINDS = (("capability", "Capabilities"), ("tool", "Tools"), ("failure_case", "Failure cases"))
COVERAGE_HEADINGS = {
    "capability": "Capabilities audited",
    "tool": "Tools",
    "failure_case": "Failure cases",
}
INTENDED_FIELDS = (
    ("trigger", "Trigger"),
    ("expected_use", "Expected use"),
    ("expected_behavior", "Expected behavior"),
    ("expected_failure_behavior", "If the tool fails"),
    ("required_tools", "Required tools"),
    ("expected_tools", "Expected tools"),
    ("prohibited_tools", "Prohibited tools"),
    ("prohibited_outputs", "Prohibited outputs"),
)
VERIFIER_CATEGORIES = frozenset(
    {"verifier_cheating", "weak_verifier", "false_rejection", "ungraded_execution", "trust_boundary"}
)
COPY = {
    "unmeasured": "Unmeasured",
    "not_demonstrated": "Not demonstrated",
    "no_coverage": "No evaluation coverage",
    "run_word": "agent run",
    "task_files": "the task files",
    "repeated_task_fix": (
        "Name {task} once per finding, as the subject of the explanation's first sentence. "
        'Elsewhere in the finding, its advisory, and its proposed tasks, write "the task", '
        '"its verifier", or "the grading script".'
    ),
    "no_items": "No items declared",
    "basis_mapped": "Mapped tasks (runs not measured)",
    "basis_demonstrated": "Demonstrated in selected runs",
    "demonstrated": "Demonstrated in {runs} selected {run_word}",
    "none_observed": "None observed",
    "proposed_task": "Proposed task:",
    "coverage_gap_title": "Coverage gaps",
    "gap_undemonstrated": "{counts} have no demonstrated coverage in the selected runs.",
    "gap_unmapped": "{counts} have no mapped task.",
    "gap_all_mapped": "Every declared item has at least one mapped task.",
    "gap_reference": "The {coverage_report} lists every declared item with its tasks and run evidence.",
    "empty_kind": "The audit declares no items in this category.",
    "no_findings": "The audit records no key findings.",
    "no_tasks": "The audit inspected no runnable tasks.",
    "no_proposals": "The audit proposes no tasks in this area.",
    "no_runs": "No selected agent runs",
    "resolved_advisory": "The finding is resolved. Its evidence remains part of this audit.",
    "unresolved_advisory": "The affected verifier results need review before they can be trusted.",
    "not_assessed": "Verifier cheat screening has not been assessed because no review is available.",
    "no_issues": "No issues found in reviewed scope",
    "summary_no_tampering": "The audit uncovered no evidence of verifier tampering in the reviewed tasks.",
    "summary_cheating": "The audit uncovered evidence of verifier cheating.",
    "summary_verifier_issues": "The audit identified verifier issues in the reviewed tasks.",
    "summary_verifier_unproven": "The audit recorded verifier concerns that remain unproven.",
    "summary_verifier_resolved": "The audit retains resolved verifier findings for reference.",
    "summary_coverage_unmeasured": "Coverage was not measured for this audit because the audit measurement step was not run.",
    "summary_coverage_empty": "Coverage was not measured because this audit declares no coverage items.",
    "summary_coverage_measured": "Coverage was measured for {measured} in the selected agent evaluation runs.",
    "summary_coverage_partial": "Coverage was partially measured: {measured} were measured, and {unmeasured} remain unmeasured.",
    "measure_coverage": (
        "To measure coverage, agree on the audit checks, select recorded traces from completed agent evaluation runs, "
        "and ask Eval Author to measure them against those checks and regenerate this report."
    ),
    "summary_ratio_measured": "{covered} of {count} declared capabilities ({ratio}) have demonstrated coverage in the selected runs.",
    "summary_ratio_mapped": "{covered} of {count} declared capabilities ({ratio}) have at least one mapped task.",
    "coverage": (
        "The tables retain every declared behavior, tool, and failure scenario in audit order. "
        "Product-area grouping is omitted because no structured product-area assignment is recorded."
    ),
    "coverage_evidence": (
        "The Tasks column lists the tasks mapped to each item, including boundary-only relationships. "
        "Run evidence shows whether selected runs demonstrated the item. For tools, demonstration establishes invocation only."
    ),
    "catalog": (
        "The catalog lists inspected runnable tasks, their intended behavior, and their audit mappings. "
        "Observed tools come from selected measurements. Findings link to each recorded issue."
    ),
    "recommended": "The recommendations draw on the inspected task definitions and the audit specification.",
    "recommended_with_ethos": (
        "The recommendations draw on the inspected task definitions, the audit specification, and the selected Ethos."
    ),
    "screening_overview": "The audit considers the following verifier cheat checks where they apply. Task-specific issues appear in the findings.",
}
SUMMARY_COUNTS = (
    "This audit includes {advisories} {advisory_word}, {findings} {finding_word}, and {tasks} recommended {task_word}."
)
FINDING_CATEGORIES = {
    "missing_task": "a missing task",
    "agent_failure": "an agent failure",
    "missing_trigger": "a failure to reach the intended trigger",
    "evidence_limit": "a limitation in the available evidence",
    "weak_verifier": "a weak verifier",
    "verifier_cheating": "verifier cheating",
    "false_rejection": "the rejection of a valid solution",
    "ungraded_execution": "an execution without usable verification",
    "trust_boundary": "a problem with the verifier's trust boundary",
}
FINDING_SENTENCES = {
    "confirmed": "{basis_subject} confirmed {category}, as documented in {evidence}.",
    "unproven": "The available {basis} evidence in {evidence} leaves this finding unproven.",
    "resolved": "The audit records this finding as resolved and retains the {basis} evidence in {evidence}.",
}
FINDING_SENTENCES_UNCITED = {
    "confirmed": "{basis_subject} confirmed {category}.",
    "unproven": "This finding is unproven.",
    "resolved": "The audit records this finding as resolved.",
}
DEFINITIONS = {
    "Behavior": "An action or outcome expected from the product, such as saving supplied research or answering with exact citations.",
    "Capability": "A named audit item describing an expected behavior. Stable identifiers connect task mappings to coverage tables.",
    "Measurement": "A comparison of selected agent evaluation runs with the audit criteria. It records which items the available run evidence demonstrates.",
    "Tool": "A function the agent can call. An observed call establishes invocation and does not establish the intended outcome.",
    "Task": "A runnable evaluation with a scenario, starting conditions, and a verifier. A trial is one execution. Task paths are relative to the audited repository root.",
    "Verifier": "A program or judging rule that determines whether a task's requirements were met.",
    "Environment": "The files, data, mocks, and starting state supplied to a task.",
    "Run Evidence": "An assessment of selected execution records. Demonstrated means at least one selected agent run satisfied an item's requirements. Not demonstrated means the selected measurements did not establish them. Unmeasured means no applicable method ran.",
}
FAMILIES = {
    "early_exit": "Early exit",
    "all_failure": "All-failure behavior",
    "reference_comments": "Reference-like comments",
    "forged_success": "Forged success",
    "valid_alternative": "Valid alternative",
    "realistic_wrong_answer": "Realistic wrong answer",
}
SCREENING_CHECKS = {
    "early_exit": "Empty output or an early exit must not earn credit before the required checks finish.",
    "all_failure": "Failing every required assertion must not produce a passing result.",
    "reference_comments": "Comments and text that resembles a reference answer must not bypass the real checks.",
    "forged_success": "Success messages and reward files written by the agent must not replace evidence produced by the verifier.",
    "valid_alternative": "A different valid solution should pass without matching one reference implementation.",
    "realistic_wrong_answer": "A plausible answer that fails the task's requirements should be rejected.",
}
