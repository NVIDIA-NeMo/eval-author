<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Harness-derived SkillEvaluator datasets

The four datasets added for audit, discovery, Intake inspection, and trace
environment derivation each contain three scoped cases and one unrelated
negative case. CI discovers them through each skill's `evals/evals.json`.

## Conversation coverage

The pinned SkillEvaluator converts each case's prompt into one Harbor task
instruction. The agent can take multiple tool steps, but this dataset path does
not replay subsequent user messages. These are independent checkpoints: opening
and review gates, narrow operations, or supplied-state handoffs. Passing them
does not establish that a complete interactive workflow passed.

The source is the `portable-synthetic-v1` harness comparison corpus in
`NVIDIA-dev/nemo-eval-author-fixtures`, pinned to commit
`ac5ad11ca4806a3e97ba0d5cc4a2be9eb519742b`. The
[provenance manifest](harness-dataset-provenance.json) maps cases to source
scenarios and records source paths, byte hashes, and transformations. Negative
arithmetic cases are new controls. Historical harness results remain separate.

## What these cases establish

| Dataset | Scope |
| --- | --- |
| Audit | Opening pause, existing-spec validation, historical coverage interpretation |
| Discovery | Inventory, Oracle/grader limitations, blocked-readiness interpretation |
| Intake inspection | Missing selection, rejected URI, unavailable CLI boundaries |
| Trace environment | Harbor and Gym proposals, blocked-validation handoff |

Inputs are synthetic. Trace provenance explicitly records no native task or
model execution. The discovery fixture retains its weak grader and Oracle-only
configuration. No real Intake source is supplied, so these cases cannot prove
successful service access or trace inspection. Supplied error receipts are
constructed checkpoint inputs, not observed runtime results.

Each case explicitly selects its input files. SkillEvaluator stages these under
`/workspace/input`; output artifacts go under `/workspace/output`. The dataset
provenance manifest and evaluator expectations are not included in those input
selections; trace provenance is included where needed. The
existing isolated workspace and default grading policy remain in use.

Native dataset validation checks structural compatibility. Agent behavior,
artifact correctness, and paired evaluation scores require a live Tier 3 run;
the dataset addition alone provides no such result. Default grading supplies
behavioral judgments, not deterministic artifact acceptance tests.
