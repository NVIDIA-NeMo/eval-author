# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/live_shard_apt.sh"
PACKAGES = ["python3", "python3-venv", "git", "curl", "ca-certificates", "bash", "iptables", "iproute2", "xz-utils"]


@pytest.fixture
def apt_commands(tmp_path):
    """Record Linux APT/chown boundaries; run real shell, chmod, and file moves."""
    binary = tmp_path / "bin"
    binary.mkdir()
    archive = tmp_path / "archives"
    archive.mkdir()
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"active": 0, "peak": 0, "calls": [], "finished": []}))
    fake = binary / "apt-get"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import fcntl, json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
state = Path(os.environ["APT_TEST_STATE"])
def update(callback):
    with state.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        value = json.load(stream)
        callback(value)
        stream.seek(0)
        json.dump(value, stream)
        stream.truncate()
def record(value):
    value["calls"].append([Path(sys.argv[0]).name, args])
update(record)
if Path(sys.argv[0]).name == "chown":
    if args[0] == "_apt:root":
        assert Path(args[1]).stat().st_mode & 0o777 == 0o700
    sys.exit(int(os.environ.get("APT_TEST_CHOWN_EXIT", "0")))
if Path(sys.argv[0]).name == "apt-get":
    if "--print-uris" in args:
        print(os.environ["APT_TEST_PLAN"], end="")
        sys.exit(int(os.environ.get("APT_TEST_PLAN_EXIT", "0")))
    assert "--no-download" in args
    assert not json.loads(state.read_text())["active"]
    archive = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("Dir::Cache::archives=")))
    for destination in json.loads(state.read_text())["finished"]:
        assert (archive / Path(destination).name).read_bytes() == b"synthetic package"
    sys.exit(0)
def start(value):
    value["active"] += 1
    value["peak"] = max(value["peak"], value["active"])
update(start)
time.sleep(0.1)
triples = args[args.index("download-file") + 1:]
assert len(triples) % 3 == 0
for url, destination, digest in zip(triples[::3], triples[1::3], triples[2::3]):
    Path(destination).write_bytes(b"synthetic package")
def finish(value):
    value["active"] -= 1
    value["finished"].extend(triples[1::3])
