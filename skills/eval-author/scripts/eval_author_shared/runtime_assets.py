# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load retained standalone runtime producers without global import-name collisions."""

import importlib.util
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "runtime"


def load(name):
    if name not in {"qualification_runtime", "review_runtime"}:
        raise ValueError("unknown runtime producer")
    qualified = f"{__package__}._{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, ASSETS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"missing runtime producer: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(qualified, None)
        raise
    return module
