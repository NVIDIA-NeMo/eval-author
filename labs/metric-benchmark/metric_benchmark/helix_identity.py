# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify preserved Helix task and agent identities against prepared inputs."""

import copy
import re
import tarfile
import tomllib
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig

from metric_benchmark.helix import Submission, _check_status_identity, _object
from metric_benchmark.helix_publication import agent_target
from metric_benchmark.io import digest_bytes, digest_file, file_manifest, input_digest, json_bytes
from metric_benchmark.models import Model, Text, relative_path, resolve_evidence


class HelixIdentity(Model):
    collection_path: Text
    source_archive_path: Text | None = None
    effective_task_path: Text | None = None


def collection_record(directory):
    try:
        return _collection_record(directory)
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(f"malformed Helix collection: {type(error).__name__}") from None


def _collection_record(directory):
    if version("harbor") not in ("0.20.0", "0.24.0"):
        raise ValueError("Helix checksum validation requires Harbor 0.20.0 or 0.24.0")
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
    provenance = _object(resolve_evidence(directory, "provenance.json"))
    if provenance["client_versions"].get("harbor") != "0.24.0":
        raise ValueError("collection requires the qualified Harbor 0.24.0 client")
    scope = record.get("scope_case_id")
    if scope is not None:
        original = _object(resolve_evidence(directory, "source_collection.json"))
        prefixes = tuple(f"runs/run-{i:04d}/" for i, run in enumerate(plan.runs) if run.case_id == scope)
        selected = {
            name: digest
            for name, digest in original["files"].items()
            if not name.startswith("runs/") or name.startswith(prefixes)
        }
        actual = dict(files)
        actual.pop("source_collection.json")
        if selected != actual or record["observations"] != [
            r for r in original["observations"] if r["case_id"] == scope
        ]:
            raise ValueError("case evidence differs from source collection")
        if any(original[key] != record[key] for key in ("submission_sha256", "cases_sha256", "run_id")):
            raise ValueError("case collection identity differs from source")
    expected = {
        (run.case_id, run.agent_bundle_id, task_id)
        for run in plan.runs
        if scope is None or run.case_id == scope
        for task_id in plan.bindings.cases[run.case_id].tasks
    }
    observed = [(row["case_id"], row["agent_bundle_id"], row["task_id"]) for row in record["observations"]]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("collection does not contain the complete expected matrix")
    return record, plan


def verify_archive(archive, source, expected_digest=None):
    if expected_digest is not None and digest_file(archive) != "sha256:" + expected_digest:
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
    if any(result[key] != defaults[key] for key in ("kind", "n_attempts", "max_retries", "n_concurrent_trials")):
        raise ValueError("Helix import requires one Harbor attempt without retries or concurrency overrides")
    if "agent" not in result["source"]:
        result["source"] = {"model_name": None, **result["source"]}
    # Earlier deployed built-in sources serialized an unused null `model` field.
    if "model" in result["source"] and result["source"]["model"] is None:
        result["source"].pop("model")
    return result


