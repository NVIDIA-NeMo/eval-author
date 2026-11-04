<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared construction contract

New construction uses `nemo.eval_author.construction.v3`, for Harbor and Gym.
Keep `construction.json` private, outside the task tree. Trace workspaces use
`private/construction.json`. Proposals may leave authority unresolved; a draft
must identify the rule or independent evidence that defines correctness.
Unresolved authority stays in the private plan and blocks construction where
needed, never becoming a runtime grading prerequisite: a shipped verifier must
emit a reward for the submission or report an actual verifier execution error.

The same contract serves first-eval, task-create and trace-derived construction.
Provider adapters interpret native observations; they do not define another
construction document. Include the applicable
[simple-cheat controls](task-validation.md#screen-simple-cheats) in the existing
cases and rationales; proposals identify verifier risks, drafts require execution
evidence before qualification.

| Field | Required contents |
|---|---|
| `schema`, `task_id`, `provider` | Schema above; stable task identity; `harbor` or `gym`. |
| `requirements` | `id`, `statement`, `authority: {basis, reference}`, `candidate_index`. Authority basis: `specification`, `user_decision`, `independent_reference`, or `unresolved`. Index is null without a bound candidate. |
| `inputs` | `path`, `sha256`, `basis`, `source`, `rationale`, `edits`, `review`. Paths are task-relative. Native inputs use `copied`/`authored`, a source description, `edits: []`, `review: null`. Trace inputs retain observed/exact-reversal/authored provenance and review. |
| `checks` | `id`, `requirements` (IDs), `success` (JSON-pointer assertion). Every requirement maps to scored checks. |
| `cases` | `case`, `kind`, `observations: [{check, expected}]`, `requirements`, `reaches`, `dependency`, `rationale`, `review`. Native cases match the result contract; unused trace metadata is `[]`/null. |
| `prerequisites` | `id`, `before`, `after`, `bypass_case`, `check`. Each bypass contradicts the dependent requirement's scored check. Usually `[]`. |
| `probes` | Trace setup/reach definitions, or `[]` for native result-contract observations. |
| `starting_state` | Trace `cutoff_turn_id`, input roots, external inputs, no-files reason and review; null for native declarations. Input artifacts live only in `inputs`. |
| `review` | Trace construction review, or null for native declarations. |
| `bindings` | Trace `candidate_sha256` and `trace_sha256` (hex SHA-256 of `safe/trace.json`), or null for native tasks. |
| `migration` | Original artifact `{path, sha256, schema}` bindings, or `[]` for a new contract. |

Assertions use `{"pointer":"/...","equals":value}` or finite `min`/`max`.
Trace checks use `/checks/<check-id>` and `PASS`/`FAIL`, read from retained
verifier rows. Negative/copy/bypass cases declare only the checks they must fail;
Oracle and valid alternatives declare every check passing. NOP declares every
check failing. Native cases use the provider's actual result fields.

The [provenance and probe field definitions](construction-qualification.md)
remain applicable to trace fields, including turn and span source references.
Input artifacts live in `inputs` with task-relative paths, and each review note
serves as its rationale. Requirements carry the bound candidate statement and an explicit
correctness authority. A source agent's answer does not establish that authority.

```bash
python <eval_author_dir>/scripts/construction_contract.py --plan <private-contract.json> --mode proposal
python <eval_author_dir>/scripts/construction_contract.py --plan <private-contract.json> \
  --mode draft --task-dir <native-task> --contract <native-result-contract.json>
python <eval_author_dir>/scripts/construction_contract.py --plan <workspace>/private/construction.json \
  --mode draft --workspace <workspace>
```

For native runs, pass this file to `task_evidence.py prepare --construction-plan`.
For trace workspace runs, the same shared evidence implementation binds the file
through `record-run-inputs`. Record a bounded repair before fresh proof for
changed task content; source control owns prior revisions.

## Import existing artifacts without rewriting evidence

`construction.v1` and `construction.v2` are refused. Trace contracts cited ATIF
steps and have no import: author them as v3 in a workspace prepared by the
current `prepare`. A native v2 contract becomes v3 by changing only its schema
id. That changes its SHA-256, so evidence manifests bound to the v2 file no
longer verify; run `task_evidence.py prepare` again with the v3 file.
`construction_plan.v1` was an earlier experimental native format; retained
experiment artifacts already use it, so its explicit import and receipt readers
remain supported. Migration writes a new private file and records the exact
original path, schema and SHA-256. It never modifies the source or its receipts.

```bash
python <eval_author_dir>/scripts/construction_contract.py --plan <old-native-plan.json> \
  --contract <native-result-contract.json> --migrate-to <private-new-plan.json>
```

Native imports without a result contract leave case kinds unresolved. Keep the
source files available: canonical validation rechecks their hashes.

An imported contract is a proposal for a new revision. Keep historical workspaces
and proof untouched. In a trace workspace that has not recorded proof, keep the
original file and select a reviewed sidecar contract:

```bash
python <eval_author_dir>/scripts/construction_contract.py --plan <workspace>/private/construction-v3.json \
  --workspace <workspace> --activate
```

Activation writes a private, digest-bound active-contract marker; new run inputs
bind both the selected path and hash. After a canonical revision fails, record
its repair before editing, then activate the current contract before fresh proof.
The repair invalidates the current marker and proof; it does not create copies
of the former contract or marker. Source control owns revision history.
Existing rewards remain attached to their original contract hashes; migration
grants no execution credit.

Use [task validation](task-validation.md) for native qualification and
[trace input handoff](trace-input.md) for trace workspace qualification. Keep
measured difficulty and coverage closure separate from construction qualification.
