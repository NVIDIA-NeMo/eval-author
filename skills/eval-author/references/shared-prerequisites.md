<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared prerequisites and Ethos

The core owns orientation and goal selection. This procedure owns the common
entry for creating, auditing, and improving evals, including direct skill calls.
It supports Gym and Harbor through the same flow. Scoped operations retain their
existing input requirements and do not acquire a full onboarding checklist.
For a new flow, begin this procedure only after the selected goal's three-item
checklist and **Ready to get started?** have been shown and the user replies to
that call to action. If the goal was initially unclear, the goal-choice reply
triggers that checklist-only orientation, not this procedure. Do not load
repository context or save state in preparation for either orientation turn.

## Check the agent and setup

After the user continues, show this introduction and table before repository
inspection or commands. Send it as a visible progress message, then perform the
checks in the same turn; do not stop for an answer or ask the user to attest to
installed software.

I’ll check the prerequisites below, then explain anything that’s missing or unclear.

| Prerequisite | Status | Findings |
|---|---|---|
| Agent and repository | Checking | — |
| Evaluation framework | Checking | — |
| Required skills | Checking | — |
| File and command access | Checking | — |
| Python and evaluation runtime | Checking | — |
| Evaluation environment | Checking | — |
| Required services and data | Checking | — |
| Agent connection and credentials | Needed before agent runs | — |

Copy the introduction, column headings, row labels, and row order exactly for
every goal and for both Gym and Harbor. Change only the status and findings cells.
Keep every row, including those marked **Not needed now**; do not add, rename,
reorder, or omit rows. Update the cells as evidence becomes available and repeat
the table with results in the final reply so it remains visible alongside questions.
On resume, reuse findings already in context; show the table before loading saved
state, then update it from applicable records. Do not keep a second progress record.
Recheck affected facts when the environment changes.

Read the repository's supplied agent files and documentation with bounded reads.
Reuse known identity, repository paths, runtime choice, and answers. When naming
the agent or setup, make its basis clear: the user's request, repository documentation,
the existing suite, or a previously confirmed answer. Describe inferred identity
and default choices as such; neither is a user confirmation. Reuse explicit
choices without asking the user to select them again. Do not enumerate or read
traces before the user selects their source and location.

Honor the user's Gym or Harbor choice, otherwise preserve the selected suite's
setup, otherwise use the existing Harbor default. Keep format compatibility
separate from availability: a first suite needs an intended supported format,
not an existing task. Explain unsupported material without converting it or
switching the selected runtime.

Read the shared `scripts/runtime-policy.json` authoring requirements for the
selected setup and its Python version. Scoped audit checks retain their own
operation-specific policy; they do not lower the shared authoring requirements.
Inspect each applicable row before asking about unresolved items:

- Find required Eval Author and Ethos skills in the available catalog and supplied
  skill locations; distinguish files found from skills available to the assistant
- Establish file and command access from host permissions and actual bounded
  reads or commands; keep any write probe under `.eval-author/.working/`
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

Check all accessible items even when one fails. A denied command, timeout, or
inaccessible location is an unknown with its actual reason, not proof that a
dependency is absent. Optional assistant skills remain optional. Do not install,
repair setup, execute agents, or run evaluations during these checks.

Then update every table row with its findings and evidence. Use **Verified** for
observed checks, **Confirmed by you** for user answers without matching execution
evidence, **Missing** for established unavailable or incompatible prerequisites,
**Unknown** for unresolved checks, and **Not needed now** where applicable. A
successful runtime probe does not prove the suite or agent is ready to run.

After this pass, ask only for unresolved information or decisions needed for the
next Ethos action, such as an inaccessible repository location, ambiguous agent,
or access required for that action. Keep later execution requirements in the
table, with the work they block and when they must be resolved. Do not label them
optional or hold up Ethos for them. Do not ask the user to confirm facts already
established by checks. Preserve progress; a saved availability question must go
through these checks before it is asked again.

## Present results and the next step

After the results table, use the following template in order, with these exact
headings and introductory sentences. Replace the bracketed content with findings
or the next action; do not display the placeholders.

