# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Invoke a supplied generation system with a fixed file contract."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from metric_benchmark.config import file_manifest
from metric_benchmark.io import digest_file, json_bytes, read_json, write_json
from metric_benchmark.models import Digest, Model, Score, Text
from metric_benchmark.scoring import identity

from generation_benchmark.config import GenerationConfig

INPUT_SUITE_DIR = Path("input") / "suite-Bi"
OUTPUT_SUITE_DIR = Path("output") / "suite-Bi-plus"


class GenerationResponse(Model):
    eval_subset_id: Text
    new_task_ids: list[Text]
    expanded_suite_digest: Digest
    summary: dict[str, object] = {}


class GenerationReceipt(Model):
    request_sha256: Digest
    configuration_sha256: Digest
    files: dict[Text, Digest]
    response: GenerationResponse


FORBIDDEN_REQUEST_KEYS = frozenset(
    {"baseline_full_score", "full_benchmark_metric", "m_b", "baseline_full_metric"}
)


def _archive_generation_failure(
    workspace: Path,
    work_root: Path,
    stdout: bytes | None,
    stderr: bytes | None,
    message: str,
) -> None:
    if not workspace.is_dir():
        raise ValueError(message)
    (workspace / "stdout.log").write_bytes(stdout or b"")
    (workspace / "stderr.log").write_bytes(stderr or b"")
    failed = work_root.parent / ("failed-" + workspace.name.removeprefix(".pending-"))
    shutil.move(str(workspace), str(failed))
    raise ValueError(f"{message}; evidence: {failed}")


def _strip_transient_generation_output(workspace: Path) -> None:
    """Remove eval-author work cells; they may contain symlinks invalid for file_manifest."""
    output_dir = workspace / "output"
    if not output_dir.is_dir():
        return
    for child in output_dir.iterdir():
        if child.is_dir() and child.name.startswith(".") and "generation-work" in child.name:
            shutil.rmtree(child)


ALLOWED_REQUEST_KEYS = frozenset(
    {"schema_version", "case_description", "eval_subset_id", "tasks", "settings", "inputs", "coverage"}
)
REQUEST_INPUT_PATHS = ("suite", "coverage_work", "output_suite")


def _relative_request_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"generation request {label} must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"generation request {label} must be a relative path")
    return value


def validate_generation_request(request: dict) -> None:
    if request.get("schema_version") != 1:
        raise ValueError("generation request schema_version must be 1")
    extra = set(request) - ALLOWED_REQUEST_KEYS
    if extra:
        raise ValueError(
            "generation request has unexpected keys: " + ", ".join(sorted(extra))
        )
    settings = request.get("settings")
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("generation request settings must be an object")
    if isinstance(settings, dict):
        for token in FORBIDDEN_REQUEST_KEYS:
            if token in settings:
                raise ValueError(f"generation request must not include {token}")
    blob = json.dumps(request)
    for token in FORBIDDEN_REQUEST_KEYS:
        if token in blob:
            raise ValueError(f"generation request must not include {token}")
    inputs = request.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("generation request inputs must be an object")
    for key in REQUEST_INPUT_PATHS:
        _relative_request_path(inputs.get(key), f"inputs.{key}")
    coverage = request.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("generation request coverage must be a score")
    Score.model_validate(coverage)


def invoke_generation(
    configuration: GenerationConfig,
    case_description: dict,
    eval_subset_id: str,
    subset_tasks: list[dict],
    suite_source: Path,
    coverage_work: Path,
    work_root: Path,
    *,
    coverage: Score,
    cached_only: bool = False,
) -> tuple[Path, GenerationReceipt]:
    executable = Path(configuration.command[0])
    if not executable.is_absolute():
        raise ValueError("generation command must start with an absolute executable path")
    work_root = work_root.resolve()
    work_root.parent.mkdir(parents=True, exist_ok=True)
    if work_root.is_symlink():
        raise ValueError("generation work root must not be a symlink")
    receipt_path = work_root / "receipt.json"
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=work_root.parent) as temporary:
        workspace = Path(temporary)
        request = {
            "schema_version": 1,
            "case_description": case_description,
            "eval_subset_id": eval_subset_id,
            "tasks": subset_tasks,
            "coverage": coverage.model_dump(),
            "inputs": {
                "suite": INPUT_SUITE_DIR.as_posix(),
                "coverage_work": "input/coverage",
                "output_suite": OUTPUT_SUITE_DIR.as_posix(),
            },
            "settings": configuration.settings,
        }
        validate_generation_request(request)
        input_suite = workspace / request["inputs"]["suite"]
        input_coverage = workspace / request["inputs"]["coverage_work"]
        shutil.copytree(suite_source, input_suite)
        shutil.copytree(coverage_work, input_coverage)
        write_json(workspace / "request.json", request)
        request_digest = digest_file(workspace / "request.json")
        config_digest = identity(configuration)
        if receipt_path.exists():
            receipt = GenerationReceipt.model_validate(read_json(receipt_path))
            if receipt.request_sha256 != request_digest or receipt.configuration_sha256 != config_digest:
                raise ValueError("completed generation request differs; use a new work directory")
            actual = file_manifest(work_root)
            actual.pop("receipt.json", None)
            if actual != receipt.files:
                raise ValueError("completed generation evidence changed")
            return work_root / request["inputs"]["output_suite"], receipt
        if cached_only:
            raise ValueError("completed generation request is missing")
        inputs = file_manifest(workspace)
        output_response = workspace / "response.json"
        env = os.environ.copy()
        labs_root = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = os.pathsep.join(
            [
                p
                for p in (
                    env.get("PYTHONPATH", ""),
                    str(labs_root / "metric-benchmark"),
                    str(labs_root / "generation-benchmark"),
                )
                if p
            ]
        )
        with subprocess.Popen(
            [*configuration.command, "--request", "request.json", "--output", "response.json"],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            env=env,
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=configuration.timeout_seconds)
            except BaseException:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                stdout, stderr = process.communicate()
                _archive_generation_failure(
                    workspace,
                    work_root,
                    stdout,
                    stderr,
                    "generation system interrupted or timed out",
                )
        if process.returncode:
            _archive_generation_failure(
                workspace,
                work_root,
                stdout,
                stderr,
                f"generation system exited {process.returncode}",
            )
        try:
            response = GenerationResponse.model_validate(read_json(output_response))
            _strip_transient_generation_output(workspace)
            for name, digest in inputs.items():
                if file_manifest(workspace).get(name) != digest:
                    raise ValueError("generation system modified its supplied inputs")
            output_suite = request["inputs"]["output_suite"]
            suite_plus = workspace / output_suite
            if not suite_plus.is_dir():
                raise ValueError(f"generation adapter did not write {output_suite}")
            from metric_benchmark.io import digest_bytes

            expanded_digest = digest_bytes(json_bytes(file_manifest(suite_plus)))
            if response.expanded_suite_digest != expanded_digest:
                raise ValueError("generation response expanded_suite_digest does not match output tree")
            files = file_manifest(workspace)
            receipt = GenerationReceipt(
                request_sha256=request_digest,
                configuration_sha256=config_digest,
                files=files,
                response=response,
            )
            write_json(workspace / "receipt.json", receipt)
            if work_root.exists():
                shutil.rmtree(work_root)
            workspace.rename(work_root)
            return work_root / output_suite, receipt
        except BaseException as exc:
            if isinstance(exc, ValueError) and "evidence:" in str(exc):
                raise
            _archive_generation_failure(
                workspace,
                work_root,
                stdout,
                stderr,
                str(exc),
            )
