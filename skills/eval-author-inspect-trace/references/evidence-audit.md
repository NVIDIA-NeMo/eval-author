<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->


# Recorded evidence audit

`<skill_dir>` is the installed `eval-author-inspect-trace` directory.

**Experimental.** After `prepare`, run this for either Harbor or NeMo Gym output:

```bash
python <skill_dir>/scripts/trace_privacy.py audit-evidence --task-dir <task-dir>
```

The helper verifies the retained safe Trace digest and structure, then writes
`private/evidence-audit.json` (schema `evidence_audit.v2`, scope
`recorded_result_coverage`, bound by `safe_trace_sha256`) with owner-only
permissions. Standard output gives counts and the report path. Findings cite
safe-trace `turn_id` or `span_id` values rather than messages, tool names,
arguments, output text, or user-chosen identifiers.
An unchanged rerun returns the same report; a changed report or source is
rejected. Preserve prior evidence and use a fresh workspace for changed inputs.
The report stays private and is not added to exported products.

## Interpret the findings

| Finding | Meaning and next action |
| --- | --- |
| Missing result | The TOOL span records no result. Locate missing source evidence; never borrow another call's result or the agent's claimed outcome. |
| Multiple results | The TOOL span records more than one result. Inspect whether loading retained fragments or duplicate outcomes; exact replay requires one usable result. |
| Unlinked result | A turn records a result that Compass could not pair with a call. It may be legitimate non-tool feedback; inspect its role without assigning it to a nearby call. |
| Explicit error / not-error | A recorded boolean flag describes the tool response. A not-error flag does not prove the task's requested outcome or side effect. |
| Unrecorded status | No supported error flag is present. Preserve uncertainty; do not classify prose, an empty response, or a final assistant claim as success. |
| Invalid / conflicting status | Flags are non-boolean or disagree. Correct source normalization with provenance; exact replay is unsupported until resolved. |
| Conflicting identical inputs | The same scoped tool and canonical arguments have different content or status. Inspect state, versions, and ordering. This does not itself identify a causal state transition. |
| Agent turn without results | Common for reasoning and final answers. It is an evidence-shape count, not a failure verdict or evidence that the task is incomplete. |

The report also counts agents, turns, calls, results, paired calls and
harness-initiated calls. Compass pairs each result with its call; reused call
IDs in another turn or agent never link results. Conflict groups are scoped to
the owning agent span and the explicit tool server. The audit does not infer
human authorship, find semantic failures, reconstruct files, recover missing
state, or score traces.

Read the relevant safe source locations during the existing privacy and
construction review. Record material limitations in `candidate.uncertainties`.
Missing requirements still block candidacy; missing original state can permit
declared reconstruction under the existing rules. This diagnostic neither adds
a readiness gate nor replaces independent truth, executable checks, or repeated
provider-native proof. An empty findings list does not prove correctness.

## Replay error status

Error status comes only from the loaded `ToolResult`: `is_error` gives `error`
or `not_error`; otherwise the loader's recorded `is_error_status` attribute
marks `invalid` (non-boolean flags) or `conflicting` (disagreeing flags), and a
result without a flag is `unrecorded`. Never infer status from content.

Explicit status is retained as `is_error` on generated call-fixture cases and
as `isError` on MCP results. Identical inputs with different status, including
unrecorded versus explicit not-error, are ambiguous for deterministic replay.
Cases without flags retain their legacy fixture shape and MCP `isError: false`
transport convention; the audit still identifies their status as unrecorded.
That convention must not be used as historical success evidence.

Existing generated fixtures with explicit flags may fail current derivation
checks because older compilation discarded status. Retain their old artifacts,
regenerate in a fresh workspace and rerun affected proof and publication review.
Do not edit retained receipts or reuse proof for changed fixtures.

## Regression checks

From an Eval Author development checkout with its test dependencies installed:

```bash
uv run --locked pytest -q skills/eval-author-trace-environment/evals/tests
uv run --locked pytest -q tests/test_trace_environment.py tests/test_tool_call_fixtures.py
```

The skill-local tests include actual stdio replay with synthetic observations,
parallel calls, repeated IDs, child scopes, unknown/malformed status, changed
source digests and linked output paths. They do not execute a live agent or
establish that a reconstructed task works in Harbor or NeMo Gym.
