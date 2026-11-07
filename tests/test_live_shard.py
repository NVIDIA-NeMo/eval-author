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


def test_runtime_installs_each_job_with_managed_python_and_keyless_env(shard, monkeypatch):
    module, root, events, phase = shard
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "never-setup")
    monkeypatch.setenv("OPENAI_API_KEY", "never-forward")
    assert phase("runtime") == 0
    executions = [e[1] for e in events if e[0] == "execute"]
    python = str(root / "workspace/live-runtime/bin/python")
    assert [e[1] for e in executions] == [
        ["uv", "venv", "--managed-python", "--python", "3.12", str(root / "workspace/live-runtime")],
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "skillevaluator[tier3] @ git+https://github.com/NVIDIA/SkillEvaluator.git@3fd2cf57e4a2af01f282e001f7515b6e76f18ec7",
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


def test_failed_phase_closes_and_cannot_advance(shard, monkeypatch):
    module, root, events, phase = shard
    monkeypatch.setattr(module, "execute_in_boundary", lambda *a, **kw: (1, None))
    with pytest.raises(RuntimeError):
        phase("runtime")
    assert [e[0] for e in events] == ["close"]
    with pytest.raises(ValueError, match="order"):
        phase("prepare")


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
    assert "skillevaluator[tier2]" in events[1][1][1][-1]
    events.clear()
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "collector-only")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated")

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
    assert call[2]["env"]["PATH"] == "/usr/local/bin:/usr/bin:/bin:" + str(root / "workspace/live-runtime/bin")
    assert call[2]["env"]["HOME"] == str(root / "control/collector-home")
    assert call[2]["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert call[2]["env"]["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert call[2]["env"]["GIT_CONFIG_VALUE_0"] == str(ROOT)
    assert "EA_REQUIRE_PATCHED_OPENCODE" not in call[2]["env"]
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
    assert env["EA_REQUIRE_PATCHED_OPENCODE"] == env["EA_REQUIRE_PREPARED_IMAGES"] == "1"
    assert Path(env["EA_PREPARED_IMAGES_MANIFEST"]).is_relative_to(root / "workspace")
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
