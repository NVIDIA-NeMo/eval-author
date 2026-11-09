# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host-only lifecycle authority for disposable per-shard Linux VMs."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import http.client
import json
import math
import os
import re
import select
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

LABEL = "io.eval-author.boundary"
SOCKET = "/var/run/docker.sock"
IDLE_SECONDS = 120
REMOTE_EXEC = """import json, os, sys
request = json.load(sys.stdin)
os.chdir(request['cwd'])
env = request['env']
env.pop('DOCKER_HOST', None)
env.pop('DOCKER_CONTEXT', None)
os.execvpe(request['argv'][0], request['argv'], env)
"""


class BoundaryError(RuntimeError):
    pass


def require_disposable_vm() -> None:
    if sys.platform != "linux":
        raise BoundaryError("isolated execution requires Linux disposable VM")
    if os.geteuid() != 0 or not Path("/run/systemd/system").is_dir():
        raise BoundaryError("boundary requires root and systemd in disposable VM")
    marker = Path("/etc/eval-author/disposable-shard")
    try:
        info = marker.lstat()
        approved = (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o022
            and marker.read_text().strip() == "isolated-vm"
        )
    except OSError:
        approved = False
    hosted = os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    vm = subprocess.run(["systemd-detect-virt", "--vm"], capture_output=True, text=True, timeout=5)
    if not (approved or hosted) or vm.returncode != 0:
        raise BoundaryError("explicit disposable VM approval required; shared runners forbidden")
    if not hasattr(os, "pidfd_open"):
        raise BoundaryError("pidfd supervision unavailable")


def _validate_paths(state, repo, workspace):
    paths = [Path(p).absolute() for p in (state, repo, workspace)]
    for path in paths:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise BoundaryError("linked boundary path")
    state, repo, workspace = paths
    if (
        state.is_relative_to(workspace)
        or state.is_relative_to(repo)
        or workspace.is_relative_to(repo)
        or repo.is_relative_to(workspace)
    ):
        raise BoundaryError("control state must be outside execution mounts")
    if not repo.is_dir() or not workspace.is_dir() or workspace.stat().st_mode & 0o077:
        raise BoundaryError("private workspace directory required")
    if str(repo) == "/" or str(workspace) in ("/", "/root", str(Path.home())):
        raise BoundaryError("unsafe execution mount")
    return state, repo, workspace


def _read(state):
    info = state.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise BoundaryError("unsafe control state")
    return json.loads(state.read_text())


