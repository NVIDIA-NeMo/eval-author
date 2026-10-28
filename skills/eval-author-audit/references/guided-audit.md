<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Audit milestones after prerequisites and Ethos

The shared prerequisite flow owns the opening checklist, prerequisite answers,
Ethos confirmation, and shared resumption. A direct full audit uses that same
flow. Afterward keep its audit item current while explaining the present
milestone: reviewing coverage, identifying gaps, and recommending improvements.
Use the [audit renderer](../../eval-author/references/conversations/audit.md) for
downstream checkpoints and reuse completed shared steps. Fixed copy belongs only
in [audit_templates.py](../../eval-author/scripts/conversation_flows/audit_templates.py).
For returning-audit entry, follow
[Return to an audit](../../eval-author/references/shared-prerequisites.md#return-to-an-audit).

## Decisions and progress

Complete authorized work, explain the result and limits, and link its artifacts.
Ask for missing source, scope, or content decisions; reuse prior answers. A
pending source selection blocks reads from that source. A pending content review
blocks dependent measurement. Retain explicit deferrals and continue independent
work that is already authorized.

Use the existing [journey-state CLI](../../eval-author/references/shared-prerequisites.md#state-helper-commands)
to save the versioned `scopes.audit.lifecycle` record with the latest
`--expected-revision`. On a stale revision, reload before applying the answer.
Use [`audit_state.py`](../../eval-author/scripts/audit_state.py) as the schema
source of truth. Read current audit progress using `journey_state.py show`
without `--state-only`; the command returns a state snapshot. Preserve framework
metadata and legacy scope fields when updating the lifecycle.

Save suite and trace selections separately, with candidates, exclusions, bounds,
and decision provenance. Set `specification` to its file path; list the selected
case and verifier paths in `suite_files` and selected run paths in `trace_files`.
The same record holds check approval, measurement, findings acceptance, the next
action, and `pending`. Keep that checkpoint inside the audit lifecycle
when switching journeys. On return, resume its applicable pending decision or
the earliest unfinished, non-deferred milestone. State-only entry reads no
artifacts; inspection without `--state-only` reads only selected sources and retained references.
Legacy notes and existing files alone do not establish approval or completion.

Use the state snapshot's `audit.checks_digest` for `checks_approval: {digest, source}`
and save that approval before updating `measurement`. After native measurement
and [report.py](../scripts/audit_spec/report.py) `--verify-existing <aggregate>`
validation, record `measurement: {checks_digest, status, evidence, source}`.
Its `evidence` is an explicit list of `{path, digest}` artifact references; its
`source` identifies the verification. Use `complete` only when all selected runs
and required methods succeeded; failed or missing work stays `partial`. Use
`deferred` only after the user explicitly chooses independent work; retain the
actual reply and reason in `source`, not an offered choice or missing-input finding.
Native scripts establish measurement correctness; the lifecycle checks retained bytes.

Save `findings: {report: {path, digest}, source}` for the coverage report and bind
`findings_review: {digest, source}` to the state snapshot's `audit.findings_digest`. Bind
`next_action: {kind, findings_digest, source}` to that same current digest only
when the user selects it. Acceptance clears answered `pending` and
`pending_question`; save both decisions before any journey switch. Mark the audit
complete only when derived `audit.complete` is true. Findings remain in
`audit-coverage-report.md`; link it whenever it is created or updated.

Treat changed inputs conservatively: changes to the agent, confirmed Ethos,
selected scope, specification bytes, or selected case, verifier, or trace bytes
reopen check review and all dependent measurement and findings. A change only to
the findings report reopens findings review. Record findings acceptance in state
without rewriting the report merely to mark it accepted. Acceptance can complete
a fully measured audit while the next action remains undecided; improvement
requires its own explicit user answer.

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
suites, render `audit_suites` with the linked inventory. Keep **Confirm evals
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

When selected paths are missing or saved selections conflict with current handoff
or authoring records, explain the mismatch in the final blocker or source-selection response.
Name the selected suites/tasks and runs, their selection provenance, and exactly
which checked paths are missing. Contrast relevant current work already known,
distinguishing saved records from verified files. Honor a current explicit
selection even when newer work exists. If scope is unclear, ask which named set
to audit before requesting its run source. Preserve prior selections until that
choice is resolved. Reassess trace availability and run mapping for a changed set;
earlier trace confirmations do not establish evidence for newly selected work.

If the user confirms no evaluations exist, say so without searching to prove it.
If the location is unknown or inaccessible, report that limit and ask for the
eval location first; do not claim absence or imply that an inventory succeeded.
Explain traces in ordinary language before the source question: recorded agent
runs show which tools and behaviors were actually exercised; test definitions
describe what was planned, and pass/fail scores alone cannot establish that
coverage. State that proposed checks can still be prepared without traces,
while measurement would remain unmeasured.

Before searching for, listing, opening, converting, or measuring traces, confirm
with the user both their source and location through `audit_traces`. A local file
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

Once selected, inspect only that source. Use local ATIF directly, including ATIF
in Harbor trials. Prepare Gym JSON or explicitly selected JSONL rows with
[`gym-to-atif`](../../gym-to-atif/SKILL.md), preserving its original-ATIF preference,
receipts, and losses. Use [`mlflow-to-atif`](../../mlflow-to-atif/SKILL.md) for complete
local `Trace.to_dict()` exports. For a selected MLflow record, check the complete
export for pagination and unique trace IDs before passing that record through
`--input -`; retain its original export and exact ID. Do not fetch remote traces,
query Intake, or generate runs to fill gaps.

For selected local trace records, save a private selection manifest before check
approval and set lifecycle `selection_manifest` to its path; leave it null when
traces are unavailable. Record each original source path and digest,
format, exact record selector, explicit suite/task/run mapping with its provenance,
evidence class, required methods, conversion options, and exclusions with reasons.
Keep original files in `trace_files`; never infer mappings from filenames. Missing
or ambiguous mappings remain unresolved. Keep this manifest stable during
execution; changed selections must reopen check review.

Retain preparation outcomes separately: for each selection link its source and
record to prepared ATIF, conversion metadata/losses, and measurement artifacts, or
record the missing, failed, or excluded outcome and reason. Include this outcome
record and the prepared files, receipts, and native results as `{path, digest}`
references in `measurement.evidence`. Before measurement or reuse, verify the
original bytes and selected record, mapping, and retained references. Changed
originals require preparation again even when prepared ATIF is unchanged.

When the selected run folder is missing, render the concrete limitation and
replacement-source or independent-work choice through `audit_blockers`. Preserve
selections; wait for the actual answer before recording a deferral or proceeding.

Complete this milestone when the user has confirmed the eval suite set (or that
none exist) and trace selection or confirmed unavailability is understood.
Explain the selected scope, what its evidence can establish, and any missing
inputs; check in for unresolved decisions before defining coverage. When the
request or accepted handoff already settles those decisions, continue to the
proposed-check review without a separate approval of this recap.
An inaccessible eval directory or a missing report does not establish no evals.
Keep an unresolved eval location open and ask for an accessible source; offer
independent specification work if useful, carrying that limitation forward.
If the user explicitly defers suite selection and requests independent
specification drafting, keep suite selection pending and draft from the confirmed
Ethos and tool registry. Retain the pending suite question; check approval, trace
inspection, and measurement still wait for suite selection or explicit
confirmation that no suites exist. Do not present candidate suites as selected
or their findings as findings about the user's confirmed evaluation scope.

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
review. Keep `audit.md` as its internal structured source. Agreement with the
complete, current Intended coverage section approves the matching checks and evidence
requirements, while review of findings remains a later decision.

Render `audit_checks` with the actual report update, validation result, verified
Intended coverage link, and concrete checks grouped by tools, capabilities,
failure cases, and grading. Keep the complete criteria in the report and label
any bounded preview as a summary. Follow [report naming](coverage-report.md#artifact-names);
do not introduce a second review document. Validation is not approval. Save the
user's agreement before measuring the agreed traces, without another readiness
question. Revisions require revalidation and renewed review; approval does not
accept the later findings. Explicitly deferred measurement allows the agreed
independent report work instead.

When no evaluations exist, explain that Ethos and the report's proposed checks
can establish what future tests should cover. Existing agent runs can still be
examined and measured when selected, but their coverage is evidence about those
runs and cannot complete the existing-suite audit lifecycle. Include first-eval
creation as a supported next action in findings review.

If required traces are unavailable, pause the full audit. Continue
specification-only work only if the user explicitly requests it. Unresolved
tool names remain open questions; do not invent names, silently omit material
behaviors, or call a partial specification complete.

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
Pass the saved task, run, and evidence class explicitly to native measurement.
Only mapped agent-under-test runs from selected suites can establish suite
coverage; production runs and task controls cannot substitute for them. Retain
their evidence roles and unresolved selections in the report. Use only applicable
coverage files from the retained outcomes and verify the aggregate before saving
measurement; conversion success alone does not complete measurement.

Complete when the agreed measurement scope has been processed and aggregated
successfully, even when coverage is low. If some inputs fail, report only the
successful subset with explicit exclusions and keep the agreed milestone partial.
Refresh `audit-coverage-report.md` with the measured subset, methods, and any
excluded inputs. Explain the result and limits, link the updated report, and
when measurement is complete, continue directly to findings review without
another approval pause.
Reuse existing measurements only when their inputs still apply.

If traces are absent, inaccessible, invalid, or unsupported, render the concrete
limitation and needed evidence through `audit_blockers`. The user must explicitly
choose replacement evidence, revised scope, or independent limited review.
Preserve selections until that answer; narrowing scope retains former inputs as
candidates or exclusions and reopens affected checks. After an explicit deferral,
retain evidence and the reason, refresh the report, and obtain its current
findings digest. Deferral cannot repair missing required inputs or stale approval;
remain blocked until resolved. Keep the full audit unfinished and absent evidence
unmeasured, not 0%. Do not fabricate measurements, generate runs, or install
dependencies without the core's required authorization.

## 4. Generate and review coverage report

Generate or update the **Audit coverage report**
(`.eval-author/audit-coverage-report.md`) following the
[report guidance](coverage-report.md), even for a specification-only outcome.
Then go over it with the user. Lead with what was established: proposed checks,
partial measurement, or coverage measured over a named set of traces. Explain
the findings, their evidence and limits, link the Audit coverage report, and
leave deferred milestones visible. Supporting evidence belongs in the report.
A specification-only outcome is useful planning work, not a completed measurement.
Distinguish items not measured from measured items lacking evidence; neither automatically means
the dataset needs another test. Keep the [artifact names](coverage-report.md#artifact-names)
consistent: approving the proposed checks and reviewing the Audit coverage
report's findings are separate decisions. Both user reviews use the Audit
coverage report: first its Intended coverage section, now its findings, evidence
limits, and next actions. Link the relevant saved sections and explicitly name
those as the content to review; do not ask the user to reapprove intended checks.

Keep the shared audit checklist item unchecked and current while awaiting the
user's review of coverage findings, gaps, and recommended next actions.
Render `audit_findings` with its combined corrections, acceptance, and next-action
question, then end the turn. Do not append another question. Save acceptance only
after the user reviews the report and corrections are resolved. A reply accepting the findings and
selecting a next step satisfies the review; do not add another approval prompt.
Record the answered review in progress and refresh the report if findings change.

### After review: offer the next step

Include the next-step offer in findings review. For an existing suite, propose new
or improved eval tasks for the report's main gaps. Name the relevant behaviors
and explain that the next deliverable is a ranked set of candidate scenarios,
expected behavior, and verification plans. Avoid ending only with a generic
invitation for questions or a bare link to another skill.

Acceptance alone ends review; render `audit_next_action` as acknowledgement
without a new offer or forced question. Do not begin proposal analysis, writes,
or background work unless the user selects that action. A broad audit-and-suggest
request does not select it. Accepting findings and the offered action together
settles both without another permission prompt.

Once proposals are selected, enter `eval-author-task-create` at **Step 1: propose dataset
improvements**. Carry the Audit coverage report, reviewed Audit specification,
available Coverage measurements (JSON) and evidence, selected source scope, user
corrections, and agreed priorities.
Do not restart the audit or repeat settled questions. Explain that proposals
will be saved in `.eval-author/proposals/dataset-recommendations.md`; creating
and running tasks is a later step with its own scope. If the user declines,
finish with the reviewed report and derived checklist, retaining incomplete measurement.

Adapt the recommendation to the findings. Without usable run evidence, explain
what measurement needs; any task ideas based on inspected definitions remain
unmeasured candidates. If missing evidence or an agent fix is the useful next
action, recommend it instead of inventing tasks. With no existing evals, offer
the first-eval workflow to turn the reviewed specification into initial cases,
grading, and a runnable suite, reusing Ethos and prior answers. Keep deferred
measurement visible and do not imply that setup or execution already passed.
