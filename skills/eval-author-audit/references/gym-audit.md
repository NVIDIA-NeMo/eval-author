<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NeMo Gym audit inputs

Audit measurement loads NeMo Gym records through NeMo Compass. For a rollouts
JSONL file, load the selected row with the eval-author
[trace loader](../../eval-author/references/trace-loading.md)'s
`--source-kind gym --row N`, keep its `loading.json` receipt with the selection,
and measure its `traces.normalized.jsonl` with `--source-kind trace`, `--task-id`,
and `--run-id`, following [Step 4](../SKILL.md#step-4-measure-one-trace). Do not
copy a row into a new file by hand. A NeMo Gym record that is already its own
`.json` file can be measured directly with `--source-kind gym`, and retained ATIF
from the run with `--source-kind atif`. Eval Author never converts NeMo Gym
records to ATIF.

Audit measurement needs Python 3.12–3.13 and the audit skill's `requirements.txt`,
with the sibling eval-author skill installed. Measurement requires no Harbor,
NeMo Gym runtime, provider locator, or `--provider gym` probe. Running a NeMo Gym
evaluation to produce new evidence is a separate action requiring user
authorization and NeMo Gym runtime readiness.
