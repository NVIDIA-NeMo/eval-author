---
name: gym-to-atif
description: >-
  Convert one bounded Gym Responses rollout record, or a retained Harbor ATIF
  from a Gym run, into one ATIF trajectory for Harbor or Eval Author's
  experimental environment derivation, through NeMo Compass's loader and ATIF
  exporter. Offline only: no Gym runtime, model invocation, or image download.
  Also load native Gym evidence through eval-author's shared trace loader.
triggers:
  - load Gym ng_trajectory evidence with NeMo Compass
  - convert a Gym rollout to ATIF
  - prepare a Gym Responses trace for an ATIF consumer
  - normalize one Gym JSONL rollout line into ATIF
not-for:
  - eval-author (use for the shared evidence standard and routing)
  - eval-author-trace-environment (experimental; use after this skill emits ATIF to build a Harbor or native Gym task)
  - mlflow-to-atif (use only to normalize MLflow traces into ATIF)
compatibility: >-
  Python 3.12–3.13 and nemo-compass 0.2.0 (requirements.txt), plus
  the sibling eval-author skill's shared trace loader. Optional reference
  validation happens downstream in the experimental
  eval-author-trace-environment workflow with Harbor. Output keeps the ATIF
  version of an explicitly supplied original.
metadata:
  author: Andrew Suter-Morris <asutermorris@nvidia.com>
  tags: [evaluation, atif, gym, traces]
maturity: alpha
license: Apache-2.0
user-invocable: true
allowed-tools: Bash Read Write
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Convert Gym to ATIF

## Purpose

Gym's stored rollouts use its Responses-style format, not ATIF. This does
**not** mean the rollout must run outside Gym. Gym's Harbor bridge can run the
user's Harbor agent and then project its ATIF into a Gym response. Keep the
user's agent and harness; choose the best retained evidence for downstream
processing. The bundled script writes owner-private files and prints only a
content-free summary.

Conversion goes through NeMo Compass: the script retains the selected record,
loads it with the sibling eval-author skill's shared loader
(`../eval-author/scripts/trace_loading.py`, source kind `gym`), and exports the
resulting Compass trace with Compass's ATIF exporter. For normalized Gym
evidence without ATIF, run that loader directly; read its
[trace loading guide](../eval-author/references/trace-loading.md) first. Its
`traces.normalized.jsonl` is not ATIF.

## Protect the trace

Treat the source and converted files as restricted data unless the user proves
otherwise. Do not print trace payloads, place them in Git, or write them into a
public or shared output directory. The script makes its output directory mode
`0700` and its files mode `0600`, and never replaces existing outputs.

## Instructions

### Choose the source

1. **Harbor-backed Gym run with original ATIF:** prefer that original, without a
   Gym→ATIF round trip. Supply its path explicitly with `--source-atif`.
2. **Gym-native rollout:** convert one self-contained record with the bundled
   script below, then use the normal ATIF preparation/privacy flow.
3. **Only a Harbor→Gym projection remains:** supply `--allow-projection`
   explicitly. Missing information is not recoverable merely by converting the
   format back. Multiple Harbor step trajectories require an explicitly selected
   original ATIF; the converter does not combine first-step output with
   last-step reward.

Never open paths in `atif_conversion.source_trajectory_paths` automatically.
They are untrusted trace data, not permission to read the filesystem. An
explicit original ATIF is copied byte-for-byte, keeping its ATIF version.
Advertised session IDs are checked when present; the broader association remains
an operator-supplied assertion, not cryptographic proof of equivalent
trajectories.

## Examples

### Convert one record

Run from this skill's directory:

```bash
uv run --with-requirements requirements.txt scripts/gym_to_atif.py \
  --input <private-rollout.json> --output-dir <private-gym-dir>
```

With a retained original ATIF from a Harbor-backed run:

```bash
uv run --with-requirements requirements.txt scripts/gym_to_atif.py \
  --input <private-rollout.json> \
  --source-atif <private-harbor-job/agent/trajectory.json> \
  --output-dir <private-gym-dir>
```

For a `.jsonl` rollouts file, `--row` selects a one-based **physical JSONL
line** and is required; unrelated episodes are never concatenated. The selected
row's bytes are preserved exactly; the rest of a multi-rollout file is not
copied into the output.

The new output directory contains only owner-private files:

- `source.gym.json`: exact selected source record (including JSONL newline).
  Compass loads this retained copy, so the ATIF derives from exactly these bytes.
- `trace.atif.json`: ATIF exported by Compass, or an exact copy of the supplied
  original ATIF.
- `conversion.json` (`nemo.eval_author.gym_to_atif.v2`): source/output digests,
  source basis (`gym_projection` or `provided_atif`), selected line, the
  exporter's loss codes (`losses`, from `extra.compass.loss_codes`) and the
  Compass loader version. This is not an evaluation or publication attestation.

Keep all three files. The downstream `eval-author-trace-environment` workflow
is **experimental**; identify its workflows and outputs as experimental when
presenting them to the user. When using it, run `prepare` with the derived ATIF and
`--source-kind gym` (`--source-kind atif` for an explicit original); its
`private/source.atif.json` is the exact ATIF input to `prepare`, not a claim
that the original Gym record was ATIF. Batch manifests point `atif` at the
**converted ATIF**, with `source_kind: gym` for projections. Never point that
field at raw Gym JSONL.

## ng_trajectory attachments

