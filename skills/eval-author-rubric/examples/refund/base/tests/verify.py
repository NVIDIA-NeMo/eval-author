# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import fcntl
import json
import os
import shutil
import stat
import sys
from pathlib import Path

# Native -I excludes the script directory. Harbor protects this helper directory.
sys.path.append(str(Path(__file__).resolve().parent))
from refund_scorer import _apply_events, _unique_pairs, evaluate, load_contract  # noqa: E402

GRADER = "reviewed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    # Harbor makes log mounts writable at startup and leaves solver children alive.
    # Keep the mount closed through consumption, and discard old inodes (open FDs
    # retain access after chmod). The native /logs parent is root-owned and 0755.
    output_fd = os.open(args.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.geteuid() == 0:
            os.fchown(output_fd, 0, 0)
        if os.fstat(output_fd).st_uid != os.geteuid():
            raise PermissionError("output_owner")
        os.fchmod(output_fd, 0o755)
        for name in ("reward.json", "reward.txt", "checks.json"):
            try:
                info = os.stat(name, dir_fd=output_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(name, dir_fd=output_fd)
            else:
                os.unlink(name, dir_fd=output_fd)
    finally:
        os.close(output_fd)
    reward = args.output / "reward.txt"
    checks = args.output / "checks.json"
    try:
        source = args.record or args.submission
        if source is None:
            raise ValueError("missing_input")
        if args.record:
            # This channel is inside the verifier-owned directory, never the public socket.
            with source.with_suffix(".lock").open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                marker = os.open(source.with_suffix(".closed"), os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(marker)
            # Keep the marker after scoring and on errors. Only a fresh record can reopen admission.
        info = source.lstat()
        limit = 4194304 if args.record else 1048576
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid_submission_file")
        value = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        candidate = value["candidate"] if args.record else value
        contract = load_contract(args.contract)
        result = evaluate(contract, candidate, grader=GRADER)
        if args.record:
            if set(value) != {"candidate", "state"} or result["health"] != "ok":
                raise ValueError("invalid_record")
            state, _ = _apply_events(contract["sources"]["initial_state"], candidate["events"])
            if json.dumps(state, sort_keys=True) != json.dumps(value["state"], sort_keys=True):
                raise ValueError("state_disagreement")
    except (OSError, ValueError, RecursionError, TypeError, KeyError):
        result = {
            "health": "error",
            "reason": "invalid_submission",
            "checks": {},
            "raw_credit": None,
            "max_credit": None,
            "total_credit": None,
            "accepted": None,
        }
    checks.write_text(json.dumps(result, sort_keys=True) + "\n")
    if result["health"] != "ok":
        return 2
    total, maximum = result["total_credit"], result["max_credit"]
    assert isinstance(total, int) and isinstance(maximum, int)
    reward.write_text(str(total / maximum) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
