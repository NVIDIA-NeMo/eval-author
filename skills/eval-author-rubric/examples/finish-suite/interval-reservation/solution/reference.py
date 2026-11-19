# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
from pathlib import Path


def solve(case: dict) -> dict:
    if case["occupied"] is None:
        choice = None
    else:
        good = []
        for option in case["options"]:
            left, right = option["interval"]
            if all(right <= start or end <= left for start, end in case["occupied"]):
                good.append(option["id"])
        if len(good) != 1:
            raise ValueError("nonunique_answer")
        choice = good[0]
    return {"choice": choice, "success": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=Path("/app/case.json"))
    parser.add_argument("--candidate", type=Path, default=Path("/work/candidate.json"))
    args = parser.parse_args()
    args.candidate.write_text(json.dumps(solve(json.loads(args.case.read_bytes()))) + "\n")
