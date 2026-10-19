<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Determinism implementation validation

This records the Phase 3–6 implementation checks in PR #75 on 2026-10-14,
following consolidation of #76. It distinguishes checker behavior from live
workflow acceptance. Nothing was merged to main and no customer evals were run.

## Local validation

The full macOS Python 3.13 suite completed with **1,036 passed, 15 skipped,
1 failed**. The failure is the unchanged
`test_scaffold_uses_harbor_native_task_layout`: it expects schema 1.3, while the
Harbor 0.24 CLI selected from the user's PATH generated schema 1.4. The same
failure was reproduced on the unchanged main baseline. The project pins Harbor
0.20 for development. This run is not reported as a green full suite.

A final focused run with the pinned development environment first on PATH
completed with **128 passed, 1 skipped**, including the scaffold test above. It
covered discovery comparison, audit binding, semantic batches, workflow capture,
Harbor/Gym receipt checks and the task pipeline, including the final retained-gate
and concurrent-completion regression fixes. The skip requires an external
semantic-ratchet runtime. Lint, formatting, type checking for Python 3.12 and 3.13 with the Linux
static target, runtime-policy consistency, copyright, and third-party license
checks passed. Local execution was on macOS; a Linux static target is not Linux
runtime proof.

The runtime locator, runtime policy reader, task scanner, Gym probe, and discovery
orchestrator have no changes relative to main. Existing locator/scanner contracts
remain in the full suite. CI additionally tests the portable workflow capture and
matrix checker beside task search on macOS, Linux and Windows, including Python
3.10 for standalone scripts. Declared CI jobs are not evidence that they passed;
consult the checks on the actual PR revision.

## Rebase and review on 2026-10-19

Rebased onto main `c1af6ca`. Fixed Windows snapshot file identity and binary
reads, required capture/review/native output for every planned turn, recorded the
actual platform, and required native session continuity for multi-turn cells.
Semantic gate receipts now bind retained batch and gate input files to the
reviewed baseline and measurement identity; incompatible locks and copied gate
receipts are rejected without changing baselines or tolerances.

The locked macOS Python 3.13 suite ran with 1,092 passed and 15 skipped; 16 other
tests failed or errored under sandbox restrictions on Git/subprocess operations.
Rerunning both affected test modules outside those restrictions passed all 86
tests, including all 16 failures/errors. Focused final workflow and semantic batch
checks passed all 16 tests. Ruff lint/format, Linux-target type checks for Python
3.12 and 3.13, runtime policy, copyright, and license checks passed. This is local
checker validation, not live cross-host acceptance; Windows runtime validation
belongs to the PR CI run for the pushed revision.

## Live host observations

The complete plan has **912 cells**, covering documented hosts, operating
systems, installation forms, contract paraphrases and repetitions. Three local
Codex attempts were retained across two cells:

- Startup failed under the initial process sandbox.
- A second attempt failed because the selected model was unavailable for the account.
- Codex 0.162.1 with the configured `gpt-6-luna` model completed the generic-help
  opening, but read repository files before the required handoff. Its final
  question was appropriate; the actual tool action violated the scope contract.

The retained summary records **0 pass, 1 fail, 911 incomplete, 0 skipped** with
**3 attempted executions**. Failures are not erased by retrying. Subsequent skill
changes also make that capture's installation digest stale; it is historic
failure evidence, not acceptance of the final revision. Raw streams and reviews
remain private. Model/provider variation and cross-host equivalence are unmeasured.

## Remaining acceptance work

- Run independent semantic batches using a reviewed baseline and concrete models.
  The checked-in pilot has placeholders and no reviewed baseline. New batch checks require retained gate input bindings. Existing reviewed locks
  use the explicit measurement-version compatibility mapping; no baseline or
  tolerance was changed during this review.
- Implement and validate native Cursor/Pi event adapters. Unknown schemas remain
  incomplete; normalized synthetic evidence cannot substitute for native capture.
- Replay and review the remaining host/install/OS cells on actual hosts, including
  Linux and Windows, and resolve the observed opening-scope violation.
- Exercise native Gym/live Solve scenarios and task-specific controls plus repeated
  real-agent closure where task acceptance is claimed. Synthetic native fixtures
  verify checking behavior only.

See [commands and evidence contracts](determinism-checks.md) and the
[phase status](determinism-plan.md). Broad Phase 3–6 live acceptance is incomplete;
the new infrastructure makes these missing or failed checks explicit.
