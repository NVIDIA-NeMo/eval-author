<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Ethos requirements across tool refactors (ASE-1059)

This focused manual exercise tests the write stage after a completed intent
interview. It is not a full exploration/interview evaluation or a repeated model
benchmark. Use fresh isolated workspaces and the repository's
[ethos-write skill](../../ethos/skills/ethos-write/SKILL.md), its template, and
validator. Supply only one case to each independent writer; do not supply the
comparison criteria or other case outputs. Preserve the actual review state:
saving and validating a draft does not establish user approval.

## Inputs and request

Create three workspaces: `before`, `after`, and `contract`. Each has `agent.py`
and `handoff.md` as below. The contract case uses the before source plus the
additional answer below. No customer data, credentials, or services are used.

Request in each workspace:

> We completed the exploration interview; the answers are in handoff.md. Write
> ethos.md for account-support from those answers and agent.py, then show it
> for review.

Restrict each writer to its workspace and the Ethos skill resources. Retain
its output file, actual validator result, and review handoff; do not fabricate
further user replies. Compare the application inputs before and after writing.

Shared `handoff.md`:

```markdown
# Account support: completed exploration handoff

The user selected the name account-support and asked to save ethos.md in this
workspace. The exploration interview is complete. These are their answers:

- Role and scope: help employees recover their own account access. Password
  resets are in scope. Account creation and recovering someone else's account
  are out of scope. Use the supplied synthetic account records.
- Mission and outcome: help employees regain access safely, without exposing
  their account. We judge successful verified recovery and appropriate
  escalation, not a business metric. The support team owns the result.
- Behavior: verify identity before any reset, never reveal account secrets,
  and escalate uncertain ownership without changing account state. An unknown
  account is uncertain ownership. Confirm success only after the reset succeeds.
- Principles: when ownership is uncertain, protect the account and escalate
  instead of guessing.
- Success: the verified owner receives confirmation and the reset is recorded;
  ambiguous or unverified requests escalate without a reset or secret disclosure.
- Trade-offs: identity verification and correctness are hard gates. Favor
  successful safe recovery over lower latency or cost. No numeric SLO is set.
- Constraints: use synthetic local data for this demo. No real accounts or
  external services. No additional organizational constraints were supplied.
- Vision: no additional use cases are planned.
- Evaluation setup: no evaluation suite is wired yet. A local manual smoke check
  can exercise a matching token, a wrong token, and an unknown account.
- Metric semantics: no ambiguous metrics have been identified.
- Author: Codex. Owner: support team.

Current implementation is in agent.py. The user asked to write the document
from these answers and source, then present it for review. The saved document
has not yet been reviewed by the user.
```

Before `agent.py` (also used by the contract case):

```python
"""Synthetic local account-support workflow; registry names are runtime tools."""

TOOLS = ("lookup_account", "verify_identity", "reset_password")


def lookup_account(request, accounts):
    return accounts.get(request["account_id"])


def verify_identity(account, token):
    return account is not None and token == account["verification_token"]


def reset_password(account):
    account["reset_count"] += 1
    return {"status": "reset_confirmed"}


def handle(request, accounts):
    account = lookup_account(request, accounts)
    if not verify_identity(account, request["token"]):
        return {"status": "escalated"}
    return reset_password(account)
```

After `agent.py`:

```python
"""Synthetic local account-support workflow; registry names are runtime tools."""

TOOLS = ("locate_and_verify", "prepare_reset", "apply_reset")


def locate_and_verify(request, accounts):
    account = accounts.get(request["account_id"])
    if account is None or request["token"] != account["verification_token"]:
        return None
    return account


def prepare_reset(account):
    return {"account": account, "next_reset_count": account["reset_count"] + 1}


def apply_reset(prepared):
    prepared["account"]["reset_count"] = prepared["next_reset_count"]
    return {"status": "reset_confirmed"}


def handle(request, accounts):
    account = locate_and_verify(request, accounts)
    if account is None:
        return {"status": "escalated"}
    prepared = prepare_reset(account)
    return apply_reset(prepared)
```

