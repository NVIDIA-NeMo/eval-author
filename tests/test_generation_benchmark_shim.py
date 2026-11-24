# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import json
import os
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from generator import harbor_shim
from generator.harbor_shim import HOST_ESCAPE_FLAGS, extract_tar, pack_tree, parse_submission
from generator.sidecar import HarborSidecar
from test_generation_benchmark_sidecar import FakeHarbor, make_task, smoke_agent

SHIM = Path(harbor_shim.__file__)


@pytest.fixture(autouse=True)
def harbor_0_24_0(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    monkeypatch.setattr(harbor_shim, "installed_harbor_version", lambda: "0.24.0")
    monkeypatch.setattr(harbor_shim, "POLL_SECONDS", 0.02)
    executed: list[list[str]] = []

    def fake_exec(args: list[str]) -> int:
        executed.append(args)
        return 0

    monkeypatch.setattr(harbor_shim, "exec_real_harbor", fake_exec)
    return executed


@pytest.fixture
def sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[HarborSidecar, FakeHarbor]]:
    runner = FakeHarbor()
    agent, config = smoke_agent()
    server = HarborSidecar(agent, config, host="127.0.0.1", harbor_runner=runner, work_root=tmp_path).start()
    monkeypatch.setenv(harbor_shim.URL_ENV, server.url)
    monkeypatch.setenv(harbor_shim.TOKEN_ENV, server.token)
    yield server, runner
    server.stop(timeout=5)


def run_shim(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = harbor_shim.main(list(args))
    return code, capsys.readouterr().err


def test_shim_runs_under_the_real_harbor_python() -> None:
    assert SHIM.read_text(encoding="utf-8").splitlines()[0] == "#!/opt/harbor-real/bin/python"
    assert os.access(SHIM, os.X_OK)


@pytest.mark.parametrize("flag", sorted(HOST_ESCAPE_FLAGS))
@pytest.mark.parametrize("inline", [False, True])
def test_shim_rejects_each_host_escape_flag(capsys, flag: str, inline: bool, harbor_0_24_0) -> None:
    escape = [f"{flag}=/etc"] if inline else [flag, "/etc"]
    for command in (["trial", "start"], ["run"]):
        code, err = run_shim(capsys, *command, "-p", "task", "-a", "oracle", *escape)
        assert code == 2 and flag in err and "host-escape" in err
    assert harbor_0_24_0 == []


@pytest.mark.parametrize(
    "args",
    [
        ["--env", "docker"],
        ["-e", "modal"],
        ["--n-concurrent", "4"],
        ["--force-build"],
        ["--environment-import-path", "x:Y"],
        ["--agent-import-path", "x:Y"],
    ],
)
def test_shim_rejects_unknown_flags(capsys, args: list[str]) -> None:
    code, err = run_shim(capsys, "trial", "start", "-p", "task", "-a", "oracle", *args)
    assert code == 2 and "unsupported flag" in err


def test_shim_rejects_positionals_wrong_kind_flags_and_bad_values(capsys) -> None:
    cases = [
        (["trial", "start", "task", "-a", "oracle"], "unexpected argument"),
        (["trial", "start", "-p", "task", "-a", "oracle", "--job-name", "j"], "does not apply"),
        (["run", "-p", "task", "-a", "oracle", "--trial-name", "t"], "does not apply"),
        (["trial", "start", "-p", "task", "-a"], "requires a value"),
        (["trial", "start", "-p", "task", "-a", "--trial-name", "t"], "requires a value"),
        (["trial", "start", "-p", "a", "-p", "b", "-a", "oracle"], "only once"),
        (["trial", "start", "-a", "oracle"], "requires -p/--path"),
        (["trial", "start", "-p", "task"], "requires -a/--agent"),
    ]
    for args, message in cases:
        code, err = run_shim(capsys, *args)
        assert code == 2 and message in err, (args, err)


def test_shim_accepts_both_flag_spellings() -> None:
    args = ["--path=t", "-a", "oracle", "--trial-name=x", "--trials-dir", "d", "-m", "m", "--config=c.json"]
    assert parse_submission(args, "trial") == {
        "path": "t",
        "agent": "oracle",
        "trial_name": "x",
        "trials_dir": "d",
        "model": "m",
        "config": "c.json",
    }
    assert parse_submission(["-p", "t", "--agent=nop", "--job-name", "j", "-o", "jobs"], "job") == {
        "path": "t",
        "agent": "nop",
        "job_name": "j",
        "jobs_dir": "jobs",
    }


@pytest.mark.parametrize("found", ["0.23.0", "0.25.0", None])
def test_shim_refuses_a_harbor_version_other_than_0_24_0(capsys, monkeypatch, found, harbor_0_24_0) -> None:
    monkeypatch.setattr(harbor_shim, "installed_harbor_version", lambda: found)
    for args in (["--version"], ["task", "init", "x"], ["trial", "start", "-p", "t", "-a", "oracle"]):
        code, err = run_shim(capsys, *args)
        assert code == 2 and "tested with Harbor 0.24.0" in err
    assert harbor_0_24_0 == []


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--version"],
        ["--help"],
        ["version"],
        ["trial", "start", "--help"],
        ["run", "-h"],
        ["jobs", "start", "--help"],
        ["task", "init", "demo/x"],
        ["tasks", "init", "demo/x"],
        ["init", "x"],
        ["check", "x"],
    ],
)
def test_help_version_and_local_task_commands_pass_through(capsys, args: list[str], harbor_0_24_0) -> None:
    assert harbor_shim.main(args) == 0
    assert harbor_0_24_0 == [args]


