<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Trace sources and normalization

**Experimental:** This guide is part of the
[trace-to-environment workflow](../SKILL.md).

One task per trace. Do not merge unrelated traces. Preserve the source's trace
and span identifiers in `extra`, order steps by recorded time where available
(otherwise retain source item order), and record every missing or lossy field under `extra.normalization.uncertainties` or
`extra.normalization.losses`. Never invent an instruction, tool result, file,
or final answer.

## Existing ATIF

Use the trajectory as the canonical input. Do not relabel its ATIF version.

## Gym

Read and follow the `gym-to-atif` skill. Gym-native records use Responses-style
messages and tools, not ATIF. Its adapter converts one bounded record or an
explicitly selected JSONL line. Retain the raw Gym record and conversion
receipt privately under the task workspace; pass the derived ATIF to
`prepare --source-kind gym`.

For a rollout carrying `ng_trajectory`, ATIF conversion still uses the Responses
envelope. The sibling NeMo Compass loader retains attachment evidence separately;
its normalized output is not ATIF. Attachment-only exports do not currently
enter this workflow. See the [Gym output example](gym-output.md#start-from-a-rollout-with-ng_trajectory).

For Gym's Harbor bridge, prefer the explicitly supplied original ATIF instead
of a lossy reverse conversion. Its bytes/version remain unchanged; prepare it
with `--source-kind atif`. Never automatically follow the source paths recorded
inside Gym metadata. Image serialization, unknown fields, and incomplete
history must remain explicit—not silently treated as equivalent text evidence.

## MLflow

Read and follow the `mlflow-to-atif` skill. Put its owner-private output under
the task workspace and use the emitted `.atif.json` file as the canonical input.

## Intake

Resolve the CLI exactly as `eval-author-inspect-trace` specifies. Read one exact
trace and every detailed span into owner-private JSON files:

```bash
umask 077
nemo intake traces get --output-format=json --workspace="WORKSPACE" --mode=detailed "TRACE_ID" > <task-dir>/private/intake-trace.json
nemo intake spans list --output-format=json --workspace="WORKSPACE" --filter.trace-id="TRACE_ID" --mode=detailed --sort=started_at --all-pages > <task-dir>/private/intake-spans.json
```

Load both files through the sibling `eval-author` skill's shared NeMo Compass
loader, which rejects any span whose `trace_id` differs from the selected trace,
and export the trace as ATIF. `--output-dir` must not exist yet:

```bash
uv run --with-requirements <eval-author skill>/requirements.txt \
  <eval-author skill>/scripts/trace_loading.py --source-kind intake \
  --input <task-dir>/private/intake-trace.json \
  --spans <task-dir>/private/intake-spans.json \
  --workspace WORKSPACE --output-dir <task-dir>/private/intake \
  --emit-atif --agent-name <agent name>
```

Then pass the emitted file to
`prepare --atif <task-dir>/private/intake/atif/<file> --source-kind intake`.
The loader's errors are content-free. When it fails, check privately whether
the trace has a complete human instruction; if not, record no_candidate.

## OpenTelemetry

OTel traces must be ingested into Intake first; then follow the Intake path above.

## Canonical verification and bounded normalization

Before continuing, verify the canonical file has one ATIF v1.0-v1.7 object,
one-based sequential step IDs, at least one user step, and resolvable tool-call
references. The helper accepts at most 128 MiB of exact source bytes and
produces canonical and safe files of at most 25 MiB. It may make only two
bounded normalizations: insert a missing JSON escape when the parser-implicated
quote immediately follows a provider-redaction placeholder; and convert
string-encoded ATIF image objects into image parts while omitting encoded
binary data, including image metadata embedded in text fields. Every operation,
offset, count, and loss is recorded in the canonical ATIF and summary. Exact
source bytes remain unchanged and hashed. Reject unrelated JSON damage instead
of repairing it heuristically.
