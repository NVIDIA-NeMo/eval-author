<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Review and rerun a generated suite

## Report generation

Use this shared reporting contract for Harbor and NeMo Gym.

For first evaluations, create both report outputs for the first-task review before
building the remaining tasks. Refresh after revisions, authoring, validation,
and actual-agent execution. Keep review, validation, and execution status
separate in the authored suite inventory, including agreed tasks not yet created.
Do not invent unsupported report metadata fields: lifecycle columns, exact-attempt
presentation, and native Gym Runs entries remain dependencies of the shared
report implementation. Missing presentation never changes the recorded result.

`.eval-author/report.html` defines the case/run review content.
Its Tasks tab lists discovered Harbor tasks and NeMo Gym workload definitions by directory,
including tasks being authored and accepted tasks at their stable paths, with provider, task name, grading,
behavior, expected outcome, and actions. Its Runs tab lists one row per native
Harbor job with date/time, the exact on-disk directory name and project-relative
path, role, status, aggregate reward, and actions. Its Harbor viewer action opens
the job and its individual trials. Standalone results appear separately under
the collapsed Legacy trials section; a folder of trials does not establish a
single job or evaluation invocation. Both show available warnings
and missing-data states. Settings configures browser viewer addresses only.

The same generator maintains a report section in `.eval-author/README.md` beside
the HTML. It appends the section when absent and updates only the content between
`<!-- BEGIN EVAL AUTHOR REPORT -->` and `<!-- END EVAL AUTHOR REPORT -->`.
Existing setup, suite commands, case descriptions, and other authored content
outside those markers remain byte-for-byte intact. Never replace a suite README
with the report. Malformed or duplicate markers stop the refresh without
publishing either output; repair the markers while preserving the content.

Within that section, each report content tab becomes an H2 section in tab order
(`## Tasks`, then `## Runs`); omit Settings. Directory groups become H3s,
tables keep the same columns and ordering, and native score labels replace visual
bars. Legacy trials becomes an H3 under Runs. Preserve statuses, warnings,
record diagnostics, and missing-data labels. Edit task documentation, native
artifacts, or supported reporting metadata to update generated content, then
refresh. Maintain operational and rerun guidance outside the marked section for
both providers.

