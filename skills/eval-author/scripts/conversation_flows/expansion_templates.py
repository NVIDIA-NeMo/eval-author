# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixed copy for expansion entry, planning, and creation checkpoints."""

OPTIONS_INTRO = "Let’s work on improving your existing evaluations. I can help you:"
OPTIONS = (
    "Expand a starter suite created with Eval Author",
    "Create a task candidate from a recorded agent interaction (experimental)",
    "Audit your suite to identify improvements",
)
OPTIONS_QUESTION = "What would you like to work on?"
UNSUPPORTED_GRADER_REPAIR = (
    "This grader change isn’t supported inside Eval Author yet. "
    "You can make the repair in a coding-agent chat using the audit recommendation."
)


SCOPE_HEADING = "### Task plan"
SCOPE_FIELDS = {
    "summary": "**Improvement:** {value}",
    "limits": "**Limits:** {value}",
}
SCOPE_TABLE = "| Task | Scenario | Expected outcome | Verification |\n|---|---|---|---|"
SCOPE_EMPTY = "No tasks are proposed yet."
SCOPE_DEFERRED = "**Deferred work:**"
SCOPE_STATUS = {
    "scope": "",
    "scope_approved": "This task plan is agreed.",
    "scope_declined": "You declined this task plan.",
    "scope_revision": "",
}

CREATION_HEADING = "### Creation and validation"
CREATION_TABLE = "| Task | Status |\n|---|---|"
CREATION_STATUS = {
    "queued": "Not created",
    "draft": "Created; checks pending",
    "passed": "Checks passed",
    "failed": "Checks failed",
    "blocked": "Blocked",
    "stale": "Checks need to be rerun",
    "missing": "Task files missing",
    "creating": "Creating",
    "validating": "Checking",
    "repairing": "Repairing",
}
CREATION_READY = "All agreed tasks passed their checks and are ready for task review."

REVIEW_HEADING = "### Review the created tasks"
REVIEW_ACTION = "Review the complete task set in the {report} or {markdown}."
REVIEW_DETAILS = "Each task README describes its input, behavior, expected result, grading, and limitations."
REVIEW_ARTIFACTS_PENDING = "The task review reports need to be refreshed."
REVIEW_TABLE = "| Task | Validation | Evidence |\n|---|---|---|"
REVIEW_REPORT_LABEL = "HTML report’s Tasks view"
REVIEW_MARKDOWN_LABEL = "Markdown task report"
REVIEW_README_LABEL = "suite README"
REVIEW_EVIDENCE_LABEL = "Validation evidence {index}"
REVIEW_NO_EVIDENCE = "No current evidence"
REVIEW_CONTROLS = "These checks validate the tasks and their graders; they do not measure agent performance."
REVIEW_PARTIAL = (
    "The complete agreed set is not ready for approval. Finish the remaining work or explicitly revise the task plan."
)
REVIEW_NEEDED = "Review the complete task set before execution."
REVIEW_STALE = "The task review is no longer current. Review the current task set again before execution."
REVIEW_APPROVED = "This task set is approved."
REVIEW_STATUS = {
    "approved": REVIEW_APPROVED + " Task approval does not authorize agent execution.",
    "changes_requested": "Changes are requested. Revise the affected drafts, repeat their checks, and return for review.",
    "declined": "You declined this task set. The drafts and their evidence remain saved.",
}
EXECUTION_HEADING = "### Agent execution settings"
EXECUTION_IDENTITY = "**Agent:** {agent}\n\n**Model:** {model}\n\n**Configuration:** {config}"
EXECUTION_UNSELECTED = "Not selected"
EXECUTION_TABLE = "| Task | Examples | Attempts | Dataset rows |\n|---|---|---|---|"
EXECUTION_SETTINGS = "**Validation:** {validation}\n\n**Limits:** {limits}\n\n**Open questions:** {questions}"
EXECUTION_SAVED = "**Saved agent/model setting:** {agent_model}\n\n**Saved attempt setting:** {attempts}"
EXECUTION_NO_QUESTIONS = "None"
EXECUTION_NO_ROWS = "Not applicable"
EXECUTION_AUTHORIZED = "Agent execution is authorized with these settings."
EXECUTION_DECLINED = "You declined agent execution. The approved tasks remain saved."
EXECUTION_PENDING = "Agent execution is awaiting applicable authorization."
EXECUTION_STALE = "The selected cases or execution settings need to be updated before agent execution."


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))
