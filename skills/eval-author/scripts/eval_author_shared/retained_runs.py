# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Locate completed Harbor agent trials without reading trajectories."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from contextlib import ExitStack
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


SAFE_DIRECTORY_READS = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.scandir in os.supports_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)
DirectoryChain = list[tuple[int, str, int]]


def directories(descriptor: int, on_error: Callable[[str, OSError], None]) -> list[tuple[str, os.stat_result]]:
    found = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            try:
                info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                on_error(entry.name, exc)
                continue
            if stat.S_ISDIR(info.st_mode):
                found.append((entry.name, info))
    return found


def open_directory(
    stack: ExitStack, parent: int | None, name: str, chain: DirectoryChain, expected: os.stat_result | None = None
) -> int:
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stack.callback(os.close, descriptor)
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or (
        expected is not None and (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        raise ValueError(f"Directory changed during lookup: {name}")
    if parent is not None:
        chain.append((parent, name, descriptor))
    return descriptor


def validate_chain(chain: DirectoryChain) -> None:
    for parent, name, descriptor in chain:
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise ValueError(f"Directory changed during lookup: {name}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"Directory changed during lookup: {name}")


def read_metadata(trial_descriptor: int) -> dict:
    descriptor = os.open(
        "result.json", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=trial_descriptor
    )
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("result metadata must be a regular file")
        if info.st_size > MAX_METADATA_BYTES:
            raise ValueError("result metadata exceeds byte limit")
        payload = source.read(MAX_METADATA_BYTES + 1)
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("result metadata exceeds byte limit")
    record = json.loads(payload)
    if not isinstance(record, dict):
        raise ValueError("result metadata must be an object")
    return record


def candidate(
    repo: Path, trial: Path, descriptor: int, chain: DirectoryChain, agent: str | None, tasks: tuple[str, ...]
) -> RunCandidate | None:
    validate_chain(chain)
    try:
        record = read_metadata(descriptor)
    except FileNotFoundError:
        return None
    info = record.get("agent_info")
    observed_agent = info.get("name") if isinstance(info, dict) else None
    task, finished = record.get("task_name"), record.get("finished_at")
    if not isinstance(observed_agent, str) or not observed_agent.strip() or observed_agent.casefold() in CONTROLS:
        return None
    if agent is not None and observed_agent != agent:
        return None
    if not isinstance(task, str) or not task.strip() or not finished or (tasks and task not in tasks):
        return None
    if not isinstance(finished, str):
        raise ValueError("finished_at must be a timestamp")
    datetime.fromisoformat(finished.replace("Z", "+00:00"))
    with ExitStack() as stack:
        agent_chain = chain.copy()
        try:
            agent_descriptor = open_directory(stack, descriptor, "agent", agent_chain)
            trajectory = os.stat("trajectory.json", dir_fd=agent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(trajectory.st_mode):
            return None
        validate_chain(agent_chain)
    return RunCandidate(
        result=(trial / "result.json").relative_to(repo).as_posix(),
        trace=(trial / "agent/trajectory.json").relative_to(repo).as_posix(),
        agent=observed_agent,
        task=task,
        finished_at=finished,
    )


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

    def scan() -> None:
        with ExitStack() as stack:
            root_chain: DirectoryChain = []
            repo_descriptor = open_directory(stack, None, repo.anchor, root_chain)
            for part in repo.parts[1:]:
                repo_descriptor = open_directory(stack, repo_descriptor, part, root_chain)
            try:
                author_descriptor = open_directory(stack, repo_descriptor, ".eval-author", root_chain)
                jobs_descriptor = open_directory(stack, author_descriptor, "jobs", root_chain)
            except FileNotFoundError:
                return
            jobs = directories(jobs_descriptor, lambda name, exc: error(root / name, str(exc)))
            validate_chain(root_chain)
            names = {task.rsplit("/", 1)[-1].casefold() for task in tasks}
            jobs.sort(
                key=lambda item: (not any(name in item[0].casefold() for name in names), -item[1].st_mtime_ns, item[0])
            )
            result["jobs_total"] = len(jobs)
            selected = jobs[offset : offset + max_jobs]
            if offset + len(selected) < len(jobs):
                result.update(complete=False, next_offset=offset + len(selected))
            for name, observed in selected:
                job = root / name
                result["jobs_checked"].append(job.relative_to(repo).as_posix())
                with ExitStack() as job_stack:
                    job_chain = root_chain.copy()
                    try:
                        job_descriptor = open_directory(job_stack, jobs_descriptor, name, job_chain, observed)
                        trials = directories(job_descriptor, lambda child, exc: error(job / child, str(exc)))
                        validate_chain(job_chain)
                    except (OSError, ValueError) as exc:
                        error(job, str(exc))
                        continue
                    for trial_name, trial_info in sorted(trials, key=lambda item: item[0]):
                        trial = job / trial_name
                        with ExitStack() as trial_stack:
                            trial_chain = job_chain.copy()
                            try:
                                trial_descriptor = open_directory(
                                    trial_stack, job_descriptor, trial_name, trial_chain, trial_info
                                )
                                found = candidate(repo, trial, trial_descriptor, trial_chain, agent, tasks)
                                validate_chain(trial_chain)
                            except (OSError, ValueError) as exc:
                                error(trial, str(exc))
                                continue
                            if found is not None:
                                candidates.append(found)

    result["jobs_total"] = 0
    if not SAFE_DIRECTORY_READS:
        error(root, "Safe descriptor-relative directory reads are unavailable on this platform")
    else:
        try:
            scan()
        except (OSError, ValueError) as exc:
            error(root, str(exc))
    status = "verified" if agent is not None and candidates else "unknown"
    source = "Retained-run metadata and trace presence: " + ", ".join(item["trace"] for item in candidates)
    if not candidates:
        source = "No qualifying completed agent runs found in the checked jobs"
    elif agent is None:
        source = "Candidate run agents need identity confirmation: " + source
    source += f". Checked {len(result['jobs_checked'])} of {result['jobs_total']} jobs at offset {offset}"
    if result["next_offset"] is not None:
        source += f". Continue at offset {result['next_offset']} before claiming absence"
    if result["errors"]:
        source += ". Lookup errors: " + json.dumps(result["errors"], sort_keys=True)
    result["state_update"] = {"prerequisites": {"data": {"status": status, "source": source}}}
    return result
