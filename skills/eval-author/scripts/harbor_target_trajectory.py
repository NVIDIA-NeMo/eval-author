# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Record the agent under test as ATIF at Harbor's target trajectory path.

The recorder uses only the standard library so a generated adapter can copy it
into a task container, where an ACP target runs during user simulation. The
host checks the export with Harbor's own ATIF model through ``check``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from harbor.models.trajectories.trajectory import Trajectory

TRAJECTORY_FILENAME = "trajectory.json"
EVENTS_FILENAME = "target-events.jsonl"
SUBJECT = "agent_under_test"
EXPORTER = "eval-author/harbor_target_trajectory"
Status = Literal["running", "completed", "failed", "interrupted"]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str | None
    content: str | None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Target evidence requires a {label}")
    return value


class TargetRecorder:
    """Append observed target steps and checkpoint ATIF after each one.

    A restarted adapter process reopens the same run's events and continues
    numbering. A different run or task in the same directory is rejected.
    """

    def __init__(
        self,
        directory: Path,
        *,
        agent_name: str,
        agent_version: str,
        run_id: str,
        task_id: str,
        model_name: str | None = None,
        restart: bool = True,
    ) -> None:
        self.directory = directory
        self.path = directory / TRAJECTORY_FILENAME
        self.events_path = directory / EVENTS_FILENAME
        self.identity = {
            "name": _identity(agent_name, "agent name"),
            "version": _identity(agent_version, "agent version"),
        }
        if model_name:
            self.identity["model_name"] = model_name
        self.run_id = _identity(run_id, "run ID")
        self.task_id = _identity(task_id, "task ID")
        self.status: Status = "running"
        self.error: str | None = None
        self.restarts = 0
        self.steps: list[dict[str, Any]] = []
        directory.mkdir(parents=True, exist_ok=True)
        if self.events_path.exists():
            self._reopen(restart)

    @classmethod
    def resume(cls, directory: Path, *, run_id: str, task_id: str) -> TargetRecorder:
        """Reopen a running recording from the host, such as at teardown, without counting an adapter restart."""
        path = directory / TRAJECTORY_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"Missing target trajectory at {path}")
        agent = json.loads(path.read_text(encoding="utf-8"))["agent"]
        return cls(
            directory,
            agent_name=agent["name"],
            agent_version=agent["version"],
            run_id=run_id,
            task_id=task_id,
            model_name=agent.get("model_name"),
            restart=False,
        )

    def _reopen(self, restart: bool) -> None:
        if not self.path.exists():
            raise FileExistsError("Target events exist without their trajectory; use a fresh evidence directory")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        extra = saved.get("extra") or {}
        if (extra.get("subject"), extra.get("run_id"), extra.get("task_id")) != (SUBJECT, self.run_id, self.task_id):
            raise FileExistsError("Target evidence belongs to another run or task; use a fresh evidence directory")
        if extra.get("status") != "running":
            raise FileExistsError("A finished target recording cannot be reopened")
        self.steps = [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]
        self.restarts = int(extra.get("adapter_restarts", 0)) + restart
        self._save()

    def user(self, message: str) -> None:
        self._append({"source": "user", "message": message})

    def agent(
        self,
        message: str,
        *,
        tool_calls: list[ToolCall] | None = None,
        results: list[ToolResult] | None = None,
        usage: Usage | None = None,
        model_name: str | None = None,
        reasoning: str | None = None,
        llm_call_count: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        step: dict[str, Any] = {"source": "agent", "message": message}
        if model_name:
            step["model_name"] = model_name
        if reasoning is not None:
            step["reasoning_content"] = reasoning
        if tool_calls:
            step["tool_calls"] = [
                {"tool_call_id": call.call_id, "function_name": call.name, "arguments": call.arguments}
                for call in tool_calls
            ]
        if results:
            known = {call.call_id for call in tool_calls or []}
            if any(result.call_id is not None and result.call_id not in known for result in results):
                raise ValueError("Tool results must reference a tool call in the same step")
            step["observation"] = {
                "results": [
                    {
                        key: value
                        for key, value in (("source_call_id", r.call_id), ("content", r.content))
                        if value is not None
                    }
                    for r in results
                ]
            }
        if usage is not None:
            metrics = {key: value for key, value in vars(usage).items() if value is not None}
            if metrics:
                step["metrics"] = metrics
        if llm_call_count is not None:
            step["llm_call_count"] = llm_call_count
        if extra:
            step["extra"] = extra
        self._append(step)

    def _append(self, step: dict[str, Any]) -> None:
        if self.status != "running":
            raise ValueError("A finished target recording cannot accept more steps")
        if not isinstance(step["message"], str):
            raise TypeError("Target messages must be text")
        record = {"step_id": len(self.steps) + 1, "timestamp": _now(), **step}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.steps.append(json.loads(line))
        self._save()

    def finish(self, status: Literal["completed", "failed", "interrupted"], *, error: str | None = None) -> None:
        if self.status != "running":
            raise ValueError("Target recording already finished")
        self.status = status
        self.error = error
        if self.steps:
            self._save()

    def _save(self) -> None:
        if not self.steps:
            return
        agent_steps = [step for step in self.steps if step["source"] == "agent" and step.get("llm_call_count") != 0]
        totals: dict[str, Any] = {"total_steps": len(self.steps)}
        for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd"):
            values = [step.get("metrics", {}).get(field) for step in agent_steps]
            if values and all(value is not None for value in values):
                totals[f"total_{field}"] = sum(values)
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": self.run_id,
            "agent": self.identity,
            "steps": self.steps,
            "final_metrics": totals,
            "extra": {
                "subject": SUBJECT,
                "task_id": self.task_id,
                "run_id": self.run_id,
                "status": self.status,
                "error": self.error,
                "adapter_restarts": self.restarts,
                "exporter": EXPORTER,
                "source_events": EVENTS_FILENAME,
                "source_sha256": hashlib.sha256(self.events_path.read_bytes()).hexdigest(),
            },
        }
        pending = self.path.with_name(self.path.name + ".pending")
        pending.write_text(json.dumps(trajectory, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(self.path)


def check_target_export(agent_dir: Path, *, agent_name: str, run_id: str, task_id: str) -> Trajectory:
    """Validate a target export with Harbor's ATIF model and its retained events."""
    from harbor.models.trajectories.step import Step
    from harbor.models.trajectories.trajectory import Trajectory

    path = agent_dir / TRAJECTORY_FILENAME
    if not path.is_file():
        raise ValueError(f"Missing target trajectory at {path}")
    trajectory = Trajectory.model_validate_json(path.read_text(encoding="utf-8"))
    extra = trajectory.extra or {}
    if extra.get("subject") != SUBJECT:
        raise ValueError("The trajectory does not record the agent under test; simulator evidence cannot substitute")
    if trajectory.agent.name != agent_name:
        raise ValueError("Target trajectory names a different agent")
    if (trajectory.session_id, extra.get("run_id"), extra.get("task_id")) != (run_id, run_id, task_id):
        raise ValueError("Target trajectory is bound to a different run or task")
    events_path = agent_dir / EVENTS_FILENAME
    if extra.get("source_events") != EVENTS_FILENAME or not events_path.is_file():
        raise ValueError("Target trajectory is missing its retained source events")
    raw = events_path.read_bytes()
    if extra.get("source_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("Target source events changed after export")
    if [Step.model_validate_json(line) for line in raw.splitlines() if line] != trajectory.steps:
        raise ValueError("Target trajectory differs from its retained source events")
    return trajectory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Validate <trial>/agent/trajectory.json for the agent under test")
    check.add_argument("--trial-dir", type=Path, required=True)
    check.add_argument("--agent-name", required=True)
    check.add_argument("--task-id", required=True)
    check.add_argument("--run-id", help="Defaults to the trial directory name")
    finish = commands.add_parser("finish", help="Close a running target recording, such as from acp_teardown()")
    finish.add_argument("--agent-dir", type=Path, required=True)
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--task-id", required=True)
    finish.add_argument("--status", choices=("completed", "failed", "interrupted"), required=True)
    finish.add_argument("--error")
    args = parser.parse_args(argv)
    if args.command == "finish":
        return _finish(args)
    try:
        trajectory = check_target_export(
            args.trial_dir / "agent",
            agent_name=args.agent_name,
            run_id=args.run_id or args.trial_dir.name,
            task_id=args.task_id,
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"valid": False, "error": str(error)}))
        return 1
    extra = trajectory.extra or {}
    print(json.dumps({"valid": True, "steps": len(trajectory.steps), "status": extra.get("status")}))
    return 0


def _finish(args: argparse.Namespace) -> int:
    path = args.agent_dir / TRAJECTORY_FILENAME
    try:
        saved = json.loads(path.read_text(encoding="utf-8")).get("extra") or {}
        if saved.get("status") != "running":
            print(json.dumps({"finished": False, "status": saved.get("status")}))
            return 0
        recorder = TargetRecorder.resume(args.agent_dir, run_id=args.run_id, task_id=args.task_id)
        recorder.finish(args.status, error=args.error)
    except (OSError, ValueError) as error:
        print(json.dumps({"finished": False, "error": str(error)}))
        return 1
    print(json.dumps({"finished": True, "status": args.status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
