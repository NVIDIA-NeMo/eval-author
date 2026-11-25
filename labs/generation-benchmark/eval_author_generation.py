# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reference `generation.command`: check the G image, start the Harbor sidecar, and run the G container.

See the Launcher section of docs/reference-generator-implementation.md.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from generation_benchmark.models import (
    GenerationAgent,
    GenerationRequest,
    GenerationResponse,
    ScoreRequest,
    read_json,
    write_model,
)
from generation_benchmark.reference_generator import (
    INTERNAL_NETWORK_ERROR,
    ImageInfo,
    ReferenceSettings,
    check_prompt_template,
    docker_image_inspect,
    image_matches,
    image_mismatch_message,
    load_operator_answers,
)
from generator.build import generator_source_digest
from generator.harbor_shim import TOKEN_ENV, URL_ENV
from generator.sidecar import HarborSidecar
from harbor.models.trial.config import AgentConfig

GAP_REPORT = "audit-coverage-report.json"
STOP_SECONDS = 20
EXIT_FAILED = 2
EXIT_TERMINATED = 128 + signal.SIGTERM
_NAME_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class Sidecar(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def container_url(self) -> str: ...

    token: str

    def stop(self, timeout: float | None = None) -> None: ...


StartSidecar = Callable[[GenerationAgent, AgentConfig, list[Path]], Sidecar]


def start_harbor_sidecar(
    agent: GenerationAgent,
    agent_config: AgentConfig,
    extra_pythonpath: list[Path],
    *,
    port: int = 0,
) -> Sidecar:
    return HarborSidecar(agent, agent_config, port=port, extra_pythonpath=extra_pythonpath).start()


@dataclass
class DockerCli:
    """The Docker commands the launcher runs; tests replace it."""

    executable: str = "docker"

    def inspect(self, image_id: str) -> ImageInfo | None:
        return docker_image_inspect(image_id, docker=self.executable)

    def start(self, args: list[str], env: Mapping[str, str]) -> subprocess.Popen[bytes]:
        # Its own session, so the orchestrator's process-group SIGTERM reaches only the launcher.
        return subprocess.Popen(
            [self.executable, *args],
            env=dict(env),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    def stop(self, name: str) -> None:
        subprocess.run(
            [self.executable, "stop", "-t", str(STOP_SECONDS), name],
            capture_output=True,
            check=False,
            timeout=STOP_SECONDS + 10,
        )

    def remove(self, name: str) -> None:
        subprocess.run([self.executable, "rm", "-f", name], capture_output=True, check=False, timeout=60)


class _Terminated(BaseException):
    pass


def _on_sigterm(_signum: int, _frame: object) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise _Terminated


class LaunchError(Exception):
    def __init__(self, error: str, detail: str | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.detail = detail or error


def _name_part(value: str) -> str:
    return _NAME_PART_RE.sub("-", value).strip("-.") or "x"


def container_name(request_path: Path, request: GenerationRequest) -> str:
    """`eval-gen-<case dir>-<subset dir>-<12 hex of the request digest>`; ids when run outside a work root."""
    pending = request_path.resolve().parent
    subset = re.sub(r"\.pending-\d+$", "", pending.name)
    case = pending.parent.parent.name
    if not (re.fullmatch(r"case-\d+", case) and re.fullmatch(r"subset-\d+", subset)):
        case, subset = _name_part(request.case_id), _name_part(request.eval_subset_id)
    digest = hashlib.sha256(request_path.read_bytes()).hexdigest()[:12]
    return f"eval-gen-{case}-{subset}-{digest}"


def _has_input_ref(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("{input:") and value.endswith("}")
    if isinstance(value, dict):
        return any(_has_input_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_input_ref(item) for item in value)
    return False


def resolve_agent_config(request: GenerationRequest, trials_dir: Path) -> AgentConfig:
    """A's Harbor config: the recorded `config.agent` when `agent.harbor` names `{input:...}` files."""
    harbor = request.agent.harbor
    if not _has_input_ref(harbor):
        if set(harbor) - AgentConfig.model_fields.keys():
            raise ValueError("unknown Harbor agent configuration field")
        return AgentConfig.model_validate(harbor)
    trials = sorted(path for path in trials_dir.iterdir() if (path / "result.json").is_file())
    if not trials:
        raise ValueError(f"no Bᵢ trial result.json under {trials_dir} to resolve agent inputs")
    result = read_json(trials[0] / "result.json")
    config = result.get("config") if isinstance(result, dict) else None
    recorded = config.get("agent") if isinstance(config, dict) else None
    if not isinstance(recorded, dict):
        raise ValueError(f"{trials[0] / 'result.json'} has no config.agent")
    return AgentConfig.model_validate(recorded)


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    readonly: bool = True

    def arg(self) -> str:
        if "," in str(self.source):
            raise LaunchError("invalid_mount", f"bind mount source must not contain ',': {self.source}")
        return f"type=bind,source={self.source},target={self.target}" + (",readonly" if self.readonly else "")


def host_mounts(
    request_path: Path,
    workspace: Path,
    output_dir: Path,
    request: GenerationRequest,
    settings: ReferenceSettings,
) -> list[Mount]:
    """The only host paths the container sees."""
    metric_output = Path(request.metric_output_dir)
    subset_metric_dir = metric_output.parent
    score_request = ScoreRequest.model_validate(read_json(subset_metric_dir / "request.json"))
    if score_request.metric_setup_dir is None:
        raise ValueError(f"{subset_metric_dir / 'request.json'} names no metric_setup_dir")
    setup_dir = subset_metric_dir / score_request.metric_setup_dir
    mounts = [
        Mount(workspace, "/input"),
        Mount(output_dir, "/output", readonly=False),
        Mount(request_path, "/request.json"),
        Mount(subset_metric_dir / "workspace" / "trials", "/mounts/trials"),
        Mount(setup_dir / ".eval-author" / "audit.md", "/mounts/audit.md"),
        Mount(metric_output / GAP_REPORT, "/mounts/gap-report.json"),
        Mount(Path(settings.prompt.path), "/mounts/prompt.md"),
        Mount(Path(settings.operator_answers.path), "/mounts/operator-answers.yaml"),
    ]
    resolved = []
    for mount in mounts:
        if mount.source.is_symlink() or not mount.source.exists():
            raise ValueError(f"mount source for {mount.target} is missing or a symlink: {mount.source}")
        resolved.append(Mount(mount.source.resolve(), mount.target, mount.readonly))
    return resolved


def docker_run_args(
    name: str,
    image_id: str,
    entrypoint: list[str],
    mounts: list[Mount],
    env_names: list[str],
) -> list[str]:
    args = ["run", "--name", name, "--init", "--add-host=host.docker.internal:host-gateway"]
    for mount in mounts:
        args += ["--mount", mount.arg()]
    for env_name in env_names:
        args += ["-e", env_name]
    return [*args, "--entrypoint", entrypoint[0], image_id, *entrypoint[1:]]


def _host_pythonpath() -> list[Path]:
    return [Path(entry) for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]


def _fail(output_dir: Path, error: str, detail: str | None = None, code: int = EXIT_FAILED) -> int:
    print(f"eval_author_generation: {detail or error}", file=sys.stderr)
    if not (output_dir / "response.json").exists():
        write_model(output_dir / "response.json", GenerationResponse(error=error))
    return code


def run(
    request_path: Path,
    workspace: Path,
    output_dir: Path,
    *,
    docker: DockerCli | None = None,
    start_sidecar: StartSidecar = start_harbor_sidecar,
) -> int:
    """Run one generation; `docker rm -f` and `sidecar.stop()` run on every exit path."""
    previous = signal.signal(signal.SIGTERM, _on_sigterm)
    docker = docker or DockerCli()
    try:
        try:
            request = GenerationRequest.model_validate(read_json(request_path))
            settings = ReferenceSettings.model_validate(request.settings)
            load_operator_answers(Path(settings.operator_answers.path))
            check_prompt_template(Path(settings.prompt.path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _fail(output_dir, f"invalid_settings: {exc}")
        if settings.network == "internal":
            return _fail(output_dir, INTERNAL_NETWORK_ERROR)
        try:
            mounts = host_mounts(request_path, workspace, output_dir, request, settings)
            name = container_name(request_path, request)
        except LaunchError as exc:
            return _fail(output_dir, exc.error, exc.detail)
        except (OSError, ValueError, KeyError) as exc:
            return _fail(output_dir, f"invalid_inputs: {exc}")
        return _launch(request, settings, mounts, name, output_dir, docker, start_sidecar)
    except _Terminated:
        return EXIT_TERMINATED
    finally:
        signal.signal(signal.SIGTERM, previous)


def _launch(
    request: GenerationRequest,
    settings: ReferenceSettings,
    mounts: list[Mount],
    name: str,
    output_dir: Path,
    docker: DockerCli,
    start_sidecar: StartSidecar,
) -> int:
    sidecar: Sidecar | None = None
    container: subprocess.Popen[bytes] | None = None
    try:
        try:
            if not image_matches(settings.image_id, generator_source_digest(), docker.inspect):
                return _fail(output_dir, "image_mismatch", image_mismatch_message(settings.image_id))
            trials = next(mount.source for mount in mounts if mount.target == "/mounts/trials")
            try:
                agent_config = resolve_agent_config(request, trials)
            except (OSError, ValueError, KeyError) as exc:
                return _fail(output_dir, "adapter_failed", f"cannot resolve A's Harbor config: {exc}")
            agent = GenerationAgent(
                agent_bundle_id=request.agent.agent_bundle_id,
                revision=request.agent.revision,
                harbor=request.agent.harbor,
            )
            try:
                sidecar = start_sidecar(agent, agent_config, _host_pythonpath())
            except (OSError, RuntimeError) as exc:
                return _fail(output_dir, "adapter_failed", f"Harbor sidecar failed to start: {exc}")
            print(f"eval_author_generation: sidecar listening on {sidecar.url}", file=sys.stderr, flush=True)
            env_names = [env_name for env_name in settings.provider_env if env_name in os.environ]
            env = {**os.environ, URL_ENV: sidecar.container_url, TOKEN_ENV: sidecar.token}
            args = docker_run_args(
                name, settings.image_id, settings.entrypoint, mounts, [URL_ENV, TOKEN_ENV, *env_names]
            )
            docker.remove(name)
            container = docker.start(args, env)
            code = container.wait()
            if code != 0:
                return _fail(output_dir, f"container exited {code}", code=code)
            return code
        except _Terminated:
            if container is not None:
                docker.stop(name)
                try:
                    container.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    container.kill()
            _fail(output_dir, "terminated", "SIGTERM: stopped the container")
            return EXIT_TERMINATED
    finally:
        if sidecar is not None:
            sidecar.stop(timeout=10)
        docker.remove(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.request, args.workspace, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
