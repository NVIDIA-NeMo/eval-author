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
Separate audit requests get a welcome and current audit checklist, followed by the
first unresolved decision without another readiness question. A direct handoff
from creation or improvement in the current conversation continues without
repeating the welcome, as do replies within an active audit.
Eval Author reuses your saved confirmations, checks only missing or changed
audit requirements, and continues at the first unfinished audit step. The checklist
can show unchecked audit requirements while keeping earlier setup answers. It reviews
Ethos again when the agent, intended behavior, or document has changed.
Previously selected suites and runs do not need another selection; when those
inputs are settled, the next review is the proposed audit checks. Traces remain
required: missing traces pause the audit while preserving your earlier work.
If saved selections refer to older work, Eval Author explains their source and
how they differ from current recorded tasks or runs, then asks which set you
intend to audit. Missing-path findings identify the particular selection that
was checked, keeping other current work distinct.

A repository with no saved progress starts normal onboarding. If saved progress
is damaged, unreadable, or belongs to another repository, Eval Author pauses
audit checks to protect earlier decisions. It explains the error and offers a
specific recovery step, such as restoring access, restoring a known valid copy,
or confirming an intentional repository move. After recovery, it reloads saved
progress and resumes the audit.

If saved trace availability is unresolved or traces are absent, the prerequisite
response explains the required action and links the audit skill instruction
that requires the pause. Unknown availability and confirmed absence are shown
separately; neither is a claim about measured coverage.

Milestone and source-selection questions hand the conversation back
to you. Work stops while the answer is pending, including background work and
delegated reviews. Independent work can finish before a checkpoint or continue
after you explicitly defer the missing input and agree to that work; asking a
question while continuing it in the background is not a check-in.

After Ethos is confirmed or reused, Eval Author shows the existing evaluations it found,
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

At **Define what the evals should cover**, review the proposed checks in the
[Audit coverage report](results.md#coverage-audits). Approval allows measurement
of the agreed traces without another readiness question. After measurement,
review the findings in that same report; there is no intermediate approval pause.

**Shared progress** (`.eval-author/.working/journey-state.json`) saves suite and
trace selections separately, review decisions, measurement status, pending
questions, and the next action. Returning to an audit, including after another
workflow, resumes its pending decision or first unfinished step. Requests only
to validate a specification or measure or aggregate coverage stay within that scope.
Audit evidence paths are relative to the repository. Each case or run must fall
within its selected suite or trace location.

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
available evidence. This readable Markdown artifact is
separate from Coverage measurements (JSON).
Test mappings describe what evaluations are designed to address and do not,
by themselves, establish measured coverage.

The Audit coverage report can be prepared before measurement and updated as
evidence becomes available. Without usable traces, you can establish the audit
proposed checks and report, but coverage is **unmeasured**, not 0%. Measurement
is deferred only after you choose independent report work. The unchecked
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
and supporting evidence. One question offers corrections, acceptance, or the
next action, then waits. Acceptance alone ends review without another question.
Accepting the findings can complete a fully measured
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
verifier would check it, planned shortcut/cheat controls, and supporting evidence.
Recommendations distinguish agent failures from verifier defects and ungraded
runs. Cheat controls remain planned during proposal-only work; when a task is
built, applicable controls must be executed before it is described as screened.
Missing evidence leaves screening unproven. They are saved in
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
