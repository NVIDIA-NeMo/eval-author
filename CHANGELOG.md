<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Changelog

Notable changes to NeMo Eval Author are recorded here. Eval Author is a research
preview: workflows, skills, and saved artifacts may change between releases.

## Unreleased

### Removed

- **Breaking: ATIF converters.** The `gym-to-atif` and `mlflow-to-atif` skills,
  their `nemo.eval_author.gym_to_atif.v1` receipts, `--emit-atif`, and the NeMo
  Gym `ng-trajectory` CI route are gone. Eval Author never converts traces to
  ATIF; ATIF is only an input format.

### Changed

- **One trace loader.** `skills/eval-author/scripts/trace_loading.py` loads
  `atif`, `mlflow`, `gym`, `intake`, and `trace` sources into NeMo Compass
  `Trace` objects (trace contract v3) and writes a
  `nemo.eval_author.trace_load.v2` receipt. Intake is a live selection from the
  Intake service by workspace and time window, authenticated with
  `NMP_ACCESS_TOKEN`; it no longer reads `nemo intake` CLI exports. See
  [Load local traces](docs/traces.md#load-local-traces).
- **Temporary NeMo Compass Git pin.** Trace loading requires `nemo-compass` from
  Git revision `a78dc41` because trace contract v3 is not in a PyPI release yet.
  The loader rejects any other install. A PyPI pin returns with the next Compass
  release.
- **Audit measurement reads Compass traces.** `measure.py` loads ATIF, MLflow,
  NeMo Gym, or normalized traces through the trace loader, so Harbor is no
  longer an audit dependency. Coverage reports are now
  `nemo.eval_author.audit_coverage_report.v2` with `trace_format:
  nemo-compass-trace` and `source_kind`, and tool-call evidence cites trace and
  span IDs.
- **NeMo Gym closure evidence** binds the selected rollout through the
  `trace_load.v2` receipt and its normalized trace instead of a conversion
  receipt. See
  [Native NeMo Gym evidence](skills/eval-author-task-create/references/gym-evidence.md).
- **Trace workspaces read ATIF only for now.** `prepare` accepts
  `--source-kind atif` until the trace workspace reads Compass Traces;
  workspaces already prepared from another source kind keep loading.

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
