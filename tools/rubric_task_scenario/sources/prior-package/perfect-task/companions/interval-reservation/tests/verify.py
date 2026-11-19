# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

MAX_BYTES = 65536
GRADER = "mandatory-gated"


def reject_constant(value: str) -> None:
    raise ValueError("nonfinite_json")


def unique_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def read_regular(path: Path) -> bytes:
    # Walk every parent with no-follow; checking only the leaf accepts parent aliases.
    parts = path.absolute().parts
    fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in parts[1:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(child)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES or before.st_nlink != 1:
                raise ValueError("invalid_file")
            raw = os.read(child, MAX_BYTES + 1)
            after = os.fstat(child)
            if (
                len(raw) > MAX_BYTES
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(raw) != after.st_size
            ):
                raise ValueError("unstable_file")
            return raw
        finally:
            os.close(child)
    finally:
        os.close(fd)


def evaluate(case: dict, candidate: Any, *, grader: str = GRADER) -> dict:
    if grader not in {"mandatory-gated", "completion-only"}:
        raise ValueError("unknown_grader")
    try:
        expected = expected_choice(case)
    except (ValueError, TypeError, KeyError, OverflowError):
        return {
            "health": "error",
            "reason": "invalid_case",
            "checks": {},
            "raw_credit": None,
            "total_credit": None,
            "max_credit": 3,
            "accepted": None,
        }
    envelope = (
        type(candidate) is dict and set(candidate) == {"choice", "success"} and type(candidate["success"]) is bool
    )
    choice = candidate.get("choice") if envelope else object()
    well_formed = envelope and (choice is None or type(choice) is str and choice in {o["id"] for o in case["options"]})
    correct = well_formed and choice == expected
    completion = envelope and candidate["success"]
    checks = {
        "well_formed_choice": int(well_formed),
        "current_evidence_correct": int(correct),
        "optional_completion": int(completion),
    }
    raw = sum(checks.values())
    total = raw if well_formed and correct else 0
    if grader == "completion-only":
        total = 3 * int(completion)
    return {
        "health": "ok",
        "checks": checks,
        "raw_credit": raw,
        "total_credit": total,
        "max_credit": 3,
        "accepted": bool(well_formed and correct),
        "grader": grader,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=Path("/app/case.json"))
    parser.add_argument("--candidate", type=Path, default=Path("/work/candidate.json"))
    parser.add_argument("--output", type=Path, default=Path("/logs/verifier"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError("root_verifier_required")
    # As in the reviewed base, close the mount and replace all consumer-priority inodes.
    fd = os.open(args.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o755)
        for name in ("reward.json", "reward.txt", "checks.json"):
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
        result = grade_paths(args.case, args.candidate)
        for name, payload in [("checks.json", json.dumps(result, sort_keys=True) + "\n")]:
            target = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            with os.fdopen(target, "w") as handle:
                handle.write(payload)
        if result["health"] != "ok":
            return 2
        target = os.open("reward.txt", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        with os.fdopen(target, "w") as handle:
            handle.write(str(result["total_credit"] / result["max_credit"]) + "\n")
        return 0
    finally:
        os.close(fd)


def grade_paths(case_path: Path, candidate_path: Path) -> dict:
    candidate_hash = None
    try:
        raw_case = read_regular(case_path)
        case = json.loads(raw_case, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        try:
            raw = read_regular(candidate_path)
        except FileNotFoundError:
            candidate = None
        else:
            candidate_hash = hashlib.sha256(raw).hexdigest()
            try:
                candidate = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
            except (ValueError, UnicodeError, RecursionError):
                candidate = None
        result = evaluate(case, candidate)
        result.update(input_sha256=hashlib.sha256(raw_case).hexdigest(), candidate_sha256=candidate_hash)
        return result
    except (OSError, ValueError, TypeError, RecursionError):
        return {
            "health": "error",
            "reason": "unreadable_or_unsafe_input",
            "checks": {},
            "raw_credit": None,
            "total_credit": None,
            "max_credit": 3,
            "accepted": None,
            "candidate_sha256": candidate_hash,
        }


def validate_options(case: dict) -> list:
    if type(case) is not dict or type(case.get("id")) is not str:
        raise ValueError("invalid_case")
    options = case.get("options")
    if type(options) is not list or not 1 <= len(options) <= 32:
        raise ValueError("invalid_options")
    ids = [o["id"] for o in options]
    if any(type(i) is not str or not 1 <= len(i) <= 80 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid_ids")
    return options


def interval(value: Any) -> tuple:
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(v) is not int for v in value)
        or not -10000 <= value[0] < value[1] <= 10000
    ):
        raise ValueError("invalid_interval")
    return tuple(value)


def expected_choice(case: dict) -> object:
    options = validate_options(case)
    ranges = [interval(o["interval"]) for o in options]
    occupied = case["occupied"]
    if occupied is None:
        return None
    if type(occupied) is not list or len(occupied) > 128:
        raise ValueError("invalid_occupied")
    # Scorer uses occupied integer slots, not the reference's interval comparisons.
    slots = set()
    for value in occupied:
        slots.update(range(*interval(value)))
    eligible = [o["id"] for o, bounds in zip(options, ranges) if slots.isdisjoint(range(*bounds))]
    if len(eligible) != 1:
        raise ValueError("nonunique_answer")
    return eligible[0]


if __name__ == "__main__":
    raise SystemExit(main())
