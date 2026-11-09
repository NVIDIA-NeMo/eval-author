# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute one published agent against one pinned benchmark taskset."""

import argparse
import os
import tempfile
import time
import uuid
from pathlib import Path

from pydantic import Field, model_validator

from metric_benchmark.config import HelixExecution, execution_settings, read_yaml
from metric_benchmark.helix import (
    NotReady,
    Submission,
    _check_status_identity,
    _retain_registered_run,
    _verify_uploaded_inputs,
    latest_default_build,
    plan_submission,
)
from metric_benchmark.helix_client import ClientSettings, HelixClient
from metric_benchmark.helix_sources import References, load_references
from metric_benchmark.io import file_manifest, json_bytes, read_object, write_json
from metric_benchmark.models import Model, Text


class RunReceipt(Model):
    references: References
    execution: HelixExecution
    case_id: Text
    agent_bundle_id: Text
    run_id: Text
    request: dict
    response: dict | None = None
    resolved: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def identity(self):
        plan = self.submission()
        if self.request != plan.runs[0].request:
            raise ValueError("run receipt request differs from selected prepared agent/taskset")
        if self.response is not None:
            _check_status_identity(self.response, plan.runs[0], plan)
        return self

    def submission(self):
        plan = plan_submission(
            self.references.inputs(Path(".")),
            self.execution,
            self.references.bindings,
            self.run_id,
            publication=self.references.publication,
        )
        runs = [run for run in plan.runs if (run.case_id, run.agent_bundle_id) == (self.case_id, self.agent_bundle_id)]
        if len(runs) != 1:
            raise ValueError("select one published case and agent")
        return Submission.model_validate(
            {**plan.model_dump(), "runs": [runs[0].model_dump()], "selection": [[self.case_id, self.agent_bundle_id]]}
        )


