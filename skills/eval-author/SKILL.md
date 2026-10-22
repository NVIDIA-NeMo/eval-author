---
name: eval-author
description: >-
  For fresh creation, audit, expansion, or undecided eval-help requests, read
  this skill, show its fixed welcome with a checklist and call to action or goal
  choices, then end the turn before repository work. Scoped utilities keep their
  requested scope.
  Build first evals from a required Ethos, work on existing evaluation suites
  in a user's repository, derive an environment from trace
  evidence (experimental), or understand an agent run from NeMo Intake. Owns the evidence
  standard that every Eval Author sub-flow
  follows. Use when the user asks "help me with my evals", "help me onboard to expand my evals",
  "what's the state of the eval suite here?", "what happened in this trace?", or
  when you need to pick between bootstrapping evals, auditing existing evals,
  finding out whether evals exist, or proposing improvements from an audit.
  Requires a confirmed ETHOS.md for creating, auditing, and improving
  evaluations, without changing the agent's implementation. The selected
  sub-flow uses the provider's supported tools and saves findings under
  `.eval-author/`.
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
  - eval-author-first-eval (use to establish Ethos and build first evals without prior coverage or traces)
  - eval-author-discover (use to run the discovery pass and get a runnable verdict)
  - eval-author-audit (use to generate, validate, measure, or aggregate audit.md coverage)
  - eval-author-task-create (use to propose dataset improvements; when task creation is requested, create and prove an eligible NeMo Gym or Harbor task)
  - eval-author-inspect-trace (use after this skill selects the trace sub-flow)
  - eval-author-trace-environment (experimental; use to derive a Harbor or native NeMo Gym task from canonical ATIF evidence)
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

The first response to a new shared flow is orientation only. Determine the goal
from the user's request and conversation already available, without inspecting the
repository. If known, show only the fixed welcome, matching checklist, and call
to action below.
If unknown, show only that welcome, the goal question, and its three choices.
**End the turn immediately after that response.**

When the user answers the goal question, show the fixed sentence for the selected
goal below, followed by the matching three-item checklist and **Ready to get started?**
as the entire final visible response, then end the turn. The welcome was already shown;
use the same checklist wording and
current-step marker as the known-goal opening. Selecting a goal does not skip
this overview or start prerequisite checks.

Before either orientation response, read only the entry skill and this core skill
as needed.
Do not inspect repository files or saved state, validate Ethos, check prerequisites,
run helper commands, write files, or delegate work. This also applies to direct
first-eval and full-audit invocations. Prerequisite questions and findings belong
to later turns. Only after the user replies to **Ready to get started?**, show the
prerequisite table from the shared procedure, then inspect the accessible prerequisites before asking about
anything unresolved. If no Blockers remain, show the selected checklist under
**Next Steps** after the results table and any Recommendations. If blocked, end with the
Blockers and omit Next Steps, the checklist, and Ethos guidance.
Continuations and goal switches within an existing shared flow reuse progress
rather than repeating this pause.

| Request | Route after shared prerequisites and Ethos |
|---|---|
| Build first evals | `eval-author-first-eval`, continuing at the first unfinished authoring stage |
| Audit coverage | `eval-author-audit`, continuing with eval and trace selection |
| Improve or expand evals | Preserve the expansion goal and use the expansion handoff below; an audit is optional |

An explicit acceptance of the first-evaluation workflow's closing audit invitation
is a goal switch within the existing flow. Reuse the confirmed `ETHOS.md`, task locations, provider,
selected run identities, available traces, and limitations. Do not repeat the
welcome or unchanged document review. Apply audit-specific prerequisite checks
and source selection before measurement; missing audit evidence blocks the audit
without undoing completed creation. Offering the invitation or a declined reply
does not start audit work. Audit acceptance does not authorize expansion or new
paid runs. Follow [first-eval state](references/first-eval-state.md) for the saved
invitation decision and handoff.

When the goal is unclear, ask “What would you like to do?” and present these
options as a bulleted list:

- Build your first evaluations
- Audit your existing coverage
- Improve your evaluation suite

Reuse known context and ask only for the unresolved goal.

Explicit inventory, planning-only, readiness, repair, trace inspection, supported NeMo Gym or MLflow
trace conversion, trace-derived authoring, proposal-only, and audit
generate/validate/measure/aggregate requests keep their
scope. Explanation-only requests do not start a workflow. Scoped operations reuse
applicable shared decisions but do not acquire unrelated onboarding requirements.
Trace-source selection remains required before searching or reading traces.
Adaptation is disabled: explain this for evaluation-framework conversion
requests, preserving the user's existing material rather than treating it as
absent. Supported trace conversion retains its existing dedicated skills.

### Shared prerequisites and Ethos

Copy this welcome, checklist, and call to action verbatim into the final user-facing
response, rendered as Markdown. They are the entire opening response when the goal
is known from the initial request; after goal clarification, replace the welcome
with the selected goal's sentence below, followed by the checklist and call to
action. Do not append findings or a prerequisite
question. Progress or thinking messages alone
do not satisfy this opening.

Welcome to Eval Author! I’ll help you evaluate your agent against the behavior you expect. Here’s what we’ll work through:

- [ ] **Check how your agent runs and how you’ll test it** — **We're here**
- [ ] **Agree on how your agent behaves using [ETHOS.md](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/README.md)**
- [ ] **Create your first evaluations**

