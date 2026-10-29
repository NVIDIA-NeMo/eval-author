# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate and prove expanded physical suites with eval-author-task-create."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from generation_benchmark.eval_author.ports.errors import ExperimentError
from generation_benchmark.eval_author.ports.io import write_json

from .generator import GeneratorConfig, load_generator_config, run_generator
from .harbor import AgentConfig, load_agent_config
from .identity import file_digest, tree_digest
from .suite_tasks import discover_suite


def generate_suite(
    suite: Path,
    *,
    measurement_path: Path,
    tasks: int,
    output: Path,
    eval_author_root: Path,
    agent_path: Path,
    generator_path: Path,
    seed: int,
    draft_dir: Path,
) -> dict[str, Any]:
    """Run task-create for a deterministic bounded set of actionable gaps."""
    if tasks < 0:
        raise ExperimentError("tasks must be non-negative")
    resolved_suite = suite.expanduser().resolve()
    discover_suite(resolved_suite)
    resolved_output = output.expanduser().resolve()
    if resolved_output == resolved_suite or resolved_suite in resolved_output.parents:
        raise ExperimentError("expanded suite output must be outside the immutable input suite")
    if resolved_output in resolved_suite.parents:
        raise ExperimentError("input suite must not be nested inside expanded suite output")
    task_skill, audit_skill = _skill_paths(eval_author_root)
    agent = load_agent_config(agent_path)
    assert agent is not None
    generator = load_generator_config(generator_path)
    measurement, coverage_report, audit = _load_measurement(
        measurement_path, suite=resolved_suite, agent=agent
    )
    identity = {
        "worker": file_digest(Path(__file__)),
        "draft_dir": draft_dir.as_posix(),
        "suite": tree_digest(resolved_suite),
        "measurement": file_digest(_measurement_file(measurement_path)),
        "measurement_identity": measurement["identity"],
        "agent": agent.digest,
        "generator": generator.digest,
        "task_create_skill": tree_digest(task_skill),
        "audit_skill": tree_digest(audit_skill),
        "tasks": tasks,
        "seed": seed,
    }
    manifest_path = _manifest_path(resolved_output)
    cached = _adopt_generation(manifest_path, resolved_output, identity)
    if cached is not None:
        return cached
    partial = _partial_checkpoint(manifest_path, resolved_output, identity)
    if partial is None and (resolved_output.exists() or manifest_path.exists()):
        raise ExperimentError(
            f"generation output or manifest already exists without a valid checkpoint: "
            f"{resolved_output}, {manifest_path}"
        )
    selected = _select_targets(
        task_skill / "scripts" / "task_pipeline.py",
        coverage_report,
        tasks=tasks,
        seed=seed,
    )
    input_digest = identity["suite"]
    work_dir = (
        resolved_output.parent / f".{resolved_output.name}-generation-work-{input_digest[-12:]}"
    )
    if partial is None:
        if work_dir.exists():
            raise ExperimentError(f"generation work directory already exists: {work_dir}")
        eval_author_dir = work_dir / ".eval-author"
        eval_author_dir.mkdir(parents=True)
        shutil.copytree(resolved_suite, work_dir / "source-suite", symlinks=True)
        shutil.copy2(audit, eval_author_dir / "audit.md")
        authored_dir = eval_author_dir / "authored"
        authored_dir.mkdir(parents=True, exist_ok=True)
        authored_audit = authored_dir / "audit.md"
        shutil.copy2(audit, authored_audit)
        _stage_coverage_binding(measurement_path, eval_author_dir, coverage_report)
        shutil.copytree(resolved_suite, resolved_output, symlinks=True)
    else:
        work_dir = Path(partial["work_dir"])
    prior = {item["task_slug"]: item for item in (partial or {}).get("attempts", [])}
    attempts: list[dict[str, Any]] = list(prior.values())
    result = {
        "schema": "nemo.eval_author.experiment_2.generation.v1",
        "status": "running",
        "cached": False,
        "resumed": partial is not None,
        "identity": identity,
        "input_suite": str(resolved_suite),
        "expanded_suite": str(resolved_output),
        "expanded_suite_digest": tree_digest(resolved_output),
        "measurement": str(_measurement_file(measurement_path)),
        "coverage_report": str(coverage_report),
        "audit": str(audit),
        "agent_config": str(agent.path),
        "generator_config": str(generator.path),
        "generator_version": generator.version,
        "generation_budget": tasks,
        "seed": seed,
        "actionable_count": len(selected),
        "funnel": _attempt_funnel(attempts),
        "attempts": attempts,
        "work_dir": str(work_dir),
    }
    if partial is None:
        write_json(manifest_path, result)
    for target in selected:
        previous = prior.get(target["task_slug"])
        attempt_index = attempts.index(previous) if previous is not None else len(attempts)
        if previous is not None and previous.get("checkpoint_state") != "running":
            recovered = _recover_attempt(previous, workspace=work_dir, target=target, draft_dir=draft_dir)
            attempts[attempt_index] = recovered
            _export_attempt(recovered, resolved_output)
            _write_generation_checkpoint(
                manifest_path,
                result=result,
                output=resolved_output,
                attempts=attempts,
            )
            continue
        pending = _empty_attempt(target)
        pending["checkpoint_state"] = "running"
        if previous is None:
            attempts.append(pending)
        else:
            attempts[attempt_index] = pending
        _write_generation_checkpoint(
            manifest_path,
            result=result,
            output=resolved_output,
            attempts=attempts,
        )
        prompt = _generation_prompt(
            workspace=work_dir,
            target=target,
            task_skill=task_skill,
            audit_skill=audit_skill,
            audit=audit,
            agent=agent,
            generator=generator,
            draft_dir=draft_dir,
        )
        try:
            run = run_generator(
                generator,
                workspace=work_dir,
                target_slug=target["task_slug"],
                prompt=prompt,
                logs_dir=work_dir / ".eval-author" / "generator-logs",
            )
            attempt = _inspect_attempt(
                target=target,
                draft=_draft_path(work_dir, draft_dir, target["task_slug"]),
                run_returncode=run.returncode,
                run_timed_out=run.timed_out,
                stdout=run.stdout,
                stderr=run.stderr,
            )
        except (ExperimentError, OSError) as exc:
            attempt = _empty_attempt(target, error=str(exc))
        attempt["checkpoint_state"] = "finished"
        attempts[attempt_index] = attempt
        _export_attempt(attempt, resolved_output)
        _write_generation_checkpoint(
            manifest_path,
            result=result,
            output=resolved_output,
            attempts=attempts,
        )
    if tree_digest(resolved_suite) != input_digest:
        raise ExperimentError("input suite changed during generation")
    if file_digest(audit) != measurement["static_identity"]["audit"]:
        raise ExperimentError("audit changed during generation")
    discover_suite(resolved_output)
    status = "complete"
    _write_generation_checkpoint(
        manifest_path,
        result=result,
        output=resolved_output,
        attempts=attempts,
        status=status,
    )
    return result


