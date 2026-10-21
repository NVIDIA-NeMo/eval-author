# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compatibility entry point; implementation lives in Eval Author's shared library."""

import sys
from pathlib import Path

_legacy_scripts = str(Path(__file__).resolve().parent)
if _legacy_scripts not in sys.path:
    sys.path.insert(0, _legacy_scripts)

_shared = Path(__file__).resolve().parents[2] / "eval-author" / "scripts"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))
from eval_author_shared import trace_workspace as _implementation  # noqa: E402
from eval_author_shared.trace_workspace import *  # noqa: E402,F401,F403
from eval_author_shared.trace_workspace import (  # noqa: E402
    _job_check_rows as _job_check_rows,
)
from eval_author_shared.trace_workspace import (  # noqa: E402
    _solution_review as _solution_review,
)

# Preserve private helpers as well as public imports used by existing callers.
globals().update({key: value for key, value in vars(_implementation).items() if not key.startswith("__")})
sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
