# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical copy for authoring, reviewing, and running a first evaluation suite."""

TRACE_INTRO = (
    "We’ll use your confirmed `ETHOS.md` to propose your first evaluation tasks. "
    "Existing traces can help us use realistic requests, but they aren’t required."
)
TRACE_QUESTION = "Would you like to use existing traces, or start from `ETHOS.md`?"
TRACE_CHOICES = ("Start from ETHOS.md", "Use existing traces")
SAVED_DESIGN_INTRO = "I found a saved design for your evaluation tasks."
SAVED_DESIGN_COUNTS = "Saved design: {tasks} tasks; {examples} distinct input examples."
SAVED_DESIGN_TABLE = "| Task ID | Behavior under test |\n|---|---|"
SAVED_DESIGN_SOURCE = "**Source:** {source}"
SAVED_SOURCE_ETHOS = "`ETHOS.md`, with no selected traces."
SAVED_SOURCE_TRACES = "`ETHOS.md` with previously selected traces."
SAVED_SOURCE_UNKNOWN = "Not recorded; the source choice still needs confirmation."
SAVED_DESIGN_APPROVAL = "**Saved design approval:** {approval}"
SAVED_APPROVAL_CURRENT = "Previously recorded for this design."
SAVED_APPROVAL_STALE = "Recorded for an earlier design revision; this design still needs approval."
SAVED_APPROVAL_UNKNOWN = "Not recorded; continuing still requires proposal approval."
SAVED_DESIGN_PROGRESS = (
    "**Saved progress:** {created}/{tasks} tasks created; {validated}/{tasks} validated; "
    "{executed}/{tasks} with agreed executions complete."
)
SAVED_BEHAVIOR_UNKNOWN = "Not recorded"
SAVED_DESIGN_CHOICES = (
    "**Continue:** Resume this saved design, reusing applicable approvals and evidence.",
    "**Revise:** Change this design before continuing.",
    "**Start a new proposal:** Set this design aside and choose sources for a new proposal.",
)
SAVED_DESIGN_QUESTION = "Would you like to continue this saved design, revise it, or start a new proposal?"
PROPOSAL_INTRO = "Here are the proposed evaluation tasks."
PROPOSAL_TABLE = "| Task ID | Input | Behavior under test | Expected result | Verifier |\n|---|---|---|---|---|"
FIRST_CHOICE = "I’ll build **{task_id}** first."
PROPOSAL_PROCESS = (
    "Proposed tasks: {tasks}; distinct input examples: {examples}. "
    "After you approve the proposal, I’ll build only that first task and pause for your review "
    "before building any remaining tasks."
)
PROPOSAL_QUESTION = (
    "Do these tasks make sense for your agent? Are we testing the right behaviors, "
    "with the right expected results and verifiers? Tell me what you’d change, add, "
    "or prioritize—or approve the proposal as written."
)
VALIDATION_SETUP_INTRO = "Before I build the tasks, I need a few details for validating their verifiers."
VALIDATION_SETUP_QUESTIONS = "**Questions before building:**"
VALIDATION_SETUP_QUESTION = "Please provide these details so I can build and validate the tasks."
FIRST_PROGRESS = (
    "I’m preparing the first task’s starting data and checking that its environment works. "
    "Then I’ll add the [reference solution](https://docs.harborframework.com/tasks/solution) "
    "and [verifier](https://docs.harborframework.com/tasks/verifier)."
)
GYM_PROGRESS = (
    "I’m preparing the first task’s starting data and checking that its environment works. "
    "Then I’ll add the reference fixtures and native Gym verifier."
)
BUILD_HEADING = "**First task: {task_id}**"
BUILD_STEPS = {
    "environment": "Prepare starting data and environment",
    "environment_check": "Check the environment",
    "grading": "Add the reference solution and verifier",
    "artifacts": "Prepare README and report",
}
GYM_BUILD_GRADING = "Add the reference fixtures and native Gym verifier"
BUILD_STATUSES = {
    "complete": "Complete",
    "in_progress": "In progress",
    "pending": "Pending",
    "blocked": "Blocked",
    "waiting": "Waiting for progress",
}
BUILD_SKILLS = {
    "environment": "eval-author-environment",
    "environment_check": "eval-author-environment",
    "grading": "eval-author-first-eval",
    "tasks": "eval-author-first-eval",
    "validation": "eval-author-first-eval",
    "execution": "eval-author-first-eval",
    "review": "eval-author-first-eval",
    "artifacts": "eval-author-first-eval, eval-author",
}
BUILD_ROW = "- [{checked}] {label} (skill in use: {skill}) — {status}"
RUN_ROW = "- [{checked}] {label} — {status}"
BUILD_TASK_COUNTS = "{label}: {completed}/{total} tasks"
REMAINING_HEADING = "**Remaining tasks**"
REMAINING_STEPS = {
    "tasks": "Build remaining tasks",
    "environment_check": "Check task environments",
    "validation": "Validate all task verifiers",
    "artifacts": "Refresh README and report",
}
NO_REMAINING_TASKS = "No additional tasks agreed."
FIRST_REVIEW_INTRO = (
    "Your first task, **{task_id}**, is ready for review. It follows your approved design and confirmed `ETHOS.md`."
)
FIRST_REVIEW_ACTION = "Review your first task in the {report} or {readme}."
FIRST_REVIEW_REPORT_LABEL = "HTML report’s Tasks view"
FIRST_REVIEW_README_LABEL = "mirrored README"
FIRST_QUESTION = (
    "Does this represent the behavior you want to test, and would its verifier assess the agent fairly? "
    "Approve the first task or tell me what to change before I build the remaining tasks."
)
REMAINDER_PROGRESS = (
    "I’ll use the approved pattern to build the remaining tasks, then check that their verifiers "
    "accept reference results and reject relevant incorrect results."
)
NO_REMAINDER_PROGRESS = "I’ll check the approved task’s verifier, then refresh the README and report."
SUITE_REVIEW_INTRO = (
    "Your evaluation suite is ready for review: {tasks} tasks; {examples} distinct input examples. "
    "It follows your approved design and confirmed `ETHOS.md`."
)
SUITE_REVIEW_ACTION = "Review your tasks in the {report} or {readme}."
SUITE_REVIEW_TABLE = "| Task ID | Behavior under test | Expected result | Verifier |\n|---|---|---|---|"
SUITE_REVIEW_STATUS = (
    "**Status:** Environment proof: {environments}/{tasks} passed. Verifier validation: {validated}/{tasks} passed. "
    "Agent execution: {executed}/{tasks} tasks complete."
)
SUITE_QUESTION = "Do these tasks look good? Approve the suite or tell me what to change before we choose how to run it."
SETTINGS_INTRO = "Here are the settings for running the approved tasks."
SETTINGS_LABELS = ("Attempts", "Validation", "Time/cost limits", "Open questions")
NO_OPEN_QUESTIONS = "None."
TRAJECTORY_INTRO = "Eval Author needs them to audit your suite’s coverage against `ETHOS.md` later."
TRAJECTORY_CONTENTS = "These records include {contents}."
TRAJECTORY_DETAILS = (
    "I’ll save them locally in {directory}, with a new folder for each run. "
    "{implications}, and you can choose a different location."
)
TRAJECTORY_QUESTION = "**Would you like to save a record of each evaluation run?**"
TRAJECTORY_CHOICES = ("Yes, save them", "No, don’t save them")
TRAJECTORY_AUTOMATIC = (
    "The selected integration already saves trajectories automatically. "
    "I’ll reuse that behavior and directory; you can choose another local directory."
)
TRAJECTORY_ENABLED = "Selected: save trajectories locally for this suite and its reruns."
TRAJECTORY_DECLINED = (
    "Optional saving is off. Future audits need other saved trajectories or a later authorized run with saving enabled. "
    "Native logs are unchanged."
)
TRAJECTORY_UNSUPPORTED = (
    "The selected integration cannot save audit-usable trajectories through its supported configuration. "
    "Audit measurement will need trajectories from another source or a later authorized run with capture support."
)
TRAJECTORY_SUMMARY = "**Trajectory saving:** {status}"
TRAJECTORY_SUMMARY_STATUS = {
    "automatic": "Automatic.",
    "enabled": "Save locally.",
    "declined": TRAJECTORY_DECLINED,
    "unsupported": "Unsupported by this integration.",
}
TRAJECTORY_SUMMARY_DIRECTORY = "**Local destination:** {directory}."
CHECKS_PASSED = (
    "The verifiers have already passed their checks. I’ll reuse those saved results; "
    "the agent run will measure your agent’s performance."
)
RUN_PROGRESS = "I’ll now run your agent on the approved tasks with the agreed settings."
RUN_QUESTION = "May I run the selected tasks with the displayed settings and limits?"
RUN_HEADING = "**Evaluation run**"
RUN_STEPS = {
    "validation": "Check task verifiers",
    "execution": "Run approved tasks",
    "review": "Review an agent attempt",
    "artifacts": "Update README and report",
}
RUN_ATTEMPTS = (
    "**Attempts:** {completed}/{planned} completed with grades; {infrastructure} execution errors; "
    "{ungraded} completed without grades."
)
RUN_IDENTITY = "**Run:** {run}"
RUN_JOB_ERRORS = "**Jobs with execution errors:** {runs}"
RUN_CURRENT_TASKS = "**Current tasks:** {tasks}"
RUN_TASK_UNKNOWN = "The runtime hasn’t reported a current task."
RUN_EVIDENCE_PENDING = (
    "Some recorded results have missing or outdated evidence; they aren’t included in completed-attempt counts."
)
FIRST_REVIEW = (
    "- **Input:** {input}\n"
    "- **Expected result:** {expected_result}\n"
    "- **Verifier:** {grading} Limitation: {grading_limitation}\n"
    "- **Status:** Environment proof: passed. Verifier validation: {validation}. Agent execution: {execution}."
)
VALIDATION_PASSED = "passed"
VALIDATION_PENDING = "pending or requiring attention"
EXECUTION_COMPLETED = "completed"
EXECUTION_PENDING = "not completed for the agreed settings"
INCOMPLETE = "Your first evaluations remain incomplete. Tasks with unfinished work: {tasks}."
SUITE_APPROVAL_PENDING = "Your evaluations are awaiting suite review and approval. Their retained validation and execution evidence remains available."
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
RUN_ATTEMPT_COUNTS = (
    "{completed} completed attempts with current grades; "
    "{infrastructure} execution failures; {ungraded} completed attempts without grades; "
    "{retained} retained attempts in total; {unavailable} retained attempts with stale or unavailable evidence. "
    "Planned and missing attempt counts for this job are unavailable."
)
RUN_INVENTORY = "- **{run}** ({role}): " + RUN_ATTEMPT_COUNTS
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
SUITE_REVIEW_BLOCKER = (
    "- **Review the suite:** Current suite approval has not been recorded. "
    "**Needed:** Review the authored tasks in the HTML report or mirrored README, then approve the suite or request changes."
)
JOB_ERRORS = (
    "Retained jobs with execution errors: {runs}. Successful attempts from these jobs count only where their "
    "evidence is current. {recovery}"
)
RECOVERY_COMPLETE = "The selected recovery runs account for the remaining work."
RECOVERY_PENDING = "Run completion still requires attention."
RUN_COUNTS = "Run **{run}**: " + RUN_ATTEMPT_COUNTS + "\n\nAgent-quality failures: {quality}"
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
RESULTS_REPORT_LABEL = "HTML report"
RESULTS_REVIEW = (
    "Review your runs in the {report} (**Runs**) or the {readme}. "
    "Follow the linked evidence to inspect each attempt’s recorded responses or actions and grade."
)
GYM_RESULTS_REVIEW = (
    "Review your native Gym runs in the {readme}, following its evidence links for attempts, "
    "recorded responses or actions, and grades. "
    "The {report} summarizes your suite; its **Runs** view currently supports Harbor runs only."
)
AUDIT_QUESTION = (
    "Would you like to see how you could improve your suite further? We can audit its coverage "
    "and verifiers against the behavior described in `ETHOS.md` to identify gaps and worthwhile improvements."
)
BLOCKER = "- **{step}:** {reason} **Needed:** {needed}"
ADAPTER_BLOCKER_ID = "agent-integration"
ADAPTER_BLOCKER_STEP = "Connect your agent"
ADAPTER_BLOCKER_NEEDED = (
    "Use the prompt below in a separate chat with your coding harness, "
    "then return with the adapter and configuration paths and verification results."
)
ADAPTER_CAPTURE_NEEDED = (
    "Resolve whether and where to save local trajectories in its separate check-in after suite approval; "
    "the adapter prompt will include that choice."
)
ADAPTER_HANDOFF = """Open a separate chat with your coding harness in this repository and copy this prompt:

```text
Connect the existing agent to the selected evaluation provider. Use this saved
context, verifying its paths and interfaces against the repository:

{context}

Inspect the actual agent entry point, installed provider version, existing
adapters, and documented extension interfaces. Use the provider's official
documentation for that version. Implement the smallest supported adapter or
configuration that invokes the actual agent with the agreed task inputs and
returns its responses, ordered actions, errors, and observable state in the form
the existing verifiers expect. For a selected Gym-Harbor path, preserve that bridge.

Implement configurable local trajectory logging using trajectory_capture in the
saved context. When enabled, save each actual-agent attempt under the selected
directory with stable run/attempt identities and fresh paths that preserve prior
outputs. Keep required native results and baseline receipts at their existing
repository-local paths; this directory configures trajectory saving separately.
Harbor adapters must emit ATIF accepted by Harbor's trajectory parser.
Native Gym adapters must retain complete rollout records supported by the existing
Gym-to-ATIF converter; preserve original ATIF for a Gym-Harbor bridge. Scores or
custom action/reply logs alone are not sufficient for an Eval Author audit.
Keep this setting and directory in the generated configuration and rerun commands
so trajectories are saved when the user runs the evaluations independently.
When optional saving is declined, leave it disabled and explain any native logging
that cannot be disabled. Verify the saved format with the existing parser or
converter using offline fixtures; do not launch model runs to prove logging
without separate authorization. Report unsupported capture instead of claiming
trajectories were saved.

Preserve the agent's behavior, model and tool configuration, ETHOS.md, agreed
task/example mapping, fixtures, reference solutions, verifiers, and prior evidence.
Keep verifier-only data away from the agent. Isolate and reset task state between
attempts. Use only the explicitly selected tasks and examples; do not select tasks
by directory membership. Report any missing planned task files without creating
them. Do not substitute another agent, reimplement its decisions, or change what
the tasks measure to make the connection work.

Keep this as repository engineering work outside Eval Author. Do not modify its
workflow state or approvals, mark the integration verified, or claim an agent
baseline from adapter completion or control results. Agent/model evaluation runs
still require their own applicable authorization and limits.

Return the adapter/configuration paths, actual agent entry point, changed files,
setup and rerun commands, trajectory configuration and output locations, checks
actually performed (including trajectory parser/conversion checks), and unresolved dependencies.
Distinguish offline checks from any authorized native agent execution, and retain
fresh native outputs and exact run identities for execution actually performed.
```

Return here with those paths and results so I can recheck the connection and resume your saved evaluations."""
LINK_LABELS = {
    "report": "the report",
    "readme": "the suite README",
    "tasks_view": "Tasks view",
    "task_readme": "task README",
    "rerun": "saved run instructions",
    "task_rerun": "Task-specific run instructions",
}
