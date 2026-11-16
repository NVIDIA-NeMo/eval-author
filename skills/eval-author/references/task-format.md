<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Task format: Harbor or NeMo Gym

Eval Author runs and checks evaluations only as Harbor tasks or NeMo Gym
environments. When a workflow finds evaluations in another format, tell the user
that the format is required, give the reasons below that apply, and offer to
convert. Convert nothing without the user's agreement, and don't describe what
the other code tests until you have read it.

## Why the format matters

Each reason names the runtime that provides it. Don't claim a Harbor guarantee for
NeMo Gym or for code in another format.

- **Hidden material stays away from the agent (Harbor).** The agent's container
  is built from `environment/` and receives `instruction.md`. Harbor copies
  `tests/` in only after the agent finishes, and `solution/` only for the
  reference run, so the agent can't read hidden tests or the reference solution
  while it works.
- **Expected answers stay out of the prompt (NeMo Gym).** The model receives
  each row's `responses_create_params`. The row's other fields, where expected
  answers belong, go to the resources server's verifier.
- **Nothing carries over between runs (Harbor).** Each trial starts in a fresh
  container, so files left by one run can't make a later run pass. State outside
  the container, such as extra mounts or external services, can still carry
  over. NeMo Gym resources servers can keep state between rollouts.
- **Results stay tied to one task version (Harbor).** Each result records the
  task's checksum, so Eval Author can tell a result from an older version of the
  task apart from a current one.
- **Public and private files are labeled by the layout.** In a Harbor task,
  `instruction.md` and `environment/` are public, and `tests/` and `solution/` are
  private. Leakage checks read these labels instead of asking the author to list
  files by hand.

## What the format doesn't prevent

The layout can't tell whether a public file contains the answer. An author can
copy an expected output into `environment/`, and Harbor treats it as an ordinary
input. The opt-in `eval-author-rubric` skill's leakage checks look for that.
