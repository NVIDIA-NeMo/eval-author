<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Task contract and scoring authority

**Experimental; selected-area opt-in only.** Follow the [skill gate and scope
selection](../SKILL.md#opt-in-gate). This validity area asks whether every scored
condition and expected result in a task has legitimate authority. Apply it the
same way to every task, whoever or whatever produced it. It has no aggregate score.

## Canonical definitions

The [frozen Rubric v4 subset](task-contract-authority.json) is the single source
for the three criterion IDs, original questions, measurements, nine checks, score
anchors, evidence requirements, and boundaries. Read each selected `items` entry
in full before assessment. Rubric-wide `policies` and the `result_template` row
format are defined once in [the provenance subset](artifact-configuration-provenance.json);
apply them here unchanged. Every selected criterion gets a result row, and every
canonical check in it gets an outcome or an explicit not-run, blocked, or
not-applicable reason.

## Inputs

Use the same inputs for every selected task:

- Solver-visible material: the instruction, workspace files, tool documentation,
  and anything reachable under the solver's permissions.
- The scoring path: verifier entry point, assertions, expected-value files, judge
  prompts, and reward writer. When an identity selection exists, reuse its
  resolved scorer and dependencies from [the identity measurement](identity.md).
- The reference solution, if present, as feasibility evidence only.
- The current Ethos, if the suite has one, as owner intent.

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

- CREATE-03-C01, CREATE-04-C01, and CREATE-05-A-C02 are `llm_as_judge` checks,
  and no judge is qualified for this area. Record your review with reviewer kind
  `agent`; never label it human review or a qualified judge.
- Agent review alone can support 0 or 1 at E1 when cited files establish the
  anchor. Confirming an unsupported condition requires inspecting the full
  declared solver-visible material; an incomplete search cannot establish absence.
- Level 2 additionally requires human adjudication of the crosswalk by the user
  or task owner, recorded as `human_review` with its source, or an authorized
  probe through the native scorer (CREATE-03-C03, CREATE-04-C03). Without either,
  an agent-only crosswalk with no defect leaves the item `NOT_ASSESSED`: report
  its measurements and name the review or probe that would complete it.
- Level 3 follows each canonical anchor and requires independent corroboration.
- E0 assertions support no numeric score. E1 is inspectable files, E2 an
  executed probe on the identified revision, and E3 independent corroboration.
  Execution raises evidence strength, not merit.
- A confirmed defect outweighs supported rows in the same criterion. Reuse one
  finding ID across criteria only when each criterion's own anchor is met.
- CREATE-04-C02 and CREATE-05-A-C03 count crosswalk rows by status against all
  material scored conditions and expected results. Report the denominators.
- `NOT_ASSESSED` is not 0. `NOT_APPLICABLE` needs a task-specific reason.

## CREATE-03 — Clear task contract

[Canonical definition](task-contract-authority.json#/items/0)

**Methods and evidence:** Extract the goal, allowed actions, outputs, constraints,
and assumptions from solver-visible material and compare each stated condition
with the scorer's expectation. Keep a cited ambiguity register. Human
interpretation resolves ambiguity; authorized valid boundary solutions test it.

**Assessment limit:** A condition the contract never states is a CREATE-04
finding. It is a CREATE-03 defect only when the stated contract also contradicts
the scorer or is too underspecified to grade. Do not resolve ambiguity from
hidden author intent, grading notes, or the reference solution; a reference solve
does not establish clarity.

## CREATE-04 — Discoverable grading requirements

[Canonical definition](task-contract-authority.json#/items/1)

**Methods and evidence:** Map every material scored condition to a span the
solver can read or reach, then count coverage and broken references against the
full scoring inventory. Blind requirement discovery under solver permissions
needs separate authorization.

**Assessment limit:** Reviewer access is not solver access. Ethos is owner intent,
not solver-visible: a condition supported only by Ethos is `unsupported` here
unless the task exposes it. A confirmed unsupported material condition is a
defect even when other rows are supported.

## CREATE-05-A — Correctness and scoring authority

[Canonical definition](task-contract-authority.json#/items/2)

**Methods and evidence:** Record a correctness source for each expected result and
each partial-credit, guardrail, entity-linkage, and protected-state rule.
Recompute fixture-derived values when the computation is exact. Human review
justifies results and resolves contradictory authority; agent comparison proposes
it.

**Assessment limit:** A gold file, recorded answer, reference solution, or source
agent's output is not authority because it exists. An expected result with no
source is a known authority gap; one that contradicts its source is a defect. No
model-majority vote replaces correctness authority.
