# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Optional Helix SDK adapter, imported only in the separate client environment."""

import tempfile
from importlib import import_module
from pathlib import Path
from urllib.parse import quote

import yaml

from metric_benchmark.config import StrictLoader
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.io import digest_file, file_manifest, input_digest


class HelixPublisher(HelixClient):
    def __init__(self, client, workspace):
        super().__init__(client, workspace)
        Evaluator = import_module("nemo_evals.sdk.resources").Evaluator

        self.evaluator = Evaluator.from_client(client)

    def validate_task(self, directory):
        discover = import_module("nhx_evals_sdk.agent_eval.runtimes.harbor.runtime").discover_harbor_tasks
        describe = import_module("nemo_evals.sdk.task_preparation").describe_harbor_task
        (source,) = discover(directory)
        return source, describe(source, workspace=self.workspace)

    def validate_agent(self, bundle, config_path):
        AgentConfig = import_module("nemo_agents_plugin.agent_config").AgentConfig
        RegisterAgentRequest = import_module("nemo_helix_plugin.agents.types").RegisterAgentRequest

        config = yaml.load((bundle / config_path).read_text(), Loader=StrictLoader)
        validated = AgentConfig.model_validate(config)
        if "${NEMO_DEFAULT_MODEL}" in str(config):
            raise ValueError("published agent config must select an explicit model")
        registration = config.pop("helix_registration", None) or {}
        config = AgentConfig.model_validate(config).model_dump(mode="json", exclude_none=True)
        request = RegisterAgentRequest.model_validate({"name": "validation", "config": config, **registration})
        return validated, request

    def publish_agent(self, name, bundle, config_path):
        FilesetFileSystem = import_module("filesets").FilesetFileSystem
        ethos_fileset_name = import_module("nemo_agents_plugin.entities").ethos_fileset_name
        AgentsClient = import_module("nemo_helix_plugin.agents.client").AgentsClient
        NotFoundError = import_module("nemo_helix_plugin.client.errors").NotFoundError
        FilesClient = import_module("nemo_helix_plugin.files.client").FilesClient
        FilesetFileManager = import_module("nemo_helix_plugin.jobs.file_manager").FilesetFileManager

        _, request = self.validate_agent(bundle, config_path)
        request.name = name
        agents = AgentsClient.from_client(self.client)
        try:
            remote = agents.get_agent(name=name, workspace=self.workspace).data()
        except NotFoundError:
            remote = agents.register_agent(body=request, workspace=self.workspace, exist_ok=True).data()
        expected = request.model_dump(mode="json", exclude={"name", "description"}, exclude_none=True)
        actual = remote.model_dump(mode="json")
        for key, value in expected.items():
            if actual.get(key) != value:
                raise ValueError(f"registered agent differs from prepared request: {key}")
        fileset = ethos_fileset_name(name)
        files = FilesetFileManager(
            workspace=self.workspace,
            fileset_name=fileset,
            filesystem=FilesetFileSystem(client=FilesClient.from_client(self.client)),
        )
        files.validate_storage()
        # A content-derived name makes interrupted uploads resumable. Download the
        # entire fileset afterwards: extra files and changed bytes are both errors.
        files.upload(bundle, remote_path="")
        with tempfile.TemporaryDirectory(prefix="metric-benchmark-agent-") as scratch:
            files.download_from_url(f"{self.workspace}/{fileset}", local_dir=Path(scratch))
            if file_manifest(Path(scratch)) != file_manifest(bundle):
                raise ValueError("uploaded agent files differ from the prepared bundle")
        return {
            "ref": f"{self.workspace}/{name}",
            "request": request.model_dump(mode="json"),
            "fileset": f"{self.workspace}/{fileset}",
            "sha256": input_digest(bundle),
            "files": file_manifest(bundle),
        }

    def publish_task(self, name, directory, archive_path):
        pinned_task_ref = import_module("nemo_evals.runs_cli").pinned_task_ref
        NotFoundError = import_module("nemo_helix_plugin.client.errors").NotFoundError
        source, expected = self.validate_task(directory)
        try:
            remote = self.evaluator.tasks.retrieve(name, workspace=self.workspace)
        except NotFoundError:
            remote = self.evaluator.tasks.replace(name, task=source, workspace=self.workspace)
        if remote.spec != expected.spec or remote.metadata != expected.metadata:
            raise ValueError("published task differs from prepared task")
        ref = pinned_task_ref(self.evaluator, self.workspace, name, remote.revision)
        spec = remote.spec.model_dump(mode="json")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if archive_path.is_symlink():
            raise ValueError("task archive output must not be a symlink")
        if not archive_path.exists():
            import_module("nemo_evals.harbor.archive").pack_task(directory, archive_path)
        if digest_file(archive_path) != "sha256:" + spec["source"]["files_hash"]:
            raise ValueError("retained task archive differs from the published source")
        return {
            "ref": ref,
            "native_task_id": spec["native_task_id"],
            "spec": spec,
            "metadata": remote.metadata,
            "sha256": input_digest(directory),
        }

    def publish_taskset(self, name, refs):
        publish_taskset = import_module("nemo_evals.runs_cli").publish_taskset

        ref = publish_taskset(self.evaluator, refs, name=name, workspace=self.workspace)
        members = self.revision("tasksets", ref)["tasks"]
        if len(members) != len(refs) or set(members) != set(refs):
            raise ValueError("published taskset differs from planned membership")
        return ref

    def request_builds(self, refs):
        builds = {}
        for ref in refs:
            owner, digest = ref.split("#")
            workspace, name = owner.split("/")
            # Servers may add image evidence fields before the client's strict
            # ImageBuild model knows them. Preserve the native response instead.
            builds[ref] = self.request(
                "POST",
                f"/apis/evals/v2/workspaces/{quote(workspace, safe='')}/tasks/"
                f"{quote(name, safe='')}/revisions/{digest}/image-builds",
                {},
            )
        return builds
