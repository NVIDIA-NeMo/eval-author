# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import sys

import pytest

from generation_benchmark.measure_hooks import run_before_measure_command


def test_before_measure_command_runs_hook(tmp_path):
    hook = tmp_path / "hook.py"
    hook.write_text(
        """
import os
from pathlib import Path
Path(os.environ["GENERATION_RESULTS_ROOT"]).joinpath("hook.ran").write_text("ok")
""",
        encoding="utf-8",
    )
    results = tmp_path / "results"
    results.mkdir()
    run_before_measure_command(
        {"before_measure_command": [sys.executable, str(hook)]},
        results,
    )
    assert (results / "hook.ran").read_text(encoding="utf-8") == "ok"


def test_before_measure_command_rejects_relative_executable(tmp_path):
    with pytest.raises(ValueError, match="absolute executable"):
        run_before_measure_command({"before_measure_command": ["echo", "x"]}, tmp_path)
