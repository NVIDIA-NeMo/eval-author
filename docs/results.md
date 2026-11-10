<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Read your results

Eval Author saves reports and evaluation artifacts in the repository you are
working on. Start with the report for your requested workflow, and read its
limitations before deciding what to change.

## Case and run catalog

The case and run catalog can be found in `.eval-author/report.html` and the
`.eval-author/README.md` which is a lighter weight version for agents and humans
who prefer markdown. These catalogs provide provide a high level overview of tasks
and runs, and link to the corresponding assets on disc, as well as provide links
to the Harbor viewer for Harbor generated tasks.

To refresh the catalog, ask: “Use Eval Author to refresh the HTML report for evaluations in
this repository.” The report is saved at `.eval-author/report.html`, with Tasks,
Runs, and viewer Settings. The same command maintains the task and run tables inside a m
arked section of `.eval-author/README.md`.

See the [Report generation](../skills/eval-author/references/suite-readme.md#report-generation)
for selected roots, metadata, and CLI usage. The report reads existing artifacts;
it does not run evaluations. Missing task descriptions show `Not documented`.
The Runs table has one row per native Harbor job, using its exact directory name
and path. The reward graph combines scored trial rewards, including partial
credit, using the documented reward range. Execution completion and scored trial
counts appear separately; an incomplete job's score is labeled partial. Missing
rewards do not become zeroes. Without a documented reward range, the numeric
total appears without a graph. Native scores without a documented pass rule
are not labeled passed or failed. Standalone controls and other individual trials
remain accessible in the separate Legacy trials section and are not combined
into inferred jobs.

## Discovery and readiness

Open `.eval-author/discovery.md`. An inventory describes the evaluations found,
what they test, and documented or source-derived run commands. It does not prove
that the suite can run. A report that found no evaluations describes the scope
inspected; it is not a claim about material outside that scope.

A readiness report adds Harbor validation evidence and any blockers. Check which
validation steps completed, which could not run, and what needs attention. A
missing backend or credential is a readiness problem, not a failed agent test.
See [Check readiness](existing-evals.md#check-readiness) for the next step.

## Review generated cases

Use the **Tasks** tab in `.eval-author/report.html` to review tasks at a high
level throughout their lifecycle. Once a task has been created, open its README
or use the Harbor viewer, where available, for a closer look.

**Proposed** tasks have not been created yet.

Standalone drafts use `.eval-author/tasks/<task-slug>/README.md`. Trace candidates use
`<task-dir>/README.md`, and generated batches also have a collection-root guide.
The guide is refreshed after runs and linked in the handoff, including when
execution is blocked.

Task README files allow users to inspect its Input, Behavior under test,
Expected result, and Verifier sections. Input links the instruction and fixtures.
Verifier links the actual verifier code and criteria or judge files. The report's
Expected outcome column shows the opening summary paragraph from Expected result;
open the task README for its detailed assertions and expected values. Existing
sections that begin with a list or table continue to display their full text.

At the accepted-task handoff, create a flat `.eval-author/tasks/` catalog for new
environments; follow the existing final task-directory convention for existing
environments. Future suite commands point to the accepted tasks in that catalog.

Its case inventory explains what each case tests, its input, expected outcome,
and grading criteria, with links to task and verifier details. Readable summaries
and links let you review the cases without reconstructing the conversation or
formatting raw JSONL. Source evidence used to design a case is labeled separately
from task validation controls and actual-agent attempts. A source trace does not
prove that the generated case passed.

Follow the guide's result links and verified viewer commands where available to
inspect saved evidence. Unexecuted cases remain visible as **not run**; missing
traces or other evidence are called out explicitly. Controls that passed do not
stand in for agent results. The same guide saves setup, full-suite and coverage
item rerun commands, and known execution blockers.

## First evaluations

The HTML report and Markdown catalog show proposed, authored, validated, and
executed task totals for your full saved plan. Use the
[generated case review guide](#review-generated-cases) to inspect cases and their
available evidence.

Task controls check the task and its verifier: NeMo Gym's known-correct and negative
verifier fixtures, or Harbor's no-op baseline and reference solution. Results
from running your actual agent are separate evidence of its performance.
A valid task, a working environment, and a successful agent run are distinct
outcomes. See [Build your first evaluations](first-evals.md).

## Open readable cases and traces

Start with the html report or the README.md for artifact paths and commands. Harbor
tasks and recorded jobs use its native local viewer. Report links target the
Harbor 0.24.0 viewer; refresh the report after upgrading to regenerate trial links.
For NeMo Gym JSONL and ATIF, MLflow, or NeMo Gym traces, Eval Author includes an
offline reader that creates a private `report.html`
you can open directly in a browser; see [Local review](../skills/eval-author/references/local-review.md)
for commands and supported formats.

NeMo Gym reports keep model input separate from verifier and other row fields. The
reader shows recorded messages, tool calls and results, and available rewards
and errors, while preserving raw records and identifying missing or unsupported
content. Select NeMo Gym records by their physical line numbers. Keep generated
cases, source evidence, task controls, and actual-agent attempts in reports
with their respective evidence roles; these are caller-supplied labels, not
verified relationships.

Read attention notices before drawing conclusions. A readable case is not a
validated task, and rendering a trace does not establish a successful run. The
HTML contains the selected source data and belongs in the same private,
gitignored workspace as the evidence. Do not publish it merely because it is
readable in a browser.

## Coverage audits

Each audit records its approach. A task review checks existing tasks, fixtures,
and verifiers against intended behavior. A measured audit includes that review
and adds coverage analysis from selected recorded runs. See
[Audit coverage](existing-evals.md#audit-coverage) for the initial choice and
token tradeoff.

The Audit coverage report is the only document you review; other outputs support
internal validation, measurement, and saved progress:

| Artifact | File | Role |
| --- | --- | --- |
| **Audit specification (internal)** | `.eval-author/audit.md` | Intended checks and evidence requirements |
| **Audit coverage report** | `.eval-author/audit-coverage-report.md` | Readable scope, test mappings, findings, limits, and next actions |
| **Coverage measurements (internal JSON)** | `.eval-author/audit-coverage-report.json` | Generated measured results aggregated over the selected runs, when available |
| **Report content (internal)** | `.eval-author/.working/audit/report-content.json` | Saved report text and evidence references |
| **Journey state (internal)** | `.eval-author/.working/journey-state.json` | Saved scope and review decisions, partial progress, pending questions, and next action |

When you switch from creating or improving evaluations into an audit, the
originating journey's `scopes` entry keeps its last question and handoff in
`audit_entry`, with the agent and state revision. This context survives an
interrupted turn. The top-level pending question still belongs to the active
conversation.

Start with the Audit coverage report. It contains the complete proposed checks
and links available supporting evidence. A mapping to an existing test describes what that test is
designed to address; it does not establish that a recorded run satisfied the
audit's coverage criteria.

The report opens with a Summary (including an ADVISORY when verifier cheating is
confirmed and the Evaluation coverage table), then Key findings. Recommended
next actions is a single list, highest priority first, that names each finding or
advisory, the capabilities and tools it involves, and the proposed task.
The Coverage report maps each audit item to its tasks and run evidence, followed by the task
catalog, verifier cheat screening, scope and execution evidence, and
definitions.

The audit report is generated. Ask Eval Author to revise and regenerate it when
you need changes. Direct Markdown edits block refresh.

Older manually written reports remain readable. Ask for conversion before
regenerating one. Conversion preserves the original report and historical
recommendation files.

Reports do not yet compare coverage across audits.

Measurement details live under `.eval-author/audit-measurements/`;
the Coverage measurements (JSON) file combines only the current selected inputs.
The Audit coverage report explains what those results mean. Journey state saves
a measurement summary and references its evidence; failed inputs and missing
methods keep measurement partial.

Saved audit evidence links original traces and selected suite runs to loaded
traces, their `loading.json` receipts, and measurements. Journey state references the saved
selection manifest. New manifests use
`.eval-author/.working/audit/selection-manifest.json`, outside the source
locations. Resumed audits keep their saved manifest path. Changing the original
source or its selection reopens check review even if the prepared trace is
unchanged. Changed prepared evidence reopens measurement.

Without usable traces, coverage is **unmeasured**, not 0%; the specification can
still be completed and the report prepared while measurement remains
pending. Deferral is recorded only after your explicit choice of independent work.
The unchecked **Measure coverage** step states the reason inline,
for example “deferred — selected trace
directory is missing,” so the limitation remains visible in later reviews.

A task review deliberately leaves run coverage unmeasured. It can complete after
you accept its findings, without a measurement JSON file or a deferred step.
The incomplete-measurement rules above apply to measured audits. Resuming either
approach preserves its choice and reviews changed inputs before reusing results.

A covered item has supporting evidence under the audit's declared criteria.
An uncovered item may lack evidence in the measured traces, or its evidence type
may not have been measured at all. Review that distinction before proposing a
new evaluation. Coverage does not establish overall agent quality or prove that
an unobserved behavior never occurs.

The final checklist step, **Generate and review coverage report**, creates or
updates the Audit coverage report and reviews its findings with you. The review
stays pending until you respond to the check-in. Whenever a session creates or
updates the report, its final response explicitly says so and links it, even if the
audit stops at an intermediate checkpoint. When an audit resumes, changed
inputs are reconciled with the report before its findings are treated as current.

The audit also inspects selected tasks' grading paths and reconciles retained
cheat-control results before findings review. Findings distinguish verifier
defects, agent failures, rejected valid solutions, and ungraded execution.
Failed controls remain relevant even when a task has no usable agent trajectory.
A later passing control doesn't erase a failure on the same task revision.
Missing or stale evidence leaves screening unproven. These verifier checks do
not establish agent coverage. Planned control definitions and expected rejection
stay in the task README's **Verifier** section. Legacy **Grading** and **Grader**
headings remain supported.

Journey state saves audit decisions and the pending checkpoint across chats
and workflow switches. Accepting findings records the answer without rewriting
the report to mark it accepted. See [audit resumption](existing-evals.md#audit-coverage)
for how changed inputs reopen review and measurement.

For the combined findings review and optional next-action handoff, see
[Audit coverage](existing-evals.md#audit-coverage).

### Identity-aware coverage results

When measurement uses the paired
[`--identity-root` and `--identity-selection` flags](existing-evals.md#measure-coverage-with-an-explicit-identity-selection),
the CLI summary and each per-method `coverage.json` include `identity_preflight`:

- `identity` retains selected artifact records, access states, digests, nullable
  revisions, and stable diagnostics.
- `route` retains the declared scorer and dependency checks. `executed_scorer`
  stays null: resolving a declaration is not evidence that this scorer ran.
- `route_request` retains the selection used to resolve that route. Aggregation
  and retained-report verification recompute the route from the selected files
  and reject an envelope that disagrees with the result.
- `subject` keeps the measured `task_id` and `run_id`, including IDs from
  native trial metadata when you supply no override.
- `measurement_allowed` means the selected identity and source-bound declared
  route passed the gate. The measured trace must also match its selected path
  and digest before the CLI writes coverage.
- `preflight_success` remains false even for a successful coverage measurement.
  This envelope does not certify workflow readiness, task quality, or rubric
  criteria.

The aggregate Coverage measurements (JSON) preserves the envelope in each
`input_reports` entry alongside that input's subject and coverage accounting.
Aggregation rejects unresolved or inconsistent envelopes rather than upgrading
them into passing evidence. It validates retained measurement data without
attesting to a fresh native execution. Ordinary reports without the opt-in
fields remain supported.

Identity-aware reports without `route_request` must be measured again before
aggregation. A route envelope alone cannot establish its source binding.

Identity failures leave coverage unmeasured, not 0%. They do not erase earlier
output files. Do not present an older result as the failed attempt's measurement.

## Trace workflows

Intake inspection writes a report under `.eval-author/traces/` with observed
behavior, issues, recoveries, and uncertainty. Review the cited trace evidence
before treating an observation as a general pattern.

The **experimental** trace-to-environment workflow writes a workspace under
`.eval-author/.working/trace-environments/` containing a summary and the evidence for its
candidate and validation decisions. A `no_candidate` result means the available
evidence did not support a task.
A generated candidate can still have an unproven environment or unresolved
software requirements. Read those limits before attempting execution or
[sharing a task](traces.md#sharing-generated-tasks).

Loading a trace into a NeMo Compass `Trace` produces a structured record for
downstream tools. Loading alone does not measure coverage or validate a
runnable evaluation task.

## Saved output

Shared internal progress and agreed scope are stored in
`.eval-author/.working/journey-state.json`; they are not a second report or plan.
When an audit reopens authoring review, legacy scope values that are not JSON
objects are retained under `legacy_context` alongside the scope's review flags.

`.eval-author/.working/` holds temporary construction files and the current state
needed to recover unfinished work. Final task files and READMEs stay at their
documented paths; source control manages task and verifier history. Eval Author
does not create revision archives. You decide how long to keep evaluation
results; they are not automatically expired or deleted.
Routine update payloads, state-response copies, renderer inputs, and polling
responses are temporary. Managed scratch is removed on ordinary completion or
errors; that does not clear existing user files or results.

These are typical paths relative to the repository being evaluated. The shared
prerequisite flow keeps the selected `ETHOS.md` at its existing agent-local or
repository-root path; see [document selection](getting-started.md#load-the-skills).
Changing that selection reopens affected approvals in the current state.
Workflows may stop before producing every artifact when prerequisites are missing.

| Workflow | Main outputs |
| --- | --- |
| First evaluations | `ETHOS.md`, `.eval-author/README.md`, `.eval-author/tasks/`, and run results under `.eval-author/runs/` for NeMo Gym or `.eval-author/jobs/` for Harbor. Harbor suites also get `.eval-author/run.yaml` when the agent integration supports it. |
| Discovery and readiness | `.eval-author/discovery.md` |
| Coverage audit | The four artifacts listed under [Coverage audits](#coverage-audits), plus supporting evidence in `.eval-author/audit-measurements/` |
| Intake trace inspection | `.eval-author/traces/` |
| Trace-derived environments (**experimental**) | A private, gitignored workspace per task under `.eval-author/.working/trace-environments/`, with a root `README.md` for generated-case review and reruns; generated batches also have a collection-root guide. No-candidate outcomes do not create a suite guide. |

[Choose another workflow](../README.md#choose-your-journey) or review
[runtime requirements](getting-started.md#requirements).
