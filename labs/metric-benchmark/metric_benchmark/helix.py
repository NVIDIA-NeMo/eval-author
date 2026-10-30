# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Experimental submit/check/collect boundary; native evidence import is not yet qualified."""

import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import Field, JsonValue, StringConstraints, model_validator

from metric_benchmark.config import Case, LoadedCases, StrictLoader
from metric_benchmark.io import digest_bytes, file_manifest, json_bytes, read_json, write_json
from metric_benchmark.models import Digest, Model, Text, relative_path, resolve_evidence, unique

PinnedRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*#[0-9a-f]{64}$")]


class HelixExecution(Model):
    base_url: Text
    workspace: Text
    dispatcher_profile: Text
    max_concurrent_task_jobs: int = Field(ge=1)
    sandbox: dict[str, JsonValue] = Field(default_factory=dict)


class TaskBinding(Model):
    ref: PinnedRef
    native_task_id: Text


class CaseBinding(Model):
    taskset: PinnedRef
    tasks: dict[Text, TaskBinding]
    targets: dict[Text, dict[str, JsonValue]]


class HelixBindings(Model):
    cases_sha256: Digest
    cases: dict[Text, CaseBinding]


class SubmissionRun(Model):
    case_id: Text
    agent_bundle_id: Text
    request: dict[str, JsonValue]


class Submission(Model):
    artifact_type: Literal["helix_submission"] = "helix_submission"
    run_id: Text
    cases_sha256: Digest
    cases: list[dict[str, JsonValue]]
    execution: HelixExecution
    bindings: HelixBindings
    runs: list[SubmissionRun] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self):
        cases = [Case.model_validate(value) for value in self.cases]
        if digest_bytes(json_bytes(self.cases)) != self.cases_sha256 or self.bindings.cases_sha256 != self.cases_sha256:
            raise ValueError("submission case snapshot differs from its recorded identity")
        unique([case.case_id for case in cases], "submission case IDs")
        expected = [(case.case_id, agent.agent_bundle_id) for case in cases for agent in case.agents()]
        if expected != [(run.case_id, run.agent_bundle_id) for run in self.runs]:
            raise ValueError("submission must include exactly the configured case/agent matrix in order")
        if set(self.bindings.cases) != {case.case_id for case in cases}:
            raise ValueError("submission bindings differ from case membership")
        return self


class NotReady(ValueError):
    """Submission is complete, but remote execution or finalization is still active."""


def load_yaml(path: Path, model):
    value = yaml.load(path.read_text(), Loader=StrictLoader)
    json_bytes(value)
    return model.model_validate(value)


def _object(path: Path) -> dict:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def plan_submission(loaded: LoadedCases, settings: HelixExecution, bindings: HelixBindings, run_id: str) -> Submission:
    cases = loaded.identity_payload()
    identity = digest_bytes(json_bytes(cases))
    if bindings.cases_sha256 != identity or set(bindings.cases) != set(loaded.cases):
        raise ValueError("uploaded bindings differ from the case definitions")
    runs = []
    for case_id, prepared in loaded.cases.items():
        case = prepared.case
        bound = bindings.cases[case_id]
        if set(bound.tasks) != {t.task_id for t in case.task_inventory.tasks}:
            raise ValueError(f"case {case_id}: uploaded task membership differs")
        if set(bound.targets) != {a.agent_bundle_id for a in case.agents()}:
            raise ValueError(f"case {case_id}: uploaded agent membership differs")
        unique([task.ref for task in bound.tasks.values()], "remote task references")
        unique([task.native_task_id.casefold() for task in bound.tasks.values()], "remote native task IDs")
        for agent in case.agents():
            target = dict(bound.targets[agent.agent_bundle_id])
            for key, value in {
                "kind": "harbor",
                "n_attempts": 1,
                "max_retries": 0,
                "n_concurrent_trials": None,
            }.items():
                if key in target and target[key] != value:
                    raise ValueError(f"case {case_id}, agent {agent.agent_bundle_id}: {key} must be {value!r}")
                target[key] = value
            if not isinstance(target.get("source"), dict) or not target["source"]:
                raise ValueError("a Helix Harbor target requires an explicit agent source")
            request = {
                "spec": {"tasks": bound.taskset, "target": target},
                "execution": {"kind": "harbor-task-jobs", **settings.model_dump(exclude={"base_url", "workspace"})},
            }
            key = digest_bytes(
                json_bytes(
                    {
                        "cases": identity,
                        "case": case_id,
                        "agent": agent.agent_bundle_id,
                        "run_id": run_id,
                        "settings": settings.model_dump(),
                        "request": request,
                    }
                )
            )
            request["idempotency_key"] = "metric-benchmark-" + key.removeprefix("sha256:")
            runs.append(
                SubmissionRun.model_validate(
                    {
                        "case_id": case_id,
                        "agent_bundle_id": agent.agent_bundle_id,
                        "request": request,
                    }
                )
            )
    return Submission(
        run_id=run_id, cases_sha256=identity, cases=cases, execution=settings, bindings=bindings, runs=runs
    )


