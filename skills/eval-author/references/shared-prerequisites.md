<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared prerequisites and Ethos

The core owns orientation and goal selection. This is the canonical operational
procedure after readiness for creating, auditing, and improving evals, including
direct skill calls. The [onboarding rendering contract](conversations/onboarding.md)
defines stage inputs and outputs; it does not duplicate this sequence.
It supports NeMo Gym and Harbor through the same flow. Scoped operations retain their
existing input requirements and do not acquire a full onboarding checklist.
For a new flow, begin this procedure only after the selected goal's rendered
checklist and readiness question have been shown and the user replies to that
question. A goal-choice reply triggers `goal_selected`, not this procedure.
Do not load repository context or save state in preparation for either
orientation turn. The [onboarding renderer](conversations/onboarding.md#orientation-and-prerequisite-entry) has a
narrow no-input command exception for those turns; other helpers remain deferred,
apart from the audit entry lookup below.

## Return to an audit

Explicit full-audit requests and accepted first-suite audit invitations can
continue creation or improvement context, including in a new chat. An offered
or declined invitation starts no audit; a later explicit request selects that
goal. Scoped audit commands retain their entry rules.

Before classification, read only the known request/workspace repository's metadata:

```bash
python3 -B <skill_dir>/scripts/journey_state.py --repo /absolute/repository --state-only show
```

This pre-orientation exception permits helper dependencies, but no state writes
or initialization, artifact inspection (including Ethos), trace enumeration,
prerequisite probes, or other repository searches. Unknown repository, absent
state, or no saved workflow answers/pending work means fresh entry.
Malformed, unreadable, or foreign state requires recovery. For this lookup or
any subsequent state-operation failure, preserve the file, render
[`audit_entry_failure`](conversations/onboarding.md#when-state-is-unavailable)
with the observed error, and stop before further checks or inspection.

For the same repository and agent, reuse Ethos metadata with a confirmed digest,
validation, confirmation source, and no unresolved intent review; prior authoring
need not be complete. Metadata does not prove current content. Resume unfinished
shared work at its pending decision. Resolve changed or ambiguous identity and
intent before auditing, retaining prior scope and evidence as history.

For reusable context, continue these steps in the same turn when unblocked:

1. Retain the handoff and original scope before `--state-only init --journey audit`
   clears the previous journey's pending question/handoff. Preserve agent and
   provider; for same-journey resumes, preserve the actual pending decision.
2. Apply [audit prerequisites](pre-reqs/audit.md) with state-only operations,
   retaining audit/authoring applicability resets. Reuse applicable common access
   and Ethos-skill confirmations; resolve only missing or changed audit skills,
   Python tooling, and trace availability from the request and retained evidence.
   Authoring data access does not prove evaluation-run traces exist. Exempt runner,
   Docker, live-service, and execution-credential requirements without probing them.
3. Unknown/missing traces or blocked audit tooling/access require the saved blocker
   and `prerequisite_results`, using the canonical trace blocker when applicable.
   Stop before Ethos or audit artifact inspection; do not offer a trace-free full
   audit or generate runs. Preserve prior scope, completion, evidence, and Ethos.
4. After saving an unblocked prerequisite pass, use normal `show` to reconcile
   Ethos. Audit state operations leave authoring artifacts unread; their completion
   is historical. Unchanged confirmed content needs no review for a new goal/chat.
   Changed/missing content, changed intent, or an unresolved review follows the
   [Ethos adapter](ethos-confirmation.md); retain its actual pending review.
5. With applicable confirmation, enter
   [guided audit](../../eval-author-audit/references/guided-audit.md). Preserve scope
   fields through normal revision-checked updates: selected suites, exact run/attempt
   evidence, trace source/location/bounds, exclusions, limitations, and provenance.
   Availability and candidate paths do not select sources; an accepted invitation
   selects only its identified inputs. Continue at the first unresolved step, or
   proposed-check review when inputs are settled, without another recap approval.
   Never measure before check approval.

Skip orientation, the `prerequisites` preview, repeated Ethos confirmation, and
the shared completion pause. Do not fabricate `ready: true` or started status,
or emit an empty checklist turn. After reconciliation, use `resume` for an actual
pending decision or continue authorized work. An audit request answers only a
generic start invitation, not pending source/content decisions. After a required
Ethos review, resume the requested audit without another invitation to begin.

## Check the agent and setup

After the user continues, load only the selected goal's prerequisite reference:

| Goal | Prerequisite table and flow-specific checks |
|---|---|
| Create first evaluations | [Creation prerequisites](pre-reqs/create.md) |
| Audit coverage, gaps, and improvement opportunities | [Audit prerequisites](pre-reqs/audit.md) |
| Improve existing evaluations | [Improvement prerequisites](pre-reqs/improve.md) |

For fresh onboarding, after the actual readiness reply, render `prerequisites` with its canonical
grounding sentence above the initial status table before inspecting the
repository or saved state. Reuse facts already in
conversation; do not perform reads or probes to populate the preview. Supply
known findings through the [preview contract](conversations/onboarding.md#orientation-and-prerequisite-entry),
leaving unchecked items at their canonical initial statuses. Add no separate
preamble, skill-use announcement, or prose checking notice.
This applies to skill-generated messages; mandatory host or tool UI instructions
remain outside the rendered-output contract. Do not add a notice merely to
satisfy a local skill convention.

Then complete bounded checks in the same turn. Finish accessible checks before
asking about unresolved items; do not ask the user to attest to software that
can be inspected. The renderer owns both tables' column headings, row labels,
and row order for NeMo Gym and Harbor; never add, rename, reorder, or omit rows.
Keep completed findings for the final reply, following the procedure below.
Do not publish an intermediate completed report before Ethos inspection.

Use `--state-only` for every prerequisite `init`, `show`, and `update`, including
pending-review bookkeeping. On resume, load saved metadata only after the initial
status table, then continue the remaining checks from applicable records.
[Returning audits](#return-to-an-audit) use their bounded lookup and skip that preview.
If a state operation fails, follow [state-failure handling](#when-prerequisite-state-is-unavailable).

Saved Ethos metadata is history, not proof a document exists, validates, or
remains current. Defer target Ethos discovery, reads, validation, draft recovery,
and preflight until the [prerequisite pass is complete and saved](#present-results-and-the-next-step).
Apply this order to fresh flows, prerequisite resumes, goal switches, and direct
flow calls, with the [audit continuation](#return-to-an-audit) exception above;
scoped utilities retain their existing boundaries. Later `resume`
responses use normal reconciled state without repeating orientation. Keep no
second progress record or saved checklist; recheck affected environment facts.

Follow the selected reference's checks and blocking conditions, using the common
checks below. Creation and improvement also use the shared authoring checks;
audit uses its own tooling checks instead.

### Common checks

Read the repository's supplied agent files and documentation with bounded reads,
excluding the target `ETHOS.md` until the prerequisite pass is complete and saved.
Reuse known identity, repository paths, runtime choice, and answers. When naming
the agent or setup, make its basis clear: the user's request, repository documentation,
the existing suite, or a previously confirmed answer. Describe inferred identity
and default choices as such; neither is a user confirmation. Reuse explicit
choices without asking the user to select them again. Do not enumerate or read
traces before the user selects their source and location.

For creation and improvements, honor the user's NeMo Gym or Harbor choice,
otherwise preserve the selected suite's setup, otherwise use the existing Harbor
default. For audit, record the actual framework using the
[audit prerequisite rules](pre-reqs/audit.md#checks-and-blocking-conditions).
Keep format compatibility
separate from availability. Explain unsupported material without converting it or
switching the selected runtime. Apply the selected reference's format requirements
before checking a provider's runtime or starting Ethos.

Select checks by the requested work. The common checks are:

- Read each required Eval Author and Ethos skill's `SKILL.md` from its supplied or
  installed location, following symlinks to verify readable targets. Check the
  local resources needed for this flow relative to the loaded skill directory,
  including `scripts/validate_ethos.py` relative to the loaded `ethos-write`
  directory. Check all three Ethos skills:
  `ethos-orchestrate`, `ethos-explore`, and `ethos-write`. A catalog entry or symlink
  alone does not establish usability. Inspect these installed resources only for
  availability and compatibility; do not run their file-location, validation,
  interview, or other Ethos preflight steps during prerequisite checks
- Verify that the installed Ethos skills meet the
  [required handoff capabilities](ethos-confirmation.md#required-handoff-capabilities),
  including preserving the selected explicit `ETHOS.md` destination. A
  version that renames it to lowercase or overrides that path is incompatible;
  record the conflicting resource as an `ethos_skills` blocker before delegation
- Establish file and command access from host permissions and actual bounded
  reads or commands; keep any write probe under `.eval-author/.working/`

If a required skill or resource is missing, broken, incompatible, or inaccessible, finish the
other accessible checks, then show **Blockers** before Ethos. Record the actual
reason under `skills` or `ethos_skills`, keep setup incomplete, and preserve
answers and confirmations. Save the blocker in phase `setup`, its repair action
as `pending_question`, and `eval-author` as `next_handoff`.
Use the [Ethos recovery instructions](ethos-confirmation.md#recovery)
for Ethos dependencies. For Eval Author dependencies, link the
[installation guide](https://github.com/NVIDIA-NeMo/eval-author/blob/main/docs/getting-started.md#install-eval-author-with-npx)
and provide `npx skills@latest add NVIDIA-NeMo/eval-author --skill '*' --agent claude-code --yes`,
substituting the current assistant's identifier and adding `--global` when repairing
a global installation. Provide the command without running it during checks;
after repair, recheck the files and resources before clearing the blocker.

### Authoring checks

For creation and improvements only, read the selected setup's authoring and Python
requirements from [runtime-policy.json](../scripts/runtime-policy.json), then:

- Use the shared [runtime locator](runtime-prerequisites.md#runtime-prerequisite-checks)
  with the selected provider and `--flow authoring` to inspect Python, package
  versions, imports, and CLI availability; retain its exact interpreter, command,
  versions, and failures under `.eval-author/.working/prerequisites/`
- Inspect the selected evaluation environment; check Docker's CLI and daemon with
  bounded, non-mutating version/info commands only when that environment needs
  Docker, without starting containers or changing the selected backend
- Identify required services and data from relevant configuration and documentation
  and inspect permitted access using existing non-mutating checks; do not read
  secrets or unselected traces, or treat configuration alone as successful access
- Record whether agent connection and credentials are configured only where that
  is safely observable without exposing values; their absence blocks actual-agent
  runs, not this prerequisite review or planning

Creation and improvement require the selected evaluation runtime and its supported
Python environment before Ethos or case planning in the full flow. If either is
missing, incompatible, or unresolved, finish the other accessible checks, then
show **Blockers** with the required action and relevant setup documentation. Keep
setup incomplete and pause; do not install software or switch providers.

Save a `runtime` or `python` blocker in phase `setup`, the required action or
unresolved question as `pending_question`, and `eval-author` as `next_handoff`.
Preserve existing Ethos and other progress, but do not begin or resume Ethos while
this blocker remains. On return, recheck the selected runtime before clearing the
blocker. Explicitly scoped inventory, planning-only, and audit operations retain
their own requirements.

### Record results

Check all applicable, accessible items even when one fails. Skip checks marked
**Not needed now** rather than probing them for completeness. A denied command, timeout, or
inaccessible location is an unknown with its actual reason, not proof that a
dependency is absent. Optional assistant skills remain optional. Do not install,
repair setup, execute agents, or run evaluations during these checks.

Then update every table row with its findings and evidence. Use **Verified** for
observed checks, **Confirmed by you** for user answers without matching execution
evidence, **Missing** for established unavailable or incompatible prerequisites,
**Unknown** for unresolved checks, and **Not needed now** where applicable. A
successful runtime probe does not prove the suite or agent is ready to run.
Use a single status per row and distinguish individual findings for grouped
checks in Findings. For a missing or unresolved later requirement, name the
operation it blocks and the needed action there, such as “Agent runs blocked:
[missing requirement and resolution].” Keep known missing dependencies explicit
when reusing saved progress.

After this pass, ask only for unresolved information or decisions needed to
continue, such as the selected runtime's location, the supported format for improvements, audit trace availability,
an inaccessible repository location, an ambiguous agent, or access required for
the next Ethos action. For creation and improvements, keep later environment,
service, data, and agent-access requirements in the table, with the work they block
and when they must be resolved. Do not label those later requirements optional or
confuse them with the runtime prerequisite above. Do not ask the
user to confirm facts already established by checks. Preserve progress; a saved availability question must go
through these checks before it is asked again.

When later requirements remain, summarize readiness for Ethos separately from
readiness to run evaluations. For example: “We can review ETHOS.md. Agent runs
remain blocked until [requirements are resolved].” These are required
dependencies; do not present them as Recommendations.

## Present results and the next step

For [returning audits](#return-to-an-audit), use that procedure's response and
Ethos-reuse rules. The full table and review sequence below applies to shared
onboarding, not an already-confirmed audit handoff.

If state initialization, reading, or saving failed, follow
[state-failure handling](#when-prerequisite-state-is-unavailable). The normal
saved-state procedure below applies only when those operations succeed.

Finish every applicable prerequisite check and save its findings through
`journey_state.py --state-only` before target Ethos inspection. Once required blockers are
resolved, save the pending Ethos action using supplied context or saved metadata
only; mark any required existing-document review pending without checking the
file. Load a fresh state-only snapshot to establish the completed pass.

If blocked, pass its state, exact revision, and bounded prerequisite findings to
[`prerequisite_results`](conversations/onboarding.md#stateful-responses), then emit
the rendering directly as the final response once: the canonical grounding
sentence, completed table, optional Recommendations, and Blockers. Do not first
show the same report in progress or inspect target Ethos artifacts. Canonical
blockers retain their own prerequisite question and linked pause attribution.

If unblocked, continue Ethos in the same turn without an intermediate completed
report or a new approval pause. The completed, saved state-only pass is required
before the first target Ethos search, read, validation, draft recovery, or
orchestrator preflight; a visible completed report is not required at that point.
Use normal state reads to reconcile artifacts before each call through the
[Ethos adapter](ethos-confirmation.md). Save returned Ethos findings and
the pending question, then reload normal state and render `results` in the final
reply. Supply the completed prerequisite findings, optional Recommendations,
and applicable Ethos, draft, question, and blocker facts. The final owns the
canonical grounding sentence, completed table, Next Steps, one state-derived
checklist, and the inspected Ethos action. Preserve the renderer's headings and
Markdown in that final response under the [shared output contract](conversation-rendering.md).

Each prerequisite turn shows an initial status table and one completed table in
the final reply. An unblocked final includes one checklist; a blocked final
includes no checklist. Do not add a third table or repeat the completed report in
progress. Subsequent actions can use `ethos` or `resume` when no new prerequisite
pass is needed.

### When prerequisite state is unavailable

Returning audits use the failure response in [Return to an audit](#return-to-an-audit),
which requires no readiness reply. For a fresh flow after readiness:

If a prerequisite `init`, `show`, or `update` fails, finish independently
accessible prerequisite checks and render
[`prerequisite_failure`](conversations/onboarding.md#when-state-is-unavailable)
directly as the final reply through stdin, without creating a payload file.
Preserve existing state; do not fabricate a snapshot, reset it, or claim the
failed operation succeeded. Keep the observed findings in the current
conversation and present the renderer's notice that they cannot be resumed in
a fresh chat until saved. Stop before target Ethos inspection and downstream
work. After resolving the state problem, reload any existing state, recheck
affected facts, and save the prerequisite pass before using normal responses.

### Findings and the Ethos action

- Recommendations are optional suggestions with their benefit and relevant
  link; they never prevent continuing.
- Blockers describe the affected step, reason, and needed action or answer.
  Save each blocker in the phase of the work it actually prevents. Supply the
  renderer only the IDs active under its
  [blocker selection rules](conversations/onboarding.md#blockers); deferred
  execution requirements remain findings and retained records. Canonical audit
  trace and expansion-format blockers require the exact loaded `skill_link`
  instead of custom wording. Their renderer-owned paragraph supplies the required
  skill-pause attribution; do not append another explanation. Any active saved
  blocker suppresses Next Steps, the checklist, Ethos guidance, and the
  continuation question. Present the whole blocked rendering unchanged.
- When no blocker remains, the final `results` rendering owns Next Steps,
  checkmarks, current position, and the canonical Ethos action, without a
  separate Ethos heading.

Creation and improvement require their selected supported runtime and Python
environment before continuing shared onboarding. Supported improvement format
and audit trace availability are also prerequisites. Agent connection and
credentials remain requirements for later actual-agent runs. Missing Ethos
skills can block the next action; missing `ETHOS.md` alone does not, because the
Ethos skills can create it.

For an existing document, first save the pending review described by
[existing Ethos review](ethos-confirmation.md#review-an-existing-document) using
state-only metadata, unless [audit continuation](#return-to-an-audit) reuses its
confirmation. After the prerequisite pass is complete and saved and the document is inspected, supply
its real link and a grounded summary as `facts.ethos` with status `existing`. For a missing document, use status `absent` and supply the
orchestrator's current question. Use `unknown` when presence is unresolved.
Keep explanations grounded in the supplied findings. The renderer produces the
canonical explanation or review question; it cannot start an interview or
establish approval. If blocked, preserve progress without starting dependent
Ethos work.

## Confirm Ethos

An eligible [audit continuation](#return-to-an-audit) reconciles and reuses its
confirmed document instead of entering a new shared Ethos review.

After completing and saving the unblocked prerequisite pass, invoke
[Ethos confirmation](ethos-confirmation.md) for the selected `ETHOS.md`.
Reuse content and prior answers, but have the user confirm an existing document
is the right one and still accurate, even when saved state records an earlier
confirmation. Before target-artifact inspection, use `--state-only update` to mark
a required review pending from supplied context or saved metadata, as described
in the adapter; this does not require locating or reading the document. Keep
Ethos unchecked until its required review is complete.
Record the returned path, validation result, confirmed digest, remaining intent
questions, and stage. Do not infer content approval from file presence,
structural validity, or an implemented behavior. Do not write the canonical
document before its content is confirmed.

## Pause after Ethos confirmation

An already-requested [returning audit](#return-to-an-audit) resumes directly;
the completion pause below is for a new shared flow.

After the saved `ETHOS.md` has been validated and its content reviewed and
confirmed, persist completion using `confirm-ethos`. A draft approval or file
write alone does not complete this step. Preserve confirmed Ethos and prerequisite
findings, then save the pending decision and downstream skill before rendering:

- **Fresh, unstarted creation, no structured saved design, no saved source choice:** Save
  the canonical source question as `pending_question` and
  `eval-author-first-eval` as `next_handoff`. Follow the
  [source checkpoint](conversations/first-eval.md#source-and-proposal-pauses) for
  the template-owned question and choices. This replaces the separate readiness
  question; do not ask whether to continue first.
- **Other unstarted cases:** Retain the selected journey's canonical continuation
  from [onboarding_templates.py](../scripts/conversation_flows/onboarding_templates.py)
  and the selected downstream skill. Preserve applicable source choices and
  progress; a saved design still requires its own reuse decision. Already-started
  work resumes its saved pending decision without another completion pause.

Reload reconciled state and render `confirmed`, including the real saved Ethos
link in `facts.ethos`. Use its Markdown as the final visible response, then
**end the turn**. The renderer derives the complete shared steps and current
journey step from state. A relevant blocker produces only the blocked action
instead of the pending decision. The pause does not establish design approval or
prove that execution is configured or successful.

Only the fresh-creation exception above includes source selection in this turn.
Do not ask task-planning questions, inspect new suite or trace sources, probe
runtimes, create evaluations, or delegate downstream work while the reply is
pending. The pause must remain visible after tools finish; commentary alone is
not sufficient. Rendering it does not answer its decision.

After the actual source answer to the combined checkpoint, save the choice and
its provenance, clear the pending question, and hand off directly to the small
task proposal. Resolve selected trace locations, runs, and exclusions before
inspection. Do not insert another readiness pause or repeat the source question.
For other cases, wait for and save the actual continuation reply before handing
off with the retained context. Do not repeat this pause when resuming downstream
work that has already started. Keep later execution requirements explicit and
pause only the work they actually block.

For creation, this continuation and `ETHOS.md` confirmation do not choose a
previously saved task design. On a fresh request, hand off to the first-eval
[saved-design checkpoint](../../eval-author-first-eval/SKILL.md#confirm-a-saved-design-before-reusing-it)
before resuming that design. Existing metadata can identify this pending choice
during the state-only pass; save its creation record only through normal updates
after the shared prerequisites and `ETHOS.md` confirmation are complete.

For creation whose source decision was not eligible for the combined checkpoint,
continue at the first-eval
[source checkpoint](conversations/first-eval.md#source-and-proposal-pauses) only
when that choice remains unresolved and no saved-design decision is pending.
Keep its overall checklist and canonical choices; do not replace either checkpoint
with a free-form source or task-preparation question.

## Save and resume

`.eval-author/.working/journey-state.json` is the sole authority for workflow
stage, prerequisite findings and answers, confirmations, task status, pending questions, and next action.
Use `scripts/journey_state.py --repo <repository> <command>` for `show`, `init`,
`update`, `confirm-ethos`, `record-task`, `import-legacy`, and `rebind`; inspect its help for command inputs.
Its validation and atomic writes are allowed bounded local commands, not runtime
probes. State identifies the repository, agent, goal, selected setup, answer
provenance, Ethos path/digest, and journey-specific progress. Never store secrets.

During prerequisites, use only `init`, `show`, and `update` with `--state-only`.
This mode validates saved JSON and persists metadata without reading canonical
Ethos, draft, or task-evidence artifacts; `artifact_checks_deferred: true` records
that boundary. It cannot establish content approval or task acceptance. Treat
saved approvals as historical, and defer reports, task-readiness checks, and
artifact inspection. After completing and saving an unblocked prerequisite pass,
omit `--state-only` so normal commands reconcile current content and evidence.
For the audit journey, normal commands reconcile only Ethos: `task_readiness`,
`first_eval_observations`, and `first_eval` are null, and creation progress cannot
be updated until an authoring journey is selected. Retained authoring evidence
does not select it as audit input.
While the current journey is `audit`, `--state-only update` also merges the
nonempty strings `scopes.audit.framework` and `scopes.audit.framework_source`
into the existing scope. These observations persist even when prerequisites are
blocked; case, corpus, and approval updates remain forbidden in this mode.

Save agreed decisions in the selected journey's `scopes` entry.
Creation uses the additive `scopes.create.first_eval` record described in the
[first-evaluation conversation contract](conversations/first-eval.md). Normal
`show` returns its reconciled progress and artifact observations; creation
updates require the latest `--expected-revision`. These fields do not change
the top-level schema or grant approvals to older records.
Put final task behavior in task READMEs and audit checks
in the audit report. Do not create a separate first-eval or expansion plan file.
Retire `first-eval.md`, `audit-progress.md`, and intent notes as active writers.
Import legacy records once as unreviewed references with provenance. Review the
imported decisions before applying them; old notes never establish approval
automatically, even when they describe a prior approval. Preserve conflicts and malformed
records for resolution; do not replace them with fresh success state.

For [audit continuation](#return-to-an-audit), use the bounded entry lookup and
reuse procedure above. When other prerequisite work must resume, complete and save the state-only pass before
reading target artifacts. Include its completed findings in the final `results`
response. When no new pass is needed, render `resume` from normally reconciled
state to present the pending question or earliest unfinished,
non-deferred step; never regenerate the welcome or readiness question. A goal
change reuses applicable common decisions. Switching between audit and authoring
reopens runtime, Python tooling, environment, service/data, execution access, and
flow-specific skill checks; previous sources remain available for reassessment.
An audit exemption or successful audit import does not establish authoring
readiness. Re-evaluate recorded blockers before displaying results for the new
goal, retaining evidence but excluding requirements that do not apply.
A changed agent, setup, or Ethos
reopens only affected work; changed Ethos invalidates its confirmation until
reconfirmed while retaining source findings and prior intent answers. Persist
source findings, answers, exact draft content/reference, and the pending question
at each Ethos pause; reload them before resuming or switching journeys. Existing
downstream evidence is retained and marked stale when
appropriate, never rewritten to match new decisions.

Changing the agent retains cases, traces, Ethos findings, answers, draft references,
and unresolved blockers while reopening the affected approvals. Agent-dependent
prerequisite evidence keeps its source with status `unknown` for reassessment.
`journey-state.json` remains the authoritative resume record; changing the agent
does not create a separate state archive. Review applicability before using
existing findings for the new agent. Reuse the same agent identifier on
resume; different spellings do not establish that two agents are equivalent.

All new working files, intermediate inputs, and temporary files live under
`.eval-author/.working/`. Historical evidence with bound paths remains readable.
Review imported references before choosing how to continue interrupted legacy
tasks. Keep those tasks at their original paths, preserving bound evidence and
relative assets; explicitly select the active legacy task root in the shared
journey scope when continuing it. Do not treat historical duplicate copies as
additional active tasks. New tasks use the stable task catalog. Old progress
notes are import-only; never resume writing them. If prerequisite state cannot
be written or read, retain observed findings in conversation and use the
[state-failure procedure](#when-prerequisite-state-is-unavailable).
Independent prerequisite checks may finish, but Ethos and downstream work wait
until the pass can be saved. Preserve existing state rather than replacing it.

## Temporary work and recovery

`.eval-author/.working/` is temporary construction space and recovery state for
unfinished work. Keep the current decisions, inputs, and validation evidence
needed to resume that work; reference existing files instead of copying them.
Final tasks and their READMEs stay at their documented permanent paths. Source
control owns task and grader revision history. Eval Author must not create
archive files or an `archives/` directory. Users decide how long to keep their
results; do not expire or delete their results automatically.

Agents may explore and use intermediate work freely. Keep routine state-update
payloads, helper responses, observations, renderer inputs, polling output, and
one-off scripts in memory or disposable scratch instead of saving every step.
Use stdin where supported and continue obtaining fresh reconciled observations
where the workflow requires them. Current approvals and claims still require
their matching revisions and available evidence.

When a tool needs a scratch file, use the existing
[`working_paths.scratch_directory`](../scripts/working_paths.py) context manager
under `.eval-author/.working/tmp/` and consume it within that context. It removes
the directory on ordinary success and failure. Put outputs needed by the current
workflow at their documented locations before recording evidence paths; do not
bind approval or validation to a file that the scratch context will remove.

## Hand off

After the user answers the completion pause, or through an authorized
[audit continuation](#return-to-an-audit), pass the selected journey,
repository/agent, setup and prerequisite evidence and answers,
confirmed Ethos result, saved source choice when applicable, and unresolved
dependencies. First-eval continues with its next unresolved source/design/scope
decision; do not repeat a source question already answered in the combined
checkpoint. Audit continues with selected evals and traces;
expansion follows the core's explicit expansion handoff. Never repeat shared
onboarding or convert confirmation into a claim of successful runtime execution.

## State helper commands

Run with the assistant's Python 3.11+; this helper uses the standard library.
Replace `<skill_dir>` with the core skill directory. The `-` in `--input -`
selects stdin for the partial JSON update; keep it when supplying input this way.
Keep responses in memory under the
[temporary-work policy](#temporary-work-and-recovery). Use the revision returned by
`show` to avoid replacing newer decisions.

```bash
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository --state-only show
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository --state-only init \
  --agent account-support --journey create --setup gym
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository --state-only update \
  --input - \
  --expected-revision 1
```

`--journey` accepts `create`, `audit`, or `expand`; `--setup` accepts `gym` or
`harbor` for an authoring setup. Audit does not require `--setup`; record its actual
framework and provenance with `--state-only update` in `scopes.audit.framework`
and `scopes.audit.framework_source`, independently of any retained authoring
choice. `init` reuses state and invalidates affected facts when identity or
setup changes, or when switching between audit and authoring. The examples above
use prerequisite mode: Ethos and evaluation checklist items remain unchecked
until normal artifact checks resume after the prerequisite pass is complete and
saved. `show` derives
checklist status without writing. Use its state-only `state` and matching revision
for `prerequisite_results`; use normal reconciled state for later rendering.
Never maintain a separate saved checklist. Rendering does not save a readiness
answer, confirmation, or continuation, and does not replace normal `show`
reconciliation of changed Ethos or draft content.

Setup completion requires agent identity, code/file/command access, usable required
skills, and the applicable Python tooling. Creation and improvements also require
a selected authoring setup and its runtime; audit instead requires confirmed or
verified availability of completed-evaluation traces. Later environment, services,
and authoring data findings do not delay this checklist step. Any explicit setup
blocker still prevents completion, including access needed for the next Ethos action.

An update is a partial JSON mapping. Allowed fields are `prerequisites`, `ethos`,
`scopes`, `journey_status`, `blockers`, `pending_question`, and `next_handoff`.
Each prerequisite has `status` (`verified`, `confirmed`, `missing`, `unknown`, or
`not_required` where applicable) and `source` identifying the actual check evidence
or user answer. Use `verified` only for observed checks and `confirmed` for user
answers; preserve that distinction on resume. Link retained command results from
`source`; a status alone is not evidence. The keys are `code_access`, `file_writes`, `command_execution`,
`skills`, `ethos_skills`, `runtime`, `python`, `environment`, `services`, `data`,
`agent_connection`, and `model_credentials`. The last two are execution-dependent.
Never put credential values in state.

For audit, record `runtime` as `not_required` with the reason that no evaluation
runner is used. Record the checked audit Python environment and its required
imports under `python`; use `not_required` for the environment, live services,
and execution credentials. Record completed-evaluation trace availability under
`data`, with the actual answer or evidence as its `source`. Unknown or missing
availability requires a `data` blocker with phase `setup`, the pending availability
question, and `eval-author` as the next handoff. On confirmation, clear that blocker
and record `data` as `confirmed`; use `verified` only for inspected evidence.
On resume, reassess legacy `data: not_required` and unrelated data checks; reuse
applicable trace-availability answers or leave this prerequisite unknown.
Keep suite/trace selection, selected evidence access, and later parser/conversion
results in the audit scope and findings. Availability alone does not approve a
source, complete the audit, or establish measured coverage.
Do not mark the audit interpreter as proof of the agent's runtime requirements.
The helper's audit `runtime_requirements` describe audit tooling independently
of the selected NeMo Gym or Harbor source format.

`scopes` is keyed by journey and holds agreed source bounds, scope tables,
case inventory, and downstream decisions. Updating a journey's scope replaces
that entry, so read and preserve unaffected fields first. `journey_status` maps
a journey to `pending`, `in_progress`, or `complete`. Blockers have `id`, `phase`
(`setup`, `ethos`, or a journey), and `message`. For a prerequisite blocker, use
its prerequisite key as `id`, or `evaluation_format` for the
[improvement format requirement](pre-reqs/improve.md#checks-and-blocking-conditions).
On an audit/authoring switch, the helper retains
affected setup blockers under the previous journey's phase and marks affected
prerequisite answers `unknown` while preserving their source. Reassess them for
the new operation, including retained blockers when returning to that journey;
clear a blocker only when the new evidence resolves it or establishes that it
does not apply. Review legacy blockers without these IDs explicitly rather
than treating them as current requirements automatically. Pending question and next handoff
are strings or null. Reopening a dependency must not silently erase old evidence.

### Recover after moving or copying a repository

The saved absolute repository path prevents another checkout from silently
reusing this checkout's approvals. After a rename, move, new worktree, or clone
that includes `.eval-author/`, run `--state-only show` in the destination. Its mismatch error
reports the saved path and revision. For an intentional relocation, use those
exact values to recover without deleting or hand-editing state:

```bash
python3 <skill_dir>/scripts/journey_state.py --repo /new/absolute/repository rebind \
  --from-repository /previous/absolute/repository --expected-revision 12
python3 <skill_dir>/scripts/journey_state.py --repo /new/absolute/repository --state-only show
```

`rebind` is the explicit state-recovery exception: it clears approvals without
inspecting Ethos or task evidence. Resume prerequisite commands with `--state-only`.
`journey-state.json` remains the authoritative resume record, without a separate
copy of the previous state. Existing findings, answers, draft and evidence
references, scope choices, priorities, legacy imports, and unresolved blockers
remain available for reassessment. Existing artifacts and paths inside them are
not rewritten. Inspect any absolute references and recheck access in the
destination before reusing them.

All prerequisite statuses become `unknown` with their sources retained. Ethos
and draft approval reopen, scopes require review, and journey completion resets.
Accepted tasks become `draft`, keeping task and evidence hashes. Recheck
applicable prerequisites, review the saved intent and scope, and complete the existing acceptance workflow
before recording acceptance again. A rebind does not establish that the agent,
environment, or task still works. `init` resumes normally after recovery; use its
agent selection if the intended agent also changed. A stale revision or a
different saved path is rejected without replacing state.

### Task status

Record each newly scaffolded task as `draft` before refreshing the report. Use
the repository-relative task root: the Harbor directory containing `task.toml`,
or the NeMo Gym scaffold root containing `environments/<name>/manifest.yaml`.
The optional top-level `tasks` mapping holds these records, keyed by task root.
Use `record-task`, rather than an arbitrary state update or report metadata patch:

```bash
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository record-task \
  --task .eval-author/tasks/<slug> --status draft --source "<authoring decision>"
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository record-task \
  --task .eval-author/tasks/<slug> --status accepted --source "<completed workflow review>" \
  --evidence .eval-author/<retained-validation-result> \
  --evidence .eval-author/<retained-agent-result>
```

`accepted` records the calling flow's completed acceptance decision; it does not
run or replace that flow's checks. Supply its retained evidence files with
repeatable `--evidence` arguments. Paths are repository-relative. For first
evaluations, retain the passed controls, validated configuration, and actual-agent
baseline required by that flow; a low agent score can still be a valid baseline.
Audit-gap creation requires its saved `accepted: true` verification and a passing
`check-acceptance` before recording or reusing acceptance.

The helper binds acceptance to the current task revision and evidence hashes.
Changed or missing task content or evidence derives **Draft** with a reason,
while preserving the recorded decision for review. An unrecorded task is
**Unknown**; discovery, a task path, or a successful run cannot infer acceptance.
The report reads this state for both the Harbor task directory and a NeMo Gym
manifest under the nearest explicitly recorded task root. Report refresh and
`show` do not accept tasks or rerun checks. In an authoring journey, use `show`'s
`task_readiness` for current status; `state.tasks` retains the original decision
and provenance.

Keep status labels out of task-local READMEs: those files are part of the task
revision used by execution receipts. Display status in the generated suite
README and report, derived from shared state. A status change does not move task
files, change suite membership automatically, or create another status document.

### Ethos state

During Ethos work, the `ethos` update accepts:

| Field | Value |
|---|---|
| `path` | Canonical repository-relative path ending in uppercase `ETHOS.md`; defaults to `ETHOS.md`. Select it through a normal update with the latest `--expected-revision` and `stage: review`; path selection is unavailable with `--state-only`. |
| `stage` | `explore`, `interview`, `draft`, `write`, or `review`; completion uses `confirm-ethos` |
| `source_findings` | List of records with nonempty `path` and `summary` |
| `prior_answers` | List of records with nonempty `topic`, `question`, `answer`, and provenance `source` |
| `draft` | Null, or `{path, digest}` referencing exact proposed content under `.eval-author/.working/`; path is repository-relative, digest is bare 64-character lowercase SHA-256 |
| `draft_confirmed` | Boolean; true requires the exact recoverable draft and a confirmation source |
| `draft_source` | Actual user-confirmation reference, or null |
| `remaining_questions` | List of unresolved intent questions |

These shared Ethos fields survive journey switches. Lists replace prior lists
on update; preserve earlier findings and answers when recording new ones. Save
returned findings, answers, and the current draft at each pause. Load them on
resume instead of reconstructing intent or repeating answered questions.
Only `complete` establishes current Ethos confirmation. During `review`, the prior
digest and provenance remain as history so file changes can still be detected.
Opening review preserves agreed scopes and completed flows; reconfirming unchanged
content retains them. Changed content or an intent-revision stage invalidates
those agreements while preserving their source material. The
`write` stage requires confirmed draft content. `show` invalidates approval of
a changed or missing draft while retaining prior answers. Changed canonical
Ethos similarly reopens its review without discarding the intent history. A
selected path change reopens affected approvals even when the bytes match;
follow the [selection and provenance rules](ethos-confirmation.md#enter-the-ethos-step).

Only after the canonical workflow returns exact confirmed saved content and
successful validation may the adapter call:

```bash
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository confirm-ethos \
  --ethos <selected-repository-relative-path> --expected-revision <latest-revision> \
  --digest <64-character-sha256> --source <user-confirmation-reference> \
  --validation <actual-validation-result-reference>
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository import-legacy
```

The digest is for the exact saved bytes at `ethos.path`. An explicit `--ethos`
must match that selection; the omitted flag retains the legacy `ETHOS.md`
default. The digest cannot be set via an arbitrary state update.
`import-legacy` records old notes as unreviewed references; review them before
recording current decisions. Errors leave the
prior valid record intact and never imply that prerequisites or Ethos passed.
