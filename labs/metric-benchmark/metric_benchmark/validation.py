# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate matrix accounting, native Harbor observations, and evidence paths."""

import argparse
import warnings
from pathlib import Path

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig
from harbor.models.trial.result import TrialResult

from metric_benchmark.config import (
    Agent,
    Case,
    LoadedBenchmark,
    ScoringConfig,
    ScoringPolicy,
    file_manifest,
    load_benchmark,
)
from metric_benchmark.io import cli, read_json, write_json
from metric_benchmark.models import ExecutionManifest, RawResults, Score, resolve_evidence


def agent_config(
    agent: Agent, root: Path, *, recorded_root: Path | None = None, allow_unsigned: bool = False
) -> AgentConfig:
    def resolve(value):
        if isinstance(value, str) and value.startswith("{input:") and value.endswith("}"):
            name = value[7:-1]
            if name not in agent.inputs:
                raise ValueError(f"unknown agent input: {name}")
            source = agent.inputs[name]
            local = source.resolve(root, allow_unsigned=allow_unsigned)
            return str(recorded_root / source.path) if recorded_root is not None else str(local)
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    values = resolve(agent.harbor)
    if set(values) - AgentConfig.model_fields.keys():
        raise ValueError("unknown Harbor agent configuration field")
    if bool(values.get("name")) == bool(values.get("import_path")):
        raise ValueError("agent must select exactly one Harbor name or import_path")
    return AgentConfig.model_validate(values)


def task_checksum(task: HarborTask) -> str:
    # Compare the legacy checksum carried by native result.json. Lock digests
    # are a different identity and cannot be substituted when validating saved
    # results. This compatibility path is pinned to Harbor 0.20.0.
    with warnings.catch_warnings(action="ignore", category=DeprecationWarning):
        return task.checksum


def native_observation(trial: TrialResult, metric: str) -> tuple[str, Score]:
    if trial.finished_at is None:
        raise ValueError("native trial has not finished")
    if trial.exception_info is not None:
        return "error", Score(value=None, reason=f"execution error: {trial.exception_info.exception_type}")
    if trial.step_results:
        raise ValueError("multi-step Harbor observations need an explicit aggregation policy")
    rewards = trial.verifier_result.rewards if trial.verifier_result else None
    if not rewards or metric not in rewards or rewards[metric] is None:
        return "succeeded", Score(value=None, reason=f"native verifier metric absent: {metric}")
    return "succeeded", Score(value=rewards[metric])


def selection_for_results(case: Case, raw: RawResults, scoring: ScoringPolicy) -> ScoringConfig:
    if raw.task_inventory != case.task_inventory.tasks:
        raise ValueError("results task inventory differs from benchmark case")
    runs: dict[str, str] = {}
    for row in raw.execution_results:
        if runs.setdefault(row.agent_bundle_id, row.agent_run_id) != row.agent_run_id:
            raise ValueError(f"case {case.case_id}, agent {row.agent_bundle_id}: select one run per agent")
    expected = {agent.agent_bundle_id for agent in case.agents()}
    if set(runs) != expected:
        raise ValueError(
            f"case {case.case_id}: incomplete agent x task matrix; "
            f"missing agents: {sorted(expected - runs.keys())}; unexpected agents: {sorted(runs.keys() - expected)}"
        )
    return case.selection(runs, scoring)


def recorded_input_root(task_path: Path | None, relative: str) -> Path:
    """Recover the producer's root lexically; never open paths on that machine."""
    parts = Path(relative).parts
    if (
        task_path is None
        or not task_path.is_absolute()
        or ".." in task_path.parts
        or task_path.parts[-len(parts) :] != parts
    ):
        raise ValueError(f"native task path does not match prepared source {relative}")
    return task_path.parents[len(parts) - 1]