def verify_native_agent(trial, target, policy, job=None):
    target = copy.deepcopy(target)
    source = target["source"]
    registered = "agent" in source
    credential_env = {}
    if registered:
        if source.get("files"):
            path = trial.config.agent.kwargs.get("fabric_config_bundle")
            roots = {
                entry["value"]
                for step in (job or {}).get("platform_spec", {}).get("steps", [])
                for entry in step.get("environment", [])
                if entry.get("name") == "NEMO_JOB_PERSISTENT_JOB_STORAGE_PATH" and isinstance(entry.get("value"), str)
            }
            if len(roots) != 1 or path != str(Path(next(iter(roots))) / "environment"):
                raise ValueError("native registered-agent bundle staging path differs")
            target["agent_kwargs"]["fabric_config_bundle"] = path
        models = target["agent_kwargs"].get("fabric_config", {}).get("models", {})
        observed_models = trial.config.agent.kwargs.get("fabric_config", {}).get("models", {})
        workspace = source["agent"].split("/", 1)[0]
        gateway_path = f"/apis/inference-gateway/v2/workspaces/{workspace}/openai/-/v1"
        for key, model in models.items():
            if urlsplit(model.get("base_url", "")).path != gateway_path:
                continue
            observed = observed_models.get(key, {}).get("base_url", "")
            parsed = urlsplit(observed)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path != gateway_path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("native registered gateway workspace/path differs")
            model["base_url"] = observed
            env_name = model.get("api_key_env")
            if env_name:
                placeholder = (trial.config.agent.env or {}).get(env_name, "")
                if re.fullmatch(r"\$\{NEMO_TRIAL_KEY_[0-9A-F]{32}\}", placeholder) is None:
                    raise ValueError("native registered gateway credential is not a per-trial placeholder")
                credential_env[env_name] = placeholder
        source = {"import_path": "nhx_evals_sdk.agent_eval.runtimes.harbor.fabric_installed_agent:FabricInstalledAgent"}

    if set(source) - {"name", "import_path", "model_name"}:
        raise ValueError("resolved agent source needs a supported identity mapping")
    if bool(source.get("name")) == bool(source.get("import_path")):
        raise ValueError("expected one explicit native agent source")
    kwargs = dict(target["agent_kwargs"])
    name, import_path = source.get("name"), source.get("import_path")
    hosts = policy.get("agent_setup_egress", [])
    if not isinstance(hosts, list) or any(not isinstance(host, str) or not host for host in hosts):
        raise ValueError("resolved agent setup egress must be a list of hostnames")
    if any(key in kwargs for key in ("agent_setup_egress_inner", "agent_setup_egress_hosts")):
        raise ValueError("prepared kwargs must not override the platform egress wrapper")
    # Some workers launch the registered Fabric adapter directly; others apply
    # the setup-egress wrapper. Both must preserve the same inner agent/kwargs.
    direct_registered = registered and trial.config.agent.import_path == import_path
    if hosts and name not in ("oracle", "nop") and not direct_registered:
        kwargs.update(agent_setup_egress_inner=import_path or name, agent_setup_egress_hosts=hosts)
        import_path, name = "nemo_evals.harbor.agent_setup_egress:AgentSetupEgress", None
    expected = AgentConfig(
        name=name,
        import_path=import_path,
        model_name=source.get("model_name"),
        kwargs=kwargs,
        env={**target["env_vars"], **{key: "${" + key + "}" for key in target["env_secrets"]}, **credential_env},
    )
    if trial.config.agent != expected:
        raise ValueError("native agent configuration differs from resolved target")


def verify_helix_identity(row, case, root, input_root, trial, cache, *, allow_unsigned=False):
    if allow_unsigned:
        raise ValueError("Helix import requires fully signed prepared inputs")
    try:
        _verify_identity(row, case, root, input_root, trial, cache)
    except (KeyError, IndexError, TypeError, StopIteration) as error:
        raise ValueError(f"malformed Helix identity evidence: {type(error).__name__}") from None


