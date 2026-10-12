<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# SkillEvaluator CI reports

SkillEvaluator provides advisory checks for the skills in this repository:
the Eval Author skills under `skills/` and the ethos skills under
`ethos/skills/`. Tier 1 covers both trees; Tier 2 and Tier 3 cover `skills/`.
Findings and incomplete evaluations remain visible in reports, but do not
replace the required unit tests, lint, or type checks.

## Checks and execution

| Check | Purpose | When it runs |
| --- | --- | --- |
| Tier 1 | Static checks of skill structure, code, security, and documentation quality | Same-repository PRs targeting `main`, pushes to `main`, manual CI dispatch, and daily at 07:05 UTC |
| Tier 2 | Model-assisted context and similarity analysis | Approved live runs on `main` |
| Tier 3 | Bounded agent evaluations using reviewed synthetic datasets | Approved live runs on `main` |

Pull requests also receive a credential-free coverage plan. They do not run
live inference. Fork PRs are excluded from these evaluation workflows.
Daily CI also runs the Python test matrix and three independent native Gym
input routes. Gym summaries remain separate from SkillEvaluator artifacts; see
[retained CI measurements](../DEVELOPMENT.md#retained-ci-measurements). The daily
schedule starts after merge to `main` and does not enable live model evaluations.

Tier 3 requires a skill-owned `evals/evals.json`; missing datasets are reported
as skipped rather than treated as successful evaluations.

The workflow files are the source of truth for tool versions, limits, and
artifact configuration:

- [Static checks](../.github/workflows/ci.yml)
- [Coverage planning and live evaluation](../.github/workflows/skill-evaluation-live.yml)

## Live evaluation approval

Live evaluation uses a protected GitHub environment. It is restricted to
`main`, requires approval from the designated maintainer team, prevents
self-approval, and disables administrator bypass. These protections are
configured in GitHub settings; declaring an environment in YAML does not
create them. Repository administrators can still change those settings.

When live evaluation is enabled, pushes to `main` request an approved run.
Manual dispatch from `main` defaults to planning only; selecting `run_live`
requests live execution for the selected tier and skill, subject to the same
approval requirements. Maintainers manage credentials and provider configuration
outside the repository. Public contributors do not need access to them.

## Reading reports

Download reports from the workflow run's **Artifacts** section:

- `skill-evaluations-<run-id>-<attempt>` contains the static-check summary.
- `skill-evaluations-live-<run-id>-<attempt>` contains the live-evaluation JSON
  and Markdown summaries, including plans when no live work was requested.

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
