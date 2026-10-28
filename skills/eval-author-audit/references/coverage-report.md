<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Writing the Audit coverage report

Write `.eval-author/audit-coverage-report.md` using the
[template](../templates/audit-coverage-report.md). This is the readable audit
output: explain what the audit intends to check, what existing tests address,
what selected runs demonstrate, what remains unknown, and what to do next. Adapt
the template to the available evidence and replace its authoring prompts before
presenting the report.

## Artifact names

The **Audit coverage report** is the guided audit's only user-facing document.
Its Intended coverage section holds proposed checks and evidence requirements;
its findings support the later review. Keep `audit.md`, measurement JSON, and
journey state internal; explicit file-operation requests retain their scope.
Use the [audit renderer](../../eval-author/references/conversations/audit.md) for
checkpoint copy and links. Claim only actual report updates and validation.

## Sources and scope

Use the current `audit.md`, inspected evaluation definitions and verifiers, and
applicable measurement artifacts. Link Ethos, selected eval locations, and
available supporting evidence inside the report. State the trace scope, methods run,
excluded or failed inputs, and whether the result is a specification only,
partial measurement, or measured audit. Distinguish confirmed absence of evals
from inaccessible or uninspected evals.

The specification defines the item names and evidence requirements. The JSON
report supplies measured counts and item coverage. Method-specific details and
judgments explain those results; keep generated JSON unchanged. Trace paths in
reports are references, not permission to open new sources: follow the audit's
source-selection rule before any further trace inspection.

## Coverage and test mappings

Summarize tools, capabilities, and failure cases separately. For each kind, show
the declared total and distinguish measured coverage, measured items not
demonstrated, and unmeasured items. Copy measured counts from applicable reports;
do not turn an unmeasured kind's zero covered count into a 0% coverage claim.
For a kind with no declared items, show that fact without a percentage.
Do not describe Harbor Oracle, NOP, or other control runs as audit evidence in
this Markdown report.

Use a section named **Intended coverage** as the complete, readable catalog of
the scope being reviewed. State whether the user has agreed to that scope;
successful schema validation alone does not make it approved. Map every current
audit item by stable `name` and kind to:

- **Intended check and required evidence:** explain in ordinary language what
  must be demonstrated and what evidence would establish it. Summarize the
  specification's tool expectations, capability behavior, or failure trigger
  and expected safe response, together with its evidence requirements. Keep
  these proposed criteria distinct from observed results. A stable name or test
  mapping alone does not tell the user what they are agreeing to.
- **Existing tests:** links to inspected cases, instructions, or verifiers that
  address the item. This describes intended testing, not observed coverage. Use
  “no match in inspected tests” or “not inspected” where appropriate, and preserve
  the scope of that statement.
- **Measurement status:** covered, measured but not demonstrated, or unmeasured.
  `not_measured_by_any_method` is unmeasured; `not_covered_by_any_input_report`
  means the selected measurements did not demonstrate the item.
- **Observed evidence and limits:** relevant task/run IDs, method, measurement
  files, judgments, or trace references. The aggregate's `input_reports` links
  covered names to task/run identities and coverage files. State the reason for
  missing evidence rather than inventing a supporting run.

Group rows by kind when helpful, or use per-item lists when a table would become
too wide. Keep the complete catalog in this section rather than maintaining
separate scope and mapping lists that can drift. Preserve material criteria when
summarizing; include the readable criteria here rather than requiring YAML or
JSON review. A manual mapping from task definitions can be useful without traces, but
does not establish measured coverage. A covered item in one run does not erase
observed failures in others or establish agent quality.

Include proposed grading checks and evidence requirements in Intended coverage.
Distinguish inspected grading logic from demonstrated grader behavior.

## Findings, limitations, and next actions

Explain material gaps with evidence: a missing scenario in inspected tests, an
observed agent failure, or insufficient measurement evidence. Where the cause
is unknown, say so. Missing judgments or an unverified failure trigger do not
prove that another task is needed. Include material fixture and verifier limits,
such as synthetic data or checks that exercise only part of the intended behavior.

Without usable traces, report measurement as unmeasured and explain the needed
input or conversion. With partial measurement, make clear that counts apply only
to the successful subset and list the exclusions. Do not fabricate a JSON report.
If inputs change, identify affected findings as stale until refreshed; old
measurements must not appear current. Retain still-applicable findings and user
corrections when updating the report.

Close with a recommended next action grounded in the findings. For an existing
suite with useful gaps to address, recommend proposing new or improved eval tasks
and name the behaviors those proposals should target. Explain when gathering
evidence, fixing the agent, or creating the first evals should come first.
Detailed ranked proposals belong to `eval-author-task-create`; the report
identifies the direction and evidence for that handoff without claiming tasks
were created or gaps closed. Use the [guided handoff](guided-audit.md#after-review-offer-the-next-step)
for the combined findings review and authorized handoff.

## Review and updates

Follow [guided review and decisions](guided-audit.md#4-generate-and-review-coverage-report).
Announce and link actual report updates in the complete rendered response, using
Intended coverage for checks review and findings for later acceptance. Read-only
requests discuss available findings without writing or claiming an update;
focused operations do not require unrelated inspection or a full guided audit.
