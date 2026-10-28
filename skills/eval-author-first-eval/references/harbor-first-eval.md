<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Harbor first evals

Use this reference for native Harbor authoring, validation, and execution.
The [parent skill](../SKILL.md) and [conversation contract](../../eval-author/references/conversations/first-eval.md)
own design approval, first-task and suite review, execution authorization, and handoff.
A configured Gym–Harbor path must execute through its verified bridge; converted
tasks run directly by Harbor do not prove that path. Keep missing integration
as an execution blocker without substituting an agent or adding a new adapter.
No audit gap or coverage-closure requirement applies.

## 5. Prepare tasks and grading

Use the verified Harbor CLI and Python environment. Read `harbor task init --help`
and `harbor trial start --help` before using options. Use an unused descriptive slug for
the approved first task directly in `.eval-author/tasks/<slug>/`. Do not
scaffold remaining tasks before first-task review approval. One task may contain
several agreed examples; preserve the agreed mapping of stable example IDs to tasks.

```bash
harbor task init <org>/<slug> --tasks-dir .eval-author/tasks \
  --description "<behavior being tested>" --author "<actual author>"
```

Verify that the expected directory was created. Build its `environment/` first,
from the proven environment kit: copy the kit's build files and its
generated data, which passed the kit's smoke task, into the task and add the
task's own records, so the task builds on its own rather than `FROM` a local
kit image. Then
complete the generated `instruction.md`, `task.toml`, `tests/test.sh`, and
`solution/solve.sh` using the installed schema, deriving expected values with an
independent reference query over that data. Set executable permissions,
realistic timeouts, and deterministic rewards; leave no scaffold placeholders.
Use Harbor’s [reference solution](https://docs.harborframework.com/tasks/solution)
and [verifier](https://docs.harborframework.com/tasks/verifier) documentation
when explaining the reference and grader. Environment proof precedes both.

Write the task README following the shared
[Task documentation](../../eval-author/references/suite-readme.md#task-documentation)
requirements. Generate and refresh the suite report following the
[Report generation](../../eval-author/references/suite-readme.md#report-generation).

Before testing, document each case's expected NOP and Oracle acceptance criteria
in its README. For a binary completion metric, NOP should score 0 and Oracle 1.
For named or graded metrics, specify the expected values or thresholds for each
relevant metric instead of imposing a universal 0/1 pair. Apply the parent skill's
explicit incorrect-behavior control when inaction itself is correct.

## 6. Prepare the execution environment

Confirm each task's `environment/` builds on the kit that
[`eval-author-environment`](../../eval-author-environment/SKILL.md) built and
proved before task grading, following its [Harbor environments](../../eval-author-environment/references/harbor.md)
reference, and rerun its smoke task if the kit changed after it passed. Check documented
access requirements, credential variable names, and reset behavior. Keep
solutions and verifier-only data outside the agent's initial environment. For
Docker-backed execution, check `docker info` first.
Verify the selected backend and required application access; record unavailable
access and the work it blocks. Show only blockers affecting current work;
defer run-only settings and blockers during independent authoring and review.

## 7. Connect the agent

Establish a supported Harbor integration for the actual repository agent from
its entry point, installed adapter code, registry, and CLI help. If the adapter
is missing when execution is ready, follow the
[missing-integration handoff](../../eval-author/references/conversations/first-eval.md#missing-actual-agent-integration-handoff).
Keep the target-agent adapter separate from grader setup: NOP/Oracle controls
can validate tasks when their own dependencies are ready, but they do not
measure the user's agent. Preserve that distinction when rechecking the adapter
after the external engineering work.

When supported, write `.eval-author/run.yaml` using the installed Harbor
JobConfig schema. Explicitly select exactly the agreed tasks, actual agent and
model settings, agreed attempts per task, and repository-local jobs under
`.eval-author/jobs/` by default.
Use the parent skill's authorized attempt count and limits; directory membership
never selects tasks. Do not include unrelated
drafts merely because they share a parent directory. Resolve paths from the
repository root and reference credential environment variables rather than
embedding secrets. Configure request delivery, conversation state, and output
collection through supported interfaces. Configuration is not proof of a working
agent connection.

Apply the shared [trajectory-saving check-in](../../eval-author/references/conversations/first-eval.md#save-evaluation-trajectories)
to the selected adapter before other execution settings or a missing-integration
handoff. Harbor has no universal capture switch: inspect the
installed adapter's supported options and output contract. If it already writes
ATIF, explain and reuse that native capture. Otherwise enable its existing
optional capture when selected; a custom action/reply JSON file is not an ATIF
trajectory. Keep an unsupported capability visible without implementing a new
adapter here. Bind the saved trajectory directory through supported capture or
export settings, keeping baseline results and receipts under the repository-local
native job root. Apply the shared procedure's limitation when the adapter cannot
separate those roots. Include the verified settings in
the saved full-suite and single-task commands so independent reruns preserve them.

## 8. Validate the evals

Use Harbor's installed validators to check task validity. Confirm the current
environment kit's smoke proof passed first; a smoke failure is an environment
defect to fix, not a task or agent result, and NOP and Oracle never stand in for
that proof. Before the first control, remove any locally built kit image
(`docker image rm <kit-tag>`), so each task proves it builds from its own
`environment/`. Exercise the environment and grading with NOP and Oracle. Run each
control as a single trial so the parent skill's evidence recorder gets fixed
result paths, retaining all trials under `.eval-author/jobs/` with fresh names
on reruns:

```bash
harbor trial start -p .eval-author/tasks/<slug> -a nop \
  --trials-dir "$PWD/.eval-author/jobs/<slug>-controls" --trial-name nop-1
harbor trial start -p .eval-author/tasks/<slug> -a oracle \
  --trials-dir "$PWD/.eval-author/jobs/<slug>-controls" --trial-name oracle-1
```

Record each as its own run, with `--result` set to that trial's `result.json`.

For each task, inspect trial results and recorded rewards. Both runs must
complete without exceptions and meet the predeclared criteria, including reward
shape. The reference solution should satisfy the intended outcome; doing nothing
should not earn completion credit when an answer or action is required. If
inaction is correct, exercise an explicit incorrect response or action as well.
Follow the parent skill's limits on validation claims, repairs, and blocked cases.

When the agent config is available, follow `eval-author-discover` to validate it
and report its per-config verdict if other configs exist. Keep unfinished config
validation visible separately from successful control runs. Do not claim a
config is runnable from YAML presence.

## 9. Evaluate the agent

Confirm the resolved task set, actual agent, model, and agreed attempts using the
parent skill's execution checks. With established execution and spend
authorization, run from the repository root:

```bash
harbor job start -c .eval-author/run.yaml
```

Retain outputs, actions, rewards, exceptions, and exact commands for every
selected task under fresh job paths. Low agent scores do not invalidate a working
suite. For capture, inspect the actual `agent/trajectory.json` or adapter-declared
ATIF path for each selected native trial, associated with that trial's retained result
and task identity. Validate the saved bytes with the installed Harbor
`harbor.models.trajectories.Trajectory.model_validate_json` parser used by audit
measurement; file presence or a viewer opening it is insufficient. Preserve the
original and describe unavailable or invalid capture in the existing run review.
If the parser is unavailable, report that usability remains unverified.
Return to the parent skill for exact-run review, reconciliation, and
linked full-suite and single-task rerun instructions. Label NOP/Oracle commands as task validation and the actual
agent command as evaluation; use new trial and job names so reruns preserve prior evidence.