Append to the contract case's handoff:

> Our integration contract requires the exact registered tools `verify_identity`
> and `reset_password`. A successful `verify_identity` call must precede
> `reset_password` for the same account on every reset, even if an alternate
> API could produce the same result. The support team owns this contract.

Before comparing documents, execute both `handle` functions with independent
copies of `{"synthetic-1": {"verification_token": "match", "reset_count": 0}}`.
For requests to `synthetic-1` with token `match`, both should confirm and increment
`reset_count` once. With token `wrong`, or account `missing`, both should escalate
and leave the state unchanged. Compare response and state, not tool names.
This bounded smoke check establishes equivalence for these three fixture paths,
not for arbitrary account implementations.

## Semantic review

Compare meaning rather than wording across all behavioral sections, especially
Purpose & Outcomes, Behavior, Success Criteria, and Constraints:

- Before and after require safe verified recovery, confirmation after success,
  no secret disclosure, and escalation without mutation for uncertain ownership.
  Scope, priorities, and the absence of business targets or numeric SLOs remain
  stable. Implementation-only details can change in Tools, Harness, and current
  validation context. Do not promote helper names, count, or invocation sequence
  to mandatory behavior merely because they appear in the source.
- Both may describe their actual helpers as context. A lack of source support
  for a confirmed behavior should be stated as an implementation gap, not used
  to weaken the requirement.
- The contract case retains both exact tool identities, successful verification
  before reset for the same account, and the user-confirmed integration basis.
  The rule remains mandatory even if another implementation yields the same
  visible result. It must not disappear during abstraction into capabilities.
- All three files pass the bundled structural validator and remain pending user
  review. No application inputs change.

For downstream audit drafting, use a scoped request to propose items from an
accepted version of each Ethos and the corresponding registry. Keep capability
and failure-case names stable; incidental helper names belong in tool items.
Without an exact-tool requirement, capabilities use `required_tools: []` and
behavior evidence, even when the implementation uses tools. The contract case
retains its justified tool gates and requires trace-span or verifier evidence
of successful verification before reset for the same account. Presence of both
calls alone cannot establish that ordering or success. Non-tool evidence remains
unjudged until actual evidence and judgments are supplied; a proposed item is
not measured coverage. Existing reviewed audit items are reconciled explicitly,
not silently weakened by regenerating from changed source.

## Retained review notes — October 13, 2026

Executed the three writing requests above in separate Codex subagents with no
inherited conversation or access to sibling cases. Each read the revised
ethos-write skill and its template, wrote a draft, ran the bundled validator,
and presented the full file for review. No further user approval was supplied.
An independent reviewer read only the three input/output triples and compared
their meaning; it found no invented requirements, omissions, or semantic
regressions. The primary reviewer checked the same outputs.

| Check | Observed result |
|---|---|
| Fixture behavior | Before and after returned equal responses and final state for matching token, wrong token, and unknown account. Only matching token incremented the reset count. |
| Outcomes | Before: “Achieve successful verified recovery and appropriate escalation.” After: “Deliver successful verified recovery or appropriate escalation when ownership is uncertain.” Both retain support-team ownership and no business metric. |
| Success | Before: “the reset is recorded and the owner receives confirmation only after success.” After: “The verified owner receives confirmation after a successful reset, and the reset is recorded.” |
| Boundaries | Both retain verification before reset, no secret disclosure, escalation without state changes for unknown or unverified accounts, and the same exclusions and hard gates. |
| Tool obligations | Before says its helper names describe “the current implementation, not required tool identities.” After says their “names and internal arrangement describe implementation, not required interfaces.” Both still require verification before reset. |
| Implementation context | Before records lookup/verification plus reset. After records combined lookup/verification plus preparation/application. Both describe a synchronous local workflow and synthetic state, without introducing a model loop. |
| Confirmed contract | The third draft retains exact identities, successful verification, ordering, same-account binding, application to every reset, rejection of alternate APIs, and the support team as contract owner. It leaves `lookup_account` as implementation context. |
| Structure and review | All three passed `validate_ethos.py` with no warnings and remained drafts awaiting review. Source hashes were unchanged after writing. |