def _skill_paths(eval_author_root: Path) -> tuple[Path, Path]:
    base = eval_author_root.expanduser().resolve() / "skills"
    task_skill = base / "eval-author-task-create"
    audit_skill = base / "eval-author-audit"
    required = (
        task_skill / "SKILL.md",
        task_skill / "scripts" / "task_pipeline.py",
        audit_skill / "SKILL.md",
        audit_skill / "scripts" / "audit_spec" / "measure.py",
        audit_skill / "scripts" / "audit_spec" / "report.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ExperimentError(
            "Eval Author generation skills are incomplete: " + ", ".join(missing)
        )
    return task_skill, audit_skill


def _stage_coverage_binding(
    measurement_path: Path, eval_author_dir: Path, coverage_report: Path
) -> None:
    """Copy bundled coverage binding artifacts into the generation work cell."""
    measurement_root = (
        measurement_path.expanduser().resolve()
        if measurement_path.is_dir()
        else measurement_path.expanduser().resolve().parent
    )
    report = json.loads(coverage_report.read_text(encoding="utf-8"))
    binding = report.get("binding")
    if not isinstance(binding, dict):
        raise ExperimentError("coverage report binding must be an object")
    artifacts = binding.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ExperimentError("coverage report binding.artifacts must be an object")
    path_updates: dict[str, str] = {}
    staged_artifacts: dict[str, str] = {}
    for absolute_path, relative_path in artifacts.items():
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ExperimentError("coverage binding artifact relative path must be a non-empty string")
        source = Path(str(absolute_path)).expanduser()
        if not source.is_file():
            source = measurement_root / relative_path
        if not source.is_file():
            raise ExperimentError(f"coverage binding artifact missing: {absolute_path}")
        destination = eval_author_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        resolved = str(destination.resolve())
        path_updates[str(absolute_path)] = resolved
        staged_artifacts[resolved] = relative_path
    binding["artifacts"] = staged_artifacts
    inputs = binding.get("inputs")
    if isinstance(inputs, list):
        for entry in inputs:
            if isinstance(entry, dict) and str(entry.get("path", "")) in path_updates:
                entry["path"] = path_updates[str(entry["path"])]
    audit = report.get("audit")
    if isinstance(audit, dict) and isinstance(audit.get("path"), str):
        previous = audit["path"]
        if previous in path_updates:
            audit["path"] = path_updates[previous]
    write_json(eval_author_dir / "audit-coverage-report.json", report)


