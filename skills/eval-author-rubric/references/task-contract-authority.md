<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Task contract and scoring authority

**Experimental; selected-area opt-in only.** Follow the [skill gate and scope
selection](../SKILL.md#opt-in-gate). This validity area asks whether every scored
condition and expected result in a task has legitimate authority. Apply it the
same way to every task, whoever or whatever produced it. It has no aggregate score.

## Canonical definitions

The [canonical subset](task-contract-authority.json) is the single source
for the three criterion IDs, original questions, measurements, nine checks, score
anchors, evidence requirements, and boundaries. Criterion IDs name this area; each
check ID adds its method. The source rubric's IDs stay in each entry's `source`
and `source_id` fields. Look up entries by ID, never by position, and read each
selected entry in full before assessment. Rubric-wide `policies` and the `result_template` row
format are defined once in [the provenance subset](artifact-configuration-provenance.json);
apply them here unchanged. Apply the result-row, check-accounting, assessment-state,
defect-precedence, and evidence-strength rules in
[assessment and evidence](artifact-configuration-provenance.md#assessment-and-evidence);
its criterion count and level-acceptance exceptions belong to that area. The
rules below add this area's requirements.

## Inputs

Use the same inputs for every selected task:

- Solver-visible material: the instruction, workspace files, tool documentation,
  and anything reachable under the solver's permissions.
- The scoring path: verifier entry point, assertions, expected-value files, judge
  prompts, and reward writer. When an identity selection exists, reuse its
  resolved scorer and dependencies from [the identity measurement](identity.md).
- The reference solution, if present, as feasibility evidence only.
- The current Ethos, if the suite has one, as owner intent. Before citing it as a
  correctness source, run the [Ethos structure measurement](identity.md#selected-ethos-structure);
  an Ethos that fails it or whose digest changed is not a usable source.

Construction plans, requirement maps, generator labels, task READMEs, and trace
candidates are claims to compare, never authority. A disagreement between them
and the task is a finding.

## Build the condition crosswalk

Follow the shared [verifier review](../../eval-author/references/task-validation.md#review-the-verifier-before-running).
Record one row per material scored condition: each assertion, tolerance, format
rule, default, preserved state, side effect, and partial-credit or guardrail rule.
Each row records:

- the scorer location that enforces it;
- the supporting solver-visible span, or `none`;
- for an expected value, its correctness source: a stated requirement, fixture
  data, a recorded owner decision, an Ethos section, or independently validated
  reference execution;
- status: `supported`, `unsupported`, `contradicted`, or `ambiguous`;
- reviewer kind: `agent` or `human`.

For a multi-turn task, also record the turn that states the requirement. A later
authorized correction can replace an earlier requirement; silence or a solver
failure cannot. Keep these rows in the result rows' measurements and findings;
they are review notes, not a new file format.

## Scoring rules

- Each check ID ends with its method. The three `.llm-judge` checks are
  `llm_as_judge`. Unless a scope-matched qualification record is supplied, record
  your review with reviewer kind `agent`; never label it human review or a
  qualified judge.
- A score of 0 or 1 at E1 rests on the cited files, not on that review's verdict.
  Cite the scorer location and the solver-visible span, fixture, or source that
  establish the anchor, so a reader can check it without trusting the review.
  Confirming an unsupported condition requires inspecting the full declared
  solver-visible material; an incomplete search cannot establish absence.
- Level 2 requires human adjudication of the crosswalk by the user or task owner,
  recorded as `human_review` with its source: the `.human-review` check of
  `task-contract.clear-contract` and `task-contract.scoring-authority`, and for
  `task-contract.discoverable-requirements` a human decision on its `.llm-judge`
  mapping, as the qualified alternative to the unqualified judge. Without it, an
  agent-only crosswalk with no defect leaves the item `NOT_ASSESSED`: report its
  measurements and name the review that would complete it.
- Authorized `.probe` checks through the native scorer supply the boundary and
  blind-discovery evidence for the level-3 anchors; they do not replace level-2
  adjudication. Level 3 also requires independent corroboration.
- The `.deterministic` checks of `task-contract.discoverable-requirements` and
  `task-contract.scoring-authority` count crosswalk rows by status against all
  material scored conditions and expected results. Report the denominators.

## task-contract.clear-contract — Clear task contract

Canonical definition: the `task-contract.clear-contract` entry in
[task-contract-authority.json](task-contract-authority.json).

**Methods and evidence:** Extract the goal, allowed actions, outputs, constraints,
and assumptions from solver-visible material and compare each stated condition
with the scorer's expectation. Keep a cited ambiguity register. Human
interpretation resolves ambiguity; authorized valid boundary solutions test it.

**Assessment limit:** A condition the contract never states is a
`task-contract.discoverable-requirements` finding. It is a defect here only when the
stated contract also contradicts
the scorer or is too underspecified to grade. Do not resolve ambiguity from
hidden author intent, grading notes, or the reference solution; a reference solve
does not establish clarity.

## task-contract.discoverable-requirements — Discoverable grading requirements

Canonical definition: the `task-contract.discoverable-requirements` entry in
[task-contract-authority.json](task-contract-authority.json).

**Methods and evidence:** Map every material scored condition to a span the
solver can read or reach, then count coverage and broken references against the
full scoring inventory. Blind requirement discovery under solver permissions
needs separate authorization.

**Assessment limit:** Reviewer access is not solver access. Ethos is owner intent,
not solver-visible: a condition supported only by Ethos is `unsupported` here
unless the task exposes it. A confirmed unsupported material condition is a
defect even when other rows are supported.

## task-contract.scoring-authority — Correctness and scoring authority

Canonical definition: the `task-contract.scoring-authority` entry in
[task-contract-authority.json](task-contract-authority.json).

**Methods and evidence:** Record a correctness source for each expected result and
each partial-credit, guardrail, entity-linkage, and protected-state rule.
Recompute fixture-derived values when the computation is exact. Human review
justifies results and resolves contradictory authority; agent comparison proposes
it.

**Assessment limit:** A gold file, recorded answer, reference solution, or source
agent's output is not authority because it exists. An expected result with no
source is a known authority gap; one that contradicts its source is a defect. No
model-majority vote replaces correctness authority.
