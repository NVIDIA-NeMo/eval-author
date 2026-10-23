# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compatibility entry point; implementation lives in Eval Author's shared library."""

import sys
from pathlib import Path

_shared = Path(__file__).resolve().parents[2] / "eval-author" / "scripts"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))
from eval_author_shared import legacy_plan as _implementation  # noqa: E402
from eval_author_shared.legacy_plan import *  # noqa: E402,F401,F403

# Preserve private helpers as well as public imports used by existing callers.
globals().update({key: value for key, value in vars(_implementation).items() if not key.startswith("__")})
sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
