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
        if "mode" in entry and source.stat().st_mode & 0o777 != entry["mode"]:
            raise ValueError(f"bundled source mode mismatch: {destination}")
    output.mkdir(mode=0o700)
    source_root = output / "source"
    source_root.mkdir(mode=0o700)
    identities = {}
    for destination, raw in captured.items():
        path = source_root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        mode = mapping[destination].get("mode", 0o755 if mapping[destination].get("executable", False) else 0o644)
        path.chmod(mode)
        identities[destination] = {"sha256": hashlib.sha256(raw).hexdigest(), "mode": mode}
    (source_root / "bundled-source-identity.json").write_text(
        json.dumps({"files": identities}, sort_keys=True) + "\n", encoding="utf-8"
    )
    return source_root


def capture_harbor(bundle: Path, registry: Path, python: Path) -> dict:
    """Read retained controls through their unchanged production admission path."""
    program = """
import json, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import rubric_native_collect as collector
from rubric_example_native import validate_pair
bundle = Path(sys.argv[3])
raw = (bundle / "native-index.json").read_bytes()
document = collector.admission(raw, bundle)
summary = validate_pair(bundle, document)
validation = collector.provider_validation(bundle / "task")
assert collector.admission((bundle / "native-index.json").read_bytes(), bundle) == document
print(json.dumps({"summary": summary, "provider_validation": validation}))
"""
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            program,
            str(ROOT / "skills/eval-author-rubric/scripts"),
            str(ROOT / "skills/eval-author/scripts"),
            str(bundle),
        ],
        env={
            "PATH": os.defpath,
            "HOME": str(Path.home()),
            "EVAL_AUTHOR_NATIVE_REGISTRY": str(registry),
            "TMPDIR": str(Path.home() / ".hermes/cache/scratch"),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    admitted = json.loads(result.stdout)
    index_raw = (bundle / "native-index.json").read_bytes()
    index = json.loads(index_raw)
    files = {}
    for row in index["files"]:
        name = row["path"]
        path = bundle / name
        if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink():
            raise ValueError("invalid native evidence path")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"] or path.stat().st_mode & 0o777 != row["mode"]:
            raise ValueError("native evidence changed after validation")
        files[name] = {"hex": raw.hex(), "mode": row["mode"]}
    files["native-index.json"] = {"hex": index_raw.hex(), "mode": 0o644}
    return {"admitted": admitted, "files": files}


def attach_harbor(source_root: Path, captured: dict) -> None:
    identities_path = source_root / "bundled-source-identity.json"
    identities = json.loads(identities_path.read_bytes())["files"]
    files = dict(captured["files"])
    files["validation.json"] = {
        "hex": (json.dumps(captured["admitted"], sort_keys=True) + "\n").encode().hex(),
        "mode": 0o644,
    }
    for name, record in files.items():
        destination = "harbor-input/" + name
        path = source_root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = bytes.fromhex(record["hex"])
        path.write_bytes(raw)
        path.chmod(record["mode"])
        identities[destination] = {"sha256": hashlib.sha256(raw).hexdigest(), "mode": record["mode"]}
    identities_path.write_text(json.dumps({"files": identities}, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new private directory outside the checkout")
    parser.add_argument("--harbor-bundle", type=Path, help="approved retained Oracle/NOP bundle; no new native runs")
    parser.add_argument("--native-registry", type=Path, help="owning external admission registry")
    parser.add_argument("--harbor-python", type=Path, help="exact recorded Harbor interpreter")
    args = parser.parse_args()
    if bool(args.harbor_bundle) != bool(args.native_registry) or bool(args.harbor_bundle) != bool(args.harbor_python):
        parser.error("Harbor bundle, registry and interpreter must be provided together")
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("output must be outside the checkout")
    if not output.parent.is_dir():
        parser.error("output parent must already exist")
    if sys.flags.optimize:
        parser.error("optimized Python disables scenario assertions")
    os.umask(0o077)
    captured = (
        capture_harbor(args.harbor_bundle.absolute(), args.native_registry.absolute(), args.harbor_python.absolute())
        if args.harbor_bundle
        else None
    )
    source_root = materialize(output)
    if captured is not None:
        attach_harbor(source_root, captured)
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
