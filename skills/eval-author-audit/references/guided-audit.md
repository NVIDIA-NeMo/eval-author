<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Audit milestones after prerequisites and Ethos

The shared prerequisite flow owns the opening checklist, prerequisite answers,
Ethos confirmation, and shared resumption. A direct full audit uses that same flow. Afterward keep
**Check what your existing evaluations cover** current in the three-item checklist while explaining the
current coverage milestone. There is no second audit opening or Ethos checkpoint.

## Decisions and progress

Complete authorized work, explain the result and limits, and link its artifacts.
Ask for missing source, scope, or content decisions; reuse prior answers. A
pending source selection blocks reads from that source. A pending content review
blocks dependent measurement. Do not require generic stage-transition acceptance
when the next step is already selected. Retain explicit deferrals and continue
independent work that is already authorized.

Store suite and trace selections separately, including candidates, exclusions,
and source bounds, in `.eval-author/.working/journey-state.json`. It also owns
completed stages, accepted content, unanswered questions, deferrals, evidence
references, and next action. Do not write an `audit-progress.md`. Import explicit
legacy decisions once with provenance; a saved file alone is not confirmation.
Keep findings in `audit-coverage-report.md` and measurements in their native
artifacts. Link the Markdown report whenever it is created or updated.

On return, resume the pending decision or earliest unfinished, non-deferred
stage. Changed Ethos, audit items, or traces reopen affected work and mark its
findings stale or unmeasured. Preserve old measurements rather than altering them.
The confirmed Ethos comes from [Ethos confirmation](../../eval-author/references/ethos-confirmation.md)
and is not reviewed again unless relevant content changes.

## 1. Confirm evals and traces

Inputs: the evaluations to audit, or the user's statement that none exist, and
the source and location of any traces to use. Reuse paths and answers from the
current conversation, discovery, or first-eval handoff when the user has selected
them for this audit. A trace records what the agent actually did during a run;
an eval definition or score alone is not that record.

Start by identifying existing evaluations from the named agent documentation,
selected eval source, or a prior inventory. Before asking about traces, report
what was found with a clickable path to each suite's directory, runner, or README
and a short description of what it tests. Link shared case definitions when useful,
but do not substitute only a case-matrix link or runner names for the suite
locations. Treat discovered suites as candidates until the user selects them.
Keep this a concise inventory; read bounded suite documentation and case
definitions to identify candidates, leaving detailed grading analysis for the
later specification and findings milestones.

For a full guided audit, unless the user already explicitly selected these
suites, end the turn with the linked inventory and a question such as: “Are these
the evaluation suites you want to audit, or should we add or exclude any?” Keep **Confirm evals
and traces** current and wait for the answer before asking about traces,
inspecting even previously selected traces, or drafting coverage. Showing the
inventory is not confirmation. A reply supplying only a trace location or saying
there are no traces leaves suite selection pending; retain that trace answer and
ask only the unanswered suite question.

An explicit request such as “audit suites A and B at these paths” already
selects that set; summarize it without asking again. A path supplied only as a
place to look, a prior discovery inventory, or a saved candidate path does not.
Apply additions and exclusions before proceeding. On resume, reuse confirmed
choices and present any still-pending suite selection before continuing.

If the user confirms no evaluations exist, say so without searching to prove it.
If the location is unknown or inaccessible, report that limit and ask for the
eval location first; do not claim absence or imply that an inventory succeeded.
Explain traces in ordinary language before the source question: recorded agent
runs show which tools and behaviors were actually exercised; test definitions
describe what was planned, and pass/fail scores alone cannot establish that
coverage. State that the specification can still be prepared without run records,
while measurement would remain unmeasured.

Before searching for, listing, opening, converting, or measuring traces, confirm
with the user both their source and location. Ask a concrete question, such as:
“Which run records should this audit use, and where are they saved?” A local file
or bounded directory plus the identified run source is sufficient; do not demand
an exact filename before searching a user-selected directory. An explicit request
such as “use the Harbor traces in `runs/release-check/`” already settles this
selection. Reuse that answer without another approval prompt. “Use available
traces,” an automatically discovered path, or a path mentioned by documentation
does not select a source. If an earlier handoff merely lists candidate paths,
ask which to use before reading them.

This rule also applies to tracing runtime tool names while drafting audit items,
opening traces referenced by a report, and implicit trace scans inside discovery
or conversion tools. Before presenting a missing-source question, inspect named
agent docs, selected eval definitions, or a tool registry within the current
milestone as useful, excluding run logs, trajectories, and trace directories
until source selection is settled. After asking the source question, end the
turn; do not continue that inspection while waiting. Independent specification
work can resume if the user explicitly defers the missing source and selects
that next step. Whole-repository `rg --files`,
`find`, or recursive globs that expose trace filenames count as trace searches,
even without opening their contents. Read directly named Ethos and agent files
first; do not batch a broad inventory alongside loading these instructions.
If the location is unknown, ask the next focused source question rather than searching the checkout, home
directory, platform accounts, or unrelated jobs for candidate traces.

