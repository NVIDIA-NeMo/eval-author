<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NeMo Gym audit inputs

Audit measurement loads NeMo Gym records directly through NeMo Compass. Save the one
selected NeMo Gym record as a `.json` file and pass it with `--trace`,
`--source-kind gym`, `--task-id`, and `--run-id`, following
[Step 4](../SKILL.md#step-4-measure-one-trace). Retained ATIF from the run can be
measured instead with `--source-kind atif`. Eval Author never converts NeMo Gym
records to ATIF.

Audit measurement needs Python 3.12–3.13 and the audit skill's `requirements.txt`,
with the sibling eval-author skill installed. Measurement requires no Harbor,
NeMo Gym runtime, provider locator, or `--provider gym` probe. Running a NeMo Gym
evaluation to produce new evidence is a separate action requiring user
authorization and NeMo Gym runtime readiness.
