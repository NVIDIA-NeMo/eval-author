# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

from trace_ingest import Span, SpanKind, ToolCall, ToolDefinition, ToolResult, Trace, TraceAggregate, Turn, TurnRole

SCRIPTS = Path(__file__).resolve().parents[3] / "eval-author" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from eval_author_shared import construction, safe_trace, trace_view, trace_workspace  # noqa: E402
from eval_author_shared.runtime_assets import ASSETS  # noqa: E402


class AccessMode(StrEnum):
    REAL = "real"
    EXACT_REPLAY = "exact_replay"
    STATEFUL_SIMULATION = "stateful_simulation"
    NONE = "none"


REVIEW = {
    "reviewer_kind": "agent",
    "note": "Reviewed independently authored synthetic .test email data. No human or historical-source review claimed.",
}
INSTRUCTION = "Find the incoming schedule confirmation, read its body, and reply confirming the requested time."


def access_contract(value: str) -> dict[str, Any]:
    mode = AccessMode(value)
    return {
        "selected": mode.value,
        "real": {"available": False, "reason": "No Workplace credentials supplied. No live access attempted."},
        "exact_replay": {
            "production_access": "mock",
            "implementation": "production fixture compiler and MCP replay runtime",
            "unknown_call_policy": "error",
        },
        "stateful_simulation": {
            "opt_in": mode == AccessMode.STATEFUL_SIMULATION,
            "production_access": None,
            "reason": "Separate prototype mode, not recognized by the production tool-access contract.",
        },
        "none": {"production_access": "none"},
        "execution": "not_run",
        "product_task_acceptance": "unmeasured",
    }