Task README actions become relative links to the source documentation (or the
HTML's saved snapshot when the source is unverified). NeMo Gym source actions link
the native workload definition; NeMo Gym workloads do not open in Harbor's viewer.
For native NeMo Gym scaffolds, the report finds the nearest task README above the
environment definition. Document dataset paths, selected rows, and each case's
input, expected result, and grading there; a workload row does not imply a
single dataset example or native validation. NeMo Gym run evidence remains linked
from the maintained README; the Runs table reads native Harbor jobs only.
Harbor actions use the HTML's default viewer URLs; Markdown cannot read browser
settings or probe a viewer. Use the linked HTML report for configured addresses
and connection help.

Reporting observes the existing workflow; it does not select evaluations, start jobs,
or change controls. The HTML report is an additional task/run review entry point; it does
not replace or suppress the plan and run summary.

### Generate or refresh

Resolve the installed core `eval-author` skill beside the calling skill. Run its
bundled script using Python 3.11+ with `jsonschema` and `markdown-it-py` 4.x available:

```bash
python <eval-author-skill-dir>/scripts/report.py refresh --repo .
```

This uses the shared file-only `search_tasks.py` scanner. It
does not run `discover.py`, import the agent, invoke Harbor, or require Docker,
credentials, or inference. By default it searches this project for tasks and
reads retained results below `.eval-author/jobs`. For selected existing roots:

```bash
python <eval-author-skill-dir>/scripts/report.py refresh --repo . \
  --task-root evals/tasks --jobs-root evals/jobs
```

Roots are project-relative and repeatable. Selected roots are retained in the
internal report state. For authoring and handoff, keep the project root selected
(`--task-root .`) unless the user selected a narrower scope. New tasks stay at
`.eval-author/tasks/<slug>` throughout authoring and validation. Default task inventory
excludes `.eval-author/.working`, `.eval-author/archives`, and historical
`.eval-author/task-drafts` copies; select one of those roots explicitly to inspect
its retained evidence. Separate selected directories remain separate rows, even
when their contents match. Ordinary nested checkouts are excluded unless
explicitly selected.

Refresh after tasks are authored, after additions or edits, and after validation
or actual-agent runs, including failed runs. Refresh again at the final handoff
and link the report. When the report needs to be mentioned, always link
`.eval-author/README.md` and `.eval-author/report.html` as the last lines of the
response, after all other communication with a note like: Review (tasks|runs)
created in this session in the `.eval-author/report.html` or `.eval-author/README.md`.

Present evaluation findings and report links to the user; describe generation internals
only when asked. Reuse the settled scope and authorization: a missing report is not
a request to redesign or execute evaluations. If report generation fails,
report that failure separately; do not alter the task, run command, execution
exit status, readiness result, or proof requirements to make the report succeed.
A returning refresh reconciles artifacts even if an earlier handoff was interrupted.

Outputs are `.eval-author/report.html`, containing the fixed template and validated
embedded JSON, and the marked report section in `.eval-author/README.md`. Both
use the same validated records; authored README content is preserved and is not
reporting state. `.eval-author/.working/report/state.json` is the authoritative
report metadata and last inventory snapshot. A legacy HTML report is imported
once when that state is absent; later refreshes use the internal state and repair
missing or edited HTML. The first import also saves the original validated records
and metadata as `.eval-author/.working/report/legacy-import.json`. Review this
snapshot and imported journey notes when resuming an old workflow. A legacy
`task-drafts` directory may contain the only unfinished task and bound receipts;
use `--task-root <existing-task-or-directory>` to inspect or continue it at its
existing path. Default inventory exclusion does not mean that its work is obsolete.
Never hand-edit the HTML or rebuild the UI per task.
Task READMEs and native evidence take precedence. Refresh repairs a missing or edited generated section while
preserving surrounding documentation. Deleted records disappear after a complete
scan; incomplete inventories retain prior in-scope records as unverified.
State and both outputs are prepared before publication; each file is updated
atomically, with state committed first. An interruption between updates is repaired
by the next refresh. An OS advisory lock prevents concurrent refreshes and releases
automatically when the process exits. Locks and temporary writes stay under
`.eval-author/.working/report/`.
Exit 0 means complete file inventory, 1 means a published incomplete inventory
with warnings, and 2 means the refresh failed. None establishes evaluation
validity or success.

Each task row includes a Task README action below its viewer or source action. It opens the saved
README as Markdown in a modal, including headings, tables, and code blocks.
The preview is embedded for offline use, sandboxed, and blocks scripts,
images, and link navigation inside the preview. Open the source README via its
Markdown link to follow documentation links. Refresh after editing a README.
Missing, unreadable,
or oversized READMEs (over 256 KiB) show an explicit unavailable message.

### Report metadata

Task/run fields are validated by the schema constants in `scripts/report.py`.
Use `--metadata <temporary-json-path>` for a bounded reporting-only patch; keep
this input under `.eval-author/.working/report/` and remove it after refresh.
For example, using actual discovered paths and a documented reward range and binary pass rule:

```json
{
  "tasks": {
    ".eval-author/tasks/refund-policy": {
      "display_id": "refund-01",
      "grading_kind": "deterministic",
      "grading_rule": "All refund-state assertions pass."
    }
  },
  "runs": {
    ".eval-author/jobs/refund-baseline": {
      "role": "agent",
      "reward_key": "reward",
      "reward_min": 0,
      "reward_max": 1,
      "pass_value": 1
    }
  }
}
```

At each run handoff, record its actual role, reward key, and documented reward
range in this metadata patch before refreshing. Record a pass rule only when
documented; it is optional and independent of reward aggregation. Use the same
declared scoring contract for repeats. Leave unknown ranges or rules unspecified.

This records a report interpretation, never a verifier setting. `pass_value`
means exact equality for that named metric; do not use it for a threshold rule.
Only set it from the documented verifier/run contract. `reward_key` defaults to
`reward` and may be supplied without `pass_value`. The generator sums that metric
across completed, valid trials, preserving fractional rewards. Other metrics are
not added to it. Pass/fail counts are secondary and require `pass_value`.

`reward_min` and `reward_max` must be supplied together, with min smaller than
max. Set them from the verifier/run contract, never from observed extrema or
`pass_value`. Without a documented range, the report displays the reward sum
without a graph. Out-of-range rewards remain visible with the graph unavailable.
For a 0–1 scale, rewards 1, 0.65, and 0 produce 1.65 / 3 reward (55%). For other
scales the graph uses the declared aggregate minimum and maximum.

The denominator counts scored trials, including intentional repeated attempts,
not distinct task names. Completion is separate: incomplete jobs label their
score partial and show scored/expected counts, execution errors, and pending or
unscored trials. Missing data, errors, and invalid evidence are never zero
rewards; no scored trials means score unavailable. Trial identities must be
unique. Duplicate IDs or more retained trials than the native job total disable
the aggregate to avoid double-counting retries. Native job summaries are not
added to child trial rewards. Do not substitute a later single-case result into
an earlier job.

A trial subsequently owned by a native job does not remain as a duplicate
legacy row.

For future Harbor suite evaluations, use one native job per invocation, with an
explicit task set, agent/model configuration, and agreed attempts. Give every
rerun a fresh job directory. This also applies to a one-task evaluation intended
for the Runs table. Controls and per-trial proof workflows may keep standalone
trials when fixed evidence paths are required; they appear under Legacy trials.
Report refresh reads evidence and never launches, rewrites, or combines runs.

Optional imported `behavior` and `expected_outcome` fields require project-relative
`source_refs`. Unique README sections take precedence. Test-case sections use each
discovered task's immediate parent directory. Headings and row paths omit the
leading `.eval-author/` for display; section identities, stored paths, and viewer
commands retain the full project-relative paths. Do not invent product-area
labels, derive categories from task-name prefixes, or rename directories for the
report. Directories with the same basename under different suites remain separate.
Tasks sharing a parent appear together, even if they belong to different run
configurations; these are filesystem sections, not inferred suite memberships.
Verifier type is `deterministic`, `model`, `hybrid`, or `unknown`. Native Oracle/NOP
runs are labeled controls; explicitly label custom controls and actual-agent
runs rather than inferring their purpose from a directory name. A run patch may
record `invalid_attempts` as native attempt ID to evidence-backed reason; native
rewards remain unchanged and the invalid attempt is excluded from passes.

### Viewer and review handoff

Test cases and Runs use Harbor's native viewers. Settings stores one jobs URL
and one task URL in browser local storage. Harbor selects the first free port
in 8080–8089 for either mode; it has no separate task-viewer port default. The
report starts with jobs on 8080 and tasks on 8081, preserving saved overrides.
Use the actual address printed by each viewer's launch command when it differs.
Clicking an action checks that URL's
`/api/config` and the requested item's detail endpoint before navigating in the
same tab. Verify task source paths and native run IDs so a same-named item in
another catalog does not silently substitute for the requested item. A running
viewer can serve existing aliases if its API resolves the correct source path.
Do not create catalog symlinks or scan arbitrary local ports.

Task mode serves immediate child task directories. If the item is missing or
the mode is wrong, show the specific problem with Settings and a launch command.
A three-second timeout or browser-blocked request must say the viewer could not
be reached, not that it is definitely stopped. Offer retry and direct navigation
when a browser cannot perform the check. The report itself works offline; only
viewer clicks make these read-only requests. Verify options and routes against
the installed version; the initial routes match Harbor 0.20.0.

Keep the existing walkthrough below: explain the relevant task, run, and saved
evidence and give the exact verified viewer launch command. Link the HTML as the
catalog. Source traces and task controls are not actual-agent results. Report
refresh does not start a viewer or claim that an environment or task is valid.

## Task documentation

Add these exact sections to every newly authored task's `README.md` for either
provider. For native NeMo Gym scaffolds, use the task root's README and link its
dataset rows and component files:

```markdown
## Input

The request or message the agent receives, with links to its instruction file
and any input fixtures. Describe the relevant starting state.

## Behavior under test

A concise description of the capability or boundary this case exercises.

## Expected result

The observable result that constitutes success for this case.

## Grading

How the verifier measures that result: checks or judge criteria, reward metrics,
pass rules, and known limitations. Link the test entry point, verifier code, and
the criteria, expected-data, or judge/rubric files actually used by this task.
```

Put `Input` first after the title and optional case/source identification, then
`Behavior under test`, `Expected result`, and `Grading`. Keep these as separate,
nonempty H2 sections. Quote a short request verbatim; for long or structured
inputs, describe the input shape and link the complete source. Link `instruction.md`
and the actual fixtures with relative Markdown links, not bare code paths. Keep
expected answers in Expected result and verifier-only data, outside Input.

Keep the `Grading` heading plain; put clickable relative links in its body.
For example, link `tests/test.sh`, `tests/verify.py`, and `tests/expected.json`
only when those files exist and are used. For model or hybrid grading, also link
the judge implementation and rubric/configuration actually used. A deterministic
verifier still needs code and criteria links; it does not need an invented judge.
Check that all README links resolve before the authoring handoff.

Do not combine expected results
and grading under `Expected outcome and grading`, or replace an existing
`Grading` heading with that combined heading. Expected result describes success;
Grading explains how it is measured. During task authoring, split a combined
section into these headings while preserving its substantive content.

Write the real behavior, result, and grading details, not the example prose.
Preserve the README's other required sections. Behavior under test and Expected
result are the canonical sources for the report's Behavior and Expected outcome
columns.  Missing, empty, duplicate, or oversized sections show `Not documented`;
rendering never asks a model to invent them. For existing tasks, reuse explicit
documentation or inspect the instruction, fixtures, verifier, and rubric once to
supply source-grounded metadata. Routine refresh must not rewrite existing task
documentation.

## Review and rerun guidance

Use this guidance for setup, run commands, and evidence review with either
provider. Maintain the suite inventory and operational instructions in the
existing suite README, and refresh the shared HTML report and marked Markdown
section for both Harbor and NeMo Gym. Keep authored setup, rerun commands, pending
case descriptions, and evidence links outside the generated markers. Neither
provider's reporting workflow replaces those sections.

First-evaluation conversations use the deterministic
[conversation renderer](first-eval-conversation.md). Use `ETHOS.md` when naming
the intended-behavior document, **tasks** for proposed or created evaluations,
**examples** for distinct inputs, and **attempts** for repeated executions.
For new first-eval suite guides, use an authored `## Rerun evaluations` section
outside the report markers. Include full-suite and single-task commands with
the saved agent settings, explicit selection, and fresh output paths. Preserve
an existing equivalent section rather than duplicating it. In the handoff,
make **saved run instructions** a direct Markdown link to that section's verified
heading anchor; never substitute a bare path or unlinked phrase. The renderer
checks local file and anchor existence, but the author must verify the commands
resolve to the intended selection through the installed provider.

When creation is complete, place the optional audit invitation after the results,
rerun guidance, and completed checklist, followed only by the report links. Reuse
the exact run and attempt reviewed here in saved state and later recaps; do not
silently switch to the newest job. If creation is incomplete, show unfinished
tasks and any **Blockers** instead of the completion invitation.

Create the review and rerun guide as soon as the first cases are authored, using
`README.md` at the top level of each generated evaluation suite and preserving
task README filenames as described below. Link it when presenting the created
cases, without waiting for execution or a question about where they are saved.
Refresh it after validation or agent runs and when cases, configuration, setup,
or commands change. A conversation reply or run report does not replace this
entry point. Multi-task suites need a suite guide in addition to individual task
READMEs.

Use the suite's existing root under `.eval-author/`: for first-eval this is
`.eval-author/README.md`; for a standalone task,
extend `.eval-author/tasks/<task-slug>/README.md`. For trace-derived work,
use `<task-dir>/README.md`, beside `task/`; a generated batch also needs a README
at its collection root. Keep the trace task's required `task/README.md` intact.
When an existing generated suite has an uppercase `README.md` at the same
location, keep its filename and content and add the review and rerun sections
there. Do not rename existing task READMEs or create files differing only in
case. Keep unrelated suite guides separate.
Proposal-only work does not create a suite or require this file.

## Review the created cases

Put a case inventory near the top of the README, before the run instructions.
Reuse the case names and requirement or coverage IDs from the agreed plan.
Include every selected case, with pending or blocked cases visible alongside
authored ones. One task may contain several examples; repeated attempts do not
add cases. The inventory can also serve as the coverage-to-task mapping below.

For each case, give a short readable description and links to the details:

- **Purpose and input:** what behavior it tests, the request and relevant
  starting state, and the exact task or dataset row. Link the instruction and
  fixtures. For NeMo Gym, identify the dataset and one-based physical JSONL line;
  describe the row's input in Markdown so the user need not format JSONL just
  to understand the case.
- **Expected outcome:** what a correct response or action achieves. Link the
  task's separate `Expected result` section; keep expected answers distinct
  from the agent's input.
- **Grading:** what the verifier actually checks, the reward metrics and pass
  rules, and any known limitation. Link the separate `Grading` section and
  verifier. Reuse existing task documentation for detailed explanations.
- **Source evidence:** when a case was derived from a trace, link the reviewed
  source evidence and derivation notes as provenance. State when no source trace
  was used. A source trace is not evidence that the generated case has run.
- **Run evidence:** link available per-case results, logs, and execution traces,
  labeling task controls separately from actual-agent attempts. Include failed
  attempts and distinguish a case that has not run from a run whose trace is
  unavailable. State known blockers or missing-evidence reasons; do not infer
  execution from a draft, configuration, or source trace. Link existing run
  reports for detailed histories rather than duplicating them in the inventory.

Use clickable links resolved from the README's location. Keep source and run
evidence in their existing locations; this guide does not change what is private
or exported. An exported guide must work from its exported root and identify
evidence omitted from the product without depending on private links.

When the installed provider has a supported viewer, include its exact launch
command, working directory, and which case or run to select. Verify the options
and directory layout against that installation. For Harbor versions supporting
`harbor view`, task review uses the directory directly containing the tasks with
`--tasks`; run review uses the jobs directory with `--jobs`. A trace workspace's
collection root may not be the task directory's parent. Use the actual paths in
the guide, and distinct ports when both views run concurrently. Link available
readable reports when a format has no verified viewer, and label any remaining
raw-only trace inspection limitation rather than inventing a command or UI.

## What a returning user needs

Write the guide using the generated files and verified runtime information.
It must be usable without the authoring conversation or an agent reconstructing
the commands. Include:

- **Setup and working directory:** where to run every command; the provider and
  runtime version used; installation or environment activation commands;
  dependency manifests/locks; required backend, services, agent adapter, model
  configuration, and access. Name credential variables and how to supply them
  to the configured runtime, without saving secret values. Link local setup
  files and explain fixture/state reset before each run.
- **Run the full suite:** a copyable command using the saved configuration and
  exact intended case set, including attempts and any required service startup.
  Use fresh job names/output paths on each invocation so prior results survive.
  Label whether this evaluates the actual agent or only checks task wiring.
- **Run one coverage item:** a table mapping the suite's existing requirement or
  coverage IDs and descriptions to task names/paths or dataset rows. Include
  commands for the mapped selections and a concrete example from this suite.
  A coverage item may need multiple cases; preserve all of them. If it shares a
  task with other checks, explain that selection runs the whole task. An audit
  tool/requirement ID is not automatically a provider CLI selector.
- **Read results and troubleshoot:** where each new run saves rewards, errors,
  logs, and available traces; how to inspect one case's result; the meaning of
  the actual reward fields; and remedies for known setup failures. Link the
  saved run summary for observed results and limitations.
- **Open readable evidence:** use the installed Harbor viewer for native tasks
  and jobs. For selected NeMo Gym JSONL records or standalone ATIF, follow
  [Local review](local-review.md) to create and link a private `report.html`.
  Save its exact regeneration command, selected source and rows, evidence role,
  and any missing or unsupported content. Introduce that review path in the
  conversation using [Show the user how to review](#show-the-user-how-to-review).
  A rendered report is not validation or proof of execution.

Keep task design and verifier details in their existing documents and link them
from the guide. For an incomplete suite, save the instructions that are known,
identify blocked cases and missing setup explicitly, and label unverified or
pending commands. Do not invent an adapter, model, credential, or successful run
to complete the README. Replace authoring placeholders before describing a
command as ready to copy; explicit blockers are preferable to fake runnable
examples. Refresh the guide and link it in the final handoff even if execution
is blocked.

## Select cases without changing the experiment

Use the installed provider's help/schema to verify commands. Preserve the full
suite's agent, model, environment, verifier, attempts, and other run settings
when selecting a subset. Check the resolved selection includes exactly the
intended cases, excluding unrelated drafts and blocked cases unless explicitly
selected. Document any deliberate setting changes.

For Harbor, reuse the full-suite config. A CLI task/path override is suitable
only after checking that it replaces the selection as intended and preserves
task-specific settings. Dataset name filters do not filter an explicit `tasks`
list. When overrides cannot preserve the configuration, save a subset config
under the suite root: retain the selected task entries, remove other task and
dataset sources, and keep the non-selection settings. Do not replace the real
agent with Oracle or NOP to demonstrate an individual agent evaluation.

For example, after saving and validating a one-case config for a starter suite
whose case is `refund-policy`, its guide could show these commands from the
repository root (use the actual runtime and paths in the generated guide):

```bash
# Full suite: actual agent evaluation, with a new output directory each time.
harbor job start -c .eval-author/run.yaml \
  --jobs-dir .eval-author/jobs --job-name "suite-$(date -u +%Y%m%dT%H%M%S)-$$"

# Only the refund-policy case, preserving the same agent and run settings.
harbor job start -c .eval-author/rerun/refund-policy.yaml \
  --jobs-dir .eval-author/jobs --job-name "refund-policy-$(date -u +%Y%m%dT%H%M%S)-$$"
```

For NeMo Gym, retain the selected composition, model configuration, dataset identity,
and reset procedure from `reproducibility.md`. Use a verified native selector or
save a subset JSONL with the complete original rows, including verifier fields,
in their original order. Show the exact selection and run commands, including
service startup when using `--no-serve`; do not invent a coverage-ID flag. Keep
full-suite and subset output paths distinct and fresh.

## Show the user how to review

At the first created-case checkpoint and again when handing back run results,
include a short review walkthrough in the user-facing reply. A general README
link or a rerun command alone does not introduce the review experience.

For Harbor, explain that its viewer is a local browser for the test cases and
saved attempts. Include the relevant launch command, working directory, and
browser URL in the reply, using the verified runtime and actual artifact paths.
Keep the same instructions in a clearly linked README section. Use the directory
directly containing the tasks with `--tasks`, and the parent of the saved job
directories with `--jobs`; verify options against the installed version. When
both views run concurrently, use separate available ports.

Give one concrete route through the relevant view:

- **Created cases:** open the task view, select a named case, read its
  **Instruction**, and use **Files** for its README, fixtures, and verifier.
  Explain that a task is one test case and that it can be reviewed before it runs.
- **Run results:** open the job view, select the actual agent's job, a named case,
  and one attempt when it exists. Explain that a job groups attempts and a trial
  is one attempt.
  Point to its recorded reward and grading evidence; show the trajectory when
  available. Keep control jobs separate from the actual-agent job.

If only controls ran or execution was blocked before any actual-agent run, say
the actual agent has not run and offer case review with any clearly labeled
control evidence instead of inventing an actual-agent job.

Use labels and navigation verified for the installed viewer. If a viewer is
already running and its content was checked, give its live URL. Otherwise say
“Run this command, then open this URL,” explain that the command stays running,
and label launch as untested when only CLI help was checked. Do not present a
planned localhost URL as a running service or require a new evaluation to review
saved artifacts. If the viewer is unavailable, state the concrete limitation
and point to the available readable report or case files.

An adapter that records custom action/reply JSON without ATIF can still supply
case files, trial results, and grading evidence. Explain that its native
trajectory is unavailable, and link the actual capture by name and path. Do not
promise that the Trajectory tab contains those actions or send unsupported
custom JSON through the NeMo Gym/ATIF reader. For supported standalone ATIF or NeMo Gym
JSONL, link the local reader's HTML report directly and explain its evidence role.

## Verify the handoff

Follow the inventory's links to each created case's input, grading, and available
evidence. Check that pending cases and missing traces remain visible and that
source evidence is not presented as a generated-case run. Check viewer commands
against the installed provider and the actual artifact roots.

Check the README's saved configurations from its stated working directory.
Use provider validation or configuration inspection to confirm both
full-suite and individual selections and their preserved settings. Record what
was validated versus actually executed; printing a config is not run evidence.
Reuse authorized run evidence where applicable. Do not spend model credentials
just to check documentation. A returning user should be able to review the cases,
follow setup, run the suite or a named item, and inspect results using this
README and the files it links. Link the refreshed README in the final
handoff, including when execution is blocked.
