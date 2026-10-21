<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Entry routing conversational acceptance scenarios

Use these scenarios to review Eval Author's first response to onboarding and
expansion requests. This is a manual protocol, not an execution record or a
claim of passing model evaluation. Use fresh, isolated synthetic workspaces
with the sibling skills available and no customer data or provider credentials.
Retain replies, channels, tool calls, and delegation logs. Judge the selected
workflow and actions. The fixed welcome and checklist must also match the core
skill's exact wording.

The [offline scope checker](../../docs/determinism-checks.md#workflow-scope) makes a subset
of these route and effect expectations executable against normalized captures.
Its synthetic tests are not a record of passing conversational replay.

## 1. Shared entry for each journey and runtime

Request first evals, an audit, and expansion in separate synthetic Gym and Harbor
repositories. The first orientation has the same three checklist items: confirm
the agent/setup, confirm Ethos, then the selected build/audit/improve item. If the
goal is unknown, ask whether to create, audit, or improve; explicit inventory
remains a scoped option.
The first turn contains only the fixed welcome and exact checklist for a known
goal, or the fixed welcome, goal question, and three choices for an unknown goal.
It ends there, even when repository or Ethos paths are supplied. Inspect the
tool trace: only the invoked skill and shared core may be loaded before this
reply. Repository/state reads, Ethos validation, prerequisite checks or questions,
helper commands, writes, and delegation all fail this opening checkpoint.
Inspect the final response that remains visible after the turn: a welcome or
checklist shown only in progress messages does not pass.

Reply to continue, or select a goal. Check that the selected checklist is shown
and prerequisite work can now begin without repeating the opening-only pause.
Only the selected third-item label, checkmarks, and current-step marker vary.
On resume, keep the same wording with saved progress. When naming the agent
and setup on subsequent turns, distinguish explicit requests,
repository findings, prior confirmations, and defaults. Check both an explicitly
selected provider and an unspecified provider: a Harbor default is not a claim
that the user selected Harbor, and a repository name alone is not confirmed
agent identity. Explicit choices do not need to be asked again.
Do not probe runtimes, install, read unselected traces, or execute agents in
the shared prerequisite flow. Confirm prerequisites together and label them user-confirmed.

## 2. Expansion does not imply an audit

Request “help me onboard to expand my evals” with no audit findings. Preserve the
expansion goal and run the shared entry. Do not route automatically to audit or
fabricate a coverage report. Starter-suite expansion resumes first-eval scope
planning; provided audit findings support proposals through task-create. When no
supported authoring flow matches the supplied suite, save the pending handoff
and explain the missing capability.

## 3. Narrow operations keep their scope

Request only inventory, readiness, audit validation, recommendations from a
supplied report, or trace inspection. Use the existing scoped operation without
unrelated prerequisite interviews or a full checklist. Readiness retains real
runtime verification; schema validation does not claim measured coverage.
Explanation-only requests do not start work. A trace source must still be selected
before searches or reads. Explicit source selections are reused.

## 4. Resume and switch journeys

Save accepted prerequisites and Ethos, a pending scope question, and evidence
references in `.eval-author/.working/journey-state.json`. Resume from a fresh
chat and switch from create to audit or improve. Reuse unchanged common decisions;
keep journey-specific source selections separate. A supplied source path does
not imply consent to use unrelated traces. Changed Ethos reopens its confirmation
and dependent findings only; changed setup reopens affected setup work.

## 5. Legacy state is imported once

Supply old first-eval and audit progress with explicit decisions, incomplete
stages, and a pending question. Import with provenance, preserve disagreements,
and never infer confirmation from file presence. New writes use shared state;
no new `first-eval.md`, `audit-progress.md`, or separate intent notes appear.
Malformed state does not silently restart or imply approval. A read-only workspace
continues with conversation state and explains the fresh-chat resume limitation.

## 6. Stable tasks and canonical Ethos

Missing or revised Ethos goes through `ethos-orchestrate`. Confirm draft content
before the canonical write; unchanged confirmed content stays unchanged. The
handoff returns a path, validation, digest, remaining questions, and stage.
New task files live at `.eval-author/tasks/<slug>/` from the start. Acceptance
changes status and explicit suite membership without promotion copies. Internal
plans, drafts, locks, and temporary files live under `.eval-author/.working/`.