def synthetic_trace() -> Trace:
    # Observations are authored independently of the simulation implementation.
    # Contact and date fields are outside this bounded observation projection.
    # The production scrubber redacts them and refuses replay of those values.
    # The seed supplies explicitly authored .test contact and timestamp values.
    email = {
        "email_id": "1",
        "inbox/outbox": "inbox",
        "subject": "Schedule confirmation",
        "body": "Please confirm Tuesday at 10:00.",
    }
    calls = [
        (
            "email_search_emails",
            {"query": "schedule"},
            {"emails": [email], "pagination": {"total_emails": 1, "page": 1, "page_size": 5, "total_pages": 1}},
            {"query": "string", "date_min": "string", "date_max": "string", "page": "integer", "page_size": "integer"},
            [],
        ),
        (
            "email_get_email_information_by_id",
            {"email_id": "1", "field": "body"},
            {"body": "Please confirm Tuesday at 10:00."},
            {"email_id": "string", "field": "string"},
            ["email_id", "field"],
        ),
        (
            "email_reply_email",
            {"email_id": "1", "body": "Confirmed for Tuesday at 10:00."},
            "Email replied successfully.",
            {"email_id": "string", "body": "string"},
            ["email_id", "body"],
        ),
    ]
    turns = [Turn(id="workplace/turn/1", index=1, role=TurnRole.USER, content=INSTRUCTION)]
    spans = []
    definitions = []
    for index, (name, arguments, output, properties, required) in enumerate(calls, 2):
        definition = {
            "name": name,
            "description": "Independently authored synthetic Workplace email observation.",
            "inputSchema": {
                "type": "object",
                "properties": {key: {"type": kind} for key, kind in properties.items()},
                "required": required,
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": name != "email_reply_email"},
        }
        definitions.append(
            ToolDefinition(
                name=name, description=definition["description"], input_schema=definition["inputSchema"], raw=definition
            )
        )
        span = Span(
            id=f"workplace/tool/{index}",
            kind=SpanKind.TOOL,
            tool_name=name,
            input=arguments,
            tool_call=ToolCall(
                call_id=f"synthetic-call-{index}", results=[ToolResult(content=json.dumps(output), is_error=False)]
            ),
        )
        spans.append(span)
        turns.append(Turn(id=f"workplace/turn/{index}", index=index, role=TurnRole.AGENT, tool_call_span_ids=[span.id]))
    return Trace(
        id="synthetic-workplace-email",
        aggregate=TraceAggregate(),
        root_spans=[
            Span(id="workplace", kind=SpanKind.AGENT, turns=turns, children=spans, tool_definitions=definitions)
        ],
    )


def command(*argv: str) -> dict[str, Any]:
    args = trace_workspace._parser().parse_args(list(argv))
    return args.run(args)


def write_json(path: Path, value: Any) -> None:
    trace_workspace._mkdir_private(path.parent)
    trace_workspace._write_bytes_once(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def retain_construction(workspace: Path, trace: Trace, task: Path) -> dict[str, Any]:
    turns = list(trace_view.iter_turns(trace))
    instruction = turns[0][1].content
    if not isinstance(instruction, str):
        raise ValueError("Synthetic instruction must be text.")
    inventory = json.loads((workspace / "private/tool-call-inventory.json").read_text())
    search = next(tool for tool in inventory["tools"] if tool["name"] == "email_search_emails")
    projection = json.loads(search["calls"][0]["results"][0]["content"])["emails"]
    seed_path = task / "environment/workplace/seed.json"
    seed_bytes = seed_path.read_bytes()
    if seed_bytes != (task / "tests/seed.json").read_bytes():
        raise ValueError("Task service and verifier seeds differ.")
    seed = json.loads(seed_bytes)
    for observed in projection:
        matching = [row for row in seed if row["email_id"] == observed["email_id"]]
        if len(matching) != 1 or any(matching[0].get(key) != value for key, value in observed.items()):
            raise ValueError("Task seed contradicts the synthetic trace projection.")
    if any(not row["sender/recipient"].endswith(".test") for row in seed):
        raise ValueError("Agent review is limited to independently authored .test seed data.")
    trace_workspace._write_bytes_once(workspace / "task/environment/workplace/seed.json", seed_bytes)
    candidate = {
        "schema": trace_workspace.CANDIDATE_SCHEMA,
        "status": "candidate",
        "decision_basis": "safe_trace_only",
        "state_basis": "reconstructed",
        "instruction": instruction,
        "requirements": [{"description": instruction, "evidence_turns": [turns[0][1].id]}],
        "verification_mode": "execution",
        "evidence_turns": [turn.id for _, turn in turns],
        "uncertainties": [
            "Synthetic source observations are independently authored, not a historical corpus.",
            "The matching email ID, inbox, subject and body match the synthetic observation projection. All contacts, timestamps and the two additional rows are authored reconstruction, not observed history.",
            "The replay baseline and provenance do not establish Harbor product-task acceptance.",
        ],
        "reason_codes": [],
        "software_requirements": [],
        "ground_truth": {
            "availability": "absent",
            "use": "none",
            "artifacts": [],
            "absence_reason": "The synthetic reply is a source demonstration, not independent grading authority.",
        },
    }
    write_json(workspace / "candidate.json", candidate)
    command("check-candidate", "--task-dir", str(workspace))
    probes = []
    for kind in ("setup", "reach"):
        probes.append(
            {
                "id": kind,
                "kind": kind,
                "argv": [
                    "python3",
                    "-c",
                    "import json; from pathlib import Path; p=Path('/opt/tool-call-fixtures/mcp-scenario.json'); print(json.dumps({'ready': p.is_file()}))",
                ],
                "cwd": "/",
                "timeout_seconds": 5,
                "json_pointer": "/ready",
                "equals": True,
                "assertion_path": "task/tests/qualification-probes.json",
            }
        )
    write_json(workspace / "task/tests/qualification-probes.json", probes)
    trace_workspace._write_bytes_once(
        workspace / "task/tests/qualification_runtime.py", (ASSETS / "qualification_runtime.py").read_bytes()
    )
    inputs = []
    for root in ("task/environment/workplace", "task/environment/tool-call-fixtures"):
        for path in sorted((workspace / root).rglob("*")):
            if path.is_file():
                inputs.append(
                    {
                        "path": path.relative_to(workspace / "task").as_posix(),
                        "sha256": construction.trace_format.file_digest(
                            workspace, path.relative_to(workspace).as_posix()
                        ),
                        "basis": "authored",
                        "source": None,
                        "edits": [],
                        "review": REVIEW,
                        "rationale": "Reconstructed from independently authored synthetic observations. Not observed historical initial state.",
                    }
                )
    cases = []
    for kind, expected, rationale in (
        ("nop", "FAIL", "No reply cannot satisfy the requested confirmation."),
        ("oracle", "PASS", "A correct confirmation is the reference outcome, not a measured result."),
        ("negative", "FAIL", "Replying with the wrong time must fail."),
        (
            "alternative",
            "PASS",
            "A semantically equivalent confirmation should pass in the separate simulation, not strict replay.",
        ),
    ):
        cases.append(
            {
                "case": kind,
                "kind": kind,
                "requirements": ["confirm-schedule"],
                "observations": [{"check": "reply", "expected": {"pointer": "/checks/reply", "equals": expected}}],
                "reaches": ["reach"],
                "dependency": None,
                "rationale": rationale,
                "review": REVIEW,
            }
        )
    document = {
        "schema": construction.SCHEMA,
        "task_id": workspace.name,
        "provider": "harbor",
        "requirements": [
            {
                "id": "confirm-schedule",
                "candidate_index": 0,
                "statement": instruction,
                "authority": {
                    "basis": "specification",
                    "reference": "Synthetic user instruction in safe/trace.json, first turn.",
                },
            }
        ],
        "inputs": inputs,
        "checks": [
            {
                "id": "reply",
                "requirements": ["confirm-schedule"],
                "success": {"pointer": "/checks/reply", "equals": "PASS"},
            }
        ],
        "cases": cases,
        "prerequisites": [],
        "probes": probes,
        "starting_state": {
            "cutoff_turn_id": turns[0][1].id,
            "roots": ["task/environment/workplace", "task/environment/tool-call-fixtures"],
            "external_inputs": [
                {
                    "description": str(task / relative)
                    + ". Exact task seed, authored reconstruction. Matching row ID1 subject/body come from the synthetic projection. Contact/time and rows ID2/ID3 are authored additions.",
                    "identity": "sha256:"
                    + construction.trace_format.file_digest(workspace, "task/environment/workplace/seed.json"),
                    "review": REVIEW,
                }
                for relative in ("environment/workplace/seed.json", "tests/seed.json")
            ],
            "no_files_reason": None,
            "review": REVIEW,
        },
        "review": REVIEW,
        "migration": [],
        "bindings": {
            "candidate_sha256": construction.trace_format.file_digest(workspace, "candidate.json"),
            "trace_sha256": construction.trace_format.file_digest(workspace, "safe/trace.json"),
        },
    }
    write_json(workspace / construction.PATH, document)
    return validate_task_binding(workspace, task)


def validate_task_binding(workspace: Path, task: Path) -> dict[str, Any]:
    document = json.loads((workspace / construction.PATH).read_text())
    seed_input = next(row for row in document["inputs"] if row["path"] == "environment/workplace/seed.json")
    for relative in ("environment/workplace/seed.json", "tests/seed.json"):
        if construction.trace_format.file_digest(task, relative) != seed_input["sha256"]:
            raise ValueError("Task seed differs from the construction-bound initial state.")
    return construction.assess(document, mode="proposal", workspace=workspace)


def prepare(output: Path, access_mode: str) -> dict[str, Any]:
    modes = access_contract(access_mode)
    if access_mode == AccessMode.REAL:
        raise ValueError(modes["real"]["reason"])
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output must not exist: {output}")
    if any(path.is_symlink() for path in output.parents):
        raise ValueError("Output parents must not be symlinks.")
    trace_workspace._mkdir_private(output)
    trace_workspace._write_bytes_once(output / ".gitignore", b"*\n")
    trace_workspace._mkdir_private(output / "private")
    root = output / "private/.eval-author/.working/trace-environments"
    workspace = Path(command("init", "--root", str(root), "--task-id", "workplace")["task_dir"])
    source = output / "private/synthetic-source.trace.json"
    trace_workspace._write_bytes_once(source, synthetic_trace().model_dump_json().encode())
    command("prepare", "--task-dir", str(workspace), "--source", str(source), "--source-kind", "trace")
    command("review-privacy", "--task-dir", str(workspace), "--reviewer-kind", "agent", "--note", REVIEW["note"])
    command("inventory-tool-calls", "--task-dir", str(workspace))
    command("plan-tool-call-access", "--task-dir", str(workspace))
    inventory = json.loads((workspace / "private/tool-call-inventory.json").read_text())
    decisions = {
        "schema": "nemo.eval_author.trace_environment_tool_access_decisions.v1",
        "decisions": [
            {
                "tool_id": tool["tool_id"],
                "name": tool["name"],
                "access": "none" if access_mode == AccessMode.NONE else "mock",
                "adapter": None if access_mode == AccessMode.NONE else "mcp",
                "note": "No tool access selected."
                if access_mode == AccessMode.NONE
                else "Exact replay baseline only. This does not select stateful simulation.",
            }
            for tool in inventory["tools"]
        ],
    }
    request = workspace / "private/requested-tool-access.json"
    write_json(request, decisions)
    command(
        "resolve-tool-call-access",
        "--task-dir",
        str(workspace),
        "--decisions",
        str(request),
        "--reviewer-kind",
        "agent",
    )
    replay_baseline = access_mode != AccessMode.NONE
    if replay_baseline:
        command("generate-mock-tool-calls", "--task-dir", str(workspace))
    task = None
    assessment = None
    if access_mode == AccessMode.STATEFUL_SIMULATION:
        task = output / "task"
        built = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "task/build.py"),
                "--output",
                str(task),
                "--control",
                "reference",
                "--network-mode",
                "no-network",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode != 0:
            raise ValueError(f"Task builder failed: {built.stderr}")
        trace = safe_trace.parse_trace((workspace / "safe/trace.json").read_bytes())
        assessment = retain_construction(workspace, trace, task)
    task_mode = "stateful_simulation" if task is not None else "none"
    modes.update(
        {
            "source_kind": "independently_authored_synthetic_compass_trace",
            "state_basis": "reconstructed",
            "observed_historical_state": False,
            "baseline_workspace": str(workspace.relative_to(output)),
            "construction": "private/construction.json" if assessment is not None else None,
            "construction_assessment": assessment,
            "task_mode": task_mode,
            "replay_baseline": replay_baseline,
            "agent_visible_trace": False,
            "baseline_visibility": "Private evidence only. Do not copy the replay fixture or source trace into the simulation agent image; the recorded reply arguments disclose the demonstration answer.",
            "simulation_task": {
                "path": "task",
                "access_mode": "stateful_simulation",
                "execution": "not_run",
            }
            if task is not None
            else None,
        }
    )
    write_json(output / "private/access-mode.json", modes)
    return {
        "output": str(output),
        "workspace": str(workspace),
        "task": str(task) if task is not None else None,
        "task_mode": task_mode,
        "replay_baseline": replay_baseline,
        "access_mode": access_mode,
        "state_basis": "reconstructed",
        "construction_status": assessment["status"] if assessment is not None else "not_attempted",
        "product_task_acceptance": "unmeasured",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a trace-derived Harbor task with private synthetic Compass evidence and an exact-replay baseline."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--access-mode", choices=[mode.value for mode in AccessMode], required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.output, args.access_mode), sort_keys=True))
    except (OSError, ValueError) as error:
        parser.exit(2, f"Preparation failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
