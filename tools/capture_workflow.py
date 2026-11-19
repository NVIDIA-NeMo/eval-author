#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture one authorized native host turn with before/after manifests.

The supplied argv must emit native JSONL and accept the exact prompt on stdin.
This recorder does not sandbox the host, grant permissions, label actions, or
approve a handoff. Use disposable fixtures and the host's own permission controls.
For follow-ups, invoke the host's resume command and record each turn separately.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from check_workflow_scope import snapshot
from discovery_determinism import encoded, read, sha
from workflow_matrix import native_calls


def rendered_reply_errors(raw: bytes, renderer_item_id: str) -> list[str]:
    """Compare native delivery after a reviewer selects the actual renderer command.

    The command-name check is a guard against selection mistakes, not attestation
    of what executed. Empty errors establish delivery equality, not host acceptance.
    """
    try:
        _, errors = native_calls("codex", raw, 0)
        if errors:
            return errors
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (ValueError, KeyError, TypeError) as exc:
        return [f"invalid native capture: {exc}"]
    if (
        sum(event.get("type") == "turn.completed" for event in events) != 1
        or sum(event.get("type") == "turn.started" for event in events) > 1
        or events[-1].get("type") != "turn.completed"
    ):
        return ["rendered delivery requires one complete native turn"]
    commands, messages = [], []
    for index, event in enumerate(events):
        if event.get("type") != "item.completed":
            continue
        item = event["item"]
        if item.get("type") == "agent_message":
            messages.append((index, item))
        command = item.get("command")
        if (
            item.get("type") == "command_execution"
            and isinstance(command, str)
            and re.search(r"(?<![\w.-])conversation_renderer\.py(?![\w.-])", command)
        ):
            commands.append((index, item))
    selected = [(index, item) for index, item in commands if item.get("id") == renderer_item_id]
    if len(selected) != 1:
        return ["selected renderer command is missing or ambiguous"]
    index, command = selected[0]
    if any(event.get("type") == "turn.started" for event in events[index + 1 :]):
        return ["selected renderer command belongs to a different native turn"]
    output = command.get("aggregated_output")
    if (
        type(command.get("exit_code")) is not int
        or command["exit_code"] != 0
        or not isinstance(output, str)
        or not output.rstrip("\r\n")
    ):
        return ["selected renderer command did not produce successful output"]
    if not messages or messages[-1][0] <= index:
        return ["final assistant reply must follow the renderer command"]
    text = messages[-1][1].get("text")
    if not isinstance(text, str) or text.rstrip("\r\n") != output.rstrip("\r\n"):
        return ["final assistant reply differs from renderer output"]
    return []


def capture(
    cell: dict,
    repo: Path,
    skills: Path,
    output: Path,
    command: list[str],
    *,
    turn: int = 0,
    timeout: int = 120,
    host_version: str,
    model: str,
    settings: dict,
) -> dict:
    repo, skills, output = repo.resolve(), skills.resolve(), output.resolve()
    if output.is_relative_to(repo) or output.is_relative_to(skills):
        raise ValueError("retain captures outside the observed repository and skill installation")
    if not command or not 0 <= turn < len(cell["prompts"]) or timeout <= 0:
        raise ValueError("command, valid turn index, and positive timeout required")
    output.mkdir(parents=True, exist_ok=False)
    before, installed = snapshot(repo), snapshot(skills)
    record = {
        "cell": cell,
        "platform": sys.platform,
        "turn": turn,
        "prompt": cell["prompts"][turn],
        "command": command,
        "repo": str(repo),
        "skills_root": str(skills),
        "skills_sha256": sha(encoded(installed)),
        "host_version": host_version,
        "model": model,
        "settings": settings,
        "before": before,
        "status": "incomplete",
        "reason": "interrupted",
        "timeout": timeout,
    }
    record_path = output / "capture.json"
    record_path.write_text(json.dumps(record, indent=2))  # retained even if interrupted
    with (output / "native.jsonl").open("wb") as stdout, (output / "stderr.txt").open("wb") as stderr:
        try:
            run = subprocess.run(
                command,
                input=cell["prompts"][turn].encode(),
                cwd=repo,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                check=False,
            )
            record["returncode"] = run.returncode
            record["reason"] = "awaiting_native_review" if run.returncode == 0 else "host_failed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            record["reason"] = type(exc).__name__
    raw_path = output / "native.jsonl"
    raw = raw_path.read_bytes()
    record["raw"] = {"path": raw_path.name, "sha256": sha(raw)}
    record["after"] = snapshot(repo)
    record["skills_unchanged"] = snapshot(skills) == installed
    try:
        calls, errors = native_calls(cell["host"], raw, turn)
        record["native_calls"] = calls
        record["adapter_errors"] = errors
    except (ValueError, KeyError, TypeError) as exc:
        record["adapter_errors"] = [str(exc)]
    record_path.write_text(json.dumps(record, indent=2))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--skills", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turn", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--host-version", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        cell = next(cell for cell in read(args.matrix)["cells"] if cell["id"] == args.cell)
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        result = capture(
            cell,
            args.repo,
            args.skills,
            args.output,
            command,
            turn=args.turn,
            timeout=args.timeout,
            host_version=args.host_version,
            model=args.model,
            settings=read(args.settings),
        )
        # Full native output stays in files; stdout is a compact accounting record.
        print(json.dumps({"status": result["status"], "reason": result["reason"], "capture": str(args.output)}))
        return 2  # capture is never a reviewed acceptance result
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        print(json.dumps({"status": "incomplete", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
