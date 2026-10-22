<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Conversation renderer for first evaluations

Use fixed wording and structure wherever the next message does not require judgment.
Render the message, inspect it, and send it without paraphrasing its fixed text. Task
inputs, expected behavior, grading limitations, and interpretation of actual evidence
still require the author’s judgment. The renderer does not inspect traces, execute
providers, manufacture evidence, or obtain approval. Rendering never advances state.

```bash
python <core-skill>/scripts/journey_state.py --repo <repo> show
python <core-skill>/scripts/first_eval_conversation.py --repo <repo> \
  --input <repo>/.eval-author/.working/conversation.json
```

`conversation.json` is private working input, not a separate user-facing planning
document. Use `{ "stage": "proposal", "revision": <current state revision>, "facts": {} }`.
The renderer rejects stale revisions and invalid navigation. Update evidence and
decisions through the existing revision-checked state interface; see
[first-eval state](first-eval-state.md). Never synthesize a user approval source.

## Optional trace choice

When the choice is unresolved, the `traces` renderer supplies its fixed introduction
and question followed by exactly these choices, in this order:

```markdown
- Start from ETHOS.md
- Use existing traces
```

When using a question tool, use the rendered introduction and question before this
bullet block as the question text, and these exact labels without bullet markers as
the choices. Do not append explanations such as “with synthetic examples” or “I’ll
specify the source.” Free-text answers remain possible. A saved trace choice omits
both the question and choices; reuse that decision without asking again.

## Saved proposal presentation

Put the proposal in `scopes.create.first_eval.plan` before rendering it. In addition
to the state helper’s stable task and example IDs, each `tasks` item has `title`,
`input`, `behavior`, `expected_result`, and `grading` strings. Preserve agreed order.
Use the same example ID only for the same distinct input; a shared input can test
multiple behaviors in separate tasks and counts once in distinct examples.
For new records, `plan.presentation` contains only the task-design explanation:

```json
{
  "coverage_note": "Tests approved refunds and policy refusals. Other regions remain untested."
}
```

The initial proposal contains only its introduction, five-column table, coverage
explanation, first-task choice, and fixed feedback question. That question is the
final text. Do not append execution details, run blockers, execution consent or
approval footers, policy citations, or skill quotations. If a genuine design
blocker prevents a meaningful proposal, render `blockers` instead.

When concrete agent runs or paid grading are ready after first-task review
approval, save the execution display separately in
`scopes.create.first_eval.execution_settings`:

```json
{
  "agent_model": "Support agent / selected model (health not yet verified)",
  "attempts": "2 per task; 4 total executions",
  "validation": "Reference checks and relevant incorrect-result controls; no paid grader",
  "limits": "Up to 10 minutes and $1; stop if a limit cannot be enforced",
  "open_questions": []
}
```

These are illustrative settings, not defaults or authorization. Include known
uncertainties in limits and open questions. The six execution-settings bullets
derive task and example counts from the approved plan and use these five display
fields for the remaining labels. Legacy records can use their existing
`plan.presentation` execution fields as fallback; do not rewrite that historical
plan merely to move the fields.

Design approval proceeds directly to first-task preparation and authoring; its
next substantive checkpoint is the built task’s artifact review. Ask only about
unresolved environment or design choices required for current work. Keep run-only
settings and blockers deferred, without adding a new environment approval stage.

Render `execution_settings` after first-task review approval when concrete agent
runs or paid grading are ready. It says “Here are the settings for running the
approved tasks.” and ends its settings text with “Agent runs and paid grading
require authorization within these limits.” Show relevant **Blockers** affecting
that execution; do not append a first-task-pause footer to this later message.
After actually sending it, record `execution_settings_presented` with the current
`settings_digest` and delivery `source`. This is a display receipt, never user
approval or execution consent. Neither settings nor this receipt gate first-task
generation or review. Reuse current previously displayed settings and legacy
progress. There is no generic settings-approval gate. Request missing bounded
paid-work authorization only when concrete work is ready, reusing applicable
prior consent. Display settings before paid validation when needed; otherwise
the automatic resume sequence reaches them after local task validation.

## Stages and inspected facts

