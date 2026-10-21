<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# SkillEvaluator checkpoint datasets

The five datasets added for audit, discovery, Intake inspection, trace environment
derivation, and Gym conversion each contain three scoped cases and one unrelated
negative case. CI discovers them through each skill's `evals/evals.json`.
Repository-authored cases also cover shared entry for first evaluations and
expansion in Gym and Harbor. The core and first-eval datasets each contain six
cases, retaining their existing routing, recovery, and negative controls.

## Conversation coverage

The pinned SkillEvaluator converts each case's prompt into one Harbor task
instruction. The agent can take multiple tool steps, but this dataset path does
not replay subsequent user messages. These are independent checkpoints: opening
and review gates, narrow operations, or supplied-state handoffs. Passing them
does not establish that a complete interactive workflow passed.

The five imported datasets use the `portable-synthetic-v1` harness comparison corpus in
`NVIDIA-dev/nemo-eval-author-fixtures`, pinned to commit
`ac5ad11ca4806a3e97ba0d5cc4a2be9eb519742b`. The
[provenance manifest](harness-dataset-provenance.json) maps cases to source
scenarios and records source paths, byte hashes, and transformations. Negative
arithmetic cases are new controls. Historical harness results remain separate.
The manifest's separate `repository_authored` section records the new first-eval
and expansion cases and synthetic agent documentation; they do not come from
the imported corpus.

## What these cases establish

| Dataset | Scope |
| --- | --- |
| First evaluations | Direct shared entry for Gym and Harbor; no existing suite or traces |
| Core expansion | Shared entry for Gym and Harbor; user-created suite, no prior audit or traces |
| Audit | Shared entry, existing-spec validation, historical coverage interpretation |
| Discovery | Inventory, Oracle/grader limitations, blocked-readiness interpretation |
| Intake inspection | Missing selection, rejected URI, unavailable CLI boundaries |
| Trace environment | Harbor and Gym proposals, blocked-validation handoff |
| Gym conversion | Offline conversion, retained original, explicit JSONL row selection |

Inputs are synthetic. Trace provenance explicitly records no native task or
model execution. The discovery fixture retains its weak grader and Oracle-only
configuration. No real Intake source is supplied, so these cases cannot prove
successful service access or trace inspection. Supplied error receipts are
constructed checkpoint inputs, not observed runtime results.

The entry cases check an opening-only first turn: the fixed welcome, selected
checklist, and “Ready to get started?” must appear in the final reply before any
repository work. Supplied agent documentation and Ethos files remain unread. Only
the entry skill and shared core may be loaded; prerequisite questions, validation, state writes,
and Ethos delegation wait for the next user turn. These cases do not test
prerequisite completion, Ethos handoff, or saved-state persistence.

Isolated staging may omit the shared core. Direct entries require loading the
core when available, or an explicit dependency handoff when it is missing.
Checking or invoking the separate Ethos skills belongs to a later turn.
Passing the opening or a dependency handoff does not establish successful
integrated onboarding, Ethos confirmation, or later-turn resumption.

Each case explicitly selects its input files. SkillEvaluator stages these under
`/workspace/input`; output artifacts go under `/workspace/output`. The dataset
provenance manifest and evaluator expectations are not included in those input
selections; trace provenance is included where needed. The
existing isolated workspace and default grading policy remain in use.

Native dataset validation checks structural compatibility. Agent behavior,
artifact correctness, and paired evaluation scores require a live Tier 3 run;
the dataset addition alone provides no such result. Default grading supplies
behavioral judgments, not deterministic artifact acceptance tests.
