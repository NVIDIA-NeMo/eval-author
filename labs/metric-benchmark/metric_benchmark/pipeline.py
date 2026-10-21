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
from metric_benchmark.io import cli, digest_file, read_json, write_json
from metric_benchmark.models import RawResults, resolve_evidence
from metric_benchmark.scoring import compute_mutant_scores, identity
from metric_benchmark.validation import validate_results


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
    runner: Callable[[JobConfig], None] = run_harbor,
) -> dict:
    """Run execution, validation, scoring, coverage and correlation.

    With results_root, only post-execution work runs. cached_only additionally
    prohibits new coverage-system invocations. No stage CLI is used internally.
    """
    loaded = load_benchmark(benchmark)
    if cached_only and results_root is None:
        raise ValueError("cached_only requires an existing results_root")
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    write_json(
        output / "experiment-inputs.json",
        {
            "benchmark_sha256": loaded.identity,
            "benchmark": loaded.definition.model_dump(mode="json"),
            "run_id": run_id,
            "implementation": implementation(),
        },
    )
    if results_root is None:
        results_root = output / "execution"
        execute_benchmark(loaded, run_id, results_root, runner=runner)
    manifest = read_json(resolve_evidence(results_root, "execution.json"))
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "benchmark_sha256", "run_id", "cases"}:
        raise ValueError("invalid execution manifest")
    if (
        manifest["schema_version"] != 1
        or manifest["run_id"] != run_id
        or manifest["benchmark_sha256"] != loaded.identity
    ):
        raise ValueError("execution manifest does not match benchmark/run identity")
    if not isinstance(manifest["cases"], dict) or set(manifest["cases"]) != {
        c.case_id for c in loaded.definition.cases
    }:
        raise ValueError("execution manifest must contain exactly the configured cases")
    prepared = []
    # Freeze and validate every case before invoking any coverage system.
    for ordinal, case in enumerate(loaded.definition.cases):
        index = resolve_evidence(results_root, manifest["cases"][case.case_id])
        if index.name != "results.json":
            raise ValueError("case results index must be named results.json")
        raw = RawResults.model_validate(read_json(index))
        if raw.experiment_id != run_id:
            raise ValueError("case results belong to another run")
        validation = validate_results(raw, case, index.parent, loaded.root)
        destination = output / "analysis" / f"case-{ordinal:04d}"
        write_json(
            destination / "inputs.json",
            {
                "raw_results_sha256": identity(raw),
                "validation": validation,
                "subsets": loaded.subsets[case.case_id].model_dump(mode="json"),
                "case_description": loaded.descriptions[case.case_id],
            },
        )
        write_json(destination / "validation.json", validation)
        write_json(destination / "selection.json", case.selection())
        write_json(destination / "coverage-config.json", case.coverage)
        write_json(destination / "subsets.json", loaded.subsets[case.case_id])
        write_json(destination / "case-description.json", loaded.descriptions[case.case_id])
        prepared.append((case, raw, index.parent, validation, destination))
    results = {}
    for case, raw, native, validation, destination in prepared:
        subsets = loaded.subsets[case.case_id]
        selection = case.selection()
        mutants = compute_mutant_scores(raw, subsets, selection)
        write_json(destination / "mutant-scores.json", mutants)
        coverage = invoke_coverage(
            raw,
            subsets,
            selection,
            case.coverage,
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
    if load_benchmark(benchmark).identity != loaded.identity:
        raise ValueError("benchmark inputs changed during execution")
    for case, raw, native, validation, _ in prepared:
        if (
            RawResults.model_validate(read_json(native / "results.json")) != raw
            or validate_results(raw, case, native, loaded.root) != validation
        ):
            raise ValueError("execution evidence changed during analysis")
    result = {"schema_version": 1, "benchmark_sha256": loaded.identity, "run_id": run_id, "cases": results}
    write_json(output / "experiment.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results", type=Path, help="existing execution root; skip agent execution")
    parser.add_argument("--cached-only", action="store_true", help="also prohibit new coverage calls")

    def run(args):
        run_experiment(
            args.benchmark, args.run_id, args.output, results_root=args.results, cached_only=args.cached_only
        )

    cli(parser, run)