def _load_measurement(
    path: Path, *, suite: Path, agent: AgentConfig
) -> tuple[dict[str, Any], Path, Path]:
    measurement_file = _measurement_file(path)
    try:
        payload = json.loads(measurement_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read before measurement {measurement_file}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("status") != "complete":
        raise ExperimentError("before measurement must be a complete measurement object")
    static = payload.get("static_identity") or {}
    if static.get("suite") != tree_digest(suite):
        raise ExperimentError("before measurement suite identity does not match --suite")
    if static.get("agent") != agent.digest:
        raise ExperimentError("before measurement agent identity does not match --agent")
    coverage_report = Path(str(payload.get("coverage_report", ""))).expanduser().resolve()
    audit = Path(str(payload.get("audit", ""))).expanduser().resolve()
    if not coverage_report.is_file() or not audit.is_file():
        raise ExperimentError("before measurement references a missing coverage report or audit")
    if file_digest(coverage_report) != payload.get("coverage_report_digest"):
        raise ExperimentError("before measurement coverage report digest does not match")
    if file_digest(audit) != static.get("audit"):
        raise ExperimentError("before measurement audit digest does not match")
    return payload, coverage_report, audit


def _measurement_file(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    return resolved / "measurement.json" if resolved.is_dir() else resolved


def _select_targets(
    task_pipeline: Path, coverage_report: Path, *, tasks: int, seed: int
) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, str(task_pipeline), "select", "--report", str(coverage_report)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(f"task-create select failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
        actionable = payload["actionable_tools"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ExperimentError(f"cannot parse task-create selection: {exc}") from exc
    if not isinstance(actionable, list) or not all(isinstance(item, dict) for item in actionable):
        raise ExperimentError("task-create selection actionable_tools must be a list of objects")
    ordered = sorted(actionable, key=lambda item: str(item.get("name", "")))
    random.Random(seed).shuffle(ordered)
    return ordered[:tasks]


def _draft_path(workspace: Path, draft_dir: Path, slug: str) -> Path:
    return workspace / draft_dir / slug


def _is_task_dir(path: Path) -> bool:
    return path.is_dir() and (path / "task.toml").is_file()


def _generation_prompt(
    *,
    workspace: Path,
    target: dict[str, Any],
    task_skill: Path,
    audit_skill: Path,
    audit: Path,
    agent: AgentConfig,
    generator: GeneratorConfig,
    draft_dir: Path,
) -> str:
    slug = target["task_slug"]
    target_name = target["name"]
    proposal = workspace / ".eval-author" / "proposals" / f"{slug}-instruction.md"
    draft = _draft_path(workspace, draft_dir, slug)
    pipeline = task_skill / "scripts" / "task_pipeline.py"
    return f"""Complete one real Eval Author task-generation attempt. Do not merely explain it.

The user authorized local Docker, Harbor, provider calls, and their cost for this run. Do not
delegate or spawn another coding agent. Work only under `{workspace}`. Treat
`{workspace}/source-suite` as read-only reference material and never edit it.

Read these instructions completely before acting:
- `{task_skill / "SKILL.md"}`
- `{audit_skill / "SKILL.md"}`
- `{audit_skill / "scripts" / "audit_spec" / "README.md"}`

Use the checked-in task-create algorithm and its deterministic verdicts. The fixed before report
is `{workspace / ".eval-author" / "audit-coverage-report.json"}`. Generate exactly the actionable
tool target `{target_name}` with slug `{slug}`. Its selection record is:
{json.dumps(target, indent=2, sort_keys=True)}

The canonical audit is `{audit}`. It has already been digest-checked against the before
measurement and retains the relative source files it needs. Use that canonical path for every
measurement and report command. The copy under this workspace is evidence only.

Run `{pipeline}` for select/scaffold/verify. Write the proposal exactly to `{proposal}`. Scaffold exactly to `{draft}`. Use task name `{agent.name}/{slug}`. Prefer `{pipeline} scaffold` so the draft lineage matches the fixed before report. Build a real objective task and a deterministic outcome verifier; do not mention the target tool or verifier implementation in the task instruction. Complete all Harbor task files and leave no placeholders.

The benchmark copies `{draft}` and runs the eval agent on that copy. Harbor trials, receipts,
and other skill evidence stay in this work cell, outside `{draft}`. The benchmark does not run
those trials again.

Before you exit, `{draft}` MUST exist and include instruction.md, task.toml, environment/Dockerfile,
tests/test.sh, solution/solve.sh, and README.md. Exit code 0 without that directory is incorrect.

If anything fails, repair only the generated proposal and `{draft}`. Never weaken the verifier
to force a pass. Generator identity: {generator.name}, model {generator.model},
runtime {generator.version}.
"""


def _inspect_attempt(
    *,
    target: dict[str, Any],
    draft: Path,
    run_returncode: int,
    run_timed_out: bool,
    stdout: Path,
    stderr: Path,
) -> dict[str, Any]:
    attempt = _empty_attempt(target)
    generated = _is_task_dir(draft)
    attempt.update(
        {
            "generator_returncode": run_returncode,
            "generator_timed_out": run_timed_out,
            "generator_stdout": str(stdout),
            "generator_stderr": str(stderr),
            "draft": str(draft),
            "generated": generated,
        }
    )
    if not generated:
        attempt["errors"].append("generator did not create the expected draft")
    elif run_returncode != 0:
        attempt["errors"].append(
            f"generator exited with {run_returncode}"
            + (" after timing out" if run_timed_out else "")
        )
    return attempt


def _empty_attempt(target: dict[str, Any], *, error: str | None = None) -> dict[str, Any]:
    return {
        "target": target.get("name"),
        "task_slug": target.get("task_slug"),
        "attempted": True,
        "generated": False,
        "technically_passed": False,
        "finalized": False,
        "human_ready": False,
        "errors": [error] if error else [],
    }


def _manifest_path(output: Path) -> Path:
    return output.parent / f"{output.name}.generation.json"


def _attempt_funnel(attempts: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "attempted": len(attempts),
        "generated": sum(bool(item["generated"]) for item in attempts),
        "technically_passed": sum(bool(item["technically_passed"]) for item in attempts),
        "finalized": sum(bool(item["finalized"]) for item in attempts),
        "human_ready": 0,
    }


def _write_generation_checkpoint(
    manifest_path: Path,
    *,
    result: dict[str, Any],
    output: Path,
    attempts: list[dict[str, Any]],
    status: str = "running",
) -> None:
    result.update(
        {
            "status": status,
            "expanded_suite_digest": tree_digest(output),
            "funnel": _attempt_funnel(attempts),
            "attempts": attempts,
        }
    )
    write_json(manifest_path, result)


def _adopt_generation(
    manifest_path: Path, output: Path, identity: dict[str, Any]
) -> dict[str, Any] | None:
    if not manifest_path.is_file() or not output.is_dir():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "complete"
        or payload.get("identity") != identity
    ):
        return None
    if tree_digest(output) != payload.get("expanded_suite_digest"):
        return None
    adopted = dict(payload)
    adopted["cached"] = True
    return adopted


def _partial_checkpoint(
    manifest_path: Path, output: Path, identity: dict[str, Any]
) -> dict[str, Any] | None:
    """Load an exact in-progress checkpoint whose immutable output is still intact."""
    if not manifest_path.is_file() or not output.is_dir():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("status") not in {"running", "partial"}
        or payload.get("identity") != identity
        or tree_digest(output) != payload.get("expanded_suite_digest")
    ):
        return None
    work_dir = Path(str(payload.get("work_dir", ""))).expanduser().resolve()
    if not work_dir.is_dir():
        return None
    return payload


def _recover_attempt(
    previous: dict[str, Any],
    *,
    workspace: Path,
    target: dict[str, Any],
    draft_dir: Path,
) -> dict[str, Any]:
    """Reread a finished attempt and export its draft again."""
    logs = workspace / ".eval-author" / "generator-logs"
    slug = target["task_slug"]
    attempt = _inspect_attempt(
        target=target,
        draft=_draft_path(workspace, draft_dir, slug),
        run_returncode=int(previous.get("generator_returncode", 0)),
        run_timed_out=bool(previous.get("generator_timed_out", False)),
        stdout=Path(previous.get("generator_stdout", logs / f"{slug}.stdout.txt")),
        stderr=Path(previous.get("generator_stderr", logs / f"{slug}.stderr.txt")),
    )
    attempt["checkpoint_state"] = previous.get("checkpoint_state", "finished")
    return attempt


def _export_attempt(attempt: dict[str, Any], output: Path) -> None:
    if not attempt["generated"]:
        return
    draft = Path(attempt["draft"])
    destination = output / "_generated" / attempt["task_slug"]
    if destination.exists():
        if tree_digest(destination) != tree_digest(draft):
            raise ExperimentError(
                f"checkpoint generated task differs from draft: {destination}"
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(draft, destination, symlinks=True)
    attempt["expanded_task"] = str(destination)
