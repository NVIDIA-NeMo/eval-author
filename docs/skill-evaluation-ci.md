<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# SkillEvaluator CI reports

SkillEvaluator provides advisory checks for the skills in this repository:
the Eval Author skills under `skills/` and the ethos skills under
`team-skills/ethos/`. Tier 1 covers both trees; Tier 2 and Tier 3 cover `skills/`.
Findings and incomplete evaluations remain visible in reports, but do not
replace the required unit tests, lint, or type checks.

## Checks and execution

| Check | Purpose | When it runs |
| --- | --- | --- |
| Tier 1 | Static checks of skill structure, code, secrets, PII, and documentation quality | Same-repository PRs targeting `main`, pushes to `main`, manual CI dispatch, and daily at 07:05 UTC |
| Tier 2 | Model-assisted context and similarity analysis | Approved live runs on `main` |
| Tier 3 | Bounded agent evaluations using reviewed synthetic datasets | Approved live runs on `main` |

Pull requests also receive a credential-free coverage plan, one patched AMD64
binary build, and a full Audit image-preparation timing check on a GitHub-hosted
runner. They do not run live inference. Fork PRs are excluded from these workflows.
Daily CI also runs the Python test matrix and three independent native NeMo Gym
input routes. NeMo Gym summaries remain separate from SkillEvaluator artifacts; see
[retained CI measurements](../DEVELOPMENT.md#retained-ci-measurements). The daily
schedule starts after merge to `main` and does not enable live model evaluations.
The independent [verifier-cheat panel](determinism-checks.md#verifier-cheat-panel)
also runs real Harbor/Docker evaluation controls without model credentials.
Its fixed-fixture measurements are not SkillEvaluator or semantic-regression scores.
The separate [live anti-cheat experiment](determinism-checks.md#live-anti-cheat-experiment)
automatically runs model calls on `main` pushes and its daily schedule. PRs receive a plan, not
live execution; the credential-free verifier-cheat panel remains unchanged.

Tier 3 requires a skill-owned `evals/evals.json`; missing datasets are reported
as skipped rather than treated as successful evaluations.

Live runs use independent jobs for each skill's Tier 2 context check and eligible
Tier 3 dataset, plus one collection-wide similarity job. At most two jobs run
at once. Each writes a fallback before setup and checkpoints completed work;
the final aggregate records missing or invalid job reports as incomplete.
Runner loss or cancellation can still prevent the aggregate job from publishing.

Tier 3 evaluation has a 45-minute timeout; its separate dataset validation
keeps a 60-second timeout. The outer live CI shard job allows 80 minutes for
dependency setup, evaluation, and report retention; the binary build runs in a
separate upstream job. These limits are a candidate
based on local measurements, not a guarantee of completion: runtime preflight
took 6.7 minutes, the with-skill arm took 12.7 minutes, and the last baseline
trial finished 34.6 minutes after evaluation started, with no final native
aggregate available. The former 30-minute evaluation limit had already expired,
so late trial results did not establish a completed evaluation. The 45-minute
limit bounds the collector's wait. The contained CI path closes its private
execution boundary before accepting native results; an unverified teardown
fails closed and requires destruction of the disposable shard VM. Case counts,
metrics, attempts, concurrency, models, and strict completeness checks are unchanged.

CI permits up to 300 million scalar comparisons per context check using the
pinned evaluator's unchanged pairwise algorithm, with a five-minute timeout.
The allowance is recorded as `full-pairwise-300m-v1` in report policy and applies
only after verifying the installed evaluator commit. All content and pairs are
retained; the 512-chunk limit, vector validation, and similarity thresholds are
unchanged. Exceeding either work or time bounds remains incomplete.
The allowance accommodates the maximum 512 chunks at 2048 embedding dimensions:
`512 × 511 / 2 × 2048 = 267,911,168` scalar comparisons. Switching
`SKILL_EVAL_EMBEDDING_MODEL` to a higher-dimensional model requires revisiting
the allowance; the actual vector dimension in the original incident was not retained.

The workflow files and their checked-in phase runner are the source of truth for tool versions, limits, and
artifact configuration:

- [Static checks](../.github/workflows/ci.yml)
- [Coverage planning and live evaluation](../.github/workflows/skill-evaluation-live.yml)
- [Live anti-cheat experiment](../.github/workflows/anti-cheat-live.yml)

### Agent version reproducibility

The evaluator and Harbor versions do not by themselves pin the agent installed
inside each trial. The live collector pins OpenCode through
[`tools/run_pinned_live_evaluator.py`](../tools/run_pinned_live_evaluator.py),
using the configured release in
[`tools/patched_opencode_runtime.py`](../tools/patched_opencode_runtime.py). Its
`policy.configured_opencode_version` records the intended release, including in
planning reports; it is not an installation or runtime observation.

The wrapper requires the pinned SkillEvaluator and Harbor packages. Unsupported
versions or conflicting OpenCode overrides stop evaluation before dispatch.

Only Tier 3 `evaluate` uses the wrapper, under `--evaluator-python` (defaulting
to the collector's Python). When using a separate evaluator environment, provide
that environment's Python as well as its `--evaluator` executable; the wrapper
requires the pinned packages in that interpreter. Dataset `tier3 validate`
still invokes the evaluator directly; Tier 2 behavior is unchanged.

`agent_diagnostics.opencode_reported_versions` contains native reported versions,
not independent binary-version evidence. The patched runtime separately verifies
the binary and records private trial receipts; missing evidence remains unobserved.
Pinning removes one source of drift; it does
not freeze provider models, network dependencies, task images, or evaluation results.

### Patched live runtime

CI and local contained reproduction share `tools/live_shard.py` phases:
`create`, `runtime`, `relocate`, `prepare`, `run`, and `close`. Each shard needs
its own disposable Linux VM with systemd, root access, and a host Docker daemon;
the supported CI runner is GitHub-hosted Ubuntu 24.04. Local VMs require the
root-owned `/etc/eval-author/disposable-shard` approval marker containing
`isolated-vm`. Never use this privileged nesting path on shared Docker Desktop
or a shared/self-hosted runner. Destroy the VM after the shard, including when
cleanup cannot be verified.

The execution image pins uv 0.12.3, Docker 29.7.2, and Compose 5.5.1. Every shard
still installs its own managed Python 3.12 evaluator environment; the image is
not a replacement for that installation. Repository inputs mount read-only;
private native outputs retain identical absolute paths inside and outside the
boundary. Host control state, collector reports, and fallback/upload files stay
outside its writable mount. Tier 2 uses the same boundary without downloading
OpenCode or preparing Tier 3 images. Successful setup phases explicitly hand
off a short idle lease; failed phases close the boundary. Native evaluation
closes it before report parsing, and CI closes it again before staging.

Direct boundary-mode collection requires an unambiguous absolute `--evaluator`
path and at most one runnable observation. The shard runner supplies that path;
the native runtime directory is added only inside the boundary, not to the host
collector's command search path. Use the CI planner for a full multi-shard run.

This is lifecycle containment, not a guarantee of credential confidentiality,
restricted egress, or protection against a compromised privileged guest.

On Apple Silicon macOS, [`tools/local_live_vm.py`](../tools/local_live_vm.py)
provides a `LocalVM` handle for owning one fresh Lima VM across these phases.
The supported default is Homebrew Lima 2.3.0 at `/opt/homebrew/bin/limactl`,
using VZ and an ARM64 guest; the Python API also accepts an explicit `limactl`
path. It creates the VM; Docker provisioning remains a separate prerequisite.
Use a fresh, short, private, canonical host control root with no symlink
ancestors: `<root>/lima/<name>/ssh.sock.1234567890123456` must
encode to fewer than 104 bytes. Longer paths are rejected before startup;
keep verbose evidence directory names outside that root. The shell API,
`LocalVM.shell(..., stdin=payload)`, accepts at most 64 KiB of in-memory bytes;
omitting stdin supplies EOF. Do not put credentials in command arguments.
For bounded credential handoffs, pass `stdin_deadline` as an absolute host
monotonic timestamp. It is checked after local validation, immediately before
the transport call; it does not shorten the command's execution timeout or
guarantee instantaneous delivery by the operating system.

For keyless local reproduction, inside an approved disposable VM, use a fresh
absolute root outside the checkout. Start the clock before downloading the exact
run/attempt binary artifact; finish that download before `create`, as CI does.
Do not insert a download between boundary phases: the 120-second idle lease is
shorter than the download step plus startup overhead.

```bash
export EA_LIVE_JOB_DEADLINE=$(python3 -c 'import sys,time; started=time.monotonic(); sys.path.insert(0, "tools"); from live_shard import LIVE_JOB_SECONDS; print(started + LIVE_JOB_SECONDS)')
# Download the exact run/attempt artifact to /absolute/downloaded-opencode now.
sudo --preserve-env=EA_LIVE_JOB_DEADLINE python3 tools/live_shard.py create --root /absolute/private/shard --tier 3 --check live --skill eval-author-audit
sudo python3 tools/live_shard.py runtime --root /absolute/private/shard
sudo python3 tools/live_shard.py relocate --root /absolute/private/shard --download /absolute/downloaded-opencode
sudo python3 tools/live_shard.py prepare --root /absolute/private/shard
sudo python3 tools/live_shard.py close --root /absolute/private/shard
```

Run phases consecutively; a paused handoff expires rather than retaining idle
privileged execution. `run` authorizes model execution and is deliberately
omitted above. The following lower-level commands remain useful for inspecting
individual components, but do not provide the shard containment lifecycle.

Live CI builds one patched OpenCode binary per platform in an upstream job using
[`tools/build_patched_opencode.py`](../tools/build_patched_opencode.py). Live shards
download that run's artifact and verify its digest and actual version before the
credential-bearing evaluation step.
The build retains the configured 1.19.5 release and applies a narrow stream-listener
lifetime fix; it is not an official release binary or a migration to OpenCode v2.
The patch keeps error handling active through writable-stream finalization rather
than suppressing broken-pipe errors or retrying failed evaluations.

For local reproduction, build outside the checkout and pass the resulting manifest
to the existing collector:

```bash
python tools/build_patched_opencode.py --output /absolute/path/patched-opencode
export EA_PATCHED_OPENCODE_MANIFEST=/absolute/path/patched-opencode/binary-manifest.json
export EA_REQUIRE_PATCHED_OPENCODE=1
```

When reusing a downloaded build artifact, rebind its manifest to the local binary
before setting `EA_PATCHED_OPENCODE_MANIFEST`:

```bash
python tools/build_patched_opencode.py \
  --relocate-manifest /absolute/path/downloaded/binary-manifest.json \
  --binary /absolute/path/downloaded/opencode \
  --output /absolute/path/verified-opencode
export EA_PATCHED_OPENCODE_MANIFEST=/absolute/path/verified-opencode/binary-manifest.json
```

PR preparation and live Tier 3 jobs now require a credential-free native gate
immediately after binary relocation, before image preparation or credential access.
`tools/verify_live_runtime.py --binary-manifest /absolute/path/verified-opencode/binary-manifest.json --output /absolute/path/new-private-gate`
runs with the installed live-runtime Python. It checks the real wrapper's
`tier3 evaluate --help`, then real Python children from both native launch-environment
helpers: activation and import identity must hold, and corrupted tokens/digests must
exit 78 before main. The gate performs no models, installs, builds or pulls; it
retains raw output privately (0600) and prints only fixed reasons, counters and timing.
This is startup compatibility evidence, not a complete live-evaluation result.

Then use the normal live-collection command and pinned evaluator environment.
Only Tier 3 evaluation receives these settings; dataset validation and context
checks remain unchanged. Without either setting, local collection retains the
pin-only behavior. CI sets the requirement explicitly and refuses a missing or
invalid manifest instead of falling back to the unpatched binary.

The activated path provisions the verified standalone binary instead of installing
OpenCode again through NVM/npm. Required system dependencies remain checked.
Nonzero exits, missing scores, and binary or runtime mismatches remain failures.

Live CI additionally prepares every task environment in both arms **before the
step receiving inference credentials**. Default Python task images share one
prepared dependency base; custom digest-pinned bases receive their own dependency
layer. Task fixtures, workspace isolation, and custom commands remain intact.
Preflight and both arms use the verified local image IDs;
missing images, changed sources, missing packages, or version mismatches stop
execution rather than invoking package repositories during a trial.

To opt into this same path locally, first build the patched binary above, then
run with the pinned evaluator's Python and a fresh private output directory:

```bash
/path/to/live-runtime/bin/python tools/prepare_live_images.py \
  --skill skills/eval-author-environment \
  --binary-manifest "$EA_PATCHED_OPENCODE_MANIFEST" \
  --output /absolute/path/prepared-live-images
export EA_PREPARED_IMAGES_MANIFEST=/absolute/path/prepared-live-images/prepared-images.json
export EA_REQUIRE_PREPARED_IMAGES=1
```

Use the normal collector command with that **single matching skill** on the same
Docker daemon. Preparation needs public registry/package access, Docker Compose,
and Linux AMD64 or ARM64 with Docker's classic builder available. Preparation
explicitly sets `DOCKER_BUILDKIT=0` for builds because BuildKit interprets local
config IDs in `FROM` as registry names. Both task-source and final layers retain
immutable local parents; there is no mutable-tag fallback. The classic builder
is [deprecated by Docker](https://docs.docker.com/engine/deprecated/#legacy-builder-for-linux-images);
Docker installations without it are unsupported and fail preparation.
Inference credentials are removed from its child environment.
Keep the preparation directory private: it includes staged task
inputs and image provenance, not a public report. Regenerate it after source or
runtime changes. Unset both image-preparation variables to retain the original
local installation path. CI always requires preparation for live shards; its
existing fallback summaries and main-only environment gate are unchanged.

For private failure diagnosis, add `--private-diagnostics /absolute/private/failure.json`.
The destination must be new, in an existing directory outside the source checkout,
with no symlink components and distinct from the public timing checkpoint. On the
first Docker nonzero exit or subprocess timeout, preparation exclusively creates
one 0600 JSON file with fixed category/phase/operation, exit code (null for timeout),
and stdout/stderr head-and-tail excerpts capped at 16 KiB each with truncation flags. This bounds
retained output, not Docker subprocess memory buffering. Retention failure does
not replace the original preparation failure. The shared shard runner enables
this at `workspace/preparation-docker-failure.private.json`, outside source and
public reports; never upload it. Success creates no diagnostic file.

The default Python 3.12.16/trixie base is pinned by platform-specific registry
digest. Added packages have exact Debian versions, including Node.js
20.19.2 and npm 9.2.0; the manifest records the full Debian revisions and final
local image IDs, and setup verifies installed versions. Other custom bases must
already use digests and support these package versions. Only single-stage,
Dockerfile-only `evals.json` tasks with isolated paired workspaces are supported;
unsupported configurations fail closed, not through a generic replacement image.
Multiple stages, parser directives, heredocs, inherited `ONBUILD` triggers,
network-control sidecars, and Compose overlays are unsupported. Resource,
working-directory, or environment overrides require new preparation.
Required Debian packages resolve through signed Debian and security snapshots
at `20261105T000000Z`, rather than expiring versions in rolling indexes. The
default dependency base also retains those snapshot sources for task APT steps;
custom task sources are not replaced. Other task downloads can remain mutable.
Maintainers update the snapshot and exact package versions together in
`tools/patched_opencode_runtime.py` and verify both architectures before changing
the pins. Unavailable snapshots or packages fail preparation, without a rolling
repository fallback. This is verified image reuse, not
a claim of bit-for-bit reproducible rebuilds. It does not change agent execution
deadlines or guarantee model responses or timeout cleanup.

Preparation has a 900-second (15-minute) aggregate deadline across all images, not a fresh
allowance per Docker command. CI also deducts elapsed dependency/artifact setup
from a shared monotonic job window. The shared phase runner reserves 2880 seconds:
60 for native dataset validation, 60 for aggregate handoff/collector/wrapper
startup, the unchanged 2700-second evaluation allowance, and the existing
60-second collection handoff. Startup is separate because the wrapper checks
the full evaluation-plus-handoff reserve again after activation, including its
prepared-image probes. The window ends 75 minutes after the fallback step starts,
leaving strictly less than 1620 seconds (27 minutes) for setup and preparation.
The 48-minute run step and this aggregate setup deadline fit the unchanged
80-minute job with two minutes for checkout and three for staging/upload.
Individual setup step caps overlap; their sum is not an additional allowance.
The timing summary derives its setup budget from `tools/live_shard.py` too.
Its `setup_and_preparation_seconds` conservatively includes teardown; the
completion cutoff remains strictly less than 1620 seconds. Before closing the
boundary, the reporting step atomically writes an incomplete fallback with
`outcomes.close: unknown` and no binary provenance. It updates that outcome to
`success` only after the close command and checkpoint projection succeed, or
`failure` for a close or projection error; the latter does not identify which
operation failed. Failures remain fatal, and uncertain closure never authorizes
reading workspace provenance or staging native results. If the reporting step
is terminated during close, its prewritten incomplete summary remains available.
The startup allowance is not a measured cold-start guarantee: overruns still
fail the unchanged final budget checks rather than consuming the evaluation reserve.
The PR timing check exercises the same setup and preparation commands, without
models, against the full Audit dataset. Its result is a preparation measurement,
not a live evaluation or full-matrix pass. If the remaining window cannot
support the handoff, preparation or collection fails before inference. Bounded
setup/preparation/collection steps and the existing `always()` fallback uploads
provide a second guard; these limits do not guarantee timely package availability.

The credential-free timing job adds `--timing-checkpoint` to preparation, writing
`preparation-operation-timing.json` beside the existing timing summary. This
opt-in file must be new, in an existing directory outside the private preparation
output. It is atomically replaced before and after each Docker operation and at
phase/image boundaries. It records only fixed event, phase, operation and failure
categories; completed unique trial-image count; a one-based trial-image ordinal
(zero before any image); and monotonic operation, image and total elapsed seconds
with remaining preparation budget. Image completion means its offline probe
passed. Shared dependency work is measured before trial-image clocks start.
`tools/prepare_live_images.py --workers {1,2,4,8}` and
`tools/live_shard.py prepare --workers {1,2,4,8}` explicitly select bounded
concurrent task-image chains. The default remains serial (1) until cold hosted
benchmarks select a winner. The immutable shared default dependency parent is
built once; each unique task identity retains source-build, layer-build and
offline-probe order. All workers share the same absolute preparation deadline.
On failure, new chains stop admission and admitted clients settle before outer
owned teardown; no incomplete manifest is published. Concurrent checkpoints
retain schema version 1, stable plan ordinals and global completion counts;
operation/image durations are worker-local, so completion ordinals may be out
of order. These are host-operation boundary timings, including checkpoint overhead, not
Dockerfile instruction or package timings; build instruction progress is not
observed. No command arguments, image IDs, paths, environment values or task/log
content are emitted. Console progress and retained history stop at 2048 events;
the latest checkpoint still updates and marks history truncation. The file is
capped at 1 MiB and counts at 128 trial images; elapsed fields saturate at one day.
The `always()` upload retains only the safe summary and any checkpoint validated
and projected after confirmed closure. Failed closure leaves the checkpoint
private rather than uploading unvalidated data. A process killed
mid-operation leaves its last observed boundary, not a fabricated final duration
or success. Runner loss can still prevent artifact upload. The checkpoint option
does not change any configured deadlines or live-shard gates.

The standalone SkillSpector **Security Scan is skipped by CI policy**. It is
excluded from `--checks`, recorded in `policy.skipped_checks`, and represented
as `skipped` in each skill's validator summary. A passing aggregate means the
selected checks passed; it does not claim a completed SkillSpector security scan.
Secrets detection, code-risk analysis, code hygiene, PII, license, Unicode,
schema/version, quality and lint checks remain enabled. Incomplete evidence from
any enabled scanner still prevents a pass. Historical reports retain their
original policy and verdicts; scores across different policies are not equivalent.

## Live evaluation on main

Live evaluation uses the `skill-evaluator` GitHub environment, restricted to
branch `main`. It does not require a separate environment approval. The branch
restriction is configured in GitHub settings; declaring an environment in YAML
does not create it. Repository administrators can still change those settings.

With `SKILL_EVALUATION_LIVE_ENABLED=true`, pushes to `main`, including merges,
automatically run Tier 2 and Tier 3 for all skills. PRs run planning and
credential-free preparation checks, not model calls. Main currently requires one PR approval; the branch rule
does not specifically require ASE or code-owner approval.

Manual dispatch defaults to planning and credential-free preparation; selecting `run_live` from `main`
executes the selected tier and skill without an additional approval.
Maintainers manage credentials and provider configuration outside the repository.
Public contributors do not need access to them.

NVSkills CI is separately opt-in through a `/nvskills-ci` PR comment by a
maintainer or administrator. The request workflow also handles trusted signature
pushes, but does not run an automatic required-status check on PR opening or
updates.

## Reading reports

Download reports from the workflow run's **Artifacts** section:

- `skill-evaluations-<run-id>-<attempt>` contains the static-check summary.
- `skill-evaluations-live-<run-id>-<attempt>` contains the live-evaluation JSON
  and Markdown aggregate summaries.
- `skill-evaluation-plan-<run-id>-<attempt>` contains the coverage plan.
- `skill-shard-<run-id>-<attempt>-<check>-<skill>` contains an individual job report.
- `patched-opencode-<platform>-<run-id>-<attempt>` contains the verified binary
  and transport manifest; it expires after seven days.
- `opencode-build-summary-<platform-index>-<run-id>-<attempt>` contains bounded
  build status, timing, and provenance, including a fixed failure category.
- `preparation-timing-<run-id>-<attempt>` contains the hosted AMD64 Audit setup
  duration and step outcomes, without native logs or task inputs.

Both preparation timing and evaluation shard artifacts include the exact
`tooling-build-diagnostics.json` file. A separate always-run projection prints
the same allowlisted diagnostic fields to the CI log immediately after create,
before inference credentials are passed. It is independent of final cleanup:
cleanup failure remains fatal and still prevents staging evaluation results.
The tooling build has a 360-second cap. Image inspection uses the remaining time.
APT may retry a transient package-download failure once within that budget.
The build itself is not retried. Permanent errors and failed hash checks remain fatal.

The diagnostic records host phase, elapsed/build/inspection seconds, process
exit code when available, and fixed failure categories. `observed_stage` is
only the last recognized marker in a bounded 256 KiB tail scan: apt index, apt bootstrap,
apt acquisition, plan validation, staging, download, promotion, apt install,
Compose download, Compose verification, version checks, or `complete`.
The last marker does not identify a stalled package or prove the build met its budget.

Schema version 2 adds `stage_timing_status` (`observed`, `unavailable`, or
`invalid`), `timing_origin_stage`, `stage_start_offsets_seconds`,
`completed_stage_seconds`, and `open_stage`. Fixed recipe markers sample Linux
`/proc/uptime`; only offsets and durations (0–86400 seconds, centisecond precision)
are published, never absolute uptime or raw log text. Maps contain only the fixed
stage names above, plus `apt_cleanup`, emitted immediately after successful
`apt-get --no-download install` and before helper/recipe cleanup. Durations require
both starts of adjacent recipe stages; a missing boundary is not bridged.
`open_stage` has no observed closing boundary, not a measured interrupted-stage
duration; `complete` clears it to `unknown`. Process failure/timeout is reported
separately. Even a successful process does not invent a missing closing marker.

The first retained marker is **not the build clock origin**. Its offset is zero;
image pulls, earlier COPY work, evicted markers, and time after the final marker
remain unmeasured. Do not subtract these offsets from host `build_seconds` to
claim an interrupted install duration. A truncated tail can still provide local
stage durations, not total budget attribution. Duplicate, backwards, malformed,
out-of-range or mixed timed/untimed marker sequences discard timing as `invalid`.
Old schema-1 checkpoints and untimed logs remain readable with timing unavailable.
`apt_cleanup` also includes the intervening layer export before Compose starts.
Markers are observations of untrusted Docker output, **not attested causes**;
cached, absent, truncated, or unrecognized markers can leave the stage unknown.
No network-error wording is used to infer a root cause. A process killed before
its final checkpoint can retain `running`, which does not prove it is still alive.

Raw `build.log`, inspection payloads, paths, URLs, arbitrary exception strings,
and Docker lines remain private and are never uploaded. The public projection
accepts only an exact typed schema from a regular, single-link file no larger
than 4 KiB, rejecting symlinks (including ancestors), malformed JSON, duplicate
keys, and unknown fields. Missing or invalid checkpoints produce fixed
unavailable diagnostics, not guessed failures. A projection write failure prints
only `projection_failed`; it does not skip otherwise eligible runtime, preparation,
or evaluation steps and cannot suppress the separate always-run cleanup.

Aggregation accepts only matching revisions, policies, and the current run
attempt. Use **Re-run all jobs** for a complete retry; rerunning only failed jobs
leaves earlier-attempt results unavailable in the new aggregate.

Summaries identify the evaluated revision, skills, checks, and available
results. A high quality score does not imply that all checks passed. Missing
reports, provider failures, and timeouts are incomplete evidence; skipped or
unrun work is not a pass. Small synthetic evaluations are smoke tests, not
proof of broad task performance.

Live summaries use schema `nemo.eval_author.live_skill_evaluations.v2` to
distinguish nullable dimension scores and lifts from the earlier v1 contract.
Shards and aggregates must use the same schema; v1 shards are not accepted into
a v2 aggregate. Historical v1 artifacts are not rewritten.

Completed Tier 3 summaries retain all five dimension keys in each arm's
`dimensions` map and in `dimension_lift`. Before accepting omitted dimensions,
the collector checks the native requested metric lists, score maps, and per-arm
`not_applicable_metrics`: every requested metric missing a score must be
explicitly declared N/A. Unexplained missing metrics are incomplete evidence,
even if every execution attempt was scored. An accepted omitted dimension is
JSON `null` and appears as `unmeasured` in Markdown, not zero.
A lift is numeric only when both arms measured that dimension; otherwise it
is also `null`. Zero scores remain measured, and negative lifts remain valid
completed results. Both arm maps are required: malformed entries, including
explicit null native scores, are incomplete evidence rather than unmeasured
dimensions. Completion still requires successful execution and every expected
attempt scored in both arms; missing dimensions do not relax those checks.
Both arms may have all dimensions unmeasured when the native N/A evidence
justifies it. Their pass rates and pass-rate lift remain separately measured;
completion does not imply that any dimension was measured.
The all-dimensions-unmeasured case is a synthetic summary-contract boundary,
not a demonstrated completed default-native run: v0.7.0 never marks its default
security metric N/A.

Context guard diagnostics retain only observed, bounded integer metadata:
`pair_count`, `vector_dimension`, `scalar_work`, and `limit` for scalar-work
rejection, or `actual` and `limit` for chunk-count rejection. Missing or invalid
values are omitted, not estimated; raw provider and source metadata is excluded.

Public diagnostics contain bounded counters and fixed failure categories, not
raw logs. `observed_signature: bun_node_epipe` means the retained output matched
a stream-error signature; it does not identify the cause or change the result.
Absent or partial diagnostics do not rule out a runtime or provider failure.
`non_json_lines` counts retained lines that could not be parsed as JSON; those
lines alone no longer make capture partial. `runtime_failure` reports a fixed
wrapper or startup-hook failure category from a private per-attempt host receipt.
Actionable row reasons include `patched_runtime_activation_failed`,
`prepared_image_mismatch`, and `insufficient_job_budget`. Invalid failure receipts
produce `runtime_failure_evidence_invalid`, not an inferred cause.
See [runtime contracts and diagnostic boundaries](dev-notes/live-runtime-contracts.md)
for the adapter, image-identity, and evidence-policy rationale.

Summary artifacts expire after 30 days. The live workflow does not upload raw
build logs, native reports, task inputs, credentials, or execution trajectories,
even for private repositories. Static-check native reports retain their separate
private-repository-only policy. Local reproduction outputs may contain private
data; inspect them locally rather than attaching them to public reports.

## Local checks without credentials

With the pinned static evaluation tools installed on `PATH`:

```bash
python tools/collect_skill_evaluations.py --output /tmp/new-skill-evaluation
```

To inspect coverage without model calls or an installed evaluator:

```bash
python tools/collect_live_skill_evaluations.py --output /tmp/new-live-plan
```

Use a fresh output directory outside the checkout. Static evaluation expects
clean skill inputs; planning may inspect local changes and records that state.
Normal development tests do not require live inference. See
[Development](../DEVELOPMENT.md) for the required local checks.

## Semantic regression

The separate [semantic regression pilot](semantic-regression.md) compares
narrative findings against reviewed Ratchet baselines. It uses its own workflow
and report schema, with the same protected live-execution boundary. It is advisory
and scoped to reports over frozen evidence, not full skill execution.
