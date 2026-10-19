#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check normalized workflow evidence against reviewed request/turn contracts.

Offline only. Does not route natural language, execute tools, or infer permission.
A passing check is limited to supplied normalized evidence, not host acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

CONTRACTS = Path(__file__).resolve().parents[1] / "tests/fixtures/workflow-scope/contracts.json"
ACTIONS = {
    "read_skill",
    "read_repository",
    "inventory",
    "runtime_probe",
    "validate",
    "execute",
    "write_artifact",
    "read_trace",
    "propose",
    "scaffold",
    "delegate",
    "install",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"Invalid repository-relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"Invalid repository-relative path: {value!r}")
    return value


def snapshot(root: Path) -> dict:
    """Hash every entry, including dotfiles and modes, without following links."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Snapshot root must be a directory")
    entries, errors = {}, []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as listing:
                children = sorted(listing, key=lambda item: item.name)
        except OSError as exc:
            errors.append({"path": directory.relative_to(root).as_posix(), "errno": exc.errno})
            continue
        for child in children:
            path = Path(child.path)
            label = path.relative_to(root).as_posix()
            try:
                # DirEntry.stat reports zero inode/device on Windows; lstat
                # supplies identities comparable to the opened file descriptor.
                info = path.lstat()
                record = {"mode": stat.S_IMODE(info.st_mode)}
                if stat.S_ISLNK(info.st_mode):
                    record.update(kind="symlink", target=os.readlink(path))
                elif getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                    errors.append({"path": label, "reason": "unsupported-reparse-point"})
                    continue
                elif stat.S_ISDIR(info.st_mode):
                    record.update(kind="directory")
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    # O_NOFOLLOW prevents a leaf symlink swap between stat and read.
                    fd = os.open(
                        path,
                        os.O_RDONLY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_NONBLOCK", 0)
                        | getattr(os, "O_BINARY", 0),
                    )
                    with os.fdopen(fd, "rb") as source:
                        opened = os.fstat(source.fileno())
                        if not stat.S_ISREG(opened.st_mode):
                            errors.append({"path": label, "reason": "changed-file-type"})
                            continue
                        hasher = hashlib.sha256()
                        while chunk := source.read(1024 * 1024):
                            hasher.update(chunk)
                        after = os.fstat(source.fileno())
                    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ) or opened.st_mtime_ns != after.st_mtime_ns:
                        errors.append({"path": label, "reason": "changed-during-snapshot"})
                        continue
                    record.update(kind="file", sha256=hasher.hexdigest())
                else:
                    errors.append({"path": label, "reason": "unsupported-file-type"})
                    continue
                entries[label] = record
            except OSError as exc:
                errors.append({"path": label, "errno": exc.errno})
    return {
        "complete": not errors,
        "entries": dict(sorted(entries.items())),
        "errors": sorted(errors, key=lambda e: e["path"]),
    }


def permitted(path: str, rules: list[str], *, directory: bool = False) -> bool:
    relative_path(path)
    for rule in rules:
        target = rule.rstrip("/")
        relative_path(target)
        if path == target or (rule.endswith("/") and path.startswith(target + "/")):
            return True
        if directory and target.startswith(path + "/"):
            return True  # Ancestor directory creation only, never deletion or chmod.
    return False


def array(value: object, name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def object_value(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def check(contracts: dict, evidence: dict, raw: bytes) -> dict:
    """Check one capture with per-turn snapshots and complete raw-call accounting."""
    object_value(contracts, "contracts")
    object_value(evidence, "evidence")
    if type(contracts["schema_version"]) is not int or type(evidence["schema_version"]) is not int:
        raise ValueError("Schema version must be an integer")
    if contracts["schema_version"] != 1 or evidence["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    expected_digest = digest(json.dumps(contracts, sort_keys=True, separators=(",", ":")).encode())
    if evidence["contracts_sha256"] != expected_digest:
        raise ValueError("Contract digest mismatch")
    if evidence["evidence_kind"] not in {"synthetic", "captured"}:
        raise ValueError("Unknown evidence kind")
    expected = array(object_value(contracts["cases"], "cases")[evidence["case"]], "case turns")
    if not expected:
        raise ValueError("Empty contract case")
    for contract in expected:
        object_value(contract, "contract turn")
        for field in ("prompts", "allowed_actions", "required_actions", "write_paths"):
            array(contract[field], field)
        if not set(contract["required_actions"]) <= set(contract["allowed_actions"]) <= ACTIONS:
            raise ValueError("Invalid action policy")
        for rule in contract["write_paths"]:
            relative_path(rule.rstrip("/"))
    if evidence["raw_trace_sha256"] != digest(raw):
        raise ValueError("Raw trace digest mismatch")
    raw_calls = array(object_value(json.loads(raw), "raw trace")["tool_calls"], "tool_calls")
    for call in raw_calls:
        object_value(call, "raw call")
        if type(call["turn"]) is not int or call["turn"] < 0:
            raise ValueError("Raw call turn must be a nonnegative integer")
    raw_ids = [call["id"] for call in raw_calls]
    if any(not isinstance(value, str) or not value for value in raw_ids) or len(set(raw_ids)) != len(raw_ids):
        raise ValueError("Raw tool call IDs must be unique nonempty strings")
    raw_by_id = {call["id"]: call for call in raw_calls}
    violations, incomplete, referenced = [], [], []
    turns = array(evidence["turns"], "turns")
    if len(turns) != len(expected):
        incomplete.append("turn-count-mismatch")
    previous = None
    for index, (contract, turn) in enumerate(zip(expected, turns)):
        object_value(turn, "turn")
        prefix = f"turn[{index}]"
        if turn["prompt"] not in contract["prompts"] or turn["context"] != contract["context"]:
            violations.append(prefix + ": request/context differs from contract")
        if turn["flow"] != contract["flow"]:
            violations.append(prefix + ": wrong flow")
        if turn["handoff"] != contract["handoff"]:
            violations.append(prefix + ": wrong handoff")
        before, after = turn["before"], turn["after"]
        for state in (before, after):
            object_value(state, "snapshot")
            array(state["errors"], "snapshot errors")
            object_value(state["entries"], "snapshot entries")
            if type(state["complete"]) is not bool:
                raise ValueError("Snapshot complete must be boolean")
            if state["complete"] is not True or state["errors"]:
                incomplete.append(prefix + ": snapshot incomplete")
            for path, entry in state["entries"].items():
                relative_path(path)
                object_value(entry, "snapshot entry")
                if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o7777:
                    raise ValueError("Invalid snapshot mode")
                if entry["kind"] == "file" and not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                    raise ValueError("Invalid snapshot hash")
                if entry["kind"] == "symlink" and not isinstance(entry["target"], str):
                    raise ValueError("Invalid symlink target")
                if entry["kind"] not in {"file", "directory", "symlink"}:
                    raise ValueError("Unsupported snapshot entry")
        if previous is not None and before != previous:
            violations.append(prefix + ": discontinuous snapshot chain")
        previous = after
        seen = set()
        for event in array(turn["events"], "events"):
            object_value(event, "event")
            call_id, action = event["call_id"], event["action"]
            referenced.append(call_id)
            if (
                call_id not in raw_by_id
                or event["tool"] != raw_by_id[call_id]["tool"]
                or raw_by_id[call_id]["turn"] != index
            ):
                incomplete.append(prefix + ": raw tool identity mismatch")
            if not isinstance(event["tool"], str) or not event["tool"]:
                raise ValueError("Tool name required")
            array(event["write_paths"], "write_paths")
            if action not in ACTIONS:
                incomplete.append(prefix + ": unknown action " + str(action))
                continue
            seen.add(action)
            if action not in contract["allowed_actions"]:
                violations.append(prefix + ": forbidden action " + action)
            # Every normalized call supplies its complete destination list, including
            # temporary writes and failed attempts. Label correctness needs adapter review.
            for path in event["write_paths"]:
                relative_path(path)
                if not permitted(path, contract["write_paths"]):
                    violations.append(prefix + ": forbidden write attempt " + path)
                for parent in (PurePosixPath(path), *PurePosixPath(path).parents):
                    entry = before["entries"].get(parent.as_posix(), {})
                    if entry.get("kind") == "symlink":
                        violations.append(prefix + ": write through symlink " + path)
            if action in {"write_artifact", "scaffold"} and not event["write_paths"]:
                incomplete.append(prefix + ": write destinations missing")
        for missing in sorted(set(contract["required_actions"]) - seen):
            violations.append(prefix + ": required action missing " + missing)
        for path in sorted(set(before["entries"]) | set(after["entries"])):
            old, new = before["entries"].get(path), after["entries"].get(path)
            if old == new:
                continue
            ancestor_creation = old is None and new is not None and new["kind"] == "directory"
            if not permitted(path, contract["write_paths"], directory=ancestor_creation):
                violations.append(prefix + ": forbidden filesystem change " + path)
            if new is not None and new["kind"] == "symlink":
                violations.append(prefix + ": artifact symlink " + path)
    if referenced != raw_ids:
        incomplete.append("raw-call-coverage-mismatch")
    return {
        "schema_version": 1,
        "case": evidence["case"],
        "contracts_sha256": expected_digest,
        "raw_trace_sha256": digest(raw),
        "evidence_kind": evidence["evidence_kind"],
        "status": "fail" if violations else "incomplete" if incomplete else "pass",
        "violations": sorted(set(violations)),
        "incomplete": sorted(set(incomplete)),
        "evidence_scope": "supplied-normalized-evidence",
        "host_acceptance": "not_measured",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="Print a read-only tree manifest; save it outside the observed tree.")
    snap.add_argument("--root", type=Path, required=True)
    verify = sub.add_parser("check", help="Check a normalized capture against frozen contracts.")
    verify.add_argument("--contracts", type=Path, default=CONTRACTS)
    verify.add_argument("--evidence", type=Path, required=True)
    verify.add_argument("--raw-trace", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(args.root)
            code = 0 if result["complete"] else 2
        else:
            result = check(
                json.loads(args.contracts.read_text()),
                json.loads(args.evidence.read_text()),
                args.raw_trace.read_bytes(),
            )
            code = {"pass": 0, "fail": 1, "incomplete": 2}[result["status"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result, code = {"status": "incomplete", "error": str(exc)}, 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
