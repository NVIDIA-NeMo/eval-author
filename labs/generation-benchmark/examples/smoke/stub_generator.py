# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stub generation command: copies one prepared task for the first uncovered tool in the gap report.

`settings.mode` makes it misbehave on purpose: `timeout`, `error`, `ignore_sigterm`, `modify_input`, or
`no_response`. It does not run `measure.py` or `report.py` and does not write `baseline-report.json`.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import time
from pathlib import Path

from generation_benchmark.models import GenerationAttempt, GenerationRequest, GenerationResponse, write_model
from metric_benchmark.io import read_json

STUB_TASKS = Path(__file__).resolve().parent / "stub_tasks"
MODES = ("normal", "timeout", "error", "ignore_sigterm", "modify_input", "no_response")
SIGTERM_MARKER = "logs/sigterm-received"


def _uncovered(metric_output_dir: Path) -> list[str]:
    report = read_json(metric_output_dir / "audit-coverage-report.json")
    if not isinstance(report, dict) or not isinstance(report.get("uncovered"), list):
        raise ValueError(f"gap report in {metric_output_dir} has no uncovered list")
    return [str(name) for name in report["uncovered"]]


def _ignore_sigterm_forever(output_dir: Path) -> None:
    marker = output_dir / SIGTERM_MARKER

    def record(_signum: int, _frame: object) -> None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{time.time()}\n", encoding="utf-8")

    signal.signal(signal.SIGTERM, record)
    while True:
        time.sleep(0.1)


def run(request_path: Path, workspace_dir: Path, output_dir: Path) -> None:
    request = GenerationRequest.model_validate(read_json(request_path))
    mode = request.settings.get("mode") or "normal"
    if mode not in MODES:
        raise ValueError(f"unknown stub mode {mode!r}; expected one of {', '.join(MODES)}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if mode == "ignore_sigterm":
        _ignore_sigterm_forever(output_dir)
    if mode == "no_response":
        return
    if mode == "error":
        write_model(output_dir / "response.json", GenerationResponse(error="simulated stub error"))
        raise SystemExit(2)
    if mode == "modify_input":
        ethos = workspace_dir / request.workspace.ethos
        ethos.write_text(ethos.read_text(encoding="utf-8") + "\nmodified by stub\n", encoding="utf-8")

    uncovered = _uncovered(Path(request.metric_output_dir))
    budget = uncovered[: request.max_gaps] if request.max_gaps is not None else uncovered
    attempts: list[GenerationAttempt] = []
    if budget:
        tool = budget[0]
        slug = f"cover-{tool}"
        source = STUB_TASKS / slug
        if source.is_dir():
            shutil.copytree(source, output_dir / "tasks" / slug)
            attempts.append(GenerationAttempt(slug=slug, tool=tool, status="accepted", task_path=f"tasks/{slug}"))
        else:
            attempts.append(GenerationAttempt(slug=slug, tool=tool, status="failed", reason="no prepared stub task"))

    if mode == "timeout":
        time.sleep(3600)

    attempted = {attempt.tool for attempt in attempts}
    write_model(
        output_dir / "response.json",
        GenerationResponse(attempts=attempts, unattempted=[name for name in uncovered if name not in attempted]),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="stub_generator", description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.request, args.workspace, args.output_dir)


if __name__ == "__main__":
    main()
