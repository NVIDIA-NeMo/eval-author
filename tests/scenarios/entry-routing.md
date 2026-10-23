<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Entry routing conversational acceptance scenarios

Use these scenarios to review Eval Author's first response to onboarding and
expansion requests. This is a manual protocol, not an execution record or a
claim of passing model evaluation. Use fresh, isolated synthetic workspaces
with the sibling skills available and no customer data or provider credentials.
Retain replies, channels, tool calls, and delegation logs. Judge the selected
workflow and actions. Compare the fixed copy against the
[conversation renderer](../../skills/eval-author/scripts/conversation_renderer.py),
[shared templates](../../skills/eval-author/scripts/conversation_templates.py), and
[onboarding templates](../../skills/eval-author/scripts/conversation_flows/onboarding_templates.py).
The [shared rendering contract](../../skills/eval-author/references/conversation-rendering.md)
defines message determinism and input authority; the
[onboarding reference](../../skills/eval-author/references/conversations/onboarding.md)
defines the stage inputs and workflow boundaries.

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
tool trace: only the invoked skill, shared core, shared rendering contract,
onboarding reference, and the renderer's own source dependencies may be loaded
before this reply. The sole
command exception is the pure renderer's no-input `orientation` preview; it
must not receive repository, state, or input-file arguments. Repository/state
reads, Ethos validation, prerequisite checks or questions, other helper commands,
writes, and delegation all fail this opening checkpoint.
Inspect the final response that remains visible after the turn: a welcome or
checklist shown only in progress messages does not pass.

If the goal was initially unclear, choose each goal in separate runs. That reply
must match the renderer's no-input `goal_selected` output for that goal: the
canonical introduction, matching three-item checklist, and readiness question as
the final visible response, then end the turn. Do not repeat the welcome or show
prerequisite work yet. The same source-loading exception applies, and only
the pure renderer preview may run. No repository/state reads, probes, other helper
commands, state writes, or delegation may occur during this goal-selection reply.
The first checklist item is current; no items are complete.

Reply to **Ready to get started?**. Only now can prerequisite work begin. The
turn opens with the canonical grounding sentence and initial status table. An
unblocked final reply has the canonical completed-results sentence, completed
findings table, optional Recommendations, and one checklist under **Next Steps**
with the inspected Ethos action. A blocked turn shows its completed table and
Blockers directly in final once, without a checklist or Ethos guidance. There
is no intermediate completed report.
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
After the actual readiness reply, render `prerequisites` with the canonical
grounding sentence above the initial status table, then begin bounded checks.
Populate that table only from current conversation facts, without reading saved
state or repository files or probing to fill it. Unchecked rows use their
canonical defaults: Checking, Unknown with the trace requirement for audit
traces, and Needed before agent runs for authoring agent access. Supplied known
facts retain their actual evidence status. `--ready` or `ready: true` represents
the actual reply; rendering supplies no readiness. No extra skill-generated
preamble, skill-use announcement, checking notice, or progress summary surrounds
the rendering. Mandatory host/tool UI messages remain outside this output
expectation; no extra notice is generated for a local skill.

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
on resume. The initial status table follows its canonical grounding sentence
before checks. The completed findings table follows its canonical grounding
sentence in the final reply. No third table or intermediate completed report
appears.

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
  using `prerequisite_failure` if journey state cannot be loaded or saved
- A saved pending question asks whether Python, Harbor, and Docker are installed:
  inspect only requirements applicable to the selected goal, then show completed
  findings once before deciding whether a question remains
- A creation or expansion environment needs no Docker, services, or live
  credentials yet: retain its eight rows and explain the applicable or deferred requirements
- Audit local NeMo Gym or Harbor definitions with Python 3.11 or newer, PyYAML, and
  jsonschema available, but neither evaluation runner, Docker, nor agent
  credentials, and with confirmed trace availability: identify the suite format
  without probing those execution dependencies and continue to Ethos confirmation
- In that audit, **Audit tooling** reports Python and the audit-library checks;
  **Evaluation environment**, **Required services and data**, and **Agent
  connection and credentials** do not appear in the completed audit table
