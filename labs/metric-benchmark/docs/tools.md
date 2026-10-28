<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Benchmark tools

These tools consume externally prepared cases. A prepared case contains:

- A baseline agent and its mutant versions, including their executable files and dependencies.
- A description of the agent's intended behavior for coverage computation.
- A fixed collection of evaluation tasks, including their environments and verifiers.
- Named, fixed subsets of those tasks for score and coverage comparisons.
- Manifests connecting these inputs, with relative paths and optional SHA-256 digests for verification.

## Benchmark config

The [benchmark format specification](benchmark-format.md) defines the recommended
directory layout, manifest fields, relative paths, and content pins:

- [Agents](benchmark-format.md#agents-agentyml): baseline, mutants and intended behavior.
- [Tasks](benchmark-format.md#tasks-tasksyml): task inventory and source directories.
- [Subsets](benchmark-format.md#subsets-subsetsjson): fixed task memberships.
- [Cases](benchmark-format.md#experiment-and-inline-cases-experimentyml): inline combinations of agents, tasks and subsets.
- [Experiment](benchmark-format.md#experiment-and-inline-cases-experimentyml): case list and execution/analysis settings.

## Signing a benchmark

Iterate with SHA fields omitted. When the prepared inputs are ready, sign the
bundle from the repository root:

```bash
uv run labs/metric-benchmark/tools/sha-sign benchmark/experiment.yml
```

`sha-sign` reads the experiment and walks every case's referenced manifests and
inputs. It checks paths, file types, schemas and subset task IDs before writing
anything. It adds `sha256` to task sources, agent inputs, descriptions, subsets,
and agent/tasks manifest references, working from input files up to the
experiment. Shared manifests are updated once. It does not run agents or the
metric tool, and it does not modify task files, agent files or JSON inputs.

If any reference already has a SHA field, signing stops without writing files
and lists those fields' locations. Delete those fields, or explicitly re-sign
the current contents with `--resign`:

```bash
uv run labs/metric-benchmark/tools/sha-sign benchmark/experiment.yml --resign
```

`--resign` replaces old pins, including ones that no longer match edited inputs.
Signing edits only SHA fields, preserving comments, key order, quoting and
formatting elsewhere. New SHA fields are inserted into their reference mapping;
existing pins keep their plain or quoted scalar style. Block-scalar, anchored or
tagged SHA values must be changed to plain or quoted scalars before re-signing.
The input files should not be edited while signing. Inputs cannot include
manifests that the signer itself rewrites, since doing so would invalidate their
hashes.

Once signed, readers verify each supplied digest as they load its reference.
`load_benchmark()` verifies all inputs; `read_benchmark()` verifies the manifests
it reads, and individual artifact loaders verify their own inputs. Omitting a
digest requires explicit `allow_unsigned=True` when loading during iteration.
SHA signing fixes expected contents; it does not authenticate a publisher.

To check that the entire benchmark is signed correctly without changing files:

```bash
uv run labs/metric-benchmark/tools/sha-sign benchmark/experiment.yml --check
```

`--check` requires a SHA on every input reference and verifies it against the
current contents. It stops at the first validation error, including a missing,
null or incorrect digest, and reports the input context. It uses the same strict
`load_benchmark()` path as normal full loading. To fill missing pins throughout
an unsigned bundle, run signing without `--check`; use `--resign` if some pins
already exist. Success exits with status zero; validation failures exit nonzero.
`--check` and `--resign` are mutually exclusive.
The top-level experiment has no self-hash, and inline cases have no separate
hash: their agent, tasks and subsets references are checked instead.

To sign or check from Python:

```python
from pathlib import Path
from metric_benchmark.signing import check_benchmark, sign_benchmark

experiment = Path("benchmark/experiment.yml")
changed_files = sign_benchmark(experiment, resign=False)
check_benchmark(experiment)
```

`sign_benchmark()` returns the paths of YAML files it changed.
`check_benchmark()` returns normally on success and raises on missing or invalid
pins or inputs. Re-signing does not require existing pins to match.

## Reading benchmarks

Unpack a benchmark bundle and pass its `experiment.yml` to the Python library.
From the repository root:

```bash
PYTHONPATH=labs/metric-benchmark uv run python - <<'PYTHON'
from pathlib import Path
from metric_benchmark.config import load_benchmark

benchmark = load_benchmark(Path("benchmark/experiment.yml"))
prepared = benchmark.case("rho-agent")
subsets = benchmark.subsets["rho-agent"]
PYTHON
```

`load_benchmark(path)` is the full entry point: it reads the experiment, verifies
all referenced manifests, task sources and agent inputs, and loads every case's
subsets and description. The returned object holds the experiment definition,
resolved cases, subsets, and descriptions. Loading requires SHA-256 digests by
default. During iteration, explicitly opt in to unsigned inputs:

```python
benchmark = load_benchmark(Path("benchmark/experiment.yml"), allow_unsigned=True)
```

This permits missing pins but still verifies every supplied digest. The lighter
`read_benchmark()` and individual artifact loaders accept the same keyword,
also defaulting to `False`. The downstream execution and analysis PRs add
`--allow-unsigned` to their benchmark-reading CLIs; `sha-sign --check` always
requires all pins.

Use `read_benchmark(path)` from the same module when only the experiment and
inline cases and referenced agent/task manifests are needed. It does not open
task sources, agent bundles, subsets, or descriptions. With this lighter reader,
load a selected case's subsets or description through
`prepared.case.load_subsets(prepared.root, allow_unsigned=prepared.allow_unsigned)` or
`prepared.case.load_description(prepared.root, allow_unsigned=prepared.allow_unsigned)`;
each verifies the referenced artifact and requires a digest unless unsigned
loading was explicitly enabled.

The [format specification](benchmark-format.md) describes validation rules.
`BenchmarkDefinition`, `CaseDefinition`, `AgentManifest`, and `TaskManifest` also
expose their format through `model_json_schema()`.

## Validate a results directory

See [Generating the report](validation-report.md#generating-the-report) for the
CLI invocation, required arguments, and output behavior.

Library: `validate_results(raw, case, root, input_root, scoring, *, allow_unsigned=False)`.
It returns the report as a dictionary after all validation checks succeed.

The [results format specification](results-format.md) defines the index, native
evidence requirements and matrix accounting. The [validation report](validation-report.md)
defines the tool’s output. Start with the [mock results](results-format.md#mock-results)
to review the checked-in mock directory.

`test_contract_and_cli_parity` in
[the validation tests](../../../tests/test_metric_benchmark_validation.py)
materializes the synthetic input tasks, copies the mock output to a temporary
directory, and checks that the library and CLI reproduce the checked-in report.
To exercise that example without Docker or model calls:

```bash
uv run --locked pytest tests/test_metric_benchmark_validation.py -k contract_and_cli_parity -q
```
