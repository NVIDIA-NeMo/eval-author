# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inspect a paired native MCP read-after-write probe, not model performance."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from harbor.agents.factory import AgentFactory
from harbor.agents.installed.codex import Codex
from harbor.models.task.config import TaskConfig
from harbor.models.trial.config import AgentConfig
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent
MODES = ("exact_replay", "stateful_simulation")
SERVER = "workplace-stateful-simulation"
TOOLS = ("email_search_emails", "email_get_email_information_by_id", "email_reply_email")
BODY = "native-live-new-body"
REFERENCE_BODY = "Confirmed for Tuesday at 10:00."
REPLAY_ERROR = "No recorded fixture matched this request."
PROMPT = (
    f"Use only the {SERVER} MCP tools. Do not use shell or read local files. "
    f"Search email for {BODY}, reply to email ID 1 with body {BODY}, "
    f"then search for {BODY} again, even if the reply was rejected. "
    "Report whether the reply became visible and explain the observed result."
)


def wait_ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("service exited before readiness")
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("service readiness timed out")


@contextmanager
def service_at(output: Path) -> Iterator[str]:
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        str(ROOT / "runtime/service.py"),
        "--seed",
        str(ROOT / "task/seed.json"),
        "--state-dir",
        str(output / "state"),
        "--port",
        str(port),
        "--host",
        "127.0.0.1",
    ]
    with (output / "service.log").open("w") as log:
        service = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            wait_ready(url, service)
            yield url
        finally:
            service.terminate()
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)


def result_text(result: Any) -> str:
    return "\n".join(item.text for item in result.content if item.type == "text")


