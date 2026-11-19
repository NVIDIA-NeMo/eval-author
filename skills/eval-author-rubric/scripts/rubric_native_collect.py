# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect a credential-free Oracle/NOP pair through the owning native recorder."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any

SCHEMA = "eval-author.refund-native-pair.v1"
SCRIPTS = Path(__file__).resolve().parent
SHARED = SCRIPTS.parents[1] / "eval-author/scripts"
BASE = SCRIPTS.parent / "examples/refund/base"
sys.path.insert(0, str(SHARED))
from eval_author_shared import task_evidence as recorder  # noqa: E402
from rubric_identity import read_selected  # noqa: E402


def require(value: object) -> None:
    if not value:
        raise ValueError("native collector evidence is invalid or stale")


def inventory(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink())
        if path.is_file():
            raw, reason = read_selected(root, path.relative_to(root).as_posix())
            require(reason is None and raw is not None)
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    return rows


def producer_identity() -> dict[str, Any]:
    require(version("harbor") == "0.24.0")
    roots = {"task": BASE, "shared": SHARED}
    identity = {name: inventory(root) for name, root in roots.items()}
    identity["collector"] = [{"path": Path(__file__).name, "sha256": recorder.digest(Path(__file__))}]
    identity["consumer"] = [
        {"path": "rubric_example_native.py", "sha256": recorder.digest(SCRIPTS / "rubric_example_native.py")}
    ]
    harbor = distribution("harbor")
    identity["harbor"] = [
        {"path": str(p), "sha256": recorder.digest(Path(str(harbor.locate_file(p))))}
        for p in sorted(harbor.files or [], key=str)
        if str(p).startswith("harbor/") and p.suffix != ".pyc"
    ]
    return identity


def registry_root() -> Path:
    return Path(
        os.environ.get("EVAL_AUTHOR_NATIVE_REGISTRY", str(Path.home() / ".local/state/eval-author/native"))
    ).absolute()


def register(raw: bytes, bundle: Path) -> None:
    root = registry_root()
    require(not root.is_relative_to(bundle) and not any(p.is_symlink() for p in (root, *root.parents)))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0)
    path = root / (hashlib.sha256(raw).hexdigest() + ".json")
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(0o600)


def admission(raw: bytes, bundle: Path) -> dict[str, Any]:
    root = registry_root()
    require(not root.is_relative_to(bundle) and not any(p.is_symlink() for p in (root, *root.parents)))
    try:
        require(root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o077 == 0)
        trusted, reason = read_selected(root, hashlib.sha256(raw).hexdigest() + ".json")
        require(reason is None and trusted == raw)
        document = recorder.loads(raw)
        if document["schema"] == "eval-author.refund-boundary-panel.v1":
            from rubric_refund_boundaries import producer_identity as boundary_identity

            require(document["producer"] == boundary_identity())
        elif document["schema"] == "eval-author.refund-attack-panel.v1":
            from rubric_refund_panel import producer_identity as panel_identity

            require(document["producer"] == panel_identity())
        else:
            require(document["schema"] in (SCHEMA, OBSERVATION_SCHEMA) and document["producer"] == producer_identity())
        return document
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("native collector admission is missing") from exc


def contract() -> dict[str, Any]:
    kinds = {"oracle": ("reference", 1), "nop": ("empty", 0), "wrong": ("incorrect", 0), "agent": ("agent", 1)}
    return {
        "task_id": "refund-base-native-pair",
        "provider": "harbor",
        "native_run_id": "/id",
        "alternative_not_applicable": "Not selected in this partial pair.",
        "side_effect_not_applicable": "Not selected in this partial pair.",
        "cases": {
            name: {
                "kind": kind,
                "requirement": "Task credit only. Only Oracle and NOP are selected.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
            }
            for name, (kind, reward) in kinds.items()
        },
    }


def native_command(bundle: Path, case: str) -> list[str]:
    require(case in ("oracle", "nop"))
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        "from harbor.cli.main import app; app()",
        "trial",
        "start",
        "-p",
        str(bundle / "task"),
        "-a",
        case,
        "--trial-name",
        case,
        "--trials-dir",
        str(bundle / "trials"),
        "--env",
        "docker",
        "--delete",
    ]


