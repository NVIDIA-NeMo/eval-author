# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run our pinned evaluator with a bounded CI context-comparison allowance."""

import json
import sys
from importlib import import_module
from importlib.metadata import distribution

from collect_skill_evaluations import EVALUATOR_REVISION

CONTEXT_SCALAR_LIMIT = 100_000_000
CONTEXT_POLICY = "full-pairwise-100m-v1"


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "context-optimization-check":
        raise ValueError("CI comparison allowance is restricted to context checks")
    origin = json.loads(distribution("skillevaluator").read_text("direct_url.json") or "{}")
    if origin.get("vcs_info", {}).get("commit_id") != EVALUATOR_REVISION:
        raise ValueError("CI comparison allowance requires the pinned evaluator commit")
    intra_skill_validator = import_module("skillevaluator.deduplication.intra_skill.intra_skill_validator")

    if intra_skill_validator.CONTENT_DEDUP_MAX_SCALAR_COMPARISONS != 25_000_000:
        raise ValueError("upstream comparison contract changed; review the CI allowance")
    # Keep all chunks, all pairs, thresholds, clustering, and findings unchanged.
    # This guard still bounds work; the collector also enforces a five-minute timeout.
    setattr(intra_skill_validator, "CONTENT_DEDUP_MAX_SCALAR_COMPARISONS", CONTEXT_SCALAR_LIMIT)
    import_module("skillevaluator.cli").cli()


if __name__ == "__main__":
    main()
