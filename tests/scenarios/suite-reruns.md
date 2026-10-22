<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Generated suite review and rerun handoff

Exercise the authoring skills in an isolated synthetic repository. Inspect saved
artifacts and provider-resolved selections; matching skill wording does not
establish that the handoff works. Do not use model credentials or start container
jobs for these documentation checks.

## Review at creation and after saved runs

Exercise task authoring with synthetic inputs and stop before execution. Without
asking separately for a review guide, inspect the generated suite-root README
and the authoring reply. The reply must link the guide, and its inventory must
make every authored task reviewable: purpose, input, expected outcome, grading
criteria, and links to the task and verifier details. Tasks must be visible as
not run. Read the input and grading summaries without formatting JSONL or
reconstructing the conversation. Source evidence, when present, must be labeled
as the basis for the task, not as a successful execution of it.

Then supply saved synthetic artifacts for a successful control, a failed
actual-agent attempt, and an attempt with no retained trace. Request a run-result
handoff using these artifacts without executing evaluations. The same README
must refresh its evidence links and observed status, distinguish controls from
agent attempts, and explicitly identify missing traces. Follow every linked
artifact from the README's stated directory. Any viewer command must match the
available runtime and point to the intended artifacts; unavailable viewers need
an explicit limitation and direct artifact links. The reply must link the guide
without implying that this documentation exercise executed a real agent.

## Returning to a Harbor starter suite

Prepare two authored tasks, `refund-policy` and `refund-reject`, plus an unrelated
draft in the same parent. Save `run.yaml` with explicit `tasks` entries for
only the two selected tasks, a real-agent configuration, nondefault model
settings, per-task overrides, and two attempts per task. Record fixture setup,
credential variable names, and result locations in local documents. Map one
coverage requirement to both tasks and a second requirement to only
`refund-reject`. Include a fixed original job name to expose output collisions.

**Request:** “Finish this suite's handoff so I can rerun all cases or one coverage
item on a later visit. Do not run model evaluations.”

Inspect `.eval-author/README.md` and follow it from the documented directory
without the prior conversation. Confirm that:

- The task inventory contains the two selected tasks and their review details,
  keeps the unrelated draft outside this suite, and links their available
  evidence separately from controls and source material.
- Setup names the actual runtime, activation/install procedure, backend,
  credentials without values, and state reset requirements.
- The full-suite command resolves to exactly the two intended tasks.
- The individual-item examples preserve both tasks for the shared requirement
  and select only `refund-reject` for the narrower one. Any saved subset configs
  retain the selected task entries and agent, model, environment, verifier,
  attempts, and task-specific overrides.
- No command runs every draft by accident, uses an unsupported coverage-ID
  filter, or substitutes NOP/Oracle for the actual agent evaluation.
- A new invocation writes to a fresh output path despite the original config's
  fixed job name. Result instructions locate per-case rewards and exceptions.
- The final handoff contains a clickable **Run instructions** link to a real
  README heading/anchor. Follow it from the saved repository without conversation
  context; a plain phrase, generic README link, or nonexistent anchor fails.
- Specific result links name the actual job/trial and task/attempt, rather than
  only linking the jobs parent. The linked record's agent, reward, and exception
  match the reply and report; controls are not passed off as actual-agent results.
- The final handoff distinguishes configuration inspection from execution
  evidence and preserves all known limitations in both report and reply.

Use the installed Harbor schema and, where supported, `--print-config` to inspect
the documented commands without launching evaluations. Resolve local datasets
when needed; merely printing a dataset path does not prove the selected tasks.

Repeat with missing agent credentials or an unavailable adapter. The README must
still exist and give the known setup and task mapping, but clearly identify
which commands remain blocked or unverified. The task review information must
remain usable before execution. The reply shows **Blockers** with the cause,
blocked step, and concrete next action; it must not claim a completed run.

## Viewer discoverability in the conversation

Exercise the two handoffs in a fresh authoring session without asking for a
viewer. At first-task creation, use one synthetic Harbor task directory and no
runs. For the result handoff, supply retained synthetic control and actual-agent
jobs with distinct names, including a failed attempt. Use an adapter fixture
that records actions/replies in custom JSON but exports no ATIF. Record that
the installed viewer's CLI options were checked but its web server has not
been launched. No live evaluations are needed for this scenario.

Ask for the first-task review checkpoint and later the final results
review. Inspect each user-facing reply separately from its linked README:

- Before execution, the reply introduces the local viewer and gives a copyable
  task-view command, working directory, browser URL, and a named task to select.
  The user can find the input and grading files without a follow-up question.
- After execution, the reply gives the run-view entry and identifies the actual
  agent job, task, and attempt; it does not send the user to a control job.
  It explains how to inspect the recorded reward and grading evidence.
- Both replies distinguish a URL to open after launch from a verified running
  service. CLI help alone does not become a claim that the viewer was opened.