async def protocol(command: str, args: list[str], access_mode: str = "stateful_simulation") -> dict:
    async with stdio_client(StdioServerParameters(command=command, args=args)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            if names != sorted(TOOLS):
                raise ValueError("discovery mismatch")
            body = BODY if access_mode == "exact_replay" else "native-probe-new-body"
            before = await session.call_tool(TOOLS[0], {"query": body})
            written = await session.call_tool(TOOLS[2], {"email_id": "1", "body": body})
            after = await session.call_tool(TOOLS[0], {"query": body})
            unsupported = await session.call_tool("unsupported", {})
            replay = access_mode == "exact_replay"
            if before.isError or written.isError != replay or after.isError or not unsupported.isError:
                raise ValueError("unexpected MCP error classification")
            if json.loads(result_text(before)) != "No emails found.":
                raise ValueError("initial read mismatch")
            if replay:
                if result_text(written) != REPLAY_ERROR or result_text(after) != result_text(before):
                    raise ValueError("exact replay did not reject unseen write and repeat initial read")
            elif json.loads(result_text(written)) != "Email replied successfully." or body not in result_text(after):
                raise ValueError("read-after-write mismatch")
            return {
                "discovered": names,
                "unseen_write_read": not replay,
                "unseen_write_rejected": replay,
                "unsupported_rejected": True,
            }


async def capture_calibration(url: str, destination: Path) -> None:
    """Capture runtime outputs in an independent session, never corpus observations."""
    args = [str(ROOT / "runtime/mcp_server.py"), "--url", url]
    async with stdio_client(StdioServerParameters(command=sys.executable, args=args)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            definitions = (await session.list_tools()).tools
            schemas = [
                {"name": tool.name, "description": tool.description, "parameters": tool.inputSchema}
                for tool in definitions
            ]
            events = []
            for name, arguments in (
                (TOOLS[0], {"query": BODY}),
                (TOOLS[1], {"email_id": "1", "field": "body"}),
                (TOOLS[2], {"email_id": "1", "body": REFERENCE_BODY}),
            ):
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise ValueError("calibration service rejected known input")
                events.append(
                    {"name": name, "arguments": arguments, "output": json.loads(result_text(result)), "exception": None}
                )
            if [event["output"] for event in events] != [
                "No emails found.",
                {"body": "Please confirm Tuesday at 10:00."},
                "Email replied successfully.",
            ]:
                raise ValueError("calibration differs from initial seed and synthetic source reference")
            (destination / "schemas.json").write_text(json.dumps(schemas, indent=2) + "\n")
            development = destination / "development"
            development.mkdir()
            (development / "calibration.observations.json").write_text(
                json.dumps(
                    {
                        "split": "development",
                        "session_id": "development-runtime-calibration",
                        "events": events,
                    },
                    indent=2,
                )
                + "\n"
            )


def prepare_replay(output: Path) -> tuple[list[str], dict]:
    calibration = output / "calibration"
    with service_at(calibration) as url:
        asyncio.run(capture_calibration(url, calibration))
    spec = importlib.util.spec_from_file_location("harness_conformance_runner", ROOT / "conformance/runner.py")
    if spec is None or spec.loader is None:
        raise ImportError("conformance runner unavailable")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    scenario = calibration / "scenario.json"
    metadata = runner.compile_replay(calibration, scenario)
    metadata.update(
        {
            "basis": "runtime-generated calibration, not source-corpus conformance",
            "reference": "prepare_trace.synthetic_trace email ID1 reference reply",
            "reference_body": REFERENCE_BODY,
            "seed_sha256": hashlib.sha256((ROOT / "task/seed.json").read_bytes()).hexdigest(),
            "heldout_read": False,
        }
    )
    return [str(runner.SCRIPTS / "replay_mcp_server.py"), "--scenario", str(scenario)], metadata


def inspect_events(events: list[dict], access_mode: str) -> dict:
    items = [event["item"] for event in events if "item" in event]
    if any(item.get("type") == "command_execution" for item in items):
        raise ValueError("agent used a shell instead of only native tools")
    completed = [event["item"] for event in events if event.get("type") == "item.completed"]
    calls = [item for item in completed if item.get("type") == "mcp_tool_call"]
    started = [
        event["item"]["id"]
        for event in events
        if event.get("type") == "item.started" and event["item"].get("type") == "mcp_tool_call"
    ]
    if any(identifier not in {call.get("id") for call in calls} for identifier in started):
        raise ValueError("native MCP call has no completed event")
    # This is an explicitly requested three-call harness probe, not a task grader.
    expected = [(TOOLS[0], {"query": BODY}), (TOOLS[2], {"email_id": "1", "body": BODY}), (TOOLS[0], {"query": BODY})]
    if [(call.get("tool"), call.get("arguments")) for call in calls] != expected:
        raise ValueError("native MCP probe calls not observed")
    outputs = []
    replay = access_mode == "exact_replay"
    for index, call in enumerate(calls):
        if call.get("server") != SERVER:
            raise ValueError("native call used an unexpected server")
        rejected = replay and index == 1
        result = call.get("result")
        if rejected:
            # Codex may expose MCP isError as a failed item with an error message,
            # or retain the protocol error content in result.
            content = result.get("content", []) if isinstance(result, dict) else []
            text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            error = call.get("error")
            message = error.get("message", "") if isinstance(error, dict) else ""
            if call.get("status") not in {"completed", "failed"} or not (
                text == REPLAY_ERROR or (call.get("status") == "failed" and REPLAY_ERROR in message)
            ):
                raise ValueError("native replay rejection not observed")
            outputs.append(None)
            continue
        if call.get("status") != "completed" or call.get("error") is not None or not isinstance(result, dict):
            raise ValueError("native call failed")
        if result.get("isError") or result.get("is_error"):
            raise ValueError("native call returned an error")
        content = result.get("content", [])
        text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
        value = json.loads(text)
        structured = result.get("structured_content")
        if structured is not None and (structured.get("unsupported") is not False or structured.get("output") != value):
            raise ValueError("native structured result disagrees with content")
        outputs.append(value)
    if outputs[0] != "No emails found.":
        raise ValueError("native initial read differs")
    if replay:
        if outputs[2] != outputs[0]:
            raise ValueError("exact replay read changed after rejected write")
    else:
        rows = outputs[2].get("emails") if isinstance(outputs[2], dict) else None
        if (
            outputs[1] != "Email replied successfully."
            or not isinstance(rows, list)
            or len(rows) != 1
            or (rows[0].get("body") != BODY or rows[0].get("inbox/outbox") != "outbox")
        ):
            raise ValueError("native write not observed in subsequent read")
    return {
        "native_calls": len(calls),
        "shell_substitution": False,
        "read_after_write": not replay,
        "outcome_reason": "unseen_reply_rejected_exact_match_only" if replay else "reply_persisted_and_searchable",
    }


def live_codex(output: Path, auth: Path | None, model: str | None, config: dict, access_mode: str) -> dict:
    if auth is None or not auth.is_file():
        raise ValueError("live execution requires an existing Codex auth file")
    home = output / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, mode=0o700)
    private_auth = codex_home / "auth.json"
    try:
        shutil.copyfile(auth, private_auth)
        private_auth.chmod(0o600)
        import toml

        config["mcp_servers"][SERVER]["tools"] = {name: {"approval_mode": "approve"} for name in TOOLS}
        (codex_home / "config.toml").write_text(toml.dumps(config))
        work = output / "agent-work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("codex not installed")
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "TMPDIR": str(output),
            "TERM": "dumb",
        }
        version = subprocess.run(
            [executable, "--version"], env=env, capture_output=True, text=True, check=True, timeout=20
        ).stdout.strip()
        cmd = [
            executable,
            "exec",
            "--sandbox",
            "read-only",
            "--json",
            "--ephemeral",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(PROMPT)
        (output / "prompt.txt").write_text(PROMPT + "\n")
        result = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=240)
        (output / "codex.jsonl").write_text(result.stdout)
        (output / "codex.stderr").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"Codex failed with exit {result.returncode}")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        report = inspect_events(events, access_mode)
        report.update(
            {
                "model_requested": model,
                "model_selection": "explicit" if model else "CLI default, unverified",
                "cli_version": version,
                "shell_tools_disabled": True,
            }
        )
        return report
    finally:
        # Remove the entire isolated home, including refreshed credentials and caches.
        shutil.rmtree(home)


