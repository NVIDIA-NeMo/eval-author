<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Data leakage and forged credit

**Experimental; selected-area opt-in only.** Follow the [skill gate and scope
selection](../SKILL.md#opt-in-gate). This validity area asks whether the agent
under test can get the answer, or earn credit it didn't work for. It has no
aggregate score.

## Canonical definitions

The [canonical subset](leakage-and-credit.json) is the single source for the
seven criterion IDs, questions, measurements, 23 checks, score anchors, evidence
requirements, and boundaries. Read each selected `items` entry in full before
assessment. Rubric-wide `policies` and the `result_template` row format are
defined once in [the provenance subset](artifact-configuration-provenance.json);
apply them, and the result-row, check-accounting, assessment-state,
defect-precedence, and evidence-strength rules in
[assessment and evidence](artifact-configuration-provenance.md#assessment-and-evidence),
unchanged. METHOD-J1 (judge trust boundaries) belongs to this area but has no
canonical definition yet; report it as unassessed and name the missing
definition.

The [anti-cheat criteria](anti-cheat.md) AC-01 to AC-04 predate these IDs and
remain the evidence-floor guidance for the scorer-control panel. Map them as
follows and report the canonical ID:

| Anti-cheat criterion | Canonical criteria |
| --- | --- |
| AC-01, protected information | AUD-05-B, CREATE-02, and AUD-03's forbidden-answer-access control |
| AC-02, shortcuts | AUD-03 |
| AC-03, current trusted results | AUD-03 |
| AC-04, prior trials | ENV-03-S01 |

## Measurements

Run these through `measure.py`; each is documented in
[the identity measurement](identity.md). They supply bounded observations for
specific checks and never assign a criterion score.

| Measurement | Supports | What it covers |
| --- | --- | --- |
| [`layout`](identity.md#harbor-task-layout-labels) | AUD-05-B-C01 | Public and private files from the Harbor layout |
| [`leakage`](identity.md#internal-answer-leakage) | AUD-05-B-C01, CREATE-02-C01 | Private content in public files and instruction references |
| [`overlap`](identity.md#development-and-test-overlap) | AUD-05-B-C01, AUD-05-S03-C01 | Duplicate and sibling tasks across declared splits |
| [`external`](identity.md#published-sources-the-agent-can-reach) | AUD-05-B-C01 | Published task sources the agent's network can reach |
| [`reuse`](identity.md#benchmark-content-in-prompts-and-skills) | AUD-05-S03-C01 | Private task content added to prompts, skills, or harness code |

The cheating evaluation set's hidden-case read is the forbidden-answer-access
control for AUD-03-C02. It counts as a defense only when its `read_boundary` is
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
- **Development and test overlap:** `overlap`.
- **Benchmark-result reuse:** `reuse`.

Data contamination means a model memorized the answer during training. None of
these measurements observe training data: never state or imply that a task is
free of contamination.

The cheating examples and their expected rewards are listed once in the Eval
Author repository's `evals/verifier-cheats/manifest.json`, which the evidence
collector reads. Grader regression work reuses that file
rather than copying controls or rewards.

## AUD-03 — Forbidden-credit resistance

[Canonical definition](leakage-and-credit.json#/items/0)

**Methods and evidence:** Select attack classes with the shared
[simple-cheat screen](../../eval-author/references/task-validation.md#screen-simple-cheats)
and the AC-02 and AC-03 guidance. Use the hidden-case read and its paired read
for forbidden answer access.

**Assessment limit:** The fake-success control covers one forged-success form.
Stale success files, agent-written reports, and wrong-version results have no
controls yet; keep those classes unresolved.

## AUD-04-B — Exploit containment

[Canonical definition](leakage-and-credit.json#/items/1)

**Methods and evidence:** No measurement or control yet. Report confirmed
findings from the other criteria as candidates for containment.

**Assessment limit:** Leave it `NOT_ASSESSED`; don't propose a repair as verified.

## AUD-05-B — Exposure disclosure

[Canonical definition](leakage-and-credit.json#/items/2)

**Methods and evidence:** Use `layout`, `leakage`, `overlap`, and `external` for
the deterministic inventory of accessible answer artifacts. Recomputing outcomes
over exposed subsets has no measurement yet.

**Assessment limit:** These measurements inspect local tasks and declared
settings. They can't support a claim about training-data exposure.

## AUD-05-S03 — Adaptive selection and confirmation

[Canonical definition](leakage-and-credit.json#/items/3)

**Methods and evidence:** Use `reuse` for task content added to prompts, skills,
or harness code since the chosen base revision, and `overlap` for split lineage.

**Assessment limit:** No decision ledger is reconciled. A clean `reuse` result
doesn't show that results weren't used to tune the agent.

## TRACE-04-A — Review secrets and proprietary information

[Canonical definition](leakage-and-credit.json#/items/4)

**Methods and evidence:** No measurement yet. Report secrets found while
reviewing other criteria under sensitive-data exposure.

**Assessment limit:** Leave it `NOT_ASSESSED`.

## ENV-03-S01 — Verify reset closure and episode isolation

[Canonical definition](leakage-and-credit.json#/items/5)

**Methods and evidence:** Harbor starts each trial in a fresh container. Review
state that outlives the container: extra mounts, Compose volumes, and external
services. When tasks share such state, the existing no-op control run after
another task's trial is the predecessor control.

**Assessment limit:** NeMo Gym resources servers can keep state between rollouts;
the fresh-container argument doesn't apply to them.

## CREATE-02 — Task-irrelevant shortcuts

[Canonical definition](leakage-and-credit.json#/items/6)

**Methods and evidence:** Use `leakage` for local answer-bearing artifacts.

**Assessment limit:** Cue-bearing filenames, answer positions, and formatting
cues have no measurement yet; keep them unresolved.
