# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

import httpx
import pytest
from metric_benchmark.config import HelixExecution
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.helix_publisher import HelixPublisher
from metric_benchmark.io import digest_file


def test_context_destination_is_resolved_and_then_asserted():
    native = SimpleNamespace(base_url="https://helix.example.com/", require_workspace=lambda: "default")
    client = HelixClient(native, None)
    configured = HelixExecution(dispatcher_profile="workers")
    resolved = client.resolve_execution(configured)
    assert configured.workspace is None
    assert resolved.workspace == "default"
    assert resolved.base_url == "https://helix.example.com"
    with pytest.raises(ValueError, match="endpoint/workspace"):
        client.resolve_execution(HelixExecution(base_url="https://other.example.com", dispatcher_profile="workers"))


def test_build_requests_preserve_additive_fields_and_accept_202():
    response = {"id": "2", "status": "running", "images": [{"context_digest": "new-field"}]}
    client = object.__new__(HelixPublisher)
    HelixClient.__init__(
        client,
        SimpleNamespace(
            base_url="https://helix.example.com",
            request_headers=lambda *args, **kwargs: {},
            http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(202, json=response))),
        ),
        "default",
    )
    ref = "default/task#" + "a" * 64
    assert client.request_builds([ref]) == {ref: response}


def test_publisher_rejects_archive_not_matching_remote_source(tmp_path, monkeypatch):
    from metric_benchmark import helix_publisher

    class NotFoundError(Exception):
        pass

    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"exact archive")
    spec = SimpleNamespace(model_dump=lambda **kwargs: {"source": {"files_hash": "0" * 64}, "native_task_id": "task"})
    remote = SimpleNamespace(spec=spec, metadata={}, revision="revision")
    client = object.__new__(HelixPublisher)
    client.workspace = "default"
    client.evaluator = SimpleNamespace(tasks=SimpleNamespace(retrieve=lambda *args, **kwargs: remote))
    monkeypatch.setattr(client, "validate_task", lambda path: (object(), remote))
    monkeypatch.setattr(
        helix_publisher,
        "import_module",
        lambda name: SimpleNamespace(
            NotFoundError=NotFoundError,
            pinned_task_ref=lambda *args: "default/task#" + "a" * 64,
        ),
    )
    with pytest.raises(ValueError, match="retained task archive differs"):
        client.publish_task("task", tmp_path, archive)
    spec.model_dump = lambda **kwargs: {"source": {"files_hash": digest_file(archive)[7:]}, "native_task_id": "task"}
    assert client.publish_task("task", tmp_path, archive)["native_task_id"] == "task"


def test_provenance_checks_client_version_before_server_request(monkeypatch):
    from metric_benchmark import helix_client

    client = HelixClient(SimpleNamespace(base_url="https://helix.example.com"), "default")
    observed = []
    monkeypatch.setattr(
        client, "request", lambda method, path: observed.append(path) or {"revision": "server-revision"}
    )
    monkeypatch.setattr(helix_client, "version", lambda name: "0.20.0")
    with pytest.raises(ValueError, match="qualified Harbor"):
        client.provenance()
    assert not observed
    monkeypatch.setattr(helix_client, "version", lambda name: "0.24.0" if name == "harbor" else "client-version")
    provenance = client.provenance()
    assert provenance["server"]["revision"] == "server-revision"
    assert provenance["client_versions"]["harbor"] == "0.24.0"
