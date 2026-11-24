#!/opt/harbor-real/bin/python
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""`harbor` inside the generator container.

`harbor trial start` and `harbor run` go to the host Harbor sidecar. Help, version,
and local task commands pass through to the real Harbor at /opt/harbor-real. Every
other command needs Docker or a server and fails. The module uses only the standard
library so the host sidecar imports the same archive helpers.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import string
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any

HARBOR_VERSION = "0.24.0"
REAL_HARBOR = "/opt/harbor-real/bin/harbor"
URL_ENV = "HARBOR_SIDECAR_URL"
TOKEN_ENV = "HARBOR_SIDECAR_TOKEN"
METADATA_HEADER = "X-Harbor-Job"
MAX_ARCHIVE_BYTES = 1024**3
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
POLL_SECONDS = 1.0


def _tarinfo(name: str, kind: bytes, mode: int, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode & 0o777
    info.size = size
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def pack_tree(root: Path, arcname: str | None = None) -> bytes:
    """Return a deterministic tar of *root*, including dotfiles; reject anything but files and directories.

    With *arcname*, members live under that top directory; otherwise at the archive root.
    """
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"archive root must be an ordinary directory: {root}")
    entries: list[tuple[str, Path]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(current) / name
            entries.append((path.relative_to(root).as_posix(), path))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        if arcname is not None:
            tar.addfile(_tarinfo(arcname, tarfile.DIRTYPE, root.lstat().st_mode))
        for rel, path in sorted(entries):
            name = f"{arcname}/{rel}" if arcname is not None else rel
            info = path.lstat()
            if path.is_symlink():
                raise ValueError(f"archive must not contain symlinks: {rel}")
            if path.is_dir():
                tar.addfile(_tarinfo(name, tarfile.DIRTYPE, info.st_mode))
            elif path.is_file():
                data = path.read_bytes()
                tar.addfile(_tarinfo(name, tarfile.REGTYPE, info.st_mode, len(data)), io.BytesIO(data))
            else:
                raise ValueError(f"archive must contain only ordinary files and directories: {rel}")
    return buffer.getvalue()


def _member_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    parts = tuple(part for part in path.parts if part != ".")
    if path.is_absolute() or not parts or ".." in parts or any("\\" in part or "\0" in part for part in parts):
        raise ValueError(f"unsafe archive member: {name!r}")
    return parts


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def extract_tar(data: bytes, dest: Path, *, top: str | None = None, durable: bool = False) -> None:
    """Extract regular files and directories under *dest*; reject traversal, links, and special members.

    With *top*, every member must be that directory or lie under it. With *durable*,
    files and directories are fsynced before returning.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds the size limit")
    dest.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        raise ValueError(f"archive destination must not be a symlink: {dest}")
    written: list[Path] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar:
            parts = _member_parts(member.name)
            if top is not None and parts[0] != top:
                raise ValueError(f"archive member outside {top}/: {member.name!r}")
            target = dest.joinpath(*parts)
            for depth in range(1, len(parts) + 1):
                if dest.joinpath(*parts[:depth]).is_symlink():
                    raise ValueError(f"archive path crosses a symlink: {member.name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod((member.mode & 0o777) | 0o700)
                written.append(target)
            elif member.isreg():
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("archive exceeds the size limit")
                if target.is_dir():
                    raise ValueError(f"archive file collides with a directory: {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError(f"unreadable archive member: {member.name!r}")
                with target.open("wb") as stream:
                    stream.write(source.read())
                    if durable:
                        stream.flush()
                        os.fsync(stream.fileno())
                target.chmod(member.mode & 0o777)
                written.append(target)
            else:
                raise ValueError(f"archive must contain only regular files and directories: {member.name!r}")
    if durable:
        for directory in sorted({path if path.is_dir() else path.parent for path in written} | {dest}):
            _fsync(directory)


LOCAL_COMMANDS = frozenset(
    {
        ("init",),
        ("check",),
        ("task", "init"),
        ("tasks", "init"),
        ("task", "schema"),
        ("task", "update"),
        ("task", "migrate"),
    }
)
INFO_ARGS = frozenset({"--version", "-v", "--help", "-h", "version"})
SUBMISSIONS = {("trial", "start"): "trial", ("run",): "job"}
VALUE_FLAGS = {
    "-p": "path",
    "--path": "path",
    "-a": "agent",
    "--agent": "agent",
    "--trial-name": "trial_name",
    "--trials-dir": "trials_dir",
    "--job-name": "job_name",
    "-o": "jobs_dir",
    "--jobs-dir": "jobs_dir",
    "-m": "model",
    "--model": "model",
    "-c": "config",
    "--config": "config",
}
FIELDS = {
    "trial": frozenset({"path", "agent", "trial_name", "trials_dir", "model", "config"}),
    "job": frozenset({"path", "agent", "job_name", "jobs_dir", "model", "config"}),
}
HOST_ESCAPE_FLAGS = frozenset(
    {
        "--mounts",
        "--mounts-json",
        "--extra-docker-compose",
        "--allow-environment-host",
        "--allow-agent-host",
        "--ae",
        "--agent-env",
        "--ak",
        "--agent-kwarg",
    }
)


class ShimError(Exception):
    """A command the shim refuses; reported on stderr with exit code 2."""


def installed_harbor_version() -> str | None:
    try:
        return version("harbor")
    except PackageNotFoundError:
        return None


def exec_real_harbor(args: list[str]) -> int:
    os.execv(REAL_HARBOR, [REAL_HARBOR, *args])
    return 127


def _is_passthrough(args: list[str]) -> bool:
    if not args or args[0] in INFO_ARGS or "--help" in args or "-h" in args:
        return True
    return tuple(args[:1]) in LOCAL_COMMANDS or tuple(args[:2]) in LOCAL_COMMANDS


def parse_submission(args: list[str], kind: str) -> dict[str, str]:
    """Parse `harbor trial start` / `harbor run` options, rejecting every flag outside the allowlist."""
    command = "trial start" if kind == "trial" else "run"
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-"):
            raise ShimError(f"unexpected argument {arg!r} for harbor {command}")
        flag, has_inline, inline = arg.partition("=")
        if flag in HOST_ESCAPE_FLAGS:
            raise ShimError(f"{flag} is a host-escape flag; the Harbor sidecar does not accept it")
        field = VALUE_FLAGS.get(flag)
        if field is None:
            allowed = ", ".join(sorted(flag for flag, name in VALUE_FLAGS.items() if name in FIELDS[kind]))
            raise ShimError(f"unsupported flag {flag} for harbor {command}; the shim accepts only {allowed}")
        if field not in FIELDS[kind]:
            raise ShimError(f"{flag} does not apply to harbor {command}")
        if has_inline:
            value = inline
        else:
            index += 1
            value = args[index] if index < len(args) else ""
        if not value or value.startswith("-"):
            raise ShimError(f"{flag} requires a value")
        if field in values:
            raise ShimError(f"{flag} may be given only once")
        values[field] = value
        index += 1
    for field, flag in (("path", "-p/--path"), ("agent", "-a/--agent")):
        if field not in values:
            raise ShimError(f"harbor {command} through the sidecar requires {flag}")
    return values


def _load_config(path: str) -> dict[str, Any]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
        if source.suffix == ".json":
            data = json.loads(text)
        else:
            import yaml

            data = yaml.safe_load(text)
    except (OSError, ValueError) as exc:
        raise ShimError(f"cannot read --config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ShimError(f"--config {path} must contain a mapping")
    return data


class SidecarClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def request(
        self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            self.url + path,
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except (ValueError, AttributeError):
                pass
            raise ShimError(f"the Harbor sidecar rejected {method} {path} ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ShimError(f"cannot reach the Harbor sidecar at {self.url}: {exc}") from exc

    def json(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        status, payload = self.request(method, path, **kwargs)
        return status, json.loads(payload)


def submit(options: dict[str, str], kind: str) -> int:
    """Send one trial or job to the sidecar, wait for it, and unpack its artifacts into the client's directory."""
    url, token = os.environ.get(URL_ENV), os.environ.get(TOKEN_ENV)
    if not url or not token:
        raise ShimError(f"{URL_ENV} and {TOKEN_ENV} must be set; the Harbor sidecar runs trials for this container")
    task_dir = Path(os.path.abspath(options["path"]))
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise ShimError(f"task path must be an ordinary directory: {task_dir}")
    if kind == "trial":
        out_root = Path(os.path.abspath(options.get("trials_dir", "trials")))
        suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(7))
        run_name = options.get("trial_name") or f"{task_dir.name[:32]}__{suffix}"
    else:
        out_root = Path(os.path.abspath(options.get("jobs_dir", "jobs")))
        run_name = options.get("job_name") or datetime.now().strftime("%Y-%m-%d__%H-%M-%S")
    if not NAME_RE.fullmatch(run_name) or len(run_name) > 100:
        raise ShimError(f"run name must match {NAME_RE.pattern} and have at most 100 characters: {run_name!r}")
    target = out_root / run_name
    name = f"{run_name}-{sha256(str(target).encode()).hexdigest()[:12]}"
    metadata: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "agent": options["agent"],
        "client_task_path": str(task_dir),
        "client_trials_dir": str(out_root),
    }
    if "model" in options:
        metadata["model"] = options["model"]
    if "config" in options:
        metadata["config"] = _load_config(options["config"])
    try:
        archive = pack_tree(task_dir, task_dir.name)
    except (OSError, ValueError) as exc:
        raise ShimError(str(exc)) from exc

    client = SidecarClient(url, token)
    status, _accepted = client.json(
        "POST",
        "/jobs",
        body=archive,
        headers={"Content-Type": "application/x-tar", METADATA_HEADER: json.dumps(metadata)},
    )
    while True:
        _, state = client.json("GET", f"/jobs/{name}")
        if state.get("status") in ("completed", "failed"):
            break
        time.sleep(POLL_SECONDS)
    _, artifacts = client.request("GET", f"/jobs/{name}/artifacts")
    if not (status == 200 and (target / "result.json").is_file()):
        try:
            extract_tar(artifacts, target, durable=True)
        except (OSError, ValueError) as exc:
            raise ShimError(f"cannot unpack sidecar artifacts into {target}: {exc}") from exc
    exit_code = state.get("exit_code")
    print(f"harbor shim: {kind} {run_name} {state['status']} (exit {exit_code}) in {target}", file=sys.stderr)
    if state["status"] == "completed" and isinstance(exit_code, int):
        return exit_code
    if state.get("error"):
        print(f"harbor shim: sidecar error: {state['error']}", file=sys.stderr)
    return exit_code if isinstance(exit_code, int) and exit_code != 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        found = installed_harbor_version()
        if found != HARBOR_VERSION:
            raise ShimError(f"tested with Harbor {HARBOR_VERSION}, but the installed Harbor is {found or 'missing'}")
        if _is_passthrough(args):
            return exec_real_harbor(args)
        for prefix, kind in SUBMISSIONS.items():
            if tuple(args[: len(prefix)]) == prefix:
                return submit(parse_submission(args[len(prefix) :], kind), kind)
        command = " ".join(args[:2])
        raise ShimError(
            f"`harbor {command}` needs Docker or a Harbor server, which this container does not have. "
            "Use `harbor trial start` or `harbor run`; the shim sends those to the host Harbor sidecar."
        )
    except ShimError as exc:
        print(f"harbor shim: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
