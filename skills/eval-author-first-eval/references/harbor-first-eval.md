<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Harbor first evals

Use this reference for Harbor-specific work in first-eval stages 5–9. Preserve
the parent skill's conversational approval gates, state, and selected setup.
No audit report or measured gap closure is required. A configured NeMo Gym–Harbor
integration uses its verified supported commands and retained native evidence;
conversion alone does not establish that the bridge executed. If the integration
is missing, retain that finding and report **Blockers** with the execution
settings when concrete runs or paid checks are ready, naming the cause, blocked
step, and setup action. Keep run-only blockers out of the initial proposal and
first-task authoring checkpoints. If a finding prevents
a meaningful task design, use the blocker checkpoint instead of a proposal.
Do not add an adapter or silently replace the selected execution path.

## 5. Prepare tasks and grading

After design approval, proceed directly to first-task environment preparation
and authoring. Ask only about unresolved choices needed for current work and
show blockers that prevent it. Defer run-only settings and blockers; the six
execution bullets are not required to build or review the first task. After its
review approval, display them when concrete agent runs or paid grading are ready,
reusing applicable previously displayed settings without another generic gate.

Use the verified Harbor CLI and Python environment. Read `harbor task init --help`
and `harbor trial start --help` before using options. Create only the approved
**first task**, at an unused descriptive `.eval-author/tasks/<slug>/` path:

```bash
harbor task init <org>/<slug> --tasks-dir .eval-author/tasks \
  --description "<behavior being tested>" --author "<actual author>"
```

Verify the resulting path. Build its `environment/` from the kit proven through
`eval-author-environment` before authoring the grader or reference solution:
copy the kit's build files and generated data, then layer the task's records.
The task must build on its own rather than `FROM` a local kit image. Derive
expected values with an independent reference query over the generated data.

