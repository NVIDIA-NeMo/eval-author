<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

### Next Steps

- [x] **Check how your agent runs and how you’ll test it**
- [x] **Agree on how your agent behaves using [ETHOS.md](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/README.md)**
- [ ] **Improve, run, and review your existing evaluations** — **We're here**

### Task plan

**Improvement:** Add a partial-refund case

| Task | Scenario | Expected outcome | Verification |
|---|---|---|---|
| Partial refund | A customer requests part of an eligible order's price | Refund the requested amount and retain the remaining balance | Check the saved refund amount and order balance |

**Limits:** At most two construction attempts; running the agent requires separate approval

**Deferred work:**

- Repairing an unrelated existing grader

This task plan is agreed.

The next steps are to reuse or prepare the test setup, create the agreed tasks with their test data, instructions, and graders, and check that correct outcomes pass and relevant incorrect outcomes fail. I’ll repair problems in these new drafts and bring the tasks back for your review. After you approve them, we’ll confirm the agent, model, cases, attempts, and limits and get your permission before running your agent.
