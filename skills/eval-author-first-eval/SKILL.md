---
name: eval-author-first-eval
description: >-
  For a fresh start, read eval-author, show its welcome, checklist, and call to
  action, and end the turn before repository inspection or prerequisite work.
  Confirm ETHOS.md, agree on evaluation tasks, and build first evals or
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
  - eval-author-task-create (use for measured audit coverage gaps)
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

# Eval Author: first eval

## Purpose

Read `eval-author` for the shared standard and boundaries. Direct invocations
enter its shared prerequisite flow, consuming an applicable saved result on
return. A fresh start follows the core's opening-only first response and ends
the turn before repository reads, prerequisite checks, or `ETHOS.md` work.
Continue after the user replies; reuse the core's three-item checklist.
A statement that no evals exist settles the starting point; no discovery is
needed to prove absence. Reuse current confirmations and prerequisite decisions.

Introduce **evaluation tasks** as requests with criteria for scoring the agent's
response or actions, then call them **tasks**. Say **first task** when discussing
the initial review. Name the behavior document `ETHOS.md` in every user-facing
reply. Do not use numbered journey labels in conversation or saved artifacts;
name the activity, such as setup, first evaluations, or suite audit. Distinguish
tasks, distinct input examples, and repeated attempts; a repeat never adds an example. Technical native fixture names may still use
`case`, but do not use that term to introduce tasks to the user.

Every user-facing checkpoint must use the required
[deterministic conversation renderer](../eval-author/references/first-eval-conversation.md):
`python <core-skill>/scripts/first_eval_conversation.py --repo <repo> --input <working-json>`.
Supply the current journey-state revision and supported facts as the contract
specifies. Copy its rendered Markdown verbatim; do not reconstruct, shorten,
paraphrase, reorder, or add to its fixed messages, questions, labels, or tables.
Use its `traces`, `proposal`, `execution_settings`, `prepare_first`, `review_first`,
`prepare_remaining`, `ready_to_run`, `results`, `resume`, and `blockers` stages at the corresponding
checkpoints. Dynamic facts, links, task content, and claims require evidence.
If state changes, refresh it and render against the current revision. This
reference owns exact wording; the descriptions below specify when to use it.

Use [First-eval state and resume](../eval-author/references/first-eval-state.md)
for every checkpoint, approval, authorization, task revision, and handoff. Save
accepted decisions before acting on them. Resume at the first unfinished stage;
a side question is not approval, and restarting the chat does not erase a gate.
When expanding a suite, reuse its reviewed tasks, selected sources, setup, and
current `ETHOS.md` confirmation. Reopen only affected decisions. Do not require
an audit just to add agreed examples.

Whenever the current step is blocked, show a **Blockers** heading in the reply. For each
blocker, name its cause, the step it prevents, and the concrete action needed to
continue. Preserve completed work and distinguish a partial delivery from the
completed journey. A missing integration or report must not silently advance
a review gate. Keep run-only blockers saved while presenting the initial task
design; display them after design approval with the execution settings. If a
blocker prevents a meaningful design proposal, render `blockers` instead of an
approvable proposal. Do not request secret values. Review replies must not append
unsolicited policy explanations, skill citations, or approval-rule quotations.

The deliverable is the agreed working suite, actual-agent run evidence, and
usable review and rerun guidance. Low agent scores are a valid baseline;
comprehensive coverage and repeated success are not completion requirements.
A working suite and strong evaluation quality are separate claims.

## 1. Reuse the confirmed ETHOS.md

Consume the path, confirmed digest, validation result, and unresolved intent from
the core's [ETHOS.md confirmation](../eval-author/references/ethos-confirmation.md)
handoff. Reuse the user's current confirmation without repeating the interview
or writing another document; reopen only changed or unresolved content. A new
shared entry follows the shared review rules rather than treating old saved
completion as current confirmation. When shared onboarding finishes, show the
core's completion pause before source selection or evaluation work. Continue
only after the user replies.

## 2. Reuse prerequisites and verify when needed