def run(
    output: Path, live: bool, auth: Path | None, model: str | None, access_mode: str = "stateful_simulation"
) -> dict:
    if access_mode not in MODES:
        raise ValueError("unsupported access mode")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    with service_at(output) as url:
        args = [str(ROOT / "runtime/mcp_server.py"), "--url", url]
        calibration = None
        if access_mode == "exact_replay":
            args, calibration = prepare_replay(output)
        server = TaskConfig.model_validate(
            {
                "environment": {
                    "mcp_servers": [
                        {
                            "name": SERVER,
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": args,
                        }
                    ]
                }
            }
        ).environment.mcp_servers[0]
        agent = AgentFactory.create_agent_from_config(
            AgentConfig(name="codex"), logs_dir=output / "registration", mcp_servers=[server]
        )
        if not isinstance(agent, Codex):
            raise TypeError("Harbor returned an unexpected agent")
        config = agent._build_effective_config()
        registered = config["mcp_servers"][server.name]
        report = {
            "access_mode": access_mode,
            "registration": "harbor_codex_writer",
            "protocol": asyncio.run(protocol(registered["command"], registered["args"], access_mode)),
            "live_agent": "unrun",
            "scope": "host process, not Harbor container qualification or model performance",
            "calibration": calibration,
            "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        }
        if live:
            report["live_tool_approval"] = "explicitly approved three synthetic tools in isolated config"
            report["live_agent"] = live_codex(output, auth, model, config, access_mode)
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        return report


def run_pair(before: Path, after: Path, live: bool, auth: Path | None, model: str | None) -> dict:
    before, after = before.resolve(), after.resolve()
    if before == after or before in after.parents or after in before.parents:
        raise ValueError("paired outputs must be independent new directories")
    if before.exists() or after.exists():
        raise FileExistsError("paired outputs must both be new")
    reports = [run(path, live, auth, model, mode) for path, mode in zip((before, after), MODES, strict=True)]
    field = "live_agent" if live else "protocol"
    flag = "read_after_write" if live else "unseen_write_read"
    observed = [report[field][flag] for report in reports]
    if observed != [False, True] or reports[0]["prompt_sha256"] != reports[1]["prompt_sha256"]:
        raise ValueError("paired read-after-write differential not observed")
    report = {
        "scope": "single same-prompt outcome inspection, not model performance or source-corpus conformance",
        "evidence": "live native model calls" if live else "credential-free protocol only",
        "before": str(before),
        "after": str(after),
        "read_after_write": dict(zip(MODES, observed, strict=True)),
        "same_prompt": True,
        "outcome_reason": "exact replay rejects the unseen body; simulation persists it",
    }
    (after / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new output directory; replay output in paired mode")
    parser.add_argument("--paired-output", type=Path, help="new simulation output directory for a paired run")
    parser.add_argument("--access-mode", choices=MODES, default="stateful_simulation")
    parser.add_argument("--live-codex", action="store_true")
    parser.add_argument("--auth", type=Path)
    parser.add_argument("--model")
    args = parser.parse_args()
    if args.paired_output:
        report = run_pair(args.output, args.paired_output, args.live_codex, args.auth, args.model)
    else:
        report = run(args.output, args.live_codex, args.auth, args.model, args.access_mode)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
