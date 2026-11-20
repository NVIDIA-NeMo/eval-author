<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Artifact, configuration, and provenance measurement contract

These experimental P1 / validity measurements check explicit local selections.
They do not discover artifacts or prove semantic completeness, mock fidelity,
runtime success, acceptable configuration differences, or general anti-cheat
quality. Criterion IDs and scoring belong to
[artifact, configuration, and provenance](artifact-configuration-provenance.md),
not to these shared implementation capabilities.

## Measurement contract

Run the bundled read-only consumer only after the rubric's opt-in gate:

```bash
python <skills>/eval-author-rubric/scripts/measure.py \
  --root <selected-repository> --selection <private-selection.json>
```

The selection is a disposable projection of existing records, not a new evidence
ledger. Reuse workflow-selected paths and the existing `{path, digest}` references
from journey/task evidence. Do not rediscover a task for each item. The shared
producer is [rubric_identity.py](../../eval-author/scripts/rubric_identity.py).
It uses the existing task-evidence digest and strict JSON reader. Native receipt
verification remains with the owning workflow; a local hash is not a native receipt.

Each artifact has exactly `id`, `role`, `path`, `digest`, `revision`, `access`,
`selection_origin`, `source`, and `evidence`. Paths are canonical repository-relative
local files, without symlink traversal. Digests use the existing lowercase SHA-256
hex format; revision may remain null when a content digest is available. Source
identifies the selection decision, not inferred ownership. Evidence contains
existing `{path, digest}` references. `access` is `available`, `missing`, `denied`,
or `unresolved`. Non-available records retain explicit unknowns; the checker does
not probe denied locations. Available means readable by this checker, not by the
solver role. Remote artifact adapters and solver-role access checks remain unmeasured.

Resolve the selected root once at entry. The shared reader rejects symlinks below
that root and checks that the opened directory chain did not change during the
read. Identity reads allow 2,000,000 bytes for a `trace` artifact and 1 MiB for
other artifacts, within the 16 MiB total budget. Optional measurements can impose
smaller limits, including the reconstruction limits below.

For example, valid task evidence keeps its selected path and measured digest;
a missing artifact has `access: missing`, null digest and empty evidence; a denied
artifact uses `access: denied` without trying the read; an unresolved selection uses
`access: unresolved`. None of the latter three becomes a successful preflight.
The checked-in mutation panel exercises these distinctions with actual local files.

Raw measurements are named `identity`, `route`, `config`, `links`, and `approvals`.
Explicit `sourced_config`, `freshness`, `native`, `reconstruction`, `ethos`, and `authority` selections add their
corresponding measurements. Absent optional selections do not establish coverage.
These measurements have statuses and diagnostics, not numeric scores.
Unknown top-level selection fields return `selection.invalid` (exit 2) rather than
silently skipping a requested measurement. Measurements never score a criterion:
every score comes from the selected criterion evidence. Record what a measurement
shows as a criterion fact; a helper passing is not criterion acceptance.
Configuration differences require interpretation; equality is not adequacy.
Declared missing/denied access differs from a performed check confirming a missing
material required artifact. Preserve that distinction when recording the fact.

The report (`rubric-measurements-v4`) has `schema`, `experimental`, `scope`,
`measurements` and `items`. `items` holds the 23 rubric criteria and METHOD-J1.
Each row has a score, evidence strength, remaining gaps and one `check_results`
entry per canonical check. Subject revisions use validated content digests.

## Selected artifact identity and access

Measure the explicit selected artifacts and their evidence references against
current local bytes. Report duplicate IDs, malformed references, missing files,
denied/unresolved access, and changed digests with stable `identity.*` diagnostics.
A missing optional revision stays null; a missing required content digest does not
pass. Do not turn content identity into an ownership or confidentiality claim.

## Criterion evidence and scoring-rule demonstrations

Select a retained packet with `criterion_evidence: {"artifact_id": "packet"}`.
The packet and its cited sources must appear in `artifacts` with their exact
digests. The [packet contract](../scripts/rubric_evidence_scoring.py) defines
criterion-specific inventories, JSON-pointer bindings and provenance fields.
Unsupported IDs, malformed facts, changed sources and contradictory records fail
closed. Select each criterion explicitly.

