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

Request first evals, an audit, and expansion in separate synthetic NeMo Gym and Harbor
repositories. For full audits, unless testing unknown or missing traces, include
the user's explicit statement that traces from completed evaluation runs exist;
leave their source and location unselected. The first orientation has the same
three checklist items: confirm
the agent/setup, confirm Ethos, then the selected build/audit/improve item. If the
goal is unknown, ask whether to create, audit, or improve; explicit inventory
remains a scoped option.
The first turn contains only the fixed welcome, exact checklist, and “Ready to
get started?” for a known goal. For an unknown goal, use the shorter welcome
“Welcome to Eval Author! I’ll help you evaluate your agent against the behavior you expect.”
followed by the goal question and three choices. Reserve “Here’s what we’ll work
through:” for introductions followed by a checklist.
It ends there, even when repository or Ethos paths are supplied. Inspect the
tool trace: only the invoked skill and shared core may be loaded before this
reply. Repository/state reads, Ethos validation, prerequisite checks or questions,
helper commands, writes, and delegation all fail this opening checkpoint.
Inspect the final response that remains visible after the turn: a welcome or
checklist shown only in progress messages does not pass.

If the goal was initially unclear, choose each goal in separate runs. That reply
must produce the core's exact sentence for that goal, followed by the matching
three-item checklist and **Ready to get started?** as the final visible response,
then end the turn. For creation, the introduction is “We’ll build your first
evaluations. Here’s what we’ll work through:” above the checklist. Use the
corresponding fixed sentence for audit and improvements. Do not repeat the welcome or
show the prerequisite table yet. No repository/state reads, probes, state writes,
or delegation may occur during this goal-selection reply. The first checklist
item is current; no items are complete.

Reply to **Ready to get started?**. Only now can prerequisite work begin. In the
results response, the selected checklist
appears under **Next Steps**, after the table and any Recommendations, only when
no Blockers remain. A blocked response ends at Blockers without a checklist or Ethos guidance.
Only the selected third-item label, checkmarks, and current-step marker vary.
Creation and improvement explicitly include running and reviewing in their
third item; audit keeps its coverage wording. The third item remains incomplete
after case files are built if required runs or result review are still pending.
On resume, keep the same wording with saved progress. When naming the agent
and setup on subsequent turns, distinguish explicit requests,
repository findings, prior confirmations, and defaults. Check both an explicitly
selected provider and an unspecified provider: a Harbor default is not a claim
that the user selected Harbor, and a repository name alone is not confirmed
agent identity. Explicit choices do not need to be asked again.
After continuation, show the fixed prerequisite table before repository/state
reads or commands and inspect all accessible prerequisites in the same turn.
For creation and expansion, use the existing runtime locator for the selected
setup. A missing or incompatible selected runtime blocks onboarding before Ethos
or case planning. An inconclusive runtime check remains unresolved. Check Docker
only when the chosen environment requires it. For audit,
identify the existing suite format and check Python 3.11 or newer with PyYAML and
jsonschema for audit tooling; skip evaluation runner, Docker, live service, and
agent credential probes. Confirm that traces from completed evaluation runs exist
before proceeding to Ethos. Unknown or missing availability leaves setup incomplete.
Check trace conversion and parser requirements only
after the audit selects its evidence. Ask only about unresolved findings after
the pass. Do not install, read unselected traces, or execute agents. Distinguish
observed checks from user-confirmed answers and from end-to-end suite readiness.

### Prerequisite inspection variants

Use the fixed eight-row table for creation and expansion and the fixed six-row
table for audit, with both NeMo Gym and Harbor. The audit rows are **Agent and
repository**, **Evaluation framework**, **Traces from completed evaluation runs**,
**Required skills**, **File and command access**, and **Audit tooling**, in that
order. Within each goal's template, only status and findings cells vary, including
on resume. Inspect the
progress message before the first check and the results table in the final reply.

- All required tools are available: complete the accessible checks and report
  evidence without asking whether Python, the runtime, or Docker is installed
- An authoring runtime exists outside the default project environment: use the
  locator's bounded declared/PATH/tool locations before asking for another location
- The selected authoring runtime is missing or incompatible: finish independent
  checks, show **Missing** with the evidence and required setup change, then end
  with **Blockers**. Keep setup incomplete, preserve progress, and omit Next Steps,
  the checklist, and Ethos guidance. Do not install or switch providers
- The selected runtime probe is denied, times out, or cannot establish compatibility:
  report **Unknown** with the actual reason, finish independent checks, and ask only
  for the information or access needed to resolve it under **Blockers**. Do not
  claim that the runtime is absent or proceed to Ethos
- Resume before the runtime issue is resolved: retain the blocker and evidence.
  Once the selected runtime is verified as supported, clear the resolved blocker
  and resume the earliest incomplete step without repeating the opening
- Docker is unavailable and the selected authoring environment requires it:
  finish the other checks, explain what Docker blocks, and preserve independent planning
- Commands or repository access are denied: record the limitation as unknown,
  check the remaining accessible rows, then ask for only the unresolved access
- A saved pending question asks whether Python, Harbor, and Docker are installed:
  show the table and inspect only requirements applicable to the selected goal
  before deciding whether a question remains
