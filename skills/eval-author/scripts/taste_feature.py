# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Control the optional Eval Author TASTE route for one private run directory."""

import argparse
import json
from pathlib import Path

from working_paths import atomic_write_json

SCHEMA = "nemo.eval_author.taste_feature.v1"


def status(run_dir: Path) -> dict:
    run_dir = run_dir.resolve()
    path = run_dir / "integration.json"
    if not path.exists():
        return {"schema": SCHEMA, "run_dir": str(run_dir), "enabled": False, "approval": None}
    value = json.loads(path.read_text())
    if (
        value.get("schema") != SCHEMA
        or value.get("run_dir") != str(run_dir)
        or type(value.get("enabled")) is not bool
        or not isinstance(value.get("approval"), str)
        or not value["approval"].strip()
    ):
        raise ValueError("TASTE integration state is malformed or belongs to a different run")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "disable", "status"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--approval", help="User instruction explicitly approving TASTE for this run")
    args = parser.parse_args(argv)
    try:
        value = status(args.run_dir)
        if args.action == "enable":
            if not args.approval or not args.approval.strip():
                raise ValueError("Enabling TASTE requires --approval with the user's explicit instruction")
            value.update(enabled=True, approval=args.approval.strip())
        elif args.action == "disable":
            value["enabled"] = False
        if args.action != "status" and value["approval"] is not None:
            args.run_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(args.run_dir / "integration.json", value)
        print(json.dumps(value))
        return 0
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
