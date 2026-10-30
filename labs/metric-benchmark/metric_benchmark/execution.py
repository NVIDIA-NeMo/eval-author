# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sequential case/agent orchestration of native Harbor jobs."""

import asyncio
import sys
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

from harbor.models.job.config import JobConfig
from harbor.models.trial.config import EnvironmentConfig
from harbor.models.trial.result import TrialResult

from metric_benchmark.config import Case, LoadedCase, LoadedExecution, file_manifest
from metric_benchmark.io import digest_bytes, digest_file, json_bytes, read_json, write_json
from metric_benchmark.models import Execution, ExecutionManifest, RawResults, Text, resolve_evidence
from metric_benchmark.validation import agent_config, native_measurements, validate_results


def execution_identity(loaded: LoadedExecution) -> str:
    """Identify execution inputs independently of analysis and experiment metadata."""
    return digest_bytes(
        json_bytes({"cases": loaded.inputs.identity_payload(verify=True), "execution": loaded.settings.model_dump()})
    )


def case_root(output: Path, case_index: int) -> Path:
    """Locate a case’s results directory by its zero-based position in the benchmark."""
    return output / "cases" / f"case-{case_index:04d}" / "execution-results"


def agent_run_id(agent_index: int) -> str:
    """Return the local executor’s logical run ID for an agent’s zero-based position."""
    return f"agent-{agent_index:04d}/run-0001"


def run_harbor(config: JobConfig) -> None:
    """Run one agent’s full task inventory through Harbor and wait for completion.
    Harbor schedules the task trials using the configured concurrency.
    """
    from harbor.job import Job

    async def run():
        job = await Job.create(config)
        await job.run()

    asyncio.run(run())


def job_configuration(
    loaded: LoadedExecution, loaded_case: LoadedCase, agent_index: int, root: Path, paths: list[Path]
) -> JobConfig:
    """Build and validate a Harbor job for one agent and all supplied task paths.
    Resolve the agent’s input files and execution settings without starting the job.
    """
    case = loaded_case.case
    settings = loaded.settings
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
            "agents": [
                agent_config(
                    case.agents()[agent_index], loaded_case.root, allow_unsigned=loaded_case.allow_unsigned
                ).model_dump(mode="json")
            ],
            "tasks": [{"path": str(path)} for path in paths],
            "quiet": True,
        }
    )


def collect_job(case: Case, agent_index: int, root: Path, request: JobConfig) -> list[Execution]:
    """Read a finished Harbor job into one Execution record per task, including status, score, and evidence path.
    Use saved task paths to identify tasks; reject unfinished jobs and missing, duplicate, or unexpected tasks.
    """
    agent = case.agents()[agent_index]
    directory = root / "agents" / f"agent-{agent_index:04d}" / "run-0001"
    job = read_json(resolve_evidence(directory, "result.json"))
    if not isinstance(job, dict) or not job.get("finished_at"):
        raise ValueError("Harbor job has not finished; preserve this run and resume it explicitly in Harbor")
    task_paths = {
        native.path: task
        for native, task in zip(request.tasks, sorted(case.task_inventory.tasks, key=lambda t: t.key()), strict=True)
    }
    rows = []
    seen = set()
    for result_path in sorted(directory.glob("*/result.json")):
        native = TrialResult.model_validate(
            read_json(resolve_evidence(directory, result_path.relative_to(directory).as_posix()))
        )
        path = native.config.task.path
        task = task_paths.get(path) if path is not None else None
        if task is None or task.task_id in seen:
            raise ValueError("unexpected or repeated native task in Harbor job")
        seen.add(task.task_id)
        status, metrics = native_measurements(native)
        rows.append(
            Execution.model_validate(
                {
                    "agent_run_id": agent_run_id(agent_index),
                    "agent_bundle_id": agent.agent_bundle_id,
                    "evaluation": {
                        "evaluation_id": f"agent-{agent_index:04d}/{task.task_id}",
                        "execution": 1,
                        "task": task.model_dump(),
                    },
                    "status": status,
                    "metrics": {name: score.model_dump() for name, score in metrics.items()},
                    "native_results_path": result_path.parent.relative_to(root).as_posix(),
                    "native_results_format": "directory",
                }
            )
        )
    if seen != {t.task_id for t in case.task_inventory.tasks}:
        raise ValueError("finished Harbor job does not account for every configured task")
    return sorted(rows, key=lambda r: r.evaluation.task.key())


