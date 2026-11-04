# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
import tarfile
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.models.trial.config import AgentConfig
from metric_benchmark.config import load_cases
from metric_benchmark.helix import plan_submission
from metric_benchmark.helix_identity import normalized_target
from metric_benchmark.helix_import import import_helix_results
from metric_benchmark.helix_publication import plan_publication, publish_helix_inputs
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, json_bytes, write_json
from metric_benchmark.validation import validate_experiment_results
from test_metric_benchmark_helix_publication import Publisher, prepared
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


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    loaded, execution, settings = prepared(tmp_path / "inputs")
    loaded = load_cases(tmp_path / "inputs/cases.yml")
    plan = plan_publication(loaded, execution, settings)
    publication, collection = tmp_path / "publication", tmp_path / "collection"
    bindings = publish_helix_inputs(plan, publication, ArchivePublisher())
    submission = plan_submission(loaded, execution, bindings, "mini")
    write_json(collection / "submission.json", submission)
    observations = []
    image = "registry.example.com/task@sha256:" + "a" * 64
    for index, run in enumerate(submission.runs):
        published = next(c for c in plan.cases if c.case_id == run.case_id)
        spec = run.request["spec"]
        assert isinstance(spec, dict)
        target = normalized_target(spec["target"])
        for task_index, task in enumerate(published.tasks):
            binding = bindings.cases[run.case_id].tasks[task.task_id]
            root = collection / f"runs/run-{index:04d}/task-{task_index:04d}"
            native = root / "agent-eval-results" / f"{Path(task.path).name}__native"
            native.mkdir(parents=True)
            effective = tmp_path / f"effective/{index}-{task_index}" / Path(task.path).name
            shutil.copytree(task.path, effective)
            bind_image(effective, image)
            trial = native_trial(effective, native, AgentConfig(name=target["source"]["name"]), float(index == 0))
            write_json(native / "result.json", trial)
            with tarfile.open(root / "bundle.tar.gz", "w:gz") as archive:
                archive.add(root / "agent-eval-results", arcname="agent-eval-results")
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
                    "sandbox_policy": {"agent_setup_egress": []},
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
            write_json(root / "member.json", {"job": job_name, "task_index": task_index})
            observations.append(
                {
                    "case_id": run.case_id,
                    "agent_bundle_id": run.agent_bundle_id,
                    "task_id": task.task_id,
                    "remote_run_id": run_id,
                    "job": job_name,
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
    report = validate_experiment_results(loaded, output)
    assert report["cases"]["rho-agent"]["observations"] == 9


@pytest.mark.parametrize(
    "change", ["checksum", "archive", "image", "task_config", "agent", "job", "incomplete", "duplicate"]
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
