# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Subprocess command execution, DirectoryModel output checks, stage 0-1 validation, and task bundle rules."""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig
from harbor.models.trial.result import TrialResult
from metric_benchmark.io import read_json

from generation_benchmark.digests import directory_manifest, pythonpath_env
from generation_benchmark.models import (
    GenerationAgent,
    GenerationExperiment,
    LoadedCase,
    OutputSpec,
    Score,
    Stage1ExecutionRow,
    Stage1Results,
    resolve_evidence,
)

_VALIDATE_OUTPUT_SCRIPT = """
import importlib
import inspect
import json
import sys
from pathlib import Path

from generation_benchmark.models import DirectoryModel, ScoreOutput

output_type = sys.argv[1]
directory = Path(sys.argv[2])
require_score = sys.argv[3] == "1"

module_name, class_name = output_type.split(":", 1)
module = importlib.import_module(module_name)
cls = getattr(module, class_name)
if not (isinstance(cls, type) and issubclass(cls, DirectoryModel)):
    raise TypeError(f"{output_type} must be a DirectoryModel subclass")
if require_score and not issubclass(cls, ScoreOutput):
    raise TypeError(f"{output_type} must be a ScoreOutput subclass")

instance = cls.load(directory)
if not isinstance(instance, cls):
    raise TypeError(f"{output_type}.load did not return an instance of {class_name}")

payload = {
    "module_file": str(Path(inspect.getfile(cls)).resolve()),
    "score": instance.score.model_dump(mode="python", warnings="error") if issubclass(cls, ScoreOutput) else None,
}
print(json.dumps(payload))
"""

_RESOLVE_OUTPUT_SCRIPT = """
import importlib
import inspect
import json
import sys
from pathlib import Path

from generation_benchmark.models import DirectoryModel, ScoreOutput

output_type = sys.argv[1]
require_score = sys.argv[2] == "1"

module_name, class_name = output_type.split(":", 1)
module = importlib.import_module(module_name)
cls = getattr(module, class_name)
if not (isinstance(cls, type) and issubclass(cls, DirectoryModel)):
    raise TypeError(f"{output_type} must be a DirectoryModel subclass")
if require_score and not issubclass(cls, ScoreOutput):
    raise TypeError(f"{output_type} must be a ScoreOutput subclass")

print(json.dumps({"module_file": str(Path(inspect.getfile(cls)).resolve())}))
"""


@dataclass(frozen=True)
class CommandOutcome:
    succeeded: bool
    returncode: int | None
    timed_out: bool
    workspace_modified: bool
    stdout: bytes
    stderr: bytes
    error: str | None


def _terminate_process_group(process: subprocess.Popen[bytes], grace_seconds: float) -> tuple[bytes, bytes]:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    try:
        return process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        return process.communicate()


def run_command_subprocess(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
    extra_pythonpath: list[Path] | None = None,
    kill_grace_seconds: float = 60.0,
) -> tuple[int | None, bool, bytes, bytes]:
    """Run *command* in a process group, adding benchmark packages to PYTHONPATH."""
    run_env = pythonpath_env(extra_dirs=extra_pythonpath, base_env=env)
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=run_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return process.returncode, False, stdout, stderr
        except subprocess.TimeoutExpired:
            stdout, stderr = _terminate_process_group(process, kill_grace_seconds)
            return None, True, stdout, stderr
        except BaseException:
            _terminate_process_group(process, min(kill_grace_seconds, 5.0))
            raise


