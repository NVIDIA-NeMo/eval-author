# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise discovery with isolated real interpreters and adversarial launchers."""

from __future__ import annotations

import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
PYTHON = sys.executable


@pytest.fixture
def locator(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("locate_runtime")
    policy = copy.deepcopy(module.load())
    for provider in policy["providers"].values():
        provider["python"] = ">=3.11"
        for flow in provider["capabilities"]:
            provider["capabilities"][flow] = {"imports": ["harbor:Present"], "help": [[]]}
    monkeypatch.setattr(module, "load", lambda: policy)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "absent/bin/python"))
    return module


def runtime(root: Path, *, version="0.20.0", cli_version=None, provider="harbor") -> tuple[Path, Path]:
    subprocess.run([PYTHON, "-m", "venv", "--without-pip", str(root)], check=True, capture_output=True)
    python = root / "bin/python"
    result = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True,
        text=True,
        capture_output=True,
    )
    site = Path(result.stdout.strip())
    (site / "harbor.py").write_text("class Present: pass\n")
    package = "harbor" if provider == "harbor" else "nemo-gym"
    metadata = site / (package.replace("-", "_") + "-" + version + ".dist-info")
    metadata.mkdir()
    (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n")
    cli = root / "bin" / ("harbor" if provider == "harbor" else "gym")
    output = ("NeMo Gym v" if provider == "gym" else "") + (cli_version or version)
    cli.write_text(f"#!{python}\nimport sys\nprint({output!r} if '--version' in sys.argv else 'Usage: runtime')\n")
    cli.chmod(0o755)
    return python, cli


def selected(report, provider="harbor"):
    return report["selection"][provider]["selected"]


def test_project_beats_global_but_records_both(locator, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    python, _ = runtime(project / ".venv")
    _, global_cli = runtime(tmp_path / "global")
    monkeypatch.setenv("PATH", str(global_cli.parent))
    report = locator.locate(project, providers=("harbor",))
    assert selected(report)["python"] == str(python)
    assert any(item["cli"] == str(global_cli) and item["status"] == "compatible" for item in report["candidates"])
    assert report["search"]["complete"]


def test_two_venvs_sharing_base_python_remain_ambiguous(locator, tmp_path):
    first, _ = runtime(tmp_path / ".venv")
    second, _ = runtime(tmp_path / "venv")
    assert first.resolve() == second.resolve()
    report = locator.locate(tmp_path, providers=("harbor",))
    assert report["selection"]["harbor"]["status"] == "ambiguous"
    assert selected(report) is None


@pytest.mark.parametrize("declared", [False, True])
def test_broken_authoritative_path_never_falls_back(locator, tmp_path, declared):
    runtime(tmp_path / ".venv")
    missing = str(tmp_path / "missing/bin/python")
    explicit = {"harbor": {"python": missing}}
    if declared:
        (tmp_path / "pyproject.toml").write_text(f'[tool.eval-author.runtimes.harbor]\npython = "{missing}"\n')
        explicit = None
    report = locator.locate(tmp_path, explicit=explicit, providers=("harbor",))
    assert selected(report) is None
    assert any(item["status"] == "compatible" for item in report["candidates"])
    assert any(item["status"] == "missing" and item["python"] == missing for item in report["candidates"])


def test_duplicate_path_sightings_do_not_create_ambiguity(locator, tmp_path, monkeypatch):
    _, cli = runtime(tmp_path / "tool")
    monkeypatch.setenv("PATH", os.pathsep.join([str(cli.parent)] * 2))
    report = locator.locate(tmp_path, providers=("harbor",))
    assert selected(report)["cli"] == str(cli)
    assert len([item for item in report["candidates"] if item["cli"] == str(cli)]) == 1


def test_capabilities_are_flow_specific(locator, tmp_path):
    runtime(tmp_path / ".venv")
    locator.load()["providers"]["harbor"]["capabilities"]["discovery"]["imports"] = ["harbor:Absent"]
    assert selected(locator.locate(tmp_path, flow="audit", providers=("harbor",)))
    report = locator.locate(tmp_path, flow="discovery", providers=("harbor",))
    assert selected(report) is None
    assert any("Absent" in item["reason"] for item in report["candidates"])


@pytest.mark.parametrize("version,cli_version", [("0.19.0", None), ("0.20.0rc1", None), ("0.20.0", "0.23.0")])
def test_reject_old_prerelease_and_mismatched_cli(locator, tmp_path, version, cli_version):
    runtime(tmp_path / ".venv", version=version, cli_version=cli_version)
    report = locator.locate(tmp_path, providers=("harbor",))
    assert selected(report) is None
    candidate = next(item for item in report["candidates"] if item["priority"] == 2 and item["status"] != "missing")
    assert candidate["version"] == version
    assert candidate["reason"]


def test_project_constraint_applies_to_explicit_runtime(locator, tmp_path):
    python, _ = runtime(tmp_path / ".venv")
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["harbor>=0.23.0"]\n')
    report = locator.locate(tmp_path, explicit={"harbor": {"python": str(python)}}, providers=("harbor",))
    assert selected(report) is None
    assert report["candidates"][0]["status"] == "incompatible"


@pytest.mark.parametrize(
    "text",
    [
        "[broken",
        "[tool.eval-author.runtimes.harbor]\npython = 42",
        "[project]\ndependencies = [\"harbor>=0.20.0; python_version > '3.11'\"]",
    ],
)
def test_invalid_or_unsupported_declarations_fail_closed(locator, tmp_path, text):
    (tmp_path / "pyproject.toml").write_text(text)
    with pytest.raises(ValueError):
        locator.locate(tmp_path, providers=("harbor",))


def test_foreign_cli_and_unknown_launcher_are_unresolved(locator, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "gym"
    cli.write_text('#!/bin/sh\necho "gym: workout tracker"\n')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    report = locator.locate(tmp_path, providers=("gym",))
    assert selected(report, "gym") is None
    assert any("identify launcher" in item["reason"] for item in report["candidates"])


def test_python_requirement_is_checked_separately(locator, tmp_path):
    runtime(tmp_path / ".venv", provider="gym", version="0.6.0")
    locator.load()["providers"]["gym"]["python"] = ">=9.0.0"
    report = locator.locate(tmp_path, providers=("gym",))
    assert selected(report, "gym") is None
    assert any(item["status"] == "incompatible" for item in report["candidates"])


def test_candidate_cap_and_time_budget_cannot_produce_selection(locator, tmp_path, monkeypatch):
    runtime(tmp_path / ".venv")
    monkeypatch.setattr(locator, "MAX_CANDIDATES", 1)
    report = locator.locate(tmp_path, providers=("harbor",))
    assert report["search"]["omitted_candidates"] > 0
    assert not report["search"]["complete"]
    assert selected(report) is None
    monkeypatch.setattr(locator, "TOTAL_SECONDS", 0)
    report = locator.locate(tmp_path, providers=("harbor",))
    assert not report["search"]["complete"]
    assert selected(report) is None


def test_existing_uv_tool_is_discovered_without_install(locator, tmp_path, monkeypatch):
    tools_root = tmp_path / "tools"
    python, _ = runtime(tools_root / "harbor")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    log = tmp_path / "uv-args.json"
    uv.write_text(
        f"#!{PYTHON}\nimport sys, json\nfrom pathlib import Path\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"print({str(tools_root)!r})\n"
    )
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    report = locator.locate(tmp_path, providers=("harbor",))
    assert selected(report)["python"] == str(python)
    assert json.loads(log.read_text()) == ["tool", "dir"]


def test_shell_trampoline_with_spaces_is_read_not_evaluated(locator, tmp_path):
    python = tmp_path / "with spaces/bin/python"
    cli = tmp_path / "harbor"
    cli.write_text(f"#!/bin/sh\n'''exec' \"{python}\" \"$0\" \"$@\"\n' '''\n")
    assert locator.launcher_python(str(cli)) == str(python)
    cli.write_text('#!/bin/sh\nexec "$(touch UNEXPECTED)" "$0"\n')
    assert locator.launcher_python(str(cli)) is None
    assert not (tmp_path / "UNEXPECTED").exists()


def test_policy_pins_and_standalone_cli():
    result = subprocess.run([PYTHON, str(ROOT / "tools/check_runtime_policy.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [PYTHON, "-I", str(SCRIPTS / "runtime_policy.py"), "--install-spec", "harbor"], capture_output=True, text=True
    )
    policy = json.loads((SCRIPTS / "runtime-policy.json").read_text())["providers"]["harbor"]
    assert result.stdout.strip() == "harbor==" + policy["test_profiles"][policy["install_profile"]]["version"]


def test_relative_cli_is_resolved_against_repo(locator, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _, cli = runtime(repo / ".venv")
    monkeypatch.chdir(tmp_path)
    report = locator.locate(repo, explicit={"harbor": {"cli": ".venv/bin/harbor"}}, providers=("harbor",))
    assert selected(report)["cli"] == str(cli)


def test_python_alias_in_same_venv_is_accepted(locator, tmp_path):
    python, _ = runtime(tmp_path / ".venv")
    alias = python.with_name("python3")
    report = locator.locate(tmp_path, explicit={"harbor": {"python": str(alias)}}, providers=("harbor",))
    assert selected(report)["python"] == str(alias)


@pytest.mark.parametrize(
    "text", ['tool = "x"', 'project = "x"', '[tool]\neval-author = "x"', '[project]\ndependencies = "harbor"']
)
def test_wrong_toml_shapes_are_reported(locator, tmp_path, text):
    (tmp_path / "pyproject.toml").write_text(text)
    with pytest.raises(ValueError):
        locator.locate(tmp_path, providers=("harbor",))


def test_pin_drift_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    checker = importlib.import_module("check_runtime_policy")
    policy_dir = tmp_path / "skills/eval-author/scripts"
    policy_dir.mkdir(parents=True)
    (policy_dir / "runtime-policy.json").write_bytes((SCRIPTS / "runtime-policy.json").read_bytes())
    project = (ROOT / "pyproject.toml").read_text().replace('"harbor==0.20.0"', '"harbor==99.0.0"')
    (tmp_path / "pyproject.toml").write_text(project)
    with pytest.raises(ValueError, match="must match runtime policy"):
        checker.check(tmp_path)


def test_broken_runtime_report_does_not_recommend_another_install(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author-discover/scripts"))
    renderer = importlib.import_module("render_report")
    report = {
        "runtime": {
            "harbor_importable": False,
            "harbor_cli": None,
            "harbor_probe": {"candidates": [{"status": "broken", "reason": "import failed"}]},
        }
    }
    text = renderer._harbor_setup_guidance(report)
    assert "failure reasons" in text
    assert "uv tool install" not in text


def test_shared_helpers_in_copied_skill_installation(tmp_path):
    installed = tmp_path / "installed-skills"
    for name in ("eval-author", "eval-author-discover"):
        shutil.copytree(ROOT / "skills" / name, installed / name, ignore=shutil.ignore_patterns("__pycache__"))
    shared = installed / "eval-author/scripts"
    discovery = installed / "eval-author-discover/scripts"
    for script, args in (
        (shared / "locate_runtime.py", ["--help"]),
        (shared / "runtime_policy.py", ["--install-spec", "harbor"]),
        (discovery / "discover.py", ["--help"]),
        (discovery / "render_report.py", ["--help"]),
    ):
        result = subprocess.run([PYTHON, "-I", str(script), *args], cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
