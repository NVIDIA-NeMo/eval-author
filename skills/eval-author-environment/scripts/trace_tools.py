# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""eval-author-environment entry point for shared trace tools."""

import sys
from pathlib import Path

_shared = Path(__file__).resolve().parents[2] / "eval-author" / "scripts"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))
from eval_author_shared.commands import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main("environment"))
