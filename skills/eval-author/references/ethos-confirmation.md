<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos confirmation adapter

This adapter connects Eval Author to
[`ethos-orchestrate`](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/SKILL.md), the
single owner of Ethos exploration, draft confirmation, validation, and writing.
Do not implement another interview or local authoring fallback in Eval Author.

## Required handoff capabilities

Pass the absolute path selected in `ethos.path` as the explicit destination to
`ethos-orchestrate`. The installed skills must preserve that exact path and
uppercase `ETHOS.md` filename, including in repositories with several agents.
They must not rename it to `ethos.md` or choose a different directory.

Check the installed skills' location and writing instructions before delegation.
If they rename the file, choose another path, or otherwise override the
supplied destination, that version is incompatible with this contract.
Report the actual conflicting skill and instructions under
**Blockers**, preserving existing documents and progress. Resolve the dependency
with a catalog version that honors the supplied path; verify its installed
instructions before clearing the blocker. Reinstalling the same incompatible
version does not resolve it.

The orchestrator and writer must also support continuation from supplied
context: reuse the selected operation, destination, answers, exact draft, and
confirmation provenance; review the draft before writing; and validate an
unchanged saved document without rewriting it. Report missing continuation
support as an unfinished dependency. Carry the user's approval through an
unchanged save and successful validation when the agent, destination, exact
content, and material findings still match; do not request the same approval
again.

## Enter the Ethos step

Enter this adapter through the shared procedure's
[results and Ethos transition](shared-prerequisites.md#present-results-and-the-next-step).
That procedure owns prerequisite ordering, state-failure and blocker handling,
artifact reconciliation, and final response selection. This adapter owns the
Ethos work and confirmation rules below. Existing evaluations describe coverage;
they do not establish intended behavior.

The shared prerequisite flow confirms the selected `ETHOS.md`, which must be a
regular file, not a symlink, inside the repository. Store its canonical
repository-relative path in `ethos.path`; repository-root `ETHOS.md` remains the
default for older records and new documents without an explicit destination.
An existing agent-local document stays in place. Do not require a move, copy, or
root symlink. Resolve ambiguous identity or conflicting intent with the user
before selecting a document; never silently choose another agent's document.

After entering the Ethos step, select a different destination with the normal
revision-checked `update` interface, setting `ethos.path` and `ethos.stage: review`.
During prerequisites, `--state-only` can mark review pending but cannot select a
different document or inspect its contents. A path change
reopens affected approvals even when its bytes match the previous document.
The current selection and decision provenance stay in shared state; do not
create a history copy of the former state. If the selected file changes
or disappears, preserve that selection and reopen review without falling back to
another file.

## Review an existing document

An eligible [returning audit](shared-prerequisites.md#return-to-an-audit) continues
the saved workflow, including in a new chat. Reconcile and reuse its confirmed
document under that procedure; do not mark it for review just because the goal
changed. Changed identity, intent, content, or an unresolved review still requires
the affected Ethos work below.

When entering the shared flow's Ethos step with an existing `ETHOS.md`, use
`ethos-orchestrate` to review it with the user before moving on. This includes a
new shared flow that loads older completed state: an earlier confirmation or
matching digest does not
establish that this is still the correct document or that its intent is current.
Reuse the content, source findings, and prior answers to make the review brief.

Before completing the prerequisite pass and inspecting target artifacts, use
`journey_state.py --repo <repository> --state-only update` to mark a required
review pending when supplied context or saved metadata identifies an existing
document: set `ethos.stage` to `review`, save the pending action or question, and
set `next_handoff` to `ethos-orchestrate`. This bookkeeping does not establish file
presence or current contents. When no document is known, retain discovery or
creation as the pending action without looking for the file yet. Keep Ethos
unchecked and current when prerequisites are complete; preserve prior content
bindings, provenance, scope, and completed flows as history.

At the shared procedure's Ethos transition, invoke the orchestrator to locate
and read the document and confirm its identity and current intended behavior.
Return its actual file link and a grounded summary of intended behavior,
boundaries, and success criteria through the
[Ethos facts contract](conversations/onboarding.md#ethos-and-draft-facts).
The shared procedure selects the final response; do not add a second review
heading, checklist, or question.

Wait for the user's answer before marking Ethos complete or
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
context for reassessment. Use the current source findings and saved context to
reassess applicability; pass only relevant Ethos context to the orchestrator.
Confirm applicability to the selected agent before reusing it, and
do not treat the former agent's document or draft approval as current permission.

## Call and return

Follow the [shared procedure](shared-prerequisites.md#present-results-and-the-next-step)
for state loading before each call. Eval Author owns the selected journey,
checklist, state layout, and return routing; Ethos needs none of those details.
Pass the selected `repository`, `agent`, `ethos_path` as the explicit
destination, the requested operation, and only the Ethos context needed to continue:

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
For a saved draft in stage `draft` or `write`, reload reconciled state and supply
`facts.draft` to the conversation renderer with its exact absolute link and
recorded digest. The renderer shows this reviewable draft before the pending
question. Recover and verify the canonical workflow's exact content before
supplying it; rendering performs no file inspection and provides no approval.
It is proposed Ethos content, not another progress or planning document. Keep canonical `ETHOS.md` unchanged until its draft is
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

Check that the document path matches `state.ethos.path` inside this repository.
Eval Author computes `confirmed_digest` as SHA-256 over the exact saved bytes,
encoded as 64 lowercase hexadecimal characters without a prefix. Before reusing
the current flow's confirmation, compare the current bytes with that
digest and retain its user-reply provenance. Changed contents reopen the affected
review or intent step. Persist unfinished stages and answers through
`journey_state.py update`. For completion, require successful returned validation
and explicit confirmation of the exact saved content, reusing approval of an
unchanged draft when applicable, then use `confirm-ethos` with `--ethos` set to
the selected repository-relative path, the latest `--expected-revision`, the
computed digest, the user's confirmation reference as
`--source`, and the validator result's provenance as `--validation`. Preserve
the validation details in a linked working artifact or the conversation; do
not pass a structured validation object as if it were the helper's provenance
string. An explicit `--ethos` must match the saved selection; omitting it retains
the legacy repository-root `ETHOS.md` default. Failed validation remains an
unfinished review, even if the file has a digest. A supported path does not waive
schema or heading validation. Use only the
confirmed exact content for downstream cases or audit items. Validation alone
is not intent confirmation. Present the returned path and unresolved gaps, then follow the
shared [completion pause](shared-prerequisites.md#pause-after-ethos-confirmation).
For a new shared flow, end the turn after the rendered `confirmed` response with its pending decision.
An already-requested [returning audit](shared-prerequisites.md#return-to-an-audit)
uses that procedure's continuation instead.
For fresh creation without a saved design or source choice, the shared pause
includes the source question instead of a readiness question. Wait for its
answer before planning or inspecting traces; do not ask the same choice again
in a later turn. Other flows retain their continuation. Do not delegate
evaluation work before the applicable reply.
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
