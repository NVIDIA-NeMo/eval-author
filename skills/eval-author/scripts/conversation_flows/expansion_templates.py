# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixed copy for the supported expansion entry checkpoint."""

OPTIONS_INTRO = "Let’s work on improving your existing evaluations. I can help you:"
OPTIONS = (
    "Expand a starter suite created with Eval Author",
    "Create a task candidate from a recorded agent interaction (experimental)",
    "Audit your suite to identify improvements",
)
OPTIONS_QUESTION = "What would you like to work on?"
UNSUPPORTED_GRADER_REPAIR = (
    "This verifier change isn’t supported inside Eval Author yet. "
    "You can make the repair in a coding-agent chat using the audit recommendation."
)
AUDIT_SELECTION_ACKNOWLEDGMENTS = {
    "one": "Your improvement selection is saved.",
    "two": "Both of your improvement selections are saved.",
    "many": "Your improvement selections are saved.",
}
AUDIT_SELECTIONS = "**Selected improvements:** {value}"
AUDIT_NEXT_STEPS = (
    "I’ll check which changes Eval Author supports and explain any limits. "
    "For supported additions, I’ll show you the task plan and resolve any missing scope "
    "before creating and validating the tasks for your review. "
    "For supported work, I’ll reuse existing decisions and confirm any missing settings and permission "
    "before running your agent."
)
AUDIT_LIMITS = "**Known support limits:** {value}"


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


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))
