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

## Temporary limit: prepare reads ATIF only

`prepare` and batch-manifest members accept `--source-kind atif` only. Eval
Author no longer converts traces into ATIF, so MLflow, Gym-native, Intake and
OpenTelemetry traces cannot enter `prepare` until trace-environment reads NeMo
Compass Traces directly. Until then, load those sources with the sibling
`eval-author` skill's
[NeMo Compass trace loader](../../eval-author/references/trace-loading.md) for
inspection or audit measurement.
Record a non-ATIF trace as `no_candidate` with this limit as the reason; do not
hand-write ATIF from it.

## Existing ATIF

Use the trajectory as the canonical input. Do not relabel its ATIF version.

## Gym

For Gym's Harbor bridge, use the explicitly supplied original Harbor ATIF. Its
bytes/version remain unchanged; prepare it with `--source-kind atif`. Never
automatically follow the source paths recorded inside Gym metadata. Image
serialization, unknown fields, and incomplete history must remain explicit—not
silently treated as equivalent text evidence. Gym-native Responses records and
`ng_trajectory` attachments are not ATIF and fall under the temporary limit
above.

## MLflow, Intake and OpenTelemetry

These sources fall under the temporary limit above. For Intake, resolve the CLI
exactly as `eval-author-inspect-trace` specifies; OTel traces must be ingested
into Intake first.

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
