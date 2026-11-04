# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify preserved Helix task and agent identities against prepared inputs."""

import copy
import tarfile
import tomllib
from pathlib import Path

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig

from metric_benchmark.helix import Submission, _object
from metric_benchmark.helix_publication import PublicationPlan
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, input_digest, json_bytes
from metric_benchmark.models import Model, Text, relative_path, resolve_evidence


class HelixIdentity(Model):
    collection_path: Text
    publication_path: Text
    source_archive_path: Text
    effective_task_path: Text


def collection_record(directory):
    record = _object(resolve_evidence(directory, "collection.json"))
    files = file_manifest(directory)
    files.pop("collection.json", None)
    if files != record["files"]:
        raise ValueError("collected evidence changed")
    saved_plan = _object(resolve_evidence(directory, "submission.json"))
    plan = Submission.model_validate(saved_plan)
    # New model defaults must not change the identity of a preserved submission.
    if record["submission_sha256"] != digest_bytes(json_bytes(saved_plan)):
        raise ValueError("collection submission identity differs")
    if record["cases_sha256"] != plan.cases_sha256 or record["run_id"] != plan.run_id:
        raise ValueError("collection case or run identity differs")
    expected = {
        (run.case_id, run.agent_bundle_id, task_id)
        for run in plan.runs
        for task_id in plan.bindings.cases[run.case_id].tasks
    }
    observed = [(row["case_id"], row["agent_bundle_id"], row["task_id"]) for row in record["observations"]]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("collection does not contain the complete expected matrix")
    return record, plan


def verify_archive(archive, source, expected_digest):
    if digest_file(archive) != "sha256:" + expected_digest:
        raise ValueError("source archive differs from resolved job")
    files = {}
    seen = set()
    with tarfile.open(archive) as tar:
        for member in tar:
            relative_path(member.name)
            parts = Path(member.name).parts
            if parts[0] != source.name or member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError("source archive has an unsafe or unexpected member")
            seen.add(member.name)
            if member.isfile():
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("source archive file is absent")
                files[Path(*parts[1:]).as_posix()] = digest_bytes(stream.read())
    if files != file_manifest(source):
        raise ValueError("source archive differs from prepared task files")


def normalized_target(target):
    # Defaults of the deployed Harbor target schema; fail closed on new behavior.
    defaults = {
        "kind": "harbor",
        "source": {},
        "agent_kwargs": {},
        "env_vars": {},
        "env_secrets": {},
        "agent_setup_timeout_multiplier": None,
        "agent_timeout_multiplier": None,
        "artifacts": [],
        "closed_book": False,
        "max_retries": 0,
        "n_attempts": 1,
        "n_concurrent_trials": None,
        "reward_key": "reward",
        "trace_dir": None,
    }
    if set(target) - defaults.keys():
        raise ValueError("unsupported Helix target fields")
    result = {**defaults, **target}
    result["source"] = {"model_name": None, **result["source"]}
    # Earlier deployed built-in sources serialized an unused null `model` field.
    if "model" in result["source"] and result["source"]["model"] is None:
        result["source"].pop("model")
    return result


def verify_native_agent(trial, target, policy):
    source = target["source"]
    if set(source) - {"name", "import_path", "model_name"}:
        raise ValueError("resolved agent source needs a supported identity mapping")
    if bool(source.get("name")) == bool(source.get("import_path")):
        raise ValueError("expected one explicit native agent source")
    kwargs = dict(target["agent_kwargs"])
    name, import_path = source.get("name"), source.get("import_path")
    hosts = policy.get("agent_setup_egress", [])
    if hosts and name not in ("oracle", "nop"):
        kwargs.update(agent_setup_egress_inner=import_path or name, agent_setup_egress_hosts=hosts)
        import_path, name = "nemo_evals.harbor.agent_setup_egress:AgentSetupEgress", None
    expected = AgentConfig(
        name=name,
        import_path=import_path,
        model_name=source.get("model_name"),
        kwargs=kwargs,
        env={**target["env_vars"], **{key: "${" + key + "}" for key in target["env_secrets"]}},
    )
    if trial.config.agent != expected:
        raise ValueError("native agent configuration differs from resolved target")


