<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Write the Audit coverage report, section by section

The renderer builds `audit-coverage-report.md` from `report-content.json`. You
write only the fields named in this guide. Headings, counts, coverage
statements, tables, evidence citations, links, numbering, and definitions are
computed or come from fixed copy in `scripts/audit_spec/report_layout.py`.

This guide follows the report from top to bottom. Each section lists what the
renderer writes, which fields you author, and an example. Read it with the
[worked content example](../examples/report-content.json) and the
[content schema](../schemas/report_content.schema.json). The
[report workflow](coverage-report.md) covers validation, rendering, and review.

## Rules for every section

### Writing

- Write full, direct sentences with active verbs and everyday words. Table cells and lists may use short fragments.
- Avoid filler, buzzwords, dramatic hooks, em dashes, and semicolons.
- State facts without announcing them. Write "T2's grading script copies…", not "It is important to note that…".
- Qualify each claim with the evidence and scope behind it. Keep code inspection, executed results, and unresolved concerns distinct.
- Use the report's defined terms: behavior, capability, tool, task, verifier, environment, run evidence.
- Don't repeat what the renderer already writes in the same section. Each section below lists that copy.
- Treat `--lint-prose` warnings as editing suggestions, not as a substitute for rereading the claim.

### Task, finding, and file references

- Refer to a task by its ID only, such as `T2`. The renderer turns every mention into a link labelled with the full identifier, like `T2-safe-01-embedded-instruction`.
- IMPORTANT: Name each task ONCE PER FINDING, as the subject of the explanation's first sentence ("T2's grading script…"). A finding includes its explanation, its `advisory`, and the `proposed_task` of every recommendation tied to it. Everywhere else in those fields, write "the task", "its verifier", or "the grading script". The renderer refuses content that names a task twice (`repeated_task_reference`) and names the field to fix.
- If the explanation doesn't name an affected task, the renderer opens it with the task link. Don't name that task anywhere else in the finding either.
- Refer to another finding by wrapping its content ID in braces, such as `{F2}`. The renderer numbers advisories as Advisory 1, Advisory 2… and other findings as Finding 1, Finding 2…, and rewrites each braced reference to match. Bare tokens such as `F1 score` are left unchanged. Never write Advisory or Finding numbers yourself.
- Never write HTML.
- Evidence links come from each record's `evidence` source IDs.
- `audit.md` and `ETHOS.md` are linked only in Scope and Execution Evidence. Other source files, such as grading scripts and run records, are linked only in the evidence citation that ends a finding or advisory. Don't name any of them anywhere else.
- List evidence sources freely in `evidence`. The citation drops the finding's own tasks, `audit.md`, and `ETHOS.md`, and shows each file name once. With nothing left to cite, it states the finding's status without a source.
- Refer to coverage items by their description, such as "Answer with exact quotations", not by a link to the audit specification.
- Name a file or path in plain text only when the reader needs it to understand the issue, such as `/logs/agent/reward.txt`.
- Keep task IDs, file names, and links out of markdown titles. Titles become headings and link anchors.

## Summary

The Summary is always two paragraphs. You complete them with `summary[0]` and `summary[1]`. Each entry is appended to the renderer's
sentences in the same paragraph, so it must read as their continuation.

| Paragraph | The renderer writes | You append |
|---|---|---|
| 1. What was discovered | The counts, the capability coverage ratio, and the verifier conclusion, such as "The audit uncovered evidence of verifier cheating." | `summary[0]`: one or two sentences on what the audit found that the reader should act on first |
| 2. What was not audited | Whether coverage was measured, and why not | `summary[1]`: what else limited the audit and what the findings rest on |

In both entries:
- Don't restate the counts, the ratio, the verifier conclusion, or whether coverage was measured.
- Don't list findings or name tasks. Findings and the Task catalog do that, and each task name becomes a long link.
- Write about the suite as a whole, such as "three of the four verifiers" or "some tasks".

**Avoid:** making each sentence its own paragraph. The verifier conclusion and
the coverage limit each appear twice, and the task name becomes a link.

