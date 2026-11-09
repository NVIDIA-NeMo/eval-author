# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Portable publication references and verified source recovery."""

import argparse
import shutil
import tempfile
from importlib import import_module
from pathlib import Path

from pydantic import JsonValue, model_validator

from metric_benchmark.config import LoadedCase, LoadedCases
from metric_benchmark.helix_client import HelixClient
from metric_benchmark.helix_publication import HelixBindings, PublicationPlan, PublishCase
from metric_benchmark.io import cli, digest_file, file_manifest, input_digest, read_json, read_object
from metric_benchmark.models import Model, relative_path, resolve_evidence


class References(Model):
    publication: PublicationPlan
    bindings: HelixBindings
    provenance: dict[str, JsonValue]

    @model_validator(mode="after")
    def identities(self):
        if self.bindings.publication_sha256 != self.publication.identity:
            raise ValueError("references differ from publication identity")
        loaded = self.inputs(Path("."))
        if loaded.identity != self.publication.cases_sha256 or loaded.identity != self.bindings.cases_sha256:
            raise ValueError("references differ from prepared case identity")
        if set(self.bindings.cases) != set(loaded.cases):
            raise ValueError("references case membership differs")
        return self

    def inputs(self, root: Path) -> LoadedCases:
        return LoadedCases({case.case_id: LoadedCase(case, root) for case in self.publication.definitions}, root)


def resource_selection(value):
    selected = frozenset(value.split(","))
    if not selected or selected - {"agents", "tasks"}:
        raise ValueError("resources must be agents, tasks, or agents,tasks")
    return selected


def load_references(path: Path, *, run=False) -> References:
    value = read_object(path)
    return References.model_validate(value["references"] if run else value)


def combine_references(previous: References | None, current: References) -> References:
    if previous is None:
        return current
    if (
        previous.publication.definitions != current.publication.definitions
        or previous.publication.destination != current.publication.destination
    ):
        raise ValueError("cannot combine references from different experiments or destinations")
    plan = current.publication.model_copy(deep=True)
    bindings = current.bindings.model_copy(deep=True)
    old_cases = {case.case_id: case for case in previous.publication.cases}
    for index, case in enumerate(plan.cases):
        old = old_cases[case.case_id]
        fields = {}
        for field, identity in (("agents", "agent_bundle_id"), ("tasks", "task_id")):
            entries = {getattr(item, identity): item for item in getattr(old, field)}
            for item in getattr(case, field):
                key = getattr(item, identity)
                if key in entries and entries[key] != item:
                    raise ValueError("publication references conflict")
                entries[key] = item
            fields[field] = list(entries.values())
        plan.cases[index] = PublishCase(case_id=case.case_id, **fields)
        bound = bindings.cases[case.case_id]
        old_bound = previous.bindings.cases[case.case_id]
        for field in ("agents", "targets", "registrations", "tasks"):
            entries = dict(getattr(old_bound, field))
            for key, value in getattr(bound, field).items():
                if key in entries and entries[key] != value:
                    raise ValueError("remote resource references conflict")
                entries[key] = value
            setattr(bound, field, entries)
        if bound.taskset and old_bound.taskset and bound.taskset != old_bound.taskset:
            raise ValueError("taskset references conflict")
        bound.taskset = bound.taskset or old_bound.taskset
    bindings.publication_sha256 = plan.identity
    return References(publication=plan, bindings=bindings, provenance=current.provenance)


def _install(source, destination, digest):
    if input_digest(source) != digest:
        raise ValueError("downloaded source differs from its signed input")
    if destination.exists():
        if input_digest(destination) != digest:
            raise ValueError("existing local source conflicts with published contents")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".helix-source-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "source"
        shutil.copytree(source, staged)
        if input_digest(staged) != digest:
            raise ValueError("source changed during installation")
        staged.rename(destination)


def source_destination(root, relative):
    relative_path(relative)
    destination = (root / relative).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError("source destination escapes output directory")
    return destination


def download_sources(
    references: References, output: Path, client, *, resources=frozenset({"agents", "tasks"}), selection=None
):
    if selection is not None and selection not in {
        (case.case_id, agent.agent_bundle_id) for case in references.publication.definitions for agent in case.agents()
    }:
        raise ValueError("source selection must name a prepared case and agent")
    destination = references.publication.destination
    client.require_destination(destination.base_url, destination.workspace)
    for case in references.publication.definitions:
        bound = references.bindings.cases[case.case_id]
        for agent in case.agents() if "agents" in resources else []:
            if selection is not None and (case.case_id, agent.agent_bundle_id) != selection:
                continue
            if agent.registered is None:
                continue
            receipt = bound.registrations.get(agent.agent_bundle_id)
            if receipt is None:
                raise ValueError("selected agent sources have not been published")
            source = agent.inputs[agent.registered.bundle_input]
            target = source_destination(output, source.path)
            if target.exists():
                if input_digest(target) != source.sha256:
                    raise ValueError("existing local source conflicts with published contents")
                continue
            retained = receipt["retained_source"]
            if not isinstance(retained, dict):
                raise ValueError("invalid retained agent source")
            with tempfile.TemporaryDirectory(prefix="helix-agent-source-") as scratch:
                scratch = Path(scratch)
                client.download_files(retained["ref"], scratch)
                if file_manifest(scratch) != retained["files"] or input_digest(scratch) != retained["sha256"]:
                    raise ValueError("retained agent source is missing or changed")
                if read_json(resolve_evidence(scratch, "agent.json")) != agent.model_dump(mode="json"):
                    raise ValueError("retained signed agent definition differs")
                _install(resolve_evidence(scratch, "bundle"), target, source.sha256)
        if selection is not None and case.case_id != selection[0]:
            continue
        for task_id, source in case.task_sources.items() if "tasks" in resources else []:
            bound_task = bound.tasks.get(task_id)
            if bound_task is None:
                raise ValueError("selected task sources have not been published")
            target = source_destination(output, source.path)
            if target.exists():
                if input_digest(target) != source.sha256:
                    raise ValueError("existing local source conflicts with published contents")
                continue
            with tempfile.TemporaryDirectory(prefix="helix-task-source-") as scratch:
                scratch = Path(scratch)
                remote = client.revision("tasks", bound_task.ref)
                if remote["spec"]["source"] != bound_task.source:
                    raise ValueError("pinned task source differs from publication")
                client.download_files(bound_task.source["fileset_ref"], scratch / "archive")
                archives = [p for p in (scratch / "archive").rglob("*") if p.is_file()]
                if len(archives) != 1 or digest_file(archives[0]) != f"sha256:{bound_task.source['files_hash']}":
                    raise ValueError("downloaded task archive differs from pinned revision")
                (scratch / "unpacked").mkdir()
                source_dir, _ = import_module("nemo_evals.harbor.archive").extract_task(
                    archives[0], scratch / "unpacked"
                )
                _install(source_dir, target, source.sha256)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--references", type=Path)
    inputs.add_argument("--run", type=Path)
    parser.add_argument("--resources", default="agents,tasks", type=resource_selection)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)

    def run(args):
        references = load_references(args.run or args.references, run=args.run is not None)
        receipt = read_object(args.run) if args.run else None
        if receipt is not None and receipt.get("response") is None:
            raise ValueError("run was not accepted; use --references to download published sources")
        selection = (receipt["case_id"], receipt["agent_bundle_id"]) if receipt else None
        client = HelixClient.from_settings(args.client)
        try:
            download_sources(references, args.output, client, resources=args.resources, selection=selection)
        finally:
            client.client.close()

    cli(parser, run)
