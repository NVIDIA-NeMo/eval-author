<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Onboarding conversation rendering

Use the [shared conversation contract](../conversation-rendering.md) and
[conversation_renderer.py](../../scripts/conversation_renderer.py) for
orientation, prerequisites, and Ethos review and confirmation across creation,
audit, and expansion. This reference defines the currently supported onboarding
stages; it does not replace the [shared prerequisite procedure](../shared-prerequisites.md)
or [Ethos adapter](../ethos-confirmation.md).

[conversation_templates.py](../../scripts/conversation_templates.py) owns shared
journey and checklist labels, status labels, and generic headings.
[onboarding_templates.py](../../scripts/conversation_flows/onboarding_templates.py)
owns onboarding copy and prerequisite rows. Use these canonical templates through
the renderer; do not copy them into instructions or reconstruct their output.

## Orientation and prerequisite entry

Before readiness, the only allowed command is this renderer's no-input preview
mode. It may load its own source dependencies. Use Python's `-B` flag to prevent
bytecode writes. Do not pass a repository, state file, or input file, create a
payload artifact, call the state helper, or inspect repository context.

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
repeating the welcome. Use the output as the entire final visible response and
end the turn. Selecting a goal does not answer the readiness question.

After the user actually answers the readiness question, show the initial status
table with its canonical grounding sentence as the first workflow output:

```bash
python3 -B <skill_dir>/scripts/conversation_renderer.py --stage prerequisites --journey create --ready
```

This stage emits the canonical progress sentence followed by the selected
journey's table. Most rows default to
Checking. The audit traces row defaults to Unknown with the canonical
`TRACE_FINDING` requirement; authoring agent access defaults to Needed before
agent runs. The trace requirement stays visible without selecting a trace
source or authorizing trace inspection.

Include already-known facts from the current conversation through `facts.rows`.
Do not read saved state, repository files, or other sources to fill the preview,
and do not probe first. The CLI form above uses defaults; `--input -` can receive
an inline JSON payload with `stage: prerequisites`, the selected `journey`,
`ready: true`, and optional `facts: {rows: ...}`. No state or revision is supplied.
The only allowed preview facts are rows: no Recommendations, Blockers, Ethos,
draft, or question facts. Use the same row IDs and factual record fields as
below, with actual findings and source provenance for known statuses.

Preview statuses may be `checking`, `verified`, `confirmed`, `missing`, `unknown`,
or applicable `not_required`. `checking` is allowed only in this initial table;
known facts must retain their actual evidence status. A user-supplied availability
answer can appear as Confirmed by you without claiming verification. The
`--ready` flag or `ready: true` represents the actual readiness reply, never
permission inferred by rendering.

After displaying the table, perform the bounded
[prerequisite checks](../shared-prerequisites.md). Complete and record them before
target Ethos inspection; present the completed table in the final reply as
described below. Add no extra preamble, skill-use announcement, or prose checking
notice around the canonical rendering. Apply this sequence to creation, audit,
expansion, and prerequisite resumes.

This guarantee covers skill-generated workflow messages. Mandatory host or tool
UI instructions still apply and are outside the renderer's output contract.
Do not generate an extra notice merely to satisfy a local skill convention.

## When state is unavailable

If a prerequisite `init`, `show`, or `update` fails, finish any independently
accessible prerequisite checks and render `prerequisite_failure` directly as
the final reply. This is the narrow fallback for unavailable state, including
a fresh read-only repository. It does not require a state file or permission to
write one. Preserve any existing state; do not fabricate a snapshot, reset it,
or claim the failed operation succeeded.

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
approval, continuation invitation, or prerequisite-success claim. Stop before
target Ethos inspection and downstream work. Once the state problem is resolved,
reload any existing state, recheck affected facts, and record the prerequisite
pass before using the normal stateful responses.

## Stateful responses

During prerequisites, use `journey_state.py --state-only` for `init`, `show`, and
`update`. Record findings, relevant blockers, and the pending Ethos action from
supplied context or saved metadata without inspecting target artifacts. Read a
fresh `--state-only show` snapshot and extract its `state` to establish the
completed pass before locating, reading, or validating the target `ETHOS.md`,
recovering a draft, or starting Ethos preflight.

If blocked, render `prerequisite_results` with that state's exact revision and
prerequisite facts directly as the final response once. It includes the canonical
completed-results sentence, table, optional Recommendations, and Blockers. Do not
first publish it in progress or inspect target Ethos artifacts. Supply no Ethos,
draft, or question facts to this stage. It never renders a Next Steps checklist,
saved pending question, or continuation invitation. A canonical blocker can
supply its own focused prerequisite question.

If unblocked, do not publish an intermediate completed report. Continue in the
same turn without a new approval pause, using normal `show` or `load_state` to
reconcile changed or missing Ethos, drafts, and task evidence. Reading installed
Ethos skill instructions during checks establishes only availability and
compatibility; target-artifact work starts only after the prerequisite pass is
complete and saved. Save returned Ethos findings and the pending decision, then
load normal state again immediately before the final `results` rendering.
Supply the completed prerequisite findings, optional Recommendations, and
applicable Ethos, draft, question, and blocker facts. The final owns the canonical
completed-results sentence, table, Next Steps, one state-derived checklist, and
the inspected Ethos or draft with the current question.

An unblocked prerequisite turn shows an initial status table and a completed
table in the final reply, with one final checklist. A blocked turn shows the
initial table and one completed report in final, without a checklist. Never add
a third table or repeat the completed report in progress. Use `ethos` for later
Ethos-only actions without repeating prerequisite findings. `results`, `ethos`,
`confirmed`, and `resume` stages require normal artifact-reconciled state, never
a state-only snapshot. The renderer never checks artifact files or digests
itself, so an old in-memory state is insufficient. Use the returned `state`
object and its exact `revision`; do not construct a success state or maintain a
separate checklist. No additional state flags are needed for this presentation.

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
| `confirmed` | Saved complete Ethos and applicable completed prerequisites, before downstream work has started; saved document confirmation and the selected continuation pause, unless a relevant blocker prevents continuing |
| `resume` | Saved pending decision or unfinished work; current progress and question, without a welcome, initial readiness question, or invented continuation approval |

Known continuation prompts
from earlier wording retain the same gate and render with current canonical copy;
rendering does not rewrite the saved question.

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
point to the exact repository-root `ETHOS.md` from state. Supply a grounded
summary for an existing-document review. File presence or successful structural
validation is not current intent confirmation.

After a completed, saved, unblocked prerequisite pass, when the saved Ethos stage is `draft` or
`write` and `state.ethos.draft` exists, supply `facts.draft` with the exact recorded
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
or place an input payload under the existing `.eval-author/.working/tmp/`
directory after readiness. Do not pipe a raw `show` response directly into the
renderer: wrap its `state` with the stage, matching revision, and factual display
inputs. Use state-only output only for `prerequisite_results`; normal artifact
reconciliation precedes other stateful stages.

After the user answers, persist the real reply and follow the existing handoff.
Rendering `confirmed` alone cannot start downstream work, and rendering `resume`
cannot answer a saved question. An already-started downstream journey resumes
its pending work without repeating the completion pause.
After a goal switch, retained started or completed journey progress also prevents
an inferred new continuation pause when the saved question is empty. An explicitly
saved continuation question from a fresh review is still presented; rendering
does not record its answer or change the selected journey's progress.
