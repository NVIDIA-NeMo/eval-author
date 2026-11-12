# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def runner():
    path = ROOT / "tools/live_shard.py"
    assert path.is_file(), "shared production shard runner missing"
    spec = importlib.util.spec_from_file_location("live_shard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_builds_pinned_image_before_boundary_and_hands_off(tmp_path, monkeypatch):
    module = runner()
    events = []
    monkeypatch.setattr(module, "require_disposable_vm", lambda: events.append("vm"))
    monkeypatch.setattr(
        module,
        "build_image",
        lambda output, platform: events.append(("build", output, platform)) or "sha256:" + "a" * 64,
    )
    monkeypatch.setattr(module, "create_boundary", lambda state, **kwargs: events.append(("create", state, kwargs)))
    monkeypatch.setattr(module, "handoff_boundary", lambda state: events.append(("handoff", state)))
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", str(module.time.monotonic() + 4500))
    args = module.parse_args(
        [
            "create",
            "--root",
            str(tmp_path / "shard"),
            "--repo",
            str(ROOT),
            "--tier",
            "3",
            "--check",
            "live",
            "--skill",
            "eval-author-audit",
        ]
    )
    assert module.run_phase(args) == 0
    assert events[0] == "vm"
    assert [event[0] for event in events[1:]] == ["build", "create", "handoff"]
    create = events[2][2]
    assert create["image"] == "sha256:" + "a" * 64
    assert create["workspace"] == tmp_path / "shard/workspace"
    assert create["repo"] == ROOT
    assert module.time.time() < create["deadline"] <= module.time.time() + 4500
    assert (create["workspace"].stat().st_mode & 0o777) == 0o700
    assert not events[2][1].is_relative_to(create["workspace"])


@pytest.fixture
def shard(tmp_path, monkeypatch):
    module = runner()
    events = []
    monkeypatch.setattr(module, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(module, "build_image", lambda *a: "sha256:" + "a" * 64)
    monkeypatch.setattr(module, "create_boundary", lambda state, **kw: state.write_text("{}"))
    monkeypatch.setattr(module, "attach_boundary", lambda state: events.append(("attach", state)))
    monkeypatch.setattr(module, "handoff_boundary", lambda state: events.append(("handoff", state)))
    monkeypatch.setattr(module, "close_boundary", lambda state: events.append(("close", state)))
    monkeypatch.setattr(module, "execute_in_boundary", lambda *a, **kw: events.append(("execute", a, kw)) or (0, None))
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", str(module.time.monotonic() + 4500))
    root = tmp_path / "shard"

    def phase(name, *extra):
        return module.run_phase(module.parse_args([name, "--root", str(root), "--repo", str(ROOT), *extra]))

    phase("create")
    events.clear()
    return module, root, events, phase


@pytest.mark.parametrize("check,extras", [("live", "tier3"), ("context", "tier2")])
def test_runtime_installs_each_job_with_managed_python_and_keyless_env(shard, monkeypatch, check, extras):
    import json

    from collect_skill_evaluations import EVALUATOR_REVISION
    from install_evaluator import EVALUATOR_REVISION as INSTALLED_REVISION
    from patched_opencode_runtime import EVALUATOR_REVISION as GUARDED_REVISION

    module, root, events, phase = shard
    assert INSTALLED_REVISION == GUARDED_REVISION == EVALUATOR_REVISION
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config["check"] = check
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "never-setup")
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward")
    assert phase("runtime") == 0
    executions = [e[1] for e in events if e[0] == "execute"]
    python = str(root / "workspace/live-runtime/bin/python")
    assert [e[1] for e in executions] == [
        ["uv", "venv", "--managed-python", "--python", "3.12", str(root / "workspace/live-runtime")],
        [
            python,
            str(ROOT / "tools/install_evaluator.py"),
            "--python",
            python,
            "--extra",
            extras,
        ],
    ]
    for execution in executions:
        assert execution[2] == ROOT
        assert not any("KEY" in k for k in execution[3])
        assert execution[3]["HOME"] == str(root / "workspace/home")
        assert execution[3]["UV_PYTHON_INSTALL_DIR"] == str(root / "workspace/managed-python")
        assert 0 < execution[5] <= 300
        assert not execution[4].is_relative_to(root / "workspace")
    assert events[-1][0] == "handoff"


def test_prepare_explicit_worker_selection_reaches_keyless_child(shard, tmp_path):
    import json

    module, root, events, phase = shard
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config["phase"] = "relocate"
    config_path.write_text(json.dumps(config))
    assert phase("prepare", "--workers", "4") == 0
    execution = next(e[1] for e in events if e[0] == "execute")
    assert execution[1][-2:] == ["--workers", "4"]
    assert not any("KEY" in name for name in execution[3])
    assert events[-1][0] == "handoff"


@pytest.mark.parametrize("value", ["0", "3", "9", "two"])
def test_prepare_workers_rejected_at_host_parser(tmp_path, value):
    module = runner()
    with pytest.raises(SystemExit):
        module.parse_args(["prepare", "--root", str(tmp_path), "--workers", value])


def test_failed_phase_closes_and_cannot_advance(shard, monkeypatch):
    module, root, events, phase = shard
    monkeypatch.setattr(module, "execute_in_boundary", lambda *a, **kw: (1, None))
    with pytest.raises(RuntimeError):
        phase("runtime")
    assert [e[0] for e in events] == ["close"]
    with pytest.raises(ValueError, match="order"):
        phase("prepare")


@pytest.mark.parametrize("name", ["opencode", "binary-manifest.json"])
@pytest.mark.parametrize("kind", ["symlink", "ancestor", "missing", "dangling", "directory", "fifo"])
def test_relocation_invalid_host_source_closes_without_native_or_ready(shard, tmp_path, name, kind):
    import json
    import os

    module, root, events, phase = shard
    phase("runtime")
    events.clear()
    download = tmp_path / "download"
    download.mkdir()
    for artifact in ("opencode", "binary-manifest.json"):
        (download / artifact).write_bytes(b"private fixture")
    source = download / name
    source.unlink()
    if kind in ("symlink", "dangling"):
        source.symlink_to(tmp_path / ("download" if kind == "symlink" else "absent"))
    elif kind == "directory":
        source.mkdir()
    elif kind == "fifo":
        os.mkfifo(source)
    elif kind == "ancestor":
        source.write_bytes(b"private fixture")
        alias = tmp_path / "alias"
        alias.symlink_to(download, target_is_directory=True)
        download = alias
    with pytest.raises((OSError, ValueError)):
        phase("relocate", "--download", str(download))
    assert [e[0] for e in events] == ["close"]
    assert json.loads((root / "control/phase.json").read_text())["phase"] == "runtime"
    assert not (root / "workspace/patched-opencode/binary-manifest.json").exists()
    copied = root / "workspace/downloaded-opencode/opencode"
    assert copied.exists() == (name == "binary-manifest.json" and kind != "ancestor")
    assert not (root / "report").exists()


@pytest.mark.parametrize("size", [16384, 16385])
def test_relocation_manifest_bound_is_enforced_by_real_downstream(shard, tmp_path, monkeypatch, size):
    import hashlib
    import json
    import subprocess

    import build_patched_opencode as build

    module, root, events, phase = shard
    phase("runtime")
    events.clear()
    download = tmp_path / "download"
    download.mkdir()
    binary = download / "opencode"
    binary.write_bytes(b"synthetic binary, never executed")
    payload = json.dumps(
        dict(
            schema_version=1,
            binary_path=str(binary),
            sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            expected_version=build.PATCHED_VERSION,
        )
    ).encode()
    (download / "binary-manifest.json").write_bytes(payload + b" " * (size - len(payload)))
    versions = []

    def version(argv, **kwargs):
        versions.append(argv)
        assert not (root / "workspace/patched-opencode/binary-manifest.json").exists()
        return subprocess.CompletedProcess(argv, 0, build.PATCHED_VERSION, "")

    def execute(state, argv, *args):
        events.append(("execute", argv))
        if "--relocate-manifest" in argv:
            output = Path(argv[argv.index("--output") + 1])
            output.mkdir()
            build.relocate_manifest(
                Path(argv[argv.index("--relocate-manifest") + 1]), Path(argv[argv.index("--binary") + 1]), output
            )
        return 0, None

    monkeypatch.setattr(module, "execute_in_boundary", execute)
    monkeypatch.setattr(build.subprocess, "run", version)
    if size == 16384:
        assert phase("relocate", "--download", str(download)) == 0
        assert len(versions) == 1
        assert [e[0] for e in events] == ["execute", "execute", "handoff"]
        assert (root / "workspace/patched-opencode/binary-manifest.json").exists()
    else:
        with pytest.raises(ValueError, match="too large"):
            phase("relocate", "--download", str(download))
        assert versions == []
        assert [e[0] for e in events] == ["execute", "close"]
        assert json.loads((root / "control/phase.json").read_text())["phase"] == "runtime"
        assert not (root / "workspace/patched-opencode/binary-manifest.json").exists()
    assert (root / "workspace/downloaded-opencode/binary-manifest.json").stat().st_size == size


def test_relocation_gates_installed_python_before_full_preparation(shard):
    module, root, events, phase = shard
    phase("runtime")
    events.clear()
    downloaded = root.parent / "download"
    downloaded.mkdir()
    (downloaded / "opencode").write_bytes(b"fixture")
    (downloaded / "binary-manifest.json").write_text("{}")
    assert phase("relocate", "--download", str(downloaded)) == 0
    commands = [e[1][1] for e in events if e[0] == "execute"]
    manifest = str(root / "workspace/patched-opencode/binary-manifest.json")
    assert commands[0] == [
        "/usr/bin/python3",
        str(ROOT / "tools/build_patched_opencode.py"),
        "--relocate-manifest",
        str(root / "workspace/downloaded-opencode/binary-manifest.json"),
        "--binary",
        str(root / "workspace/downloaded-opencode/opencode"),
        "--output",
        str(root / "workspace/patched-opencode"),
    ]
    assert commands[1] == [
        str(root / "workspace/live-runtime/bin/python"),
        str(ROOT / "tools/verify_live_runtime.py"),
        "--binary-manifest",
        manifest,
        "--output",
        str(root / "workspace/live-runtime-gate-private"),
    ]
    assert events[-1][0] == "handoff"
    events.clear()
    assert phase("prepare") == 0
    command = events[0][1][1]
    assert command == [
        str(root / "workspace/live-runtime/bin/python"),
        str(ROOT / "tools/prepare_live_images.py"),
        "--skill",
        str(ROOT / "skills/eval-author-audit"),
        "--binary-manifest",
        manifest,
        "--output",
        str(root / "workspace/prepared-live-images"),
        "--private-diagnostics",
        str(root / "workspace/preparation-docker-failure.private.json"),
    ]
    assert 0 < events[0][1][5] <= 900
    assert events[-1][0] == "handoff"


@pytest.mark.parametrize("check", ["context", "similarity"])
def test_tier2_runs_isolated_without_opencode_and_closes_before_return(shard, monkeypatch, check):
    import json

    module, root, events, phase = shard
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config.update(tier="2", check=check, skill="all" if check == "similarity" else "eval-author-audit")
    config_path.write_text(json.dumps(config))
    phase("runtime")
    assert events[1][1][1][-2:] == ["--extra", "tier2"]
    events.clear()
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "collector-only")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated")
    for name in (
        "EA_REQUIRE_PATCHED_OPENCODE",
        "EA_REQUIRE_PREPARED_IMAGES",
        "EA_PATCHED_OPENCODE_MANIFEST",
        "EA_PREPARED_IMAGES_MANIFEST",
    ):
        monkeypatch.setenv(name, "must-not-forward")

    def collect(argv, **kwargs):
        events.append(("collector", argv, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module.subprocess, "run", collect)
    assert phase("run") == 0
    assert events[0] == ("attach", root / "control/boundary.json")
    call = events[1]
    assert call[0] == "collector"
    argv = call[1]
    assert argv[1] == str(ROOT / "tools/collect_live_skill_evaluations.py")
    assert argv[argv.index("--boundary-state") + 1] == str(root / "control/boundary.json")
    assert argv[argv.index("--native-output") + 1] == str(root / "workspace/skill-live")
    assert argv[argv.index("--output") + 1] == str(root / "report")
    assert call[2]["env"]["INFERENCE_HUB_API_KEY"] == "collector-only"
    assert "OPENAI_API_KEY" not in call[2]["env"]
    assert call[2]["env"]["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert argv[argv.index("--evaluator") + 1] == str(root / "workspace/live-runtime/bin/skillevaluator")
    assert argv[argv.index("--evaluator-python") + 1] == str(root / "workspace/live-runtime/bin/python")
    assert call[2]["env"]["HOME"] == str(root / "control/collector-home")
    assert call[2]["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert call[2]["env"]["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert call[2]["env"]["GIT_CONFIG_VALUE_0"] == str(ROOT)
    assert (
        not {
            "EA_REQUIRE_PATCHED_OPENCODE",
            "EA_REQUIRE_PREPARED_IMAGES",
            "EA_PATCHED_OPENCODE_MANIFEST",
            "EA_PREPARED_IMAGES_MANIFEST",
        }
        & call[2]["env"].keys()
    )
    assert "collector-only" not in str(argv)
    assert 0 < call[2]["timeout"] <= module.RUN_PHASE_SECONDS
    assert events[-1][0] == "close"
    events.clear()
    assert phase("close") == 0
    assert events[-1][0] == "close"


@pytest.mark.parametrize("unsafe", [False, True])
def test_close_projects_only_valid_timing_after_confirmed_teardown(shard, tmp_path, unsafe):
    import json

    module, root, events, phase = shard
    from patched_opencode_runtime import PreparationTiming

    private = root / "workspace/preparation-operation-timing.json"
    PreparationTiming(private).emit("started")
    payload = json.loads(private.read_text())
    if unsafe:
        payload["latest"]["event"] = "private task content"
        private.write_text(json.dumps(payload))
    public = tmp_path / "timing.json"
    if unsafe:
        with pytest.raises(ValueError):
            phase("close", "--timing-checkpoint", str(public))
        assert not public.exists()
    else:
        assert phase("close", "--timing-checkpoint", str(public)) == 0
        assert json.loads(public.read_text()) == payload
    assert events[-1][0] == "close"


@pytest.mark.parametrize(
    "kind", ["missing", "symlink", "dangling", "ancestor", "hardlink", "directory", "fifo", "oversize"]
)
def test_timing_source_safety_after_close(shard, tmp_path, kind):
    import os

    from patched_opencode_runtime import PreparationTiming

    module, root, events, phase = shard
    source = root / "workspace/preparation-operation-timing.json"
    target = tmp_path / "valid.json"
    PreparationTiming(target).emit("started")
    if kind in ("symlink", "dangling"):
        source.symlink_to(target if kind == "symlink" else tmp_path / "absent")
    elif kind == "ancestor":
        source.parent.rmdir()
        source.parent.symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / source.name).write_bytes(target.read_bytes())
    elif kind == "hardlink":
        os.link(target, source)
    elif kind == "directory":
        source.mkdir()
    elif kind == "fifo":
        os.mkfifo(source)
    elif kind == "oversize":
        source.write_bytes(target.read_bytes() + b" " * 1048576)
    public = tmp_path / "public.json"
    if kind == "missing":
        assert phase("close", "--timing-checkpoint", str(public)) == 0
    else:
        with pytest.raises((OSError, ValueError)):
            phase("close", "--timing-checkpoint", str(public))
    assert [e[0] for e in events] == ["close"]
    assert not public.exists()


@pytest.mark.parametrize("attack", ["symlink_swap", "growth_before_open", "growth_during_read", "exact_cap"])
def test_timing_reader_bounds_at_open_and_read(shard, tmp_path, monkeypatch, attack):
    import os

    from patched_opencode_runtime import PreparationTiming

    module, root, events, phase = shard
    source = root / "workspace/preparation-operation-timing.json"
    PreparationTiming(source).emit("started")
    raw = source.read_bytes()
    source.write_bytes(raw + b" " * (1048576 - len(raw)))
    original_open, original_fstat = os.open, os.fstat
    victim = tmp_path / "victim.json"
    victim.write_bytes(raw)
    attacked = False

    def open_file(path, flags, *args, **kwargs):
        nonlocal attacked
        if str(path) in (str(source), source.name) and not attacked:
            attacked = True
            if attack == "symlink_swap":
                source.unlink()
                source.symlink_to(victim)
            elif attack == "growth_before_open":
                with source.open("ab") as stream:
                    stream.write(b" ")
        return original_open(path, flags, *args, **kwargs)

    def fstat(fd):
        info = original_fstat(fd)
        if attack == "growth_during_read" and info.st_ino == source.stat().st_ino:
            with source.open("ab") as stream:
                stream.write(b" ")
        return info

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "fstat", fstat)
    public = tmp_path / "public.json"
    if attack == "exact_cap":
        assert phase("close", "--timing-checkpoint", str(public)) == 0
        assert public.exists()
    else:
        with pytest.raises((OSError, ValueError)):
            phase("close", "--timing-checkpoint", str(public))
        assert not public.exists()
    assert attacked
    assert [e[0] for e in events] == ["close"]


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "top",
        "list",
        "event",
        "counter",
        "counter_limit",
        "seconds",
        "seconds_bool",
        "nan",
        "infinity",
        "events",
        "missing",
        "malformed",
    ],
)
def test_shared_timing_schema_round_trips_producer(shard, tmp_path, mutation, capsys):
    import json

    import patched_opencode_runtime as runtime

    module, root, events, phase = shard
    source = root / "workspace/preparation-operation-timing.json"
    timing = runtime.PreparationTiming(source)
    timing.emit("started")
    row = json.loads(source.read_text())["latest"]
    timing.events = [row] * runtime.PREPARATION_TIMING_MAX_EVENTS
    timing.emit("completed")
    data = json.loads(source.read_text())
    assert data["events_truncated"] is True
    assert data["latest"]["event"] == "completed" != data["events"][-1]["event"]
    if mutation == "top":
        data["PRIVATE"] = "secret"
    elif mutation == "list":
        data = []
    elif mutation == "event":
        data["latest"]["event"] = "PRIVATE"
    elif mutation == "counter":
        data["latest"]["completed_images"] = True
    elif mutation == "counter_limit":
        data["latest"]["completed_images"] = 129
    elif mutation == "seconds":
        data["latest"]["total_elapsed_seconds"] = 86401
    elif mutation == "seconds_bool":
        data["latest"]["total_elapsed_seconds"] = False
    elif mutation in ("nan", "infinity"):
        data["latest"]["total_elapsed_seconds"] = float(mutation.replace("infinity", "inf"))
    elif mutation == "events":
        data["events"].append(row)
    elif mutation == "missing":
        del data["latest"]["phase"]
    if mutation in ("none", "malformed"):
        assert runtime.validate_preparation_timing(data) == data
    else:
        with pytest.raises(ValueError, match="^invalid timing checkpoint$"):
            runtime.validate_preparation_timing(data)
    source.write_text("PRIVATE malformed JSON" if mutation == "malformed" else json.dumps(data))
    public = tmp_path / "public.json"
    if mutation == "none":
        assert phase("close", "--timing-checkpoint", str(public)) == 0
        assert json.loads(public.read_text()) == data
    else:
        with pytest.raises(ValueError):
            phase("close", "--timing-checkpoint", str(public))
        assert not public.exists()
    assert "PRIVATE" not in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["inside", "symlink", "ancestor", "exists", "directory"])
