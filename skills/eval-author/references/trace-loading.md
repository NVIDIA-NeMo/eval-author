<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Load traces with NeMo Compass

`scripts/trace_loading.py` is Eval Author's only trace-loading boundary. Provider
parsing, normalization into NeMo Compass `Trace` objects, and ATIF export belong
to the pinned [`nemo-compass`](https://github.com/NVIDIA-NeMo/nemo-compass)
distribution; sibling skills import this module instead of carrying their own
loaders. Loading makes no model calls or live queries.

## Environment and command

Use Python 3.12 or 3.13. The script never installs dependencies; `uv` resolves
this skill's `requirements.txt` (`nemo-compass[mlflow]==0.2.0`)
into an ephemeral environment:

```bash
uv run --with-requirements <eval-author>/requirements.txt \
  <eval-author>/scripts/trace_loading.py --source-kind <kind> \
  --input <private-source> --output-dir <new-private-dir>
```

The loader accepts only the PyPI release `nemo-compass==0.2.0`. It rejects other
versions, Git or local-directory installs, and the older `trace-ingest` and
`insight-agent` distributions, because each of those can shadow the
`trace_ingest` package. Use a fresh environment when migrating.

## Source kinds

| `--source-kind` | Input | Extra arguments |
|---|---|---|
| `atif` | ATIF trajectory: a `.json` file holds one pretty-printed trajectory; other suffixes are JSONL | — |
| `mlflow` | Complete MLflow export: a trace list or `{"traces": [...]}` | Exports with a continuation token or unresolved parents are rejected |
| `gym` | Gym rollout record: one `.json` record, or a JSONL file | `--row N` (one-based physical line) is required for JSONL |
| `intake` | `nemo intake traces get --output-format=json --mode=detailed TRACE_ID` output | `--spans` (`nemo intake spans list --output-format=json --mode=detailed --filter.trace-id=TRACE_ID --sort=started_at --all-pages` output) and `--workspace` |
| `trace` | Canonical `Trace` JSONL, such as a previous `traces.normalized.jsonl` | — |

`--max-traces` (default 1000) rejects larger sources instead of truncating them.
A Gym `--row` selects exactly one line; unrelated episodes are never read into
the output.

## Outputs

The new output directory is created with mode `0700`; every file is written
once with mode `0600`, and an existing path is refused.

- `source.<kind>.json` (or `.jsonl`): exact bytes of the input. Intake writes
  `source.intake-trace.json` and `source.intake-spans.json`.
- `traces.normalized.jsonl`: one Compass `Trace` per line. This is **not ATIF**;
  reload it with `--source-kind trace`.
- `atif/<trace-id>.atif.json`: with `--emit-atif`, one ATIF trajectory per trace
  from the Compass exporter. Non-ATIF sources need `--agent-name`;
  `--agent-version` defaults to `unknown`. Exporter losses are listed under
  `extra.compass.loss_codes`.
- `loading.json`: schema `nemo.eval_author.trace_load.v1` with the source kind,
  loader distribution and version, `Trace` schema version, `sha256:` digests of
  each source copy and of the normalized file, trace count, `max_traces`, and
  each exported ATIF path and digest.

Only the files under `atif/` are trajectories for Harbor or the experimental
trace-environment `prepare` command.

## Evidence handling and failures

Outputs contain restricted trace data; do not publish them or treat them as a
sanitized report. Success prints `{"status": "loaded", "traces": N,
"atif_emitted": bool}`. Any failure exits 2 with a content-free message, removes
only the newly created output directory, and leaves existing paths untouched.
Check the installed pinned version, source shape, and completeness privately.
Missing values stay missing: the loader and exporter never invent a user
instruction, tool result, or reward.
