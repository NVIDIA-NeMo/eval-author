<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Agree on the corpus and coverage

Read this when planning first evals or proposing and creating additional coverage.
Keep the current agreement in [shared workflow state](shared-prerequisites.md#save-and-resume),
with links to any source manifest and case inventory. Audit-based proposals
use the [Audit coverage report](../../eval-author-audit/references/coverage-report.md#findings-limitations-and-next-actions).
A proposal is provisional until the user settles its open choices; it is not
permission to generate or run evaluations.

## Select trace evidence before using it

Traces are optional for first evals. The absence of evals does not establish the
absence of traces, and a repository fixture is not automatically the intended
corpus. Before searching for or reading traces, present source candidates already
known from the user's message, selected documentation, or prior inventory, with
their locations and what they appear to contain. Treat documentation counts as
reported, not verified. If no source is known, ask where the relevant traces are
or whether to proceed from Ethos without them. Do not recursively search the
workspace for traces to populate this question.

Propose which source and bounded corpus to use and ask the user to confirm or
change it. Reuse an explicit selection already supplied, including a choice to
use no traces. A general request to build evals is not a source selection. Once
selected, inspect only that location and apply the agreed filters or sample.
If inspection reveals materially different contents, unavailable inputs, or a
need for another source, explain the change and settle the affected choice
before using a substitute. Independent Ethos-based planning can continue.

Record in the selected journey scope:

- Selected source paths or export identifiers, inclusion filters, time window
  or snapshot when relevant, and excluded sources; retain the user's selection.
- The available count, inspected subset, and usable subset separately, with
  unknown counts explicit. Record stable example/trace IDs or a manifest so a
  later step can use the same selection. Do not call a sample the full corpus.
- Any derived fixtures and their source IDs; mark synthetic examples and
  transformations separately from observed traces. Trace evidence does not
  determine the intended answer by itself; use the behavior specification.

Carry this selection into later generation, expansion, and requested coverage
work. Reuse the audit's confirmed selection when entering from an audit. Fresh
evaluation runs are new evidence with their own run IDs; they do not silently
enlarge or replace the source corpus. If corpus membership changes, update the
agreement and identify affected plans, fixtures, and coverage claims for review.

## Enable experimental user simulation

User simulation is experimental and off by default for each suite. Treat
“enable user simulation” and “enable experimental user simulation for this
evaluation suite” as explicit opt-in. An explicit request to disable user
simulation turns it off for that suite. Reuse the choice when resuming the same
suite. Start a new suite with simulation off, unless the user enables it for
that new suite. Installation, saved simulation files, task-design approval,
and a general request for multi-turn coverage don't enable it.

This is a scoped settings change, so it doesn't start onboarding, task creation,
or a run. Use the active or explicitly named suite. If the suite is ambiguous,
ask which suite the choice applies to. With a known repository but no suite yet,
save the choice for its next authoring proposal. Preserve the active journey,
pending question, existing approvals, and retained evidence.

Read the saved metadata with `journey_state.py --repo <repo> --state-only show`.
For an existing authoring scope, use its current revision for a state-only update:

```json
{"scopes": {"create": {"user_simulation": {"enabled": true, "source": "enable user simulation"}}}}
```

Use `create` for first-eval, including expansion routed to that flow, and
`expand` for task-create. Retain the user's actual request in `source`. Use
`enabled: false` for withdrawal. If state doesn't exist, initialize only the
selected authoring journey with `--state-only init`, without probing prerequisites
or inventing an agent or provider. Save through the existing revision-checked
`--state-only update --input <patch.json> --expected-revision <revision>` command.
The owning validator is `validate_user_simulation` in
[`interaction_types.py`](../scripts/interaction_types.py). Missing metadata means off.

While off, propose Single Turn or Scripted Multi-step when they can exercise the
behavior. Keep behaviors requiring adaptive user replies as uncovered or blocked.
Retain existing `user_sim` designs, scenarios, and results for inspection, with
simulation authoring and execution blocked. Use `first_eval_simulation_disabled`
for the saved first-eval suite's disabled status. Changing a task's type still
requires design review. Don't silently turn an adaptive scenario into a script.

With simulation enabled, normal design review, environment proof, integration
checks, and execution authorization still apply. Before scenario authoring,
translation, or execution, check the selected scope's saved choice. Direct helper
invocation and native Harbor `user_agent` configuration don't establish opt-in.
Discovery may inspect existing configurations and report their capabilities while
authoring and execution stay off. Disabling preserves historical evidence and
revokes first-eval run authorization. Re-enabling doesn't restore that authorization.
Clear the saved choice when starting a different suite in the same scope.

## Choose the interaction type

- Apply the [simulation opt-in gate](#enable-experimental-user-simulation) before selecting interaction types.
- Choose an interaction type for each task before saving or reviewing its
  proposal. Save `interaction_type` alongside the request, expected behavior,
  and grading rule.
- For both Scripted Multi-step and User Simulation, save `state_requirements`. Describe
  which session, conversation history, and environment state must continue
  between requests and what resets between attempts.
- Follow the field requirements validated by
  [`interaction_types.py`](../scripts/interaction_types.py).
- Choose based on the user input needed to exercise the behavior. Conversation
  length, tool-call count, and a conversational interface alone don't justify
  User Simulation.

### Single Turn

- Use `single_turn` when one user request can exercise the behavior. The agent
  may make several tool calls to complete it.
- For example, a delivery-date request that includes the order number and
  postcode can require order lookup, carrier tracking, and policy checks without
  needing another user message.

### Scripted Multi-step

- Use `scripted_multi_step` when predefined requests and checked intermediate
  outcomes lead to the behavior under test. The expected progression must be
  predictable, even if the agent's wording varies.
- Use this type for critical paths that an adaptive conversation might miss.
  For example, add a specified item to a cart, verify that it's there, then
  request its removal. The removal request stays the same across valid replies.
- Save a nonempty `prerequisite_checks` list. Each check must establish the
  state needed by a later request. A failed prerequisite counts as a failure
  and leaves the later behavior unexercised. A fixed script can't guarantee
  that the agent reaches that behavior.

### User Simulation

- Use `user_sim` when later user messages must respond to what the agent
  actually asks, offers, or does. The next message depends on the meaning of
  the response, not just its wording. For example:
  - The agent may ask for the order number or damage description first. The
    simulator supplies the requested fact and keeps the other fact available.
  - The agent may offer different replacement items as stock and prices change.
    The simulator chooses an eligible item from the actual offer, within the
    user's budget, and confirms that item's identifier.
  - The agent may find that a requested appointment is no longer available.
    The simulator accepts a suitable alternative or asks for another time based
    on the user's availability and the agent's actual response.
- Save `conversation_scenario` to describe the adaptive exchange, user-known
  facts, allowed reactions, and observable agent behavior to check. Include any
  delayed disclosure that the target behavior requires.
- Reuse the task goal and fixtures. Start with a neutral user and shared behavior
  defaults. Add disclosure or stopping rules only when the target behavior needs
  them. Persona generation is outside this stage.

### Ground the design in available evidence

- When selected traces are available, identify turns where the next user input
  depends on an earlier response. Cite the selected trace and turn identifiers
  in the proposal's evidence. Distinguish choices among offered options from
  wording differences or incidental chatter.
- Treat traces as evidence of observed paths. Use Ethos and policy to establish
  intended behavior. Without enough trace evidence, label the scenario synthetic
  or uncertain. Don't invent observed exchanges.
- Keep interaction type separate from execution stages and runner capabilities.
  Missing runtime support leaves execution blocked. It doesn't change the
  intended interaction type or establish that simulation ran.
- Preserve task-create's existing source-mode and automatic tool-gap eligibility.
- Leave legacy plans without a type unspecified until reviewed. Don't infer
  Single Turn. A change to the interaction type or scenario changes the proposed
  design and requires review of the affected agreement.

## Plan breadth per behavior

Explain the counting units before discussing scope:

- A **distinct input example** is a different request, fixture, or starting
  condition that exercises a meaningful scenario. Replaying it or renaming its
  file does not create another example. Preserve stable IDs and explain any
  transformation that makes a new example meaningfully different.
- A **task** packages instructions, environment, and grading. A Harbor task can
  exercise one example or several; a NeMo Gym draft can contain several dataset rows.
  Map examples to the selected runner's tasks or rows; a draft count alone says
  little about breadth. An example reused across behaviors can appear in each
  behavior's mapping but counts once in the suite's unique total.
- An **attempt** repeats an execution unit to observe variability: a Harbor task
  or a selected NeMo Gym dataset row. Repeats and verifier controls do not add
  distinct examples or new scenarios.

For each behavior, save a compact table or list containing:

- The Ethos requirement or reviewed criterion, purpose, and priority, including
  user-requested concerns such as execution cost.
- The proposed and then agreed number of distinct examples; their meaningful
  variations, difficulty, positive scenarios, negative scenarios, and relevant
  difficult negatives. Explain why this breadth fits the purpose and which
  kinds of mistakes it could expose. There is no universal example quota.
- Expected outcomes or labels, the evidence supporting them, and grading rules
  that distinguish correct and incorrect behavior. Mark unresolved semantics
  for review instead of treating an existing model prediction as ground truth.
- Excluded scenarios and remaining gaps, with reasons; planned task packaging,
  examples per task, and attempts per task. Keep these units separate.
- Execution-cost implications, known prerequisites, and decisions still open.

When recommending a **pilot**, explain the term in the plan-review reply itself:
a limited first set of evaluation examples to check the test setup and get an
initial measure of performance on selected behaviors. Explain what this pilot
will help the user learn, why its proposed breadth fits that purpose, and what
would remain untested even if every example passed. For example, a check of one
model decision does not establish that its chosen action actually happened.
Calling a set "small" or linking the plan does not supply this explanation.

Offer a minimal pilot when useful, and explain what broader coverage would add
in variation and difficulty. The user can choose either or revise the scope.
Do not silently turn a pilot into the final breadth standard or require
comprehensive coverage as an onboarding gate. Counts must follow the agreed
purpose and resources, not a fixed minimum or the first convenient fixtures.

## Choose useful negatives

Where a behavior distinguishes a trigger from similar inputs, include relevant
difficult negatives: examples that plausibly resemble the trigger but should
not satisfy it under the intended semantics. Inspect such candidates when
available in the selected corpus instead of taking only the first clean records.
For a complaint detector, a quoted complaint, resolved historical frustration,
or a technical use of a negative word might qualify, depending on the reviewed
definition. None of those categories has a universal label.

For each selected negative, record the expected label, the specification or
reviewed criterion that justifies it, and why it is confusable. Classifier scores
can rank candidates; they cannot be the sole ground truth. Keep ambiguous labels
open for review or explicitly excluded. If the selected corpus has no relevant
hard candidates, record the gap and propose reviewed synthetic variations or a
source change; do not fabricate observed evidence or quietly widen the corpus.

## Explain ongoing cost and settle the plan

Resolve the runner from the user's request and existing suite before finalizing
execution counts. If it remains undecided, label counts as conditional. Show
distinct examples, task/draft count, and repeat settings separately:

- For Harbor tasks indexed by `t`, planned agent trials are `sum(attempts[t])`;
  example executions are `sum(examples[t] * attempts[t])` when each task runs all
  its examples. State sampling or early-exit behavior if that assumption is false.
- For NeMo Gym, count rollouts over the selected dataset rows and their configured
  repeats. One draft with four selected rows and two repeats means eight agent
  executions for one agent configuration, not two. Duplicate rows or repeated
  rollouts do not increase the distinct-example count.

Account for every selected agent/model configuration when it multiplies the
execution set. Explain how added examples and repeats change the work. Include
control runs, model-based grading, environment setup, and retries when relevant;
separate one-time preparation from costs paid on every rerun.

Use measured comparable token, time, and cost data or supplied pricing when
available, with provenance and assumptions. Otherwise give relative work counts
and say monetary cost is unknown; do not invent a dollar estimate. Cost can be
a budget constraint, an observed metric, or a pass/fail criterion: record which
the user intends and any agreed threshold. A cheap failing answer is not success
unless it satisfies the agreed behavior and grading criteria.

Present the concrete proposed plan before creating fixtures, instructions, or
graders. Summarize cases, outcomes, grading, priorities, breadth and difficulty,
exclusions, and execution cost. Use the calling flow's scope check-in to ask
explicitly what the user wants to change or prioritize. Name relevant choices
and their tradeoffs, such as lower ongoing cost with fewer examples or repeats,
measuring the agent's cost or latency, broader behavior coverage, more difficult
cases, or another user priority. Keep the cost of running evaluations distinct
from evaluating the agent's cost efficiency; clarify that distinction when the
user's cost preference is ambiguous.
A generic "Does this look right?" alone is insufficient. For example:

> Before I generate these cases, what would you like to change or prioritize:
> lower rerun cost, the agent's cost or latency, broader coverage, harder failure
> cases, or something else?
> You can also keep the proposed plan as written.

Adapt the question to the actual plan and priorities already established. If
the user already chose lower cost, say how the plan reflects that and invite
changes to the specific remaining tradeoff; do not restart a preference interview
or imply that cost must take priority. This is the existing scope checkpoint,
not another approval step after an agreed plan. Put the explanation and feedback
invitation in the review reply, even when they also appear in the saved scope in journey state.
For journey `expand`, use [the saved scope checkpoint](conversations/expansion.md#existing-scope-checkpoint)
to render and retain this review and its actual answer.

Wait for unresolved scope choices, incorporate feedback in the saved
agreement, then generate the agreed cases. Before the first generation or
scaffolding call, save the accepted revision with the user's decision, selected
corpus and exclusions, priorities and cost constraints, pilot or expansion status, all
[per-behavior planning fields](#plan-breadth-per-behavior), current example
IDs/counts, task mapping, and attempt settings. Replace superseded
current values or label them as history; an approval recorded only in the
conversation while the plan still says "pending" is not a saved agreement.
Reuse decisions and authorization
already established; do not ask again merely because a new stage starts. Scope
agreement does not expand the requested execution scope. Proposal-only work ends with its
recommendations and open decisions, without demanding approval or scaffolding.

## Expand and reconcile

When adding coverage, reopen scope for the affected behaviors. Preserve the
existing corpus, priorities, and accepted cases; distinguish retained examples,
new examples, and the new unique total. Explain the added variation/difficulty,
updated cost, and remaining gaps. Settle changes before generating the additions.
Persist the revised agreement using the checkpoint above before creating any
added fixture or task; do not wait until the final handoff to update the plan.
Do not inherit the pilot's size or count repeated runs as expansion. If a tool's
creation path cannot implement the agreed breadth, explain that limit and agree
on a supported subset or leave the unsupported portion explicitly pending.

At handoff, reconcile agreed versus delivered distinct examples for every
behavior using example IDs, task paths, and criteria/label rationale. Report
generated, validated, and actually executed examples separately, alongside task
counts and attempts. Name omissions, substitutions, blocked work, and remaining
gaps; a smaller delivered subset does not rewrite the agreement. Verification
of one tool in repeated trials proves only that limited closure, not the full
behavior scope. Keep the checklist honest about unfinished work and identify
the next action from the saved scope in journey state.
