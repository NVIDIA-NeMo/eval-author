# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Experimental submit/check/collect boundary; native evidence import is not yet qualified."""

import os
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from metric_benchmark.config import Case, HelixExecution, LoadedCases
from metric_benchmark.helix_publication import CaseBinding, HelixBindings, PublicationPlan, agent_target
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, json_bytes, read_json, read_object, write_json
from metric_benchmark.models import Digest, Model, Text, relative_path, resolve_evidence, unique


class SubmissionRun(Model):
    case_id: Text
    agent_bundle_id: Text
    request: dict[str, JsonValue]
    run_id: Text | None = None


class Submission(Model):
    artifact_type: Literal["helix_submission"] = "helix_submission"
    run_id: Text
    cases_sha256: Digest
    cases: list[dict[str, JsonValue]]
    execution: HelixExecution
    bindings: HelixBindings
    publication: PublicationPlan
    runs: list[SubmissionRun] = Field(min_length=1)
    selection: list[list[Text]] | None = None

    @model_validator(mode="after")
    def consistent(self):
        cases = [Case.model_validate(value) for value in self.cases]
        if digest_bytes(json_bytes(self.cases)) != self.cases_sha256 or self.bindings.cases_sha256 != self.cases_sha256:
            raise ValueError("submission case snapshot differs from its recorded identity")
        unique([case.case_id for case in cases], "submission case IDs")
        expected = [(case.case_id, agent.agent_bundle_id) for case in cases for agent in case.agents()]
        selected = [(run.case_id, run.agent_bundle_id) for run in self.runs]
        declared = [tuple(pair) for pair in self.selection] if self.selection is not None else expected
        if (
            selected != declared
            or len(set(selected)) != len(selected)
            or any(pair not in expected for pair in selected)
        ):
            raise ValueError("submission matrix differs from its explicit case/agent selection")
        if set(self.bindings.cases) != {case.case_id for case in cases}:
            raise ValueError("submission bindings differ from case membership")
        if self.publication.identity != self.bindings.publication_sha256:
            raise ValueError("submission publication identity differs from bindings")
        if self.publication.cases_sha256 != self.cases_sha256 or (
            self.publication.destination.base_url != self.execution.base_url
            or self.publication.destination.workspace != self.execution.workspace
        ):
            raise ValueError("publication differs from submission inputs or execution destination")
        published = {case.case_id: case for case in self.publication.cases}
        if set(published) != {case.case_id for case in cases}:
            raise ValueError("publication case membership differs")
        for case in cases:
            bound = self.bindings.cases[case.case_id]
            agents = {agent.agent_bundle_id: agent for agent in published[case.case_id].agents}
            tasks = {task.task_id: task for task in published[case.case_id].tasks}
            if set(agents) != {agent.agent_bundle_id for agent in case.agents()} or set(tasks) != set(
                case.task_sources
            ):
                raise ValueError("publication agent/task membership differs")
            if set(bound.agents) != set(agents) or set(bound.tasks) != set(tasks):
                raise ValueError("binding packaging or task membership differs")
            for task_id, task in tasks.items():
                if case.task_sources[task_id].sha256 != task.sha256:
                    raise ValueError("publication task differs from signed source")
            for agent in case.agents():
                published_agent = agents[agent.agent_bundle_id]
                packaging = bound.agents[agent.agent_bundle_id]
                if published_agent.packaging != packaging or published_agent.target != agent_target(
                    agent, packaging, self.execution.workspace
                ):
                    raise ValueError("publication target differs from prepared agent derivation")
                if agent.registered is not None:
                    if agent.inputs[agent.registered.bundle_input].sha256 != published_agent.sha256:
                        raise ValueError("publication bundle differs from prepared agent input")
        for run in self.runs:
            expected_run = next(
                r
                for r in _submission_runs(
                    cases, self.execution, self.bindings, run.run_id or self.run_id, self.cases_sha256
                )
                if (r.case_id, r.agent_bundle_id) == (run.case_id, run.agent_bundle_id)
            )
            if run != expected_run:
                raise ValueError("submission requests differ from the prepared experiment")
        return self


class NotReady(ValueError):
    """Submission is complete, but remote execution or finalization is still active."""


_object = read_object


