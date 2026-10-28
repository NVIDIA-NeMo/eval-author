<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Work on existing evaluations

Follow [the setup guide](getting-started.md) to load Eval Author in your coding
agent. Choose the request below that matches your goal. If you are unsure where
the evaluations live, start with the setup guide's inventory request.
Review the [runtime requirements](getting-started.md#requirements) for the
operation you choose.

## Find local tasks without running them

The shared [task search](../skills/eval-author/references/task-search.md) finds
Harbor task directories and NeMo Gym workload candidates from provider layouts
and Eval Author's existing extension markers.
It uses Python's standard library and reports stable paths, marker files, and
search limits without importing providers or executing repository code. Matches
are inventory candidates, including incomplete scaffolds and legacy configs;
use readiness checks only when you want to validate or execute them.

## Check readiness

```text
Check whether the NeMo Gym or Harbor evaluations in this repository are ready to run.
Explain any blockers and show the command for each suite you check.
```

Eval Author uses provider-native checks: Harbor validates its configs and tasks,
and NeMo Gym validates native manifests and runs verifier fixtures. Discovery also
runs a bounded sample of Harbor tasks with Oracle. It saves findings and evidence
in `.eval-author/discovery.md`.

Verified checks require the corresponding runtime and execution dependencies.
The report explains what passed, failed, or remains unverified for each suite.
These checks do not measure the actual agent's performance or evaluation quality.

Discovery can also describe other evaluation formats and their documented run
commands. Eval Author does not convert those suites; use the
[conversion handoff](#convert-a-suite-for-expansion) when expansion requires a
supported format.

## Convert a suite for expansion

If expansion pauses because your suite uses an unsupported format, Eval Author
provides the [canonical conversion prompt](../skills/eval-author/scripts/conversation_flows/onboarding_templates.py).
Paste it into a separate coding agent chat in the same repository. Fill in the
suite path, current framework, and target format: Harbor or NeMo Gym.

Return to Eval Author with the converted suite paths and validation results.
It checks the actual format and any remaining prerequisites before resuming
expansion.

## Audit coverage

```text
Audit these evaluations against my agent's intended behavior using the
available traces. Explain what is covered, what is missing, and what the
evidence cannot establish.
```

A full audit starts with the [shared setup and Ethos confirmation](getting-started.md).
If you have already completed those steps while creating or improving evaluations,
ask to audit the suite directly, including in a new chat in the same repository.
Separate audit requests get a welcome and progress checklist, followed by the
first unresolved decision without another readiness question. A direct handoff
from creation or improvement in the current conversation continues without
repeating the welcome, as do replies within an active audit.
Eval Author reuses your saved confirmations, checks only missing or changed
audit requirements, and continues at the first unfinished audit step. It reviews
Ethos again when the agent, intended behavior, or document has changed.
Previously selected suites and runs do not need another selection; when those
inputs are settled, the next review is the proposed audit checks. Traces remain
required: missing traces pause the audit while preserving your earlier work.
If saved selections refer to older work, Eval Author explains their source and
how they differ from current recorded tasks or runs, then asks which set you
intend to audit. Missing-path findings identify the particular selection that
was checked, keeping other current work distinct.

If saved trace availability is unresolved or traces are absent, the prerequisite
response explains the required action and links the audit skill instruction
that requires the pause. Unknown availability and confirmed absence are shown
separately; neither is a claim about measured coverage.

Milestone and source-selection questions hand the conversation back
to you. Work stops while the answer is pending, including background work and
delegated reviews. Independent work can finish before a checkpoint or continue
after you explicitly defer the missing input and agree to that work; asking a
question while continuing it in the background is not a check-in.

After the Ethos check-in, Eval Author shows the existing evaluations it found,
links their suite and case locations, and explains what they test. It asks you
to confirm which suites belong in the audit, including any additions or
exclusions, and waits before moving to traces. Suites you already explicitly
selected are reused without another confirmation. Discovery alone does not
select a suite, and giving a trace location does not confirm the suite list.

Once the evaluation scope is confirmed, it explains that traces record the
agent's actual actions during a run, letting the audit check what was exercised
beyond the test definitions and pass/fail scores. It then asks you to select
the trace source and location, before searching or reading traces.
This also applies to using traces to identify runtime tool names. A source you
already explicitly selected is reused. If the location is unknown, it helps you
choose a bounded place to look before searching.

At **Define what the evals should cover**, Eval Author explains the two review
artifacts: the **Audit specification** (`.eval-author/audit.md`) defines intended
checks and evidence requirements; the **Audit coverage report**
(`.eval-author/audit-coverage-report.md`) explains that scope, test mappings,
findings, limits, and next actions. The specification is a draft pending your
review; the report is preliminary at this point. These names stay the same as
their review state changes.
The review reply opens with what was created, updated, or validated and a brief
explanation of each document's purpose beside its link, before listing checks
or findings. This explanation remains in the reply even if the documents were
introduced earlier.
For this review, read **Intended coverage** in the Audit coverage report and
decide whether its checks and required evidence are right. The Audit specification
is the structured source behind that section; a separate YAML review is not
required. Later, review the findings, evidence limits, and next actions in the
same Audit coverage report.

The report's **Intended coverage** section lists every audit item's stable name
and kind, a plain-language explanation of what should be checked, and the
evidence required to establish coverage. Existing tests, observed evidence, and
measurement status stay distinct from those intended checks. At the scope
check-in, the reply previews the proposed checks grouped by tools, capabilities,
and failure cases and links directly to the full **Intended coverage** section.
It asks which intended checks are missing or incorrect and names the next
milestone. Agreeing to this scope does not approve the final report's findings.

**Shared progress** (`.eval-author/.working/journey-state.json`) saves suite and
trace selections separately, review decisions, measurement status, pending
questions, and the next action. Returning to an audit, including after another
workflow, resumes its pending decision or first unfinished step. Requests only
to validate a specification or measure or aggregate coverage stay within that scope.

Changes to the agent, Ethos, selected scope, specification, cases, verifiers, or
traces reopen check review and dependent measurement and findings. Changing only
the findings report requires findings review again. Saved progress therefore
reflects the current inputs when you resume.

Coverage measurement compares that specification with recorded ATIF traces.
ATIF is a structured record of an agent's interaction. Measurements are saved
under `.eval-author/audit-measurements/`. **Coverage measurements (JSON)**
(`.eval-author/audit-coverage-report.json`) is the file of generated results
aggregated over the selected runs, when available.

Eval Author writes the Audit coverage report using the shared template and links
the specification and available evidence. This readable Markdown artifact is
separate from Coverage measurements (JSON).
Test mappings describe what evaluations are designed to address and do not,
by themselves, establish measured coverage.

The Audit coverage report can be prepared before measurement and updated as
evidence becomes available. Without usable traces, you can establish the audit
specification and report, but coverage is **unmeasured**, not 0%. The unchecked
measurement step includes the reason, such as “deferred — selected trace
directory is missing,” including in later
check-ins and the final review. If you have no evaluations yet, Eval Author can define
the specification and then offer the [first-eval workflow](first-evals.md).
An uncovered item can reflect a missing scenario, an agent failure, or
insufficient evidence. Coverage measurements (JSON) alone does not identify
which explanation applies or establish overall agent quality.

The final checklist step includes generating the Audit coverage report and
reviewing it with you. Saving it does not complete that step: the review remains
pending until you respond to the review check-in. Whenever a session creates or updates
the report, its final response says so and links it, including at intermediate
checkpoints. On resume, Eval Author reconciles the report with changed inputs
before presenting its findings as current.

The review ends with a concrete offer to help with the next step. For an existing
suite with supported gaps, Eval Author names the main gaps and offers to propose
new or improved evaluation tasks, ranked with their expected behavior, verifier,
and supporting evidence. It explicitly asks whether you want those proposals,
ends the turn, and waits. Accepting the findings can complete a fully measured
audit while you leave the next action undecided; it does not start proposal
work. A deferred or partial measurement remains unfinished after findings review.
Accepting the findings and that offer moves directly
into the proposal workflow, reusing the report, specification, selected sources,
and prior answers.

The offer follows the evidence. With no evaluations, it points to the
[first-eval workflow](first-evals.md). With insufficient evidence, it recommends
the needed measurement first; any candidate task proposals are explicitly
unmeasured. If the findings do not support more tasks, it explains the useful
alternative, such as addressing an observed agent failure or gathering specific
evidence.

See [Read your results](results.md) for the saved artifacts and their limits.

## Propose new evaluations

```text
Use this Audit coverage report to propose the next evaluations to add or improve.
Rank the recommendations and explain the evidence behind each one.
```

Eval Author considers the Audit coverage report, underlying measurements,
traces, and existing cases. Recommendations may include a new scenario, a
stronger check, retaining a failing case as a regression test, or gathering more
evidence. Each recommendation explains its priority, expected behavior, how a
verifier would check it, and supporting evidence. They are saved in
`.eval-author/proposals/dataset-recommendations.md`. You can request this directly
or accept Eval Author's offer during the audit review; the handoff reuses the
audit inputs and settled answers.

The report's next steps provide context for this separate proposal
workflow; they do not replace its recommendations artifact.
Accepting the proposal offer produces recommendations. Creating, validating,
or running tasks remains separately scoped: ask for the selected proposal to be
implemented and validated when you are ready. The current automatic creation
workflow supports one eligible, measured tool-coverage gap at a time; other
recommendations may need further task design or measurement.

For the complete workflows, see the [discovery](../skills/eval-author-discover/SKILL.md),
[audit](../skills/eval-author-audit/SKILL.md), and
[task-creation](../skills/eval-author-task-create/SKILL.md) skills.
