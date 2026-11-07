# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit controls use fake host seams; VM controls are explicitly opt-in."""

import importlib
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def boundary():
    assert (ROOT / "tools/live_shard_boundary.py").exists(), "production boundary is missing"
    return importlib.import_module("live_shard_boundary")


def test_boundary_rejects_non_linux_before_docker(tmp_path, monkeypatch):
    module = boundary()
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(module.BoundaryError, match="Linux"):
        module.create_boundary(
            tmp_path / "control/state.json",
            repo=ROOT,
            workspace=tmp_path / "work",
            image="sha256:" + "a" * 64,
            deadline=9999999999,
        )


def test_remote_runner_real_process_stdin_not_command_line(tmp_path):
    module = boundary()
    secret = "synthetic-private-value"
    payload = {
        "argv": [sys.executable, "-c", "import os; print(os.environ['KEY']); raise SystemExit(23)"],
        "cwd": str(tmp_path),
        "env": {"KEY": secret},
    }
    process = subprocess.run(
        [sys.executable, "-c", module.REMOTE_EXEC], input=json.dumps(payload), text=True, capture_output=True
    )
    assert process.returncode == 23
    assert process.stdout.strip() == secret
    assert secret not in module.REMOTE_EXEC


def test_state_must_not_be_mounted(tmp_path):
    module = boundary()
    repo, work = tmp_path / "repo", tmp_path / "work"
    repo.mkdir()
    work.mkdir(mode=0o700)
    with pytest.raises(module.BoundaryError, match="control"):
        module._validate_paths(work / "state.json", repo, work)


def test_closed_boundary_cannot_be_reused(tmp_path):
    module = boundary()
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "closed"}))
    state.chmod(0o600)
    with pytest.raises(module.BoundaryError, match="closed"):
        module.execute_in_boundary(state, ["true"], ROOT, {}, tmp_path / "native.log", 1)


@pytest.mark.parametrize(("code", "timeout", "final"), [(0, 2, True), (23, 2, False), (0, 0.05, False)])
def test_real_transport_preserves_outcome_and_closes_privately(tmp_path, monkeypatch, code, timeout, final):
    module = boundary()
    owner = {"pid": 123, "start": "1"}
    monkeypatch.setattr(module, "_identity", lambda: owner)
    monkeypatch.setattr(module, "_dead", lambda owner: False)
    state = tmp_path / "state.json"
    module._save(
        state,
        {
            "status": "ready",
            "owner": owner,
            "deadline": time.time() + 60,
            "lease": time.time() + 60,
            "docker_config": str(tmp_path),
        },
    )
    monkeypatch.setattr(module, "_docker_exec", lambda data: [sys.executable, "-c", module.REMOTE_EXEC])
    closed = []
    monkeypatch.setattr(module, "close_boundary", lambda s: closed.append(s))
    log = tmp_path / "native.log"
    argv = [
        sys.executable,
        "-c",
        f"import os,time; print(os.environ['KEY'],flush=True); time.sleep(.2); raise SystemExit({code})",
    ]
    result = module.execute_in_boundary(state, argv, tmp_path, {"KEY": "private-canary"}, log, timeout, final=final)
    assert closed == [state]
    assert result[1] == ("timeout" if timeout < 0.2 else None)
    assert result[0] < 0 if timeout < 0.2 else result[0] == code
    assert log.stat().st_mode & 0o777 == 0o600
    assert "private-canary" not in state.read_text()


def test_handoff_only_idle_and_bounded(tmp_path, monkeypatch):
    module = boundary()
    owner = {"pid": 1, "start": "1"}
    monkeypatch.setattr(module, "_identity", lambda: owner)
    monkeypatch.setattr(module, "_dead", lambda owner: False)
    state = tmp_path / "state.json"
    module._save(state, {"status": "active", "owner": owner, "lease": time.time() + 400, "deadline": time.time() + 500})
    with pytest.raises(module.BoundaryError, match="idle"):
        module.handoff_boundary(state)
    module._update(state, status="ready")
    module.handoff_boundary(state)
    data = module._read(state)
    assert data["owner"] is None
    assert 0 < data["lease"] - time.time() <= 120


def test_attach_guards_phase_supervisor_before_collector_spawn(tmp_path, monkeypatch):
    module = boundary()
    state = tmp_path / "state.json"
    owner = {"pid": 1, "start": "1"}
    monkeypatch.setattr(module, "_identity", lambda: owner)
    module._save(state, {"status": "ready", "owner": None, "lease": time.time() + 60, "deadline": time.time() + 500})
    assert hasattr(module, "attach_boundary"), "phase supervisor must attach before spawning collector"
    module.attach_boundary(state)
    assert module._read(state)["owner"] == owner


