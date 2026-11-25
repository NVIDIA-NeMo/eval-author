#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""G container entrypoint: seed journey state, remeasure Bᵢ, and run task-create once per uncovered tool.

`eval_author_generation.py` mounts `/input`, `/output`, `/request.json`, and `/mounts/`. The
steps follow the Entrypoint section of docs/reference-generator-implementation.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HARBOR_PYTHON = "/opt/harbor-real/bin/python"

if __name__ == "__main__" and os.path.exists(HARBOR_PYTHON) and sys.executable != HARBOR_PYTHON:
    # The base image's python3 has only the standard library; Harbor's venv has pydantic, PyYAML, and Harbor.
    os.execv(HARBOR_PYTHON, [HARBOR_PYTHON, *sys.argv])

from generation_benchmark.models import (  # noqa: E402
    GenerationAttempt,
    GenerationRequest,
    GenerationResponse,
    resolve_evidence,
    write_model,
)
from generation_benchmark.reference_generator import (  # noqa: E402
    OperatorAnswers,
    ReferenceSettings,
    check_prompt_template,
    fill_prompt,
    load_operator_answers,
)
from generation_benchmark.staging import copy_tree_filtered  # noqa: E402
from generation_benchmark.validation import validate_generation_output  # noqa: E402
from generation_controls import DEFAULT_SCRIPT_RELPATH, SCRIPT_CONTROL_IMPORT_PATH  # noqa: E402
from metric_benchmark.io import read_json  # noqa: E402

SKILLS = Path("/opt/skills")
JOURNEY_STATE = SKILLS / "eval-author" / "scripts" / "journey_state.py"
LOCATE_RUNTIME = SKILLS / "eval-author" / "scripts" / "locate_runtime.py"
AUDIT_SKILL = SKILLS / "eval-author-audit"
TASK_PIPELINE = SKILLS / "eval-author-task-create" / "scripts" / "task_pipeline.py"

INPUT = Path("/input")
OUTPUT = Path("/output")
REQUEST = Path("/request.json")
MOUNTS = Path("/mounts")
WORKSPACE = Path("/workspace")
SNAPSHOT = Path("/tmp/eval-author-snapshot")
EVAL_AUTHOR = WORKSPACE / ".eval-author"
AUDIT = EVAL_AUTHOR / "audit.md"
REPORT = EVAL_AUTHOR / "audit-coverage-report.json"
BASELINE_TRIALS = EVAL_AUTHOR / "jobs" / "baseline"
BASELINE_MEASUREMENTS = EVAL_AUTHOR / "baseline-measurements"
JOURNEY_FILES = ("journey-state.json", "journey-state.lock")

BASELINE_REPORT = "baseline-report.json"
BASELINE_MISMATCH = "baseline_measurement_mismatch"
VERIFICATION_SCHEMA = "nemo.eval_author.task_gap_verification.v2"
_TAIL = 2000


class StepFailed(Exception):
    def __init__(self, error: str, code: int = 1) -> None:
        super().__init__(error)
        self.error = error
        self.code = code


def log(message: str) -> None:
    print(f"entrypoint: {message}", file=sys.stderr, flush=True)


