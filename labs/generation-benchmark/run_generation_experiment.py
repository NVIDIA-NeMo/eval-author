#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run generation benchmark stages 0-2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_GEN_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _GEN_ROOT.parents[1]
_VENV_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"

try:
    import pydantic  # noqa: F401
    import yaml  # noqa: F401
except ImportError:
    if _VENV_PYTHON.is_file() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
        env = dict(os.environ)
        venv_bin = str(_VENV_PYTHON.parent)
        path_entries = [p for p in env.get("PATH", "").split(os.pathsep) if p]
        if venv_bin not in path_entries:
            env["PATH"] = os.pathsep.join([venv_bin, *path_entries])
        os.execve(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv], env)
    raise

for _path in (str(_GEN_ROOT), str(_GEN_ROOT.parent / "metric-benchmark")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from generation_benchmark.pipeline import INVALIDATE_STAGE_CHOICES, run_experiment  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="run_generation_experiment.py",
        description="Run generation benchmark stages 0-2 on a signed generation-experiment.yml.",
    )
    parser.add_argument("--benchmark", type=Path, required=True, help="Path to generation-experiment.yml")
    parser.add_argument("--run-id", required=True, help="Run identifier; must remain identical when reusing work root")
    parser.add_argument("--work-root", type=Path, required=True, help="Output work root directory")
    parser.add_argument(
        "--invalidate-stage",
        action="append",
        choices=INVALIDATE_STAGE_CHOICES,
        default=[],
        help=(
            "Invalidate stage directories so they rerun (setup, baseline, subsets); "
            "deleting those directories by hand is the same operation."
        ),
    )
    parser.add_argument(
        "--case",
        dest="case_filter",
        help="Run stages 0-2 only for CASE_ID and leave other cases untouched.",
    )

    args = parser.parse_args(argv)
    try:
        summary = run_experiment(
            args.benchmark,
            args.run_id,
            args.work_root,
            invalidate_stages=args.invalidate_stage,
            case_filter=args.case_filter,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
