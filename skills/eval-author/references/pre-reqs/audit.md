<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Audit prerequisites

Use for a full audit of existing evaluation coverage. An explicitly requested
specification-only operation retains its scoped entry. Follow the
[shared procedure](../shared-prerequisites.md#check-the-agent-and-setup) for when
to show this table, how to inspect prerequisites, and how to present results.

## Prerequisite table

| Prerequisite | Status | Findings |
|---|---|---|
| Agent and repository | Checking | — |
| Evaluation framework | Checking | — |
| Traces from completed evaluation runs | Unknown | This audit requires saved traces from evaluations that have already run. Their availability must be confirmed before we continue |
| Required skills | Checking | — |
| File and command access | Checking | — |
| Audit tooling | Checking | — |

## Checks and blocking conditions

Use the [common checks](../shared-prerequisites.md#common-checks) for agent and
repository identity, required skills, and file and command access. Identify the
existing evaluation format from supplied context or bounded suite documentation.
Required skills are the shared, Ethos, and audit skills; do not recommend
authoring-only assistant skills for this step.

During the audit journey, record the actual framework and its source as nonempty
strings in `scopes.audit.framework` and `scopes.audit.framework_source` using
`--state-only update`, including while other prerequisites are blocked. This
merges those observations without selecting cases or a corpus or approving scope.
An audit does not require a NeMo Gym or Harbor authoring setup: leave `--setup` unset for other
frameworks, and do not apply the Harbor default. A retained authoring choice or
Harbor parser dependency does not identify the audited suite's framework.
This does not select a suite or trace source, or extend the supported evidence
formats; selected evidence still needs ATIF or an existing supported conversion.

### Traces from completed evaluation runs

Keep this prerequisite visible in both the initial and results tables. Reuse any
supplied availability answer: use **Confirmed by you** when the user says those
traces exist, **Missing** when they say none exist, and **Unknown** otherwise.
Evaluation definitions and pass/fail scores alone do not satisfy this prerequisite.

Unknown or missing trace availability blocks full audit onboarding. Finish the
other accessible checks, then show **Blockers** and pause before Ethos. For unknown
availability, ask: “Do you have saved traces from evaluations that have already run
for this agent?” For confirmed absence, explain that the audit needs those traces
and ask the user to provide them or choose a different scope. Do not report that
the prerequisites passed, mark setup complete, or show Next Steps while this is
unresolved. Never mark traces **Not needed now** or run evaluations to create them.

Availability confirmation does not select a trace source or prove usable evidence.
Select suites and traces within the audit after Ethos confirmation, then verify
their usability, suite/run mapping, and preparation/measurement dependencies.

### Audit tooling

Check an existing Python environment against `audit_tools` in
[runtime-policy.json](../../scripts/runtime-policy.json) for audit specification
generation, validation, and report aggregation. Use bounded version and import
checks and retain the exact interpreter and results. Report them under
**Audit tooling**. This does not require an installed NeMo Gym or Harbor execution
runtime.

On resume, reuse `python` evidence only when it establishes this audit tooling.
An older authoring-runtime check may verify Python without the audit libraries;
mark that prerequisite `unknown` and check the audit requirements before deriving
the results checklist, even when the saved goal is already audit.

Skip evaluation runner, Docker, live agent service, and agent credential probes.
Access to selected evaluation definitions and traces is checked with those inputs
within the audit. Reassess old audit state the same way: replace obsolete execution
requirements with explained exemptions and remove their blocking effect on this
audit, retaining the earlier evidence.

### Later measurement requirements

ATIF measurement needs the Harbor trajectory parser, including for converted NeMo Gym
traces; use the existing locator with `--provider harbor --flow audit` then.
It does not need a NeMo Gym runner, Docker, or agent credentials. Local NeMo Gym and MLflow
export conversion uses the selected converter's requirements; access to a live
source is relevant only when the user explicitly selects it. A parser, converter,
or selected-source access failure discovered at this later stage blocks dependent
measurement; preserve completed Ethos and independently authorized specification
work. Preserve the user's evaluation framework when choosing a parser dependency.
Refer to the [audit skill](../../../eval-author-audit/SKILL.md) for those operations;
do not move trace selection or measurement into shared prerequisites.
