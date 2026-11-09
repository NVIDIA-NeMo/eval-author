# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import stat
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "discover_scenarios.py"


@pytest.fixture
def scenarios(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("W", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spec = importlib.util.spec_from_file_location("discover_scenarios_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    monkeypatch.setattr(module, "show", lambda result: None)
    monkeypatch.setattr(module, "write_grid", lambda rows: "")
    return module


def test_default_runs_use_fresh_private_workspaces(scenarios, monkeypatch, tmp_path, capsys):
    roots = []
    monkeypatch.setattr(scenarios, "STEPS", [lambda: roots.append(scenarios.WORK_DIR)])
    scenarios.main()
    scenarios.main()
    output = capsys.readouterr().out
    assert len(set(roots)) == 2
    for root in roots:
        assert root.is_absolute()
        assert root.parent == tmp_path
        assert root.is_dir()
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert str(root) in output


@pytest.mark.parametrize("kind", ["symlink", "unmarked", "public", "file", "empty-value"])
def test_unsafe_explicit_roots_are_rejected_without_deletion(scenarios, monkeypatch, tmp_path, kind):
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o700)
    sentinel = victim / "fixtures" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep")
    root = victim
    if kind == "symlink":
        root = tmp_path / "link"
        root.symlink_to(victim, target_is_directory=True)
    elif kind == "public":
        victim.chmod(0o777)
    elif kind == "file":
        root = sentinel
    monkeypatch.setenv("W", "" if kind == "empty-value" else str(root))
    # Keep a regressed step 1 offline, but exercise its real deletion path.
    monkeypatch.setattr(scenarios, "build_harbor_fixtures", lambda: None)
    monkeypatch.setattr(scenarios, "build_gym_fixtures", lambda: None)
    monkeypatch.setattr(scenarios, "create_venv", lambda *args: None)
    monkeypatch.setattr(scenarios, "discover", lambda *args: None)
    monkeypatch.setattr(scenarios, "STEPS", [scenarios.step_01])
    try:
        with pytest.raises(SystemExit, match="[Ww]ork directory|W must"):
            scenarios.main()
    finally:
        assert sentinel.read_text() == "keep"


@pytest.mark.parametrize("work", [None, "missing", "empty"])
def test_resume_requires_initialized_explicit_workspace(scenarios, monkeypatch, tmp_path, work):
    if work is not None:
        root = tmp_path / work
        if work == "empty":
            root.mkdir(mode=0o700)
        monkeypatch.setenv("W", str(root))
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "2"])
    monkeypatch.setattr(scenarios, "STEPS", [lambda: None, lambda: None])
    with pytest.raises(SystemExit, match="[Rr]esum|[Ii]nitialized"):
        scenarios.main()
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("existing", [False, True])
def test_explicit_relative_workspace_can_resume_without_reset(scenarios, monkeypatch, tmp_path, existing):
    root = tmp_path / "my work"
    if existing:
        root.mkdir(mode=0o700)
    monkeypatch.setenv("W", "my work")
    monkeypatch.setattr(scenarios, "STEPS", [lambda: None])
    scenarios.main()
    sentinel = root / "fixtures" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "2"])
    visited = []
    monkeypatch.setattr(scenarios, "STEPS", [lambda: pytest.fail("must not reset"), lambda: visited.append(True)])
    scenarios.main()
    assert visited == [True]
    assert scenarios.WORK_DIR == root
    assert sentinel.read_text() == "keep"


@pytest.mark.parametrize("name", ["fixtures", "harbor-repo", "gym-repo", "reports", "venv-harbor", "venv-gym"])
def test_step_one_rejects_linked_children_before_any_cleanup(scenarios, monkeypatch, tmp_path, name):
    monkeypatch.setattr(scenarios, "build_harbor_fixtures", lambda: None)
    monkeypatch.setattr(scenarios, "build_gym_fixtures", lambda: None)
    monkeypatch.setattr(scenarios, "create_venv", lambda *args: None)
    monkeypatch.setattr(scenarios, "discover", lambda *args: None)
    scenarios.prepare_work_dir()
    keep = scenarios.WORK_DIR / "fixtures" / "keep.txt"
    keep.parent.mkdir()
    keep.write_text("keep")
    target = tmp_path / "outside"
    target.mkdir()
    outside = target / "keep.txt"
    outside.write_text("outside")
    child = scenarios.WORK_DIR / name
    if name == "fixtures":
        keep.unlink()
        child.rmdir()
    child.symlink_to(target, target_is_directory=True)
    with pytest.raises(SystemExit, match="[Ww]ork directory"):
        scenarios.step_01()
    assert outside.read_text() == "outside"
    if name != "fixtures":
        assert keep.read_text() == "keep"


