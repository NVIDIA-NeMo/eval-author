<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Semantic regression pilot

This advisory check measures whether narrative reports retain recurring findings
and remain consistent across repeated runs over fixed synthetic evidence. It
measures report stability; factual correctness and full skill execution require
separate evaluations.

## Baseline

Results are compared against a fixed, reviewed baseline. Merges and passing runs
do not replace it automatically. Skill edits are evaluated against that reference;
reporting and repository metadata changes do not invalidate it.

Changes to the evidence or measurement method require a version update and
baseline review. Replacement baselines must be checked with independent runs and
deliberately damaged results before acceptance.

## Results

- **Passed:** retention and consistency meet the calibrated thresholds.
- **Regression:** a measured result falls below a threshold.
- **Incomplete:** there is insufficient valid evidence to reach a verdict.

Reports include the candidate and recorded baseline revisions. Failed or
interrupted measurements remain visible without being counted as passes. Public
summaries contain aggregate results; raw reports and baseline review materials
remain private.

## CI

PRs run a plan without inference. Live comparisons run on `main` pushes, daily,
and on manual dispatch. The check is advisory, so a green workflow alone does
not establish a passing measurement; use the report's verdict and diagnostics.

Measured regressions emit GitHub warnings and leave the comparison step successful.
The JSON artifact retains `status: regression` and the measurement's exit code;
the summary shows measured values, baseline thresholds, and the allowed drop.
Execution errors and missing evidence remain incomplete and fail the comparison
step, even when another case has a measured regression. The job remains advisory
to the overall workflow. Local checks stay strict unless `--warn-on-regression`
is explicitly passed to `tools/run_semantic_ci.py`.

Review detailed comparisons before changing a baseline or tolerance. CI retains
sanitized summaries, not the underlying claim comparisons; a summary alone cannot
establish whether reduced consistency represents a meaningful behavioral change.

See [the workflow](../.github/workflows/semantic-regression.yml) for execution
settings and [SkillEvaluator CI reports](skill-evaluation-ci.md) for artifacts.
