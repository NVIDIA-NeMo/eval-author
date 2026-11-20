<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Assess and create the agent runner

A runner connects the user's actual agent to the evaluation provider. Assess it
while planning tasks, so the proposal tells the user what connection work the
agreed tasks need. Build it after design approval. Hand it to a separate
engineering chat only when the assessment shows that a thin adapter cannot
preserve the agent.

## Two roles

| Task type | Runners |
|---|---|
| Single Turn | The target runner sends one message to the agent. |
| Scripted Multi-step | The target runner keeps the state that `state_requirements` specifies across the scripted inputs. |
| User Simulation | The conversational target runner, plus a customer runner that chooses each user message. |

User simulation adds a customer runner. It doesn't replace the target runner.
Assess the whole proposed task set before building either one, so a single-turn
runner isn't built and then immediately replaced. Build the conversation
capability from the start when any agreed task needs it, and share invocation and
ATIF export code with single-turn execution. When later tasks add conversation
to a single-turn suite, explain the added capability in that proposal and extend
the existing runner where practical.

## Assess during planning

Inspect before proposing: existing provider integrations and adapters, the
agent's entry points, required inputs and configuration, event or output
formats, session and history handling, and state reset. Reuse an existing
integration when it already invokes the actual agent with the needed capability.

Create or extend a runner in this chat only when every check below holds for the
**required** capability. An easy single-turn interface doesn't make a
conversational runner easy.

| Check | Eligible for an in-chat runner |
|---|---|
| Invocation | One documented command, Python callable, or existing HTTP endpoint with a clear completion signal |
| Inputs | A message plus a small, fixed set of configuration values |
| Setup | Existing dependencies and documented credential variable names |
| Behavior | The runner preserves the actual agent, model, tools, prompts, and policy |
| Evidence | Structured output or accessible events expose responses and ordered actions |
| Isolation | Each trial gets fresh state or a proven reset |
| Scope | A thin evaluation wrapper suffices; no application behavior changes |
| Conversation, when needed | Existing session or history handling, identifiable turn completion, and bounded cancellation |

A familiar framework is a signal, not a qualification. Inspect its actual session
persistence and event access. Missing token counts or private reasoning don't
disqualify a runner; those fields stay unavailable. Missing observable actions
needed to understand or grade a task do.

Save the result as `plan.runner` before rendering the first-eval proposal or
expansion scope:

```json
{
  "target_action": "create",
  "transport": "python",
  "capability": "conversation",
  "reason": "agent.chat(session_id, message) keeps history and returns tool events.",
  "source": "src/agent/app.py:42; README.md#running-the-agent"
}
```

`target_action` is `reuse`, `create`, `extend`, or `handoff`. `transport` is
`native` for a provider-supported agent, or `cli`, `python`, or `http`.
`capability` is `conversation` whenever any planned task is User-sim. `reason`
is the user-facing finding; for `handoff`, name the unmet check. `source` cites
the inspected files. [runner_plan.py](../scripts/runner_plan.py) validates this
record, and the renderer owns the proposal copy.

## Build after approval

Keep generated agent-specific files under `.eval-author/runners/<agent>/`.
Reusable helpers stay in Eval Author's installed scripts; copy them beside the
runner when they must run inside the task environment. Don't change application
code, prompts, tools, or models.

- **Single-turn Harbor runner:** a custom agent class selected by `import_path`.
  `run()` invokes the agent and records target ATIF on the host into its
  `logs_dir`. `environment.exec()` can return stderr mixed into stdout, so
  redirect the agent's output, errors, and exit code to separate files and read
  each one back.
- **Conversational Harbor runner:** implement `ACPAgentMixin` and declare
  `capabilities = AgentCapabilities(bridges=[BridgeKind.ACP])`; preflight
  rejects the runner without it. Its `acp_command()` starts a small stdio ACP
  server inside the environment that answers `initialize`, `session/new`,
  `session/load`, `session/prompt`, and `session/cancel`. Each prompt goes to
  the agent's existing session interface; the server streams the reply as an
  `agent_message_chunk` update and ends the turn with `end_turn`. Pass run and
  task identity, never credentials, through `acp_command()` and `acp_env()`.
  The bridge may start a new server process for a later prompt, so keep session
  state in the agent and never finish the recording when the process exits.
  `acp_install()` uploads the server and recorder; `acp_teardown()` closes the
  recording with the recorder's `finish` command below.
- **Customer runner:** select `harbor_structured_user:StructuredACPUser`.
  [Harbor user simulation](harbor-user-simulation.md) binds its scenario and
  options.

Keep failed or uncertain deliveries as failures. Don't retry a message unless the
interface proves the retry won't repeat an action.

## Target ATIF is required

Every runner writes the agent under test to `<trial>/agent/trajectory.json`,
where Harbor's viewer reads its Trajectory tab. Harbor (0.24 through 0.26) doesn't export a
target trajectory in user-sim trials, so the target runner must write it itself.
Use `TargetRecorder` from
[harbor_target_trajectory.py](../scripts/harbor_target_trajectory.py). It uses
only the standard library, so the ACP server can run it inside the container.
Record each observed user message, agent reply, tool call, tool result, error,
and available usage in order. Finish with `completed`, `failed`, or
`interrupted`; partial evidence is retained. The recorder keeps raw
`target-events.jsonl` beside the export and reopens the same run if the adapter
restarts. Close a conversational recording from the host at teardown, which
doesn't count as a restart and leaves an already finished recording unchanged:

```bash
python <dir>/harbor_target_trajectory.py finish --agent-dir <agent logs> \
  --run-id <trial> --task-id <task> --status completed
```

Customer evidence stays under `user-agent/`. Simulator messages, usage, and
logs are never target evidence.

## Prove it

Report runner status in three distinct states. Don't merge them.

1. **Runner created:** files exist and load.
2. **Offline checks passed:** controls exercise invocation, turn completion,
   cancellation, and ATIF export without a model-backed agent run.
3. **Native execution verified:** an authorized trial ran the actual agent, and
   its export passes:

   ```bash
   python <eval-author>/scripts/harbor_target_trajectory.py check \
     --trial-dir <job>/<trial> --agent-name <agent> --task-id <task>
   ```

   For conversation, also check exact opening delivery, session continuity,
   fresh-trial reset, private-scenario separation, and action ordering. Bind
   this proof through `selection.bridge`.

Load the export through the audit's normal `--trial-dir` path. Created or offline
runners don't authorize agent runs; execution still needs its own checkpoint.

## Hand off when it isn't simple

When an eligibility check fails, save `target_action: "handoff"` with the unmet
check in `reason`. Execution then uses the
[external engineering handoff](conversations/first-eval.md#missing-actual-agent-integration-handoff),
whose prompt carries these same ATIF and conversation requirements.