def test_timing_destination_rejection_never_overwrites(shard, tmp_path, kind):
    from patched_opencode_runtime import PreparationTiming

    module, root, events, phase = shard
    PreparationTiming(root / "workspace/preparation-operation-timing.json").emit("started")
    untouched = tmp_path / "untouched"
    untouched.write_text("sentinel")
    public = tmp_path / "public.json"
    if kind == "inside":
        public = root / "public.json"
    elif kind == "symlink":
        public.symlink_to(untouched)
    elif kind == "ancestor":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        public = alias / "public.json"
    elif kind == "exists":
        public.write_text("sentinel")
    elif kind == "directory":
        public.mkdir()
    with pytest.raises((OSError, ValueError)):
        phase("close", "--timing-checkpoint", str(public))
    assert [e[0] for e in events] == ["close"]
    assert untouched.read_text() == "sentinel"
    assert not (root / "public.json").exists()
    if kind == "exists":
        assert public.read_text() == "sentinel"


def test_timing_close_failure_never_reads_checkpoint(shard, tmp_path, monkeypatch):
    module, root, events, phase = shard

    def fail(state):
        raise RuntimeError("uncertain closure")

    monkeypatch.setattr(module, "close_boundary", fail)
    monkeypatch.setattr(module, "publish_timing", lambda *a: pytest.fail("read before closure"))
    with pytest.raises(RuntimeError, match="uncertain closure"):
        phase("close", "--timing-checkpoint", str(tmp_path / "public.json"))


