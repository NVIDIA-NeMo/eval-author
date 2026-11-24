# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reference tool_calls setup and score commands and DirectoryModel outputs."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from metric_benchmark.io import digest_file, json_bytes, read_json

from generation_benchmark.models import DirectoryModel, Score, ScoreOutput, ScoreRequest, SetupRequest

SETUP_STATE_SCHEMA = "nemo.eval_author.reference_metric_setup_state.v1"
REPORT_SCHEMA = "nemo.eval_author.audit_coverage_report.v1"
SETUP_CHANGED_ERROR = "metric-setup changed; use a new work root"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _audit_spec_dir() -> Path:
    return _repo_root() / "skills" / "eval-author-audit" / "scripts" / "audit_spec"


def _load_audit_spec(audit_path: Path) -> dict[str, Any]:
    audit_dir = str(_audit_spec_dir())
    if audit_dir not in sys.path:
        sys.path.insert(0, audit_dir)
    schema_mod = importlib.import_module("_schema")
    return schema_mod.load_audit_spec(audit_path)


class ToolCallsSetupOutput(DirectoryModel):
    """Setup directory D for reference tool_calls measurement."""

    ethos_path: str
    audit_path: str
    agent: str
    tool_names: list[str]

    @classmethod
    def load(cls, directory: Path) -> ToolCallsSetupOutput:
        ethos_file = directory / "ETHOS.md"
        if ethos_file.is_symlink() or not ethos_file.is_file():
            raise ValueError(f"missing ETHOS.md in {directory}")
        audit_file = directory / ".eval-author" / "audit.md"
        if audit_file.is_symlink() or not audit_file.is_file():
            raise ValueError(f"missing .eval-author/audit.md in {directory}")
        spec = _load_audit_spec(audit_file)
        sources = spec.get("sources")
        if not isinstance(sources, list) or not sources or sources[0].get("path") != "../ETHOS.md":
            raise ValueError(f"audit sources[0].path must be '../ETHOS.md' in {audit_file}")
        items = spec.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"audit has no items in {audit_file}")
        non_tools = [item.get("name") for item in items if item.get("kind") != "tool"]
        if non_tools:
            raise ValueError(f"audit must contain only kind: tool items; found {non_tools} in {audit_file}")
        return cls(
            ethos_path=str(ethos_file),
            audit_path=str(audit_file),
            agent=str(spec["agent"]),
            tool_names=[str(item["name"]) for item in items],
        )


class ToolCallsScoreOutput(ScoreOutput):
    """Score directory output for reference tool_calls measurement."""

    coverage_report: dict[str, Any]

    @classmethod
    def load(cls, directory: Path) -> ToolCallsScoreOutput:
        base = ScoreOutput.load(directory)
        report_path = directory / "audit-coverage-report.json"
        if report_path.is_symlink() or not report_path.is_file():
            raise ValueError(f"missing audit-coverage-report.json in {directory}")
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid audit-coverage-report.json in {directory}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != REPORT_SCHEMA:
            raise ValueError(f"unexpected audit-coverage-report.json schema in {directory}")
        return cls(score=base.score, coverage_report=payload)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _resolve_items_path(settings: dict[str, Any]) -> Path:
    raw = settings.get("items")
    if isinstance(raw, dict):
        raw = raw.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("metric setup request missing settings.items path")
    items_path = Path(raw)
    if items_path.is_symlink() or not items_path.is_file():
        raise ValueError(f"missing setup items file: {items_path}")
    return items_path


