<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Load traces with NeMo Compass

`scripts/trace_loading.py` is Eval Author's only trace-loading boundary, and a
NeMo Compass `Trace` is its only trace interface. Provider parsing and
normalization into `Trace` objects belong to the pinned
[`nemo-compass`](https://github.com/NVIDIA-NeMo/nemo-compass) distribution;
sibling skills import this module instead of carrying their own loaders. ATIF
is an input format only: Eval Author never converts traces into ATIF. Loading
makes no model calls. Only `--source-kind intake` queries a live service.

## Environment and command

Use Python 3.12 or 3.13. The script never installs dependencies; `uv` resolves
this skill's `requirements.txt` into an ephemeral environment:

```bash
uv run --with-requirements <eval-author>/requirements.txt \
  <eval-author>/scripts/trace_loading.py --source-kind <kind> \
  --input <private-source> --output-dir <new-private-dir>
```

`requirements.txt` temporarily pins NeMo Compass to a Git revision, because
Compass trace contract v3 is not in a PyPI release yet:

```text
nemo-compass[mlflow] @ git+https://github.com/NVIDIA-NeMo/nemo-compass.git@a78dc416da247a9f4bcff17881c5fd20e90a7256
```

The loader accepts only that revision. It rejects any other install, including
another commit, a local or editable directory, and the PyPI `0.2.0` release,
which predates trace contract v3. It also rejects the older `trace-ingest` and
`insight-agent` distributions, because each can shadow the `trace_ingest`
package. Use a fresh environment when migrating. The pin returns to a PyPI
release with the next Compass release.

## Source kinds

| `--source-kind` | Input | Extra arguments |
|---|---|---|
| `atif` | `--input` ATIF file. A `.json` file holds one trajectory, such as Harbor's pretty-printed `agent/trajectory.json`; any other suffix is JSONL with one trajectory per line | — |
| `mlflow` | `--input` complete MLflow export: a trace list or `{"traces": [...]}` | Exports with a continuation token or unresolved parents are rejected. MLflow traces that share a session load as one `session:<id>` trace |
| `gym` | `--input` NeMo Gym rollout record: one `.json` record, or a JSONL file | `--row N` (one-based physical line) is required for JSONL; the trace's `source_url` cites that line of the retained copy |
| `intake` | No input file: a live selection from the Intake service through Compass's HTTP loader | `--intake-base-url URL --workspace WS --started-after ISO8601 --started-before ISO8601`, with explicit time zones, and optional `--session-id ID`. Authentication comes from the `NMP_ACCESS_TOKEN` environment variable. Records that share a session load as one trace |
| `trace` | `--input` canonical `Trace` JSONL, such as a previous `traces.normalized.jsonl` | — |

`--max-traces` (default 1000) rejects larger sources instead of truncating them.
A NeMo Gym `--row` normalizes only the selected line, but the output keeps an
exact copy of the whole source file, other episodes included, so treat it as
restricted as the source itself.

Direct OpenTelemetry input is not supported; ingest OpenTelemetry data into
Intake and load it with `--source-kind intake`.

## Outputs

The new output directory is created with mode `0700`; every file is written
once with mode `0600`, and an existing path is refused.

- `source.<kind>.json` (or `.jsonl`): exact bytes of the input. Intake has no
  input file and writes no source copy.
- `traces.normalized.jsonl`: one Compass `Trace` (trace contract v3) per line.
  This is **not ATIF**; reload it with `--source-kind trace`.
- `loading.json`: schema `nemo.eval_author.trace_load.v2` with `source_kind`;
  `loader` (`distribution` `nemo-compass`, `version` `0.2.0`, and `revision`
  `a78dc416da247a9f4bcff17881c5fd20e90a7256`); `trace_schema_version` `"3"`;
  `source_sha256` digests of each source copy; `normalized_sha256`;
  `trace_count`; `max_traces`; the NeMo Gym `row`; and `intake`, the Intake
  selection (each `null` for other kinds).

## Evidence handling and failures

Outputs contain restricted trace data; do not publish them or treat them as a
sanitized report. Success prints `{"status": "loaded", "traces": N}`. Any
failure exits 2 with a content-free message, removes only the newly created
output directory, and leaves existing paths untouched. Check the installed
pinned revision, source shape, and completeness privately. Missing values stay
missing: the loader never invents a user instruction, tool result, or reward.
