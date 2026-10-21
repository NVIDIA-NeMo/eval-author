---
name: eval-author-audit
description: >-
  For a fresh full audit, read eval-author, show its welcome, checklist, and call
  to action, and end the turn before repository inspection or prerequisite work.
  Guide coverage audits with milestone check-ins and user-confirmed eval and trace sources.
  Generate, validate, measure, and report on an audit-spec coverage denominator
  for Eval Author. Use when the user wants a hand-editable audit.md file derived
  from Ethos, needs schema enforcement for declared tools, capabilities, failure
  cases, evidence, and references, wants to measure which audit items one ATIF
  trace covers, wants to aggregate coverage across measured traces, or accepts
  a coverage audit of existing evals. Changes
  none of the user's source, and saves audit artifacts under `.eval-author/`.
triggers:
  - audit my existing evals
  - generate audit.md from ETHOS.md
  - validate audit.md coverage schema
  - measure audit.md coverage against a harbor trace
  - aggregate audit.md coverage reports
  - check audit.md coverage denominator
  - what should my evals cover from the agent ethos
  - review the audit coverage denominator
  - audit coverage of my evals against the ethos
not-for:
  - eval-author (use for the standard, the boundaries, and to pick a sub-flow)
  - eval-author-discover (use to check Gym or Harbor suite readiness)
  - eval-author-inspect-trace (use after eval-author selects an Intake trace)
  - nemo-experimentalist (use to optimize an agent from Insights or explicit datasets)
compatibility: >-
  Python 3.11 or later for generation and validation; Python 3.12 or later for
  ATIF measurement via Harbor's trajectory model. Dependencies are listed in requirements.txt.
  Generation, validation, measurement, and aggregation read local files only;
  they do not start Harbor jobs or call platform services.
maturity: alpha
license: Apache-2.0
user-invocable: true
allowed-tools: Bash Read Write Grep Glob
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Eval Author: audit

## Purpose

Read `eval-author` for the shared standard, vocabulary, and boundaries. This
sub-flow generates and validates a finite coverage denominator from `<ethos_path>` and
reviewed audit items, then can measure ATIF traces and aggregate coverage reports
against it. It also writes a human-readable `audit-coverage-report.md` and reviews
the findings with the user. It does not generate tasks yet.

## Start a guided audit

A full audit enters the core's shared prerequisite flow, including direct
invocation. For a fresh audit, show only its fixed welcome, three-item checklist
with **Check what your existing evaluations cover** selected, and call to action,
then end the turn.
Read only this skill and the core before that response; repository inspection,
Ethos validation, prerequisite questions, and state writes wait until the user
continues. Reuse confirmed agent, prerequisites, and Ethos on continuation.
Apply the shared flow's audit checks: local audit tooling and source access,
without evaluation runner, Docker, live agent service, or credential probes.
Confirm that traces from completed evaluations exist before continuing to Ethos;
unknown or missing availability pauses onboarding under **Blockers**.
Check conversion and trajectory-parser dependencies only after evidence selection.

After shared onboarding's completion pause and the user's continuation, follow
[Guided audit milestones](references/guided-audit.md):
confirm evals and traces, define intended coverage, measure selected evidence,
then generate and review `audit-coverage-report.md`. Keep source and content
reviews explicit. Use bounded agent and eval reads. Do not enumerate or read traces
until their source and location are selected. An existing accepted source choice
needs no repeated confirmation.

Explicit generate, validate, measure, and aggregate requests keep their scope.
Generation needs confirmed Ethos. Existing-spec validation, measurement, and
aggregation use their supplied inputs without new onboarding. File-only and
provider-independent operations do not require runtime installation. Read-only
and suggestion requests remain read-only.

## Ethos prerequisite

Use the confirmed result from [Ethos confirmation](../eval-author/references/ethos-confirmation.md).
An explicit `--ethos <path>` overrides a prior handoff path; recheck applicability
and confirmation for changed content. Before drafting audit items or generating
`audit.md`, require the selected agent's confirmed intended behavior. If it is
missing or changed, invoke the canonical `ethos-orchestrate` through that adapter;
do not run a separate audit interview, writer, or review loop.

## Coverage specification

The audit-spec approach has three item kinds in v1:

| Kind | Meaning |
|---|---|
| `tool` | A canonical tool name the agent may call |
| `capability` | A high-level behavior the agent should exercise |
| `failure_case` | Expected safe behavior when a capability cannot proceed normally |

