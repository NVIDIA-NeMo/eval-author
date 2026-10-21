<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos confirmation adapter

This adapter connects Eval Author to
[`ethos-orchestrate`](../../../team-skills/ethos/ethos-orchestrate/SKILL.md), the
single owner of Ethos exploration, draft confirmation, validation, and writing.
Do not implement another interview or local authoring fallback in Eval Author.

Explain that Ethos records what the agent should do, what it should avoid, and
what a good result looks like. Reuse the Journey 0 orientation. Journey 0
confirms repository-root `ETHOS.md`. Preserve existing alternate-path
documents; do not silently copy, delete, or replace them. Resolve the intended
root document with the user if needed. Explicitly scoped `--ethos` utilities can
use their supplied path without claiming Journey 0 confirmed that document.
Resolve ambiguous identity or conflicting intent before accepting it.

## Call and return

Load shared journey state before each call, including a fresh chat or a switch
between create, audit, and expand. Pass `caller=eval-author`, selected `journey`,
`repository`, `agent`, `ethos_path`, `orientation_shown=true`, and:

- `source_findings` from `ethos.source_findings`: records with `path` and `summary`.
- `prior_answers` from `ethos.prior_answers`: records with `topic`, `question`,
  `answer`, and `source` identifying the actual reply or other provenance.
- `progress` from the saved Ethos fields: `stage`, `draft_confirmed`,
  `confirmed_digest`, and `remaining_questions`. Include the recovered draft
  contents and its `draft_source` confirmation provenance in the handoff context.

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

The completion result carries `path`, `validation`, `remaining_questions`,
`confirmed_digest`, and `stage`. The continuation context carries source
findings, user replies with provenance, and the updated draft content and
confirmation state; the caller maps those into the shared fields above. The
digest is the SHA-256 of the exact saved bytes as 64 lowercase hexadecimal
characters without a prefix. Check that
`path` resolves to this repository's root `ETHOS.md`. Persist unfinished stages
and answers through `journey_state.py update`. For completion, require a
successful returned validation and explicit saved-content confirmation, then
use `confirm-ethos` with that digest, the user's confirmation reference as
`--source`, and the validator result's provenance as `--validation`. Preserve
the validation details in a linked working artifact or the conversation; do
not pass a structured validation object as if it were the helper's provenance
string. Failed validation remains an unfinished review, even if the file has
a digest. Use only the
confirmed exact content for downstream cases or audit items. Validation alone
is not intent confirmation; unchanged previously accepted content needs no
repeated approval. Present the returned path and unresolved gaps, then continue
the selected journey's next action without another generic transition gate.

## Recovery

If the canonical Ethos skills are unavailable, identify the missing dependency
and provide their installation location. Retain source findings and answers in
shared state or conversation and continue independent work; do not invent a
fallback authoring workflow. If writing fails, preserve the reviewed draft and
answers under `.eval-author/.working/` when writable, otherwise in conversation.
Keep the actual validation or filesystem limitation explicit.
