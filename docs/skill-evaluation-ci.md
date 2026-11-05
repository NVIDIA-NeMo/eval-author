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

Pull requests also receive a credential-free coverage plan. They do not run
live inference. Fork PRs are excluded from these evaluation workflows.
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
keeps a 60-second timeout. The outer live CI shard job allows 60 minutes for
dependency setup, evaluation, and report retention. These limits are a candidate
based on local measurements, not a guarantee of completion: runtime preflight
took 6.7 minutes, the with-skill arm took 12.7 minutes, and the last baseline
trial finished 34.6 minutes after evaluation started, with no final native
aggregate available. The former 30-minute evaluation limit had already expired,
so late trial results did not establish a completed evaluation. The 45-minute
limit bounds the collector's wait; cancellation does not guarantee that all
child processes or container work stop. Case counts,
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

The workflow files are the source of truth for tool versions, limits, and
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

Harbor supports `--agent-kwarg version=<release>`, but SkillEvaluator 0.7.0 does
not expose that option; its per-agent configuration accepts only a model.
Passing the Harbor flag to `skillevaluator evaluate` is not a supported pin.
The adapter instead checks the installed evaluator's exact Git revision from
`direct_url.json`, its release version, Harbor 0.24.0, and the unchanged private
agent-kwargs table before adding OpenCode's version. Contract drift or an existing
OpenCode override fails closed before evaluator dispatch. Existing agent entries
are preserved. The native Harbor command builder forwards the pin to runtime
preflight and both A/B trial arms without changing Docker backends or grading.