def verify_helix_identity(row, case, root, input_root, trial):
    from metric_benchmark.validation import task_checksum

    proof = HelixIdentity.model_validate(_object(resolve_evidence(root, row.helix_identity_path)))
    collection = resolve_evidence(root, proof.collection_path)
    record, submission = collection_record(collection)
    case_snapshot = next((value for value in submission.cases if value["case_id"] == case.case_id), None)
    if case_snapshot != case.model_dump(mode="json"):
        raise ValueError("Helix submission differs from prepared case")
    publication = PublicationPlan.model_validate(_object(resolve_evidence(root, proof.publication_path)))
    if publication.cases_sha256 != submission.cases_sha256 or publication.execution != submission.execution:
        raise ValueError("publication and submission inputs differ")
    matches = [
        r
        for r in record["observations"]
        if r["case_id"] == case.case_id
        and r["agent_bundle_id"] == row.agent_bundle_id
        and r["task_id"] == row.evaluation.task.task_id
    ]
    if len(matches) != 1:
        raise ValueError("collection must contain exactly one matching observation")
    observation = matches[0]
    if resolve_evidence(root, row.native_results_path) != resolve_evidence(
        collection, observation["native_results_path"]
    ):
        raise ValueError("native evidence differs from collection observation")
    if row.agent_run_id != observation["remote_run_id"] or row.evaluation.execution != 1:
        raise ValueError("imported execution differs from native run")
    run = next(r for r in submission.runs if r.case_id == case.case_id and r.agent_bundle_id == row.agent_bundle_id)
    bound = submission.bindings.cases[case.case_id]
    task_id = row.evaluation.task.task_id
    task_binding = bound.tasks[task_id]
    published_case = next(c for c in publication.cases if c.case_id == case.case_id)
    published_task = next(t for t in published_case.tasks if t.task_id == task_id)
    published_agent = next(a for a in published_case.agents if a.agent_bundle_id == row.agent_bundle_id)
    source = case.task_sources[task_id].resolve(input_root)
    if input_digest(source) != published_task.sha256:
        raise ValueError("published task differs from prepared input")
    target = normalized_target(bound.targets[row.agent_bundle_id])
    if normalized_target(run.request["spec"]["target"]) != target or run.request["spec"]["tasks"] != bound.taskset:
        raise ValueError("submission target or taskset differs from bindings")
    if normalized_target(published_agent.target) != target:
        raise ValueError("published target differs from submitted target")
    agent = next(a for a in case.agents() if a.agent_bundle_id == row.agent_bundle_id)
    if published_agent.transport == "task_image":
        if published_agent.image_bundle_path is None or published_agent.sha256 is None:
            raise ValueError("task-image publication lacks pinned bundle identity")
        if not any(input_digest(v.resolve(input_root)) == published_agent.sha256 for v in agent.inputs.values()):
            raise ValueError("published bundle is not a prepared agent input")
        bundled = resolve_evidence(source, published_agent.image_bundle_path)
        if input_digest(bundled) != published_agent.sha256:
            raise ValueError("task image context does not contain the prepared agent bundle")
    elif published_agent.sha256 is not None:
        raise ValueError("registered-agent import requires a qualified resolved-config mapping")
    elif target["source"].get("name") not in ("oracle", "nop") or agent.harbor != {"name": target["source"]["name"]}:
        raise ValueError("control target differs from prepared agent")
    job = _object(resolve_evidence(collection, observation["job_spec_path"]))
    job_directory = resolve_evidence(collection, observation["job_spec_path"]).parent
    bundle = resolve_evidence(job_directory, "bundle.tar.gz")
    verify_archive(bundle, resolve_evidence(job_directory, "agent-eval-results"), digest_file(bundle)[7:])
    spec = job["spec"]
    if (
        job["name"] != observation["job"]
        or job["workspace"] != submission.execution.workspace
        or spec["run_id"] != observation["remote_run_id"]
    ):
        raise ValueError("resolved job differs from native run")
    member = _object(resolve_evidence(collection, str(Path(observation["job_spec_path"]).with_name("member.json"))))
    if member["job"] != job["name"] or member["task_index"] != spec["task_index"]:
        raise ValueError("resolved job differs from task member")
    tasks = spec["spec"]["tasks"]
    if len(tasks) != 1 or tasks[0]["id"] != task_binding.native_task_id:
        raise ValueError("resolved job task identity differs")
    native = tasks[0]["spec"]
    owner, revision = task_binding.ref.split("#")
    if (
        native["kind"] != "harbor"
        or native["native_task_id"] != source.name
        or native["provenance"] != {"entity_name": owner, "revision_digest": revision}
    ):
        raise ValueError("resolved task revision differs from pinned binding")
    original = tomllib.loads((source / "task.toml").read_text())
    if native["config"] != original or native["instruction"] != (source / "instruction.md").read_text():
        raise ValueError("resolved task configuration differs from prepared input")
    environment = spec["environment"]
    archive_digest = native["source"]["files_hash"]
    if (
        environment["task_revision_digest"] != revision
        or environment["archive_digest"] != archive_digest
        or environment["agent"]["source_archive_digest"] != archive_digest
    ):
        raise ValueError("prepared image is not bound to the published source archive")
    image = environment["agent"]["image_ref"]
    if "@sha256:" not in image or len(image.rsplit("@sha256:", 1)[1]) != 64:
        raise ValueError("effective task image must be pinned by digest")
    if environment.get("verifier") is not None or environment.get("compose_images"):
        raise ValueError("separate verifier and compose image mappings are not qualified")
    verify_archive(resolve_evidence(root, proof.source_archive_path), source, archive_digest)
    effective = resolve_evidence(root, proof.effective_task_path)
    expected_files, effective_files = file_manifest(source), file_manifest(effective)
    expected_files.pop("task.toml")
    effective_files.pop("task.toml", None)
    if expected_files != effective_files:
        raise ValueError("effective task changed files other than image binding")
    expected_config = copy.deepcopy(original)
    expected_config.setdefault("environment", {})["docker_image"] = image
    if tomllib.loads((effective / "task.toml").read_text()) != expected_config:
        raise ValueError("effective task changes more than the image binding")
    if trial.task_name != source.name or trial.task_checksum != task_checksum(HarborTask(effective)):
        raise ValueError("native task checksum differs from verified effective task")
    if trial.config.task.path is None or trial.config.task.path.name != source.name:
        raise ValueError("native task path differs from prepared task identity")
    if normalized_target(spec["spec"]["target"]) != target:
        raise ValueError("resolved target differs from published agent")
    verify_native_agent(trial, target, spec["sandbox_policy"])
