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
Explicit `sourced_config`, `freshness`, `native`, `reconstruction`, and `ethos` selections add their
corresponding measurements. Absent optional selections do not establish coverage.
They have statuses and diagnostics, not numeric scores. The eleven canonical
criteria consume this evidence under their own anchors; a helper passing is not
automatically criterion acceptance. Configuration differences require interpretation;
equality is not adequacy. Declared missing/denied access differs from a performed
check confirming a missing material required artifact. Preserve that distinction
when choosing a zero versus `NOT_ASSESSED`.
`preflight_success` is always false: this consumer cannot certify a workflow.
Every canonical item includes the complete result template and one `check_results`
entry per canonical check. Keep the check's method, condition, evidence boundary,
remaining gap and next action with its outcome. `PARTIAL` is not a deterministic
pass; `NOT_RUN` review/probe checks cannot inherit helper successes. Item-level
`measurements` uses the canonical list format; former counter maps are retained
as `legacy_measurements`. Subject revisions use validated content digests.

## Selected artifact identity and access

Measure the explicit selected artifacts and their evidence references against
current local bytes. Report duplicate IDs, malformed references, missing files,
denied/unresolved access, and changed digests with stable `identity.*` diagnostics.
A missing optional revision stays null; a missing required content digest does not
pass. Do not turn content identity into an ownership or confidentiality claim.

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

## Source-backed inventory assessment

`rubric_scope`, when present, must be an object; other JSON types produce the sanitized
`selection.invalid` CLI response (exit 2, empty stderr). Its optional `task_artifacts`
and `grading_dependencies` objects contain `source` (selected inventory artifact ID),
`required` (nonempty distinct artifact IDs), and `unresolved_material` (string gaps).
Grading dependencies also name `review_source` (selected review artifact ID).
Each required artifact's `source` must equal the inventory artifact's path.

For content findings, the selected inventory file must be a JSON object with the
relevant section in this exact shape (both sections may share one source):

```json
{
  "task_artifacts": {"required": ["task"], "unresolved_material": []},
  "grading_dependencies": {"required": ["test"], "unresolved_material": []}
}
```

The relevant section must exactly match the selection's `required` and
`unresolved_material`, including list order, with no extra section fields.
A full DISC-01-C additionally requires a `review`-role artifact whose `source` is
the inventory path and whose JSON object has exactly these fields:

```json
{"source": "inventory-id", "required": ["test"], "unresolved_material": [], "complete": true}
```

Here `source` is the inventory artifact **ID**, `required` matches the selected
list, and `complete` must be the JSON boolean true. Extra review fields, omitted
IDs, contradictory conclusions/gaps, unsupported formats, or unreadable/stale
sources cannot establish full credit; retain `NOT_ASSESSED` and null score rather
than infer semantics. Existing measured missing/denied evidence (0) and measured
partial identity evidence (1) remain usable. A materiality-gap declaration alone
cannot establish 1; its source projection must match too.

`assess(measurements, selection, root=None)` uses only the explicitly selected
sources; `measure.py` supplies `root`. Without it, direct observations cannot
establish inventory content findings. Source reads are capped at 1 MiB, use the
shared local-path and strict JSON checks, and hash the exact bytes parsed against
the measured digest. This verifies a bounded source projection, not undeclared
reference discovery, independent review, reviewer authentication, or runtime proof.

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

For package preflight or full `DISC-01-B` credit, retain `dependencies` in the dispatch JSON or select
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

An explicit `native` selection also emits `receipt_integrity`, version
`receipt-integrity-v1`. Read each control's typed `integrity` fields separately:
record consistency, execution health, declared behavior, status-claim agreement,
and freshness. The owning receipt validator derives them from the same selected
receipt, manifest, and native result. No task tree is scanned.

A confirmed contradiction requires a supported closure-control receipt claiming
`passed`, a digest-bound manifest and same-run result, passing health assertions,
and failing declared behavior assertions. This is record inconsistency. It does
not establish that an agent ignored feedback, that evidence was visible to the
model, or that context growth caused the result. A correctly recorded failed
control is consistent evidence. Zero reward, permitted partial credit, valid
alternatives, and legitimate no-action controls follow their declared checks.
Agent, diagnostic, and baseline receipts do not supply eligible closure claims.

Report contradictions divided by eligible receipt claims. Keep unknown and
excluded claims separate. Execution errors and mismatches are additional,
possibly overlapping diagnostic counts, not extra trials. A zero denominator
has a null rate. Unselected attempts and controls missing from selected manifests
are evidence gaps, not observed skipped verification. Retain earlier findings
when another selected receipt passes; ordering alone does not establish recovery.

`CREATE-07-C01` includes `selected_receipt_status_consistent` as a bounded
`PASS`, `FAIL`, or `BLOCKED` predicate after source revalidation. Its canonical
check remains `PARTIAL`, and this evidence alone leaves the criterion score null.
Native current-task freshness and `context_rot_score` remain unknown/null.
Use existing selected-dependency freshness separately; do not transfer a historical
pass to a changed task. General false-closure claims, superseded-value actions,
retry analysis, request-context visibility, and prevention experiments have no
qualified adapter here and remain unassessed. Do not prescribe a checkpoint as a
proven remedy from these measurements.

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
being upgraded into substitution approvals. Canonical `ENV-02-A-C03` retains the
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