def test_step_one_resets_only_managed_directories(scenarios, monkeypatch, tmp_path):
    scenarios.prepare_work_dir()
    for name in ("fixtures", "harbor-repo", "gym-repo", "reports", "venv-harbor", "venv-gym"):
        folder = scenarios.WORK_DIR / name
        folder.mkdir()
        (folder / "old.txt").write_text("old")
    unrelated = scenarios.WORK_DIR / "notes.txt"
    unrelated.write_text("keep")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (scenarios.FIXTURES / "outside-link").symlink_to(outside)
    monkeypatch.setattr(scenarios, "build_gym_fixtures", lambda: None)

    def uv_venv(command, **kwargs):
        Path(command[-1]).mkdir()

    monkeypatch.setattr(scenarios.subprocess, "run", uv_venv)
    monkeypatch.setattr(scenarios, "discover", lambda *args: None)
    scenarios.step_01()
    assert not list(scenarios.WORK_DIR.glob("*/old.txt"))
    assert (scenarios.FIXTURES / "harbor" / "add-1" / "task.toml").is_file()
    assert scenarios.VENV_HARBOR.is_dir()
    assert scenarios.VENV_GYM.is_dir()
    assert unrelated.read_text() == "keep"
    assert outside.read_text() == "outside"


def test_step_one_rejects_root_replaced_with_symlink(scenarios, tmp_path):
    scenarios.prepare_work_dir()
    root = scenarios.WORK_DIR
    saved = tmp_path / "saved"
    root.rename(saved)
    root.symlink_to(saved, target_is_directory=True)
    sentinel = saved / "fixtures" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep")
    with pytest.raises(SystemExit, match="[Ww]ork directory"):
        scenarios.step_01()
    assert sentinel.read_text() == "keep"


def test_explicit_root_rejects_symlink_ancestor_before_cleanup(scenarios, monkeypatch, tmp_path):
    scenarios.prepare_work_dir()
    victim = scenarios.WORK_DIR
    sentinel = victim / "fixtures" / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep")
    alias = tmp_path / "alias"
    alias.symlink_to(victim.parent, target_is_directory=True)
    monkeypatch.setenv("W", str(alias / victim.name))
    with pytest.raises(SystemExit, match="ancestor"):
        scenarios.prepare_work_dir()
    assert sentinel.read_text() == "keep"


def test_explicit_root_rejects_writable_ancestor_before_creation(scenarios, monkeypatch, tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    root = parent / "new"
    monkeypatch.setenv("W", str(root))
    with pytest.raises(SystemExit, match="ancestor"):
        scenarios.prepare_work_dir()
    assert not root.exists()


def test_explicit_root_accepts_sticky_shared_parent(scenarios, monkeypatch, tmp_path):
    parent = tmp_path / "sticky"
    parent.mkdir()
    parent.chmod(0o1777)
    monkeypatch.setenv("W", str(parent / "private"))
    scenarios.prepare_work_dir()
    assert stat.S_IMODE(scenarios.WORK_DIR.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system alias")
def test_system_temporary_alias_is_canonicalized_without_writes(scenarios):
    assert scenarios.checked_ancestry(Path("/tmp/uncreated-probe")) == Path("/private/tmp/uncreated-probe")


def test_list_does_not_create_workspace(scenarios, monkeypatch, tmp_path, capsys):
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--list"])
    scenarios.main()
    assert "20." in capsys.readouterr().out
    assert set(tmp_path.iterdir()) == before
