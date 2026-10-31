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
from metric_benchmark.execution import execute_benchmark, run_harbor
from metric_benchmark.io import cli, digest_file, file_manifest, read_json, write_json
from metric_benchmark.models import ExecutionManifest, resolve_evidence
from metric_benchmark.scoring import compute_mutant_scores, identity
from metric_benchmark.validation import read_case_index, selection_for_results, validate_experiment_results


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
    analysis = loaded.analysis
    analysis_sha256 = analysis.identity
    if cached_only and results_root is None:
        raise ValueError("cached_only requires an existing results_root")
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    if results_root is None:
        results_root = output / "execution"
        execute_benchmark(loaded.execution, run_id, results_root, runner=runner)
        write_json(output / "validation.json", validate_experiment_results(analysis.inputs, results_root))
    manifest = ExecutionManifest.model_validate(read_json(resolve_evidence(results_root, "execution.json")))
    if manifest.run_id != run_id or manifest.cases_sha256 != analysis.inputs.identity:
        raise ValueError("execution manifest does not match cases/run identity")
    if manifest.cases.keys() != analysis.inputs.cases.keys():
        raise ValueError("results case inventory differs from configured cases")
    try:
        write_json(
            output / "experiment-inputs.json",
            {
                "analysis_sha256": analysis_sha256,
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
    evidence = file_manifest(results_root)
    write_json(output / "execution-evidence.json", evidence)
    prepared = []
    directories = set()
    # Freeze and validate every case before invoking any coverage system.
    for ordinal, prepared_case in enumerate(analysis.inputs.cases.values()):
        case = prepared_case.case
        index, raw = read_case_index(results_root, manifest, case.case_id)
        if index.parent in directories:
            raise ValueError("results directory reused by multiple cases")
        directories.add(index.parent)
        destination = output / "analysis" / f"case-{ordinal:04d}"
        selection = selection_for_results(case, raw, analysis.settings.scoring)
        subsets = analysis.load_subsets(case.case_id)
        description = analysis.load_description(case.case_id)
        write_json(destination / "selection.json", selection)
        write_json(destination / "coverage-config.json", analysis.settings.coverage)
        write_json(destination / "subsets.json", subsets)
        write_json(destination / "case-description.json", description)
        mutants = compute_mutant_scores(raw, subsets, selection)
        prepared.append((case.case_id, raw, index.parent, destination, mutants, subsets, selection, description))
    results = {}
    for case_id, raw, native, destination, mutants, subsets, selection, description in prepared:
        write_json(destination / "mutant-scores.json", mutants)
        coverage = invoke_coverage(
            raw,
            subsets,
            selection,
            analysis.settings.coverage,
            description,
            native,
            destination / "coverage-evidence",
            cached_only=cached_only,
        )
        write_json(destination / "coverage-scores.json", coverage)
        correlation = correlate(coverage, mutants, missing_policy=analysis.settings.missing_policy)
        write_json(destination / "correlation.json", correlation)
        results[case_id] = {
            "correlation": (destination / "correlation.json").relative_to(output).as_posix(),
            "sha256": identity(correlation),
        }
    # A trusted adapter can access the host. Detect input/evidence modification
    # before publishing experiment completion, including changes to earlier cases.
    if load_benchmark(benchmark, allow_unsigned=allow_unsigned).analysis.identity != analysis_sha256:
        raise ValueError("analysis inputs changed during execution")
    if file_manifest(results_root) != evidence:
        raise ValueError("execution evidence changed during analysis")
    result = {
        "analysis_sha256": analysis_sha256,
        "execution_sha256": manifest.execution_sha256,
        "run_id": run_id,
        "cases": results,
    }
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