def save_receipt(path, receipt):
    # Only this explicitly mutable receipt advances from requested to accepted.
    # The request and retry key are durable before the POST, including crash recovery.
    if path.is_symlink():
        raise ValueError("run receipt must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json_bytes(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def wait_for_images(plan, client, *, timeout=1800, poll_seconds=5, record=lambda ref, value: None):
    bound = plan.bindings.cases[plan.runs[0].case_id]
    deadline = time.monotonic() + timeout
    requested = set()
    observations = {}
    while True:
        pending = []
        for task in bound.tasks.values():
            latest = latest_default_build(client.image_builds(task.ref)["data"])
            if latest is None:
                if task.ref not in requested:
                    observations[task.ref] = client.request_builds([task.ref])[task.ref]
                    record(task.ref, observations[task.ref])
                    requested.add(task.ref)
                pending.append(task.ref)
            elif latest.get("status") == "ready":
                observations[task.ref] = latest
                record(task.ref, latest)
            elif latest.get("status") in ("failed", "cancelled", "expired"):
                record(task.ref, latest)
                raise ValueError("task image build failed; inspect the retained build receipt before retrying")
            else:
                pending.append(task.ref)
        if not pending:
            return observations
        if time.monotonic() >= deadline:
            raise NotReady("task images still building; retry the same run receipt")
        time.sleep(poll_seconds)


def captured_evidence(root):
    captured = {
        path.relative_to(root).as_posix(): read_object(path)
        for path in root.rglob("*.json")
        if "agent-files" not in path.relative_to(root).parts
    }
    if (root / "agent-files").is_dir():
        captured["agent_files"] = file_manifest(root / "agent-files")
    return captured


def capture_run(receipt, client):
    plan = receipt.submission()
    if receipt.response is None:
        raise ValueError("run has not been submitted")
    run = plan.runs[0]
    bound = plan.bindings.cases[receipt.case_id]
    if receipt.agent_bundle_id not in bound.registrations:
        return
    with tempfile.TemporaryDirectory(prefix="helix-run-capture-") as temporary:
        root = Path(temporary)
        _retain_registered_run(root, receipt.response, run, plan, client)
        captured = captured_evidence(root)
        if receipt.resolved and captured != receipt.resolved:
            # Later task jobs can appear; existing observations must remain unchanged.
            if any(captured.get(key) != value for key, value in receipt.resolved.items()):
                raise ValueError("captured run configuration changed")
        receipt.resolved.update(captured)


def execute_agent(
    references, settings, case_id, agent_id, output, client, *, run_id=None, plan_only=False, build_timeout=1800
):
    if output.exists():
        receipt = RunReceipt.model_validate(read_object(output))
        if (
            receipt.references != references
            or receipt.case_id != case_id
            or receipt.agent_bundle_id != agent_id
            or receipt.execution != settings
            or (run_id is not None and receipt.run_id != run_id)
        ):
            raise ValueError("existing run receipt belongs to a different request; use a new output for a new run")
    else:
        run_id = run_id or uuid.uuid4().hex
        plan = plan_submission(
            references.inputs(Path(".")), settings, references.bindings, run_id, publication=references.publication
        )
        selected = [run for run in plan.runs if (run.case_id, run.agent_bundle_id) == (case_id, agent_id)]
        if len(selected) != 1:
            raise ValueError("select one published case and agent")
        receipt = RunReceipt(
            references=references,
            execution=plan.execution,
            case_id=case_id,
            agent_bundle_id=agent_id,
            run_id=run_id,
            request=selected[0].request,
        )
        write_json(output, receipt)
    if plan_only:
        return receipt
    plan = receipt.submission()
    client.require_destination(plan.execution.base_url, plan.execution.workspace)
    if receipt.response is None:
        receipt.provenance = client.provenance()

        def record_build(ref, value):
            receipt.provenance.setdefault("image_builds", {})[ref] = value
            save_receipt(output, receipt)

        registration = plan.bindings.cases[case_id].registrations.get(agent_id)
        if registration is not None:
            client.verify_agent(registration)
        wait_for_images(plan, client, timeout=build_timeout, record=record_build)
        _verify_uploaded_inputs(plan, client)
        save_receipt(output, receipt)
        receipt.response = client.submit(receipt.request)
        _check_status_identity(receipt.response, plan.runs[0], plan)
        save_receipt(output, receipt)
    if agent_id in plan.bindings.cases[case_id].registrations:
        deadline = time.monotonic() + 300
        while not receipt.resolved:
            capture_run(receipt, client)
            save_receipt(output, receipt)
            if receipt.resolved:
                break
            status = client.status(receipt.response["run_id"])
            _check_status_identity(status, plan.runs[0], plan)
            if status["phase"] in ("completed", "failed", "cancelled", "expired"):
                break
            if time.monotonic() >= deadline:
                raise NotReady("run accepted; resolved configuration still pending; retry the same receipt")
            time.sleep(2)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--build-timeout", type=int, default=1800)
    args = parser.parse_args()
    try:
        references = load_references(args.references)
        settings = execution_settings(read_yaml(args.execution))
        if not isinstance(settings, HelixExecution):
            raise ValueError("execution requires backend: helix")
        configured = ClientSettings.model_validate(read_yaml(args.client))
        if (settings.base_url and settings.base_url != configured.base_url) or (
            settings.workspace and settings.workspace != configured.workspace
        ):
            raise ValueError("execution differs from client destination")
        settings = HelixExecution.model_validate(
            {**settings.model_dump(), "base_url": configured.base_url, "workspace": configured.workspace}
        )
        client = None if args.plan_only else HelixClient.from_settings(args.client)
        try:
            receipt = execute_agent(
                references,
                settings,
                args.case,
                args.agent,
                args.output,
                client,
                run_id=args.run_id,
                plan_only=args.plan_only,
                build_timeout=args.build_timeout,
            )
            print("Planned one run." if receipt.response is None else "Run accepted; receipt saved.")
        finally:
            if client is not None:
                client.client.close()
    except NotReady as error:
        parser.exit(2, f"{error}\n")
    except (ValueError, OSError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
