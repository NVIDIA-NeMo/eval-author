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
For a new design without a saved source choice, the confirmation message asks
which source to use directly, without an extra readiness question.
If a new request finds a saved task design, Eval Author shows its scope and
progress and asks whether to continue it, revise it, or start a new proposal.
Confirming `ETHOS.md` alone does not resume that design. Earlier artifacts stay saved.

## Review the design and first task

Source selection and the task proposal show your overall progress, with setup
and `ETHOS.md` complete and evaluation creation marked as the current step.
The task proposal explains the inputs, intended behaviors, expected results,
verifiers, and remaining coverage gaps. You can change the proposal or approve
it and the first task to build. Task labels use stable IDs throughout the conversation so
you can match them to the report and task directories. Expected results are
[brief summaries with detailed criteria in each task README](results.md#review-generated-cases).
The default starting proposal has one to three tasks, with one example each;
you can request broader scope. Eval Author waits for your source choice and
then your proposal approval before starting task construction.

After design approval, Eval Author checks what is needed to validate the verifiers
before creating any task. If a model verifier needs a judge choice, access, or
authorization, it asks for those inputs and the validation limits up front.
Existing applicable settings and authorization are reused without another
approval pause. It then builds one task, proves its environment, and validates
its verifier before presenting its README and report for review. Compact
build checklists show completed, active, pending, and blocked steps, with the
responsible skill beside each item. The review separates environment proof and
successful verifier validation from whether your actual agent has run.
You can request revisions to that task before the remaining tasks are created.

If a verifier uses an AI model to judge responses, validation exercises that judge
with known-correct and relevant incorrect results, including valid alternatives
where permitted. Missing judge inputs appear as focused questions before task
creation. Unavailable tools or failing access appear under **Blockers**, with the
action needed to continue. If access later stops working, completed drafts stay
saved while the affected work pauses. The model and budget questions for running
your agent still follow suite approval.
On resume, a fully authored and validated suite can refresh its report or return
to review without repeating judge setup for checks that are already complete.

Task directories remain under `.eval-author/tasks/<task-slug>/`. The shared
report is `.eval-author/report.html`, with a Markdown counterpart in
`.eval-author/README.md`. Both link the task documentation and available evidence.

## Run and inspect the suite

After you approve the first task, Eval Author builds and validates the remainder.
Its checklist shows task counts and progress through environment checks, verifier
validation, and the README/report refresh.
You then review the whole authored suite, with more detail in the HTML report
or mirrored README. Approve the tasks or request changes. Every task's required
verifier checks, including model-based checks when used, must pass before this
review.
After suite approval, Eval Author separately explains trajectory saving for a
future audit against `ETHOS.md`, including known storage/cost effects and limits.
When saving is optional, you choose whether to save and can select a local directory;
automatic capture needs no additional consent. Defaults are fresh directories
under `.eval-author/jobs/` for Harbor and `.eval-author/runs/` for NeMo Gym.
Alternative trajectory locations depend on integration support; Eval Author
explains any limitation, and baseline results stay in the repository. Your choice
carries forward on resume and authorized reruns, including README commands;
a new suite gets its own choice.
Changing that choice or directory preserves completed results. Future runs use
the updated settings and need applicable execution authorization.

Eval Author then presents concrete run settings, attempts, limits, and unresolved
needs, with a brief reminder of your saving choice and directory. Design, task,
or saving approval does not authorize paid runs; applicable prior authorization
is reused. Repeating an input adds attempts rather than new examples.
During execution, a standard checklist shows completed attempts, observed run
status, and execution errors.

A supported runtime is required for full onboarding and authoring; see
[runtime requirements](getting-started.md#requirements). Running your agent also
requires a compatible connection, access, and the selected environment backend.
A combined Gym–Harbor setup needs a configured bridge. If the actual-agent
adapter or bridge is missing, Eval Author gives you a copyable implementation
prompt for a separate chat in your chosen coding harness. This can happen before
suite approval so integration work can proceed alongside task authoring.
The prompt includes the saved capture settings when available. Otherwise, saving
stays disabled until you answer the later saving check-in. Your tasks, graders,
and evidence stay saved; independent work can continue. Return with the
integration changes and check results so Eval Author can recheck the connection.
Completing that engineering work does not authorize agent runs. Other blocked
work is explained with the action needed to continue.

The result summary separates measured agent failures from execution failures
and missing attempts. It links an exact saved attempt and the suite README's
rerun instructions, so you can inspect or repeat the same selection. Low agent
scores can still provide a valid baseline; successful task checks alone do not
measure your agent's performance. See [Read your results](results.md).

The suite README links saved trajectories alongside reports and rerun instructions.
Eval Author checks audit usability and explains missing or unusable evidence;
declining or unsupported saving limits future audits, not valid baseline results
or task creation. Scores cannot replace trajectories. Saving consent does not
start an audit or trigger extra runs to fill missing evidence.

When the agreed suite is complete, Eval Author asks whether you want to audit
its coverage and verifier design against `ETHOS.md`. The audit starts only if you accept.
Missing audit evidence does not undo the completed suite. You can decline and
keep all artifacts, or later [request an audit](existing-evals.md#audit-coverage).

To refresh the saved report, ask:

```text
Refresh the evaluation report in .eval-author.
```

The [first-evaluation skill](../skills/eval-author-first-eval/SKILL.md) defines the
agent workflow and its [conversation contract](../skills/eval-author/references/conversations/first-eval.md).