def _verify_uploaded_inputs(plan: Submission, client):
    for case_id, bound in plan.bindings.cases.items():
        remote = client.revision("tasksets", bound.taskset)
        members = remote["tasks"]
        if len(members) != len(bound.tasks) or set(members) != {task.ref for task in bound.tasks.values()}:
            raise ValueError(f"case {case_id}: published taskset membership differs from bindings")
        for task_id, task in bound.tasks.items():
            definition = client.revision("tasks", task.ref)["spec"]
            if definition["kind"] != "harbor" or definition["native_task_id"] != task.native_task_id:
                raise ValueError(f"case {case_id}, task {task_id}: published native task identity differs")


def _response_path(root: Path, index: int) -> Path:
    return root / "runs" / f"run-{index:04d}.json"


def _check_status_identity(status, run: SubmissionRun, plan: Submission):
    if (
        status.get("workspace") != plan.execution.workspace
        or status.get("taskset_ref") != plan.bindings.cases[run.case_id].taskset
        or status.get("idempotency_key") != run.request["idempotency_key"]
        or not status.get("run_id")
    ):
        raise ValueError(f"case {run.case_id}, agent {run.agent_bundle_id}: Helix run identity differs")


def submit_helix_evals(plan: Submission, output: Path, client) -> list[dict]:
    plan = Submission.model_validate(plan.model_dump())
    client.require_destination(plan.execution.base_url, plan.execution.workspace)
    # All preflight reads finish before any paid work is submitted.
    _verify_uploaded_inputs(plan, client)
    write_json(output / "submission.json", plan)
    accepted = []
    for index, run in enumerate(plan.runs):
        path = _response_path(output, index)
        response = _object(path) if path.exists() else client.submit(run.request)
        _check_status_identity(response, run, plan)
        write_json(path, response)
        accepted.append(response)
    unique([row["run_id"] for row in accepted], "remote run IDs")
    return accepted


def check_helix_runs(root: Path, client):
    plan = Submission.model_validate(read_json(resolve_evidence(root, "submission.json")))
    client.require_destination(plan.execution.base_url, plan.execution.workspace)
    statuses = []
    waiting = []
    failed = []
    for index, run in enumerate(plan.runs):
        path = _response_path(root, index)
        if not path.is_file():
            raise ValueError(f"case {run.case_id}, agent {run.agent_bundle_id}: submission incomplete; resume submit")
        accepted = _object(path)
        _check_status_identity(accepted, run, plan)
        status = client.status(accepted["run_id"])
        _check_status_identity(status, run, plan)
        if status["run_id"] != accepted["run_id"]:
            raise ValueError("Helix returned a different run ID")
        label = f"case {run.case_id}, agent {run.agent_bundle_id}"
        phase, result = status["phase"], status["result_state"]
        if phase in ("failed", "cancelled", "expired") or result == "failed":
            failed.append(f"{label}: {phase}, results {result}")
            continue
        if phase in ("accepted", "running") or (phase == "completed" and result == "pending"):
            waiting.append(f"{label}: {phase}, results {result}")
            continue
        if phase != "completed" or result != "ready":
            failed.append(f"{label}: unsupported or incomplete state {phase}/{result}")
            continue
        members = client.members(status["run_id"])
        expected = len(plan.bindings.cases[run.case_id].tasks)
        if len(members) != expected or {m["task_index"] for m in members} != set(range(expected)):
            failed.append(f"{label}: incomplete or duplicated task matrix")
            continue
        if any(m["phase"] != "succeeded" or not m.get("job") for m in members):
            failed.append(f"{label}: task jobs failed or remain unfinished")
            continue
        unique([m["job"] for m in members], "remote task jobs")
        statuses.append((index, status, sorted(members, key=lambda m: m["task_index"])))
    if failed:
        raise ValueError("; ".join(failed))
    if waiting:
        raise NotReady("Not done running / results still being prepared: " + "; ".join(waiting))
    return plan, statuses