Only Tier 3 `evaluate` uses the wrapper, under `--evaluator-python` (defaulting
to the collector's Python). When using a separate evaluator environment, provide
that environment's Python as well as its `--evaluator` executable; the wrapper
requires the pinned packages in that interpreter. Dataset `tier3 validate`
still invokes the evaluator directly; Tier 2 behavior is unchanged.

Keep an intended version separate from an observed runtime version. Harbor's
`agent_info.version` and trajectory agent version may echo the configured pin:
when a version is configured, Harbor skips its normal version-detection command.
The collector exposes these only as `agent_diagnostics.opencode_reported_versions`.
These public fields are not independent binary-version evidence. The patched
runtime separately records trusted version-command output and binary checksums
in private trial receipts; missing evidence remains unobserved.
Pinning removes one source of drift; it does
not freeze provider models, network dependencies, task images, or evaluation results.

### Patched live runtime

Live CI builds a patched OpenCode binary before the credential-bearing evaluation
step, using [`tools/build_patched_opencode.py`](../tools/build_patched_opencode.py).
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

Then use the normal live-collection command and pinned evaluator environment.
Only Tier 3 evaluation receives these settings; dataset validation and context
checks remain unchanged. Without either setting, local collection retains the
pin-only behavior. CI sets the requirement explicitly and refuses a missing or
invalid manifest instead of falling back to the unpatched binary.

The runtime preserves Harbor's base setup, gateway, Docker backend, arguments,
and scoring. For the explicitly activated patched path, it replaces the redundant
NVM/npm OpenCode bootstrap with verified standalone-binary provisioning. The
original system dependency checks for curl, bash, coreutils, nodejs, and npm remain;
this path does not install an additional NVM-managed Node 22. The unactivated
installer is unchanged. The patched CLI embeds its own runtime.

It uploads the built binary into each trial, checks its host/container hash and
patched version, and substitutes only the agent executable. The private
`.ea-patched-binary.json` receipt distinguishes the configured release from the
observed patched build. Nonzero exits and missing scores remain failures. Build
or runtime-contract mismatches prevent execution; they do not become passing scores.

Live CI additionally prepares every task environment in both arms **before the
step receiving inference credentials**. It retains the native task Dockerfile,
fixtures, workspace isolation, and custom commands, then adds the required
system packages. Preflight and both arms use the verified local image IDs;
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
and Linux AMD64 or ARM64; inference credentials are removed from its child
environment. Keep the preparation directory private: it includes staged task
inputs and image provenance, not a public report. Regenerate it after source or
runtime changes. Unset both image-preparation variables to retain the original
local installation path. CI always requires preparation for live shards; its
existing fallback summaries and main-only environment gate are unchanged.

The default Python 3.12.16/trixie base is pinned by platform-specific registry
digest. Added packages have exact Debian versions, including Node.js
20.19.2 and npm 9.2.0; the manifest records the full Debian revisions and final
local image IDs, and setup verifies installed versions. Other custom bases must
already use digests and support these package versions. Only single-stage,
Dockerfile-only `evals.json` tasks with isolated paired workspaces are supported;
unsupported configurations fail closed, not through a generic replacement image.
The original custom Dockerfile is validated before native staging; multiple stages,
parser directives, heredocs, and inherited `ONBUILD` triggers are unsupported.
Layering uses the inspected immutable parent ID, preserves the task's execution
metadata, and runs package installation with an explicit shell.
Task identities bind each arm, native task name, normalized full task configuration,
and environment content. Only exact same-name gateway credential/provider
placeholders may differ between keyless and credentialed staging. Resource,
working-directory, or arbitrary environment overrides require new preparation.
Only public startup and phase network policies are supported: network-control
sidecars and Compose overlays are rejected before native startup rather than
built after credentials arrive. The offline setup test disconnects networking
externally; it does not establish native no-network-policy support.
Debian indexes and transitive dependencies in native task installation remain
mutable: snapshot package-index retrieval was unavailable during validation.
Exact direct-package versions failing to resolve cause preparation to fail;
they are never replaced with newer versions. This is verified image reuse, not
a claim of bit-for-bit reproducible rebuilds. It does not change agent execution
deadlines or guarantee model responses or timeout cleanup.

Preparation has a 600-second aggregate deadline across all images, not a fresh
allowance per Docker command. CI also deducts elapsed dependency/binary setup
from a shared monotonic job window, reserving the unchanged 2700-second evaluation
allowance plus 60 seconds for collection overhead. The window ends 55 minutes
after the fallback step starts, leaving reporting headroom within the 60-minute
job (less checkout time). If the remaining window cannot
support the handoff, preparation or collection fails before inference. Bounded
setup/preparation/collection steps and the existing `always()` fallback uploads
provide a second guard; these limits do not guarantee timely package availability.

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
automatically run Tier 2 and Tier 3 for all skills. PRs only generate a plan
without model calls. Main currently requires one PR approval; the branch rule
does not specifically require ASE or code-owner approval.

Manual dispatch from `main` defaults to planning only; selecting `run_live`
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

Public diagnostics must be an allowlisted projection, not redacted copies of
raw logs: retain bounded counters, fixed failure categories, and version metadata
explicitly distinguished as configured, reported, or independently observed.
Raw exception messages, command arguments,
paths, URLs, prompts, environment values, provider response bodies, and agent
output can contain private data. Do not publish them to explain a failure.
An incomplete diagnostic is preferable to guessing a provider error from an
exit code or from the presence of `--format=json` in a failed command. A native
`NonZeroAgentExitCodeError` may retain `observed_signature: bun_node_epipe` when
a bounded, non-JSON block in the same trial's `opencode.txt` matches the Bun/Node
stream-error signature. This is an observation, not a provider/authentication
category or causal attribution: identical spoofed raw output is indistinguishable.
JSON payloads and ordinary EPIPE mentions do not qualify. Capture remains partial;
stdout and runtime stderr are covered only when retained in that native file,
without identifying the originating stream, process, or pipeline stage. Other
signatures and unretained stderr remain unclassified. The observation survives
sanitized aggregation without changing `agent_command_failed`, incomplete status,
scoring, or retry behavior.

Artifacts expire after 30 days. Full native reports are retained separately
only while the repository is private, under its access controls. They may
contain task text and unredacted findings; review them before sharing. Private
native retention is not permission to include raw evidence in public summaries.
Logs, credentials, and full execution trajectories are not intended as published
report artifacts.

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