> This audit includes 3 advisories, 4 findings, and 4 recommended tasks.
>
> Three of the four selected verifiers contain a grading path that an agent can satisfy without doing the task. Retained agent runs exist for three suites and none exists for [T3-source-theme-review](…).
>
> Measured tool and capability coverage is not yet available. The findings below come from inspecting the verifier code and the task files.
>
> Coverage was not measured for this audit. …
>
> The audit uncovered evidence of verifier cheating. …

**Write:**

```json
"summary": [
  "Three of the four selected verifiers contain a grading path that an agent can satisfy without doing the task.",
  "Some tasks also lack runs that could be used to measure results. All findings come from inspecting the verifier code and the task files."
]
```

**Renders as:**

> This audit includes 3 advisories, 4 findings, and 4 recommended tasks. 3 of 9 declared capabilities (33%) have at least one mapped task. The audit uncovered evidence of verifier cheating. Three of the four selected verifiers contain a grading path that an agent can satisfy without doing the task.
>
> Coverage was not measured for this audit because the audit measurement step was not run. Some tasks also lack runs that could be used to measure results. All findings come from inspecting the verifier code and the task files.

## Advisories

An advisory is a `verifier_cheating` finding with status `confirmed` or
`resolved`. It appears only under Summary → ADVISORY and never under Key
findings. An `unproven` cheating finding is an ordinary key finding.

