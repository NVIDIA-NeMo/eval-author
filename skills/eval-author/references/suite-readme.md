<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Review and rerun a generated suite

## Report generation

Use this section for the HTML report and generated Markdown mirror.

`.eval-author/report.html` defines the case/run review content.
Its Tasks tab lists discovered cases by directory, with task name, grading,
behavior, expected outcome, and actions. Its Runs tab lists native runs with
date/time, identity, role, status, score, and actions. Both show available warnings
and missing-data states. Settings configures browser viewer addresses only.

The same generator automatically writes `.eval-author/README.md` beside the HTML
as a Markdown representation of that content. Do not choose, summarize, or author
README content separately: each report content tab becomes an H2 section in tab
order (`## Tasks`, then `## Runs`); omit Settings. Directory groups become H3s,
tables keep the same columns and ordering, and native score labels replace visual
bars. Preserve statuses, warnings, record diagnostics, and missing-data labels.
Task README actions become relative links to the source documentation (or the
HTML's saved snapshot when the source is unverified). Harbor actions use the
HTML's default viewer URLs; Markdown cannot read browser settings or probe a
viewer. Use the linked HTML report for configured addresses and connection help.

Refresh both outputs with `report.py`; never hand-edit the generated root README
or add independent summaries, plans, or sections to it. Edit task documentation,
native artifacts, or supported reporting metadata, then regenerate. Preserve
functional setup/rerun documentation in task READMEs and existing workflow
records before adopting the generated root README. The remaining README guidance
below applies to Gym and functional task documentation, not to authoring another
Harbor catalog.

Reporting observes the existing workflow; it does not select evaluations, start jobs,
or change controls. The HTML report is an additional task/run review entry point; it does
not replace or suppress the plan and run summary.

### Generate or refresh

Resolve the installed core `eval-author` skill beside the calling skill. Run its
bundled script using Python 3.11+ with `jsonschema` and `markdown-it-py` 4.x available:

```bash
python <eval-author-skill-dir>/scripts/report.py refresh --repo .
```

This consumes the shared file-only `search_tasks.py` from PR #67 unchanged. It
does not run `discover.py`, import the agent, invoke Harbor, or require Docker,
credentials, or inference. By default it searches this project for tasks and
reads retained results below `.eval-author/jobs`. For selected existing roots:

```bash
python <eval-author-skill-dir>/scripts/report.py refresh --repo . \
  --task-root evals/tasks --jobs-root evals/jobs
```

Roots are project-relative and repeatable. Selected roots are retained in the
HTML. Ordinary nested checkouts are excluded by the report unless explicitly
selected; source scanner behavior is unchanged.

Refresh after cases are authored, after additions or edits, and after validation
or actual-agent runs, including failed runs. Refresh again at the final handoff
and link the report. Present evaluation findings and report links to the user;
describe generation internals only when asked. Reuse the settled scope and
authorization: a missing report is not a request to redesign or execute evaluations. If report generation fails,
report that failure separately; do not alter the task, run command, execution
exit status, readiness result, or proof requirements to make the report succeed.
A returning refresh reconciles artifacts even if an earlier handoff was interrupted.

Outputs are `.eval-author/report.html`, containing the fixed template and validated
embedded JSON, and its generated `.eval-author/README.md` mirror. Both use the
same validated records; the README is never an input or separate state store.
No companion state/data file, report-specific plan, or per-run narrative is
generated. Never hand-edit either output or rebuild the UI per task. The HTML's
embedded metadata is read on refresh; task READMEs and native evidence take
precedence. Each refresh also repairs a missing or edited root README when the
HTML is unchanged. Both outputs are prepared before publication; each file is
replaced atomically. An interruption between replacements is repaired by the
next refresh. Exit 0 means complete file inventory, 1 means a published incomplete
inventory with warnings, and 2 means the refresh failed. None establishes evaluation
validity or success.

Each task row includes a Task README action below View task. It opens the saved
README as Markdown in a modal, including headings, tables, and code blocks.
The preview is embedded for offline use, sandboxed, and blocks scripts and
remote resources. Refresh the report after editing a README. Missing, unreadable,
or oversized READMEs (over 256 KiB) show an explicit unavailable message.

### Report metadata

Task/run fields are validated by the schema constants in `scripts/report.py`.
Use `--metadata <temporary-json-path>` for a bounded reporting-only patch; keep
this input in a temporary directory, not as another persistent project report.
For example, using actual discovered paths and a documented binary reward rule:

```json
{
  "tasks": {
    ".eval-author/task-drafts/refund-policy": {
      "display_id": "refund-01",
      "grading_kind": "deterministic",
      "grading_rule": "All refund-state assertions pass."
    }
  },
  "runs": {
    ".eval-author/jobs/refund-baseline": {
      "role": "agent",
      "reward_key": "reward",
      "pass_value": 1
    }
  }
}
```

At each run handoff, record its actual role and documented pass rule in this
metadata patch before refreshing; use the same declared rule for repeats of the
same grading contract. If the rule is unavailable, leave it unspecified.

This records a report interpretation, never a verifier setting. `pass_value`
means exact equality for that named metric; do not use it for a threshold rule.
Only set it from the documented verifier/run contract. Without a rule, the report
shows a native reward mean when available rather than manufacturing pass/fail.
Missing data and errors are never zero rewards. Trials in jobs are counted only
within their parent job. Retries are retained; conflicting totals disable the
complete-run bar. Do not substitute a later single-case result into an earlier job.

Optional imported `behavior` and `expected_outcome` fields require project-relative
`source_refs`. Unique README sections take precedence. Test-case sections use each
discovered task's immediate parent directory. Headings and row paths omit the
leading `.eval-author/` for display; section identities, stored paths, and viewer
commands retain the full project-relative paths. Do not invent product-area
labels, derive categories from task-name prefixes, or rename directories for the
report. Directories with the same basename under different suites remain separate.
Tasks sharing a parent appear together, even if they belong to different run
configurations; these are filesystem sections, not inferred suite memberships.
Older embedded `product_area` labels are discarded on refresh.
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

Add these exact sections to every newly authored Harbor task's `README.md`:

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
provider. For Gym, keep the suite inventory and operational instructions in the
suite README. For Harbor, use the generated report and its Markdown mirror for
the inventory; keep setup and rerun instructions in task READMEs and existing
workflow records, linked from the report. Do not add hand-authored sections to
the generated Harbor root README.

The suite README authoring instructions below apply to Gym suite guides and
functional task or export documentation. Harbor's root report and README follow
[Report generation](#report-generation).

Create the review and rerun guide as soon as the first cases are authored, using
`README.md` at the top level of each generated evaluation suite and preserving
task README filenames as described below. Link it when presenting the created
cases, without waiting for execution or a question about where they are saved.
Refresh it after validation or agent runs and when cases, configuration, setup,
or commands change. A conversation reply or run report does not replace this
entry point. Multi-task suites need a suite guide in addition to individual task
READMEs.

Use the suite's existing root under `.eval-author/`: for first-eval this is
`.eval-author/README.md`, beside `first-eval.md`; for a standalone task draft,
extend `.eval-author/task-drafts/<task-slug>/README.md`. For trace-derived work,
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
  fixtures. For Gym, identify the dataset and one-based physical JSONL line;
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
  and jobs. For selected Gym JSONL records or standalone ATIF, follow
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
harbor job start -c .eval-author/first-eval.yaml \
  --jobs-dir .eval-author/jobs --job-name "suite-$(date -u +%Y%m%dT%H%M%S)-$$"

# Only the refund-policy case, preserving the same agent and run settings.
harbor job start -c .eval-author/rerun/refund-policy.yaml \
  --jobs-dir .eval-author/jobs --job-name "refund-policy-$(date -u +%Y%m%dT%H%M%S)-$$"
```

For Gym, retain the selected composition, model configuration, dataset identity,
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
custom JSON through the Gym/ATIF reader. For supported standalone ATIF or Gym
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
