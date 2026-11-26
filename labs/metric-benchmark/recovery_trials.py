# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run isolated recovery comparisons without changing rubric scores."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/eval-author-environment/scripts"))

from metric_benchmark.recovery import main  # noqa: E402

if __name__ == "__main__":
    main()
