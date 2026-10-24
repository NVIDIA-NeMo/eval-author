---
name: eval-author-first-eval
description: >-
  For a fresh start, read the eval-author core skill at ../eval-author/SKILL.md,
  use its pure conversation renderer for orientation and end the turn before
  repository inspection or prerequisite work.
  Confirm ETHOS.md, plan evaluation tasks, and build first evals or
  expand a starter NeMo Gym or Harbor suite while explaining
  its parts. No prior traces or coverage reports are required. Missing selected
  runtime blocks shared onboarding before ETHOS.md and task planning.
triggers:
  - help me build my first evals
  - my agent has no evals yet
  - expand the starter eval suite we created
  - create an evaluation suite from scratch
not-for:
  - eval-author (use for the shared standard and routing)
  - eval-author-task-create (use for measured audit coverage gaps or ATIF-source tasks)
  - eval-author-discover (use to check an existing suite)
compatibility: >-
  ETHOS.md is required before evaluation design and is saved and checked locally
  in the user's repository. No NeMo service, account, platform CLI, or upload is needed.
  Shared onboarding requires the selected runtime. NeMo Gym and Harbor authoring need the Python/CLI
  pairs verified by eval-author’s shared locator and runtime-policy.json.
  Keep their runtime environments separate. Execution may require Docker,
  an agent adapter, and provider credentials.
maturity: alpha
license: Apache-2.0
user-invocable: true
allowed-tools: Bash Read Write Grep Glob
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Eval Author: first evaluations

## ATIF as a starting source

For a user-selected ATIF trace, follow the shared [ATIF source handoff](../eval-author/references/trace-input.md).
Confirm `ETHOS.md` and admit the selected source before constructing its task;
preserve privacy decisions, provenance, and the environment-kit handoff. A rejected
source remains a recorded outcome, not a reason to invent a replacement task.

## Purpose and conversation

Read the [core skill](../eval-author/SKILL.md) for shared boundaries and onboarding.
Fresh invocations use its pure onboarding renderer and stop at its orientation
checkpoint. Complete the saved state-only prerequisite pass before target
`ETHOS.md` inspection, then reconcile artifacts normally. Reuse current
[ETHOS.md confirmation](../eval-author/references/ethos-confirmation.md)
and source choices after the shared completion pause; continue only
after the user's reply. A stated absence of evaluations does not require discovery.

Introduce **evaluation tasks**, then use **tasks**. Name the behavior document
`ETHOS.md`; use **first task**, distinct input **examples**, and repeated
**attempts**. Do not use numbered journey labels or call the first task a pilot.
Keep provider-native identifiers when discussing their files or commands.

Use the [first-evaluation conversation contract](../eval-author/references/conversations/first-eval.md)
with the shared renderer for every creation checkpoint. Its template module owns fixed
copy, table columns, question-tool choices, settings labels, and closing order.
Present the rendered output unchanged rather than copying those templates here
or adding routine policy explanations and duplicate checklists. Dynamic task
content and evidence interpretation still require judgment.