def test_dead_active_client_expires_even_if_phase_supervisor_lives(monkeypatch):
    module = boundary()
    monkeypatch.setattr(module, "_dead", lambda owner: owner == "dead-client")
    assert module._expired(
        {"deadline": time.time() + 60, "lease": time.time() + 60, "owner": "live-parent", "client": "dead-client"}
    )


def test_admission_failure_closes_boundary(tmp_path, monkeypatch):
    module = boundary()
    state = tmp_path / "state.json"
    module._save(state, {"status": "ready", "owner": None, "deadline": 1, "lease": 1})
    closed = []
    monkeypatch.setattr(module, "close_boundary", closed.append)
    with pytest.raises(module.BoundaryError):
        module.execute_in_boundary(state, ["true"], ROOT, {}, tmp_path / "log", 1)
    assert closed == [state]


def test_create_template_uses_private_storage_and_namespaces(tmp_path, monkeypatch):
    module = boundary()
    state = tmp_path / "state.json"
    module._save(
        state,
        {
            "status": "creating",
            "owner": {},
            "image": "sha256:" + "a" * 64,
            "token": "owned",
            "repo": "/repo",
            "workspace": "/work",
        },
    )
    monkeypatch.setattr(module, "_pidfd", lambda owner: os.open(os.devnull, os.O_RDONLY))
    monkeypatch.setattr(module, "_expired", lambda data: False)
    captured = []

    def api(method, path, data, expected):
        captured.append(data)
        raise module.BoundaryError("stop after payload capture")

    monkeypatch.setattr(module, "_api", api)
    with pytest.raises(module.BoundaryError, match="payload"):
        module._watch(state)
    payload = captured[0]
    assert payload["Entrypoint"] == ["dind", "docker-init", "--", "dockerd"]
    assert "--storage-driver=overlay2" in payload["Cmd"]
    assert payload["HostConfig"]["PidMode"] == ""
    assert payload["HostConfig"]["NetworkMode"] == "bridge"
    assert payload["HostConfig"]["Binds"] == ["/repo:/repo:ro", "/work:/work:rw"]
    assert all(mount["Type"] == "volume" and "Source" not in mount for mount in payload["HostConfig"]["Mounts"])


def test_exact_ownership_mismatch_never_kills(tmp_path, monkeypatch):
    module = boundary()
    state = tmp_path / "state.json"
    cid = "a" * 64
    module._save(state, {"status": "ready", "container_id": cid, "image": "sha256:" + "b" * 64, "token": "owned"})
    calls = []

    def api(method, path, **kwargs):
        calls.append((method, path))
        return 200, {"Id": cid, "Image": "sha256:" + "b" * 64, "Config": {"Labels": {module.LABEL: "unrelated"}}}

    monkeypatch.setattr(module, "_api", api)
    with pytest.raises(module.BoundaryError, match="ownership"):
        module._remove(state)
    assert all(method == "GET" for method, path in calls)
    assert module._read(state)["status"] == "uncertain"


@pytest.mark.parametrize("attach_next", [False, True])
def test_watch_revalidates_stale_owner_after_successful_handoff(tmp_path, monkeypatch, attach_next):
    module = boundary()
    state = tmp_path / "state.json"
    old_owner = {"pid": 123, "start": "1"}
    next_owner = {"pid": 456, "start": "2"}
    owner = old_owner
    old_dead = False
    cid = "a" * 64
    image = "sha256:" + "b" * 64
    module._save(
        state,
        {
            "status": "creating",
            "owner": old_owner,
            "image": image,
            "token": "owned",
            "repo": "/repo",
            "workspace": "/work",
            "docker_config": str(tmp_path),
            "deadline": time.time() + 60,
            "lease": time.time() + 60,
        },
    )
    monkeypatch.setattr(module, "_identity", lambda: owner)
    monkeypatch.setattr(module, "_dead", lambda candidate: candidate == old_owner and old_dead)
    monkeypatch.setattr(module, "_pidfd", lambda owner: os.open(os.devnull, os.O_RDONLY))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    removed = []

    def api(method, path, data=None, expected=(200,)):
        if path.startswith("/containers/create?"):
            return 201, {"Id": cid}
        if path == f"/containers/{cid}/json":
            return (
                (404, {})
                if removed
                else (200, {"Id": cid, "Image": image, "Config": {"Labels": {module.LABEL: "owned"}}})
            )
        if method == "DELETE":
            removed.append(path)
        return expected[0], {}

    monkeypatch.setattr(module, "_api", api)
    read = module._read
    interleaved = False

    def stale_read(path):
        nonlocal interleaved, old_dead, owner
        snapshot = read(path)
        if snapshot["status"] == "ready" and not interleaved:
            interleaved = True
            module.handoff_boundary(path)
            old_dead = True
            if attach_next:
                owner = next_owner
                module.attach_boundary(path)
        return snapshot

    monkeypatch.setattr(module, "_read", stale_read)
    survived = []

    def next_tick(seconds):
        current = read(state)
        assert current["status"] == "ready"
        assert current["owner"] == (next_owner if attach_next else None)
        assert not removed
        survived.append(True)
        module._update(state, close_requested=True)

    monkeypatch.setattr(module.time, "sleep", next_tick)
    module._watch(state)
    assert interleaved
    assert survived == [True], "stale owner death destroyed a successfully handed-off boundary"
    assert len(removed) == 1
    assert read(state)["status"] == "closed"


