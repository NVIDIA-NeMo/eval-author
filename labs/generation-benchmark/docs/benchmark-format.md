<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark format

One experiment keeps the metric layout. `experiment.yml` references `cases.yml`,
`execution.yml`, `analysis.yml`, and `generation.yml`.

```text
experiment.yml
  -> cases.yml
  -> execution.yml
  -> analysis.yml
  -> generation.yml
```

## Experiment

The generation benchmark's own `sha-sign` pins all four references. See the
metric benchmark's [sha-sign](../../metric-benchmark/docs/sha-sign.md) for the
signing style.

The case directory is read-only. See [invariants](architecture.md#invariants).
B is the prepared local task inventory: task IDs, revisions, and content
hashes. Preparation that resolves a dataset selector stays outside the five
stages. G's network cannot fetch that selector.

`analysis.yml` is the metric experiment's file. Generation reads its fixed
subset manifest and ignores its mutant scoring. Agent A is the case baseline.
The mutant cohort does not run. `coverage.methods`, the images, the audit
source, and the generator settings live in `generation.yml`. The shared
analysis file keeps whatever methods the metric experiment measures.

## Images

The user configures `images.m_base` and `images.g_base`. Each has the same
shape. Each value is an image digest, or a Dockerfile plus a context pin.
[images](architecture.md#images) describes what each base must already contain.

## Eval Author pins

`eval_author.m` and `eval_author.g` are the source digests of the skills copied
into each image.

## Execution

H(B) and H(Gᵢ) are the runs of A that this benchmark executes. Their Harbor
settings live in `execution.yml`, as they do in the metric benchmark.
`generation.yml` does not name a Harbor version, retries, or agents.

```yaml
harbor_version: "0.20.0"
retries: 1
n_concurrent_trials: 1
adapter: local
pool: 4
```

`harbor_version` is the pin for the adapter and for the Harbor venv in the
reference G image. The initial value matches the metric benchmark. `retries`
is how many times Harbor retries an exception. `n_concurrent_trials` is the
width stage 1 and stage 4 request when they run H(B) and H(Gᵢ). `adapter` is a
URL, or `local` to start the adapter on the host. `pool` is that process's slot
limit when `adapter` is `local`. A URL points at an adapter that already has
its pool. The benchmark does not set a second size. The
[Harbor adapter](architecture.md#harbor-adapter) defines how the pool admits
G jobs and that width.

Per-trial timeouts and turn limits live in A's Harbor config: agent timeout,
`timeout_multiplier`, `max_timeout_sec`, and agent `kwargs`.

A G job's agent must be `oracle`, `nop`, or the baseline `agent_bundle_id`
from `agent.yml`. A generator does not declare those names. The adapter rejects
a model or trial config that differs from `agent.yml`. H(B) and H(Gᵢ) name A
in the Harbor job the stage CLI builds. Harbor loads that class in the adapter
process.

## Agent

`cases.yml` points each case at an `agent.yml`. That file is the metric
benchmark's [agent manifest](../../metric-benchmark/docs/benchmark-format.md#agents-agentyml).
A is the `baseline` entry. The manifest still lists at least one mutant.
Generation runs the baseline only.

Harbor loads the agent class in the adapter process, on the host. The class's
`install` copies the agent program into the task container. `agent.yml` names
that class and pins the files `install` reads.

```yaml
baseline:
  agent_bundle_id: rho-agent
  revision: '1'
  inputs:
    source:
      path: agents/rho-agent
      kind: directory
  harbor:
    import_path: rho_agent.eval.harbor.agent:RhoAgent
    model_name: openai/gpt-5.2
    kwargs:
      install_source: local
      source_dir: "{input:source}"
mutants:
  - agent_bundle_id: nop
    revision: '1'
    harbor:
      name: nop
```

`path` is relative to the directory that contains `cases.yml`. The adapter
replaces a harbor value that is exactly `{input:source}` with the absolute
path of that directory. A signed case also checks that directory's digest.
`RhoAgent.install` tars that path and uploads it into the task container. The
adapter process must already be able to import `rho_agent`. A built-in name
such as `oracle` needs no `inputs`: Harbor already provides that class.

The same launch as a local Harbor trial is:

```text
harbor trial start -p <task> \
  --agent rho_agent.eval.harbor.agent:RhoAgent \
  --model openai/gpt-5.2 \
  --agent-kwarg install_source=local \
  --agent-kwarg source_dir=<absolute path of inputs.source>
```

`agent.yml` stores those fields under `harbor` and pins `source_dir` so the
case identity covers the uploaded tree. The adapter and the tree are on the
same host.

## Audit

```yaml
audit:
  items_from: items_file
```

The audit settings include an ETHOS pin. `audit.items_from` is `items_file` or
`baseline_traces`, and `items_file` is the default. An items pin is required in
`items_file` mode. There is no `author_command`.
[audit rendering](architecture.md#audit-rendering) describes what the two modes
mean.

## Coverage

```yaml
coverage:
  methods: [tool_calls]
  score_scope: overall
```

`coverage.methods` must be `[tool_calls]`. `coverage.score_scope` is `overall`.
The loader rejects any other method and any non-tool item. See
[coverage adapter](architecture.md#coverage-adapter).

## Generation

`generation.yml` declares G. It supplies the in-container argv, the timeout,
the names of the environment variables to pass through, the provider host for
the relay, and the generator config pin. Stage 3 runs that argv. It does not
require a Harbor agent before the process starts. An optional attempt cap is a
run flag, not part of the benchmark definition. [Stages](stages.md) gives its
receipt rule. The [generator contract](architecture.md#generator-contract) and
[network](architecture.md#network) sections cover the rest.
