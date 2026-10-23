<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Journey conversation fixtures

Each JSON input has a matching Markdown response. The stateful examples contain a
fixed, synthetic repository path and timestamp so replay never depends on a live
agent repository. Rendering must preserve the input and must not open that path.

Replay any example from the repository root:

```bash
python3 skills/eval-author/scripts/journey_conversation.py \
  --input tests/fixtures/journey-conversation/results-audit-existing-ethos.json
```

The fixtures cover unknown and selected goals, live prerequisite status tables, checked
results, optional recommendations, blocking requirements, missing/existing/changed
Ethos, confirmation pauses, and resumed decisions. The goal-switch example retains
an authoring blocker as history while resuming the current audit decision.

`tests/test_journey_conversation.py` compares both the pure renderer and CLI output
with every Markdown file. Changes to wording or layout require reviewing the
resulting Markdown alongside the workflow contract before updating expectations.

Markdown fixtures begin with two SPDX metadata comments and a blank line. These
three lines license the fixture; they are not part of the conversation output.
The tests remove that exact header before comparing renderer bytes. To compare a
CLI replay with its fixture in Bash or Zsh:

```bash
python3 skills/eval-author/scripts/journey_conversation.py \
  --input tests/fixtures/journey-conversation/ethos-draft-review.json | \
  diff - <(tail -n +4 tests/fixtures/journey-conversation/ethos-draft-review.md)
```

Draft fixtures bind the displayed link to the saved working path and content
digest. The review example retains unconfirmed content; the resumed write example
retains the user's existing approval and presents the next save/validation action.
Neither renderer call changes approval or writes the canonical document.

The completed handoff fixtures distinguish an unavailable authoring route, which
blocks initial planning, from a saved endpoint requirement that applies later.
Deferred requirements remain in state and become blocking once the selected
downstream work starts; only active blockers have presentation explanations.

The `prerequisites-*` inputs render the live status table after the readiness
reply, preceded by the fixed progress introduction. Supplied findings retain
their observed or user-confirmed status, while omitted rows remain Checking or keep their
flow-specific initial status. The audit example carries forward the user's
framework choice and shows the still-unknown completed-run trace requirement.
The `prerequisite-results-*` examples use state-only snapshots before artifact
inspection. They show the completed prerequisite table, optional recommendations,
and a brief transition into Ethos review. They do not repeat a checklist, even
when the snapshot retains historical confirmation or completed evaluation
metadata. They do not display a saved intent question, inspect or describe
Ethos, or repeat a continuation prompt.
The audit example retains its observed framework and selected scope; the blocked
example shows the prerequisite repair action without any checklist.

Within one resumed turn, the initial `prerequisites` message shows live status,
then the final `results` reply contains the completed table, recommendations,
progress checklist, and current intent question. Both tables have their fixed
introductory sentence. Do not insert a completed report between those messages:
the turn contains one live table and one completed table, with the completed
table remaining in the final reply. A blocked final reply uses the state-only
`prerequisite_results` stage. Its unblocked fixtures document the standalone
state-only projection, not an extra message in the normal successful flow.

The unsupported improvement-format fixtures include a copyable conversion prompt
under Blockers, for use in a separate conversion task. This guidance appears only
for the selected expansion flow's saved `evaluation_format` setup blocker. The
state-only variant provides the same prompt without resuming an intent question;
both retain the blocking decision and omit any continuation checklist.

Known audit trace-availability and unsupported improvement-format blockers use
canonical reasons, needed actions, and pause wording. Their inputs supply the
saved blocker ID and the skill file actually read; the fixture skill paths are
synthetic installation locations. Historical chat paraphrases remain in state
without overriding the rendered copy. Unknown and missing audit trace
availability have distinct canonical findings, and the unknown state-only
fixture presents its availability question once. Custom blocker explanations
retain their own required actions when mixed with a known blocker.
