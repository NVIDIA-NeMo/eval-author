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


def _symlink(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows runner lacks symlink creation privilege")
        raise


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
    _symlink(root / "linked", outside, directory=True)
    _symlink(root / "task/tests", outside, directory=True)
    _symlink(root / "environments/link.yaml", outside / "task.toml")
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


@pytest.mark.parametrize("spec", ["Dockerfile", "docker-compose.yaml", "docker-compose.yml", "Apptainer.def"])
def test_harbor_environment_and_verifier_evidence(search, tmp_path, spec):
    names = [
        "single/task.toml",
        "single/instruction.md",
        f"single/environment/{spec}",
        "single/tests/test.sh",
        "single/tests/verifier.sh",
        f"single/tests/{spec}",
        "single/verifier.sh",
        "single/solution/solve.sh",
    ]
    populate(tmp_path, names)
    report = search.search_tasks(tmp_path)
    assert len(report["candidates"]) == 1
    task = report["candidates"][0]
    assert task["provider"] == "harbor"
    assert task["evidence"] == sorted(names)
    assert task["observed_steps"] == []


def test_multi_step_task_without_root_instruction_or_verifier(search, tmp_path):
    names = [
        "multi/task.toml",
        "multi/environment/Dockerfile",
        "multi/steps/z-first/instruction.md",
        "multi/steps/z-first/tests/test.sh",
        "multi/steps/z-first/tests/verifier.sh",
        "multi/steps/z-first/solution/solve.sh",
        "multi/steps/a-second/instruction.md",
        "multi/steps/a-second/tests/test.bat",
        "multi/steps/a-second/tests/Dockerfile",
        "multi/steps/a-second/workdir/setup.sh",
    ]
    populate(tmp_path, names)
    tasks = search.search_tasks(tmp_path)["candidates"]
    assert len(tasks) == 1
    assert tasks[0]["path"] == "multi"
    assert tasks[0]["provider"] == "harbor"
    assert tasks[0]["evidence"] == sorted(names)
    assert tasks[0]["observed_steps"] == ["multi/steps/a-second", "multi/steps/z-first"]
    assert tasks[0]["validation"] == "not_checked"


def test_prebuilt_and_base_verifier_fallback_need_no_dockerfile(search, tmp_path):
    names = ["task.toml", "tests/test.sh", "steps/one/instruction.md", "steps/two/instruction.md"]
    populate(tmp_path, names)
    (tmp_path / "task.toml").write_text('[environment]\ndocker_image = "example:test"\n')
    tasks = search.search_tasks(tmp_path)["candidates"]
    assert len(tasks) == 1
    assert tasks[0]["path"] == "."
    assert tasks[0]["evidence"] == sorted(names)
    assert tasks[0]["observed_steps"] == ["steps/one", "steps/two"]


def test_support_files_alone_do_not_make_a_task(search, tmp_path):
    populate(tmp_path, ["Dockerfile", "verifier.sh", "tests/test.sh", "steps/one/instruction.md"])
    assert search.search_tasks(tmp_path)["candidates"] == []


def test_step_evidence_obeys_bounds_and_symlink_exclusions(search, tmp_path):
    root, outside = tmp_path / "repo", tmp_path / "outside"
    populate(root, ["task.toml", "steps/deep/instruction.md", "environment/Dockerfile"])
    populate(outside, ["instruction.md"])
    _symlink(root / "steps/linked", outside, directory=True)
    report = search.search_tasks(root, max_depth=1)
    assert not report["search"]["complete"]
    assert report["candidates"][0]["observed_steps"] == []
    assert report["candidates"][0]["evidence"] == ["environment/Dockerfile", "task.toml"]
    report = search.search_tasks(root)
    assert report["candidates"][0]["observed_steps"] == ["steps/deep"]


def test_nested_task_markers_belong_to_the_nearest_task_root(search, tmp_path):
    populate(tmp_path, ["task.toml", "steps/child/task.toml", "steps/child/instruction.md"])
    tasks = search.search_tasks(tmp_path)["candidates"]
    assert tasks[0]["path"] == "."
    assert tasks[0]["evidence"] == ["task.toml"]
    assert tasks[0]["observed_steps"] == []
    assert tasks[1]["path"] == "steps/child"
    assert tasks[1]["evidence"] == ["steps/child/instruction.md", "steps/child/task.toml"]


@pytest.mark.parametrize("extension", ["sh", "bat"])
def test_solution_and_verifier_scripts_for_posix_and_windows(search, tmp_path, extension):
    names = [
        "task.toml",
        "instruction.md",
        "tests/helpers.py",
        f"tests/test.{extension}",
        f"solution/solve.{extension}",
        "steps/first/instruction.md",
        f"steps/first/tests/test.{extension}",
        f"steps/first/solution/solve.{extension}",
        "steps/first/tests/helpers.py",
    ]
    populate(tmp_path, names)
    task = search.search_tasks(tmp_path)["candidates"][0]
    assert task["provider"] == "harbor"
    assert task["evidence"] == sorted(names)
    assert task["observed_steps"] == ["steps/first"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction behavior")
def test_windows_junction_does_not_search_outside_root(search, tmp_path):
    outside = tmp_path / "outside"
    populate(outside, ["task.toml"])
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "linked"), str(outside)], check=True, capture_output=True)
    report = search.search_tasks(root)
    assert report["candidates"] == []
    assert report["search"]["complete"]
    assert report["search"]["excluded"] == [{"path": "linked", "reason": "reparse-point"}]


