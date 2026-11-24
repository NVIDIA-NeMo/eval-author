---
name: smoke-agent
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Smoke Agent ETHOS

The smoke agent reads the `TOOL: <name>` marker in each task instruction, calls the named tool (`alpha`, `beta`, `gamma`, or `delta`), and writes `ready` to `/app/answer`.
