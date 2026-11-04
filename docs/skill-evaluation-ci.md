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
the source of truth for `OPENCODE_VERSION`. Its
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
It does not collect independent binary-version evidence or claim an observed
version. An observed version needs trusted runtime version-command output;
missing evidence remains unobserved. Pinning removes one source of drift; it does
not freeze provider models, network dependencies, task images, or evaluation results.

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
