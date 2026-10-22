<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos confirmation adapter

This adapter connects Eval Author to
[`ethos-orchestrate`](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/SKILL.md), the
single owner of Ethos exploration, draft confirmation, validation, and writing.
Do not implement another interview or local authoring fallback in Eval Author.

## Required handoff capabilities

Pass the absolute repository-root `ETHOS.md` path as the explicit destination
to `ethos-orchestrate`. The installed skills must preserve that exact path and
uppercase filename, including in repositories with several agents. They must
not rename it to `ethos.md` or choose the agent's package root instead.

Check the installed skills' location and writing instructions before delegation.
If they rename the file, choose a package-level path, or otherwise override the
supplied destination, that version is incompatible with this contract.
Report the actual conflicting skill and instructions under
**Blockers**, preserving existing documents and progress. Resolve the dependency
with a catalog version that honors the supplied path; verify its installed
instructions before clearing the blocker. Reinstalling the same incompatible
version does not resolve it.

## Enter the Ethos step

Enter or resume Ethos in the shared flow only when no Blockers remain. Use **Next Steps** and the
shared progress checklist from [Present results and the next step](shared-prerequisites.md#present-results-and-the-next-step)
before its explanation, review, or question. If prerequisite results are included,
place that section after the table and any Recommendations. A blocked response
ends with Blockers and omits Next Steps, the checklist, and Ethos guidance. Keep this
orientation in Eval Author; the Ethos skills own the content that follows. Existing evaluations describe
current coverage; they do not establish the agent's intended behavior.

Reuse the shared prerequisite orientation. The
shared prerequisite flow confirms repository-root `ETHOS.md`. Preserve existing
alternate-path documents; do not silently copy, delete, or replace them. Resolve
the intended root document with the user if needed. Explicitly scoped `--ethos`
utilities can use their supplied path without claiming the shared prerequisite
flow confirmed that document. Resolve ambiguous identity or conflicting intent
before accepting it.

## Review an existing document

When entering the shared flow's Ethos step with an existing `ETHOS.md`, use
`ethos-orchestrate` to review it with the user before moving on. This includes a
new shared flow that loads older completed state: an earlier confirmation or
matching digest does not
establish that this is still the correct document or that its intent is current.
Reuse the content, source findings, and prior answers to make the review brief.

Before displaying the results checklist, use `journey_state.py update` to set
`ethos.stage` to `review`, save the question as `pending_question`, and set
`next_handoff` to `ethos-orchestrate`. Keep the Ethos item unchecked and current
when prerequisites are complete. This pending review retains the previous content
binding and provenance as history, along with agreed scope and completed flows.
Pass the orchestrator the existing content and
ask it to confirm both document identity and current intended behavior. Use this
complete **Next Steps** section after the results table and any Recommendations:

```markdown
### Next Steps

[Shared three-item checklist from the core, with saved progress]

I found [ETHOS.md](path-to-the-document).

[Brief summary of this agent's intended behavior, boundaries, and success criteria]

Is this the right ETHOS.md for [agent], and does it accurately describe how the agent should behave today?
```

Replace placeholders with the actual file link, agent, and a summary grounded in
the document. Wait for the user's answer before marking Ethos complete or
offering to continue with evaluations. Keep uncertainty in `review` and return to
the orchestrator for clarification. A wrong document or confirmed change in intent
starts the appropriate `explore`, `interview`, or `draft` stage, invalidating the
affected agreements before dependent work resumes. Do not describe an old
confirmation as approval for the current flow.

Within the current flow, resume an unanswered review instead of starting
it again. Once the user has explicitly confirmed the correct saved document and
its current intent, reuse that reply for unchanged content through the completion
pause, goal switches, and downstream work. Resuming that downstream work in a new
conversation does not start a new shared flow or reopen this review; retain its
accepted scope, pending question, and handoff. Do not reopen review on every state
read or repeat the interview. Changes to the agent or document reopen the affected review.

After an agent change, retained findings, answers, and draft content are historical
context for reassessment. Use the referenced agent-history snapshot to distinguish
the previous identity and approvals; pass only relevant Ethos context to the
orchestrator. Confirm applicability to the selected agent before reusing it, and
do not treat the former agent's document or draft approval as current permission.

## Call and return

Load shared journey state before each call, including a fresh chat or a switch
between create, audit, and expand. Eval Author owns the selected journey,
checklist, state layout, and return routing; Ethos needs none of those details.
Pass the selected `repository`, `agent`, root `ethos_path`, and only the Ethos
context needed to continue:

- `source_findings` from `ethos.source_findings`: records with `path` and `summary`.
- `prior_answers` from `ethos.prior_answers`: records with `topic`, `question`,
  `answer`, and `source` identifying the actual reply or other provenance.
- Describe the unfinished Ethos step and remaining questions from saved state.
  Include prior user confirmation only for the exact content it established.
  Label an earlier flow's confirmation as history; explicitly request
  the pending review of the document and its current intent instead of treating
  that history as completion.
- Recover the exact draft contents and any `draft_source` confirmation
  provenance. Map the stored reference into ordinary conversation context; the
  Ethos skills do not need to read or understand Eval Author's state file.

The supplied context establishes an ongoing Ethos session. Continue its earliest
unfinished stage rather than requesting another standalone opening.

A `draft` is null or a reference with `path` relative to the repository under
`.eval-author/.working/` and `digest` for its exact bytes as 64 lowercase SHA-256
hexadecimal characters. Read and pass that exact draft content on resume; a
digest or reference without recoverable content is insufficient. The canonical
workflow owns exploration, revision,
and writing, preserving custom sections and creation metadata.

At each pause, persist the returned source findings, complete prior-answer list,
exact draft reference, stage, and unanswered questions through `update` before
handing back the conversation. These lists replace their stored values, so retain
previous entries while adding new findings or answers. Eval Author stages the
canonical workflow’s returned draft content under `.eval-author/.working/` and
records its path and digest; the orchestrator does not own that staging artifact.
It is proposed Ethos content, not another progress
or planning document. Keep canonical `ETHOS.md` unchanged until its draft is
confirmed. Record the actual confirmation reply in `draft_source` and set
`draft_confirmed` only for the matching recoverable draft. The `write` stage
requires that confirmation; inferred intent or a checksum alone does not supply
approval. If the draft changes or disappears, retain findings and answers but
reopen draft review. Reuse them rather than restarting the interview.

Ask Ethos to report the document path, actual validation outcome, unresolved
questions, and what was confirmed or remains unfinished. Retain new findings,
user replies with provenance, and draft contents from that conversation. Eval
Author maps these facts into its own `stage`, `validation`, `remaining_questions`,
and confirmation fields; Ethos does not implement that state schema.

Check that the document path resolves to this repository's root `ETHOS.md`.
Eval Author computes `confirmed_digest` as SHA-256 over the exact saved bytes,
encoded as 64 lowercase hexadecimal characters without a prefix. Before reusing
the current flow's confirmation, compare the current bytes with that
digest and retain its user-reply provenance. Changed contents reopen the affected
review or intent step. Persist unfinished stages and answers through
`journey_state.py update`. For completion, require successful returned validation
and explicit saved-content confirmation, then use `confirm-ethos` with the
computed digest, the user's confirmation reference as
`--source`, and the validator result's provenance as `--validation`. Preserve
the validation details in a linked working artifact or the conversation; do
not pass a structured validation object as if it were the helper's provenance
string. Failed validation remains an unfinished review, even if the file has
a digest. Use only the
confirmed exact content for downstream cases or audit items. Validation alone
is not intent confirmation. Present the returned path and unresolved gaps, then follow the
shared [completion pause](shared-prerequisites.md#pause-after-ethos-confirmation).
End the turn after Next Steps, the updated checklist, and the continuation question.
Do not begin source selection or delegate evaluation work until the user continues.
If Blockers remain, use the blocked response instead.

## Recovery

If an Ethos skill or required resource is missing, unreadable, or has a broken
symlink, identify the dependency under **Blockers** and link the
[canonical installation guide](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/README.md#how-to-use).
Provide this command from the agent repository root, substituting the current
assistant's identifier for `claude-code` and adding `--global` when repairing
a global installation:

```bash
npx skills@latest add nvidia/skills \
  --skill ethos-orchestrate \
  --skill ethos-explore \
  --skill ethos-write \
  --agent claude-code --yes
```

Do not install during prerequisite checks or substitute another authoring
workflow. After repair, recheck the actual skill files and required resources;
a catalog listing alone does not resolve the blocker.

Preserve the current flow's confirmation of the exact document or draft, its
destination, and intended behavior. If validation is still pending, retain the
actual reply in `ethos.prior_answers`, including the reviewed path, content digest,
and agent identity in its `source`; preserve any draft and `draft_source` too.
After repair, pass that confirmation to the orchestrator and resume the unfinished
validation or writing step. Ask again only if the agent, destination, content, or
material validation findings change. Complete through `confirm-ethos` only after
successful validation. If Ethos was already complete, update only the skill
prerequisite and blocker; skill availability alone does not reset Ethos or scope.

If writing fails, preserve the reviewed draft and answers under
`.eval-author/.working/` when writable, otherwise in conversation. Keep the actual
validation or filesystem limitation explicit.