def invoke_workspace_command(
    command: list[str],
    *,
    invocation_dir: Path,
    timeout_seconds: int,
    request_name: str = "request.json",
    workspace_dir: Path | None = None,
    output_arg: str = "output",
    extra_pythonpath: list[Path] | None = None,
    kill_grace_seconds: float = 60.0,
) -> CommandOutcome:
    """Invoke setup/score/generation command and verify workspace immutability even on failure."""
    before_ws: dict[str, str] | None = None
    if workspace_dir is not None:
        before_ws = directory_manifest(workspace_dir)

    argv = [*command, "--request", request_name]
    if workspace_dir is not None:
        argv.extend(["--workspace", workspace_dir.name])
    argv.extend(["--output-dir", output_arg])

    returncode: int | None = None
    timed_out = False
    stdout = b""
    stderr = b""
    workspace_modified = False
    error: str | None = None

    try:
        returncode, timed_out, stdout, stderr = run_command_subprocess(
            argv,
            cwd=invocation_dir,
            timeout_seconds=timeout_seconds,
            extra_pythonpath=extra_pythonpath,
            kill_grace_seconds=kill_grace_seconds,
        )
    except OSError as exc:
        error = f"failed to launch command {command[0]}: {exc}"
    finally:
        if before_ws is not None and workspace_dir is not None:
            try:
                after_ws = directory_manifest(workspace_dir)
                if after_ws != before_ws:
                    workspace_modified = True
            except (OSError, ValueError):
                workspace_modified = True

    if workspace_modified:
        return CommandOutcome(
            succeeded=False,
            returncode=returncode,
            timed_out=timed_out,
            workspace_modified=True,
            stdout=stdout,
            stderr=stderr,
            error="workspace_modified",
        )
    if error is not None:
        return CommandOutcome(
            succeeded=False,
            returncode=None,
            timed_out=False,
            workspace_modified=False,
            stdout=stdout,
            stderr=stderr,
            error=error,
        )
    if timed_out:
        return CommandOutcome(
            succeeded=False,
            returncode=None,
            timed_out=True,
            workspace_modified=False,
            stdout=stdout,
            stderr=stderr,
            error=f"command timed out after {timeout_seconds}s",
        )
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or stdout.decode("utf-8", errors="replace").strip()
        return CommandOutcome(
            succeeded=False,
            returncode=returncode,
            timed_out=False,
            workspace_modified=False,
            stdout=stdout,
            stderr=stderr,
            error=detail or f"command exited {returncode}",
        )
    return CommandOutcome(
        succeeded=True,
        returncode=0,
        timed_out=False,
        workspace_modified=False,
        stdout=stdout,
        stderr=stderr,
        error=None,
    )


def _format_context(case_id: str, subset_id: str | None, path: Path, message: str) -> str:
    parts = [f"case {case_id}"] if case_id else []
    if subset_id is not None:
        parts.append(f"subset {subset_id}")
    parts.append(f"path {path}")
    return f"{', '.join(parts)}: {message}"


def resolve_output_type_module(
    output_spec: OutputSpec,
    *,
    require_score_output: bool = False,
    timeout_seconds: int = 60,
    extra_pythonpath: list[Path] | None = None,
) -> Path:
    """Verify *output_spec* resolves in its declared Python and return its module file path."""
    cwd = extra_pythonpath[0] if extra_pythonpath else Path.cwd()
    try:
        returncode, timed_out, stdout, stderr = run_command_subprocess(
            [output_spec.python, "-c", _RESOLVE_OUTPUT_SCRIPT, output_spec.type, "1" if require_score_output else "0"],
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            extra_pythonpath=extra_pythonpath,
        )
    except OSError as exc:
        raise ValueError(f"cannot run output python {output_spec.python} for {output_spec.type}: {exc}") from exc
    if timed_out:
        raise ValueError(f"resolving output type {output_spec.type} timed out after {timeout_seconds}s")
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or f"exited {returncode}"
        raise ValueError(f"invalid output type {output_spec.type}: {detail}")
    payload = json.loads(stdout.decode("utf-8"))
    module_file = Path(payload["module_file"])
    if not module_file.is_file():
        raise ValueError(f"output type {output_spec.type} module file does not exist: {module_file}")
    return module_file


