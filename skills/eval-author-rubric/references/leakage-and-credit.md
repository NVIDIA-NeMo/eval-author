<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Data leakage and forged credit

**Experimental; selected-area opt-in only.** Follow the [skill gate and scope
selection](../SKILL.md#opt-in-gate). This validity area asks whether the agent
under test can get the answer, or earn credit it didn't work for. It has no
aggregate score.

## Canonical definitions

The [canonical subset](leakage-and-credit.json) is the single source for the
nine criterion IDs, questions, measurements, 29 checks, score anchors, evidence
requirements, and boundaries. Read each selected `items` entry in full before
assessment. Rubric-wide `policies` and the `result_template` row format are
defined once in [the provenance subset](artifact-configuration-provenance.json);
apply them, and the result-row, check-accounting, assessment-state,
defect-precedence, and evidence-strength rules in
[assessment and evidence](artifact-configuration-provenance.md#assessment-and-evidence),
unchanged. These IDs use the `AUDIT-` prefix where the source rubric writes
`AUD-`; the definitions are otherwise unchanged. METHOD-J1 (judge trust
boundaries) belongs to this area but has no
canonical definition yet; report it as unassessed and name the missing
definition.

## Task and suite levels

Each item has a `level`. A **task** criterion is scored for one evaluation from that
evaluation's own evidence. A **suite** criterion needs evidence from other tasks,
task families or selection history, so it is assessed for a suite and never
contributes to a single task's score. Items without `level`, including every
item in the other areas, are task criteria.

| Level | Criteria |
| --- | --- |
| task | AUDIT-03, AUDIT-04-B, TRACE-04-A, ENV-03-S01, CREATE-02 |
| suite | AUDIT-04-B-SUITE, AUDIT-05-B, AUDIT-05-S03, CREATE-02-SUITE |

AUDIT-04-B and CREATE-02 were split: their cross-task clauses (retained and new
tasks, cross-family replay, new task families) moved to the `-SUITE` items.

The [anti-cheat criteria](anti-cheat.md) AC-01 to AC-04 predate these IDs and
remain the evidence-floor guidance for the scorer-control panel. Map them as
follows and report the canonical ID:

| Anti-cheat criterion | Canonical criteria |
| --- | --- |
| AC-01, protected information | AUDIT-05-B, CREATE-02, and AUDIT-03's forbidden-answer-access control |
| AC-02, shortcuts | AUDIT-03 |
| AC-03, current trusted results | AUDIT-03 |
| AC-04, prior trials | ENV-03-S01 |

## Measurements

Run these through `measure.py`; each is documented in
[the identity measurement](identity.md). They supply bounded observations for
specific checks and never assign a criterion score.

| Measurement | Supports | What it covers |
| --- | --- | --- |
| [`layout`](identity.md#harbor-task-layout-labels) | AUDIT-05-B-C01 | Public and private files from the Harbor layout |
| [`leakage`](identity.md#internal-answer-leakage) | AUDIT-05-B-C01, CREATE-02-C01, AUDIT-03-C01 | Private content in public files, file names, and instructions; hidden-case classification |
| [`overlap`](identity.md#development-and-test-overlap) | AUDIT-05-B-C01, AUDIT-05-S03-C01 | Duplicate and sibling tasks across declared splits |
| [`external`](identity.md#published-sources-the-agent-can-reach) | AUDIT-05-B-C01 | The task's source repository, when any agent phase can reach it |
| [`reuse`](identity.md#benchmark-content-in-prompts-and-skills) | AUDIT-05-S03-C01 | Private task content in the agent's prompts, skills, or harness code |

For forbidden answer access, `leakage` classifies each task's hidden tests as
`protected`, `exposed`, or `unknown` from its files. The cheating evaluation
set's hidden-case read is the executed control for AUDIT-03-C02. It counts as a defense only when its `read_boundary` is
`protected`: the private read was denied and the paired read of the agent's own
file succeeded.

When a selected evaluation isn't a Harbor task or NeMo Gym environment, these
measurements return unknown. Tell the user why the format matters, following
the [task format guidance](../../eval-author/references/task-format.md).

## Scoring rules

- A measurement finding is a candidate. Before a 0, review whether the matched
  content is answer-bearing and unintended, and cite the measurement path and
  the reviewed files. A declared allowed overlap is not a finding.
- Unknown measurement statuses and missing splits, revisions, or files leave the
  affected checks unrun, never passed.
- The human-review checks need the user or task owner. Record agent review with
  reviewer kind `agent`; never label it human review.

## Reports

Keep four kinds of finding apart, each under its own heading:

- **Answer leakage:** `leakage`, `external`, and the hidden-case read.
- **Sensitive-data exposure:** secrets, credentials, or personal data found
  while reviewing. Report them without copying values; TRACE-04-A has no
  measurement yet.
- **Development and test overlap:** `overlap`. Duplicates are defects; siblings
  need review.
- **Benchmark-result reuse:** `reuse`.

Data contamination means a model memorized the answer during training. None of
these measurements observe training data: never state or imply that a task is
free of contamination.

The cheating examples and their expected rewards are listed once in the Eval
Author repository's `evals/verifier-cheats/manifest.json`, which the evidence
collector reads. Grader regression work reuses that file
rather than copying controls or rewards.

## AUDIT-03 — Forbidden-credit resistance

[Canonical definition](leakage-and-credit.json#/items/0)

**Methods and evidence:** Select attack classes with the shared
[simple-cheat screen](../../eval-author/references/task-validation.md#screen-simple-cheats)
and the AC-02 and AC-03 guidance. For forbidden answer access, use `leakage`'s
`hidden_cases` for each task and the hidden-case read with its paired read as the
executed control.

**Assessment limit:** The fake-success control covers one forged-success form.
Stale success files, agent-written reports, and wrong-version results have no
controls yet; keep those classes unresolved.

## AUDIT-04-B — Exploit containment

[Canonical definition](leakage-and-credit.json#/items/1)

**Methods and evidence:** No measurement or control yet. Report confirmed
findings from the other criteria as candidates for containment.

**Assessment limit:** Leave it `NOT_ASSESSED`; don't propose a repair as verified.

## AUDIT-04-B-SUITE — Exploit containment across the suite

[Canonical definition](leakage-and-credit.json#/items/2)

**Methods and evidence:** No measurement or control yet. List the retained tasks
affected by each confirmed exploit from the task-level AUDIT-04-B results.

**Assessment limit:** Leave it `NOT_ASSESSED` until affected retained and new
tasks have replay results.

## AUDIT-05-B — Exposure disclosure

[Canonical definition](leakage-and-credit.json#/items/3)

**Methods and evidence:** Use `layout`, `leakage`, `overlap`, and `external` for
the deterministic inventory of accessible answer artifacts. Recomputing outcomes
over exposed subsets has no measurement yet.

**Assessment limit:** These measurements inspect local tasks and declared
settings. They can't support a claim about training-data exposure.

## AUDIT-05-S03 — Adaptive selection and confirmation

[Canonical definition](leakage-and-credit.json#/items/4)

**Methods and evidence:** Use `reuse` for private task content anywhere in the
agent's prompts, skills, or harness code, and `overlap` for split lineage.

**Assessment limit:** No decision ledger is reconciled. A clean `reuse` result
doesn't show that results weren't used to tune the agent.

## TRACE-04-A — Review secrets and proprietary information

[Canonical definition](leakage-and-credit.json#/items/5)

**Methods and evidence:** No measurement yet. Report secrets found while
reviewing other criteria under sensitive-data exposure.

**Assessment limit:** Leave it `NOT_ASSESSED`.

## ENV-03-S01 — Verify reset closure and episode isolation

[Canonical definition](leakage-and-credit.json#/items/6)

**Methods and evidence:** Harbor starts each trial in a fresh container. Review
state that outlives the container: extra mounts, Compose volumes, and external
services. When tasks share such state, the existing no-op control run after
another task's trial is the predecessor control.

**Assessment limit:** NeMo Gym resources servers can keep state between rollouts;
the fresh-container argument doesn't apply to them.

## CREATE-02 — Task-irrelevant shortcuts

[Canonical definition](leakage-and-credit.json#/items/7)

**Methods and evidence:** Use `leakage` for local answer-bearing artifacts.

**Assessment limit:** Cue-bearing filenames, answer positions, and formatting
cues have no measurement yet; keep them unresolved.

## CREATE-02-SUITE — Task-irrelevant shortcuts across task families

[Canonical definition](leakage-and-credit.json#/items/8)

**Methods and evidence:** No measurement yet. Reuse the task-level CREATE-02
controls on each independently authored family.

**Assessment limit:** Leave it `NOT_ASSESSED` until at least one new family has
qualified cue controls.
