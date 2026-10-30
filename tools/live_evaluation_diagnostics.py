# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded, redacted diagnostics for live evaluation summaries."""

import base64
import json
import os
import re
from urllib.parse import quote

from run_pinned_evaluator import CONTEXT_SCALAR_LIMIT

LIMIT = 12000
FIELDS = {
    "check_name",
    "severity",
    "message",
    "description",
    "recommendation",
    "suggestion",
    "file_path",
    "line_number",
    "details",
    "location",
}


# Bounds cover the pinned evaluator's 512 chunks and 65,536-value vectors,
# including rejected work above the CI allowance. Never infer missing values.
GUARD_METADATA_BOUNDS = {
    "scalar_comparison_limit": {
        "pair_count": (0, 512 * 511 // 2),
        "vector_dimension": (1, 65536),
        "scalar_work": (0, 512 * 511 // 2 * 65536),
        "limit": (1, CONTEXT_SCALAR_LIMIT),
    },
    "chunk_count_limit": {"actual": (0, 65536), "limit": (1, 512)},
}


def redact(text):
    # Redact before truncating: a boundary must not expose half a credential.
    for name, value in os.environ.items():
        if value and re.search(r"key|token|secret|password|credential", name, re.I):
            for encoded in (
                value,
                json.dumps(value)[1:-1],
                quote(value, safe=""),
                base64.b64encode(value.encode()).decode(),
            ):
                text = text.replace(encoded, "[REDACTED]")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=:-]+", "[REDACTED AUTH]", text)
    text = re.sub(
        r"""(?ix)([\w-]*(?:api[_-]?key|token|secret|password|authorization|credential)[\w-]*["']?\s*[:=]\s*)
        (?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)""",
        r"\1[REDACTED]",
        text,
    )
    # URLs can contain userinfo, signed queries, and credentials in paths.
    text = re.sub(r"https?://[^\s<>\"']+", "[REDACTED URL]", text)
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    return text


def diagnostics(report, target):
    """Select findings and nested execution errors, never whole reports or traces."""
    entries = []

    def walk(value, path="report", depth=0):
        if depth > 12 or len(entries) >= 40:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("execution_errors", "incomplete_scans") and child:
                    entries.append(f"{path}.{key}: {json.dumps(child, ensure_ascii=True)}")
                elif key in ("execution_status", "expected_attempts", "scored_attempts"):
                    entries.append(f"{path}.{key}: {json.dumps(child, ensure_ascii=True)}")
                elif key == "findings" and isinstance(child, list):
                    for finding in child[:20]:
                        if isinstance(finding, dict):
                            selected = {k: v for k, v in finding.items() if k in FIELDS}
                            bounds = GUARD_METADATA_BOUNDS.get(str(finding.get("check_name")), {})
                            metadata = finding.get("metadata")
                            if isinstance(metadata, dict):
                                safe = {
                                    key: metadata[key]
                                    for key, (minimum, maximum) in bounds.items()
                                    if type(metadata.get(key)) is int and minimum <= metadata[key] <= maximum
                                }
                                if safe:
                                    selected["metadata"] = safe
                            entries.append(f"{path}.finding: {json.dumps(selected, ensure_ascii=True)}")
                elif key in ("agents", "conditions", "results") or path != "report":
                    walk(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:40]):
                walk(child, f"{path}[{index}]", depth + 1)

    walk(report)
    # Missing/malformed reports and validation failures still need evidence.
    # Read only collector-owned regular logs, never follow evaluator symlinks.
    if target is not None:
        for name in ("validate.log", "console.log"):
            path = target / name
            try:
                if path.is_file() and not path.is_symlink():
                    with path.open("rb") as stream:
                        # Bound memory while keeping the trailing error/traceback.
                        size = stream.seek(0, 2)
                        stream.seek(max(0, size - 24000))
                        data = stream.read(24000).decode("utf-8", errors="replace")
                    if size > 24000:
                        # Discard the partial first line (possibly a cut credential).
                        data = data.partition("\n")[2]
                    entries.append(f"{name} (tail):\n{redact(data)[-4000:]}")
            except OSError:
                entries.append(f"{name}: log unavailable")
    text = redact("\n".join(entries))
    if len(text) > LIMIT:
        text = text[:LIMIT] + "\n[diagnostics truncated]"
    return text