def _run_audit_script(script_name: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    script_path = _audit_spec_dir() / script_name
    proc = subprocess.run(
        [sys.executable, "-B", str(script_path), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exited {proc.returncode}"
        raise RuntimeError(f"{script_name} failed: {detail}")
    return proc


def run_setup(*, request_path: Path, output_dir: Path, argv: list[str]) -> ToolCallsSetupOutput:
    """Render D into *output_dir* or reuse it only if receipt and bytes are unchanged."""
    request = SetupRequest.model_validate(read_json(request_path))
    ethos_src = Path(request.ethos_path)
    if ethos_src.is_symlink() or not ethos_src.is_file():
        raise ValueError(f"missing setup ETHOS file: {ethos_src}")
    items_src = _resolve_items_path(request.settings)

    ethos_src_sha = digest_file(ethos_src)
    items_sha = digest_file(items_src)

    output_dir = output_dir.resolve()
    ethos_dest = output_dir / "ETHOS.md"
    audit_out = output_dir / ".eval-author" / "audit.md"
    state_path = output_dir / ".eval-author" / "setup-state.json"

    already_rendered = any(path.exists() or path.is_symlink() for path in (ethos_dest, audit_out, state_path))
    if already_rendered:
        if state_path.is_symlink() or not state_path.is_file():
            raise ValueError(SETUP_CHANGED_ERROR)
        try:
            state = read_json(state_path)
        except (OSError, ValueError) as exc:
            raise ValueError(SETUP_CHANGED_ERROR) from exc
        if not isinstance(state, dict) or state.get("schema") != SETUP_STATE_SCHEMA:
            raise ValueError(SETUP_CHANGED_ERROR)
        if ethos_dest.is_symlink() or not ethos_dest.is_file() or audit_out.is_symlink() or not audit_out.is_file():
            raise ValueError(SETUP_CHANGED_ERROR)
        matches = (
            state.get("audit_sha256") == digest_file(audit_out)
            and state.get("ethos_dest_sha256") == digest_file(ethos_dest)
            and state.get("ethos_src_sha256") == ethos_src_sha
            and state.get("items_sha256") == items_sha
            and state.get("argv") == argv
        )
        if not matches:
            raise ValueError(SETUP_CHANGED_ERROR)
        return ToolCallsSetupOutput.load(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    ethos_dest.write_bytes(ethos_src.read_bytes())
    _run_audit_script(
        "generate.py",
        [
            "--ethos",
            "ETHOS.md",
            "--items",
            str(items_src.resolve()),
            "--out",
            ".eval-author/audit.md",
            "--mode",
            "replace",
            "--items-mode",
            "full",
        ],
        cwd=output_dir,
    )
    loaded_output = ToolCallsSetupOutput.load(output_dir)
    state_payload = {
        "schema": SETUP_STATE_SCHEMA,
        "audit_sha256": digest_file(audit_out),
        "ethos_dest_sha256": digest_file(ethos_dest),
        "ethos_src_sha256": ethos_src_sha,
        "items_sha256": items_sha,
        "argv": argv,
    }
    _atomic_write_bytes(state_path, json_bytes(state_payload))
    return loaded_output


def _resolve_trial_dir(raw_trial_dir: str, *, request_dir: Path, workspace_dir: Path | None) -> Path:
    candidate_raw = Path(raw_trial_dir)
    if candidate_raw.is_absolute():
        candidate = candidate_raw.resolve()
    else:
        candidate = (request_dir / candidate_raw).resolve()
        if not candidate.exists() and workspace_dir is not None:
            alt = (workspace_dir / candidate_raw).resolve()
            if alt.exists():
                candidate = alt
    if workspace_dir is not None and not candidate.is_relative_to(workspace_dir.resolve()):
        raise ValueError(f"trial directory escapes workspace: {raw_trial_dir}")
    if not candidate.is_dir():
        raise ValueError(f"missing trial directory: {candidate}")
    return candidate


def run_score(*, request_path: Path, workspace_dir: Path | None, output_dir: Path) -> ToolCallsScoreOutput:
    """Measure workspace trials against metric-setup/ in place and write score + report."""
    request = ScoreRequest.model_validate(read_json(request_path))
    method = request.settings.get("method", "tool_calls")
    if method != "tool_calls":
        raise ValueError(f"unsupported reference metric method: {method!r}")
    if request.metric_setup_dir is None:
        raise ValueError("metric_setup_dir is required for reference tool_calls scoring")

    setup_dir = Path(request.metric_setup_dir).resolve()
    setup_output = ToolCallsSetupOutput.load(setup_dir)
    audit_path = Path(setup_output.audit_path).resolve()

    request_dir = request_path.resolve().parent
    resolved_workspace = workspace_dir.resolve() if workspace_dir is not None else None
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    measurements_dir = output_dir / "measurements"
    if measurements_dir.exists():
        shutil.rmtree(measurements_dir)
    measurements_dir.mkdir(parents=True, exist_ok=True)

    measured_count = 0
    for trial in request.trials:
        trial_dir = _resolve_trial_dir(trial.trial_path, request_dir=request_dir, workspace_dir=resolved_workspace)
        trace_path = trial_dir / "agent" / "trajectory.json"
        if not trace_path.is_file():
            continue
        _run_audit_script(
            "measure.py",
            [
                "--audit",
                str(audit_path),
                "--trial-dir",
                str(trial_dir),
                "--task-id",
                trial.task_id,
                "--run-id",
                f"{request.case_id}:{request.scope}:{trial.task_id}",
                "--evidence-class",
                "agent_under_test",
                "--measure",
                "tool_calls",
                "--out-dir",
                str(measurements_dir),
            ],
            cwd=output_dir,
        )
        measured_count += 1

    if measured_count == 0:
        score = Score(value=None, missing_score_reason="no_measured_trials")
        _atomic_write_bytes(output_dir / "score.json", json_bytes(score.model_dump(mode="json")))
        raise ValueError("no measured trials with agent/trajectory.json found")

    report_path = output_dir / "audit-coverage-report.json"
    _run_audit_script(
        "report.py",
        [
            "--audit",
            str(audit_path),
            "--coverage-dir",
            str(measurements_dir),
            "--out",
            str(report_path),
        ],
        cwd=output_dir,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    overall = report["coverage"]["overall"]
    item_count = int(overall["item_count"])
    covered_count = int(overall["covered_count"])
    score = (
        Score(value=covered_count / item_count, missing_score_reason=None)
        if item_count > 0
        else Score(value=None, missing_score_reason="empty_audit")
    )
    _atomic_write_bytes(output_dir / "score.json", json_bytes(score.model_dump(mode="json")))
    return ToolCallsScoreOutput.load(output_dir)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="reference_metric.py",
        description="Reference tool_calls setup and scoring command for generation-benchmark.",
    )
    parser.add_argument("--setup", action="store_true", help="Render metric-setup/ in place instead of scoring")
    parser.add_argument("--request", type=Path, required=True, help="Path to request.json")
    parser.add_argument("--workspace", type=Path, help="Read-only workspace path for scoring")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    args = parser.parse_args(argv)
    effective_argv = list(sys.argv[1:] if argv is None else argv)

    try:
        if args.setup:
            run_setup(request_path=args.request, output_dir=args.output_dir, argv=effective_argv)
        else:
            run_score(request_path=args.request, workspace_dir=args.workspace, output_dir=args.output_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