Complete `instruction.md`, `task.toml`, `tests/test.sh`, and `solution/solve.sh`
using the installed schema, executable permissions, realistic timeouts, and
deterministic rewards. Remove all scaffold placeholders. Explain the task's
[reference solution](https://docs.harborframework.com/tasks/solution) and
[grader](https://docs.harborframework.com/tasks/verifier), linking both at their
introduction. Oracle executes the prepared reference; it is not the actual agent.

Write the actual task `README.md` following [Task documentation](../../eval-author/references/suite-readme.md#task-documentation).
Describe the input, `ETHOS.md` behavior, expected result, environment, reference,
grader, limitations, controls, and setup/rerun instructions. Before controls,
document reward names/ranges and expected outcomes: for binary action completion,
NOP should score 0 and Oracle 1; named or graded rewards use their declared
thresholds. Correct inaction requires a separate explicitly incorrect action.

Record whether formal grading controls have run; they may remain pending at this
review. Proven environment and the actual task files are required. Refresh [the report and Markdown counterpart](../../eval-author/references/suite-readme.md#report-generation),
and link them plus the actual task README for the first-task review. Follow the
parent's deterministic review message and save the approved revision before
scaffolding any remaining task. Requested changes revise this directory in place,
retain older evidence, invalidate affected checks, refresh review outputs, and
require review again. Rerun affected controls when applicable.
A blocked first task must not cause other tasks to be built ahead of approval.
After approval, build the remaining tasks and apply stage 8 to all tasks. Reuse
current checks and existing bounded execution authorization; do not add a separate
human approval gate for validation or for every remaining task.

## 6. Prepare the execution environment

Confirm every task's `environment/` builds from the kit that
[`eval-author-environment`](../../eval-author-environment/SKILL.md) built and
proved, following its [Harbor reference](../../eval-author-environment/references/harbor.md).
Rerun smoke proof after changes. Keep solutions and verifier-only data outside the
agent's initial environment. For Docker execution, check `docker info` first.
Verify backend access, application access, credential variable names, isolation,
and reset. Show unavailable access under **Blockers**, naming the affected step
and action needed; finding configuration requirements does not prove access.

## 7. Connect the agent

Establish a supported integration for the actual repository agent from its entry
point, installed adapter code, registry, and CLI help. Missing integration blocks
actual-agent evaluation; available NOP/Oracle controls remain task checks. Do not
substitute a built-in agent or a reference solution for the requested agent.

Write `.eval-author/run.yaml` using the installed Harbor JobConfig schema.
Explicit `tasks` entries must select exactly the approved, validated tasks,
with actual agent/model settings, attempts, task-specific settings, and jobs
under `.eval-author/jobs/`. Do not select the whole tasks directory or include
unrelated drafts. Save and resolve paths from the repository root. Retain reset,
request delivery, conversation state, and output/action collection requirements.
Reference credential environment variables rather than secret values.

Use `run.yaml` for newly authored suites. If a legacy `first-eval.yaml` already
exists, inspect it, preserve its settings and evidence references, and migrate
the intended selection to `run.yaml` without overwriting unrelated user work.
Configuration presence does not prove a working agent connection.

## 8. Validate the evals

After first-task approval, apply native structural checks and controls to all
agreed tasks, reusing any current checks completed earlier. Formal grading checks
may have been pending at first-task review. Confirm the kit's
smoke proof passed first; NOP and Oracle never stand in for environment proof.
Before the first control, remove any locally built kit image
(`docker image rm <kit-tag>`), so each task proves it builds from its own
`environment/`. Run controls as individual trials, retaining fresh output paths:

```bash
harbor trial start -p .eval-author/tasks/<slug> -a nop \
  --trials-dir "$PWD/.eval-author/jobs/<slug>-controls" --trial-name nop-1
harbor trial start -p .eval-author/tasks/<slug> -a oracle \
  --trials-dir "$PWD/.eval-author/jobs/<slug>-controls" --trial-name oracle-1
```

Use fresh names on reruns. Record each command through the shared evidence
recorder with that trial's `result.json` as `--result`. Run every other declared
control too, including a realistic incorrect result distinct from NOP, valid
alternatives and preservation/side-effect checks where applicable. Inspect native
exceptions and reward shape as well as values. A broken environment is not a
successful rejection. Keep failed and superseded evidence when repairing tasks.

Follow `eval-author-discover` to validate the selected config and inspect its
resolved task set; report that config's verdict if others exist. Keep unresolved
integration/config checks separate from successful controls under **Blockers**.
Do not claim runnable status from YAML presence or controls alone. Save the exact
validated selection and revision through the parent's state contract.

## 9. Evaluate the agent

Confirm resolved tasks, actual agent/model, attempts, and execution bounds. Use
the parent's required deterministic checkpoint messages, and run only under
explicit execution/spend authorization covering this selection. From the
repository root, use the validated config and a fresh job name/output path:

```bash
harbor job start -c .eval-author/run.yaml \
  --jobs-dir .eval-author/jobs --job-name "suite-$(date -u +%Y%m%dT%H%M%S)-$$"
```

For a selected supported Gym–Harbor integration, use its verified equivalent
and retain evidence of that integration actually executing. A direct Harbor run
or converted directory cannot replace it. Missing bridge support is a blocker.

Inspect every selected task's native job/trial results, including scores and
exceptions. Low actual-agent scores do not invalidate a working suite. Label
controls separately and link the actual job, named task/trial, recorded reward,
grading/log evidence, and available trajectory. Missing ATIF remains an explicit
limitation, not invented evidence. Refresh the report and Markdown with these
same informative references and limitations.

Return to stage 10 for reconciliation and the final handoff. Save full-suite and
needed single-task rerun commands preserving agent/model, task overrides, and
attempts. Verify the actual README section anchor and link **Run instructions**
to it. Commands must use fresh output paths and resolve exactly the intended
tasks. Supply the verified viewer launch/open instructions and an actual saved
job/trial to inspect; distinguish a planned localhost URL from a running viewer.