Recorded (`recorded-evidence-assessment`) and simulated
(`synthetic-scoring-demonstration`) packets use the same criterion predicates, and
both score rows in `items`. Simulated rows have strength E1. Recorded rows have E2,
or E3 when every corroborating control has a different actor than the original
observation. A bound JSON record does not authenticate a reviewer, establish solver
permissions or prove native execution.

## Selected Ethos structure

`ethos` is `{"artifact_id": <id>}` naming one selected artifact with role `ethos`.
Pass `--ethos-validator <ethos-write-skill-dir>/scripts/validate_ethos.py`, with the
skill directory located as the
[shared prerequisites](../../eval-author/references/shared-prerequisites.md) describe.
A relative path resolves from the current directory, not from `--root`. Never
substitute a copy or another schema. The measurement reads the document
through the shared reader, records its digest and the validator file's digest, and
runs the validator on those bytes. A rejection is `ethos.invalid` (fail) with the
validator's reason; its warnings are kept. A changed digest is
`ethos.digest_mismatch`. A missing or broken validator leaves validity unknown.
A valid result is structure only: it does not show the content was confirmed,
is current, or states adequate intent.

## Harbor task layout labels

`layout` is `{"task": <task directory relative to --root>}`; `"."` selects the
root itself. The measurement labels every file under that directory from the
Harbor layout. `instruction.md`, `trajectory.json`, and `environment/` are
`public`; `tests/` and `solution/` are `private`; `task.toml` is
`configuration`; anything else is `unlabeled`. A declared step under
`steps/<name>/` follows the same rules, and its `workdir/` is `public`. Each
record keeps its task-relative path and digest.

A directory without `task.toml`, or without both `instruction.md` and declared
`[[steps]]`, is `layout.not_harbor` (unknown): give the
[format warning](../../eval-author/references/task-format.md) instead of
labeling it by hand. Symlinks are never followed and stay `unresolved`. A
directory that can't be listed is `layout.directory_unreadable` (unknown), never
an empty directory. A Dockerfile or Compose file under `environment/` that names
`tests` or `solution` is `layout.private_path_in_environment` (unknown), a
candidate for review. The labels describe the layout, not a tested runtime
boundary or whether a public file contains an answer.

## Internal answer leakage

`leakage` is `{"task": <task directory>, "allowed_overlap": [...]}`. It uses the
Harbor task layout labels above and compares every readable private file with
every readable public file. Each finding records `kind`, `method`, `private` and
`public` paths, the public `lines` range when there is one, and whether declared
overlap allows it. Findings never contain the matched content.

- `full`: the whole private file appears in a public file, by `exact`,
  `normalized` (whitespace collapsed, lowercased), or `json-canonical` (sorted
  keys) comparison. An unallowed full match is `leakage.full` (fail).
- `hint`: part of a private file appears, as a `normalized-line` of at least 24
  characters or a `json-value` of at least 6. An unallowed hint is
  `leakage.hint` (unknown).
- `reference`: an instruction names a private file's task path, a distinctive
  file name, or a `test_` function from a private Python file. It is
  `leakage.reference` (unknown).
- `filename`: a public file or directory name contains an answer, such as
  `expected_total_4213.csv`. Answers here are private JSON values of at least 4
  characters and one-line plain answer files. It is `leakage.filename` (unknown).

`hidden_cases` classifies the task's hidden tests: `protected` when the layout
keeps every test file private and no public file holds any of a test's content,
`exposed` when a public file holds a full copy of a test file, and `unknown`
otherwise, including layout diagnostics, unscanned files, and partial matches
awaiting review.

Each `allowed_overlap` entry is `{"private", "public", "reason"}` with paths
relative to the task directory. Use it only for content the task intends the
agent to see, such as the input a task asks the agent to transform. When a
`reconstruction` measurement declares a public file `generated`, overlap with
it is never allowed: `leakage.generated_overlap_not_allowed` (fail).
`generated_files_checked` says whether a reconstruction measurement was
available to apply that rule.

