# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from metric_benchmark.config import load_cases
from metric_benchmark.helix import (
    HelixBindings,
    HelixExecution,
    NotReady,
    Submission,
    check_helix_runs,
    download_helix_results,
    plan_submission,
    submit_helix_evals,
)
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.helix_publication import PublicationPlan, PublishAgent, agent_target
from metric_benchmark.io import json_bytes, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests/fixtures/metric_benchmark/cases.yml"


@pytest.fixture
def plan():
    loaded = load_cases(CASES)
    settings = HelixExecution(
        base_url="https://helix.example.com",
        workspace="example",
        dispatcher_profile="workers",
        max_concurrent_task_jobs=2,
    )
    publication = PublicationPlan.model_validate(
        {
            "cases_sha256": loaded.identity,
            "execution": settings.model_dump(),
            "cases": [
                {
                    "case_id": case_id,
                    "tasks": [
                        {
                            "task_id": tid,
                            "name": "task-" + str(i),
                            "path": source.path,
                            "sha256": source.sha256,
                            "context_sha256": source.sha256,
                        }
                        for i, (tid, source) in enumerate(prepared.case.task_sources.items())
                    ],
                    "agents": [
                        {
                            "agent_bundle_id": agent.agent_bundle_id,
                            "packaging": {},
                            "target": agent_target(agent, PublishAgent()),
                        }
                        for agent in prepared.case.agents()
                    ],
                }
                for case_id, prepared in loaded.cases.items()
            ],
        }
    )
    bindings = HelixBindings.model_validate(
        {
            "cases_sha256": loaded.identity,
            "publication_sha256": publication.identity,
            "cases": {
                case_id: {
                    "taskset": "example/suite#" + "a" * 64,
                    "tasks": {
                        task.task_id: {
                            "ref": f"example/task-{i}#" + str(i) * 64,
                            "native_task_id": f"native-{i}",
                        }
                        for i, task in enumerate(prepared.case.task_inventory.tasks)
                    },
                    "agents": {agent.agent_bundle_id: {} for agent in prepared.case.agents()},
                    "targets": {
                        agent.agent_bundle_id: agent_target(agent, PublishAgent()) for agent in prepared.case.agents()
                    },
                }
                for case_id, prepared in loaded.cases.items()
            },
        }
    )
    return plan_submission(loaded, settings, bindings, "test-run", publication=publication)


