<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Reference generator implementation

This page specifies the v0 metric and the v0 task generator. The experiment takes any metric and generator command that meets the contracts in [architecture](architecture.md).

v0 points `metric.setup.command` and `metric.score.command` at one executable. The setup invocation passes `--setup`. The score invocation does not.

## Metric setup

`--setup` renders `D` for `tool_calls` into `cases/case-NNNN/metric-setup/`. `request.json` names the case's ETHOS and items files. The command copies the ETHOS to `ETHOS.md` in that directory, then runs:

```text
uv run --with pyyaml --with jsonschema skills/eval-author-audit/scripts/audit_spec/generate.py \
  --ethos ETHOS.md --items <items from request.json> \
  --out audit.md --mode replace --items-mode full
```

The command copies those inputs. It does not generate items from ETHOS. Its items file is the case setting `settings.items` in `cases.yml`. Every item has `kind: tool`. `sources[0].path` is `../ETHOS.md`. The command checks the audit it wrote. It records the digests of `audit.md`, ETHOS, the items file, and the argv. It reuses that audit only when the receipt matches and those bytes are unchanged. Otherwise it fails with `metric-setup changed; use a new work root`.

## Metric command

Without `--setup`, the executable reads the `metric-setup/` path named in the request, when setup is declared, and `workspace/trials/`. It writes `output/score.json` and its other output, including the gap report. [Host measurement](architecture.md#host-measurement) passes that output's path to the generator. It does not write a new audit.

## Generator

The entry point is `labs/generation-benchmark/eval_author_generation.py`. It
starts a Harbor sidecar and a container, waits for the container, and stops
both before it exits. The container runs `eval-author-task-create`. The sidecar
runs the Harbor trials that container requests. The orchestrator does not
configure either process. On timeout it signals this implementation's process
group.

The container name is
`eval-gen-case-NNNN-<subset-dir>-<first 12 hex of the request digest>`.
After every exit the entry point runs `docker rm -f` on that name.

## Image

The image is built from the user's coding-agent base. It copies
`skills/eval-author`, `skills/eval-author-audit`,
`skills/eval-author-task-create`, and `skills/eval-author-environment`, and
sets the label `org.nvidia.eval-author.generator-source-sha256` to
the `glob` digest of those trees. The experiment's `generation.digest` uses
`method: glob` with `patterns` that name the four trees
(`<eval-author checkout>/skills/<name>/**`), so the label and the lock's
`generation.digest` entry hold the same value. The build warms the uv cache for
every inline-metadata script and `--with-requirements` file those skills
invoke, then sets `UV_OFFLINE=1`. A missing package is an image-build bug.
The image installs Harbor 0.24.0 at `/opt/harbor-real`, off `PATH`, and the
shim first on `PATH`. The base already contains the coding agent. This
implementation does not install it.

`generation.settings.image_id` (`sha256:<64 hex>`) is required. `sign-generation`
checks that the Docker image exists and that its label equals
the lock's `generation.digest`. Otherwise it fails with
`G image <id> missing or built from different skills; rebuild and re-sign`.
The entry point checks that `docker image inspect` reports that id and that the
label matches `generation.digest`. A mismatch writes `image_mismatch` to
`response.json` and exits 2. No container starts. `image_id` is part of
`generation.settings`, so it is in `configuration_sha256`.

## Settings

The contract leaves `generation.settings` freeform. This implementation uses:

```yaml
settings:
  image_id: sha256:<64 hex>
  prompt:
    path: prompt.md
  operator_answers:
    path: operator-answers.yaml
  entrypoint: [python3, /opt/generation/entrypoint.py]
  agent_argv: [codex, exec, --json, --skip-git-repo-check, -s, danger-full-access, -C, /workspace, -]
  provider_env: [CODEX_API_KEY]
  network: default                    # or internal
```

`prompt` and `operator_answers` are file references, so the lock pins them and a changed file reruns generation. `entrypoint` is the container entrypoint. `agent_argv` is the coding agent's argv. `provider_env` names the host variables passed into the container. `network` is `default` or `internal`.

## Operator answers

`operator-answers.yaml` has schema `nemo.eval_author.generation_operator_answers.v1`:

```yaml
schema: nemo.eval_author.generation_operator_answers.v1
ethos_confirmation:
  source: <text>
  validation: <text>
journey_update:
  prerequisites:
    code_access: {status: confirmed, source: <text>}
    file_writes: {status: confirmed, source: <text>}
    command_execution: {status: confirmed, source: <text>}
    skills: {status: confirmed, source: <text>}
    ethos_skills: {status: confirmed, source: <text>}
    environment: {status: confirmed, source: <text>}
    services: {status: confirmed, source: <text>}
    data: {status: confirmed, source: <text>}
    agent_connection: {status: confirmed, source: <text>}
    model_credentials: {status: confirmed, source: <text>}
  scopes:
    expand:
      request: <text>
      corpus: {source: .eval-author/audit-coverage-report.json}
      priorities: <text>
      cost_constraints: <text>
      examples_per_behavior: 1
      variations_and_difficulty: <text>
      scenarios: {positive: <text>, negative: <text>}
      expected_outcomes: <text>          # verifier grades the task outcome, not the tool call
      attempts_per_task: 2
      pilot_or_expansion: pilot
      authorizations:
        docker: true
        harbor: true
        real_agent_runs: true
        model_calls: true
      decision_source: <text>
      confirmed: true
      needs_review: false
  journey_status: {expand: in_progress}
  blockers: []
  pending_question: null
  next_handoff: eval-author-task-create
authorizations:
  docker: true
  harbor: true
  real_agent_runs: true
  model_calls: true
```

`sign-generation` fails when `authorizations` differs from `journey_update.scopes.expand.authorizations`. Both copies are staged for the command.

## Launcher

The launcher runs on the host.

1. Check the image, as above.
2. Start the sidecar with a new 32-byte token. It binds `127.0.0.1` on macOS
   and the Docker bridge gateway on Linux, on a free port. A failed start
   writes `adapter_failed` and exits 2.
3. Run the container. Mount the staged workspace read-only at `/input` and
   the output directory read-write at `/output`. Mount `request.json`
   read-only at `/request.json`, outside the workspace. Pass the sidecar URL,
   the token, and the provider variables named in `generation.settings`.
   Mount nothing else: no repository and no Docker socket.
4. On SIGTERM, stop the container within 30 seconds, stop the sidecar, and
   exit 143.

## Container layout

```text
/input/                                   # read-only mount of the staged workspace
/request.json                             # read-only; not under /workspace
/workspace/                               # entrypoint copy of /input
  ETHOS.md
  .eval-author/audit.md                   # D; sources[0].path == ../ETHOS.md
  .eval-author/jobs/baseline/<task_id>/   # H(Bᵢ) trial directory
  tasks/<task_id>/                        # Bᵢ
  agent/                                  # A's source, after excludes; absent if A has none
  .eval-author/.working/environments/<agent-slug>/
  .generation/prompt.md
  .generation/operator-answers.yaml
/output/                                  # the container writes here
/logs/                                    # attempt logs, under /output/logs
```

The launcher also mounts `H(Bᵢ)` and the audit in `metric-setup/`. Those files are not part of the staged workspace. The entrypoint copies `/input` to `/workspace`, adds the mounted trials and audit, and reads the gap report. Host measurement trees are not mounted. `measure.py` and `report.py` remeasure `Bᵢ` for task-create. The host score stays the stage 2 score. Before authoring, this implementation checks `baseline-report.json` against the gap report's covered set.

## Sidecar

The sidecar is a host process. The launcher starts it for one run. It serves the Harbor trials the container starts, one job at a time. A second submission waits until that job finishes.

One random token authenticates the run, in the `Authorization` header. The
launcher revokes it when the run ends. A sidecar restart fails the run. The
subset becomes `unavailable`. A rerun deletes that subset's directory and
starts fresh.

| Endpoint | Purpose |
| --- | --- |
| `POST /jobs` | One task bundle tar plus `{name, agent, model?, config?, client_task_path, client_trials_dir}`. Returns when the job is queued |
| `GET /jobs/{name}` | Status and exit code |
| `GET /jobs/{name}/artifacts` | The trial directory, with agent environment values removed |

`POST /jobs` is idempotent on the token and `name` when the bundle digest
matches. The same name and the same digest return the existing job. A
different digest for that name is rejected. The shim writes into
`<trials_dir>/<trial_name>/`, which the client has already created, so a
repeated submit with the same digest must not write that directory again.

The sidecar checksums each bundle with the Harbor it checked at startup. The
job request does not include a Harbor version.

**Agents.** `agent` must be `A`'s `agent_bundle_id`, `oracle`, `nop`, or
`generation_controls:ScriptControl`. That class is code in this
implementation, imported on the host. The container writes the wrong answer as
a nonempty file under the task's `private/` directory. The class runs that
file inside the trial container, where Harbor runs `solve.sh`. The container's
Python is never imported on the host. An arbitrary `-a` is rejected before
Harbor starts, because Harbor would load that class in the sidecar process.
`A`'s model and trial config come from `agent.yml`. A supplied model or config
that differs is rejected.

The prompt names `generation_controls:ScriptControl`. The coding agent passes
that string as `-a` and as `--control-agent`, and passes the script path as
`--control-source`. `task_evidence.py` checks the path and the sha256. It
does not import the class.

**Bundle validation.** The sidecar rejects a bundle that contains any of:

- `environment/docker-compose.yaml` or `.yml`
- a `${…}` template in `[environment].env`, `[verifier]`, or `[solution]` of
  `task.toml`
- `mcp_servers`
- a prebuilt `docker_image`
- the session token

Stage 4 repeats the first four checks and records a failure. Refer to
[stage 4](stages.md#stage-4-run-and-measure). The sidecar does not accept a host
path or a shell command as a job field. Validation may reject a bundle. It
does not rewrite or strip files.

**Trial directory.** The shim returns a directory with these properties. Path
rewriting is optional.

- The trial runs on the bundle's full file set, with the same names and bytes,
  including dotfiles.
- The shim writes into `<trials_dir>/<trial_name>/`, which the client has
  already created, including `agent/` for an agent run.
- Every edit finishes before the shim exits. Task-create hashes `result.json`
  and `trajectory.json` at that moment.
- Scrubbing does not change `id`, `task_checksum`, `exception_info`,
  `verifier_result`, `config.agent.name`, or `config.agent.import_path`.
  Agent environment values are removed.

Harbor runs with the agent's own environment and nothing else from the sidecar
process. The bundle tar's top directory is the basename of the task path the
client used. The sidecar unpacks it under that same name.

## Shim

The shim is part of this image. It is not part of the generator contract.
Task-create calls the `harbor` executable. A different generator does not use
the shim.

The shim is tested against Harbor 0.24.0 and refuses to run when the installed
Harbor is a different version.

| Command | Behavior |
| --- | --- |
| `harbor trial start -p <task> -a <agent> --trial-name <n> --trials-dir <d>` | Tar the task under its basename, `POST /jobs` as a trial named `<n>`, block until it finishes, unpack the artifacts into `<d>/<n>/`, and exit with Harbor's code |
| `harbor run -p <task> -a <agent> --jobs-dir <d> --job-name <n>` | The same, as a job, unpacked into `<d>/<n>/` |
| `harbor task init` and other subcommands that need no Docker | Pass through to `/opt/harbor-real/bin/harbor` |
| `harbor view` and any other subcommand that needs Docker or a server | Exit non-zero with a message that names the sidecar |

The shim accepts `-p`/`--path`, `-a`/`--agent`, `--trial-name`, `--trials-dir`,
`--job-name`, and `-o`/`--jobs-dir`. It forwards `-m`/`--model` and
`-c`/`--config` when the command includes them. The sidecar checks those
values against `agent.yml`. Any other flag is rejected before submission.
That covers host-escape flags (`--mounts`, `--mounts-json`,
`--extra-docker-compose`, `--allow-environment-host`, `--allow-agent-host`)
and `--ae`/`--agent-kwarg`. `-a` must be `A`, `oracle`, `nop`, or
`generation_controls:ScriptControl`.

A bare `harbor` always reaches the shim. Python imports are unaffected. The
scripts inside the container import `harbor.models.task.task.Task` for
checksums. That import needs no Docker. The coding agent runs
`task_evidence.py` with `/opt/harbor-real/bin/python`.

## Entrypoint

The entrypoint runs inside the container, in this order.

1. Copy `/input` to `/workspace`. Delete a copied journey state and its lock
   if they were in the workspace. Journey state binds the absolute repository
   path, so a host copy makes `init` fail.
2. Seed journey state with `journey_state.py` inside the container. Run
   `locate_runtime.py` so `python` and `runtime` are `verified`. Apply
   `--journey expand`. `operator-answers.yaml` supplies the ETHOS confirmation
   and the `journey_update` payload. The payload's `authorizations` match the
   file's top-level `authorizations`. The prompt quotes the top-level copy.
3. Run `measure.py` and `report.py` on the copied `Bᵢ` trials. Write
   `.eval-author/audit-coverage-report.json` and copy it to
   `/output/baseline-report.json`. When the covered set differs from
   `/request.json`, write `baseline_measurement_mismatch` and exit 3.
4. Run `task_pipeline.py select` on that report. Actionable tools are sorted
   by name and truncated to `max_gaps` when that field is set. Snapshot
   `.eval-author/`.
5. For each tool, fill the prompt, run the coding agent with the prompt on
   stdin and cwd `/workspace`, and restore the snapshot before the next tool.
   The prompt names `generation_controls:ScriptControl` and tells the agent to
   pass it as `-a` and `--control-agent`, with the wrong-answer script as
   `--control-source`. The agent's argv is `generation.settings.agent_argv`.
   For Codex that is
   `codex exec --json --skip-git-repo-check -s danger-full-access -C /workspace -`.
   `workspace-write` would block the shim's calls to the sidecar. The
   container is already the sandbox.
6. Copy a task to `/output/tasks/<slug>/` when task-create records it
   accepted. This copy rule is this implementation's. Stage 4 receives every
   directory under `output/tasks/`. Each attempt line in `logs/attempts.jsonl` includes
   the journey's `pending_question`.
7. Write `/output/response.json` and exit 0.

## Network

The container uses Docker's default network and reaches the model provider
directly. `settings.network: internal` fails with
`internal network requires the relay (Q1)` until the relay exists.

The relay is the network for a case whose tasks or repository can be fetched
from the network. The container then runs on an internal network whose only
extra peer forwards to the sidecar and to the provider addresses pinned for
that run. Trial containers the sidecar starts have open egress. Restricting
that egress is part of the same later work.

The provider variables named in `generation.settings` are the only host
credentials that enter the container. `A`'s model credentials stay on the host.
The sidecar runs `A`'s trials.
