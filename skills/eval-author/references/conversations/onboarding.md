<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Onboarding conversation rendering

Use the [shared conversation contract](../conversation-rendering.md) and
[conversation_renderer.py](../../scripts/conversation_renderer.py) for
orientation, prerequisites, and Ethos review and confirmation across creation,
audit, and expansion. This reference defines the currently supported onboarding
stage inputs and outputs. Follow the [shared prerequisite procedure](../shared-prerequisites.md)
for the operational sequence and response channels, and the
[Ethos adapter](../ethos-confirmation.md) for document work.

[conversation_templates.py](../../scripts/conversation_templates.py) owns shared
journey and checklist labels, status labels, and generic headings.
[onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py)
owns onboarding copy and prerequisite rows. Use these canonical templates through
the renderer; do not copy them into instructions or reconstruct their output.

## Orientation and prerequisite entry

`orientation` and `goal_selected` use the no-input preview mode under the
[core entry rules](../../SKILL.md#choose-the-entry-route). They accept no repository,
state, revision, facts, or input-file context. Source dependencies may be loaded;
use Python's `-B` flag to prevent bytecode writes.

```bash
python3 -B <skill_dir>/scripts/conversation_renderer.py --stage orientation
python3 -B <skill_dir>/scripts/conversation_renderer.py --stage orientation --journey create
python3 -B <skill_dir>/scripts/conversation_renderer.py --stage goal_selected --journey audit
```

Use one command appropriate to the current turn, replacing `<skill_dir>` with
the core skill directory. Journeys are `create`, `audit`, and `expand`.
`orientation` without a journey uses the shorter welcome and asks for the goal,
without introducing a checklist. With a known journey it shows the full welcome,
matching checklist, and readiness question. `goal_selected`
shows the selected goal's introduction, checklist, and readiness question, without
repeating the welcome. Selecting a goal does not establish readiness; the core
owns the orientation and goal-selection pauses.

For an explicit audit request, the core's
[returning-audit procedure](../shared-prerequisites.md#return-to-an-audit)
governs entry and whether the [returning audit welcome](#returning-audit-welcome) applies.

`prerequisites` requires the selected journey and the actual readiness reply:

```bash
python3 -B <skill_dir>/scripts/conversation_renderer.py --stage prerequisites --journey create --ready
```

This stage emits the canonical progress sentence followed by the selected
journey's table. Most rows default to
Checking. The audit traces row defaults to Unknown with the canonical
`TRACE_FINDING` requirement; authoring agent access defaults to Needed before
agent runs. The trace requirement stays visible without selecting a trace
source or authorizing trace inspection.

`facts.rows` accepts only already-known facts from the current conversation;
[the shared procedure](../shared-prerequisites.md#check-the-agent-and-setup)
controls when inspection may begin. The CLI form above uses defaults; `--input -` can receive
an inline JSON payload with `stage: prerequisites`, the selected `journey`,
`ready: true`, and optional `facts: {rows: ...}`. No state or revision is supplied.
The only allowed preview facts are rows: no Recommendations, Blockers, Ethos,
draft, or question facts. Use the same row IDs and factual record fields as
below, with actual findings and source provenance for known statuses.

Use the statuses and evidence requirements in [Prerequisite row facts](#prerequisite-row-facts).
The initial preview additionally accepts `checking`; it is not a final status.
Known facts must retain their actual evidence status. A user-supplied availability
answer can appear as Confirmed by you without claiming verification. The
`--ready` flag or `ready: true` represents the actual readiness reply, never
permission inferred by rendering.

## Returning audit welcome

`audit_welcome` accepts only `stage`, the complete `state` from the fresh
state-only audit snapshot, and its exact `revision`. Use `--input -`; this stage
is not a no-input preview. The saved journey must be `audit`; no `ready`,
`journey`, or `facts` overrides are accepted.

It renders the canonical welcome, state-derived checklist, and saved-answer
reuse notice from [onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py).
The checklist reflects saved prerequisite progress; Ethos and downstream steps
remain unchecked until artifact reconciliation, without invalidating saved approval.

The [entry procedure](../shared-prerequisites.md#return-to-an-audit) owns when to
publish or omit this welcome. The stage emits no question, preserves saved state,
and grants no inspection or execution permission.

## When state is unavailable

`audit_entry_failure` is a stateless response for an observed saved-state error
during audit entry. Pass the payload through `--input -` on stdin:

```json
{
  "stage": "audit_entry_failure",
  "journey": "audit",
  "facts": {"state_error": "Saved progress belongs to a different repository."}
}
```

Supply only the observed nonempty error, at most 2,000 characters. This stage
accepts no readiness, state, revision, or row facts, and is not a no-input
preview. It emits only the shared Blockers heading and template-owned recovery
instruction; it cannot establish prerequisites, confirmation, or absence of
prior work. Rendering does not change entry routing or authorize inspection.
Preserve the original state and follow the applicable recovery procedure.

`prerequisite_failure` is the stateless response selected by the shared
[state-failure procedure](../shared-prerequisites.md#when-prerequisite-state-is-unavailable).
It requires no state file or write access and accepts no fabricated snapshot.

Pass the payload through `--input -` on stdin, without creating an input file:

```json
{
  "stage": "prerequisite_failure",
  "journey": "create",
  "ready": true,
  "facts": {
    "state_error": "Permission denied while initializing shared progress.",
    "rows": {
      "access": {
        "status": "missing",
        "findings": "The repository is readable, but shared progress cannot be written.",
        "source": "Observed state-helper permission error"
      }
    }
  }
}
```

Use the actual selected journey and readiness reply. Supply a bounded description
of the observed helper error (at most 2,000 characters) in `facts.state_error`.
Optional `facts.rows` contains observed findings and their sources. Unchecked rows remain Unknown;
`checking` is not a final status. No `state`, `revision`, recommendations,
custom blockers, Ethos, draft, or question inputs are accepted. This stage is
not available through the no-input `--stage` preview mode.

The renderer supplies the completed-results introduction, findings table, fixed
state-error blocker, and notice that these findings remain in this conversation
and cannot be resumed in a fresh chat until saved. It emits no checklist,
approval, continuation invitation, or prerequisite-success claim. Recovery and
the boundary before Ethos work remain owned by the linked procedure.

## Stateful responses

Use the [shared presentation sequence](../shared-prerequisites.md#present-results-and-the-next-step)
to select the stage and response channel. `prerequisite_results` accepts the
`state` extracted from a fresh `journey_state.py --state-only show` response and
its exact revision. Its facts are limited to rows, Recommendations, and active
Blockers. It accepts no Ethos, draft, or question facts and renders no checklist,
saved pending question, or continuation invitation; a canonical blocker may
supply its own prerequisite question.

`results`, `ethos`, `confirmed`, and `resume` require artifact-reconciled state.
Read it with `journey_state.py show` without `--state-only`, or call `load_state`
with artifact checks enabled. The renderer never checks artifact files or digests itself, so an old in-memory state is
insufficient. Use the returned `state` and exact `revision`; do not construct a
success state, maintain a separate checklist, or add presentation state flags.
The stage table below defines each response's supported content.

The JSON payload has this shape. Bracketed descriptions below explain values;
replace them with actual facts, and omit optional facts that are unavailable.
The blocker entry shown here is for a custom blocker; canonical blocker inputs
are described below.

```json
{
  "stage": "results",
  "revision": 4,
  "state": "<the complete reconciled state object, not this string>",
  "facts": {
    "rows": {
      "runtime": {
        "status": "verified",
        "findings": "<bounded description of the observed check>",
        "source": "<actual retained check evidence or reply reference>",
        "links": [{"label": "<evidence label>", "target": "<absolute path or HTTP(S) URL>"}]
      }
    },
    "recommendations": [
      {"text": "<optional suggestion and benefit>", "link": "<relevant documentation>"}
    ],
    "blockers": [
      {"id": "<saved blocker ID>", "step": "<affected action>", "reason": "<why it is blocked>", "needed": "<required change or answer>"}
    ],
    "ethos": {
      "status": "existing",
      "link": "<actual ETHOS.md link>",
      "summary": "<grounded summary of intended behavior>"
    },
    "draft": {
      "link": "<absolute path to the exact saved working draft>",
      "digest": "<recorded state.ethos.draft.digest>"
    },
    "question": "<the current unresolved question saved in state>"
  }
}
```

| Stage | Required context and rendered response |
|---|---|
| `prerequisite_results` | Fresh state-only snapshot and recorded checks; canonical completed-results sentence, table, optional Recommendations, and Blockers directly in final when blocked; no checklist or target-artifact facts |
| `results` | Routine final after a completed, recorded prerequisite pass and normal artifact reconciliation; canonical completed-results sentence, table, optional Recommendations, then Blockers or Next Steps with one checklist and the current Ethos action; also available for standalone full reports |
| `ethos` | Later Ethos-only action; Next Steps, state-derived checklist, and inspected Ethos/draft/question without rows or Recommendations |
| `confirmed` | Saved complete Ethos and applicable completed prerequisites, before downstream work has started; saved document confirmation and the selected pending decision. Fresh creation without a structured saved design or source choice shows the canonical first-eval source choice directly; other cases retain their continuation. A relevant blocker takes precedence. |
| `resume` | Saved pending decision or unfinished work; current progress and question, without a welcome, initial readiness question, or invented continuation approval |

When rendering `resume`, `results`, or `confirmed` for the audit journey, read
the current journey state using `journey_state.py show` without `--state-only`.
Forward the returned state snapshot's `audit_observations` as
`facts.audit_observations`, alongside its `state` and matching `revision`.
The renderer derives current audit completion and the applicable saved checkpoint
from those observations and `scopes.audit.lifecycle`; do not pass derived approval
or completion flags. Without observations, audit completion remains unchecked.
These facts are unavailable to other journeys or state-only stages. Pending audit
questions never replace unresolved prerequisite or Ethos questions, and rendering
does not persist its question projection or inspect artifact files.

Known continuation prompts from earlier wording remain readable. For fresh
creation without a structured saved design or source choice, they render as the
combined source checkpoint instead of an extra readiness gate; other cases keep
their continuation and current canonical copy. Rendering does not rewrite the
saved question or record an answer. Save the canonical source question and
`eval-author-first-eval` handoff before presenting a new combined checkpoint,
following [the shared pause](../shared-prerequisites.md#pause-after-ethos-confirmation).
Its question and choices use
[first_eval_templates.py](../../scripts/conversation_flows/first_eval_templates.py),
not a second copy of that text. The same fresh, pre-start condition applies when
`results` or `resume` reaches the completed-Ethos handoff; it does not replace a
saved-design decision or restart a journey that has already begun.

## Prerequisite row facts

Row IDs for creation and
expansion are `agent_repository`, `evaluation_framework`, `skills`, `access`,
`runtime`, `environment`, `services_data`, and `agent_access`. Audit uses
`agent_repository`, `evaluation_framework`, `traces`, `skills`, `access`, and
`tooling`. Their order and display labels are defined in [onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py).

Row statuses are `verified`, `confirmed`, `missing`, `unknown`, and
`not_required`. Only `environment`, `services_data`, and `agent_access` can be
`not_required`. Except for the canonical audit trace blocker row described below,
every non-Unknown row requires findings and an actual source; missing row facts
render as Unknown. Never fill missing evidence with a success claim. Keep
user-confirmed answers distinct from observed checks. For stateful responses,
record each finding before displaying it: non-Unknown statuses for rows mapped by
`ROW_PREREQUISITES` in the [onboarding renderer](../../scripts/conversation_flows/onboarding.py) must match their saved prerequisites. Composite
rows use this precedence: `missing`, `unknown`, `confirmed`, `verified`, then
`not_required`; an absent prerequisite key counts as `unknown`. For example,
verified runtime availability with user-confirmed Python produces a Confirmed by
you runtime row, while missing Python makes that row Missing. Unknown display
is allowed when a finding is unavailable; it cannot justify an invented
non-Unknown status.

Supply links separately through optional row `links` entries with `label` and
`target`, a recommendation's `link`, or the Ethos `link`, following the
[shared escaping and link contract](../conversation-rendering.md#facts-and-links).

## Ethos and draft facts

For Ethos facts, use `existing`, `absent`, or `unknown`. An existing Ethos link must
point to the exact selected document, formed from `state.repository` and
`state.ethos.path`. Supply a grounded summary for an existing-document review.
File presence or successful structural
validation is not current intent confirmation.

When the saved Ethos stage is `draft` or `write` and `state.ethos.draft` exists, supply `facts.draft` with the exact recorded
`digest` and an absolute `link` formed from `state.repository` and `state.ethos.draft.path`. The renderer shows
that draft link before the pending question so the user can review the proposed
content. The caller must recover and verify the exact draft returned by
`ethos-orchestrate`, persist its reference, and reconcile state before rendering;
the renderer does not inspect its path or bytes. This is the existing canonical
draft workflow, not another authoring path. Omit draft facts when no saved draft
is being presented, and never treat rendering a draft as its approval.

## Blockers

Supply structured blocker inputs for exactly the active saved blocker IDs.
The renderer selects active blockers from state:

- `setup` and `ethos` blockers always apply. At `prerequisite_results`, these
  are the only active phases because artifact checks are still deferred.
- At later stages, current-journey blockers apply only after both shared steps
  are complete.
  Before downstream work starts, `environment`, `services`, `data`,
  `agent_connection`, and `model_credentials` remain deferred; another
  current-journey blocker, such as a missing authoring route, can block the handoff.
  Once downstream status is `in_progress` or `complete`, every current-journey
  blocker applies.
- Blockers belonging to another journey remain inactive for this response.

Record each blocker's phase according to the work it actually prevents. Early
missing execution credentials remain prerequisite findings; they do not become
`setup` or `ethos` blockers when those steps can proceed. Retain deferred blocker
records for the work they will affect. Rendering never clears or rewrites them.
Incomplete setup without a saved blocker is rejected; record the actual
unresolved prerequisite before rendering.

An active blocker suppresses every Next Steps section, checklist, Ethos action,
and continuation invitation. The renderer owns canonical blocker wording for:

| Saved blocker | Canonical input and attribution |
|---|---|
| Journey `audit`, ID `data`, phase `setup`, with saved `prerequisites.data.status` `unknown` or `missing` | `id` and `skill_link` to the exact loaded `eval-author-audit/SKILL.md` |
| Journey `expand`, ID `evaluation_format`, phase `setup` | `id` and `skill_link` to the exact loaded core `eval-author/SKILL.md` |

For these blockers, use the minimal input shape:

```json
{"id": "data", "skill_link": "<verified absolute path or HTTP(S) URL of the exact loaded SKILL.md>"}
```

The caller verifies the link against the skill actually read; the renderer never
loads it or assumes an installation-directory name. The target must identify
that skill's `SKILL.md`, not its README or a reference file. Canonical
specifications own the blocker label, reason, required action, question, and
linked paragraph explaining which skill instruction requires the
pause. The audit specification also owns the traces row's status and finding,
derived from the saved availability status. Record actual availability before
rendering; Unknown and Missing remain distinct. An explicit trace-row status
must match that saved status. Findings can be omitted because the template owns
them, and the saved prerequisite source can supply an omitted row source. Row
links remain caller-verified factual inputs. Other rows and custom cases retain
factual findings, evidence sources, and the existing status checks.

The same saved canonical blocker status renders the same copy even when saved
messages or caller prose differ. Legacy `step`, `reason`, and `needed` fields may
be present but cannot override a canonical specification. Other blockers still
require their actual `step`, `reason`, and `needed` facts, including when they
appear alongside a canonical blocker. When any canonical blocker appears, the
renderer does not append the saved `pending_question`; it retains that state
unchanged and uses the canonical action or question instead.

Present the complete rendering unchanged. Do not append another pause explanation,
quote, skill attribution, question, or Next Steps section: the canonical paragraph
already supplies the linked instruction. Put any required custom blocker
question in its `needed` fact when it accompanies a canonical blocker. Clearing any blocker still requires the existing
workflow's recheck and state update, not omission from a rendering payload.

The canonical expansion-format blocker also includes the conversion handoff from
[onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py) inside Blockers. It
provides instructions and a copyable prompt for repository engineering work in a
separate coding-agent chat, with placeholders for the suite path, current
framework, and Harbor or NeMo Gym target. Supply its saved blocker and loaded
core skill link; do not duplicate the prompt. Other blockers, phases, and
journeys do not trigger this handoff. Rendering neither executes conversion nor
clears the blocker; follow the
[format recheck](../pre-reqs/improve.md#checks-and-blocking-conditions) when the user
returns with converted paths and validation results. No state or schema revision
is introduced by these display specifications.

## Render and continue

Follow the [shared input contract](../conversation-rendering.md#input-envelope)
when passing the payload to the renderer. Use stdin to avoid an extra artifact,
following the [temporary-work policy](../shared-prerequisites.md#temporary-work-and-recovery)
when a tool requires a payload file after readiness. Do not pipe the raw state
snapshot directly into the renderer: wrap its `state` with the stage, matching
revision, and factual display inputs. Use state-only output only for
`prerequisite_results`; normal artifact
reconciliation precedes other stateful stages.

After the user answers, persist the real reply and follow the existing handoff.
Rendering `confirmed` alone cannot start downstream work, and rendering `resume`
cannot answer a saved question. An already-started downstream journey resumes
its pending work without repeating the completion pause.
For the combined creation checkpoint, wait for the actual source choice before
planning or trace inspection, then save its provenance and clear the pending
question. Continue directly to the bounded task proposal under the
[first-eval contract](first-eval.md#source-and-proposal-pauses); do not render
another readiness or source-choice pause. Saved-design, known-source, audit, and
expansion handoffs keep their applicable decisions.
After a goal switch, retained started or completed journey progress also prevents
an inferred new continuation pause when the saved question is empty. An explicitly
saved continuation question from a fresh review is still presented; rendering
does not record its answer or change the selected journey's progress.
