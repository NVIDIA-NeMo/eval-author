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

After the shared confirmation continuation, render `first_eval_traces` as the
final response when no source choice is saved, then end the turn. Its canonical
question and choices come from the template module. Do not continue planning or
investigating integrations while the answer is pending, including after a
nonblocking question tool returns. Reuse a saved choice instead of asking again.
Use the rendered question and proposal as the checkpoint messages; do not add
free-form previews of task scope or adapter implementation in commentary.

Save the actual source answer and its provenance. For existing traces, resolve
the selected location, runs, and exclusions before inspection. Then prepare only
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

Render `first_eval_prepare_first` before work. It includes the provider's initial
explanation and fixed build checklist. During the build, use
`first_eval_build_progress` for subsequent commentary snapshots instead of
free-form build narration. Keep the same rows and ordering; the template owns
their labels, responsible skills in parentheses, and short statuses. Refresh
after a status changes and keep a snapshot visible during long-running checks.
Delegated workers should return milestone results as they finish so the
coordinating agent can refresh the checklist. Yield while waiting for those
checks so progress can be shown. Do not append deliberation, speculative next
steps, command transcripts, or another checklist. Retain verbose command output
in evidence logs and surface its outcome through the checklist. Capture state-helper
output for subsequent operations instead of printing full state and history.
A focused
question is still appropriate when work needs a user decision.

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

Completing this checklist means the task is ready for its existing review
checkpoint. It does not establish grading validation, agent performance, or
human approval. Keep those separate in `first_eval_review_first`. After the
first task is approved, later report refreshes do not restart this build.

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
validation counts all agreed tasks, including any pending first-task controls.
Tasks can progress individually, so the active row need not follow a global
sequence. An empty remaining set creates no new tasks. Reuse compatible
environment proof; only changed or additional environment work needs rebuilding.

After refreshing both report artifacts, save `artifacts` as
`{digest, source, evidence}`. Its `digest` is the current derived
`remaining_build_digest`; its evidence names the actual `.eval-author/report.html`
and `.eval-author/README.md` with their byte digests. This binds the refresh to
current task revisions and check results. Missing or stale artifacts stay pending;
later refreshes during execution do not restart completed authoring.

Pass only blockers affecting current build or validation work. Run-only access
does not block independent authoring. When paid validation needs authorization,
use the existing execution-settings checkpoint for the missing decision, then
continue the checklist; task or first-task approval does not authorize spending.
The checklist never supplies validation evidence or agent performance claims.

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

When preparing execution, save `execution_settings`; after presenting it, record
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
| `first_eval_prepare_first` | Previously saved design approval; initial provider explanation and compact build checklist. Author exactly one task in its permanent directory, prove its environment before writing its grader, and return for review. No execution checklist is required for local authoring. |
| `first_eval_build_progress` | Saved approved first-task build activity and fresh observations; the same fixed checklist with short completed, active, pending, or blocked statuses. Use commentary during work, without adding narration or requesting approval. |
| `first_eval_review_first` | Current first-task files, saved environment proof, and refreshed HTML and Markdown artifacts. Lead with the approved task's name and grounding in `ETHOS.md`, then direct the user to review the HTML report's Tasks view or mirrored README. Follow with input, expected result, grading with a meaningful limitation, and separate environment, grading-validation, and execution status. Link the task README and end with the first-task approval question. |
| `first_eval_prepare_remaining` | Saved approval of the current first-task revision and environment proof. Initial explanation and skill-labeled checklist for building the remainder, checking environments, validating all graders, and refreshing artifacts. |
| `first_eval_remaining_progress` | Current remaining-build activity after first-task approval; compact checklist with evidence-derived task counts, responsible skills, short statuses, and current build blockers. |
| `first_eval_execution_settings` | Saved design and first-task approval, when actual agent execution or paid grading is being prepared. Display six settings bullets, separately counting distinct examples and repeated attempts, plus relevant run blockers. This display never supplies spending authorization. |
| `first_eval_ready_to_run` | Current validation of every selected task and a saved applicable settings display. Clarify that reference checks establish working tasks, not agent performance. Announce execution only with applicable explicit authorization, access, and no blocking dependency; otherwise ask one focused question about the missing setting or authorization. |
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
The renderer derives task and distinct-example counts. Record the actual display
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

Review requires `readme`, `tasks_view` with its actual `cases` fragment, and
`task_readme`. Results require `report`, `readme`, and `rerun` with a verified
fragment. Optional `task_rerun` also requires a verified fragment. Do not hard-code
a heading slug without checking the generated README.

Results additionally require `links.run_evidence` and `links.attempt_evidence`.
Each is a mapping from the exact saved evidence path to its verified destination.
Their key sets must match the reviewed run and attempt evidence respectively.
Later runs do not replace that saved review identity. Inspect the actual attempt
before supplying its input, actions, and grading explanation; configured identity
alone does not establish the observed backend model or model health.

First-task review puts its report and mirrored README links in the leading
review action, before the item summaries. Do not append the generic report footer
or routine skill-pause explanation to that rendering; separately required host
attribution still applies. Results use the single canonical closing sentence in
[conversation_templates.py](../../scripts/conversation_templates.py).
Follow [suite review guidance](../suite-readme.md); do not duplicate artifact links.

## Blockers and the audit invitation

Save blockers through the existing state helper. Supply `facts.blockers` as
objects containing the matching saved `id`, `step`, `reason`, and `needed`.
The renderer uses the shared Blockers heading and escapes these explanations.
Proposal approval remains focused on design; use the dedicated blocker stage
when design itself cannot proceed. During local authoring, show only supplied
blockers for the current action and defer run-only dependencies. At execution
and results checkpoints, explain all applicable saved creation blockers.

When creation is complete, save a pending audit invitation before rendering the
handoff. The fixed invitation follows the substantive results and precedes only
the closing artifact sentence. Acceptance enters the existing audit flow with
confirmed `ETHOS.md`, task locations, provider, selected evidence, exact reviewed
run and attempt, limitations, and preferences. Recheck only changed or
audit-specific prerequisites. Missing audit traces block the audit while leaving
creation complete; do not silently create them with extra evaluations. Declining
starts nothing and suppresses repeat invitations. Acceptance alone authorizes
neither task expansion nor paid execution. Incomplete creation instead leads
with unfinished work and its blockers.
