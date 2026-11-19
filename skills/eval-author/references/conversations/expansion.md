<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Expansion continuation

Use the [shared rendering entry point](../../scripts/conversation_renderer.py)
with `stage: expansion_continue` for an unresolved entry decision or
`stage: expansion_scope` for the selected authoring flow's existing scope review,
then `stage: expansion_progress` during agreed creation and validation and
`stage: expansion_review` for task review and execution approval, followed by
`stage: expansion_results` for post-run review.
Rendering does not construct tasks, validate verifiers, or authorize execution.
For an unselected improvement path, use the core's
[expansion entry](../../SKILL.md#expansion-handoff) before shared checks;
continuation retains an already-selected path and its unresolved decision.

## Selected audit improvements

The audit already records the recommended changes, and its handoff identifies
which ones the user selected. Reuse that report and the retained decisions;
do not offer to prepare another proposal or repeat the recommendation pass.

Immediately after retaining the selection and entering `expand`, render
`expansion_audit_opening` with the complete saved state and exact revision.
Supply `facts.selected_improvements` as an object mapping exactly the handoff's
selected recommendation IDs to their current report labels, plus `facts.limits`
when a support limit is already known. Show the returned Markdown unchanged in
commentary before eligibility checks, adapter investigation, or task-plan details,
then continue in the same turn. This opening needs no ready prerequisites or
planning lifecycle and asks no new question. It acknowledges only the saved
selection, without approving construction or execution. Resolve changed or
missing recommendation context before rendering; do not repeat the opening on
every planning reply or resume of an unchanged selection.

Check which selected changes have a supported authoring path. For an audit
tool-gap addition, continue at [task-create Step 2](../../../eval-author-task-create/SKILL.md#step-2-select-one-actionable-gap),
matching eligibility to the selected recommendation. Do not substitute an
unselected gap. Resolve only missing construction details at the existing scope
and design checkpoints, reusing applicable agreement and execution authorization.
An explicit request for additional planning keeps that scope and may use
task-create Step 1; selecting an audit recommendation does not request it.

For repairs within an existing authoring flow, use the
[repair and revalidation procedure](../task-validation.md#repair-and-retain).
The audit handoff does not add a general workflow for editing existing suite
cases or graders. Describe an unavailable implementation path as not yet
supported, retaining the audit's recommendation without offering to write it
again. For an unsupported verifier repair, use `UNSUPPORTED_GRADER_REPAIR` from
[expansion_templates.py](../../scripts/conversation_flows/expansion_templates.py).
Include known limits in the opening above without repeating them afterward. If a
limit is discovered later, explain it without another selection acknowledgment.
When all selected changes are unsupported, do not offer or enter task planning.
Keep any report-update failure in a separate paragraph. Do not apply this limitation to supported
authoring or repair work or describe it as a blanket prohibition on source edits.

`expansion_continue` presents a saved unresolved entry question; it is not a
prerequisite to the selected authoring flow. Use it only when an unanswered
entry input prevents that work from proceeding. If a retained entry question
repeats the audit's settled selection, replace it with the actual unresolved
decision through the revision-checked update below. Preserve the lifecycle and
update its question and the top-level `pending_question` together.

## Existing scope checkpoint

Use `expansion_scope` for the selected workflow's existing
[coverage-planning checkpoint](../coverage-planning.md), not a separate proposal
or approval step. Starter expansion keeps its
[existing owner](../../../eval-author-first-eval/SKILL.md#starter-expansion);
recorded interactions keep the [trace handoff](../trace-input.md), including
privacy review and admission before planning that depends on those inputs.

Save `expansion_state.new_scope(state, path=..., plan=..., question=..., source=...)`
as `scopes.expand.lifecycle` through the latest revision-checked update. Use
`starter_suite`, `audit_tool_gap`, or `recorded_interaction` for `path`. The plan
contains `summary`, `tasks` (`task`, `scenario`, `outcome`, `verification`),
`breadth`, `limits`, and `deferred`; the [validator](../../scripts/expansion_state.py)
owns its shape. Retain corpus/example selection and counts in `breadth` as saved
planning context. Present material counts and source/scenario choices in
`summary` or the task rows, and exclusions, attempt settings, and cost constraints
in `limits`; do not add a separate breadth section. Preserve unaffected scope fields.
Save the actual unanswered scope question in both lifecycle
`pending` and top-level `pending_question`; use no question when it is settled.

Populate `deferred` only with work the user explicitly selected or postponed
that remains outside this plan, including selected unsupported audit improvements.
Otherwise save `[]` so the section is omitted. Do not add speculative cases or
routine later authoring, validation, or execution steps. Keep relevant coverage
limits and exclusions in `limits`.

Reload normal state and render `expansion_scope` with its exact revision and
`facts.expansion_observations`. Record the user's actual answer as lifecycle
`decision: {kind, source}`: `approved`, `declined`, or `revise`. Approval or decline
clears both pending questions; revision retains the actual next scope question.
Reuse a prior applicable answer rather than asking again to populate this record.
Changes to the plan require the user's decision on those changes.

After agreement, use the [expansion templates](../../scripts/conversation_flows/expansion_templates.py)
to explain the remaining work before task review and agent execution. Preserve
completed work and current evidence on resume, retain unresolved inputs, and
honor explicit pauses. Describe planned work as future work; run approval does
not mean a run has started.

Resume an unanswered scope question through `expansion_scope`. After an answer,
continue the existing authoring workflow and leave its later questions with that
owner. Shared prerequisite and Ethos recovery take precedence. Task-create keeps
selection, eligibility, authoring, validation, and execution-authorization rules;
this checkpoint adds no construction command or evidence-validation layer.

## Creation and validation progress

After scope agreement, initialize the creation record below and set the first
task's actual starting activity before substantial environment preparation or
task construction. The `tasks` map may still be empty; do not wait for task files
or validation results before showing progress. On resume, reuse the saved record
and reconcile its current activity and evidence before continuing work.

Render `expansion_progress` and send its complete returned Markdown unchanged in
an assistant commentary message before that work begins. A renderer call, tool
output, or saved Markdown file alone is not a visible progress update. Use this
rendered task table instead of free-form construction narration. Repeat it when
a task, activity, validation result, or actual blocker meaningfully changes,
before a long-running validation check, and on resume before more work. The
coordinating agent owns these messages; delegated workers return milestones and
evidence so it can update the shared progress.

Continue authorized work in the same turn after progress updates; do not add an
approval pause. Use the rendered response as the final reply when its actual
unresolved question requires the user's answer. Shared prerequisite and Ethos
recovery still take precedence. Honor explicit pauses; otherwise pause only work
that depends on a blocker.

Save `creation` in the existing schema-2 lifecycle through the latest
revision-checked update. Its `tasks` map uses `expansion_state.task_key(row)` for
each agreed task row. Each entry retains the native task `path`, current `sha256`,
and `validation`, initially `null`. A validation record contains
`sha256`, `status` (`passed`, `failed`, or `blocked`), `source`, and retained
`evidence: [{path, digest}]`. Record the owning workflow's actual native-control
outcome, bound to the task revision, following [task validation](../task-validation.md).
A scaffold, filename, saved boolean, or intended repair does not establish a
passing result. The progress observer checks task and evidence freshness; the
existing workflow owns native validation, without a second receipt validator.
Controls do not establish agent performance or measured audit-gap closure.

Set `active` to the actually started task and activity as
`{task: <task key>, status: creating|validating|repairing|blocked, detail: <activity>}`,
or `null` when no work is active. Save `pending: {question, source}` only for an
actual unresolved question, keeping top-level `pending_question` in sync; otherwise
save `null`. Reload normal state and pass current `facts.expansion_observations`
to the renderer. Fixed copy belongs to the
[expansion templates](../../scripts/conversation_flows/expansion_templates.py).
Keep activity details concise: name the actual task, the work or blocker, and
the next step. Distinguish completed work from remaining work instead of
replaying completed steps on resume.

Account for every agreed task, including tasks not yet created. Keep partial
artifacts and failed evidence, and repair drafts only through the existing
[repair procedure](../task-validation.md#repair-and-retain) and its budgets.
After task or evidence changes, renew only affected validation. Retain historical
task-map entries after scope edits; they do not replace or reduce the current
agreed set. Return scope or cost changes to the existing scope checkpoint.
Derived `creation.ready_for_review` requires current passing validation for the
complete agreed set. It permits the owning workflow's authored-task review;
it does not accept tasks, approve their review, authorize agent execution, or
complete the journey.

## Task review and execution approval

Use `expansion_review` after creation to review the complete agreed set, including
partial or blocked tasks. Reuse the current report's Tasks view and each task's
[Input, Behavior under test, Expected result, and Verifier sections](../suite-readme.md#task-documentation);
do not create another review report or change their formats. Refresh the existing
HTML and Markdown reports before review and bind the refresh to the current
derived `review.digest`. Link the Markdown task report first and the HTML report
second, without a fragment; Tasks is the default report view. Passing controls
establish verifier validity, not agent performance. Missing artifacts or required
validation prevent approval, not an
honest display of unfinished work. Never substitute a ready subset for the
agreed set without an explicit scope revision.

Save optional `review` in the lifecycle through the revision-checked state
interface. `artifacts` contains `{digest, source, evidence}`, with retained
`{path, digest}` references to the existing `.eval-author/report.html` and
`.eval-author/report_markdown/tasks.md`. `presented` contains `{digest, source}`
for the actual delivered review. `decision` contains
`{digest, decision: approved|changes_requested|declined, source}` for the user's
actual answer. Bind these records to the current derived `review.digest`; fields
may be `null` while unresolved. Keep `pending: {question, source}` synchronized
with top-level `pending_question`, or `null` when no question is pending.
Present the review and wait for the actual decision before preparing execution.
Reuse applicable prior review and agreement with their real message sources.

A changes request repairs only newly authored drafts through their existing
owner and [repair procedure](../task-validation.md#repair-and-retain), followed
by affected native revalidation and renewed review. Requests outside the agreed
scope return to the scope checkpoint; this stage does not add an editor for
arbitrary existing suite cases or verifiers. Decline preserves the drafts and
evidence without treating them as approved or executed.

After current task approval, use the same stage to present concrete execution
settings and obtain separate bounded permission. Save optional `execution` with
`execution_settings` and `selection` using the existing
[selection contract](first-eval.md#saving-the-agreed-design-and-evidence) and
[execution settings fields](first-eval.md#stages-and-review-boundaries).
Set each selected case's `task_id` to `expansion_state.task_key(agreed_row)`,
including every agreed task.
Use the saved task scenarios and expected results to explain what the agent will
do and what counts as success. Write `execution_settings.validation` in plain
language, distinguishing completed task/verifier checks from remaining checks
on actual agent performance. Keep completed checks in the past tense. In
`limits`, retain the actual retry policy, step/time limits, and allowed or blocked
actions when known; resolve missing limits that affect permission instead of
inventing defaults. Keep schema/adapter diagnostics in the configuration evidence
unless they affect the run decision. Configuration links follow the run plan.
Keep `selection.authorization: {digest, source}` separate from task approval.
Save actual delivery as `execution_settings_presented: {digest, source}` and an
execution decline as `declined: {digest, source}`; these fields may be `null`.
Bind display, authorization, and decline to the current derived
`execution.selection_digest`. Save its actual unresolved `pending` question in
both lifecycle and top-level state. Reuse applicable authorization rather than
asking again; presenting or rendering settings never grants permission.

Render with only normal state, its exact revision, and current
`facts.expansion_observations`, as for the prior checkpoints. Task or native
validation-evidence changes reopen dependent review and execution authorization.
Execution settings, config, or input changes reopen authorization while retaining
unchanged task validation. On resume, show the current unanswered decision or
blocker without repeating settled questions. Current review and authorization
permit the existing authoring owner to execute within that permission; they do
not establish a run, results, acceptance, or journey completion.
Normal state suppresses stale approval questions until their evidence is current;
reopening scope clears later questions while retaining their decision history.

Finish report refreshes and state reconciliation before the final renderer call.
Send its returned Markdown as the entire final reply, preserving both report
links, the run plan, and its actual pending question. Do not replace it with a
hand-written recap or append configuration-check results after rendering. Follow
the [shared delivery contract](../conversation-rendering.md) for every review and
execution-settings response, including resume.

## Results review and task acceptance

After execution, inspect the current provider-native results, outputs, traces, and
verifier explanations. Recheck their binding to the authorized task revisions,
agent/model, settings, and exact native attempt identities using the owning
workflow's evidence checks. Do not substitute controls, earlier runs, or another
task's evidence. Preserve partial, failed, and superseded attempts in the suite
guide; missing or stale evidence is unavailable, never a passing result.

Use `expansion_results` with normal state, revision, and current
`facts.expansion_observations`. Supply `facts.attempts` as a transient list of
inspected results, not another saved lifecycle or report. Each row contains:

| Field | Meaning |
|---|---|
| `task_id` | The selected `expansion_state.task_key(agreed_row)` |
| `example_id` | Selected Gym example ID; `null` for Harbor's task-level attempts |
| `attempt` | One-based attempt number within the authorized selection |
| `status` | `completed`, `error` for execution failure, or `incomplete` |
| `score` | Actual score as text, or `null` when unavailable; never infer a missing score |
| `error` | Actual execution error, or `null` when none is recorded |
| `output`, `trace`, `verification` | Verified absolute paths to this attempt's retained output/actions, full trace, and verifier explanation; `null` when unavailable |

Resolve links to real files under this repository's `.eval-author/`; do not guess
provider filenames. For Harbor, these can be `artifacts/output/interaction.json`,
`agent/trajectory.json`, and `verifier/checks.json` when actually retained. For Gym,
link the native rollout and explanation that contain the selected example and
attempt, retaining its exact identity in the suite guide. The caller owns evidence
inspection and freshness under the shared rendering contract. The renderer
lists every authorized attempt, including missing results, and never accepts a
task or treats a score as task validation.

Save the actual post-run decision question in `execution.pending` and top-level
`pending_question`. Use `RESULTS_QUESTION` from
[the templates](../../scripts/conversation_flows/expansion_templates.py) for the
standard question, then deliver the complete rendering unchanged and wait for
the user's answer. Do not reuse task-design approval or run permission as final
acceptance. Feedback that changes a draft follows the existing repair/revalidation
procedure and reopens affected approvals; a pause preserves Draft and evidence.

Only after explicit user approval of the current results for named tasks, and
all of their owning workflow's technical acceptance checks, record those tasks
as **Accepted** through [Task status](../shared-prerequisites.md#task-status).
Use the user's actual post-run reply as `source`. Bind the inspected native
results, required validation evidence, and the authorized selection's configuration
and input files (`selection.config` and `selection.inputs`) with `--evidence`.
Verify that the native result's agent/model and execution settings match that
selection; a prior Accepted decision for other settings cannot approve this run. Passing checks or a technical
verification JSON with `accepted: true` alone leaves new tasks **Draft**. Recheck
changed or missing task/evidence before acceptance; new results require renewed
user review. Accept only the approved subset, keeping other tasks and their
unanswered review visible. Refresh the existing reports and suite guide after
the decision without changing finalized task content.

After the user accepts the results for the complete agreed set, save
`execution.results_review` as `{digest, source}`: use the current derived
`execution.selection_digest` and the actual post-run reply, clearing the answered
`execution.pending` and top-level `pending_question` in the same save. This optional field
binds the final review to the authorized tasks, agent, and settings; it does not
replace individual task acceptance or the owning workflow's technical checks.
Clear it when changing tasks, scope, settings, selection, or authorization; the
same save may clear the obsolete review and its pending question during repair.
Before asking permission for any rerun, clear both `results_review` and
`selection.authorization`, then save the new permission question in
`execution.pending`. Reuse the current settings display when unchanged; render
`expansion_review`, wait for the actual permission reply, and bind the new
authorization before execution. Retain earlier results as history; new results
need a new user review. Do not reuse a pre-run
decision's source or save this review together with initial run authorization.

## Completion choices

After the user has explicitly accepted the current results for every task in the
agreed plan, reload normal state and use `expansion_completion`. Its
`expansion_observations.acceptance` comes from the existing task-lifecycle checks;
missing, changed, or unaccepted tasks and a missing or stale `results_review`
prevent this checkpoint. Keep the plan,
run selection, and acceptance evidence unchanged while asking what to do next.

Always offer an audit of the updated suite or finishing. Include other audit
improvements only when the originating report has supported, unselected work:
inspect its current validated recommendations, exclude all already-selected
items and those with recorded follow-through, and check each remaining item's
eligibility through the existing authoring workflow. A `new_task` label alone
does not establish support: the audit-gap generator still requires an actionable
measured tool gap. Do not offer arbitrary existing-task or verifier repairs,
manual-design recommendations, or work whose eligibility is unknown. This is a
filter of existing recommendations, not a second recommendation pass.

Supply `facts.remaining_improvements` as an ordered mapping of the verified
remaining recommendation IDs to their scenario labels, or `{}` when none are
eligible (including direct entry without an audit). The caller owns report
validation, source freshness, and support checks under the shared rendering
contract. If the originating report is stale or unavailable, omit its
recommendations and preserve that limitation with the retained audit context.
Save the corresponding completion question from
[the templates](../../scripts/conversation_flows/expansion_templates.py) in
`execution.pending` and top-level `pending_question`, with its actual source,
before rendering. Deliver the complete response unchanged and wait for the
user's choice; start no audit, additional construction, or agent run automatically.

On a reply, clear the answered pending question through the existing
revision-checked update. Finishing records `journey_status.expand: complete`
only while all agreed tasks remain accepted. On resume, route saved results-review
checkpoints to `expansion_completion`, using freshly reconciled observations and
remaining-improvement facts for an unanswered choice. A finished expansion uses
the same renderer to acknowledge finishing without asking the answered question
again. Follow the [shared onboarding observation contract](onboarding.md#expansion-observations)
when presenting shared recovery or resuming through onboarding. Supply current
`facts.expansion_observations` to `expansion_continue` as well.
Stale acceptance routes to `expansion_review`; a changed, unresolved scope routes
to `expansion_scope`. Resolve that checkpoint before presenting completion.
An audit choice uses the existing
[return-to-audit route](../shared-prerequisites.md#return-to-an-audit), preserving
the task and run evidence. Selecting another recommendation uses the existing
reviewed audit next-action handoff and scope checkpoint; do not extend the
current agreed task set silently. A follow-up audit must reassess findings and
coverage from current evidence; passing task runs alone do not prove improvement.

## Shared checkpoint contract

Extend this flow at real decision boundaries using the following ownership and
authority rules. Each owner delivers its lifecycle fields, transition gates,
renderer and fixed-copy templates, fixtures, and resume tests together. The
checkpoint names below describe responsibilities; they are not registered stage
names or accepted lifecycle fields. Missing decisions or evidence remain
renderable as unresolved, pending, or blocked work. The gates below control
advancement and success claims, not whether an incomplete checkpoint can be shown.

| Checkpoint | Owned state and advancement gates |
| --- | --- |
| Unresolved entry/path inputs and supported scope agreement | Retain the request or audit selection and resolve the supported creation path. Bind explicit scope agreement to the exact proposed task set; an audit selection alone cannot approve construction. Save decline or revision without approval. |
| Creation/validation progress and blockers | Agreed scope permits construction. Current task artifacts and applicable native evidence establish validation. Preserve partial work and blockers; drafts and intended repairs do not establish validation. |
| Generated-task review and execution settings/authorization | Present the complete agreed task set and its current validation for review. Require explicit task review and separate authorization bound to the exact task set, agent, and settings before execution. |
| Execution progress, results, rerun, and continuation | Explicit execution authorization permits a run. Current actual-agent evidence bound to the selected tasks/settings establishes run status and results. Keep missing or partial results visible and require a new decision for changed run scope or settings. |

Reuse onboarding for shared prerequisite and Ethos recovery, and the audit
workflow's retained handoff for selected recommendations and originating report
context. Entry and scope agreement consume that selection without creating a
second recommendation pass. These checkpoints orchestrate existing supported
authoring workflows; they do not extend which cases or graders can be changed.

All checkpoints use the existing state and rendering interfaces:

| Interface | Responsibility |
| --- | --- |
| `expansion_state.record` / `validate` | Read and validate the versioned lifecycle under `scopes.expand.lifecycle`; preserve sibling scope fields and legacy context |
| `expansion_state.observe` | Inspect only artifacts required by the implemented checkpoint, outside rendering, with bounded observations tied to repository and revision |
| `expansion_state.derive` | Compute current progress, unresolved decisions, and evidence validity from supplied state and observations without I/O or mutation |
| `expansion_state.transition` through `journey_state.update` | Validate a proposed transition against current context and the exact expected revision before saving a real user decision |
| `conversation_renderer.render` | Dispatch a registered checkpoint and render its complete response from the reconciled snapshot, exact revision, and permitted facts |

The lifecycle schema version identifies the persisted record shape so unsupported
records fail validation instead of being interpreted as current decisions.
Schema version 1 retains the entry record described below. Schema version 2
saves the selected authoring workflow's existing coverage-planning checkpoint
through `expansion_state.new_scope`: a plan for display, its actual user decision,
and an optional unresolved question, with optional
[creation and validation progress](#creation-and-validation-progress) and
[task review and execution approval](#task-review-and-execution-approval). The
[validator](../../scripts/expansion_state.py) owns the exact presentation fields.
Task-create continues to own gap selection, authoring, native validation, and
acceptance; starter expansion and trace mode retain their existing workflows.
There is no additional construction approval or command. Once its question is
answered, this checkpoint leaves later authoring questions with their owner.
Preserve shared prerequisite/Ethos recovery and the existing context bindings.
Do not save a parallel checklist, introduce another state file, or register
future-stage placeholders. Reuse shared formatting and labels.

## Cross-checkpoint invariants

Keep audit selection, construction scope agreement, generated-task review, and
execution authorization as separate decisions with their own source and content
bindings. Saved questions and settled decisions survive resume. A renderer call,
presentation fact, filename, routing handoff, or generic completion flag cannot
create any of these decisions or establish evidence validity.

Reconcile changes before presenting the next checkpoint. Retain the audit
handoff, prior decisions as history, partial artifacts, and unaffected reusable
work. Reopen only decisions and checks that depend on changed inputs:

| Changed input | Required reconciliation in its owning workflow |
| --- | --- |
| Selected provider, confirmed intent, or selected audit handoff | Reconcile affected entry/scope decisions and their downstream bindings; retain the originating context for review |
| Planned task set or construction scope | Reopen scope agreement and approvals bound to that set; retain reusable tasks and evidence for unchanged work |
| Task or grader content | Recompute validation and reopen dependent task review, execution authorization, and run/result validity for the changed tasks |
| Validation or run evidence | Recompute the statuses, reviews, and approvals that depend on that evidence; missing or stale evidence cannot remain current |
| Agent or execution settings | Reopen execution authorization and dependent run/result bindings; retain task construction and validation that remain applicable |

An approved subset cannot silently replace the complete agreed set. Record an
explicit scope revision before treating a reduced set as the agreed work.
Declining execution preserves validated tasks and their review without implying
a run. Native validation controls do not count as actual-agent runs; a completed
run does not establish passing results or measured audit-gap closure. Derive
each claim from its applicable current evidence. The entry record always derives
`complete: false`.

Each stage extension must cover unchanged resume, approval/decline/revision,
partial or blocked work, stale revisions without writes, and targeted
invalidation. Keep fixture-backed byte-identical rendering checks separate from
an end-to-end assistant demonstration: fixtures prove supplied-state rendering
and gates, while a demonstration records the assistant's actual workflow choices.
Neither establishes deterministic task generation or evaluation results.

## State authority

Reuse the normal reconciled state from `journey_state.py show`, never
`--state-only`. Entry requires complete shared prerequisites and Ethos. Scope
review uses the selected authoring flow's requirements, including trace-source
mode's existing scoped Ethos exception; resolve its applicable blockers first.
The expansion lifecycle is versioned
under `scopes.expand.lifecycle`; existing audit handoff, legacy context, and
other scope fields remain available alongside it. No additional state file is
introduced.

Saved intent questions block entry even if older state marks Ethos complete.
Reopen `ethos.stage: review` through a revision-checked update, save the actual
unresolved intent question and `next_handoff: ethos-orchestrate`, then use shared
Ethos recovery. Retain the expansion lifecycle and audit handoff while those
questions are resolved; confirming the unchanged document restores its entry.

After inspecting the request and retained context, create the entry record with
`expansion_state.new_record(state, question=..., source=...)`. The question is
the actual unresolved entry decision, and its source identifies the request or
context that requires it. Save the record and the same top-level
`pending_question` through the existing revision-checked update interface.
Choose that question using the core's [expansion handoff](../../SKILL.md#expansion-handoff)
and the selected authoring flow's eligibility rules. Retain unsupported selected
improvements as context and explain their implementation limit; do not frame
them as available construction choices. Explain limits only when relevant to
the selected work; do not claim construction scope is pending when it is agreed.
Loading legacy scope text does not infer a lifecycle or approval. Introducing a
structured lifecycle retains that text as `legacy_context`.

The audit handoff leaves construction scope unconfirmed and marked for review.
Preserve those flags during entry planning; they do not invalidate the entry
record's provider, Ethos, and handoff bindings or establish scope agreement.

The record binds the selected provider (`setup`), confirmed Ethos content, and
the saved audit handoff. Older entry records without a provider binding remain
readable but require explicit reconciliation through `new_record` before
continuation; never infer their provider from the current setup. Consume the
handoff supplied by the audit workflow; do not edit its selection from the
expansion workflow. The binding identifies saved handoff context, not fresh
audit measurements or report validity. Consult the originating audit records
before relying on their findings. This checkpoint inspects only Ethos content;
it does not inspect tasks, source traces, or native receipts.

## Rendering and recovery

After saving, reload normal state and pass the complete `state`, exact
`revision`, and `facts.expansion_observations` from the same `show` result.
`expansion_state.observe` obtains current Ethos content outside rendering;
`derive` checks supplied observations without I/O. Entry shows the shared
checklist and saved question. Scope shows the saved plan and actual unresolved
question, without a generic selection preamble.

Render and present the complete response unchanged. Wrong revisions, missing
observations, mismatched pending questions, active blockers, and changed context
reject continuation. These are agent-facing validation failures: the CLI returns
exit code 2 with a JSON error on stderr and no rendered response or state write.
Reload stale input or reconcile the indicated state before rendering again.
Resolve shared blockers through onboarding; present the actual blocker or pending
decision to the user, rather than the raw exception. If recovery cannot proceed,
explain what is missing without claiming continuation succeeded. Reconcile a
changed provider, Ethos, or handoff and save the relevant entry question with fresh
bindings; do not manufacture a success state or render stale decisions.
Unchanged entry decisions retain their question across normal resume and journey
switches. Shared recovery questions take precedence while prerequisites or
blockers remain unresolved. After recovery clears its question, normal state
inspection restores the retained entry question only when its bindings are
current; state-only inspection never performs that artifact reconciliation.
Successful Ethos confirmation also restores a current entry question after an
unchanged review when no active blockers remain.
