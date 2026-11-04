# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Revision-guarded OpenCode binary substitution for the pinned live evaluator."""

import hashlib
import json
import os
import re
import stat
import sys
from importlib import import_module
from importlib.metadata import distribution
from pathlib import Path
from types import CodeType

EVALUATOR_REVISION = "3fd2cf57e4a2af01f282e001f7515b6e76f18ec7"
SOURCE_HASHES = {
    "harbor/agents/installed/opencode.py": "589eab12accc9f08469f392366b8bd14d1d8f6816fc38ed6f5720e7cb4517e2c",
    "harbor/agents/installed/base.py": "31a7721e0cfa5522f85056439495b9741e2232869537f9ec21fb9ac95b611647",
    "skillevaluator/tier3/harbor/runner.py": "448414c43e0f67d3c853e9bbd9c92a57834668eed06f19dcdb01abedecea46e4",
    "skillevaluator/tier3/harbor/secure_docker_environment.py": "fd5ac97179d5b345c4c52b0ea9d2cfaf3fef8ce87333e0c3449d8fbda8298bd4",
    "skillevaluator/tier3/harbor/runtime_preflight.py": "4ee498033103db9cf8fd1ce760dfdd54553b48adad8bb4ed89aa780e3396fe3f",
    "skillevaluator/tier3/harbor/local_agents.py": "2e75af940679128a0e899ce13789c34093a52198243fc2834db48e3c22863a07",
    "harbor/environments/base.py": "b677e2376b0e4ee2a9c63220ea1108e4e4917797da2ee48c4177dc1aa3efe1d0",
    "harbor/environments/docker/docker.py": "77911f4df7fe29850d006ec969786a920302668fa7cdf35ff7c31cc96337bf65",
    "skillevaluator/tier3/harbor/__init__.py": "2891fe068ab02dca57a96ca481cfa984d38ab0ff5d41354c7f7d28faa7a7a3d9",
}

CONFIGURED_VERSION = "1.19.5"
EXPECTED_VERSION = "1.19.5+nodesink.e991596fecc9"
REMOTE = "/opt/ea-patched-opencode/opencode"
PREFIX = "[ -f ~/.nvm/nvm.sh ] && . ~/.nvm/nvm.sh; "
MANIFEST_ENV = "EA_PATCHED_OPENCODE_MANIFEST"
HOOK_DIR = Path(__file__).resolve().parent / "patched_opencode_hook"
TOKEN_ENV = "_EA_PATCHED_OPENCODE_HOOK"
DIGEST_ENV = "_EA_PATCHED_OPENCODE_RUNTIME_SHA256"
MANIFEST_DIGEST_ENV = "_EA_PATCHED_OPENCODE_MANIFEST_SHA256"
TOKEN = "pinned-v1"


