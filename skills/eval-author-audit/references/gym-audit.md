<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Gym audit inputs

Audit measurement loads Gym records directly through NeMo Compass. Save the one
selected Gym record as a `.json` file and pass it with `--trace`,
`--source-kind gym`, `--task-id`, and `--run-id`, following
[Step 4](../SKILL.md#step-4-measure-one-trace). Retained ATIF from the run can be
measured instead with `--source-kind atif`.

Audit measurement needs Python 3.12+ and the audit skill's `requirements.txt`,
with the sibling eval-author skill installed. Measurement requires no Gym runtime,
provider locator, or `--provider gym` probe. Running a Gym evaluation to produce new evidence is a separate action
requiring user authorization and Gym runtime readiness.