def archive(native_id, *, malicious=False, duplicate=False, overwrite_metadata=False):
    trial = f"{native_id}__trial"
    directory = f"evidence/harbor/job/{trial}"
    result = {
        "task_name": native_id,
        "trial_name": trial,
        "finished_at": "2026-01-01T00:00:00Z",
        "verifier_result": {"rewards": {"reward": 0.0, "other": -1.0}},
    }
    files = {
        "trials.jsonl": json_bytes(
            {
                "id": trial,
                "task_id": native_id,
                "evidence": {"descriptors": {"trial_dir": {"kind": "filesystem", "format": "dir", "ref": directory}}},
            }
        )
        .decode()
        .replace("\n", "")
        + "\n",
        f"{directory}/result.json": json.dumps(result),
        f"{directory}/agent/trajectory.json": '{"trace": "retained"}',
        "summary.json": '{"arbitrary_aggregate": 99}',
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            member = tarfile.TarInfo("agent-eval-results/" + name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        if malicious:
            member = tarfile.TarInfo("agent-eval-results/escape")
            member.type = tarfile.SYMTYPE
            member.linkname = "/tmp"
            tar.addfile(member)
        if duplicate:
            member = tarfile.TarInfo("agent-eval-results/summary.json")
            member.size = 2
            tar.addfile(member, io.BytesIO(b"{}"))
        if overwrite_metadata:
            member = tarfile.TarInfo("job.json")
            member.size = 2
            tar.addfile(member, io.BytesIO(b"{}"))
    return stream.getvalue()


class MockHelixClient(HelixClient):
    def provenance(self):
        return {"client_versions": {"harbor": "0.24.0"}, "server": {"revision": "mock"}}


class Server:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []
        self.accepted = {}
        self.phase = "completed"
        self.result_state = "ready"
        self.lose_response = False
        self.bad_members = False
        self.bad_taskset = False
        self.malicious = False
        self.duplicate = False
        self.duplicate_task = False
        self.fail_download = False
        self.fail_download_after = None
        self.failed_run = None
        self.page_size = 2
        self.images_ready = True
        self.wrong_job = False
        self.overwrite_metadata = False
        self.failed_member = False
        self.missing_job = False
        self.client = MockHelixClient(
            SimpleNamespace(
                base_url=plan.execution.base_url,
                http_client=httpx.Client(transport=httpx.MockTransport(self.handle)),
                request_headers=lambda *args, **kwargs: {},
            ),
            plan.execution.workspace,
        )

    def handle(self, request):
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        bound = next(iter(self.plan.bindings.cases.values()))
        if path.endswith("/image-builds"):
            return httpx.Response(
                200, json={"data": [{"status": "ready" if self.images_ready else "running", "platform": None}]}
            )
        if "/tasksets/" in path:
            tasks = [t.ref for t in bound.tasks.values()]
            return httpx.Response(200, json={"tasks": tasks[:-1] if self.bad_taskset else tasks})
        if "/tasks/task-" in path:
            index = int(path.split("task-")[1].split("/")[0])
            return httpx.Response(200, json={"spec": {"kind": "harbor", "native_task_id": f"native-{index}"}})
        if request.method == "POST":
            body = json.loads(request.content)
            key = body["idempotency_key"]
            self.accepted.setdefault(
                key,
                {
                    "run_id": f"remote-{len(self.accepted)}",
                    "workspace": "example",
                    "taskset_ref": body["spec"]["tasks"],
                    "idempotency_key": key,
                    "phase": "accepted",
                    "result_state": "pending",
                },
            )
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("lost submit response")
            return httpx.Response(201, json=self.accepted[key])
        if path.endswith("/download"):
            if self.fail_download or (
                self.fail_download_after is not None
                and sum(p.endswith("/download") for _, p in self.calls) > self.fail_download_after
            ):
                raise httpx.ReadTimeout("interrupted download")
            job = path.rsplit("/jobs/", 1)[1].split("/")[0]
            index = 0 if self.duplicate_task else int(job.split("-task-")[1])
            return httpx.Response(
                200,
                content=archive(
                    f"native-{index}",
                    malicious=self.malicious,
                    duplicate=self.duplicate,
                    overwrite_metadata=self.overwrite_metadata,
                ),
            )
        if "/results/agent-eval-results" in path:
            return httpx.Response(200, json={"name": "agent-eval-results"})
        if "/apis/jobs/" in path:
            job = path.rsplit("/jobs/", 1)[1]
            remote, index = job.split("-task-")
            return httpx.Response(
                200,
                json={
                    "name": job,
                    "workspace": "example",
                    "spec": {"run_id": "wrong" if self.wrong_job else remote, "task_index": int(index)},
                },
            )
        remote = path.split("/runs/")[1].split("/")[0]
        status = next(value for value in self.accepted.values() if value["run_id"] == remote)
        if path.endswith("/tasks"):
            members: list[dict] = [
                {"task": f"task-{i}", "task_index": i, "phase": "succeeded", "job": f"{remote}-task-{i}"}
                for i in range(len(bound.tasks))
            ]
            if self.failed_member:
                members[1].update(phase="failed", error="worker did not produce native evidence")
                if self.missing_job:
                    members[1]["job"] = None
            if self.bad_members:
                members.pop()
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json={
                    "data": members[(page - 1) * self.page_size : page * self.page_size],
                    "pagination": {
                        "page": page,
                        "total_pages": (len(members) + self.page_size - 1) // self.page_size,
                        "total_results": len(members),
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                **status,
                "phase": "failed" if remote == self.failed_run else self.phase,
                "result_state": self.result_state,
            },
        )


def test_plan_cli_library_parity_without_executable_inputs(plan, tmp_path):
    settings, bindings = tmp_path / "execution.yml", tmp_path / "bindings.yml"
    settings.write_text(yaml.safe_dump(plan.execution.model_dump()))
    bindings.write_text(yaml.safe_dump(plan.bindings.model_dump()))
    write_json(tmp_path / "publication.json", plan.publication)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "labs/metric-benchmark/submit_helix_evals.py"),
            "--cases",
            str(CASES),
            "--execution",
            str(settings),
            "--bindings",
            str(bindings),
            "--run-id",
            plan.run_id,
            "--output",
            str(tmp_path / "plan"),
            "--plan-only",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "nothing submitted" in result.stdout
    assert (tmp_path / "plan/submission.json").read_bytes() == json_bytes(plan)
    assert len(plan.runs) == 3
    assert all(run.request["spec"]["target"]["max_retries"] == 0 for run in plan.runs)


def test_mismatched_uploads_fail_before_post(plan, tmp_path):
    server = Server(plan)
    server.bad_taskset = True
    with pytest.raises(ValueError, match="membership"):
        submit_helix_evals(plan, tmp_path / "submitted", server.client)
    assert not any(method == "POST" for method, _ in server.calls)


def test_ambiguous_submit_is_replayed_without_new_runs(plan, tmp_path):
    server = Server(plan)
    server.lose_response = True
    with pytest.raises(ValueError, match="ReadTimeout"):
        submit_helix_evals(plan, tmp_path, server.client)
    first_key = next(iter(server.accepted))
    result = submit_helix_evals(plan, tmp_path, server.client)
    assert result[0]["idempotency_key"] == first_key
    assert len(server.accepted) == len(plan.runs)
    before = len([call for call in server.calls if call[0] == "POST"])
    assert submit_helix_evals(plan, tmp_path, server.client) == result
    assert len([call for call in server.calls if call[0] == "POST"]) == before


def test_unbuilt_images_prevent_all_submissions(plan, tmp_path):
    server = Server(plan)
    server.images_ready = False
    with pytest.raises(NotReady):
        submit_helix_evals(plan, tmp_path, server.client)
    assert not any(method == "POST" for method, _ in server.calls)


@pytest.mark.parametrize(
    ("phase", "result_state"), [("accepted", "pending"), ("running", "pending"), ("completed", "pending")]
)
def test_not_ready_downloads_nothing(plan, tmp_path, phase, result_state):
    server = Server(plan)
    submit_helix_evals(plan, tmp_path / "submitted", server.client)
    server.phase, server.result_state = phase, result_state
    with pytest.raises(NotReady, match="Not done"):
        download_helix_results(tmp_path / "submitted", tmp_path / "output", server.client)
    assert not (tmp_path / "output").exists()
    assert not any("/results/" in path for _, path in server.calls)


def test_terminal_failure_does_not_hide_other_active_runs(plan, tmp_path):
    server = Server(plan)
    submit_helix_evals(plan, tmp_path, server.client)
    server.phase = "running"
    server.failed_run = "remote-1"
    with pytest.raises(NotReady, match="Not done"):
        check_helix_runs(tmp_path, server.client)


def test_missing_submission_and_partial_taskset_are_not_complete(plan, tmp_path):
    server = Server(plan)
    submit_helix_evals(plan, tmp_path, server.client)
    server.bad_members = True
    with pytest.raises(ValueError, match="incomplete.*matrix"):
        check_helix_runs(tmp_path, server.client)
    (tmp_path / "runs/run-0001.json").unlink()
    with pytest.raises(ValueError, match="submission incomplete"):
        check_helix_runs(tmp_path, server.client)


def test_paginated_collection_preserves_every_native_file_and_is_not_analysis_ready(plan, tmp_path):
    server = Server(plan)
    submission, output = tmp_path / "submitted", tmp_path / "output"
    submit_helix_evals(plan, submission, server.client)
    result = download_helix_results(submission, output, server.client)
    assert len(result["observations"]) == 9
    assert result["analysis_ready"] is False
    assert not (output / "execution.json").exists()
    first_download = next(i for i, (_, path) in enumerate(server.calls) if path.endswith("/download"))
    assert sum("/results/" in p for _, p in server.calls[:first_download]) == 9
    for row in result["observations"]:
        native = output / row["native_results_path"]
        native_result = read_json(native / "result.json")
        assert isinstance(native_result, dict)
        assert native_result["verifier_result"]["rewards"] == {"reward": 0.0, "other": -1.0}
        assert (native / "agent/trajectory.json").read_text() == '{"trace": "retained"}'
        job_path = output / row["job_spec_path"]
        job = read_json(job_path)
        assert isinstance(job, dict)
        assert job["spec"]["run_id"].startswith("remote-")
        assert (job_path.parent / "bundle.tar.gz").is_file()
    downloads = sum(path.endswith("/download") for _, path in server.calls)
    assert download_helix_results(submission, output, server.client) == result
    assert sum(path.endswith("/download") for _, path in server.calls) == downloads
    (output / result["observations"][0]["native_results_path"] / "result.json").write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        download_helix_results(submission, output, server.client)


@pytest.mark.parametrize(
    "failure", ["malicious", "duplicate", "duplicate_task", "fail_download", "wrong_job", "overwrite_metadata"]
)
def test_bad_or_interrupted_download_never_publishes_output(plan, tmp_path, failure):
    server = Server(plan)
    submit_helix_evals(plan, tmp_path / "submitted", server.client)
    setattr(server, failure, True)
    with pytest.raises((ValueError, httpx.ReadTimeout)):
        download_helix_results(tmp_path / "submitted", tmp_path / "output", server.client)
    assert not (tmp_path / "output").exists()
    setattr(server, failure, False)
    assert len(download_helix_results(tmp_path / "submitted", tmp_path / "output", server.client)["observations"]) == 9


def test_collection_supports_symlinked_output_parent(plan, tmp_path):
    server = Server(plan)
    submitted = tmp_path / "submitted"
    submit_helix_evals(plan, submitted, server.client)
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    result = download_helix_results(submitted, link / "output", server.client)
    assert len(result["observations"]) == 9
    for row in result["observations"]:
        assert (actual / "output" / row["native_results_path"]).is_dir()


def test_destination_binding_and_redacted_http_error(plan, tmp_path):
    server = Server(plan)
    server.client.base_url = "https://other.example.com"
    with pytest.raises(ValueError, match="endpoint"):
        submit_helix_evals(plan, tmp_path, server.client)
    assert not server.calls
    server.client.base_url = plan.execution.base_url
    server.client.client.http_client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, text="private token and native config"))
    )
    with pytest.raises(ValueError, match="HTTP 403") as exc:
        submit_helix_evals(plan, tmp_path, server.client)
    assert "private token" not in str(exc.value)


