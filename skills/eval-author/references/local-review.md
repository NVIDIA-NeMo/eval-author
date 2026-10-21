<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Read Gym records and provider traces locally

Use the [bundled reader](../scripts/render_review.py) for generated Gym cases,
retained Gym rollout rows, and ATIF, MLflow, Gym, or normalized NeMo Compass
trace files that Harbor's native viewer cannot open. Keep native Harbor
tasks and jobs in `harbor view`; verify its commands against the installed
runtime. Rendering reads existing evidence and does not execute an evaluation,
validate a task, measure coverage, or prove the agent succeeded.

The `gym-jsonl` reader needs only Python 3.11+ and the standard library. Every
other format loads through the sibling [`trace_loading.py`](../scripts/trace_loading.py)
and needs Python 3.12–3.13 with `uv run --with-requirements
"<eval-author-skill-dir>/requirements.txt"`. Resolve `eval-author` in the same
installed skill tree as the selected authoring sub-flow; do not assume the
evaluated repository contains this script. Replace the skill-directory
placeholder below with that resolved absolute path when saving copyable
commands in a suite README.

## Select evidence explicitly

Use a known local artifact and an explicit evidence role:

| `--evidence-role` | Meaning supplied by the caller |
| --- | --- |
| `case` | A generated case specification, including its grading fields |
| `source` | Evidence used to propose or construct a case |
| `control` | A recorded task/verifier control, such as a reference solution |
| `agent` | A recorded attempt by the evaluated agent |

These labels describe the selected artifact's intended role. The reader does
not verify them or establish that a source trace is an attempt at a generated
case. Use separate reports for different evidence roles. Never associate inputs
and attempts by row order or prompt similarity; preserve the provider identities
and known mappings in the suite's existing records.

For Gym JSONL, `--rows` is required and selects one-based **physical line
numbers**. Commas and inclusive ranges are accepted, for example `1,3-5`. Blank
and malformed selected lines remain visible with their original line numbers;
they do not shift later selections. Select the known cases or attempts, without
silently expanding the selection to an entire dataset.

```bash
python "<eval-author-skill-dir>/scripts/render_review.py" \
  --input .eval-author/gym/cases.jsonl --format gym-jsonl --rows '1,3-5' \
  --evidence-role case --output-dir .eval-author/review/cases-001
```

Use the same command shape for recorded rollouts, selecting the actual retained
file and lines and choosing `control` or `agent` as appropriate. A case row with
no recorded response remains a case specification, not a completed run.
For an `agent` or `control` record, a missing response adds an attention notice;
the reader cannot establish whether execution occurred without capture.

For a trace file, omit `--rows` and name its `--format`: `atif` (one
trajectory per `.json` file, JSONL otherwise), `mlflow` (a `Trace.to_dict()`
export), `gym` (one `.json` rollout record), or `trace` (normalized
`traces.normalized.jsonl` from `trace_loading.py`). Every trace in the file
becomes one report record:

```bash
uv run --with-requirements "<eval-author-skill-dir>/requirements.txt" \
  "<eval-author-skill-dir>/scripts/render_review.py" \
  --input .eval-author/traces/source.atif.json --format atif \
  --evidence-role source --output-dir .eval-author/review/source-001
```

The output directory must be new; create its parent first if needed. Use a fresh
name when regenerating a report so prior evidence survives. Open the resulting
`report.html` directly in a browser. No server, browser installation by the
script, model credentials, network connection, or upload is required.

## Read the report with its limits

The report presents Gym input and instructions separately from other row fields,
including verifier expectations. Recorded Responses messages, tool calls and
results, rewards, errors, and provider indices appear when present. It preserves
unknown data for inspection; it cannot infer hidden grader behavior from fields
alone. Missing capture remains missing, and a reward is not relabeled as a
successful run.

For trace formats, the reader shows the loaded NeMo Compass trace: every span
depth-first, indented by depth, with its kind, tool or span name, model, start
and end times, input and output, recorded error, each AGENT span's conversation
turns (role, content, reasoning, and unlinked tool results), and each tool result with its
`error_status` (`unrecorded` when the source recorded no usable flag or status). Span
attributes, tool definitions, and token counts stay under expandable fields. It
is not a provider schema validator: a file the pinned loader rejects is reported
as a malformed or unsupported record without its content. Images and external
attachments are never fetched or displayed, and continuation or source paths are
never followed.

Each decodable Gym record includes its raw text, and the report links the explicitly
selected original input. Read the report's attention notices alongside the
transcript. A report containing malformed or unsupported records can still be
created successfully: the content-free CLI summary reports `status: attention`
and exits 0. This exit status means the report was written, not that its evidence
is complete or valid. Usage, resource-limit, and output errors exit 2.

The reader accepts at most 32 MiB per input file, 8 MiB of selected records,
2 MiB per record, and 200 selected lines, with a 32 MiB limit on rendered record
content. Records beyond 64 nesting levels or 20,000 values receive a raw-only
view and an attention notice. Invalid UTF-8 receives a notice and a record
digest; use the original input for those bytes. If a size limit is exceeded, use a smaller
explicit selection or prepare a bounded source artifact while retaining its
provenance. Do not truncate evidence silently.

## Keep the report with the private evidence

The new directory has mode `0700` and `report.html` mode `0600`. The HTML contains
the selected evidence, including raw data and potentially sensitive grading
fields; it is as sensitive as its source. Keep it in the existing private,
gitignored workspace. Do not commit, publish, or share it outside the source's
existing publication boundary. Permissions are not redaction.

Link the generated `report.html`, the selected source path and row numbers, its
evidence role, and the exact regeneration command from the suite README. Note
any attention notices, missing records, and rendering limits in the handoff.
Retain the original provider artifacts and native Harbor review commands where
applicable; the HTML is a readable snapshot of the selected evidence.
