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
    "I’ll reuse existing decisions and confirm any missing settings and permission before running your agent."
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
CREATION_PLAN = (
    "The next steps are to reuse or prepare the test setup, create the agreed tasks with their test data, "
    "instructions, and verifiers, and check that correct outcomes pass and relevant incorrect outcomes fail. "
    "I’ll repair problems in these new drafts and bring the tasks back for your review. "
    "After you approve the tasks, we’ll confirm which agent and model to evaluate, "
    "the run settings, and your permission to run the evaluation."
)

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
CREATION_NEXT = (
    "I’ll bring the complete task set back for your review once the remaining work and any missing inputs are resolved."
)
CREATION_READY = (
    "All agreed tasks passed their task and verifier checks. Next, I’ll show you the tasks and their validation "
    "evidence for review. After task approval, we’ll confirm the agent and run settings."
)

REVIEW_HEADING = "### Review the created tasks"
REVIEW_READY = "The agreed tasks have been created and passed their task and verifier checks."
REVIEW_ACTION = "Review the complete task set in the {markdown}. You can also use the {report}."
REVIEW_DETAILS = "Each task README describes its input, behavior, expected result, verifier, and limitations."
REVIEW_ARTIFACTS_PENDING = "The task review reports need to be refreshed."
REVIEW_TABLE = "| Task | Validation | Evidence |\n|---|---|---|"
REVIEW_REPORT_LABEL = "HTML report’s Tasks view"
REVIEW_MARKDOWN_LABEL = "Markdown task report"
REVIEW_README_LABEL = "suite README"
REVIEW_EVIDENCE_LABEL = "Validation evidence {index}"
REVIEW_NO_EVIDENCE = "No current evidence"
REVIEW_CONTROLS = "These checks validate the tasks and their verifiers; they do not measure agent performance."
REVIEW_PARTIAL = (
    "The complete agreed set is not ready for approval. Next, I’ll finish or recheck the remaining work "
    "and bring the tasks back for review, after any missing inputs or blockers are resolved. "
    "You can also revise the agreed task plan."
)
REVIEW_NEEDED = (
    "Review the tasks and their verifiers. If you approve them, I’ll show the agent and run settings "
    "for a separate run decision."
)
REVIEW_STALE = (
    "The previous task review no longer applies. I’ll finish the remaining checks or report updates, "
    "then show the current task set for review."
)
REVIEW_UPDATED = "The updated tasks have passed their checks and need your approval. Agent and run settings come next."
REVIEW_APPROVED = "This task set is approved."
REVIEW_STATUS = {
    "approved": REVIEW_APPROVED + " Next, we’ll confirm the agent and run settings before you decide whether to run.",
    "changes_requested": "I’ll update the affected new drafts, repeat their checks, and bring the revised tasks back for your review.",
    "declined": "You declined this task set. The drafts and their evidence remain saved.",
}
EXECUTION_HEADING = "### Review the agent run plan"
EXECUTION_PURPOSE = (
    "The task and verifier checks have passed. Running your agent will test its performance on the approved tasks."
)
EXECUTION_IDENTITY = "Agent to test: **{agent}**, using **{model}**."
EXECUTION_CONFIG = "**Run configuration:** {config}"
EXECUTION_UNSELECTED = "Not selected"
EXECUTION_TABLE = "| Task | Examples | Attempts per {unit} | Dataset rows |\n|---|---|---|---|"
EXECUTION_ATTEMPT_UNIT = {"gym": "example", "harbor": "task"}
EXECUTION_SETTINGS = "**Checks:** {validation}\n\n**Run limits:** {limits}"
EXECUTION_QUESTIONS = "**Open questions:** {questions}"
EXECUTION_SAVED = "**Saved agent/model setting:** {agent_model}\n\n**Saved attempt setting:** {attempts}"
EXECUTION_NO_QUESTIONS = "None"
EXECUTION_NO_ROWS = "Not applicable"
EXECUTION_AUTHORIZED = "You’ve authorized this run with these settings. The next step is to run the selected tasks."
EXECUTION_DECLINED = "You declined agent execution. The approved tasks remain saved for when you want to run them."
EXECUTION_PENDING = (
    "Review these settings before deciding whether to run the approved tasks. This run needs your permission."
)
EXECUTION_STALE = (
    "Next, we need to resolve or update the selected cases and run settings. "
    "I’ll show the complete settings for your permission before running your agent."
)


def options_prompt() -> str:
    choices = "\n".join(f"- {option}" for option in OPTIONS)
    return "\n\n".join((OPTIONS_INTRO, choices, OPTIONS_QUESTION))


RESULTS_HEADING = "### Agent run results"
RESULTS_TABLE = "| Attempt | Result | Output and actions | Full trace | Verification |\n|---|---|---|---|---|"
RESULT_ATTEMPT = "{task} — attempt {number}"
RESULT_STATUS = {"completed": "Completed", "error": "Execution error", "incomplete": "Incomplete"}
RESULT_SCORE = "; score {value}"
RESULT_SCORE_UNAVAILABLE = "; score not available"
RESULT_ERROR = "; {value}"
RESULT_NO_ERROR = "; no execution error"
RESULT_MISSING = "Missing result"
RESULT_UNAVAILABLE = "Not available"
RESULT_LINK_LABELS = {"output": "Output and actions", "trace": "Full trace", "verification": "Verifier explanation"}
RESULTS_REVIEW = (
    "Review the outputs and verifier explanations. Passing checks does not accept a task; "
    "final acceptance requires your approval of these results. You can give feedback or request changes."
)
RESULTS_QUESTION = "Which tasks do you approve, or what would you like to change?"