@pytest.mark.parametrize("reason", ["live", "close_requested", "owner", "client", "lease", "deadline", "emergency"])
def test_conditional_removal_preserves_expiry_and_force_semantics(tmp_path, monkeypatch, reason):
    module = boundary()
    state = tmp_path / "state.json"
    data = {
        "status": "ready",
        "container_id": "a" * 64,
        "owner": None,
        "deadline": time.time() + 60,
        "lease": time.time() + 60,
    }
    if reason in ("owner", "client"):
        data[reason] = "dead"
    elif reason in ("lease", "deadline"):
        data[reason] = 0
    elif reason == "close_requested":
        data[reason] = True
    module._save(state, data)
    monkeypatch.setattr(module, "_dead", lambda owner: owner == "dead")
    monkeypatch.setattr(module, "_owned", lambda data: None)
    calls = []
    monkeypatch.setattr(module, "_api", lambda *a, **k: calls.append(a))
    if reason == "emergency":
        assert module._remove(state) is True
    else:
        assert module._remove(state, expired_only=True) is (reason != "live")
    assert module._read(state)["status"] == ("ready" if reason == "live" else "closed")
    assert calls == ([] if reason == "live" else [("GET", "/_ping")])


@pytest.mark.parametrize("transition", ["attach_boundary", "handoff_boundary"])
def test_expiry_claim_blocks_real_process_phase_transition(tmp_path, monkeypatch, transition):
    module = boundary()
    state = tmp_path / "state.json"
    module._save(
        state,
        {
            "status": "ready",
            "container_id": "a" * 64,
            "owner": None,
            "deadline": time.time() + 60,
            "lease": 0,
        },
    )
    script = """import fcntl, os, sys
from pathlib import Path
import live_shard_boundary as b
state = Path(sys.argv[1])
fd = os.open(str(state) + '.lock', os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print('blocked', flush=True)
else:
    raise AssertionError('expiry check released the admission lock')
finally:
    os.close(fd)
b._identity = lambda: {'pid': os.getpid(), 'start': 'test'}
try:
    getattr(b, sys.argv[2])(state)
except b.BoundaryError:
    assert b._read(state)['status'] == 'closed'
    print('rejected', flush=True)
else:
    raise AssertionError('transition admitted during closure')
"""
    children = []
    expired = module._expired

    def contend(data):
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(state), transition],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"PYTHONPATH": str(ROOT / "tools"), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        children.append(process)
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 5)[0], "phase process did not reach lock"
        assert process.stdout.readline().strip() == "blocked"
        return expired(data)

    monkeypatch.setattr(module, "_expired", contend)
    monkeypatch.setattr(module, "_owned", lambda data: None)
    monkeypatch.setattr(module, "_api", lambda *a, **k: (200, {}))
    try:
        assert module._remove(state, expired_only=True) is True
        assert len(children) == 1
        stdout, stderr = children[0].communicate(timeout=5)
        assert children[0].returncode == 0, stderr
        assert stdout.strip() == "rejected"
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_lost_create_response_remains_uncertain_without_exact_id(tmp_path, monkeypatch):
    module = boundary()
    state = tmp_path / "state.json"
    module._save(
        state,
        {
            "status": "creating",
            "owner": {},
            "image": "sha256:" + "a" * 64,
            "token": "owned",
            "repo": "/repo",
            "workspace": "/work",
        },
    )
    monkeypatch.setattr(module, "_pidfd", lambda owner: os.open(os.devnull, os.O_RDONLY))
    monkeypatch.setattr(module, "_expired", lambda data: False)
    accepted = []

    def lost_response(method, path, data=None, expected=(200,)):
        assert method == "POST" and path == "/containers/create?name=ea-shard-owned"
        accepted.append(data)
        raise TimeoutError("accepted create response lost")

    monkeypatch.setattr(module, "_api", lost_response)
    with pytest.raises(TimeoutError, match="response lost"):
        module._watch(state)
    assert module._read(state)["status"] == "uncertain"
    assert not module._read(state).get("container_id")
    with pytest.raises(module.BoundaryError, match="destroy shard VM"):
        module.close_boundary(state)
    with pytest.raises(module.BoundaryError, match="settlement uncertain"):
        module._remove(state)
    assert module._read(state)["status"] == "uncertain"
    assert len(accepted) == 1


