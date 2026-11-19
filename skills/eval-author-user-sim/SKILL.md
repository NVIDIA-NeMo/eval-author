---
name: eval-author-user-sim
description: >-
  Author a neutral user-simulation scenario for an approved User-sim task after
  the calling first-eval or task-create flow proves its Harbor environment. Reuses the
  agreed goal and prepared fixtures to write and validate user-sim.yaml, then
  defines conversation-dependent verifier checks and controls. This stage authors
  scenario artifacts, not a Harbor or NeMo UserSim runtime integration.
triggers:
  - eval-author routed an approved User-sim task for authoring
  - author the agreed adaptive conversation scenario
  - continue neutral user-simulation task authoring
not-for:
  - eval-author (use for the evidence standard and workflow routing)
  - eval-author-first-eval (use to plan and review a starter evaluation suite)
  - eval-author-task-create (use to select an eligible additional task)
  - eval-author-environment (use to build and prove task environments)
compatibility: >-
  Reads the caller's approved task and prepared local fixtures. Writes only the
  selected task's artifacts under .eval-author/. Validation needs the sibling
  eval-author skill and Python with PyYAML in the selected authoring runtime.
  Runtime execution requires a separately verified interactive agent integration.
maturity: alpha
license: Apache-2.0
user-invocable: false
allowed-tools: Bash Read Write Grep Glob
metadata:
  owner: NVIDIA-NeMo/ase_team
  owner_url: https://github.com/orgs/NVIDIA-NeMo/teams/ase_team
  tags: [evaluation, authoring]
---
<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Eval Author: user simulation

Use [eval-author](../eval-author/SKILL.md) for the shared standard. Consume the
calling flow's context without repeating onboarding, Ethos confirmation, source
selection, or design approval. The caller retains its progress updates, task
eligibility rules, first-task review, and execution checkpoints.

## Check the handoff

Require Harbor as the selected provider, the approved task identifier,
`interaction_type: user_sim`, its
conversation scenario, required state continuity, prepared task directory,
applicable Ethos and policy, environment proof, and the selected fixture sources.
Reuse the caller's confirmed decisions and author only the named task. If a
required input is missing or inconsistent, return that specific gap to the caller.
Do not invent environment proof, broaden task-create eligibility, or switch the
selected provider to bypass an unavailable integration. For native NeMo Gym,
preserve the User-sim design and return the unsupported authoring route to the
caller before creating scenario files.

The proven environment kit must precede production task verifiers and reference
solutions. A narrowly requested synthetic authoring exercise may use supplied
test fixtures, but those fixtures and local controls do not establish a proven
environment or an actual-agent run.

## Author the scenario

Use the shared [scenario contract](references/scenario-contract.md)
as the only definition of fields, neutral behavior defaults, and information
visibility. Write `<task>/user-sim.yaml`. Do not create a persona biography,
persona variants, runtime configuration, or another scenario schema.

Reuse the task goal and derive relevant user-known facts from the prepared
fixtures. Check identifiers, ownership, item choices, preferences, and constraints
against those sources. Distinguish what this user knows from what only the agent's
tools or verifier can observe. Keep reference answers, hidden grading data, and
internal policy implementation details out of simulator inputs.

Write the opening message so it preserves the intended information gap. Add only
the disclosure, reaction, and stopping rules needed for this task. Later replies
must depend on the agent's actual questions or proposals when the scenario calls
for adaptation. Do not predict a particular reply, hardcode an expected agent
utterance, or reveal an answer merely to guide the agent toward passing.

The scenario file stays outside the target agent's starting environment. The
caller must retain the simulator/target visibility requirements for the future
adapter. Task termination is not evidence of successful completion.

Run the contract validator with the selected runtime's Python:

```bash
python <eval-author>/scripts/user_sim.py validate --path <task>/user-sim.yaml
```

Repair structural errors and rerun validation. Then check facts and behavior
against the agreed scenario and fixtures. A valid file does not prove either
semantic correctness or runtime readiness.

## Make the exchange matter to grading

Apply the caller's [construction contract](../eval-author/references/construction-plan.md)
and place requirements in the existing task README's Verifier section and native
verifier files. Name the observable user and target-agent events, required order,
state changes, and independent source for each expected outcome. Preserve the
task's reward semantics and the caller's required validation procedure.

Check the conversation-dependent behavior, not just a plausible final answer.
For a required confirmation, verify that the user confirmed the proposed action
before the target agent performed it. A later confirmation cannot repair an
earlier unauthorized action. Do not award target behavior based on simulator
messages or treat unrelated tool calls as proof of the intended action.

Build a positive control and relevant incorrect-behavior controls from the same
task requirement. Include a bypass control, such as performing the action without
confirmation, and an ordering control when order matters. Test paraphrases when
the check concerns meaning. Check end state independently when the task requires
an actual change. If the available evidence cannot distinguish these cases,
report the missing observation rather than weakening the verifier.

Synthetic transcript or state controls test the verifier. They do not replace
the caller's native task qualification, environment proof, or actual-agent trials.
Do not mark a task accepted or ready for authored-task approval while the caller's
required checks remain blocked.

## Return to the calling flow

Return the scenario path, validator result, fixture sources used to check user
facts, conversation-dependent verifier requirements, control results, and any
remaining blockers. Put this information into the existing task README and parent
workflow artifacts instead of creating a separate planning document.

This subskill authors portable scenarios. When the caller reaches execution
configuration, use the shared [Harbor translation](../eval-author/references/harbor-user-simulation.md)
helper with the selected target and simulator settings. Translation doesn't prove
interactive runner support or execution evidence integration. Keep unmet runtime
requirements explicit and use the caller's existing integration handoff. Do not
pass `user-sim.yaml` to Harbor as a native job configuration, flatten the scenario
into a single request, or claim that validated YAML or local controls demonstrate
an actual-agent conversation. Return to the calling authoring stage. The first task still needs approval before
remaining tasks are built, and each later user-sim task uses this same handoff.
