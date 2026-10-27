# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute a configured benchmark through reusable stage libraries."""

import argparse
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

from harbor.models.job.config import JobConfig

from metric_benchmark.config import load_benchmark
from metric_benchmark.correlation import correlate
from metric_benchmark.coverage import invoke_coverage
from metric_benchmark.execution import benchmark_identity, execute_benchmark, run_harbor
from metric_benchmark.io import cli, digest_file, read_json, write_json
from metric_benchmark.models import ExecutionManifest, RawResults, resolve_evidence
from metric_benchmark.scoring import compute_mutant_scores, identity
from metric_benchmark.validation import selection_for_results, validate_experiment_results


def implementation() -> dict:
    root = Path(__file__).parent
    return {
        "sources": {p.name: digest_file(p) for p in sorted(root.glob("*.py"))},
        "harbor": version("harbor"),
        "pydantic": version("pydantic"),
    }


def run_experiment(
    benchmark: Path,
    run_id: str,
    output: Path,
    *,
    results_root: Path | None = None,
    cached_only: bool = False,
    allow_unsigned: bool = False,
    runner: Callable[[JobConfig], None] = run_harbor,
) -> dict:
    """Run execution, validation, scoring, coverage and correlation.

    With results_root, only post-execution work runs. cached_only additionally
    prohibits new coverage-system invocations. No stage CLI is used internally.
    """
    loaded = load_benchmark(benchmark, allow_unsigned=allow_unsigned)
    benchmark_sha256 = benchmark_identity(loaded)
    if cached_only and results_root is None:
        raise ValueError("cached_only requires an existing results_root")
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    try:
        write_json(
            output / "experiment-inputs.json",
            {
                "benchmark_sha256": benchmark_sha256,
                "benchmark": loaded.definition.model_dump(mode="json"),
                "run_id": run_id,
                "implementation": implementation(),
            },
        )
    except ValueError as error:
        raise ValueError(
            f"{error}; benchmark, run ID or implementation differs. "
            "Use a new --output directory and --results to reuse saved executions."
        ) from error
    if results_root is None:
        results_root = output / "execution"
        execute_benchmark(loaded, run_id, results_root, runner=runner)
    manifest = ExecutionManifest.model_validate(read_json(resolve_evidence(results_root, "execution.json")))
    if manifest.run_id != run_id or manifest.benchmark_sha256 != benchmark_sha256:
        raise ValueError("execution manifest does not match benchmark/run identity")
    validation = validate_experiment_results(loaded, results_root)
    write_json(output / "validation.json", validation)
    prepared = []
    # Freeze and validate every case before invoking any coverage system.
    for ordinal, prepared_case in enumerate(loaded.cases.values()):
        case = prepared_case.case
        index = resolve_evidence(results_root, manifest.cases[case.case_id])
        raw = RawResults.model_validate(read_json(index))
        destination = output / "analysis" / f"case-{ordinal:04d}"
        write_json(destination / "selection.json", selection_for_results(case, raw, loaded.definition.scoring))
        write_json(destination / "coverage-config.json", loaded.definition.coverage)
        write_json(destination / "subsets.json", loaded.subsets[case.case_id])
        write_json(destination / "case-description.json", loaded.descriptions[case.case_id])
        prepared.append((prepared_case, raw, index.parent, destination))
    results = {}
    for prepared_case, raw, native, destination in prepared:
        case = prepared_case.case
        subsets = loaded.subsets[case.case_id]
        selection = selection_for_results(case, raw, loaded.definition.scoring)
        mutants = compute_mutant_scores(raw, subsets, selection)
        write_json(destination / "mutant-scores.json", mutants)
        coverage = invoke_coverage(
            raw,
            subsets,
            selection,
            loaded.definition.coverage,
            loaded.descriptions[case.case_id],
            native,
            destination / "coverage-evidence",
            cached_only=cached_only,
        )
        write_json(destination / "coverage-scores.json", coverage)
        correlation = correlate(coverage, mutants, missing_policy=loaded.definition.missing_policy)
        write_json(destination / "correlation.json", correlation)
        results[case.case_id] = {
            "correlation": (destination / "correlation.json").relative_to(output).as_posix(),
            "sha256": identity(correlation),
        }
    # A trusted adapter can access the host. Detect input/evidence modification
    # before publishing experiment completion, including changes to earlier cases.
    if benchmark_identity(load_benchmark(benchmark, allow_unsigned=allow_unsigned)) != benchmark_sha256:
        raise ValueError("benchmark inputs changed during execution")
    if (
        ExecutionManifest.model_validate(read_json(results_root / "execution.json")) != manifest
        or validate_experiment_results(loaded, results_root) != validation
    ):
        raise ValueError("execution evidence changed during analysis")
    for _, raw, native, _ in prepared:
        if RawResults.model_validate(read_json(native / "results.json")) != raw:
            raise ValueError("execution evidence changed during analysis")
    result = {"benchmark_sha256": benchmark_sha256, "run_id": run_id, "cases": results}
    write_json(output / "experiment.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results", type=Path, help="existing execution root; skip agent execution")
    parser.add_argument("--cached-only", action="store_true", help="also prohibit new coverage calls")

    def run(args):
        run_experiment(
            args.benchmark,
            args.run_id,
            args.output,
            results_root=args.results,
            cached_only=args.cached_only,
            allow_unsigned=args.allow_unsigned,
        )

    cli(parser, run)
