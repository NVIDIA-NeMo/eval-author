<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Build your first evaluations

Use this guide when your agent has no evaluations yet. Eval Author helps you
build a starter suite that you can rerun after changes using NeMo Gym or Harbor.
As in audit-derived and trace-derived task creation, Eval Author follows your
choice, otherwise preserves the existing suite's provider, and defaults to
Harbor when neither is specified. Gym is compatible with Harbor, and you have
the final say in which tools to use.

Follow [the setup guide](getting-started.md) to load Eval Author in your coding
agent, with the repository you want to evaluate open. Then ask:

```text
Help me build my first evaluations for this agent. I do not have evals yet.
Show me the plan first, and explain the cases and how they will be graded.
```

## Establish what the agent should do

Eval Author first helps you establish an **Ethos**: a local document describing
your agent's purpose, boundaries, and success criteria. It can reuse an existing
`ethos.md` or help you create and review one.

If you have traces, Eval Author confirms which source and subset to use before
searching or reading them. That selection and any exclusions carry into case
creation and later expansion. You can also choose to plan from Ethos without
traces; traces are not a prerequisite.

Before generating cases, you review the priorities and breadth for each behavior:
how many distinct input examples to include, their variations and difficulty,
positive and negative scenarios, expected outcomes, grading, and exclusions.
A **pilot** is a limited first set of examples to check the test setup and get an
initial measure of performance on selected behaviors. Eval Author explains why
it recommends that breadth, what you would learn, and what would remain untested
even if every example passed. You can choose a minimal pilot or broader coverage;
there is no fixed example quota. Difficult sentiment or complaint examples need
labels justified by the intended meaning; a classifier score alone does not
establish the answer.

At the plan review, Eval Author explicitly asks what you want to change or
prioritize: lower rerun cost, measuring the agent's cost or latency, broader
coverage, harder cases, or another concern. It carries forward preferences you
already gave. You can revise the
proposal or accept it as written; your choices are saved before case generation.

Distinct examples, native tasks, and repeated attempts are counted separately.
A task packages one or more examples with their environment and grading;
repeating it does not add new examples. Discuss cost before generation, including
the work each rerun requires and whether cost is a budget, a reported metric,
or a scoring rule. Without reliable pricing or comparable measurements, the plan
uses work counts and marks monetary cost unknown.

You can plan cases before either runtime is installed. Native Gym authoring
requires Gym v0.6.0+ in its separate Python 3.13.14+ environment.
For Harbor authoring, see the [Harbor setup guide](../skills/eval-author-discover/references/harbor-setup.md).
Execution also needs the selected environment backend, any required application
access, and a supported connection to your agent. See the full
[runtime requirements](getting-started.md#requirements).

The checklist shows the current stage and unfinished work at each checkpoint.
If you detour to discuss grading, cost, or setup, Eval Author preserves the agreed
scope and resumes from that stage without restarting onboarding.

## Check the cases and evaluate the agent

Eval Author creates each case's instructions, environment, reference solution,
and grader. The environment reuses your repository's own services where it can,
starts from realistic seeded data, and is proven to work before any case relies
on it. For Gym, it validates the manifest and runs verifier fixtures with
known-correct and negative/no-action controls through `gym env validate` and
`gym env test`. For Harbor:

- **NOP** runs a no-op baseline to check what happens when no work is done.
- **Oracle** runs the prepared reference solution to check that the grader
  recognizes the expected outcome.

A realistic incorrect result must also be rejected. Where relevant, Eval Author
checks a different valid solution and the actual state changes requested by the
task. Instructions read like your own request, without the root cause, the fix,
or the expected results; data a task must leave unchanged is checked by content
rather than by row counts; and when a task requires an action, doing nothing
earns no credit. It retains the task revision and run evidence, so editing a
case requires fresh validation. Missing evidence and setup errors stay separate
from behavior failures.

These checks test the cases themselves. The agent's performance is measured by
running your actual agent with its configured model and credentials. Eval Author
reports that run separately, including scores and execution errors.

A working suite can reveal that your agent performs poorly. Its value is giving
you a repeatable baseline. A few passing cases do not establish broad coverage
or prove that the grader handles every possible response.

## Read the output and run again

The plan and run summary are saved in `.eval-author/first-eval.md`. Generated
tasks live under `.eval-author/task-drafts/`, and run artifacts under
`.eval-author/runs/` for Gym or `.eval-author/jobs/` for Harbor. Gym keeps the
selected composition, dataset rows, agent/model settings, and native commands
in the plan and suite README. For Harbor, when the agent integration supports it,
Eval Author creates `.eval-author/first-eval.yaml` with the selected cases and run settings.

Start with `.eval-author/README.md` when you return to run evaluations. It saves
the required setup, a full-suite command, and a mapping from coverage items to
cases with commands for running them individually. It also explains where to
find rewards, errors, and logs. These commands preserve the saved agent settings
and use fresh output paths. Task sanity checks and actual agent evaluations are
labeled separately.

The summary compares agreed and delivered examples for each behavior, separating
what was generated, validated, and actually run. It links the examples to their
tasks and names missing variations, substitutions, or blocked work. Passing a
pilot does not mean the broader planned coverage is complete.

If a dependency or agent connection is unavailable, the summary identifies the
blocked work and what is needed to continue. See [Read your results](results.md)
for the meaning of the saved outputs.

To extend the suite, ask:

```text
Show me how to rerun this suite, inspect one result, and plan broader coverage.
Update its rerun README when adding the agreed cases.
```

For expansion, review the added examples, difficulty, expected outcomes, and
rerun cost before generation. Existing cases and the selected corpus carry
forward; the updated plan distinguishes retained examples from new additions.

Once you have run evidence, [audit coverage and propose new evaluations](existing-evals.md#audit-coverage).
The [first-eval skill](../skills/eval-author-first-eval/SKILL.md) documents the
complete workflow.
