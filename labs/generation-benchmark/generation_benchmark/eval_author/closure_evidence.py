# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Register task-create closure receipts via real Harbor runs (task_evidence)."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from generation_benchmark.eval_author.ports.errors import ExperimentError

from .harbor import AgentConfig, harbor_trial_command
from .proof import _revision_manifests_with_ledgers

_RECEIPT_SCHEMA = "nemo.eval_author.task_evidence.v1"


def ensure_generation_closure_evidence(
    *,
    workspace: Path,
    draft: Path,
    slug: str,
    agent: AgentConfig,
    task_skill: Path,
    worker_records_author_controls: bool = False,
    worker_runs_oracle_repeats: bool = False,
) -> None:
    """Run Harbor through task evidence and register closure receipts for verify."""
    measurements = workspace / ".eval-author" / "task-measurements" / slug
    measurements.mkdir(parents=True, exist_ok=True)
    task_evidence = task_skill / "scripts" / "task_evidence.py"
    if not task_evidence.is_file():
        raise ExperimentError(f"task evidence script missing: {task_evidence}")
    contract_path = _ensure_contract(slug, measurements)
    manifest_path = _ensure_revision_manifest(
        draft=draft,
        measurements=measurements,
        contract_path=contract_path,
        task_evidence=task_evidence,
    )
    _reset_incomplete_closure(measurements, manifest_path)
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read closure contract {contract_path}: {exc}") from exc
    cases = contract.get("cases")
    if not isinstance(cases, dict):
        raise ExperimentError("closure contract cases must be an object")
    revision_index = _revision_index(manifest_path)
    run_root = workspace / ".eval-author" / "task-runs" / slug

    if worker_records_author_controls:
        _ensure_author_control_receipts(
            task_evidence=task_evidence,
            manifest_path=manifest_path,
            measurements=measurements,
            revision_index=revision_index,
            draft=draft,
            agent=agent,
            cases=cases,
            run_root=run_root,
        )

    if "reference" in cases:
        _ensure_recorded(
            task_evidence=task_evidence,
            manifest_path=manifest_path,
            case_id="reference",
            run_id="reference-worker",
            receipt_out=measurements / "reference-worker.json",
            draft=draft,
            agent=agent,
            trials_dir=run_root / "oracle",
            trial_name="oracle",
            harbor_agent="oracle",
            include_model=False,
            trace=None,
        )
    if "incorrect" in cases:
        incorrect_receipt = measurements / f"incorrect-{revision_index}.json"
        if not _receipt_passed(incorrect_receipt, manifest_path):
            raise ExperimentError(
                f"missing passed incorrect control receipt: {incorrect_receipt} "
                "(author must record incorrect and empty controls via task_evidence)"
            )
    if "empty" in cases:
        empty_receipt = measurements / f"empty-{revision_index}.json"
        if not _receipt_passed(empty_receipt, manifest_path):
            raise ExperimentError(
                f"missing passed empty control receipt: {empty_receipt} "
                "(author must record incorrect and empty controls via task_evidence)"
            )
    for repeat in ("repeat-1", "repeat-2"):
        if "agent" not in cases:
            continue
        index = repeat.split("-", 1)[1]
        trial_dir = run_root / repeat / repeat
        repeat_trace = trial_dir / "agent" / "trajectory.json"
        _ensure_recorded(
            task_evidence=task_evidence,
            manifest_path=manifest_path,
            case_id="agent",
            run_id=repeat,
            receipt_out=measurements / f"agent-{index}.json",
            draft=draft,
            agent=agent,
            trials_dir=run_root / repeat,
            trial_name=repeat,
            harbor_agent="oracle" if worker_runs_oracle_repeats else None,
            include_model=not worker_runs_oracle_repeats,
            trace=repeat_trace,
        )


def _reset_incomplete_closure(measurements: Path, manifest_path: Path) -> None:
    """Remove partial ledger/receipt state so worker reruns can re-record closure."""
    if _closure_complete(measurements, manifest_path):
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    ledger_value = manifest.get("attempts")
    if isinstance(ledger_value, str) and ledger_value.strip():
        ledger = Path(ledger_value)
        if ledger.is_dir():
            shutil.rmtree(ledger)
    for path in measurements.iterdir():
        if path.suffix != ".json" or path.name in {"contract.json"} or path.name.startswith("revision-"):
            continue
        if not _receipt_passed(path, manifest_path) and path.is_file():
            path.unlink()


def _closure_complete(measurements: Path, manifest_path: Path) -> bool:
    revision_index = _revision_index(manifest_path)
    required = [
        measurements / "reference-worker.json",
        measurements / "agent-1.json",
        measurements / "agent-2.json",
        measurements / f"incorrect-{revision_index}.json",
        measurements / f"empty-{revision_index}.json",
    ]
    return all(path.is_file() and _receipt_passed(path, manifest_path) for path in required)


