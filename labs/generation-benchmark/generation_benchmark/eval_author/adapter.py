# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Eval Author reference generation adapter (request.json → response.json)."""

import argparse
import shutil
from pathlib import Path

from generation_benchmark.config import draft_dir_from_settings
from generation_benchmark.eval_author.generation import generate_suite
from generation_benchmark.eval_author.suite_tasks import discover_suite
from generation_benchmark.generation import GenerationResponse, validate_generation_request
from generation_benchmark.measurement_export import export_subset_measurement
from metric_benchmark.config import file_manifest
from metric_benchmark.io import cli, digest_bytes, json_bytes, read_json, write_json
from metric_benchmark.models import Score


def _promote_generated_tasks(suite: Path) -> None:
    """Move eval-author exports from ``_generated/<slug>`` to top-level Harbor task dirs."""
    staging = suite / "_generated"
    if not staging.is_dir():
        return
    for task_dir in sorted(staging.iterdir()):
        if not task_dir.is_dir():
            continue
        destination = suite / task_dir.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(task_dir, destination, symlinks=True)
    shutil.rmtree(staging)


def _staged_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"generation request path must stay inside the work cell: {value}")
    resolved = path.resolve()
    root = Path.cwd().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"generation request path must stay inside the work cell: {value}")
    return resolved


def _case_root(settings: dict) -> Path | None:
    if settings.get("subset_local_measurement_export"):
        return None
    raw = settings.get("case_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def run_from_request(request_path: Path, output_path: Path) -> None:
    request = read_json(request_path)
    if not isinstance(request, dict):
        raise ValueError("generation request must be an object")
    validate_generation_request(request)
    settings = request["settings"]
    eval_author_root = Path(str(settings["eval_author_root"])).resolve()
    generator_config = Path(str(settings["generator_config"])).resolve()
    agent_config = Path(str(settings["agent_config"])).resolve()
    budget = settings.get("generation_budget") or {}
    max_new = int(budget.get("max_new_tasks", 1))
    seed = int(budget.get("seed", 0))
    suite = _staged_path(request["inputs"]["suite"])
    coverage_work = _staged_path(request["inputs"]["coverage_work"])
    out_suite = _staged_path(request["inputs"]["output_suite"])
    out_suite.parent.mkdir(parents=True, exist_ok=True)
    score = Score.model_validate(request["coverage"])
    measurement = Path(".measurement").resolve()
    export_subset_measurement(
        coverage_work_cell=coverage_work,
        suite_root=suite,
        agent_config_path=agent_config,
        output_dir=measurement,
        score_value=score.value,
        eval_author_root=eval_author_root,
        case_root=_case_root(settings),
    )
    generate_suite(
        suite,
        measurement_path=measurement,
        tasks=max_new,
        output=out_suite,
        eval_author_root=eval_author_root,
        agent_path=agent_config,
        generator_path=generator_config,
        seed=seed,
        draft_dir=draft_dir_from_settings(settings),
    )
    _promote_generated_tasks(out_suite)
    before = {task.path.name for task in discover_suite(suite)}
    after = discover_suite(out_suite)
    new_ids = sorted({task.path.name for task in after} - before)
    response = GenerationResponse(
        eval_subset_id=request["eval_subset_id"],
        new_task_ids=new_ids,
        expanded_suite_digest=digest_bytes(json_bytes(file_manifest(out_suite))),
        summary={"new_task_count": len(new_ids), "tasks_attempted": max_new},
    )
    write_json(output_path, response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args: argparse.Namespace) -> None:
        run_from_request(args.request.resolve(), args.output.resolve())

    cli(parser, run)
