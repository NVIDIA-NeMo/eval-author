# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Materialize and execute the explicitly fictional task scenario offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent


def materialize(output: Path) -> Path:
    mapping = json.loads((PACKAGE / "source-map.json").read_bytes())["files"]
    captured: dict[str, bytes] = {}
    for destination, entry in mapping.items():
        relative = Path(destination)
        source = ROOT / entry["source"]
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid bundled destination")
        if not source.resolve().is_relative_to(ROOT):
            raise ValueError("source escapes checkout")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"bundled source identity mismatch: {destination}")
        captured[destination] = raw
    output.mkdir(mode=0o700)
    source_root = output / "source"
    source_root.mkdir(mode=0o700)
    identities = {}
    for destination, raw in captured.items():
        path = source_root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        mode = 0o755 if mapping[destination].get("executable", False) else 0o644
        path.chmod(mode)
        identities[destination] = {"sha256": hashlib.sha256(raw).hexdigest(), "mode": mode}
    (source_root / "bundled-source-identity.json").write_text(
        json.dumps({"files": identities}, sort_keys=True) + "\n", encoding="utf-8"
    )
    return source_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new private directory outside the checkout")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("output must be outside the checkout")
    if not output.parent.is_dir():
        parser.error("output parent must already exist")
    if sys.flags.optimize:
        parser.error("optimized Python disables scenario assertions")
    os.umask(0o077)
    source_root = materialize(output)
    execution = source_root / "execution"
    command = [sys.executable, "-I", "-S", "-B", str(source_root / "run.py"), str(execution)]
    with (output / "execution.log").open("x") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env={"PATH": os.defpath}, timeout=180)
    if result.returncode:
        print(f"Scenario failed; retained diagnostic log: {output / 'execution.log'}", file=sys.stderr)
        return result.returncode
    verification = json.loads((execution / "FINAL-VERIFICATION.json").read_bytes())
    print(json.dumps({"output": str(execution), **verification}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
