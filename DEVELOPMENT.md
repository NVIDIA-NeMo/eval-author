<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Development

## Environment and skill layout

Development uses Python 3.12 or 3.13 and uv. Run `uv sync --locked` to prepare
the environment, including the test runner, validators, MCP, and Harbor 0.24.0.
No NeMo Helix checkout is required. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the CI tool versions and contribution requirements.

This repository is not installed as a Python package. Users
[install the skills with `npx`, copy them manually, or load a checkout by file path](docs/getting-started.md#load-the-skills).
Keep runtime instructions and resources inside the skill directories, and
preserve relative paths between sibling skills. A skill installation does not
include repository-level documentation or tests. The bundled scripts do not
expose a published Python package API. Shared runtime helpers and their policy
live in `skills/eval-author/scripts/`; provider-specific discovery scripts stay
under `skills/eval-author-discover/scripts/`. Install the sibling `eval-author`
skill alongside discovery so its probes and report renderer can load those helpers.

For repository-wide use, run `python tools/locate-runtime.py --help` or
`python tools/runtime-policy.py --install-spec harbor` from the checkout root.
These entrypoints delegate to the bundled implementation without copying logic.
The existing top-level `tools/` directory is the repository script location.
The implementation, policy JSON, and runtime setup references stay inside the
umbrella skill so both `npx skills` and manual skill copies remain self-contained;
moving them solely to a repository-level directory would break those installs.

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

## ETHOS.md authoring

The [`ethos-orchestrate`, `ethos-explore`, and `ethos-write` skills](team-skills/ethos/ethos-orchestrate/README.md) create
and maintain an agent's `ETHOS.md` (schema version 1). `ethos-orchestrate` routes requests
to the other two. They live under `team-skills/ethos/` and install separately from
the Eval Author skills, which link to them from
[Ethos confirmation](skills/eval-author/references/ethos-confirmation.md). The ethos-write
skill bundles a standard-library validator, `scripts/validate_ethos.py`.

The ethos skills are published to the [NVIDIA/skills](https://github.com/NVIDIA/skills)
catalog, which accepts only skill content signed by NVSkills CI. Before merging a
pull request that changes `team-skills/ethos/<skill>/`, a maintainer or admin
comments `/nvskills-ci` in the pull request conversation (not in a review) after
the last skill change, confirms the `NVSkills CI` status passed, and keeps the
`Attach NVSkills validation signatures` commit it pushes. Unsigned changes do not
reach the catalog, which keeps the last signed version. Keep ethos skill changes
out of pull requests that change `skills/`: one run covers every changed skill,
so a core-skill failure would block the ethos signature. The
`request / require-nvskills-ci / require-nvskills-ci` check fails on any pull
request that changes `skills/` or `team-skills/` without a passing run; it is not
required, so core-skill pull requests can merge without one.

## NeMo Compass dependency

Trace loading and the development environment pin one NeMo Compass release, the
first with trace contract v3:

```text
nemo-compass[mlflow]==0.2.1
```

Eval Author loads every trace into a Compass `Trace` and never writes ATIF.
`uv sync --locked` installs the pinned release and its dependencies; skills
that load traces list the same pin in their `requirements.txt`, used with
`uv run --with-requirements <skill>/requirements.txt`.

`skills/eval-author/scripts/trace_loading.py` records the distribution and
version in its receipts. It rejects any other version, a Git, local or editable
install, and the older `trace-ingest` and `insight-agent` distributions, since
those share Python import paths. When updating the dependency, change
`trace_loading.py`, `pyproject.toml`, and every `skills/*/requirements.txt`
together; `tools/check_runtime_policy.py` rejects a mismatch.

For a stacked PR whose base is not `main`, run the same test matrix manually
with `gh workflow run ci.yml --repo NVIDIA-NeMo/eval-author --ref BRANCH`.

## Skill reference

Start with `eval-author`; it selects the appropriate workflow and loads supporting
instructions. The individual skill files document each workflow in detail.

| Skill | Purpose |
| --- | --- |
| [`ethos-orchestrate`](team-skills/ethos/ethos-orchestrate/SKILL.md), [`ethos-explore`](team-skills/ethos/ethos-explore/SKILL.md), [`ethos-write`](team-skills/ethos/ethos-write/SKILL.md) | Route, explore intended behavior, then write and validate an ETHOS.md. Installed separately. |
| [`eval-author`](skills/eval-author/SKILL.md) | Entry point and shared workflow guidance. |
| [`eval-author-first-eval`](skills/eval-author-first-eval/SKILL.md) | Plan, build, and validate a small starter evaluation suite. |
| [`eval-author-discover`](skills/eval-author-discover/SKILL.md) | Inventory evaluations and check Harbor readiness. |
| [`eval-author-audit`](skills/eval-author-audit/SKILL.md) | Define intended behavior and measure coverage against trace evidence. |
| [`eval-author-task-create`](skills/eval-author-task-create/SKILL.md) | Propose improvements from an audit and create a supported task when requested. |
| [`eval-author-environment`](skills/eval-author-environment/SKILL.md) | Build and prove the environment a Harbor or NeMo Gym task runs in; reached from first-eval and task-create. |
| [`eval-author-inspect-trace`](skills/eval-author-inspect-trace/SKILL.md) | Explain an Intake trace selected through the entry skill. |
| [`eval-author-rubric`](skills/eval-author-rubric/SKILL.md) | **Experimental; explicit end-user opt-in only.** Assess validity, coverage, and diversity; populated validity areas are anti-cheat; artifact, configuration, and provenance; and task contract and scoring authority. |
| [`eval-author-trace-environment`](skills/eval-author-trace-environment/SKILL.md) | **Experimental.** Derive and validate a private Harbor or native NeMo Gym task from trace evidence. |

## Validation

See the [generated case review capability assessment](docs/generated-case-review-assessment.md)
for the pinned Harbor and NeMo Gym review tools, observed limits, and remaining
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

### Native APT retry controls

The three native APT download controls in `tests/test_live_shard_apt.py` check
transient HTTP retries, permanent HTTP failure, and hash rejection. An
unconfigured local host skips them if `apt-helper` is absent or either `apt` or
`libapt-pkg6.0t64` is missing or below `2.8.4ubuntu0.1` (including Debian 12,
Ubuntu 22.04, and unpatched Ubuntu 24.04). Skips are not native retry evidence.
CI (`GITHUB_ACTIONS=true` or `CI=true`) and an explicitly set
`EVAL_AUTHOR_NATIVE_APT_ROOT` fail closed instead; an empty or broken root never
falls back to host APT. Both host packages must meet the floor before retries.

To opt in locally, use a compatible Ubuntu 24.04 Linux host (amd64 or arm64)
with `apt-get`, `dpkg`, `dpkg-deb`, Bash, GNU `timeout`, CA certificates, the Ubuntu
archive keyring at `/usr/share/keyrings/ubuntu-archive-keyring.gpg`, and network
access to the signed snapshot. From the checkout root, choose a **new, nonexistent**
private directory with an existing parent on an executable filesystem:

```bash
native_apt_root="$HOME/.cache/eval-author-native-apt-$(date +%s)"
mkdir -p "$HOME/.cache"
bash tools/bootstrap_native_apt.sh "$native_apt_root" && \
  EVAL_AUTHOR_NATIVE_APT_ROOT="$native_apt_root" uv run --locked pytest -q \
    tests/test_live_shard_apt.py -k native_apt_retries_only_transient_downloads
```

The bootstrap creates that directory, downloads exactly `apt=2.8.4ubuntu0.1`
and `libapt-pkg6.0t64=2.8.4ubuntu0.1` from the signed
`20261105T000000Z` noble-security/main snapshot with retries disabled, verifies
both archives' package/version/architecture, and extracts them unprivileged.
It does not install packages or change host APT state. The fixture validates the
private library, archive metadata, and helper version before selecting its
methods and subprocess-only `LD_LIBRARY_PATH`. Do not set `LD_LIBRARY_PATH`
globally. Debian 12 and Ubuntu 22.04 can run the default suite with skips; this
Ubuntu 24.04 bootstrap is not a compatibility installer for older hosts.

## NeMo Gym compatibility checks

NeMo Gym is an optional, isolated runtime. The baseline is release v0.6.0 at
`3045a793346a31291d7ea4ae6af3f94a35036ce5`, requiring Python 3.13.14+.
With that runtime already installed, run:

```bash
EVAL_AUTHOR_GYM_PYTHON=/path/to/Gym/.venv/bin/python uv run --locked pytest -q tests/test_gym_native.py
```

These tests invoke the real scaffolder and validators from both an audit gap
and a privacy-reviewed trace candidate, including missing-dataset and overwrite
failures. They complete a synthetic ledger scenario, exercise its
verifier cases and HTTP tools with NeMo Gym's SimpleAgent, and collect repeated runs
through `gym eval run --no-serve` with aggregation and health checks enabled.
Positive, no-action, and wrong-answer policies are explicitly scripted controls;
their rewards do not measure model performance. The scripted endpoint emits no
model captures or token counts, so NeMo Gym reports CLI rollout health as `unobserved`;
the test retains and checks this limitation with no health checks ignored.
Use pytest's `--basetemp` with a fresh private directory to retain the drafts
and controls for inspection; pytest clears that directory at the start of a run.

Skipped native tests do not establish NeMo Gym compatibility. A live model evaluation additionally requires the actual
agent/model configuration and credentials.

### Retained CI measurements

CI runs daily at 07:05 UTC: Tier 1 static evaluation, the Python 3.12/3.13 test
matrix, and native NeMo Gym compatibility for audit and ATIF trace
inputs. Each NeMo Gym route runs independently; a failure in one does not cancel the
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
  --route trace --output /tmp/new-gym-trace-evidence
```

`--route` accepts `audit` (the default) or `trace`. Each run
requires both native tests and the controls for that specific route; evidence
from another route cannot substitute for it.

`gym-summary.json` records the source route, source revision, clean-checkout status, exact
panel digest (including fixture bytes and the harness dependency lock), runtime,
individual test outcomes, control counts, and unobserved model health. Missing,
skipped, malformed, or timed-out evidence cannot pass. The command returns
nonzero for failed or incomplete measurements and retains a summary after
runtime setup failures. Existing output directories are rejected.

CI retains separate aggregate artifacts:

| Measurement | Artifact prefix | Retention |
| --- | --- | --- |
| Audit (`gym-ledger-controls-v1`) | `gym-evidence` | 90 days |
| ATIF trace (`gym-trace-ledger-controls-v1`) | `gym-trace-evidence` | 90 days |
| [Scripted verifier-cheat panel](docs/determinism-checks.md#verifier-cheat-panel) | `verifier-cheat-evidence` | 30 days |
| [Live anti-cheat experiment](docs/determinism-checks.md#live-anti-cheat-experiment) | `anti-cheat-live` | 90 days |

Each name ends with `-<run-id>-<attempt>`. JUnit, native rollouts, and detailed
logs use the corresponding `*-native` prefix and are retained for 30 days only
while the repository is private. These include pytest's synthetic `.eval-author`
workspaces with synthetic traces and task drafts. Missing summaries
fail the upload step.

The verifier-cheat panel publishes only its allowlisted summary; its raw native
evidence uses `verifier-cheat-native` with the same private-repository-only,
30-day policy. Live anti-cheat uploads only its two aggregate summary files and
never uploads raw model/native evidence, even for private repositories.

The audit artifact retains the schema, check names, and counters expected by the
companion fixtures importer. The trace artifact is a separate measurement;
the current audit importer does not ingest it. Download it directly from
the CI run. Future dashboard ingestion must recognize its distinct protocol
rather than combine it with audit results. Downloaded artifacts are evidence,
not executable inputs.

For paired comparisons, `--source-root /path/to/other/checkout` changes the skill
source under test while keeping this checkout's tests, fixtures and dependencies
fixed. The fixtures repository's `automation/gym-benchmark/run.py` freezes both
revisions and runs interleaved repeats. This measures compatibility only; it does
not establish live model performance, generated-task validity or defect sensitivity.

## Workflow scope regression checks

The [workflow scope checker](docs/determinism-checks.md#workflow-scope) compares normalized
tool evidence and file manifests against frozen request/turn contracts.
Run its mutation and disposable-inventory tests with:

```bash
uv run --locked pytest tests/test_workflow_scope.py -q
```

These checks do not claim live routing acceptance.

See [determinism checks](docs/determinism-checks.md) for discovery comparisons,
bound audit/task evidence, factual ledgers, and advisory host/semantic accounting.

## Retained reference compatibility

Receiving skills own the current trace reference contracts. The full legacy
references remain available for existing workflows. `test_reference_ownership.py`
checks their differences against reviewed owner-specific deltas: command paths,
cross-links, canonical-schema guidance and admission semantics. Update shared
rules in both copies; changes to owner-specific differences require reviewing and
updating `tests/fixtures/trace-reference-owner-deltas.json`. After reviewing those
changes, run `python tests/test_reference_ownership.py --update` to regenerate the
fixture. The check compares changed lines, ignoring unchanged context and line
numbers, so synchronized edits do not require a fixture update.

Compatibility covers command invocations and retained artifact formats. Private
Python helper globals and monkeypatch targets are implementation details; patch
the module that owns the helper when writing repository tests.