update(finish)
fail = os.environ.get("APT_TEST_DOWNLOAD_FAIL")
sys.exit(1 if fail and (fail == "1" or any(fail in name for name in triples[1::3])) else 0)
"""
    )
    fake.chmod(0o755)
    (binary / "apt-helper").symlink_to(fake)
    (binary / "chown").symlink_to(fake)
    env = {
        "PATH": str(binary) + os.pathsep + os.defpath,
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "APT_TEST_STATE": str(state),
        "APT_TEST_PLAN": "".join(plan_entry(i) for i in range(9)),
    }

    def run(**changes):
        result = subprocess.run(
            ["/bin/bash", str(HELPER), str(archive), *PACKAGES],
            env={**env, **changes},
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result, json.loads(state.read_text())

    return run, archive


def plan_entry(index):
    name = f"pkg{index}_1%3a2.0+ubuntu1_arm64.deb"
    return (
        f"'https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/pool/main/p/pkg/{name}' {name} 123 SHA256:{'a' * 64}\n"
    )


def test_bounded_acquisition_precedes_same_no_download_install(apt_commands):
    run, archive = apt_commands
    result, state = run()
    assert result.returncode == 0, result.stderr
    assert 1 < state["peak"] <= 4
    assert len(state["finished"]) == 9
    apt = [args for name, args in state["calls"] if name == "apt-get"]
    assert len(apt) == 2
    assert "--print-uris" in apt[0] and "Acquire::ForceHash=SHA256" in apt[0]
    assert "--no-download" in apt[1]
    for args in apt:
        assert args[args.index("install") + 1 :] == PACKAGES
        assert "--no-install-recommends" in args
        assert "-y" in args
        assert f"Dir::Cache::archives={archive}" in args
    downloads = [args for name, args in state["calls"] if name == "apt-helper"]
    assert len(downloads) == 4
    assert sorted((len(args) - 3) // 3 for args in downloads) == [2, 2, 2, 3]
    for args in downloads:
        assert args[:3] == ["-o", "Acquire::Retries=0", "download-file"]
        for url, destination, digest in zip(args[3::3], args[4::3], args[5::3]):
            assert digest == "SHA256:" + "a" * 64
            assert Path(destination).parent.parent == archive
            assert url.endswith(Path(destination).name)
    assert result.stdout.splitlines() == [
        "EA_TOOLING_STAGE=apt_acquire",
        "EA_TOOLING_STAGE=apt_plan_validate",
        "EA_TOOLING_STAGE=apt_staging",
        "EA_TOOLING_STAGE=apt_download",
        "EA_TOOLING_STAGE=apt_promote",
        "EA_TOOLING_STAGE=apt_install",
    ]


@pytest.mark.parametrize(
    "bad_line",
    [
        plan_entry(0).replace("SHA256:", "MD5Sum:"),
        plan_entry(0).replace("a" * 64, "a" * 63),
        plan_entry(0).replace("'https:", "'file:"),
        plan_entry(0).replace("snapshot.ubuntu.com", "untrusted.example"),
        plan_entry(0).replace("' pkg0_", "' ../pkg0_"),
        plan_entry(0).replace(" 123 ", " -1 "),
        plan_entry(0).replace(" 123 ", " 123;touch /tmp/injected "),
        plan_entry(0).rstrip() + " extra\n",
        "unexpected output\n",
        plan_entry(1),
    ],
)
def test_entire_plan_validated_before_any_download(apt_commands, bad_line):
    run, _ = apt_commands
    result, state = run(APT_TEST_PLAN=plan_entry(1) + bad_line)
    assert result.returncode != 0
    assert len(state["calls"]) == 1
    assert not state["finished"]
    assert "EA_TOOLING_STAGE=apt_install" not in result.stdout


@pytest.mark.parametrize("failure", ["plan", "download", "first_batch"])
def test_acquisition_failure_never_installs_or_retries(apt_commands, failure):
    run, archive = apt_commands
    result, state = run(
        **{
            ("APT_TEST_PLAN_EXIT" if failure == "plan" else "APT_TEST_DOWNLOAD_FAIL"): "pkg0_"
            if failure == "first_batch"
            else "1"
        }
    )
    assert result.returncode != 0
    assert sum(name == "apt-get" for name, _ in state["calls"]) == 1
    assert "EA_TOOLING_STAGE=apt_install" not in result.stdout
    assert len(state["finished"]) == len(set(state["finished"]))
    assert state["active"] == 0
    assert not list(archive.iterdir())
    if failure == "first_batch":
        assert len(state["finished"]) == 9


def test_empty_transaction_does_not_launch_downloader(apt_commands):
    run, _ = apt_commands
    result, state = run(APT_TEST_PLAN="")
    assert result.returncode == 0, result.stderr
    assert [name for name, _ in state["calls"]] == ["apt-get", "apt-get"]


def test_downloads_use_owned_private_staging_then_promote(apt_commands):
    run, archive = apt_commands
    before = archive.stat()
    result, state = run()
    assert result.returncode == 0, result.stderr
    staging = {Path(destination).parent for destination in state["finished"]}
    assert len(staging) == 1
    stage = staging.pop()
    assert stage != archive and stage.parent == archive
    assert ["chown", ["_apt:root", str(stage)]] in state["calls"]
    assert not stage.exists()
    assert archive.stat().st_mode == before.st_mode
    assert archive.stat().st_uid == before.st_uid
    assert len(list(archive.glob("*.deb"))) == 9
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in archive.glob("*.deb"))


def test_staging_ownership_failure_never_downloads(apt_commands):
    run, archive = apt_commands
    result, state = run(APT_TEST_CHOWN_EXIT="1")
    assert result.returncode != 0
    assert all(name != "apt-helper" for name, _ in state["calls"])
    assert "EA_TOOLING_STAGE=apt_install" not in result.stdout
    assert not list(archive.iterdir())


@pytest.mark.parametrize("count", [1, 4, 57])
def test_batch_coverage_and_process_count(apt_commands, count):
    run, archive = apt_commands
    result, state = run(APT_TEST_PLAN="".join(plan_entry(i) for i in range(count)))
    assert result.returncode == 0, result.stderr
    downloads = [args for name, args in state["calls"] if name == "apt-helper"]
    assert len(downloads) == min(4, count)
    assert 1 <= state["peak"] <= 4
    expected = {f"pkg{i}_1%3a2.0+ubuntu1_arm64.deb" for i in range(count)}
    assert {Path(destination).name for destination in state["finished"]} == expected
    assert len(state["finished"]) == count
    assert {path.name for path in archive.iterdir()} == expected