| Stage | Preconditions and facts |
|---|---|
| `traces` | Requires confirmed `ETHOS.md`; can run before a proposal exists. Fixed optional-trace question and choices are omitted when `scopes.create.trace_choice` (or `plan.trace_choice`) is saved. Save `none` or an object containing selected locations, run IDs, and exclusions. Select sources before inspection. |
| `proposal` | Renders the introduction, five-column table, coverage explanation, first-task choice, and fixed feedback question as the final text. Run-only blockers and execution settings remain deferred. No task generation before the design approval is saved. |
| `execution_settings` | Requires current first-task review approval. Renders the six execution-settings bullets when concrete runs or paid checks are ready, with relevant blockers. It can precede paid validation after that approval; automatic resume reaches it after local validation. Save actual display without adding user approval or spending consent. |
| `prepare_first` | Requires saved design approval; no execution-settings display is required. Provider selects fixed Harbor documentation links or native Gym wording. Author one task at its permanent path, then prove its environment before tests. |
| `review_first` | Requires current first-task environment proof, task files, report, and mirror. Facts: `input`, `expected_result`, `grading`, `grading_limitation`. Displays derived environment, validation, and execution statuses separately. Formal grading checks may still be pending. |
| `prepare_remaining` | Requires saved approval of the current first-task revision and its environment proof. The fixed message announces remaining authoring and controls; it does not imply individual review of later tasks. |
| `ready_to_run` | Requires current validation of all selected tasks and no pending execution-settings display. `access_ready: true` adds the run announcement only with saved execution authorization and no blockers. Otherwise supply one `missing_execution_question`. Verify credentials without retaining secret values. |
| `results` | Requires a saved exact agent run/attempt review whose evidence remains applicable. Facts: `input`, `observed`, `grade_explanation`, `quality_failures`, `identity_limitations`, `reconciliation`. Describe quality failures using the provider’s actual criteria; a numeric zero alone is not a universal failure rule. Review limitations must include unknown or unavailable evidence. |
| `resume` | Uses current progress: `proposal` → `prepare_first` → `review_first` → `prepare_remaining` → `execution_settings` after local validation → `ready_to_run`/`results`. Reuse current displayed settings and completed legacy progress. Explicit settings presentation may precede ready paid checks after first-task approval. Supply the selected stage’s facts and reuse decisions; rendering executes nothing. |
| `blockers` | Requires `facts.blockers`; usable without a proposed scope. Render a concrete recovery action, then continue independent work. |

`facts.blockers` is a list of `{ "step": "…", "reason": "…", "needed": "…" }`.
Retain run-only blockers in state while proposing, building, and reviewing tasks;
`prepare_first`, `review_first`, and `prepare_remaining` do not require their
display. Show them when the affected runs or paid checks are ready. Show
blockers preventing current environment or authoring work at that checkpoint.
A blocker that prevents task design uses `blockers` instead of `proposal`.
When a stage displays
saved blockers, supply their explanations. The renderer uses exactly
`### Blockers` with the blocked step, cause, and required action. Errors from the
renderer are diagnostic failures: fix missing state/evidence or render the blocker;
do not bypass a rejected gate by writing a success message by hand.

## Artifact and evidence navigation

Default report and mirror paths are `.eval-author/report.html` and
`.eval-author/README.md`; `facts.report` and `facts.markdown` can select existing
alternatives. First-task review defaults to `.eval-author/report.html#cases`, the
current report’s actual Tasks navigation target. `facts.tasks_view` may override it
with another verified local view anchor. The task README always resolves from its
saved permanent task directory. Generate and refresh both report artifacts before
rendering first-task review, after edits, authoring, validation, and runs.

Result output links the saved exact attempt’s evidence files, never the newest run.
Keep provider viewer commands or additional navigation in the saved summary only
after verifying the installed viewer against that actual job/rollout. The renderer
checks that local destinations and anchors exist; it cannot verify a remote viewer,
interpret a trajectory, or guarantee that a report displays all required lifecycle
information. Those remain explicit integration acceptance checks.

The rerun link defaults to `.eval-author/README.md#rerun-evaluations`. Create a durable
`## Rerun evaluations` section outside generated mirror markers, with provider-resolved
full-suite and single-task commands and fresh output paths. To retain an existing
equivalent heading, set `facts.rerun` to its exact verified anchor. Optional
`facts.task_rerun` links separate task instructions. Report generation must not change
workflow state or result evidence.

Once all agreed work and the exact attempt review are complete, save
`audit_invitation.status: pending` before rendering the final invitation. The renderer
adds the fixed question only for this pending state, followed by report and Markdown
links. Await the answer. A declined or accepted invitation is not reoffered; unfinished
creation never gets the invitation. Acceptance uses the existing audit handoff and
retains the completed creation record. Missing audit evidence blocks only the audit;
acceptance does not authorize more tasks, agent edits, or paid runs.
