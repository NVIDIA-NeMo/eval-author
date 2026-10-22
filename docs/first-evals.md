<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Build your first evaluations

Use this guide when your agent has no evaluations yet. Eval Author helps you
build evaluation tasks, review the first task before creating the rest, and run
the agreed suite against your agent. Each task is a request with criteria for
scoring the response or resulting actions. The suite gives you a baseline you
can rerun after changes.

Follow [the setup guide](getting-started.md) with your agent's repository open,
then ask:

```text
Help me build my first evaluations for this agent. I do not have evals yet.
Show me the proposed tasks and explain how they will be graded.
```

Eval Author follows your choice of Harbor or native NeMo Gym, preserves an
existing suite's provider when you have not chosen another, and defaults to
Harbor otherwise. A combined NeMo Gym–Harbor setup needs a configured supported
integration. Both tools being installed does not establish that integration.

## Agree on what to test

Shared setup confirms how your agent runs and the selected evaluation runtime.
You then review `ETHOS.md`, the local document describing the agent's purpose,
boundaries, and expected behavior. Eval Author can reuse an existing document
or help create it. Current confirmations carry into task authoring.

Traces and example conversations are optional. You can select a source and
bounded subset, or proceed from `ETHOS.md` with synthetic examples. Eval Author
asks before searching or reading traces, preserves your exclusions, and reuses
an existing choice. You do not need a prior suite, audit, or coverage report.

The task proposal appears directly in the conversation:

| Task | Input | Behavior under test | Expected result | Grading |
| --- | --- | --- | --- | --- |

Its rows describe concrete requests and starting conditions, intended outcomes,
and how the grader distinguishes correct and incorrect behavior. You review
the tasks and their breadth, including useful negative or difficult examples
and what remains untested. Labels come from the intended meaning in `ETHOS.md`;
an existing model score alone does not establish the correct answer.

The proposal explains the suite's purpose and limits, identifies the first task
to build, and asks for your feedback or design approval. There is no separate
planning document to review.

After design approval, Eval Author starts preparing the first task. It asks only
about unresolved environment or design choices needed for that work; run-only
settings and blockers wait until execution is ready.

## Review the first task

Eval Author builds one task in `.eval-author/tasks/<slug>/` before building the
remainder. It prepares and proves the task's environment first, using realistic
seeded data and checking observable state and reset. Then it writes the request,
grader, reference solution or known-correct interaction, and task README.
The environment's technical plan and native run evidence are saved locally.