Preserve the selected setup and recorded prerequisite evidence and answers.
Shared onboarding requires the supported runtime before `ETHOS.md` and task
planning. Before scaffolding or another runtime-dependent operation, reuse
applicable checks or follow [Runtime prerequisite checks](../eval-author/references/runtime-prerequisites.md#runtime-prerequisite-checks).
Record actual checks separately from user confirmation. If the selected runtime
becomes unavailable, preserve progress and report the blocker before continuing
dependent work. An explicit planning-only request can proceed without execution.

For a combined NeMo Gym–Harbor setup, record each tool's role and verify the
configured supported integration. Format conversion or both CLIs being installed
is not proof of that integration. Do not replace it with direct Harbor execution,
switch providers, or create a new adapter to make the journey appear complete.

## 3. Understand the evaluation starting point

Reuse the user's stated absence of evals and available repository findings.
Explain that the tasks will test the behavior in `ETHOS.md` and can be rerun
after changes to detect improvement or regression.

Read [Corpus and coverage planning](../eval-author/references/coverage-planning.md#select-trace-evidence-before-using-it)
before searching for or inspecting traces. If the choice is unresolved, use the
renderer’s exact optional-trace question and choices (`traces`), including when
using a question tool, as specified in its optional trace choice contract.
Present only already-known source candidates and confirm a bounded selection
before reading them. Reuse an explicit selection or no-trace choice; do not ask
again. No evals does not imply no traces. Without traces, proceed from `ETHOS.md`
and mark generated inputs as synthetic. Do not hunt for traces, manufacture a
coverage report, or use unselected repository fixtures as evidence.
Save source paths, bounds, exclusions, selected/inspected/usable counts, and
stable source IDs as soon as established. Carry them into authoring and expansion.

## 4. Define the evaluation scope

Read the agent entry point, tools, and usage docs. Propose tasks based on
`ETHOS.md` and selected evidence, covering useful positive, negative, difficult,
and boundary examples at the agreed breadth. Justify expected outcomes by
meaning and intended behavior, not an existing classifier's score. Mark ambiguous
semantics unresolved. Explain which behavior remains outside the proposed suite.

Use [Execution dependencies](../eval-author/references/execution-dependencies.md)
and Steps 1 and 2 of [environment preparation](../eval-author-environment/SKILL.md)
to establish provider fit, dependency inventory, and result collection. Files
and embedded databases count as dependencies. Save run-only integration or access
findings for the execution-settings checkpoint after design approval. A finding
that prevents a meaningful task design requires `blockers` instead of a proposal;
a provider change requires the user's decision and verification of its runtime.
Do not make the user supply runtime YAML or invent a replacement agent.

Present the proposal **in the conversation**, with this table and concrete rows:

| Task | Input | Behavior under test | Expected result | Grading |
| --- | --- | --- | --- | --- |

Use readable task names; describe their inputs and starting conditions, observable
outcomes, and what the grader checks. Retain requirement/source IDs in saved
state and later task documentation. Do not create or ask the user to review a
separate planning document. The environment's technical plan is still required.

Explain a small initial suite's purpose and limits without calling the first
reviewed task a separate breadth standard. Lower rerun cost, measuring the
agent's own cost or latency, broader coverage, and harder examples are different
priorities. Carry forward the user's preferences and identify the first task to
build. The initial `proposal` contains only its introduction, table, coverage
explanation, first-task choice, and this fixed question as its final text:

> Do these tasks make sense for your agent? Are we testing the right behaviors, with the right expected results and grading? Tell me what you’d change, add, or prioritize—or approve the proposal as written.

Do not append the execution bullets, run blockers, execution authorization request,
approval footer, or policy citations to this proposal. Ask for task-design feedback
first; design approval does not authorize paid work.

Save the accepted proposal and feedback using the
[state contract](../eval-author/references/first-eval-state.md) before scaffolding,
fixtures, instructions, graders, or delegated authoring. Unanswered task-design
choices stop that work. Proposal-only requests end with the conversational
proposal. Expansion
retains existing examples and records additions, new totals, difficulty, and cost.

After design approval, save `execution_settings` separately from the plan and
render `execution_settings` with exactly these six bulleted labels:

- **Agent/model:** the actual agent and model configuration, or what is unresolved.
- **Tasks and examples:** agreed task count, distinct examples and their mapping,
  the first task to build, and remaining planned tasks and exclusions.
- **Attempts:** attempts per task or selected Gym row and total agent executions;
  count repeats separately from examples and control runs.
- **Validation:** environment proof and task controls, what they establish, and
  any paid judge or model work they require.
- **Time/cost limits:** agreed bounds, timeout/retry limits, and estimated work;
  use supported prices or measured evidence, otherwise say monetary cost is unknown.
- **Open questions:** unresolved access, integration, budget, or run choices;
  say none when settled instead of inventing another question.

Show relevant **Blockers** in this checkpoint, distinguishing what prevents
building the first task from what only prevents later execution. A run-only
blocker does not stop independent authorized authoring. Record actual delivery
with `execution_settings_presented` before `prepare_first`; that record is a
display receipt, not user approval or spending consent. Do not add another generic
approval gate for these settings. Legacy already-created first tasks resume at
their current stage without a retroactive display checkpoint.

Obtain missing **explicit bounded execution authorization** when concrete paid
work is ready: identify the selected agent/model, tasks/examples, attempts,
validation work, paid judging, and retry/time/spend limits. Reuse authorization
that already covers the exact work; do not ask again because the stage changed.
A design approval, first-task approval, displayed settings, or general positive
response never implicitly authorizes spend. Changes beyond authorized bounds
require a revised decision.

## 5. Prepare tasks and grading

Read the selected provider reference for stages 5–9:

- [Harbor first evals](references/harbor-first-eval.md): native tasks, reference
  solutions, NOP/Oracle controls, configuration, and execution.
- [Native NeMo Gym first evals](references/gym-first-eval.md): native components,
  selected dataset rows, known-correct fixtures, controls, and execution.

A combined setup uses its configured supported integration and relevant parts of
both references. Reading a reference does not change the selected provider.

Render `prepare_first`, then build **one first task** at
`.eval-author/tasks/<slug>/`; do not build the
remaining tasks yet, including through delegation. Map its stable example IDs
to the native task or selected Gym rows. Keep provenance and expected answers
outside the agent's starting environment.

Build and prove the kit before the tests. Before writing the first task's grader
or reference solution, complete Steps 3 to 7 of
[`eval-author-environment`](../eval-author-environment/SKILL.md): choose dependencies,
generate seeded starting data, expose observable end state, isolate/reset the
environment, and run its smoke task, recording each job in `environment-plan.md`.
Layer the task's records onto generated data and derive expected values through
an independent reference query. Hand-typed fixtures of a few rows are not
starting data. An unproven environment blocks grader/reference construction;
keep the first-task gate pending rather than authoring the remaining tasks.

Apply [Task validation and execution evidence](../eval-author/references/task-validation.md).
Write the request, environment, grader, reference solution or native known-correct
interaction, and actual task README. Explain the grader's assertion and a
plausible wrong result it must reject. For Harbor, introduce and link the
[reference solution](https://docs.harborframework.com/tasks/solution) and
[grader](https://docs.harborframework.com/tasks/verifier) when creating them.
Keep these explanations about the task being built rather than a separate tutorial.

Document reward names/ranges, predeclared control expectations, requirement,
inputs, expected result, environment limitations, verifier links, and known run
instructions in the task README. Follow [Task documentation](../eval-author/references/suite-readme.md#task-documentation)
and [Review and rerun guidance](../eval-author/references/suite-readme.md#review-and-rerun-guidance).
Record native validation and control status accurately. Formal grading controls
may still be pending at first-task review; they do not block review once the
environment is proven and the task's actual files are available. After approval,
apply stage 8 to the first and remaining tasks under the applicable authorization.
Do not add a separate control/validation approval gate when bounded authorization
already covers that work. Actual-agent execution is also separate from this review.

Refresh `.eval-author/report.html` and `.eval-author/README.md` using the existing
report contract. Present the first task's input, expected result, grader,
reference, completed or pending control outcomes, and limitations. Link the **actual task README**,
the HTML report, and its Markdown counterpart; do not link only a general guide.
Show how to inspect its files and available evidence using the installed viewer
or native readable artifacts. Mark the actual agent as not run when appropriate.
If report generation fails, retain the Markdown/task review and report the cause
under **Blockers**; do not claim the required review artifacts are complete.

Use `review_first` to request explicit approval of this first task before
building the remainder. Preserve the renderer's exact first-task question.
A request to fix it is not approval. Revise the same task in place, retain prior
run evidence, invalidate affected old controls and rerun them when applicable,
refresh the review artifacts, and present
it for review again. Save the approved revision through the state contract.
Only then render `prepare_remaining` and build the remaining agreed tasks using
the reviewed approach; validate
each and keep its README/report current. A blocked first task does not unlock
remaining task generation. A changed task invalidates its affected validation
and approval; do not apply approval of earlier bytes to a changed task.

## 6. Prepare the execution environment

Hand off the kit proven at stage 5 with Step 8 of
[`eval-author-environment`](../eval-author-environment/SKILL.md). Confirm each
remaining task builds on that kit and rerun its smoke task if the kit changed.
Keep solutions and verifier-only data outside the initial agent environment.
This stage requires `environment-plan.md`, the seeded generator, and smoke
results under `.eval-author/.working/environments/<agent-slug>/`; task controls
never count as environment proof. Without the smoke task's jobs, status stays
`unproven`. Verify actual backend and application access. Under **Blockers**, name
missing access, the tasks/checks it prevents, and the required setup action.

## 7. Connect the agent

Use the selected provider's supported integration for the actual agent, verified
from its entry point, installed interfaces, registry, and CLI help. Do not
substitute another agent, rewrite the application, or add an adapter. A missing
integration blocks actual-agent evaluation; native task controls may still be
available.

Save a provider-native run configuration selecting **exactly** the approved,
validated tasks/examples, actual agent/model, agreed attempts, reset, and output
paths. Harbor uses `.eval-author/run.yaml`; native Gym preserves its composition
and exact selected dataset rows. Exclude unrelated drafts and blocked tasks,
and make any unfinished agreed work visible rather than silently shrinking scope.
Resolve paths from the documented working directory and reference credential
variables without embedding secrets. Verify request delivery, conversation
state, and output/action collection; configuration presence is not run evidence.

## 8. Validate the evals

After first-task approval, apply this stage to the first task and the remaining
tasks. Reuse current controls already run; formal controls were allowed to remain
pending at review. Do not add human approval gates for each later task or for
validation already covered by explicit bounded execution authorization. Follow [Execution recovery](../eval-author/references/execution-recovery.md)
for controls, target-agent runs, repairs, and recaps. Confirm current environment
proof first; failures there are environment defects, not agent-quality results.

Run provider-native structural validation and every declared control with fresh
artifacts through the shared evidence recorder. Inspect rewards, reward shape,
and exceptions against predeclared expectations. Include a realistic incorrect
result separately from no-action, applicable valid alternatives, and trusted
state/preservation checks. When an action is required, no action earns no
completion credit. Correct inaction needs an explicitly wrong action control.

Fix false acceptance/rejection or leaked answers without weakening the intended
behavior, then rerun affected checks for the new revision. These controls prove
particular checks, not broad coverage or actual-agent performance. Keep failed,
interrupted, and superseded evidence. Show unavailable controls, config validation,
or integration under **Blockers** with cause, blocked step, and next action.
A partial working subset does not complete the whole agreed journey.

Validate the saved config and resolve its exact selected tasks/rows and settings.
Do not equate YAML presence with a runnable config. If a shared Gym dataset or
component changes while adding tasks, renew evidence for every affected task;
its previous dataset/config digest is no longer current.

## 9. Evaluate the agent

Confirm the resolved selection matches the approved tasks and examples. Render
`ready_to_run` with current validation and authorization facts. Show
the actual agent/model, attempts, provider-native command, execution bounds, and
where rewards and errors will appear. Verify it is covered by explicit execution
and spend authorization; if not, request that concrete bounded authorization
before running. Reuse an existing matching authorization without another gate.

Execute through the configured supported provider integration. Retain fresh
native results for every selected task/example/attempt: replies, actions,
rewards, exceptions, resolved settings, and exact commands. Inspect all results;
low scores are valid observations. Baseline evidence-recorder receipts are
optional and do not require audit closure, repeated successes, or ATIF.
A local probe or synthetic reconstructed result cannot replace native execution.

## 10. Review results and explain reruns

Keep tasks at their existing paths; acceptance changes status and suite membership,
not directory location. Verify the config and report links resolve to those
same approved tasks. Reconcile agreed versus generated, validated, and actually
executed examples using stable IDs. Report missing work and distinguish agent
failures, infrastructure errors, and absent runs; do not count repeats as examples.

Refresh the report and Markdown inventory without changing evaluation evidence
or workflow state. In both the handoff and saved reports, explain what each task
tests, translate its observed controls, state whether the actual agent ran, and
describe its scores and material limits. Link **specific saved jobs/trials or Gym
rollouts**, including a named task and attempt, with informative labels. Include
available logs, grading evidence, and traces; distinguish source traces from new
run traces. Do not invent ATIF or promise a trajectory the adapter did not emit.

Follow [Show the user how to review](../eval-author/references/suite-readme.md#show-the-user-how-to-review):
for Harbor, give the verified viewer launch command and browser URL, or a verified
live link, and identify the actual-agent job/trial to select. For native Gym,
link readable selected-row evidence with the saved attempt identity. Mark an
unlaunched viewer as needing launch. Controls-only work stays labeled controls;
under **Blockers**, explain why actual-agent execution remains unfinished.

Save full-suite and task-specific rerun instructions with exact selection,
agent/model settings, attempts, reset, setup, and fresh output paths. Verify the
README heading/anchor and link it as **Run instructions** in the handoff; a plain
phrase or invented anchor is insufficient. Include a concrete one-task rerun
when needed to inspect or repair a result. Verify subset configs retain all
non-selection settings and do not accidentally include drafts. Keep long commands
in that linked section except the review-launch guidance needed in the reply.

The journey completes only when the agreed tasks are reviewed, built, validated,
and executed through the supported actual-agent integration, and the required
report, Markdown/task documentation, results, and rerun handoff are available.
A low score does not prevent completion. A missing run, integration, required
review artifact, or unfinished agreed task does. Save completion and artifacts
using the [state contract](../eval-author/references/first-eval-state.md), then
show the final checklist accurately. A missing optional trace does not invalidate
a completed baseline, though it may limit a later audit.

Only after completion, render `results` with the verified handoff facts and
offer this optional next step once, verbatim:

> Would you like to see how you could improve your suite further? We can audit its coverage and grading against the behavior described in `ETHOS.md` to identify gaps and worthwhile improvements.

On yes, hand off to the existing `eval-author-audit` flow with current `ETHOS.md`
confirmation, setup, accepted suite paths, run evidence, selected source context,
and unresolved limits. Reuse current confirmations; apply the audit's own trace
and scope requirements. Do not start an audit before the user accepts. On decline,
record that decision and do not repeat the offer. Missing audit traces block the
audit's dependent step, not the already-completed creation journey.

Place the report and Markdown links at the end of the reply, after the invitation
or blocker explanation. Do not link internal planning state unless requested.

## Prerequisites

The selected supported runtime and confirmed `ETHOS.md` precede full task design.
No existing evals, prior traces, coverage report, or measured gap is required.
Execution needs the selected backend, supported agent connection, application
access, and explicit authorization covering any model or paid-judge work.

## Limitations

A starter suite measures its selected tasks. Native controls check particular
verifier behavior, and do not establish comprehensive coverage or model quality.
Retain progress when a dependency is unavailable and show **Blockers** rather than
silently changing the provider, behavior, or completion criteria.

## Troubleshooting

- Missing or changed `ETHOS.md`: resume the shared confirmation handoff for the
  affected intent before task design; retain unchanged decisions.
- Runtime unavailable: preserve tasks and decisions, name the required setup,
  and install only within the user's authorization.
- Incorrect control results: inspect starting state, reference, and grader;
  repair without weakening intended behavior and retain fresh evidence.
- Missing agent integration or credentials: name the supported entry point or
  required variable and the blocked run; never request secret values in chat.