@pytest.fixture
def genuine_vm(tmp_path):
    if os.environ.get("EVAL_AUTHOR_BOUNDARY_ACCEPTANCE") != "1":
        pytest.skip("genuine disposable-VM Docker boundary controls are opt-in")
    module = boundary()
    module.require_disposable_vm()
    image = os.environ["EVAL_AUTHOR_BOUNDARY_IMAGE"]
    work = Path(os.environ.get("EVAL_AUTHOR_BOUNDARY_WORKSPACE", str(tmp_path / "work")))
    work.mkdir(mode=0o700, exist_ok=True)
    state = tmp_path / "control/state.json"
    module.create_boundary(state, repo=ROOT, workspace=work, image=image, deadline=time.time() + 180)
    try:
        yield module, state, work
    finally:
        module.close_boundary(state)


@pytest.mark.parametrize(("mode", "forced"), [("cooperative", False), ("ignore", False), ("late", True)])
def test_genuine_vm_native_worker_containment(genuine_vm, mode, forced):
    module, state, work = genuine_vm
    native = os.environ.get("EVAL_AUTHOR_BOUNDARY_NATIVE_PYTHON")
    assert native, "acceptance requires the pinned native interpreter inside the workspace"
    case = work / ("case-" + state.parent.parent.name)
    case.mkdir(mode=0o700)
    child = """import os,signal,sys,time,subprocess
from pathlib import Path
root=Path(sys.argv[1]); mode=sys.argv[2]
if mode == 'ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)
image=(root/'image').read_text().strip()
def create():
    import json
    compose=root/'compose.json'
    compose.write_text(json.dumps({'services':{'worker':{'image':image,'command':['sleep','30'],'container_name':'late-creation'}}}))
    command=['docker','compose','-p','boundary-control','-f',str(compose),'up','-d']
    process=subprocess.Popen(command,start_new_session=True)
    assert process.wait(timeout=15)==0
    cid=subprocess.check_output(['docker','inspect','--format','{{.Id}}','late-creation'],text=True).strip()
    (root/'container-id').write_text(cid)
if mode != 'late': create()
(root/'ready').write_text('ready')
time.sleep(10)
if mode == 'late': create()
(root/'escaped').write_text('escaped')
"""
    worker = """import os,signal,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import distribution
import json
assert distribution('skillevaluator').version == '0.7.0'
assert distribution('harbor').version == '0.24.0'
assert json.loads(distribution('skillevaluator').read_text('direct_url.json'))['vcs_info']['commit_id']=='3fd2cf57e4a2af01f282e001f7515b6e76f18ec7'
from skillevaluator.tier3.harbor.runner import _run_bounded_harbor_process
root=Path(sys.argv[1])
pool=ThreadPoolExecutor(max_workers=1)
future=pool.submit(_run_bounded_harbor_process,[sys.executable,'-c',sys.argv[2],str(root),sys.argv[3]],env=dict(os.environ),cwd=root,stdin_text=None,timeout_seconds=None,max_output_bytes=8192,diagnostic_tail_chars=256,secret_values=set())
if sys.argv[4]=='forced':
    end=time.monotonic()+10
    while not (root/'ready').exists() and time.monotonic()<end: time.sleep(.01)
    os.kill(os.getpid(),signal.SIGKILL)
future.result()
"""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(work),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "HARBOR_TELEMETRY": "0",
    }
    prepare = [
        "sh",
        "-ec",
        'docker pull busybox:1.37.0 && docker image inspect --format "{{.Id}}" busybox:1.37.0 > "$1"',
        "sh",
        str(case / "image"),
    ]
    assert module.execute_in_boundary(state, prepare, work, env, case / "prepare.log", 60) == (0, None)
    limits_log = case / "limits.log"
    limits = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--cpus=1",
        "--memory=512m",
        "--read-only",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--env=HOME=/tmp/ea-home",
        "--user=root",
        "--entrypoint=/bin/sh",
        (case / "image").read_text().strip(),
        "-ec",
        "cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max",
    ]
    assert module.execute_in_boundary(state, limits, work, env, limits_log, 30) == (0, None)
    quota, memory = limits_log.read_text().splitlines()
    maximum, period = map(int, quota.split())
    assert maximum == period
    assert int(memory) == 512 * 1024 * 1024
    from collect_live_skill_evaluations import execute_with_progress

    code, error = execute_with_progress(
        [native, "-c", worker, str(case), child, mode, "forced" if forced else "timeout"],
        work,
        env,
        case / "native.log",
        8 if not forced else 15,
        boundary_state=state,
        final=True,
    )
    assert (case / "ready").exists(), "native ThreadPoolExecutor launch was not reached"
    if mode != "late":
        assert (case / "container-id").exists(), "native Docker creation was not reached"
    assert code != 0
    assert error == (None if forced else "timeout")
    assert module._read(state)["status"] == "closed"
    assert module._owned(module._read(state)) is None
    time.sleep(11)
    assert not (case / "escaped").exists()


