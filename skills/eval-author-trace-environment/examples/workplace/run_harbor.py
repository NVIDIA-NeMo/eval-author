# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run credential-free Harbor controls against isolated Workplace state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent
COLLECTOR_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONTROLS = [
    ("reference", 1),
    ("alternative", 1),
    ("noop", 0),
    ("wrong-body", 0),
    ("extra-reply", 0),
    ("wrong-id", 0),
    ("duplicate-reply", 0),
    ("reference-repeat", 1),
]


def validate_negative_state(control: str, state: list[dict], seed: list[dict]) -> None:
    if control not in {"wrong-body", "wrong-id", "extra-reply", "duplicate-reply"}:
        return
    reply = {
        "inbox/outbox": "outbox",
        "sender/recipient": "alex@example.test",
        "subject": "RE: Schedule confirmation",
        "sent_datetime": "2023-11-30 23:59:00",
        "body": "Confirmed for Tuesday at 10:00.",
    }
    invoice = dict(reply, **{"sender/recipient": "billing@example.test", "subject": "RE: November invoice"})
    expected = {
        "wrong-body": [dict(reply, body="Confirmed for Wednesday at 11:00.")],
        "wrong-id": [invoice],
        "extra-reply": [reply, dict(invoice, body="Received the invoice.")],
        "duplicate-reply": [reply, reply],
    }[control]
    originals = {row["email_id"]: row for row in seed}
    current = {row["email_id"]: row for row in state}
    if any(current.get(identity) != row for identity, row in originals.items()):
        raise ValueError("Control final state changed or removed an original email")
    additions = [
        {key: value for key, value in row.items() if key != "email_id"}
        for row in state
        if row["email_id"] not in originals
    ]
    # Generated IDs are not mutation evidence. Preserve duplicate reply counts.
    if sorted(json.dumps(row, sort_keys=True) for row in additions) != sorted(
        json.dumps(row, sort_keys=True) for row in expected
    ):
        raise ValueError("Control final state does not contain the intended mutation")


def summarize_result(results: list[Path], control: str, process_exit: int | None, seed: list) -> dict:
    expected = dict(CONTROLS)[control]
    record = {
        "control": control,
        "expected": expected,
        "process_exit": process_exit,
        "native_result_count": len(results),
        "passed": False,
        "errors": [],
    }
    if process_exit != 0:
        record["errors"].append("Harbor process did not succeed")
    if len(results) != 1:
        record["errors"].append("Expected exactly one native result")
        return record
    path = results[0]
    record["native_result"] = str(path)
    try:
        grader = runpy.run_path(str(ROOT / "task/grade.py"))
        result = grader["parse_json"](path.read_text())
        if not isinstance(result, dict):
            raise ValueError("Native result must be an object")
        trial_id = result["id"]
        job_id = result["config"]["job_id"]
        for identity in (trial_id, job_id):
            if not isinstance(identity, str) or str(UUID(identity)) != identity:
                raise ValueError("Invalid native identity")
        if result["trial_name"] != path.parent.name:
            raise ValueError("Native trial name does not match its directory")
        agent = "nop" if control == "noop" else "oracle"
        if result["config"]["agent"]["name"] != agent:
            raise ValueError("Unexpected native agent")
        reward = result["verifier_result"]["rewards"]["reward"]
        if type(reward) not in (int, float) or reward != expected:
            raise ValueError("Missing, invalid, or unexpected reward")
        error = result["exception_info"]
        record.update(trial_id=trial_id, job_id=job_id, trial_name=result["trial_name"], reward=reward)
        if error is not None:
            raise ValueError("Native trial reports an exception")
        trial = path.parent
        # Installed Harbor Oracle writes this file only for failed commands.
        agent_ok = not (trial / "agent/exit-code.txt").exists() and (
            agent == "nop" or (trial / "agent/oracle.txt").is_file()
        )
        record["agent_execution_ok"] = agent_ok
        if not agent_ok:
            raise ValueError("Agent execution did not succeed")
        snapshot = grader["parse_json"]((trial / "artifacts/export/state.json").read_text())
        state = grader["rows_from_snapshot"](snapshot)
        state_ok = snapshot["initial_state"] == seed and (control != "noop" or state == seed)
        record["seed_and_export_ok"] = state_ok
        if not state_ok:
            raise ValueError("Export does not match the initial seed or NOP reset")
        validate_negative_state(control, state, seed)
        record["state_sha256"] = hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        record["errors"].append(f"{type(exc).__name__}: {exc}")
    record["passed"] = not record["errors"]
    return record