Ready to get started?

After goal clarification, use exactly one of these sentences above the checklist:

| Selected goal | Introductory sentence |
|---|---|
| First evaluations | We’ll build your first evaluations. Here’s what we’ll work through: |
| Audit | We’ll check what your existing evaluations cover. Here’s what we’ll work through: |
| Improvements | We’ll improve your existing evaluations. Here’s what we’ll work through: |

For an audit, replace the third item with **Check what your existing evaluations cover**.
For improvements, use **Improve your existing evaluations**. These exact third-item
labels, completed checkboxes, and the position of **We're here** are the only
variations. Keep the welcome, checklist wording, order, and Ethos link unchanged.
Keep scope limits and other explanations outside the checklist items. For
prerequisite results, use the **Next Steps** placement defined in the shared
procedure. On resume, use the same checklist wording
with saved progress and the pending question instead of the opening call to action;
do not reset completed items.
After the opening turn, explain findings and ask for missing decisions or content confirmation.
Record user-confirmed prerequisites separately from runtime verification.
Mark each step complete when its work and required review are finished.

Read [Shared prerequisites and Ethos](references/shared-prerequisites.md) for
the common procedure, shared state, resumption, and the exact handoff. It selects
the prerequisite table and checks for [creation](references/pre-reqs/create.md),
[audit](references/pre-reqs/audit.md), or [improvement](references/pre-reqs/improve.md).
Keep the selected table fixed and reassess requirements when the goal changes.

Use `ethos-orchestrate` to review, create, or update `ETHOS.md`. Pass along the
context and answers already collected. For an existing document, ask the user to
confirm it is the right Ethos for this agent and accurately describes its current
intended behavior. A saved confirmation from an earlier flow does not
replace this review. When [Ethos confirmation](references/ethos-confirmation.md)
is complete, show the [completion pause](references/shared-prerequisites.md#pause-after-ethos-confirmation)
before resuming the selected evaluation flow.

The shared prerequisite flow confirms repository-root `ETHOS.md`. Preserve supplied
alternate-path documents and resolve the root requirement without silently copying
or replacing them. Scoped utilities retain their explicit `--ethos` path behavior.

### Expansion handoff

An expansion request does not imply an audit. Carry supplied suite context,
requested additions, confirmed Ethos, prerequisites, and pending decisions into
the selected authoring flow. Resolve an unsupported suite format during
[shared prerequisites](references/shared-prerequisites.md#check-the-agent-and-setup),
before Ethos. If the suite uses an unsupported format, ask the user to convert
it to NeMo Gym or Harbor before proceeding.
For a starter suite created by first-eval, resume its scope stage. For supplied
reviewed audit findings, use `eval-author-task-create` proposals and its existing
eligibility checks. If no supported authoring flow matches the supplied suite,
save the pending handoff in shared state and explain the missing capability.
An explicitly selected addition or proposal-only request keeps that scope.

### Downstream progress

Once prerequisites and Ethos are confirmed, present the completion pause and wait
for the user to continue before following the selected skill's stages.
Keep the selected third checklist item current. Reuse completed shared steps.
Keep scope and evidence reviews, source selection, and execution authorization.

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
| `eval-author-audit` | Generate and validate a finite `audit.md` coverage denominator, measure and aggregate trace coverage, and report findings |
| `eval-author-task-create` | Propose concrete dataset improvements from audit findings; when task creation is requested, create one eligible NeMo Gym or Harbor task and prove it with native controls and repeated measured runs |
| `eval-author-inspect-trace` | Understand one Intake trace without presuming that the trace contains a failure. Not user-invocable; this skill selects it |
| `eval-author-trace-environment` | **Experimental.** Normalize one trace to ATIF, make a privacy-reviewed candidate decision, and build a private Harbor or native NeMo Gym task when evidence supports it |
| `eval-author-environment` | Build and prove the environment a Harbor or NeMo Gym task runs in: provider fit, how each dependency is provided, starting data and reset, end-state export, and isolation. Not user-invocable; first-eval and task-create hand it their agreed cases |

`eval-author-audit` works one level above tasks: it generates and validates the
coverage denominator, measures traces against it, and aggregates deterministic
coverage reports. `eval-author-task-create` owns the proposal step: prioritize
dataset improvements from those findings across tools, capabilities, and failure
cases while preserving their measured or unmeasured status. Proposal-only
requests stop there. Its subsequent task-creation path consumes only actionable
tool gaps and uses the selected NeMo Gym or Harbor native scaffolder rather than guessing a task
layout. For a requested full workflow, proceed from audit to proposals even when
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
  evidence, keep the old evidence, and report the discrepancy instead of
  reconciling it by hand. Archiving unchanged evidence is fine; preserve its
  linked artifacts and relative layout.
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
  writes only private, gitignored task workspaces there.
  `eval-author-first-eval` writes plans, drafts, configs, and jobs there and uses
  the narrow Ethos write exception above. `eval-author-environment` writes
  environment kits, generated data, and smoke tasks under
  `.eval-author/.working/environments/`, copying any repository file it needs.
  All internal state, plans, scratch, locks, and intermediate inputs belong under
  `.eval-author/.working/`; final tasks, reports, run configs, and evidence retain
  their documented deliverable paths. Journey state is the sole workflow-state authority.
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
environments remain experimental. Existing-eval adaptation is disabled.

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
