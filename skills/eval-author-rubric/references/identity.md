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
They have statuses and diagnostics, not numeric scores. The eleven canonical W02
criteria consume this evidence under their own anchors; a helper passing is not
automatically criterion acceptance. Configuration differences require interpretation;
equality is not adequacy. Declared missing/denied access differs from a performed
check confirming a missing material required artifact. Preserve that distinction
when choosing a zero versus `NOT_ASSESSED`.
`preflight_success` is always false: this consumer cannot certify a workflow.

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

## Declared versus observed configuration

Supply `declared_config` and `observed_config`, both projected from identified
sources, not copied from each other to obtain equality. Include agent, run, task,
tool_permissions, provider, runtime_pins, and material environment settings.
Unknown or empty sections remain unknown. Use explicit lists (including an empty
permission list) for known emptiness. Exact JSON types matter; booleans and integers
are different. Lists retain order. Output sorted JSON-pointer differences and
unknowns without raw values; `acceptable` is always null. Review decides which
material settings and acceptable differences apply; never include credentials.

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

## Calibration and handoffs

Use handcrafted positive, defective, and ambiguous selections with frozen expected
item outcomes. Mutate one relevant property at a time, keep the valid neighbor,
and assert exact diagnostics as well as scores. Include broken-scorer and
false-rejection controls when measuring AC items through existing native controls;
a wrong route alone proves an identity defect, not successful cheating. A task
with no detected cheat is not universally cheat-free. Semantic ambiguity stays a
review gap unless a deterministic predicate actually establishes a contradiction.

This work implements W02 measurements and criterion assessment. W01/W05 consumer
integration is a downstream handoff, not an additional workflow implemented by
this rubric change. W03 can reuse authority/revision links, and W04 can reuse stable
task/run references. Do not claim those packages complete from W02 fixture results.
