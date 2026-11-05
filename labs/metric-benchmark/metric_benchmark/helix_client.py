# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small authenticated client for native Helix taskset runs and task artifacts."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import quote

from httpx import HTTPError

from metric_benchmark.config import HelixExecution, check_base_url


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
        for name in ("nemo-helix-plugin", "nemo-evals-plugin", "nhx-evals-sdk", "harbor"):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                packages[name] = None
        if packages["harbor"] != "0.24.0":
            raise ValueError("Helix publication/execution requires the qualified Harbor 0.24.0 client environment")
        return {"client_versions": packages, "server": self.request("GET", "/cluster-info")}