def test_live_collection_uses_prepared_manifests_and_keeps_report_host_only(shard, monkeypatch):
    import json

    module, root, events, phase = shard
    phase("runtime")
    download = root.parent / "artifact"
    download.mkdir()
    for name in ("opencode", "binary-manifest.json"):
        (download / name).write_text("fixture")
    phase("relocate", "--download", str(download))
    phase("prepare")
    events.clear()

    def collect(argv, **kwargs):
        events.append(("collector", argv, kwargs))
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr(module.subprocess, "run", collect)
    assert phase("run") == 1
    assert events[0] == ("attach", root / "control/boundary.json")
    env = events[1][2]["env"]
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert env["EA_REQUIRE_PATCHED_OPENCODE"] == env["EA_REQUIRE_PREPARED_IMAGES"] == "1"
    assert env["EA_PREPARED_IMAGES_MANIFEST"] == str(root / "workspace/prepared-live-images/prepared-images.json")
    assert env["EA_PATCHED_OPENCODE_MANIFEST"] == str(root / "workspace/patched-opencode/binary-manifest.json")
    assert events[-1][0] == "close"
    assert not (root / "report").is_relative_to(root / "workspace")
    assert json.loads((root / "control/phase.json").read_text())["phase"] == "prepare"


@pytest.mark.parametrize("check", ["live", "context", "similarity"])
@pytest.mark.parametrize("attach_fails", [False, True])
def test_run_attaches_supervisor_before_collector_or_closes_on_failure(shard, monkeypatch, check, attach_fails):
    import json

    from live_shard_boundary import BoundaryError

    module, root, events, phase = shard
    state = root / "control/boundary.json"
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config.update(check=check, phase="prepare" if check == "live" else "runtime", deadline=4000.0)
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(module.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(module.time, "time", lambda: 1800000000.0)

    def attach(path):
        events.append(("attach", path))
        if attach_fails:
            raise BoundaryError("supervisor attachment failed")

    def collect(argv, **kwargs):
        assert not attach_fails, "collector spawned despite required attachment failure"
        assert events == [("attach", state)], "supervisor must attach before collector spawn"
        assert kwargs["timeout"] == module.RUN_PHASE_SECONDS
        assert kwargs["env"]["EA_LIVE_JOB_DEADLINE"] == "4000.0"
        events.append(("collector", state))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(module, "attach_boundary", attach)
    monkeypatch.setattr(module.subprocess, "run", collect)
    if attach_fails:
        with pytest.raises(BoundaryError, match="supervisor attachment failed"):
            phase("run")
        assert events == [("attach", state), ("close", state)]
    else:
        assert phase("run") == 0
        assert events == [("attach", state), ("collector", state), ("close", state)]
    assert json.loads(config_path.read_text()) == config


@pytest.mark.parametrize("check", ["live", "context", "similarity"])
def test_host_collector_child_inherits_only_fixed_system_path(shard, monkeypatch, check):
    import json

    module, root, events, phase = shard
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config.update(check=check, phase="prepare" if check == "live" else "runtime")
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("PATH", "/ambient-writable-bin")
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "synthetic-key")
    run = module.subprocess.run

    def inert_collector(argv, **kwargs):
        assert argv[0] == sys.executable
        assert argv[argv.index("--evaluator") + 1] == str(root / "workspace/live-runtime/bin/skillevaluator")
        assert argv[argv.index("--evaluator-python") + 1] == str(root / "workspace/live-runtime/bin/python")
        return run(
            [sys.executable, "-I", "-B", "-c", "import os; print(os.environ['PATH'])"],
            **kwargs,
        )

    monkeypatch.setattr(module.subprocess, "run", inert_collector)
    assert phase("run") == 0
    assert (root / "control/collector.log").read_text().strip() == "/usr/local/bin:/usr/bin:/bin"
    assert [e[0] for e in events] == ["attach", "close"]


