<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# First evaluation conversations

Use the [shared renderer](../../scripts/conversation_renderer.py) for creation
responses after the shared prerequisites and confirmed `ETHOS.md`. The fixed
copy lives only in
[first_eval_templates.py](../../scripts/conversation_flows/first_eval_templates.py).
Present its output unchanged, following the shared contract for response order
and any explanations separately required by the host.

## Saved-design checkpoint

A fresh creation request may find an earlier design without asking to resume it.
After the shared prerequisites, `ETHOS.md` confirmation, and completion
continuation, register this unresolved choice before using its source selection
or proceeding with authoring. Use normal revision-checked updates, preserving the
existing record and artifacts. Set its optional `saved_design_review` to:

```json
{
  "digest": "<current derived plan_digest>",
  "decision": "pending",
  "request_source": "<actual fresh request reference>",
  "source": null
}
```

Render `first_eval_saved_design` as the final response and end the turn. The
renderer summarizes the saved tasks, distinct examples, source selection,
historical design approval, and evidence-backed progress before its fixed
choice. It does not inspect trace contents or require execution settings.
Neither `ETHOS.md` confirmation nor the shared continuation answers this choice.
Do not turn a historical continuation reference into a reply in the current chat.

Save the actual response in `source` and change `decision` to `continue`,
`revise`, or `new`. Continuing must refer to the same current `plan_digest`;
if it changes before the answer, present the updated saved design again.
Continuing reuses applicable approvals, evidence, and authorization; it grants
no new spending permission. Revising reopens proposal approval while retaining
the source choice. A new proposal also reopens source selection. Both retain
prior task and run records and historical evidence; choose stable new task IDs
and directories when building new tasks rather than overwriting earlier work.
Retire any old audit invitation and handoff into history when revising or
starting a new proposal, so it cannot route or suppress the new completion handoff.
Source changes and a replacement plan still require their ordinary checkpoints.

Skip this choice when the current user request explicitly identifies the saved
design to resume, or when it was already answered during ongoing work. Do not
re-prompt on every turn or treat every legacy record as unapproved. Missing
`saved_design_review` remains readable, but it does not establish the user's
intent on a fresh request. Once pending, the state helper and renderer block
dependent stages until the saved-design choice is resolved.

## Source and proposal pauses

Source selection, saved-design confirmation, and task proposals start with the
shared overall progress checklist. It shows the completed prerequisite and
`ETHOS.md` steps and marks creation as current before the decision content.
Keep its heading and current-step marker when presenting the rendered response.
This context does not introduce build steps, execution settings, or a new pause;
run-only blockers do not prevent independent design review.