def _ensure_contract(slug: str, measurements: Path) -> Path:
    contract_path = measurements / "contract.json"
    if contract_path.is_file():
        return contract_path
    contract_path.write_text(
        json.dumps(_default_harbor_contract(slug), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract_path


def _default_harbor_contract(task_id: str) -> dict[str, object]:
    case = {
        "requirement": "Satisfy the task verifier.",
        "health": [{"pointer": "/exception_info", "equals": None}],
    }
    return {
        "task_id": task_id,
        "provider": "harbor",
        "native_run_id": "/id",
        "alternative_not_applicable": "The task has one graded outcome.",
        "side_effect_not_applicable": "The task only reports the graded outcome.",
        "cases": {
            "reference": {**case, "kind": "reference", "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 1, "max": 1}]},
            "incorrect": {**case, "kind": "incorrect", "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 0}]},
            "empty": {**case, "kind": "empty", "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 0}]},
            "agent": {**case, "kind": "agent", "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 1, "max": 1}]},
        },
    }


def _construction_unmeasured(manifest: dict[str, object]) -> bool:
    return manifest.get("construction") == {"status": "unmeasured"}


def _purge_invalid_revision_manifests(measurements: Path) -> None:
    for revision in sorted(measurements.glob("revision-*.json")):
        try:
            manifest = json.loads(revision.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            revision.unlink(missing_ok=True)
            continue
        if _construction_unmeasured(manifest):
            continue
        ledger_value = manifest.get("attempts")
        if isinstance(ledger_value, str) and ledger_value.strip():
            ledger = Path(ledger_value).expanduser()
            if ledger.is_dir():
                shutil.rmtree(ledger)
        revision.unlink(missing_ok=True)


def _ensure_revision_manifest(
    *,
    draft: Path,
    measurements: Path,
    contract_path: Path,
    task_evidence: Path,
) -> Path:
    _purge_invalid_revision_manifests(measurements)
    def _revision_number(path: Path) -> int:
        match = re.fullmatch(r"revision-(\d+)\.json", path.name)
        return int(match.group(1)) if match else 0

    revisions = sorted(measurements.glob("revision-*.json"), key=_revision_number, reverse=True)
    for revision in revisions:
        try:
            manifest = json.loads(revision.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if _construction_unmeasured(manifest):
            return revision
    next_index = 1
    for revision in measurements.glob("revision-*.json"):
        match = re.fullmatch(r"revision-(\d+)\.json", revision.name)
        if match:
            next_index = max(next_index, int(match.group(1)) + 1)
    manifest_path = measurements / f"revision-{next_index}.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(task_evidence),
            "prepare",
            "--task-dir",
            str(draft),
            "--contract",
            str(contract_path),
            "--out",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not manifest_path.is_file():
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(f"task evidence prepare failed: {detail[-2000:]}")
    return manifest_path


def _ensure_author_control_receipts(
    *,
    task_evidence: Path,
    manifest_path: Path,
    measurements: Path,
    revision_index: int,
    draft: Path,
    agent: AgentConfig,
    cases: dict[str, object],
    run_root: Path,
) -> None:
    if "incorrect" in cases:
        import_agent, import_env = _incorrect_harbor_agent_import(draft)
        _ensure_recorded(
            task_evidence=task_evidence,
            manifest_path=manifest_path,
            case_id="incorrect",
            run_id="incorrect",
            receipt_out=measurements / f"incorrect-{revision_index}.json",
            draft=draft,
            agent=agent,
            trials_dir=run_root / "incorrect",
            trial_name="incorrect",
            harbor_agent=import_agent,
            include_model=False,
            trace=None,
            extra_agent_env=import_env,
        )
    if "empty" in cases:
        _ensure_recorded(
            task_evidence=task_evidence,
            manifest_path=manifest_path,
            case_id="empty",
            run_id="empty",
            receipt_out=measurements / f"empty-{revision_index}.json",
            draft=draft,
            agent=agent,
            trials_dir=run_root / "empty",
            trial_name="empty",
            harbor_agent="nop",
            include_model=False,
            trace=None,
        )


def _ensure_recorded(
    *,
    task_evidence: Path,
    manifest_path: Path,
    case_id: str,
    run_id: str,
    receipt_out: Path,
    draft: Path,
    agent: AgentConfig,
    trials_dir: Path,
    trial_name: str,
    harbor_agent: str | None,
    include_model: bool,
    trace: Path | None,
    extra_agent_kwargs: tuple[tuple[str, str], ...] = (),
    extra_agent_env: tuple[tuple[str, str], ...] = (),
) -> None:
    if _receipt_passed(receipt_out, manifest_path):
        return
    if receipt_out.is_file():
        receipt_out.unlink()
    _clear_trial_slot(trials_dir, trial_name)
    trial_dir = trials_dir / trial_name
    resolved_agent = harbor_agent or agent.agent
    command = harbor_trial_command(
        draft,
        agent,
        trials_dir=trials_dir,
        trial_name=trial_name,
        harbor_agent=resolved_agent,
        include_model=include_model,
        extra_agent_kwargs=extra_agent_kwargs,
        extra_agent_env=extra_agent_env,
    )
    if case_id == "agent" and resolved_agent == "oracle":
        command = _command_with_oracle_atif_seed(command, trial_dir)
        if trace is None:
            trace = trial_dir / "agent" / "trajectory.json"
    _task_evidence_run(
        task_evidence,
        manifest_path=manifest_path,
        case_id=case_id,
        run_id=run_id,
        receipt_out=receipt_out,
        result=trial_dir / "result.json",
        trace=trace,
        command=command,
        extra_env=extra_agent_env,
    )


def _command_with_oracle_atif_seed(command: list[str], trial_dir: Path) -> list[str]:
    """Run Harbor, then seed a minimal ATIF trace for oracle agent closure receipts."""
    seed = (
        "import json; from pathlib import Path; "
        f"t=Path({str(trial_dir)!r}); "
        "(t / 'agent').mkdir(parents=True, exist_ok=True); "
        "p=t / 'agent' / 'trajectory.json'; "
        "payload={"
        "'schema_version':'ATIF-v1.7',"
        "'session_id':t.name,"
        "'trajectory_id':t.name,"
        "'agent':{'name':'oracle','version':'1.0.0'},"
        "'steps':[{'step_id':1,'source':'agent','message':'oracle closure repeat'}]"
        "}; "
        "p.write_text(json.dumps(payload), encoding='utf-8') if not p.is_file() else None"
    )
    harbor = " ".join(shlex.quote(part) for part in command)
    return ["sh", "-c", f"{harbor} && {sys.executable} -c {shlex.quote(seed)}"]


def _clear_trial_slot(trials_dir: Path, trial_name: str) -> None:
    trial_dir = trials_dir / trial_name
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    trials_dir.mkdir(parents=True, exist_ok=True)


def _task_evidence_run(
    script: Path,
    *,
    manifest_path: Path,
    case_id: str,
    run_id: str,
    receipt_out: Path,
    result: Path,
    trace: Path | None,
    command: list[str],
    extra_env: tuple[tuple[str, str], ...] = (),
) -> None:
    argv = [
        sys.executable,
        str(script),
        "run",
        "--manifest",
        str(manifest_path),
        "--case",
        case_id,
        "--run-id",
        run_id,
        "--out",
        str(receipt_out),
        "--result",
        str(result),
    ]
    if trace is not None:
        argv.extend(["--trace", str(trace)])
    argv.extend(["--purpose", "closure", "--", *command])
    environment = os.environ.copy()
    for key, value in extra_env:
        environment[key] = value
    completed = subprocess.run(argv, check=False, capture_output=True, text=True, env=environment)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(f"task evidence run failed for {case_id}/{run_id}: {detail[-2000:]}")
    try:
        payload = _parse_task_evidence_stdout(completed.stdout)
    except (json.JSONDecodeError, ExperimentError) as exc:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(
            f"task evidence run returned invalid JSON for {case_id}/{run_id}: {detail[-2000:]}"
        ) from exc
    if payload.get("status") != "passed":
        raise ExperimentError(
            f"task evidence run for {case_id}/{run_id} did not pass: {payload.get('error', payload)}"
        )


def _receipt_passed(receipt_out: Path, manifest_path: Path) -> bool:
    if not receipt_out.is_file():
        return False
    try:
        receipt = json.loads(receipt_out.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if receipt.get("schema") != _RECEIPT_SCHEMA:
        return False
    if receipt.get("status") != "passed" or receipt.get("returncode") != 0:
        return False
    if receipt.get("purpose") != "closure":
        return False
    if Path(str(receipt.get("manifest", ""))).resolve() != manifest_path.resolve():
        return False
    return True


def _revision_index(manifest_path: Path) -> int:
    match = re.fullmatch(r"revision-(\d+)\.json", manifest_path.name)
    if not match:
        raise ExperimentError(f"unexpected revision manifest name: {manifest_path.name}")
    return int(match.group(1))


def _parse_task_evidence_stdout(stdout: str) -> dict[str, object]:
    """Parse the receipt object Harbor may prefix with human-readable trial logs."""
    text = stdout.strip()
    if not text:
        raise ExperimentError("task evidence stdout was empty")
    decoder = json.JSONDecoder()
    last: dict[str, object] | None = None
    index = 0
    while index < len(text):
        next_brace = text.find("{", index)
        if next_brace < 0:
            break
        try:
            value, end = decoder.raw_decode(text, next_brace)
        except json.JSONDecodeError:
            index = next_brace + 1
            continue
        if isinstance(value, dict):
            last = value
        index = end
    if last is None:
        raise json.JSONDecodeError("no JSON object in task evidence stdout", text, 0)
    return last


def _incorrect_harbor_agent_import(draft: Path) -> tuple[str, tuple[tuple[str, str], ...]]:
    module = draft / "controls" / "incorrect_agent.py"
    class_name = "IncorrectAgent"
    if module.is_file():
        text = module.read_text(encoding="utf-8")
        match = re.search(r"class (\w+)\(BaseAgent\)", text)
        if match:
            class_name = match.group(1)
    return f"controls.incorrect_agent:{class_name}", (("PYTHONPATH", str(draft)),)
