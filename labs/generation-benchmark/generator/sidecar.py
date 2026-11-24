# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Host Harbor sidecar: runs the trials the generator container submits through its harbor shim.

One sidecar serves one generator run. It binds a private address, authenticates every
request with a per-run bearer token, validates each task bundle, and runs one Harbor
job at a time with Harbor 0.24.0.
"""

from __future__ import annotations

import hmac
import json
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from generation_benchmark.digests import pythonpath_env
from generation_benchmark.models import GenerationAgent
from generation_benchmark.validation import check_bundle, task_checksum
from generation_controls import SCRIPT_CONTROL_IMPORT_PATH
from harbor.models.job.config import JobConfig
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from harbor.utils.env import get_required_host_vars, resolve_env_vars
from metric_benchmark.io import digest_bytes, json_bytes

from generator.harbor_shim import HARBOR_VERSION, MAX_ARCHIVE_BYTES, METADATA_HEADER, NAME_RE, extract_tar, pack_tree

BUILTIN_AGENTS = ("oracle", "nop")
JOB_FIELDS = frozenset({"name", "kind", "agent", "model", "config", "client_task_path", "client_trials_dir"})
REQUIRED_JOB_FIELDS = frozenset({"name", "agent", "client_task_path", "client_trials_dir"})
RESULT_KEYS = ("id", "task_checksum", "exception_info", "verifier_result")
FINISHED = frozenset({"completed", "failed"})
# Shorter values (e.g. "1", "true") would corrupt unrelated text when redacted.
MIN_REDACTED_LENGTH = 8
UNAUTHORIZED_DRAIN_BYTES = 1024 * 1024
PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SSL_CERT_FILE",
    "XDG_RUNTIME_DIR",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)

JobKind = Literal["trial", "job"]


class JobRejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class HarborRun:
    """One Harbor execution: the trial (or job) directory is `output_dir / name`."""

    name: str
    kind: JobKind
    task_dir: Path
    output_dir: Path
    agent_config: AgentConfig
    env: dict[str, str]


HarborRunner = Callable[[HarborRun], int]


def installed_harbor_version() -> str:
    return version("harbor")


def build_trial_config(run: HarborRun) -> TrialConfig:
    return TrialConfig(
        task=TaskConfig(path=run.task_dir),
        trial_name=run.name,
        trials_dir=run.output_dir,
        agent=run.agent_config,
    )


def build_job_config(run: HarborRun) -> JobConfig:
    return JobConfig(
        job_name=run.name,
        jobs_dir=run.output_dir,
        tasks=[TaskConfig(path=run.task_dir)],
        agents=[run.agent_config],
        n_attempts=1,
        n_concurrent_trials=1,
    )


class HarborCliRunner:
    """Run Harbor's own CLI in a child process with only the agent's environment."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def __call__(self, run: HarborRun) -> int:
        harbor = Path(sys.executable).with_name("harbor")
        workdir = run.output_dir.parent
        config_path = workdir / f"{run.kind}-config.json"
        if run.kind == "trial":
            config_json = build_trial_config(run).model_dump_json()
            argv = [str(harbor), "trial", "start", "--config", str(config_path)]
        else:
            config_json = build_job_config(run).model_dump_json()
            argv = [str(harbor), "run", "--config", str(config_path)]
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(config_json)
        with (workdir / "harbor.log").open("wb") as log:
            self._process = subprocess.Popen(
                argv,
                cwd=workdir,
                env=run.env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
            try:
                return self._process.wait()
            finally:
                self._process = None

    def cancel(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()


def default_bind_host(system: str | None = None, docker: str = "docker") -> str:
    """Return 127.0.0.1 on macOS, otherwise the Docker bridge gateway address."""
    if (system or platform.system()) == "Darwin":
        return "127.0.0.1"
    result = subprocess.run(
        [docker, "network", "inspect", "bridge", "--format", "{{range .IPAM.Config}}{{.Gateway}} {{end}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    gateways = [value for value in result.stdout.split() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value)]
    if result.returncode != 0 or not gateways:
        raise RuntimeError(f"cannot find the Docker bridge gateway: {result.stderr.strip() or result.stdout.strip()}")
    return gateways[0]


def bundle_digest(root: Path) -> str:
    """Digest every name, permission, and byte under *root*, including dotfiles."""
    entries: list[list[Any]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode & 0o777
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f"bundle must contain only ordinary files and directories: {rel}")
        if path.is_dir():
            entries.append([rel, "dir", mode])
        else:
            entries.append([rel, "file", mode, sha256(path.read_bytes()).hexdigest()])
    return digest_bytes(json_bytes(entries))


def scrub_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep only the trial identity, health, and reward fields task-create and measurement read."""
    agent = (result.get("config") or {}).get("agent") or {}
    scrubbed = {key: result.get(key) for key in RESULT_KEYS}
    scrubbed["config"] = {"agent": {"name": agent.get("name"), "import_path": agent.get("import_path")}}
    return scrubbed


def _strip_agent_settings(value: Any, *, under_agent: bool = False) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if under_agent and key in ("env", "kwargs"):
                continue
            out[key] = _strip_agent_settings(item, under_agent=key in ("agent", "agents", "user_agent"))
        return out
    if isinstance(value, list):
        return [_strip_agent_settings(item, under_agent=under_agent) for item in value]
    return value


def scrub_artifacts(
    root: Path,
    secret_values: Iterable[str],
    *,
    path_replacements: Iterable[tuple[str, str]] = (),
) -> None:
    """Remove agent environment values and kwargs from a Harbor trial or job directory in place."""
    secrets_by_length = sorted({v for v in secret_values if len(v) >= MIN_REDACTED_LENGTH}, key=len, reverse=True)
    replacements = sorted(
        ((src, dst) for src, dst in path_replacements if src),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if path.suffix == ".json":
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                if path.name == "result.json" and "task_checksum" in payload:
                    payload = scrub_result(payload)
                else:
                    payload = _strip_agent_settings(payload)
                text = json.dumps(payload, indent=4) + "\n"
        for src, dst in replacements:
            text = text.replace(src, dst)
        for secret in secrets_by_length:
            text = text.replace(secret, "[REDACTED]")
        if text.encode("utf-8") != data:
            path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class JobSpec:
    name: str
    kind: JobKind
    agent: str
    model: str | None
    config: dict[str, Any] | None
    client_task_path: str
    client_trials_dir: str
    task_dirname: str

    def params(self) -> str:
        return json.dumps(
            [self.kind, self.agent, self.model, self.config, self.task_dirname], sort_keys=True, separators=(",", ":")
        )


@dataclass
class JobRecord:
    spec: JobSpec
    bundle_sha256: str
    task_checksum: str
    workdir: Path
    task_dir: Path
    agent_config: AgentConfig
    status: str = "queued"
    exit_code: int | None = None
    error: str | None = None
    artifacts: Path | None = None
    finished: threading.Event = field(default_factory=threading.Event)

    @property
    def output_dir(self) -> Path:
        return self.workdir / "out"

    def public(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "status": self.status,
            "exit_code": self.exit_code,
            "error": self.error,
            "bundle_sha256": self.bundle_sha256,
            "task_checksum": self.task_checksum,
        }


def parse_job_metadata(metadata: object) -> JobSpec:
    if not isinstance(metadata, dict):
        raise JobRejected(400, "job metadata must be a JSON object")
    unknown = sorted(set(metadata) - JOB_FIELDS)
    if unknown:
        raise JobRejected(
            400,
            f"unsupported job field(s): {', '.join(unknown)}; the sidecar takes no host path, shell command, "
            "mount, environment, or agent kwargs",
        )
    missing = sorted(REQUIRED_JOB_FIELDS - set(metadata))
    if missing:
        raise JobRejected(400, f"missing job field(s): {', '.join(missing)}")
    for key in ("name", "agent", "client_task_path", "client_trials_dir"):
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise JobRejected(400, f"job field {key} must be nonempty text")
    name = metadata["name"]
    if not NAME_RE.fullmatch(name):
        raise JobRejected(400, f"job name must match {NAME_RE.pattern}")
    kind = metadata.get("kind", "trial")
    if kind not in ("trial", "job"):
        raise JobRejected(400, "job kind must be trial or job")
    model = metadata.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise JobRejected(400, "job model must be nonempty text")
    config = metadata.get("config")
    if config is not None and not isinstance(config, dict):
        raise JobRejected(400, "job config must be a JSON object")
    task_dirname = PurePosixPath(metadata["client_task_path"]).name
    if task_dirname in ("", ".", "..") or "\\" in task_dirname or "\0" in task_dirname:
        raise JobRejected(400, "client_task_path must end in a task directory name")
    return JobSpec(
        name=name,
        kind=kind,
        agent=metadata["agent"],
        model=model,
        config=config or None,
        client_task_path=metadata["client_task_path"],
        client_trials_dir=metadata["client_trials_dir"],
        task_dirname=task_dirname,
    )


class HarborSidecar:
    """Serve `POST /jobs`, `GET /jobs/{name}`, and `GET /jobs/{name}/artifacts` for one generator run."""

    def __init__(
        self,
        agent: GenerationAgent,
        agent_config: AgentConfig,
        *,
        host: str | None = None,
        port: int = 0,
        harbor_runner: HarborRunner | None = None,
        work_root: Path | None = None,
        extra_pythonpath: list[Path] | None = None,
    ) -> None:
        installed = installed_harbor_version()
        if installed != HARBOR_VERSION:
            raise RuntimeError(f"Harbor sidecar requires Harbor {HARBOR_VERSION}; installed Harbor is {installed}")
        self.agent = agent
        self.agent_config = agent_config
        self.host = host or default_bind_host()
        self.token = secrets.token_urlsafe(32)
        self._requested_port = port
        self._runner: HarborRunner = harbor_runner or HarborCliRunner()
        self._work_root_arg = work_root
        self._work_root: Path | None = None
        self._extra_pythonpath = list(extra_pythonpath or [])
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[JobRecord | None] = queue.Queue()
        self._revoked = False
        self._server: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("sidecar is not running")
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def container_url(self) -> str:
        """URL the generator container uses; Docker Desktop reaches host loopback via host.docker.internal."""
        host = "host.docker.internal" if self.host == "127.0.0.1" else self.host
        return f"http://{host}:{self.port}"

    def start(self) -> HarborSidecar:
        if self._server is not None or self._revoked:
            raise RuntimeError("a sidecar starts once; a restart fails the run")
        self._work_root = Path(tempfile.mkdtemp(prefix="harbor-sidecar-", dir=self._work_root_arg))
        self._server = ThreadingHTTPServer((self.host, self._requested_port), _handler_for(self))
        self._server.daemon_threads = True
        for target, name in ((self._server.serve_forever, "sidecar-http"), (self._work, "sidecar-worker")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def revoke(self) -> None:
        self._revoked = True

    def stop(self, timeout: float | None = None) -> None:
        """Revoke the token, stop serving, cancel a running Harbor child, and remove job files."""
        self.revoke()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._queue.put(None)
        cancel = getattr(self._runner, "cancel", None)
        if callable(cancel):
            cancel()
        for thread in self._threads:
            thread.join(timeout)
        if self._work_root is not None and not any(thread.is_alive() for thread in self._threads):
            shutil.rmtree(self._work_root, ignore_errors=True)

    def __enter__(self) -> HarborSidecar:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def authorized(self, header: str | None) -> bool:
        if self._revoked or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header.removeprefix("Bearer ").encode(), self.token.encode())

    def job(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._jobs.get(name)
            return None if record is None else record.public()

    def _check_agent(self, spec: JobSpec) -> None:
        own = self.agent.agent_bundle_id
        if spec.agent == own:
            expected_model = self.agent.harbor.get("model_name")
            if spec.model is not None and spec.model != expected_model:
                raise JobRejected(400, f"model {spec.model!r} differs from {own}'s model in agent.yml")
            if spec.config is not None:
                self._check_agent_config(spec.config)
            return
        if spec.agent in (*BUILTIN_AGENTS, SCRIPT_CONTROL_IMPORT_PATH):
            if spec.model is not None or spec.config is not None:
                raise JobRejected(400, f"agent {spec.agent} takes no model or config")
            return
        allowed = ", ".join([own, *BUILTIN_AGENTS, SCRIPT_CONTROL_IMPORT_PATH])
        raise JobRejected(400, f"agent {spec.agent!r} is not allowed; use one of {allowed}")

    def _check_agent_config(self, config: dict[str, Any]) -> None:
        own = self.agent.agent_bundle_id
        extra = sorted(set(config) - {"agent"})
        if extra:
            raise JobRejected(400, f"config may set only agent; unsupported key(s): {', '.join(extra)}")
        agent_config = config.get("agent") or {}
        if not isinstance(agent_config, dict):
            raise JobRejected(400, "config.agent must be a mapping")
        for key, value in agent_config.items():
            if key not in self.agent.harbor:
                raise JobRejected(400, f"config.agent.{key} is not in {own}'s agent.yml entry")
            if value != self.agent.harbor[key]:
                raise JobRejected(400, f"config.agent.{key} differs from {own}'s agent.yml entry")

    def _agent_config_for(self, spec: JobSpec, task_dir: Path) -> AgentConfig:
        if spec.agent == self.agent.agent_bundle_id:
            return self.agent_config.model_copy(deep=True)
        if spec.agent == SCRIPT_CONTROL_IMPORT_PATH:
            return AgentConfig(import_path=SCRIPT_CONTROL_IMPORT_PATH, kwargs={"task_dir": str(task_dir)})
        return AgentConfig(name=spec.agent)

    def submit(self, metadata: object, archive: bytes) -> tuple[int, dict[str, Any]]:
        """Validate and queue one job; return the HTTP status and job state."""
        spec = parse_job_metadata(metadata)
        self._check_agent(spec)
        if self._work_root is None:
            raise JobRejected(503, "sidecar is not running")
        workdir = Path(tempfile.mkdtemp(prefix="job-", dir=self._work_root))
        queued = False
        try:
            bundle_root = workdir / "bundle"
            try:
                extract_tar(archive, bundle_root, top=spec.task_dirname)
            except (OSError, ValueError, EOFError) as exc:
                raise JobRejected(400, f"invalid task bundle: {exc}") from exc
            task_dir = bundle_root / spec.task_dirname
            if not task_dir.is_dir():
                raise JobRejected(400, f"task bundle has no {spec.task_dirname}/ directory")
            digest = bundle_digest(task_dir)
            with self._lock:
                existing = self._jobs.get(spec.name)
                if existing is not None:
                    if existing.bundle_sha256 == digest and existing.spec.params() == spec.params():
                        return 200, existing.public()
                    raise JobRejected(409, f"job {spec.name} already exists with a different bundle or parameters")
                try:
                    check_bundle(task_dir, session_token=self.token)
                except ValueError as exc:
                    raise JobRejected(400, str(exc)) from exc
                try:
                    checksum = task_checksum(HarborTask(task_dir))
                except Exception as exc:
                    raise JobRejected(400, f"Harbor cannot load the task: {exc}") from exc
                record = JobRecord(
                    spec=spec,
                    bundle_sha256=digest,
                    task_checksum=checksum,
                    workdir=workdir,
                    task_dir=task_dir,
                    agent_config=self._agent_config_for(spec, task_dir),
                )
                record.output_dir.mkdir()
                self._jobs[spec.name] = record
                self._queue.put(record)
                queued = True
                return 202, record.public()
        finally:
            if not queued:
                shutil.rmtree(workdir, ignore_errors=True)

    def _runner_env(self, agent_config: AgentConfig) -> dict[str, str]:
        env = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
        for name, _default in get_required_host_vars(agent_config.env):
            if name in os.environ:
                env[name] = os.environ[name]
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return pythonpath_env(extra_dirs=self._extra_pythonpath, base_env=env)

    def _secret_values(self, agent_config: AgentConfig) -> list[str]:
        values = list(agent_config.env.values())
        try:
            values.extend(resolve_env_vars(agent_config.env).values())
        except ValueError:
            pass
        return values

    def _work(self) -> None:
        while True:
            record = self._queue.get()
            if record is None:
                return
            with self._lock:
                record.status = "running"
            exit_code: int | None = None
            error: str | None = None
            run = HarborRun(
                name=record.spec.name,
                kind=record.spec.kind,
                task_dir=record.task_dir,
                output_dir=record.output_dir,
                agent_config=record.agent_config,
                env=self._runner_env(record.agent_config),
            )
            try:
                exit_code = self._runner(run)
            except Exception as exc:
                error = f"Harbor runner failed: {type(exc).__name__}: {exc}"
            run_dir = record.output_dir / record.spec.name
            artifacts = record.workdir / "artifacts.tar"
            try:
                if run_dir.is_dir():
                    scrub_artifacts(
                        run_dir,
                        self._secret_values(record.agent_config),
                        path_replacements=(
                            (str(record.task_dir), record.spec.client_task_path),
                            (str(record.task_dir.resolve()), record.spec.client_task_path),
                            (str(record.output_dir), record.spec.client_trials_dir),
                            (str(record.output_dir.resolve()), record.spec.client_trials_dir),
                            (str(record.workdir), "/tmp/sidecar-job"),
                            (str(record.workdir.resolve()), "/tmp/sidecar-job"),
                        ),
                    )
                    artifacts.write_bytes(pack_tree(run_dir))
                else:
                    artifacts.write_bytes(pack_tree(Path(tempfile.mkdtemp(dir=record.workdir))))
            except (OSError, ValueError) as exc:
                error = error or f"cannot package artifacts: {exc}"
            if error is None and exit_code == 0 and not (run_dir / "result.json").is_file():
                error = "Harbor wrote no result.json"
            if error is None and exit_code != 0:
                log = record.workdir / "harbor.log"
                tail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.is_file() else ""
                error = f"Harbor exited {exit_code}" + (f": {tail.strip()}" if tail.strip() else "")
            with self._lock:
                record.exit_code = exit_code
                record.error = error
                record.artifacts = artifacts if artifacts.is_file() else None
                record.status = "completed" if error is None else "failed"
            record.finished.set()

    def artifacts(self, name: str) -> bytes:
        with self._lock:
            record = self._jobs.get(name)
            if record is None:
                raise JobRejected(404, f"no job named {name}")
            if record.status not in FINISHED:
                raise JobRejected(409, f"job {name} is {record.status}")
            path = record.artifacts
        if path is None:
            raise JobRejected(404, f"job {name} has no artifacts")
        return path.read_bytes()


def _handler_for(sidecar: HarborSidecar) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "harbor-sidecar"

        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _body(self, limit: int) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise JobRejected(400, "invalid Content-Length") from exc
            if length < 0 or length > limit:
                raise JobRejected(413, "request body exceeds the size limit")
            return self.rfile.read(length) if length else b""

        def _handle(self, method: str) -> None:
            try:
                if not sidecar.authorized(self.headers.get("Authorization")):
                    # Drain a small body so closing the socket does not reset the client before it reads 401.
                    try:
                        self._body(UNAUTHORIZED_DRAIN_BYTES)
                    except JobRejected:
                        pass
                    raise JobRejected(401, "missing or invalid sidecar token")
                body = self._body(MAX_ARCHIVE_BYTES)
                path = urlsplit(self.path).path
                match = re.fullmatch(r"/jobs/([^/]+)(/artifacts)?", path)
                if method == "POST" and path == "/jobs":
                    try:
                        metadata = json.loads(self.headers.get(METADATA_HEADER) or "")
                    except ValueError as exc:
                        raise JobRejected(400, f"{METADATA_HEADER} must be JSON job metadata") from exc
                    status, payload = sidecar.submit(metadata, body)
                    self._json(status, payload)
                elif method == "GET" and match and match.group(2):
                    self._send(200, sidecar.artifacts(match.group(1)), "application/x-tar")
                elif method == "GET" and match:
                    state = sidecar.job(match.group(1))
                    if state is None:
                        raise JobRejected(404, f"no job named {match.group(1)}")
                    self._json(200, state)
                else:
                    raise JobRejected(404, f"no route for {method} {path}")
            except JobRejected as exc:
                self._json(exc.status, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

    return Handler
