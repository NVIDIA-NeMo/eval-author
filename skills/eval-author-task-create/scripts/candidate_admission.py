# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""eval-author-task-create entry point for shared candidate admission."""

import sys
from pathlib import Path

_shared = Path(__file__).resolve().parents[2] / "eval-author" / "scripts"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))
from eval_author_shared.admission import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
