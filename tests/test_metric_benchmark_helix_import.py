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


@pytest.fixture(params=["builtin"])
def experiment(tmp_path, monkeypatch, request):
    loaded, execution, settings = prepared(tmp_path / "inputs")
    if request.param == "multi_case":
        original = next(iter(loaded.cases.values()))
        case = original.case.model_copy(deep=True, update={"case_id": "second-case"})
        loaded.cases[case.case_id] = replace(original, case=case)
        settings.agents[case.case_id] = copy.deepcopy(settings.agents[original.case.case_id])
    plan = plan_publication(loaded, execution, settings)
    publication, collection = tmp_path / "publication", tmp_path / "collection"
    bindings = publish_helix_inputs(plan, publication, ArchivePublisher(), source_root=loaded.root)
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
            shutil.copytree(loaded.root / task.path, effective)
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
            source = loaded.root / task.path
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


def test_registered_agent_import_checks_prepared_config_and_snapshot(tmp_path, monkeypatch):
    pytest.importorskip("nemo_agents_plugin")
    from importlib import import_module

    import yaml
    from metric_benchmark.helix_identity import verify_registered_target

    PlatformAgentConfig = import_module("nemo_agents_plugin.agent_config").AgentConfig
    resolve_agent_config_for_deployment = import_module(
        "nemo_agents_plugin.agent_config_formats"
    ).resolve_agent_config_for_deployment
    translate_agent_config = import_module("nemo_agents_plugin.fabric.translator").translate_agent_config
    AgentRef = import_module("nemo_evals.api.schemas").AgentRef
    FilesetRef = import_module("nemo_evals.filesets").FilesetRef
    HarborRunnerTarget = import_module("nemo_evals.jobs.agent_spec").HarborRunnerTarget
    resolution = import_module("nemo_evals.jobs.registered_agent_resolution")
    _fill_target, _ResolvedRegisteredAgent = resolution._fill_target, resolution._ResolvedRegisteredAgent
    from test_metric_benchmark_helix_publication import registered_inputs

    loaded, execution, settings = registered_inputs(tmp_path)
    prepared_case = next(iter(loaded.cases.values()))
    agent = prepared_case.case.agents()[0]
    assert agent.registered is not None
    bundle = agent.inputs[agent.registered.bundle_input].resolve(prepared_case.root)
    config_path = bundle / agent.registered.config_path
    config = yaml.safe_load(config_path.read_text())
    config["skills"] = {"paths": ["skills"]}
    config_path.write_text(yaml.safe_dump(config))
    from metric_benchmark.io import input_digest

    agent.inputs[agent.registered.bundle_input].sha256 = input_digest(bundle)
    from metric_benchmark.helix_publication import agent_target

    requested = agent_target(
        agent, settings.agents[prepared_case.case.case_id][agent.agent_bundle_id], execution.workspace
    )
    source = requested["source"]
    assert isinstance(source, dict)
    ref = source["agent"]
    assert isinstance(ref, str)
    monkeypatch.setenv("NHX_BASE_URL", "https://server.example.com")
    config = resolve_agent_config_for_deployment(
        "nemo-agents-spec-v1", config, workspace=execution.workspace, agent_name=ref.split("/", 1)[1]
    )
    translated = translate_agent_config(PlatformAgentConfig.model_validate(config)).model_dump(
        mode="json", exclude_none=True
    )
    native = HarborRunnerTarget.model_validate(requested)
    actual = _fill_target(
        native,
        native.source,
        _ResolvedRegisteredAgent(
            ref=AgentRef(root=ref), config=translated, env_secrets={}, files=FilesetRef(root="default/snapshot")
        ),
    ).model_dump(mode="json")
    monkeypatch.setenv("NHX_BASE_URL", "https://offline-client.example.com")
    retained = tmp_path / "retained"
    shutil.copytree(bundle, retained / "agent-files")
    verify_registered_target(agent, prepared_case.root, requested, actual, execution.workspace, retained)
    changed = copy.deepcopy(actual)
    changed["agent_kwargs"]["fabric_config"]["unexpected_behavior"] = True
    with pytest.raises(ValueError, match="differs from prepared agent"):
        verify_registered_target(agent, prepared_case.root, requested, changed, execution.workspace, retained)
    changed = copy.deepcopy(actual)
    changed["source"]["files"] = None
    with pytest.raises(ValueError, match="snapshot missing"):
        verify_registered_target(agent, prepared_case.root, requested, changed, execution.workspace, retained)
    (retained / "agent-files" / agent.registered.config_path).write_text("changed")
    with pytest.raises(ValueError, match="snapshot differs"):
        verify_registered_target(agent, prepared_case.root, requested, actual, execution.workspace, retained)


