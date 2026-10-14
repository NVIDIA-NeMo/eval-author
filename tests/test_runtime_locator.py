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
    monkeypatch.setattr(locator, "PROVIDER_SECONDS", 0)
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


@pytest.mark.parametrize(
    "script,args",
    [
        ("locate_runtime.py", ["--help"]),
        ("runtime_policy.py", ["--install-spec", "harbor"]),
    ],
)
def test_repository_entrypoint_matches_bundled_helper(script, args, tmp_path):
    def run(path):
        return subprocess.run([PYTHON, "-I", str(path), *args], cwd=tmp_path, capture_output=True, text=True)

    bundled = run(SCRIPTS / script)
    entrypoint = run(ROOT / "tools" / script.replace("_", "-"))
    assert entrypoint.returncode == bundled.returncode == 0
    assert entrypoint.stdout == bundled.stdout
    assert entrypoint.stderr == bundled.stderr


@pytest.mark.parametrize("priority", ["explicit", "declared", "project", "global"])
def test_uv_failure_only_blocks_competing_priorities(locator, tmp_path, monkeypatch, priority):
    env = tmp_path / (".venv" if priority == "project" else "runtime")
    python, cli = runtime(env)
    uv = cli.parent / "uv"
    uv.write_text("#!/bin/sh\nexit 2\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(cli.parent))
    explicit = {"harbor": {"python": str(python)}} if priority == "explicit" else None
    if priority == "declared":
        (tmp_path / "pyproject.toml").write_text(f'[tool.eval-author.runtimes.harbor]\npython = "{python}"\n')
    report = locator.locate(tmp_path, explicit=explicit, providers=("harbor",))
    assert not report["search"]["complete"]
    assert bool(selected(report)) is (priority != "global")
    assert report["search"]["incomplete_priorities"]["harbor"] == [3]


@pytest.mark.parametrize(
    "dependency", ['harbor>=0.20; python_version >= "3.12"', "harbor[modal]>=0.20", "harbor~=0.20"]
)
def test_unsupported_requirement_preserves_candidates_and_other_provider(locator, tmp_path, dependency):
    harbor, _ = runtime(tmp_path / "harbor")
    gym, _ = runtime(tmp_path / "gym", provider="gym", version="0.6.0")
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies = [" + json.dumps(dependency) + "]\n")
    report = locator.locate(tmp_path, explicit={"harbor": {"python": str(harbor)}, "gym": {"python": str(gym)}})
    assert selected(report) is None
    assert selected(report, "gym")
    assert dependency in report["selection"]["harbor"]["reason"]
    assert report["candidates"][0]["checks"]
    assert report["requirement_errors"]["harbor"]


@pytest.mark.parametrize("name", ["nemo_gym", "NeMo.Gym", "nemo-gym"])
def test_normalized_project_requirement_is_enforced(locator, tmp_path, name):
    gym, _ = runtime(tmp_path / ".venv", provider="gym", version="0.6.0")
    (tmp_path / "pyproject.toml").write_text(f'[project]\ndependencies = ["{name}>=99.0"]\n')
    report = locator.locate(tmp_path, explicit={"gym": {"python": str(gym)}}, providers=("gym",))
    assert selected(report, "gym") is None
    assert report["candidates"][0]["status"] == "incompatible"


def test_invoking_interpreter_does_not_change_standalone_selection(locator, tmp_path, monkeypatch):
    first, _ = runtime(tmp_path / "unlisted")
    _, cli = runtime(tmp_path / "global")
    monkeypatch.setenv("PATH", str(cli.parent))
    before = locator.locate(tmp_path, providers=("harbor",))
    monkeypatch.setattr(sys, "executable", str(first))
    after = locator.locate(tmp_path, providers=("harbor",))
    assert before == after
    assert selected(after)["cli"] == str(cli)


def test_aliases_are_probed_once(locator, tmp_path, monkeypatch):
    python, cli = runtime(tmp_path / ".venv")
    alias = python.with_name("python3")
    cli.write_text(cli.read_text().replace(str(python), str(alias)))
    monkeypatch.setenv("PATH", os.pathsep.join([str(cli.parent)] * 2))
    report = locator.locate(tmp_path, providers=("harbor",))
    found = [c for c in report["candidates"] if c["status"] == "compatible"]
    assert len(found) == 1
    assert found[0]["sources"] == ["project", "PATH"]
    assert len(report["search"]["locations"]) == len(set(report["search"]["locations"]))


@pytest.mark.parametrize("unknown_priority,best_priority,can_select", [(2, 3, False), (3, 2, True), (2, 2, False)])
def test_timeout_uncertainty_respects_priority(locator, unknown_priority, best_priority, can_select):
    candidate = {
        "provider": "harbor",
        "status": "compatible",
        "priority": best_priority,
        "prefix": "/venv",
        "version": "0.20.0",
    }
    assert bool(locator.select([candidate], "harbor", [unknown_priority])["selected"]) is can_select


def test_missing_current_python_recommends_existing_runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author-discover/scripts"))
    renderer = importlib.import_module("render_report")
    report = {
        "runtime": {
            "harbor_importable": False,
            "harbor_cli": None,
            "harbor_probe": {
                "candidates": [{"status": "missing"}, {"status": "compatible", "python": "/other/bin/python"}],
                "selection": {"harbor": {"status": "unresolved"}},
            },
        }
    }
    text = renderer._harbor_setup_guidance(report)
    assert "/other/bin/python" in text and "rerun discovery" in text
    assert "uv tool install" not in text


@pytest.mark.parametrize("script", ["discover.py", "render_report.py"])
def test_missing_sibling_has_actionable_error(tmp_path, script):
    copied = tmp_path / "eval-author-discover"
    shutil.copytree(ROOT / "skills/eval-author-discover", copied, ignore=shutil.ignore_patterns("__pycache__"))
    result = subprocess.run([PYTHON, "-I", str(copied / "scripts" / script), "--help"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Incomplete skill installation" in result.stderr
    assert "Traceback" not in result.stderr


def test_python_below_minimum_reports_clean_error(tmp_path):
    # Exercise the early guard before tomllib can import, without requiring an old interpreter.
    command = "import runpy,sys; sys.version_info=(3,9,0); runpy.run_path(sys.argv[1],run_name='__main__')"
    result = subprocess.run(
        [PYTHON, "-I", "-c", command, str(SCRIPTS / "locate_runtime.py")], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "Python 3.11" in json.loads(result.stdout)["error"]
    assert "Traceback" not in result.stderr


def test_provider_budget_does_not_starve_other_provider(locator, tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(locator.time, "monotonic", lambda: now[0])
    seen = []

    def inspect(candidate, flow, deadline, policy):
        seen.append((candidate["provider"], deadline - now[0]))
        now[0] += 400 if candidate["provider"] == "harbor" else 1
        return {**candidate, "status": "compatible", "prefix": candidate["provider"], "version": "0.20.0"}

    monkeypatch.setattr(locator, "inspect", inspect)
    report = locator.locate(tmp_path)
    assert selected(report, "gym")
    assert seen[0] == ("gym", 390)
    assert ("harbor", 390) in seen
    assert report["search"]["incomplete_priorities"]["harbor"] == [2]
    assert report["search"]["incomplete_priorities"]["gym"] == []


def test_cold_gym_probe_uses_policy_timeout(locator, tmp_path, monkeypatch):
    python, _ = runtime(tmp_path / ".venv", provider="gym", version="0.6.0")
    calls = []
    original = locator.invoke

    def invoke(argv, deadline, timeout=locator.PROBE_SECONDS):
        calls.append(timeout)
        return original(argv, deadline, timeout)

    monkeypatch.setattr(locator, "invoke", invoke)
    report = locator.locate(tmp_path, explicit={"gym": {"python": str(python)}}, providers=("gym",))
    assert selected(report, "gym")
    assert calls and set(calls) == {60}


def test_timed_out_candidate_preserves_uncertainty(locator, tmp_path, monkeypatch):
    runtime(tmp_path / ".venv")
    monkeypatch.setattr(locator, "invoke", lambda *args: {"ok": False, "error": "timeout", "incomplete": True})
    report = locator.locate(tmp_path, providers=("harbor",))
    assert not report["search"]["complete"]
    assert report["search"]["incomplete_priorities"]["harbor"] == [2]
    assert report["candidates"][0]["status"] == "unresolved"


def test_parent_only_import_visibility_is_explained(locator, tmp_path, monkeypatch):
    python, _ = runtime(tmp_path / ".venv")
    site = next((tmp_path / ".venv/lib").glob("python*/site-packages"))
    (site / "harbor.py").unlink()
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(locator.importlib.util, "find_spec", lambda name: object())
    report = locator.locate(tmp_path, explicit={"harbor": {"python": str(python)}}, providers=("harbor",))
    assert "user site-packages and PYTHONPATH" in report["candidates"][0]["reason"]
    assert "ModuleNotFoundError" in report["candidates"][0]["reason"]


def test_uv_and_path_launcher_alias_share_one_probe(locator, tmp_path, monkeypatch):
    tools_root = tmp_path / "tools"
    python, cli = runtime(tools_root / "harbor")
    alias = python.with_name("python3")
    cli.write_text(cli.read_text().replace(str(python), str(alias)))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "harbor").symlink_to(cli)
    uv = bin_dir / "uv"
    uv.write_text(f"#!{PYTHON}\nprint({str(tools_root)!r})\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    report = locator.locate(tmp_path, providers=("harbor",))
    matches = [c for c in report["candidates"] if c["status"] == "compatible"]
    assert len(matches) == 1
    assert matches[0]["sources"] == ["PATH", "uv-tool"]


def test_importing_provider_preserves_discover_module_precedence():
    discovery = ROOT / "skills/eval-author-discover/scripts"
    command = (
        "import sys; sys.path.insert(0,sys.argv[1]); "
        "import providers.harbor._probe; "
        "assert sys.path.index(sys.argv[1]) < sys.path.index(sys.argv[2])"
    )
    result = subprocess.run([PYTHON, "-I", "-c", command, str(discovery), str(SCRIPTS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
