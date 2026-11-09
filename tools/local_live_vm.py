# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Own one fresh Lima VM outside the guest for the entire local shard job.

Use ``with LocalVM.create(fresh_root) as vm: vm.shell([...], timeout=...)``.
The host coordinator keeps this handle across live_shard.py phases. Guest exit
is not teardown; only close()['eliminated'] establishes outer containment.
Control/log files are private and must not be published or mounted in the guest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import select
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

LIMA = Path("/opt/homebrew/bin/limactl")
IMAGE = "https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-arm64.img"
IMAGE_DIGEST = "sha256:50e24e1fb79e89116e939d90aa7738f7af48c442fa1bc2510131e0b39c50348e"


def _config(share):
    return {
        "minimumLimaVersion": "2.3.0",
        "vmType": "vz",
        "os": "Linux",
        "arch": "aarch64",
        "cpus": 4,
        "memory": "8GiB",
        "disk": "60GiB",
        "images": [{"location": IMAGE, "arch": "aarch64", "digest": IMAGE_DIGEST}],
        "mountType": "virtiofs",
        "mounts": [{"location": str(share), "mountPoint": "/mnt/ea-share", "writable": True}],
        "ssh": {
            "loadDotSSHPubKeys": False,
            "forwardAgent": False,
            "forwardX11": False,
            "forwardX11Trusted": False,
            "overVsock": True,
        },
        "containerd": {"system": False, "user": False},
        "networks": [],
        "networkOpts": {"hostLoopback": False},
        "portForwards": [
            {
                "guestIP": "0.0.0.0",
                "guestIPMustBeZero": False,
                "guestPortRange": [1, 65535],
                "proto": "any",
                "ignore": True,
            }
        ],
        "propagateProxyEnv": False,
        "env": {},
        "hostResolver": {"enabled": False},
        "dns": ["1.1.1.1", "1.0.0.1"],
        "user": {
            "name": "runner",
            "comment": "Isolated local CI runner",
            "home": "/home/runner",
            "shell": "/bin/bash",
            "uid": 1000,
            "passwordlessSudo": True,
        },
        "upgradePackages": False,
        "timezone": "UTC",
        "vmOpts": {"vz": {"rosetta": {"enabled": False, "binfmt": False}}},
        "nestedVirtualization": False,
    }


def _write(path, data):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink in control path")
    st = path.stat()
    return [st.st_dev, st.st_ino]


