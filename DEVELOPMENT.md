<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Development

## Environment and skill layout

Development uses Python 3.12 or 3.13 and uv. Run `uv sync --locked` to prepare
the environment, including the test runner, validators, MCP, and Harbor 0.20.0.
No NeMo Helix checkout is required. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the CI tool versions and contribution requirements.

This repository is not installed as a Python package. Users
[install the skills with `npx`, copy them manually, or load a checkout by file path](docs/getting-started.md#load-the-skills).
Keep runtime instructions and resources inside the skill directories, and
preserve relative paths between sibling skills. A skill installation does not
include repository-level documentation or tests. The bundled scripts do not
expose a published Python package API.

Adding a runtime dependency to a bundled script changes the requirements for
users who copied the skill. Contract tests inspect script imports and reject
dependencies outside the standard library, sibling modules, and explicitly
allowed third-party dependencies. Update the relevant skill's compatibility
instructions and the [workflow requirements](docs/getting-started.md#requirements)
when those requirements change.

The audit validator is
[`skills/eval-author-audit/scripts/audit_spec/validate.py`](skills/eval-author-audit/scripts/audit_spec/validate.py).
See the [audit script guide](skills/eval-author-audit/scripts/audit_spec/README.md)
for measurement assumptions, evidence sidecars, and aggregation. Command examples
are in the [audit skill](skills/eval-author-audit/SKILL.md).
The experimental trace-environment skill documents its
[artifact contract](skills/eval-author-trace-environment/SKILL.md#artifact-contract).
For standalone Gym trace conversion, see
[`gym-to-atif`](skills/gym-to-atif/SKILL.md).

## ethos.md authoring

The [`ethos-orchestrate`, `ethos-explore`, and `ethos-write` skills](ethos/README.md) create
and maintain an agent's `ethos.md` (schema version 1). `ethos-orchestrate` routes requests
to the other two. They live under `ethos/skills/` and install separately from
the Eval Author skills, which link to them from
[Local Ethos](skills/eval-author/references/local-ethos.md). The ethos-write
skill bundles a standard-library validator, `scripts/validate_ethos.py`.

## Public NeMo Compass dependency

The development dependencies include `trace-ingest[mlflow]` pinned to an exact
commit in the public [NVIDIA-NeMo/labs-nemo-compass](https://github.com/NVIDIA-NeMo/labs-nemo-compass)
repository. Local development and GitHub Actions fetch it over HTTPS with
`uv sync --locked`; no GitHub login, token, deploy key, or separate checkout is
required. Keep the Git revision in `pyproject.toml` and `uv.lock` synchronized
when deliberately updating the dependency.

For a stacked PR whose base is not `main`, run the same test matrix manually
with `gh workflow run ci.yml --repo NVIDIA-NeMo/labs-eval-author --ref BRANCH`.

## Skill reference

Start with `eval-author`; it selects the appropriate workflow and loads supporting
instructions. The individual skill files document each workflow in detail.

| Skill | Purpose |
| --- | --- |
| [`ethos-orchestrate`](ethos/skills/ethos-orchestrate/SKILL.md), [`ethos-explore`](ethos/skills/ethos-explore/SKILL.md), [`ethos-write`](ethos/skills/ethos-write/SKILL.md) | Route, explore intended behavior, then write and validate a local ethos.md. Installed separately. |
| [`eval-author`](skills/eval-author/SKILL.md) | Entry point and shared workflow guidance. |
| [`eval-author-first-eval`](skills/eval-author-first-eval/SKILL.md) | Plan, build, and validate a small starter evaluation suite. |
| [`eval-author-discover`](skills/eval-author-discover/SKILL.md) | Inventory evaluations and check Harbor readiness. |
| [`eval-author-audit`](skills/eval-author-audit/SKILL.md) | Define intended behavior and measure coverage against trace evidence. |
| [`eval-author-task-create`](skills/eval-author-task-create/SKILL.md) | Propose improvements from an audit and create a supported task when requested. |
| [`eval-author-environment`](skills/eval-author-environment/SKILL.md) | Build and prove the environment a Harbor or Gym task runs in; reached from first-eval and task-create. |
| [`eval-author-inspect-trace`](skills/eval-author-inspect-trace/SKILL.md) | Explain an Intake trace selected through the entry skill. |
| [`eval-author-trace-environment`](skills/eval-author-trace-environment/SKILL.md) | **Experimental.** Derive and validate a private Harbor or native Gym task from trace evidence. |
| [`mlflow-to-atif`](skills/mlflow-to-atif/SKILL.md) | Convert MLflow traces to ATIF. |
| [`gym-to-atif`](skills/gym-to-atif/SKILL.md) | Convert one Gym Responses record or retain original Harbor ATIF from a Gym run. |

## Validation

See the [generated case review capability assessment](docs/generated-case-review-assessment.md)
for the pinned Harbor and Gym review tools, observed limits, and remaining
rendering scope.

Run `uv sync --locked` and `make hooks` to install the DCO commit-message hook.
See [CONTRIBUTING.md](CONTRIBUTING.md) for sign-off and PR-title conventions.
Before committing, check a prepared message with
`make commit-check COMMIT_MSG=/path/to/prepared-commit-message`; the hook also
runs on `git commit`. Plain `make commit-check` validates the current `HEAD`
commit, so it also works after cloning or pulling a merged change.

When adding NVIDIA-authored files or changing dependencies, update the tracked
licensing artifacts first:

```bash
make update-copyright-headers
make update-licenses
```

For dependency changes, run `uv add <package>` and `make update-licenses`, review
the generated diff, and commit it. Generation requires `osv-scanner` 2.3.3 on `PATH`
and automatically collects license and attribution texts for locked runtime
dependencies and all extras into `third_party/NOTICES.txt`, with indexes in
`THIRD_PARTY_LICENSES.md` and `third_party/licenses.jsonl`.

Only exceptions require manual work: if a distribution omits its license, add a
version-specific, checksum-pinned upstream document in `third_party/license_exceptions.yaml`.
Unknown license terms require a shared text under `third_party/license_texts/` and,
where necessary, a reviewed compatibility entry in `third_party/license_overrides.yaml`.
The standard texts were sourced from SPDX license-list-data commit
`16f3aa6c3bdd62e50f8b1cf618f32d2a510250ee`; original package notices are retained separately.

`make check-licenses` verifies the generated disclosures. Scanner intermediates and
download caches live in ignored `tmp/`; a cold cache requires network access.
These notices cover this source distribution;
shipping containers or bundled native environments requires collecting notices from
those actual artifacts too, since platform wheels can bundle additional libraries.

Run the same read-only checks used by CI:

```bash
make check-copyright-headers
make check-licenses
uv run --locked ruff check .
uv run --locked ty check --python-platform linux --python-version 3.12
uv run --locked ty check --python-platform linux --python-version 3.13
uv run --locked ruff format --check .
uv run --locked pytest
```

The default tests use synthetic local evidence and mocked providers. Tests that
need a running environment backend skip when one is unavailable. The optional
Ethos parser compatibility test skips without the NeMo Agents plugin. Live
model execution is opt-in outside configured CI workflows; see
[trace fixture checks](docs/trace-derived-fixtures.md#regression-checks) and
[skill evaluation CI](docs/skill-evaluation-ci.md).

## Gym compatibility checks

Gym is an optional, isolated runtime. The baseline is release v0.6.0 at
`3045a793346a31291d7ea4ae6af3f94a35036ce5`, requiring Python 3.13.14+.
With that runtime already installed, run:

```bash
EVAL_AUTHOR_GYM_PYTHON=/path/to/Gym/.venv/bin/python uv run --locked pytest -q tests/test_gym_native.py
```

These tests invoke the real scaffolder and validators from both an audit gap
and a privacy-reviewed trace candidate, including missing-dataset and overwrite
failures. They complete a synthetic ledger scenario, exercise its
verifier cases and HTTP tools with Gym's SimpleAgent, and collect repeated runs
through `gym eval run --no-serve` with aggregation and health checks enabled.
Positive, no-action, and wrong-answer policies are explicitly scripted controls;
their rewards do not measure model performance. The scripted endpoint emits no
model captures or token counts, so Gym reports CLI rollout health as `unobserved`;
the test retains and checks this limitation with no health checks ignored.
The `ng-trajectory` cases first produce real Gym rollout attachments, retain one
JSONL row with both the attachment loader and Responses-to-ATIF converter, then
build and exercise a new native task from that evidence. They also verify that
an attachment-only input is rejected by the ATIF converter. To select this path:

```bash
EVAL_AUTHOR_GYM_PYTHON=/path/to/Gym/.venv/bin/python uv run --locked pytest -q tests/test_gym_native.py -k ng-trajectory
```

Use pytest's `--basetemp` with a fresh private directory to retain the source
rollouts, conversion receipts, drafts, and controls for inspection; pytest
clears that directory at the start of a run. Attachment capture in these
scripted controls does not establish model-call capture or measured performance.

Skipped native tests do not establish Gym compatibility. A live model evaluation additionally requires the actual
agent/model configuration and credentials.

### Retained CI measurements

CI runs daily at 07:05 UTC: Tier 1 static evaluation, the Python 3.12/3.13 test
matrix, and native Gym compatibility for audit, ATIF trace, and `ng_trajectory`
inputs. Each Gym route runs independently; a failure in one does not cancel the
others. Daily runs have their own concurrency group so a push cannot cancel them.
No inference credentials or model calls are used. Tier 2/3 live evaluation keeps
its existing approval controls and is not scheduled by this workflow.

The schedule becomes active after the workflow lands on `main`. GitHub schedules
are best-effort and may be delayed or dropped; use **Actions → CI → Run workflow**
for a missed run. See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
Use the same collector locally with a fresh output directory per route:

```bash
uv run --locked python tools/collect_gym_evidence.py \
  --gym-python /path/to/Gym/.venv/bin/python --gym-root /path/to/Gym \
  --gym-revision 3045a793346a31291d7ea4ae6af3f94a35036ce5 \
  --route ng-trajectory --output /tmp/new-gym-ng-trajectory-evidence
```

`--route` accepts `audit` (the default), `trace`, or `ng-trajectory`. Each run
requires both native tests and the controls for that specific route; evidence
from another route cannot substitute for it.

`gym-summary.json` records the source route, source revision, clean-checkout status, exact
panel digest (including fixture bytes and the harness dependency lock), runtime,
individual test outcomes, control counts, and unobserved model health. Missing,
skipped, malformed, or timed-out evidence cannot pass. The command returns
nonzero for failed or incomplete measurements and retains a summary after
runtime setup failures. Existing output directories are rejected.

CI retains separate aggregate artifacts for 90 days:

| Input route | Artifact prefix | Protocol |
| --- | --- | --- |
| Audit | `gym-evidence` | `gym-ledger-controls-v1` |
| ATIF trace | `gym-trace-evidence` | `gym-trace-ledger-controls-v1` |
| Gym rollout attachment | `gym-ng-trajectory-evidence` | `gym-ng-trajectory-ledger-controls-v1` |

Each name ends with `-<run-id>-<attempt>`. JUnit, native rollouts, and detailed
logs use the corresponding `*-native` prefix and are retained for 30 days only
while the repository is private. These include pytest's synthetic `.eval-author`
workspaces with source rollouts, conversions, and task drafts. Missing summaries
fail the upload step.

The audit artifact retains the schema, check names, and counters expected by the
companion fixtures importer. The trace artifacts are separate measurements;
the current audit importer does not ingest them. Download them directly from
the CI run. Future dashboard ingestion must recognize their distinct protocols
rather than combine them with audit results. Downloaded artifacts are evidence,
not executable inputs.

For paired comparisons, `--source-root /path/to/other/checkout` changes the skill
source under test while keeping this checkout's tests, fixtures and dependencies
fixed. The fixtures repository's `automation/gym-benchmark/run.py` freezes both
revisions and runs interleaved repeats. This measures compatibility only; it does
not establish live model performance, generated-task validity or defect sensitivity.
