<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Build your first evaluations

Use Eval Author with an agent that has no evaluation suite, or expand a starter
suite you already created. Follow [the setup guide](getting-started.md), open
your agent's repository, and ask:

```text
Use eval-author to build my first evaluations for [agent name] using [NeMo Gym or Harbor].
I don't have evaluations yet.
```

Eval Author confirms your setup and `ETHOS.md`, the local document describing
how the agent should behave. Existing traces are optional: you can choose
specific runs or start from `ETHOS.md` with generated input examples. Your
source choices and exclusions carry forward when you resume.
If a new request finds a saved task design, Eval Author shows its scope and
progress and asks whether to continue it, revise it, or start a new proposal.
Confirming `ETHOS.md` alone does not resume that design. Earlier artifacts stay saved.

## Review the design and first task

You first receive a task proposal explaining the inputs, intended behaviors,
expected results, grading, and remaining coverage gaps. You can change the
proposal or approve it and the first task to build.
The default starting proposal has one to three tasks, with one example each;
you can request broader scope. Eval Author waits for your source choice and
then your proposal approval before starting task construction.

After design approval, Eval Author builds one task and presents its README and
report for review. A compact build checklist shows completed, active, pending,
and blocked steps while the task is being prepared. The review distinguishes
whether its environment works,
whether grading has been validated, and whether your actual agent has run.
You can request revisions to that task before the remaining tasks are created.

Task directories remain under `.eval-author/tasks/<task-slug>/`. The shared
report is `.eval-author/report.html`, with a Markdown counterpart in
`.eval-author/README.md`. Both link the task documentation and available evidence.

## Run and inspect the suite

After you approve the first task, Eval Author builds and validates the remainder.
When agent runs or paid grading are ready, it presents the concrete settings,
attempt counts, limits, and unresolved execution needs. Approving the design or
first task does not authorize paid runs; applicable prior authorization is reused.
Repeating an input adds attempts rather than new examples.

A supported runtime is required for full onboarding and authoring; see
[runtime requirements](getting-started.md#requirements). Running your agent also
requires a compatible connection, access, and the selected environment backend.
A combined Gym–Harbor setup needs a configured bridge. Blocked work is explained
with the action needed to continue, while completed work remains saved.

The result summary separates measured agent failures from execution failures
and missing attempts. It links an exact saved attempt and the suite README's
rerun instructions, so you can inspect or repeat the same selection. Low agent
scores can still provide a valid baseline; successful task checks alone do not
measure your agent's performance. See [Read your results](results.md).

When the agreed suite is complete, Eval Author asks whether you want to audit
its coverage and grading against `ETHOS.md`. The audit starts only if you accept;
missing audit evidence does not undo the completed suite. You can decline and
keep all artifacts, or later [request an audit](existing-evals.md#audit-coverage).

To refresh the saved report, ask:

```text
Refresh the evaluation report in .eval-author.
```

The [first-evaluation skill](../skills/eval-author-first-eval/SKILL.md) defines the
agent workflow and its [conversation contract](../skills/eval-author/references/conversations/first-eval.md).
