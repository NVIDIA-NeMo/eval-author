# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import re
import shutil
import subprocess
import sys

import pytest
import yaml
from metric_benchmark.config import load_benchmark
from metric_benchmark.signing import check_benchmark, sign_benchmark
from test_metric_benchmark_config import AGENT_DIR, LAB, TASK_DIR, prepared_benchmark


def manifest_bytes(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*.yml")}


def remove_pins(root):
    for path in root.rglob("*.yml"):
        path.write_bytes(re.sub(rb"(?m)^\s*sha256: [^\r\n]*\r?\n", b"", path.read_bytes()))


def unsigned_benchmark(root):
    experiment, _ = prepared_benchmark(root)
    remove_pins(root)
    return experiment


def sha_cli(experiment, *flags):
    return subprocess.run(
        [sys.executable, str(LAB / "tools/sha-sign"), str(experiment), *flags],
        capture_output=True,
        text=True,
        check=False,
    )


def test_unsigned_to_signed_library_and_cli_round_trip(tmp_path):
    experiment = unsigned_benchmark(tmp_path)
    agent_path = tmp_path / AGENT_DIR / "agent.yml"
    agent = yaml.safe_load(agent_path.read_text())
    bundle = tmp_path / AGENT_DIR / "baseline"
    bundle.mkdir()
    (bundle / "agent.py").write_text("print('prepared agent')\n")
    agent["baseline"]["inputs"] = {"bundle": {"path": bundle.relative_to(tmp_path).as_posix(), "kind": "directory"}}
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False))
    with pytest.raises(ValueError, match="missing SHA"):
        load_benchmark(experiment)
    unsigned = load_benchmark(experiment, allow_unsigned=True)
    assert unsigned.allow_unsigned
    assert unsigned.case("rho-agent").allow_unsigned
    before = manifest_bytes(tmp_path)
    check = sha_cli(experiment, "--check")
    assert check.returncode == 2 and "missing SHA" in check.stderr
    assert manifest_bytes(tmp_path) == before
    signed = sha_cli(experiment)
    assert signed.returncode == 0, signed.stderr
    check_benchmark(experiment)
    assert not load_benchmark(experiment).allow_unsigned
    assert sha_cli(experiment, "--check").returncode == 0
    assert sign_benchmark(experiment, resign=True) == []
    (bundle / "agent.py").write_text("print('changed agent')\n")
    with pytest.raises(ValueError, match="input digest differs"):
        load_benchmark(experiment, allow_unsigned=True)
    with pytest.raises(ValueError, match="input digest differs"):
        check_benchmark(experiment)
    assert sha_cli(experiment, "--resign").returncode == 0
    check_benchmark(experiment)


@pytest.mark.parametrize("flow", [False, True])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_resign_preserves_comments_quotes_and_formatting(tmp_path, flow, newline):
    experiment, _ = prepared_benchmark(tmp_path)
    if flow:
        raw = experiment.read_text()
        raw = re.sub(
            r"  agent:\n    path: ([^\n]+)\n    sha256: ([^\n]+)\n",
            r"""  agent: {path: '\1', sha256: "\2"} # keep inline comment\n""",
            raw,
        )
        experiment.write_text(raw)
    agent_path = tmp_path / AGENT_DIR / "agent.yml"
    agent_path.write_text(
        re.sub(r"sha256: (sha256:[a-f0-9]+)", r"sha256: '\1' # keep SHA comment", agent_path.read_text())
    )
    for path in tmp_path.rglob("*.yml"):
        path.write_bytes(path.read_bytes().replace(b"\n", newline))
    before = manifest_bytes(tmp_path)
    (tmp_path / AGENT_DIR / "description.json").write_text('{"required": ["changed-behavior"]}\n')
    sign_benchmark(experiment, resign=True)
    check_benchmark(experiment)
    after = manifest_bytes(tmp_path)
    assert before.keys() == after.keys()
    for name in before:
        assert re.sub(rb"sha256:[a-f0-9]{64}", b"sha256:<PIN>", before[name]) == re.sub(
            rb"sha256:[a-f0-9]{64}", b"sha256:<PIN>", after[name]
        )
    assert b"Synthetic stand-ins" in after[AGENT_DIR / "agent.yml"]
    assert sign_benchmark(experiment, resign=True) == []
    assert manifest_bytes(tmp_path) == after


@pytest.mark.parametrize("style", ["block", "flow", "null"])
def test_sign_inserts_missing_sha_without_losing_comments(tmp_path, style):
    experiment = unsigned_benchmark(tmp_path)
    raw = experiment.read_text()
    if style == "flow":
        raw = raw.replace(
            "  agent:\n    path: agents/rho-agent/agent.yml\n",
            "  agent: {path: agents/rho-agent/agent.yml} # retained\n",
        )
    elif style == "null":
        raw = raw.replace(
            "    path: agents/rho-agent/agent.yml\n", "    path: agents/rho-agent/agent.yml\n    sha256: # retained\n"
        )
    else:
        raw = raw.replace("    path: agents/rho-agent/agent.yml\n", "    path: agents/rho-agent/agent.yml # retained\n")
    experiment.write_text(raw)
    sign_benchmark(experiment, resign=style == "null")
    check_benchmark(experiment)
    assert "# retained" in experiment.read_text()
    assert sign_benchmark(experiment, resign=True) == []


def test_signing_refusal_and_invalid_input_leave_every_manifest_unchanged(tmp_path):
    experiment, _ = prepared_benchmark(tmp_path)
    before = manifest_bytes(tmp_path)
    result = sha_cli(experiment)
    assert result.returncode == 2
    assert "--resign" in result.stderr
    assert "experiment.yml:cases[rho-agent].agent" in result.stderr
    assert "agents/rho-agent/agent.yml:description" in result.stderr
    assert manifest_bytes(tmp_path) == before
    # A later missing task directory must be discovered before any earlier pins are written.
    shutil.rmtree(tmp_path / TASK_DIR / "search-workspace")
    with pytest.raises(ValueError, match="missing pinned input"):
        sign_benchmark(experiment, resign=True)
    assert manifest_bytes(tmp_path) == before
    result = sha_cli(experiment, "--check", "--resign")
    assert result.returncode == 2
