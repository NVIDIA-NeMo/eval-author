<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Stateful Workplace prototype

- Run three email tools against reproducible synthetic state.
- Compare their responses and state transitions with reserved observations from pinned NeMo Gym source.
- Grade a reply task in Harbor's separate, offline verifier.

## Run

Use Eval Author's prepared Python 3.12 or 3.13 development environment with Harbor 0.24.0 and MCP installed.
Run from the repository root.

```bash
python skills/eval-author-trace-environment/examples/workplace/prepare_trace.py --access-mode stateful_simulation --output /path/to/new-private-workspace
python skills/eval-author-trace-environment/examples/workplace/check_harness.py --output /path/to/new-harness-evidence
python skills/eval-author-trace-environment/examples/workplace/run_harbor.py --output /path/to/new-harbor-evidence --home /path/to/isolated-home
```

The isolated home needs Docker CLI Compose and Buildx plugins available without copying personal Docker credentials.
The Harbor runner retains failed attempts, checks actual rewards and agent exit status, and stops on an unexpected result.
A rerun requires a new output directory.

For an authorized model-backed host check, add `--live-codex --auth /path/to/existing/auth.json` to `check_harness.py`.
It uses the existing Codex account, a private temporary configuration, and read-only shell sandboxing.
Only the three synthetic MCP tools receive explicit approval in that temporary configuration.
Authentication copies are removed when execution ends.
This check uses Harbor's registration writer and actual Codex MCP calls, but is not a model-backed Harbor container trial.
Add `--paired-output /path/to/new-simulation-evidence` to run the same prompt first against exact replay and then against simulation.
The replay calibration is captured from an independent simulation session and compiled through the production replay compiler.
This paired check measures one unseen write/read outcome, not source conformance or general model performance.

See [conformance](conformance/README.md) for the frozen development and held-out comparison commands.
Keep that directory out of agent images.

## Supported behavior

The tool interface exposes `email_search_emails`, `email_get_email_information_by_id`, and `email_reply_email`.
Search supports case-insensitive word matching, inclusive ISO dates, pagination, and empty results.
Replies append an outbox record visible to subsequent reads.
Missing records and fields retain source-domain responses.
Malformed calls and behavior outside the bounded implementation return explicit unsupported results.
Transport failures remain protocol or execution errors.

The simulation follows [Gym's pinned email implementation](https://github.com/NVIDIA-NeMo/Gym/blob/0c5cfb71dd8615008fc69b63cdc98c2775278aa5/resources_servers/workplace_assistant/workplace_assistant_tools/email.py) for the documented subset.
The source clock is fixed at `2023-11-30 23:59:00`.
Equal-timestamp ordering, non-ISO date filters, nonpositive page sizes, and generated-ID collisions remain unsupported.
No fallback returns an unrelated recorded response.

## State and grading

`prepare_trace.py` binds the generated task's service and verifier seeds to the private construction snapshot.
The synthetic trace supplies a matching message's identifier, subject, and body.
Contacts, timestamps, and the other initial records are explicitly authored reconstruction, not recovered historical mailbox state.
The service owns state in a separate container with no shared state volume or agent-accessible reset/export endpoint.
A collect hook closes writes under a lock and exports the actual resulting state.
Harbor transfers that artifact to its separate no-network verifier.
The export distinguishes reconstructed initial state from observed simulation state.
The verifier checks its own trusted seed rather than accepting the export's initial-state claim as authority.

The task requires exactly one confirmation reply while preserving every original record.
Read order and redundant reads do not affect the grade.
Wrong bodies, wrong message targets, duplicate replies, and unrelated extra replies fail.
Missing or malformed exports are infrastructure failures, not task failures.
Each trial starts a new service from the same seed.
Reset tests also compare restored state directly rather than relying only on repeated successful scores.

## Access and evidence boundaries

`real`, `exact_replay`, `stateful_simulation`, and `none` are distinct prototype access modes.
Real service access is not provided.
The existing product `mock` contract remains exact replay and is unchanged.
The simulation is an explicitly selected example adapter, not an automatic replacement in the production fixture compiler.

The conformance corpus contains actual executions of pinned Gym code on independently authored synthetic records.
It is not the requested Oct 30 tool corpus and does not establish fidelity to live workplace services.
Development and held-out source sessions were reserved before backend inspection.
The independently implemented backend was written from the source contract without reading held-out observations.
The before comparison uses development-only exact-response replay.
State, output semantics, output shape, error behavior, and unsupported rates remain separate measurements.
Injected faults are test controls, not source observations.

The prototype does not establish broad task acceptance, production workflow integration, or a before/after model-performance improvement.
The retained [native checks](native-checks.json) contain allowlisted control outcomes and source bindings.
Native scripted controls test environment and verifier behavior.
The live host check tests actual agent discovery and calls.
Both remain separate from source conformance and historical-corpus coverage.