def test_reject_wrong_case_identity_and_repetitions(plan):
    loaded = load_cases(CASES)
    bindings = copy.deepcopy(plan.bindings)
    bindings.cases_sha256 = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="case definitions"):
        plan_submission(loaded, plan.execution, bindings, "run", publication=plan.publication)
    bindings = copy.deepcopy(plan.bindings)
    next(iter(bindings.cases.values())).targets["baseline"]["n_attempts"] = 2
    with pytest.raises(ValueError, match="prepared agent configuration"):
        plan_submission(loaded, plan.execution, bindings, "run", publication=plan.publication)


@pytest.mark.parametrize("phase", ["completed", "failed", "cancelled", "expired"])
@pytest.mark.parametrize("missing_job", [False, True])
def test_terminal_failures_preserve_successes_and_account_for_missing_trials(plan, tmp_path, phase, missing_job):
    server = Server(plan)
    submit_helix_evals(plan, tmp_path / "submitted", server.client)
    server.phase, server.failed_member, server.missing_job = phase, True, missing_job
    result = download_helix_results(tmp_path / "submitted", tmp_path / "output", server.client)
    assert len(result["observations"]) == 9
    missing = [row for row in result["observations"] if row["status"] == "missing"]
    assert len(missing) == 3
    for row in missing:
        assert "worker did not produce" in row["unavailable_evidence"]["native_results"]
        assert not (tmp_path / "output" / row["native_results_path"]).exists()
        assert (tmp_path / "output" / row["member_path"]).is_file()
        assert (row["job_spec_path"] is None) == missing_job
    assert sum(path.endswith("/download") for _, path in server.calls) == 6


