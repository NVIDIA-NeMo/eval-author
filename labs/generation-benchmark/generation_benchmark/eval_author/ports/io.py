# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small, non-destructive I/O helpers."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import ExperimentError


def prepare_output(path: Path, *, force: bool) -> Path:
    """Resolve an output and refuse an implicit overwrite."""
    resolved = path.expanduser().resolve()
    if resolved.exists() and not force:
        raise ExperimentError(f"refusing to overwrite {resolved}; pass --force")
    return resolved


def write_json(path: Path, payload: Any) -> None:
    """Atomically write human-readable JSON."""
    write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def write_bytes(path: Path, payload: bytes) -> None:
    """Atomically write bytes without changing trace-bound evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_scores(
    path: Path,
    *,
    field: str,
    label: str,
    allow_missing: bool = False,
) -> tuple[dict[str, float], dict[str, Any]]:
    if not path.is_file():
        raise ExperimentError(f"{label} result does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read {label} result {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("subsets"), list):
        raise ExperimentError(f"{label} result {path} must contain a subsets list")
    scores: dict[str, float] = {}
    seen: set[str] = set()
    for index, subset in enumerate(payload["subsets"]):
        if not isinstance(subset, dict):
            raise ExperimentError(f"{label} result subset {index} must be an object")
        subset_id = subset.get("subset_id")
        if not isinstance(subset_id, str) or not subset_id:
            raise ExperimentError(f"{label} result subset {index} has no subset_id")
        if subset_id in seen:
            raise ExperimentError(f"{label} result contains duplicate subset_id {subset_id!r}")
        seen.add(subset_id)
        value = subset.get(field)
        if allow_missing and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExperimentError(f"{label} result subset {subset_id!r} has no numeric {field}")
        score = float(value)
        if not math.isfinite(score):
            raise ExperimentError(f"{label} result subset {subset_id!r} has non-finite {field}")
        scores[subset_id] = score
    return scores, payload
