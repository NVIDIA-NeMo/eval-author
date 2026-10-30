<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Audit conversations

Use the [shared renderer](../../scripts/conversation_renderer.py) after shared
onboarding. Fixed copy lives only in
[audit_templates.py](../../scripts/conversation_flows/audit_templates.py).
Follow [guided audit decisions and evidence handling](../../../eval-author-audit/references/guided-audit.md)
and [report authoring](../../../eval-author-audit/references/coverage-report.md);
this reference defines presentation inputs, not another workflow.

## Input and saved questions

Read `journey_state.py show` without `--state-only` after permitted inspection
and reconciliation. Pass its complete `state`, exact `revision`, and
`audit_observations` as `facts.audit_observations`, together with `stage`.
Audit stages require the saved audit journey and have no stateless preview.
The [shared contract](../conversation-rendering.md) owns purity, input authority,
escaping, and unchanged output; callers own freshness and semantic agreement.

Before suite, trace, checks, or findings review, call the pure
`decision_question(stage, lifecycle, facts)` helper. Save that question in
`lifecycle.pending` with its decision stage, current digest, and presentation
provenance through a revision-checked update:

| Presentation | Pending stage | Digest |
|---|---|---|
| `audit_suites` | `suite_selection` | `audit.checkpoint_digests.suite_selection` |
| `audit_traces` | `trace_selection` | `audit.checkpoint_digests.trace_selection` |
| `audit_checks` | `checks_review` | `audit.checks_digest` |
| `audit_findings` | `findings_review` | `audit.findings_digest` |

Current partial measurement initially derives `audit.stage: "measurement"`.
To review its findings, save `pending.stage: "findings_review"` with the current,
non-null `audit.findings_digest` and canonical findings question. This pending
checkpoint makes partial findings review applicable without completing measurement.
After acceptance, an explicit next-action question uses `pending.stage: "next_action"`
and that same current findings digest. Measurement has no decision checkpoint or
entry in `checkpoint_digests`. Never save `pending.stage: "measurement"`.

Refresh state and observations after saving and before rendering.
Missing, stale, or conflicting pending decisions are rejected.
Once shared onboarding is complete, use `audit_resume` for a saved lifecycle.
Onboarding `resume` rejects these downstream checkpoints.

## Stages and facts

Every stage accepts `audit_observations`, optional `links`, and applicable
`blockers`. Additional facts and gates are listed below; unknown fields fail.

| Stage | Additional facts and behavior |
|---|---|
| `audit_suites` | `suites`: `{path, label, description}` rows matching saved candidates exactly. Empty candidates ask for a location. Optional `report_action`. |
| `audit_traces` | Optional `report_action`. Requires resolved suite selection and unresolved trace selection. |
| `audit_checks` | `checks`: lists keyed by `tools`, `capabilities`, `failure_cases`, and `grading`, with `{check, evidence}` rows; use empty lists for absent areas. Requires `validation: "passed"`, `report_action`, `limits`, optional `changes`, a current checks digest and report. |
| `audit_measurement` | Optional `progress` and `report_action`. Requires current check approval and unresolved measurement; emits no decision question. |
| `audit_findings` | `conclusion`, a nonempty `findings` list, `limits`, `recommendations`, `report_action`. Requires current checks/findings and complete, current partial, or explicitly deferred measurement, with a saved findings-review checkpoint. |
| `audit_next_action` | Acknowledges accepted findings and any selected action without creating a question. Optional `recommendations` accompany only an applicable previously saved action question. |
| `audit_blockers` | Saved blockers and explanations; optional `report_action`. Uses top-level `pending_question` for the actual recovery choice. |
| `audit_resume` | Resolves to the blocker or current milestone using its facts; accepted findings resolve to acknowledgement. |

`report_action` is `created`, `updated`, or `unchanged`, matching this turn's
actual work. Save the report in `lifecycle.findings.report`; its digest must match
observations. Include action and link when writing during source selection,
measurement, or a blocked turn. A preliminary report does not establish approval.

`limits` and optional `changes` are plain-text lists. Explain deferral reasons in
`limits`. Saved decision provenance in `measurement.source` is not rendered.
Preserve material conversion
losses, missing methods, excluded runs, and grading limitations. Saved suite/trace
paths and exclusions supply scope; explain private selection-manifest details in
the report, using existing `evidence`, `limits`, `progress`, or blocker fields
for bounded excerpts. No preparation payload or manifest inspection is performed.

Each finding is `{kind, label, evidence}`; kinds are `strength`,
`missing_scenario`, `grading_weakness`, `agent_failure`, `insufficient_evidence`.
Lead `conclusion` with the established outcome. Each recommendation is
`{kind, label, reason}`; kinds are `improve`, `create`, `collect_evidence`,
`fix_agent`. Supply at most three prioritized choices, or none when unsupported.
Improvement labels are noun phrases without a leading article; template copy
supplies it. Detailed findings and evidence belong in the report.

## Links and blockers

`links` accepts `report`, `intended_coverage`, and `findings`, each with `target`
and optional caller-verified `fragment`. Every target must equal the saved
report's absolute path. Checks review requires `report` and `intended_coverage`.
Findings review requires `report` and accepts an optional `findings` link.
Acceptance accepts only `report`. Other stages accept `report` only when
announcing `report_action`. Stage-inapplicable links are rejected.
Section links require fragments. Supporting evidence stays inside the report.

Blockers follow the [shared contract](onboarding.md#blockers): one explanation
per saved blocker with `id`, `step`, `reason`, `needed`, and `skill_link` where
required. Shared prerequisites and Ethos take precedence. Blockers suppress
success checklists and preserve the recovery question over an older review question.