@pytest.mark.parametrize("field", ["source", "agent_kwargs", "env_vars"])
def test_changed_binding_target_never_submits(plan, tmp_path, field):
    server = Server(plan)
    bound = next(iter(plan.bindings.cases.values()))
    bound.targets["baseline"][field] = {"name": "nop"} if field == "source" else {"runtime_path": "/opt/other"}
    with pytest.raises(ValueError, match="target differs"):
        submit_helix_evals(plan, tmp_path, server.client)
    assert not server.calls


def test_changed_publication_identity_never_submits(plan, tmp_path):
    server = Server(plan)
    plan.bindings.publication_sha256 = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="publication identity"):
        submit_helix_evals(plan, tmp_path, server.client)
    assert not server.calls


def test_requests_match_installed_helix_schema(plan):
    schemas = pytest.importorskip(
        "nemo_evals.api.taskset_run_schemas", reason="optional pinned Helix source environment"
    )
    for run in plan.runs:
        request = schemas.TasksetRunInput.model_validate(run.request)
        assert request.execution.kind == "harbor-task-jobs"
        assert request.spec.target.n_attempts == 1
        assert request.spec.target.max_retries == 0


@pytest.mark.parametrize("change", ["missing-run", "changed-case"])
def test_submission_snapshot_cannot_silently_change_the_matrix(plan, change):
    data = plan.model_dump()
    if change == "missing-run":
        data["runs"].pop()
    else:
        data["cases"][0]["baseline"]["revision"] = "new"
    with pytest.raises(ValueError, match="matrix|identity"):
        Submission.model_validate(data)


