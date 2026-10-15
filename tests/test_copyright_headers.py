# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preserve signed NVSkills reports without exempting ordinary source files."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "copyright_headers", Path(__file__).resolve().parents[1] / "tools/check_copyright_headers.py"
)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr(checker, "PROJECT_ROOT", tmp_path)
    return tmp_path


@pytest.mark.parametrize("fix_nvidia", [False, True])
def test_signed_reports_are_accepted_without_modifying_bytes(repository, fix_nvidia):
    originals = {}
    for skill in ("ethos-explore", "ethos-orchestrate", "ethos-write"):
        for name in ("BENCHMARK.md", "skill-card.md"):
            path = repository / "team-skills/ethos" / skill / name
            path.parent.mkdir(parents=True, exist_ok=True)
            content = f"# Generated {name} for {skill}\r\nSigned report bytes.\r\n".encode()
            path.write_bytes(content)
            originals[path] = content

    assert checker.check_headers(fix_nvidia=fix_nvidia) == []
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("fix_nvidia", [False, True])
def test_report_exception_does_not_exempt_other_files(repository, fix_nvidia):
    paths = [
        "BENCHMARK.md",
        "skill-card.md",
        "skills/ethos-explore/BENCHMARK.md",
        "team-skills/ethos/another-skill/skill-card.md",
        "team-skills/ethos/ethos-explore/README.md",
        "team-skills/ethos/ethos-orchestrate/SKILL.md",
        "team-skills/ethos/ethos-write/scripts/validate_ethos.py",
        "team-skills/ethos/ethos-write/evals/BENCHMARK.md",
    ]
    for name in paths:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Ordinary source without a header.\n")

    invalid = checker.check_headers(fix_nvidia=fix_nvidia)

    if fix_nvidia:
        assert invalid == []
        for name in paths:
            path = repository / name
            assert checker._has_nvidia_header(path.read_text(), checker._comment_style(path))
    else:
        assert set(invalid) == {Path(name) for name in paths}
