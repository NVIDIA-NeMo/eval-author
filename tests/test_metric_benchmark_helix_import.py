# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import shutil
import tarfile
import tomllib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.models.trial.config import AgentConfig
from metric_benchmark.helix import plan_submission
from metric_benchmark.helix_identity import normalized_target
from metric_benchmark.helix_import import import_helix_results
from metric_benchmark.helix_publication import plan_publication, publish_helix_inputs
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, json_bytes, write_json
from metric_benchmark.validation import validate_experiment_results
from test_metric_benchmark_helix_publication import Publisher, prepared, task_image_inputs
from test_metric_benchmark_validation import native_trial


def bind_image(path, image):
    file = path / "task.toml"
    text = file.read_text()
    if "[environment]" not in text:
        text += "\n[environment]\n"
    file.write_text(text.replace("[environment]", f'[environment]\ndocker_image = "{image}"'))


class ArchivePublisher(Publisher):
    def publish_task(self, name, path, archive_path):
        result = super().publish_task(name, path, archive_path)
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(path, arcname=path.name)
        return result


def save(path, value):
    path.write_bytes(json_bytes(value))


def reseal(collection):
    record = json.loads((collection / "collection.json").read_text())
    record["files"] = file_manifest(collection)
    record["files"].pop("collection.json", None)
    save(collection / "collection.json", record)


@pytest.fixture(params=["builtin", "task_image"])
def experiment(tmp_path, monkeypatch, request):
    factory = task_image_inputs if request.param == "task_image" else prepared
    loaded, execution, settings = factory(tmp_path / "inputs")
    if request.param == "multi_case":
        original = next(iter(loaded.cases.values()))
        case = original.case.model_copy(deep=True, update={"case_id": "second-case"})
        loaded.cases[case.case_id] = replace(original, case=case)
        settings.agents[case.case_id] = copy.deepcopy(settings.agents[original.case.case_id])
    plan = plan_publication(loaded, execution, settings)
    publication, collection = tmp_path / "publication", tmp_path / "collection"
    bindings = publish_helix_inputs(plan, publication, ArchivePublisher())
    submission = plan_submission(loaded, execution, bindings, "mini", publication=plan)
    write_json(collection / "submission.json", submission)
    write_json(collection / "provenance.json", ArchivePublisher().provenance())
    observations = []
    image = "registry.example.com/task@sha256:" + "a" * 64
    for index, run in enumerate(submission.runs):
        published = next(c for c in plan.cases if c.case_id == run.case_id)
        spec = run.request["spec"]
        assert isinstance(spec, dict)
        target = normalized_target(spec["target"])
        run_root = collection / f"runs/run-{index:04d}"
        write_json(
            run_root / "status.json",
            {
                "workspace": execution.workspace,
                "run_id": f"run-{index}",
                "phase": "completed",
                "result_state": "ready",
                "taskset_ref": bindings.cases[run.case_id].taskset,
                "idempotency_key": run.request["idempotency_key"],
            },
        )
        write_json(
            run_root / "taskset.json",
            {
                "ref": bindings.cases[run.case_id].taskset,
                "tasks": [bindings.cases[run.case_id].tasks[t.task_id].ref for t in published.tasks],
            },
        )
        for task_index, task in enumerate(published.tasks):
            binding = bindings.cases[run.case_id].tasks[task.task_id]
            root = collection / f"runs/run-{index:04d}/task-{task_index:04d}"
            native = root / "bundle/agent-eval-results" / f"{Path(task.path).name}__native"
            native.mkdir(parents=True)
            effective = tmp_path / f"effective/{index}-{task_index}" / Path(task.path).name
            shutil.copytree(publication / "contexts" / task.name / Path(task.path).name, effective)
            bind_image(effective, image)
            kwargs = dict(target["agent_kwargs"])
            name, import_path = target["source"].get("name"), target["source"].get("import_path")
            hosts = ["packages.example.com"] if import_path else []
            if hosts:
                kwargs.update(agent_setup_egress_inner=import_path, agent_setup_egress_hosts=hosts)
                import_path = "nemo_evals.harbor.agent_setup_egress:AgentSetupEgress"
            trial = native_trial(
                effective, native, AgentConfig(name=name, import_path=import_path, kwargs=kwargs), float(index == 0)
            )
            write_json(native / "result.json", trial)
            with tarfile.open(root / "bundle.tar.gz", "w:gz") as archive:
                archive.add(root / "bundle/agent-eval-results", arcname="agent-eval-results")
            source = Path(task.path)
            archive_hash = digest_file(publication / f"archives/{task.name}.tar.gz")[7:]
            owner, revision = binding.ref.split("#")
            job_name, run_id = f"job-{index}-{task_index}", f"run-{index}"
            job = {
                "name": job_name,
                "workspace": execution.workspace,
                "spec": {
                    "run_id": run_id,
                    "task_index": task_index,
                    "sandbox_policy": {"agent_setup_egress": hosts},
                    "environment": {
                        "task_revision_digest": revision,
                        "archive_digest": archive_hash,
                        "agent": {"source_archive_digest": archive_hash, "image_ref": image},
                    },
                    "spec": {
                        "target": target,
                        "tasks": [
                            {
                                "id": binding.native_task_id,
                                "spec": {
                                    "kind": "harbor",
                                    "native_task_id": source.name,
                                    "provenance": {"entity_name": owner, "revision_digest": revision},
                                    "config": tomllib.loads((source / "task.toml").read_text()),
                                    "instruction": (source / "instruction.md").read_text(),
                                    "source": {"files_hash": archive_hash},
                                },
                            }
                        ],
                    },
                },
            }
            write_json(root / "job.json", job)
            write_json(root / "member.json", {"job": job_name, "task_index": task_index, "phase": "succeeded"})
            observations.append(
                {
                    "case_id": run.case_id,
                    "agent_bundle_id": run.agent_bundle_id,
                    "task_id": task.task_id,
                    "remote_run_id": run_id,
                    "job": job_name,
                    "status": "collected",
                    "member_path": (root / "member.json").relative_to(collection).as_posix(),
                    "taskset_path": (run_root / "taskset.json").relative_to(collection).as_posix(),
                    "job_spec_path": (root / "job.json").relative_to(collection).as_posix(),
                    "native_results_path": native.relative_to(collection).as_posix(),
                }
            )
    write_json(
        collection / "collection.json",
        {
            "cases_sha256": loaded.identity,
            "run_id": "mini",
            "submission_sha256": digest_bytes(json_bytes(submission)),
            "observations": observations,
            "files": file_manifest(collection),
        },
    )
    monkeypatch.setattr(
        "metric_benchmark.helix_import.import_module", lambda _: SimpleNamespace(bind_task_image=bind_image)
    )
    return loaded, publication, collection, tmp_path / "imported"


