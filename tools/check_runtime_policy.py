#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep declarative package metadata aligned with the bundled runtime policy."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path = ROOT) -> None:
    policy = json.loads((root / "skills/eval-author/scripts/runtime-policy.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())
    harbor = policy["providers"]["harbor"]
    expected = "harbor==" + harbor["test_profiles"]["ci"]["version"]
    pins = [item for item in project["dependency-groups"]["dev"] if item.startswith("harbor")]
    if pins != [expected]:
        raise ValueError(
            f"Harbor dev pin {pins} must match runtime policy: {expected}; regenerate uv.lock after changes"
        )
    check_trace_ingest(root)


def check_trace_ingest(root: Path = ROOT) -> None:
    """Require every NeMo Compass trace-ingest pin to match trace_loading.TRACE_INGEST_VERSION."""
    module = ast.parse((root / "skills/eval-author/scripts/trace_loading.py").read_text())
    versions = [
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "TRACE_INGEST_VERSION" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(versions) != 1:
        raise ValueError("trace_loading.py must define TRACE_INGEST_VERSION exactly once as a string")
    pattern = re.compile(r"nemo-compass-trace-ingest(\[[A-Za-z0-9_,-]+\])?==" + re.escape(versions[0]))
    project = tomllib.loads((root / "pyproject.toml").read_text())
    required = {
        "pyproject.toml trace-loaders extra": project["project"]["optional-dependencies"]["trace-loaders"],
        "pyproject.toml dev group": project["dependency-groups"]["dev"],
    }
    sources = dict(required)
    for extra, items in project["project"]["optional-dependencies"].items():
        sources.setdefault(f"pyproject.toml {extra} extra", items)
    for path in sorted((root / "skills").glob("*/requirements.txt")):
        sources[str(path.relative_to(root))] = [line.strip() for line in path.read_text().splitlines()]
    for where, items in sources.items():
        pins = [item for item in items if item.startswith("nemo-compass-trace-ingest")]
        if where in required and not pins:
            raise ValueError(f"{where} must require nemo-compass-trace-ingest=={versions[0]}")
        for pin in pins:
            if not pattern.fullmatch(pin):
                raise ValueError(f"{where} pin {pin!r} must be exactly nemo-compass-trace-ingest=={versions[0]}")


if __name__ == "__main__":
    check()
    print("Runtime policy matches package metadata")
