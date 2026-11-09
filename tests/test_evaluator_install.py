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
    dependencies, source, check, policy = [c[0] for c in calls]
    assert policy == [
        "/fresh/bin/python",
        "-s",
        "-B",
        str(ROOT / "tools/install_evaluator.py"),
        "--check-environment",
        "--extra",
        extra,
    ]
    assert "--require-hashes" in dependencies
    assert dependencies[dependencies.index("--only-binary") + 1] == ":all:"
    assert dependencies[dependencies.index("--requirements") + 1] == str(module.LOCKS / f"{extra}.txt")
    assert "--no-deps" in source and "--no-build-isolation" in source
    assert source[-1] == (
        f"skillevaluator[{extra}] @ git+https://github.com/NVIDIA/SkillEvaluator.git@{module.EVALUATOR_REVISION}"
    )
    assert check == ["uv", "pip", "check", "--python", "/fresh/bin/python", "--no-config"]
    assert all(c[1]["check"] for c in calls)
    assert all("--no-config" in c[0] for c in calls[:3])


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


def test_llm_profiles_pin_reviewed_security_override():
    from packaging.requirements import Requirement

    module = installer()
    for extra in ("tier2", "tier3"):
        text = (module.LOCKS / f"{extra}.txt").read_text()
        assert "litellm==1.101.3 \\" in text
        assert "--overrides tools/evaluator-locks/llm-overrides.in" in text
    source = (module.LOCKS / "llm-overrides.in").read_text()
    requirements = [Requirement(line) for line in source.splitlines() if line and not line.startswith("#")]
    assert [str(requirement) for requirement in requirements] == ["litellm==1.101.3"]
    assert "<1.94.0.dev0" in source and "metadata" in source


def policy_distributions(module, *, litellm="1.101.3", requirement=None, revision=None):
    import json
    from email.message import Message
    from types import SimpleNamespace

    def dist(name, version, requires=(), extras=()):
        metadata = Message()
        metadata["Name"] = name
        for extra in extras:
            metadata["Provides-Extra"] = extra
        return SimpleNamespace(metadata=metadata, version=version, requires=list(requires))

    evaluator = dist(
        "skillevaluator",
        "0.7.0",
        [
            'skillevaluator[llm]; extra == "tier2"',
            'skillevaluator[llm]; extra == "tier3"',
            requirement or 'litellm<1.94.0.dev0,>=1.92.0; extra == "llm"',
        ],
        extras=("security", "tier2", "tier3", "llm"),
    )
    evaluator.read_text = lambda name: json.dumps(
        {
            "url": "https://github.com/NVIDIA/SkillEvaluator.git",
            "vcs_info": {
                "vcs": "git",
                "commit_id": revision or module.EVALUATOR_REVISION,
            },
        }
    )
    return [
        evaluator,
        dist("litellm", litellm, ["child[crypto]>=2"]),
        dist("child", "2", ['crypto>=3; extra == "crypto"'], extras=("crypto",)),
        dist("crypto", "3"),
    ]


@pytest.mark.parametrize("extra", ["tier2", "tier3"])
def test_compatibility_policy_reports_exact_metadata_exception(extra, capsys):
    module = installer()
    module.validate_environment(extra, policy_distributions(module))
    assert "intentional upstream metadata conflict" in capsys.readouterr().out


@pytest.mark.parametrize(
    "mutation", ["old-pin", "other-pin", "new-cap", "revision", "missing", "transitive", "other-parent"]
)
def test_compatibility_policy_rejects_every_unreviewed_conflict(mutation):
    module = installer()
    kwargs = {
        "old-pin": {"litellm": "1.93.2"},
        "other-pin": {"litellm": "1.101.4"},
        "new-cap": {"requirement": 'litellm<1.95; extra == "llm"'},
        "revision": {"revision": "0" * 40},
    }.get(mutation, {})
    distributions = policy_distributions(module, **kwargs)
    if mutation == "missing":
        distributions.pop()
    if mutation == "transitive":
        distributions[-1].version = "2"
    if mutation == "other-parent":
        distributions[-1].requires = ["litellm<1.94"]
    with pytest.raises(RuntimeError):
        module.validate_environment("tier3", distributions)


@pytest.mark.parametrize("root", [True, False])
def test_compatibility_policy_rejects_undeclared_extras(root):
    module = installer()
    distributions = policy_distributions(module)
    target = distributions[0] if root else distributions[2]
    del target.metadata["Provides-Extra"]
    with pytest.raises(RuntimeError, match="undeclared extra"):
        module.validate_environment("tier2", distributions)


def test_compatibility_policy_normalizes_extra_names():
    module = installer()
    distributions = policy_distributions(module)
    distributions[1].requires = ["child[Crypto_Feature]>=2"]
    distributions[2].metadata["Provides-Extra"] = "crypto-feature"
    distributions[2].requires = ['crypto>=3; extra == "crypto.feature"']
    module.validate_environment("tier2", distributions)
    distributions.pop()
    with pytest.raises(RuntimeError, match="missing active dependency"):
        module.validate_environment("tier2", distributions)


@pytest.mark.parametrize("stage", [2, 3])
def test_install_propagates_dependency_check_failures(monkeypatch, stage):
    module = installer()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == stage + 1:
            raise module.subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(module.subprocess.CalledProcessError):
        module.install("/fresh/bin/python", "tier2")
    assert len(calls) == stage + 1


def test_security_profile_has_no_metadata_exception(capsys):
    module = installer()
    distributions = policy_distributions(module)
    distributions = [distributions[0]]
    module.validate_environment("security", distributions)
    assert not capsys.readouterr().out


def test_compatibility_policy_requires_declared_exception():
    module = installer()
    distributions = policy_distributions(module)
    distributions[0].requires = []
    with pytest.raises(RuntimeError, match="requirement missing"):
        module.validate_environment("tier2", distributions)


def test_compatibility_policy_ignores_inactive_markers():
    module = installer()
    distributions = policy_distributions(module)
    distributions[-1].requires = ['absent; python_version < "2"', 'absent; extra == "unused"']
    module.validate_environment("tier2", distributions)


@pytest.mark.parametrize(
    "override",
    ["litellm>=1.101.3", "litellm==1.101.*", "other==1", "litellm==1.101.3\nother==1", "litellm[proxy]==1.101.3"],
)
def test_compatibility_policy_rejects_broad_overrides(tmp_path, monkeypatch, override):
    module = installer()
    (tmp_path / "llm-overrides.in").write_text(override)
    monkeypatch.setattr(module, "LOCKS", tmp_path)
    with pytest.raises(RuntimeError, match="override"):
        module.validate_environment("tier2", policy_distributions(module))


def test_unknown_profile_fails_before_install(monkeypatch):
    module = installer()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("must not install"))
    with pytest.raises(ValueError, match="extra"):
        module.install("/fresh/bin/python", "all")
