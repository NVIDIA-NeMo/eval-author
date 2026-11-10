# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]

PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
SECURITY_CONSTRAINTS = [Requirement(value) for value in PROJECT["tool"]["uv"]["constraint-dependencies"]]


@pytest.mark.parametrize("constraint", SECURITY_CONSTRAINTS, ids=str)
def test_root_security_dependencies_meet_patched_floors(constraint):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == constraint.name.lower()]
    assert versions, f"Missing locked dependency: {constraint.name}"
    assert all(Version(version) in constraint.specifier for version in versions), (constraint, versions)


def test_external_reusable_workflows_are_immutable():
    for path in (ROOT / ".github/workflows").iterdir():
        if path.suffix not in {".yml", ".yaml"}:
            continue
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            reference = job.get("uses", "")
            if reference and not reference.startswith("./"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), (path.name, reference)
