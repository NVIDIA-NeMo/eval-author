<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generate the Audit coverage report

## Artifact names

- Introduce `.eval-author/audit-coverage-report.md` as the audit's readable review artifact.
- Treat `.eval-author/.working/audit/report-content.json` as authored input and the Markdown as generated, read-only output.
- Keep the specification, measurement JSON, source bindings, and journey state internal unless explicitly requested.
- Use the [audit renderer](../../eval-author/references/conversations/audit.md) for conversation checkpoints and verified report links.

## Author and render

- Author each field by following the [section guide](report-sections.md). It shows what the renderer writes in every report section, which fields you write, the reference and writing rules, and examples.
- Select sources through the existing audit workflow before reading them. Bind only selected, inspected sources with their actual digests.
- Keep live journey state out of immutable source bindings. Bind its selected artifacts instead and retain decisions only in workflow state.
- Obtain a selected file's digest with `report_content.py --repo . --source-digest <path>`. Add `--task-tree` for the native task revision digest. This command writes nothing.
- Preserve stable IDs, user corrections, agreed scope, and decisions while updating authored input.
- Keep narrative separate from facts computed from the specification, native measurements, and receipts. Leave missing task-to-run mappings unresolved.
- Preserve the audit's recorded date and code revision. Render no history or trend claims from refresh time.
- Run the validator before rendering. Repair the named input fields when validation fails.
- After rendering, check that task identifiers parse as Markdown links. If one appears as plain text, correct its authored field to use the task identifier and rerender. The renderer rebuilds the link from the task's canonical path, so this is a repair step, not a reason to block the report.

```bash
python <audit-skill-dir>/scripts/audit_spec/report_content.py --repo . \
  --content .eval-author/.working/audit/report-content.json --lint-prose
python <audit-skill-dir>/scripts/audit_spec/render_report.py --repo . \
  --content .eval-author/.working/audit/report-content.json
```

State the selected approach in Summary and Scope. Task review uses inspected
tasks, fixtures, and verifiers, labels all run coverage unmeasured, and writes
`inputs.measurement: null` in the report content. It has no measurement file or
`measurement` lifecycle record. Do not describe it as a deferred or incomplete
measured audit, or reuse historical run findings as current evidence.

- Link the generated Markdown after a successful write. Report actual validation and rendering outcomes only.
- Preserve the existing report when inputs are invalid, stale, or unavailable.
- Never interpret or merge direct Markdown edits. When the renderer reports `generated_output_modified`, make the requested correction in the field it names and rerun. To discard the edits instead, rerun with `--discard-edits <sha>` using the digest in the error. The edited copy is kept at `.eval-author/.working/audit/edited-report-<sha>.md`.
- Leave hand-authored legacy reports readable until the user requests conversion. On conversion, author validated content from the reviewed facts and preserve stable IDs, decisions, and links. Pass the old Markdown's SHA-256 with `--convert-legacy <digest>` only for that explicit conversion. The renderer retains its original bytes outside the generated report.
- Leave HTML, all READMEs, and the suite report generator unchanged.

## Inspect verifier behavior and retained controls

- Inspect each selected task's reachable grading path and agent-visible inputs using the shared [simple-cheat screen](../../eval-author/references/task-validation.md#screen-simple-cheats).
- Follow entry points and guards through assertions and reward writers. Include inspected code, mounts, expected data, and writable report paths in `suite_files` before scope approval.
- Assess each cheat family, valid alternatives, and realistic wrong answers against the task. Link control definitions in the task README's Verifier section without editing it during an audit.
- Inspect retained task measurements and acceptance records, including failures and overrides. Follow selected manifests to native results only within authorized sources. Exclusion from agent coverage doesn't exclude control evidence from verifier review.
- Record each discovered issue separately, with its task revision, explanation, basis, and evidence. Keep false acceptance, false rejection, agent failure, missing trigger exposure, and unusable execution evidence distinct.
- Retain current and historical failures. A later passing control doesn't erase another failure on the same revision. Check individual assertions even when aggregate reward is zero.
- Keep missing, stale, unhealthy, and unsupported evidence unproven. A successful reconciliation doesn't qualify a task or establish agent coverage.
- Run the reconciliation checker against validated content before findings review. The checker reads existing evidence and executes no task code.

```bash
python <eval-author-skill-dir>/scripts/audit_verifier_evidence.py --repo . \
  --content .eval-author/.working/audit/report-content.json
```

- Save its JSON result beneath `.eval-author/.working/audit/` and retain its file evidence in `measurement.evidence`. For task review, which records no measurement, bind the saved result as a `file` entry in `findings.inputs` alongside the renderer's typed `bindings`. Use the renderer's typed `bindings` for findings inputs.
- Resolve missing explained findings and review grading logic before presenting findings for acceptance. The checker cannot assess the accuracy of the explanation.
- Use `--report <path>` with explicit task and evidence paths only for legacy Markdown reports. That compatibility path retains the old heading-based check.

