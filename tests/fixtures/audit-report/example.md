<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

[eval-author-audit-report]: urn:eval-author:audit-report:%7B%22body_sha256%22%3A%20%22a2539570f1fe1a8a77803b55e44c51e4829969850afef5ebaa62afbd233b04a3%22%2C%20%22content_sha256%22%3A%20%224ac97454cee3e6dd1a9894962e205422f81e54038319d63826d48b814115181c%22%2C%20%22renderer_version%22%3A%201%2C%20%22sources_sha256%22%3A%20%22c3f754c65550da88c25a6f2457ee1139d23bc0181574efe31c2cdec19c9d535b%22%2C%20%22validator_version%22%3A%201%7D

# Audit coverage report: research-agent

- **Date:** 2026-11-04
- **Prepared by:** [Eval Author](https://github.com/NVIDIA-NeMo/eval-author)
- **Repository:** example/research-agent
- **Audited commit:** abcdef123456

## Summary

This audit includes 1 advisory, 3 findings, and 3 recommended tasks. 1 of 1 declared capabilities (100%) have at least one mapped task. The audit uncovered evidence of verifier cheating. The suite's only verifier prints success without checking the submitted quotation, so its passing results cannot be trusted.

Coverage was not measured for this audit because the audit measurement step was not run. The audit inspected one task and one illustrative run record, and no controls were retained. All findings come from inspecting the verifier code and the task files.

### Evaluation coverage

|  | Current audit |
| --- | --- |
| Capabilities | 1 |
| Capabilities covered | 1 |
| Coverage ratio | 100% |
| Coverage basis | Mapped tasks (runs not measured) |

No comparable previous audit

### ADVISORY

#### Advisory 1 - The verifier accepts an unchecked success response

[T1-source-answer](../report-sources/tasks/source-answer/instruction.md)'s verifier prints success without checking the submitted answer, so an empty submission can pass even when the task's quotation requirement is unmet. Code inspection confirmed verifier cheating, as documented in [verify.py](../report-sources/tasks/source-answer/verify.py).

The unchecked success path makes the task result unreliable until the verifier is repaired and exercised.

Proposed task: Revise the verifier to compare the submitted quotation with verifier-owned source data and reject empty answers.

## Key findings

### Finding 1 - Coverage gaps

0 of 1 capabilities, 0 of 1 tools and 1 of 1 failure cases have no mapped task. The [Coverage report](#coverage-report) lists every declared item with its tasks and run evidence.

- **The suite lacks a missing-evidence task.** [T1-source-answer](../report-sources/tasks/source-answer/instruction.md) asks the agent to return a quotation from the supplied source, but the inspected suite has no scenario for a question that the source cannot answer. The audit therefore cannot assess how the agent handles missing evidence.
  - Proposed task: Supply a source without an answer and require the agent to acknowledge that limitation.

### Finding 2 - Retained controls are unavailable

[T1-source-answer](../report-sources/tasks/source-answer/instruction.md) has no supported control receipts in the selected material, so the audit cannot establish whether its verifier rejects wrong answers or accepts valid alternatives. The available code inspection evidence in the task files leaves this finding unproven.

### Finding 3 - Quotation accuracy is unchecked

[T1-source-answer](../report-sources/tasks/source-answer/instruction.md)'s verifier does not compare the answer with the selected source, so a passing result does not establish whether the quotation is accurate. Code inspection confirmed a weak verifier, as documented in [verify.py](../report-sources/tasks/source-answer/verify.py).

Proposed task: Require the trace to show a read\_file call on the cited source before the answer, and reject quotations that do not appear in what was read.

## Recommended next actions

The recommendations draw on the inspected task definitions, the audit specification, and the selected Ethos.

- **High** · [Advisory 1 - The verifier accepts an unchecked success response](#advisory-1---the-verifier-accepts-an-unchecked-success-response) · cited\_answers (capability)
  - Repair the verifier. Revise the verifier to compare the submitted quotation with verifier-owned source data and reject empty answers.

- **Medium** · [Finding 1 - Coverage gaps](#finding-1---coverage-gaps) · missing\_evidence (failure case)
  - Add a task. Supply a source without an answer and require the agent to acknowledge that limitation.
  - **Verification:** Accept a reply that says the sources do not contain the answer. Reject a reply that supplies a quotation, a participant, or a count, because none can come from the supplied source.
  - **Breadth:** Provisionally three inputs: no answer in the source, a partial answer, and an answer that exists only in a different source. Exclude multi-turn variants until the first set is agreed.
  - **Next step:** Needs manual task design

- **Medium** · [Finding 3 - Quotation accuracy is unchecked](#finding-3---quotation-accuracy-is-unchecked) · read\_file (tool)
  - Revise a task ([T1-source-answer](../report-sources/tasks/source-answer/instruction.md)). Require the trace to show a read\_file call on the cited source before the answer, and reject quotations that do not appear in what was read.

- **Low** · [Finding 2 - Retained controls are unavailable](#finding-2---retained-controls-are-unavailable) · no mapped capability or tool
  - Collect more evidence.

## Task catalog

The catalog lists inspected runnable tasks, their intended behavior, and their audit mappings. Observed tools come from selected measurements. Findings link to each recorded issue.

| Task | Behavior | Capabilities and tools | Findings |
| --- | --- | --- | --- |
| [T1-source-answer](../report-sources/tasks/source-answer/instruction.md): Source answer | Answer with a quotation from the project source. | Capabilities: cited\_answers. Tools: Unmeasured. The selected record for [T1-source-answer](../report-sources/tasks/source-answer/instruction.md) does not establish a source read. | [Advisory 1 - The verifier accepts an unchecked success response](#advisory-1---the-verifier-accepts-an-unchecked-success-response) (confirmed); [Finding 2 - Retained controls are unavailable](#finding-2---retained-controls-are-unavailable) (unproven); [Finding 3 - Quotation accuracy is unchecked](#finding-3---quotation-accuracy-is-unchecked) (confirmed) |

## Coverage report

The tables retain every declared behavior, tool, and failure scenario in audit order. Product-area grouping is omitted because no structured product-area assignment is recorded.

The Tasks column lists the tasks mapped to each item, including boundary-only relationships. Run evidence shows whether selected runs demonstrated the item. For tools, demonstration establishes invocation only.

### Capabilities audited

| Functionality | Intended check and required evidence | Tasks | Run evidence |
| --- | --- | --- | --- |
| cited\_answers - Answer with exact quotations. | Expected behavior: Ground each quotation in the selected source. Required tools: read\_file. Required evidence: Each quotation matches the source. | [T1-source-answer](../report-sources/tasks/source-answer/instruction.md) | Unmeasured |

### Tools

| Functionality | Intended check and required evidence | Tasks | Run evidence |
| --- | --- | --- | --- |
| read\_file - Read project sources. | Expected use: Read evidence before quoting it. If the tool fails: Disclose an unreadable source. Required evidence: The trace contains a source read. Tool: read\_file. | [T1-source-answer](../report-sources/tasks/source-answer/instruction.md) | Unmeasured |

### Failure cases

| Functionality | Intended check and required evidence | Tasks | Run evidence |
| --- | --- | --- | --- |
| missing\_evidence - Acknowledge missing evidence. | Trigger: The sources do not answer the question. Expected behavior: State that the answer is unavailable. Required evidence: The answer preserves the source limitation. | No evaluation coverage | Unmeasured |

## Verifier cheat screening

The audit considers the following verifier cheat checks where they apply. Task-specific issues appear in the findings.

- **Early exit:** Empty output or an early exit must not earn credit before the required checks finish.
- **All-failure behavior:** Failing every required assertion must not produce a passing result.
- **Reference-like comments:** Comments and text that resembles a reference answer must not bypass the real checks.
- **Forged success:** Success messages and reward files written by the agent must not replace evidence produced by the verifier.
- **Valid alternative:** A different valid solution should pass without matching one reference implementation.
- **Realistic wrong answer:** A plausible answer that fails the task's requirements should be rejected.

## Scope and Execution Evidence

- **Audit specification:** [audit.md](../report-sources/audit.md). Specification status: draft. Workflow state owns review decisions.
- **Ethos:** [ETHOS.md](../report-sources/ETHOS.md).
- **Run Evidence JSON:** Unavailable. Coverage is unmeasured. To measure coverage, agree on the audit checks, select recorded traces from completed agent evaluation runs, and ask Eval Author to measure them against those checks and regenerate this report.
- **Evaluations inspected:** [T1-source-answer](../report-sources/tasks/source-answer/instruction.md).
- **Run Evidence:** 1 selected agent run in `../report-sources/`.

- **Verifier review:** The review inspected the synthetic success path. Every proposed control remains unexecuted.

- **Fixture limits:** The task, source records, and findings are illustrative synthetic data. They do not establish a live agent result. [ETHOS.md](../report-sources/ETHOS.md), [T1-source-answer](../report-sources/tasks/source-answer/instruction.md), [run.json](../report-sources/run.json)

## Definitions

- **Behavior:** An action or outcome expected from the product, such as saving supplied research or answering with exact citations.
- **Capability:** A named audit item describing an expected behavior. Stable identifiers connect task mappings to coverage tables.
- **Measurement:** A comparison of selected agent evaluation runs with the audit criteria. It records which items the available run evidence demonstrates.
- **Tool:** A function the agent can call. An observed call establishes invocation and does not establish the intended outcome.
- **Task:** A runnable evaluation with a scenario, starting conditions, and a verifier. A trial is one execution. Task paths are relative to the audited repository root.
- **Verifier:** A program or judging rule that determines whether a task's requirements were met.
- **Environment:** The files, data, mocks, and starting state supplied to a task.
- **Run Evidence:** An assessment of selected execution records. Demonstrated means at least one selected agent run satisfied an item's requirements. Not demonstrated means the selected measurements did not establish them. Unmeasured means no applicable method ran.