def test_import_validates_complete_matrix_and_replays_without_changes(experiment):
    loaded, publication, collection, output = experiment
    before = file_manifest(collection)
    result = import_helix_results(*experiment)
    assert len(result["cases"]) == 1
    assert file_manifest(collection) == before
    imported = file_manifest(output)
    assert import_helix_results(*experiment) == result
    assert file_manifest(output) == imported
    assert len(list(output.glob("**/effective/*/instruction.md"))) == 3
    report = validate_experiment_results(loaded, output)
    assert report["cases"]["rho-agent"]["observations"] == 9


@pytest.mark.parametrize(
    "change",
    [
        "checksum",
        "archive",
        "image",
        "task_config",
        "agent",
        "job",
        "incomplete",
        "duplicate",
        "runtime_swap",
        "egress",
    ],
)
def test_mismatched_native_identity_never_publishes(experiment, change):
    loaded, publication, collection, output = experiment
    record = json.loads((collection / "collection.json").read_text())
    row = record["observations"][0]
    job_path = collection / row["job_spec_path"]
    job = json.loads(job_path.read_text())
    if change == "checksum":
        path = collection / row["native_results_path"] / "result.json"
        trial = json.loads(path.read_text())
        trial["task_checksum"] = "0" * 64
        save(path, trial)
    elif change == "archive":
        job["spec"]["environment"]["agent"]["source_archive_digest"] = "0" * 64
    elif change == "image":
        job["spec"]["environment"]["agent"]["image_ref"] = "registry.example.com/task:latest"
    elif change == "task_config":
        job["spec"]["spec"]["tasks"][0]["spec"]["config"]["agent"] = {"timeout_sec": 999}
    elif change == "agent":
        job["spec"]["spec"]["target"]["source"]["name"] = "nop"
    elif change == "runtime_swap":
        job["spec"]["spec"]["target"]["agent_kwargs"]["runtime_path"] = "/opt/agents/mutant"
    elif change == "egress":
        job["spec"]["sandbox_policy"]["agent_setup_egress"] = ["substitute.example.com"]
        if job["spec"]["spec"]["target"]["source"].get("name") in ("oracle", "nop"):
            pytest.skip("controls do not use setup egress wrapper")
    elif change == "job":
        job["spec"]["run_id"] = "unrelated"
    else:
        record["observations"] = (
            record["observations"][:-1] if change == "incomplete" else record["observations"] + [row]
        )
        save(collection / "collection.json", record)
    save(job_path, job)
    reseal(collection)
    with pytest.raises(ValueError):
        import_helix_results(*experiment)
    assert not output.exists()