The contract draft's Behavior contains:

> The user-confirmed integration contract requires the exact registered tools
> `verify_identity` and `reset_password`. Every `reset_password` call must be
> preceded by a successful `verify_identity` call for the same account. An
> alternate API producing the same outcome does not satisfy this contract.
> The support team owns the contract.

Its Evaluation Setup also notes that returned status and state alone cannot
establish the exact identities, ordering, and same-account verification. This
is a retained contract requirement, not a claim that coverage was measured.

Artifact SHA-256 digests from the isolated exercise:

| Artifact | SHA-256 |
|---|---|
| Before source (also contract source) | `affb04ef13e5d7022bb58988e866151d8113e9058b3fa2ac96e17108a262b9c8` |
| After source | `e004e5644360752c120f6721c93f5ec957e1d6210bac6a70df3bf459b609bd16` |
| Before Ethos | `ed87cf72008ebef539fe4c316b3cf99b8662d74ea6c4bfe80dac326cd7187299` |
| After Ethos | `e093c6a88461cf8cbd19ac00f461750586a6b209d9d2702a154c7c2069f63f8e` |
| Contract Ethos | `05c49a29c4119f74c42c4e54f9ffd0fc92764057f2bc2ff60c5427cb2a308bbf` |

These notes retain the inputs, request, observed comparison, and salient output
excerpts; they do not depend on the temporary workspace surviving. This is one
generation per case after a supplied interview, not a full exploration run, a
repeated stability measurement, or a comparison against the old skill. It shows
the revised writing guidance can preserve the intended distinction in these
cases; it does not establish a general failure rate.

The repository's Ethos/schema/measurement contract tests provide separate
structural and runtime checks. The generic skill-creator validator rejects this
repository's existing `compatibility`, `not-for`, `triggers`, and `user-invocable`
metadata; those fields were preserved and checked with the repository's own
skill contracts instead.

### Downstream audit draft

A further independent subagent received only the after source and Ethos plus
this simulated user request:

> I have reviewed and accepted this ethos.md for account-support. Only draft
> .eval-author/audit-items.yaml from that Ethos and the registry in agent.py.
> There are no recorded traces to inspect and no wired eval suite. Show me
> the proposed scope and its limitations for review; don't run measurements
> or create tasks.

This supplied acceptance applied only to the audit exercise; the original
writing exercises remained review drafts. Following the revised audit guidance,
the agent drafted seven items: three registry tools, `account_recovery`, and
failure cases for uncertain ownership, unsuccessful reset, and out-of-scope
requests. The capability used `required_tools: []`. Every behavioral item used
non-tool evidence; exact `tool_call` checks appeared only on inventory items.
The verification-before-reset requirement used verifier evidence and the
confirmation-after-success requirement used ordered trace-span evidence.
No item inferred an obligation to use the renamed helpers.

The writer parsed YAML and checked references but reported unavailable schema
dependencies in its interpreter. The primary reviewer subsequently validated
the proposal against the full audit JSON Schema using the repository's virtual
environment and a draft envelope in memory; validation passed. No audit.md,
measurements, or tasks were generated. The proposal remained pending scope
review and made no coverage or runtime-readiness claims.

Proposal SHA-256:
`bc62fbdc7ea18347a13396ee6d73fb0b811d1b1cfd1095817389343230adb99d`.
This checks one downstream draft; the before/after and confirmed-contract
comparisons above exercised Ethos writing, not repeated audit generation.
