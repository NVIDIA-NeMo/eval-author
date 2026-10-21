<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Journey 0: shared prerequisites and Ethos

The core owns orientation and goal selection. This procedure owns the common
entry for creating, auditing, and improving evals, including direct skill calls.
It supports Gym and Harbor through the same flow. Scoped operations retain their
existing input requirements and do not acquire a full onboarding checklist.

## Confirm the agent and setup

Read the repository's supplied agent files and documentation with bounded reads.
Reuse known identity, repository paths, runtime choice, and answers. Ask for an
accessible path or agent identity only if unresolved. Do not enumerate or read
traces before the user selects their source and location.

Honor the user's Gym or Harbor choice, otherwise preserve the selected suite's
setup, otherwise use the existing Harbor default. Keep format compatibility
separate from availability: a first suite needs an intended supported format,
not an existing task. Explain unsupported material without converting it or
switching the selected runtime.

Read the shared `scripts/runtime-policy.json` authoring requirements for the
selected setup and its Python version. Scoped audit checks retain their own
operation-specific policy; they do not lower Journey 0’s authoring requirements. Confirm only outstanding prerequisites together: installed skills
and file/command permissions, the applicable runtime, required environment and
service/data access. Do not ask for credentials or secret values. Credentials
are required only for the execution that uses them. Optional assistant skills
remain optional. Reuse supplied answers and explain what each missing item blocks.

Journey 0 does not run the runtime locator, install software, repair setup, or
execute agents. Record answers as **user-confirmed**, never tested or ready.
Missing setup leaves dependent work pending while independent agent review,
Ethos work, and planning continue. Runtime verification belongs to the downstream
operation that needs the runtime and reuses these answers.

## Confirm Ethos

Invoke [Ethos confirmation](ethos-confirmation.md) for repository-root `ETHOS.md`. Reuse confirmed unchanged
content and prior answers. Record the returned path, validation result, confirmed
digest, remaining intent questions, and stage. Do not infer content approval from
file presence, structural validity, or an implemented behavior. Do not write the
canonical document before its content is confirmed.

## Save and resume

`.eval-author/.working/journey-state.json` is the sole authority for workflow
stage, prerequisite answers, confirmations, pending questions, and next action.
Use `scripts/journey_state.py --repo <repository> <command>` for `show`, `init`,
`update`, `confirm-ethos`, and `import-legacy`; inspect its help for command inputs.
Its validation and atomic writes are allowed bounded local commands, not runtime
probes. State identifies the repository, agent, goal, selected setup, answer
provenance, Ethos path/digest, and journey-specific progress. Never store secrets.

Present scope tables in conversation and save agreed decisions in the selected
journey's `scopes` entry. Put final task behavior in task READMEs and audit checks
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

Pass the selected journey, repository/agent, setup and prerequisite answers,
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
Each prerequisite has `status` (`confirmed`, `missing`, `unknown`, or
`not_required` where applicable) and `source` identifying the actual user answer
or source. The keys are `code_access`, `file_writes`, `command_execution`,
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
