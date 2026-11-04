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
Control runs do not establish agent coverage. Link relevant control results
under Findings and limitations as evidence about the verifier, separately
from the coverage measurements.

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

### Inspect verifier behavior and retained controls

For every selected task, inspect the reachable grading path and agent-visible
inputs using the shared [simple-cheat screen](../../eval-author/references/task-validation.md#screen-simple-cheats).
This applies to existing, revised, and trace-derived tasks in Harbor and NeMo Gym.
Follow entry points and guards through assertions and reward writers. Inspect the
build, mounts, expected data, and writable runner or report paths. Reading task
documentation alone does not complete this review. Include inspected task files
in `suite_files` before check approval so changed inputs reopen the review.

Assess each screening family against the actual task. Keep applicable planned
controls, expected rejection, and concrete non-applicability reasons in the
task README's Verifier section when authoring or revising it. An audit reads
these definitions without changing the task. An absent control or unsupported
runtime leaves screening unproven. Check valid alternatives and realistic wrong
answers too: rejecting every input is not protection. Distinguish exposed private
answers from reference-like text in inert comments. Neither establishes executed
behavior. Link the existing definitions instead of repeating them in the report.

Before findings review, reconcile the selected tasks' retained control evidence
and acceptance records, including failures and explicit acceptance overrides.
For locally authored tasks, inspect their `task-measurements/<task>/` records
alongside the selected task. Follow the manifests to native results only within
authorized sources. Reuse settled selections. If a source was excluded from
inspection, resolve that scope before reading it. Exclusion from agent coverage
alone does not exclude control evidence from verifier review. A missing agent
trajectory does not prevent reporting a verifier defect.

Check revision and evidence bindings, native health, and individual assertions.
Keep current failures, historical failures, ungraded execution, and missing
controls distinct. A successful later control does not erase another failure on
the same revision. Historical acceptance does not prove screening. Review
false rejection of valid alternatives separately from false credit for a cheat.
Apply the shared acceptance and repair rules without silently changing a task's
acceptance decision or executing new controls during an audit.

Run the bundled [evidence reconciliation check](../../eval-author/scripts/audit_verifier_evidence.py)
against the report before presenting findings. Pass each selected task and its
bounded evidence directory, plus the already selected sources containing linked
manifests and native results. For example:

```bash
python <eval-author-skill-dir>/scripts/audit_verifier_evidence.py \
  --repo . --report .eval-author/audit-coverage-report.md \
  --task .eval-author/tasks/example \
  --evidence-dir .eval-author/task-measurements/example \
  --source-root .eval-author/jobs/selected-controls
```

Repeat the path flags for additional selected tasks and sources. With no retained
evidence, omit `--evidence-dir` and explain that limitation beside an inline link
to the task directory in Findings and limitations. The checker reads existing
records and executes no task code. It checks the shared native task-evidence
format for both providers. Other evidence formats require manual review and an
explicit limitation, not a claim that screening passed.

Each failed current control needs an inline receipt link and an explanation in
Findings and limitations of what should have happened, what happened, and why it
matters. Stale or incomplete evidence may share one explanation linked to its
manifest. Preserve unresolved failed evidence after a repair until current
results establish what changed. Missing declared controls must remain visible.
The checker rejects missing links and bare link lists and flags evidence
mismatches for explanation. It cannot judge the prose or discover a code
defect: review those explanations and the grading path yourself. Do not present
findings for acceptance until both reviews are complete.

Save the check's JSON output under `.eval-author/.working/audit/` and retain it
and its returned `{path, digest}` evidence references in the existing
`measurement.evidence` list alongside coverage artifacts. They establish report
provenance, not agent coverage. Keep task inputs in `suite_files`. Recheck after
any task, control evidence, or report change before recording findings review.

### Explain findings and recommend actions

Explain material gaps with evidence: a missing scenario in inspected tests, an
observed agent failure, or insufficient measurement evidence. Where the cause
is unknown, say so. Missing judgments or an unverified failure trigger do not
prove that another task is needed. Include material fixture and verifier limits,
such as synthetic data or checks that exercise only part of the intended behavior.

When shortcut screening exposes a defect, record it in the relevant finding
with the task revision and native evidence link. Distinguish an agent failure,
a verifier accepting an invalid solution, a verifier rejecting a valid solution,
and execution with no usable grading evidence. Apply the existing
[screening rules](../../eval-author/references/task-validation.md#screen-simple-cheats)
when describing results. Keep planned, failed, incomplete, and passed checks
distinct. Missing or stale evidence stays unproven. A cheat receiving forbidden
credit blocks acceptance even if aggregate reward is zero. Summarize the flaw
without copying private answers or raw attack payloads.

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
Use the [guided handoff](guided-audit.md#after-review-offer-the-next-step) for
the combined findings review and authorized handoff. An audit can finish
without a justified change.

After the user selects proposal work, `eval-author-task-create` expands the
existing **Next actions and open questions** section. Its
[proposal guidance](../../eval-author-task-create/SKILL.md#step-1-propose-dataset-improvements)
defines the needed detail. Read the report first and add only missing
information. Link existing findings and Intended coverage rows instead of
repeating their evidence, scenarios, or checks. Keep a single ranked list of
actions with stable IDs, decisions, and links to resulting tasks and evidence.
Do not add a second overview, mapping table, or lifecycle section. Do not
copy recommendations into the suite README or HTML report.

Keep unresolved verifier risks with the action. Planned cheat controls and their
expected rejection belong only in the task README's
[Verifier section](../../eval-author/references/suite-readme.md#task-documentation).
Link it once the task exists. Record only new findings and relevant
outcomes in the audit report, with revision and run links. Selection, task
construction, validation, screening, agent execution, and measured coverage
are separate facts. Preserve the original audit commit, scope, and evidence
and do not change its coverage claims based on later control runs. Keep
changing status outside validated task folders.

When resuming `.eval-author/proposals/dataset-recommendations.md`, reconcile
its entries into these existing sections without duplicating content. Preserve
IDs, evidence links, decisions, agreed scope, and open questions. Rebase relative
links for the report's location. Leave the original file untouched as historical material, link it once, and maintain
only the audit report going forward. Reuse existing journey state for scope
and approvals. Do not infer execution permission from a selected action.
Carry historical control definitions into the task README when authoring it,
rather than copying them into the audit report.

## Review and updates

Follow [guided review and decisions](guided-audit.md#4-generate-and-review-coverage-report).
Announce and link actual report updates in the complete rendered response, using
Intended coverage for checks review and findings for later acceptance. Read-only
requests discuss available findings without writing or claiming an update;
focused operations do not require unrelated inspection or a full guided audit.
