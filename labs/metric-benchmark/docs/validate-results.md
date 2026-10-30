<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# validate_results.py

Validate saved Harbor evidence against the prepared benchmark inputs. This command
does not execute agents or compute coverage.

## Usage

From the repository root, invoke the validation CLI with:

```bash
uv run python labs/metric-benchmark/validate_results.py \
  --benchmark experiment.yml \
  --results-root experiment-results \
  --output validation.json
```

Supply these required arguments:

- `--benchmark`: path to the experiment YAML file and its references to prepared inputs.
- `--results-root`: whole experiment output directory containing `execution.json` and the case results directories.
- `--output`: path for the validation report, outside the results root; `validation.json` is an example filename.

By default, the tool validates every configured case. Add `--case case-id` to
validate only that case example, using the same `--results-root` path. The
experiment’s `execution.json` maps each case ID to its results index.

Optionally, add `--allow-unsigned` when input SHA pins are missing during iteration;
any supplied pins are still verified.

The CLI checks all selected cases and writes one report only if every selected
case passes. A filtered invocation uses the same report structure with one case.
Explicitly unavailable observations do not themselves cause validation to fail. If any constraint fails, the command exits nonzero and
no new report is written. An existing output file can be reused only if its
contents are identical; choose a new output path for changed results.

## Python library

`validate_experiment_results(loaded, root, *, case_id=None)` returns the
same report as the CLI. Pass a `LoadedBenchmark` from `load_benchmark` and the
whole experiment output root; `case_id` optionally selects one case.

The per-case `validate_results(raw, case, root, input_root, scoring, *, allow_unsigned=False)`
function remains available for execution and analysis libraries. Its `root` is
that case’s results directory, and it returns the individual case report.

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
