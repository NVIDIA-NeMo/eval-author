# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Optional coverage settings hooks (external scripts; no built-in evidence synthesis)."""

import os
import subprocess
from pathlib import Path


def run_before_measure_command(settings: dict[str, object] | None, results_root: Path) -> None:
    """Run ``before_measure_command`` from coverage settings with ``GENERATION_RESULTS_ROOT`` set."""
    if not settings:
        return
    command = settings.get("before_measure_command")
    if command is None:
        return
    if not isinstance(command, list) or not command:
        raise ValueError("coverage.settings.before_measure_command must be a non-empty command list")
    if not all(isinstance(part, str) and part for part in command):
        raise ValueError("coverage.settings.before_measure_command entries must be non-empty strings")
    executable = Path(command[0])
    if not executable.is_absolute():
        raise ValueError("before_measure_command must start with an absolute executable path")
    env = os.environ.copy()
    env["GENERATION_RESULTS_ROOT"] = str(results_root.resolve())
    labs_root = Path(__file__).resolve().parents[2]
    for relative in ("metric-benchmark", "generation-benchmark"):
        root = str(labs_root / relative)
        if root not in env.get("PYTHONPATH", ""):
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (env.get("PYTHONPATH", ""), root)))
    completed = subprocess.run(command, cwd=results_root, env=env, check=False)
    if completed.returncode:
        raise ValueError(
            f"before_measure_command exited {completed.returncode}: {' '.join(command)}"
        )