- Include **Recommendations** only when there are optional suggestions, such as
  Harbor assistant skills; these do not prevent continuing
- Include **Blockers** only when an unresolved issue prevents the next Ethos
  action; explain the affected action and the answer or change needed, with
  relevant setup documentation
- If **Blockers** is present, end with the required action or focused question
  in that section; omit Next Steps, the checklist, and all Ethos guidance
- Include **Next Steps** only when no Blockers remain, after the table and any
  Recommendations, followed by the shared three-item checklist with current progress
- Copy the checklist wording, Ethos link, and selected goal's third item from the
  core; derive checkmarks and the current-step marker from shared state, and do
  not repeat the checklist before the table
- When unblocked, continue below the checklist through `ethos-orchestrate`
  without a separate Ethos heading

```markdown
### Recommendations

These suggestions are optional and are not required to continue.

- [Suggestion, benefit, and relevant link]

### Blockers

These items prevent us from continuing with Ethos.

- [Blocked action, reason, and the action or answer needed]

[Focused question needed to resolve a blocker; end the blocked response here]

### Next Steps

[Shared three-item checklist from the core, with saved progress]

[Ethos explanation and next action]
```

Omit empty Recommendations and Blockers sections. Required runtime setup or
credentials for later evaluation runs stay in the table as later requirements,
not optional recommendations or blockers to Ethos. Missing Ethos skills can block
this step; missing `ETHOS.md` alone does not, because the Ethos skills can create it.
When entering Ethos, explain below the checklist that `ETHOS.md` records what the
agent should do, what it should avoid, and what a good result looks like, then
present the orchestrator's review or next question. If blocked, preserve progress
without displaying the checklist or starting dependent Ethos work.

## Confirm Ethos

Invoke [Ethos confirmation](ethos-confirmation.md) for repository-root `ETHOS.md`. Reuse confirmed unchanged
content and prior answers. Record the returned path, validation result, confirmed
digest, remaining intent questions, and stage. Do not infer content approval from
file presence, structural validity, or an implemented behavior. Do not write the
canonical document before its content is confirmed.

## Pause after Ethos confirmation

After the saved `ETHOS.md` has been validated and its content reviewed and
confirmed, link the file and briefly report that result. A draft approval or file
write alone does not complete this step. If an issue blocks the selected next
step, show **Blockers** with “These items prevent us from continuing with your
evaluations.” and omit Next Steps, the checklist, and readiness language.

Otherwise, show this in the final visible response, then **end the turn**:

```markdown
### Next Steps

[Shared three-item checklist with saved progress]

We’re ready to build your evaluation suite. Ready to continue?
```

Use the core's exact checklist wording and selected third item. Mark prerequisites
and Ethos complete only when their work is complete; leave the selected evaluation
step unchecked and derive the current marker from shared state. When the first
two steps are complete, the third is current. For audit, use “We’re ready to check what your existing
evaluations cover. Ready to continue?” For expansion, use “We’re ready to improve
your existing evaluations. Ready to continue?” This indicates readiness to begin
that flow, not proof that agent execution is configured or has succeeded.

Save this continuation question as `pending_question` and the selected downstream
skill as `next_handoff`. Preserve confirmed Ethos and prerequisite findings. Do
not ask source-selection or case-planning questions, inspect new suite or trace
sources, probe runtimes, create evaluations, or delegate downstream work in this
turn. The pause must remain visible after tools finish; commentary alone is not
sufficient.

After the user continues, clear the pending continuation question and hand off
with the saved context. Do not repeat this pause when resuming downstream work
that has already started. Keep later execution requirements explicit and pause
only the work they actually block.

## Save and resume

`.eval-author/.working/journey-state.json` is the sole authority for workflow
stage, prerequisite findings and answers, confirmations, pending questions, and next action.
Use `scripts/journey_state.py --repo <repository> <command>` for `show`, `init`,
`update`, `confirm-ethos`, and `import-legacy`; inspect its help for command inputs.
Its validation and atomic writes are allowed bounded local commands, not runtime
probes. State identifies the repository, agent, goal, selected setup, answer
provenance, Ethos path/digest, and journey-specific progress. Never store secrets.

