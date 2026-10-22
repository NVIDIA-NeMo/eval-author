<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Save and resume first evaluations

Use the existing `journey_state.py show` and `update` commands. Save first-evaluation progress
under `scopes.create.first_eval` in `.eval-author/.working/journey-state.json`.
The shared file remains schema version 1. Do not create another first-eval plan
file, infer approval from legacy notes, or copy native results into workflow
state. Keep native evidence and task files at their existing paths.

`show` returns a derived `first_eval` object alongside the shared checklist.
Its facts are the input to the fixed first-evaluation conversation renderer. They
describe whether recorded observations still apply; they do not run a provider,
attest a remote deployment, or independently judge a verifier's correctness.
Record an observation only after performing and inspecting the named check.
Evidence file presence alone never establishes the recorded observation.

## The additive record

Start with this shape after the shared prerequisites and ETHOS.md handoff. Populate
the plan from the concrete proposal, including presentation content required by
the conversation renderer, before requesting approval:

```json
{
  "schema_version": 1,
  "provider": "harbor",
  "ethos_digest": "<confirmed ETHOS.md SHA-256>",
  "plan": {
    "tasks": [
      {"id": "refund-policy", "example_ids": ["refund-eligible", "refund-ineligible"]}
    ],
    "corpus": "No traces; agreed synthetic examples",
    "breadth": "Initial refund tasks",
    "exclusions": ["Other regions"]
  },
  "plan_approval": null,
  "first_task": "refund-policy",
  "tasks": {},
  "selection": null,
  "runs": [],
  "run_review": null,
  "audit_invitation": null
}
```

`provider` is `harbor` or `gym`. The plan contains a nonempty `tasks` list, each
with a unique stable `id` and nonempty unique `example_ids`. Keep the request,
behavior, expected outcome, grading, ETHOS.md references, source selection,
priorities, breadth, exclusions, proposed agent/model, attempts, costs, and open
questions in the plan. Extra planning and presentation fields are retained and
included in its digest. `first_task` identifies exactly one planned task whose
implementation the user will review before the remaining tasks are generated.
A native Gym task may contain several explicitly selected examples/rows.

Read `first_eval.plan_digest` from `show`. After the user agrees to that exact
proposal, save `plan_approval: {"digest": "<plan_digest>", "source": "<actual user
decision reference>"}`. The digest includes the plan, first-task selection,
provider, and confirmed ETHOS.md. Save this approval in a separate update before
recording the first created task. A revised plan retains prior artifacts, but
its earlier approval no longer applies. Reconcile retained and added examples;
do not reduce the agreement to whatever happened to finish.
If the shared flow marked the creation scope `needs_review`, clear that flag
and record its `confirmed` decision only when the revised plan is actually agreed.

Updates that contain the structured creation scope require `--expected-revision`
from the latest `show`. They preserve omitted sibling fields of `scopes.create`
and other journeys. The `first_eval` record itself is replaced as a whole: load
it, retain unaffected fields, change the intended records, and submit the complete
record. A stale revision or malformed update leaves the previous file intact.
Keep payloads under `.eval-author/.working/`.

If shared state marked `scopes.create.needs_review` after an agent, provider, or
`ETHOS.md` change, resolve that changed context and obtain approval of the revised
proposal first. Then explicitly set `needs_review: false` alongside the saved
agreement. A matching plan digest alone does not clear that shared review flag.

Updates automatically retain superseded plan agreements, task observations,
selections, native-run observations, exact run reviews, and audit decisions in
optional `history`. Each snapshot records its kind/key, originating shared-state
revision, prior record, and content digest. Callers may omit this generated field;
the helper carries it forward. Existing history cannot be rewritten. Retire a
task by removing it from the plan, retaining its task record and native evidence.
Correct mistaken native-run metadata under its actual ID; the prior observation
is retained and the old review digest becomes stale. An actual new execution
requires a fresh native run ID and output paths. Prior native run records cannot
be silently deleted to make the results look cleaner.

## First-task approval and task observations

Each `tasks` entry is keyed by planned task ID:

```json
{
  "path": ".eval-author/tasks/refund-policy",
  "sha256": "<current task revision>",
  "review": null,
  "environment": null,
  "validation": null,
  "limitations": ["Only the agreed regional fixtures are exercised"]
}
```

