#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Executable entrypoint for the reference tool_calls setup and score commands."""

from __future__ import annotations

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
        os.execve(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv], env)
    raise

for _path in (str(_GEN_ROOT), str(_GEN_ROOT.parent / "metric-benchmark")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from generation_benchmark.reference_metric import main  # noqa: E402

if __name__ == "__main__":
    main()