- A creation or expansion environment needs no Docker, services, or live
  credentials yet: retain its eight rows and explain the applicable or deferred requirements
- Audit local NeMo Gym or Harbor definitions with Python 3.11 or newer, PyYAML, and
  jsonschema available, but neither evaluation runner, Docker, nor agent
  credentials, and with confirmed trace availability: identify the suite format
  without probing those execution dependencies and continue to Ethos confirmation
- In that audit, **Audit tooling** reports Python and the audit-library checks;
  **Evaluation environment**, **Required services and data**, and **Agent
  connection and credentials** do not appear in the initial or results table
- The audit trace prerequisite is visible before checks and in the results,
  explaining that measurement requires traces from evaluations that have already
  run; unknown availability stays **Unknown**, an explicit user statement that
  traces exist is **Confirmed by you**, and confirmed absence is **Missing**
- Unknown or missing trace availability blocks full audit onboarding: finish
  accessible checks and show **Blockers**; when availability is unknown, ask
  whether traces from completed evaluation runs exist. Omit Next Steps, the
  checklist, and Ethos guidance
- A user reply that traces exist records **Confirmed by you** and allows Ethos
  to proceed when other prerequisites pass; it does not select sources or locations
- Confirmed absence stays **Missing** and keeps setup incomplete; do not claim
  prerequisites passed, label traces optional or **Not needed now**, ask for
  source selection during onboarding, search unselected sources, or run evaluations
  to produce traces. Reuse the absence answer instead of asking it again
- Resume an older audit whose saved Python verification came from an authoring
  runtime probe: unless its evidence also establishes the current audit tooling,
  reopen and check PyYAML/jsonschema availability before marking setup complete;
  obsolete runner or Docker failures must not block this audit
- Resume an older audit with `data: not_required` or trace availability recorded
  only as unknown in scope: this does not establish readiness. Reassess applicable
  trace-availability evidence, preserve provenance, and keep setup blocked until
  availability is confirmed
- After the audit selects local ATIF evidence, check the Harbor trajectory
  parser for measurement, including when the evidence was converted from NeMo Gym;
  do not check a NeMo Gym runner or launch Harbor jobs
- The required conversion tool or parser is absent for selected audit evidence:
  explain the blocked conversion or measurement, retain the selection, and
  preserve Ethos and agreed independent specification work without installing
  dependencies or claiming measured coverage

Retain the command evidence and shared state under `.eval-author/.working/`.
Check that observed results save as `verified`, user-only answers as `confirmed`,
and a missing or timed-out check is never promoted to successful execution.

### Results and the transition to Ethos

Check the response after the results table against the shared reference's exact
headings and introductory sentences. Keep the section order across goals, NeMo Gym,
and Harbor; omit sections only under the template's stated conditions.

- Optional assistant skills are absent: show **Recommendations**, explicitly say
  suggestions are optional and not required to continue, then show **Next Steps**
  and the shared progress checklist before the Ethos explanation or question
- The selected authoring runtime is missing, incompatible, or unresolved: show
  **Blockers**, preserve progress, and omit Next Steps, the checklist, and Ethos
  guidance. An existing or historically confirmed Ethos does not bypass this check
- Docker or execution credentials are missing but Ethos can proceed:
  keep their later requirements in the table, without classifying them as
  optional or adding them to **Blockers**; continue below the checklist in **Next Steps**
- Required Ethos skills or access needed for the next Ethos action are missing:
  show **Blockers** with the affected action, remedy, and focused question; omit
  Next Steps, the checklist, and all Ethos guidance, even if Recommendations are present
- Audit trace availability is unknown or missing: show the same blocked response
  and keep prerequisites incomplete, even when every tooling and access check passed
- No suggestions or blockers remain: omit those empty sections and go directly
  from the table to **Next Steps** and the checklist
- An existing `ETHOS.md` needs current confirmation: show the complete review
  section, including the literal **Next Steps** heading before the checklist;
  a checklist directly after the results table without that heading fails
- `ETHOS.md` is missing but the Ethos skills are available: proceed below the
  checklist; the absent document is not a blocker
- Resume an Ethos question: use **Next Steps** and saved checklist progress only
  if no Blockers remain, without restarting onboarding or imposing prerequisite
  tables on scoped utilities

An unblocked results response contains one checklist, after the table and any
Recommendations. A blocked response contains none. Neither has a separate Ethos heading. Preserve the
fresh welcome/checklist/call-to-action response before any prerequisite work.

### Ethos completion and the pause before evaluation work

Finish the canonical saved-content validation and review, then confirm the saved
Ethos. Repeat for create, audit, and expand with NeMo Gym and Harbor. Inspect the final
visible response: it links `ETHOS.md`, reports confirmation, shows **Next Steps**
and the shared progress checklist, and ends with the selected goal's exact
readiness/continuation sentence. Completed prerequisites and Ethos are checked;
the selected third item remains unchecked and current.

