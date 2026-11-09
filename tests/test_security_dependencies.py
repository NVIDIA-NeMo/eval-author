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

SECURITY_FLOORS = {
    "aiohttp": "3.14.4",
    "filelock": "4.2.1",
    "gitpython": "3.2.1",
    "litellm": "1.101.3",
    "pyasn1": "0.6.5",
    "setuptools": "84.1.0",
    "virtualenv": "21.14.13",
}


@pytest.mark.parametrize("name,floor", SECURITY_FLOORS.items())
def test_root_security_dependencies_meet_patched_floors(name, floor):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == name]
    assert versions, f"Missing locked dependency: {name}"
    assert all(Version(version) >= Version(floor) for version in versions), (name, versions, floor)

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    constraints = [Requirement(value) for value in project["tool"]["uv"]["constraint-dependencies"]]
    assert any(
        constraint.name.lower() == name and str(constraint.specifier) == f">={floor}" for constraint in constraints
    ), f"Missing security constraint: {name}>={floor}"


def test_security_updates_preserve_runtime_pins_and_existing_override():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    for name, version in {"harbor": "0.24.0", "nemo-compass": "0.1.0"}.items():
        assert [p["version"] for p in lock["package"] if p["name"] == name] == [version]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["tool"]["uv"]["override-dependencies"] == ["protobuf>=7.36.0"]


def test_locked_certifi_excludes_compromised_trust_store():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == "certifi"]
    assert versions
    assert all(tuple(map(int, version.split("."))) >= (2026, 10, 19) for version in versions)


def test_external_reusable_workflows_are_immutable():
    for path in (ROOT / ".github/workflows").iterdir():
        if path.suffix not in {".yml", ".yaml"}:
            continue
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            reference = job.get("uses", "")
            if reference and not reference.startswith("./"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), (path.name, reference)
