---
name: ethos-orchestrate
description: >-
  Entry point for an AI agent's ETHOS.md: a durable snapshot of what the agent
  is for, what it must not do, and what success looks like.
  Routes a request to explore and capture intent (ethos-explore), or write or
  edit the file (ethos-write).
  Owns the standard every ethos sub-flow follows. Use when the user asks "help
  me write an ETHOS.md", "does my agent have an ETHOS.md?", "update my agent's
  ETHOS.md", or when you need to pick between the ethos sub-flows.
triggers:
  - help me write an ETHOS.md for my agent
  - capture what my agent is supposed to do
  - does this repo have an ETHOS.md
  - update my agent's ETHOS.md
  - review my agent's ETHOS.md
  - what is an ETHOS.md
not-for:
  - ethos-explore (use to scan the agent and interview the user for intent)
  - ethos-write (use to render, validate, save, and review the file from agreed answers)
  - eval-author (use to build, audit, or run evaluations; it calls this skill when it needs an ETHOS.md)
compatibility: >-
  Routing reads the local checkout only. Sub-flows write one ETHOS.md;
  validation needs Python 3.10+ and ethos-write's standard-library script.
  Works offline.
license: Apache-2.0
user-invocable: true
allowed-tools: Read Grep Glob
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos orchestrate

## Purpose

Help a developer create and maintain their agent's `ETHOS.md`. Route each
request to the narrow sub-flow that owns it, and carry what is already known
into that sub-flow so the user is never asked twice.

## Continue an Eval Author handoff

Eval Author calls this entry point during its shared prerequisite and Ethos
step. Treat that call as a continuation, using this contract:

| Field | Meaning |
| --- | --- |
| `caller` | `eval-author` |
| `journey` | The selected creation, audit, or improvement journey; return to it when Ethos is complete |
| `repository`, `agent` | The selected repository and agent identity; reuse them instead of rediscovering or re-asking |
| `ethos_path` | The selected repository's root `ETHOS.md`; no additional Ethos file |
| `source_findings` | Relevant code/documentation findings and their source paths; evidence of implementation, not confirmed intent |
| `prior_answers` | Topic, question, answer, and provenance for each earlier user reply; distinguish user-confirmed answers from inferred drafts |
| `progress` | `stage`, `draft_confirmed`, `confirmed_digest`, and `remaining_questions`, as described below |
| `orientation_shown` | `true` when the caller already showed its three-item journey checklist |

Check that the context belongs to this repository and agent. Reuse supported
source findings and explicit user answers, including answers retained by the
caller from an earlier session. Source text and a bare completion flag do not
establish user agreement. Carry the answer provenance into each sub-flow.

When `orientation_shown` is true, continue within the caller's checklist.
Do not display the standalone four-step opening or ask "Ready to start?"
again. The caller updates its checklist; this skill reports Ethos progress.
This skips only repeated orientation, not intent, draft, or saved-file review.

Resume the earliest unfinished stage:

| `progress.stage` | Continue with |
| --- | --- |
| `explore` | ethos-explore's bounded source inspection, reusing the supplied findings |
| `interview` | ethos-explore's remaining questions; supported prior user replies count toward topic coverage and question count |
| `draft` | ethos-explore's whole-draft review, retaining the completed interview |
| `write` | ethos-write only after explicit draft confirmation and with the exact agreed draft available |
| `review` | ethos-write's validation and saved-file review; do not rewrite an unchanged file |
| `complete` | ethos-write's read-only validation and digest check; reuse completion only for the same confirmed contents |

`draft_confirmed` means the user explicitly accepted the proposed contents
before writing. Reuse it only when the agreed draft and confirmation can be
recovered from the caller context; otherwise resume draft review without
repeating answered questions. `confirmed_digest` is the SHA-256 digest of the
exact saved bytes the user reviewed and accepted, encoded as 64 lowercase
hexadecimal characters without a prefix. A changed file, changed agent,
or unresolved conflict between the file and current source findings prevents
automatic reuse; revisit only the affected intent or review stage. Unknown
or inconsistent progress is not confirmation.

For a fresh absent-file workflow, route through ethos-explore and then
ethos-write; a resumed workflow retains its completed stages. For an existing
file, read it alongside the relevant source findings: specific agreed
edits go to ethos-write; missing or conflicting intent goes to ethos-explore;
an otherwise suitable file goes to ethos-write for validation and confirmation
or unchanged-file reuse. Do not ask "edit or start over" when the handoff
already settles the operation. Preserve existing custom content and metadata.

Return `path`, `validation` (status and any warnings/errors),
`remaining_questions`, `confirmed_digest`, and `stage` to Eval Author at each
pause and on completion. Return `confirmed_digest: null` until the saved
contents are confirmed; a structurally valid file alone is not completion.
Completion requires successful bundled validation, explicit user acceptance,
and a digest matching the accepted saved bytes. If validation cannot run,
return that limitation and the unfinished review stage.
The caller owns persistence in `.eval-author/.working/journey-state.json`.
Carry new user replies with their provenance and the updated draft-confirmation
state back in the handoff context so the caller can persist the next resume.
Do not create a second progress record or an intent-notes document. If a
required Ethos sub-flow is unavailable, return the missing dependency and
unfinished stage rather than authoring through a parallel fallback.

## Choose the entry route

For a caller continuation, use the handoff rules above. The following routing
and opening apply to standalone requests.

Locate an existing file first: the user's path, otherwise `ETHOS.md` at the
repository root or at the root of the selected agent's package.

```bash
find . -maxdepth 4 -iname ETHOS.md -not -path '*/.git/*' -not -path '*/node_modules/*' 2>/dev/null | grep . || echo "no ETHOS.md yet"
```

