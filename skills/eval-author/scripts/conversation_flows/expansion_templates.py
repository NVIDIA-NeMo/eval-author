# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixed copy for expansion entry and construction-scope decisions."""

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

SCOPE_HEADING = "### Task scope"
SCOPE_REQUEST = "**Your requested improvement:** {outcome}"
SCOPE_PATH = "**Path:** {path}"
PATHS = {
    "starter_suite": "Expand your starter evaluation suite",
    "audit_tool_gap": "Create a task for a selected audit tool gap",
    "recorded_interaction": "Create a task candidate from a recorded interaction (experimental)",
}
SCOPE_TABLE = (
    "| Task | Selected | Scenario | Expected outcome | Grading and validation | Unresolved inputs |\n"
    "|---|---|---|---|---|---|"
)
YES = "Yes"
NO = "No"
NONE = "None"
GRADING_VALIDATION = "**Grading:** {grading}<br>**Validation:** {validation}"
NO_TASKS = "No tasks are proposed for construction yet."
NO_SELECTION = "No tasks are selected for construction."
COUNTS = "**Selected tasks:** {tasks}; **distinct input examples:** {examples}."
ATTEMPTS_HEADING = "**Planned agent attempts:**"
ATTEMPTS = "- **{task}:** Distinct input examples ({example_count}): {examples}; attempts per {unit}: {attempts}"
ATTEMPT_UNITS = {"harbor": "task", "gym": "selected dataset row"}
ATTEMPTS_NOTE = "Repeated attempts do not add input examples. Agent execution requires separate authorization."
SOURCES_HEADING = "**Selected sources:**"
SOURCE = "- {path} ({kind})"
SOURCE_KINDS = {"file": "source file", "task": "task directory"}
SOURCES_EMPTY = "No sources are selected yet."
LIMITS_HEADING = "**Construction limits:**"
LIMITS = (
    "- Construction attempts per task: at most {attempts}, including the initial attempt\n"
    "- Time limit: {minutes}\n"
    "- Cost limit: {cost}"
)
MINUTES = "{value} minutes"
DOLLARS = "${value}"
NO_CAP = "No cap agreed"
UNSETTLED = "Not settled"
LIMITS_QUESTIONS = "**Unresolved limits:** {inputs}"
UNRESOLVED = "**Other unresolved inputs:** {inputs}"
EXCLUSIONS = "**Outside this scope:** {exclusions}"
DEFERRED_HEADING = "**Selected improvements not included in construction:**"
DEFERRED = "- **{improvement}:** {reason}"
BLOCKERS_HEADING = "**Before construction can continue:**"
BLOCKER = "- {reason}"
APPROVED = "You approved this task selection and its construction limits."
CONSTRUCTION_READY = "I can continue with the selected authoring workflow within those limits."
CONSTRUCTION_BLOCKED = "Construction is paused until the items above are resolved."
EXECUTION_SEPARATE = "This scope approval does not authorize running your agent."
DECLINED = "You declined this scope. Your saved tasks and choices remain available; construction is paused."
REVISION = "You requested changes to this scope. Construction is paused until the revised scope is agreed."
PENDING = "Review the selected tasks, expected outcomes, grading, and construction limits before construction."
STALE_DECISION = "The saved scope needs reconciliation before construction can continue."


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))
