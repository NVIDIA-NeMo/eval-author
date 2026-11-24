# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generation benchmark models, signing, validation, and stage 0-2 runner."""

import sys
from pathlib import Path

_GENERATION_ROOT = Path(__file__).resolve().parents[1]
_METRIC_ROOT = _GENERATION_ROOT.parent / "metric-benchmark"

for _path in (str(_GENERATION_ROOT), str(_METRIC_ROOT)):
    if _path not in sys.path:
        sys.path.append(_path)
