# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The isolated evaluator has no unlocked dependency or build fallback."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def tools_path(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))


def installer():
    path = ROOT / "tools/install_evaluator.py"
    assert path.is_file(), "shared locked installer missing"
    spec = importlib.util.spec_from_file_location("install_evaluator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("extra", ["security", "tier2", "tier3"])
def test_install_enforces_hashes_then_builds_only_pinned_evaluator(monkeypatch, extra):
    module = installer()
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    module.install("/fresh/bin/python", extra)
    dependencies, source, check = [c[0] for c in calls]
    assert "--require-hashes" in dependencies
    assert dependencies[dependencies.index("--only-binary") + 1] == ":all:"
    assert dependencies[dependencies.index("--requirements") + 1] == str(module.LOCKS / f"{extra}.txt")
    assert "--no-deps" in source and "--no-build-isolation" in source
    assert source[-1] == (
        f"skillevaluator[{extra}] @ git+https://github.com/NVIDIA/SkillEvaluator.git@{module.EVALUATOR_REVISION}"
    )
    assert check == ["uv", "pip", "check", "--python", "/fresh/bin/python", "--no-config"]
    assert all(c[1]["check"] for c in calls)
    assert all("--no-config" in c[0] for c in calls)


def test_failed_dependency_install_never_builds_source(monkeypatch):
    module = installer()
    calls = []

    def fail(argv, **kwargs):
        calls.append(argv)
        raise module.subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(module.subprocess.CalledProcessError):
        module.install("/fresh/bin/python", "tier3")
    assert len(calls) == 1


def test_install_drops_credentials_and_resolver_overrides(monkeypatch):
    module = installer()
    calls = []
    for name in ("OPENAI_API_KEY", "INFERENCE_HUB_API_KEY", "UV_NO_VERIFY_HASHES", "UV_OVERRIDE", "PYTHONPATH"):
        monkeypatch.setenv(name, "must-not-reach-installer")
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **kw: calls.append(kw["env"]))
    module.install("/fresh/bin/python", "tier2")
    assert all("must-not-reach-installer" not in env.values() for env in calls)


@pytest.mark.parametrize("extra", ["security", "tier2", "tier3"])
def test_generated_closure_is_pinned_hashed_and_includes_build_backend(extra):
    import re

    from packaging.requirements import Requirement

    module = installer()
    source = (module.LOCKS / f"{extra}.in").read_text()
    assert (
        f"skillevaluator[{extra}] @ git+https://github.com/NVIDIA/SkillEvaluator.git@{module.EVALUATOR_REVISION}"
        in source
    )
    text = (module.LOCKS / f"{extra}.txt").read_text().replace("\\\n", " ")
    names = set()
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        requirement, *hashes = line.split("--hash=")
        parsed = Requirement(requirement.strip())
        assert parsed.url is None
        specs = list(parsed.specifier)
        assert len(specs) == 1 and specs[0].operator == "==" and "*" not in specs[0].version
        assert hashes and all(re.fullmatch(r"sha256:[0-9a-f]{64}", value.strip()) for value in hashes)
        names.add(parsed.name)
    assert {"setuptools", "wheel", "packaging"} <= names
    assert "skillevaluator" not in names
    assert ("harbor" in names) == (extra == "tier3")
    if extra == "tier3":
        assert "harbor==0.24.0" in text


def test_corrupt_dependency_wheel_fails_before_git_install(tmp_path, monkeypatch, capfd):
    import hashlib
    import shutil
    import zipfile

    if shutil.which("uv") is None:
        pytest.skip("uv is required for the keyless hash-enforcement probe")
    module = installer()
    wheel = tmp_path / "lock_probe-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("lock_probe-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: lock-probe\nVersion: 1.0\n")
        archive.writestr(
            "lock_probe-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        archive.writestr("lock_probe-1.0.dist-info/RECORD", "")
    original_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("lock_probe.py", "tampered = True\n")
    (tmp_path / "tier2.txt").write_text(f"lock-probe @ {wheel.as_uri()} --hash=sha256:{original_hash}\n")
    monkeypatch.setattr(module, "LOCKS", tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path))
    import venv

    target = tmp_path / "venv"
    venv.EnvBuilder(symlinks=True).create(target)
    python = str(target / "bin/python")
    with pytest.raises(module.subprocess.CalledProcessError):
        module.install(python, "tier2")
    assert "Hash mismatch" in capfd.readouterr().err
    result = module.subprocess.run(
        [python, "-c", "import importlib.metadata as m; print([d.metadata['Name'] for d in m.distributions()])"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"


def test_unknown_profile_fails_before_install(monkeypatch):
    module = installer()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("must not install"))
    with pytest.raises(ValueError, match="extra"):
        module.install("/fresh/bin/python", "all")
