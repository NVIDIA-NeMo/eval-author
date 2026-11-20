# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

_METRIC = Path(__file__).resolve().parents[1] / "metric-benchmark"
if str(_METRIC) not in sys.path:
    sys.path.insert(0, str(_METRIC))

from generation_benchmark.eval_author.adapter import main  # noqa: E402

if __name__ == "__main__":
    main()
