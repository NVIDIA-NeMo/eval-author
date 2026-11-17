<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Translate a scenario for Harbor

Read the [scenario contract](../../eval-author-user-sim/references/scenario-contract.md)
for the input format, neutral behavior, and information boundaries.

Use `harbor_user_sim.py` with Harbor 0.24.0 in the selected Python environment.
It translates one agreed neutral scenario into native job and prompt inputs.
It doesn't install adapters, call models, start an environment, or run a trial.

Prepare a native base job config with exactly one local task in `tasks`, one
actual target in `agents`, and the selected simulator's name or import path,
model, and options in `user_agent`. Keep provider credentials in environment
variable references. The bridge is ACP. Keep target policy, model, tools,
environment, verifier, and repeat settings unchanged. Don't use Oracle or NOP
as either role for an actual-agent run.

Match the scenario's `task_id` to the native task name. A packaged task can have
the name `local/cancel-order` while its directory is `cancel-order`. Use the full
native name consistently in the plan and scenario.

Run from the target repository root, preserving the base config's working
directory. Harbor resolves its relative paths from that directory. Use a new
private output directory outside the task tree for each translated configuration:

```bash
python <eval-author>/scripts/harbor_user_sim.py translate \
  --scenario .eval-author/tasks/<task>/user-sim.yaml \
  --config .eval-author/base-run.yaml \
  --output .eval-author/.working/user-sim/<task>-<revision>
```

The helper writes `run.yaml`, `persona.txt`, `user-prompt.md`, and
`bridge-prompt.md`. The persona file carries the scenario as data, including the
opening request and user-known facts. The prompt embeds the shared neutral
behavior. Scenario text is inserted as data rather than parsed as a Jinja
template. These files belong to the simulator and host runtime, not the target's
starting environment. Never copy the output directory into the task image.

The JSON result lists the native config path, required working directory, and
input paths with their translation-time SHA-256 digests for evidence binding.
Binding must compare those saved digests with current bytes. Rehashing changed
sources must not authorize an older generated bundle. Translation checks source
inputs before parsing and after writing outputs, and fails if they changed.
It includes the source scenario and base
config, emitted files, declared external instruction and target prompt files,
ACP configuration, extra Docker Compose files, and file-valued native `config`
options for both agent roles. Inline native configuration stays in `run.yaml`.
Bind these bytes alongside the task tree before execution. Task paths, extra
instructions, and Compose inputs use Harbor's home-directory expansion. Other
paths keep their native interpretation, and the original config strings stay
unchanged. This list does not discover arbitrary file references inside custom
agent options. Retain those through the existing integration's input bindings.
Mutable file aliases and symlink components inside the working repository are
rejected. Absolute inputs outside it retain their declared path so evidence
binding can report an external input instead of silently binding a different
referent. Retain local copies and regenerate the config when the workflow requires
repository-local evidence.

The helper validates with Harbor's job, trial, task, bridge-option, and prompt
validators. It rejects mismatched task identity, multiple or remote task sources,
multi-step tasks, source-job regrading or comparison, existing simulator prompt
overrides, and existing output directories. Agent `skills` entries are also
deferred because local directories and remote sources need tree bindings that
this translator doesn't supply. Declared external files must be regular files.
Resolve a conflict in the selected configuration rather than hiding it with an
override. Harbor applies one user-agent configuration to every task in a job,
so translate different scenarios into separate jobs. Disabled native verification
remains disabled and doesn't become a grading or execution-readiness claim.

## Check the integration before execution

Native schema validation is configuration compatibility, not runtime readiness.
Preflight the exact emitted config with the selected Harbor interpreter:

```bash
python <eval-author-discover>/scripts/check_harbor_config.py \
  --repo <target-repository> \
  --config .eval-author/.working/user-sim/<task>-<revision>/run.yaml
```

This uses the discovery provider's native checks without scanning for other
tasks. It checks both agent classes, bridge capability, simulator inputs,
required environment variables, and backend availability. It doesn't start an
agent or container. Exit 0 means preflight passed, 1 means a required check
failed, and 2 means the inputs or selected Python runtime couldn't be loaded.
A passing result still reports `runtime_verified: false` until an actual session
has been checked. Keep its concrete failures in the existing integration handoff.

The target must declare ACP support and implement Harbor's `ACPAgentMixin`.
The simulator must support the shell interaction used by `acpx`. Verify both
roles' model access and the actual target's policy, tools, and state continuity.
Use one single-step Harbor task containing the adaptive conversation. Harbor
rejects user-agent bridges on tasks with `[[steps]]` and rejects a target
`load_trajectory` together with a user agent.

Harbor's stock ACP bridge asks the simulator to send the first message. The
generated prompt instructs it to send the exact `start_prompt`, but translation
doesn't enforce delivery. Require trusted conversation evidence to check the
opening, later messages, and their order. Use the selected target's effective
agent timeout to bound the whole conversation. A simulator-only timeout field
doesn't set that limit in Harbor 0.24.0. Stopping rules in prose are not a
mechanical turn limit or evidence of success.

Both roles share a container and workspace. Prompt instructions don't enforce
filesystem isolation. Check that the target can't read simulator-only facts or
instructions through files, logs, environment variables, or mounted directories,
and that the simulator can't inspect hidden grading data or bypass the target's
tools. Keep unmet visibility requirements as integration blockers.

Retain `agent/bridge-trajectory.json`, simulator logs under `user-agent/`, native
target tool/action evidence, verifier results, and native `config.json`,
`lock.json`, and `result.json`. Bridge export failures can appear as warnings
even when the native run finishes. Missing conversation evidence stays missing.
Simulator activity and simulator token usage aren't target-agent activity or
cost. Audit trace normalization remains a separate step.