For Harbor, the [reference solution](https://docs.harborframework.com/tasks/solution)
demonstrates a correct implementation, and the
[grader](https://docs.harborframework.com/tasks/verifier) checks the requested
outcome. Native NeMo Gym uses its own verifier fixtures and known-correct
interactions. Neither substitutes for running your actual agent.

Task validation checks include a known-correct result, no action, a realistic
incorrect result, and applicable valid alternatives and state preservation.
Harbor uses NOP and Oracle for its no-op and reference controls. Native Gym uses
`gym env validate` and `gym env test` with native fixtures. When the task requires
an action, doing nothing earns no completion credit. Infrastructure failures
remain distinct from a grader correctly rejecting bad behavior.

You receive the first task's actual README, `.eval-author/report.html`, and the
Markdown counterpart `.eval-author/README.md`. The review explains its input,
expected result, grading, reference, available controls, and limitations, with
links to the files and evidence. The environment must already be proven, while
formal grading controls may still be pending. Those statuses are stated
separately, including whether the actual agent has run.

You approve the first task before Eval Author builds the remaining tasks. If
you request changes, it updates the same task, retains prior evidence, reruns
affected checks, refreshes the review, and asks you to review that revision.
Approval applies to the reviewed task; it does not carry across material changes.
The same sequence applies to native Gym, where the first task's selected rows
are explicit and do not silently include every row in a draft.

## Run the agreed suite

After you approve the first built task, Eval Author presents these six details
when concrete agent runs or paid grading are ready:

- **Agent/model:** which actual agent and model configuration would run.
- **Tasks and examples:** task count, distinct inputs, their mapping, and the
  reviewed first task and remaining agreed tasks.
- **Attempts:** how many times each task or selected Gym row would run.
- **Validation:** environment checks and task controls, separate from measuring
  the agent's performance.
- **Time/cost limits:** execution bounds, retry/time limits, and supported cost
  estimates; monetary cost stays unknown when reliable evidence is unavailable.
- **Open questions:** choices or missing access that still need to be resolved.

That message also shows any **Blockers** affecting those runs or paid checks.
The settings are never a prerequisite to building or reviewing the first task.
Displaying them does not add another approval checkpoint, and current previously
displayed settings are reused.

Repeating a task does not add a new input example. Cost of running the suite is
also different from testing whether the agent itself operates efficiently.
You can prioritize lower rerun cost, wider coverage, harder examples, latency,
or another concern, and revise the agreed scope in the conversation.

Design approval and displayed settings do not implicitly authorize paid execution.
Eval Author asks for explicit authorization when the selected agent, tasks,
attempts, validation, and execution limits define a concrete bounded run. Existing
applicable authorization carries forward without being requested again.

After first-task approval, Eval Author builds the remaining agreed tasks and
validates all tasks, reusing current checks. It does not add individual human
review gates for every remaining task or ask again about validation already
covered by explicit bounded authorization. It saves the exact selected tasks or dataset rows, actual agent/model,
attempts, reset requirements, and output paths, then checks that configuration.
Unrelated drafts and blocked tasks are excluded from runnable selections and
remain visible as unfinished work.

Harbor suites use `.eval-author/run.yaml` and save results under
`.eval-author/jobs/`. Native Gym preserves its native component configuration
and selected JSONL rows, and saves results under `.eval-author/runs/`. A native
Gym suite does not need a Harbor job configuration. A selected Gym–Harbor
integration must execute through that supported integration.

Under the agreed execution authorization, Eval Author runs the actual agent and
inspects each selected task, example, and attempt. A completed run can have low
scores: that is a useful baseline, not a reason to weaken the grader. Task-control
results stay separate from actual-agent results.

If anything prevents a required step, the conversation shows **Blockers** with
the cause, the blocked step, and the action needed to continue. Completed work
is retained. Missing integrations, required review artifacts, or actual-agent
runs do not become a completed journey. The flow resumes from the unfinished
step after a detour or interruption.

## Read results and run again

Start with `.eval-author/report.html` or its Markdown counterpart to review tasks
and results. Each task links to its README, input, expected result, grading, and
available evidence. Results distinguish controls from agent attempts and include
specific saved jobs/trials or Gym rollouts, scores, errors, and limitations.
Missing traces stay explicit; captured replies do not imply ATIF was produced.

The final reply explains what the tasks measured and how the agent performed,
and links a named task/attempt for inspection. For Harbor, it explains how to
launch the installed viewer and open that saved evidence. For native Gym, it
links readable selected-row evidence. A viewer URL is only described as live
when it has been checked.

A **Run instructions** link opens a verified section of the saved README. That
section includes setup, exact full-suite and needed single-task commands, state
reset, preserved agent/model settings, and fresh output paths. You can follow it
without reconstructing the conversation. To refresh the report, ask:

```text
Refresh the evaluation report in .eval-author.
```

The final reconciliation distinguishes agreed, generated, validated, and executed
examples and names omissions or limits. A few passing tasks do not establish
broad coverage or guarantee that the grader handles every valid response.

After completing the suite and its handoff, Eval Author offers an optional audit
of coverage and grading against `ETHOS.md`. It starts that audit only if you
accept, reuses the current confirmations and saved suite evidence, and does not
repeat the offer after a decline. Missing traces for the audit do not undo a
completed first-evaluation baseline. See [Audit coverage](existing-evals.md#audit-coverage).

To expand later, ask to retain the current tasks and review additional behaviors,
examples, difficulty, and rerun cost before generating them. See the
[first-eval workflow](../skills/eval-author-first-eval/SKILL.md) for the complete
implementation and [runtime requirements](getting-started.md#requirements) for
setup. A missing selected runtime pauses full onboarding before `ETHOS.md` and
task design; an explicit planning-only request can proceed without it.