def _submission_runs(cases, settings, bindings, run_id, identity):
    runs = []
    for case in cases:
        case_id = case.case_id
        bound = bindings.cases[case_id]
        if set(bound.tasks) != {t.task_id for t in case.task_inventory.tasks}:
            raise ValueError(f"case {case_id}: uploaded task membership differs")
        if set(bound.targets) != {a.agent_bundle_id for a in case.agents()}:
            raise ValueError(f"case {case_id}: uploaded agent membership differs")
        unique([task.ref for task in bound.tasks.values()], "remote task references")
        unique([task.native_task_id.casefold() for task in bound.tasks.values()], "remote native task IDs")
        if set(bound.agents) != {a.agent_bundle_id for a in case.agents()}:
            raise ValueError("uploaded agent packaging differs from case membership")
        for agent in case.agents():
            target = agent_target(agent, bound.agents[agent.agent_bundle_id], settings.workspace)
            if bound.targets[agent.agent_bundle_id] != target:
                raise ValueError("uploaded target differs from prepared agent configuration")
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
                "execution": {
                    "kind": "harbor-task-jobs",
                    **settings.model_dump(exclude={"base_url", "workspace", "backend"}),
                },
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
                        "run_id": run_id,
                        "request": request,
                    }
                )
            )
    return runs


def plan_submission(
    loaded: LoadedCases, settings: HelixExecution, bindings: HelixBindings, run_id: str, *, publication: PublicationPlan
) -> Submission:
    cases = loaded.identity_payload()
    identity = digest_bytes(json_bytes(cases))
    if bindings.cases_sha256 != identity or set(bindings.cases) != set(loaded.cases):
        raise ValueError("uploaded bindings differ from the case definitions")
    if settings.base_url is None or settings.workspace is None:
        settings = HelixExecution.model_validate(
            {
                **settings.model_dump(),
                "base_url": settings.base_url or publication.destination.base_url,
                "workspace": settings.workspace or publication.destination.workspace,
            }
        )
    if settings.base_url is None or settings.workspace is None:
        raise ValueError("publication must record the resolved execution destination")
    runs = _submission_runs([case.case for case in loaded.cases.values()], settings, bindings, run_id, identity)
    return Submission(
        run_id=run_id,
        cases_sha256=identity,
        cases=cases,
        execution=settings,
        bindings=bindings,
        publication=publication,
        runs=runs,
    )


def _verify_uploaded_inputs(plan: Submission, client):
    selected = {(run.case_id, run.agent_bundle_id) for run in plan.runs}
    for case_id, bound in plan.bindings.cases.items():
        if not any(case_id == case for case, _ in selected):
            continue
        for agent_id, receipt in bound.registrations.items():
            if (case_id, agent_id) in selected:
                client.verify_agent(receipt)
        remote = client.revision("tasksets", bound.taskset)
        members = remote["tasks"]
        if len(members) != len(bound.tasks) or set(members) != {task.ref for task in bound.tasks.values()}:
            raise ValueError(f"case {case_id}: published taskset membership differs from bindings")
        for task_id, task in bound.tasks.items():
            definition = client.revision("tasks", task.ref)["spec"]
            if definition["kind"] != "harbor" or definition["native_task_id"] != task.native_task_id:
                raise ValueError(f"case {case_id}, task {task_id}: published native task identity differs")
            builds = client.image_builds(task.ref)["data"]
            latest = latest_default_build(builds)
            if latest is None or latest.get("status") != "ready":
                raise NotReady(f"case {case_id}, task {task_id}: published task image is not ready")


def latest_default_build(builds):
    if not isinstance(builds, list) or any(not isinstance(build, dict) for build in builds):
        raise ValueError("Helix image build history must be a list of build records")
    candidates = [build for build in builds if build.get("platform") is None]
    if len(candidates) < 2:
        return candidates[0] if candidates else None
    dated = []
    for build in candidates:
        value = build.get("created_at")
        if not isinstance(value, str):
            raise ValueError("multiple image builds require creation timestamps")
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("invalid image build creation timestamp") from None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        dated.append((timestamp, build))
    latest = max(timestamp for timestamp, _ in dated)
    matches = [build for timestamp, build in dated if timestamp == latest]
    if len(matches) != 1:
        raise ValueError("image build history has an ambiguous latest build")
    return matches[0]


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