No source-selection or case-planning question, new suite or trace inspection,
runtime probe, scaffolding, or downstream delegation occurs in this turn.
The saved state retains the exact confirmed Ethos digest, pending continuation
question, and intended handoff. A draft-only approval or unreviewed saved file
must not trigger this completion response. Blockers suppress Next Steps,
the checklist, and readiness language as elsewhere in the shared flow.

Reply “Continue” and verify that source/scope selection begins from the saved
context without repeating the readiness pause or Ethos interview. Also resume
from a fresh chat while that continuation question is pending, and verify that
an interrupted pause cannot silently start evaluation work.

## 2. Expansion does not imply an audit

Request “help me onboard to expand my evals” with no audit findings. Preserve the
expansion goal and run the shared entry. Do not route automatically to audit or
fabricate a coverage report. Starter-suite expansion resumes first-eval scope
planning; provided audit findings support proposals through task-create. When no
supported authoring flow matches the supplied suite, save the pending handoff
and explain the missing capability.

### Existing suite uses an unsupported format

Request improvements to a synthetic NAT or DeepEval suite with an existing
`ETHOS.md` and saved Harbor authoring state. After the opening and continuation,
show the eight-row table and complete independent checks. The framework row must
identify the incompatible format; the saved Harbor setup does not establish
compatibility or justify provider-specific probes yet.

Under **Blockers**, say “Eval Author only supports NeMo Gym or Harbor formats.
Convert your existing evaluations to one of those formats before we continue.”
Keep setup incomplete and omit Next Steps, the checklist, and Ethos guidance.
The blocker must appear before Ethos confirmation, including when an earlier
flow already confirmed that document.

- Preserve progress and pause for the user to convert the suite; no conversion
  or unsupported-format authoring occurs
- Do not offer separate NeMo Gym or Harbor evaluations as a workaround
- Resume before conversion: retain the setup blocker and required action; do not
  enter Ethos, probe a default provider, or mark prerequisites complete
- Return with a converted supported suite: verify its format from supplied
  context, clear the resolved blocker, and resume the earliest incomplete step
- Switch to audit: remove the improvement-format blocker's effect and apply
  audit prerequisites while preserving its findings and the existing suite

Keep explicit audit operations and supported trace conversion outside this
authoring-format requirement. No new adapter or general expansion implementation
is implied by converting to a supported format.

## 3. Narrow operations keep their scope

Request only inventory, explicit case planning, readiness, audit validation, recommendations from a
supplied report, or trace inspection. Use the existing scoped operation without
unrelated prerequisite interviews or a full checklist. Readiness retains real
runtime verification; schema validation does not claim measured coverage.
Explicit inventory and planning-only requests do not acquire the full authoring
runtime gate or authorize scaffolding and execution.
An explicit specification-only request can use confirmed Ethos without completed-run
traces; it does not complete full audit onboarding or establish measured coverage.
Explanation-only requests do not start work. A trace source must still be selected
before searches or reads. Explicit source selections are reused.

### Existing Ethos requires current confirmation

For each new shared entry with NeMo Gym and Harbor, supply a valid root `ETHOS.md` and
saved state with a matching confirmed digest from an earlier flow. Complete the
opening and prerequisite checks. Under **Next Steps**, Ethos must remain
unchecked and current when setup is complete. The response links the actual file,
briefly summarizes its intended behavior, and asks whether it is the right
document for the selected agent and still accurate. It must not reuse historical
approval, mark Ethos complete, or show the evaluation readiness question yet.

- Confirm it is correct and current: validate and record the exact saved content
  with this reply's provenance, then show the completion pause and wait
- Say it belongs to another agent or needs changes: keep review pending and let
  `ethos-orchestrate` resolve the identity or revise intent before confirmation
- Ask a side question or say you are unsure: keep Ethos incomplete; preserve the
  review question without starting evaluations
- Resume after the question: retain the pending review; after answering it in
  this flow, continue or switch goals without asking for the same review again

Keep the existing file unchanged when accepted without edits. Retain prior
answers and source findings instead of restarting the interview. Inspect shared
state as well as the visible checklist; old `complete` state must not bypass review.

## 4. Resume and switch journeys

Save accepted prerequisites and Ethos, a pending scope question, and evidence
references in `.eval-author/.working/journey-state.json`. Resume the same goal
from a fresh chat with its accepted scope, current Ethos confirmation, pending
question, and handoff; do not restart shared entry or reopen unchanged review.
Then switch between audit and creation or expansion. Retain shared confirmation,
saved scopes, and evidence, but select the new goal's question and handoff;
keep journey-specific source selections separate. A supplied source path does
not imply consent to use unrelated traces. Changed Ethos reopens its confirmation
and dependent findings only; changed setup reopens affected setup work.

Audit's execution exemptions cannot establish authoring
readiness. On a switch to creation or expansion, check newly applicable runtime,
environment, and service requirements, and defer agent credentials until actual
agent runs. On a switch to audit, skip execution probes and clear their blocking
effect on audit work while preserving prior results as evidence. Recheck audit
tooling and trace availability if they have not been established. Creation's
data exemptions do not satisfy audit trace availability. Neither direction loses
the accepted Ethos, saved scope decisions, or evidence references, and a switch does not
authorize unrelated trace reads or agent execution.

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