def activation_environment(manifest_path):
    environment = {
        MANIFEST_ENV: str(manifest_path),
        TOKEN_ENV: TOKEN,
        DIGEST_ENV: sha256(Path(__file__).resolve()),
        MANIFEST_DIGEST_ENV: sha256(manifest_path),
        "PYTHONPATH": str(HOOK_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    _check_environment(os.environ, environment)
    regular_file(HOOK_DIR / "sitecustomize.py")
    customization = sys.modules.get("sitecustomize")
    if (
        customization is not None
        and Path(getattr(customization, "__file__", "")).resolve() != HOOK_DIR / "sitecustomize.py"
    ):
        raise RuntimeError("conflicting sitecustomize instrumentation")
    if "usercustomize" in sys.modules:
        raise RuntimeError("conflicting usercustomize instrumentation")
    return environment


def _check_environment(current, expected):
    for key in (MANIFEST_ENV, TOKEN_ENV, DIGEST_ENV, MANIFEST_DIGEST_ENV, "PYTHONPATH"):
        if key in current and current[key] != expected[key]:
            raise RuntimeError("conflicting patched OpenCode activation environment")
    if current.get("PYTHONHOME") or current.get("PYTHONSTARTUP"):
        raise RuntimeError("conflicting Python instrumentation environment")


def regular_file(path):
    path = Path(path)
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("expected an absolute regular file without symlinks")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("expected a regular file")
    return path


def sha256(path):
    with regular_file(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def load_manifest(path):
    path = regular_file(path)
    if path.stat().st_size > 16384:
        raise ValueError("binary manifest is too large")
    data = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    if not isinstance(data, dict) or set(data) != {"schema_version", "binary_path", "sha256", "expected_version"}:
        raise ValueError("invalid binary manifest schema")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["expected_version"] != EXPECTED_VERSION
    ):
        raise ValueError("invalid binary manifest version")
    if not isinstance(data["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", data["sha256"]):
        raise ValueError("invalid binary SHA256")
    if not isinstance(data["binary_path"], str):
        raise ValueError("invalid binary path")
    binary = regular_file(data["binary_path"])
    if not os.access(binary, os.X_OK):
        raise ValueError("binary is not executable")
    if sha256(binary) != data["sha256"]:
        raise ValueError("host binary SHA256 mismatch")
    return data


def _codes(code):
    yield code
    for child in code.co_consts:
        if isinstance(child, CodeType):
            yield from _codes(child)


def verify_contract():
    evaluator = distribution("skillevaluator")
    if evaluator.version != "0.7.0" or distribution("harbor").version != "0.24.0":
        raise RuntimeError("patched OpenCode package version contract changed")
    origin = json.loads(evaluator.read_text("direct_url.json") or "{}")
    if (
        not isinstance(origin, dict)
        or origin.get("vcs_info", {}).get("vcs") != "git"
        or origin.get("vcs_info", {}).get("commit_id") != EVALUATOR_REVISION
        or "dir_info" in origin
    ):
        raise RuntimeError("patched OpenCode evaluator revision contract changed")
    modules = {}
    compiled = {}
    for relative, expected in SOURCE_HASHES.items():
        path = regular_file(distribution(relative.split("/")[0]).locate_file(relative))
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != expected:
            raise RuntimeError("patched OpenCode installed source contract changed")
        name = relative.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        module = import_module(name)
        if not isinstance(module.__file__, str) or Path(module.__file__).resolve() != path.resolve():
            raise RuntimeError("patched OpenCode imported source contract changed")
        modules[name] = module
        compiled[name] = {
            code.co_qualname: code
            for code in _codes(compile(source, str(path), "exec", dont_inherit=True, optimize=sys.flags.optimize))
        }
    functions = {
        "harbor.agents.installed.base": [
            "BaseInstalledAgent.setup",
            "BaseInstalledAgent._exec",
            "BaseInstalledAgent.exec_as_root",
            "BaseInstalledAgent.exec_as_agent",
        ],
        "harbor.agents.installed.opencode": ["OpenCode.run", "OpenCode.install"],
        "skillevaluator.tier3.harbor.runner": ["_harbor_launch_environment", "build_harbor_run_command", "_run_harbor"],
        "skillevaluator.tier3.harbor.runtime_preflight": ["run_agent_runtime_preflight"],
        "skillevaluator.tier3.harbor.local_agents": ["SkillEvaluatorGatewayOpenCode.__init__"],
    }
    for name, attrs in functions.items():
        for attr in attrs:
            function = modules[name]
            for part in attr.split("."):
                function = getattr(function, part)
            if attr == "OpenCode.run":
                if (
                    function.__code__
                    != compiled["harbor.agents.installed.base"].get("with_prompt_template.<locals>.wrapper")
                    or len(function.__closure__ or ()) != 1
                ):
                    raise RuntimeError("patched OpenCode decorator contract changed")
                function = function.__closure__[0].cell_contents
            if (
                getattr(function, "__code__", None) != compiled[name].get(attr)
                or getattr(function, "__module__", None) != name
            ):
                raise RuntimeError("patched OpenCode function contract changed")
    base = modules["harbor.agents.installed.base"].BaseInstalledAgent
    opencode = modules["harbor.agents.installed.opencode"].OpenCode
    gateway = modules["skillevaluator.tier3.harbor.local_agents"].SkillEvaluatorGatewayOpenCode
    runner = modules["skillevaluator.tier3.harbor.runner"]
    preflight = modules["skillevaluator.tier3.harbor.runtime_preflight"]
    if (
        opencode.setup is not base.setup
        or gateway.setup is not base.setup
        or opencode._exec is not base._exec
        or gateway._exec is not base._exec
        or gateway.run is not opencode.run
        or gateway.install is not opencode.install
        or preflight._harbor_launch_environment is not runner._harbor_launch_environment
    ):
        raise RuntimeError("patched OpenCode inherited function contract changed")


def activate(manifest_path):
    import shlex

    verify_contract()
    manifest_path = regular_file(manifest_path)
    bound = load_manifest(manifest_path)
    activation = activation_environment(manifest_path)
    BaseInstalledAgent = import_module("harbor.agents.installed.base").BaseInstalledAgent
    OpenCode = import_module("harbor.agents.installed.opencode").OpenCode
    SkillEvaluatorGatewayOpenCode = import_module(
        "skillevaluator.tier3.harbor.local_agents"
    ).SkillEvaluatorGatewayOpenCode
    SkillEvaluatorSecureDockerEnvironment = import_module(
        "skillevaluator.tier3.harbor.secure_docker_environment"
    ).SkillEvaluatorSecureDockerEnvironment

    original_exec = BaseInstalledAgent._exec

    def validate(self, environment):
        if type(self) not in (OpenCode, SkillEvaluatorGatewayOpenCode):
            raise RuntimeError("unexpected OpenCode class")
        if self._version != CONFIGURED_VERSION:
            raise RuntimeError("expected original OpenCode install pin")
        if type(environment) is not SkillEvaluatorSecureDockerEnvironment:
            raise RuntimeError("expected SecureDocker environment")
        if load_manifest(manifest_path) != bound:
            raise RuntimeError("binary manifest changed after activation")

    async def check_remote(self, environment):
        result = await original_exec(self, environment, "sha256sum " + REMOTE, user="root")
        if (result.stdout or "").strip() != bound["sha256"] + "  " + REMOTE:
            raise RuntimeError("container binary SHA256 mismatch")

    async def install(self, environment):
        self._ea_patched_ready = None
        validate(self, environment)
        # Keep native system dependencies; the standalone Bun CLI needs no NVM bootstrap.
        await self.ensure_system_dependencies(environment, ("curl", "bash", "coreutils", "nodejs", "npm"))
        validate(self, environment)
        await self.exec_as_root(
            environment,
            command="set -eu; test ! -e /opt/ea-patched-opencode; test ! -L /opt/ea-patched-opencode; mkdir -m 755 /opt/ea-patched-opencode; chown root:root /opt/ea-patched-opencode",
        )
        await environment.upload_file(Path(bound["binary_path"]), REMOTE)
        validate(self, environment)
        await self.exec_as_root(
            environment,
            command="set -eu; test -f "
            + REMOTE
            + "; test ! -L "
            + REMOTE
            + "; chown root:root "
            + REMOTE
            + "; chmod 755 "
            + REMOTE,
        )
        await check_remote(self, environment)
        result = await original_exec(self, environment, REMOTE + " --version")
        observed = (result.stdout or "").strip()
        if observed != EXPECTED_VERSION:
            raise RuntimeError("container binary version mismatch")
        await check_remote(self, environment)
        receipt = dict(
            schema_version=1,
            configured_version=CONFIGURED_VERSION,
            expected_version=EXPECTED_VERSION,
            observed_version=observed,
            host_sha256=bound["sha256"],
            container_sha256=bound["sha256"],
            agent_class=type(self).__module__ + ":" + type(self).__name__,
        )
        path = self.logs_dir / ".ea-patched-binary.json"
        if any(parent.is_symlink() for parent in path.parents):
            raise RuntimeError("unsafe private binary receipt directory")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(receipt, stream)
        self._ea_patched_ready = environment

    async def execute(self, environment, command, *args, **kwargs):
        if isinstance(self, OpenCode) and command.startswith(PREFIX + "opencode"):
            validate(self, environment)
            if getattr(self, "_ea_patched_ready", None) is not environment:
                raise RuntimeError("patched binary setup not verified")
            suffix = " 2>&1 </dev/null | stdbuf -oL tee " + shlex.quote(
                (self.environment_logs_dir / "opencode.txt").as_posix()
            )
            if (
                not command.startswith(PREFIX + "opencode --model=")
                or not command.endswith(suffix)
                or " run --format=json " not in command.partition(" -- ")[0]
            ):
                raise RuntimeError("OpenCode launcher source contract mismatch")
            await check_remote(self, environment)
            # Replace exactly the executable token; never touch argv or the pipeline.
            command = PREFIX + REMOTE + command[len(PREFIX + "opencode") :]
        return await original_exec(self, environment, command, *args, **kwargs)

    runner = import_module("skillevaluator.tier3.harbor.runner")
    runtime_preflight = import_module("skillevaluator.tier3.harbor.runtime_preflight")

    original_launch = runner._harbor_launch_environment

    def launch(environment):
        _check_environment(environment, activation)
        if load_manifest(manifest_path) != bound:
            raise RuntimeError("binary manifest changed before Harbor launch")
        result = original_launch(environment)
        _check_environment(result, activation)
        result.update(activation)
        return result

    setattr(install, "_ea_patched_opencode", True)
    OpenCode.install = install
    BaseInstalledAgent._exec = execute
    setattr(runner, "_harbor_launch_environment", launch)
    setattr(runtime_preflight, "_harbor_launch_environment", launch)
