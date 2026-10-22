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

Tier 3 requires a skill-owned `evals/evals.json`; missing datasets are reported
as skipped rather than treated as successful evaluations.

Live runs use independent jobs for each skill's Tier 2 context check and eligible
Tier 3 dataset, plus one collection-wide similarity job. At most two jobs run
at once. Each writes a fallback before setup and checkpoints completed work;
the final aggregate records missing or invalid job reports as incomplete.
Runner loss or cancellation can still prevent the aggregate job from publishing.

CI permits up to 100 million scalar comparisons per context check using the
pinned evaluator's unchanged pairwise algorithm, with a five-minute timeout.
The allowance is recorded in report policy and applies only after verifying the
installed evaluator commit. Exceeding either bound remains incomplete.

The workflow files are the source of truth for tool versions, limits, and
artifact configuration:

- [Static checks](../.github/workflows/ci.yml)
- [Coverage planning and live evaluation](../.github/workflows/skill-evaluation-live.yml)

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

Artifacts expire after 30 days. Full native reports are retained separately
only while the repository is private, under its access controls. They may
contain task text and unredacted findings; summaries and full reports should
be reviewed before sharing. Logs, credentials, and full execution trajectories
are not intended as published report artifacts.

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
