#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep declarative package metadata aligned with the bundled runtime policy."""

from __future__ import annotations

import importlib.util
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
    check_compass_pin(root)


def check_compass_pin(root: Path = ROOT) -> None:
    """Require every NeMo Compass pin to name the build that trace_loading.py checks for."""
    path = root / "skills/eval-author/scripts/trace_loading.py"
    spec = importlib.util.spec_from_file_location("_policy_trace_loading", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = f"git+{module.COMPASS_REPOSITORY}@{module.LOADER['revision']}"
    pattern = re.compile(r"nemo-compass(\[[A-Za-z0-9_,-]+\])? @ " + re.escape(expected))
    project = tomllib.loads((root / "pyproject.toml").read_text())
    required = {
        "pyproject.toml trace-loaders extra": project["project"]["optional-dependencies"]["trace-loaders"],
        "pyproject.toml dev group": project["dependency-groups"]["dev"],
    }
    sources = dict(required)
    for extra, items in project["project"]["optional-dependencies"].items():
        sources.setdefault(f"pyproject.toml {extra} extra", items)
    for requirements in sorted((root / "skills").glob("*/requirements.txt")):
        sources[str(requirements.relative_to(root))] = [line.strip() for line in requirements.read_text().splitlines()]
    for where, items in sources.items():
        pins = [item for item in items if item.startswith("nemo-compass")]
        if where in required and not pins:
            raise ValueError(f"{where} must require nemo-compass @ {expected}")
        for pin in pins:
            if not pattern.fullmatch(pin):
                raise ValueError(f"{where} pin {pin!r} must be exactly nemo-compass @ {expected}")


if __name__ == "__main__":
    check()
    print("Runtime policy matches package metadata")