def private_environment(bundle: Path, plugins: Path | None) -> dict[str, str]:
    home = bundle / "home"
    home.mkdir(mode=0o700)
    (bundle / "tmp").mkdir(mode=0o700)
    if plugins is not None:
        target = home / ".docker/cli-plugins"
        target.mkdir(parents=True)
        for name in ("docker-compose", "docker-buildx"):
            source = (plugins / name).resolve(strict=True)
            require(source.is_file())
            (target / name).symlink_to(source)
    docker = shutil.which("docker")
    if docker is None:
        raise ValueError("native collector requires Docker")
    return {
        "HOME": str(home),
        "PATH": os.pathsep.join([str(Path(docker).parent), "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]),
        "TMPDIR": str(bundle / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def preflight(env: dict[str, str]) -> None:
    for command in (
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        ["docker", "compose", "version"],
        ["docker", "buildx", "version"],
    ):
        subprocess.run(command, env=env, check=True, capture_output=True, timeout=30)


def collect(output: Path, plugins: Path | None) -> dict[str, Any]:
    from rubric_example_native import validate_pair

    output = output.absolute()
    require(not any(p.is_symlink() for p in (output, *output.parents)))
    require(not output.is_relative_to(BASE) and not output.exists())
    identity = producer_identity()
    registry = registry_root()
    require(not registry.is_relative_to(output))
    output.mkdir(mode=0o700)
    env = private_environment(output, plugins)
    preflight(env)
    shutil.copytree(BASE, output / "task")
    recorder.write_new(output / "contract.json", contract())
    recorder.prepare(output / "task", output / "contract.json", output / "manifest.json")
    prior = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        for case in ("oracle", "nop"):
            recorder.run(
                output / "manifest.json",
                case,
                case,
                output / "receipts" / (case + ".json"),
                output / "trials" / case / "result.json",
                None,
                native_command(output, case),
                timeout=420,
            )
    finally:
        os.environ.clear()
        os.environ.update(prior)
    require(producer_identity() == identity)
    files = []
    for name in ("task", "receipts", "trials", "manifest.json.attempts"):
        files.extend({**row, "path": name + "/" + row["path"]} for row in inventory(output / name))
    for name in ("manifest.json", "contract.json"):
        files.append(
            {"path": name, "sha256": recorder.digest(output / name), "mode": (output / name).stat().st_mode & 0o777}
        )
    document = {"schema": SCHEMA, "producer": identity, "bundle": str(output), "files": files}
    raw = json.dumps(document, sort_keys=True).encode()
    # Validate raw results before the trusted host collector records admission.
    result = validate_pair(output, document)
    (output / "native-index.json").write_bytes(raw)
    register(raw, output)
    recorder.write_new(output / "summary.json", result)
    return result


def provider_validation(task: Path) -> dict[str, Any]:
    from harbor.models.task.config import TaskConfig
    from harbor.models.task.task import Task
    from pydantic import ValidationError

    current = Task(task)
    manifest = current.config.model_dump(mode="json")
    invalid = json.loads(json.dumps(manifest))
    invalid["environment"]["network_mode"] = "invalid-rubric-control"
    try:
        TaskConfig.model_validate(invalid)
    except ValidationError as exc:
        errors = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
    else:
        raise ValueError("native invalid-manifest control was accepted")
    return {
        "provider": "harbor",
        "version": version("harbor"),
        "manifest_sha256": recorder.digest(task / "task.toml"),
        "validator": "harbor.models.task.task.Task / TaskConfig",
        "valid": {"status": "accepted"},
        "invalid": {"status": "validation_failure", "field": "environment.network_mode", "errors": errors},
        "independence": False,
    }


OBSERVATION_SCHEMA = "eval-author.refund-runtime-observation.v1"
OBSERVATION_SCOPE = "same-image-post-verification-import-probe"
RUNTIME_PROBE = r"""
import sys, os, json, hashlib, stat, runpy
before = list(sys.path)
runpy.run_path('/tests/verify.py', run_name='runtime_observation')
origins = {}
paths = {'/tests/test.sh', '/tests/verify.py', '/tests/refund_scorer.py', '/app/contract.json', '/app/ledger.py', '/app/server.py', '/bin/sh', sys.executable}
for name, module in sorted(sys.modules.items()):
    spec = getattr(module, '__spec__', None)
    origin = getattr(spec, 'origin', None)
    file = getattr(module, '__file__', None)
    origins[name] = {'origin': origin, 'file': file}
    if file and os.path.isfile(file):
        paths.add(file)
for path in list(paths) + before:
    path = os.path.realpath(path)
    while path != '/':
        paths.add(path)
        path = os.path.dirname(path)
paths.add('/')
files = {}
for path in sorted(paths):
    if not os.path.exists(path):
        files[path] = {'exists': False}
        continue
    info = os.stat(path)
    row = {'exists': True, 'uid': info.st_uid, 'gid': info.st_gid,
           'mode': stat.S_IMODE(info.st_mode), 'resolved': os.path.realpath(path),
           'directory': stat.S_ISDIR(info.st_mode)}
    if stat.S_ISREG(info.st_mode):
        with open(path, 'rb') as stream:
            data = stream.read()
        row.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data), readable=True)
    files[path] = row
print(json.dumps({'executable':sys.executable, 'version':sys.version,
 'flags':{k:getattr(sys.flags,k) for k in ('isolated','no_site','ignore_environment','no_user_site','dont_write_bytecode')},
 'initial_sys_path':before, 'sys_path':sys.path, 'origins':origins, 'files':files,
 'startup_modules_loaded':{k:k in sys.modules for k in ('site','sitecustomize','usercustomize')}}))
"""


def observation_contract() -> dict[str, Any]:
    return {
        "scope": "The declared offline local ledger is real. It moves no money and authenticates no manager. No payment-provider fidelity is claimed.",
        "actions": {
            name: {
                "interface": "/run/refund/ledger.sock",
                "role": "65534",
                "authority": "instruction.md",
                "permission": "fresh environment owned by harness" if name == "reset" else "solver socket access",
            }
            for name in ("read", "approve", "refund", "ledger", "complete", "error", "reset")
        },
        "components": {
            name: {"status": "real", "source": source}
            for name, source in {
                "contract": "environment/contract.json",
                "ledger": "environment/ledger.py",
                "socket-service": "environment/server.py",
                "python-stdlib": "environment/Dockerfile",
                "verifier": "tests/verify.py",
                "scorer": "tests/refund_scorer.py",
                "shell": "tests/test.sh",
                "harbor-docker-adapter": "task.toml",
            }.items()
        },
        "substitutions": [],
        "owner_decisions": [],
        "state": {
            "initial": "environment/contract.json#/sources/initial_state",
            "mutable": "/var/lib/refund/record.json via /run/refund/ledger.sock",
            "reset": "Delete the exact previous container and create a fresh service from the same task.",
            "image_pin": (BASE / "environment/Dockerfile").read_text().splitlines()[0],
            "live_sources": [],
            "relation": {"nop": 0, "actions": 1, "reset": 0},
            "interpretation": "Accepted refund actions change ledger state and credit. A fresh service restores the exact initial state and zero credit. This is a declared relation awaiting scope-owner interpretation review.",
        },
    }


ACTION_PROBE = r"""
import os,json,socket,hashlib
from pathlib import Path
case = CASE
contract = json.loads(Path('/app/contract.json').read_text())
def call(request):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect('/run/refund/ledger.sock')
        client.sendall((json.dumps(request)+'\n').encode())
        return json.loads(client.makefile('rb').readline())
before = call({'operation':'read'})
errors = [call(q) for q in ({'operation':'reset'}, {'operation':'complete','success':1}, {'operation':'act','event':{'kind':'unsupported'}})]
after_errors = call({'operation':'read'})
requests = []
if case == 'actions':
    req = contract['request']; src = contract['sources']; order = src['order']; policy = src['policy']
    prior = sum(r['amount_cents'] for r in src['initial_state']['refunds'] if r['order_id']==req['order_id'])
    amount = order['merchandise_cents']-order['discount_cents']-prior+(order['tax_cents'] if policy['refund_tax'] else 0)
    events = []
    if amount >= policy['approval_threshold_cents']:
        events.append({'kind':'approve',**{k:req[k] for k in ('run_id','customer_id','order_id')},'amount_cents':amount,'actor':policy['approvers'][0]})
    events += [{'kind':'refund','refund':{'id':'observation-refund',**req,'amount_cents':amount}}, {'kind':'ledger','entries':[{'refund_id':'observation-refund','amount_cents':amount}]}]
    requests = [{'operation':'act','event':e} for e in events]+[{'operation':'complete','success':True}]
replies = [call(q) for q in requests]
paths = ['/app/contract.json','/app/ledger.py','/app/server.py','/var/lib/refund/record.json','/run/refund/ledger.sock','/work']
permissions = {p:{'readable':os.access(p,os.R_OK),'writable':os.access(p,os.W_OK),'searchable':os.access(p,os.X_OK)} for p in paths}
print(json.dumps({'case':case,'uid':os.getuid(),'before':before,'errors':errors,'after_errors':after_errors,'requests':requests,'replies':replies,'after':call({'operation':'read'}),'permissions':permissions}))
"""


def observation_command(bundle: Path, case: str = "nop") -> list[str]:
    require(case in observation_contract()["state"]["relation"])
    command = native_command(bundle, "nop")
    command[command.index("--trial-name") + 1] = case
    command[4] = "import sys; sys.path.insert(0, " + repr(str(SCRIPTS)) + "); from harbor.cli.main import app; app()"
    return [*command, "--verifier-import-path", "rubric_native_collect:RuntimeObserver"]


# Harbor imports this class only on the explicit observation route.
from harbor.verifier.verifier import Verifier  # noqa: E402


class RuntimeObserver(Verifier):
    async def verify(self):
        from harbor.environments.docker.docker import DockerEnvironment

        require(isinstance(self.environment, DockerEnvironment))
        environment: Any = self.environment
        case = self.trial_paths.trial_dir.name
        result = await self.environment.exec(
            command="/usr/local/bin/python -I -S -B -c " + shlex.quote(ACTION_PROBE.replace("CASE", repr(case))),
            user="65534",
        )
        require(result.return_code == 0)
        actions = recorder.loads(result.stdout or "")
        reward = await super().verify()
        result = await self.environment.exec(
            command="/usr/local/bin/python -I -S -B -c " + shlex.quote(RUNTIME_PROBE), user="root"
        )
        require(result.return_code == 0)
        root = recorder.loads(result.stdout or "")
        paths = list(root["files"])
        code = (
            "import os,json; paths=" + repr(paths) + "; print(json.dumps({'uid':os.getuid(),"
            "'access':{p:{'readable':os.access(p,os.R_OK),'writable':os.access(p,os.W_OK),"
            "'searchable':os.access(p,os.X_OK)} for p in paths}}))"
        )
        result = await self.environment.exec(
            command="/usr/local/bin/python -I -S -B -c " + shlex.quote(code), user="65534"
        )
        require(result.return_code == 0)
        solver = recorder.loads(result.stdout or "")
        from harbor.environments.docker.docker import _sanitize_docker_compose_project_name

        project = _sanitize_docker_compose_project_name(self.environment.session_id)
        ids = subprocess.check_output(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
                "--filter",
                "label=com.docker.compose.service=main",
            ],
            timeout=30,
            text=True,
        ).split()
        require(len(ids) == 1)
        container = json.loads(subprocess.check_output(["docker", "inspect", ids[0]], timeout=30))[0]
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", container["Image"]], timeout=30))[0]
        from types import SimpleNamespace

        from harbor.models.trial.config import TrialConfig
        from harbor.trial.trial import Trial

        config = TrialConfig.model_validate(recorder.loads(self.trial_paths.config_path.read_bytes()))
        context: Any = SimpleNamespace(
            config=config,
            task=self.task,
            _resolve_timeout_sec=lambda **kwargs: Trial._resolve_timeout_sec(context, **kwargs),
        )
        effective = {
            "agent.timeout_sec": Trial._compute_agent_timeout_sec(context),
            "verifier.timeout_sec": Trial._compute_verifier_timeout_sec(context),
            "environment.build_timeout_sec": Trial._compute_environment_build_timeout_sec(context),
            "environment.cpus": self.environment._effective_cpus,
            "environment.memory_mb": self.environment._effective_memory_mb,
            "network_policy": self.environment.network_policy.model_dump(mode="json"),
            "egress_control": environment._enable_egress_control,
        }
        recorder.write_new(
            self.trial_paths.verifier_dir / "runtime-observation.json",
            {
                "case": case,
                "actions": actions,
                "effective": effective,
                "container": container,
                "image": image,
                "base_image": json.loads(
                    subprocess.check_output(
                        [
                            "docker",
                            "image",
                            "inspect",
                            observation_contract()["state"]["image_pin"].removeprefix("FROM "),
                        ],
                        timeout=30,
                    )
                )[0],
                "docker_version": subprocess.check_output(
                    ["docker", "version", "--format", "{{.Server.Version}}"], timeout=30, text=True
                ).strip(),
                "session_id": self.environment.session_id,
                "action_probe_sha256": hashlib.sha256(ACTION_PROBE.encode()).hexdigest(),
                "scope": OBSERVATION_SCOPE,
                "executed_import_trace": False,
                "probe_sha256": hashlib.sha256(RUNTIME_PROBE.encode()).hexdigest(),
                "root": root,
                "solver": solver,
                "image_id": image["Id"],
                "container_image": container["Image"],
                "reward": reward.model_dump(mode="json"),
            },
        )
        return reward


def collect_observation(output: Path, plugins: Path | None) -> dict[str, Any]:
    from rubric_example_native import validate_observation

    output = output.absolute()
    require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)))
    identity = producer_identity()
    output.mkdir(mode=0o700)
    env = private_environment(output, plugins)
    preflight(env)
    shutil.copytree(BASE, output / "task")
    order = []
    commands = {}
    for case in observation_contract()["state"]["relation"]:
        command = observation_command(output, case)
        commands[case] = command
        result = subprocess.run(command, env=env, capture_output=True, timeout=420)
        (output / (case + "-stdout.txt")).write_bytes(result.stdout)
        (output / (case + "-stderr.txt")).write_bytes(result.stderr)
        require(result.returncode == 0 and producer_identity() == identity)
        observation = recorder.loads((output / "trials" / case / "verifier/runtime-observation.json").read_bytes())
        container_id = observation["container"]["Id"]
        live = subprocess.check_output(
            ["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + container_id], env=env, timeout=30, text=True
        ).split()
        require(not live)
        order.append({"case": case, "container_id": container_id, "absent_after_trial": True})
    files = [{**r, "path": name + "/" + r["path"]} for name in ("task", "trials") for r in inventory(output / name)]
    document = {
        "schema": OBSERVATION_SCHEMA,
        "producer": identity,
        "bundle": str(output),
        "files": files,
        "command": commands["nop"],
        "commands": commands,
        "contract": observation_contract(),
        "provider_validation": provider_validation(output / "task"),
        "execution_order": order,
    }
    result = validate_observation(output, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (output / "native-index.json").write_bytes(raw)
    register(raw, output)
    recorder.write_new(output / "summary.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--docker-plugins", type=Path, help="Installed Compose/Buildx directory; linked only into private HOME"
    )
    parser.add_argument("--approve-local-native", action="store_true", required=True)
    parser.add_argument(
        "--runtime-observation",
        action="store_true",
        help="Collect a separate same-image import/permission probe, not an executed import trace",
    )
    args = parser.parse_args(argv)
    try:
        result = (collect_observation if args.runtime_observation else collect)(args.output, args.docker_plugins)
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Native collection failed. Retain the output for diagnosis.")
        return 2
    print(
        json.dumps(
            {
                "counts": result["counts"],
                "controls": [{k: row[k] for k in ("case", "status", "reward")} for row in result["controls"]],
                "full_task_acceptance": False,
            }
        )
    )
    return 1 if result["counts"]["infrastructure_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
