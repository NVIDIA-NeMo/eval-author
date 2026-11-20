# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/refund"
BASE = EXAMPLE / "base"
ASSESSMENT_REQUEST = (
    "Apply eval-author-rubric to the supplied Harbor example. Assess all populated areas. "
    "Run the approved local checks and produce the results table. Ask only for permissions "
    "or decisions that you cannot infer from the selected evidence.\n"
)


class FixtureManifest(TypedDict):
    schema: str
    case: str
    base_sha256: str
    task_sha256: str
    evidence_status: str
    invalidated_evidence: list[str]
    changed_paths: list[str]
    rerun_controls: list[str]
    parent_task_sha256: str | None


def _safe_path(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("unsafe_path")
    path = path.absolute()
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError("unsafe_path")
    return path


def _separate_output(output: Path, *inputs: Path) -> Path:
    output = _safe_path(Path(output))
    for source in (EXAMPLE, *inputs):
        source = _safe_path(Path(source))
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("output_overlaps_input")
    return output


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    root = _safe_path(root)
    result = {}
    for directory, dirs, names, parent_fd in os.fwalk(root, follow_symlinks=False):
        for name in dirs:
            if not stat.S_ISDIR(os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode):
                raise ValueError("unsafe_source")
        for name in sorted(names):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            except OSError:
                raise ValueError("unsafe_source") from None
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > 4194304:
                    raise ValueError("unsafe_source")
                data = stream.read(4194305)
                after = os.fstat(stream.fileno())
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    any(getattr(before, key) != getattr(info, key) for key in fields for info in (after, current))
                    or len(data) != before.st_size
                ):
                    raise ValueError("unsafe_source")
            relative = (Path(directory) / name).relative_to(root).as_posix()
            result[relative] = (data, before.st_mode & 0o777)
    return result


def _identity(files: dict[str, tuple[bytes, int]]) -> str:
    rows = [[name, mode, hashlib.sha256(data).hexdigest()] for name, (data, mode) in sorted(files.items())]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def tree_identity(root: Path) -> str:
    return _identity(_snapshot(Path(root)))


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _selected(mutation: str) -> tuple[dict[str, tuple[bytes, int]], list[str]]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", mutation):
        raise ValueError("unknown_mutation")
    files = _snapshot(BASE)
    changed = []
    if mutation != "base":
        recipes = _snapshot(EXAMPLE / "mutations")
        if f"{mutation}.json" not in recipes:
            raise ValueError("unknown_mutation")
        recipe = json.loads(recipes[f"{mutation}.json"][0])
        if (
            set(recipe) != {"schema", "id", "category", "edits"}
            or recipe["schema"] != "rubric-example-mutation-v1"
            or recipe["id"] != mutation
            or recipe["category"]
            not in {"task_or_scorer_defect", "task_artifact_defect", "runtime_configuration_defect", "state_drift"}
            or not isinstance(recipe["edits"], list)
            or not recipe["edits"]
        ):
            raise ValueError("invalid_recipe")
        for edit in recipe["edits"]:
            if isinstance(edit, dict) and set(edit) == {"path", "delete"}:
                name = edit["path"]
                if not isinstance(name, str) or name not in files or name in changed or edit["delete"] is not True:
                    raise ValueError("invalid_recipe")
                del files[name]
                changed.append(name)
                continue
            if (
                not isinstance(edit, dict)
                or set(edit) != {"path", "old", "new"}
                or not all(isinstance(value, str) for value in edit.values())
            ):
                raise ValueError("invalid_recipe")
            name = edit["path"]
            if (
                name not in files
                or name in changed
                or ".." in Path(name).parts
                or Path(name).is_absolute()
                or not edit["old"]
                or edit["old"] == edit["new"]
            ):
                raise ValueError("invalid_recipe")
            data, mode = files[name]
            old, new = edit["old"].encode(), edit["new"].encode()
            if data.count(old) != 1:
                raise ValueError("mutation_precondition")
            files[name] = (data.replace(old, new), mode)
            changed.append(name)
    return files, sorted(changed)


