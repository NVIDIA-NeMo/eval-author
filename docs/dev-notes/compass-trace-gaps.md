<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NeMo Compass trace integration: known gaps

Eval Author loads every trace through NeMo Compass `trace_ingest` (trace contract
v3) in `skills/eval-author/scripts/trace_loading.py`, and reads only the Compass
`Trace` after that. This note lists what we know is missing from that integration.
Each item is deliberately deferred until real usage shows it matters. When one
becomes a problem, fix it here or in Compass rather than working around it in a
single skill.

## Eval Author

| Gap | Today | Revisit when |
|---|---|---|
| More sources | Compass can load Langfuse (live and v3/v4 exports), LangSmith (live and export file), Braintrust and live MLflow experiments. Eval Author exposes only `atif`, `mlflow` (file), `gym`, `intake` and `trace`. Compass has no command that writes normalized traces, so `--source-kind trace` cannot stand in for these sources | A user brings traces from one of these sources |
| Intake selection | `--source-kind intake` passes only `--session-id` through. Compass's `IntakeTraceQuery` also filters by `agent_name`, `experiment_id`, `evaluation_name`, `test_case_name` and `status`, but not by trace ID. The inspect workflow selects one trace by ID through the `nemo` CLI, so a user moving from inspection to `prepare` has to look up its session and time window | Real Intake usage, or a window that holds more than `--max-traces` sessions |
| Intake credentials | The base URL and `NMP_ACCESS_TOKEN` are supplied by hand instead of read from the `nemo` CLI's configuration | Users trip over it |
| Loss policy | The loader refuses `merge_incomplete`, `continued_trajectory_not_followed` and unknown codes. It accepts `llm_calls_aggregated`, `unresolved_subagent_ref` and `steps_outside_agent_not_in_turns` and leaves them in `Trace.losses`, but no consumer reports them: audit doesn't count an unresolved subagent's tool calls, and the trace workspace doesn't block on it | Coverage or task results turn out wrong because of a missing subagent |
| Repairs outside Compass | `prepare` escapes one provider-redaction quote before loading (`_parse_or_repair`) and strips encoded image data after loading (`bound_images`). `trace_loading.py`, audit and review do neither, so they likely reject a file `prepare` accepts (not verified) | Someone hits that mismatch |
| Trace walkers | Audit (`audit_spec/measurements/trace_tools.py`), local review (`render_review.py`) and the trace workspace (`eval_author_shared/trace_view.py`) each walk the Trace. Review shows each agent's turns in tree order, not `turns_in_execution_order` | Next change to review rendering |

## Asks for Compass

| Ask | Why Eval Author needs it | Eval Author's stand-in |
|---|---|---|
| Follow external ATIF `trajectory_path` and continuation files | Harbor's Terminus-2 writes summarization subagents and, with linear history, `trajectory.cont-N.json` continuations as separate files. Compass reads neither, so those subagents stay unresolved and continued runs are refused | None; they load partially or are refused |
| A public loader factory in `trace_ingest` that builds a loader from a source configuration | Selecting a loader is private to Compass's CLI (`_configured_trace_loader`), so every consumer repeats the dispatch | An `if`/`elif` per source kind in `trace_loading.py` |
| A typed tool-result error status | `ToolResult.is_error` is `null` both when no flag was recorded and when flags were invalid or conflicting; the ATIF and Intake loaders record the difference only in `attributes["is_error_status"]` | `trace_view.error_status` reads that attribute |
| Identifier marks on model fields | The safe copy must pseudonymize every identifier, and new fields must not pass through unscrubbed | `trace_view.FIELD_ROLES` classifies every field by hand and fails on any field it doesn't know |
| Turn execution order across agents, and strict tool-definition scope | Citations and construction need the order turns ran and the tools each call could see (deferred in NVIDIA-NeMo/nemo-compass#79) | `trace_view.turns_in_execution_order` and `tool_definitions_in_scope` |
| An Intake file loader and a trace-ID query | Offline Intake and OpenTelemetry data cannot be loaded; NVIDIA-NeMo/nemo-compass#72 was closed | Live Intake only |
| Turns for NeMo Gym `ng_trajectory` rollouts | `prepare` needs turns, so those rollouts are refused | None |
| Merging ATIF continuation files that share a `session_id` | Listed as open in Compass's trace-sessions proposal | Refused as `continued_trajectory_not_followed` |

## Packaging

- `nemo-compass` is pinned to a Git revision in every `requirements.txt` and
  `pyproject.toml`, because trace contract v3 is not on PyPI yet. Switching back
  to a PyPI pin also means rewriting `check_installation()`, which requires the
  Git revision recorded in `direct_url.json`.
- Loading traces installs Compass's whole analysis stack, including litellm,
  scikit-learn, tokenizers, nooa and the OpenTelemetry exporter. A loader-only
  `trace-ingest` distribution would remove that. `trace_loading.py` currently
  refuses an installed `trace-ingest` distribution (`_SHADOWING`), which must
  change before adopting one.
