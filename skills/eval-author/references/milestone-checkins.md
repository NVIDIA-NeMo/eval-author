<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Authoring milestones after prerequisites and Ethos

The shared prerequisite flow owns common orientation, prerequisite answers,
Ethos confirmation, and resumption. Consume its accepted result instead of
opening another onboarding flow. This reference owns downstream first-eval work
and scope reviews only.

## Explain progress and ask for decisions

Complete authorized work, explain its outcome and limitations, and show the
three-item core checklist with the selected third item current until authoring
finishes. Within that item, name the current authoring stage in ordinary prose.
Do not add a generic start or transition approval. Ask a focused question when
intent, source selection, scope, or execution authorization is unresolved. Reuse
answered decisions. A side question does not approve pending content or reset
onboarding; answer it, then resume the unfinished work.

Finish independent work before a required question or continue work unaffected
by the missing answer. Do not perform the dependent action until the answer is
settled. Keep task preparation, environment validation, agent configuration,
and actual agent execution distinct. A plan is not runnable evidence and a
control run is not the agent's baseline. Partial cases remain incomplete when
the agreed scope is larger. A completed evaluation may have poor scores.

## Progress and resumption

Store stage, prerequisite answers, accepted decisions, pending questions, and
next action only in `.eval-author/.working/journey-state.json`. Present scope
tables in conversation and save agreed corpus, examples, grading, and priorities
in the selected journey's `scopes` entry. Native receipts and measurements remain
authoritative evidence; state references them. Do not create `first-eval.md`,
`audit-progress.md`, replacement plan documents, or progress sections in task
READMEs. Task READMEs explain task behavior and reruns. Import old state as
unreviewed snapshots and preserve historical evidence paths.

## Consume shared prerequisites

The shared prerequisite flow supplies agent identity, selected Gym or Harbor
setup, prerequisite answers, and the confirmed Ethos result from
[Ethos confirmation](ethos-confirmation.md).
Neither a file nor a user-confirmed installation proves runtime readiness.
Planning can proceed without the runtime. At the first authoring operation that
requires it, use the shared [runtime locator](runtime-prerequisites.md) with
`--flow authoring` and the selected provider. Preserve the exact Python/CLI pair
and recorded capability evidence in shared state. Reuse valid verification when
its inputs are unchanged; never silently switch runtimes or install software.

Reuse confirmed absence of evals or the selected suite; do not scan again to
prove it. Before searching traces, follow [Corpus selection](coverage-planning.md#select-trace-evidence-before-using-it)
and reuse the user's source or no-trace choice. Record bounds and exclusions.
Where the starting point remains unresolved, use bounded discovery inventory
without runtime probes. A source choice does not establish scoring quality or
coverage. Continue to scope planning with the accepted Ethos and source choice.

## Scope checkpoint

At **Define the evaluation scope**, explain what the selected cases will measure
before moving to **Prepare cases and grading**. For first-eval and expansion,
follow [Corpus and coverage planning](coverage-planning.md) to record proposed
and agreed scope in shared journey state before generation. Make it concrete:

- **Behavior and purpose:** what the agent should accomplish and why these cases
  matter, using established workflow priorities when available.
- **Success and evidence:** the expected outcome, what the grader must inspect,
  and whether the rule comes from existing criteria or is a proposed decision.
- **Breadth and difficulty:** distinct examples per behavior, meaningful
  variations, positive and negative scenarios, relevant difficult negatives,
  and why the proposed breadth fits the purpose. If recommending a pilot,
  explain in this reply what that limited first set will establish and what
  remains untested even if it passes. Separate example counts from native tasks
  and attempts; name exclusions and remaining gaps.
- **Priorities and cost:** explicitly ask what the user wants to change or
  prioritize, with relevant tradeoffs such as lower ongoing cost, broader
  coverage, harder cases, or another concern. Explain execution work and grounded
  estimates or uncertainty; a generic approval question alone is insufficient.
- **Execution requirements:** the capabilities, software, starting state, and
  access the cases need; distinguish known requirements from verified readiness.
- **Limits and open decisions:** missing criteria or evidence, which checks or
  runs they affect, and what is needed to resolve them.

Explain this through a representative case, grouping others with the same needs
and calling out material differences. Distinguish checking a tool sequence from
checking its resulting outcome. Explain the role of operational metrics such as
cost or latency when present; they establish success only where the agreed
criteria use them. Preserve existing scoring and explain its limits rather than
silently replacing it.

Use the existing stage check-in to settle the next affected decision or confirm
the scope, following the [feedback invitation](coverage-planning.md#explain-ongoing-cost-and-settle-the-plan).
Reuse known priorities when framing that question, without adding another
approval loop for an already agreed plan. Incorporate feedback in shared scope
before generating affected fixtures, instructions, or graders. Reuse established answers; missing inputs
for one check need not hold up preparation of independently agreed cases.
This checkpoint explains the selected scope without requiring a coverage audit
or a new intake document.
