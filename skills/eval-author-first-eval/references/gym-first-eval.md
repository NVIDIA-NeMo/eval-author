<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Native NeMo Gym first evals

Use this reference for native NeMo Gym authoring, validation, and execution.
The [parent skill](../SKILL.md) and [conversation contract](../../eval-author/references/conversations/first-eval.md)
own design approval, first-task and suite review, execution authorization, and handoff.
A selected Gym–Harbor setup uses its verified configured bridge; this native
path is not a substitute. Keep missing integration as an execution blocker
without adding a production adapter. No audit gap or coverage closure applies.

## 5. Prepare tasks and grading

Use the NeMo Gym Python/CLI pair selected by the
[shared runtime locator](../../eval-author/references/runtime-prerequisites.md#runtime-prerequisite-checks)
with `--provider gym --flow authoring`. Its central policy owns version and
Python requirements; keep NeMo Gym in its separate environment. Inspect its CLI
help and native scaffolder before use. Create only the
approved first task directly in `.eval-author/tasks/<slug>/` using
NeMo Gym's `nemo_gym.environment.scaffold.scaffold_environment(kind="environment",
name=<module_name>, root=<task_path>)`. Use a valid Python module name for the
NeMo Gym environment. Do not call the audit-driven `task_pipeline.py scaffold` route:
first evaluations require no coverage report or uncovered-tool selector.
Do not create remaining task directories or rows before first-task review approval.
One reviewed task may map to several explicitly agreed complete dataset rows;
retain verifier metadata and count distinct examples separately from repeats.

Follow [Complete the task without changing its intended capability](../../eval-author-task-create/references/gym-tasks.md#complete-the-task-without-changing-its-intended-capability)
for the generated manifest, component configuration, resources server, dataset
rows, and verifier fixtures. Replace template tools, rewards, and fixtures with
the agreed cases. Seed the resources server from the environment kit's generated
data and pass the kit's persistence and reset cases before writing verifier
fixtures, then derive expected rewards and end states with an independent
reference over that data. Map stable task/example IDs to selected
dataset rows; keep expected answers and provenance outside model inputs. Start
with exactly the approved first task and wait for its review before extending
the pattern to the agreed breadth.

Document each case's reward contract and expected positive and negative control
results before testing. Include a known-correct response or interaction and a
no-action case; when inaction is correct, also include an explicitly incorrect
response or action. Explain the grader's assertion and a limitation to the user.
Maintain each task README and the authored suite guidance in `.eval-author/README.md`.
Use [Report generation](../../eval-author/references/suite-readme.md#report-generation)
for the shared HTML report and marked README section after authoring and runs;
preserve existing documentation outside the generated markers.

## 6. Prepare the execution environment

Prepare the generated resources, agent, and model components, dependency inputs,
fixtures, and state reset. Build the backends the resources server wraps, their
seeded session state, and its reset from the environment kit that
[`eval-author-environment`](../../eval-author-environment/SKILL.md) built and
proved before task grading, following its [NeMo Gym environments](../../eval-author-environment/references/gym.md)
reference, and add each case's own records to its dataset row or session seed.
Apply the [NeMo Gym version and rerun plan](../../eval-author-task-create/references/gym-tasks.md#version-and-rerun-plan)
to the selected suite, recording it in the selected journey’s `scopes` entry in `.eval-author/.working/journey-state.json` and the drafts'
`reproducibility.md`; no dataset recommendation or audit proposal is required.
Use the component packaging checks linked there for the selected runtime.
Verify actual application access and component setup. Record missing credential
variable names and blocked work without recording secret values. Defer run-only
settings and blockers until execution is ready while independent authoring proceeds.

## 7. Connect the agent

Use the actual repository agent through a supported NeMo Gym integration. Inspect
its entry point and the installed NeMo Gym interfaces; do not substitute NeMo Gym's example
agent to claim the user's agent ran. Record the resolved composition, configured
agent instance, model settings, selected dataset rows, reset procedure, and
output paths. If integration is missing when execution is ready, use the
[missing-integration handoff](../../eval-author/references/conversations/first-eval.md#missing-actual-agent-integration-handoff),
retaining task and verifier work. Grader setup remains a separate dependency;
verifier controls cannot prove the user's agent is connected.

Apply the shared [trajectory-saving check-in](../../eval-author/references/conversations/first-eval.md#save-evaluation-trajectories)
to the actual composition before other execution settings or a missing-integration
handoff. Reuse its existing native capture or enable supported
optional capture when selected. `observability_enabled` only captures model calls
that pass through the rollout-prefixed Gym Model Server; direct-provider calls
can bypass it. Retain complete Responses input/history and outputs in native
rollout JSONL, including any `ng_trajectory` attachment, rather than only an
aggregate export. An attachment alone is not audit-ready ATIF. Inspect supported
startup and capture/export settings; keep baseline rollouts, results, and receipts
under the repository-local native run root. Preserve a supported separate
trajectory destination in the configuration and standalone rerun commands,
following the shared procedure when native capture cannot be separated from that
root. Keep the configured Gym–Harbor
bridge and its original ATIF artifacts when that path is selected.

Propose one attempt per selected example for the initial baseline unless the
agreed purpose requires repeats. Record the count and cost in shared scope. A Harbor
JobConfig or `.eval-author/run.yaml` is not required for NeMo Gym.

## 8. Validate the evals

Confirm the environment kit's proof passed first; a proof failure is an
environment defect to fix, not a task or agent result. Its persistence and reset
verifier cases run with the commands below and are recorded in the kit's plan,
not declared as task controls.

From each draft root, use the verified NeMo Gym CLI:

```bash
/path/to/Gym/.venv/bin/gym env validate --manifest environments/<name>/manifest.yaml --json
/path/to/Gym/.venv/bin/gym env test <name> --json
```

Apply [Task validation and execution evidence](../../eval-author/references/task-validation.md)
to retain the task revision and native control results. Include the declared
incorrect, valid-alternative, and side-effect cases, with explicit reasons for
non-applicability. Record the verifier controls with the shared recorder and the
maintained [native NeMo Gym adapter](../../eval-author-task-create/references/gym-evidence.md);
this does not require an audit report or agent baselines.
Retain validation and verifier-control output under fresh run paths. Require the
controls to complete without exceptions and meet their predeclared reward
criteria, exercising real tools and reset behavior for stateful cases. Repair
broken cases without weakening assertions. Report blocked cases separately.
NeMo Gym has no Harbor `-a nop` or `-a oracle` requirement. Passing verifier fixtures
does not establish actual agent performance or broad coverage.

## 9. Evaluate the agent

Follow the native service startup and execution instructions in
[Validate, run, and retain evidence](../../eval-author-task-create/references/gym-tasks.md#validate-run-and-retain-evidence),
using the actual agent, selected rows, and agreed attempts. Its two recorded
attempts and measured-gap-closure requirements belong to audit task creation;
first-eval uses the agreed baseline count and does not require coverage measurement.
Check installed help before using options. Preserve the distinction between
explicit-file execution against running services (`--no-serve --input`) and
managed execution using prepared splits.

Run only with the established execution and spend authorization. Retain fresh
outputs, rewards, errors, resolved settings, and exact commands for every selected
task under the repository-local run directory (default `.eval-author/runs/`). Keep
exact native run and attempt identities and readable selected-row evidence in the
suite README, outside finalized task
directories, using
paths resolved from the documented working directory. A working suite requires
native execution of the actual agent and recorded rewards, even when scores are
low.

Verify already-saved interaction evidence offline, matching each selected
actual-agent attempt's native rollout/task/repeat identities. Prefer an explicitly
identified original ATIF for a bridged run and validate it read-only with Harbor's
audit parser; do not copy it merely to verify usability.

Check the saved `execution_settings.trajectory_capture` before creating conversion
artifacts. When capture is enabled (`mode: automatic`, or `mode: optional` with
`choice: true`), use the existing [`gym-to-atif` converter](../../gym-to-atif/SKILL.md),
selecting the attempt's one-based physical JSONL row and following its existing
source-selection rules for projections. Preserve original native outputs and keep
its source, converted ATIF, and receipt under fresh per-attempt paths in the saved
supported trajectory destination, including conversion losses or uncertainties,
and validate the ATIF with Harbor's audit parser.
When optional saving is declined or pending, capture is unsupported, or
the setting is absent, do not invoke the converter or create optional persisted
trajectory copies. Retain required native results and inspect any already-saved
original ATIF read-only as above; otherwise explain that conversion was not
performed and audit usability remains unverified.

Missing history, unsupported content, and failed conversions remain limitations
in the existing run review; do not rerun the agent or replace its baseline result
to repair capture.
If the converter or parser is unavailable, report that usability remains unverified.
Return to the parent skill for reconciliation and linked rerun instructions. Report NeMo Gym
control results in their own terms, without labeling them Harbor NOP/Oracle jobs.
