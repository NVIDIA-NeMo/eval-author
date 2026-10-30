---
name: eval-author
description: >-
  Use for "help me with my evals", "help me onboard to expand my evals",
  "what's the state of the eval suite here?", "what happened in this trace?",
  or requests to create, discover, audit, or improve evaluations.
  For fresh full workflows, read this skill, show its rendered orientation,
  and end the turn before repository work. Scoped utilities keep their scope.
  Explicit audits first check bounded saved context. Returning audits reuse
  applicable prerequisites and confirmed Ethos. Separate requests show a welcome
  and current audit checklist without another readiness pause.
  Build first evals from ETHOS.md, work on existing suites, inspect a NeMo Intake
  run, or derive a trace environment (experimental). Owns the evidence standard
  for every Eval Author flow. Creating, auditing, and improving evals require
  confirmed ETHOS.md and do not change the agent implementation.
triggers:
  - help me build evals for my agent
  - my agent has no evals yet
  - audit my existing evals
  - I am not sure whether this repo has evals
  - suggest new evals based on this audit
  - help me with the evals in this repo
  - help me with evals
  - get me started with eval author
  - how do I use eval author
  - help me onboard to expand my evals
  - walk me through improving my existing evals
  - what is the state of the eval suite here
  - I inherited a repo with Harbor tasks in it
  - work on my evaluation suite
  - my evals do not use Harbor
  - what happened in this agent trace
  - create an evaluation environment from a trace
  - which eval author step do I need
