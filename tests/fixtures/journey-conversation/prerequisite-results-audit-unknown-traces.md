<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Here’s what the prerequisite checks found, including anything that still needs attention.

| Prerequisite | Status | Findings |
|---|---|---|
| Agent and repository | Verified | Applicable check passed. |
| Evaluation framework | Verified | Applicable check passed. |
| Traces from completed evaluation runs | Unknown | Availability of saved traces from completed evaluation runs has not been confirmed. |
| Required skills | Verified | Applicable check passed. |
| File and command access | Verified | Applicable check passed. |
| Audit tooling | Verified | Applicable check passed. |

### Blockers

These items need to be resolved before we can continue.

- **Saved evaluation traces:** A full audit needs saved traces from completed evaluation runs. Evaluation definitions and pass/fail scores alone are insufficient. **Needed:** Do you have saved traces from evaluations that have already run for this agent?

The [Eval Author audit skill](</skills/eval-author-audit/SKILL.md>) requires “Confirm that traces from completed evaluations exist before continuing to Ethos”. This prerequisite must be resolved before we continue.