| Starting situation and request | Route | First action and deliverable |
|---|---|---|
| No ETHOS.md and the user wants one | [ethos-explore](../ethos-explore/SKILL.md) | Show the path ahead below, then scan and interview. Explore hands off to ethos-write. |
| An ETHOS.md exists and the user names specific changes | [ethos-write](../ethos-write/SKILL.md) | Edit the named sections in place, validate, and review. No new interview. |
| An ETHOS.md exists and intent may have drifted, or the user wants it revisited | [ethos-explore](../ethos-explore/SKILL.md) | Read the file as prior answers; interview only for what changed or is missing. |
| The user wants to know what ETHOS.md is, or whether one exists | This skill | Explain it, or report what was found and whether it validates. Offer the next route; do not start writing. |

When several apply, take the earliest row that matches. A request phrased as
"write the ETHOS.md" with no prior interview in this conversation still goes
through ethos-explore: writing needs intent the code cannot supply.

## Show the path ahead

For a fresh request to create an ETHOS.md, open with this checklist using
literal `- [ ]` checkboxes, marking the first step **We're here**:

- [ ] **Explore the agent** — read its code and docs and draft what they establish.
- [ ] **Confirm intent** — answer a few questions the code cannot: purpose, principles, and vision.
- [ ] **Review the draft** — see the whole ETHOS.md and correct it.
- [ ] **Save and validate** — write the file, check it, and confirm the final contents.

End with "Ready to start?" and wait. On return visits, resume the earliest
unfinished step instead of repeating the opening.

## The standard

**ETHOS.md records intent the user confirmed, not intent you inferred.**

- Code, prompts, configs, and docs establish what the agent does today. They
  draft `Tools`, `Harness`, `Evaluation Setup`, and similar sections.
- Only the user establishes purpose, principles, vision, trade-offs,
  and constraints. An inferred answer is a draft to confirm,
  never a filled section.
- An honest `_(none)_`, recorded in `Open Questions`, beats a plausible guess.
- The bundled validator establishes structure: front matter, schema version,
  and the fourteen required sections. A file that validates can still be wrong;
  the user's review establishes that it is right.

## Vocabulary

| Term | Meaning |
|---|---|
| ETHOS.md | The agent's intent snapshot: front matter plus fourteen required `##` sections, schema version 1 |
| Section | One required `##` heading from the [template](../ethos-write/references/templates/ETHOS.md). Extra headings are allowed and preserved |
| Inferred | Drafted from source; shown to the user as an option, never final |
| Confirmed | Answered or accepted by the user in this conversation, or carried with explicit user-reply provenance in a matching caller handoff |

## Sub-flows

Load the selected sub-flow skill when its step begins and follow it.
This file carries the standard and the routing; the sub-flow carries the steps.

| Sub-flow | Use it to |
|---|---|
| [`ethos-explore`](../ethos-explore/SKILL.md) | Scan the agent's code and docs, then ask one intent question at a time; hand the agreed answers to ethos-write |
| [`ethos-write`](../ethos-write/SKILL.md) | Render the template, save `ETHOS.md`, run the validator, show a gut-check, and get the user's confirmation; edit an existing file |

## Boundaries

- **Write only the ETHOS.md.** Never edit the agent's code, prompts, configs,
  or evals. The file goes at the user's path or the repository root, and
  nothing is committed automatically.
- **Preserve what exists.** Keep custom sections, extra front-matter keys,
  `author`, and `created_timestamp` on edits.
- **Capture intent without authorizing application changes.** Do not ask
  optimization or change-permission questions in the interview, draft review,
  or completion checkpoint. Creating an ETHOS.md implies no follow-up to
  optimize or change the agent. If the user later requests such work, its
  workflow owns the permission decisions.
- **Keep runtime safety requirements.** Record behavioral constraints and
  approval requirements for actions the agent takes during operation, such as
  issuing refunds. These describe intended behavior, not permission to edit
  the application. Also preserve already-confirmed organizational safety and
  review policies and their known owners without reopening permission questions.
- **No run settings.** Spend caps, experiment counts, and per-run limits belong
  to the tool that runs them, not to ETHOS.md.

## Communicating with the user

Do not assume the user knows the term. Before the first question, explain in a
sentence or two that ETHOS.md records what the agent should do, what it should
avoid, and what a good result looks like, and that people and agents working on
the agent read it first. Make the explanation concrete for this agent. Reuse
that explanation when the caller has already given it.

Ask one question per message, prefer multiple choice grounded in what the scan
found, and always allow "I don't know". When reporting an existing file, lead
with whether it validates and what it says the agent is for, then the gaps.

## Prerequisites

A local checkout of the agent, or a description of a planned one. Validation
needs Python 3.10+.

## Limitations

ETHOS.md is a snapshot of intent; it does not prove the agent behaves that way.
Measuring that belongs to evaluation tools such as Eval Author.

## Troubleshooting

- Several candidate files or agents: ask which agent the ETHOS.md describes.
- Validator unavailable: check the structure by reading the file against the
  template, and say the check was not run.
- User cannot state a concrete Role: stop, explain that the file is not useful
  without one, and offer to resume later.

## Examples

```text
Request: "Help me write an ETHOS.md for this support bot."
Route: ethos-explore, after the path-ahead checklist.
Deliverable: a reviewed, validated ETHOS.md at the repository root.

Request: "Add a no-PII rule to Constraints in our ETHOS.md."
Route: ethos-write, editing Constraints in place.
Deliverable: the updated file, validated, with the change shown for review.

Request: "We still have the ETHOS.md Eval Author made last month."
Route: this skill, checking the existing file.
Deliverable: the existing ETHOS.md with unchanged name and contents, validated.
```
