# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small authenticated client for native Helix taskset runs and task artifacts."""

import os
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from httpx import HTTPError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from metric_benchmark.config import HelixExecution, Workspace, check_base_url, read_yaml
from metric_benchmark.models import Model, Text


class HelixDestination(Model):
    base_url: Text
    workspace: Workspace

    @model_validator(mode="after")
    def valid_url(self):
        self.base_url = check_base_url(self.base_url)
        return self


class ClientAuth(Model):
    access_token_env: Text | None = None
    context: Text | None = None
    config_path: Text | None = None

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.access_token_env) == bool(self.context) or (self.config_path and not self.context):
            raise ValueError("select access_token_env or a supported Helix context")
        return self


class ClientSettings(HelixDestination):
    auth: ClientAuth


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    run_id: str
    workspace: str
    taskset_ref: str
    idempotency_key: str
    phase: Literal["accepted", "running", "completed", "failed", "cancelled", "expired"]
    result_state: Literal["pending", "ready", "failed", "none"]


class RunMember(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    task: str
    task_index: int = Field(ge=0)
    phase: Literal["image_preparing", "image_ready", "registered", "succeeded", "failed", "cancelled", "expired"]
    job: str | None = None
    error: str | None = None


def native_response(model, value):
    try:
        model.model_validate(value)
    except ValueError:
        raise ValueError(f"Helix returned an invalid {model.__name__} response") from None
    return value


class HelixClient:
    def __init__(self, client, workspace: str | None):
        self.client = client
        self.workspace = workspace if workspace is not None else client.require_workspace()
        self.base_url = check_base_url(str(client.base_url))
        self.prefix = f"/apis/evals/v2/workspaces/{quote(self.workspace, safe='')}/"

    @classmethod
    def from_context(cls, workspace: str | None = None, *, context: str | None = None):
        try:
            nemo_client = import_module("nemo_helix_plugin.client.client").NemoClient
        except ImportError:
            raise ValueError(
                "run this prototype in a Helix client environment; nemo-helix-plugin is required"
            ) from None
        return cls(nemo_client.from_config(context=context), workspace)

    @classmethod
    def from_settings(cls, path: Path):
        settings = ClientSettings.model_validate(read_yaml(path))
        native = import_module("nemo_helix_plugin.client.client").NemoClient
        if settings.auth.context:
            client = native.from_config(context=settings.auth.context, config_path=settings.auth.config_path)
        else:
            token = os.environ.get(settings.auth.access_token_env or "")
            if not token:
                raise ValueError("configured Helix access-token environment variable is empty")
            client = native(base_url=settings.base_url, workspace=settings.workspace, auth=token)
        try:
            result = cls(client, settings.workspace)
            result.require_destination(settings.base_url, settings.workspace)
            return result
        except Exception:
            client.close()
            raise

    def download_files(self, reference, output):
        FilesetFileSystem = import_module("filesets").FilesetFileSystem
        FilesClient = import_module("nemo_helix_plugin.files.client").FilesClient
        FilesetFileManager = import_module("nemo_helix_plugin.jobs.file_manager").FilesetFileManager
        workspace, name = reference.split("#", 1)[0].split("/", 1)
        manager = FilesetFileManager(
            workspace=workspace,
            fileset_name=name,
            filesystem=FilesetFileSystem(client=FilesClient.from_client(self.client)),
            ensure_fileset_exists=False,
        )
        output.mkdir(parents=True, exist_ok=True)
        manager.download_from_url(reference, local_dir=output)

    def request(self, method: str, path: str, body=None):
        url = self.base_url + path
        try:
            response = self.client.http_client.request(
                method,
                url,
                json=body,
                headers=self.client.request_headers(None, url=url),
                timeout=120,
                follow_redirects=False,
            )
        except HTTPError as error:
            raise ValueError(
                f"Helix {method} {path}: {type(error).__name__}; retry using the same submission directory"
            ) from None
        if response.status_code not in (200, 201, 202):
            # Do not echo server bodies: they can contain native configuration or credentials.
            raise ValueError(f"Helix {method} {path}: HTTP {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("expected a Helix JSON object response")
        return body

    def revision(self, collection: str, reference: str):
        owner, fragment = reference.split("#")
        workspace, name = owner.split("/")
        return self.request(
            "GET",
            f"/apis/evals/v2/workspaces/{quote(workspace, safe='')}/{collection}/"
            f"{quote(name, safe='')}/revisions/{quote(fragment, safe='')}",
        )

    def require_destination(self, base_url: str | None, workspace: str | None):
        if base_url is not None and self.base_url != check_base_url(base_url):
            raise ValueError("Helix context differs from the recorded endpoint/workspace; select the matching context")
        if workspace is not None and self.workspace != workspace:
            raise ValueError("Helix context differs from the recorded endpoint/workspace; select the matching context")

    def resolve_execution(self, execution: HelixExecution) -> HelixExecution:
        self.require_destination(execution.base_url, execution.workspace)
        return HelixExecution.model_validate(
            {**execution.model_dump(), "base_url": self.base_url, "workspace": self.workspace}
        )

    def provenance(self) -> dict:
        packages = {}
        for name in (
            "nemo-helix-plugin",
            "nemo-evals-plugin",
            "nemo-agents-plugin",
            "nhx-evals-sdk",
            "harbor",
            "nemo-fabric",
            "nemo-fabric-runtime",
            "nemo-fabric-adapter-contract",
        ):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = None
        if packages["harbor"] != "0.24.0":
            raise ValueError("Helix publication/execution requires the qualified Harbor 0.24.0 client environment")
        return {"client_versions": packages, "server": self.request("GET", "/cluster-info")}

    def verify_agent(self, receipt):
        import tempfile

        from metric_benchmark.io import file_manifest

        AgentsClient = import_module("nemo_helix_plugin.agents.client").AgentsClient
        workspace, name = receipt["ref"].split("/", 1)
        actual = (
            AgentsClient.from_client(self.client)
            .get_agent(name=name, workspace=workspace)
            .data()
            .model_dump(mode="json")
        )
        for key, value in receipt["request"].items():
            if key not in {"name", "description"} and value is not None and actual.get(key) != value:
                raise ValueError("registered agent changed since publication")
        with tempfile.TemporaryDirectory(prefix="metric-benchmark-verify-agent-") as temporary:
            self.download_files(receipt["fileset"], Path(temporary))
            if file_manifest(Path(temporary)) != receipt["files"]:
                raise ValueError("registered agent files changed since publication")

    def submit(self, body):
        return native_response(RunStatus, self.request("POST", self.prefix + "agent-evaluate/runs", body))

    def request_builds(self, refs):
        builds = {}
        for ref in refs:
            owner, digest = ref.split("#")
            workspace, name = owner.split("/")
            builds[ref] = self.request(
                "POST",
                f"/apis/evals/v2/workspaces/{quote(workspace, safe='')}/tasks/"
                f"{quote(name, safe='')}/revisions/{digest}/image-builds",
                {},
            )
        return builds

    def image_builds(self, reference):
        owner, digest = reference.split("#")
        workspace, name = owner.split("/")
        return self.request(
            "GET",
            f"/apis/evals/v2/workspaces/{quote(workspace, safe='')}/tasks/"
            f"{quote(name, safe='')}/revisions/{digest}/image-builds",
        )

    def status(self, run_id):
        return native_response(
            RunStatus, self.request("GET", self.prefix + f"agent-evaluate/runs/{quote(run_id, safe='')}")
        )

    def members(self, run_id):
        rows = []
        page = 1
        while True:
            result = self.request(
                "GET", self.prefix + f"agent-evaluate/runs/{quote(run_id, safe='')}/tasks?page={page}&page_size=1000"
            )
            pagination = result.get("pagination")
            if (
                not isinstance(pagination, dict)
                or not isinstance(result.get("data"), list)
                or any(type(pagination.get(key)) is not int for key in ("page", "total_pages", "total_results"))
            ):
                raise ValueError("invalid Helix task pagination")
            if pagination["page"] != page or (page > 1 and not result["data"]):
                raise ValueError("invalid Helix task pagination")
            rows.extend(native_response(RunMember, row) for row in result["data"])
            if page >= pagination["total_pages"]:
                if len(rows) != pagination["total_results"]:
                    raise ValueError("Helix task pagination changed; retry collection")
                return rows
            page += 1

    def artifact(self, job: str):
        path = f"/apis/jobs/v2/workspaces/{quote(self.workspace, safe='')}/jobs/{quote(job, safe='')}/results/agent-eval-results"
        return self.request("GET", path)

    def job(self, job: str):
        return self.request(
            "GET", f"/apis/jobs/v2/workspaces/{quote(self.workspace, safe='')}/jobs/{quote(job, safe='')}"
        )

    def download(self, job: str, output: Path):
        path = f"/apis/jobs/v2/workspaces/{quote(self.workspace, safe='')}/jobs/{quote(job, safe='')}/results/agent-eval-results/download"
        url = self.base_url + path
        try:
            with self.client.http_client.stream(
                "GET", url, headers=self.client.request_headers(None, url=url), timeout=120, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise ValueError(f"Helix artifact download: HTTP {response.status_code}")
                with output.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
        except HTTPError as error:
            raise ValueError(f"Helix artifact download: {type(error).__name__}; rerun collection") from None
