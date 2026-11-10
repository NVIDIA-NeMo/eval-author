# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Locate completed Harbor agent trials without reading trajectories."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import TypedDict

JOBS = Path(".eval-author/jobs")
CONTROLS = {"oracle", "nop", "no-op", "noop"}
MAX_METADATA_BYTES = 1024 * 1024


class RunCandidate(TypedDict):
    result: str
    trace: str
    agent: str
    task: str
    finished_at: str


def directories(path: Path) -> list[Path]:
    with os.scandir(path) as entries:
        return [Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)]


def regular(path: Path) -> bool:
    return stat.S_ISREG(path.lstat().st_mode)


def lookup(
    repo: Path, *, agent: str | None = None, tasks: tuple[str, ...] = (), offset: int = 0, max_jobs: int = 200
) -> dict:
    if offset < 0 or max_jobs < 1 or (agent is not None and (not agent.strip() or agent.casefold() in CONTROLS)):
        raise ValueError("Use a non-control agent, a nonnegative offset, and a positive job limit")
    repo = repo.resolve(strict=True)
    root = repo / JOBS
    candidates: list[RunCandidate] = []
    result: dict = {"candidates": candidates, "jobs_checked": [], "errors": [], "next_offset": None, "complete": True}

    def error(path: Path, reason: str) -> None:
        result["errors"].append({"path": path.relative_to(repo).as_posix(), "reason": reason})
        result["complete"] = False

    try:
        if (repo / ".eval-author").is_symlink() or root.is_symlink():
            raise ValueError("symlinked jobs root")
        jobs = directories(root)
        names = {task.rsplit("/", 1)[-1].casefold() for task in tasks}
        jobs.sort(
            key=lambda job: (not any(name in job.name.casefold() for name in names), -job.stat().st_mtime_ns, job.name)
        )
    except FileNotFoundError:
        jobs = []
    except (OSError, ValueError) as exc:
        error(root, str(exc))
        jobs = []
    selected = jobs[offset : offset + max_jobs]
    if offset + len(selected) < len(jobs):
        result.update(complete=False, next_offset=offset + len(selected))
    result["jobs_total"] = len(jobs)
    for job in selected:
        result["jobs_checked"].append(job.relative_to(repo).as_posix())
        try:
            trials = sorted(directories(job))
        except OSError as exc:
            error(job, str(exc))
            continue
        for trial in trials:
            metadata, trace = trial / "result.json", trial / "agent/trajectory.json"
            try:
                if not regular(metadata):
                    continue
                fd = os.open(metadata, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError("result metadata must be a regular file")
                    payload = source.read(MAX_METADATA_BYTES + 1)
                if len(payload) > MAX_METADATA_BYTES:
                    raise ValueError("result metadata exceeds byte limit")
                record = json.loads(payload)
                if not isinstance(record, dict):
                    raise ValueError("result metadata must be an object")
                info = record.get("agent_info")
                observed_agent = info.get("name") if isinstance(info, dict) else None
                task, finished = record.get("task_name"), record.get("finished_at")
                if (
                    not isinstance(observed_agent, str)
                    or not observed_agent.strip()
                    or observed_agent.casefold() in CONTROLS
                ):
                    continue
                if agent is not None and observed_agent != agent:
                    continue
                if not isinstance(task, str) or not task.strip() or not finished:
                    continue
                if tasks and task not in tasks:
                    continue
                if not isinstance(finished, str):
                    raise ValueError("finished_at must be a timestamp")
                datetime.fromisoformat(finished.replace("Z", "+00:00"))
                if (trial / "agent").is_symlink() or not regular(trace):
                    continue
                candidates.append(
                    RunCandidate(
                        result=metadata.relative_to(repo).as_posix(),
                        trace=trace.relative_to(repo).as_posix(),
                        agent=observed_agent,
                        task=task,
                        finished_at=finished,
                    )
                )
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                error(metadata, str(exc))
    status = "verified" if agent is not None and candidates else "unknown"
    source = "Retained-run metadata and trace presence: " + ", ".join(item["trace"] for item in candidates)
    if not candidates:
        source = "No qualifying completed agent runs found in the checked jobs"
    elif agent is None:
        source = "Candidate run agents need identity confirmation: " + source
    source += f". Checked {len(selected)} of {len(jobs)} jobs at offset {offset}"
    if result["next_offset"] is not None:
        source += f". Continue at offset {result['next_offset']} before claiming absence"
    if result["errors"]:
        source += ". Lookup errors: " + json.dumps(result["errors"], sort_keys=True)
    result["state_update"] = {"prerequisites": {"data": {"status": status, "source": source}}}
    return result
