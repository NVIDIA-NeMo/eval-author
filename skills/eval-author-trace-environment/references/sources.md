<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Trace sources and normalization

**Experimental:** This guide is part of the
[trace-to-environment workflow](../SKILL.md).

One task per trace. Do not merge unrelated traces. Never invent an
instruction, tool result, file, or final answer.

## Loading through NeMo Compass

`prepare --source <file> --source-kind <kind>` loads the source through the
sibling `eval-author` skill's
[NeMo Compass trace loader](../../eval-author/references/trace-loading.md) and
works on the resulting Compass `Trace` (schema version 3). Add `--row N` for a
Gym JSONL source and `--spans <file> --workspace <name>` for Intake; an ATIF
JSONL source must contain exactly one trajectory. The source must
hold exactly one trace. Batch-manifest members take the same `source`,
`source_kind`, `row`, `spans` and `workspace` fields.

Every loader kind is accepted when its trace records turns. Today that is ATIF;
Gym, MLflow and Intake follow once Compass records turns for them. Any other
source is rejected with `source kind <kind> does not record turns yet`. Until
then, inspect or audit those sources with the loader.
Record such a trace as `no_candidate` with this limit as the reason; do not
hand-write ATIF from it. Loader failures report
`could not load the source as <kind>: ...` without trace content.

## ATIF

ATIF is an input format Compass loads. Use the trajectory as-is and do not
relabel its version. Compass requires Harbor-valid ATIF (v1.0-v1.7, agent name
and version, a unique `trajectory_id` and at least one step for every embedded
subagent).

## Gym

For Gym's Harbor bridge, use the explicitly supplied original Harbor ATIF with
`--source-kind atif`; its bytes remain unchanged. Never automatically follow
the source paths recorded inside Gym metadata. Gym-native Responses records and
`ng_trajectory` attachments wait until Compass records turns for Gym.

## MLflow, Intake and OpenTelemetry

These wait until Compass records turns for them. For Intake, resolve the CLI
exactly as `eval-author-inspect-trace` specifies; OTel traces must be ingested
into Intake first.

## Canonical form and bounded normalization

The root agent must have at least one user turn with text. The helper accepts
at most 128 MiB of exact source bytes, keeps them verbatim as
`private/source.<kind>.json` (or `.jsonl`), and writes
`private/canonical.trace.json` and `safe/trace.json` of at most 25 MiB. Both are
Compass Traces serialized as sorted, indented UTF-8 JSON. It may make only two
bounded normalizations: insert a missing JSON escape when the parser-implicated
quote immediately follows a provider-redaction placeholder (`.json` sources);
and replace encoded image data, including image objects embedded in text, with
empty data. Each is counted in the summary's `normalizations` and recorded in
the canonical trace's `losses`, next to the losses Compass reports while
loading. Exact source bytes remain unchanged and hashed. Reject unrelated JSON
damage instead of repairing it heuristically. A repaired source is loaded from a
private copy, where subagent trajectory files named by a relative
`trajectory_path` cannot be read; if any such reference goes unresolved,
`prepare` refuses the source, so fix the escape in the source and re-run.

External subagent trajectory files that an ATIF source names by
`trajectory_path` are read once while loading. Their content becomes part of
the canonical and safe traces, which their digests bind, but the files
themselves are not copied, hashed, or size-checked beside
`private/source.<kind>.json`; keep them unchanged with the source if the
original evidence must be reproducible.
