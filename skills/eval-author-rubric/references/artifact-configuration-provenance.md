<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Artifact, configuration, and provenance

**Experimental; selected-area opt-in only.** Follow the [skill gate and scope
selection](../SKILL.md#opt-in-gate). This area belongs to validity, not a separate
pillar. Retained-credit checks and identity-aware coverage are separate consumers,
not prerequisites for this assessment. Integration tests must show that these
consumers use the shared validated identities. A passing local rubric demo does
not establish that integration.

## Canonical definitions

The [canonical Rubric v4 subset](artifact-configuration-provenance.json) is the
single source for the 11 criterion IDs, original questions, applicability,
measurements, 38 checks, 44 score anchors, evidence requirements, and boundaries.
Read each selected `items` entry in full before assessment. Links below use JSON
Pointer fragments to identify those entries; if a viewer does not navigate JSON
fragments, locate the entry by its original ID. Do not rewrite the source or
replace distinct criteria with helper capability scores. Source code references
and implementation-task descriptions are provenance, not proof of runnable
integration or permission to execute them.

Source provenance: this subset follows the supplied `Rubric.v4.json`.
The embedded rubric in the supplied Vibe Rubric draft has the same selected
criterion definitions and score anchors, but omits `CREATE-07-C04`, its ETHOS
evidence entry, and `policies.ethos`. Those additions in `Rubric.v4.json` implement
the explicit ETHOS-freshness requirement. Treat them as a
traceable augmentation, not an unchanged extraction of the draft's embedded JSON.

The guidance below interprets methods and evidence; it does not replace the
canonical definitions. Reuse [the identity helper](identity.md) for bounded
observations and the owning workflow's native records, not a parallel ledger.

## Assessment and evidence

Every selected criterion must have a result row using its original ID and the
JSON `result_template` fields: subject/revision, applicability, assessment and
execution states, score or null, evidence strength, measurements with units and
denominators, check results, qualifications, evidence and approval references,
findings, remaining gaps, and next action. This is a response/private-note
projection, not a new native receipt format. No aggregate score is permitted.

Every source check must be accounted for within each selected criterion: record
its original check ID, condition, method, evidence, and outcome, or an explicit
not-run, blocked, unresolved, or condition-not-applicable reason. Conditional or
optional methods do not require every stage to run. An area-wide assessment
accounts for all 11 criteria; an explicit subset accounts only for the selected
IDs. Retain unsupported portions rather than silently dropping them.

- Use `NOT_ASSESSED` with a null score when evidence cannot establish an anchor.
  A confirmed missing required artifact may satisfy a defect anchor; an
  unattempted lookup cannot. `NOT_APPLICABLE` needs a criterion-specific scope
  reason; missing permission or runtime alone is not such a reason.
- Confirmed material defects take precedence over partial or positive evidence
  within the same criterion and scope. Reuse one finding ID across affected
  criteria only when each criterion's defect condition is independently met;
  reused evidence is not independent corroboration.
- Record E0 assertions, E1 inspectable source evidence, E2 directly tested selected
  revision, and E3 independent scoped corroboration separately from merit.
  E0 alone supports no numeric score. Stronger evidence can confirm a defect or
  partial satisfaction; it does not raise the score. Generic helper tests are
  not evidence about the selected task.
- Do not import anti-cheat's evidence-floor table. Acceptance follows each
  canonical anchor and its applicable checks: source-backed identification can
  meet level 2 without agent execution. CREATE-07 and AUD-02 can meet their
  level-3 anchors at E2; E3 requires independent corroboration. Other criteria
  retain their explicit independent review or repeat requirements.
- Deterministic extraction/comparison does not decide semantic materiality,
  fairness, permission authority, live-state interpretation, or actual runtime
  behavior. A script calling a model is not deterministic semantic evidence.
  Qualify material judge/classifier decisions for this scope, retain disputes,
  and distinguish human authority/adjudication from exhaustive manual review.
- Use existing evidence before requesting execution. Controls, provisioning,
  installs, model calls, and writes require separate authorization. Preserve
  historical receipts and report stale evidence through the owning validators.

## DISC-01-A — Task artifact access

[Canonical definition](artifact-configuration-provenance.json#/items/0)

**Methods and evidence:** Deterministic manifest/reference inventory and performed
reviewer-access checks establish observed access and identity; independent source
review supplies the level-3 corroboration.

**Assessment limit:** Count accessible, missing, denied, and unresolved required
artifacts separately. A confirmed inspection-blocking absence can establish a
zero; a failed or incomplete search is not proof of absence. Reviewer readability
says nothing about solver permissions or scoring correctness.

## DISC-01-B — Grading-path identification

[Canonical definition](artifact-configuration-provenance.json#/items/1)

**Methods and evidence:** Resolve explicit registrations, dispatch, entry points,
and configuration deterministically; compare existing run metadata only when
available. Ambiguous code requires review; optional qualified model assistance
cannot establish inaccessible behavior. Complete source resolution can support
2/E1; level 3 needs independent resolution.

**Assessment limit:** Keep dynamic branches unresolved rather than importing or
executing arbitrary repository code during inventory. Identifying a route does
not prove that it grades correctly or that a launcher actually selected it.

## DISC-01-C — Grading dependency access

[Canonical definition](artifact-configuration-provenance.json#/items/2)

**Methods and evidence:** Walk declared dependencies for the selected grading
route and check reviewer access/version identity. Review unclear generated or
external dependencies for completeness; independent coverage review is separate
from successful deterministic reads.

**Assessment limit:** An unrelated missing inventory artifact is not automatically
a missing dependency of this scorer. Do not expose protected inputs to the solver
or award leakage-resistance credit from reviewer access.

## DISC-02-A — Declared configuration identity

[Canonical definition](artifact-configuration-provenance.json#/items/3)

**Methods and evidence:** Extract sourced configuration fields deterministically;
review claim-specific implications of unknowns. A complete declaration and
explicit provider-hidden limitations can support 2/E1. Independent reconstruction
of the sourced manifest, not reconstruction of the agent, supports level 3.

**Assessment limit:** Omitted, inaccessible, and provider-hidden are different
states. Do not infer credentials, internals, or observed execution from declared
settings. Materiality of an unknown field remains a review decision.

## DISC-02-B — Observed configuration agreement

[Canonical definition](artifact-configuration-provenance.json#/items/4)

**Methods and evidence:** Compare trusted run-bound observations with declarations
and retain per-field matched, mismatched, and unobservable outcomes. Level 3
requires an authorized independent repeat with configuration/control observations.

**Assessment limit:** Without a run, leave this NOT_ASSESSED; do not launch one
merely to fill the rubric. A configured version echoed by a launcher is not an
independently observed binary version. Unknown fields are not matches, and
justified differences must bound the claim rather than disappear from the diff.

## DISC-03-A — Runtime route compatibility

[Canonical definition](artifact-configuration-provenance.json#/items/5)

**Methods and evidence:** Use authorized provider-owned manifest and adapter
validation, retaining runtime identities and typed errors. Independent clean
setup and invalid-manifest controls provide the level-3 robustness evidence.

**Assessment limit:** A file-only assessment leaves runtime compatibility
NOT_ASSESSED. Distinguish unavailable runtime, unsupported manifest, and confirmed
validation failure; none is an agent-performance result. Local route-identity
checks are not provider validation or an end-to-end smoke pass.

## AUD-02 — Comparison setup fairness

[Canonical definition](artifact-configuration-provenance.json#/items/6)

**Methods and evidence:** Produce a deterministic setup diff, then obtain
scope-matched review of each material justification. Authorized matched
sensitivity tests address disputed effects and can support 3/E2 when they satisfy
the canonical robustness anchor; independent corroboration is additional.

**Assessment limit:** A diff alone supplies no full fairness score. Identical
configurations are not universally required; an intentional accommodation is not
a demonstrated unjustified advantage. Keep unresolved justification and effect
questions visible without starting an Audit workflow.

## ENV-01 — Verify solver tool access

[Canonical definition](artifact-configuration-provenance.json#/items/7)

**Methods and evidence:** Review the required action/tool/permission map, compare
exposed schemas and snapshots, and use authorized actual solver-role call,
reset, and error probes. Independent clean-runtime boundary checks are needed
for the level-3 corroboration.

**Assessment limit:** A schema inventory alone cannot establish working partial
coverage or award levels 2 or 3. Retain the native runtime evidence gap instead
of converting reviewer access or task reward into solver-readiness evidence.

## ENV-02-A — Inventory dependencies and substitution approval

[Canonical definition](artifact-configuration-provenance.json#/items/8)

**Methods and evidence:** Inventory observed real/replacement components, obtain
scope-owner decisions, and match their allowed differences and revisions to
installed identities. Independent provisioning/approval reconciliation is
separate from deterministic reference matching.

**Assessment limit:** Inaccessible approval records leave authority unresolved;
they do not prove that approval is absent. A matching digest or nonempty approval
reference cannot confer authority. Approval provides no replacement-fidelity
credit and does not authorize extra permissions.

## ENV-03 — Control external state

[Canonical definition](artifact-configuration-provenance.json#/items/9)

**Methods and evidence:** Capture dependency pins, snapshots, or live-source/time/
response identities; review live-state bounds and outcome relations. Authorized
replay and deliberate perturbation test those relations, with independent
corroboration for level 3.

**Assessment limit:** A pin alone cannot establish complete state control or
interpretability. Permitted state changes may legitimately change reward; do not
require invariance or call every changed outcome invalid. Retain unmeasured drift
and unresolved attribution rather than assigning full automated scores.

## CREATE-07 — Evidence revision consistency

[Canonical definition](artifact-configuration-provenance.json#/items/10)

**Methods and evidence:** Bind material task, scoring, configuration, runtime, and
applicable ETHOS/obligation identities; compare retained evidence with current
identities. Target-scoped change-injection tests must demonstrate affected stale
invalidation and unchanged lineage preservation for 3/E2; independent
corroboration, if present, supports E3.

**Assessment limit:** Shared hashes do not establish semantic compatibility or
holdout independence. Unresolved ETHOS change impact needs authorized review,
not an inferred unchanged state. Preserve original results and regrading history;
report affected stale evidence without rewriting native receipts or silently
transferring old scores.
