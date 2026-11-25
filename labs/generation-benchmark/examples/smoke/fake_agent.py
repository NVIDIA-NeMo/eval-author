#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scripted stand-in for the coding agent in the G container's smoke run.

It reads the filled prompt on stdin and follows the eval-author-task-create
commands for one tool: write the proposal, scaffold the draft, fill the task,
and record it as a draft. For each `--accept` tool it then records the five
closure trials through the Harbor shim, measures the two agent runs, verifies
closure, and records the task as accepted. Each command is one JSON line on
stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKILLS = Path("/opt/skills")
JOURNEY_STATE = SKILLS / "eval-author" / "scripts" / "journey_state.py"
AUDIT_SKILL = SKILLS / "eval-author-audit"
TASK_CREATE = SKILLS / "eval-author-task-create" / "scripts"
REWARD = "/verifier_result/rewards/reward"
_FIELD_RE = re.compile(r"^- (?P<label>[^:]+): `(?P<value>[^`]*)`\s*$")
_TAIL = 4000


class CommandFailed(Exception):
    pass


def emit(event: dict) -> None:
    print(json.dumps(event, sort_keys=True), flush=True)


def parse_prompt(text: str) -> dict[str, str]:
    return {match["label"].strip(): match["value"] for line in text.splitlines() if (match := _FIELD_RE.match(line))}


def run(argv: list[str | Path], *, stdin: str | None = None) -> str:
    command = [str(arg) for arg in argv]
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)
    emit(
        {
            "type": "command",
            "argv": command,
            "returncode": result.returncode,
            "stdout": result.stdout[-_TAIL:],
            "stderr": result.stderr[-_TAIL:],
        }
    )
    if result.returncode != 0:
        raise CommandFailed(f"{' '.join(command[:4])} exited {result.returncode}: {result.stderr[-_TAIL:]}")
    return result.stdout


def journey(*args: str | Path, stdin: str | None = None) -> str:
    return run(["python3", JOURNEY_STATE, "--repo", Path.cwd(), *args], stdin=stdin)


def task_pipeline(*args: str | Path) -> str:
    return run(["uv", "run", TASK_CREATE / "task_pipeline.py", *args])


def audit_script(name: str, *args: str | Path) -> str:
    script = AUDIT_SKILL / "scripts" / "audit_spec" / name
    return run(["uv", "run", "--with-requirements", AUDIT_SKILL / "requirements.txt", script, *args])


def write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def fill_task(draft: Path, tool: str, slug: str) -> None:
    """Replace the `harbor task init` scaffold with a task the smoke agent solves by calling *tool*."""
    (draft / "tests" / "test_outputs.py").unlink(missing_ok=True)
    write(
        draft / "task.toml",
        f'version = "1.0"\n[task]\nname = "smoke/{slug}"\n[agent]\ntimeout_sec = 30\n[verifier]\ntimeout_sec = 30\n',
    )
    write(draft / "environment" / "Dockerfile", "FROM python:3.12-slim\nWORKDIR /app\n")
    write(draft / "README.md", f"# {slug}\n\nCovers the smoke tool `{tool}`.\n")
    write(
        draft / "tests" / "test.sh",
        "#!/bin/bash\nmkdir -p /logs/verifier\n"
        'if [ -f /app/answer ] && [ "$(cat /app/answer)" = "ready" ]; then echo 1; else echo 0; fi'
        " > /logs/verifier/reward.txt\n",
        0o755,
    )
    write(draft / "solution" / "solve.sh", "#!/bin/bash\nmkdir -p /app && printf ready > /app/answer\n", 0o755)


def contract(slug: str) -> dict:
    def case(kind: str, requirement: str, reward: float) -> dict:
        return {
            "kind": kind,
            "requirement": requirement,
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": REWARD, "min": reward, "max": reward}],
        }

    return {
        "task_id": slug,
        "provider": "harbor",
        "native_run_id": "/id",
        "alternative_not_applicable": f"{slug} has one accepted answer",
        "side_effect_not_applicable": f"{slug} writes one file",
        "cases": {
            "reference": case("reference", "the oracle solution writes ready", 1.0),
            "incorrect": case("incorrect", "the wrong-answer script scores zero", 0.0),
            "empty": case("empty", "doing nothing scores zero", 0.0),
            "agent": case("agent", "the agent under test writes ready", 1.0),
        },
    }


