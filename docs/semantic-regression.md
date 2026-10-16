<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Semantic regression pilot

The advisory semantic check measures whether narrative reports retain recurring
findings and remain consistent across repeated runs over the same synthetic
evidence. The pilot covers audit evidence gaps and limits of reconstructed-task
proof. Each case asks for a short, focused finding using the relevant skill as
context.

This measures report stability. It does not establish factual correctness, full
skill execution, task quality, coverage arithmetic, or environment fidelity.
Deterministic tests and execution-based evaluations provide separate evidence.

## Baseline and comparison

A calibration run creates the first proposed baseline; it needs no earlier
baseline. The proposal must detect deliberately damaged results and its claim
mapping must be reviewed before it becomes the reference for later checks.
Recurring checks compare against that fixed reference. They do not automatically
replace it after a regression.

Before configuring a replacement baseline for recurring checks, review the claim
mapping and run independent comparison batches against the same candidate. Keep
all results, including failures; a calibration self-test proves sensitivity to
synthetic damage, not that fresh unchanged reports pass. Also check paraphrased
findings and deliberately omitted or reversed findings through extraction and
matching. Do not weaken thresholds or select a better retention run to obtain a
pass.

The audit pilot asks directly whether the traces establish each capability.
Merely noting that a tool was not called does not answer whether its capability
is evidenced. Clustering and retention matching use the same frozen case context
to resolve references, while preserving that distinction and the difference
between missing evidence and disproven capability.

A deliberate change to the evidence or measurement method requires recalibration.
Results from different baselines or measurement methods are separate comparisons.
Provider changes can also affect results and should be investigated before
attributing a change to a skill edit.

## Reading results

- **Passed:** the measured findings and consistency meet the calibrated thresholds.
- **Regression:** a measured result falls below a threshold.
- **Incomplete:** the check lacks enough valid evidence to reach a verdict.

A plan confirms which cases would run; it contains no measured scores. Missing
baselines, failed model calls, invalid claim mappings, and interrupted runs cannot
produce an unqualified pass. Empty reports remain in the sample count.

Public summaries contain aggregate measurements and source identifiers. Raw
reports, claim mappings, baseline review packets, and operational configuration
remain private.

## Fixed baseline compatibility

The baseline is a fixed, reviewed reference. Merges, passing checks and daily
runs never replace it automatically. The evaluated skill text can change without
invalidating the baseline; those changes are the subject of the comparison.

Bindings use an explicit `measurement_version` instead of hashing the Python
source files. Repository names, comments and report formatting do not change
measurement compatibility. Changes to extraction, clustering, retention matching,
measurement prompts or scoring must bump `MEASUREMENT_VERSION` and receive a
reviewed baseline update. This version is a maintenance contract, not automatic
proof that an implementation change preserves measurement behavior.

The October 6 reviewed pipeline hash is recognized as `narrative-measurement-v1`
when reading existing locks. Only that exact historical hash is accepted; unknown
hashes and mixed legacy/version bindings fail closed. This compatibility mapping
never changes lock bytes, baseline measurements, thresholds or review evidence,
and does not allow the old baseline to match a future measurement version.
The repository name remains `NVIDIA-NeMo/eval-author`.

Query/evidence, run count, extractor, judge, endpoint and external Ratchet runtime
remain pinned. Baseline and bundle hashes remain necessary integrity checks.
Changing the frozen evidence or the external measurement runtime still requires
compatibility review; the runtime pin includes its Python and Markdown assets.

CI uses `tools/run_semantic_ci.py` to publish safe diagnostics. A binding mismatch
produces an incomplete report with `baseline_binding_mismatch` and mismatched
field names, such as `measurement_version`. Malformed or unrecognized legacy
bindings produce `baseline_input_error`. Private values and exception bodies are
never published. If any baseline fails preflight, other cases are `not_run`.

PRs currently run the plan only. Live checks run on main pushes, daily and manual
dispatches. They remain advisory (`continue-on-error: true`), so inspect the live
job and aggregate report even when the workflow is green. Live PR evaluation
against this fixed baseline and a main control is a separate workflow extension;
this compatibility repair does not enable PR access to inference credentials.
