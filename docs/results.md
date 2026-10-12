<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Read your results

Eval Author saves reports and evaluation artifacts in the repository you are
working on. Start with the report for your requested workflow, and read its
limitations before deciding what to change.

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

Start at the generated suite's root `README.md` as soon as cases are authored.
For first evaluations this is `.eval-author/README.md`; standalone drafts use
`.eval-author/task-drafts/<task-slug>/README.md`. Trace candidates use
`<task-dir>/README.md`, and generated batches also have a collection-root guide.
The guide is refreshed after runs and linked in the handoff, including when
execution is blocked.

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

The plan in `.eval-author/first-eval.md` records the intended behavior, proposed
cases, and acceptance criteria. Task drafts and run results show which checks
have actually completed. Use the [generated case review guide](#review-generated-cases)
to inspect the authored cases and their available evidence.

Task controls check the task and its verifier: Gym's known-correct and negative
verifier fixtures, or Harbor's no-op baseline and reference solution. Results
from running your actual agent are separate evidence of its performance.
A valid task, a working environment, and a successful agent run are distinct
outcomes. See [Build your first evaluations](first-evals.md).

## Open readable cases and traces

Start with the generated suite's README for artifact paths and commands. Harbor
tasks and recorded jobs use its native local viewer. For Gym JSONL and standalone
ATIF, Eval Author includes an offline reader that creates a private `report.html`
you can open directly in a browser; see [Local review](../skills/eval-author/references/local-review.md)
for commands and supported formats.

Gym reports keep model input separate from verifier and other row fields. The
reader shows recorded messages, tool calls and results, and available rewards
and errors, while preserving raw records and identifying missing or unsupported
content. Select Gym records by their physical line numbers. Keep generated
cases, source evidence, task controls, and actual-agent attempts in reports
with their respective evidence roles; these are caller-supplied labels, not
verified relationships.

Read attention notices before drawing conclusions. A readable case is not a
validated task, and rendering a trace does not establish a successful run. The
HTML contains the selected source data and belongs in the same private,
gitignored workspace as the evidence. Do not publish it merely because it is
readable in a browser.

## Coverage audits

The audit uses four artifact names consistently, regardless of review state:

| Artifact | File | Role |
| --- | --- | --- |
| **Audit specification** | `.eval-author/audit.md` | Intended checks and evidence requirements |
| **Audit coverage report** | `.eval-author/audit-coverage-report.md` | Readable scope, test mappings, findings, limits, and next actions |
| **Coverage measurements (JSON)** | `.eval-author/audit-coverage-report.json` | Generated measured results aggregated over the selected runs, when available |
| **Audit progress** | `.eval-author/audit-progress.md` | Conversation state, selected paths, pending questions, and next action |

Start with the Audit coverage report. It links the specification and available
supporting evidence. A mapping to an existing test describes what that test is
designed to address; it does not establish that a recorded run satisfied the
audit's coverage criteria.

Use the report's **Intended coverage** section to review the proposed audit scope.
It lists every item's stable name and kind, explains the intended check in plain
language, and states the required evidence separately from existing tests,
observed evidence, and measurement status. Totals and gap findings alone do not
describe what the audit intends to check.

At the scope check-in, Eval Author explains these roles and states that the Audit
specification is a draft pending your review and the Audit coverage report is
preliminary. The reply previews intended checks grouped by tools, capabilities,
and failure cases, links directly to the full
**Intended coverage** section, and asks what is missing or incorrect before
moving to the named next milestone. Approving this scope is separate from
reviewing the findings at the final report milestone.

Measurement details live under `.eval-author/audit-measurements/`;
the Coverage measurements (JSON) file combines the available results. The Audit coverage
report explains what those results mean.

Without usable traces, coverage is **unmeasured**, not 0%; the specification can
still be completed and the report prepared while measurement remains
pending. The unchecked **Measure coverage** step states the reason inline,
for example “deferred — selected trace
directory is missing,” so the limitation remains visible in later reviews.

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

Open Audit progress for the current milestone, selected eval
and trace paths, limitations, answered or pending questions, and next action.
This workflow record lets Eval Author resume unfinished work without repeating
completed milestones and stays separate from the report and evidence.

At the report review, Eval Author names the main supported gaps and offers to
[propose new or improved evaluation tasks](existing-evals.md#propose-new-evaluations).
It asks whether to begin, ends the turn, and waits for your answer. Reviewing the
report alone does not start proposal work.
Accepting the findings and this offer starts the proposal workflow directly,
reusing the report, specification, selected sources, and prior answers. Its
recommendations are ranked with expected behavior, verifier design, and evidence,
and saved in `.eval-author/proposals/dataset-recommendations.md`. Creating or
running the proposed tasks remains separately scoped.

The next-step offer adapts to the findings: with no evaluations, Eval Author
offers first-eval creation; with missing evidence, it recommends the needed
measurement and labels any candidate proposals unmeasured. If more tasks would
not address the findings, it explains a useful alternative, such as an agent fix
or gathering specific evidence.

## Trace workflows

Intake inspection writes a report under `.eval-author/traces/` with observed
behavior, issues, recoveries, and uncertainty. Review the cited trace evidence
before treating an observation as a general pattern.

The **experimental** trace-to-environment workflow writes a workspace under
`.eval-author/trace-environments/` containing a summary and the evidence for its
candidate and validation decisions. A `no_candidate` result means the available
evidence did not support a task.
A generated candidate can still have an unproven environment or unresolved
software requirements. Read those limits before attempting execution or
[sharing a task](traces.md#sharing-generated-tasks).

Converting a trace to ATIF, the Agent Trajectory Interchange Format, produces a
structured record for downstream tools. Conversion alone does not measure
coverage or validate a runnable evaluation task.

## Saved output

These are typical paths relative to the repository being evaluated. An existing
Ethos can be reused from a different location, and workflows may stop before
producing every artifact when prerequisites are missing.

| Workflow | Main outputs |
| --- | --- |
| First evaluations | `ethos.md`, `.eval-author/first-eval.md`, `.eval-author/README.md`, `.eval-author/task-drafts/`, and run results under `.eval-author/runs/` for Gym or `.eval-author/jobs/` for Harbor. Harbor suites also get `.eval-author/first-eval.yaml` when the agent integration supports it. |
| Discovery and readiness | `.eval-author/discovery.md` |
| Coverage audit | The four artifacts listed under [Coverage audits](#coverage-audits), plus supporting evidence in `.eval-author/audit-measurements/` |
| Intake trace inspection | `.eval-author/traces/` |
| Trace-derived environments (**experimental**) | A private, gitignored workspace per task under `.eval-author/trace-environments/`, with a root `README.md` for generated-case review and reruns; generated batches also have a collection-root guide. No-candidate outcomes do not create a suite guide. |
| MLflow conversion | One `.atif.json` file per trace in the private output directory you select. |

[Choose another workflow](../README.md#start-here) or review
[runtime requirements](getting-started.md#requirements).
