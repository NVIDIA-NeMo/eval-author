<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Recorded evidence audit

**Experimental.** After `prepare`, run this for either Harbor or NeMo Gym output:

```bash
python <skill_dir>/scripts/trace_environment.py audit-evidence --task-dir <task-dir>
```

The helper verifies the retained safe ATIF digest and structure, then writes
`private/evidence-audit.json` with owner-only permissions. Standard output gives
counts and the report path. The report contains scoped JSON locations rather
than messages, tool names, arguments, output text, or user-chosen identifiers.
An unchanged rerun returns the same report; a changed report or source is
rejected. Preserve prior evidence and use a fresh workspace for changed inputs.
The report stays private and is not added to exported products.

## Interpret the findings

| Finding | Meaning and next action |
| --- | --- |
| Missing observation | No result is linked to this call in its ATIF step. Locate missing source evidence; never borrow another step's result or the agent's claimed outcome. |
| Multiple observations | More than one result references the call. Inspect whether normalization retained fragments or duplicate outcomes; exact replay requires one usable result. |
| Unlinked observation | A result has no `source_call_id`. It may be legitimate non-tool feedback; inspect its role without assigning it to a nearby call. Unknown non-null call references are rejected by ATIF validation before auditing. |
| Explicit error / not-error | A recorded boolean flag describes the tool response. A not-error flag does not prove the task's requested outcome or side effect. |
| Unrecorded status | No supported error flag is present. Preserve uncertainty; do not classify prose, an empty response, or a final assistant claim as success. |
| Invalid / conflicting status | Flags are non-boolean or disagree. Correct source normalization with provenance; exact replay is unsupported until resolved. |
| Conflicting identical inputs | The same scoped tool and canonical arguments have different content or status. Inspect state, versions, and ordering. This does not itself identify a causal state transition. |
| Agent step without observation | Common for reasoning and final answers. It is an evidence-shape count, not a failure verdict or evidence that the task is incomplete. |

Pairing uses the call ID within the same step and trajectory. Reused IDs in a
later step or child trajectory never link their observations. Conflict groups
also respect explicit server identity. The audit does not infer human authorship,
find semantic failures, reconstruct files, recover missing state, or score traces.

Read the relevant safe source locations during the existing privacy and
construction review. Record material limitations in `candidate.uncertainties`.
Missing requirements still block candidacy; missing original state can permit
declared reconstruction under the existing rules. This diagnostic neither adds
a readiness gate nor replaces independent truth, executable checks, or repeated
provider-native proof. An empty findings list does not prove correctness.

## Replay error status

The compiler recognizes boolean `isError` and `is_error` on an observation result
or directly in its `extra` object. These are explicit source-extension flags,
not required ATIF fields. Multiple flags must agree; integers, strings and null
are invalid. Unrecognized nested status formats remain unrecorded and require
source normalization if their semantics matter. Never infer a flag from content.

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