@pytest.mark.parametrize("wrapped", [False, True])
def test_registered_native_runtime_preserves_agent_with_platform_bindings(wrapped):
    from metric_benchmark.helix_identity import verify_native_agent

    adapter = "nhx_evals_sdk.agent_eval.runtimes.harbor.fabric_installed_agent:FabricInstalledAgent"
    target = normalized_target(
        {
            "source": {"agent": "default/prepared", "files": "default/snapshot"},
            "agent_kwargs": {
                "fabric_config": {
                    "models": {
                        "default": {
                            "provider": "nvidia",
                            "model": "prepared-model",
                            "api_key_env": "GATEWAY_KEY",
                            "base_url": "http://api/apis/inference-gateway/v2/workspaces/default/openai/-/v1",
                        }
                    }
                }
            },
        }
    )
    kwargs = copy.deepcopy(target["agent_kwargs"])
    kwargs["fabric_config_bundle"] = "/scratch/environment"
    kwargs["fabric_config"]["models"]["default"]["base_url"] = (
        "http://worker-api/apis/inference-gateway/v2/workspaces/default/openai/-/v1"
    )
    policy = {"agent_setup_egress": ["packages.example.com"]}
    import_path = adapter
    if wrapped:
        kwargs.update(agent_setup_egress_inner=adapter, agent_setup_egress_hosts=policy["agent_setup_egress"])
        import_path = "nemo_evals.harbor.agent_setup_egress:AgentSetupEgress"
    trial = SimpleNamespace(
        config=SimpleNamespace(
            agent=AgentConfig(
                import_path=import_path, kwargs=kwargs, env={"GATEWAY_KEY": "${NEMO_TRIAL_KEY_" + "A" * 32 + "}"}
            )
        )
    )
    job = {
        "platform_spec": {
            "steps": [{"environment": [{"name": "NEMO_JOB_PERSISTENT_JOB_STORAGE_PATH", "value": "/scratch"}]}]
        }
    }
    verify_native_agent(trial, target, policy, job)
    for key, value, message in (
        ("model", "different-model", "configuration differs"),
        (
            "base_url",
            "http://worker-api/apis/inference-gateway/v2/workspaces/other/openai/-/v1",
            "gateway workspace/path",
        ),
    ):
        changed = copy.deepcopy(trial)
        changed.config.agent.kwargs["fabric_config"]["models"]["default"][key] = value
        with pytest.raises(ValueError, match=message):
            verify_native_agent(changed, target, policy, job)
    changed = copy.deepcopy(trial)
    changed.config.agent.kwargs["fabric_config_bundle"] = "/other/environment"
    with pytest.raises(ValueError, match="staging path"):
        verify_native_agent(changed, target, policy, job)
    changed = copy.deepcopy(trial)
    changed.config.agent.env["GATEWAY_KEY"] = "${UNRELATED_KEY}"
    with pytest.raises(ValueError, match="per-trial placeholder"):
        verify_native_agent(changed, target, policy, job)


def test_harbor_legacy_task_version_alias_imports_without_rewriting_task(experiment):
    loaded, publication, collection, output = experiment
    record = json.loads((collection / "collection.json").read_text())
    for row in record["observations"]:
        path = collection / row["job_spec_path"]
        job = json.loads(path.read_text())
        config = job["spec"]["spec"]["tasks"][0]["spec"]["config"]
        if "version" in config:
            config["schema_version"] = config.pop("version")
        save(path, job)
    reseal(collection)
    import_helix_results(loaded, publication, collection, output)
    validate_experiment_results(loaded, output)


def test_collection_requires_exact_explicit_run_selection(experiment):
    from metric_benchmark.helix import Submission
    from metric_benchmark.helix_collection import selected_submission
    from metric_benchmark.helix_execution import RunReceipt
    from metric_benchmark.helix_sources import References
    from metric_benchmark.io import read_json, read_object

    loaded, publication, collection, output = experiment
    plan = Submission.model_validate(read_json(collection / "submission.json"))
    refs = References(publication=plan.publication, bindings=plan.bindings, provenance={})
    receipts = [
        RunReceipt(
            references=refs,
            execution=plan.execution,
            case_id=run.case_id,
            agent_bundle_id=run.agent_bundle_id,
            run_id=plan.run_id,
            request=run.request,
            response=read_object(collection / f"runs/run-{index:04d}/status.json"),
        )
        for index, run in enumerate(plan.runs)
    ]
    combined, ordered = selected_submission(refs, list(reversed(receipts)))
    assert combined.runs == plan.runs
    assert ordered == receipts
    for invalid in (receipts[:-1], [*receipts, receipts[0]]):
        with pytest.raises(ValueError, match="exactly one run"):
            selected_submission(refs, invalid)


def test_pending_collection_does_not_publish_output(experiment, monkeypatch):
    from metric_benchmark.helix import NotReady, Submission
    from metric_benchmark.helix_collection import collect_results
    from metric_benchmark.helix_execution import RunReceipt
    from metric_benchmark.helix_sources import References
    from metric_benchmark.io import read_json, read_object

    loaded, publication, collection, output = experiment
    plan = Submission.model_validate(read_json(collection / "submission.json"))
    refs = References(publication=plan.publication, bindings=plan.bindings, provenance={})
    receipts = [
        RunReceipt(
            references=refs,
            execution=plan.execution,
            case_id=run.case_id,
            agent_bundle_id=run.agent_bundle_id,
            run_id=plan.run_id,
            request=run.request,
            response=read_object(collection / f"runs/run-{index:04d}/status.json"),
        )
        for index, run in enumerate(plan.runs)
    ]

    def pending(*args):
        raise NotReady("running")

    monkeypatch.setattr("metric_benchmark.helix_collection.check_helix_runs", pending)
    client = SimpleNamespace(require_destination=lambda *args: None)
    with pytest.raises(NotReady):
        collect_results(refs, receipts, loaded.root, output, client)
    assert not output.exists()