Once selected, inspect only that source to establish which records are accessible
and usable. Audit measurement accepts local ATIF files or Harbor trial directories
containing ATIF; selecting a remote source does not grant access or make it ATIF.
For other formats or inaccessible services, explain the needed local export or
appropriate supported conversion. Do not query Intake or start platform discovery
from this skill. Preserve the source selection and describe the limitation.

When the selected run folder is missing, explain that measurement is unavailable.
If the confirmed evaluation definitions are accessible, offer the remaining
work in plain language: “Should I outline what your evaluations should
check and compare that with your selected evaluation tasks, or would you like
to use another folder of recorded runs?” Adapt the task description to the
confirmed suite. Use **Audit specification** when naming the saved artifact;
the choice of next action should not require understanding “coverage
specification” or “test mappings.” Wait for the answer before proceeding.

Complete this milestone when the user has confirmed the eval suite set (or that
none exist) and trace selection or confirmed unavailability is understood.
Explain the selected scope, what its evidence can establish, and any missing
inputs; resolve remaining source or scope decisions before dependent work.
An inaccessible eval directory or a missing report does not establish no evals.
Keep an unresolved eval location open and ask for an accessible source; offer
independent specification work if useful, carrying that limitation forward.
If the user explicitly defers suite selection and requests independent
specification work, keep this milestone incomplete and use the accepted Ethos
and registry. Do not present candidate suites as selected or their findings as
findings about the user's confirmed evaluation scope.

## 2. Define what the evals should cover

Inputs: checked Ethos and authoritative tool names from the registry, agent
configuration, or confirmed traces. Group the audit skill's **Steps 1–3** here:
draft items, generate or reconcile `audit.md`, and validate it. These items
describe intended coverage; they are not runnable tests or proof that the
existing tests cover it.

Create or update `.eval-author/audit-coverage-report.md` using the
[report guidance](coverage-report.md) and the available findings. Its **Intended
coverage** section is the readable review surface: include every proposed item,
its intended check, and its required evidence from `audit.md`. Map audit items to
inspected test definitions where possible, with measurement explicitly unmeasured
until applicable results exist. Record unresolved mappings without inventing
tests or inspecting unselected traces.
Direct the user to this section of the Audit coverage report for the current
review. The Audit specification is its structured source; do not require a
second review of the YAML. Agreement with the complete, current Intended
coverage section approves the matching specification's checks and evidence
requirements, while review of findings remains a later decision.

In the turn-ending specification check-in:

