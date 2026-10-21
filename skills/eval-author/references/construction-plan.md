<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Private construction declarations

Use the same evidence discipline for proposals and new tasks: identify who or
what defines correctness, distinguish copied inputs from authored starting state,
and connect every requirement to an observable check. Keep these declarations
outside the agent-visible task tree. A trace's final state is not proof of its
starting state; a source agent's answer or self-test is not an independent oracle.

For proposal-only work, record this in the proposal without scaffolding a task.
For trace-derived proposals or starting state, also use
[trace construction](trace-construction.md) to preserve the source finding,
cutoff, reconstruction limits and intended behavioral distinction.
An unresolved answer rule is a useful design question, not permission to invent
an expected answer. Missing input files can be authored later when the domain
rules support them; label that intent rather than claiming historical recovery.
Do not add a dependency chain to a simple task merely to make it harder.

For a selected new draft, write `construction-plan.json` in its private
measurement directory. Use the validator below before running controls, then
pass the plan to `task_evidence.py prepare --construction-plan <path>`. The
recorder binds exact plan and task-input bytes and rechecks them at execution
and receipt verification. Historical manifests remain readable; callers without
a plan have unmeasured construction declarations.

## Format and validation

The schema is `nemo.eval_author.construction_plan.v1`. Its required fields are:

| Field | Contents |
|---|---|
| `task_id`, `provider` | Match the native result contract; provider is `harbor` or `gym`. |
| `requirements` | Objects with `id`, `statement`, and `authority: {basis, reference}`. Basis is `specification`, `user_decision`, `independent_reference`, or `unresolved`. Cite the actual rule or evidence, not the source agent's confidence. |
| `inputs` | Objects with task-relative `path`, `basis` (`copied` or `authored`), `source`, `rationale`, and `sha256`. Hash may be null in proposal mode; draft mode requires the materialized bytes. Empty is appropriate when no files are needed. |
| `checks` | Objects with `id`, `requirements` (IDs), and `success` (native JSON-pointer assertion). Map all requirements, including preserved state where relevant. |
| `cases` | Objects with `case` (native contract case ID) and `observations`: objects with `check` (ID) and `expected` (native assertion). These must exactly match that case's behavior assertions. |
| `prerequisites` | Usually empty. Each declared prerequisite has `before`, `after` (requirement IDs), `bypass_case`, and `check`. The bypass must fail the dependent requirement's own check, not an unrelated or aggregate check. |

IDs use lowercase kebab-case. Assertions use `{"pointer": "/...", "equals": value}`
or `{"pointer": "/...", "min": number, "max": number}`. Use the actual native
result shape. Do not write an adapter to fabricate observations a provider lacks.

For example, a requirement `total` can cite an independently reviewed sum rule;
its check `total-correct` can map to native `/verifier_result/rewards/reward`
with `min: 1, max: 1`. Reference and agent cases map that success assertion.
An incorrect control expecting reward zero maps the same check with
`min: 0, max: 0`: its control receipt passes when the task success condition fails.
Map the empty and any alternative or side-effect cases too. For nonbinary rewards,
use the actual agreed success threshold rather than assuming one means success.
A correct zero-valued answer is not an empty submission.

```bash
python <task_create_dir>/scripts/construction_plan.py \
  --plan <private-plan.json> --mode proposal
python <task_create_dir>/scripts/construction_plan.py \
  --plan <private-plan.json> --mode draft \
  --task-dir <task-directory> --contract <native-contract.json>
```

Proposal validation permits unresolved authority and unmaterialized inputs.
Draft validation rejects unresolved authority, mismatched hashes, unmapped
requirements, incompatible native assertions, and cyclic prerequisite declarations.
The plan is a declaration, not an additional verifier or a replacement for the
native contract. Use concrete alternative/side-effect not-applicable explanations
in that contract when appropriate.

## What this proves and what remains to run

A structurally valid plan proves consistency of declarations and byte bindings.
It does not prove the grading rule is correct, a dependency is necessary, the
starting state is historical, or the task is difficult. Review the evidence and
run the native controls described in [task validation](task-validation.md).

Keep setup health separate from behavior evidence. An import smoke, absent
exception, or aggregate reward alone does not establish that a targeted negative
reached the intended behavior. Inspect the native control evidence; if it cannot
show that distinction, retain it as unmeasured instead of claiming qualification.
A failed healthy real-agent attempt can be a useful first-eval baseline; do not
rewrite its expected success condition or repair the task to turn it into a pass.
Task-create's successful repeated gap closure is a separate acceptance rule.