def source_hashes(root: Path) -> dict[str, str]:
    paths = [root / "run_harbor.py"]
    for directory in ("task", "runtime"):
        paths.extend(
            path
            for path in (root / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    hashes = {}
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError(f"Symlinked source: {path}")
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def summarize_controls(records: list[dict], selected: list[str]) -> dict:
    names = [item["control"] for item in records]
    errors = []
    complete = names == [name for name, _ in CONTROLS if name in selected]
    if len(set(selected)) != len(selected) or not selected or set(selected) - dict(CONTROLS).keys():
        errors.append("Invalid control selection")
    if len(set(names)) != len(names) or set(names) - set(selected):
        errors.append("Unexpected or duplicated controls")
    for field in ("trial_id", "job_id", "trial_name"):
        identities = [item.get(field) for item in records]
        if any(not identity for identity in identities) or len(set(identities)) != len(identities):
            errors.append(f"Missing or duplicated {field}")
    for item in records:
        if item.get("expected") != dict(CONTROLS).get(item["control"]):
            errors.append("Control expectation changed")
    by_name = {item["control"]: item for item in records}
    repeat = by_name.get("reference-repeat")
    if repeat is not None:
        reference = by_name.get("reference")
        if (
            not reference
            or not reference.get("state_sha256")
            or (repeat.get("state_sha256") != reference["state_sha256"])
        ):
            errors.append("Reference repeat does not match reference state")
    return {
        "access_mode": "stateful_simulation",
        "agent": "scripted_oracle_and_nop",
        "controls": records,
        "complete": complete,
        "passed": complete and bool(records) and not errors and all(item["passed"] is True for item in records),
        "errors": errors,
        "network_policy": "no-network",
        "verifier": "separate",
        "model_performance": "unmeasured",
    }


def run(output: Path, home: Path, selected: list[str] | None = None) -> dict:
    selected = [name for name, _ in CONTROLS] if selected is None else selected
    if not selected or len(set(selected)) != len(selected) or set(selected) - dict(CONTROLS).keys():
        raise ValueError("Select distinct known controls")
    if "reference-repeat" in selected and "reference" not in selected:
        raise ValueError("Reference repeat requires reference")
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "TMPDIR": str(output),
        "HARBOR_TELEMETRY": "off",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    records = []
    baseline = None
    report = summarize_controls(records, selected)
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    for name, expected in CONTROLS:
        if name not in selected:
            continue
        task = output / "tasks" / name
        frozen = output / "sources" / name
        control = "reference" if name == "reference-repeat" else name
        process_exit = None
        record = {"control": name, "expected": expected, "passed": False, "errors": [], "source_stable": False}
        try:
            before = source_hashes(ROOT)
            record["source_hashes_before"] = before
            if baseline is None:
                baseline = before
            if before != baseline or before["run_harbor.py"] != COLLECTOR_SHA256:
                raise ValueError("Source changed before build")
            for relative in before:
                target = frozen / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
                target.chmod((ROOT / relative).stat().st_mode & 0o777)
            if source_hashes(frozen) != before or source_hashes(ROOT) != before:
                raise ValueError("Source changed during copy")
            record["source_copy"] = str(frozen)
            (output / "summary.json").write_text(
                json.dumps(summarize_controls(records + [record], selected), indent=2) + "\n"
            )
            with (output / f"{name}.log").open("w") as log:
                subprocess.run(
                    [sys.executable, str(frozen / "task/build.py"), "--output", str(task), "--control", control],
                    check=True,
                    env=env,
                    stdout=log,
                    stderr=log,
                    timeout=600,
                )
                if source_hashes(ROOT) != before or source_hashes(frozen) != before:
                    raise ValueError("Source changed during build")
                command = [
                    str(Path(sys.executable).parent / "harbor"),
                    "run",
                    "--path",
                    str(task),
                    "--agent",
                    "nop" if name == "noop" else "oracle",
                    "--env",
                    "docker",
                    "--jobs-dir",
                    str(output / "jobs"),
                    "--job-name",
                    name,
                    "--n-concurrent",
                    "1",
                    "--max-retries",
                    "0",
                    "--delete",
                    "--quiet",
                ]
                completed = subprocess.run(command, env=env, stdout=log, stderr=log, timeout=600)
                process_exit = completed.returncode
            record["source_stable"] = source_hashes(ROOT) == before and source_hashes(frozen) == before
            if not record["source_stable"]:
                raise ValueError("Source changed during native execution")
            seed = json.loads((frozen / "task/seed.json").read_text())
            results = sorted((output / "jobs" / name).glob("*/result.json"))
            record.update(summarize_result(results, name, process_exit, seed))
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
            record.update(process_exit=process_exit, passed=False)
            record["errors"].append(f"{type(exc).__name__}: {exc}")
        records.append(record)
        report = summarize_controls(records, selected)
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        if not record["passed"] or report["errors"]:
            break
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--control", action="append", choices=[name for name, _ in CONTROLS])
    args = parser.parse_args()
    result = run(args.output.resolve(), args.home.resolve(), args.control)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["complete"] and result["passed"] else 1)
