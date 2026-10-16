<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# CI evidence audit — October 16, 2026

Snapshot: available semantic history September 29–October 16, through main
revision `48c2b3bd528a5584882f95f68d2fbcc180e891f2` at 17:58 UTC. Inventory
included 108 semantic workflow runs and 124 main-branch workflow runs in this
period (29 semantic, 49 CI, 34 SkillEvaluator Tier 2/3, 12 NVSkills requests).
Main-branch evaluation job attempts and aggregate artifacts were inspected;
ordinary CI PR artifacts were not exhaustively audited. Counts below refer to
this snapshot, not later reruns or the repaired branch.

## Semantic regression

- 79 PR runs: all published a plan; no live comparison was expected.
- 29 main runs, 30 attempts: two intentionally skipped live; all 28 started live
  attempts published an aggregate, including the cancelled attempt.
- 14 attempts passed both cases; two had measured regressions; 11 were entirely
  incomplete; one cancellation retained one passing case and one incomplete case.
- Thus 11/28 (39.3%) live attempts were entirely incomplete, and 12/28 (42.9%)
  had at least one incomplete case. There were no missing live reports.
- All 22 observations in the 11 entirely incomplete attempts carried
  `execution_or_input_error`. These began with the repository-name change in
  [run 38605545577](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/38605545577).
  The baseline compatibility repair addresses that pre-measurement failure.
- Before the rename, [run 38595383500](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/38595383500)
  had a genuine measured regression, while
  [run 38530651520](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/38530651520)
  passed both cases. Incomplete evidence must not be pooled with either outcome.

## Tier 1 collector in CI

- 49 main CI runs: 48 Tier 1 aggregate reports published; one scan was cancelled
  before the collector's final write and published no aggregate.
- All 48 reports were incomplete: 548/548 skill observations reported
  `required_scanner_evidence_incomplete`. Successful collection is not a passing
  security evaluation. Quality scores and validator statuses are still present.
- The latest report's Security Scan validator is incomplete. Its summary records
  SkillSpector 2.11.2 and evaluator 0.2.1, but does not retain the scanner's detailed
  incomplete reason. The exact scanner failure cannot be established from this
  aggregate alone. Do not weaken the gate or turn these into passes.
- [Cancelled run 38286059894](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/38286059894)
  demonstrates the publication gap: scan cancelled, upload step successful,
  aggregate absent. The collector wrote only after the full loop. Atomic initial
  and per-skill checkpoints address this gap; missing-file upload errors make a
  future delivery failure explicit.
- Two overall CI failures in the period were third-party license checks in the
  Python 3.13 test job, not lost evaluation reports:
  [37587717876](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/37587717876)
  and [36622298148](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/36622298148).

## Tier 2/3 live evaluation

- 34 main workflow runs: five live jobs actually executed (three finished and two
  were cancelled during evaluation). All five published aggregate reports.
- The remaining 29 live jobs had no executed steps: 28 cancelled and one waiting.
  These are not failed measurements or lost live reports. Plan artifacts account
  for intended work separately.
- Across the five live reports: 15 incomplete, 12 not run, 31 skipped, 11 failed,
  23 passed and 17 completed observations. These combine different tiers and
  checks and are not one comparable quality score.
- Of 15 incomplete observations, 11 diagnostics identify the Tier 2 scalar
  comparison limit (25,000,000), two are interruptions, and two older reports
  retain only `analysis_incomplete`. All 31 skips were missing datasets.
- All 11 measured failures have duplicate-content diagnostics. For example,
  [38922241879](https://github.com/NVIDIA-NeMo/eval-author/actions/runs/38922241879)
  flags repeated license headers as duplicate content and the scalar-comparison
  limit for three large skills. These need measurement-method review; they do
  not justify deleting required notices or raising limits without investigation.

## Remaining operational limits

A report can represent failed or partial measurement without claiming a score.
Initial reports, atomic checkpoints and unconditional upload cover normal tool
failures and mid-run cancellation. Checkout failure, runner loss before the first
write, and artifact-service outages can still prevent delivery. Those must remain
visible as publication gaps; an artifact cannot be guaranteed when its transport
is unavailable. No new model calls were made during this audit.
