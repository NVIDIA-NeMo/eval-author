# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run our pinned evaluator with a bounded CI context-comparison allowance."""

import json
import re
import sys
from importlib import import_module
from importlib.metadata import distribution

from collect_skill_evaluations import EVALUATOR_REVISION

UPSTREAM_CONTEXT_CHUNK_LIMIT = 512
UPSTREAM_CONTEXT_SCALAR_LIMIT = 25_000_000
# 640 * 639 / 2 pairs at an assumed 2048 dimensions require 418,775,040 scalars.
# Actual dimensions remain provider-observed and subject to the scalar guard.
CONTEXT_CHUNK_LIMIT = 640
CONTEXT_SCALAR_LIMIT = 450_000_000
CONTEXT_POLICY = "full-pairwise-640chunks-450m-v1"


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "context-optimization-check":
        raise ValueError("CI comparison allowance is restricted to context checks")
    origin = json.loads(distribution("skillevaluator").read_text("direct_url.json") or "{}")
    if origin.get("vcs_info", {}).get("commit_id") != EVALUATOR_REVISION:
        raise ValueError("CI comparison allowance requires the pinned evaluator commit")
    intra_skill_validator = import_module("skillevaluator.deduplication.intra_skill.intra_skill_validator")

    semantic_clustering = import_module("skillevaluator.deduplication.intra_skill.semantic_clustering")
    # v0.7.0 independently guards both embedding admission and actual clustering.
    # Check every contract before mutating either module or the SPDX binding.
    limits = (
        ("CONTENT_DEDUP_MAX_CHUNKS", UPSTREAM_CONTEXT_CHUNK_LIMIT, CONTEXT_CHUNK_LIMIT),
        ("CONTENT_DEDUP_MAX_SCALAR_COMPARISONS", UPSTREAM_CONTEXT_SCALAR_LIMIT, CONTEXT_SCALAR_LIMIT),
    )
    for module in (intra_skill_validator, semantic_clustering):
        for name, expected, _ in limits:
            value = getattr(module, name, None)
            if type(value) is not int or value != expected:
                raise ValueError("upstream comparison contract changed; review the CI allowance")
    chunker = import_module("skillevaluator.deduplication.utils.chunker")
    original_spdx = import_module("skillevaluator.spdx").is_spdx_only_html_comment
    if chunker.is_spdx_only_html_comment is not original_spdx:
        raise ValueError("upstream SPDX contract changed; review the CI adapter")

    def recognize_spdx(text: str) -> bool:
        if original_spdx(text):
            return True
        # v0.7.0 accepts one SPDX comment, but our header tool emits two.
        match = re.fullmatch(r"\s*<!--([^<>]*?)-->\s*<!--([^<>]*?)-->\s*", text)
        return bool(match and original_spdx(f"<!-- {match[1].strip()}\n{match[2].strip()} -->"))

    # Keep all content chunks, all pairs, thresholds, clustering, and findings unchanged.
    # This guard still bounds work; the collector also enforces a five-minute timeout.
    for module in (intra_skill_validator, semantic_clustering):
        for name, _, allowance in limits:
            setattr(module, name, allowance)
    setattr(chunker, "is_spdx_only_html_comment", recognize_spdx)
    import_module("skillevaluator.cli").cli()


if __name__ == "__main__":
    main()
