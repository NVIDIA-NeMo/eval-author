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

Reuse the saved `scopes.audit.mode` chosen at entry. `task_review` inspects
existing tasks against confirmed Ethos and can finish without traces. `measured`
includes that same task review plus coverage measurement from selected recorded
runs. Follow the core's
[approach procedure](../../eval-author/references/shared-prerequisites.md#choose-the-audit-approach)
for a new choice or an explicit change.

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
and decision provenance. Use canonical repository-relative paths throughout the
lifecycle. Set `specification` to its file path. List the selected case and verifier
paths in `suite_files` and selected run paths in `trace_files`. Leave each file list
empty until its source is selected, then keep every file within that selection's paths.
The same record holds check approval, measurement, recorded findings, the next
action, and `pending`. Keep that checkpoint inside the audit lifecycle
when switching journeys. In the update that records a checkpoint's answer, set
`pending` to `null` or replace it with the next applicable checkpoint.
On return, resume its applicable pending decision or
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

Save the generated report, content, and input bindings in `findings` using the
[report binding rules](coverage-report.md#review-and-bindings) and bind
Current recorded findings are reviewed automatically; no user acceptance or
`findings_review` record is needed. A task review records the checks report as
`findings` before it analyzes the tasks, so approving the checks does not finish it.
After the analysis, save the final report in `findings`, then record
`findings_final: {digest, source}` with the digest of that report. No user prompt is
involved. Rewriting the report reopens the finalization. Bind
`next_action: {kind, findings_digest, source}` to `audit.findings_digest` only
when the user selects it. Answering a next-action question clears `pending` and
`pending_question`; save both before any journey switch. Mark the audit
complete only when derived `audit.complete` is true. Findings remain in
`audit-coverage-report.md`; link it whenever it is created or updated.

A fresh audit may set aside the earlier `.eval-author/.working/audit/` folder. Leave
`.eval-author/.working/audit-history/` in place: it holds the previous audits that the
new report compares against.

Treat changed inputs conservatively: changes to the agent, confirmed Ethos,
selected scope, specification or selection manifest bytes, or selected case,
verifier, or trace bytes reopen check review and all dependent measurement and
findings. A change only to
the findings report makes the recorded findings stale until they are regenerated
and recorded again. Recording current findings can complete a task review or
fully measured audit while the next action remains undecided.
Improvement requires its own explicit user answer.

## 1. Confirm evals and traces

For task review, only eval selection applies. After the suite answer, leave a
new lifecycle's trace selection pending and its trace file list empty, then
continue to section 2. Do not ask for traces, enumerate them, convert them, or
read retained measurements. Keep any earlier run selections as history.
The trace-selection procedure in this section applies to measured audits.

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
Apply the [retained-run lookup](../../eval-author/references/shared-prerequisites.md#locate-retained-evaluation-runs)
before reporting unavailable runs or asking the user to locate them.

If the user confirms no evaluations exist, say so without searching to prove it.
If the location is unknown or inaccessible, report that limit and ask for the
eval location first; do not claim absence or imply that an inventory succeeded.
Explain traces in ordinary language before the source question: recorded agent
runs show which tools and behaviors were actually exercised; test definitions
describe what was planned, and pass/fail scores alone cannot establish that
coverage. State that proposed checks can still be prepared without traces,
while measurement would remain unmeasured.

Before the source question, use the same retained-run lookup to offer relevant
candidates from the default jobs directory. Reuse current lookup evidence from
prerequisites. This metadata-only exception does not select a trace corpus.
Before other searches, listing, opening, converting, or measuring traces, confirm
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
or trace-loading tools. Before presenting a missing-source question, inspect named
agent docs, selected eval definitions, or a tool registry within the current
milestone as useful. Apart from the retained-run lookup, exclude run logs,
trajectories, and trace directories until source selection is settled.
After asking the source question, end the
turn; do not continue that inspection while waiting. Independent specification
work can resume if the user explicitly defers the missing source and selects
that next step. Whole-repository `rg --files`,
`find`, or recursive globs that expose trace filenames count as trace searches,
even without opening their contents. Read directly named Ethos and agent files
first; do not batch a broad inventory alongside loading these instructions.
If the location is still unknown after the retained-run lookup, ask the next
focused source question rather than searching the checkout, home directory,
platform accounts, or unrelated jobs for candidate traces.

Once selected, inspect only that source and load its traces through the shared
[trace loader](../../eval-author/references/trace-loading.md); never convert
them to ATIF. `measure.py` measures exactly one trace per file. Measure a local
ATIF trajectory directly, including ATIF in Harbor trials, and a source file that
holds exactly one trace with its own `--source-kind`. For a complete local MLflow
`Trace.to_dict()` export, an ATIF JSONL file, or a NeMo Gym rollouts JSONL file
that holds several traces, load each selected trace with the loader's
`--trace-id ID` (or `--row N` for a NeMo Gym row) into its own output directory,
then measure its `traces.normalized.jsonl` with `--source-kind trace`. Record
that selector as the record's exact selector. Do not fetch remote traces, query
Intake, or generate runs to fill gaps.

For selected local traces, reuse the saved `lifecycle.selection_manifest` on
resume. For a new manifest, use
`.eval-author/.working/audit/selection-manifest.json`. Resolve it with
[`working_paths.working_path(repo, "audit", "selection-manifest.json")`](../../eval-author/scripts/working_paths.py)
and save it with `working_paths.atomic_write_json`. Keep the manifest outside
every suite or trace source path, candidate directory, and exclusion. Do this
before check approval and set lifecycle `selection_manifest` to its
repository-relative path. Leave it null when traces are unavailable.
Record each original source path and digest,
format, exact record selector, explicit suite/task/run mapping with its provenance,
evidence class, required methods, loading options, and exclusions with reasons.
Keep original files in `trace_files`. Never infer mappings from filenames or
trajectory IDs. When a suite/task/run mapping is missing or ambiguous, retain
the candidates and leave trace selection pending until the user resolves it.
Keep this manifest stable during execution. Changed selections must reopen check review.

Retain preparation outcomes separately: for each selection link its source and
record to its loaded trace and `loading.json` receipt and measurement artifacts, or
record the missing, failed, or excluded outcome and reason. Include this outcome
record and the prepared files, receipts, and native results as `{path, digest}`
references in `measurement.evidence`. Before measurement or reuse, verify the
original bytes and selected record, mapping, and retained references. Changed
originals require preparation again even when loaded traces are unchanged.

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

After suite selection, complete the report guidance's
[verifier inspection](coverage-report.md#inspect-verifier-behavior-and-retained-controls)
before checks review. Do not mark grading not applicable merely because no
agent trajectory exists or the inventory read only task documentation.

Inputs: checked Ethos and authoritative tool names from the registry, agent
configuration, or confirmed traces. Group the audit skill's **Steps 1–3** here:
draft items, generate or reconcile `audit.md`, and validate it. These items
describe intended coverage; they are not runnable tests or proof that the
existing tests cover it.

Author or update `.eval-author/.working/audit/report-content.json`, then validate
and render it with the [report workflow](coverage-report.md). The generated
coverage tables are the readable review surface: include every proposed item,
its intended check, and its required evidence from `audit.md`. Map audit items to
inspected test definitions where possible, with measurement explicitly unmeasured
until applicable results exist. Record unresolved mappings without inventing
tests or inspecting unselected traces.
Direct the user to this section of the Audit coverage report for the current
review. Keep `audit.md` as its internal structured source. Agreement with the
complete, current coverage tables approves the matching checks and evidence
requirements, while the findings are not a user decision.

Render `audit_checks` with the actual report update, validation result, verified
coverage-table link, and concrete checks grouped by tools, capabilities,
failure cases, and grading. Keep the complete criteria in the report and label
any bounded preview as a summary. Follow [report naming](coverage-report.md#artifact-names);
do not introduce a second review document. Validation is not approval. Save the
user's agreement before measuring the agreed traces, without another readiness
question. Revisions require revalidation and renewed review; approval does not
approve the findings, which need no approval. Explicitly deferred measurement allows the agreed
independent report work instead.

When no evaluations exist, explain that Ethos and the report's proposed checks
can establish what future tests should cover. Existing agent runs can still be
examined and measured when selected, but their coverage is evidence about those
runs and cannot complete the existing-suite audit lifecycle. Include first-eval
creation as a supported next action offered with the findings.

For task review, approval of the checks permits task analysis and findings,
with every run-coverage dimension unmeasured. Do not fabricate a deferred
measurement record. Task mappings describe intended behavior, not demonstrated
execution. If no evaluations exist, report that limit and offer first-eval
creation instead of claiming task review is complete. Continue to section 4
after the authorized review work. Measured audits use section 3.

If required traces are unavailable for a measured audit, pause that audit. Continue
specification-only work only if the user explicitly requests it. Unresolved
tool names remain open questions; do not invent names, silently omit material
behaviors, or call a partial specification complete.

## 3. Measure coverage

This milestone applies only to measured audits. Report the selected run count
and relevant trace-size limits at the existing source/check review so the user
can narrow the scope. Longer traces, more runs, and behavioral judgments
increase token usage. Do not promise a fixed token or price estimate.

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
measurement; loading success alone does not complete measurement.

Complete when the agreed measurement scope has been processed and aggregated
successfully, even when coverage is low. If some inputs fail, report only the
successful subset with explicit exclusions and keep the agreed milestone partial.
Update the authored content and regenerate `audit-coverage-report.md` with the measured subset, methods, and any
excluded inputs. Explain the result and limits, link the updated report, and
when measurement is complete, continue directly to recording findings without
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

Complete [verifier evidence reconciliation](coverage-report.md#inspect-verifier-behavior-and-retained-controls)
before presenting findings. Account for failed controls, false rejection, and
unproven screening even when agent coverage measurement is complete. Use the
same findings and limits in the rendered conversation and saved report.

Generate or update the **Audit coverage report**
(`.eval-author/audit-coverage-report.md`) following the
[report guidance](coverage-report.md), even for a specification-only outcome.
Then go over it with the user. Lead with what was established: proposed checks,
partial measurement, or coverage measured over a named set of traces. Explain
the findings, their evidence and limits, link the Audit coverage report, and
leave deferred milestones visible. Supporting evidence belongs in the report.
A task review completes once current findings are recorded while run coverage remains
unmeasured. A specification-only outcome within a measured audit is useful
planning work, not a completed measurement.
Distinguish items not measured from measured items lacking evidence; neither automatically means
the dataset needs another test. Keep the [artifact names](coverage-report.md#artifact-names)
consistent: only the proposed checks need user approval. The findings
need none; present them from the Audit coverage report: its findings, evidence
limits, and next actions. Link the relevant saved sections and explicitly name
those as the content to review; do not ask the user to reapprove intended checks.

Record the findings in state, then render `audit_findings` and end the turn. It
asks no approval question and saves no pending checkpoint. A user correction
changes the content; regenerate the report and record the findings again.
When the findings are recorded, set `journey_status.audit` to `complete` if
`audit.complete` is true, then run the history snapshot once.

### After review: offer the next step

Include the next-step offer with the findings. For an existing suite, propose new
or improved eval tasks for the report's main gaps. Name the relevant behaviors
and explain that the next deliverable is a ranked set of candidate scenarios,
expected behavior, and verification plans. Avoid ending only with a generic
invitation for questions or a bare link to another skill.

Recording findings ends the audit; render `audit_next_action` as acknowledgement
without a new offer or forced question. Do not begin proposal analysis, writes,
or background work unless the user selects that action. A broad audit-and-suggest
request does not select it. A reply choosing the offered action settles it
without another permission prompt.

Once proposals are selected, enter `eval-author-task-create` at **Step 1: propose dataset
improvements**. Carry the Audit coverage report, reviewed Audit specification,
available Coverage measurements (JSON) and evidence, selected source scope, user
corrections, and agreed priorities.
Do not restart the audit or repeat settled questions. Expand the existing
next actions in the same Audit coverage report using its
[report guidance](coverage-report.md#findings-limitations-and-next-actions).
Creating and running tasks stays separately scoped. If the user declines,
finish with the reviewed report and derived checklist, retaining incomplete measurement.

Adapt the recommendation to the findings. Without usable run evidence, explain
what measurement needs; any task ideas based on inspected definitions remain
unmeasured candidates. If missing evidence or an agent fix is the useful next
action, recommend it instead of inventing tasks. With no existing evals, offer
the first-eval workflow to turn the reviewed specification into initial cases,
grading, and a runnable suite, reusing Ethos and prior answers. Keep deferred
measurement visible and do not imply that setup or execution already passed.
