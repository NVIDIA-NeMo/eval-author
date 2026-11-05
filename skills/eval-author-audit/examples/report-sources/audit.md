<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Audit

<!-- BEGIN:nemo-eval-author-audit:v1 -->
```yaml
schema: nemo.eval_author.audit.v1
agent: research-agent
status: draft
items:
- kind: tool
  name: read_file
  description: Read project sources.
  expected_use: Read evidence before quoting it.
  expected_failure_behavior: Disclose an unreadable source.
  evidence_required:
  - kind: tool_call
    tool: read_file
    description: The trace contains a source read.
- kind: capability
  name: cited_answers
  description: Answer with exact quotations.
  required_tools:
  - read_file
  expected_behavior: Ground each quotation in the selected source.
  evidence_required:
  - kind: output
    description: Each quotation matches the source.
- kind: failure_case
  name: missing_evidence
  description: Acknowledge missing evidence.
  applies_to:
  - cited_answers
  trigger: The sources do not answer the question.
  expected_behavior: State that the answer is unavailable.
  evidence_required:
  - kind: output
    description: The answer preserves the source limitation.
```
<!-- END:nemo-eval-author-audit:v1 -->
