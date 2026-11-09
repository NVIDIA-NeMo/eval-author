# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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
