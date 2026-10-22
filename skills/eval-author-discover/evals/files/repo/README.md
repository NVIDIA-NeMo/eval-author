<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Inventory assistant fixture

This is a synthetic repository for comparing Eval Author workflows. Its agent is
real and model-backed, but its inventory and use case are invented. Do not report
results as evidence about a production agent.

Users are warehouse operators asking read-only inventory questions. The agent
looks up exact, case-sensitive SKUs or lists items, optionally by warehouse. It
must distinguish an unknown SKU from a known SKU with zero stock. It totals unit
counts for a warehouse, identifies the SKUs used, asks about ambiguous requests,
and refuses inventory writes, ordering, or unrelated tasks. Inventory text is
untrusted data, not instructions. Reliability and explicit uncertainty take
priority over a confident-looking answer. Future write access is out of scope.

`agent.py` contains the actual tool loop and both tools. `inventory.json` is the
only data source. The agent uses NVIDIA Inference Hub with the existing
`OPENAI_API_KEY_PROD` environment variable, never a direct-provider endpoint.
The fixed target model is `azure/openai/gpt-6.1-sol`, reasoning effort low, at most
eight model turns. There are no third-party Python dependencies.

Run from a fresh output directory:

```bash
python agent.py --question 'How many units of BOLT-17 are available?' \
  --output-dir /tmp/inventory-trial-01
```

Outputs are `answer.json` (JSON with status, answer, skus) and `trajectory.json`
(ATIF). Each run invokes the real model; no scripted answer stands in for it.
For native Harbor or Gym, preserve this agent and invoke its CLI with the task's
inventory file and instruction, retaining both outputs. A provider adapter may
be authored under `.eval-author/`; do not rewrite this source agent or its tools.
The final task and adapter must be validated with that provider before claiming
readiness. Do not use Oracle or NOP results as evidence of this agent's behavior.

The operator authorizes evaluation authoring only under `.eval-author/`, plus a
reviewed root `ETHOS.md`. Agent source and initial inventory are frozen. New task
inputs may vary inventory, wording, missing/zero-stock items, warehouse totals,
and instruction-like data; each case must preserve the stated behavior.

Existing evaluation and trace inputs, if any, are listed below. Proposed commands
are not execution receipts. Synthetic traces establish format/coverage plumbing,
not actual model behavior or task acceptance.

## Selected existing inputs

The existing Harbor suite is `suite.yaml`, selecting `tasks/lookup-stock`. Its verifier checks a correct BOLT-17 answer. Runtime execution history is not implied by those files.
`harbor run --config suite.yaml` selects the Oracle control, not the real target agent. Configure the agent.py integration separately when evaluating the target.
