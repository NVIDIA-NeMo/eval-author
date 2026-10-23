# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sequential case/agent orchestration of native Harbor jobs."""

import argparse
import asyncio
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

from harbor.models.job.config import JobConfig
from harbor.models.trial.config import EnvironmentConfig
from harbor.models.trial.result import TrialResult

from metric_benchmark.config import Case, LoadedBenchmark, file_manifest, load_benchmark
from metric_benchmark.io import cli, digest_bytes, json_bytes, read_json, write_json
from metric_benchmark.models import Execution, RawResults, Text, resolve_evidence
from metric_benchmark.validation import agent_config, native_observation, validate_results


def case_root(output: Path, ordinal: int) -> Path:
    return output / "cases" / f"case-{ordinal:04d}" / "execution-results"


def run_harbor(config: JobConfig) -> None:
    from harbor.job import Job

    async def run():
        job = await Job.create(config)
        await job.run()

    asyncio.run(run())


def job_configuration(loaded: LoadedBenchmark, case: Case, agent_index: int, root: Path) -> JobConfig:
    settings = loaded.definition.execution
    if set(settings.environment) - EnvironmentConfig.model_fields.keys():
        raise ValueError("unknown Harbor environment setting")
    return JobConfig.model_validate(
        {
            "job_name": "run-0001",
            "jobs_dir": str(root / "agents" / f"agent-{agent_index:04d}"),
            "n_attempts": 1,
            "retry": {"max_retries": 0, "exclude_exceptions": []},
            "n_concurrent_trials": settings.n_concurrent_trials,
            "environment": settings.environment,
            "agents": [agent_config(case.agents()[agent_index], loaded.root).model_dump(mode="json")],
            "tasks": [
                {"path": str(case.task_sources[t.task_id].resolve(loaded.root))}
                for t in sorted(case.task_inventory.tasks, key=lambda t: t.key())
            ],
            "quiet": True,
        }
    )


def collect_job(case: Case, agent_index: int, root: Path, input_root: Path) -> list[Execution]:
    """Read native metadata; never derive task identities from trial suffixes."""
    agent = case.agents()[agent_index]
    directory = root / "agents" / f"agent-{agent_index:04d}" / "run-0001"
    job = read_json(resolve_evidence(directory, "result.json"))
    if not isinstance(job, dict) or not job.get("finished_at"):
        raise ValueError("Harbor job has not finished; preserve this run and resume it explicitly in Harbor")
    task_paths = {case.task_sources[t.task_id].resolve(input_root): t for t in case.task_inventory.tasks}
    rows = []
    seen = set()
    for result_path in sorted(directory.glob("*/result.json")):
        native = TrialResult.model_validate(
            read_json(resolve_evidence(directory, result_path.relative_to(directory).as_posix()))
        )
        path = native.config.task.path
        task = task_paths.get(path.resolve()) if path is not None else None
        if task is None or task.task_id in seen:
            raise ValueError("unexpected or repeated native task in Harbor job")
        seen.add(task.task_id)
        status, score = native_observation(native, case.scoring.metric)
        rows.append(
            Execution.model_validate(
                {
                    "run_id": case.selection().run_ids[agent.agent_bundle_id],
                    "agent_bundle_id": agent.agent_bundle_id,
                    "evaluation": {
                        "evaluation_id": f"agent-{agent_index:04d}/{task.task_id}",
                        "execution": 1,
                        "task": task.model_dump(),
                    },
                    "status": status,
                    "metrics": {case.scoring.metric: score.model_dump()},
                    "native_results_path": result_path.parent.relative_to(root).as_posix(),
                    "native_results_format": "directory",
                }
            )
        )
    if seen != {t.task_id for t in case.task_inventory.tasks}:
        raise ValueError("finished Harbor job does not account for every configured task")
    return sorted(rows, key=lambda r: r.evaluation.task.key())


def execute_benchmark(
    loaded: LoadedBenchmark,
    run_id: Text,
    output: Path,
    *,
    runner: Callable[[JobConfig], None] = run_harbor,
) -> dict:
    """Run baseline then mutants for each case; reuse only verified completed jobs.

    A partial native job is never silently restarted. Complete it with Harbor's
    own resume command or choose a new output directory. Completed earlier jobs
    remain reusable, including when a later agent fails before creating its job.
    """
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    if version("harbor") != loaded.definition.execution.harbor_version:
        raise ValueError("installed Harbor version differs from benchmark execution version")
    output = output.resolve()
    # Resolve and validate all jobs before launching any, so a bad later case
    # cannot spend a valid earlier case's execution budget.
    jobs = [
        (case, i, case_root(output, ordinal), job_configuration(loaded, case, i, case_root(output, ordinal)))
        for ordinal, case in enumerate(loaded.definition.cases)
        for i in range(len(case.agents()))
    ]
    write_json(
        output / "execution-inputs.json",
        {
            "benchmark_sha256": loaded.identity,
            "benchmark": loaded.definition.model_dump(mode="json"),
            "run_id": run_id,
        },
    )
    indexes = {}
    for ordinal, case in enumerate(loaded.definition.cases):
        root = case_root(output, ordinal)
        rows = []
        for _, agent_index, _, config in [entry for entry in jobs if entry[0].case_id == case.case_id]:
            parent = config.jobs_dir
            directory = parent / config.job_name
            receipt_path = parent / "receipt.json"
            request = json_bytes(config)
            write_json(parent / "request.json", config)
            if receipt_path.exists():
                receipt = read_json(resolve_evidence(parent, "receipt.json"))
                if receipt != {"configuration_sha256": digest_bytes(request), "files": file_manifest(directory)}:
                    raise ValueError("completed Harbor job changed")
            else:
                if directory.exists():
                    # A crash after Harbor finished may leave no receipt. Validate
                    # the complete job before sealing it; never replay observations.
                    collect_job(case, agent_index, root, loaded.root)
                else:
                    runner(config)
                collect_job(case, agent_index, root, loaded.root)
                write_json(
                    receipt_path, {"configuration_sha256": digest_bytes(request), "files": file_manifest(directory)}
                )
            rows.extend(collect_job(case, agent_index, root, loaded.root))
        raw = RawResults(experiment_id=run_id, task_inventory=case.task_inventory, results=rows)
        validate_results(raw, case, root, loaded.root)
        write_json(root / "results.json", raw)
        indexes[case.case_id] = (root / "results.json").relative_to(output).as_posix()
    result = {"schema_version": 1, "benchmark_sha256": loaded.identity, "run_id": run_id, "cases": indexes}
    write_json(output / "execution.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args):
        execute_benchmark(load_benchmark(args.benchmark), args.run_id, args.output)

    cli(parser, run)
