<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Tool-call corpus

**Experimental:** Part of the [trace-to-environment workflow](../SKILL.md).

`corpus-import` records every tool call from many ATIF traces in one private
corpus. Each call keeps its source, trace and session identity, tool and schema
version, call ID, arguments, results, error status, order, and timing. Values
are copied as recorded. The command does not infer, normalize, deduplicate,
or merge anything, and it does not derive a task.

## Members manifest

List the selected traces and describe how you chose them:

```json
{
  "schema": "nemo.eval_author.tool_corpus_members.v1",
  "selection": "All production runs of support-agent 2.1, 2026-10-01 to 2026-10-14",
  "members": [
    {"atif": "traces/run-001.atif.json", "source_kind": "mlflow"}
  ]
}
```

Relative `atif` paths resolve against the manifest's directory. `source_kind`
records the original format (`atif`, `intake`, `mlflow`, `otel`, or `gym`). A
member must be a regular file of at most 128 MiB containing ATIF v1.0–v1.7
that passes the same validation as `prepare`. The manifest rejects a path or
byte-identical file listed twice.

```bash
python <skill_dir>/scripts/trace_environment.py corpus-import \
  --members members.json --corpus-dir .eval-author/tool-corpora/<name>
```

The import validates every member before writing. A failure leaves no corpus
directory behind. The corpus directory must not already exist; import into a
new directory each time.

## Output

`manifest.json` (`nemo.eval_author.tool_corpus.v1`):

- `sources[]`: one entry per member, in manifest order.
  - Source fields: `source_sha256` (digest of the exact source bytes), `path`
    as listed, `source_kind`, `selection`, and `atif_version`.
  - `trajectories[]`: one entry for the root and each subagent trajectory.
    Each holds `trajectory_path`, the recorded `session_id` and
    `trajectory_id`, the recorded `agent` object without its tool
    definitions, and `tool_definitions` keyed by definition digest.
- `counts`, `calls_sha256`, `calls_schema`, and the `extractor` digests of the
  code that produced the corpus.

`calls.jsonl` (`nemo.eval_author.tool_corpus_calls.v1`) has one row per tool
call:

| Field | Meaning |
|---|---|
| `source_sha256`, `trajectory_path`, `step_id`, `call_index` | Locator of the call in its source |
| `ordinal` | Call order within that trajectory, starting at 1 |
| `timestamp` | The step's recorded timestamp, or `null` |
| `tool_call_id`, `function_name`, `server` | Call identity as recorded; `server` only from `extra.tool_scope.server` |
| `schema_status`, `schema_sha256` | `declared` with the definition digest, or `undeclared` / `ambiguous` with `null` |
| `arguments`, `call_extra` | Verbatim call arguments and `extra` |
| `results[]` | Every same-step result whose `source_call_id` matches, in recorded order: `result_index`, `error_status`, and the verbatim `result` |

A call's schema resolves only to definitions in the same trajectory with the
same function name and server. `ambiguous` means that scope holds more than one
non-equivalent definition. `error_status` comes from explicit `isError` or
`is_error` flags only: `error`, `not_error`, `unrecorded`, `invalid`, or
`conflicting`. Converters that do not emit those flags produce `unrecorded`.

## Limits

- `ordinal` orders calls within one trajectory. ATIF does not record how a
  parent and its subagents interleave; use `timestamp` where recorded.
- Rows are per call. A result without a matching call, or a call without a
  result, is recorded as-is (`results: []`) but not yet classified or counted.
- The corpus copies source content verbatim, including whatever sensitive
  data the sources contain. Import privacy-reviewed ATIF. The corpus is
  owner-private (`0700` directory, `0600` files) and has no export path.
