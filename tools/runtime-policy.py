#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Repository entrypoint for the shared, installable runtime_policy helper."""

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    scripts = Path(__file__).resolve().parents[1] / "skills" / "eval-author" / "scripts"
    sys.path.insert(0, str(scripts))
    runpy.run_path(str(scripts / "runtime_policy.py"), run_name="__main__")
