<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Refund the remaining balance

Perform refund actions against the local ledger. Read `/app/contract.json` for
the request, policy, order, initial state, and scoring rules. This offline ledger
is the target system. It does not move money or authenticate a manager.
An `approve` action represents a manager event in this simulation.

Connect to the Unix socket `/run/refund/ledger.sock`. Send one UTF-8 JSON object
followed by a newline per connection. Read one JSON response, then close the
connection. Python's standard-library `socket` module supports this interface.
The socket needs no network or credentials.

Requests have exactly one of these forms:

- `{"operation": "read"}` returns `{"ok": true, "value": {"candidate": ..., "state": ...}}`.
- `{"operation": "act", "event": EVENT}` performs one action and returns `{"ok": true}`.
- `{"operation": "complete", "success": true}` sets the optional completion signal and returns `{"ok": true}`.

`success` must be a boolean. You can set it to false. Errors return
`{"ok": false, "error": "invalid_request"}` without applying that request.
Each accepted action persists before the response. Calls are serial. The ledger
has no undo, deletion, idempotency key, or solver reset operation. Read the ledger
before retrying an action whose response you did not receive. A repeated action
creates a repeated event and can lose credit.

Each event has one of these exact forms:

- `approve`: `kind`, `run_id`, `customer_id`, `order_id`, `amount_cents`, `actor`.
- `refund`: `kind` and `refund`. The refund has `id`, `run_id`, `customer_id`,
  `order_id`, `amount_cents`, and `destination`.
- `ledger`: `kind` and `entries`. Each entry has `refund_id` and `amount_cents`.

Use `"kind": "approve"`, `"kind": "refund"`, or `"kind": "ledger"` to select
an action. Text fields must be nonempty strings. Ledger batches must be nonempty.
The ledger validates request shapes. It permits semantically wrong amounts,
entities, approvals, and destinations so the verifier can assess those effects.

Refund discounted merchandise plus eligible tax, minus prior refunds. Exclude
shipping. Each new refund must be positive. The sum must equal the remaining balance. If that total
reaches the inclusive approval threshold, a listed manager must approve it before
any refund action. Use the current run, requested customer, and requested order.

You can split refunds and batch ledger entries. Each new refund needs one exact
ledger entry, a unique fresh ID, and the requested original payment destination.
Ledger entries may come before or after refund actions, in any row order.
The final ledger must reconcile every new refund exactly once, with no extras.
Required approval must still precede every refund action.
With no refund actions, the ordering check passes because no refund occurred out
of order. Other mandatory checks still reject missing approval or required work.
Preserve unrelated state and all historical rows. The action interface exposes
no operation that edits that history or protected state.

For a zero balance, perform no refund actions only when the policy permits no
action. The public contract defines all check weights and mandatory gates.
The completion signal earns one optional point. All mandatory checks must pass
for any credit. Valid work without the optional point retains its other credit.

Limits: at most 1000 actions, 1 MiB per request and serialized candidate,
and 10000 entries per ledger batch. Money uses integer cents from 0 through
1000000000000. This is the request-shape range. A zero-cent new refund loses
the mandatory amount check even when the total is correct. Boolean values are
not integers. Duplicate JSON keys are invalid.
Do not add fields, non-finite numbers, or diagnostic fault actions. The service
waits at most two seconds for each request. Close each connection after its response.

Write scratch files only under `/work`. A submitted file or final message cannot
replace ledger actions. The verifier reads the service's private action record
and checks its state against event replay. The environment owns the record and
resets it on a fresh service start. Do not change the contract, service, verifier,
or reward files. Missing or inconsistent records are execution errors with no
reward. They are not successful negative controls.

Verifier source is public and readable when uploaded by the harness. It is
integrity-protected: you may inspect it but must not change it. Reference
solutions and private record files are not solver-readable inputs. Use the
socket to read state. Only the harness may reset the service to exact initial state.
The contract, helpers, and pinned image define this local simulation. No live
API, payment provider, or clock determines credit. A tax or policy change needs
a new correct answer and fresh evidence.