def validate_directory_output(
    output_spec: OutputSpec | None,
    directory: Path,
    *,
    timeout_seconds: int = 120,
    require_score_output: bool = False,
    case_id: str,
    subset_id: str | None = None,
    extra_pythonpath: list[Path] | None = None,
    kill_grace_seconds: float = 60.0,
) -> dict[str, Any]:
    """Load *directory* using *output_spec* (or ScoreOutput) in a subprocess with timeout."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(_format_context(case_id, subset_id, directory, "output directory is missing or not a dir"))
    python_exe = output_spec.python if output_spec is not None else sys.executable
    output_type = output_spec.type if output_spec is not None else "generation_benchmark.models:ScoreOutput"
    try:
        returncode, timed_out, stdout, stderr = run_command_subprocess(
            [
                python_exe,
                "-c",
                _VALIDATE_OUTPUT_SCRIPT,
                output_type,
                str(directory),
                "1" if require_score_output else "0",
            ],
            cwd=directory,
            timeout_seconds=timeout_seconds,
            extra_pythonpath=extra_pythonpath,
            kill_grace_seconds=kill_grace_seconds,
        )
    except OSError as exc:
        raise ValueError(_format_context(case_id, subset_id, directory, f"cannot run {python_exe}: {exc}")) from exc

    if timed_out:
        raise ValueError(
            _format_context(case_id, subset_id, directory, f"output load timed out after {timeout_seconds}s")
        )
    if returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip() or f"validator exited {returncode}"
        raise ValueError(_format_context(case_id, subset_id, directory, detail))

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(_format_context(case_id, subset_id, directory, f"invalid validator output: {exc}")) from exc
    if not isinstance(payload, dict):
        raise ValueError(_format_context(case_id, subset_id, directory, "validator returned non-object payload"))
    if payload.get("score") is not None:
        payload["score"] = Score.model_validate(payload["score"])
    return payload


def validate_stage0_setup(
    experiment: GenerationExperiment,
    case_id: str,
    metric_setup_dir: Path,
    *,
    extra_pythonpath: list[Path] | None = None,
) -> dict[str, Any] | None:
    """Validate metric-setup/ when metric.setup is declared; skip when absent."""
    if experiment.metric.setup is None:
        return None
    return validate_directory_output(
        experiment.metric.setup.output,
        metric_setup_dir,
        timeout_seconds=experiment.metric.setup.timeout_seconds,
        require_score_output=False,
        case_id=case_id,
        extra_pythonpath=extra_pythonpath,
    )


def resolve_harbor_agent_config(
    agent: GenerationAgent,
    cases_root: Path,
    *,
    recorded_root: Path | None = None,
) -> AgentConfig:
    """Resolve `{input:NAME}` references in `agent.harbor` for Harbor host execution."""

    def resolve(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("{input:") and value.endswith("}"):
            name = value[7:-1]
            if name not in agent.inputs:
                raise ValueError(f"unknown agent input: {name}")
            source = agent.inputs[name]
            local = source.resolve_path(cases_root)
            return str(recorded_root / source.path) if recorded_root is not None else str(local)
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    values = resolve(agent.harbor)
    if set(values) - AgentConfig.model_fields.keys():
        raise ValueError("unknown Harbor agent configuration field")
    if bool(values.get("name")) == bool(values.get("import_path")):
        raise ValueError("agent must select exactly one Harbor name or import_path")
    return AgentConfig.model_validate(values)


def task_checksum(task: HarborTask) -> str:
    with warnings.catch_warnings(action="ignore", category=DeprecationWarning):
        return task.checksum


_COMPOSE_FILE_RE = re.compile(r"^(?:docker-compose.*|compose\.ya?ml)$", re.IGNORECASE)


def _contains_template(value: object) -> bool:
    if isinstance(value, str):
        return "${" in value
    if isinstance(value, dict):
        return any(_contains_template(k) or _contains_template(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_template(item) for item in value)
    return False


def _check_task_toml(path: Path) -> None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"bundle task.toml does not parse: {exc}") from exc
    environment = data.get("environment") or {}
    agent = data.get("agent") or {}
    if not isinstance(environment, dict) or not isinstance(agent, dict):
        raise ValueError("bundle task.toml [environment] and [agent] must be tables")
    for label, value in (
        ("[environment].env", environment.get("env")),
        ("[verifier]", data.get("verifier")),
        ("[solution]", data.get("solution")),
    ):
        if _contains_template(value):
            raise ValueError(f"bundle task.toml must not contain a ${{...}} template in {label}")
    # `harbor task init` writes `mcp_servers = []`; only entries are rejected.
    for label, value in (
        ("[environment].mcp_servers", environment.get("mcp_servers")),
        ("[agent].mcp_servers", agent.get("mcp_servers")),
        ("mcp_servers", data.get("mcp_servers")),
    ):
        if value:
            raise ValueError(f"bundle task.toml must not declare {label} entries")
    if environment.get("docker_image") not in (None, ""):
        raise ValueError("bundle task.toml must not set a prebuilt [environment].docker_image")


def check_bundle(path: Path, *, session_token: str | None = None) -> None:
    """Reject a task bundle that breaks a sandbox rule; never modify it.

    Rules: only ordinary files and directories, no docker-compose file, a parseable
    `task.toml` without `${...}` templates in `[environment].env`, `[verifier]`, or
    `[solution]`, no `mcp_servers` entries, no prebuilt `docker_image`, and no
    occurrence of *session_token* in any file name or file content.
    """
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"bundle must be an ordinary directory: {path}")
    token = session_token.encode("utf-8") if session_token else None
    for current, dirnames, filenames in os.walk(path, followlinks=False):
        for name in [*dirnames, *filenames]:
            entry = Path(current) / name
            rel = entry.relative_to(path).as_posix()
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"bundle must not contain symlinks: {rel}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"bundle must contain only ordinary files and directories: {rel}")
            if token is not None and token in name.encode("utf-8"):
                raise ValueError(f"bundle must not contain the session token: {rel}")
            if not stat.S_ISREG(mode):
                continue
            if _COMPOSE_FILE_RE.match(name):
                raise ValueError(f"bundle must not contain a docker-compose file: {rel}")
            if token is not None and token in entry.read_bytes():
                raise ValueError(f"bundle must not contain the session token: {rel}")
    task_toml = path / "task.toml"
    if task_toml.is_symlink() or not task_toml.is_file():
        raise ValueError("bundle must contain task.toml")
    _check_task_toml(task_toml)


def _recorded_input_root(task_path: Path | None, relative: str) -> Path:
    parts = Path(relative).parts
    if (
        task_path is None
        or not task_path.is_absolute()
        or ".." in task_path.parts
        or task_path.parts[-len(parts) :] != parts
    ):
        raise ValueError(f"native task path does not match prepared source {relative}")
    return task_path.parents[len(parts) - 1]


def _extract_rewards(trial: TrialResult) -> tuple[str, dict[str, Score]]:
    if trial.finished_at is None:
        raise ValueError("native trial has not finished")
    if trial.exception_info is not None:
        return "error", {
            "reward": Score(
                value=None,
                missing_score_reason=f"execution error: {trial.exception_info.exception_type}",
            )
        }
    if trial.step_results:
        raise ValueError("multi-step Harbor observations need an explicit aggregation policy")
    rewards = trial.verifier_result.rewards if trial.verifier_result else None
    if not rewards:
        return "succeeded", {"reward": Score(value=None, missing_score_reason="native verifier metric absent: reward")}
    parsed: dict[str, Score] = {}
    for metric_name, val in rewards.items():
        if val is None:
            parsed[metric_name] = Score(
                value=None,
                missing_score_reason=f"native verifier metric absent: {metric_name}",
            )
        else:
            parsed[metric_name] = Score(value=float(val))
    return "succeeded", parsed


def _check_no_extra_agents(exec_root: Path, case_id: str) -> None:
    agents_dir = exec_root / "agents"
    if not agents_dir.is_dir():
        raise ValueError(f"case {case_id}, path {agents_dir}: missing agents/ directory")
    unexpected = sorted(entry.name for entry in agents_dir.iterdir() if entry.name != "agent")
    if unexpected:
        raise ValueError(
            f"case {case_id}, path {agents_dir}: unexpected Harbor agent directories in stage 1: {', '.join(unexpected)}"
        )


def collect_stage1_job(
    loaded_case: LoadedCase,
    exec_root: Path,
    *,
    expected_task_paths: dict[str, Path] | None = None,
) -> list[Stage1ExecutionRow]:
    """Read and verify the finished stage 1 Harbor job under *exec_root*/agents/agent/run-0001."""
    case_id = loaded_case.definition.case_id
    _check_no_extra_agents(exec_root, case_id)
    run_dir = exec_root / "agents" / "agent" / "run-0001"
    try:
        job_result = read_json(resolve_evidence(run_dir, "result.json"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"case {case_id}, path {run_dir}: {exc}") from exc
    if not isinstance(job_result, dict) or not job_result.get("finished_at"):
        raise ValueError(f"case {case_id}, path {run_dir}: Harbor job has not finished")

    tasks_by_id = {task.task_id: task for task in loaded_case.tasks.task_inventory.tasks}
    task_rel_by_id = {entry.task_id: entry.path for entry in loaded_case.tasks.tasks}
    resolved_paths = expected_task_paths or {tid: path.resolve() for tid, path in loaded_case.task_paths.items()}
    path_to_task_id = {str(path.resolve()): tid for tid, path in resolved_paths.items()}
    harbor_tasks = {tid: HarborTask(path) for tid, path in resolved_paths.items()}
    checksums = {tid: task_checksum(t) for tid, t in harbor_tasks.items()}

    rows: list[Stage1ExecutionRow] = []
    seen_tasks: set[str] = set()
    configs: dict[Path, AgentConfig] = {}

    for result_path in sorted(run_dir.glob("*/result.json")):
        trial_dir = result_path.parent
        rel_trial = trial_dir.relative_to(exec_root).as_posix()
        try:
            native = TrialResult.model_validate(
                read_json(resolve_evidence(run_dir, result_path.relative_to(run_dir).as_posix()))
            )
            native_task_path = native.config.task.path
            task_id = path_to_task_id.get(str(native_task_path)) if native_task_path is not None else None
            if task_id is None and native_task_path is not None:
                # Match by longest relative suffix if relocated.
                for tid, rel in sorted(task_rel_by_id.items(), key=lambda item: len(Path(item[1]).parts), reverse=True):
                    parts = Path(rel).parts
                    if native_task_path.is_absolute() and native_task_path.parts[-len(parts) :] == parts:
                        task_id = tid
                        break
            if task_id is None:
                raise ValueError(f"unexpected task path {native_task_path}")
            if task_id in seen_tasks:
                raise ValueError(f"duplicate trial for task {task_id}")
            seen_tasks.add(task_id)

            prepared_task = harbor_tasks[task_id]
            if native.task_name != prepared_task.name or native.task_checksum != checksums[task_id]:
                raise ValueError(f"task {task_id}: native task identity or checksum differs from prepared input")
            if native.trial_name != trial_dir.name:
                raise ValueError(f"task {task_id}: native trial name differs from directory {trial_dir.name}")

            producer_root = _recorded_input_root(native_task_path, task_rel_by_id[task_id])
            if producer_root not in configs:
                configs[producer_root] = resolve_harbor_agent_config(
                    loaded_case.agent, loaded_case.cases_root, recorded_root=producer_root
                )
            if native.config.agent != configs[producer_root]:
                raise ValueError(f"task {task_id}: native agent configuration differs from benchmark")

            status, rewards = _extract_rewards(native)
            if status != "succeeded":
                raise ValueError(f"task {task_id}: baseline trial status is {status!r}, expected 'succeeded'")
            try:
                traj_path = resolve_evidence(trial_dir, "agent/trajectory.json")
            except FileNotFoundError:
                raise ValueError(f"task {task_id}: missing agent/trajectory.json") from None
            if not traj_path.is_file():
                raise ValueError(f"task {task_id}: agent/trajectory.json is not a regular file")

            rows.append(
                Stage1ExecutionRow(
                    agent_run_id="agent/run-0001",
                    agent_bundle_id=loaded_case.agent.agent_bundle_id,
                    task=tasks_by_id[task_id],
                    status=status,
                    rewards=rewards,
                    native_results_path=rel_trial,
                    trajectory_path=(Path(rel_trial) / "agent/trajectory.json").as_posix(),
                )
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"case {case_id}, path {trial_dir}: {exc}") from exc

    missing = sorted(set(tasks_by_id) - seen_tasks)
    if missing:
        raise ValueError(f"case {case_id}, path {run_dir}: missing baseline trials for task(s): {', '.join(missing)}")

    rows.sort(key=lambda r: r.task.key())
    return rows


def validate_stage1_execution(exec_root: Path, loaded_case: LoadedCase) -> Stage1Results:
    """Validate stage 1 completion: succeeded rows with agent/trajectory.json and no nop job."""
    case_id = loaded_case.definition.case_id
    _check_no_extra_agents(exec_root, case_id)
    try:
        raw_results = Stage1Results.model_validate(read_json(resolve_evidence(exec_root, "results.json")))
    except (OSError, ValueError) as exc:
        raise ValueError(f"case {case_id}, path {exec_root / 'results.json'}: {exc}") from exc

    if raw_results.case_id != case_id:
        raise ValueError(
            f"case {case_id}, path {exec_root / 'results.json'}: results case_id {raw_results.case_id!r} differs"
        )
    if raw_results.task_inventory != loaded_case.tasks.task_inventory.tasks:
        raise ValueError(f"case {case_id}, path {exec_root / 'results.json'}: results task_inventory differs from case")

    collected = collect_stage1_job(loaded_case, exec_root)
    if raw_results.execution_results != collected:
        raise ValueError(
            f"case {case_id}, path {exec_root / 'results.json'}: results rows differ from Harbor job trials"
        )
    return raw_results
