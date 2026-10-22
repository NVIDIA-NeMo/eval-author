<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Shared construction contract

New construction uses `nemo.eval_author.construction.v2`, for Harbor and Gym.
Keep `construction.json` private, outside the task tree. Trace workspaces use
`private/construction.json`. Proposals may leave authority unresolved; a draft
must identify the rule or independent evidence that defines correctness.

The same contract serves first-eval, task-create and trace-derived construction.
Provider adapters interpret native observations; they do not define another
construction document.

| Field | Required contents |
|---|---|
| `schema`, `task_id`, `provider` | Schema above; stable task identity; `harbor` or `gym`. |
| `requirements` | `id`, `statement`, `authority: {basis, reference}`, `candidate_index`. Authority basis: `specification`, `user_decision`, `independent_reference`, or `unresolved`. Index is null without a bound candidate. |
| `inputs` | `path`, `sha256`, `basis`, `source`, `rationale`, `edits`, `review`. Paths are task-relative. Native inputs use `copied`/`authored`, a source description, `edits: []`, `review: null`. Trace inputs retain observed/exact-reversal/authored provenance and review. |
| `checks` | `id`, `requirements` (IDs), `success` (JSON-pointer assertion). Every requirement maps to scored checks. |
| `cases` | `case`, `kind`, `observations: [{check, expected}]`, `requirements`, `reaches`, `dependency`, `rationale`, `review`. Native cases match the result contract; unused trace metadata is `[]`/null. |
| `prerequisites` | `id`, `before`, `after`, `bypass_case`, `check`. Each bypass contradicts the dependent requirement's scored check. Usually `[]`. |
| `probes` | Trace setup/reach definitions, or `[]` for native result-contract observations. |
| `starting_state` | Trace cutoff, input roots, external inputs, no-files reason and review; null for native declarations. Input artifacts live only in `inputs`. |
| `review` | Trace construction review, or null for native declarations. |
| `bindings` | Trace `candidate_sha256` and `trace_sha256`, or null for native tasks. |
| `migration` | Original artifact `{path, sha256, schema}` bindings, or `[]` for a new contract. |

Assertions use `{"pointer":"/...","equals":value}` or finite `min`/`max`.
Trace checks use `/checks/<check-id>` and `PASS`/`FAIL`, read from retained
verifier rows. Negative/copy/bypass cases declare only the checks they must fail;
Oracle and valid alternatives declare every check passing. NOP declares every
check failing. Native cases use the provider's actual result fields.

The [provenance and probe field definitions](construction-qualification.md)
remain applicable to trace fields. In v2, move `starting_state.artifacts` into
`inputs`, remove the `task/` path prefix, and use each artifact review note as
its rationale. Requirements carry the bound candidate statement and an explicit
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
through `record-run-inputs`. Changes require a new revision and retained repair
history before fresh proof.

## Import existing artifacts without rewriting evidence

`construction.v1` is the published trace format. `construction_plan.v1` was an
earlier experimental format from this work; retained experiment artifacts already
use it, so its explicit import and receipt readers remain supported. New native
and trace authoring uses only v2. Both old formats remain readable through their
existing script APIs. Migration writes a new private file and records the exact
original path, schema and SHA-256. It never modifies the source or its receipts.

```bash
python <eval_author_dir>/scripts/construction_contract.py --plan <old-native-plan.json> \
  --contract <native-result-contract.json> --migrate-to <private-new-plan.json>
python <eval_author_dir>/scripts/construction_contract.py --plan <old-workspace>/private/construction.json \
  --workspace <old-workspace> --migrate-to <private-imported-contract.json>
```

Native imports without a result contract leave case kinds unresolved. Trace
imports preserve provenance, probes, controls, reviews and dependencies, while
leaving correctness authority unresolved for explicit review. Keep the source
files available: canonical validation rechecks their hashes.

An imported contract is a proposal for a new revision. Keep historical workspaces
and proof untouched. To activate it, create a fresh private workspace, retain the
bound source/candidate and task inputs, review the imported authority, and use
v2 as its construction file before recording new run inputs. For a sidecar import
in an unproved workspace, keep the original file and select the reviewed v2 file:

```bash
python <eval_author_dir>/scripts/construction_contract.py --plan <workspace>/private/construction-v2.json \
  --workspace <workspace> --activate
```

Activation writes a private, digest-bound active-contract marker; new run inputs
bind both the selected path and hash. After a canonical revision fails, record
its repair before editing, keep the old contract, write a new contract path and
activate it before fresh proof. The repair archives the former marker and exact
contract bytes. Imports from already-proved legacy workspaces activate in a new
workspace, preserving the original proof unchanged. Historical rewards
remain attached to their original contract hashes; migration grants no execution
credit.

Use [task validation](task-validation.md) for native qualification and
[trace input handoff](trace-input.md) for trace workspace qualification. Keep
measured difficulty and coverage closure separate from construction qualification.