Non-UTF-8 files are listed in `unscanned` (`leakage.unscanned_files`, unknown).
A match shows shared text, not that the agent can use it or that the author
didn't intend it. No match doesn't establish the task is free of leakage:
paraphrases, image contents, and answers computable from public inputs stay
unchecked.

## Development and test overlap

`overlap` is `{"splits": {<name>: [<task directory>, ...]}, "required": [<name>,
...]}`. `required` names at least two splits a complete check needs, such as
`development` and `test`. A required split that isn't declared is
`overlap.split_missing` (unknown), never an empty clean split. Take split
membership from the suite's own dataset configs or manifests; don't infer it
from directory names.

Every pair of tasks in different splits is compared:

- `duplicate` (fail): the same directory (`same-directory`), identical
  instruction bytes (`exact-instruction`), or identical text after collapsing
  whitespace and lowercasing (`normalized-instruction`). A multi-step task's
  step instructions are compared together.
- `sibling` (unknown): different tasks with the same repository and commit
  (`repository-commit`), read from `task.toml` `[metadata]` keys such as `repo`
  and `base_commit`, or else from `git clone` and `git checkout` lines in the
  environment's Dockerfile. Siblings share a codebase rather than a task; review
  whether the development task reveals what the test task measures.

A task outside the Harbor layout or with an unreadable instruction is unknown.
Each split reports how many tasks were declared, compared, and had a recoverable
origin; a task without an origin can't be checked for siblings. Findings name
tasks, never their content. Paraphrased duplicates, siblings with other kinds of
shared origin, and model-training contamination are not checked.

## Published sources the agent can reach

`external` is `{"task": <task directory>}`. It reads `task.toml` and never fetches
anything. `networks` lists the agent's network policy for the task and for each
declared step. Harbor applies a step's `[steps.agent]` policy first, then the
task's `[agent]` policy, then the `[environment]` one, and finally its default
`public`; `allow_internet = false` means `no-network`. Allowed hosts are trimmed,
lowercased, and stripped of a trailing dot, as Harbor does. The task's origin
comes from the same repository-and-commit lookup as the overlap check above.

- `external.source_reachable` (fail): the task names its repository and commit,
  and some agent phase can reach that repository's host, through `public`
  network or a matching `allowlist`. The repository's later commits may contain
  the fix.
- `external.network_open` (unknown): some agent phase has `public` network but no
  source repository was identified. Other published copies of the answer can't
  be ruled out.

A job config can add allowed hosts at run time; this check sees only the task.
Use the cheating evaluation set's hidden-case read for the runtime read
boundary: it counts as protected only when the private read is denied and its
paired read of the agent's own workspace file succeeds.

## Benchmark content in prompts and skills

`reuse` is `{"tasks": [<task directory>], "paths": [<file or directory>]}` with
paths relative to `--root`. `paths` names the prompts, skills, and harness code
the agent uses. The agent has these before any evaluation starts, so the
measurement reads every selected file in full; files inside the selected tasks
are skipped. The root must contain the tasks rather than be one.

Each finding records `kind`, the `task`, the `private` source file, and the
agent `file` and line. It never copies the matched content.

- `expected-value` (fail): a JSON value of at least 6 characters from a private
  file.
- `private-line` (fail): a line of at least 24 characters from a private file,
  compared after collapsing whitespace and lowercasing.
- `test-name` (unknown): a `test_` function name from a private Python file.
- `task-id` (unknown): the task directory's name, when it has at least 6
  characters.

Layout diagnostics from a selected task, such as a symlinked `tests/`, and
missing, unreadable, or non-UTF-8 agent files leave the result unknown, never
clean. A match shows that task-specific text reaches the agent, not how it got
there. Generalized advice tuned to one grader, such as always writing a file the
grader checks for, is not detected.

## Construction authority references