- The audit trace prerequisite is visible in the initial and completed tables,
  explaining that measurement requires traces from evaluations that have already
  run; unknown availability stays **Unknown**, an explicit user statement that
  traces exist is **Confirmed by you**, and confirmed absence is **Missing**
- Unknown or missing trace availability blocks full audit onboarding: finish
  accessible checks, save `data` in phase `setup` with the actual prerequisite
  status, and supply `id: data` plus the exact loaded audit `skill_link`. Render
  the canonical trace row, blocker, question, and linked skill-pause attribution.
  Omit Next Steps, the checklist, Ethos guidance, and any appended explanation
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

Compare the rendered results with the shared and onboarding templates' headings and
introductory sentences. Keep the renderer's section order across goals, NeMo Gym,
and Harbor; omit sections only under its stated conditions.

Inspect tool order: `prerequisites` uses only known conversation facts and comes
before repository or state reads. Prerequisite `init`, `show`, and `update` use
`--state-only`. Every applicable check must be finished and its findings saved
before any target Ethos search, read, validation, draft recovery, or orchestrator
preflight. The completed pass does not require a visible intermediate report.
Reading installed skill instructions establishes only availability and
compatibility.

Continue unblocked work in the same turn without an intermediate completed
report or a new approval pause. Normal state reconciliation precedes the final
`results` rendering, which includes the canonical completed-results sentence,
completed table, optional Recommendations, Next Steps, one checklist, and the
inspected Ethos/draft/question. If blocked at prerequisites with usable saved state, emit
`prerequisite_results` directly as final once; do not show it in progress first
or inspect target Ethos. That final has its canonical grounding sentence,
completed table, optional Recommendations, and Blockers, without Next Steps,
checklist, Ethos facts, draft, saved pending question, or continuation invitation.
Canonical blocker questions and linked attributions remain part of that final.
Apply this sequence to fresh entry, prerequisite resumes, goal switches, and
direct flow invocations. Subsequent Ethos-only replies can use `ethos` without
repeating prerequisite findings.

- Optional assistant skills are absent: put optional Recommendations after the
  completed table in final, followed by the Ethos checklist and question
- The selected authoring runtime is missing, incompatible, or unresolved: show
  the completed table and **Blockers** once in final, preserve progress, and omit
  Next Steps, the checklist, and Ethos guidance. Existing or historically
  confirmed Ethos does not bypass this gate
- Docker or execution credentials are missing but Ethos can proceed: keep their
  later requirements in the completed table. Do not classify them as optional or
  add them to Blockers. Retained execution blockers stay deferred through shared
  onboarding and its completion pause, then apply once downstream work starts
- Required Ethos skills or access for the next Ethos action are missing: finish
  the other accessible checks and render the completed table, applicable
  Recommendations, and Blockers once in final with the required action
- Audit trace availability is unknown or missing: use the same single blocked
  final and keep prerequisites incomplete even when all other checks passed
- No suggestions or blockers remain: the final completed table is followed by
  Next Steps, one checklist, and the inspected Ethos action
- Existing `ETHOS.md` needs current confirmation: the final `results` response has
  the completed table, one Next Steps heading, one checklist, and the inspected
  review material; no intermediate completed report or separate Ethos heading
- `ETHOS.md` is absent but the Ethos skills are available: the final `results`
  response includes the completed table, then its explanation or draft and
  current question below the checklist
- Resume an Ethos question without new prerequisite work: use `ethos` or `resume`
  with saved progress; do not repeat the prerequisite table or impose it on a
  scoped utility

Count across the entire prerequisite turn, including progress and final channels:
both variants have one initial status table and one completed table in final,
each introduced by its canonical grounding sentence. An unblocked turn has one
checklist; a blocked turn has no checklist. No third table appears. The earlier orientation turn is
separate and still ends at its readiness question. No duplicate rendering or
assistant-authored restatement of the findings or checklist passes this check.

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
`ETHOS.md` and saved Harbor authoring state. After the opening and readiness reply,
show the canonical grounding sentence and initial status table using only known
context. Complete independent checks, then render the completed eight-row table
with its grounding sentence and Blockers once in final. The framework row must
identify the incompatible format; the saved Harbor setup does not establish
compatibility or justify provider-specific probes yet.

