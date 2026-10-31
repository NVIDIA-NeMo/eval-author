<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared generation cases. A prepared case contains:

- A baseline eval agent, including its executable files and dependencies.
- A description of the agent's intended behavior for coverage computation.
- A fixed collection of evaluation tasks, including their environments and verifiers.
- Named, fixed subsets of those tasks.
- A coverage adapter command and a generation adapter command.
- Pinned sidecar files for the Harbor agent config and the generator config (`generation.yml` `inputs`).
- Manifests connecting these inputs, with relative paths and optional SHA-256 digests.

## Benchmark config

The [generation benchmark format](benchmark-format.md) defines the directory
layout, manifest fields, relative paths, and content pins:

- [Agents](benchmark-format.md#agents-agentyml): baseline eval agent.
- [Tasks](benchmark-format.md#tasks): task inventory and source directories.
- [Cases, execution, and analysis](benchmark-format.md#cases-execution-and-analysis): Harbor settings, subsets, description, and coverage.
- [Generation](benchmark-format.md#generation-generationyml): generation adapter and sidecar pins.

Shared pin rules, task entries, subset JSON, coverage command fields, Harbor
execution settings, and scoring policy match the metric benchmark where those
objects exist. The generation format states which of them this experiment uses.

## Signing a benchmark

Iterate with SHA fields omitted. When the prepared inputs are ready, sign the
bundle from the repository root:

```bash
uv run python labs/generation-benchmark/tools/sha-sign benchmark/experiment.yml
```

`sha-sign` reads the experiment and walks `cases.yml`, `execution.yml`,
`analysis.yml`, and `generation.yml`, including task sources, agent inputs,
descriptions, subsets, and generation sidecar files.
It checks paths and file types before writing. It adds `sha256` from input
files up to the experiment. It does not run agents, coverage, or generation.

If any reference already has a SHA field, signing stops without writing files.
Delete those fields, or re-sign with `--resign`:

```bash
uv run python labs/generation-benchmark/tools/sha-sign benchmark/experiment.yml --resign
```

`--resign` replaces old pins. Signing edits only SHA fields. To check pins
without writing files:

```bash
uv run python labs/generation-benchmark/tools/sha-sign benchmark/experiment.yml --check
```

`--check` requires a SHA on every input reference and verifies it. Success
exits with status zero.

To sign or check from Python:

```python
from pathlib import Path
from generation_benchmark.signing import check_generation_benchmark, sign_generation_benchmark

experiment = Path("benchmark/experiment.yml")
sign_generation_benchmark(experiment, resign=False)
check_generation_benchmark(experiment)
```

`load_generation_benchmark()` verifies pins by default. During iteration, pass
`allow_unsigned=True`. The metric `sha-sign` tool reads metric bundles. Use
this tool for generation bundles.

## Reading benchmarks

```bash
PYTHONPATH=labs/metric-benchmark:labs/generation-benchmark uv run python - <<'PYTHON'
from pathlib import Path
from generation_benchmark.config import load_generation_benchmark

benchmark = load_generation_benchmark(Path("benchmark/experiment.yml"))
case = benchmark.case("case-id")
PYTHON
```

## Run the benchmark

```bash
uv run python labs/generation-benchmark/run_generation_benchmark.py \
  --benchmark /path/to/experiment.yml \
  --case <case_id> \
  --work-root /path/to/work \
  --run-id local-001
```

Repeat `--subset-id <eval_subset_id>` to run one subset. Omit it to run every
subset in `subsets.json`.

Library: `run_generation_benchmark(benchmark, case_id, work_root, run_id, *, cached_only=False, subset_ids=None)`.

The work root receives `execution-results/`, `coverage-baseline/`,
`subsets/<id>/`, `generation-results.json`, and `generation-analysis.json`.

The first run executes the baseline agent on the full inventory. Harbor,
Docker, and the credentials for the pinned generator are required for a live
run. A subset that produces no draft is a result: the driver measures the
subset again and continues. Optional `scripts/run_e2e.sh` runs a smoke or live
check against a prepared experiment. It passes when the driver exits 0 and at
least one subset adds a task that raises coverage. Smoke generation uses
`scripts/smoke_generation_adapter.py`, which copies checked-in Harbor drafts
into the configured `draft_dir`. Set `GENERATION_CASE_ROOT` to the case
directory that holds those drafts.

## Coverage adapter

Same command contract as the metric benchmark:

```bash
uv run python labs/metric-benchmark/eval_author_coverage.py --print-source-digest "$EVAL_ROOT"
```

Pin the command under `analysis.yml` `coverage.command`. See the metric
[coverage tool notes](../../metric-benchmark/docs/tools.md#compute-coverage)
for the request and response files.

## Generation adapter

```bash
uv run python labs/generation-benchmark/eval_author_generate.py \
  --request request.json --output response.json
```

Pin that command under `generation.yml` `command`. `load_generation_benchmark()`
checks `generation.settings.process_sha256` against the command metadata, and
checks `eval_author_root` against `source_sha256`.

`request.json` names the staged paths `input/tasks/`, `input/coverage/`, and
`output/tasks-generated/`, and carries the subset coverage score.
`response.json` lists `new_task_ids` and the expanded-suite digest. The
reference adapter reads `authored/audit.md` and `report.json` from the coverage
work cell. A different generator can ignore those files.

The reference generator config uses `schema: nemo.eval_author.generator.v1`.
The example config pins Codex as a host subprocess: `codex exec` with
`-s workspace-write`, `-c approval_policy=never`, and `--ephemeral`, and with
`-C` set to the generation work cell.

After each generation attempt, logs may appear under the subset work cell:

```text
.eval-author/generator-logs/<slug>.prompt.md
.eval-author/generator-logs/<slug>.stdout.txt
.eval-author/generator-logs/<slug>.stderr.txt
```

`coverage.settings.before_measure_command` runs an external program with
`GENERATION_RESULTS_ROOT` set before coverage measurement.
