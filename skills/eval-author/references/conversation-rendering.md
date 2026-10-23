<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Conversation rendering

Use [conversation_renderer.py](../scripts/conversation_renderer.py) as the shared
presentation entry point for Eval Author conversations. Its pure `render(payload)`
function returns Markdown; the CLI prints the same Markdown. Identical inputs
produce byte-identical messages without clocks, randomness, repository reads,
or writes. This guarantee concerns rendered messages. It does not make workflow
execution, model decisions, tool results, or evaluations deterministic.

Present rendered Markdown unchanged, with no additional opening, duplicate
checklist, or appended next-step invitation. Canonical shared journey and
checklist labels, status labels, and generic headings live in
[conversation_templates.py](../scripts/conversation_templates.py). Flow-specific
copy belongs to that flow's template module, not a second prose template.

The renderer presents decisions; it never makes them. It does not inspect the
repository, load or save journey state, probe prerequisites, validate files,
delegate work, choose sources, run evaluations, or grant approval. Reading its
output cannot establish readiness, Ethos confirmation, permission to continue,
or authorization to spend. Persist actual user replies and observed findings
through the existing state helper before rendering stateful responses.

## Input envelope

Every payload selects a supported `stage`. Stateless inputs supply the journey
and factual context allowed by that stage. Stateful inputs supply `stage`, the
complete `state` returned by the state helper, its exact `revision`, and optional
`facts` allowed by the flow. `revision` must equal `state.revision`. State
controls the journey, progress, blockers, confirmations, and pending decision;
facts supply bounded findings and links, not alternate authority. Never
construct a success state or maintain a separate saved checklist for display.

If prerequisite state cannot be initialized, read, or saved, use onboarding's
[nonpersistent failure response](conversations/onboarding.md#when-state-is-unavailable).
It reports observed findings without a state snapshot or any completion claim.

The caller owns source inspection, evidence provenance, freshness, and artifact
reconciliation. Follow the selected flow's rules for obtaining the state
snapshot; the renderer does not inspect files or digests to reconcile it. An
old in-memory state is insufficient where fresh reconciliation is required.
Invalid input is an error to resolve through the actual state or missing facts,
not a reason to hand-build a success response. Rendering never updates state.

Pass a complete JSON payload through stdin or an explicitly permitted input
file, replacing `<skill_dir>` with the core skill directory:

```bash
python3 -B <skill_dir>/scripts/conversation_renderer.py --input -
python3 -B <skill_dir>/scripts/conversation_renderer.py --input /absolute/path/conversation.json
```

The CLI reads only its supplied input or stdin and may load its own source
dependencies. Use `-B` to prevent bytecode writes. Flow-specific no-input modes
and file-access restrictions still apply; the CLI does not authorize creating
or reading a payload file.

## Facts and links

Findings and questions are plain text, escaped by the renderer. Supply links in
separate structured fields permitted by the flow, not embedded Markdown in
prose. Targets must be caller-verified absolute local paths or HTTP(S) URLs;
the renderer formats them without resolving or inspecting them. Preserve the
distinction between observed verification and user confirmation. Missing facts
never justify a fabricated success claim.

## Supported flows and extensions

[Onboarding rendering](conversations/onboarding.md) defines orientation,
prerequisites, and Ethos stages for creation, audit, and expansion, including
preview permissions, row facts, blocker selection, and final-response ordering.
Use it alongside [Shared prerequisites and Ethos](shared-prerequisites.md).
Shared rendering guidance does not change those workflow gates.

[First evaluation rendering](conversations/first-eval.md) continues creation
with optional traces, design and first-task review, validation, execution
settings, results, and the optional audit handoff. Its stages require reconciled
saved state and use the `first_eval_` prefix; they have no stateless preview.

[conversation_renderer.py](../scripts/conversation_renderer.py) dispatches through
an explicit [journey/stage registry](../scripts/conversation_flows/__init__.py)
to implemented flow renderers. Duplicate registrations are rejected. Implemented
flows are [onboarding.py](../scripts/conversation_flows/onboarding.py) and
[first_eval.py](../scripts/conversation_flows/first_eval.py), with copy in their
respective `_templates.py` modules.
Shared formatting and escaping live in
[conversation_formatting.py](../scripts/conversation_formatting.py).

When implementing another conversation flow, add its real renderer and templates,
register its supported `JOURNEYS`, `STAGES`, and stateless `PREVIEWS`, and document
its input and workflow contract. Each flow's `render(payload)` validates its
stage-specific fields and state gates before rendering. Reuse shared labels and
formatting; keep flow-specific copy in its own
template module. Do not create empty future-flow stubs, duplicate templates,
or move workflow execution or state mutation into rendering. Identify journeys
and stages by descriptive names, never journey numbers.