The structured record and `first_eval_*` stages below apply to `create`. For an
active starter expansion, retain `expand` and follow [Starter expansion](#starter-expansion)
without routing its progress through the creation record.

Save creation progress under `scopes.create.first_eval` through the existing revision-checked
[state interface](../eval-author/references/shared-prerequisites.md#save-and-resume).
The conversation contract defines the additive record and observed facts used
by its stages. Save actual user decisions before acting; a side question is not
approval. Reuse applicable answers and authorization without restarting onboarding.

### Confirm a saved design before reusing it

When a fresh request finds a saved creation design, first establish whether the
user wants that design. Follow the [saved-design checkpoint](../eval-author/references/conversations/first-eval.md#saved-design-checkpoint)
after the shared completion pause and continuation. It owns the separate user
decision, renderer pause, retained artifacts, and revision-checked updates.
Confirmation of `ETHOS.md` does not select a saved design.

## 1. Select the task sources

Use confirmed `ETHOS.md`, the agent entry point, tools, and usage documentation.
Existing traces are optional. If the choice is unresolved, use `first_eval_traces`
and its exact choices as the final reply, then end the turn. Wait for the user's
answer before trace inspection, task planning, environment work, or integration
investigation. A nonblocking question tool returning does not supply that answer;
do not keep working while it is pending. Reuse an already established choice.
Clarify locations, selected runs, bounds,
and exclusions before inspection, following [source selection](../eval-author/references/coverage-planning.md#select-trace-evidence-before-using-it).
Do not search unselected traces or silently treat repository fixtures as the
chosen corpus. Save the selected sources or explicit no-trace choice and mark
authored inputs as synthetic.

Preserve the selected supported runtime and existing prerequisite evidence. A
configured Gym–Harbor path needs verified integration; conversion or two installed
CLIs is insufficient. Recheck only affected prerequisites through the [runtime locator](../eval-author/references/runtime-prerequisites.md#runtime-prerequisite-checks).
Do not switch providers or add a production adapter to bypass missing integration.
Missing run access does not prevent independent design or authoring.

## 2. Propose and agree on the tasks

After the source answer is saved, propose a small starting subset: one to three
tasks with one distinct input example per task by default. Choose fewer when
that is sufficient; do not turn `ETHOS.md` into an exhaustive suite. A broader
initial proposal requires the user's explicit request, retained as
`plan.breadth_request.source`, rather than an inferred preference or invented
approval. Keep additional ideas out of this initial agreement.

Derive concrete requests, expected behavior, outcomes, and grading from `ETHOS.md`
and selected evidence. Include useful positive, negative, difficult, and boundary
examples at the agreed breadth. Explain why the tasks help, what remains untested,
and which will be built first. Do not use a model's current answer as grading
truth or replace subjective quality with brittle wording checks.

Reuse prerequisite findings and the selected source to make the proposal.
Read only the additional agent interface or selected evidence needed to describe
its input, observable result, and grading. Defer adapter investigation, hook
design, environment preparation, and runtime implementation until design approval;
unresolved execution details do not prevent this design review. Consult
[execution dependencies](../eval-author/references/execution-dependencies.md) and
[environment preparation](../eval-author-environment/SKILL.md) when that work is due.
Retain an untestable requirement as blocked; only the user can change its scope.

Save the proposed design and render `first_eval_proposal`. The initial review
contains the task table, scope explanation, chosen first task, and final feedback
question defined by the template. It contains no execution-settings checklist,
run-only blockers, or spending request. If a design blocker prevents a meaningful
proposal, use `first_eval_blockers` instead of offering an approvable design.
The template explains that approval leads to building the named first task and
pausing for its review before the remainder. Emit this proposal as the final
reply and end the turn. Do not continue inspection, delegate implementation,
or generate task files while awaiting the user's response.

Incorporate feedback until the design is approved. Record actual approval for the
current plan digest before any scaffolding, fixtures, instructions, graders, or
delegated authoring. Do not create a separate user-facing planning document.
An approved design is not paid-execution authorization. Expansion preserves
retained tasks and source choices and reviews only affected design changes.

## 3. Build and review exactly the first task

After design approval, proceed directly to `first_eval_prepare_first`; ask only
about unresolved environment or design choices needed for this work. No new
setup-approval stage or execution-settings display precedes authoring.
Initialize the saved build activity and use the [build-progress contract](../eval-author/references/conversations/first-eval.md#first-task-build-progress)
for concise renderer checklists throughout authoring, including environment work
and delegated work. These progress updates continue the build; they are not
additional approval pauses.

Read the selected [Harbor](references/harbor-first-eval.md) or [native Gym](references/gym-first-eval.md)
reference. Create exactly the agreed first task directly in its permanent
`.eval-author/tasks/<slug>/` directory. Record the native scaffold as **Draft**
through [Task status](../eval-author/references/shared-prerequisites.md#task-status).
Do not generate remaining tasks or Gym rows before first-task approval, including
through delegation. A reviewed native Gym task may contain several agreed rows.

Complete and prove the [environment kit](../eval-author-environment/SKILL.md)
before writing the task's grader or reference solution. Its realistic starting
data, observable state, isolation, and smoke evidence establish environment
readiness; task controls never prove the kit. Build on that proven kit and keep reference
answers and verifier-only information outside the agent's initial environment.

Use [private construction declarations](../eval-author/references/construction-plan.md)
for grading authority, authored or copied inputs, and observable checks. Validate
and bind the construction plan before recording native controls. Preserve
correct inaction and nonbinary rewards when required by the task.

Finish the task README following [Task documentation](../eval-author/references/suite-readme.md#task-documentation),
including input, behavior, expected result, grading, a meaningful limitation,
and setup/rerun instructions. Finalize it before revision-bound execution evidence;
put later run results and evidence links in the outer suite README.

Generate the report and Markdown mirror using [Report generation](../eval-author/references/suite-readme.md#report-generation)
before `first_eval_review_first`. Link the actual Tasks view and task README,
and report environment proof, grading validation, and actual-agent execution
separately. Formal grading controls may still be pending once the environment
is proven and review artifacts exist. Missing required artifacts block review.

The report's Runs table currently reads native Harbor jobs; native Gym evidence
remains linked from the suite README. Keep planned-but-uncreated tasks, separate
review/validation/execution findings, exact attempt navigation, model identity,
and evidence limitations explicit in the saved summary. Do not claim missing
report capabilities exist; record required report-owner work as a blocker or
limitation instead of inventing UI behavior.

Request approval of the current first-task revision. Apply requested changes in
the same directory, retain historical evidence, invalidate affected observations,
refresh artifacts, and request review again. First-task approval covers the
reviewed approach; it does not claim individual review of every later task.

## 4. Build and validate the remainder

Only after current first-task approval, render `first_eval_prepare_remaining`
and build the remaining agreed tasks with the approved pattern. Reuse applicable
proof and controls, refreshing the report after authoring, revisions, and validation.
Keep task paths stable; a blocked first task never unlocks remaining authoring.
Follow the [remaining-task build checklist](../eval-author/references/conversations/first-eval.md#remaining-task-build-progress)
for saved activity and compact renderer snapshots throughout this work, including
delegation. Keep the skill labels beside each checklist item.

Follow [task validation](../eval-author/references/task-validation.md) and
[execution recovery](../eval-author/references/execution-recovery.md). Finalize
task files and README instructions before preparing immutable evidence. Use
provider-native reference checks and relevant incorrect-result controls, including
valid alternatives and state preservation where applicable. Keep controls separate
from target-agent attempts. Reference checks establish that tasks work, not the
agent's quality. Do not import audit gap closure or two successful agent attempts.

Preserve failed and superseded evidence under fresh output names. Task changes
reopen affected approval and validation; configuration changes reopen affected
execution claims. Unknown or missing evidence never establishes completion.

## 5. Agree on and execute concrete runs

Use `first_eval_execution_settings` only after first-task review approval when
concrete agent runs or paid grading are ready. The conversation contract owns its
settings checklist; record the actual target/model, task/example mapping, repeats,
validation work, time/cost bounds, and unresolved questions. Mark costs unknown
without supported estimates. Settings are separate from design and never gate
first-task generation or review. They may precede paid validation when needed;
otherwise finish local validation first. Reuse applicable previously displayed
settings and prior authorization without another generic approval gate.

Paid work requires explicit authorization within its concrete displayed bounds.
Design approval, first-task approval, or positive feedback alone grants no spending
consent. Ask only about the missing authorization or execution setting, preserving
independent work. Do not ask again when current authorization already covers it.

Use the actual repository agent through its supported integration. Save and
inspect explicit provider-native selection, agent/model settings, attempts, reset,
and fresh output paths. Harbor uses `.eval-author/run.yaml`; directory membership
never selects tasks. Native Gym retains native composition, complete selected
dataset rows, and repeats. A Gym–Harbor run must use its configured bridge.
Never substitute another agent or direct Harbor execution for that requirement.

After successful checks, render `first_eval_ready_to_run`. Announce execution only
when current authorization, selected configuration, and actual access are ready;
otherwise resolve the one missing decision. Run through the selected provider and
retain native outputs, rewards, errors, commands, and exact job/attempt identities.
A low score is a valid baseline; absent or diagnostic-only execution is not.

## 6. Review evidence and hand off

Inspect an informative actual-agent attempt. Explain its input, observed actions
or response, grade, available agent/model identity, evidence navigation, and
material limitations. Save the exact reviewed run and attempt; later runs must
not silently change that identity. Separate completed attempts, agent-quality
failures, infrastructure failures, and missing attempts in the reply and saved
summary. Do not invent traces or model-health information.

Maintain [review and rerun guidance](../eval-author/references/suite-readme.md#review-and-rerun-guidance)
in the suite README. Link the verified rerun heading as the template requires,
with actual full-suite and task-specific instructions. Verify commands and
navigation against the installed provider and retained evidence. Keep later
results outside finalized task directories so they do not change task digests.

Reconcile agreed, created, validated, and executed tasks and examples. Record
**Accepted** only when final-revision controls, configuration, baseline, and result
review support that decision through [Task status](../eval-author/references/shared-prerequisites.md#task-status).
Keep incomplete tasks **Draft**; low agent scores do not prevent acceptance of
functioning tasks. Report refresh observes evidence and never accepts tasks,
changes results, or advances workflow state.

Only after all agreed work and required artifacts are complete, render
`first_eval_results` with its completed checklist and saved pending audit invitation.
Follow the [report closing sentence](../eval-author/references/suite-readme.md#report-generation)
after the invitation. When creation is incomplete, lead with unfinished work
and actual **Blockers**, preserving progress.

Wait for the user's audit decision. Acceptance enters the existing audit flow
with confirmed `ETHOS.md`, task paths, provider, exact reviewed run/attempt,
selected sources, evidence limits, and preferences. Reuse unchanged prerequisites
and resolve only audit-specific or changed needs. Missing audit evidence blocks
only the audit; never silently run evaluations to create traces. Declining keeps
the completed suite and does not start or repeatedly offer an audit. Audit consent
does not authorize more tasks, agent edits, or additional paid runs.

## Starter expansion

Keep the existing `expand` goal and `scopes.expand`; do not reinitialize creation
or require a new `scopes.create.first_eval` record to continue. Follow the core's
[expansion handoff](../eval-author/SKILL.md#expansion-handoff) and [coverage scope](../eval-author/references/coverage-planning.md#expand-and-reconcile).
Reuse the confirmed `ETHOS.md`, selected corpus, provider, reviewed task pattern,
accepted task paths, and applicable approvals and evidence. Recheck only changed
prerequisites; an audit is optional rather than an entry gate.

Review retained and added tasks, distinct examples, grading, breadth, exclusions,
and meaningful difficulty changes before generating additions. Record the actual
scope agreement in `scopes.expand`. Apply the authoring, environment, construction,
first-task review, validation, explicit selection, and evidence standards above
without invoking the create-only `first_eval_*` stages. Reuse an already-approved
first-task pattern where it remains applicable; reopen only affected review and
validation claims rather than requiring new approval of unchanged tasks.

Use the provider references for native authoring and execution, preserving main's
Draft/Accepted lifecycle and finalized task documentation. Show concrete execution
settings when paid work is ready and reuse existing bounded authorization. Update
the suite README/report outside validated task trees, reconcile retained versus
added work, and retain the existing expansion progress and handoff. Do not claim
that the create-only renderer or record establishes expansion completion.

## Blocked work and resume

Use the flow's blocker stage for actual current-step blockers, with the cause,
blocked action, and specific needed answer or action. Retain run-only limitations
without presenting them during independent first-task preparation and review.
Pause only dependent work. Do not request secret values or fabricate approval,
delivery, validation, or execution observations. Resume from current saved progress
with applicable source choices, task revisions, and authorization intact after
the saved-design choice above when entering from a fresh request.