@contextlib.contextmanager
def _lock(state):
    fd = os.open(str(state) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _save(state, data):
    temporary = state.with_name(state.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(state)


def _update(state, **fields):
    with _lock(state):
        data = _read(state)
        data.update(fields)
        _save(state, data)
        return data


class _Docker(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)


def _api(method, path, data=None, expected=(200,)):
    connection = _Docker("localhost", timeout=20)
    try:
        connection.request(
            method,
            path,
            body=json.dumps(data) if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read()
        if response.status not in expected:
            raise BoundaryError("Docker control request failed")
        return response.status, json.loads(body) if body and body[:1] in (b"{", b"[") else body
    finally:
        connection.close()


def _identity(pid=None):
    pid = os.getpid() if pid is None else pid
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start": fields[19]}


def _pidfd(owner):
    fd = getattr(os, "pidfd_open")(owner["pid"])
    if _identity(owner["pid"]) != owner:
        os.close(fd)
        raise BoundaryError("supervisor identity changed")
    return fd


def _dead(owner):
    if owner is None:
        return False
    try:
        fd = _pidfd(owner)
    except (OSError, BoundaryError):
        return True
    try:
        return bool(select.select([fd], [], [], 0)[0])
    finally:
        os.close(fd)


def _expired(data):
    return (
        data.get("close_requested", False)
        or time.time() >= min(data["deadline"], data["lease"])
        or _dead(data["owner"])
        or _dead(data.get("client"))
    )


def _owned(data):
    cid = data.get("container_id")
    if not isinstance(cid, str) or not re.fullmatch("[a-f0-9]{64}", cid):
        raise BoundaryError("outer container identity unavailable")
    status, row = _api("GET", f"/containers/{cid}/json", expected=(200, 404))
    if status == 404:
        return None
    if row["Id"] != cid or row["Image"] != data["image"] or row["Config"]["Labels"].get(LABEL) != data["token"]:
        raise BoundaryError("outer container ownership mismatch")
    return row


def _remove(state, *, expired_only=False):
    with _lock(state):
        data = _read(state)
        if data["status"] == "closed":
            return True
        # A handoff/attach may have superseded the watchdog's unlocked snapshot.
        # Revalidate and claim closure under the same lock as phase admission.
        if expired_only and not _expired(data):
            return False
        if not data.get("container_id"):
            raise BoundaryError("creation settlement uncertain")
        data["status"] = "closing"
        _save(state, data)
        try:
            row = _owned(data)
            cid = data["container_id"]
            if row is not None:
                # PID 1 death destroys the entire private PID namespace, including
                # detached native sessions and the private daemon's late creations.
                _api("POST", f"/containers/{cid}/kill?signal=KILL", expected=(204, 409, 404))
                _api("DELETE", f"/containers/{cid}?force=true&v=true", expected=(204, 404))
            if _owned(data) is not None:
                raise BoundaryError("outer container removal unverified")
            _api("GET", "/_ping")
            data["status"] = "closed"
            _save(state, data)
            return True
        except BaseException:
            data["status"] = "uncertain"
            _save(state, data)
            raise


def _docker_exec(data):
    return [
        "docker",
        "--host",
        "unix://" + SOCKET,
        "--config",
        data["docker_config"],
        "exec",
        "-i",
        data["container_id"],
        "python3",
        "-c",
        REMOTE_EXEC,
    ]


def _watch(state):
    data = _read(state)
    try:
        fd = _pidfd(data["owner"])
        os.close(fd)
        if _expired(data):
            _update(state, status="closed")
            return
        payload = {
            "Image": data["image"],
            "Entrypoint": ["dind", "docker-init", "--", "dockerd"],
            "Cmd": ["--host=unix:///var/run/docker.sock", "--storage-driver=overlay2", "--userland-proxy=false"],
            "Labels": {LABEL: data["token"]},
            "Env": ["DOCKER_TLS_CERTDIR="],
            "HostConfig": {
                "Privileged": True,
                "PidMode": "",
                "NetworkMode": "bridge",
                "CgroupnsMode": "private",
                "Binds": [
                    data["repo"] + ":" + data["repo"] + ":ro",
                    data["workspace"] + ":" + data["workspace"] + ":rw",
                ],
                "Mounts": [
                    {"Type": "volume", "Target": "/var/lib/docker"},
                    {"Type": "volume", "Target": "/var/lib/containerd"},
                ],
                "RestartPolicy": {"Name": "no"},
            },
        }
        _, created = _api("POST", "/containers/create?name=ea-shard-" + data["token"], payload, (201,))
        cid = created["Id"]
        if not re.fullmatch("[a-f0-9]{64}", cid):
            raise BoundaryError("invalid outer container ID")
        data = _update(state, container_id=cid)
        _owned(data)
        if _expired(data):
            return
        _api("POST", f"/containers/{cid}/start", expected=(204,))
        # Readiness is native daemon readiness, not merely outer-container start.
        ready_until = min(time.time() + 60, data["deadline"])
        while True:
            remaining = ready_until - time.time()
            if remaining <= 0:
                raise BoundaryError("private Docker daemon not ready")
            data = _read(state)
            if _expired(data):
                return
            remaining = ready_until - time.time()
            if remaining <= 0:
                raise BoundaryError("private Docker daemon not ready")
            try:
                result = subprocess.run(
                    _docker_exec(data),
                    input=json.dumps(
                        {
                            "argv": ["docker", "info"],
                            "cwd": "/",
                            "env": {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root"},
                        }
                    ),
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=min(5, remaining),
                )
                ready = result.returncode == 0
            except subprocess.TimeoutExpired:
                ready = False
            remaining = ready_until - time.time()
            if remaining <= 0:
                raise BoundaryError("private Docker daemon not ready")
            if _expired(_read(state)):
                return
            if ready:
                break
            time.sleep(min(0.25, max(0, ready_until - time.time())))
        _update(state, status="ready")
        while True:
            data = _read(state)
            if data["status"] == "closed":
                return
            if _expired(data) and _remove(state, expired_only=True):
                return
            time.sleep(0.2)
    finally:
        data = _read(state)
        if data.get("container_id"):
            _remove(state)
        elif data["status"] != "closed":
            _update(state, status="uncertain")


def create_boundary(state: Path, *, repo: Path, workspace: Path, image: str, deadline: float) -> dict:
    require_disposable_vm()
    state, repo, workspace = _validate_paths(state, repo, workspace)
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise BoundaryError("immutable execution image ID required")
    if not math.isfinite(deadline) or not time.time() < deadline <= time.time() + 4500:
        raise BoundaryError("invalid absolute job deadline")
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.parent.stat().st_mode & 0o077 or state.exists():
        raise BoundaryError("fresh private host control path required")
    _, observed = _api("GET", f"/images/{image}/json")
    if observed["Id"] != image:
        raise BoundaryError("execution image mismatch")
    token = uuid.uuid4().hex
    config = state.parent / ("docker-" + token)
    config.mkdir(mode=0o700)
    data = {
        "status": "creating",
        "container_id": None,
        "image": image,
        "token": token,
        "repo": str(repo),
        "workspace": str(workspace),
        "deadline": deadline,
        "lease": min(deadline, time.time() + IDLE_SECONDS),
        "owner": _identity(),
        "docker_config": str(config),
        "unit": "ea-shard-" + token,
    }
    _save(state, data)
    try:
        # systemd owns creation too: a dying client cannot orphan an in-flight
        # create/start transaction before its exact-ID receipt is registered.
        subprocess.run(
            [
                "systemd-run",
                "--quiet",
                "--unit=" + data["unit"],
                "--property=Type=exec",
                "--property=UMask=0077",
                "--property=StandardOutput=null",
                "--property=StandardError=null",
                "--property=RuntimeMaxSec=" + str(math.ceil(deadline - time.time() + 60)),
                "--property=TimeoutStopSec=60",
                "--property=ExecStopPost="
                + sys.executable
                + " "
                + str(Path(__file__).resolve())
                + " --emergency-close "
                + str(state),
                sys.executable,
                str(Path(__file__).resolve()),
                "--watch",
                str(state),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        until = min(deadline, time.time() + 100)
        while time.time() < until:
            data = _read(state)
            if data["status"] == "ready":
                return data
            if data["status"] in ("uncertain", "closed"):
                raise BoundaryError("boundary initialization failed")
            time.sleep(0.1)
        raise BoundaryError("boundary initialization timed out")
    except BaseException:
        close_boundary(state)
        raise


def close_boundary(state: Path) -> None:
    data = _update(state, close_requested=True)
    if data["status"] == "closed":
        return
    # Only the watchdog may settle a create/start already admitted to Docker.
    until = time.monotonic() + 90
    while time.monotonic() < until:
        data = _read(state)
        if data["status"] == "closed":
            return
        if data["status"] == "uncertain":
            raise BoundaryError("boundary teardown uncertain; destroy shard VM")
        time.sleep(0.1)
    raise BoundaryError("boundary teardown unverified; destroy shard VM")


def attach_boundary(state: Path) -> None:
    with _lock(state):
        data = _read(state)
        if data["status"] != "ready" or data["owner"] not in (None, _identity()) or _expired(data):
            raise BoundaryError("only idle handed-off boundary may attach")
        data.update(owner=_identity(), lease=min(data["deadline"], time.time() + IDLE_SECONDS))
        _save(state, data)


def handoff_boundary(state: Path) -> None:
    with _lock(state):
        data = _read(state)
        if data["status"] != "ready" or data["owner"] != _identity() or _expired(data):
            raise BoundaryError("only idle owning supervisor may hand off boundary")
        data.update(owner=None, lease=min(data["deadline"], time.time() + IDLE_SECONDS))
        _save(state, data)


def execute_in_boundary(state: Path, argv, cwd, env, log, timeout, *, final=False):
    process = None
    try:
        with _lock(state):
            data = _read(state)
            if data["status"] != "ready" or _expired(data):
                raise BoundaryError("boundary closed or not ready")
            if data["owner"] not in (None, _identity()) and not _dead(data["owner"]):
                # A collector child can attach only while its original external
                # phase supervisor remains guarded; never replace that owner.
                owner = data["owner"]
            else:
                owner = _identity()
            if not math.isfinite(timeout) or timeout <= 0 or time.time() + timeout > data["deadline"]:
                raise BoundaryError("insufficient boundary execution budget")
            data.update(
                status="active",
                owner=owner,
                client=_identity(),
                lease=min(data["deadline"], time.time() + timeout + 10),
            )
            _save(state, data)

        payload = json.dumps({"argv": list(map(str, argv)), "cwd": str(cwd), "env": dict(env)})
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            process = subprocess.Popen(
                _docker_exec(data),
                stdin=subprocess.PIPE,
                stdout=stream,
                stderr=stream,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": data["docker_config"]},
            )
            try:
                process.communicate(payload.encode(), timeout=timeout)
                outcome = (process.returncode, None)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                outcome = (process.returncode, "timeout")
        if final or outcome[0] != 0 or outcome[1]:
            close_boundary(state)
        else:
            with _lock(state):
                current = _read(state)
                if current["status"] != "active" or _expired(current):
                    raise BoundaryError("boundary expired during execution")
                current.update(status="ready", client=None, lease=min(data["deadline"], time.time() + IDLE_SECONDS))
                _save(state, current)
        return outcome
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        if state.exists():
            close_boundary(state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watch", type=Path)
    group.add_argument("--emergency-close", type=Path)
    args = parser.parse_args()
    if args.watch:
        _watch(args.watch)
    else:
        _remove(args.emergency_close)


if __name__ == "__main__":
    main()