- Open with the actual creation or update of the **Audit coverage report** and
  validation of the draft **Audit specification**. Attach a brief purpose to
  each linked name in that opening, before the scope preview or findings: the
  report explains the proposed checks, what the selected evaluations test, and
  the findings and evidence limits; the specification defines what the
  evaluations should check and the evidence required for each check. Include
  this explanation in the turn-ending reply even if it appeared earlier or in
  commentary. Names, filenames, and status words alone do not explain purpose.
  Use the [opening example](coverage-report.md#artifact-names), adapting it to
  the actions actually performed. **Draft** means awaiting agreement on the
  specification; the report is preliminary here.
- Show a short, concrete preview grouped into tools, capabilities, and failure
  cases. Describe intended checks, not just totals, item names, or gap findings.
  For a large scope, label the preview as a summary and link directly to the
  report's complete **Intended coverage** section using a supported section or
  line link verified against the saved file, labeled **Audit coverage report —
  Intended coverage**. Name the section in the reply so the user can find it
  even if the client opens only the file.
- Report the validation result, then explicitly say what to read and decide:
  “Please review the Intended coverage section of the Audit coverage report.
  Are these the right checks and evidence requirements, or should anything
  change?” Link that section directly. A generic “Does this scope look right?”
  leaves the review target unclear even when both artifacts are linked above.
  Name the next milestone that agreement would allow: measurement, or report
  review when measurement is deferred.
  Approval of intended coverage does not approve the final report or establish
  that any item is covered.

Validation proves structure and references, not completeness or agreement with
intent. Keep review pending until the user confirms the proposed coverage;
apply requested changes and revalidate. Do not mark it approved just because the
schema passed. An answer accepting the specification and the named next step
settles this milestone's check-in without a second prompt.

When no evaluations exist, explicitly explain that Ethos and this specification
can establish what future tests should cover. Existing agent runs can still be
examined when selected, but their coverage is evidence about those runs, not an
evaluation suite that does not exist. Without run evidence, proceed with the
specification alone. Unresolved tool names remain open questions; do not invent
names, silently omit material behaviors, or call a partial specification complete.

## 3. Measure coverage

Inputs: the reviewed, validated specification and usable records from the
user-confirmed source. Explain which trace set and measurement methods will be
used. Execute the audit skill's **Steps 4–5**, including supported judgments when
required, then aggregate the measurements. Report tools, capabilities, and failure
cases separately; methods not run remain unmeasured. A tool-only run cannot
establish capability or failure-case coverage. Keep an explicit list of coverage
files produced for the selected trace set and pass those with repeated `--coverage`
arguments to aggregation, or use a fresh output directory containing only that
set. Do not recursively aggregate a shared measurements directory containing
other audits' results. Reused coverage must belong to the current selected scope
as well as match the current specification and trace inputs.

Complete when the agreed measurement scope has been processed and aggregated
successfully, even when coverage is low. If some inputs fail, report only the
successful subset with explicit exclusions and keep the agreed milestone partial.
Refresh `audit-coverage-report.md` with the measured subset, methods, and any
excluded inputs. Explain the result and limits, link the updated report, refresh
the checklist, and continue to **Generate and review coverage report** when already requested.
Reuse existing measurements only when their inputs still apply.

If traces are absent, inaccessible, invalid, or unavailable in a supported format,
name the specific limitation and needed evidence. Record measurement as deferred
in shared state with its reason, such as “Selected trace directory is missing”
or “Run records need an ATIF export.” Keep **Check what your existing evaluations cover** unfinished
and explain that limitation beside the current coverage stage until resolved.
Do not report 0% coverage, fabricate
measurement files, or treat the lack of traces as uncovered behavior. The
specification and its review can still finish. Record the limitation in the
Markdown report. Continue the requested **Generate and review coverage report**
work with measurement explicitly unmeasured. Do not run evals or
install dependencies just to fill the gap without the
authorization required by the core.

## 4. Generate and review coverage report

Generate or update the **Audit coverage report**
(`.eval-author/audit-coverage-report.md`) following the
[report guidance](coverage-report.md), even for a specification-only outcome.
Then go over it with the user. Lead with what was established: a specification,
partial measurement, or coverage measured over a named set of traces. Explain
the findings, their evidence and limits, link the Audit coverage report and
supporting artifacts that actually exist, and leave deferred milestones visible.
A specification-only outcome is useful planning work, not a completed measurement.
Distinguish items not measured from measured items lacking evidence; neither automatically means
the dataset needs another test. Keep the [artifact names](coverage-report.md#artifact-names)
consistent: approving the Audit specification and reviewing the Audit coverage
report's findings are separate decisions. Both user reviews use the Audit
coverage report: first its Intended coverage section, now its findings, evidence
limits, and next actions. Link the relevant saved sections and explicitly name
those as the content to review; do not ask the user to reapprove intended checks.

Keep **Check what your existing evaluations cover** unchecked and current while awaiting the user's review.
Ask whether the Audit coverage report's findings need correction and offer the
recommended next step below, then end the turn. Mark it complete only after the
user has reviewed the report and any requested corrections are resolved. A reply accepting the findings and
selecting a next step satisfies the review; do not add another approval prompt.
Record the answered review in progress and refresh the report if findings change.

### After review: offer the next step

After the user accepts the findings, mark the report review complete and guide
them forward. For an existing suite, lead with proposing new or improved eval
tasks to improve coverage of the report's main gaps. Name the relevant behaviors
and explain that the next deliverable is a ranked set of candidate scenarios,
expected behavior, and verification plans. Avoid ending only with a generic
invitation for questions or a bare link to another skill.

For example: “The audit identified gaps around unverified account recovery.
I recommend proposing tasks to exercise that behavior, with checks for the
expected safe response. Would you like me to prepare those proposals?” This
offer can be included in the report-review question. Make the handoff explicit:
ask whether to start proposing tasks, end the turn, and wait for the user's
answer. Do not start proposal analysis, write proposals, or leave proposal
subagents running while the answer is pending. If proposals were already requested, proceed after the findings review without
asking permission again. For audit-only work, accepting findings alone does not
request proposals; a reply accepting both settles the handoff.

Once accepted, enter `eval-author-task-create` at **Step 1: propose dataset
improvements**. Carry the Audit coverage report, reviewed Audit specification,
available Coverage measurements (JSON) and evidence, selected source scope, user
corrections, and agreed priorities.
Do not restart the audit or repeat settled questions. Explain that proposals
will be saved in `.eval-author/dataset-recommendations.md`; creating
and running tasks is a later step with its own scope. If the user declines,
finish with the reviewed report and completed checklist.

Adapt the recommendation to the findings. Without usable run evidence, explain
what measurement needs; any task ideas based on inspected definitions remain
unmeasured candidates. If missing evidence or an agent fix is the useful next
action, recommend it instead of inventing tasks. With no existing evals, offer
the first-eval workflow to turn the reviewed specification into initial cases,
grading, and a runnable suite, reusing Ethos and prior answers. Keep deferred
measurement visible and do not imply that setup or execution already passed.
