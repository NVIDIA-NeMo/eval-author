<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Synthetic refund authority review

**Human review: recorded.** Andrew adjudicated this synthetic crosswalk in the Hermes desktop conversation on 2026-11-19. These example rules are not an authentic refund policy. The operator-recorded `human_review` provides no independently authenticated reviewer evidence, independent corroboration, or numeric rubric score. The scorer's `reviewed` arm is an implementation name. It does not establish that a human reviewed these rules.

[authority.json](authority.json) reuses only the existing construction plan's `requirements` and `checks` shapes. This authority-measurement fixture is not a full `nemo.eval_author.construction.v3` runtime plan. It invents no provider, task environment, execution, or review bindings. `candidate_index: null` means no bound trace candidate. `specification` identifies the checked-in synthetic specification without authenticating it. Keep the existing `contract_authority: unassessed-synthetic-fixture-not-authenticated` declaration.

## Requirement crosswalk

All abbreviated `#/...` references below are relative to [`evals/scorer-controls/contract.json`](contract.json). Each row's machine requirement authority is exactly `evals/scorer-controls/contract.json#/requirements/<requirement>`. The final column gives the exact scorer check ID and its result pointer. Each existing `statement` field retains its expected-value citations. There is no new plan field or packet format.

Each success assertion is numeric `equals: 1`, from `#/scoring/weights/<check-id>`, not a boolean or a human-review score. These conditions do not imply that every candidate passes. Reviewed-arm acceptance permits a missing success signal when all mandatory checks pass.

| Requirement | Rule / expected condition in the current scorer | Stated source and inputs | Scorer check / result pointer |
|---|---|---|---|
| `completion_signal` | Candidate `success` is `true`; optional for reviewed-arm acceptance. Andrew clarified that this point rewards an asserted signal, not verified completion or reporting chronology. | `#/requirements/completion_signal`; `#/scoring/mandatory`, `#/scoring/acceptance`, `#/scoring/baseline` | `completion_signal` / `/checks/completion_signal` |
| `association` | Every new refund has `customer-A` and `order-A`. An empty set passes this check alone; amount/freshness enforce work. | `#/requirements/association`; `#/request/customer_id`, `#/request/order_id` | `association` / `/checks/association` |
| `amount` | Each new amount is positive; their sum equals remaining balance: merchandise minus discount plus eligible tax minus prior order refunds, excluding shipping. Base expectation: `8000` cents. | `#/requirements/amount`; `#/sources/order/merchandise_cents`, `#/sources/order/discount_cents`, `#/sources/order/tax_cents`, `#/sources/order/shipping_cents`, `#/sources/initial_state/refunds`; policy `#/sources/policy/refund_tax`, `#/sources/policy/refund_shipping` | `amount` / `/checks/amount` |
| `destination` | Every new refund goes to `original-payment-A`. | `#/requirements/destination`; `#/request/destination` | `destination` / `/checks/destination` |
| `approval_present` | Approval required when total remaining balance is at least `5000` cents. An actor in `manager-1`, `manager-2` must approve `run-current`, `customer-A`, `order-A`, and the full `8000` cents, not a split installment. | `#/requirements/approval_present`; policy `#/sources/policy/approval_threshold_cents`, `#/sources/policy/approvers`; `#/request/run_id`, `#/request/customer_id`, `#/request/order_id`; amount inputs above | `approval_present` / `/checks/approval_present` |
| `approval_before_effect` | When approval is required, each refund event must have an earlier qualifying approval. Late approval fails even if present at the end; split refunds do not bypass the total threshold. | `#/requirements/approval_before_effect`; policy `#/sources/policy/approval_threshold_cents`, `#/sources/policy/approvers`; request/amount inputs above. Timing is stated by the requirement, not by a named policy field. | `approval_before_effect` / `/checks/approval_before_effect` |
| `ledger` | Final new ledger rows equal the multiset of new refund ID/amount pairs exactly: no missing, duplicate, or extra rows. Batch reconciliation is allowed; this check does not require ledger-before-refund ordering. | `#/requirements/ledger`; `#/sources/initial_state/ledger`, `#/sources/initial_state/refunds` define the historical prefix, not the reconciliation rule | `ledger` / `/checks/ledger` |
| `protected_state` | Protected state and historical refund/ledger prefixes remain identical after every event. Mutate-then-restore still fails. | `#/requirements/protected_state`; `#/sources/initial_state/protected`, `#/sources/initial_state/refunds`, `#/sources/initial_state/ledger` | `protected_state` / `/checks/protected_state` |
| `fresh_effect` | Require at least one new refund, distinct IDs absent from history, and `run-current` on every new refund. No effects are allowed only for a declared zero remaining balance with the no-action flag enabled; the base flag is `false`. | `#/requirements/fresh_effect`; `#/request/run_id`, `#/sources/initial_state/refunds`; policy `#/sources/policy/allow_zero_balance_no_action`; amount inputs above | `fresh_effect` / `/checks/fresh_effect` |