def run_or_reuse_job(
    loaded: LoadedExecution,
    case: Case,
    agent_index: int,
    root: Path,
    config: JobConfig,
    identity: str,
    *,
    runner: Callable[[JobConfig], None] = run_harbor,
) -> list[Execution]:
    """Run or verify one agent’s Harbor job, collect its task observations, and seal completed evidence.
    Preserve partial jobs for explicit recovery and report failures with case and agent context.
    """
    agent = case.agents()[agent_index]
    try:
        parent = config.jobs_dir
        directory = parent / config.job_name
        receipt_path = parent / "receipt.json"
        sealed = receipt_path.exists()
        request = {
            "execution_sha256": identity,
            "case_id": case.case_id,
            "agent_bundle_id": agent.agent_bundle_id,
            "execution": loaded.settings.model_dump(mode="json"),
        }
        write_json(parent / "request.json", request)
        native_path = parent / "native-request.json"
        if not native_path.exists():
            if directory.exists() or sealed:
                raise ValueError("existing job lacks native-request.json; original task bindings are required")
            write_json(native_path, config)
        native_request = JobConfig.model_validate(read_json(resolve_evidence(parent, "native-request.json")))
        expected = {
            "configuration_sha256": digest_bytes(json_bytes(request)),
            "native_request_sha256": digest_file(native_path),
        }
        print(
            f"case {case.case_id}, agent {agent.agent_bundle_id} ({agent_index + 1}/{len(case.agents())}): "
            + ("checking saved job" if directory.exists() else "running Harbor"),
            file=sys.stderr,
        )
        if sealed:
            receipt = read_json(resolve_evidence(parent, "receipt.json"))
            if receipt != {**expected, "files": file_manifest(directory)}:
                raise ValueError("completed Harbor job changed")
        elif not directory.exists():
            # An unstarted request is reusable only at its original paths;
            # relocation support applies to completed jobs.
            if native_request != config:
                raise ValueError("unstarted job request moved; use a new output for this execution")
            runner(config)
        collected = collect_job(case, agent_index, root, native_request)
        if not sealed:
            write_json(receipt_path, {**expected, "files": file_manifest(directory)})
        return collected
    except (OSError, ValueError) as error:
        raise ValueError(f"case {case.case_id}, agent {agent.agent_bundle_id}: {error}") from error


def execute_benchmark(
    loaded: LoadedExecution,
    run_id: Text,
    output: Path,
    *,
    runner: Callable[[JobConfig], None] = run_harbor,
) -> dict:
    """Run one Harbor job per case/agent and publish validated case results plus an experiment manifest.
    Reuse verified completed jobs; leave incomplete jobs for explicit Harbor recovery.
    """
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    if version("harbor") != loaded.settings.harbor_version:
        raise ValueError("installed Harbor version differs from benchmark execution version")
    identity = execution_identity(loaded)
    output = output.resolve()
    # Resolve and validate all jobs before launching any, so a bad later case
    # cannot spend a valid earlier case's execution budget.
    jobs = {}
    for case_index, loaded_case in enumerate(loaded.inputs.cases.values()):
        case = loaded_case.case
        paths = [
            case.task_sources[t.task_id].resolve(loaded_case.root, allow_unsigned=loaded_case.allow_unsigned)
            for t in sorted(case.task_inventory.tasks, key=lambda t: t.key())
        ]
        if len(set(paths)) != len(paths):
            raise ValueError(f"case {case.case_id}: task sources must have distinct paths")
        for agent_index in range(len(case.agents())):
            jobs[case_index, agent_index] = job_configuration(
                loaded, loaded_case, agent_index, case_root(output, case_index), paths
            )
    inputs_path = output / "execution-inputs.json"
    inputs = {
        "execution_sha256": identity,
        "execution": loaded.settings.model_dump(mode="json"),
        "run_id": run_id,
    }
    if inputs_path.exists() and read_json(inputs_path) != inputs:
        raise ValueError("execution inputs, settings, or run ID changed; use a new output directory")
    write_json(inputs_path, inputs)
    indexes = {}
    # Each agent gets one job containing all case tasks; Harbor schedules the individual trials.
    for case_index, loaded_case in enumerate(loaded.inputs.cases.values()):
        case = loaded_case.case
        root = case_root(output, case_index)
        rows = []
        for agent_index in range(len(case.agents())):
            rows.extend(
                run_or_reuse_job(
                    loaded, case, agent_index, root, jobs[case_index, agent_index], identity, runner=runner
                )
            )
        raw = RawResults(run_id=run_id, task_inventory=case.task_inventory.tasks, execution_results=rows)
        validate_results(raw, case, root, loaded_case.root, allow_unsigned=loaded.inputs.allow_unsigned)
        write_json(root / "results.json", raw)
        indexes[case.case_id] = (root / "results.json").relative_to(output).as_posix()
    result = ExecutionManifest(
        cases_sha256=loaded.inputs.identity, execution_sha256=identity, run_id=run_id, cases=indexes
    ).model_dump()
    write_json(output / "execution.json", result)
    return result
