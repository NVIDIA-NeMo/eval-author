# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Checkout compatibility entrypoint; implementation ships with the rubric skill."""

import runpy
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/current_rubric_completion.py"
sys.path.insert(0, str(SOURCE.parent))
globals().update(runpy.run_path(str(SOURCE), run_name="__main__" if __name__ == "__main__" else "current_completion"))
