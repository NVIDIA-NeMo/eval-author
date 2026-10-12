# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only inventory contracts, independent of provider installations."""

from __future__ import annotations

import errno
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author/scripts/search_tasks.py"


@pytest.fixture
def search(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    return importlib.import_module("search_tasks")


def populate(root, names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("invalid configuration; inventory must not parse or execute it\n")


def test_native_layouts_and_root_task(search, tmp_path):
    populate(
        tmp_path,
        [
            "task.toml",
            "instruction.md",
            "tests/test.bat",
            ".eval-author/task-drafts/new/task.toml",
            "nested/suite/task/task.toml",
            "environments/chat/manifest.yaml",
            "environments/chat/config.yaml",
            "environments/legacy/config.yaml",
            "environments/deleted/config.yaml",
            "environments/deleted/.nemo_gym_tombstone",
            "benchmarks/math/config.yaml",
            "benchmarks/math/config_easy.yaml",
            "benchmarks/math/configs/hard.yaml",
            "benchmarks/group/task/manifest.yaml",
            "resources_servers/tool/configs/tool.yaml",
            "resources_servers/tool/app.py",
            "extensions/python/task.toml",
            "extensions/python/tests/verifier.py",
            "extensions/rows/tasks.jsonl",
            "extensions/rows/instruction.template.md",
            "docs/example/manifest.yaml",
            "ordinary/config.yaml",
            "ordinary/test.py",
        ],
    )
    report = search.search_tasks(tmp_path)
    found = {item["path"]: item for item in report["candidates"]}
    assert report["search"]["complete"]
    assert found["."]["provider"] == "harbor"
    assert "tests/test.bat" in found["."]["evidence"]
    assert found[".eval-author/task-drafts/new"]["kind"] == "task"
    assert found["nested/suite/task"]["validation"] == "not_checked"
    assert found["environments/chat/manifest.yaml"]["evidence"] == [
        "environments/chat/config.yaml",
        "environments/chat/manifest.yaml",
    ]
    assert "environments/chat/config.yaml" not in found
    assert found["environments/legacy/config.yaml"]["kind"] == "legacy-config-candidate"
    assert found["environments/deleted/config.yaml"]["tombstone"] == "environments/deleted/.nemo_gym_tombstone"
    for name in ("config.yaml", "config_easy.yaml", "configs/hard.yaml"):
        assert found["benchmarks/math/" + name]["catalog_kind"] == "benchmark"
    assert found["benchmarks/group/task/manifest.yaml"]["catalog_tree"] == "benchmarks"
    assert found["resources_servers/tool/configs/tool.yaml"]["kind"] == "resource-config-candidate"
    assert found["extensions/python"]["kind"] == "python-verifier-task"
    assert found["extensions/rows"]["kind"] == "parameterized-task"
    assert found["docs/example/manifest.yaml"]["catalog_tree"] is None
    assert len(found) == 14
    assert "ordinary/config.yaml" not in found
    assert "resources_servers/tool/app.py" not in found


def test_repeatable_across_creation_order_cwd_and_filters(search, tmp_path, monkeypatch):
    names = ["z/task.toml", "A/task.toml", "benchmarks/Z/config.yaml", "benchmarks/a/config.yaml"]
    first, second = tmp_path / "first", tmp_path / "second"
    populate(first, names)
    populate(second, reversed(names))
    before = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    one = search.search_tasks(first)
    monkeypatch.chdir(tmp_path)
    two = search.search_tasks(Path("first"))
    assert one == two
    other = search.search_tasks(second)
    assert {k: v for k, v in one.items() if k != "repo"} == {k: v for k, v in other.items() if k != "repo"}
    selected = search.search_tasks(first, provider="gym", query="BENCHMARKS/z")
    assert [item["path"] for item in selected["candidates"]] == ["benchmarks/Z/config.yaml"]
    assert all(item["provider"] == "harbor" for item in search.search_tasks(first, provider="harbor")["candidates"])
    assert before == {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}


def test_symlinks_exclusions_and_explicit_roots(search, tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    populate(root, ["task/task.toml", ".venv/hidden/task.toml", "environments/item/config.yaml"])
    populate(outside, ["verifier.py", "task.toml"])
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "task/tests").symlink_to(outside, target_is_directory=True)
    (root / "environments/link.yaml").symlink_to(outside / "task.toml")
    report = search.search_tasks(root)
    assert {item["path"] for item in report["candidates"]} == {"task", "environments/item/config.yaml"}
    assert next(item for item in report["candidates"] if item["path"] == "task")["provider"] == "harbor"
    assert len(report["search"]["excluded"]) == 4
    assert search.search_tasks(root / ".venv")["candidates"][0]["path"] == "hidden"
    assert search.search_tasks(root / "environments")["candidates"][0]["catalog_tree"] == "."


def test_depth_and_entry_limits_are_incomplete_and_order_independent(search, tmp_path):
    names = ["z/task.toml", "a/task.toml", "middle/other.txt"]
    first, second = tmp_path / "first", tmp_path / "second"
    populate(first, names)
    populate(second, reversed(names))
    for root in (first, second):
        shallow = search.search_tasks(root, max_depth=0)
        assert not shallow["search"]["complete"]
        assert shallow["candidates"] == []
        limited = search.search_tasks(root, max_entries=2)
        assert limited["candidates"] == []
        assert limited["search"]["limits_reached"] == [{"path": ".", "limit": "max_entries"}]
        assert limited["search"]["directories_scanned"] == 0
    assert (
        search.search_tasks(first, max_entries=4)["candidates"]
        == search.search_tasks(second, max_entries=4)["candidates"]
    )


def test_unreadable_directory_is_reported(search, tmp_path, monkeypatch):
    populate(tmp_path, ["blocked/task.toml", "visible/task.toml"])
    original = search.os.scandir

    def scandir(path):
        if Path(path).name == "blocked":
            raise PermissionError(errno.EACCES, "denied")
        return original(path)

    monkeypatch.setattr(search.os, "scandir", scandir)
    report = search.search_tasks(tmp_path)
    assert not report["search"]["complete"]
    assert report["search"]["errors"] == [{"path": "blocked", "errno": errno.EACCES}]
    assert [item["path"] for item in report["candidates"]] == ["visible"]


@pytest.mark.parametrize("args,code", [([], 0), (["--max-depth", "0"], 1), (["--max-entries", "0"], 2)])
def test_copied_script_without_site_packages_or_provider_imports(tmp_path, args, code):
    script = tmp_path / "installed/scripts/search_tasks.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, script)
    repo = tmp_path / "repo"
    populate(repo, ["task/task.toml", "environments/task/manifest.yaml"])
    # These tripwires would fail if repository or provider modules were imported.
    for name in ("harbor.py", "nemo_gym.py", "yaml.py", "sitecustomize.py"):
        (repo / name).write_text('raise RuntimeError("must not import repository code")\n')
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "--repo", str(repo), *args], cwd=repo, text=True, capture_output=True
    )
    assert result.returncode == code, result.stderr
    data = json.loads(result.stdout)
    assert data["search"]["complete"] is (code == 0)


def test_missing_root_and_empty_root_are_distinct(search, tmp_path):
    with pytest.raises(FileNotFoundError):
        search.search_tasks(tmp_path / "absent")
    empty = search.search_tasks(tmp_path)
    assert empty["candidates"] == []
    assert empty["search"]["complete"]
