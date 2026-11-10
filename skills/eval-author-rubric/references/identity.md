<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# W02 measurement helper contract

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

For example, valid task evidence keeps its selected path and measured digest;
a missing artifact has `access: missing`, null digest and empty evidence; a denied
artifact uses `access: denied` without trying the read; an unresolved selection uses
`access: unresolved`. None of the latter three becomes a successful preflight.
The checked-in mutation panel exercises these distinctions with actual local files.

Raw measurements are named `identity`, `route`, `config`, `links`, and `approvals`.
Explicit `sourced_config`, `freshness`, and `native` selections add their
corresponding measurements. Absent optional selections do not establish coverage.
They have statuses and diagnostics, not numeric scores. The eleven canonical W02
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

## Source-backed inventory assessment

`w02_scope`, when present, must be an object; other JSON types produce the sanitized
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

For package preflight, retain `dependencies` in the dispatch JSON or select
`route.dependency_source: {artifact_id, pointer}` naming a selected JSON graph.
The caller's dependency map must exactly match the retained map. Require both
`status: pass` and `bound_dependency: true`; caller-only legacy maps are unbound
even when their local traversal passes. The selected closure rejects cycles and
duplicate edges without treating defects on an unused branch as selected evidence.

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
that the whole W01 or W05 workflow consumes the new interface.

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

The W02 acceptance panel exercises the shared selection through the W01 retained
credit validator and W05 coverage measurement/aggregation entrypoints. The panel
and its human-facing documentation run from a repository checkout, not a copied
skill installation. This identity handoff does not implement the remaining
W01/W05 packages. W03 can reuse authority/revision links, and W04 can reuse stable
task/run references.