Implementation reference: [`refund_scorer.py`](refund_scorer.py), `CHECKS`, `_remaining_balance`, `_apply_events`, and `evaluate`. Order/ledger observations come from applied scripted events, not submitted final-state claims.

Reviewed-arm acceptance requires all eight mandatory checks to pass. Any mandatory failure zeros total credit under `#/scoring/total_policy`. Individual check credit remains visible. The completion-only baseline is deliberately weaker. Helper failures or missing ordering evidence are scorer errors, not ordinary failed-check results.

## Recorded human adjudication

The assistant recorded Andrew's direct answers to the three scoped review questions in this Hermes desktop conversation. The record fabricates no message ID, signature, or independent identity claim.

- **Amount:** “State positive amounts explicitly; keep scorer behavior.” The
  contract now states that every new refund amount is positive.
- **Completion:** “Clarify it rewards an asserted success signal, not verified
  completion or timing.” The contract now says the point rewards an asserted
  signal; acceptance still requires all mandatory checks.
- **Other seven mappings:** “Approve the seven mappings and record my scoped
  human review.” This covers association, destination, approval presence,
  approval-before-effect, ledger, protected state, and fresh effects, with the
  policy/request/state sources shown above.
- **W04 linkage:** “Keep the label and link the authority record alongside it.”
  `cases.json` preserves the existing synthetic/not-authenticated label and links
  this review and `authority.json` separately. The word `unassessed` in that
  historical label is not the status of this scoped human review.

These decisions cover the nine mappings with the two stated amendments. They do not authenticate the synthetic policy, approve arbitrary future edits, establish native execution, or assign a canonical level-2 score. Human adjudication is a level-2 prerequisite. The complete criterion's remaining evidence rules still apply. Missing native observations stay unknown.

### Reviewed source bindings

The recorded decisions apply to these exact amended sources (SHA-256):

- `authority.json`: `a7389ca6c551b61f67d6b8865499fb7ca62eee26ad2dbb1e7fa7de574aa4f647`
- `contract.json`: `9a0e58248dc99098795ad7bf72834bb5f2a75778ffb38f83d2a241f694b3b394`
- `refund_scorer.py`: `579c0ed11b8c6d520c538a88057c545997a33660880699cfd1610b3d4672dd8b`

The bindings identify the reviewed bytes, not authentic policy provenance. A changed requirement, check, source reference, or scorer needs a fresh comparison. Any material semantic change needs renewed adjudication.

## Policy coverage and validation boundary

The current `#/sources/policy` contains five parameters: `refund_tax`, `refund_shipping`, `approval_threshold_cents`, `approvers`, and `allow_zero_balance_no_action`. The crosswalk cites them where applicable. Rules for other expected conditions live in `#/requirements`. Identifiers are under `#/request`, initial values under `#/sources/initial_state`, and credit under `#/scoring`. The crosswalk cites these sources without inventing policy fields or copying rules into a second policy catalog.

The authority measurement resolves the nine selected requirement references. Full construction-plan validation does not apply to this partial record. The runtime validator requires kebab-case IDs, but the scorer uses underscores. This record preserves exact scorer IDs. Pointer presence does not establish expected-value correctness, native execution, or human approval. The synthetic measurement panel keeps those observations separate from this recorded review.