def _verify_identity(row, case, root, input_root, trial, cache):
    from metric_benchmark.validation import task_checksum

    proof = HelixIdentity.model_validate(_object(resolve_evidence(root, row.identity_proof_path)))
    collection = resolve_evidence(root, proof.collection_path)
    key = ("collection", collection)
    if key not in cache:
        cache[key] = collection_record(collection)
    record, submission = cache[key]
    if record.get("scope_case_id", case.case_id) != case.case_id:
        raise ValueError("collection scope differs from prepared case")
    case_snapshot = next((value for value in submission.cases if value["case_id"] == case.case_id), None)
    if case_snapshot != case.model_dump(mode="json"):
        raise ValueError("Helix submission differs from prepared case")
    matches = [
        r
        for r in record["observations"]
        if (r["case_id"], r["agent_bundle_id"], r["task_id"])
        == (case.case_id, row.agent_bundle_id, row.evaluation.task.task_id)
    ]
    if len(matches) != 1:
        raise ValueError("collection must contain exactly one matching observation")
    observation = matches[0]
    # Missing native paths deliberately do not exist; still enforce their lexical binding.
    if row.native_results_path != str(Path(proof.collection_path) / observation["native_results_path"]):
        raise ValueError("native evidence differs from collection observation")
    if row.agent_run_id != observation["remote_run_id"] or row.evaluation.execution != 1:
        raise ValueError("imported execution differs from native run")
    run_index, run = next(
        (i, r)
        for i, r in enumerate(submission.runs)
        if r.case_id == case.case_id and r.agent_bundle_id == row.agent_bundle_id
    )
    bound = submission.bindings.cases[case.case_id]
    binding = bound.tasks[row.evaluation.task.task_id]
    if (
        not observation["member_path"].startswith(f"runs/run-{run_index:04d}/")
        or observation["taskset_path"] != f"runs/run-{run_index:04d}/taskset.json"
    ):
        raise ValueError("observation evidence belongs to a different run")
    member = _object(resolve_evidence(collection, observation["member_path"]))
    status = _object(resolve_evidence(collection, f"runs/run-{run_index:04d}/status.json"))
    _check_status_identity(status, run, submission)
    if status["run_id"] != row.agent_run_id or status["phase"] not in ("completed", "failed", "cancelled", "expired"):
        raise ValueError("collection run is not the expected completed run")
    taskset = _object(resolve_evidence(collection, observation["taskset_path"]))
    if (
        taskset["ref"] != bound.taskset
        or len(taskset["tasks"]) != len(bound.tasks)
        or set(taskset["tasks"]) != {task.ref for task in bound.tasks.values()}
        or type(member["task_index"]) is not int
        or member["task_index"] < 0
        or taskset["tasks"][member["task_index"]] != binding.ref
    ):
        raise ValueError("native member differs from pinned taskset position")
    if member.get("job") != observation["job"]:
        raise ValueError("native member job differs from collection")
    job = None
    if observation["job_spec_path"] is not None:
        job = _object(resolve_evidence(collection, observation["job_spec_path"]))
        if (
            job["name"] != observation["job"]
            or job["workspace"] != submission.execution.workspace
            or job["spec"]["run_id"] != row.agent_run_id
            or job["spec"]["task_index"] != member["task_index"]
        ):
            raise ValueError("resolved job differs from native run member")
    if observation["status"] == "missing":
        if (
            trial is not None
            or row.status != "missing"
            or member["phase"] not in ("failed", "cancelled", "expired")
            or row.unavailable_evidence != observation["unavailable_evidence"]
            or bool(observation["job"]) != (job is not None)
            or proof.effective_task_path is not None
            or proof.source_archive_path is not None
        ):
            raise ValueError("missing observation is not supported by failed task evidence")
        return
    if observation["status"] != "collected" or member["phase"] != "succeeded" or job is None or trial is None:
        raise ValueError("collected observation requires a succeeded native task job")
    job_directory = resolve_evidence(collection, observation["job_spec_path"]).parent
    verify_archive(
        resolve_evidence(job_directory, "bundle.tar.gz"), resolve_evidence(job_directory, "bundle/agent-eval-results")
    )
    published_case = next(c for c in submission.publication.cases if c.case_id == case.case_id)
    published_task = next(t for t in published_case.tasks if t.task_id == row.evaluation.task.task_id)
    source = case.task_sources[row.evaluation.task.task_id].resolve(input_root)
    target = normalized_target(bound.targets[row.agent_bundle_id])
    agent = next(a for a in case.agents() if a.agent_bundle_id == row.agent_bundle_id)
    if (
        normalized_target(agent_target(agent, bound.agents[row.agent_bundle_id], submission.execution.workspace))
        != target
    ):
        raise ValueError("submitted agent differs from prepared definition")
    spec = job["spec"]
    if agent.registered is not None:
        target = verify_registered_target(
            agent,
            input_root,
            target,
            spec["spec"]["target"],
            submission.execution.workspace,
            collection / f"runs/run-{run_index:04d}/resolved",
        )
    elif normalized_target(spec["spec"]["target"]) != target:
        raise ValueError("resolved target differs from prepared agent")
    if proof.effective_task_path is None or proof.source_archive_path is None:
        raise ValueError("completed task lacks image derivation evidence")
    effective = resolve_evidence(root, proof.effective_task_path)
    archive = resolve_evidence(root, proof.source_archive_path)
    derivation_key = (
        "derivation",
        case.case_id,
        published_task.task_id,
        effective,
        archive,
        digest_bytes(json_bytes(spec["environment"])),
        digest_bytes(json_bytes(spec["spec"]["tasks"])),
    )
    if derivation_key not in cache:
        verify_task_derivation(
            case, input_root, published_case, published_task, binding, spec, source, effective, archive
        )
        cache[derivation_key] = task_checksum(HarborTask(effective))
    if trial.task_name != source.name or trial.task_checksum != cache[derivation_key]:
        raise ValueError("native task checksum differs from verified effective task")
    if trial.config.task.path is None or trial.config.task.path.name != source.name:
        raise ValueError("native task path differs from prepared task identity")
    verify_native_agent(trial, target, spec["sandbox_policy"], job)


