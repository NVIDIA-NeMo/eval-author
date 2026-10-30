<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Here’s what the prerequisite checks found, including anything that still needs attention.

| Prerequisite | Status | Findings |
|---|---|---|
| Agent and repository | Verified | Applicable check passed. |
| Evaluation framework | Missing | The supplied suite uses an unsupported format. |
| Required skills | Verified | Applicable check passed. |
| File and command access | Verified | Applicable check passed. |
| Python and evaluation runtime | Verified | Applicable check passed. |
| Evaluation environment | Verified | Applicable check passed. |
| Required services and data | Verified | Applicable check passed. |
| Agent connection and credentials | Verified | Applicable check passed. |

### Blockers

These items need to be resolved before we can continue.

- **Improve existing evaluations:** The supplied suite uses an unsupported format. Eval Author supports NeMo Gym or Harbor for improving existing evaluations. **Needed:** Convert the existing evaluations to NeMo Gym or Harbor before we continue.

Start a separate chat with your coding agent in the same repository to convert the suite. Replace the three placeholders below with your suite path, current framework, and chosen target: NeMo Gym or Harbor.

```text
Convert the existing evaluation suite at <suite path> from <current framework>
to <NeMo Gym or Harbor>. Handle this as repository engineering work outside
the Eval Author workflow.

Inspect the existing cases, fixtures, tool interfaces, setup/reset logic, and
graders. Use the target framework's current official documentation and the
runtime version supported by Eval Author.

Write the converted suite to a new directory, preserving the original suite
and agent implementation. Carry over scenario identifiers, inputs, expected
behavior, scoring rules and thresholds, and failure cases. Reuse existing
fixtures and mock or in-memory boundaries where applicable.

Map each original case and check to its converted counterpart. Explain any
behavior that cannot be represented faithfully and ask about unresolved
semantic choices before changing what a test measures.

Validate the target format and run offline verifier checks with known passing
and failing fixtures where available. Keep live agent/model runs separate;
report their commands and requirements without claiming they passed.

Report the converted suite path, mapping, changed files, setup/run commands,
checks actually performed, and any remaining gaps or dependencies.
```

Return here with the converted suite path and validation results so Eval Author can recheck prerequisites.

The [Eval Author skill](</skills/eval-author/SKILL.md>) requires “Keep the blocker until the converted suite's actual format is checked”. This prerequisite must be resolved before we continue.