## Review and bindings

- Follow [guided decisions](guided-audit.md#4-generate-and-review-coverage-report) for source selection, scope approval, findings review, and the next action.
- Review all declared checks through the generated coverage tables. Use the generated Markdown heading links for scope review, starting at `#capabilities-audited`.
- Record `findings.report` with the generated Markdown's path and SHA-256, and `findings.content` with the authored JSON's path and SHA-256.
- Copy the renderer's `bindings` into `findings.inputs`. Retain the existing provenance in `findings.source` and bind findings acceptance to the current derived digest.
- Revalidate after content, task, receipt, or other bound input changes. Changed content reopens findings review. Changed inspected task or specification inputs retain the existing scope and measurement invalidation rules.
- Record acceptance in workflow state without rewriting the report merely to mark it accepted.

## Findings, limitations, and next actions

- Explain missing scenarios, observed failures, fixture limits, and insufficient measurement separately. Missing judgments or an unencountered trigger don't prove that another task is needed.
- Recommend the next useful action from the evidence. Retain an existing failing task instead of proposing a duplicate when the scenario already exists.
- Expand the same authored recommendation records only after the user selects proposal work. Follow [task-create](../../eval-author-task-create/SKILL.md#step-1-propose-dataset-improvements) without restarting settled audit decisions.
- Keep planned cheat controls and expected rejection only in the task README's [Verifier section](../../eval-author/references/suite-readme.md#task-documentation) during task authoring. Link those definitions once they exist.
- Keep selection, construction, validation, control execution, agent execution, acceptance, and measured coverage as separate facts.
- Reconcile historical `dataset-recommendations.md` entries into the authored content during requested conversion. Preserve IDs, decisions, evidence, and unresolved risks. Leave the historical file intact and rebase relative links.
- Regenerate Markdown after authorized content changes. Never maintain another authored recommendation list or edit the generated report directly.

## Selected improvement handoff and follow-through

At findings review, use the report's stable recommendation IDs in conversation
recommendations (`id`, alongside `kind`, `label`, and `reason`). Keep IDs when
reordering or expanding recommendations; never reuse an ID for a different action.
A recommendation's priority is advice until the user agrees it.

When the user selects specific improvements, update the lifecycle's `next_action`
with `kind: improve`, its current `findings_digest`, the reply's `source`, and a
nonempty `recommendation_ids` list such as `["R1", "R3"]`. Use the normal
revision-checked journey-state update after saving findings acceptance. The same
update can record acceptance and selection when the reply settles both. Selection
is checked against the bound report content, including its action types.
`collect_evidence` and `fix_agent` may also record matching recommendation IDs;
`finish` records no IDs. Acceptance alone records no new next action.

The helper saves an `audit_handoff` in `scopes.expand` with the selected IDs,
originating report/content references, findings digest, and decision source.
Keep the audit scope intact: it retains source selections and exclusions,
measurement, and review context. Reuse that scope and the report for findings,
priorities, and limits rather than copying another recommendation list. An old
`improve` decision without IDs needs only the unresolved specific selection.
Never infer it or repeat onboarding to recover it.
Expansion-scope updates with a retained handoff also require the latest
`--expected-revision`; they preserve the handoff automatically. Change the
selection through a new reviewed audit next-action decision, not by replacing
the handoff directly.

On entering `expand`, retain this handoff and reuse the confirmed Ethos and
applicable prerequisites. Continue selected proposal work at task-create Step 1;
Journey 3 still agrees its construction scope and execution separately. Additions,
case revisions, and verifier repairs can all be selected. Automatic tool-gap
eligibility limits implementation, not selection. If no supported authoring path
can perform the chosen change, preserve the handoff and pending question, explain
the missing capability, and stop before construction. Do not restart the audit.
If selected IDs are absent from the evolved report or its source context differs,
retain the original decision and ask only how to reconcile that missing context.

Record follow-through on the same recommendation in authored content, using
`follow_through.status` (`selected`, `in_progress`, `blocked`, or `implemented`),
`summary` (what changed or remains), and `evidence` (bound source IDs, possibly
empty before implementation). `implemented` requires evidence of the change;
it does not claim validated grading, successful agent execution, or better
coverage. Record those facts only when their own evidence exists. Do not add
progress for unselected recommendations. Refresh the same report through the
validator and renderer; a progress edit does not undo the originating selection.

Leave freshness checks unchanged. If task files changed, earlier measurements
cannot establish current coverage. Explain that limitation and retain the
original findings and task revisions. Before refreshing, inspect and explicitly
rebind changed report inputs, retaining old findings as historical and any old
measurements as applicable only to their original revisions. Never silently
update evidence digests or claim a current audit from stale measurements. If a
valid refresh needs further review, retain the existing report and handoff and
explain the blocker. Repeating the audit or running new controls is a separate
request, not an automatic consequence of improving a task.
