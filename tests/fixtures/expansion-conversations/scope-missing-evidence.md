<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

### Next Steps

- [x] **Check how your agent runs and how you’ll test it**
- [x] **Agree on how your agent behaves using [ETHOS.md](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/README.md)**
- [ ] **Improve, run, and review your existing evaluations** — **We're here**

**Your requested improvement:** Add coverage for partial refunds

**Path:** Create a task for a selected audit tool gap

| Task | Selected | Scenario | Expected outcome | Grading and validation | Unresolved inputs |
|---|---|---|---|---|---|
| cover-refund | Yes | A customer requests a partial refund for an eligible order | Refund the approved amount and preserve the remaining order balance | **Grading:** Check the persisted refund amount and order balance<br>**Validation:** Positive, no-action, and wrong-amount controls | None |

**Selected tasks:** 1; **distinct input examples:** 1.

**Planned agent attempts:**

- **cover-refund:** Distinct input examples (1): **partial-refund**; attempts per selected dataset row: 2

Repeated attempts do not add input examples. Agent execution requires separate authorization.

**Selected sources:**

- .eval-author/.working/audit/coverage.json (source file)

**Construction limits:**

- Construction attempts per task: at most 4, including the initial attempt
- Time limit: 20 minutes
- Cost limit: $3

**Outside this scope:** Changes to existing cases or graders

**Selected improvements not included in construction:**

- **Repair the existing order grader:** Applying this grader repair inside Eval Author is not yet supported

**Before construction can continue:**

- Restore or review changed source: .eval-author/.working/audit/coverage.json
- Review changed audit inputs before relying on the selected findings for construction
- cover-refund does not match an eligible measured tool gap and its task identity

The saved scope needs reconciliation before construction can continue.
