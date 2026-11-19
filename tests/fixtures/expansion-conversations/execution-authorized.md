<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

### Review the agent run plan

This task set is approved.

The task and verifier checks have passed. Running your agent will test its performance on the approved tasks.

Agent to test: **refund-agent**, using **configured-target**.

| Task | Examples | Attempts per example | Dataset rows |
|---|---|---|---|
| Partial refund | refund-0 | 2 | 1 |
| Duplicate refund request | refund-1 | 2 | 2 |

**Checks:** The verifier controls passed. I’ll check the agent’s refund amount and remaining balance in each attempt.

**Run limits:** Four attempts; no automatic retries; at most six steps per attempt; synthetic orders only

**Run configuration:** [.eval-author/.working/agent.yaml](</repo/.eval-author/.working/agent.yaml>)

**Open questions:** None

You’ve authorized this run with these settings. The next step is to run the selected tasks.
