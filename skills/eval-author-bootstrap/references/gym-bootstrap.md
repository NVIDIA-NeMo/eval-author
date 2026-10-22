<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Native NeMo Gym bootstrap

Use this reference for native NeMo Gym stages 5–9. Preserve the parent skill's
conversation, first-task approval, state, and completion requirements. No audit
report, measured gap, or closure attempt count is required. A selected combined
Gym–Harbor setup must use its configured supported integration; this native path
is not a substitute. Retain missing integration findings and report them under
**Blockers** with execution settings when concrete runs or paid checks are ready,
naming the cause, blocked step, and required action, without adding a new adapter.
Keep run-only blockers out of the initial proposal and first-task authoring;
a finding that prevents meaningful
task design uses the blocker checkpoint instead of a proposal.

## 5. Prepare tasks and grading

After design approval, proceed directly to first-task environment preparation
and authoring. Ask only about unresolved choices needed for current work and
show blockers that prevent it. Defer run-only settings and blockers; the six
execution bullets are not required to build or review the first task. After its
review approval, display them when concrete agent runs or paid grading are ready,
reusing applicable previously displayed settings without another generic gate.

Use the Python/CLI pair from the [shared runtime locator](../../eval-author/references/runtime-prerequisites.md#runtime-prerequisite-checks)
with `--provider gym --flow authoring`. Keep Gym in its separate environment and
inspect installed help. Scaffold only the approved first task in a fresh
`.eval-author/tasks/<slug>/` using
`nemo_gym.environment.scaffold.scaffold_environment(kind="environment",
name=<module_name>, root=<draft_path>)`, with a valid Python module name.
Do not use audit-driven `task_pipeline.py scaffold`: no coverage gap is required.

The first task is one reviewed scenario, normally one selected dataset row. If
that scenario needs several rows, preserve the agreed mapping explicitly; do
not silently include an entire draft/dataset. Count task directories, distinct
examples/rows, and repeated rollouts separately. Do not create remaining tasks
or their rows before the user approves the first task's reviewed revision.

Follow [Native task files](../../eval-author-task-create/references/gym-tasks.md#complete-the-task-without-changing-its-intended-capability)
for the manifest, component configuration, resources server, dataset, and verifier
fixtures. Replace template behavior. Seed the resources server from the proven
kit and pass its persistence/reset checks before writing the task's grader or
known-correct reference interaction. Derive rewards and expected end state with
an independent reference over generated data. Keep answers and provenance outside
model inputs, while retaining verifier fields in the full dataset rows.

Document reward contracts and expected controls before testing. Include the
known-correct reference, empty/no-action, and realistic incorrect interactions,
plus valid alternatives and side effects where applicable. Explain the concrete
assertion and a limitation. Native Gym uses known-correct fixtures/interactions;
do not impose Harbor's `solve.sh`, NOP, or Oracle protocol.

Write the actual task-root `README.md` with input, `ETHOS.md` behavior, expected
result, grading/reference links, environment limits, controls, and known commands.
Show whether native grading checks have run; they may remain pending at this
review after environment proof and task construction. Refresh the existing report and Markdown counterpart following
[Report generation](../../eval-author/references/suite-readme.md#report-generation).
Present all three review artifacts using the parent's deterministic review
checkpoint, with readable input, verifier, and control evidence. Preserve authored
README content outside generated markers. Missing required review output is a
blocker; do not build the remaining tasks before first-task approval.

Revise the same first-task directory on feedback, retain previous evidence,
invalidate affected controls, refresh review outputs, and request approval of the
new revision. Rerun affected controls when applicable. After approval, prepare
the agreed remaining tasks/rows and apply stage 8 to all tasks. Reuse current
checks and matching bounded authorization; do not require individual approval
of each remaining task or another validation approval. A shared dataset or
component edit changes the task revision: refresh manifests and controls for
every affected task rather than carrying forward stale validation or approval.

## 6. Prepare the execution environment

Confirm generated resources, agent, model components, dependencies, state reset,
and application access build on the kit proven by
[`eval-author-environment`](../../eval-author-environment/SKILL.md), following its
[Gym reference](../../eval-author-environment/references/gym.md). Layer task-specific
records over the kit. Follow the [version and rerun plan](../../eval-author-task-create/references/gym-tasks.md#version-and-rerun-plan)
and packaging checks, keeping exact source/runtime/configuration identities in
`reproducibility.md` and references in journey state. No separate user-facing
planning document or audit proposal is required. Show missing access under
**Blockers** with the affected step and action; name credential variables only.

## 7. Connect the agent

Use the actual repository agent through an existing supported Gym integration.
Inspect its entry point and installed interfaces; do not substitute Gym's example
agent to claim the user's agent ran.

Preserve provider-native configuration: `environments/<name>/config.yaml`, model
settings, configured agent instance, exact selected dataset rows and order,
reset, attempts, and output paths. A Harbor JobConfig or `.eval-author/run.yaml`
is not required for native Gym. For subsets, retain complete original rows,
including verifier fields, in a selected JSONL and map physical rows back to
stable example IDs. Exclude unapproved/blocked rows and unrelated drafts.

Propose one attempt per selected example unless the agreed purpose needs repeats.
Record attempts and total rollouts separately from examples and controls. Verify
resolved composition and selected implementations, not just file presence.
Keep missing integration/configuration visible under **Blockers** with a concrete
next action; do not label model performance measured when only controls ran.

## 8. Validate the evals

After first-task approval, apply native validation to the first and remaining
tasks, reusing current checks already completed. Formal grading checks may have
been pending at first-task review. Confirm the environment kit's persistence/reset
proof passed first and is recorded in its plan separately from task controls.
From the task root, use the verified CLI:

```bash
/path/to/Gym/.venv/bin/gym env validate --manifest environments/<name>/manifest.yaml --json
/path/to/Gym/.venv/bin/gym env test <name> --json
```

Apply [Task validation and execution evidence](../../eval-author/references/task-validation.md)
and the maintained [native Gym evidence adapter](../../eval-author-task-create/references/gym-evidence.md).
Run every declared realistic incorrect, no-action, known-correct, applicable
alternative, and side-effect control; record concrete non-applicability reasons.
Use actual tools and reset for stateful checks. Retain the task revision, full
native reports, observed rewards, exceptions, and fresh output paths. A fixture
success is not actual-agent performance or proof of broad coverage.

Each evidence manifest binds one physical dataset row and the full dataset,
configuration, runtime, reset, and task revision. Preserve that binding when
selecting rows and repeats. When any shared input changes, prepare a new manifest
and rerun affected controls. Missing/malformed output is infrastructure failure,
not a negative-control success. Repair without weakening intended behavior.

Resolve and validate the exact approved dataset selection, native composition,
model/agent, and attempts. Report failures under **Blockers**; do not silently
replace the native path, enlarge the selection, or claim configuration readiness
from passing verifier fixtures alone.

## 9. Evaluate the agent

Use the parent's deterministic checkpoints and explicit bounded execution/spend
authorization. Follow [Native startup and execution](../../eval-author-task-create/references/gym-tasks.md#validate-run-and-retain-evidence)
with the actual agent, selected rows, and agreed attempts. Its two successful
attempts and measured-gap-closure rules apply to audit task creation, not this
baseline. Check installed help and preserve the distinction between:

- Explicit-file execution against started services: `gym eval run --no-serve
  --agent <configured-instance> --input <selected.jsonl> --num-repeats <N>`.
- Managed execution using prepared splits and repeats in dataset configuration;
  do not combine unsupported `--input` or example-split flags with this mode.

Verify actual row selection, repeat count, service startup, reset, and fresh
output paths before launching. Retain every selected rollout's replies/actions,
reward, failure reason, native identity, and health evidence under fresh
`.eval-author/runs/` paths. Keep the resolved configuration, execution inputs,
version identities, and exact commands. Do not disable health checks or remove
failed rows to make a run look complete.

Recording baseline receipts is optional; when used, choose `--purpose baseline`.
Neither ATIF nor repeated successful agent attempts is required. Keep unobserved
model health explicitly unobserved. Low agent rewards are valid results; missing
rollouts or infrastructure failures remain unfinished work under **Blockers**.

Link specific saved rollout/attempt evidence and readable selected-row input,
reward, and grading details in both the final reply and report/Markdown. Preserve
actual traces when emitted; do not promise an ATIF trajectory that does not exist.
Return to stage 10 for reconciliation and completion. Save full-suite and needed
single-task/row reruns preserving composition, model, verifier metadata, row
order, attempts, reset, and fresh paths. Verify the task/suite README section
anchor and link **Run instructions** directly to it. Do not label native fixtures
Harbor NOP/Oracle jobs or substitute those commands for native agent execution.