`authority` is `{"plan_artifact_id": <id>, "result_artifact_id": <optional id>}`.
The plan has role `construction`. The optional selected native result has role `result`. Every artifact a reference cites must also be selected.
A whitespace-free reference is a locator when its path names a selected artifact
or contains `/` or `.`; its path is relative to the measurement root. Each locator
is resolved against the selected bytes: `path` names the artifact, `path#Heading`
a Markdown heading's visible text or its GitHub anchor, `path#L3-L5` a line range,
and `path#/json/pointer` a JSON value. Headings in front matter and fenced code
don't count.

| Code | Status | Meaning |
| --- | --- | --- |
| `authority.reference_broken` | fail | The fragment is missing, or the path is unselected and doesn't exist. |
| `authority.reference_changed` | fail | The cited bytes differ from the selected digest. |
| `authority.reference_invalid` | fail | The cited path is a symlink, escapes the root, or isn't a regular file. |
| `authority.reference_unselected` | unknown | The path exists but wasn't selected. |
| `authority.reference_unreadable` | unknown | The selected artifact isn't available or couldn't be read. |
| `authority.reference_prose` | unknown | The reference is prose; never guess what it refers to. |
| `authority.unresolved` | unknown | The requirement's basis is `unresolved`. |
| `authority.requirement_unmapped` | unknown | No check maps the requirement; proposal plans may allow this. |
| `authority.check_dangling` | fail | A check cites an undeclared requirement. |
| `authority.reference_implementation` | fail | The authority locator resolves to a selected scorer artifact. |
| `authority.check_unobserved` | fail | The selected result lacks a check's success pointer. |
| `authority.check_unknown` | unknown | No result is selected, available, or usable, or the partial plan omits success. |
| `authority.check_pointer_invalid` | fail | A selected-result check declares an invalid JSON pointer. |
| `authority.result_malformed` | fail | The selected result is not valid JSON. |
| `authority.result_changed`, `authority.result_invalid`, `authority.result_unreadable` | fail, fail, unknown | The same source checks apply to the selected result. |
| `authority.plan_malformed` | fail | The plan lacks the requirement and check shape this measurement reads. |
| `authority.plan_changed`, `authority.plan_invalid`, `authority.plan_unreadable` | fail, fail, unknown | The same as the `reference_` codes, for the plan itself. |

The result also counts requirements by basis and status, scored checks, and
mapped requirements. Full plan validation stays with `construction_contract.py`.
A resolved reference shows that the cited content exists, not that it supports
the requirement.

A requirement citation that resolves to an artifact with role `scorer` fails with `authority.reference_implementation`. Implementation does not establish authority for the intended requirement.

The result's `checks` rows resolve each plan check's `success.pointer` in the selected result. A present value is `observed`, including false, zero, or null. A missing pointer is `unobserved`. It fails with `authority.check_unobserved`. Without a selected result, observations stay `unknown`.

Selected result bytes must pass the same path and digest checks as other selected evidence. These checks do not compare the observed value with the expected value. They do not authenticate native execution.