New task paths are `.eval-author/tasks/<slug>` and remain stable through review,
validation, execution, and acceptance. Calculate `sha256` with
`first_eval_state.task_digest(repo, relative_path)`, which uses the existing
task-evidence recorder's revision rules, including task README content. A
changed task requires refreshing the applicable observations; preserve old
native jobs and receipts instead of rewriting them to match new bytes.

`environment` and `validation` are independent observations with this shape:

```json
{
  "sha256": "<task revision checked>",
  "status": "passed",
  "source": "<observed check and its retained result location>",
  "evidence": [{"path": "<repository-relative evidence path>", "digest": "<file SHA-256>"}]
}
```

Their status is `passed`, `failed`, or `blocked`. Environment evidence refers to
the environment sub-flow's actual smoke/persistence/reset proof and the kit
inputs it proves; task controls do not replace that proof. Validation evidence
refers to the declared provider-native checks and controls, their receipts,
and retained native artifacts. Include all files that determine applicability,
including shared Gym component/configuration and dataset inputs outside the task
tree. A reference is always a repository-relative `path` plus lowercase SHA-256
`digest`; symlink traversal and path escapes are rejected. Never put secrets in
state, sources, config arguments, or evidence descriptions.

Finalize task README content before validation. Once evidence is bound, update
run summaries and result links in the suite README outside the task tree. A task
README edit changes the recorder's existing task revision; preserve that fact
and plan affected work explicitly rather than automatically spending credentials
on new executions merely to refresh documentation.

Use `review: {"sha256": "<reviewed revision>", "decision": "approved",
"source": "<actual user feedback reference>"}` after first-task approval.
`changes_requested` retains feedback while leaving that approval unresolved.
The first task needs current environment proof and the user's saved review
approval before any remaining task is recorded. Formal grading controls can
still be pending at that checkpoint. Save the approved first task in a separate
update before recording remaining tasks. Its approval covers the demonstrated
approach; it does not claim the user individually reviewed later task files.
Later reviews can be recorded when they occur, but are not extra human approval
gates or required for completion. Every selected task still needs actual native
validation before the selected agent run can establish suite completion.

The helper derives `created`, `reviewed`, `environment_proven`, `validated`, and
`executed` separately for each planned task. `first_task_approved` requires the
current first-task review and environment proof. Missing credentials retain
these observations and the independent task work. Record the execution blocker,
pending question, and next handoff in the shared fields without restarting
prerequisites or ETHOS.md.

## Explicit selection and actual execution

`selection` records the verified native configuration:

```json
{
  "config": {"path": ".eval-author/run.yaml", "digest": "<file SHA-256>"},
  "cases": [{"task_id": "refund-policy", "example_ids": ["refund-eligible", "refund-ineligible"], "attempts": 1}],
  "agent": "<actual configured agent>",
  "model": "<actual configured model>",
  "source": "<native configuration/selection inspection reference>"
}
```

An optional `inputs` list binds additional configuration inputs with file
references. For native Gym it is required: include the selected dataset and
applicable component/runtime/reset inputs, and give each selected case a `rows`
list containing one unique positive one-based dataset row per `example_ids`
entry, in matching order. `attempts` is the positive repeat count for each
selected row in Gym and for each selected native task in Harbor. The complete
selection must match the agreed task/example set; unrelated drafts are excluded.
Native configuration inspection must establish the saved selection. This helper
does not parse arbitrary provider configurations or prove an agent connection.

`show` returns `selection_digest`, binding the selection, approved plan digest,
and selected task revisions. Execution/spend authorization is independent
of scope approval. Carry forward applicable prior authorization or the user's
new answer as optional `selection.authorization: {"digest": "<selection_digest>",
"source": "<actual authorization reference>"}`. Carry earlier consent forward only
when this concrete selection still falls within its authorized execution and
spend bounds; do not infer that relationship from scope approval. Authorization itself is excluded
from the selection digest. Changing agent/model, attempts, selected cases, or
config/input digests invalidates the old authorization. `execution_authorized`
is false until the selection and its authorization agree.

Each retained `runs` item has:

- `id`: the exact native job/run identity; `role`: `agent` or `control`;
  `status`: `completed` or `infrastructure_error`.
- `selection_digest`, the executed `task_revisions` map from task ID to digest,
  `source` identifying the actual inspected run, and nonempty `evidence` file
  references for its saved native artifacts.
