<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

### Task scope

**Your requested improvement:** Add coverage for partial refunds

**Path:** Create a task candidate from a recorded interaction (experimental)

| Task | Selected | Scenario | Expected outcome | Grading and validation | Unresolved inputs |
|---|---|---|---|---|---|
| cover-refund | Yes | A customer requests a partial refund for an eligible order | Refund the approved amount and preserve the remaining order balance | **Grading:** Check the persisted refund amount and order balance<br>**Validation:** Positive, no-action, and wrong-amount controls | None |

**Selected tasks:** 1; **distinct input examples:** 1.

**Planned agent attempts:**

- **cover-refund:** Distinct input examples (1): **partial-refund**; attempts per selected dataset row: 2

Repeated attempts do not add input examples. Agent execution requires separate authorization.

**Selected sources:**

- traces/refund.json (source file)

**Construction limits:**

- Construction attempts per task: at most 4, including the initial attempt
- Time limit: 20 minutes
- Cost limit: $3

**Outside this scope:** Changes to existing cases or graders

Review the selected tasks, expected outcomes, grading, and construction limits before construction.

Should I create this task within these construction limits?
