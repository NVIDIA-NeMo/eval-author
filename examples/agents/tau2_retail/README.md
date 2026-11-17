<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# τ² retail user-simulation example

This example runs the baseline's unchanged ReAct agent through Harbor's native
ACP bridge. A neutral model user reveals customer facts as the agent asks for
them, then confirms a cancellation. A separate verifier checks the actual tool
call, confirmation order, and final retail database.

The example copies only the reference agent, its license, and the retail policy
and database from `eval-author-baseline/baseline_100/tau2-bench`.
[`upstream/provenance.json`](upstream/provenance.json) records their source paths,
revision, and hashes. The supporting τ² library installs from that exact Git
revision inside the task container. The model remains
`openai/azure/openai/gpt-4.1-mini` at temperature `0.0`, with the baseline's
100-step and 10-tool-error limits. The pinned dependency constraints also come
from the baseline. This example doesn't install those dependencies into the
Eval Author development environment or distribute a built container.

## Run it

You need the repository's development environment, Docker, access to the pinned
GitHub dependency, and a model endpoint that serves the configured model. Follow
[the development setup](../../../DEVELOPMENT.md) to create the root `.venv`.
Set `OPENAI_API_KEY` and `OPENAI_BASE_URL` in your shell. Both roles use that
connection. Keep credential values out of the YAML files and source tree.

From this directory:

```bash
repo_root="$(git rev-parse --show-toplevel)"
export PYTHONPATH="$PWD"
export LITELLM_LOCAL_MODEL_COST_MAP=True
"$repo_root/.venv/bin/python" prepare.py
"$repo_root/.venv/bin/python" "$repo_root/skills/eval-author/scripts/harbor_user_sim.py" translate \
  --scenario .eval-author/tasks/cancel-order/user-sim.yaml \
  --config .eval-author/base-run.yaml \
  --output .eval-author/translated/attempt-1
"$repo_root/.venv/bin/harbor" jobs start -c .eval-author/translated/attempt-1/run.yaml
```

`prepare.py` creates the task, its isolated Docker environments, and a base run
configuration under the ignored `.eval-author/` directory. It refuses to replace
an existing task. The translator also requires a fresh output directory. Keep
these inputs and run outputs together when comparing results.

To run another attempt, give `job_name` a new value in the base configuration and
translate to a new directory. Keep the existing task and previous outputs. Each
Harbor trial starts with a fresh retail database. To change the prepared task,
archive the existing `.eval-author/` directory first, then prepare it again.

## What to inspect

Each trial under `.eval-author/jobs/<job-name>/` contains:

- `agent/bridge-trajectory.json`: Harbor's exported ACP conversation, including
  the stable session identifier and each user request.
- `agent/llm_debug/`: the reference agent's separate reasoning and action model
  calls. These are target evidence, separate from simulator usage.
- `user-agent/conversation.json`: the messages the simulator sent and received,
  plus whether it chose to stop.
- `artifacts/app/output/transcript.json`: the target's conversation and retail
  tool calls, recorded by the adapter.
- `artifacts/app/state/db.json`: the final retail database.
- `verifier/results.json` and `verifier/reward.txt`: independent checks and reward.
- `result.json`: Harbor's trial status, exceptions, and timings. Its token totals
  cover the simulator only. They don't measure the target or total run cost.

The verifier requires at least three customer turns, exactly one correct
cancellation call, a preceding confirmation, a successful tool response, and the
expected cancellation and refund with no other database changes. This scenario
asks the neutral user to reply with exactly `yes` to the correct cancellation
proposal. The confirmation check is deliberately narrow: it checks that reply
and the preceding message's order identifier and cancellation text. It's an
integration check, not a general semantic grader of retail policy compliance.
A simulator's decision to stop does not establish target success.

`NeutralUserHarbor` is an example-specific simulator. It sends the exact opening
request mechanically, then asks its model for each next message or a stopping
decision. It can only send text through `acpx prompt`. It has no model-accessible
file or retail tools, and it cannot issue arbitrary shell commands. The target
receives customer messages and its original retail policy, not the private
scenario or verifier. The two roles share Harbor's environment, so this is not
an operating-system isolation boundary.

This one task establishes a working multi-turn path. It doesn't establish
coverage gains, broad baseline performance, or reproducibility across repeated
model runs. The example uses `local/cancel-order` as both its scenario ID and native task
name. Authored scenarios keep their agreed plan ID, which may differ from the
native name. Translation checks the selected task directory to bind the two.

## Offline checks

From the repository root:

```bash
.venv/bin/pytest -q tests/test_tau2_retail_example.py
```

These tests check source hashes, session restoration, failure cases for the
verifier, and exact simulator message delivery. They use no model calls and
aren't substitutes for the live Harbor run above.