- `attempts`: each has distinct `id` and `native_id`, `task_id`, `example_ids`,
  `status`, a `rewards` map of metric names to finite numbers, and nonempty
  `evidence` references. Use `completed` or `infrastructure_error` for status.
  Missing output is an execution error, never a zero reward. A legitimate
  observed zero reward is a completed attempt when execution itself worked.

Harbor attempts map to the examples packaged in their selected task. Each Gym
attempt identifies one selected row/example; repeated rows do not increase the
distinct-example total. Retain exact native artifact identities in both cases.
A partial job can retain successful per-task execution while the whole suite
remains incomplete. Controls cannot establish actual-agent execution. These
records are observed facts with provenance, not fabricated receipts or a new
adapter format. The existing gap-closure `verify_receipts` function requires two
successful agent runs and traces, so do not use it unchanged for first evaluations.

When recovering a partial run, retain its successful results and execute only
the required retry subset within applicable authorization. A run's
`task_revisions` may identify that selected subset. Retain and inspect each
invocation's actual configuration, agent/model settings, and command in its
evidence; `selection_digest` binds the agreed baseline being completed and does
not claim that every job executed every task. Do not combine incompatible
settings or silently authorize additional attempts.

Set optional `selected_run_ids` on the `first_eval` record to explicitly choose
the native jobs that together establish that baseline. Their successful attempts
must reconcile exactly to the agreed example/attempt counts. Reused native
attempt identities, controls, stale jobs, and unrelated examples cannot count.
Failed attempts remain visible but contribute no successful completion. Do not
create a fictional aggregate native job or copy old results into a retry. If
`selected_run_ids` is absent, one complete native run is sufficient and jobs are
never automatically summed. `show.first_eval.suite` exposes the selected IDs,
planned/completed/observed attempts, planned/executed unique examples, completion,
and reconciliation errors. Its `job_errors` list retains the IDs of selected
jobs with infrastructure errors even when successful retry coverage completes
the agreed baseline. A single error job cannot complete the suite merely because
it was explicitly selected. Stale or malformed selected attempt evidence remains
a blocker, rather than being hidden by another job's successful outputs.

## Review, completion, and the optional audit

`show` returns `run_digests`. Save the exact reviewed evidence as:

```json
{
  "run_id": "<exact native job/run>",
  "attempt_id": "<exact retained attempt id>",
  "digest": "<run_digests entry>",
  "source": "<actual results walkthrough and user feedback reference>",
  "limitations": ["Native ATIF was not emitted", "Report feedback awaits the external report implementation"]
}
```

Store this under `run_review`. Review may cover an informative partial or error
run; `reviewed_run_current` does not mean the whole suite completed. The derived
`runs` map exposes each run's `reviewable`, `complete`, and per-attempt
`reviewable`/`completed` facts. Exact result identity and limitations survive
resumption; a newer unrelated job does not replace the reviewed run.

Derived `complete` requires the agreed scope, approved first-task implementation,
current validation of all selected tasks, an actual-agent native run (or explicitly
selected recovery runs) containing the selected attempts with recorded rewards,
and review of an exact selected run and attempt. It does not require a positive reward, repeated successes, native ATIF, or
human approval of every remaining task. Keep partial/blocked work visible and
leave shared `journey_status.create` open. Once the conditions are satisfied,
record `journey_status.create: "complete"`. File/config/ETHOS.md changes make affected
claims stale on read without rewriting the state file or historical evidence.

Only after completion, record `audit_invitation` as `{ "status": "pending",
"source": null }`, or `accepted`/`declined` with the actual user-decision source.
Offering or declining never changes journeys. On acceptance, call the existing
`init --journey audit` transition. It preserves completed creation, its exact
reviewed run, and the confirmed ETHOS.md while reopening audit-specific prerequisite
applicability. Missing audit traces do not undo completed creation. Do not start
an audit merely because a baseline or a report exists.

Legacy unstructured creation scopes stay readable and retain their decisions,
but cannot establish first-evaluation completion. Review and explicitly carry forward
their decisions and artifact evidence into the additive record; do not interpret
historical checkboxes as current approval. Report content and schema changes
remain outside this contract; record report limitations without inventing proof
or creating another authority for workflow state.