For fresh creation without a structured saved design or source choice, the shared
[confirmed handoff](onboarding.md#stateful-responses) includes the canonical source
introduction, question, and choices from `first_eval_templates.py`, immediately
after document confirmation and the overall checklist. Save that canonical
question as `pending_question` and `eval-author-first-eval` as `next_handoff`
before rendering, then end the turn and wait for the source answer. Do not
insert a readiness question before it or render `first_eval_traces` again after
the user has answered it. Legacy continuation questions remain readable and
render as that same combined checkpoint when the fresh condition applies.

Otherwise, after the applicable shared continuation or saved-design decision,
render `first_eval_traces` as the final response when the source choice remains
unresolved, then end the turn. For a standalone checkpoint without a saved
design, supply only `stage: first_eval_traces`, the fresh reconciled `state`, and
its `revision` to the shared renderer. No proposal record, task planning,
observations, or execution settings are needed first.
When a saved design remains after choosing a new proposal, also supply its fresh
`facts.observations` as usual. The canonical question and choices come from the
template module.

For either checkpoint, present the complete output, including `Next Steps`, both
completed shared steps, and the current creation step before the source question.
Collect the user's next reply to this rendered checkpoint; do not move its source question
into a separate question tool. Do not continue planning or investigating
integrations while the answer is pending. Reuse a saved choice instead of asking
again.
Use the shared entry point for the proposal too; its internal `proposal` helper
omits the checklist and state gates. Do not add free-form previews of task scope
or adapter implementation in commentary.

Save the actual source answer and its provenance, then clear the pending source
question. For existing traces, resolve the selected location, runs, and exclusions
before inspection. Then prepare only
the bounded design needed for `first_eval_proposal`: by default one to three
tasks with one example per task. Larger task or example counts require the
user's explicit broader request in `plan.breadth_request.source`. This optional
field binds the proposal to that request; it does not grant approval or spending
authorization. Existing records remain readable and previously approved scope
is retained.

The proposal states the counts, shows the task table and coverage limitations,
names the first task, and explains the one-task build/review pause before the
remainder. Its fixed feedback question is the final content. End the turn and
wait for actual design approval before implementation or further integration
work. Source selection and readiness to continue do not approve the design.
No additional planning document or approval checkpoint is needed.

## Grader validation before authored-task review

Before `first_eval_review_first`, validate the first task's complete grader. Before
`first_eval_review_suite`, every agreed task must have current grading-validation
evidence. Follow [task validation](../task-validation.md): known-correct results
and permitted valid alternatives must pass, while relevant incorrect results
must reach grading and receive the expected rejection. Bind retained native
control evidence to the current task revision and declared checks.

When grading uses a model to judge reply meaning or another semantic requirement,
the controls must exercise that actual judge as configured. Writing its rubric,
checking output structure, passing environment smoke checks, or running only the
deterministic portion cannot establish full grading validation. A substitute
judge, hardcoded verdict, skipped semantic check, or target-agent run does not
satisfy this gate. Task revisions reopen affected validation before review.

Reuse an existing compatible judge configuration, runtime, access, and explicit
authorization within its applicable bounds. Do not guess a model, incur
unauthorized spend, add an integration to bypass missing support, or move the
model, budget, or execution-settings questions before whole-suite approval.
If required judge configuration, access, or authorization is absent, show
**Blockers** with the missing prerequisite and needed action, and hold the
dependent authored-task review. Preserve completed work and allow draft report
refreshes with validation clearly incomplete; do not use review-ready wording
or an approval question. The initial task proposal remains a design review
before building and does not require grader execution.

## First-task build progress

After saving design approval, initialize `first_task_build` in the existing
creation record through a normal revision-checked update. Bind it to the derived
`plan_digest` and agreed `first_task`, set `active_step` to `environment`, and
record the actual started activity in `source`:

```json
{
  "plan_digest": "<current derived plan_digest>",
  "task_id": "<agreed first task ID>",
  "active_step": "environment",
  "source": "<actual started build activity reference>",
  "receipts": {}
}
```

Render `first_eval_prepare_first` once before work. It includes the provider's initial
explanation and fixed build checklist. During the build, use
`first_eval_build_progress` for subsequent commentary snapshots instead of
free-form build narration. Keep the same rows and ordering; the template owns
their labels, responsible skills in parentheses, and short statuses. Refresh
only when a displayed status, task count, or blocker changes. Keep the latest
snapshot visible during long-running checks; tool calls, polling, and delegation
do not require another unchanged checklist.

The coordinating agent alone emits visible build checklists. Delegated workers,
including environment work, return milestone results and evidence as they finish
so the coordinator can update the shared progress. Yield during long checks so
changed progress can be shown. Do not append deliberation, speculative next
steps, command transcripts, or another checklist. Retain verbose command output
in evidence logs and capture state-helper output instead of printing full state
and history. A focused question remains appropriate when work needs a user decision.

For `first_eval_build_progress` and `first_eval_remaining_progress`, include
`facts.previous_progress` when a snapshot has already been shown for this build
in the current chat. Supply that exact last visible rendered build message,
including its initial explanation and **Blockers** when present. Do not reconstruct that message or store it in
workflow state. After validating current state and evidence, the renderer returns
empty Markdown when the complete visible checklist and blockers are unchanged.
Do not post an empty result or replace it with a handwritten duplicate. If a
commentary update is needed while no displayed progress has changed, use one
brief factual sentence about the current operation without claiming completion.

Omit `previous_progress` for a new chat, an explicit status request, plan or task
rebinding, or a return from review, so the full current snapshot is shown. This
field is accepted only on the two explicit build-progress stages, not on
`first_eval_resume` or review stages. It affects presentation only; current
approvals, evidence, and workflow gates remain required.

The ordered step keys are `environment`, `environment_check`, `grading`, and
`artifacts`. Update `active_step` when the corresponding work starts. Save a
receipt under `receipts.<step>` only after that checkpoint succeeds, with
`{source, evidence: [{path, digest}]}` identifying inspected artifacts or command
results. Preparation retains the actual environment files and starting data;
environment checking retains successful smoke/reset evidence; grading retains
the authored reference and grader files; artifacts retains the finalized task
README, HTML report, and Markdown mirror. Use scoped file digests, so adding a
grader does not invalidate environment preparation. Failed or merely started
checks never receive a completion receipt. Preserve their logs and keep the
step in progress or explain its blocker.

Reload normal state and observations before each snapshot. The helper derives
completed rows from current receipts; a changed or missing receipt reopens its
row and dependent work. A changed plan or first task requires rebinding build
progress with empty receipts, then re-recording any still-applicable evidence.
When replacing an earlier receipt, clear its dependent later receipts before
recording their renewed checkpoints; earlier evidence remains in history.
Absent legacy receipts remain pending. Pass only current build blockers
in `facts.blockers`; the active row shows Blocked and the usual **Blockers**
section explains the needed action. Missing agent credentials or later run
integration do not block independent authoring.

Authored reference and grader files establish construction, not successful
grading validation. Checklist receipts never replace the
[pre-review validation gate](#grader-validation-before-authored-task-review),
agent-performance evidence, or human approval. Keep those claims separate in
`first_eval_review_first`. After the first task is approved, later report
refreshes do not restart this build.

## Remaining-task build progress

After saving approval of the current first-task revision, initialize
`remaining_build` through the existing revision-checked update interface:

```json
{
  "plan_digest": "<current derived plan_digest>",
  "first_task_sha256": "<approved first-task revision>",
  "active_step": "tasks",
  "source": "<actual started remaining-task activity reference>",
  "artifacts": null
}
```

Choose the actual current step from `tasks`, `environment_check`, `validation`,
and `artifacts`; completed prior work may make a later step the right starting
point. Render `first_eval_prepare_remaining` initially, then
`first_eval_remaining_progress` for subsequent snapshots. Follow the same compact
commentary and evidence-output rules as the first-task checklist. Both checklists
include the responsible skill names beside each item, owned by the template.
Do not add individual approval pauses for later tasks.

Record task creation, environment proof, and grading validation through the
existing task/evidence records. The renderer derives their counts from current
evidence. Creation and environment rows count only remaining agreed tasks;
validation counts all agreed tasks, including the validated first task.
Tasks can progress individually, so the active row need not follow a global
sequence. An empty remaining set creates no new tasks. Reuse compatible
environment proof; only changed or additional environment work needs rebuilding.

After authoring all tasks, proving their environments, and satisfying the
[pre-review validation gate](#grader-validation-before-authored-task-review),
refresh both report artifacts and save `artifacts` as
`{digest, source, evidence}`. Its `digest` is the current derived
`remaining_build_digest`; its evidence names the actual `.eval-author/report.html`
and `.eval-author/README.md` with their byte digests. This binds the refresh to
current task revisions and successful grading controls. Earlier draft refreshes
do not establish review readiness. Missing or stale artifacts stay pending;
later refreshes during execution do not restart completed authoring.

Pass only blockers affecting current build or validation work. Run-only access
does not block independent authoring. Missing judge prerequisites block grading
validation and review as specified above; task or first-task approval does not
authorize spending. Keep target-agent execution questions after suite approval.
The checklist never supplies validation evidence or agent performance claims.

## Review the authored suite before execution settings

When every agreed task is authored, its environment is proven, the
[pre-review validation gate](#grader-validation-before-authored-task-review)
passes, and the refreshed HTML report and Markdown mirror are current, render
`first_eval_review_suite`.
Use its grounding in the approved design and `ETHOS.md`, leading review links,
task summaries, honest validation/execution status, limitations, and final
approval question. Required model-based grading and calibration must already
have passed. Task review does not require choosing the target-agent execution
model, credentials, limits, or how to execute it.
Keep those questions and run-only blockers out of this review. Do not append a
generic report footer or routine pause explanation.

Emit the rendering as the final response and end the turn. A nonblocking
question-tool return does not answer it. Do not proceed with execution settings
or agent runs while the suite decision is pending. Revise tasks in place,
refresh their evidence and artifacts, and request review again when
needed. A side question or first-task approval does not approve the whole suite.

Save `suite_review_presented` as `{digest, source}`, using the derived
`suite_digest` and the actual delivered review message reference. After the
user explicitly approves, save `suite_approval` with that digest and the actual
approval reference in a separate revision-checked update. If the host ends the
turn when delivering the review, record its presentation from the prior message
at the start of the response turn, before recording the user's decision.

The digest binds the approved plan and exact task revisions. Changed task files
or design reopen approval; later validation results or report refreshes alone
do not. Missing legacy fields remain unknown, rather than implied approval.
Historical evidence stays readable. Only after current suite approval, present
the execution-settings checklist and resolve missing run decisions. Suite approval
authorizes the task design, not spending or extra evaluations.

## Agent-run progress

After the provider confirms that the approved job has launched or entered its
queue, save an optional `run_progress` under `scopes.create.first_eval` through
the revision-checked state interface:

```json
{
  "selection_digest": "<current derived selection_digest>",
  "run_id": "<exact native job identity>",
  "status": "running",
  "active_task_ids": [],
  "source": "<actual native status observation reference>",
  "evidence": [{"path": "<immutable saved status snapshot>", "digest": "<actual file digest>"}]
}
```

Use `running` or `waiting` only when observed in native job status. Name an active
task only when that status identifies it, using a subset of the explicitly
selected tasks; otherwise leave `active_task_ids` empty. Retain an immutable
status snapshot with its exact run identity rather than binding progress to a
changing log. A launch announcement, elapsed time, or unfinished attempt is not
evidence that a job is running.

Render `first_eval_run_progress` with fresh observations. Its applicability
requires current suite approval, explicit authorized selection, environment and
grading evidence, matching selection digest, and intact snapshot evidence. A
matching current, explicitly selected actual-agent run with status `running`
preserves the live snapshot; a terminal run or control record with that identity
retires it. The renderer's four fixed rows come from the template module and
omit skill labels; they cover grading, execution,
inspection of an actual attempt, and the final artifact refresh. The checklist
does not create evidence or grant permission for retries or additional runs.

Completed counts come only from current terminal attempt evidence in explicitly
selected actual-agent runs, including a job whose status remains `running`.
Retain completed attempts in that same native run record and explicitly select
its exact ID; do not invent separate jobs or wait for job exit to record progress.
Attempt statuses stay terminal, and the suite remains incomplete until the
selected running job becomes terminal. A completed attempt with a zero grade
still counts as complete; execution errors remain explicit. Controls do not count as agent
attempts. Never infer active attempts by subtracting completed from planned
attempts or imply that every unfinished attempt is running. A retained exact
review may complete its row only while its evidence remains current.

Show the checklist after confirmed launch or queuing and refresh it when a task,
attempt, or native status changes. During a quiet run, inspect native status
about once a minute and refresh only when there is a new observation or meaningful
change; do not repeat unchanged updates. Use concise commentary snapshots,
without command logs, deliberation, or additional approval questions. Pass
current blockers through the usual **Blockers** section and pause only dependent
work. Use the affected template row's label as `blocker.step` to mark that row
blocked; an artifact blocker must not mark the running job blocked.
On interruption or resume, inspect the native job before describing it as
running or launching it again. When the job finishes, retain its terminal native
evidence and continue to result review and artifact refresh; the live checklist
does not establish completion of that handoff.

## State and artifact authority

All creation stages require the complete normal, artifact-reconciled state from
the existing state helper and its exact revision. They have no stateless preview.
The saved journey is `create`; `state.ethos.stage` must be `complete`. Normal
state inspection retains completed work and reconciles changed `ETHOS.md`, task
files, and evidence. A state-only prerequisite snapshot is insufficient.

The caller obtains fresh `first_eval_observations` from the state helper and
passes them as `facts.observations`. They contain actual task digests under
`tasks` and actual retained-file digests under `files`; missing artifacts are
`null`. The renderer derives applicability from these observations and the saved
`scopes.create.first_eval` record. It never accepts supplied approval, validation,
completion, or progress booleans. Save decisions and actual results before
presenting them. Rendering is read-only and cannot establish permission to build,
run an agent, or spend money.

Before a design record exists, `first_eval_resume` renders the source question
when no choice is saved. Once the source is known, save the small concrete
proposal before resuming; the renderer does not invent its task rows or insert
another readiness question. Bind the saved choice in `plan.trace_choice` before
presenting that design. An unanswered source choice resumes at `traces`, and an
unapproved design resumes at `proposal`.

```json
{
  "stage": "first_eval_resume",
  "state": "<complete normal state object, not this string>",
  "revision": 7,
  "facts": {
    "observations": {
      "tasks": {"<saved task ID>": "<actual SHA-256 or null>"},
      "files": {"<saved evidence path>": "<actual SHA-256 or null>"}
    }
  }
}
```

Submit the complete payload through the shared entry point:

```bash
python3 -B <skill_dir>/scripts/conversation_renderer.py --input -
```

The caller owns all file inspection, provider checks, report refreshes, and
evidence provenance. No renderer stage performs those actions or changes saved
state. Identical supplied inputs produce identical Markdown.

## Saving the agreed design and evidence

Use the existing `journey_state.py --repo <repository> update --input <patch.json>
--expected-revision <revision>` interface. A patch places `first_eval` inside the
`scopes.create`; preserve the rest of the saved record.
The minimal initial record has this shape (replace descriptions with actual
values):

```json
{
  "schema_version": 1,
  "provider": "harbor",
  "ethos_digest": "<state.ethos.confirmed_digest>",
  "plan": {
    "trace_choice": "none",
    "tasks": [{
      "id": "<stable task ID>",
      "example_ids": ["<distinct input ID>"],
      "title": "<task title>",
      "input": "<request and relevant starting state>",
      "behavior": "<behavior under test>",
      "expected_result": "<observable intended result>",
      "grading": "<grading method and limits>"
    }],
    "presentation": {"coverage_note": "<usefulness, untested behavior, and grading explanation>"}
  },
  "plan_approval": null,
  "first_task": "<one planned task ID>",
  "tasks": {},
  "selection": null,
  "runs": [],
  "run_review": null,
  "audit_invitation": null
}
```

Select only the user's agreed provider. For selected traces, replace `none` with
the bounded source selection already agreed with the user. Copy a previously
saved `scopes.create.trace_choice` into `plan.trace_choice` before obtaining
design approval so that its scope digest binds the reused selection. Omitted decisions and
evidence stay unknown. The schema and pure applicability implementation are in
[first_eval_state.py](../../scripts/first_eval_state.py); use its `derive(state,
observations)` return values instead of reimplementing digest formulas. Normal
`journey_state show` exposes both `first_eval` progress and
`first_eval_observations` for this purpose.

After the actual design approval, save `plan_approval` with the current derived
`plan_digest` as `digest` and the user's reply reference as `source`. Save it
before recording generated tasks. Each task record contains its stable `path`,
observed task-tree `sha256`, `review` (initially `null`), `environment`,
`validation` (initially `null`), and `limitations`. Environment and validation
observations use `{sha256, status, source, evidence}`; each retained evidence
reference uses `{path, digest}`. A first-task approval uses
`{sha256, decision: "approved", source}` bound to the actual reviewed revision.
Obtain task bytes from `journey_state.task_digest(repo, task_path)` and retained
file digests from SHA-256 of their actual bytes. Never invent a digest from a
filename, intended result, or approval. The state helper enforces previously saved checkpoints, so approval cannot be
combined with the dependent generation in one update.

After current suite approval, when preparing execution, save `execution_settings`;
after presenting it, record
`execution_settings_presented` with the current derived `settings_digest` and
the actual delivered message reference. `selection` retains the explicit config
reference, selected `cases`, agent/model identity, source, and applicable
`settings_digest`. Each case names `task_id`, agreed `example_ids`, and positive
`attempts`; native Gym also names the corresponding one-based dataset `rows` and
retains complete input references under `selection.inputs`. Actual bounded
permission is recorded separately in `selection.authorization` using the derived
`selection_digest` and the authorizing reply reference.

Retain native `runs` with their stable IDs, selected task revisions, native
attempt identities, statuses, rewards, and evidence. Explicitly save
`selected_run_ids`; directory membership, append order, and a reviewed attempt do
not select a run. Save `run_review` with `run_id`, `attempt_id`, the run's derived
`run_digests[run_id]` as `digest`, inspection `source`, and material `limitations`.
Write an immutable per-run Markdown review outside the task tree, such as
`.eval-author/task-measurements/<run-id>-review.md`, identifying the same run and
attempt and its observed findings. Bind it under `run_review.summary` as
`{path, digest, run_id, attempt_id}` after inspecting the actual saved text.
Do not bind this immutable review to the report-generated suite README, which
changes on refresh. Missing or changed summary evidence reopens the review.

After completion, `audit_invitation` records `{status: "pending", source: null}`;
the user's actual response changes its status to `accepted` or `declined` with
that reply's source. The existing handoff state controls entering the audit.

## Stages and review boundaries

| Stage | Required context and rendered response |
|---|---|
| `first_eval_saved_design` | A pending saved-design choice on a fresh request; compact saved scope, historical approval, and current progress, then a fixed continue/revise/new-proposal question. End the turn before any dependent work. |
| `first_eval_traces` | Confirmed `ETHOS.md`; fixed optional-source question and choices unless the source decision is already saved in the creation scope or plan. Inspect only selected traces after resolving their location, runs, and exclusions. |
| `first_eval_proposal` | Saved proposed task designs; five-column task table, concise coverage and limitations, first-task choice, and fixed design feedback question. The question is the last content. Execution settings and run-only blockers remain deferred. |
| `first_eval_prepare_first` | Previously saved design approval; initial provider explanation and compact build checklist. Author exactly one task in its permanent directory, prove its environment before writing its grader, and satisfy the pre-review validation gate before returning for review. No execution checklist is required for local authoring. |
| `first_eval_build_progress` | Saved approved first-task build activity and fresh observations; the same fixed checklist with short completed, active, pending, or blocked statuses. Use commentary during work, without adding narration or requesting approval. |
| `first_eval_review_first` | Current first-task files, saved environment proof, complete current grading validation, and refreshed HTML and Markdown artifacts. Lead with the approved task's name and grounding in `ETHOS.md`, then direct the user to review the HTML report's Tasks view or mirrored README. Follow with input, expected result, grading with a meaningful limitation, and separate environment, grading-validation, and execution status. Link the task README and end with the first-task approval question. |
| `first_eval_prepare_remaining` | Saved approval of the current first-task revision and environment proof. Initial explanation and skill-labeled checklist for building the remainder, checking environments, validating all graders, and refreshing artifacts. |
| `first_eval_remaining_progress` | Current remaining-build activity after first-task approval; compact checklist with evidence-derived task counts, responsible skills, short statuses, and current build blockers. |
| `first_eval_review_suite` | Every authored task's current environment and complete grading-validation evidence plus current report artifacts. Ground the summary in the approved design and `ETHOS.md`, link the HTML Tasks view or mirrored README, summarize each task and current validation/execution evidence, and end with the explicit suite-approval question. No run settings or run-only blockers. |
| `first_eval_execution_settings` | Current authored-suite approval, when actual agent execution is being prepared. Display attempts, validation work, time/cost limits, and open questions, plus relevant run blockers. Do not repeat agent/model or task/example inventory bullets. This display never supplies spending authorization. |
| `first_eval_ready_to_run` | Current suite approval, validation of every selected task, and a saved applicable settings display. Explain that graders accepted the known correct results and rejected the incorrect results tested; this validates grading rather than measuring agent performance. Announce execution only with applicable explicit authorization, access, and no blocking dependency; otherwise ask one focused question about the missing setting or authorization. |
| `first_eval_run_progress` | Observed native launch or queued status with current authorized selection and immutable status evidence. Render the standard checklist without skill labels, evidence-derived completed/planned attempts, recorded execution errors, and only actually observed activity. No run-only approval pause or inferred activity. |
| `first_eval_results` | Review an exact retained current actual-agent attempt when its evidence is available; otherwise present the incomplete inventory and missing-evidence blocker. Separate completed, quality-failing, infrastructure-failing, missing, and unavailable-evidence outcomes. Link verified evidence and rerun instructions, reconcile delivered work, and show completion only when justified. |
| `first_eval_resume` | Derive the pending creation stage from saved decisions and fresh artifact observations, including an unresolved saved-design choice. Reuse applicable answers and authorization after that choice. It emits no welcome or initial readiness question. |
| `first_eval_blockers` | One or more saved blockers preventing the current action, with specific reasons and needed actions. Preserve independent progress. |

The proposal uses the saved plan's `presentation.coverage_note` for its usefulness,
untested behavior, and grading explanation. Task rows provide `title`, `input`,
`behavior`, `expected_result`, and `grading`. Do not create a separate user-facing
planning document.

Late execution settings come from the saved record's `execution_settings`, with
existing plan-presentation fields retained as a compatibility fallback. They
contain `agent_model`, `attempts`, `validation`, `limits`, and `open_questions`.
Retain target/model and selection details for reproducibility without repeating
them as settings bullets. Ask a focused question only when a model choice is
still missing. Record the actual display
through the state helper; viewing rendered output cannot count as delivery or
human approval. Local controls remain distinct from actual-agent runs.

## Facts and verified navigation

Prose facts are plain text and escaped as literal content. First-task review
requires `input`, `expected_result`, `grading`, and `grading_limitation`. Results
require `input`, `observed`, `grade_explanation`, `quality_failures`,
`identity_limitations`, and `reconciliation`; retain unknown model health and
other material limitations. If no current selected actual-agent review exists,
the incomplete results response requires only the honest `reconciliation` and
verified report links; optional rerun navigation may be supplied when available.
It reports saved identities as historical, never fabricates a reviewed response
or agent performance, and shows a missing-evidence blocker. `access_ready` is an observed boolean for run
readiness, never an authorization flag. Supply `missing_execution_question` only
when a focused question is needed.

Pass destinations separately under `facts.links`. Each destination is an object
with an absolute `target` and optional `fragment`:

```json
{
  "links": {
    "report": {"target": "/absolute/repository/.eval-author/report.html"},
    "readme": {"target": "/absolute/repository/.eval-author/README.md"},
    "tasks_view": {
      "target": "/absolute/repository/.eval-author/report.html",
      "fragment": "<verified Tasks navigation ID>"
    },
    "task_readme": {"target": "/absolute/repository/.eval-author/tasks/<task-slug>/README.md"},
    "rerun": {
      "target": "/absolute/repository/.eval-author/README.md",
      "fragment": "<verified saved run instructions heading anchor>"
    }
  }
}
```

Before calling the renderer, verify that every local target exists, is within
the allowed repository without symlink traversal, and identifies the intended
artifact. Verify HTML fragments against real element IDs and Markdown fragments
against actual heading anchors, including duplicate-heading suffixes. A plain
local target containing `#` names that literal file; supply a navigation fragment
in the separate field. The renderer formats these verified values without
reading files or proving that navigation exists.

Both task reviews require `readme` and `tasks_view` with its actual `cases`
fragment; first-task review also requires `task_readme`. Results require
`report`, `readme`, and `rerun` with a verified
fragment. Optional `task_rerun` also requires a verified fragment. Do not hard-code
a heading slug without checking the generated README.

Results additionally require `links.run_evidence` and `links.attempt_evidence`.
Each is a mapping from the exact saved evidence path to its verified destination.
Their key sets must match the reviewed run and attempt evidence respectively.
Later runs do not replace that saved review identity. Inspect the actual attempt
before supplying its input, actions, and grading explanation; configured identity
alone does not establish the observed backend model or model health.

First-task and full-suite review put their report and mirrored README links in the leading
review action, before the item summaries. Do not append the generic report footer
or routine skill-pause explanation to that rendering; separately required host
attribution still applies. Incomplete results use the single canonical closing
sentence in [conversation_templates.py](../../scripts/conversation_templates.py).
Follow [suite review guidance](../suite-readme.md); do not duplicate artifact links.

## Completed run review

Completed creation results use the flow template's linked run-review guidance
after the substantive results and completed checklist, immediately before any
pending audit invitation. Link the verified HTML report and mirrored README
once each in that guidance; do not append the generic report footer. Keep this
guidance when an accepted or declined invitation is no longer shown.

Explain how to inspect the saved run's tasks, attempts, grades, and available
evidence using the provider's supported navigation. For Harbor, direct the user
to the report's Runs tab and the selected saved run. Native NeMo Gym run evidence
is linked from the README; do not imply that its attempts appear in the report's
Harbor-only Runs table. Preserve verified evidence links and limitations, and
follow [review walkthrough requirements](../suite-readme.md#show-the-user-how-to-review)
without duplicating the report or README links.

## Blockers and the audit invitation

Save blockers through the existing state helper. Supply `facts.blockers` as
objects containing the matching saved `id`, `step`, `reason`, and `needed`.
The renderer uses the shared Blockers heading and escapes these explanations.
Proposal approval remains focused on design; use the dedicated blocker stage
when design itself cannot proceed. During local authoring, show only supplied
blockers for the current action and defer run-only dependencies. At execution
and results checkpoints, explain all applicable saved creation blockers.

When creation is complete, save a pending audit invitation before rendering the
handoff. The fixed invitation follows the linked completed-run review guidance
and is the final substantive content. Acceptance enters the existing audit flow with
confirmed `ETHOS.md`, task locations, provider, selected evidence, exact reviewed
run and attempt, limitations, and preferences. Recheck only changed or
audit-specific prerequisites. Missing audit traces block the audit while leaving
creation complete; do not silently create them with extra evaluations. Declining
starts nothing and suppresses repeat invitations. Acceptance alone authorizes
neither task expansion nor paid execution. Incomplete creation instead leads
with unfinished work and its blockers.