def close_gap(fields: dict[str, str], workspace: Path, draft: Path, measurements: Path, slug: str, tool: str) -> Path:
    """Record the controls and two agent runs, measure the agent runs, and verify closure."""
    harbor_python = fields["Harbor Python"]
    report = workspace / fields["Gap report"]
    audit = workspace / ".eval-author" / "audit.md"
    trials = measurements / "trials"
    receipts = measurements / "receipts"
    manifest = measurements / "revision-1.json"
    write(measurements / "contract.json", json.dumps(contract(slug), indent=2) + "\n")
    task_evidence = TASK_CREATE / "task_evidence.py"
    run(
        [
            harbor_python,
            task_evidence,
            "prepare",
            "--task-dir",
            draft,
            "--contract",
            measurements / "contract.json",
            "--out",
            manifest,
        ]
    )
    wrong_answer = fields["Wrong-answer agent"]
    runs = [
        ("reference", "reference-1", "oracle"),
        ("incorrect", "incorrect-1", wrong_answer),
        ("empty", "empty-1", "nop"),
        ("agent", "agent-1", fields["Agent under test"]),
        ("agent", "agent-2", fields["Agent under test"]),
    ]
    for case, run_id, agent in runs:
        trace = ["--trace", trials / run_id / "agent" / "trajectory.json"] if case == "agent" else []
        run(
            [
                harbor_python,
                task_evidence,
                "run",
                "--manifest",
                manifest,
                "--case",
                case,
                "--run-id",
                run_id,
                "--out",
                receipts / f"{run_id}.json",
                "--result",
                trials / run_id / "result.json",
                *trace,
                "--",
                "harbor",
                "trial",
                "start",
                "-p",
                draft,
                "-a",
                agent,
                "--trial-name",
                run_id,
                "--trials-dir",
                trials,
            ]
        )
    after: list[str | Path] = []
    for run_id in ("agent-1", "agent-2"):
        audit_script(
            "measure.py",
            "--audit",
            audit,
            "--trial-dir",
            trials / run_id,
            "--task-id",
            slug,
            "--run-id",
            run_id,
            "--measure",
            "tool_calls",
            "--out-dir",
            measurements / run_id,
        )
        out = measurements / f"{run_id}-report.json"
        audit_script("report.py", "--audit", audit, "--coverage-dir", measurements / run_id, "--out", out)
        after += ["--after", out]
    evidence: list[str | Path] = []
    for _, run_id, _ in runs:
        evidence += ["--evidence", receipts / f"{run_id}.json"]
    verification = task_pipeline("verify", "--before", report, *after, "--target", tool, *evidence)
    write(measurements / "verify.json", verification)
    return measurements / "verify.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept", action="append", default=[], help="tool to close; repeatable")
    parser.add_argument("--draft-only", action="store_true", help="stop after recording the draft")
    parser.add_argument("--write-marker", help="workspace-relative file to report and then create")
    args = parser.parse_args(argv)

    workspace = Path.cwd()
    fields = parse_prompt(sys.stdin.read())
    tool, slug = fields["Tool"], fields["Task slug"]
    draft, measurements = workspace / fields["Draft"], workspace / fields["Measurements"]
    proposal = workspace / fields["Proposal"]
    task = fields["Draft"]
    emit({"type": "start", "tool": tool, "slug": slug, "codex_home": os.environ.get("CODEX_HOME")})
    if args.write_marker:
        marker = workspace / args.write_marker
        emit({"type": "marker", "path": args.write_marker, "present_at_start": marker.exists()})
        write(marker, f"{slug}\n")
    try:
        emit({"type": "show", "output": json.loads(journey("show"))})
        write(proposal, f"TOOL: {tool}\n\nCall the `{tool}` tool and write `ready` to `/app/answer`.\n")
        task_pipeline(
            "scaffold",
            "--report",
            workspace / fields["Gap report"],
            "--target",
            tool,
            "--out",
            draft,
            "--task-name",
            f"smoke/{slug}",
            "--description",
            f"Cover the smoke tool {tool}",
            "--author",
            "fake_agent",
            "--instruction-file",
            proposal,
        )
        fill_task(draft, tool, slug)
        write(
            draft / fields["Wrong-answer script"], "#!/bin/bash\nmkdir -p /app && printf wrong > /app/answer\n", 0o755
        )
        journey("record-task", "--task", task, "--status", "draft", "--source", "fake_agent scaffolded the draft")
        if args.draft_only or tool not in args.accept:
            question = f"Run the closure trials for {slug}?"
            journey("update", "--input", "-", stdin=json.dumps({"pending_question": question}))
            emit({"type": "stop", "status": "draft", "pending_question": question})
            return 0
        verification = close_gap(fields, workspace, draft, measurements, slug, tool)
        journey(
            "record-task",
            "--task",
            task,
            "--status",
            "accepted",
            "--source",
            "task_pipeline.py verify accepted two closure runs",
            "--evidence",
            verification.relative_to(workspace).as_posix(),
        )
    except (CommandFailed, KeyError, OSError, ValueError) as exc:
        emit({"type": "error", "error": str(exc)})
        try:
            blockers = json.loads(journey("show"))["state"]["blockers"]
            blockers.append({"id": f"task-create-{slug}", "phase": "expand", "message": str(exc)[:500]})
            journey("update", "--input", "-", stdin=json.dumps({"blockers": blockers}))
        except (CommandFailed, KeyError, ValueError):
            pass
        return 1
    emit({"type": "stop", "status": "accepted"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