def _env(root, cli):
    return {
        "HOME": str(root / "home"),
        "LIMA_HOME": str(root / "lima"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "TMPDIR": str(root / "tmp"),
        "PATH": f"{cli.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


class _Owner:
    def __init__(self, pid):
        # Register while still the direct child, then check reparenting. A reused
        # PID cannot masquerade as our parent across this registration window.
        self.kqueue = None
        self.pidfd = None
        if hasattr(select, "kqueue"):
            self.kqueue = select.kqueue()
            self.kqueue.control(
                [
                    select.kevent(
                        pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                0,
                0,
            )
        elif hasattr(os, "pidfd_open"):
            self.pidfd = getattr(os, "pidfd_open")(pid)
        else:
            raise RuntimeError("PID-safe owner monitoring unavailable")
        if os.getppid() != pid:
            raise RuntimeError("owner exited before registration")

    def dead(self):
        if self.kqueue is not None:
            return bool(self.kqueue.control([], 1, 0))
        assert self.pidfd is not None
        return bool(select.select([self.pidfd], [], [], 0)[0])


class _Stop(Exception):
    pass


def _vm_name(token):
    # The short instance name binds the full token; LIMA_HOME is private per job.
    return "ea-" + hashlib.sha256(token.encode("ascii")).hexdigest()[:8]


def _validate_socket_path(root, name):
    # Lima 2.3 on macOS reserves this temporary SSH control-socket suffix.
    path = root / "lima" / name / "ssh.sock.1234567890123456"
    length = len(os.fsencode(path))
    if length >= 104:
        raise ValueError(
            f"Lima SSH socket path is {length} bytes; must be less than 104 bytes; choose a shorter private root"
        )


def _validate_binding(root, cli, spec):
    if root / "lima" != Path(spec["lima_home"]) or root != Path(spec["root"]):
        raise ValueError("ownership path mismatch")
    if len(spec["token"]) != 32 or spec["name"] != _vm_name(spec["token"]):
        raise ValueError("invalid owned name")
    int(spec["token"], 16)
    _validate_socket_path(root, spec["name"])
    for name, identity in spec["directories"].items():
        if name not in ("", "lima", "home", "cache", "tmp", "share"):
            raise ValueError("invalid directory binding")
        if _identity(root / name) != identity:
            raise ValueError("ownership directory changed")
    if _digest(root / "lima.yaml") != spec["config_sha256"]:
        raise ValueError("owned configuration changed")
    if json.loads((root / "lima.yaml").read_text()) != _config(root / "share"):
        raise ValueError("unsafe configuration")
    if _digest(cli) != spec["limactl_sha256"]:
        raise ValueError("Lima executable changed")


class _Watchdog:
    def __init__(self, root, channel, owner):
        self.root, self.channel, self.owner = root, channel, owner
        self.spec = json.loads((root / "ownership.json").read_text())
        self.cli = Path(self.spec["limactl"])
        self.name = self.spec["name"]
        self.home = root / "lima"
        self.env = _env(root, self.cli)
        self.deadline = self.spec["deadline"]
        self.command_seconds = self.spec["command_seconds"]
        self.serial = 0
        self.admitted = False
        self.start_settled = False
        self.stop_reason = None

    def validate(self):
        _validate_binding(self.root, self.cli, self.spec)

    def check(self, defer=False):
        if self.owner.dead():
            # kqueue's exit notification is one-shot; retain it during start.
            self.stop_reason = "owner_died"
        if self.stop_reason is None and time.monotonic() >= self.deadline:
            self.stop_reason = "deadline"
        if self.stop_reason is None and select.select([self.channel], [], [], 0)[0]:
            self.channel.recv(32)
            self.stop_reason = "owner_closed"
        if self.stop_reason is not None and not defer:
            raise _Stop(self.stop_reason)

    def command(self, args, seconds, monitor=True, defer_stop=False):
        self.validate()
        self.serial += 1
        with (
            (self.root / f"lima-{self.serial}.log").open("w+b") as output,
            (self.root / f"lima-{self.serial}.stderr.log").open("wb") as diagnostic,
        ):
            os.chmod(output.name, 0o600)
            os.chmod(diagnostic.name, 0o600)
            proc = subprocess.Popen(
                [str(self.cli), *args],
                env=self.env,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=diagnostic,
                start_new_session=True,
            )
            try:
                end = time.monotonic() + seconds
                while proc.poll() is None:
                    if monitor:
                        self.check(defer=defer_stop)
                    if time.monotonic() >= end:
                        raise TimeoutError("Lima command deadline")
                    time.sleep(0.05)
                output.seek(0)
                text = output.read(1048577)
                if len(text) > 1048576:
                    raise ValueError("Lima command output truncated")
                return proc.returncode, text.decode(errors="replace")
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=2)

    def inventory(self, monitor=False):
        code, text = self.command(["list", "--json"], self.command_seconds, monitor)
        if code:
            raise RuntimeError("Lima inventory unavailable")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows):
            raise ValueError("invalid Lima inventory")
        owned = [row for row in rows if row["name"] == self.name]
        if len(owned) > 1:
            raise ValueError("duplicate owned VM")
        if owned and Path(owned[0]["dir"]) != self.home / self.name:
            raise ValueError("owned VM path mismatch")
        return owned[0] if owned else None

    def cleanup(self, reason):
        result = {"schema_version": 1, "status": "uncertain", "reason": reason, "eliminated": False}
        if not self.admitted:
            _write(self.root / "result.json", result)
            return result
        try:
            # Stop even when start failed before returning a result. No inner
            # Docker receipt or guest phase outcome participates in this decision.
            self.command(["stop", "--force", self.name], self.command_seconds, False)
            row = self.inventory()
            if (row is None and os.path.lexists(self.home / self.name)) or (
                row is not None and row.get("status") != "Stopped"
            ):
                raise RuntimeError("VM not verifiably stopped")
            self.command(["delete", "--force", self.name], self.command_seconds, False)
            if self.inventory() is not None or (self.home / self.name).exists():
                raise RuntimeError("VM deletion unverified")
            # Empty inventory cannot settle a failed/interrupted start: a
            # detached creator may still materialize this exact VM later.
            if self.start_settled:
                result.update(status="closed", eliminated=True)
        except Exception:
            pass
        _write(self.root / "result.json", result)
        return result

    def run(self):
        reason = "start_failed"
        try:
            self.validate()
            _write(self.root / "armed.json", {"watchdog_pid": os.getpid()})
            self.channel.sendall(b"R")
            while not select.select([self.channel], [], [], 0.05)[0]:
                if self.owner.dead() or time.monotonic() >= self.deadline:
                    raise _Stop("owner_died_before_start")
            if self.channel.recv(1) != b"G":
                raise _Stop("owner_closed")
            self.check()
            code, version = self.command(["--version"], self.command_seconds)
            if code or version.strip() != "limactl version 2.3.0":
                raise RuntimeError("Lima 2.3.0 required")
            # Fresh LIMA_HOME admits no adoption, including an existing owned name.
            if any(self.home.iterdir()):
                raise RuntimeError("nonempty private Lima home")
            self.check()
            self.admitted = True
            # Once admitted, let Lima finish within its startup budget even if
            # the owner dies/closes or the job expires. Killing the CLI does not
            # cancel detached creation; an unsuccessful start remains uncertain.
            code, _ = self.command(
                ["start", "--tty=false", "--name", self.name, str(self.root / "lima.yaml")],
                self.spec["start_seconds"],
                defer_stop=True,
            )
            if code or (self.inventory() or {}).get("status") != "Running":
                raise RuntimeError("VM start unverified")
            self.start_settled = True
            self.check()
            _write(self.root / "running.json", {"status": "running"})
            self.channel.sendall(b"S")
            while True:
                self.check()
                time.sleep(0.05)
        except _Stop as exc:
            reason = str(exc)
        except Exception:
            pass
        finally:
            self.cleanup(reason)


class LocalVM:
    _transport_failed = False
    root: Path
    share: Path
    lima_home: Path
    name: str
    cli: Path
    spec: dict
    channel: socket.socket
    process: subprocess.Popen

    @classmethod
    def create(cls, root: Path, *, limactl: Path = LIMA, job_seconds=7200, command_seconds=30, start_seconds=600):
        for value in (job_seconds, command_seconds, start_seconds):
            if not math.isfinite(value) or not 0 < value <= 86400:
                raise ValueError("invalid lifecycle budget")
        root = Path(root).absolute()
        token = uuid.uuid4().hex
        vm_name = _vm_name(token)
        _validate_socket_path(root, vm_name)
        _identity(root.parent)
        cli = Path(limactl).resolve(strict=True)
        root.mkdir(mode=0o700)
        for name in ("lima", "home", "cache", "tmp", "share"):
            (root / name).mkdir(mode=0o700)
        _write(root / "lima.yaml", _config(root / "share"))
        spec = {
            "root": str(root),
            "lima_home": str(root / "lima"),
            "token": token,
            "name": vm_name,
            "limactl": str(cli),
            "limactl_sha256": _digest(cli),
            "config_sha256": _digest(root / "lima.yaml"),
            "directories": {name: _identity(root / name) for name in ("", "lima", "home", "cache", "tmp", "share")},
            "deadline": time.monotonic() + job_seconds,
            "command_seconds": command_seconds,
            "start_seconds": start_seconds,
        }
        _write(root / "ownership.json", spec)
        vm = cls()
        vm.root, vm.share, vm.lima_home = root, root / "share", root / "lima"
        vm.name, vm.cli, vm.spec = spec["name"], cli, spec
        vm.channel, child = socket.socketpair()
        with (root / "watchdog.log").open("wb") as log:
            os.chmod(log.name, 0o600)
            vm.process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "_watchdog",
                    str(root),
                    str(child.fileno()),
                    str(os.getpid()),
                ],
                pass_fds=(child.fileno(),),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=_env(root, cli),
                cwd=root,
            )
        child.close()
        try:
            vm._receive(b"R", min(job_seconds, 10))
            vm.channel.sendall(b"G")
            vm._receive(b"S", min(job_seconds, start_seconds + 2 * command_seconds + 5))
            return vm
        except BaseException:
            vm.close()
            raise

    def _receive(self, expected, timeout):
        self.channel.settimeout(timeout)
        if self.channel.recv(1) != expected:
            raise RuntimeError("VM watchdog failed; inspect private result.json")

    def shell(self, argv, *, timeout, stdin=None, stdin_deadline=None):
        _validate_binding(self.root, self.cli, self.spec)
        if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
            raise ValueError("invalid guest argv")
        if stdin is not None and (not isinstance(stdin, bytes) or len(stdin) > 65536):
            raise ValueError("guest stdin must be bytes of at most 65536 bytes")
        if self.process.poll() is not None or (self.root / "result.json").exists():
            raise RuntimeError("VM lease is closed")
        remaining = self.spec["deadline"] - time.monotonic()
        if not math.isfinite(timeout) or not 0 < timeout <= remaining:
            raise ValueError("guest command exceeds VM lease")
        if self._transport_failed:
            raise RuntimeError("VM transport failed; close and verify owned VM elimination")
        command = [str(self.cli), "shell", "--workdir", "/", self.name, "--", *argv]
        env = _env(self.root, self.cli)
        # Admission only: the credential lease must not cap model execution.
        if stdin_deadline is not None and (
            type(stdin_deadline) not in (int, float)
            or not math.isfinite(stdin_deadline)
            or time.monotonic() >= stdin_deadline
        ):
            raise ValueError("guest stdin admission deadline invalid or expired")
        proc = subprocess.Popen(
            command,
            env=env,
            cwd=self.root,
            stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
            return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
        except BaseException:
            # This only stops the host transport group, not detached guest work.
            # Keep the independent watchdog alive and forbid another guest phase.
            self._transport_failed = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except BaseException:
                pass
            try:
                proc.wait(timeout=2)
            except BaseException:
                pass
            raise
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except BaseException:
                        pass

    def close(self):
        try:
            self.channel.sendall(b"C")
        except OSError:
            pass
        try:
            self.process.wait(timeout=4 * (self.spec["command_seconds"] + 2) + 5)
        except subprocess.TimeoutExpired:
            return {"schema_version": 1, "status": "uncertain", "reason": "watchdog_unresponsive", "eliminated": False}
        finally:
            self.channel.close()
        try:
            return json.loads((self.root / "result.json").read_text())
        except (OSError, ValueError):
            return {"schema_version": 1, "status": "uncertain", "reason": "watchdog_lost", "eliminated": False}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if not self.close()["eliminated"]:
            raise RuntimeError("VM elimination unverified; inspect private control root")


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "_watchdog":
        os.umask(0o077)
        owner = _Owner(int(sys.argv[4]))
        _Watchdog(Path(sys.argv[2]), socket.socket(fileno=int(sys.argv[3])), owner).run()
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, required=True, help="new private job directory; never an existing Lima home"
    )
    parser.add_argument("--job-seconds", type=float, default=7200)
    parser.add_argument(
        "command", nargs=argparse.REMAINDER, help="guest command after --; lifecycle owned until it exits"
    )
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("guest command required")
    with LocalVM.create(args.root, job_seconds=args.job_seconds) as vm:
        result = vm.shell(command, timeout=max(0.001, vm.spec["deadline"] - time.monotonic() - 1))
        # Guest output may contain secrets; retain it privately, not on stdout.
        for name, data in (("guest.stdout", result.stdout), ("guest.stderr", result.stderr)):
            path = vm.root / name
            with path.open("wb") as stream:
                os.chmod(path, 0o600)
                stream.write(data)
    print(json.dumps(vm.close()))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