def _retain_registered_run(destination, response, run, plan, client):
    destination.mkdir(parents=True, exist_ok=True)
    if not response.get("plan_ref"):
        response = client.status(response["run_id"])
        _check_status_identity(response, run, plan)
    if not response.get("plan_ref"):
        # Older servers expose the resolved target only on task jobs.
        for member in client.members(response["run_id"]):
            if member.get("job"):
                job = client.job(member["job"])
                if job.get("spec", {}).get("run_id") != response["run_id"]:
                    raise ValueError("resolved job differs from run")
                target = job["spec"]["spec"]["target"]
                job_path = destination / "jobs" / f"task-{member['task_index']:04d}.json"
                identity = {key: job[key] for key in ("name", "workspace", "spec")}
                if job_path.exists():
                    saved = _object(job_path)
                    if {key: saved[key] for key in identity} != identity:
                        raise ValueError("resolved job specification changed")
                else:
                    write_json(job_path, identity)
                source = target["source"]
                receipt = plan.bindings.cases[run.case_id].registrations[run.agent_bundle_id]
                if source.get("agent") != receipt["ref"]:
                    raise ValueError("resolved registered agent differs from publication")
                if source.get("files"):
                    files = destination / "agent-files"
                    if not files.exists():
                        client.download_files(source["files"], files)
                    if file_manifest(files) != receipt["files"]:
                        raise ValueError("run agent snapshot differs from published bundle")
        return
    receipt = plan.bindings.cases[run.case_id].registrations[run.agent_bundle_id]
    plan_path = destination / "plan.json"
    if not plan_path.exists():
        client.download_files(response["plan_ref"], destination)
    if digest_file(plan_path).removeprefix("sha256:") != response["plan_digest"].removeprefix("sha256:"):
        raise ValueError("resolved run plan differs from Helix digest")
    resolved = _object(plan_path)
    source = resolved["spec"]["target"]["source"]
    if source.get("agent") != receipt["ref"]:
        raise ValueError("resolved registered agent differs from publication")
    if source.get("files"):
        files = destination / "agent-files"
        if not files.exists():
            client.download_files(source["files"], files)
        if file_manifest(files) != receipt["files"]:
            raise ValueError("run agent snapshot differs from published bundle")


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
        if run.agent_bundle_id in plan.bindings.cases[run.case_id].registrations:
            _retain_registered_run(root / "resolved" / f"run-{index:04d}", status, run, plan, client)
        label = f"case {run.case_id}, agent {run.agent_bundle_id}"
        phase, result = status["phase"], status["result_state"]
        if phase in ("accepted", "running") or result == "pending":
            waiting.append(f"{label}: {phase}, results {result}")
            continue
        if phase not in ("completed", "failed", "cancelled", "expired") or result not in ("ready", "failed", "none"):
            failed.append(f"{label}: unsupported or incomplete state {phase}/{result}")
            continue
        members = client.members(status["run_id"])
        expected = len(plan.bindings.cases[run.case_id].tasks)
        if len(members) != expected or {m["task_index"] for m in members} != set(range(expected)):
            failed.append(f"{label}: incomplete or duplicated task matrix")
            continue
        if any(
            m["phase"] not in ("succeeded", "failed", "cancelled", "expired")
            or (m["phase"] == "succeeded" and not m.get("job"))
            for m in members
        ):
            failed.append(f"{label}: task jobs failed or remain unfinished")
            continue
        unique([m["job"] for m in members if m.get("job")], "remote task jobs")
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
            if PurePosixPath(normalized).parts[0] != "agent-eval-results":
                raise ValueError("Helix artifact member is outside agent-eval-results")
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


def _cached_download(client, job: str, archive: Path, cached: Path):
    receipt = cached.with_suffix(".json")
    if receipt.exists() and cached.exists():
        if (
            cached.is_symlink()
            or receipt.is_symlink()
            or _object(receipt) != {"job": job, "sha256": digest_file(cached)}
        ):
            raise ValueError("saved download cache changed")
        shutil.copyfile(cached, archive)
    else:
        client.download(job, archive)