Save agreed decisions in the selected journey's `scopes` entry.
Put final task behavior in task READMEs and audit checks
in the audit report. Do not create a separate first-eval or expansion plan file.
Retire `first-eval.md`, `audit-progress.md`, and intent notes as active writers.
Import legacy records once as unreviewed snapshots with provenance. Review the
imported decisions before applying them; old notes never establish approval
automatically, even when they describe a prior approval. Preserve conflicts and malformed
records for resolution; do not replace them with fresh success state.

Resume the pending question or earliest unfinished, non-deferred step. A goal
change reuses applicable common decisions. A changed agent, setup, or Ethos
reopens only affected work; changed Ethos invalidates its confirmation until
reconfirmed while retaining source findings and prior intent answers. Persist
source findings, answers, exact draft content/reference, and the pending question
at each Ethos pause; reload them before resuming or switching journeys. Existing
downstream evidence is retained and marked stale when
appropriate, never rewritten to match new decisions.

All new working files, intermediate inputs, and temporary files live under
`.eval-author/.working/`. Historical evidence with bound paths remains readable.
Review imported snapshots before choosing how to continue interrupted legacy
tasks. Keep those tasks at their original paths, preserving bound evidence and
relative assets; explicitly select the active legacy task root in the shared
journey scope when continuing it. Do not treat historical duplicate copies as
additional active tasks. New tasks use the stable task catalog. Old progress
notes are import-only; never resume writing them. If the repository cannot
be written, retain progress in conversation, explain that fresh-chat resume is
unavailable, and continue independently possible work.

## Hand off

After the user continues from the completion pause, pass the selected journey,
repository/agent, setup and prerequisite evidence and answers,
confirmed Ethos result, and unresolved dependencies. First-eval continues with
its source/scope decision; audit continues with selected evals and traces;
expansion follows the core's explicit expansion handoff. Never repeat shared
onboarding or convert confirmation into a claim of successful runtime execution.

## State helper commands

Run with the assistant's Python 3.11+; this helper uses the standard library.
Replace `<skill_dir>` with the core skill directory. Keep update payloads under
`.eval-author/.working/tmp/` and use the revision returned by `show` to avoid
replacing newer decisions.

```bash
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository show
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository init \
  --agent account-support --journey create --setup gym
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository update \
  --input /absolute/repository/.eval-author/.working/tmp/answers.json \
  --expected-revision 1
```

`--journey` accepts `create`, `audit`, or `expand`; `--setup` accepts `gym` or
`harbor`. `init` reuses state and invalidates affected facts when identity or
setup changes. `show` derives checklist status without writing. Do not maintain
a separate saved checklist.

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

`scopes` is keyed by journey and holds agreed source bounds, scope tables,
case inventory, and downstream decisions. Updating a journey's scope replaces
that entry, so read and preserve unaffected fields first. `journey_status` maps
a journey to `pending`, `in_progress`, or `complete`. Blockers have `id`, `phase`
(`setup`, `ethos`, or a journey), and `message`. Pending question and next handoff
are strings or null. Reopening a dependency must not silently erase old evidence.

During Ethos work, the `ethos` update accepts:

| Field | Value |
|---|---|
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
resume instead of reconstructing intent or repeating answered questions. The
`write` stage requires confirmed draft content. `show` invalidates approval of
a changed or missing draft while retaining prior answers. Changed canonical
Ethos similarly reopens its review without discarding the intent history.

Only after the canonical workflow returns exact confirmed saved content and
successful validation may the adapter call:

```bash
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository confirm-ethos \
  --digest <64-character-sha256> --source <user-confirmation-reference> \
  --validation <actual-validation-result-reference>
python3 <skill_dir>/scripts/journey_state.py --repo /absolute/repository import-legacy
```

The digest is for the exact saved bytes of root `ETHOS.md`. It cannot be set via
an arbitrary state update. `import-legacy` retains old notes as unreviewed
snapshots; review them before recording current decisions. Errors leave the
prior valid record intact and never imply that prerequisites or Ethos passed.