def test_malformed_control_closes_before_raising(shard):
    module, root, events, phase = shard
    (root / "control/phase.json").write_text("not json")
    with pytest.raises(ValueError):
        phase("runtime")
    assert events[-1][0] == "close"


def test_creation_has_shared_four_minute_bootstrap_cap(tmp_path, monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "require_disposable_vm", lambda: None)

    def build(*args):
        assert 0 < module.signal.getitimer(module.signal.ITIMER_REAL)[0] <= 240
        raise TimeoutError("bounded build failure")

    monkeypatch.setattr(module, "build_image", build)
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", str(module.time.monotonic() + 4500))
    with pytest.raises(TimeoutError):
        module.run_phase(module.parse_args(["create", "--root", str(tmp_path / "shard")]))
    assert module.signal.getitimer(module.signal.ITIMER_REAL)[0] == 0


def test_create_rejects_mount_overlap_before_image_build(tmp_path, monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(module, "build_image", lambda *a: pytest.fail("build before path validation"))
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", str(module.time.monotonic() + 4500))
    with pytest.raises(ValueError):
        module.run_phase(module.parse_args(["create", "--repo", str(tmp_path), "--root", str(tmp_path / "bad")]))


@pytest.mark.parametrize("remaining,attach_seconds", [(2760, 0), (2820, 0), (2880, 0), (2880.001, 60.002)])
def test_run_rejects_missing_validation_or_startup_reserve_before_credentials(
    shard, monkeypatch, remaining, attach_seconds
):
    import json

    module, root, events, phase = shard
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config.update(phase="prepare", deadline=4500.0)
    config_path.write_text(json.dumps(config))
    clock = [4500.0 - remaining]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def attach(state):
        events.append(("attach", state))
        clock[0] += attach_seconds

    monkeypatch.setattr(module, "attach_boundary", attach)
    original_getitem = type(module.os.environ).__getitem__

    def read(environment, name):
        if name == "INFERENCE_HUB_API_KEY":
            pytest.fail("credential accessed before complete run reservation")
        return original_getitem(environment, name)

    monkeypatch.setattr(type(module.os.environ), "__getitem__", read)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("collector must not start"))
    with pytest.raises(ValueError, match="budget"):
        phase("run")
    assert [e[0] for e in events] == (["attach", "close"] if attach_seconds else ["close"])
    if not attach_seconds:
        assert not (root / "control/collector-home").exists()