def run(argv: list[str | Path], *, step: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one skill command in /workspace; a non-zero exit fails *step*."""
    result = subprocess.run(
        [str(arg) for arg in argv],
        cwd=WORKSPACE,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-_TAIL:]
        raise StepFailed(f"{step} failed ({result.returncode}): {detail or 'no output'}")
    return result


def run_json(argv: list[str | Path], *, step: str, stdin: str | None = None) -> dict[str, Any]:
    stdout = run(argv, step=step, stdin=stdin).stdout
    try:
        payload = json.loads(stdout)
    except ValueError as exc:
        raise StepFailed(f"{step} printed no JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise StepFailed(f"{step} printed a non-object JSON value")
    return payload


def journey(*args: str | Path) -> list[str | Path]:
    return ["python3", JOURNEY_STATE, "--repo", WORKSPACE, *args]


def audit_script(name: str, *args: str | Path) -> list[str | Path]:
    script = AUDIT_SKILL / "scripts" / "audit_spec" / name
    return ["uv", "run", "--with-requirements", AUDIT_SKILL / "requirements.txt", script, *args]


def task_pipeline(*args: str | Path) -> list[str | Path]:
    return ["uv", "run", TASK_PIPELINE, *args]


def covered_tools(report: object) -> list[str]:
    """The agent-under-test covered set when the report splits evidence classes, else its covered list."""
    if not isinstance(report, dict):
        raise ValueError("coverage report must be a JSON object")
    by_class = report.get("coverage_by_evidence_class")
    bucket = by_class.get("agent_under_test") if isinstance(by_class, dict) else None
    covered = bucket.get("covered") if isinstance(bucket, dict) else report.get("covered")
    if not isinstance(covered, list):
        raise ValueError("coverage report has no covered list")
    return sorted(str(name) for name in covered)


def match_output_owner(root: Path) -> None:
    """Give the host user the files a root container wrote into the bind-mounted output."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        owner = root.stat()
    except OSError:
        return
    if (owner.st_uid, owner.st_gid) == (0, 0):
        return
    for current, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            try:
                os.lchown(Path(current) / name, owner.st_uid, owner.st_gid)
            except OSError:
                pass


class Generation:
    def __init__(
        self,
        request: GenerationRequest,
        settings: ReferenceSettings,
        answers: OperatorAnswers,
        template: str,
    ) -> None:
        self.request = request
        self.settings = settings
        self.answers = answers
        self.template = template
        self.attempts: list[GenerationAttempt] = []
        self.pending: list[dict[str, Any]] = []
        self.unattempted: list[str] = []
        self.baseline_report_path: str | None = None

    @classmethod
    def from_mounts(cls) -> Generation:
        request = GenerationRequest.model_validate(read_json(REQUEST))
        settings = ReferenceSettings.model_validate(request.settings)
        answers = load_operator_answers(MOUNTS / "operator-answers.yaml")
        template = (MOUNTS / "prompt.md").read_text(encoding="utf-8")
        check_prompt_template(template)
        return cls(request, settings, answers, template)

    def run(self) -> int:
        (OUTPUT / "logs").mkdir(parents=True, exist_ok=True)
        error: str | None = None
        code = 0
        try:
            self.prepare_workspace()
            self.seed_journey()
            self.measure_baseline()
            self.select()
            while self.pending:
                tool = self.pending[0]
                attempt = self.attempt(tool)
                self.pending.pop(0)
                self.attempts.append(attempt)
                with (OUTPUT / "logs" / "attempts.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(attempt.model_dump(mode="json"), sort_keys=True) + "\n")
                self.restore()
        except StepFailed as exc:
            log(exc.error)
            error, code = exc.error, exc.code
        response = GenerationResponse(
            attempts=self.attempts,
            unattempted=[str(tool["name"]) for tool in self.pending] + self.unattempted,
            error=error,
            baseline_report_path=self.baseline_report_path,
        )
        write_model(OUTPUT / "response.json", response)
        try:
            validate_generation_output(OUTPUT, case_id=self.request.case_id, subset_id=self.request.eval_subset_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return code or 1
        return code

    def prepare_workspace(self) -> None:
        log("copying /input to /workspace")
        copy_tree_filtered(INPUT, WORKSPACE)
        # Journey state binds the absolute repository path, so a host copy makes init fail.
        for name in JOURNEY_FILES:
            (EVAL_AUTHOR / ".working" / name).unlink(missing_ok=True)
        BASELINE_TRIALS.mkdir(parents=True, exist_ok=True)
        for trial in sorted((MOUNTS / "trials").iterdir()):
            if trial.is_dir() and not trial.is_symlink():
                copy_tree_filtered(trial, BASELINE_TRIALS / trial.name)
        shutil.copyfile(MOUNTS / "audit.md", AUDIT)

    def seed_journey(self) -> None:
        log("seeding journey state")
        located = run_json(
            ["python3", LOCATE_RUNTIME, "--repo", WORKSPACE, "--provider", "harbor", "--flow", "authoring"],
            step="locate_runtime.py",
        )
        harbor = (located.get("selection") or {}).get("harbor") or {}
        selected = harbor.get("selected")
        if harbor.get("status") != "selected" or not isinstance(selected, dict):
            raise StepFailed(f"locate_runtime.py selected no Harbor runtime: {harbor.get('reason')}")
        source = "locate_runtime.py --provider harbor --flow authoring in the G container"
        observed = {
            "python": {
                "status": "verified",
                "source": f"{source}: {selected.get('python')} (Python {selected.get('python_version')})",
            },
            "runtime": {"status": "verified", "source": f"{source}: {selected.get('cli')} {selected.get('version')}"},
        }
        agent = self.request.agent.agent_bundle_id
        run(journey("init", "--journey", "expand", "--setup", "harbor", "--agent", agent), step="journey_state.py init")
        confirmation = self.answers.ethos_confirmation
        ethos_digest = hashlib.sha256((WORKSPACE / "ETHOS.md").read_bytes()).hexdigest()
        run(
            journey(
                "confirm-ethos",
                "--digest",
                ethos_digest,
                "--source",
                confirmation.source,
                "--validation",
                confirmation.validation,
            ),
            step="journey_state.py confirm-ethos",
        )
        update = self.answers.journey_update.model_dump(mode="json")
        update["prerequisites"].update(observed)
        run(journey("update", "--input", "-"), step="journey_state.py update", stdin=json.dumps(update))

    def measure_baseline(self) -> None:
        log("measuring the copied Bᵢ trials")
        measured = 0
        for trial in sorted(BASELINE_TRIALS.iterdir()):
            if not (trial / "agent" / "trajectory.json").is_file():
                continue
            run(
                audit_script(
                    "measure.py",
                    "--audit",
                    AUDIT,
                    "--trial-dir",
                    trial,
                    "--task-id",
                    trial.name,
                    "--run-id",
                    f"baseline-{trial.name}",
                    "--evidence-class",
                    "agent_under_test",
                    "--measure",
                    "tool_calls",
                    "--out-dir",
                    BASELINE_MEASUREMENTS,
                ),
                step=f"measure.py on {trial.name}",
            )
            measured += 1
        if not measured:
            raise StepFailed("no copied Bᵢ trial has agent/trajectory.json")
        run(
            audit_script("report.py", "--audit", AUDIT, "--coverage-dir", BASELINE_MEASUREMENTS, "--out", REPORT),
            step="report.py",
        )
        shutil.copyfile(REPORT, OUTPUT / BASELINE_REPORT)
        self.baseline_report_path = BASELINE_REPORT
        try:
            remeasured = covered_tools(read_json(REPORT))
            stage2 = covered_tools(read_json(MOUNTS / "gap-report.json"))
        except (OSError, ValueError) as exc:
            raise StepFailed(f"cannot compare covered sets: {exc}") from exc
        if remeasured != stage2:
            log(f"remeasured covered set {remeasured} differs from the stage 2 gap report's {stage2}")
            raise StepFailed(BASELINE_MISMATCH, code=3)

    def select(self) -> None:
        selection = run_json(task_pipeline("select", "--report", REPORT), step="task_pipeline.py select")
        tools = sorted(selection.get("actionable_tools") or [], key=lambda tool: str(tool["name"]))
        limit = self.request.max_gaps
        self.pending = tools if limit is None else tools[:limit]
        self.unattempted = [str(tool["name"]) for tool in tools[len(self.pending) :]]
        log(f"attempting {[tool['name'] for tool in self.pending]}; unattempted {self.unattempted}")
        shutil.rmtree(SNAPSHOT, ignore_errors=True)
        shutil.copytree(EVAL_AUTHOR, SNAPSHOT, symlinks=True)

    def restore(self) -> None:
        shutil.rmtree(EVAL_AUTHOR)
        shutil.copytree(SNAPSHOT, EVAL_AUTHOR, symlinks=True)

    def prompt_for(self, tool: dict[str, Any]) -> str:
        paths = tool["paths"]
        return fill_prompt(
            self.template,
            {
                "tool_name": str(tool["name"]),
                "task_slug": str(tool["task_slug"]),
                "proposal_path": str(paths["proposal"]),
                "draft_path": str(paths["draft"]),
                "measurements_path": str(paths["measurements"]),
                "report_path": REPORT.relative_to(WORKSPACE).as_posix(),
                "harbor_python": HARBOR_PYTHON,
                "agent_id": self.request.agent.agent_bundle_id,
                "wrong_answer_agent": SCRIPT_CONTROL_IMPORT_PATH,
                "wrong_answer_script": DEFAULT_SCRIPT_RELPATH,
                "authorizations": json.dumps(self.answers.authorizations.model_dump(mode="json"), sort_keys=True),
            },
        )

    def attempt(self, tool: dict[str, Any]) -> GenerationAttempt:
        name, slug = str(tool["name"]), str(tool["task_slug"])
        log_dir = OUTPUT / "logs" / slug
        log_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.prompt_for(tool)
        (log_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        log(f"running the coding agent for {name} ({slug})")
        codex_home = Path(tempfile.mkdtemp(prefix=f"codex-home-{slug}-"))
        try:
            with (log_dir / "transcript.jsonl").open("wb") as stdout, (log_dir / "stderr.log").open("wb") as stderr:
                agent = subprocess.run(
                    self.settings.agent_argv,
                    input=prompt.encode("utf-8"),
                    cwd=WORKSPACE,
                    stdout=stdout,
                    stderr=stderr,
                    env={**os.environ, "CODEX_HOME": str(codex_home)},
                    check=False,
                )
            rollouts = sorted(codex_home.glob("sessions/**/rollout-*.jsonl"))
            if rollouts:
                with (log_dir / "rollout.jsonl").open("wb") as stream:
                    for rollout in rollouts:
                        stream.write(rollout.read_bytes())
        finally:
            shutil.rmtree(codex_home, ignore_errors=True)
        status, reason, pending_question = self.acceptance(slug, log_dir)
        if agent.returncode != 0:
            reason = "; ".join(part for part in (reason, f"agent exited {agent.returncode}") if part)
        task_path: str | None = None
        if status == "accepted":
            copy_tree_filtered(EVAL_AUTHOR / "tasks" / slug, OUTPUT / "tasks" / slug)
            task_path = f"tasks/{slug}"
        log(f"{slug}: {status}{f' ({reason})' if reason else ''}")
        return GenerationAttempt(
            slug=slug,
            tool=name,
            status=status,
            task_path=task_path,
            log_path=f"logs/{slug}",
            reason=reason,
            pending_question=pending_question,
        )

    def acceptance(self, slug: str, log_dir: Path) -> tuple[str, str | None, str | None]:
        """Accept only when journey state, the verification JSON, and `check-acceptance` all agree."""
        task = f".eval-author/tasks/{slug}"
        try:
            shown = run_json(journey("show"), step="journey_state.py show")
        except StepFailed as exc:
            return "failed", exc.error, None
        state = shown.get("state") or {}
        pending_question = state.get("pending_question")
        record = (state.get("tasks") or {}).get(task)
        readiness = (shown.get("task_readiness") or {}).get(task) or {}
        if record is None:
            return "failed", f"task-create recorded no status for {task}", pending_question
        if record.get("status") != "accepted" or readiness.get("status") != "accepted":
            return "draft", readiness.get("reason") or record.get("source"), pending_question
        failures: list[str] = []
        for evidence in record.get("evidence") or []:
            try:
                path = resolve_evidence(WORKSPACE, str(evidence["path"]))
                verification = read_json(path)
            except (OSError, ValueError, KeyError) as exc:
                failures.append(f"{evidence.get('path')}: {exc}")
                continue
            if not (
                isinstance(verification, dict)
                and verification.get("schema") == VERIFICATION_SCHEMA
                and verification.get("accepted") is True
            ):
                continue
            check = subprocess.run(
                [str(arg) for arg in task_pipeline("check-acceptance", "--report", path)],
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                check=False,
            )
            (log_dir / "check-acceptance.json").write_text(check.stdout, encoding="utf-8")
            if check.returncode == 0:
                shutil.copyfile(path, log_dir / "verify.json")
                return "accepted", None, pending_question
            failures.append(f"check-acceptance on {evidence['path']} exited {check.returncode}")
        detail = "; ".join(failures) or f"no {VERIFICATION_SCHEMA} evidence with accepted: true"
        return "failed", detail, pending_question


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def main() -> int:
    signal.signal(signal.SIGTERM, _terminate)
    try:
        try:
            generation = Generation.from_mounts()
        except (OSError, ValueError) as exc:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            write_model(OUTPUT / "response.json", GenerationResponse(error=f"invalid generation inputs: {exc}"))
            print(str(exc), file=sys.stderr)
            return 2
        return generation.run()
    finally:
        match_output_owner(OUTPUT)


if __name__ == "__main__":
    raise SystemExit(main())
