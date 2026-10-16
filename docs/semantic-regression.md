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

## Pipeline binding and CI diagnostics

The reviewed baseline binds the exact bytes of `tools/semantic_regression.py`
and `tools/semantic_ratchet.py`. Even a metadata-only edit changes this binding.
These files retain the October 6 reviewed pipeline; the historical repository
name inside it is intentional. CI uses `tools/run_semantic_ci.py` to publish the
current repository name and diagnostics without changing the measurement code.

The runner checks baseline inputs before inference. A binding mismatch produces
an incomplete report with `baseline_binding_mismatch` and the mismatched field
names, such as `pipeline_digest`. Private values and exception bodies are never
published. If any baseline fails preflight, other cases are marked `not_run`.
Baseline digests, review metadata and run counts are also checked. CI never
rewrites locks or recalibrates to clear these errors.

A deliberate measurement change requires a reviewed baseline update and an
update to the pinned pipeline digest test. Reporting changes belong in the CI
runner. The live job remains advisory (`continue-on-error: true`), so inspect
its job result and aggregate report even when the workflow is green.
