---
name: mlflow-to-atif
description: >-
  Convert bounded live MLflow traces or exported Trace.to_dict() JSON into one
  canonical ATIF trajectory per trace for Harbor or Eval Author audit coverage,
  loading through NeMo Compass and its ATIF exporter.
  Use for MLflow-to-ATIF conversion, not Intake ingestion.
triggers:
  - convert MLflow traces to ATIF
  - prepare an MLflow trace for an ATIF consumer
  - measure Eval Author audit coverage from an MLflow run
not-for:
  - nemo-intake (use to import MLflow into Intake or query normalized spans)
  - eval-author-inspect-trace (use to explain one trace that is already in Intake)
  - eval-author-audit (use after this skill emits ATIF to measure audit coverage)
compatibility: >-
  Python 3.12–3.13 and nemo-compass 0.2.0 (requirements.txt,
  which includes MLflow for live queries), plus the sibling eval-author skill's
  shared trace loader. Optional reference validation requires Harbor. Output is
  ATIF v1.7 from the Compass exporter.
maturity: alpha
license: Apache-2.0
user-invocable: true
allowed-tools: Bash Read Write
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Convert MLflow to ATIF

## Purpose

Produce canonical ATIF without routing trace data through Intake. The bundled
script loads the export with the pinned NeMo Compass MLflow loader through the
sibling `eval-author` skill's `scripts/trace_loading.py`, exports each trace with
Compass's ATIF exporter, writes one owner-private `.atif.json` file per MLflow
trace, and prints only a content-free summary.

For normalized Compass `Trace` evidence rather than ATIF, use the sibling
`eval-author` skill's `scripts/trace_loading.py --source-kind mlflow` directly.

## Protect the trace

Treat the source and converted files as restricted data unless the user proves
otherwise. Do not print trace payloads, place them in Git, or write them into a
public or shared output directory. The script makes its output directory mode
`0700` and trajectory files mode `0600`.

## Choose the input

For exported data, accept one `Trace.to_dict()` value, a JSON array of those
values, or an object shaped as `{"traces": [...]}`:

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/convert_mlflow_to_atif.py \
  --input <private-export.json> \
  --output-dir <private-atif-dir> \
  --agent-name <stable-agent-name> \
  --agent-version <agent-version>
```

For a live MLflow store, require an explicit experiment and bounded time range.
Do not change MLflow authentication on the user's behalf:

```bash
uv run --with-requirements <skill_dir>/requirements.txt \
  <skill_dir>/scripts/convert_mlflow_to_atif.py \
  --tracking-uri <mlflow-uri> \
  --experiment-id <experiment-id> \
  --since <inclusive-ISO-8601-time> \
  --until <exclusive-ISO-8601-time> \
  --output-dir <private-atif-dir> \
  --agent-name <stable-agent-name> \
  --agent-version <agent-version>
```

Live conversion requires HTTPS for remote tracking servers, permits HTTP only
for loopback hosts, rejects disabled TLS verification, and does not follow HTTP
redirects. It uses the paginated `MlflowClient.search_traces()` API so it also
works with MLflow versions that predate the fluent API's `return_type="list"`.

The script refuses to overwrite an existing trajectory unless the user asks to
replace the same conversion and `--overwrite` is supplied.

## Version and validation

The Compass exporter emits ATIF v1.7. Add `--validate-with-harbor` when the
script runs in an environment that also has Harbor installed. Do not relabel
v1.7 output as a newer ATIF version.

## Conversion contract

- The Compass loader rejects exports with a non-empty `next_page_token`,
  unresolved parents, parent cycles, duplicate trace IDs, or traces without
  spans; collect the complete export before conversion.
- Recover the human instruction from the root span input. Fail with
  `trace has no recoverable user instruction` instead of inventing one.
- Emit LLM spans as agent steps with model and token metrics when recorded, and
  tool spans as agent steps with paired tool calls and observations. Retriever,
  embedding, reranker, and guardrail spans are not emitted as steps.
- Flatten the span tree depth-first. `extra.compass` records the exporter,
  the Compass trace ID and schema version, and every known lossy projection
  under `extra.compass.loss_codes`.

After conversion, pass one emitted file to the downstream consumer as canonical
ATIF. Keep restricted originals separate from restricted converted output.

## Prerequisites

Install the sibling `eval-author` skill alongside this one; the converter imports
its shared trace loader. Offline conversion needs a complete local
`Trace.to_dict()` export. Live conversion additionally needs configured access
to the selected store, an experiment ID, and explicit time bounds. Optional
Harbor validation needs Harbor in the same environment. Choose a private output
directory and a stable agent name and version.

## Available Scripts

Run with `uv run --with-requirements <skill_dir>/requirements.txt`, or with an
environment that already satisfies `requirements.txt`.

| Script | Purpose | Arguments |
|---|---|---|
| `scripts/convert_mlflow_to_atif.py` | Export one ATIF trajectory per selected MLflow trace | `--input` or bounded live-store flags; `--output-dir --agent-name --agent-version` |

## Limitations

The exporter projects text and JSON into ATIF v1.7 and records known losses;
it cannot recover unrecorded human input, tool output, or multimedia. Conversion
does not prove task correctness or coverage. Live reads are bounded to the
supplied experiment and time range and do not change authentication.

## Troubleshooting

- `trace has no recoverable user instruction`: obtain a complete export; never
  invent input text.
- Incomplete export or `next_page_token`: collect the remaining export pages before conversion.
- Unresolved parents or cycles: obtain a consistent trace export rather than
  deleting spans to make it parse.
- Pinned-dependency error: install exactly `nemo-compass==0.2.0`
  from `requirements.txt`, in a fresh environment if `trace-ingest` or
  `insight-agent` is installed.
- TLS or redirect rejection: use the authorized HTTPS endpoint with verification
  enabled; HTTP is supported only for loopback.
- Existing output: use a fresh directory unless replacement of this same
  conversion is explicitly requested; only then use `--overwrite`.