Requirement rows with `prose`, `unresolved`, or `unselected` status carry a `question` with `requirement_id`, `reference`, and `decision_needed`. The fixed wording is in `rubric_authority.py`. See [unresolved mappings for review packets](task-contract-authority.md#unresolved-mappings-for-review-packets) for agent crosswalk ambiguities and contradictions. Honest unknowns remain valid measurements. They do not establish passing observations or resolved decisions.

## Declared grading route and dependencies

`route` contains `entrypoint` and `expected_scorer` artifact IDs and a `dependencies`
mapping from artifact ID to explicit dependency IDs. Empty arrays explicitly mean
no declared dependencies; an omitted entry means unresolved closure. Include imports,
judge prompts and required inputs using their existing inventory IDs.

The first supported dispatch format is a selected JSON file with `selector` and
`routes` (selector-to-artifact-ID mapping). The checker reads that exact file and
follows only the selected branch, then its declared transitive dependencies. It
compares the selected scorer to the expected scorer. Null/dynamic selectors,
unsupported dispatch formats and absent dependencies stay unresolved; never guess
which grader ran. `executed_scorer` stays null. Arbitrary Python/shell import analysis,
provider-native dispatch adapters, and runtime selection attestation are not
implemented. A declaration of dependency closure does not prove source completeness.

For package preflight or full `provenance.grading-path` credit, retain `dependencies` in the dispatch JSON or select
`route.dependency_source: {artifact_id, pointer}` naming a selected JSON graph.
The caller's dependency map must exactly match the retained map. Require both
`status: pass` and `bound_dependency: true`; caller-only legacy maps are unbound
even when their local traversal passes. The selected closure rejects cycles and
duplicate edges without treating defects on an unused branch as selected evidence.
A wrong-scorer zero also requires `expected_scorer` in the retained dispatch JSON
to match the selection. A caller-only expectation cannot establish that defect.

## Declared versus observed configuration

Supply `declared_config` and `observed_config`, both projected from identified
sources, not copied from each other to obtain equality. Include agent, run, task,
tool_permissions, provider, runtime_pins, and material environment settings.
Unknown or empty sections remain unknown. Use explicit lists (including an empty
permission list) for known emptiness. Exact JSON types matter; booleans and integers
are different. Lists retain order. Output sorted JSON-pointer differences and
unknowns without raw values; `acceptable` is always null. Review decides which
material settings and acceptable differences apply; never include credentials.

For source-backed measurements, instead select `sourced_config` with `declared`
and `observed` objects. Their keys are public comparison JSON pointers rooted in
the seven configuration categories; each value is
`{"artifact_id": "selected-id", "pointer": "/source/field"}`. Select JSON or TOML
artifacts through the common identity records. The helper reads and hashes the
exact selected bytes, returns provenance and counts, and never exports values or
discovers source object keys. Explicit unknowns use `state` and `reason`:
`omitted/field_not_declared`, `inaccessible/access_denied`,
`provider_hidden/provider_not_disclosed`, or `unobservable/not_recorded`.
Unselected categories stay unknown and remain in the denominator.

An optional `binding` contains `run` and `task` field references in the same
artifact/pointer form, `configuration_artifact_id`, and `observations` mapping
each observed artifact ID to its `run`, `task`, and `configuration_digest`
pointers. Matching those selected records is an assertion binding, not native
execution attestation. Preserve `native_attestation: false` and `acceptable: null`.
See [the configuration producer](../../eval-author/scripts/rubric_configuration.py)
for the accepted reason pairs and resource bounds. Descriptor-relative no-follow
file access requires POSIX; unsupported platforms cannot claim measured access.

## Identity and reference relationships

Check explicit task/run/trace IDs, tool-call/result references, and dependency links
with [the shared relationship checker](../../eval-author/scripts/rubric_links.py).
Supply `nodes` (`id`, `role`) and `links` (`source`, `target`, `relation`). Parent
links point child to parent: run to task, trace to run, tool_call to trace, and
result to both run and tool_call. Qualify native step/call IDs by their trace and
step before projection; they are not globally unique. Dependency links must be
acyclic. Reuse the owning ATIF evidence audit for native call/observation inventory
rather than deriving a second inventory here. Missing, duplicate, cross-run, inconsistent,
and unresolved links must not become successful integrity checks. Relationship
validation is not trace completeness, native execution proof, or causal attribution.

For retained native records, select `native` with explicit `receipt_ids`,
`manifest_ids`, `result_ids`, and `trace_ids` arrays of existing artifact IDs.
[The native projection](../../eval-author/scripts/rubric_native.py) consumes
task-evidence receipts/manifests, Harbor results and ATIF-v1.7 references through
the existing control validator. It hashes qualified references, retains negative
control evidence, and does not export prompts, arguments or tool outputs. It
does not execute tasks, establish current task-tree freshness, qualify closure,
validate every ATIF field, or traverse nested/subagent traces. Unsupported native
formats remain unresolved. Existing control/accounting seam tests do not prove
that the whole retained-credit or coverage workflow consumes this interface.

## Selected receipt consistency

Every explicit `native` selection automatically reports three measurements. The owning
receipt validator derives each one from the same selected receipt, manifest, and
native result. None of them scans a task tree.

- `receipt_integrity` (`receipt-integrity-v1`) counts receipt status claims.
- `required_verification` (`required-verification-v1`) gives each required
  control of each selected manifest one state.
- `superseded_references` (`manifest-supersession-v1`) lists receipts bound to a
  manifest that a selected repair manifest explicitly supersedes.

Read each control's typed `integrity` fields separately: claim, basis, record
consistency, execution health, declared behavior, status-claim agreement, and
freshness.

Only a supported receipt for a closure control that claims `passed` is eligible.
The claim is a contradiction when one of these is true:

- Its return code is nonzero.
- Its digest-bound native result from the same run fails a health assertion.
- That result passes health and fails a declared behavior assertion.

A contradiction shows that the records are inconsistent. It does not show that
an agent ignored feedback, that evidence was visible to the model, or that
context growth caused the result. A correctly recorded failure or infrastructure
error is not an eligible claim. Zero reward, permitted partial credit, valid
alternatives, and legitimate no-action controls follow their declared checks.
Agent, diagnostic, baseline, out-of-scope, and superseded receipts are excluded.
When a later reference check fails, the validator withdraws a native-basis claim
as unknown. A return-code contradiction stands.

Report contradictions divided by eligible claims. Keep unknown, excluded, and
not-applicable receipts separate. Execution errors and mismatches are overlapping
diagnostic counts, not extra trials. A zero denominator has a null rate.

For required controls, report each state separately: `pass`, `fail`, `missing`,
`mismatched`, `execution_error`, `unknown`, and `stale`. Freshness is `unknown`
here. Do not report a rubric pass as a current pass. A missing control is an
evidence gap. It does not show that verification was skipped. Retain earlier
contradictions when another selected receipt passes. Ordering alone does not
establish recovery.

The native measurement reports `receipt_integrity`, `required_verification` and
`superseded_references` as observations. They do not score a criterion.
`required_verification` freshness is unknown here because this native projection
cannot establish current task freshness.

Treat each superseded reference as a review candidate. Receipts have no
trustworthy time, so `confirmed` stays 0. Use `task_pipeline.py
check-acceptance --integrity` to test whether a saved claim relies on a
superseded result.

`context_rot_score` stays null. Use the existing freshness checks for selected
dependencies separately. Do not transfer a historical pass to a changed task.
These remain unassessed here:

- General false-closure claims in prose.
- Actions in traces that use superseded values.
- Visibility into the request context.
- Prevention experiments.

Retry candidates belong to the inspect-trace retry audit, not this rubric.
Do not prescribe a checkpoint as a proven remedy from these measurements.

## Selected reconstruction provenance

The optional `reconstruction` array selects existing candidate artifacts and retained
Compass Trace turns. Use [rubric_reconstruction.py](../../eval-author/scripts/rubric_reconstruction.py)
through `measure.py`; its callable is `project_reconstruction(root, artifacts, selection)`.
No reconstruction, scorer, model, or task code runs. This measurement does not
change canonical criterion IDs, check IDs, scores, or preflight decisions.

Each entry has exactly these fields:

```json
{
  "artifact_id": "initial-file",
  "candidate": {
    "artifact_id": "candidate",
    "pointer": "/ground_truth/artifacts/0"
  },
  "source": {"artifact_id": "summary", "pointer": "/source"},
  "trace_id": "safe-trace",
  "step": {"pointer": "/root_spans/0/turns/0", "step_id": "trajectory/turn/1"},
  "content": {"pointer": "/content", "encoding": "utf-8"},
  "declaration": "recovered"
}
```

Select all four artifacts through the common identity records. Reuse the owning
trace-environment candidate record and prepared summary. The candidate uses schema
`nemo.eval_author.trace_environment_candidate.v3`; its selected ground-truth artifact
binds `path`, `sha256`, and `provenance`. Current provenance has `kind: trace_turn`,
unique string `turn_ids`, and null `uri`, `revision`, and `source_id`.
The selected files must be `candidate.json` and `summary.json` in the same workspace
directory. The current summary uses `nemo.eval_author.trace_environment_summary.v4`.
Its `candidate` must equal
`{"path": "candidate.json", **candidate}` from the selected candidate document.
The comparison preserves JSON types, list order, and every candidate field.
The candidate document cannot override the summary's reserved `path` field.
This follows the owning producer's
[`_finalize` and `_check` bindings](../../eval-author/scripts/eval_author_shared/trace_workspace.py).
The helper does not validate candidate semantics, privacy review, or environment readiness.
A prepared summary without an embedded candidate cannot establish this binding.
Artifact paths in the candidate are relative to its directory. The source pointer must be `/source`.
The selected summary source binds
`safe_path`, `safe_sha256`, and `safe_size_bytes` to the selected trace. Its path
is relative to the summary directory. Native digests retain their `sha256:` prefix;
common identity records use bare lowercase hex. Never normalize malformed paths
or digest values.

The current producer loads source traces through Compass and retains `safe/trace.json`
with `schema_version: "3"`. It does not write ATIF. The selection keeps the `step`
field name for compatibility. Its pointer selects a turn in an AGENT span under
`/root_spans/<index>`, with optional `/children/<index>` segments, then `/turns/<index>`.
Its string `step_id` must match that turn's `id` and the candidate's `turn_ids`.
Every cited turn must exist exactly once across the retained AGENT spans.
The helper reads this selected normalized structure without loading raw trace formats.
The content pointer is relative to the selected turn. Select only a retained string
whose UTF-8 bytes are the claimed file bytes. It never joins content parts.
The helper preserves line endings and compares exact bytes for `recovered`.
It does not extract file contents from command text or infer what a tool did.
Use `content: null` when the source does not retain exact bytes. A dangling pointer
is an error, not a declaration of unavailable evidence.

Historical retained evidence remains readable: candidate v2 pairs only with summary
v2 or v3, `kind: atif_step`, unique integer `step_ids`, and an `ATIF-v1.7` trace.
Select `/steps/<index>` and a relative string pointer such as `/message` for that
format. This is read-only compatibility, not support for preparing old workspaces.
Do not mix historical and current schema families.

`declaration` is `recovered`, `partial`, or `generated`. These are caller declarations,
not semantic findings. A recovered declaration passes only when the selected
bindings and bytes match. Partial and generated declarations keep `source_proof:
unknown`, even when their known artifact digest matches selected content. An
optional content pointer can measure known source bytes for either declaration;
it does not assert equality with the resulting artifact. The candidate's existing
`state_basis` remains `recorded` or `reconstructed` and does not establish recovery.

Output uses the common `status`, `records`, and `diagnostics` fields. Each record
contains `selection_index`, `status`, `declaration`, `artifact_digest`,
`candidate_digest`, `source_digest`, `trace_digest`, `source_content_digest`,
`source_step_id`, `source_step_index`, `source_proof`, and `semantic_proof: false`.
`source_step_id` is the selected turn ID for current traces or integer step ID for
historical ATIF. `source_step_index` is the local array position, not execution order.
Digests and step fields stay null until checked. A valid recovered entry uses
`source_proof: exact_selected_bytes`; all other entries retain `unknown`.
The helper exports no artifact paths, pointer text, session IDs, content, or exception text.
The selected safe-trace turn ID is retained verbatim.
Diagnostic subjects use `reconstruction:<selection-index>`.

Stable `reconstruction.*` diagnostics distinguish `artifact_digest_mismatch`,
`candidate_digest_mismatch`, `source_digest_mismatch`, `trace_digest_mismatch`,
`candidate_artifact_mismatch`, `candidate_source_mismatch`, `source_schema_unsupported`, `source_trace_mismatch`,
`candidate_step_mismatch`, `candidate_step_missing`, `step_missing`, `step_mismatch`,
`step_ambiguous`, and `content_digest_mismatch`. Unselected references use
`<role>_not_selected`. Declared unavailable artifacts use `<role>_access_missing`,
`<role>_access_denied`, or `<role>_access_unresolved`. Missing content uses
`content_unavailable`; partial and generated entries use
`partial_source_proof_unknown` and `generated_source_proof_unknown`.
Malformed fields produce fixed diagnostics instead of raw exceptions. Failed
entries do not erase valid neighbors or already measured artifact identities.

The helper admits at most 64 entries and 64 artifact records. Reads share a
64-file, 16 MiB budget, with at most 1 MiB per file and 64 evidence references per
artifact. Failed reads consume budget. JSON structure and pointer limits reuse
the configuration reader; current traces admit at most 2,000 spans and 2,000 turns,
and historical traces admit at most 2,000 steps. All file reads use
the shared descriptor-relative no-follow reader. Declared unavailable paths are
not read by this helper, including through another selected artifact's evidence.
These bounds cover the optional helper; other measurements retain their own bounds.
A complete selected manifest still does not prove a runnable environment.

## Scoped approval and freshness

Bind substitution approvals to the existing decision and relevant task/run scope.
Supply `approvals` and `decisions` using the exact projection contracts in the
shared checker. Approvals contain `id`, `decision_id`, `scope`, `substitution`,
and `bindings`; decisions replace `decision_id` with `outcome` and `source`.
Substitution has distinct `from` and `to` artifact IDs. Bindings map every selected
artifact ID to its digest. This initial scope is `task:<task-id>/substitution`;
run-specific approval adapters remain unsupported. The decision outcome must
literally be `approved`. Current observations come from the identity producer, not a second
caller-supplied digest inventory. Include ETHOS, task, scorer, substitution endpoints,
and every relevant dependency. Empty approval selection remains unknown, not N/A.
Compare the decision's bound digests to the current selected ETHOS, task, scorer,
and dependency identities. Changes invalidate evidence reuse; do not amend old
receipts or infer a renewed approval. A matching decision record proves binding,
not the human's identity or authority. Keep independent decision-source review
and the owning workflow's native evidence verifier in place.

Use `approval_sources` to bind each projected decision ID to
`{"artifact_id": "selected-decision", "pointer": "/decisions/0"}`. The selected
source must have role `decision`; the pointed-to retained object must match all
decision fields exactly. Supporting decision artifacts are excluded from material
bindings to avoid self-reference; all other selected artifacts remain required.
Without this source mapping, raw approval comparisons establish only projected
record consistency, not a retained decision. Existing audit checkpoints or journey
acceptances without substitution scope and endpoints remain unresolved rather than
being upgraded into substitution approvals. Canonical `provenance.dependency-substitution.deterministic-2` retains the
source, scope, and freshness predicates separately from runtime adequacy and authority.

Select `freshness` as an array of retained evidence entries. A source-bound entry
has `id`, `kind: bindings`, `source` (selected artifact ID), and `pointer` to a
retained object. That object contains `evidence: {id, digest}` and nonempty
`dependencies`; each dependency contains `id`, historical `digest`, `revision`
(nullable), and `impact` (`material` or `unknown`). All referenced artifacts must
be explicitly selected. The read-only result classifies each selected evidence
as current, stale, or unresolved and retains old/new lineage. A changed material
dependency makes that evidence stale; unknown impact stays unresolved and unusable.
Unchanged neighbors remain current. Do not infer ETHOS semantic equivalence,
rewrite historical records, or treat a derived freshness report as a binding source.
`usable` means current evidence, including negative evidence, not task acceptance.

## Calibration and handoffs

Use handcrafted positive, defective, and ambiguous selections with frozen expected
item outcomes. Mutate one relevant property at a time, keep the valid neighbor,
and assert exact diagnostics as well as scores. Include broken-scorer and
false-rejection controls when measuring AC items through existing native controls;
a wrong route alone proves an identity defect, not successful cheating. A task
with no detected cheat is not universally cheat-free. Semantic ambiguity stays a
review gap unless a deterministic predicate actually establishes a contradiction.

The acceptance panel exercises the shared selection through the retained-credit
validator and coverage measurement/aggregation entrypoints. The panel and its
human-facing documentation run from a repository checkout, not a copied skill
installation. This identity handoff does not implement the full retained-credit
or coverage workflows. Authority reviews can reuse authority/revision links.
Task and run consumers can reuse stable references.
