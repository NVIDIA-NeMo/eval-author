# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small authenticated client for native Helix taskset runs and task artifacts."""

from importlib import import_module
from pathlib import Path
from urllib.parse import quote, urlsplit

from httpx import HTTPError


class HelixClient:
    def __init__(self, client, workspace: str):
        self.client = client
        self.workspace = workspace
        self.base_url = str(client.base_url).rstrip("/")
        self.prefix = f"/apis/evals/v2/workspaces/{quote(workspace, safe='')}/"

    @classmethod
    def from_context(cls, workspace: str, *, context: str | None = None):
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
        if response.status_code not in (200, 201):
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

    def submit(self, body):
        return self.request("POST", self.prefix + "agent-evaluate/runs", body)

    def status(self, run_id):
        return self.request("GET", self.prefix + f"agent-evaluate/runs/{quote(run_id, safe='')}")

    def members(self, run_id):
        rows = []
        page = 1
        while True:
            result = self.request(
                "GET", self.prefix + f"agent-evaluate/runs/{quote(run_id, safe='')}/tasks?page={page}&page_size=1000"
            )
            pagination = result["pagination"]
            if pagination["page"] != page or (page > 1 and not result["data"]):
                raise ValueError("invalid Helix task pagination")
            rows.extend(result["data"])
            if page >= pagination["total_pages"]:
                if len(rows) != pagination["total_results"]:
                    raise ValueError("Helix task pagination changed; retry collection")
                return rows
            page += 1

    def artifact(self, job: str):
        path = f"/apis/jobs/v2/workspaces/{quote(self.workspace, safe='')}/jobs/{quote(job, safe='')}/results/agent-eval-results"
        return self.request("GET", path)

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

    def require_destination(self, base_url: str, workspace: str):
        expected = urlsplit(base_url)
        if (
            expected.scheme not in ("http", "https")
            or not expected.hostname
            or expected.username
            or expected.password
            or expected.query
            or expected.fragment
        ):
            raise ValueError("Helix base URL must be an HTTP(S) endpoint without credentials")
        if self.base_url != base_url.rstrip("/") or self.workspace != workspace:
            raise ValueError("Helix context differs from the recorded endpoint/workspace; select the matching context")
