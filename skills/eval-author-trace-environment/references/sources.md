<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Trace sources and normalization

**Experimental:** This guide is part of the
[trace-to-environment workflow](../SKILL.md).

One task per trace. Do not merge unrelated traces. `prepare` loads the source
through the [trace loader](../../eval-author/references/trace-loading.md) into
one NeMo Compass Trace; Eval Author never hand-converts a trace. Never invent an
instruction, tool result, file, or final answer.
`<skill_dir>` is the installed `eval-author-trace-environment` directory and
`<eval_author_dir>` the installed `eval-author` directory.

```bash
python <skill_dir>/scripts/trace_environment.py prepare --task-dir <task-dir> \
  --source <file> --source-kind <atif|mlflow|gym|trace> [--row <n>]
```

The source must hold exactly one trace whose root agent records turns,
including a user turn with text. Otherwise `prepare` fails with
`source kind <kind> does not record turns yet` or names the missing user turn.

## ATIF

Pass an existing ATIF file with `--source-kind atif`, such as a Harbor trial's
`agent/trajectory.json`. Do not relabel its ATIF version.

## NeMo Gym

Pass a NeMo Gym rollout record with `--source-kind gym`; for JSONL, `--row`
selects the one-based JSONL line. Responses rollout envelopes record turns;
`ng_trajectory` rollouts do not and are refused. Never automatically follow the
source paths recorded inside NeMo Gym metadata.

## MLflow

Pass an exported MLflow trace with `--source-kind mlflow`.

## Intake and OpenTelemetry

`prepare --source-kind intake` is refused. Load the Intake selection first with
the [trace loader](../../eval-author/references/trace-loading.md#source-kinds),
narrowing with `--session-id`; records that share a session merge into one trace:

```bash
python <eval_author_dir>/scripts/trace_loading.py --source-kind intake ... \
  --output-dir <private-dir>
python <skill_dir>/scripts/trace_environment.py prepare --task-dir <task-dir> \
  --source <private-dir>/traces.normalized.jsonl --source-kind trace
```

The trace loader does not read OpenTelemetry directly; ingest OpenTelemetry
traces into Intake first.

## Workspace files and bounded normalization

`prepare` accepts at most 128 MiB of exact source bytes and writes:

| File | Content |
|---|---|
| `private/source.<kind>.json` or `.jsonl` | Exact source bytes, hashed |
| `private/canonical.trace.json` | The loaded Compass Trace as canonical JSON, at most 25 MiB |
| `safe/trace.json` | The scrubbed Trace, which still validates, at most 25 MiB |
| `safe/privacy.json`, `private/privacy-audit.json` | Scrub counts and findings as JSON paths into the safe Trace |

`summary.json` records the source kind, loader distribution/version/revision,
trace schema version, row, and path, SHA-256 and size of each file. Eval Author
makes only two bounded normalizations, each recorded as a `Trace.losses` entry:
`provider_redaction_quote_escaped` inserts a missing JSON escape when the
parser-implicated quote immediately follows a provider-redaction placeholder,
and `image_data_omitted` omits encoded image bytes. Reject unrelated JSON damage
instead of repairing it heuristically.

## Safe copy

The safe Trace replaces images with an `[image omitted]` text part and audio
with `[audio omitted]`, drops Trace and Span attributes, and keeps only
`is_error_status`/`is_error_flags` on tool results. Identifiers become
consistent `anon-<sha256[:16]>` pseudonyms, including identifier-like keys in
tool payloads and raw identifier values repeated in free text. Loader-positional
span and turn ids such as `trajectory/turn/2` or `gym/response/...` are kept;
other span and turn ids keep only their positional suffix (`anon-…/turn/2`).
Secret-named keys become `<redacted:secret>`; text is redacted for emails, SSNs,
phone numbers, IPv4 addresses, home paths, bearer tokens, private keys and
internal hosts. A Compass field the scrubber does not classify makes `prepare`
fail rather than pass through unscrubbed. A user turn holding only images blocks
the source with `image_only_user_instruction:<safe turn id>`.
