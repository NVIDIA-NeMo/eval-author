<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NeMo Gym audit inputs

Audit measures ATIF traces from either Harbor or NeMo Gym using Harbor's
trajectory model. The trace's origin does not change that parsing dependency.

For a NeMo Gym run, prefer the original retained ATIF when available. Otherwise, use
[gym-to-atif](../../gym-to-atif/SKILL.md) to convert a supported, bounded NeMo Gym
Responses rollout record. Pass the ATIF to audit measurement with `--trace`,
`--task-id`, and `--run-id`, following [Step 4](../SKILL.md#step-4-measure-one-atif-trace).
Native `ng_trajectory` attachments are not direct ATIF inputs to this measurement
script; loading them with NeMo Compass does not make them ATIF.

Offline NeMo Gym-to-ATIF conversion needs Python 3.11+ and the standard library.
Audit measurement needs Python 3.12+ and the audit skill's `requirements.txt`.
Select that interpreter with the shared locator's `--provider harbor --flow audit`.
Neither conversion nor measurement requires a NeMo Gym runtime or a `--provider gym`
probe. Running a NeMo Gym evaluation to produce new evidence is a separate action
requiring user authorization and NeMo Gym runtime readiness.