def validate_results(
    raw: RawResults, case: Case, root: Path, input_root: Path, scoring: ScoringPolicy, *, allow_unsigned: bool = False
) -> dict:
    """Validate an accounted matrix; unavailable observations remain unavailable.

    This validates structural consistency, not whether a mutation actually changed
    behavior, the verifier is correct, or a model judgment is scientifically sound.
    """
    # Revalidate callers' mutable model instances as well as file inputs.
    raw = RawResults.model_validate(raw.model_dump())
    selection = selection_for_results(case, raw, scoring)
    agents = {a.agent_bundle_id: a for a in case.agents()}
    expected = {(a, t.task_id, t.revision) for a in agents for t in case.task_inventory.tasks}
    tasks = {
        tid: HarborTask(source.resolve(input_root, allow_unsigned=allow_unsigned))
        for tid, source in case.task_sources.items()
    }
    checksums = {tid: task_checksum(task) for tid, task in tasks.items()}
    configs: dict[tuple[str, Path], AgentConfig] = {}
    groups: dict[tuple, list] = {}
    paths = set()
    unavailable = []
    for row in raw.execution_results:
        try:
            key = (row.agent_bundle_id, *row.evaluation.task.key())
            if key not in expected:
                raise ValueError("unexpected agent or task in results")
            groups.setdefault(key, []).append(row)
            if scoring.metric not in row.metrics:
                raise ValueError("measurement must be numeric or explicitly unavailable")
            score = row.metrics[scoring.metric]
            if row.status == "missing":
                if score.value is not None or not row.unavailable_evidence.get("native_results"):
                    raise ValueError("missing execution needs unavailable score and evidence reason")
                if (root / row.native_results_path).exists():
                    raise ValueError("missing execution unexpectedly has native evidence")
            else:
                if row.native_results_path in paths:
                    raise ValueError("native trial evidence reused by multiple observations")
                paths.add(row.native_results_path)
                native = resolve_evidence(root, row.native_results_path)
                if row.native_results_format != "directory" or not native.is_dir():
                    raise ValueError("native validation currently requires a Harbor trial directory")
                trial = TrialResult.model_validate(read_json(resolve_evidence(native, "result.json")))
                task = row.evaluation.task.task_id
                prepared_task = tasks[task]
                if trial.task_name != prepared_task.name or trial.task_checksum != checksums[task]:
                    raise ValueError("native task identity or checksum differs from prepared input")
                if trial.trial_name != native.name:
                    raise ValueError("native trial identity differs from directory")
                producer_root = recorded_input_root(trial.config.task.path, case.task_sources[task].path)
                config_key = row.agent_bundle_id, producer_root
                if config_key not in configs:
                    configs[config_key] = agent_config(
                        agents[row.agent_bundle_id],
                        input_root,
                        recorded_root=producer_root,
                        allow_unsigned=allow_unsigned,
                    )
                if trial.config.agent != configs[config_key]:
                    raise ValueError("native agent configuration differs from benchmark")
                status, observed = native_observation(trial, scoring.metric)
                if status != row.status or score != observed:
                    raise ValueError("native status or measurement differs from index")
            if score.value is None:
                unavailable.append(
                    {
                        "agent": row.agent_bundle_id,
                        "task": key[1],
                        "attempt": row.evaluation.execution,
                        "reason": score.reason,
                    }
                )
        except (OSError, ValueError) as error:
            raise ValueError(
                f"case {case.case_id}, agent {row.agent_bundle_id}, task {row.evaluation.task.task_id}, "
                f"evidence {row.native_results_path}: {error}"
            ) from error
    if set(groups) != expected:
        raise ValueError("results do not account for the complete agent x task matrix")
    for rows in groups.values():
        if len({r.evaluation.evaluation_id for r in rows}) != 1:
            raise ValueError("ambiguous repetitions for agent/task")
        if len(rows) != 1 and selection.attempt_policy == "only":
            raise ValueError("multiple attempts with attempt_policy=only")
    manifest = file_manifest(root)
    # Derived reports are outside this root. The index is hashed separately by
    # consumers, so it may be published after validation by an execution backend.
    manifest.pop("results.json", None)
    return {
        "case_id": case.case_id,
        "run_id": raw.run_id,
        "expected_pairs": len(expected),
        "observations": len(raw.execution_results),
        "unavailable": unavailable,
        "files": manifest,
    }


def validate_experiment_results(loaded: LoadedBenchmark, root: Path, *, case_id: str | None = None) -> dict:
    manifest = ExecutionManifest.model_validate(read_json(resolve_evidence(root, "execution.json")))
    selected = {case_id: loaded.case(case_id)} if case_id is not None else loaded.cases
    missing = selected.keys() - manifest.cases.keys()
    unexpected = manifest.cases.keys() - loaded.cases.keys()
    if missing or unexpected:
        raise ValueError(f"results case inventory differs; missing: {sorted(missing)}; unexpected: {sorted(unexpected)}")
    reports = {}
    directories = set()
    for selected_id, prepared in selected.items():
        try:
            index = resolve_evidence(root, manifest.cases[selected_id])
            if index.name != "results.json":
                raise ValueError("case index must be named results.json")
            if index.parent in directories:
                raise ValueError("results directory reused by multiple cases")
            directories.add(index.parent)
            raw = RawResults.model_validate(read_json(index))
            if raw.run_id != manifest.run_id:
                raise ValueError("case run_id differs from execution.json")
            reports[selected_id] = validate_results(
                raw,
                prepared.case,
                index.parent,
                prepared.root,
                loaded.definition.scoring,
                allow_unsigned=prepared.allow_unsigned,
            )
        except (OSError, ValueError) as error:
            raise ValueError(f"case {selected_id}: {error}") from error
    return {"run_id": manifest.run_id, "cases": reports}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument(
        "--allow-unsigned", action="store_true", help="allow missing input pins; still verify supplied SHAs"
    )
    parser.add_argument("--case", help="validate only this case; default: all configured cases")
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args):
        loaded = load_benchmark(args.benchmark, allow_unsigned=args.allow_unsigned)
        write_json(args.output, validate_experiment_results(loaded, args.results_root, case_id=args.case))

    cli(parser, run)
