<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Task format: Harbor or NeMo Gym

Eval Author runs and checks evaluations only as Harbor tasks or NeMo Gym
environments. When a workflow finds evaluations in another format, warn the user
that Eval Author can't check them for data leakage, give the reasons below, and
offer to convert. Continue the workflow for the tasks that are in a supported
format. Convert nothing without the user's agreement, and don't describe what the
other code tests until you have read it.

## Why the format matters

Claim each reason only for the runtime named.

- **Hidden material stays away from the agent (Harbor).** The agent gets
  `instruction.md` and the container built from `environment/`, the task's public
  files. Harbor copies `tests/` in only after the agent finishes, and `solution/`
  only for the reference run. These private files are what leakage checks compare
  the public files against. The layout can't tell whether a public file contains
  an answer; the opt-in `eval-author-rubric` skill checks that.
- **Expected answers stay out of the prompt (NeMo Gym).** The model receives
  each row's `responses_create_params`. The row's other fields, where expected
  answers belong, go to the resources server's verifier.
- **Nothing carries over between runs (Harbor).** Each trial starts in a fresh
  container. State outside the container, such as extra mounts or external
  services, can still carry over, and NeMo Gym resources servers can keep state
  between rollouts.
- **Results stay tied to one task version (Harbor).** Each result records the
  task's checksum, so a result from an older version of the task can be told
  apart from a current one.