def verify_registered_target(agent, input_root, requested, actual, workspace, retained):
    # Use the same pure platform translation as registration; no remote reads.
    try:
        PlatformAgentConfig = import_module("nemo_agents_plugin.agent_config").AgentConfig
        translator = import_module("nemo_agents_plugin.fabric.translator")
        translate_agent_config = translator.translate_agent_config
        utils = import_module("nemo_agents_plugin.utils")
        inject_fabric_gateway_url = utils.inject_fabric_gateway_url
        inject_fabric_trace_agent_name = utils.inject_fabric_trace_agent_name
        AgentRef = import_module("nemo_evals.api.schemas").AgentRef
        FilesetRef = import_module("nemo_evals.filesets").FilesetRef
        agent_spec = import_module("nemo_evals.jobs.agent_spec")
        HarborRunnerTarget = agent_spec.HarborRunnerTarget
        registered_agent_config_needs_files = agent_spec.registered_agent_config_needs_files
        resolution = import_module("nemo_evals.jobs.registered_agent_resolution")
        _fill_target = resolution._fill_target
        _ResolvedRegisteredAgent = resolution._ResolvedRegisteredAgent
    except ImportError:
        raise ValueError("registered-agent import requires the qualified Helix client environment") from None
    import yaml

    from metric_benchmark.config import StrictLoader

    bundle = agent.inputs[agent.registered.bundle_input].resolve(input_root)
    config = yaml.load((bundle / agent.registered.config_path).read_text(), Loader=StrictLoader)
    ref = requested["source"]["agent"]
    config = inject_fabric_gateway_url(config, workspace, base_url="https://registered-gateway.invalid")
    config = PlatformAgentConfig.model_validate(config).model_dump(exclude_none=True)
    config = inject_fabric_trace_agent_name(config, ref.split("/", 1)[1])
    translated = translate_agent_config(PlatformAgentConfig.model_validate(config)).model_dump(
        mode="json", exclude_none=True
    )
    # Translation forwards the calling process's platform coordinates. Rebuild
    # those separately so an offline client's environment cannot affect identity.
    authored_env = config.get("environment", {}).get("env", {})
    translated_env = translated["environment"]["env"]
    for key in translator.PLATFORM_RUNTIME_ENV_VARS:
        if key in authored_env:
            translated_env[key] = authored_env[key]
        else:
            translated_env.pop(key, None)
    source = actual["source"]
    if set(source) - {"agent", "environment", "files"} or source.get("environment") is not None:
        raise ValueError("unsupported registered-agent source override")
    files = source.get("files")
    if bool(files) != registered_agent_config_needs_files(translated):
        raise ValueError("registered-agent file snapshot missing or unexpected")
    if files and file_manifest(resolve_evidence(retained, "agent-files")) != file_manifest(bundle):
        raise ValueError("registered-agent snapshot differs from prepared bundle")
    native = HarborRunnerTarget.model_validate(requested)
    resolved = _ResolvedRegisteredAgent(
        ref=AgentRef(root=ref), config=translated, env_secrets={}, files=FilesetRef(root=files) if files else None
    )
    expected = _fill_target(native, native.source, resolved).model_dump(mode="json")
    # Gateway hosts are deployment routing. Preserve and compare the workspace/path,
    # and never normalize explicit model URLs supplied by the prepared agent.
    expected_models = expected["agent_kwargs"]["fabric_config"].get("models", {})
    original_actual = copy.deepcopy(actual)
    actual = copy.deepcopy(actual)
    actual_models = actual.get("agent_kwargs", {}).get("fabric_config", {}).get("models", {})

    gateway_origins = set()
    for key, model in expected_models.items():
        url = model.get("base_url")
        if isinstance(url, str) and url.startswith("https://registered-gateway.invalid/"):
            observed = actual_models.get(key, {}).get("base_url")
            if not isinstance(observed, str):
                raise ValueError("registered model gateway URL missing")
            parsed = urlsplit(observed)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path != urlsplit(url).path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("registered model gateway workspace/path differs")
            gateway_origins.add(f"{parsed.scheme}://{parsed.netloc}")
            actual_models[key]["base_url"] = url
    actual_env = actual.get("agent_kwargs", {}).get("fabric_config", {}).get("environment", {}).get("env", {})
    for key in translator.PLATFORM_RUNTIME_ENV_VARS:
        if key in authored_env or key not in actual_env:
            continue
        value = actual_env[key]
        if (key == "NHX_WORKSPACE" and value != workspace) or (key != "NHX_WORKSPACE" and value not in gateway_origins):
            raise ValueError("registered platform environment differs from gateway/workspace")
        actual_env.pop(key)
    if normalized_target(actual) != normalized_target(expected):
        raise ValueError("resolved registered target differs from prepared agent")
    return normalized_target(original_actual)


