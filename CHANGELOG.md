<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

Notable changes to NeMo Eval Author are recorded here. Eval Author is a research
preview: workflows, skills, and saved artifacts may change between releases.

## Unreleased

### Added

- Opt-in verification-integrity checks report receipt contradictions,
  states of required controls, superseded manifests, saved acceptance claims, and
  unchanged repeats after tool errors. Repair manifests record the digest they
  supersede. See
  [verification-integrity checks](docs/determinism-checks.md#verification-integrity-checks).

### Removed

- **Breaking: ATIF converters.** The `gym-to-atif` and `mlflow-to-atif` skills,
  their `nemo.eval_author.gym_to_atif.v1` receipts, `--emit-atif`, and the NeMo
  Gym `ng-trajectory` CI route are gone. Eval Author never converts traces to
  ATIF; ATIF is only an input format.
- **Breaking: live MLflow queries.** Traces are no longer read from an MLflow
  tracking server; export them and load the complete local export.
- **Breaking: v1 audit coverage files.** `report.py` accepts only
  `nemo.eval_author.audit_coverage.v2` inputs; measure existing traces again to
  aggregate them.
- **Breaking: legacy trace workspace paths.** `record-validation
  --allow-aggregate-only` is gone; every proof job needs per-check rows.
  Summary v2/v3, run-input v1/v2 and validation v6 workspaces, the
  `private/workspace.json` creation marker, `construction.v1` and
  `construction.v2`, and the historical trace construction import are no longer
  read. Older workspaces are refused; re-run `prepare` in a new task directory.
  A native first-eval or task-create `construction.v2` contract becomes v3 by
  changing its schema id; prepare its task evidence again. See
  [importing existing artifacts](skills/eval-author/references/construction-plan.md#import-existing-artifacts-without-rewriting-evidence).

### Changed

- **One trace loader.** `skills/eval-author/scripts/trace_loading.py` loads
  `atif`, `mlflow`, `gym`, `intake`, and `trace` sources into NeMo Compass
  `Trace` objects (trace contract v3) and writes a
  `nemo.eval_author.trace_load.v2` receipt. `--trace-id` (or `--row` for NeMo Gym
  JSONL) selects one trace of a larger source. Intake is a live selection from the
  Intake service by workspace and time window, authenticated with
  `NMP_ACCESS_TOKEN`; it no longer reads `nemo intake` CLI exports. A run that
  Compass loaded only in part, such as a partially loaded session or an ATIF
  trajectory that continues in another file, is refused rather than read as
  whole. See [Load local traces](docs/traces.md#load-local-traces).
- **NeMo Compass 0.2.1.** Trace loading requires `nemo-compass` 0.2.1 from
  PyPI, the first release with trace contract v3. The loader rejects any other
  version and Git, local, or editable installs.
- **Audit measurement reads Compass traces.** `measure.py` loads one ATIF,
  MLflow, NeMo Gym, or normalized trace per file through the trace loader, so
  Harbor is no longer an audit dependency. Coverage files and reports are now
  `nemo.eval_author.audit_coverage.v2` and
  `nemo.eval_author.audit_coverage_report.v2` with `trace_format:
  nemo-compass-trace` and `source_kind`, and tool-call evidence cites trace and
  span IDs.
- **NeMo Gym closure evidence** binds the selected rollout through the
  `trace_load.v2` receipt and its normalized trace instead of a conversion
  receipt. See
  [Native NeMo Gym evidence](skills/eval-author-task-create/references/gym-evidence.md).
- **Breaking: trace workspaces read Compass Traces.** `prepare --source <file>
  --source-kind {atif,mlflow,gym,trace} [--row N] [--trace-id ID]` loads one
  trace through the trace loader and writes `private/canonical.trace.json` and a
  scrubbed `safe/trace.json` that still validates as a Compass `Trace`. Load Intake with
  `trace_loading.py` first, then prepare its `traces.normalized.jsonl` with
  `--source-kind trace`. The safe copy omits media and model token ids, drops
  source attributes and pseudonymizes identifiers consistently. Tool calls whose
  arguments, results or definitions hold a redacted or pseudonymized value are
  not offered for exact replay. Candidates, construction, tool-call
  fixtures and the evidence audit cite safe-trace turn and span ids instead of
  ATIF step ids: `evidence_turns`, provenance `trace_turn`, `cutoff_turn_id`,
  and `{span_id, call_id}` call evidence. See
  [trace sources](skills/eval-author-inspect-trace/references/sources.md).
  Schema changes:

  | Artifact | Old | New |
  | --- | --- | --- |
  | summary | v2, v3 | v4 |
  | candidate | v2 | v3 |
  | privacy audit | v1 | v2 |
  | tool-call generation | v1 | v2 |
  | product | v4 | v5 |
  | batch (members `{task_id, source, source_kind, row?}`) | v1 | v2 |
  | tool calls, tool-call plan, tool access, call fixtures | v2 | v3 |
  | MCP scenario, schema overrides, evidence audit | v1 | v2 |
  | construction | v2 | v3 |

  Run-input v3, validation v7, tool-access decisions v1 and publication review
  v1 are unchanged.

## 0.1.0

Initial research-preview release, tested with Harbor 0.20.0 and NeMo Gym v0.6.0.
To install exactly this version, clone the `v0.1.0` tag and follow the
[manual installation steps](docs/getting-started.md#manual-installation-without-nodejs).

### Added

- **Eleven skills that install together**, with `eval-author` as the entry point
  that selects a workflow and saves its output under `.eval-author/`. See
  [Get started](docs/getting-started.md) and the
  [skill reference](DEVELOPMENT.md#skill-reference).
- **Ethos authoring** with the bundled [`ethos` skill](skills/ethos/SKILL.md),
  which records your agent's purpose, boundaries, and success criteria in a
  local `ETHOS.md` that first evaluations and audits build on.
- **First evaluations for NeMo Gym or Harbor.** Agree on a reviewed plan, build
  a starter suite in a proven environment, and validate each task before
  evaluating your agent. See [Build your first evaluations](docs/first-evals.md).
- **Discovery and readiness checks.** Inventory the evaluations in a repository
  and check whether its NeMo Gym and Harbor suites are ready to run. See
  [Find existing evaluations](docs/getting-started.md#find-existing-evaluations)
  and [Check readiness](docs/existing-evals.md#check-readiness).
- **Guided coverage audits.** Define what your evaluations should cover, measure
  coverage from ATIF traces, and review gaps in a readable Audit coverage report.
  See [Audit coverage](docs/existing-evals.md#audit-coverage).
- **Proposals and task creation.** Turn audit findings into ranked
  recommendations, and create a NeMo Gym or Harbor task for an eligible, measured
  tool-coverage gap. See
  [Propose new evaluations](docs/existing-evals.md#propose-new-evaluations) and
  the [NeMo Gym task-authoring guide](skills/eval-author-task-create/references/gym-tasks.md).
- **Reviewable, rerunnable suites.** Each generated suite gets a `README.md` with
  a case inventory and rerun commands, and NeMo Gym records and ATIF traces open in an
  offline HTML reader. See
  [Review generated cases](docs/results.md#review-generated-cases) and
  [Open readable cases and traces](docs/results.md#open-readable-cases-and-traces).
- **Trace tools.** Explain a NeMo Intake trace and convert MLflow and NeMo Gym traces
  to ATIF. See [Inspect an Intake trace](docs/traces.md#inspect-an-intake-trace)
  and [Convert local traces](docs/traces.md#convert-local-traces).
- **Experimental trace-to-environment workflow**, which derives a private Harbor
  or native NeMo Gym task from trace evidence. See
  [Build an evaluation task](docs/traces.md#build-an-evaluation-task).