def _unpack(archive: Path, output: Path):
    with tarfile.open(archive) as tar:
        seen = set()
        for member in tar.getmembers():
            relative_path(member.name)
            normalized = PurePosixPath(member.name).as_posix()
            if not (member.isfile() or member.isdir()) or normalized in seen:
                raise ValueError("Helix artifact contains a link, special file, or duplicate path")
            seen.add(normalized)
        tar.extractall(output, filter="data")
    return resolve_evidence(output, "agent-eval-results")


def _trial(bundle: Path, bound: CaseBinding):
    lines = resolve_evidence(bundle, "trials.jsonl").read_text().splitlines()
    if len(lines) != 1:
        raise ValueError("expected one trial per Helix task job")
    row = _object(bundle / "trials.jsonl")
    matches = [task_id for task_id, task in bound.tasks.items() if task.native_task_id == row["task_id"]]
    if len(matches) != 1:
        raise ValueError("downloaded trial does not identify an expected task")
    descriptor = row["evidence"]["descriptors"]["trial_dir"]
    if descriptor.get("kind") != "filesystem" or descriptor.get("format") != "dir":
        raise ValueError("expected a native trial directory descriptor")
    native = resolve_evidence(bundle, descriptor["ref"])
    result = _object(resolve_evidence(native, "result.json"))
    if result.get("task_name") != row["task_id"] or result.get("trial_name") != native.name or row["id"] != native.name:
        raise ValueError("native trial identity differs from Helix evidence descriptor")
    if not result.get("finished_at"):
        raise ValueError("native Harbor trial is unfinished")
    return matches[0], native.relative_to(bundle).as_posix()


def download_helix_results(submission: Path, output: Path, client) -> dict:
    plan, statuses = check_helix_runs(submission, client)
    identity = digest_bytes(json_bytes(plan))
    if output.exists():
        record = _object(resolve_evidence(output, "collection.json"))
        if record["submission_sha256"] != identity:
            raise ValueError("collection destination belongs to a different submission")
        files = file_manifest(output)
        files.pop("collection.json", None)
        if files != record["files"]:
            raise ValueError("collected evidence changed")
        return record
    # Confirm every task artifact exists before transferring the first one.
    for _, _, members in statuses:
        for member in members:
            client.artifact(member["job"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".helix-collect-", dir=output.parent) as temporary:
        staged = Path(temporary) / "results"
        staged.mkdir()
        observations = []
        for index, status, members in statuses:
            run = plan.runs[index]
            observed = set()
            for member in members:
                destination = staged / f"runs/run-{index:04d}/task-{member['task_index']:04d}"
                destination.mkdir(parents=True)
                archive = Path(temporary) / "bundle.tar.gz"
                client.download(member["job"], archive)
                bundle = _unpack(archive, destination)
                task_id, native = _trial(bundle, plan.bindings.cases[run.case_id])
                if task_id in observed:
                    raise ValueError(f"case {run.case_id}, agent {run.agent_bundle_id}: duplicate downloaded task")
                observed.add(task_id)
                observations.append(
                    {
                        "case_id": run.case_id,
                        "agent_bundle_id": run.agent_bundle_id,
                        "task_id": task_id,
                        "remote_run_id": status["run_id"],
                        "job": member["job"],
                        "native_results_path": (bundle / native).relative_to(staged).as_posix(),
                    }
                )
            if observed != set(plan.bindings.cases[run.case_id].tasks):
                raise ValueError(f"case {run.case_id}: downloaded matrix is incomplete")
        write_json(staged / "submission.json", plan)
        record = {
            "artifact_type": "helix_evidence_collection",
            "analysis_ready": False,
            "reason": "Helix runtime-to-prepared-input validation has not yet been qualified",
            "run_id": plan.run_id,
            "cases_sha256": plan.cases_sha256,
            "submission_sha256": identity,
            "observations": observations,
            "files": file_manifest(staged),
        }
        write_json(staged / "collection.json", record)
        if output.exists():
            raise ValueError("collection destination was created by another writer")
        staged.rename(output)
    return record