def test_effective_task_is_verified_again_on_offline_validation(experiment):
    loaded, _, _, output = experiment
    import_helix_results(*experiment)
    effective = next(output.glob("**/effective/*/instruction.md"))
    effective.write_text("substituted task")
    with pytest.raises(ValueError, match="changed files"):
        validate_experiment_results(loaded, output)
    with pytest.raises(ValueError, match="changed"):
        import_helix_results(*experiment)


def test_saved_submission_hash_survives_added_model_defaults(experiment):
    loaded, publication, collection, output = experiment
    path = collection / "submission.json"
    plan = json.loads(path.read_text())
    plan["execution"].pop("backend")
    save(path, plan)
    record = json.loads((collection / "collection.json").read_text())
    record["submission_sha256"] = digest_bytes(json_bytes(plan))
    job_path = collection / record["observations"][0]["job_spec_path"]
    job = json.loads(job_path.read_text())
    job["spec"]["spec"]["target"]["source"]["model"] = None
    save(job_path, job)
    save(collection / "collection.json", record)
    reseal(collection)
    import_helix_results(*experiment)
    validate_experiment_results(loaded, output)


def test_failed_task_job_is_accounted_without_fabricating_native_results(experiment):
    loaded, publication, collection, output = experiment
    record = json.loads((collection / "collection.json").read_text())
    row = record["observations"][0]
    member_path = collection / row["member_path"]
    member = json.loads(member_path.read_text())
    member["phase"] = "failed"
    member["job"] = None
    root = member_path.parent
    shutil.rmtree(root)
    write_json(member_path, member)
    row.update(
        status="missing",
        job=None,
        job_spec_path=None,
        native_results_path=str(root.relative_to(collection) / "missing-native"),
        unavailable_evidence={"native_results": "Helix task job failed: sandbox launch failed"},
    )
    save(collection / "collection.json", record)
    reseal(collection)
    import_helix_results(*experiment)
    report = validate_experiment_results(loaded, output)
    assert len(report["cases"]["rho-agent"]["unavailable"]) == 1
    imported_member = next(output.glob("**/collection/" + row["member_path"]))
    member["phase"] = "succeeded"
    save(imported_member, member)
    with pytest.raises(ValueError):
        validate_experiment_results(loaded, output)


def test_results_backend_cannot_select_an_unrelated_identity_scheme(experiment):
    loaded, _, _, output = experiment
    manifest = import_helix_results(*experiment)
    index = output / next(iter(manifest["cases"].values()))
    raw = json.loads(index.read_text())
    raw["backend"] = "local"
    save(index, raw)
    with pytest.raises(ValueError, match="backend"):
        validate_experiment_results(loaded, output)


@pytest.mark.parametrize("experiment", ["multi_case"], indirect=True)
def test_import_partitions_native_evidence_by_case(experiment):
    loaded, _, collection, output = experiment
    manifest = import_helix_results(*experiment)
    assert len(manifest["cases"]) == 2
    original_bundles = list(collection.glob("**/bundle.tar.gz"))
    imported_bundles = list(output.glob("**/collection/**/bundle.tar.gz"))
    assert len(original_bundles) == len(imported_bundles) == 18
    for case_id, index in manifest["cases"].items():
        root = (output / index).parent
        scoped = json.loads((root / "helix/collection/collection.json").read_text())
        assert scoped["scope_case_id"] == case_id
        assert all(row["case_id"] == case_id for row in scoped["observations"])
    validate_experiment_results(loaded, output)


@pytest.mark.parametrize("change", ["registered", "unqualified_version"])
def test_unsupported_mapping_and_client_are_rejected(experiment, change):
    _, _, collection, output = experiment
    if change == "registered":
        path = collection / "submission.json"
        plan = json.loads(path.read_text())
        plan["publication"]["cases"][0]["agents"][0]["packaging"]["transport"] = "registered"
        save(path, plan)
    else:
        path = collection / "provenance.json"
        provenance = json.loads(path.read_text())
        provenance["client_versions"]["harbor"] = "0.99.0"
        save(path, provenance)
    reseal(collection)
    with pytest.raises(ValueError):
        import_helix_results(*experiment)
    assert not output.exists()


def test_validation_hashes_each_collection_once_and_reuses_task_derivations(experiment, monkeypatch):
    from metric_benchmark import helix_identity

    loaded, _, _, output = experiment
    import_helix_results(*experiment)
    counts = {"collection": 0, "derivation": 0}
    original_manifest = helix_identity.file_manifest
    original_derivation = helix_identity.verify_task_derivation

    def manifest(directory):
        if directory.name == "collection":
            counts["collection"] += 1
        return original_manifest(directory)

    def derivation(*args):
        counts["derivation"] += 1
        return original_derivation(*args)

    monkeypatch.setattr(helix_identity, "file_manifest", manifest)
    monkeypatch.setattr(helix_identity, "verify_task_derivation", derivation)
    validate_experiment_results(loaded, output)
    assert counts == {"collection": 1, "derivation": 3}