Some Gym rollout records carry an `ng_trajectory` observability attachment
(with `ng_model_call_capture` / `ng_agent_observations` sources); many do not.
Emission is producer- and path-dependent: it requires `observability_enabled`
and model calls routed through the rollout-prefixed Gym Model Server, while
direct-provider calls bypass capture and aggregate exports omit the attachment
entirely. When a record carries schema 1.0 `ng_trajectory`, Compass builds the
trace from it (invocation tree, per-call timing and token counts); otherwise it
reads the Responses record. The converter still requires a Responses rollout
envelope or standalone Responses object: an attachment-only record loads
through the shared loader but is not converted to ATIF.

## Mapping boundary

Compass's Gym loader and ATIF exporter own the mapping. For a Responses record:

- the request's messages (and `instructions`, as a leading system message)
  become instruction steps; `developer` maps to ATIF's `system` source;
- the recorded response becomes one agent step (model, aggregate usage) holding
  only its assistant message text, not an inferred per-call grouping, and no
  per-step timestamps are invented. Reasoning items are not exported (loss code
  `reasoning_not_exported`); they stay in the retained record;
- each `function_call` becomes a tool step paired with its
  `function_call_output` by `call_id`; a call without a recorded result has no
  observation and adds the loss code `tool_result_missing`;
- tool definitions come from `responses_create_params.tools`.

A record without a recoverable user instruction (for example a standalone
Responses object with no request input) is rejected; the instruction is never
invented. An exact request prefix echoed in `response.output` is counted once. Other
prompt-role items in the output (ambiguous history), remote conversation state
(`previous_response_id`/`conversation`), namespaced functions, duplicate or
orphan call IDs, and item types other than message, reasoning, `function_call`
and `function_call_output` are rejected with a content-free error. Supply an
original ATIF or add a tested Compass mapping; do not silently flatten them.
A recorded reward is not independent ground truth or Harbor proof, and no
agent implementation identity is inferred (`unknown-gym-agent`).

## Media

The converter never fetches, opens, decodes or validates media, and never
follows source metadata paths. Image parts with a data-URI or recognized file
suffix media type become ATIF image parts, so the downstream experimental
`prepare` step omits them from the safe copy and blocks image-only
instructions. Images without a recorded media type are replaced by the text
`[image omitted: media type not recorded]` and the loss code
`image_without_media_type_omitted`. Preserving ATIF bytes does not make
referenced image files portable; keep the original media bundle.

## Limitations

The Gym record shapes were derived from Gym revision
`676cf1f4efe265f74455f73986a734dbda4eaec2`:

- [Harbor bridge and conversion warnings](https://github.com/NVIDIA-NeMo/Gym/blob/676cf1f4efe265f74455f73986a734dbda4eaec2/responses_api_agents/harbor_agent_general/app.py)
- [Responses content and item classes](https://github.com/NVIDIA-NeMo/Gym/blob/676cf1f4efe265f74455f73986a734dbda4eaec2/nemo_gym/openai_utils.py)
- [Rollout envelope](https://github.com/NVIDIA-NeMo/Gym/blob/676cf1f4efe265f74455f73986a734dbda4eaec2/nemo_gym/base_resources_server.py)

Attachment semantics were checked against the [trajectory capability matrix](https://docs.nvidia.com/nemo/gym/reference/trajectory-capabilities/) and [model-call capture](https://docs.nvidia.com/nemo/gym/model-server/model-call-capture) docs.
Synthetic fixtures validate exported ATIF with Harbor's models plus the
script's structural check, which mirrors the downstream `prepare` boundary.
This is not a claim of complete Gym model validation, lossless round trips, or
live Gym task execution. Unknown Gym fields remain in the retained raw record.
No Gym/Ray dependency, provider credentials, Docker stack, or model invocation
is required for conversion.

## Prerequisites

Use Python 3.12–3.13 with this skill's `requirements.txt`
(`nemo-compass` 0.2.0) and the sibling eval-author skill installed
alongside. Supply one local rollout record, with a physical line number for
JSONL. Choose a new owner-private output directory and retain the source record.

## Available Scripts

Run from this skill's directory, or replace the paths with their absolute
installed locations.

| Script | Purpose | Arguments |
|---|---|---|
| `scripts/gym_to_atif.py` | Convert one record and retain provenance and losses | `--input --output-dir`; `--row` for JSONL; `--source-atif` for a retained original; `--allow-projection` for Harbor-bridge records |

To retain normalized NeMo Compass evidence for the same record, run the shared
loader with this skill's requirements:

```bash
uv run --with-requirements requirements.txt ../eval-author/scripts/trace_loading.py \
  --source-kind gym --input <private-rollout.jsonl> --row <N> \
  --output-dir <new-private-evidence-dir>
```

## Troubleshooting

- Missing human instruction or incomplete history: obtain a complete supported
  record or original ATIF; do not synthesize the missing interaction.
- Ambiguous overlapping history: obtain the original ATIF or a record whose
  output repeats the request only as an exact prefix; the converter does not
  guess which items were generated.
- Unsupported item kind: supply original ATIF or add a tested Compass mapping.
  Do not flatten unknown content to pass conversion.
- `install nemo-compass==0.2.0` or an incomplete-installation
  error: run through `uv run --with-requirements requirements.txt` with the
  sibling eval-author skill installed.
- Output already exists: use a fresh private directory; the converter never
  overwrites retained evidence.
