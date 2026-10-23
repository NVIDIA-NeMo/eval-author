# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical copy for authoring, reviewing, and running a first evaluation suite."""

TRACE_INTRO = (
    "We’ll use your confirmed `ETHOS.md` to propose your first evaluation tasks. "
    "Existing traces can help us use realistic requests, but they aren’t required."
)
TRACE_QUESTION = "Would you like to use existing traces, or start from `ETHOS.md`?"
TRACE_CHOICES = ("Start from ETHOS.md", "Use existing traces")
PROPOSAL_INTRO = "Here are the proposed evaluation tasks."
PROPOSAL_TABLE = "| Task | Input | Behavior under test | Expected result | Grading |\n|---|---|---|---|---|"
FIRST_CHOICE = "I’ll build **{title}** first."
PROPOSAL_PROCESS = (
    "Proposed tasks: {tasks}; distinct input examples: {examples}. "
    "After you approve the proposal, I’ll build only that first task and pause for your review "
    "before building any remaining tasks."
)
PROPOSAL_QUESTION = (
    "Do these tasks make sense for your agent? Are we testing the right behaviors, "
    "with the right expected results and grading? Tell me what you’d change, add, "
    "or prioritize—or approve the proposal as written."
)
FIRST_PROGRESS = (
    "I’m preparing the first task’s starting data and checking that its environment works. "
    "Then I’ll add the [reference solution](https://docs.harborframework.com/tasks/solution) "
    "and [grader](https://docs.harborframework.com/tasks/verifier)."
)
GYM_PROGRESS = (
    "I’m preparing the first task’s starting data and checking that its environment works. "
    "Then I’ll add the reference fixtures and native Gym verifier."
)
FIRST_QUESTION = (
    "Does this represent the behavior you want to test, and would its grading judge the agent fairly? "
    "Approve the first task or tell me what to change before I build the remaining tasks."
)
REMAINDER_PROGRESS = (
    "I’ll use the approved pattern to build the remaining tasks, then check that their graders "
    "accept reference results and reject relevant incorrect results."
)
SETTINGS_INTRO = "Here are the settings for running the approved tasks."
SETTINGS_NOTICE = "Agent runs and paid grading require authorization within these limits."
SETTINGS_LABELS = ("Agent/model", "Tasks and examples", "Attempts", "Validation", "Time/cost limits", "Open questions")
TASK_COUNTS = "{tasks} tasks; {examples} distinct input examples."
NO_OPEN_QUESTIONS = "None."
CHECKS_PASSED = (
    "All selected tasks passed their reference checks. Those checks establish that the tasks work; "
    "they don’t measure your agent’s performance."
)
RUN_PROGRESS = "I’ll now run the approved tasks with the agreed settings."
RUN_QUESTION = "May I run the selected tasks with the displayed settings and limits?"
FIRST_REVIEW = (
    "- **Input:** {input}\n"
    "- **Expected result:** {expected_result}\n"
    "- **Grading:** {grading} Limitation: {grading_limitation}\n"
    "- **Status:** Environment proof: passed. Grading validation: {validation}. Agent execution: {execution}."
)
VALIDATION_PASSED = "passed"
VALIDATION_PENDING = "pending or requiring attention"
EXECUTION_COMPLETED = "completed"
EXECUTION_PENDING = "not completed for the agreed settings"
INCOMPLETE = "Your first evaluations remain incomplete. Tasks with unfinished work: {tasks}."
PENDING_RUN = "run completion"
TASK_STATUS = "- **{task}:** Created: {created}; validated: {validated}; agreed executions complete: {executed}."
YES = "yes"
NO = "no"
SUITE_COUNTS = (
    "Selected suite: {completed}/{planned} completed attempts with current evidence across {runs} selected runs "
    "({observed} observed attempts). Selected run IDs: {run_ids}."
)
NO_RUNS = "none"
REVIEW_UNAVAILABLE = (
    "Agent performance has not been established by a current reviewed attempt. "
    "Control results, missing execution evidence, and stale evidence cannot establish an agent baseline."
)
HISTORICAL_REVIEW = (
    "Saved review identity: run **{run}**; attempt **{attempt}**. "
    "Its evidence is unavailable, stale, or not applicable to the current agreement."
)
RUN_INVENTORY = (
    "- **{run}** ({role}): {completed}/{planned} completed attempts with current grades; "
    "{infrastructure} execution failures; {ungraded} completed attempts without grades; "
    "{missing} missing attempts; {unavailable} retained attempts with stale or unavailable evidence."
)
MISSING_REVIEW_BLOCKER = (
    "- **Review agent performance:** A current actual-agent attempt and its retained review evidence are missing. "
    "**Needed:** Resolve the missing or changed evidence and explicitly select and review an actual-agent attempt; "
    "obtain execution authorization first if a new run is needed."
)
INCOMPLETE_EXECUTION_BLOCKER = (
    "- **Complete the agreed evaluations:** Task validation or the selected executions remain unfinished. "
    "**Needed:** Resolve the unfinished work listed above, reusing applicable approval and obtaining "
    "authorization before additional paid work."
)
JOB_ERRORS = (
    "Retained jobs with execution errors: {runs}. Successful attempts from these jobs count only where their "
    "evidence is current. {recovery}"
)
RECOVERY_COMPLETE = "The selected recovery runs account for the remaining work."
RECOVERY_PENDING = "Run completion still requires attention."
RUN_COUNTS = (
    "Run **{run}**: {completed}/{planned} completed attempts with grades; {infrastructure} execution failures; "
    "{ungraded} completed attempts without grades; {missing} missing attempts; "
    "{unavailable} retained attempts with stale or unavailable evidence.\n\nAgent-quality failures: {quality}"
)
ATTEMPT_REVIEW = (
    "Reviewed run: **{run}**. Attempt: **{attempt}** (native identity: {native}).\n\n"
    "- **Input:** {input}\n"
    "- **Observed response/actions:** {observed}\n"
    "- **Grade:** {rewards}. {grade_explanation}\n"
    "- **Agent/model:** Configured identity: {agent}; {model}. {identity_limitations}"
)
RUN_EVIDENCE = "Run evidence: {links}"
ATTEMPT_EVIDENCE = "Attempt evidence: {links}"
LIMITATIONS = "Limitations: {limitations}"
RERUN = "You can rerun the full suite or just this task using the **{instructions}**."
AUDIT_QUESTION = (
    "Would you like to see how you could improve your suite further? We can audit its coverage "
    "and grading against the behavior described in `ETHOS.md` to identify gaps and worthwhile improvements."
)
BLOCKER = "- **{step}:** {reason} **Needed:** {needed}"
LINK_LABELS = {
    "report": "the report",
    "readme": "the suite README",
    "tasks_view": "Tasks view",
    "task_readme": "task README",
    "rerun": "saved run instructions",
    "task_rerun": "Task-specific run instructions",
}
