# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

FIELDS = {"email_id", "inbox/outbox", "sender/recipient", "subject", "sent_datetime", "body"}
EXPECTED_REPLY = {
    "inbox/outbox": "outbox",
    "sender/recipient": "alex@example.test",
    "subject": "RE: Schedule confirmation",
    "sent_datetime": "2023-11-30 23:59:00",
    "body": "Confirmed for Tuesday at 10:00.",
}


class SnapshotError(ValueError):
    pass


def reject_constant(value: str) -> None:
    raise SnapshotError(f"Nonfinite JSON value: {value}")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SnapshotError(f"Nonfinite JSON number: {value}")
    return result


def parse_json(text: str) -> object:
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant, parse_float=finite_float)


def rows_from_snapshot(snapshot: object) -> list[dict[str, str]]:
    fields = {"access_mode", "initial_state_basis", "state_basis", "initial_state", "state", "closed"}
    if not isinstance(snapshot, dict) or set(snapshot) != fields:
        raise SnapshotError("Unrecognized state export schema.")
    if (
        snapshot["closed"] is not True
        or snapshot["access_mode"] != "stateful_simulation"
        or snapshot["initial_state_basis"] != "reconstructed"
        or snapshot["state_basis"] != "observed"
    ):
        raise SnapshotError("Export must be closed observed simulation state with reconstructed seed provenance.")
    validate_rows(snapshot["initial_state"])
    return validate_rows(snapshot["state"])


def validate_rows(snapshot: object) -> list[dict[str, str]]:
    if not isinstance(snapshot, list):
        raise SnapshotError("Email state must be an array.")
    rows: list[dict[str, str]] = []
    ids: set[str] = set()
    for row in snapshot:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise SnapshotError("Each email must contain exactly the six email fields.")
        if any(not isinstance(value, str) for value in row.values()):
            raise SnapshotError("Email fields must be strings.")
        if not row["email_id"] or row["email_id"] in ids:
            raise SnapshotError("Email IDs must be nonempty and unique.")
        ids.add(row["email_id"])
        rows.append(row)
    return rows


def grade(snapshot: object) -> int:
    rows = rows_from_snapshot(snapshot)
    # The runtime's initial_state is not grading authority.
    seed = validate_rows(parse_json(Path(__file__).with_name("seed.json").read_text()))
    originals = {row["email_id"]: row for row in seed}
    current = {row["email_id"]: row for row in rows}
    if any(current.get(email_id) != row for email_id, row in originals.items()):
        return 0
    additions = [row for row in rows if row["email_id"] not in originals]
    if len(additions) != 1:
        return 0
    reply = {key: value for key, value in additions[0].items() if key != "email_id"}
    return int(reply == EXPECTED_REPLY)


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade the collected Workplace email state.")
    parser.add_argument("--state", type=Path, default=Path("/export/state.json"))
    parser.add_argument("--reward-dir", type=Path, default=Path("/logs/verifier"))
    args = parser.parse_args()
    args.reward_dir.mkdir(parents=True, exist_ok=True)
    for name in ("reward.txt", "reward.json"):
        (args.reward_dir / name).unlink(missing_ok=True)
    try:
        reward = grade(parse_json(args.state.read_text()))
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Infrastructure failure: {exc}", file=sys.stderr)
        return 2
    (args.reward_dir / "reward.txt").write_text(f"{reward}\n")
    print(json.dumps({"reward": reward, "basis": "final_state"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