def test_genuine_vm_supervisor_sigkill_and_unrelated_survival(genuine_vm):
    module, sentinel, work = genuine_vm
    victim = sentinel.parent.parent / "victim/state.json"
    script = """import sys,time
from pathlib import Path
import live_shard_boundary as b
b.create_boundary(Path(sys.argv[1]),repo=Path(sys.argv[2]),workspace=Path(sys.argv[3]),image=sys.argv[4],deadline=time.time()+100)
print('ready',flush=True)
work=Path(sys.argv[3])
code="import time; from pathlib import Path; Path("+repr(str(work/'victim-active'))+").write_text('ready'); time.sleep(90)"
b.execute_in_boundary(Path(sys.argv[1]),['python3','-c',code],work,{'PATH':'/usr/local/bin:/usr/bin:/bin'},work/'victim.log',90,final=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(victim), str(ROOT), str(work), os.environ["EVAL_AUTHOR_BOUNDARY_IMAGE"]],
        stdout=subprocess.PIPE,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": str(ROOT / "tools")},
        text=True,
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 110)[0], "boundary readiness timed out"
        assert process.stdout.readline().strip() == "ready"
        until = time.monotonic() + 10
        while not (work / "victim-active").exists() and time.monotonic() < until:
            time.sleep(0.05)
        assert (work / "victim-active").exists(), "remote workload was not admitted"
        process.kill()
        process.wait(timeout=5)
        until = time.monotonic() + 30
        while time.monotonic() < until and module._read(victim)["status"] != "closed":
            time.sleep(0.1)
        assert module._read(victim)["status"] == "closed"
        assert module._owned(module._read(victim)) is None
        assert module._owned(module._read(sentinel))["State"]["Running"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if victim.exists():
            module.close_boundary(victim)


def test_genuine_vm_handoff_admits_next_process(genuine_vm):
    module, state, work = genuine_vm
    module.handoff_boundary(state)
    script = """import sys
from pathlib import Path
import live_shard_boundary as b
assert b.execute_in_boundary(Path(sys.argv[1]),['python3','-c','print(42)'],Path(sys.argv[2]),{'PATH':'/usr/local/bin:/usr/bin:/bin'},Path(sys.argv[2])/'handoff.log',10,final=True)==(0,None)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(state), str(work)],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": str(ROOT / "tools")},
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert process.returncode == 0
    assert (work / "handoff.log").read_text().strip() == "42"
    assert module._read(state)["status"] == "closed"


def test_genuine_vm_idle_lease_expires(genuine_vm):
    module, state, work = genuine_vm
    module.handoff_boundary(state)
    module._update(state, lease=time.time() + 0.5)
    until = time.monotonic() + 30
    while time.monotonic() < until and module._read(state)["status"] != "closed":
        time.sleep(0.1)
    assert module._read(state)["status"] == "closed"
    assert module._owned(module._read(state)) is None