@pytest.mark.parametrize("git_directory", [False, True])
def test_nested_repository_excluded_but_explicit_root_allowed(search, tmp_path, git_directory):
    populate(tmp_path, ["owned/task.toml", "nested/task.toml"])
    marker = tmp_path / "nested/.git"
    marker.mkdir() if git_directory else marker.write_text("gitdir: elsewhere")
    report = search.search_tasks(tmp_path)
    assert [item["path"] for item in report["candidates"]] == ["owned"]
    assert {"path": "nested", "reason": "nested-repository"} in report["search"]["excluded"]
    assert [item["path"] for item in search.search_tasks(tmp_path / "nested")["candidates"]] == ["."]


def test_unreadable_root_is_cli_error(search, tmp_path, monkeypatch, capsys):
    def denied(path):
        raise PermissionError(errno.EACCES, "denied", str(path))

    monkeypatch.setattr(search.os, "scandir", denied)
    monkeypatch.setattr(sys, "argv", ["search_tasks", "--repo", str(tmp_path)])
    assert search.main() == 2
    assert json.loads(capsys.readouterr().out)["search"]["complete"] is False


def test_limit_sample_is_bounded_and_sorted(search, tmp_path):
    for index in range(150):
        (tmp_path / f"d{index:03}").mkdir()
    report = search.search_tasks(tmp_path, max_depth=0)["search"]
    assert report["limits_reached_count"] == 150
    assert len(report["limits_reached"]) == 100
    assert [item["path"] for item in report["limits_reached"]] == [f"d{i:03}" for i in range(100)]


def test_resource_server_root_requires_server_component(search, tmp_path):
    root = tmp_path / "resources_servers"
    populate(root, ["configs/stray.yaml", "server/configs/valid.yaml"])
    assert [item["path"] for item in search.search_tasks(root)["candidates"]] == ["server/configs/valid.yaml"]


def test_regular_reparse_file_remains_marker(search, tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    populate(tmp_path, ["task.toml"])

    class Entry:
        name = "task.toml"

        def stat(self, **kwargs):
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)

    class Listing:
        def __enter__(self):
            return iter([Entry()])

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(search.os, "scandir", lambda path: Listing())
    assert search.search_tasks(tmp_path)["candidates"][0]["path"] == "."


def test_unlimited_search_crosses_default_depth(search, tmp_path):
    path = "/".join(["deep"] * 15) + "/task.toml"
    populate(tmp_path, [path])
    assert not search.search_tasks(tmp_path)["search"]["complete"]
    report = search.search_tasks(tmp_path, max_depth=None, max_entries=None)
    assert report["search"]["complete"]
    assert len(report["candidates"]) == 1


def test_nested_repository_listings_consume_entry_budget(search, tmp_path):
    populate(tmp_path, ["a/.git", "a/task.toml", "b/.git", "b/task.toml"])
    report = search.search_tasks(tmp_path, max_entries=5)
    assert not report["search"]["complete"]
    assert report["search"]["entries_retained"] == 4
    assert report["search"]["limits_reached"] == [{"path": "b", "limit": "max_entries"}]
    assert report["candidates"] == []