def materialize(output: Path, mutation: str = "base", *, parent_task_sha256: str | None = None) -> FixtureManifest:
    output = _separate_output(output)
    files, changed = _selected(mutation)
    identity = tree_identity(BASE)
    manifest: FixtureManifest = {
        "schema": "rubric-example-materialization-v1",
        "case": mutation,
        "base_sha256": identity,
        "task_sha256": _identity(files),
        "evidence_status": "not_run",
        "invalidated_evidence": ["all_source_bound_observations"],
        "changed_paths": changed,
        "rerun_controls": ["all_local_controls", "all_source_bound_native_controls"],
        "parent_task_sha256": parent_task_sha256,
    }
    output.mkdir(mode=0o700)
    for name, (data, mode) in files.items():
        target = output / "assessment/task" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    (output / "assessment/request.md").write_text(ASSESSMENT_REQUEST)
    (output / "assessment/request.md").chmod(0o644)
    (output / "private").mkdir(mode=0o700)
    (output / "private/manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


def restore_to_base(previous: Path, output: Path) -> FixtureManifest:
    prior = verify_materialization(previous)
    output = _separate_output(output, previous)
    return materialize(output, parent_task_sha256=prior["task_sha256"])


def verify_materialization(output: Path) -> FixtureManifest:
    output = _safe_path(Path(output))
    manifest = json.loads(_snapshot(output / "private")["manifest.json"][0])
    parent = manifest.get("parent_task_sha256")
    if parent is not None:
        known = {tree_identity(BASE)} | {
            _identity(_selected(Path(name).stem)[0]) for name in _snapshot(EXAMPLE / "mutations")
        }
        if not isinstance(parent, str) or parent not in known or manifest["case"] != "base":
            raise ValueError("stale_materialization")
    selected, changed = _selected(manifest["case"])
    expected = {
        "schema": "rubric-example-materialization-v1",
        "case": manifest["case"],
        "base_sha256": tree_identity(BASE),
        "task_sha256": _identity(selected),
        "evidence_status": "not_run",
        "invalidated_evidence": ["all_source_bound_observations"],
        "changed_paths": changed,
        "rerun_controls": ["all_local_controls", "all_source_bound_native_controls"],
        "parent_task_sha256": manifest.get("parent_task_sha256"),
    }
    assessment = _snapshot(output / "assessment")
    expected_assessment = {f"task/{name}": value for name, value in selected.items()}
    expected_assessment["request.md"] = (ASSESSMENT_REQUEST.encode(), 0o644)
    if manifest != expected or assessment != expected_assessment:
        raise ValueError("stale_materialization")
    return manifest


class LocalCaseResult(TypedDict):
    returncode: int
    result: dict
    reward: str | None


class LocalPanel(TypedDict):
    schema: str
    execution_kind: str
    task_sha256: str
    controls_sha256: str
    cases: dict[str, LocalCaseResult]


def _merge(target: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def run_local_controls(selected: Path, output: Path) -> LocalPanel:
    manifest = verify_materialization(selected)
    files, _ = _selected(manifest["case"])
    controls_bytes = (EXAMPLE / "controls.json").read_bytes()
    controls = json.loads(controls_bytes)["cases"]
    ids = [row.get("id") for row in controls]
    if (
        not ids
        or not all(isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9-]*", name) for name in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("invalid_controls")
    output = _separate_output(output, selected)
    output.mkdir(mode=0o700)
    trusted = output / "trusted"
    trusted.mkdir()
    for name in ("verify.py", "refund_scorer.py"):
        (trusted / name).write_bytes(files[f"tests/{name}"][0])
    result: LocalPanel = {
        "schema": "rubric-example-local-panel-v1",
        "execution_kind": "local_verifier_subprocess_not_native",
        "task_sha256": manifest["task_sha256"],
        "controls_sha256": hashlib.sha256(controls_bytes).hexdigest(),
        "cases": {},
    }
    for row in controls:
        folder = output / row["id"]
        folder.mkdir()
        contract = json.loads(files["environment/contract.json"][0])
        _merge(contract, row.get("contract_variant", {}))
        (folder / "contract.json").write_bytes(_json_bytes(contract))
        (folder / "submission.json").write_bytes(_json_bytes(row["candidate"]))
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(trusted / "verify.py"),
                "--contract",
                str(folder / "contract.json"),
                "--submission",
                str(folder / "submission.json"),
                "--output",
                str(folder / "logs"),
            ],
            capture_output=True,
            timeout=10,
            cwd=trusted,
            env={"PATH": os.defpath, "HOME": str(output), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        (folder / "stdout.txt").write_bytes(completed.stdout)
        (folder / "stderr.txt").write_bytes(completed.stderr)
        observed = json.loads((folder / "logs/checks.json").read_bytes())
        reward = folder / "logs/reward.txt"
        result["cases"][row["id"]] = {
            "returncode": completed.returncode,
            "result": observed,
            "reward": reward.read_text().strip() if reward.is_file() else None,
        }
    (output / "local-results.json").write_bytes(_json_bytes(result))
    return result


def stop_process(process: subprocess.Popen) -> tuple[bytes | None, bytes | None]:
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)


def _probe_ledger(endpoint: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(max(deadline - time.monotonic(), 0.001))
                client.connect(endpoint)
                client.settimeout(max(deadline - time.monotonic(), 0.001))
                client.sendall(b'{"operation":"read"}\n')
                data = bytearray()
                while not data.endswith(b"\n"):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("ledger_read_timeout")
                    client.settimeout(remaining)
                    chunk = client.recv(65536)
                    if not chunk:
                        raise ValueError("ledger_read_incomplete")
                    data.extend(chunk)
                response = json.loads(data)
                if not isinstance(response, dict) or response.get("ok") is not True or "value" not in response:
                    raise ValueError("ledger_read_failed")
                return
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(min(0.01, max(deadline - time.monotonic(), 0)))
    raise TimeoutError("ledger_start_timeout")


def wait_for_ledger(endpoint: Path, process: subprocess.Popen, *, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    probe = None
    try:
        # Connect from the socket directory to avoid the macOS Unix-socket path limit.
        probe = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                'import runpy,sys; runpy.run_path(sys.argv[1])["_probe_ledger"](sys.argv[2], float(sys.argv[3]))',
                str(Path(__file__).resolve()),
                endpoint.name,
                str(deadline),
            ],
            cwd=endpoint.absolute().parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                probe.communicate(timeout=min(0.01, remaining))
            except subprocess.TimeoutExpired:
                continue
            if probe.returncode == 0 and process.poll() is None:
                return
            break
        stdout, stderr = stop_process(process)
        raise ValueError(
            f"ledger_start_failed at {endpoint}: exit={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        if probe is not None:
            stop_process(probe)


def run_ledger_controls(selected: Path, output: Path) -> dict:
    manifest = verify_materialization(selected)
    files, _ = _selected(manifest["case"])
    if files["environment/Dockerfile"] != _snapshot(BASE)["environment/Dockerfile"]:
        raise ValueError("native_route_required")
    controls_bytes = _snapshot(EXAMPLE)["controls.json"][0]
    controls = json.loads(controls_bytes)["cases"]
    ids = [row.get("id") for row in controls]
    if (
        not ids
        or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name) for name in ids)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("invalid_controls")
    output = _separate_output(output, selected)
    output.mkdir(mode=0o700)
    trusted = output / "trusted"
    trusted.mkdir()
    for name in ("environment/ledger.py", "environment/server.py", "tests/verify.py", "tests/refund_scorer.py"):
        (trusted / Path(name).name).write_bytes(files[name][0])
    panel = {
        "schema": "rubric-example-ledger-panel-v1",
        "execution_kind": "local_ledger_service_not_native",
        "task_sha256": manifest["task_sha256"],
        "controls_sha256": hashlib.sha256(controls_bytes).hexdigest(),
        "cases": {},
    }
    client_code = (
        "import json,socket,sys; "
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(3); "
        "s.connect('ledger.sock'); s.sendall((sys.argv[1]+'\\n').encode()); "
        "print(s.makefile('rb').readline().decode()); s.close()"
    )
    for row in controls:
        folder = output / row["id"]
        folder.mkdir()
        contract = json.loads(files["environment/contract.json"][0])
        _merge(contract, row.get("contract_variant", {}))
        (folder / "contract.json").write_bytes(_json_bytes(contract))
        env = {"PATH": os.defpath, "HOME": str(folder), "PYTHONDONTWRITEBYTECODE": "1"}
        with (folder / "server.stdout").open("wb") as stdout, (folder / "server.stderr").open("wb") as stderr:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(trusted / "server.py"),
                    "--contract",
                    str(folder / "contract.json"),
                    "--socket",
                    "ledger.sock",
                    "--record",
                    str(folder / "record.json"),
                ],
                cwd=folder,
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                wait_for_ledger(folder / "ledger.sock", process)
                responses = []
                requests = [{"operation": "act", "event": event} for event in row["candidate"]["events"]]
                requests.append({"operation": "complete", "success": row["candidate"]["success"]})
                for request in requests:
                    completed = subprocess.run(
                        [sys.executable, "-I", "-S", "-B", "-c", client_code, json.dumps(request)],
                        cwd=folder,
                        env=env,
                        capture_output=True,
                        timeout=5,
                        check=True,
                    )
                    responses.append(json.loads(completed.stdout))
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(trusted / "verify.py"),
                        "--contract",
                        str(folder / "contract.json"),
                        "--record",
                        str(folder / "record.json"),
                        "--output",
                        str(folder / "logs"),
                    ],
                    cwd=folder,
                    env=env,
                    capture_output=True,
                    timeout=5,
                )
                errors = sum(response != {"ok": True} for response in responses)
                reward = folder / "logs/reward.txt"
                panel["cases"][row["id"]] = {
                    "status": "input_rejected" if errors else "completed",
                    "action_errors": errors,
                    "responses": responses,
                    "returncode": completed.returncode,
                    "result": json.loads((folder / "logs/checks.json").read_bytes()),
                    "reward": reward.read_text().strip() if reward.is_file() else None,
                }
            finally:
                stop_process(process)
    (output / "ledger-results.json").write_bytes(_json_bytes(panel))
    return panel