Every item uses `name` as its stable coverage key. Names must be unique across
the whole file; do not add sequential numeric IDs. Tool references in
`required_tools`, `expected_tools`, and `evidence_required[].tool` must match the
`name` of a declared `tool` item. `prohibited_tools` may name any syntactically
valid tool name, including tools the agent must never call and therefore should
not declare as allowed tools.

Write audit artifacts under `.eval-author/`. Audit operations do not edit the
customer's source, existing evals, source-of-truth documents, or `ETHOS.md`;
prerequisite Ethos work belongs to the shared procedure in the pre-flight above.

## Audit outputs

Use these names in replies and artifact links; follow [artifact naming](references/coverage-report.md#artifact-names).

| Name | File under `.eval-author/` | Purpose |
|---|---|---|
| Audit specification | `audit.md` | Structured source of checks and required evidence, reviewed through the report's Intended coverage section |
| Audit coverage report | `audit-coverage-report.md` | Readable intended coverage, test mappings, measured coverage, gaps, evidence limits, and next actions |
| Coverage measurements (JSON) | `audit-coverage-report.json` | Script-generated measurements aggregated over the selected runs, when available |
| Journey state (internal) | `.working/journey-state.json` | Shared decisions, audit progress, and resumption |

For a full audit, create the Audit coverage report with the validated Audit
specification, refresh it after measurement, and update it for final review.
Follow the [report guidance](references/coverage-report.md) and [template](templates/audit-coverage-report.md) even when measurement is deferred.

Whenever the Markdown report is created or updated, explicitly say so and link
it in the turn-ending response, including intermediate milestone check-ins.

Focused operations need this report only when requested or continuing a full
audit; keep their scope. Read-only and `suggest` requests remain read-only.

## Scripts

Audit-spec mechanics live under `scripts/audit_spec/`:

Read `scripts/audit_spec/README.md` for the current measurement assumptions:
ATIF input, Harbor trajectory parsing, v1 `tool_calls`, `capabilities`, and
`failure_cases` coverage, and coverage aggregation from `coverage.json` files.

| Script | Use it to |
|---|---|
| `scripts/audit_spec/generate.py` | Create, reconcile, replace, or preview `.eval-author/audit.md` from `ETHOS.md` and reviewed item proposals |
| `scripts/audit_spec/measure.py` | Measure one ATIF trace or Harbor trial directory against `audit.md` and write coverage/details files for each selected method |
| `scripts/audit_spec/report.py` | Aggregate per-trace `coverage.json` files into one coverage report with uncovered audit items |
| `scripts/audit_spec/validate.py` | Validate the marked audit-spec block in `audit.md` |

Shared helpers, measurement method contracts, schemas, and examples are
documented in `scripts/audit_spec/README.md`.
Runtime dependencies are listed in `requirements.txt`.

## Step 1: Draft Or Update Audit Items

Before drafting or updating `.eval-author/.working/audit-items.yaml`, read
`templates/audit.md` and `schemas/audit.schema.json`. Use the template as the
worked example and the JSON Schema descriptions as the field definitions. Do not
use validation as the primary way to discover the format; validation is the
enforcement and repair step after drafting.

Read `<ethos_path>` and draft audit items at the level between Ethos and runnable
tasks: canonical tools, high-level capabilities, and material failure cases. Keep
the list finite. Do not create separate items for prompt paraphrases, fixture
variants, or ordinary happy-path permutations.

For `tool` items, use the names that appear in the actual runtime traces or tool
registry, including eval-specific tools that may be more precise than product
tools named in Ethos prose. If Ethos describes a generic tool such as `sqlite`
but measurement traces expose `execute_sql` and `submit_sql`, declare the
runtime tool names and connect capabilities or failure cases to those names.
Do not invent tool names that will not appear in the measurement surface.
Read traces for this purpose only after user confirmation of their source and
location. Without usable traces, use an authoritative tool registry or agent
configuration; keep unresolved names as open questions rather than inventing
tool items. Explain any resulting limits on the proposed specification.

Save the reviewed item proposals as `.eval-author/.working/audit-items.yaml`. The items
file may be either a mapping with an `items` key or the item list itself. It
should use the same item shape shown in `templates/audit.md` and enforced by
`schemas/audit.schema.json`.

For an initial audit, this file should contain the full proposed denominator. For
an update, it may contain only the proposed additions or edits. Existing reviewed
`audit.md` items remain the source of truth in reconcile mode.

Capabilities that do not need tools, such as policy refusals or out-of-scope
handling, should use `required_tools: []`. Failure cases attach to capability
names through `applies_to`; tool-level failure expectations stay on the tool item
as `expected_failure_behavior`.

For failure cases, make the trigger and safe response explicit in
`evidence_required`. Include prohibited output classes in an `output` evidence
description when their absence must gate coverage. Measurement uses
`prohibited_tools` as a deterministic gate; `applies_to`, `expected_tools`,
`trigger`, `expected_behavior`, and `prohibited_outputs` otherwise provide the
rubric and authoring context rather than separate hidden checks.

## Step 2: Generate Or Reconcile Audit.md

Create or update `.eval-author/audit.md` from `<ethos_path>` and the reviewed item
proposals:

```bash
uv run --with pyyaml --with jsonschema \
  <skill_dir>/scripts/audit_spec/generate.py \
  --ethos <ethos_path> \
  --items .eval-author/.working/audit-items.yaml \
  --out .eval-author/audit.md
```

The default mode is `reconcile`. If `.eval-author/audit.md` does not exist, it
creates the file. If it already exists, the generator parses the existing marked
block, updates source metadata such as the Ethos digest, preserves existing item
bodies by stable `name`, appends new proposed items, and reports proposed edits
without silently rewriting them. Hand-authored prose outside the marked block is
preserved.

By default, the generator treats `.eval-author/.working/audit-items.yaml` as a partial
update proposal. Missing existing items are not stale in that mode, because the
items file may contain only additions or edits. Use `--items-mode full` only when
the items file is intended to be the complete denominator; then existing items
omitted from the proposal are reported as `possibly_stale_items`.

Use the explicit modes when the default is not what the user wants:

```bash
uv run --with pyyaml --with jsonschema \
  <skill_dir>/scripts/audit_spec/generate.py \
  --ethos <ethos_path> \
  --items .eval-author/.working/audit-items.yaml \
  --out .eval-author/audit.md \
  --mode suggest

uv run --with pyyaml --with jsonschema \
  <skill_dir>/scripts/audit_spec/generate.py \
  --ethos <ethos_path> \
  --items .eval-author/.working/audit-items.yaml \
  --out .eval-author/audit.md \
  --mode reconcile \
  --items-mode full

uv run --with pyyaml --with jsonschema \
  <skill_dir>/scripts/audit_spec/generate.py \
  --ethos <ethos_path> \
  --items .eval-author/.working/audit-items.yaml \
  --out .eval-author/audit.md \
  --mode replace
```

`suggest` performs the same comparison as `reconcile` but writes nothing.
`replace` rewrites the whole file from the item proposal file, including prose
outside the marked block, and should be used only when the user wants to discard
the existing generated audit file.

The generator prints a JSON summary containing `written`, `added_items`,
`conflicting_items`, `conflicting_items_applied`, and `possibly_stale_items`.
Treat `conflicting_items` as items where the proposal differs from the reviewed
audit item; reconcile mode preserves the reviewed item and reports
`conflicting_items_applied: false` so the user can accept the change manually or
through a future editor. If reconcile adds items, finds conflicts, reports stale
items in full mode, or detects an agent-name change, an approved audit is
demoted to `status: draft` unless the user passes `--status approved`.

The generator adds an optional `sources` entry for Ethos with `name: ethos`, a
path relative to `audit.md`, and a real `sha256` digest. It uses the frontmatter
`name` from `ETHOS.md` when `--agent` is omitted. If that inferred name differs
from the existing audit's `agent`, reconcile preserves the reviewed audit name
and reports `agent_change` unless the user passes `--agent` explicitly.
`source_refs` are advisory provenance notes in v1; the validator preserves them
but does not resolve them against `sources` until a future generator or grammar
defines that reference format.

## Step 3: Validate

Run validation after every generated or hand-edited audit file:

```bash
uv run --with pyyaml --with jsonschema \
  <skill_dir>/scripts/audit_spec/validate.py --audit .eval-author/audit.md
```

`schemas/audit.schema.json` is the canonical structural schema. The Python
validator applies that schema first, then checks any source digests that are
provided and cross-item references such as `required_tools` and `applies_to`.

Validation proves only structure and references, not that the denominator is
complete or correct.

## Step 4: Measure One ATIF Trace

After validation and the guided audit's specification review (when applicable),
measure one completed trial directory or one ATIF trajectory file from the
user-confirmed source. If that input is missing or unusable, follow the guided
audit's unavailable-evidence guidance; do not search elsewhere without the
user selecting another location. Use `--measure` to select one or more
comma-separated measurement methods; the default is `tool_calls`.

Prefer `--trial-dir` for Harbor trials so `measure.py` stamps `subject.evidence_class`
from `result.json`. Measure the **agent under test** only—no Oracle/NOP trials,
synthetic control ATIF, or control runs in audit artifacts (see
[coverage report guidance](references/coverage-report.md)). With `--trace`, pass
`--evidence-class agent_under_test` or `--skip-evidence-class` for legacy inputs only.

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/audit_spec/measure.py \
  --audit .eval-author/audit.md \
  --trial-dir <harbor-job-dir>/<trial-dir> \
  --measure tool_calls \
  --out-dir .eval-author/audit-measurements
```

Harbor trial dirs normally contain `agent/trajectory.json`; pass `--evidence-class`
to override stamping. `--trace` does not infer runner role from ATIF bytes alone.

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/audit_spec/measure.py \
  --audit .eval-author/audit.md \
  --trace <path-to>/trajectory.json \
  --task-id <task-id> \
  --run-id <run-id> \
  --evidence-class agent_under_test \
  --measure tool_calls \
  --out-dir .eval-author/audit-measurements
```

`--measure` may be passed more than once or as CSV, for example
`--measure tool_calls,capabilities,failure_cases`. The default is `tool_calls`;
include the other methods when the user wants the same trace to count against
capability or failure-case items. The script loads the trajectory once, then runs
each selected method against the same parsed Harbor trajectory model. Unknown
method names fail before the trace is loaded.

When capability evidence contains non-tool kinds such as `user_intent`, `output`,
`outcome`, `policy_boundary`, or `verifier`, inspect the trace and write a
structured judgment file under `.eval-author/` using
`schemas/audit_capability_judgments.schema.json`. Each judgment must target the
capability `name` plus the zero-based `evidence_required` index, copy the
evidence `kind` and `description` exactly, and judge only non-tool evidence. Do
not write judgments for `tool_call`; the script measures those deterministically.
Set the sidecar's required `trace_sha256` to `sha256:` followed by the lowercase
SHA-256 digest of the exact ATIF trajectory file you inspected. Measurement
rejects the sidecar if those trace bytes have changed or another trace is used.
Use the capability description, evidence description, and concrete trace content
as the rubric: mark `satisfied` only when the trace clearly demonstrates the
requirement, `missing` when it clearly does not, and `unclear` when the trace is
ambiguous or insufficient. Include brief rationale and supporting trace
references when available. A subjective judgment can satisfy only the non-tool
evidence it targets; it cannot override missing required tools or missing
`tool_call` evidence.
Pass the sidecar when measuring capabilities:

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/audit_spec/measure.py \
  --audit .eval-author/audit.md \
  --trace <path-to>/trajectory.json \
  --task-id <task-id> \
  --run-id <run-id> \
  --measure capabilities \
  --capability-judgments .eval-author/capability-judgments.json \
  --out-dir .eval-author/audit-measurements
```

Capability coverage is conjunctive: every deterministic requirement must be
satisfied, and every judged evidence requirement must be satisfied. Missing
judgments leave the capability uncovered. Stale judgments fail measurement
before the script writes a coverage report, including judgments bound to a
different trace digest.

Failure-case coverage follows the same pattern. Inspect the trace and write
`schemas/audit_failure_case_judgments.schema.json`, targeting each non-tool
evidence requirement by failure-case `name`, zero-based index, exact `kind`, and
exact `description`. Judge only what `evidence_required` states, using the
failure case's trigger, expected behavior, and prohibited outputs as context.
Then measure it with:

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/audit_spec/measure.py \
  --audit .eval-author/audit.md \
  --trace <path-to>/trajectory.json \
  --task-id <task-id> \
  --run-id <run-id> \
  --measure failure_cases \
  --failure-case-judgments .eval-author/failure-case-judgments.json \
  --out-dir .eval-author/audit-measurements
```

A failure case is covered only when every evidence requirement is satisfied and
none of its `prohibited_tools` appears anywhere in the trace. Missing judgments
leave it uncovered, and a subjective judgment cannot override missing
`tool_call` evidence or an observed prohibited tool. The same trace-digest and
stale-target checks used for capability judgments apply.

The script writes one folder per task, run, and method. Task and run ids are
encoded as single path components so ids containing `/` cannot create nested or
escaping paths:

```text
.eval-author/audit-measurements/task=<encoded-task-id>/run=<encoded-run-id>/<method>/coverage.json
.eval-author/audit-measurements/task=<encoded-task-id>/run=<encoded-run-id>/<method>/details.json
```

`coverage.json` uses the shared coverage schema and contains only the stable
audit item names this trace covered plus provider-neutral subject identity
(`trace`, `trace_format`, `task_id`, and `run_id`). It also records
`item_kind_count`, the denominator for the measured item kind. Coverage
aggregation should consume this file and ignore method-specific debug details.
`details.json` is specific to the selected method and carries traceability data
for humans.

For current method semantics and details schemas, see
`scripts/audit_spec/README.md`.

The script validates `coverage.json` against `schemas/audit_coverage.schema.json`
and validates `details.json` against the selected method's details schema before
writing. Use the next step to aggregate coverage across tasks and runs.

## Step 5: Aggregate Coverage Reports

After measuring one or more traces, aggregate the per-trace `coverage.json`
files into a coverage report. For a guided audit, include only measurements from
the selected trace set, using explicit `--coverage` files or a dedicated directory.
The directory example below assumes it contains only the intended measurements:

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/audit_spec/report.py \
  --audit .eval-author/audit.md \
  --coverage-dir .eval-author/audit-measurements \
  --out .eval-author/audit-coverage-report.json
```

Use `--coverage <path-to-coverage.json>` for explicit files, or repeat
`--coverage-dir` and `--coverage` when the inputs are split across directories.
The script scans coverage directories recursively for files named
`coverage.json`, validates every input against
`schemas/audit_coverage.schema.json`, and rejects inputs whose audit metadata no
longer matches the current `audit.md`. A status-only mismatch, such as
measurements produced while the audit was `draft` and aggregated after it became
`approved`, is reported as a warning instead of forcing a re-measure.

Aggregate JSON shape, `coverage_by_evidence_class`, headline fields, and
`uncovered_items` reasons are defined in
[`scripts/audit_spec/README.md`](scripts/audit_spec/README.md). Hand labeled
gaps to [`eval-author-task-create`](../eval-author-task-create/SKILL.md) using
`coverage_by_evidence_class.agent_under_test` when present.

## Next Steps

- For audit-generation inputs and reconciliation modes, return to
  [Step 2: Generate Or Reconcile Audit.md](#step-2-generate-or-reconcile-auditmd).
- After the report review, offer new or improved eval task proposals using the
  [guided handoff](references/guided-audit.md#after-review-offer-the-next-step).
  End the turn and wait for acceptance before entering the proposal step in
  [`eval-author-task-create`](../eval-author-task-create/SKILL.md) with the current
  report and evidence. Proposal acceptance does not authorize task execution.
- The proposal step considers tools, capabilities, and failure cases. Automatic
  task creation still accepts only uncovered tool items with
  `reason: not_covered_by_any_input_report`. Items with
  `reason: not_measured_by_any_method` remain unmeasured, even when the proposal
  step suggests a candidate scenario for them.

## Prerequisites

Establish Ethos and review audit items first. Generation/validation need Python 3.11+, PyYAML, and jsonschema; no provider probe.
ATIF measurement from Harbor or NeMo Gym needs Python 3.12+ and Harbor's trajectory model; use `requirements.txt`.
Use the [shared locator](../eval-author/references/runtime-prerequisites.md#runtime-prerequisite-checks) with `--provider harbor --flow audit`.
For Gym traces, follow [Gym audit inputs](references/gym-audit.md); offline conversion and measurement need no Gym runtime. Commands read local files, not services.

## Limitations

Validation proves structure, not denominator completeness. Coverage applies
only to supplied traces and selected methods; missing judgments leave items
uncovered. It does not establish task quality, agent correctness, or absent scenarios.

## Troubleshooting

- Invalid tool or capability reference: match the declared stable `name`, repair
  the audit item, and rerun `validate.py` before measurement.
- Missing or invalid ATIF: obtain the original trajectory or a supported
  conversion; a reward alone cannot substitute for interaction evidence.
- Stale judgment digest or target: inspect the current trace and evidence
  requirement, regenerate the judgment, and remeasure; never relabel old evidence.
- Aggregate metadata mismatch: remeasure against the current audit. A status-only
  draft-to-approved change is a warning, not a reason to rewrite coverage JSON.
