# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate matrix accounting, native Harbor observations, and evidence paths."""

import argparse
import warnings
from pathlib import Path

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig
from harbor.models.trial.result import TrialResult

from metric_benchmark.config import Agent, Case, file_manifest, load_benchmark
from metric_benchmark.io import cli, read_json, write_json
from metric_benchmark.models import RawResults, Score, resolve_evidence


def agent_config(agent: Agent, root: Path) -> AgentConfig:
    def resolve(value):
        if isinstance(value, str) and value.startswith("{input:") and value.endswith("}"):
            name = value[7:-1]
            if name not in agent.inputs:
                raise ValueError(f"unknown agent input: {name}")
            return str(agent.inputs[name].resolve(root))
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    values = resolve(agent.harbor)
    if set(values) - AgentConfig.model_fields.keys():
        raise ValueError("unknown Harbor agent configuration field")
    result = AgentConfig.model_validate(values)
    if bool(result.name) == bool(result.import_path):
        raise ValueError("agent must select exactly one Harbor name or import_path")
    return result


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


def validate_results(raw: RawResults, case: Case, root: Path, input_root: Path) -> dict:
    """Validate an accounted matrix; unavailable observations remain unavailable.

    This validates structural consistency, not whether a mutation actually changed
    behavior, the verifier is correct, or a model judgment is scientifically sound.
    """
    # Revalidate callers' mutable model instances as well as file inputs.
    raw = RawResults.model_validate(raw.model_dump())
    if raw.task_inventory != case.task_inventory:
        raise ValueError("results task inventory differs from benchmark case")
    selection = case.selection()
    agents = {a.agent_bundle_id: a for a in case.agents()}
    expected = {(a, t.task_id, t.revision) for a in agents for t in case.task_inventory.tasks}
    groups: dict[tuple, list] = {}
    paths = set()
    unavailable = []
    for row in raw.results:
        key = (row.agent_bundle_id, *row.evaluation.task.key())
        if key not in expected or selection.run_ids[row.agent_bundle_id] != row.run_id:
            raise ValueError("unexpected agent, run, or task in results")
        groups.setdefault(key, []).append(row)
        if row.native_results_path in paths:
            raise ValueError("native trial evidence reused by multiple observations")
        paths.add(row.native_results_path)
        if case.scoring.metric not in row.metrics:
            raise ValueError("measurement must be numeric or explicitly unavailable")
        score = row.metrics[case.scoring.metric]
        if row.status == "missing":
            if score.value is not None or not row.unavailable_evidence.get("native_results"):
                raise ValueError("missing execution needs unavailable score and evidence reason")
            if (root / row.native_results_path).exists():
                raise ValueError("missing execution unexpectedly has native evidence")
        else:
            native = resolve_evidence(root, row.native_results_path)
            if row.native_results_format != "directory" or not native.is_dir():
                raise ValueError("native validation currently requires a Harbor trial directory")
            trial = TrialResult.model_validate(read_json(resolve_evidence(native, "result.json")))
            task = row.evaluation.task.task_id
            prepared_task = HarborTask(case.task_sources[task].resolve(input_root))
            if trial.task_name != prepared_task.name or trial.task_checksum != task_checksum(prepared_task):
                raise ValueError("native task identity or checksum differs from prepared input")
            if trial.trial_name != native.name:
                raise ValueError("native trial identity differs from directory")
            if trial.config.agent != agent_config(agents[row.agent_bundle_id], input_root):
                raise ValueError("native agent configuration differs from benchmark")
            status, observed = native_observation(trial, case.scoring.metric)
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
        "schema_version": 1,
        "case_id": case.case_id,
        "experiment_id": raw.experiment_id,
        "expected_pairs": len(expected),
        "observations": len(raw.results),
        "unavailable": unavailable,
        "files": manifest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args):
        loaded = load_benchmark(args.benchmark)
        cases = {c.case_id: c for c in loaded.definition.cases}
        if args.case not in cases:
            raise ValueError("unknown case")
        write_json(
            args.output,
            validate_results(
                RawResults.model_validate(read_json(args.results_root / "results.json")),
                cases[args.case],
                args.results_root,
                loaded.root,
            ),
        )

    cli(parser, run)