def _retain_download(job: str, archive: Path, cached: Path):
    cached.parent.mkdir(parents=True, exist_ok=True)
    if not cached.exists():
        with tempfile.NamedTemporaryFile(dir=cached.parent) as temporary:
            shutil.copyfile(archive, temporary.name)
            try:
                os.link(temporary.name, cached)
            except FileExistsError:
                pass
    if cached.is_symlink() or digest_file(cached) != digest_file(archive):
        raise ValueError("saved download cache differs from verified archive")
    write_json(cached.with_suffix(".json"), {"job": job, "sha256": digest_file(cached)})


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
    artifacts = {}
    for _, _, members in statuses:
        for member in members:
            if member["phase"] == "succeeded":
                artifacts[member["job"]] = client.artifact(member["job"])
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = output.parent / f".{output.name}.helix-downloads"
    if cache.is_symlink():
        raise ValueError("download cache must not be a symlink")
    write_json(cache / "submission.json", {"submission_sha256": identity})
    with tempfile.TemporaryDirectory(prefix=".helix-collect-", dir=output.parent) as temporary:
        staged = Path(temporary).resolve() / "results"
        staged.mkdir()
        observations = []
        for index, status, members in statuses:
            run = plan.runs[index]
            write_json(staged / f"runs/run-{index:04d}/status.json", status)
            bound = plan.bindings.cases[run.case_id]
            if run.agent_bundle_id in bound.registrations:
                retained = submission / "resolved" / f"run-{index:04d}"
                _retain_registered_run(retained, status, run, plan, client)
                shutil.copytree(retained, staged / f"runs/run-{index:04d}/resolved")
            taskset = client.revision("tasksets", bound.taskset)
            refs = taskset.get("tasks")
            if (
                not isinstance(refs, list)
                or len(refs) != len(bound.tasks)
                or set(refs) != {task.ref for task in bound.tasks.values()}
            ):
                raise ValueError("resolved taskset membership differs from bindings")
            task_ids = {task.ref: task_id for task_id, task in bound.tasks.items()}
            taskset_path = staged / f"runs/run-{index:04d}/taskset.json"
            write_json(taskset_path, {"ref": bound.taskset, "tasks": refs})
            observed = set()
            for member in members:
                destination = staged / f"runs/run-{index:04d}/task-{member['task_index']:04d}"
                destination.mkdir(parents=True)
                write_json(destination / "member.json", member)
                expected_task_id = task_ids[refs[member["task_index"]]]
                if member["phase"] != "succeeded":
                    job_path = None
                    if member.get("job"):
                        job = client.job(member["job"])
                        if (
                            job.get("name") != member["job"]
                            or job.get("workspace") != plan.execution.workspace
                            or job.get("spec", {}).get("run_id") != status["run_id"]
                            or job.get("spec", {}).get("task_index") != member["task_index"]
                        ):
                            raise ValueError("failed job specification differs from its run member")
                        write_json(destination / "job.json", job)
                        job_path = (destination / "job.json").relative_to(staged).as_posix()
                    observed.add(expected_task_id)
                    observations.append(
                        {
                            "case_id": run.case_id,
                            "agent_bundle_id": run.agent_bundle_id,
                            "task_id": expected_task_id,
                            "remote_run_id": status["run_id"],
                            "job": member.get("job"),
                            "job_spec_path": job_path,
                            "member_path": (destination / "member.json").relative_to(staged).as_posix(),
                            "taskset_path": taskset_path.relative_to(staged).as_posix(),
                            "status": "missing",
                            "native_results_path": (destination / "missing-native").relative_to(staged).as_posix(),
                            "unavailable_evidence": {
                                "native_results": f"Helix task job {member['phase']}: {member.get('error') or 'no completed native trial'}"
                            },
                        }
                    )
                    continue
                job = client.job(member["job"])
                spec = job.get("spec", {})
                if (
                    job.get("name") != member["job"]
                    or job.get("workspace") != plan.execution.workspace
                    or spec.get("run_id") != status["run_id"]
                    or spec.get("task_index") != member["task_index"]
                ):
                    raise ValueError("downloaded job specification differs from the run member")
                write_json(destination / "job.json", job)
                write_json(destination / "member.json", member)
                write_json(destination / "artifact.json", artifacts[member["job"]])
                archive = destination / "bundle.tar.gz"
                cached = cache / f"{index:04d}-{member['task_index']:04d}.tar.gz"
                _cached_download(client, member["job"], archive, cached)
                bundle = _unpack(archive, destination / "bundle")
                task_id, native = _trial(bundle, plan.bindings.cases[run.case_id])
                if task_id != expected_task_id:
                    raise ValueError("native task differs from its resolved taskset position")
                if task_id in observed:
                    raise ValueError(f"case {run.case_id}, agent {run.agent_bundle_id}: duplicate downloaded task")
                _retain_download(member["job"], archive, cached)
                observed.add(task_id)
                observations.append(
                    {
                        "case_id": run.case_id,
                        "agent_bundle_id": run.agent_bundle_id,
                        "task_id": task_id,
                        "remote_run_id": status["run_id"],
                        "job": member["job"],
                        "job_spec_path": (destination / "job.json").relative_to(staged).as_posix(),
                        "member_path": (destination / "member.json").relative_to(staged).as_posix(),
                        "taskset_path": taskset_path.relative_to(staged).as_posix(),
                        "status": "collected",
                        "native_results_path": (bundle / native).relative_to(staged).as_posix(),
                    }
                )
            if observed != set(plan.bindings.cases[run.case_id].tasks):
                raise ValueError(f"case {run.case_id}: downloaded matrix is incomplete")
        write_json(staged / "submission.json", plan)
        write_json(staged / "provenance.json", client.provenance())
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
    shutil.rmtree(cache)
    return record