def verify_task_derivation(case, input_root, published_case, published_task, binding, spec, source, effective, archive):
    if input_digest(source) != published_task.sha256:
        raise ValueError("published task differs from prepared input")
    tasks = spec["spec"]["tasks"]
    if len(tasks) != 1 or tasks[0]["id"] != binding.native_task_id:
        raise ValueError("resolved job task identity differs")
    native = tasks[0]["spec"]
    owner, revision = binding.ref.split("#")
    if (
        native["kind"] != "harbor"
        or native["native_task_id"] != source.name
        or native["provenance"] != {"entity_name": owner, "revision_digest": revision}
    ):
        raise ValueError("resolved task revision differs from pinned binding")
    original = tomllib.loads((source / "task.toml").read_text())
    # Harbor 0.24 serializes the legacy task.toml `version` key as `schema_version`.
    canonical = dict(original)
    if "version" in canonical:
        canonical.setdefault("schema_version", canonical.pop("version"))
    if (
        native["config"] not in (original, canonical)
        or native["instruction"] != (source / "instruction.md").read_text()
    ):
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
    if re.fullmatch(r".+@sha256:[0-9a-f]{64}", image) is None:
        raise ValueError("effective task image must be pinned by digest")
    if environment.get("verifier") is not None or environment.get("compose_images"):
        raise ValueError("separate verifier and compose image mappings are not qualified")
    if input_digest(source) != published_task.context_sha256:
        raise ValueError("task context differs from unchanged prepared task")
    verify_archive(archive, source, archive_digest)
    expected_files, effective_files = file_manifest(source), file_manifest(effective)
    expected_files.pop("task.toml")
    effective_files.pop("task.toml", None)
    if expected_files != effective_files:
        raise ValueError("effective task changed files other than image binding")
    expected_config = copy.deepcopy(original)
    expected_config.setdefault("environment", {})["docker_image"] = image
    if tomllib.loads((effective / "task.toml").read_text()) != expected_config:
        raise ValueError("effective task changes more than the image binding")