def test_interrupted_collection_reuses_verified_downloads(plan, tmp_path):
    server = Server(plan)
    submit_helix_evals(plan, tmp_path / "submitted", server.client)
    server.fail_download_after = 2
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="ReadTimeout"):
        download_helix_results(tmp_path / "submitted", output, server.client)
    assert not output.exists()
    assert len(list((tmp_path / ".output.helix-downloads").glob("*.tar.gz"))) == 2
    server.fail_download_after = None
    result = download_helix_results(tmp_path / "submitted", output, server.client)
    assert len(result["observations"]) == 9
    assert sum(p.endswith("/download") for _, p in server.calls) == 10
    assert not (tmp_path / ".output.helix-downloads").exists()


def test_image_build_readiness_uses_creation_time_not_server_order():
    from metric_benchmark.helix import latest_default_build

    old = {"platform": None, "created_at": "2026-11-01T00:00:00", "status": "ready"}
    new = {"platform": None, "created_at": "2026-11-02T00:00:00Z", "status": "running"}
    assert latest_default_build([old, new]) == latest_default_build([new, old]) == new
    with pytest.raises(ValueError, match="ambiguous"):
        latest_default_build([new, dict(new, status="ready")])
    with pytest.raises(ValueError, match="timestamps"):
        latest_default_build([old, {"platform": None, "status": "ready"}])