def test_setup_timeout_reserves_validation_startup_evaluation_and_handoff(shard, monkeypatch):
    import json

    module, root, events, phase = shard
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config.update(phase="relocate", deadline=4500.0)
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(module.time, "monotonic", lambda: 1000.0)
    assert phase("prepare") == 0
    execution = next(e[1] for e in events if e[0] == "execute")
    assert execution[5] == 4500 - 1000 - (60 + 60 + 2700 + 60)


@pytest.mark.parametrize("startup_overrun", [0, 1])
def test_upper_setup_bound_honors_real_collector_and_wrapper_guards(shard, monkeypatch, startup_overrun):
    import ast
    import json
    from types import SimpleNamespace

    import collect_live_skill_evaluations as collector
    import run_pinned_live_evaluator as wrapper

    module, root, events, phase = shard
    # Bind the reservation to the actual collector's validation call, not a
    # synthetic second implementation of its timeout.
    tree = ast.parse((ROOT / "tools/collect_live_skill_evaluations.py").read_text())
    validation = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_with_progress"
        and node.args
        and isinstance(node.args[0], ast.List)
        and any(isinstance(item, ast.Constant) and item.value == "validate" for item in node.args[0].elts)
    )
    assert ast.literal_eval(validation.args[4]) == module.VALIDATION_SECONDS == 60
    assert module.RUN_PHASE_SECONDS == 2880
    assert module.SETUP_SECONDS == 1620
    clock = [module.SETUP_SECONDS - 0.001]
    config_path = root / "control/phase.json"
    config = json.loads(config_path.read_text())
    config.update(phase="prepare", deadline=4500.0)
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", "4500.0")
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr(module, "attach_boundary", lambda state: advance(10))
    monkeypatch.setattr(wrapper, "configure_runtime", lambda: advance(30 + startup_overrun))
    monkeypatch.setattr(wrapper, "install_failure_channel", lambda: None)
    reached = []
    monkeypatch.setattr(wrapper, "import_module", lambda name: SimpleNamespace(cli=lambda: reached.append("cli")))

    def native(argv, repo, env, log, timeout):
        if argv == ["validate"]:
            advance(timeout)
        else:
            wrapper.main()  # Keep both real full-budget guards around activation.
        return 0, None

    monkeypatch.setattr(collector, "execute", native)

    def collect(argv, **kwargs):
        assert kwargs["timeout"] == pytest.approx(module.RUN_PHASE_SECONDS - 10)
        advance(20)  # Collector imports, source checks and launch overhead.
        collector.execute_with_progress(["validate"], ROOT, {}, root / "validate.log", module.VALIDATION_SECONDS)
        collector.execute_with_progress(
            ["evaluate"], ROOT, {}, root / "evaluate.log", collector.TIER3_TIMEOUT_SECONDS, reserve_live_budget=True
        )
        assert reached == ["cli"]
        assert 4500 - clock[0] > collector.TIER3_TIMEOUT_SECONDS + module.LIVE_HANDOFF_SECONDS
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", collect)
    if startup_overrun:
        with pytest.raises(TimeoutError, match="insufficient live job budget"):
            phase("run")
        assert reached == []
    else:
        assert phase("run") == 0
    assert events[-1][0] == "close"


def test_expired_setup_closes_before_execution(shard, monkeypatch):
    module, root, events, phase = shard
    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: float(__import__("json").loads((root / "control/phase.json").read_text())["deadline"]) - 2759,
    )
    with pytest.raises(ValueError, match="budget"):
        phase("runtime")
    assert [e[0] for e in events] == ["close"]