@pytest.mark.parametrize(
    "args",
    [["view", "jobs"], ["jobs", "start", "-p", "t"], ["trial", "summarize", "t"], ["upload", "x"], ["adapters", "run"]],
)
def test_other_harbor_commands_fail_naming_the_sidecar(capsys, args: list[str], harbor_0_24_0) -> None:
    code, err = run_shim(capsys, *args)
    assert code == 2 and "sidecar" in err
    assert harbor_0_24_0 == []


def test_submission_without_sidecar_settings_fails_naming_the_sidecar(capsys, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(harbor_shim.URL_ENV, raising=False)
    monkeypatch.delenv(harbor_shim.TOKEN_ENV, raising=False)
    code, err = run_shim(capsys, "trial", "start", "-p", str(make_task(tmp_path)), "-a", "oracle")
    assert code == 2 and "sidecar" in err


def test_shim_writes_a_trial_into_a_preexisting_empty_directory(capsys, sidecar, tmp_path: Path) -> None:
    _server, runner = sidecar
    task = make_task(tmp_path / "client")
    trials = tmp_path / "client" / "trials"
    (trials / "t").mkdir(parents=True)
    args = ["trial", "start", "-p", str(task), "-a", "oracle", "--trial-name", "t", "--trials-dir", str(trials)]
    code, err = run_shim(capsys, *args)
    assert code == 0, err
    result = json.loads((trials / "t" / "result.json").read_text(encoding="utf-8"))
    assert result["verifier_result"] == {"rewards": {"reward": 1.0}}
    assert (trials / "t" / "agent").is_dir()
    assert runner.runs[0].name.startswith("t-")

    code, err = run_shim(capsys, *args)
    assert code == 0, err
    assert len(runner.runs) == 1


def test_shim_runs_a_job_and_reports_a_failed_run(capsys, sidecar, tmp_path: Path, monkeypatch) -> None:
    _server, runner = sidecar
    task = make_task(tmp_path / "client")
    jobs = tmp_path / "client" / "jobs"
    code, err = run_shim(capsys, "run", "-p", str(task), "-a", "nop", "--job-name", "j", "-o", str(jobs))
    assert code == 0, err
    assert (jobs / "j" / "result.json").is_file()
    assert runner.runs[0].kind == "job"

    runner.exit_code = 1
    code, err = run_shim(capsys, "trial", "start", "-p", str(task), "-a", "oracle", "--trials-dir", str(jobs))
    assert code == 1 and "failed" in err


def test_shim_reports_a_sidecar_rejection(capsys, sidecar, tmp_path: Path) -> None:
    task = make_task(tmp_path / "client")
    code, err = run_shim(capsys, "trial", "start", "-p", str(task), "-a", "claude-code")
    assert code == 2 and "sidecar rejected" in err and "not allowed" in err


def test_pack_tree_is_deterministic_and_keeps_dotfiles(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    first = pack_tree(task, "alpha")
    os.utime(task / "task.toml", (1, 1))
    assert pack_tree(task, "alpha") == first
    out = tmp_path / "out"
    extract_tar(first, out, top="alpha")
    assert (out / "alpha" / ".eval-author-lineage.json").read_text(encoding="utf-8") == '{"lineage": true}\n'
    (task / "link").symlink_to("task.toml")
    with pytest.raises(ValueError, match="symlinks"):
        pack_tree(task)


def _tar(*members: tarfile.TarInfo) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for member in members:
            tar.addfile(member, io.BytesIO(b"x" * member.size) if member.isreg() else None)
    return buffer.getvalue()


def _member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = 1 if kind == tarfile.REGTYPE else 0
    info.linkname = linkname
    return info


@pytest.mark.parametrize(
    "member",
    [
        _member("../escape"),
        _member("/abs"),
        _member("alpha/../../escape"),
        _member("alpha/link", tarfile.SYMTYPE, "/etc/passwd"),
        _member("alpha/hard", tarfile.LNKTYPE, "alpha/task.toml"),
        _member("alpha/fifo", tarfile.FIFOTYPE),
        _member("beta/file"),
    ],
)
def test_extract_tar_rejects_unsafe_members(tmp_path: Path, member: tarfile.TarInfo) -> None:
    with pytest.raises(ValueError):
        extract_tar(_tar(_member("alpha", tarfile.DIRTYPE), member), tmp_path / "out", top="alpha")
    assert not (tmp_path / "escape").exists()


def test_extract_tar_refuses_to_write_through_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "agent").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        extract_tar(_tar(_member("agent/file")), dest)
    assert not any(outside.iterdir())
