<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Recorded evidence audit

**Experimental.** After `prepare`, run this for either Harbor or Gym output:

```bash
python <skill_dir>/scripts/trace_environment.py audit-evidence --task-dir <task-dir>
```

The helper verifies the retained safe trace digest and structure, then writes
`private/evidence-audit.json` (`nemo.eval_author.trace_environment_evidence_audit.v2`,
bound by `safe_trace_sha256`) with owner-only permissions. Standard output gives
counts and the report path. Findings cite span and turn ids of the safe trace
(`span_id`, `turn_id`, result index) rather than messages, tool names,
arguments, output text, or user-chosen identifiers.
An unchanged rerun returns the same report; a changed report or source is
rejected. Preserve prior evidence and use a fresh workspace for changed inputs.
The report stays private and is not added to exported products.

## Interpret the findings

| Finding | Meaning and next action |
| --- | --- |
| Missing result (`missing_result`) | No result is linked to this tool call span. Locate missing source evidence; never borrow another call's result or the agent's claimed outcome. |
| Multiple results (`multiple_results`) | More than one result belongs to the call. Inspect whether the source retained fragments or duplicate outcomes; exact replay requires one usable result. |
| Unlinked result (`unlinked_result`) | A result on a turn is not tied to any call (`Turn.unlinked_results`). It may be legitimate non-tool feedback; inspect its role without assigning it to a nearby call. |
| `result_status_error` / `result_status_not_error` | The source recorded an error flag for the result. A not-error flag does not prove the task's requested outcome or side effect. |
| `result_status_unrecorded` | No supported error flag is present. Preserve uncertainty; do not classify prose, an empty response, or a final assistant claim as success. |
| `result_status_invalid` / `result_status_conflicting` | Flags are non-boolean or disagree. Correct the source with provenance; exact replay is unsupported until resolved. |
| Conflicting identical inputs (`conflicting_results_for_identical_arguments`) | The same scoped tool and canonical arguments have different content or status. Inspect state, versions, and ordering. This does not itself identify a causal state transition. |
| Agent turn without results (`agent_turn_without_results`) | Common for reasoning and final answers. It is an evidence-shape count, not a failure verdict or evidence that the task is incomplete. |

Compass pairs each result with its call inside one agent; ids reused by a
subagent never link across agents. Conflict groups also respect explicit server
identity. Calls the harness initiated are counted separately
(`harness_initiated_calls`). The audit does not infer human authorship,
find semantic failures, reconstruct files, recover missing state, or score traces.

Read the relevant safe source locations during the existing privacy and
construction review. Record material limitations in `candidate.uncertainties`.
Missing requirements still block candidacy; missing original state can permit
declared reconstruction under the existing rules. This diagnostic neither adds
a readiness gate nor replaces independent truth, executable checks, or repeated
provider-native proof. An empty findings list does not prove correctness.

## Replay error status

Each Compass `ToolResult` carries `error_status`: `error`, `not_error`,
`unrecorded`, `invalid` or `conflicting`. For ATIF, Compass derives it from
boolean `isError`/`is_error` on the result or in its `extra`; multiple flags
must agree, and integers, strings and null are invalid. For MLflow and Intake
only an ERROR status is recorded (`error`); other results are `unrecorded`. Never infer
a status from content.

Generated call-fixture cases keep an optional `is_error`: `true` for `error`,
`false` for `not_error`, absent when unrecorded. MCP results carry the same
status as `isError`. Identical inputs with different status, including
unrecorded versus not-error, are ambiguous for deterministic replay. Cases
without a status keep the MCP `isError: false` transport convention; the audit
still identifies their status as unrecorded. That convention must not be used
as historical success evidence.

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
establish that a reconstructed task works in Harbor or Gym.
