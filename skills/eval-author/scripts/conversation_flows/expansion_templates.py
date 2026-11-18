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


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))
