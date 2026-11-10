# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Locate completed Harbor agent trials without reading trajectories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_author_shared.retained_runs import lookup  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--agent",
        help="Known agent identifier from run metadata. Omit to inventory agents without verifying availability",
    )
    parser.add_argument("--task", action="append", default=[], help="Known task name from the selected suite")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-jobs", type=int, default=200)
    args = parser.parse_args()
    print(
        json.dumps(
            lookup(args.repo, agent=args.agent, tasks=tuple(args.task), offset=args.offset, max_jobs=args.max_jobs)
        )
    )


if __name__ == "__main__":
    main()