not-for:
  - eval-author-first-eval (use to establish Ethos and build first evals without prior coverage)
  - eval-author-discover (use to run the discovery pass and get a runnable verdict)
  - eval-author-audit (use to generate, validate, measure, or aggregate audit.md coverage)
  - eval-author-task-create (use to propose dataset improvements; when task creation is requested, create and prove an eligible NeMo Gym or Harbor task)
  - eval-author-inspect-trace (use after this skill selects the trace sub-flow)
  - eval-author-trace-environment (experimental; use only when explicitly requested or continuing its existing workspace)
  - eval-author-environment (use after an authoring sub-flow hands over its cases to build and prove their task environment)
  - nemo-intake (use to instrument agents, ingest telemetry, or query Intake outside Eval Author)
  - mlflow-to-atif (use to convert MLflow traces into canonical ATIF files)
  - gym-to-atif (use to convert NeMo Gym Responses traces into canonical ATIF files)
  - nemo-experimentalist (use to run insight-driven optimization end to end, which drives the Eval Author agent itself)
  - nemo-evaluator (use to run an existing benchmark rather than work on a repository's own suite)
compatibility: >-
  The core manages shared state and delegates Ethos to ethos-orchestrate;
  runtime execution is delegated to sub-flows.
  Discovery, audit, and task creation use the local checkout.
  Task execution requires the selected NeMo Gym or Harbor runtime and may require Docker and provider
  credentials. Trace inspection requires the nemo CLI, an explicit workspace,
  and read access to configured Intake.
maturity: alpha
license: Apache-2.0
user-invocable: true
allowed-tools: Bash Read Write Grep Glob
metadata:
  owner: NVIDIA-NeMo/ase_team
  owner_url: https://github.com/orgs/NVIDIA-NeMo/teams/ase_team
  tags: [evaluation, authoring]
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Eval Author

## Purpose

Work on repository-owned evaluation suites and understand agent traces. Route
each request to the narrow sub-flow that owns it.

## Choose the entry route

Use the requested outcome and prior answers before asking a question. Creating,
auditing, and improving evals use the **Shared prerequisites and Ethos** flow
below for NeMo Gym and Harbor, including direct invocations of their skills.

For an explicit full-audit request in a known repository, first follow
[Return to an audit](references/shared-prerequisites.md#return-to-an-audit).
It owns saved-context reuse and welcome versus direct continuation. Fresh
requests retain the opening below.

The first response to a new shared flow is orientation only. Determine the goal
from the user's request and conversation already available, without inspecting the
repository. Render `orientation` using the pure conversation renderer, with the
known journey or no journey for goal selection, and use its Markdown as the
entire final visible response. The unknown-goal opening uses the shorter welcome
without a checklist introduction. **End the turn immediately after that response.**
When the user selects a goal, render `goal_selected` with that journey and end
the turn again. Selecting a goal does not establish readiness or start checks.

Before either orientation response, read only the entry skill, this core skill,
the [conversation rendering contract](references/conversation-rendering.md), and
the [onboarding reference](references/conversations/onboarding.md) as needed.
An explicit audit may also load the linked audit-entry procedure and perform
its metadata-only lookup. Apart from that exception, the only permitted command
is this renderer's no-input preview mode:

```bash
python3 -B <skill_dir>/scripts/conversation_renderer.py --stage orientation --journey create
```

Replace the journey with `audit` or `expand`, or omit it for an undecided request.
Use `--stage goal_selected --journey <selected-journey>` after goal selection.
The renderer may load its own source dependencies, but must not inspect repository
files or saved state, validate Ethos, check prerequisites, write files, or delegate
work. Apart from the audit-entry lookup, no other helper commands are allowed before readiness. This also applies
to direct first-eval and full-audit invocations.

After the actual readiness reply, follow the
[shared prerequisite procedure](references/shared-prerequisites.md#check-the-agent-and-setup).
It owns the check sequence, state-failure handling, transition into Ethos, and
final response for fresh flows, prerequisite resumes, goal switches, and direct
invocations. Continuations follow its [saved-progress rules](references/shared-prerequisites.md#save-and-resume)
instead of repeating orientation.

| Request | Route after shared prerequisites and Ethos |
|---|---|
| Build first evals | `eval-author-first-eval`; confirm reuse of any saved design on a fresh request before continuing its unfinished stage |
| Audit coverage, gaps, and improvement opportunities | `eval-author-audit`, continuing with eval and trace selection |
| Improve or expand evals | Preserve the expansion goal and use the expansion handoff below; an audit is optional |
| Create a task from a recorded interaction | [Task-create ATIF source mode](../eval-author-task-create/SKILL.md#atif-source-mode), or [first-eval](../eval-author-first-eval/SKILL.md#atif-as-a-starting-source) for a new suite; use the [shared ATIF handoff](references/trace-input.md), without requiring an audit gap |
| Explicit trace-environment request or existing workspace continuation | [Trace environment](../eval-author-trace-environment/SKILL.md); retain existing artifacts and the compatibility workflow |

Acceptance of the first-evaluation flow's [audit invitation](references/conversations/first-eval.md#blockers-and-the-audit-invitation)
and explicit audit requests after creation or improvement use the same
[audit continuation](references/shared-prerequisites.md#return-to-an-audit).
Offering an invitation or receiving a decline starts no audit work.

When the goal is unclear, use `orientation` without a journey. Its shorter
welcome and canonical goal choices ask only for the unresolved goal; reuse known
context.

Explicit inventory, planning-only, readiness, repair, trace inspection, supported NeMo Gym or MLflow
trace conversion, trace-derived authoring, proposal-only, and audit
generate/validate/measure/aggregate requests keep their
scope. Explanation-only requests do not start a workflow. Scoped operations reuse
applicable shared decisions but do not acquire unrelated onboarding requirements.
Trace-source selection remains required before searching or reading traces.

### Shared prerequisites and Ethos

Read [Shared prerequisites and Ethos](references/shared-prerequisites.md) for
the common procedure, shared state, resumption, and exact handoff. It selects the
flow-specific checks and calls the [Ethos adapter](references/ethos-confirmation.md),
which owns document review, destination handling, and the canonical Ethos handoff.
The [shared rendering contract](references/conversation-rendering.md) and
[onboarding input/output contract](references/conversations/onboarding.md) own
renderer use and canonical copy. Follow the shared procedure's
[completion pause](references/shared-prerequisites.md#pause-after-ethos-confirmation)
for new shared flows; returning audits use the continuation route above.

### Expansion handoff

An expansion request does not imply an audit. Carry supplied suite context,
requested additions, confirmed Ethos, prerequisites, and pending decisions into
the selected authoring flow. Resolve an unsupported suite format during
[shared prerequisites](references/shared-prerequisites.md#check-the-agent-and-setup),
before Ethos. For an unsupported suite, follow the
[improvement prerequisite blocker](references/pre-reqs/improve.md#checks-and-blocking-conditions):
the renderer supplies a copyable conversion handoff for a separate coding-agent
chat. Keep the blocker until the converted suite's actual format is checked;
Eval Author does not perform the conversion. Supply the exact loaded core skill
link for the canonical blocker attribution and present the rendering unchanged,
without appending another pause explanation.
For a starter suite created by first-eval, resume its scope stage. For supplied
reviewed audit findings, use `eval-author-task-create` proposals and its existing
eligibility checks. If no supported authoring flow matches the supplied suite,
save the pending handoff in shared state and explain the missing capability.
An explicitly selected addition or proposal-only request keeps that scope.

### Downstream progress

Once prerequisites and Ethos are confirmed, follow the
[completion pause](references/shared-prerequisites.md#pause-after-ethos-confirmation)
for a new shared flow, or continue the already-requested returning audit.
For fresh creation without a saved design or source choice, that pause asks for
task sources directly; do not add a separate readiness question before it.
Other flows retain their selected continuation and saved decisions.
Keep the selected third checklist item current. Reuse completed shared steps.
Keep scope and evidence reviews, source selection, and execution authorization.
For creation and improvement, that item covers building the agreed cases or
changes, running them, and reviewing results. Leave it incomplete while required
execution or review remains blocked or unfinished.

When the user asks a side question, answer it, then resume the pending decision
or unfinished work. A side question does not confirm pending content.

## The standard

**Every fact you record comes from authoritative evidence, not a guess.**

The authority depends on the sub-flow:

- For suite discovery, the selected provider's validators and execution checks
  judge readiness: NeMo Gym for native manifests and Harbor for its configs and tasks.
  File presence alone does not prove runnability.
- For first evals, Ethos establishes intended behavior. NeMo Gym's positive/negative
  controls or Harbor's NOP and Oracle check basic task wiring and verifier
  behavior; the user's agent run establishes a baseline. Working setup does not
  establish evaluation quality or coverage,
  and a plan alone is not proof of runnability.
- For task environments, the environment's Harbor smoke runs or NeMo Gym native
  controls establish that its dependencies, starting data, reset, and end-state
  export work. Build files and an environment plan are not that proof.
- For audit-spec validation, the bundled schema and validator judge the finite
  `audit.md` coverage denominator.
- For task creation, NeMo Gym's positive/negative controls and native execution, or
  Harbor's Oracle, check task behavior. Measured ATIF proves whether repeated
  real-agent runs close the selected gap; reference controls do not measure agent ability.
- For trace inspection, Intake establishes what happened. Local source code can
  explain behavior, but it can't replace recorded trace evidence.
- For experimental trace-derived environments, canonical ATIF establishes the request.
  Harbor's NOP and Oracle runs or NeMo Gym's native validation and repeated controls
  establish the generated task's behavior within the reported proof limits.

No sub-flow reimplements a provider's rules. When evidence can't settle a claim,
the report marks the claim unproven or uncertain.

Before executing, recovering, or reporting evaluation runs, follow
[Execution recovery](references/execution-recovery.md). Preserve the selected
execution path and original verifiers; diagnostic results cannot complete the
requested evaluation.

## Vocabulary

The sub-flows share this language, and reports use it verbatim.

| Term | Meaning |
|---|---|
| Check | One named result: `pass`, `warn`, or `fail`. Carries a message and, when it fails, a hint |
| Required | A failing required check blocks the suite. Report the suite as not ready |
| Advisory | A warning worth surfacing that blocks nothing |
| Rung | One step of a provider's validation ladder, ordered so a lower rung's failure often clears once a higher one is fixed |
| Proven | A provider judged this check. An unproven check is an observation and never evidence |
| Provider | The evaluation framework that owns the rules. NeMo Gym and Harbor are supported and compatible; follow the shared provider-selection rule |
| Finding | One trace claim categorized as `behavior`, `issue`, `recovery`, or `uncertainty`, with evidence IDs |
| Outcome | The trace assessment: `success`, `failure`, or `unknown` |

## Sub-flows

Read the selected sub-flow's own `SKILL.md` when its stage begins and follow it.
This file carries the standard and boundaries; the sub-flow carries the steps.

| Sub-flow | Use it to |
|---|---|
| `eval-author-first-eval` | Confirm prerequisites and Ethos, plan cases, and build or expand a starter suite at the agreed breadth while teaching the user how to run it |
| `eval-author-discover` | Establish whether a repository's evaluations run, name the rung that fails, and get the exact command to run them |
| `eval-author-audit` | Define intended coverage in `audit.md`, measure selected trace coverage, identify gaps, and recommend improvements supported by the findings |
| `eval-author-task-create` | Propose concrete dataset improvements from audit findings; when task creation is requested, create one eligible NeMo Gym or Harbor task and prove it with native controls and repeated measured runs |
| `eval-author-inspect-trace` | Understand one Intake trace without presuming that the trace contains a failure. Not user-invocable; this skill selects it |
| `eval-author-trace-environment` | **Experimental.** Continue an existing trace-environment workspace or handle an explicit request for this compatibility workflow |
| `eval-author-environment` | Build and prove the environment a Harbor or NeMo Gym task runs in: provider fit, how each dependency is provided, starting data and reset, end-state export, and isolation. Not user-invocable; first-eval and task-create hand it their agreed cases |

`eval-author-audit` works one level above tasks: it generates and validates the
coverage denominator, measures traces against it, and aggregates deterministic
coverage reports. `eval-author-task-create` owns the proposal step: prioritize
dataset improvements from those findings across tools, capabilities, and failure
cases while preserving their measured or unmeasured status. Proposal-only
requests stop there. Its audit-gap task-creation mode consumes only actionable
tool gaps and uses the selected NeMo Gym or Harbor native scaffolder rather than guessing a task
layout. Its ATIF source mode instead applies privacy and candidate admission without
requiring an audit gap. For a requested full workflow, proceed from audit to proposals even when
there are no eligible tool gaps; an audit-only request ends with the findings.

## Select the evaluation provider

Use the same rule for first-eval, audit-derived task creation, and trace-derived
task creation: honor the user's explicit NeMo Gym or Harbor choice; otherwise preserve
the existing suite's provider. When neither is specified, default to Harbor.
Both providers are supported, and NeMo Gym is compatible with Harbor. If the user
selects a setup using both, record which provider owns each artifact and execution
step and verify the runtimes that setup uses. For a mixed suite, preserve the
setup of the affected cases; clarify only when the target is ambiguous. The user
has the final say and can override the default or request a provider change.

All flows that need a runtime use the shared
[runtime locator and policy](references/runtime-prerequisites.md#runtime-prerequisite-checks).
Use `--flow authoring` for creation/improvement prerequisites, first-eval,
task-create, environment, and trace-derived authoring; `--flow discovery` for
readiness; `--provider harbor --flow audit` for selected ATIF measurement.
Creation and improvement require an available supported runtime before Ethos.
Audit entry checks its Python tooling without probing an evaluation runner.
This does not add runtime requirements to explicitly scoped file-only inventory,
planning-only, or provider-independent audit work. Keep the selected Python/CLI pair
and recorded version; missing setup or ambiguity never authorizes a provider switch.

Select the output provider independently of the evidence source: a NeMo Gym trace
does not require NeMo Gym output. Use the selected provider's native scaffolding,
validation, and execution path. Missing setup is a reported prerequisite, not
permission to silently switch providers.

## Keep task paths stable

Create new tasks directly in `.eval-author/tasks/<task-slug>/`, or the selected
existing suite's established task directory when that flow permits it.
Keep task files at the same path as their status changes. Include only accepted
tasks in the saved suite configuration.
Record new tasks as **Draft** in shared state after scaffolding. Record
**Accepted** only after the calling flow's existing validation, execution, and
review requirements are satisfied, with its evidence and decision provenance.
Use [Task status](references/shared-prerequisites.md#task-status) to record and
recheck that status; directory presence and run scores do not establish acceptance.
The generated suite README and report display the current status without changing
task files or introducing a separate progress record.

For tasks derived from traces, follow the trace-environment skill's
[workspace and publication rules](../eval-author-trace-environment/SKILL.md).

Before final handoff, verify that the provider configuration resolves exactly
the accepted task set, that report and rerun links use those stable paths, and
that validation receipts describe the accepted revision. Keep historical paths
and receipts intact; do not rerun models merely to update a status.

## Gather requirements and setup from evidence

During discovery and authoring source inspection, read and follow
[Requirements and setup evidence](references/authoring-context.md#gather-requirements-and-setup-from-evidence).
It defines the requirements to gather, their source and verification status,
and where to record unknowns. Reuse documentation and prior answers; ask only
about missing facts affecting the next work. Never request secret values.

## Boundaries

These hold for every sub-flow. They exist because the repository belongs to the
user, not to you.

- **Treat recorded evidence as read-only.** Never edit, delete, rename, or
  hand-regenerate measurements, receipts, gate results, summaries, or attempt
  ledgers to change a verdict. If a result looks wrong, rerun to produce new
  evidence and report the discrepancy instead of reconciling it by hand.
  Keep evidence needed for current claims available; the user owns longer-term
  result retention under the [temporary-work policy](references/shared-prerequisites.md#temporary-work-and-recovery).
- **Propose, never mutate customer source.** Read the user's source and report on
  it. Do not edit, move, or reformat any of it, including its `.gitignore`. The
  evaluation artifacts you add belong under `.eval-author/`, which is theirs to
  commit or ignore. The sole additional write scope is the requested local
  `ETHOS.md`: invoke [Ethos confirmation](references/ethos-confirmation.md) to create or make
  user-requested edits to it in the repo. Preserve existing Ethos content and
  custom sections; downstream audit measurement does not rewrite it.
  `eval-author-discover` scripts use bounded temporary files only under
  `.eval-author/.working/`; `eval-author-audit` writes only
  requested audit artifacts; `eval-author-task-create` writes only drafts,
  proposals, job outputs, and measurements there; `eval-author-trace-environment`
  writes only private, gitignored task workspaces there. For the shared ATIF flow,
  inspect-trace, task-create and environment also write their owned artifacts
  inside the selected private `.eval-author/.working/trace-inputs/` workspace.
  `eval-author-first-eval` writes plans, drafts, configs, and jobs there and uses
  the narrow Ethos write exception above. `eval-author-environment` writes
  environment kits, generated data, and smoke tasks under
  `.eval-author/.working/environments/`, copying any repository file it needs.
  All internal state, plans, scratch, locks, and intermediate inputs belong under
  `.eval-author/.working/`; final tasks, reports, run configs, and evidence retain
  their documented deliverable paths. Journey state is the sole workflow-state authority.
  Follow [Temporary work and recovery](references/shared-prerequisites.md#temporary-work-and-recovery)
  for construction files and unfinished-work recovery.
- **A missing tool is a finding, not a task.** When the provider is not installed,
  say so and stop short of proving anything. Report what you found regardless, and
  do not install the provider automatically. For NeMo Gym, report its separate runtime
  requirements and repository setup instructions; for Harbor, use discovery's
  setup guidance to explain installation and verification. An explicit request to install is
  authorization for that setup; a broad eval request alone is not.
- **Do not run without approval.** Discovery proves an existing suite can run and
  hands over the command. Task creation may run NeMo Gym verifier controls or Harbor Oracle locally, then starts
  real-agent jobs only when the user explicitly asked for or approved that spend.
  First-eval authoring may run local task sanity checks when task creation is requested;
  real-agent or paid-judge runs require authorization for that execution and spend.
- **Trusted repositories only.** Validating a config can execute repository code,
  because an agent named by import path gets imported. If the repository is not
  trusted, say so and stop.
- **Intake reads are narrow.** Only `eval-author-inspect-trace` reads Intake. It
  uses read-only `nemo intake` commands against the configured instance and
  workspace. No sub-flow discovers accounts, ingests data, uploads files, or
  changes a remote resource.

## Communicating with the user

Omit trailing periods from user-facing list items.

Do not append a footer that names or links a skill, quotes its instructions, or
explains that a skill requires you to ask or wait, unless the host explicitly
requires that disclosure. Keep the questions, check-ins, waits, and authorization
requirements unchanged.

### Explain the eval pieces as they become relevant

Before asking the user to make an authoring or setup decision, read
[Explaining evaluation components](references/authoring-context.md#explain-the-eval-pieces-as-they-become-relevant).
Use its definitions for the pieces relevant now, grounded in inspected material
and the user's answers. Explain unfamiliar grading terms before asking about them;
keep task grading distinct from coverage auditing.

### Validation and result reports

For completed discovery, validation, or execution reports, lead with the verdict
or outcome, then the evidence. This format does not apply to onboarding or an
intent question while establishing Ethos.

For proposal and task-construction evidence, use
[private construction declarations](references/construction-plan.md) to connect
authority, starting inputs, and native checks. Preserve the selected flow's scope
and review checkpoints; proposal declarations do not authorize task scaffolding.

For dataset proposals, follow `eval-author-task-create` Step 1: lead with concrete
recommendations and their evidence, then supporting coverage counts and limits. An empty
task-generation selection does not establish that the dataset needs no changes.

In validation and execution reports, state whether findings are proven, whether
the suite is ready, and the checks that failed. Never describe a suite as ready while a required check
fails, and never present an observation as proof. When a sub-flow could not reach
its provider, its validation report must state that readiness was not proven.
An empty inventory can still be discussed. Creation and improvement follow their
shared runtime prerequisite before Ethos and case planning.

For a trace, use `success`, `failure`, or `unknown`. Tie key moments and findings
to span IDs, evaluator result IDs, or source symbols. A healthy trace doesn't
need an issue finding.

## Prerequisites

Routing needs the user's desired outcome and any supplied repository or trace
location, not Harbor or credentials. Authoring needs Ethos; runtime
dependencies belong to the selected flow.

## Limitations

The router does not execute evals or establish readiness itself. Discovery is
not an audit, a coverage gap is not proof of an agent defect, and trace-derived
environments remain experimental.

## Troubleshooting

- Unclear desired outcome: ask one focused question; reuse known starting facts.
- Missing Ethos: follow [Ethos recovery](references/ethos-confirmation.md#recovery) when the
  selected flow requires it; inventory and trace inspection can proceed without it.
- Missing provider or inaccessible source: report the blocked claim and use the
  selected flow's recovery; an access failure never proves that evals are absent.

## Examples

```text
Request: "Audit the evals in ./evals; the agent Ethos is ./ETHOS.md."
Route: eval-author-audit, carrying both paths into its pre-flight.
Deliverable: coverage findings and their evidence, not a readiness-only report.

Request: "I don't know whether this repo has agent evals."
Route: eval-author-discover's inventory entry, without runtime probes.
Deliverable: candidate paths, documented run commands, and search limits.
```