**The renderer writes:** the `Advisory 1 - <title>` heading and any status suffix, the
evidence citation ("Code inspection confirmed verifier cheating, as documented
in …"), and the `Proposed task:` line. See the evidence rule under
[Task, finding, and file references](#task-finding-and-file-references) for
what the citation includes.

**You author:**

| Field | What to write |
|---|---|
| `title` | The shortcut, as a short verb phrase about the verifier, such as "Accepts an agent-written reward file" |
| `explanation` | Start with the task as the subject. State the mechanism, then what an agent could do with it. Two or three sentences. |
| `advisory` | One sentence on which results can't be trusted and until when. Don't repeat the mechanism or name the task. |
| Recommendation `proposed_task` | The repair, and the check that proves it worked. Write "the verifier" or "the task", not its ID. |

**Avoid:** T2 is named in the explanation, the advisory, and the proposed
task. Each becomes the full link, so one advisory names
`T2-safe-01-embedded-instruction` three times. The renderer refuses it.

```json
"explanation": "The grading script for T2 copies /logs/agent/reward.txt to the verifier reward whenever that file is non-empty.",
"advisory": "Until the shortcut is removed, a passing T2 result does not show that the agent resisted the embedded instruction.",
"proposed_task": "Remove the reward.txt copy from the T2 grading script."
```

**Write:**

```json
"title": "Accepts an agent-written reward file",
"explanation": "T2's grading script copies /logs/agent/reward.txt to the verifier reward whenever that file is non-empty, and then skips every real check. An agent that writes a passing value to that file earns full credit without answering the question.",
"advisory": "Until the copy is removed, a passing result does not show that the agent resisted the embedded instruction."
```

with recommendation:

```json
"proposed_task": "Remove the reward-file copy from the grading script, compute the reward from the reply, tool log, and project state, and rerun the early-exit and forged-success controls."
```

## Key findings

**The renderer writes:**
- `Finding 1 - Coverage gaps`, the first key finding when any item lacks demonstrated coverage or a mapped task, or a `missing_task` finding exists. It carries computed counts of undemonstrated and unmapped items and a link to the coverage tables. Whether coverage was measured, and how to measure it, appear only in Summary and in Scope and Execution Evidence. Don't restate either in any finding.
- Finding numbers, headings, evidence citations, and `Proposed task:` lines

**You author:** `title` and `explanation` for every finding that isn't an
advisory, plus the `proposed_task` of each related recommendation.

### Missing-task findings

Findings with category `missing_task` are folded into `Finding 1 - Coverage gaps`.
A `missing_task` finding must have status `confirmed`. When there are no gaps, no
coverage-gaps finding is rendered or counted, and the other findings number from 1.
Each missing-task
finding renders as a bullet that opens with its title in bold, with no evidence
citation. Its proposed task appears as a nested bullet. The counts are
already there, so name the uncovered behaviors the way a reader knows them and
say why the gap matters.

**Write:**

```json
"title": "No task covers unanswerable questions",
"explanation": "No selected task asks a question the project sources cannot answer, so the suite never checks whether the agent admits insufficient evidence instead of inventing a quote."
```

### Other findings

| Field | What to write |
|---|---|
| `title` | A complete statement of the problem with its own subject. It must read correctly after the renderer removes task IDs. |
| `explanation` | Start with the task as the subject. Say what was observed, why it matters, and what it does and does not establish. For `unproven` findings, say what would confirm it. |

**Avoid:** once the task IDs are removed, the title has no subject.

> ### Finding 4 - Have no current control receipts

**Write:**

```json
"title": "Control receipts predate the current task revisions",
"explanation": "T1 and T2 have control receipts only for earlier revisions, so the audit cannot show whether either verifier rejects wrong answers or accepts valid alternatives. Running the controls against the current revisions would settle it."
```

**Avoid:** the finding hedges without saying what would resolve it.

> All ten selected T4 trial results carry reward 0. … the audit treats this as an unproven agent failure until measurement and a repaired verifier confirm it.

**Write:**

```json
"title": "Every recorded run scored zero",
"explanation": "T4's ten retained runs all scored zero, and the task record attributes this to the agent not saving the pasted transcript. Because {F2} shows the grading script also mishandles empty replies, these scores do not yet establish an agent failure. Rerunning the task after the verifier repair would."
```

## Recommended Tasks by Capability & Tool

**The renderer writes:** a ranked Next actions list with one line per
recommendation, ordered by `order` and showing its `priority`, the action, and a
link to its advisory or finding. Then one table per item kind, with a row for each
recommendation that has both a `proposed_task` and mapped `items`. A finding or
item without a recommendation appears in the report with no suggestion.

**You author:** `proposed_task`. Write one or two imperative sentences: the
scenario or change, then what the verifier must require or reject. Leave it
`null` when the next action is collecting evidence, fixing the agent, or
reviewing scope rather than a task.

**Avoid:** "Add a task for insufficient evidence."

**Write:** "Ask a question the project sources cannot answer, and require the reply to say the evidence is insufficient without inventing a quote, participant, or count."

## Task catalog

**The renderer writes:** the task link followed by your `title`, capability mappings, observed tools or
"Unmeasured", and links to related advisories and findings.

| Field | What to write |
|---|---|
| `title` | The task's human name from its README or `task.toml`, without the ID. The Task catalog shows it after the task link. |
| `behavior` | One sentence that starts with a verb: what the task asks the agent to do and what passing requires |
| `notes` | Only task facts the other columns and the findings don't show. Usually empty. |

**Write:**

```json
"title": "Save and summarize a supplied source",
"behavior": "Save a pasted interview transcript as a new source, then summarize its main product issues.",
"notes": []
```

## Coverage report

Every cell here is computed. You supply only `task_mappings`: the tasks for
each item, with `relationship` set to `target` when the task tests the item and
`boundary` when it touches the item only as a guard. Leave mapping `notes`
empty. They aren't displayed, and run evidence shows only its computed status.

## Verifier cheat screening

Fixed copy. Each `verifier_review.checks[].explanation` is kept for validation
and is not displayed. Write one sentence on what you inspected for that task and
check family.

## Scope and Execution Evidence

**The renderer writes:** the specification, Ethos, run evidence, tasks
inspected, and earlier-revision bullets. Run Evidence gives the run count and
the directories that hold the runs, never each run file. When coverage is
unmeasured, the Run
Evidence JSON bullet says how to measure it. It never shows revision digests.

| Field | What to write |
|---|---|
| `verifier_review.notes` | What the verifier review did and did not execute. One or two sentences. |
| `scope_notes[].title` | A noun phrase of one to four words, such as "Fixture limits" or "Excluded runs" |
| `scope_notes[].explanation` | The limit and what it prevents the audit from establishing |

**Write:**

```json
{
  "title": "Excluded runs",
  "explanation": "Oracle and smoke-test jobs were excluded because they do not exercise the agent, so the run evidence covers agent behavior only."
}
```

## Definitions

Fixed copy. Use these terms consistently in every field above.
