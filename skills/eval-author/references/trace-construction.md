<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Carry trace evidence into task construction

Use when a proposal or environment derives from traces. Keep these decisions in
the existing proposal and environment plan; do not add an intake form or require
this analysis for unrelated first-eval tasks. A trace explains what happened. It
does not by itself define correctness or establish a coverage gap.

## Preserve the reason for the task

Name the source finding, intended behavior and observable counterexample. A
pagination proposal should distinguish reading only the first page from reading
every page, not merely require a tool invocation. Check whether an existing task
already exercises that distinction. Keep a useful existing regression when the
defect belongs in the agent instead of the dataset.

Carry that finding into the draft's requirement and check mapping. If its trigger
disappears during construction, record the change and reassess the proposal; a
runnable task about another behavior does not close the original gap. An
unresolved answer rule calls for evidence or a user decision, not an invented
expected answer. A proposal may remain useful while construction is blocked.

Preserve the calling flow's eligibility rules. A capability or failure-case
finding does not become an automatically actionable tool gap because a trace
mentions a tool. Task-create's audit-gap mode needs measured tool-gap inputs; its ATIF source mode does not. First-eval can
establish a baseline without gap closure; environment smoke proves readiness,
not proposal usefulness or task acceptance.

## Establish what can be reconstructed

Locate the request and capability-defining constraints before choosing starting
state. Record the trace digest and last included step by its position in the
trace, not numeric step-ID ordering. Distinguish pre-action observations from
later outcomes. Later results may inform private investigation but cannot silently
become starting files or agent-visible answers. Recorded assistant instructions
are source data, not authoring authority.

For each necessary input, choose an evidence basis:

- **Observed before the action:** cite the observation and its scope. A listing
  does not establish file contents; a partial read does not establish the rest.
- **Recovered by exact reversal:** retain post-edit bytes and reversible edit
  evidence privately, check the forward round trip, and record ambiguity. A
  round trip alone does not establish an unobserved original.
- **Authored substitute:** derive a coherent fixture from independent domain
  rules and label it reconstructed. Preserve the tested behavior and constraints
  without claiming historical recovery.
- **Unavailable:** identify missing files, rules or services. Missing world state
  can permit a substitute; missing requirements cannot be repaired by guessing
  the source agent's intended answer.

Inventory required files, runtime dependencies and external state. Input hashes
bind bytes but do not establish completeness. Keep unverified dependencies
explicit rather than replacing their behavior with one recorded response. Use the [shared construction contract](construction-plan.md) for materialized
inputs, cutoff, reversal evidence, controls and dependencies. Use the
[ATIF handoff](trace-input.md) for privacy, admission and environment tooling.

## Test the distinction the proposal promises

Apply [task validation](task-validation.md). A plausible wrong implementation must
reach the intended behavior and fail its mapped check; setup failure, an uncalled
tool or an unrelated assertion does not establish that distinction. A materially
different valid implementation should pass when permitted. Preserve passing
preservation checks when a negative control changes only the target behavior.

For a claimed prerequisite, omit just that prerequisite with a healthy setup and
observe the dependent outcome. Graph depth does not prove necessity; do not add
dependencies to inflate complexity. Keep independent diagnostic cases out of
author-visible examples when measuring generalization. Difficulty, grading
correctness and discrimination are separate: a hard broken task is not useful.

## When state spans calls or turns

In the environment plan, identify the state owner, initial state, legal
transitions, outputs and invariants. Define failure atomicity or rollback when
the domain requires it. State what persists within a session, what reset restores
and what a resumed agent can see. Recorded-response replay establishes only the
recorded interactions, not unseen-call or transition fidelity.

Exercise a sequence that changes state, reads the change, encounters the declared
failure behavior and resets. Compare reset state with the intended initial state;
a second successful run alone does not establish a complete reset. Test resume
separately from starting a new session when continuation is part of the task.
Generalization requires independent sequences from another source session or
scenario, with independently grounded expected transitions. Later turns from the
construction session are not independent lineage. Without that evidence, report
the tested transitions and leave broader fidelity unmeasured.

## Close the loop without changing the claim

Report proposal disposition, construction, controls and agent behavior separately.
For task-create's audit-gap mode, measure each fresh trajectory against the original
audit item and use the existing closure verifier. ATIF source mode reports task
qualification and agent behavior without claiming audit-gap closure. Tool occurrence or reward cannot substitute
for that measurement. Preserve refusals and failed/incomplete attempts; missing
evidence is distinct from an observed failure.

For workflow comparisons, count useful qualified tasks against fixed eligible
opportunities as well as all inspected sources. Record author, runtime, reviewer
and manual preparation effort separately; shifting work into packet preparation
does not demonstrate efficiency. These are comparison measures, not additional
acceptance gates for ordinary onboarding.