def mutate_evidence(selected: Path, evidence: Path, output: Path, mutation: str) -> dict:
    if mutation != "withhold-reference":
        raise ValueError("unknown_evidence_mutation")
    manifest = verify_materialization(selected)
    files = _snapshot(Path(evidence))
    panel = json.loads(files["local-results.json"][0])
    with tempfile.TemporaryDirectory(prefix="refund-evidence-") as temporary:
        replay = run_local_controls(selected, Path(temporary) / "replay")
    if panel != replay or panel["task_sha256"] != manifest["task_sha256"]:
        raise ValueError("invalid_local_evidence")
    output = _separate_output(output, selected, evidence)
    output.mkdir(mode=0o700)
    reduced = copy.deepcopy(panel)
    del reduced["cases"]["reference"]
    (output / "assessment").mkdir()
    (output / "assessment/local-results.json").write_bytes(_json_bytes(reduced))
    (output / "private").mkdir(mode=0o700)
    (output / "private/mutation.json").write_bytes(
        _json_bytes(
            {
                "schema": "rubric-example-evidence-mutation-v1",
                "mutation": mutation,
                "source_sha256": _identity(files),
                "task_sha256": manifest["task_sha256"],
                "changed_paths": ["cases.reference"],
                "repair": "Run fresh local controls. Preserve this reduced evidence.",
                "observed_score": None,
            }
        )
    )
    return reduced


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a refund example or run local controls. Native execution needs separate approval."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("materialize")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--mutation", default="base")
    verify = commands.add_parser("verify")
    verify.add_argument("--input", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    for name in ("local-controls", "ledger-controls"):
        controls = commands.add_parser(name)
        controls.add_argument("--input", type=Path, required=True)
        controls.add_argument("--output", type=Path, required=True)
    evidence = commands.add_parser("mutate-evidence")
    evidence.add_argument("--input", type=Path, required=True)
    evidence.add_argument("--evidence", type=Path, required=True)
    evidence.add_argument("--output", type=Path, required=True)
    evidence.add_argument("--mutation", choices=["withhold-reference"], required=True)
    args = parser.parse_args()
    try:
        if args.command == "mutate-evidence":
            result = mutate_evidence(args.input, args.evidence, args.output, args.mutation)
        elif args.command == "materialize":
            result = materialize(args.output, args.mutation)
        elif args.command == "verify":
            result = verify_materialization(args.input)
        elif args.command == "restore":
            result = restore_to_base(args.input, args.output)
        elif args.command == "ledger-controls":
            result = run_ledger_controls(args.input, args.output)
        else:
            result = run_local_controls(args.input, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("example_operation_failed", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
