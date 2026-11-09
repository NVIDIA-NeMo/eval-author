# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Optional Helix SDK adapter, imported only in the separate client environment."""

import shutil
import tempfile
from importlib import import_module
from pathlib import Path

import yaml

from metric_benchmark.config import StrictLoader
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.io import digest_file, file_manifest, input_digest, write_json


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
        if "${NEMO_DEFAULT_MODEL}" in str(config):
            raise ValueError("published agent config must select an explicit model")
        validated = AgentConfig.model_validate(config)
        config = validated.model_dump(mode="json", exclude_none=True)
        request = RegisterAgentRequest.model_validate({"name": "validation", "config": config})
        return validated, request

    def publish_agent(self, name, bundle, config_path):
        ethos_fileset_name = import_module("nemo_agents_plugin.entities").ethos_fileset_name
        AgentsClient = import_module("nemo_helix_plugin.agents.client").AgentsClient
        NotFoundError = import_module("nemo_helix_plugin.client.errors").NotFoundError

        _, request = self.validate_agent(bundle, config_path)
        request.name = name
        agents = AgentsClient.from_client(self.client)
        try:
            remote = agents.get_agent(name=name, workspace=self.workspace).data()
        except NotFoundError:
            remote = agents.register_agent(body=request, workspace=self.workspace, exist_ok=False).data()
        expected = request.model_dump(mode="json", exclude={"name", "description"}, exclude_none=True)
        actual = remote.model_dump(mode="json")
        for key, value in expected.items():
            if actual.get(key) != value:
                raise ValueError(f"registered agent differs from prepared request: {key}")
        fileset = ethos_fileset_name(name)
        self._retain_files(fileset, bundle)
        return {
            "ref": f"{self.workspace}/{name}",
            "request": request.model_dump(mode="json"),
            "fileset": f"{self.workspace}/{fileset}",
            "sha256": input_digest(bundle),
            "files": file_manifest(bundle),
        }

    def _retain_files(self, name, source):
        FilesetFileSystem = import_module("filesets").FilesetFileSystem
        FilesClient = import_module("nemo_helix_plugin.files.client").FilesClient
        FilesetFileManager = import_module("nemo_helix_plugin.jobs.file_manager").FilesetFileManager
        files = FilesetFileManager(
            workspace=self.workspace,
            fileset_name=name,
            filesystem=FilesetFileSystem(client=FilesClient.from_client(self.client)),
        )
        files.validate_storage()
        ref = f"{self.workspace}/{name}"
        expected = file_manifest(source)
        with tempfile.TemporaryDirectory(prefix="metric-benchmark-existing-source-") as scratch:
            files.download_from_url(ref, local_dir=Path(scratch))
            existing = file_manifest(Path(scratch))
            if any(expected.get(path) != digest for path, digest in existing.items()):
                raise ValueError("retained source differs; refusing to overwrite")
        for path in expected.keys() - existing.keys():
            files.upload(source / path, remote_path=path)
        with tempfile.TemporaryDirectory(prefix="metric-benchmark-verify-source-") as scratch:
            files.download_from_url(ref, local_dir=Path(scratch))
            if file_manifest(Path(scratch)) != expected:
                raise ValueError("retained source differs after upload")
        return {"ref": ref, "files": expected, "sha256": input_digest(source)}

    def retain_agent(self, name, bundle, definition):
        # This ordinary FileSet has no agent/run owner and no snapshot TTL.
        # Registration and run cleanup must never manage this retained copy.
        with tempfile.TemporaryDirectory(prefix="metric-benchmark-retain-agent-") as scratch:
            root = Path(scratch)
            shutil.copytree(bundle, root / "bundle")
            write_json(root / "agent.json", definition)
            return self._retain_files("mb-sources-" + input_digest(root)[7:55], root)

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

    def reuse_task(self, ref, directory, archive_path):
        _, expected = self.validate_task(directory)
        remote = self.revision("tasks", ref)
        spec = remote["spec"]
        if spec != expected.spec.model_dump(mode="json") or remote.get("metadata", []) != expected.metadata:
            raise ValueError("existing task differs from prepared task")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if archive_path.is_symlink():
            raise ValueError("task archive output must not be a symlink")
        if not archive_path.exists():
            import_module("nemo_evals.harbor.archive").pack_task(directory, archive_path)
        if digest_file(archive_path) != "sha256:" + spec["source"]["files_hash"]:
            raise ValueError("existing task archive differs from prepared task")
        return {
            "ref": ref,
            "native_task_id": spec["native_task_id"],
            "spec": spec,
            "metadata": remote.get("metadata", []),
            "sha256": input_digest(directory),
        }

    def publish_taskset(self, name, refs):
        publish_taskset = import_module("nemo_evals.runs_cli").publish_taskset

        ref = publish_taskset(self.evaluator, refs, name=name, workspace=self.workspace)
        members = self.revision("tasksets", ref)["tasks"]
        if len(members) != len(refs) or set(members) != set(refs):
            raise ValueError("published taskset differs from planned membership")
        return ref
