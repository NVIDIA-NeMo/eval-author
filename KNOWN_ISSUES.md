<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Known issues

Eval Author is a research preview. Review generated cases, grading, and run
evidence before relying on results. This page describes current limitations
and practical ways to work with them; it is not an exhaustive bug tracker.

## Generated tasks can be too easy

Generated cases may let an agent pass without exercising the behavior you
intended to evaluate. A high pass rate on a small starter suite does not
establish that the agent handles realistic variations or harder cases.
Task validation checks whether the environment and grader work; it does not
calibrate difficulty for your agent.

**What to do:** Review the proposed inputs, variations, and success criteria
before generation. Include realistic negative cases and edge cases, and inspect
actual-agent runs for shortcuts or instructions that reveal the answer. Use
those results to revise the cases and rerun validation after edits. Keep task
controls and actual-agent results separate when interpreting scores.

See [planning task breadth and difficulty](docs/first-evals.md#agree-on-what-to-test)
and [validating tasks and evaluating the agent](docs/first-evals.md#run-the-agreed-suite).

## Authoring prompts may need iteration

An open-ended request such as “help me with my evals” does not specify whether
you want to build a first suite, inspect existing evaluations, or audit coverage.
You may need to clarify the workflow and refine the proposed cases over several
turns. Case design also needs your intended behaviors, difficulty, and grading
criteria; the first proposal should not be treated as a complete specification.

**What to do:** Name the workflow, provide relevant repository or artifact paths,
and state your priorities and exclusions. Review the plan before generation,
and correct missing behaviors or assumptions before accepting it.
For example:

```text
Help me build my first evaluations for this agent using ETHOS.md.
Propose realistic cases, including difficult inputs and negative cases for the
behaviors we agree to test. Explain what each case measures, how it is graded,
and what remains untested. Show me the plan before generating tasks.
```

See the [workflow starter prompts](README.md#start-here) and
[skill-access check](docs/getting-started.md#verify-your-assistant-can-use-eval-author)
if the assistant cannot find the installed skills.

## Report an issue

Follow the [research feedback guidance](README.md#help-shape-the-research).
Include the workflow, expected behavior, observed behavior, and a small
anonymized example. For task difficulty, explain how the agent passed and which
intended behavior it did not need to exercise. For prompting, describe the
requested workflow and how the assistant's response differed from it.
