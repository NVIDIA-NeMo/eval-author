#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep declarative package metadata aligned with the bundled runtime policy."""

from __future__ import annotations

import json
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
    expected_audit = "harbor" + harbor["supported"]["audit"]
    pins = [item for item in project["project"]["optional-dependencies"]["audit"] if item.startswith("harbor")]
    if pins != [expected_audit]:
        raise ValueError(f"Harbor audit requirement {pins} must match runtime policy: {expected_audit}")
    requirements = (root / "skills/eval-author-audit/requirements.txt").read_text().splitlines()
    pins = [line for line in requirements if line.startswith("harbor")]
    if pins != [expected_audit]:
        raise ValueError(f"Bundled audit requirement {pins} must match runtime policy: {expected_audit}")


if __name__ == "__main__":
    check()
    print("Runtime policy matches package metadata")
