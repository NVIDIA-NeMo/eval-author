# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from generation_benchmark.models import GenerationLock
from generation_benchmark.signing import check_experiment, lock_path_for, sign_experiment, verify_lock_entries
from metric_benchmark.io import digest_file, read_json
from test_generation_benchmark_models import write_sample_experiment

GEN_LAB_ROOT = Path(__file__).resolve().parents[1] / "labs" / "generation-benchmark"
SIGN_TOOL = GEN_LAB_ROOT / "tools" / "sign-generation"


def _write_signable_experiment(root: Path, *, with_setup: bool = True, case_id: str = "smoke") -> Path:
    exp = write_sample_experiment(root, with_setup=with_setup, case_id=case_id)
    (exp.parent / "local_outputs.py").write_text(
        "from pathlib import Path\n"
        "from generation_benchmark.models import DirectoryModel, ScoreOutput\n\n"
        "class LocalSetupOutput(DirectoryModel):\n"
        "    marker: str\n"
        "    @classmethod\n"
        "    def load(cls, directory: Path) -> 'LocalSetupOutput':\n"
        "        marker = directory / 'marker.txt'\n"
        "        if not marker.is_file():\n"
        "            raise ValueError(f'missing marker.txt in {directory}')\n"
        "        return cls(marker=marker.read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    if with_setup:
        raw = yaml.safe_load(exp.read_text(encoding="utf-8"))
        raw["metric"]["setup"]["output"]["type"] = "local_outputs:LocalSetupOutput"
        exp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return exp


def _cli(experiment: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SIGN_TOOL), str(experiment), *flags],
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot_yamls(root: Path) -> dict[Path, bytes]:
    return {p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*.yml"))}


def test_sign_and_check_cli_round_trip_without_editing_yaml(tmp_path: Path) -> None:
    exp = _write_signable_experiment(tmp_path / "exp", with_setup=True)
    before_yamls = _snapshot_yamls(exp.parent)

    res_sign = _cli(exp)
    assert res_sign.returncode == 0, res_sign.stderr
    assert _snapshot_yamls(exp.parent) == before_yamls

    lock_path = lock_path_for(exp)
    assert lock_path.is_file()
    lock_payload = read_json(lock_path)
    assert isinstance(lock_payload, dict)
    assert lock_payload["schema"] == "nemo.eval_author.generation_lock.v1"

    lock = GenerationLock.model_validate(lock_payload)
    assert "benchmark_id" not in lock.entries
    assert "revision" not in lock.entries
    assert "execution.n_concurrent_trials" not in lock.entries
    assert "metric.setup.output.type" in lock.entries
    assert "cases[smoke].agent.inputs.source" in lock.entries
    assert "cases[smoke].settings.items" in lock.entries
    assert "cases[smoke].agent.digest" not in lock.entries

    res_check = _cli(exp, "--check")
    assert res_check.returncode == 0, res_check.stderr

    # Second sign without --resign fails and lists every existing key
    res_dup = _cli(exp)
    assert res_dup.returncode != 0
    for key in lock.entries:
        assert key in res_dup.stderr


def test_comments_key_order_and_non_identity_fields(tmp_path: Path) -> None:
    exp = _write_signable_experiment(tmp_path / "exp", with_setup=True)
    sign_experiment(exp)
    lock_before = GenerationLock.model_validate(read_json(lock_path_for(exp)))

    # Reorder keys, add YAML comments, change benchmark_id, revision, and n_concurrent_trials
    raw = yaml.safe_load(exp.read_text(encoding="utf-8"))
    reordered = {
        "generation": raw["generation"],
        "metric": raw["metric"],
        "execution": {
            "environment": raw["execution"]["environment"],
            "n_concurrent_trials": 8,
            "harbor_version": raw["execution"]["harbor_version"],
            "backend": raw["execution"]["backend"],
        },
        "cases": raw["cases"],
        "revision": 99,
        "benchmark_id": "renamed-benchmark",
    }
    exp.write_text("# comment only\n" + yaml.safe_dump(reordered, sort_keys=False), encoding="utf-8")

    # Non-identity edits leave every lock entry unchanged and --check passes
    _, lock_checked = check_experiment(exp)
    assert lock_checked.entries == lock_before.entries

    # Changing harbor_version changes execution block digest
    reordered["execution"]["harbor_version"] = "0.25.0"
    exp.write_text(yaml.safe_dump(reordered, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="execution"):
        check_experiment(exp)
    sign_experiment(exp, resign=True)
    lock_after_harbor = GenerationLock.model_validate(read_json(lock_path_for(exp)))
    assert lock_after_harbor.entries["execution"].sha256 != lock_before.entries["execution"].sha256

    # Changing command string changes metric or generation
    reordered["generation"]["command"] = [sys.executable, "-c", "print('changed')"]
    exp.write_text(yaml.safe_dump(reordered, sort_keys=False), encoding="utf-8")
    sign_experiment(exp, resign=True)
    lock_after_gen = GenerationLock.model_validate(read_json(lock_path_for(exp)))
    assert lock_after_gen.entries["generation"].sha256 != lock_after_harbor.entries["generation"].sha256


def test_moved_ethos_missing_and_orphan_keys_in_check(tmp_path: Path) -> None:
    exp = write_sample_experiment(tmp_path / "exp", with_setup=False)
    sign_experiment(exp)
    lock_path = lock_path_for(exp)
    initial_lock = GenerationLock.model_validate(read_json(lock_path))
    ethos_sha_before = initial_lock.entries["cases[smoke].ethos"].sha256

    # Move ethos.md and update cases.yml
    old_ethos = exp.parent / "agents/smoke/ethos.md"
    new_ethos = exp.parent / "agents/smoke/moved_ethos.md"
    new_ethos.write_bytes(old_ethos.read_bytes())
    old_ethos.unlink()

    cases_path = exp.parent / "cases.yml"
    cases_data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    cases_data[0]["ethos"]["path"] = "agents/smoke/moved_ethos.md"
    cases_path.write_text(yaml.safe_dump(cases_data, sort_keys=False), encoding="utf-8")

    # --check reports the changed path without modifying the lock file
    lock_bytes_before = lock_path.read_bytes()
    res_moved = _cli(exp, "--check")
    assert res_moved.returncode != 0
    assert "cases[smoke].ethos" in res_moved.stderr
    assert "path" in res_moved.stderr
    assert lock_path.read_bytes() == lock_bytes_before

    # --resign updates path while keeping same key and ethos sha256; --check then passes
    sign_experiment(exp, resign=True)
    resigned_lock = GenerationLock.model_validate(read_json(lock_path))
    assert resigned_lock.entries["cases[smoke].ethos"].path == "agents/smoke/moved_ethos.md"
    assert resigned_lock.entries["cases[smoke].ethos"].sha256 == ethos_sha_before
    check_experiment(exp)

    # Missing key in lock fails --check naming the key and writes nothing
    payload = read_json(lock_path)
    assert isinstance(payload, dict)
    entries = payload["entries"]
    assert isinstance(entries, dict)
    removed_entry = entries.pop("cases[smoke].ethos")
    lock_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    before_missing = lock_path.read_bytes()
    with pytest.raises(ValueError, match=r"missing lock key: cases\[smoke\]\.ethos"):
        check_experiment(exp)
    assert lock_path.read_bytes() == before_missing

    # Orphan key in lock fails --check naming the orphan key
    entries["cases[smoke].ethos"] = removed_entry
    entries["cases[smoke].orphan"] = removed_entry
    lock_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"orphan lock key: cases\[smoke\]\.orphan"):
        check_experiment(exp)


def test_generation_settings_and_digest_methods_in_lock(tmp_path: Path) -> None:
    exp = _write_signable_experiment(tmp_path / "exp", with_setup=True)
    root = exp.parent
    gen_dir = root / "generator_src"
    gen_dir.mkdir()
    (gen_dir / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (gen_dir / "skip.tmp").write_text("ignored\n", encoding="utf-8")
    prompt_file = root / "prompt.txt"
    prompt_file.write_text("v1\n", encoding="utf-8")

    # Add generation.settings path ref and generation.digest (glob)
    exp_data = yaml.safe_load(exp.read_text(encoding="utf-8"))
    exp_data["generation"]["settings"] = {"prompt": {"path": "prompt.txt"}}
    exp_data["generation"]["digest"] = {
        "method": "glob",
        "patterns": ["generator_src/**/*"],
        "excludes": ["**/*.tmp"],
    }
    exp_data["metric"]["score"]["digest"] = {
        "method": "command",
        "command": [sys.executable, "-c", "import pathlib; print(pathlib.Path('prompt.txt').read_text())"],
    }
    exp.write_text(yaml.safe_dump(exp_data, sort_keys=False), encoding="utf-8")

    # Add agent.yml digest
    agent_path = root / "agents/smoke/agent.yml"
    agent_data = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent_data["digest"] = {"method": "command", "command": [sys.executable, "-c", "print('agent-v1')"]}
    agent_path.write_text(yaml.safe_dump(agent_data, sort_keys=False), encoding="utf-8")

    sign_experiment(exp)
    lock_v1 = GenerationLock.model_validate(read_json(lock_path_for(exp)))
    assert "generation.digest" in lock_v1.entries
    assert "metric.score.digest" in lock_v1.entries
    assert "metric.setup.digest" not in lock_v1.entries
    assert "cases[smoke].agent.digest" in lock_v1.entries
    assert "generation.settings.prompt" in lock_v1.entries

    # Changing excluded file does NOT change generation.digest or --check
    (gen_dir / "skip.tmp").write_text("ignored-modified\n", encoding="utf-8")
    check_experiment(exp)

    # Changing matched glob file changes generation.digest and generation block
    (gen_dir / "keep.py").write_text("x = 2\n", encoding="utf-8")
    sign_experiment(exp, resign=True)
    lock_v2 = GenerationLock.model_validate(read_json(lock_path_for(exp)))
    assert lock_v2.entries["generation.digest"].sha256 != lock_v1.entries["generation.digest"].sha256
    assert lock_v2.entries["generation"].sha256 != lock_v1.entries["generation"].sha256

    # Changing generation.settings file changes generation.settings.prompt AND generation block,
    # and also changes command digest stdout here
    prompt_file.write_text("v2\n", encoding="utf-8")
    sign_experiment(exp, resign=True)
    lock_v3 = GenerationLock.model_validate(read_json(lock_path_for(exp)))
    assert lock_v3.entries["generation.settings.prompt"].sha256 != lock_v2.entries["generation.settings.prompt"].sha256
    assert lock_v3.entries["generation"].sha256 != lock_v2.entries["generation"].sha256
    assert lock_v3.entries["metric.score.digest"].sha256 != lock_v2.entries["metric.score.digest"].sha256

    # Empty glob match fails sign-generation
    exp_data["generation"]["digest"] = {"method": "glob", "patterns": ["generator_src/*.none"]}
    exp.write_text(yaml.safe_dump(exp_data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="matched no files"):
        sign_experiment(exp, resign=True)

    # Non-zero command digest exit fails sign-generation
    exp_data["generation"]["digest"] = {"method": "command", "command": [sys.executable, "-c", "raise SystemExit(3)"]}
    exp.write_text(yaml.safe_dump(exp_data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="digest command exited 3"):
        sign_experiment(exp, resign=True)


def test_output_module_resolution_and_stage_verify_subset(tmp_path: Path) -> None:
    exp = write_sample_experiment(tmp_path / "exp", with_setup=False)
    mod_file = exp.parent / "local_outputs.py"
    mod_file.write_text(
        "from pathlib import Path\n"
        "from generation_benchmark.models import ScoreOutput\n"
        "class MyScore(ScoreOutput):\n"
        "    @classmethod\n"
        "    def load(cls, directory: Path) -> 'MyScore':\n"
        "        return cls(score=ScoreOutput.load(directory).score)\n",
        encoding="utf-8",
    )
    exp_data = yaml.safe_load(exp.read_text(encoding="utf-8"))
    exp_data["metric"]["score"]["output"] = {
        "python": sys.executable,
        "type": "local_outputs:MyScore",
    }
    exp.write_text(yaml.safe_dump(exp_data, sort_keys=False), encoding="utf-8")

    sign_experiment(exp)
    computed, actual = check_experiment(exp)
    assert actual.entries["metric.score.output.type"].type == "module"
    assert actual.entries["metric.score.output.type"].selector == "MyScore"
    assert actual.entries["metric.score.output.type"].sha256 == digest_file(mod_file)

    verified = verify_lock_entries(actual, computed.lock, ["cases[smoke].ethos", "metric.score.output.type"])
    assert set(verified.keys()) == {"cases[smoke].ethos", "metric.score.output.type"}

    # Editing the output module file fails --check naming metric.score.output.type
    mod_file.write_text(mod_file.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"metric\.score\.output\.type"):
        check_experiment(exp)

    # Non-ScoreOutput declared for metric.score.output fails signing without writing lock
    (exp.parent / "bad_outputs.py").write_text(
        "from pathlib import Path\n"
        "from generation_benchmark.models import DirectoryModel\n"
        "class NotAScore(DirectoryModel):\n"
        "    @classmethod\n"
        "    def load(cls, directory: Path) -> 'NotAScore':\n"
        "        return cls()\n",
        encoding="utf-8",
    )
    exp_data["metric"]["score"]["output"]["type"] = "bad_outputs:NotAScore"
    exp.write_text(yaml.safe_dump(exp_data, sort_keys=False), encoding="utf-8")
    lock_before = lock_path_for(exp).read_bytes()
    with pytest.raises(ValueError, match="ScoreOutput"):
        sign_experiment(exp, resign=True)
    assert lock_path_for(exp).read_bytes() == lock_before
