#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Account for every reviewed workflow case, host, install form and repeat.

Native captures are retained separately from reviewed action/route labels. Unknown
native events make evidence incomplete. Passing this gate requires both capture
integrity and scope checks; it does not measure provider execution or prose quality.
"""

from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

from check_workflow_scope import CONTRACTS, check, snapshot
from discovery_determinism import encoded, read, sha

SCHEMA = "nemo.workflow_matrix.v1"
HOSTS = ("codex", "claude-code", "cursor", "pi")
FORMS = ("checkout", "copied")
PLATFORMS = ("darwin", "linux", "win32")


def plan(contracts: dict, repeats: int = 2) -> dict:
    if type(repeats) is not int or repeats < 2:
        raise ValueError("at least two repeats are required")
    cells = []
    # Every bounded paraphrase is a separate cell. Multi-turn variants use aligned
    # variants, repeating the last prompt for a turn with fewer variants.
    for host, form, platform, (case, turns) in product(HOSTS, FORMS, PLATFORMS, contracts["cases"].items()):
        for variant in range(max(len(turn["prompts"]) for turn in turns)):
            for repeat in range(repeats):
                cells.append(
                    {
                        "id": f"{host}/{form}/{platform}/{case}/{variant}/{repeat}",
                        "host": host,
                        "installation": form,
                        "platform": platform,
                        "case": case,
                        "repeat": repeat,
                        "prompts": [turn["prompts"][min(variant, len(turn["prompts"]) - 1)] for turn in turns],
                    }
                )
    return {"schema": SCHEMA, "contracts_sha256": sha(encoded(contracts)), "repeats": repeats, "cells": cells}


def native_calls(host: str, raw: bytes, turn: int) -> tuple[list, list]:
    """Versioned JSONL adapters. Unknown event/item types fail closed, including new tools."""
    calls, errors = [], []
    finished = False
    pending = set()
    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("native event must be an object")
        kind = event.get("type")
        if host == "codex":
            if kind in {"thread.started", "turn.started"}:
                continue
            if kind in {"item.started", "item.updated"}:
                pending.add(event["item"]["id"])
                continue
            if kind == "turn.completed":
                finished = True
                continue
            if kind == "item.completed":
                item = event["item"]
                pending.discard(item["id"])
                item_type = item.get("type")
                if item_type in {"agent_message", "reasoning", "todo_list"}:
                    continue
                if item_type in {"command_execution", "file_change", "mcp_tool_call", "web_search"}:
                    calls.append({"id": f"{turn}:{item['id']}", "tool": item_type, "turn": turn, "native": item})
                    continue
        elif host == "claude-code":
            if kind in {"system", "user", "rate_limit_event"}:
                continue
            if kind == "result":
                if event.get("is_error"):
                    errors.append("host returned an error")
                else:
                    finished = True
                continue
            if kind == "assistant":
                for item in event["message"]["content"]:
                    if item.get("type") == "tool_use":
                        calls.append({"id": f"{turn}:{item['id']}", "tool": item["name"], "turn": turn, "native": item})
                    elif item.get("type") not in {"text", "thinking"}:
                        errors.append(f"unknown assistant content at line {index}")
                continue
        errors.append(f"unsupported native event {kind!r} at line {index}")
    if pending:
        errors.append("native items did not complete")
    if not finished:
        errors.append("native turn did not complete")
    return calls, errors


def evaluate(contracts: dict, attempt: dict) -> dict:
    """Re-read every retained artifact; a review is bound to its actual capture."""
    errors = []
    for field in ("host_version", "model", "source_revision", "fixture_sha256", "settings"):
        if not attempt.get(field):
            errors.append(f"missing {field}")
    installed = snapshot(Path(attempt["skills_root"]))
    if not installed["complete"] or sha(encoded(installed)) != attempt["skills_sha256"]:
        errors.append("loaded skill installation changed or incomplete")
    review = read(Path(attempt["review"]))
    native, raw_digests = [], []
    for turn, entry in enumerate(attempt["raw_turns"]):
        raw = Path(entry["path"]).read_bytes()
        raw_digests.append(sha(raw))
        if sha(raw) != entry["sha256"]:
            errors.append("native capture changed")
        calls, unknown = native_calls(attempt["host"], raw, turn)
        native.extend(calls)
        errors.extend(unknown)
    # A reviewer supplies labels, never a model's assertion that it obeyed scope.
    if (
        review.get("raw_sha256") != raw_digests
        or not review.get("reviewed_by")
        or review.get("skills_sha256") != attempt["skills_sha256"]
    ):
        errors.append("missing or stale native action and handoff review")
    captures = attempt.get("captures", [])
    if len(captures) != len(attempt["raw_turns"]) or not captures:
        errors.append("every native turn requires its retained capture record")
    for turn, (entry, _) in enumerate(zip(captures, attempt["raw_turns"])):
        capture_raw = Path(entry["path"]).read_bytes()
        captured = json.loads(capture_raw)
        if sha(capture_raw) != entry["sha256"]:
            errors.append("capture record changed")
        if type(captured.get("returncode")) is not int or captured["returncode"] != 0:
            errors.append("native host process did not succeed")
        if captured.get("reason") != "awaiting_native_review" or captured.get("skills_unchanged") is not True:
            errors.append("capture incomplete or skills changed during execution")
        if (
            captured.get("turn") != turn
            or captured.get("prompt") != attempt["prompts"][turn]
            or captured.get("raw") != attempt["raw_turns"][turn]
            or any(attempt.get(key) != value for key, value in captured["cell"].items())
        ):
            errors.append("capture differs from planned turn or native stream")
        for field in ("host_version", "model", "settings", "skills_root", "skills_sha256"):
            if captured.get(field) != attempt.get(field):
                errors.append("capture provenance differs: " + field)
        if turn == 0 and sha(encoded(captured["before"])) != attempt["fixture_sha256"]:
            errors.append("fixture identity differs from captured starting state")
        reviewed_turn = review["evidence"]["turns"][turn]
        if any(captured.get(field) != reviewed_turn.get(field) for field in ("before", "after")):
            errors.append("reviewed snapshots differ from capture")
    evidence = review["evidence"]
    if evidence["evidence_kind"] != "captured":
        errors.append("synthetic evidence cannot establish host acceptance")
    raw_envelope = encoded({"tool_calls": native})
    result = check(contracts, evidence, raw_envelope)
    if [turn["prompt"] for turn in evidence["turns"]] != attempt["prompts"]:
        errors.append("captured prompts differ from planned prompts")
    if evidence["case"] != attempt["case"]:
        errors.append("captured case differs from planned case")
    status = result["status"]
    if errors and status != "fail":
        status = "incomplete"
    return {
        "status": status,
        "workflow": result,
        "incomplete": errors,
        "provider_execution": "not_measured",
        "model_variance": "not_measured",
    }


def summarize(matrix: dict, contracts: dict, attempts: list[dict]) -> dict:
    if matrix.get("schema") != SCHEMA or matrix["contracts_sha256"] != sha(encoded(contracts)):
        raise ValueError("matrix contract binding changed")
    if matrix != plan(contracts, matrix["repeats"]):
        raise ValueError("planned matrix was edited; missing cells cannot be removed")
    cells = matrix["cells"]
    if not cells or len({cell["id"] for cell in cells}) != len(cells):
        raise ValueError("matrix must contain distinct cells")
    by_id = {cell["id"]: cell for cell in cells}
    recorded = {}
    raw_identities = set()
    attempt_ids = set()
    for attempt in attempts:
        identifier = attempt["id"]
        attempt_id = attempt.get("attempt_id", identifier)
        if identifier not in by_id or attempt_id in attempt_ids:
            raise ValueError("unknown or duplicate attempt identity")
        attempt_ids.add(attempt_id)
        cell = by_id[identifier]
        if any(attempt.get(key) != value for key, value in cell.items()):
            raise ValueError("attempt identity differs from plan")
        if attempt.get("skip_reason"):
            row = {"status": "skipped", "reason": attempt["skip_reason"]}
        else:
            try:
                identities = [sha(Path(entry["path"]).read_bytes()) for entry in attempt["raw_turns"]]
                if any(identity in raw_identities for identity in identities) or len(set(identities)) != len(
                    identities
                ):
                    raise ValueError("native capture reused across attempts or turns")
                raw_identities.update(identities)
                row = evaluate(contracts, attempt)
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                row = {"status": "incomplete", "reason": str(exc)}
        recorded.setdefault(identifier, []).append({"attempt_id": attempt_id, **row})
    counts = {"pass": 0, "fail": 0, "incomplete": 0, "skipped": 0}
    rows = []
    for cell in cells:
        trials = recorded.get(cell["id"], [])
        priority = {"fail": 0, "incomplete": 1, "skipped": 2, "pass": 3}
        row = (
            min(trials, key=lambda item: priority[item["status"]])
            if trials
            else {"status": "incomplete", "reason": "not_attempted"}
        )
        counts[row["status"]] += 1
        rows.append({"id": cell["id"], **row, "attempts": trials})
    return {
        "schema": SCHEMA,
        "planned": len(cells),
        "attempted": sum(not attempt.get("skip_reason") for attempt in attempts),
        "counts": counts,
        "observations": rows,
        "gate": "advisory",
        "passed": counts["pass"] == len(cells),
        "cross_host_equivalence": "not_established",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", type=Path, default=CONTRACTS)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--attempt", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        contracts = read(args.contracts)
        result = (
            summarize(read(args.matrix), contracts, [read(p) for p in args.attempt]) if args.matrix else plan(contracts)
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if args.matrix is None or result["passed"] else 1 if result["counts"]["fail"] else 2
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({"status": "incomplete", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