- The results reply explains that no native trajectory was captured and links
  the actual custom JSON artifact. It neither promises a populated Trajectory
  tab nor invokes the NeMo Gym/ATIF reader on this unsupported format.
- The README retains the same review instructions. Passing file-link checks
  alone does not pass this scenario: a reply containing only the general guide
  link and an evaluation rerun command reproduces the discoverability failure.

Follow the documented launch and navigation steps against those synthetic
artifacts when a local browser is available. Also exercise a runtime without
viewer support: the reply must name that limitation and link available evidence
instead of silently omitting review or inventing a viewer command.
For a blocked or control-only run, it must mark the actual agent as not run and
offer task review without inventing an actual-agent job or attempt.

## NeMo Gym and trace-derived suites

For NeMo Gym, supply an existing draft with a model composition, `reproducibility.md`,
and JSONL rows containing both model input and verifier metadata. Request the
same handoff. Follow the draft-root `README.md`: full and subset runs must use
the selected NeMo Gym runtime and composition, retain verifier fields and row order,
document server startup for `--no-serve`, reset state, and use fresh outputs.
No Harbor-only selection or control flags may appear as NeMo Gym commands.
Also exercise the creation and saved-run review checks above: each selected row
must have a readable input, expected outcome, and grading summary, with its
dataset locator and links to available native evidence. Verifier metadata must
not be presented as part of the model's input. Passing native controls must stay
distinct from actual-agent attempts.

For a trace candidate, check `<task-dir>/README.md` alongside the existing
`task/README.md`. Controls must be labeled as controls, with missing real-agent
configuration visible. The root guide must summarize the generated case and
link the source evidence separately from validation and actual-agent evidence.
For a batch containing a candidate, an unproven task,
and a no-candidate member, inspect the collection guide: all members and statuses
remain visible, and executable selections point to actual task directories.
Follow its case review links without opening raw trace JSON; a no-candidate
member must not be presented as an authored or passing case.

For requested publication, inspect the preview and exported root README, then
follow its paths from the export without access to the private source workspace.
No private report or unexported config may be required. Changing the README after
review must invalidate the publication attestation. Existing task README
content and its uppercase filename must survive; there must be no case-only
filename pair in one directory. For a standalone draft, verify that rerun
and case review sections extend the existing task-level `README.md` without
renaming it.

Retain the generated guides, configs, resolved selections, and any observed
failure as evidence. One successful exercise is a bounded check, not a claim
that all future authoring runs will produce correct documentation.

## First-task review and deterministic handoff

Apply the first-eval renderer and state contracts when this suite is authored
through the first-evaluation workflow. Preserve exact fixed text; dynamic task/result values must
come from the current saved state and linked artifacts. Retain renderer input
and output with each observed reply so wording regressions are reviewable.

At first-task review, require links to the actual task README, HTML report, and
Markdown counterpart, plus concrete input, expected result, grading, controls,
and limitations. Formal grading controls may be pending if accurately labeled;
the environment must already be proven. A missing HTML renderer dependency is a blocker with retained
Markdown/task review, not permission to build the remaining tasks. If the user
requests a change, revise the same task, keep old evidence, update the affected
checks, and review the current revision again. Explicit first-task approval
unlocks the remaining approved tasks; they require validation and documentation,
not a new human review gate for every task.

For the final result fixture, use one actual-agent attempt for each agreed task,
with at least one reward 0 and no ATIF. Provide complete native results and
controls, valid configuration, report/Markdown/task READMEs, and verified run
instructions. Completion must not demand two successful attempts, native ATIF,
or measured audit-gap closure. Follow both a specific successful and a failed
agent result link, then a control link; their identities and purposes must remain
distinct. The reply and saved report explain low scores and absent trajectories.

Delete one native result in a separate fixture. The handoff must show missing
work and **Blockers**; a linked README or synthetic diagnostic score cannot fill
that gap. Restore it and request only report refresh: no additional agent run or
changed approval is permitted, and artifact refresh must not mutate run evidence.

After complete creation, the exact audit invitation appears once. Declining it
persists through later report refreshes and rerun explanations; accepting it
hands current setup, `ETHOS.md` confirmation, suite selection, and run evidence
to the existing audit flow. Audit-specific missing traces do not retroactively
make the creation journey incomplete. Do not issue that invitation while a
required creation artifact or attempt is missing.

For native Gym, repeat the handoff with a two-row selected JSONL and two repeats,
plus an excluded third row. Preserve row order and verifier fields and identify
four rollouts, two examples, and their native identities. The task README keeps
the actual composition, agent/model, startup/reset, and repeat settings. Its
single-task/row command selects the intended full original row, does not widen
the dataset, and writes fresh output and health paths. Changing selected dataset
bytes must invalidate affected old evidence rather than presenting it as current.