Save `evaluation_format` in phase `setup` on journey `expand`, then supply
`id: evaluation_format` and `skill_link` to the exact loaded core `SKILL.md`.
Render the blocked response. Compare its label, reason, action, question, linked
skill-pause attribution, and conversion prompt against the canonical template. The handoff belongs inside **Blockers**, with placeholders
for the suite path, current framework, and target Harbor or NeMo Gym format. It
directs conversion to a separate coding-agent chat, preserving the original suite
and agent code, mapping scenarios and grading into new output, identifying
gaps, and requesting offline validation with live requirements reported separately.
Keep setup incomplete and omit Next Steps, the checklist, and Ethos guidance.
The blocker must appear before Ethos confirmation, including when an earlier
flow already confirmed that document.

- Preserve progress and pause for the user to convert the suite; no conversion
  or unsupported-format authoring occurs
- Do not offer separate NeMo Gym or Harbor evaluations as a workaround
- Resume before conversion: retain the setup blocker and required action; do not
  enter Ethos, probe a default provider, or mark prerequisites complete
- Return with converted suite paths and validation results: check the actual
  format before clearing the blocker and resuming the earliest incomplete step;
  a displayed prompt or a completion claim alone never clears it
- An unrelated blocker, another journey, or an `evaluation_format` record in
  another phase does not produce the conversion handoff
- Switch to audit: remove the improvement-format blocker's effect and apply
  audit prerequisites while preserving its findings and the existing suite

Keep explicit audit operations and supported trace conversion outside this
authoring-format requirement. No new adapter or general expansion implementation
is implied by converting to a supported format.

### Canonical blocker wording across entry paths

Repeat audit through the core route and direct `eval-author-audit` entry with the
same saved `data` setup blocker, availability status, and loaded audit skill link.
Vary the saved blocker's message, pending question, and legacy supplied
`step`/`reason`/`needed` prose. The traces row and complete canonical blocker
section must match across these entries. Its linked paragraph identifies the
exact loaded audit `SKILL.md` and quotes its actual pause instruction; no
assistant-authored explanation or stale saved question follows the rendering.
The saved state remains intact.

Repeat separately for Unknown and Missing availability. They must produce their
respective canonical status, finding, and action or question; Unknown must not be
presented as absence, and Missing must not be presented as an unanswered
availability check. A contradictory explicit trace-row status fails rendering.
Factual row links and other prerequisite findings remain available.

Repeat expansion after a known-goal entry and after selecting improvement from
the goal question, using the same `evaluation_format` setup blocker and loaded
core skill link. Canonical blocker text, attribution, and conversion handoff must
match despite different saved or supplied prose. The quoted instruction must
come from the linked core `SKILL.md`. No Next Steps, Ethos action, extra pause
explanation, or conversion execution follows. Add a separate custom blocker and
verify that its factual required action remains visible alongside the canonical
blocker. Other phases or journeys retain their own behavior.

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
Malformed state does not silently restart or imply approval. Repeat with failed
state `init`, `show`, and `update`, including a fresh read-only workspace.
Each failure uses `prerequisite_failure` through stdin without creating a payload
file or inventing saved state. The final contains observed findings, the helper
error, and the notice that these findings cannot resume in a fresh chat until
saved; no checklist, confirmation, or continuation appears. Existing state is
preserved, and Ethos and downstream work wait. After access is restored, reload
existing state, recheck affected facts, and save the pass before continuing.

## 6. Stable tasks and canonical Ethos

Missing or revised Ethos goes through `ethos-orchestrate`. Confirm draft content
before the canonical write; unchanged confirmed content stays unchanged. The
handoff returns a path, validation, digest, remaining questions, and stage.
New task files live at `.eval-author/tasks/<slug>/` from the start. Acceptance
changes status and explicit suite membership without promotion copies. Internal
plans, drafts, locks, and temporary files live under `.eval-author/.working/`.
