# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Revision-guarded OpenCode binary substitution for the pinned live evaluator."""

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from contextvars import ContextVar
from importlib import import_module
from importlib.metadata import distribution
from pathlib import Path
from types import CodeType

EVALUATOR_REVISION = "3fd2cf57e4a2af01f282e001f7515b6e76f18ec7"
SOURCE_HASHES = {
    "harbor/models/task/config.py": "f9cd1d01960de82f1ec025c285a7367cec5eb8e3a2bb0b8e6313e7bce3283e5e",
    "harbor/models/task/task.py": "6b87b99d62b438b8e2692f7a03c12b2f14a9ecf26a667efd7c2c16b970254211",
    "harbor/trial/trial.py": "3bb2c0b9dd7553022ba98da96ef5188abc27e2c8475ac949d541f47c5d86ecc6",
    "skillevaluator/tier3/harbor/adapter.py": "45135d18ea50fb3fd995df0c94167a75395f0a3621a6bdde099bd3ddd40425e1",
    "harbor/environments/definition.py": "6bb4a9bb5747fd3c8dcc92dfa57f4637749fef1dbf3adf932e7400dbe6b0e3ff",
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
PREPARATION_ENV = "EA_PREPARED_IMAGES_MANIFEST"
PREPARATION_DIGEST_ENV = "_EA_PREPARED_IMAGES_SHA256"
REQUIRE_PREPARATION_ENV = "EA_REQUIRE_PREPARED_IMAGES"
SYSTEM_DEPENDENCIES = ("curl", "bash", "coreutils", "nodejs", "npm")
DEPENDENCY_CHECK = " && ".join(
    "command -v " + name + " >/dev/null 2>&1" for name in ("curl", "bash", "stdbuf", "node", "npm")
)
IMAGE_LABEL = "org.eval-author.preparation"
BASE_IMAGES = {
    "linux/amd64": "python:3.12.16-slim-trixie@sha256:05a994d2344eb364cc186910f4fc8786a38215f39f00d0acc25bff33a08f4177",
    "linux/arm64": "python:3.12.16-slim-trixie@sha256:c61cffc3bacb94ed026ff8b45d5114b7693dd0420baca0a1ace0e3d0ce9035fd",
}
PACKAGE_VERSIONS = {
    "curl": "8.14.1-2+deb13u7",
    "bash": "5.2.37-2+b10",
    "coreutils": "9.7-3",
    "nodejs": "20.19.2+dfsg-1+deb13u3",
    "npm": "9.2.0~ds1-3",
}
PACKAGE_QUERY = "dpkg-query -W -f='${Package}=${Version}\\n' " + " ".join(SYSTEM_DEPENDENCIES)


def dependency_install_command(*, default_sources=False):
    # Use isolated, signed snapshot indexes without replacing custom apt sources.
    sources = "\\n".join(
        f"deb [check-valid-until=no] https://snapshot.debian.org/archive/{archive}/20261105T000000Z/ {suite} main"
        for archive, suite in (("debian", "trixie"), ("debian-security", "trixie-security"))
    )
    apt = 'apt-get -o Acquire::Retries=3 -o Dir::Etc::sourcelist="$sources" -o Dir::Etc::sourceparts=- '
    return (
        "set -eu; sources=$(mktemp); trap 'rm -f \"$sources\"' EXIT; "
        + f"printf '%b\\n' '{sources}' > \"$sources\"; rm -rf /var/lib/apt/lists/*; "
        + apt
        + "update && "
        + apt
        + "install -y --no-install-recommends "
        + " ".join(name + "=" + PACKAGE_VERSIONS[name] for name in SYSTEM_DEPENDENCIES)
        + " && rm -rf /var/lib/apt/lists/* && "
        + DEPENDENCY_CHECK
        + (
            ' && cp "$sources" /etc/apt/sources.list && chmod 644 /etc/apt/sources.list'
            " && rm -f /etc/apt/sources.list.d/debian.sources"
            if default_sources
            else ""
        )
    )


def pinned_dockerfile(source, platform, *, dependency_image=None):
    # Bound the supported grammar rather than mistaking physical lines or
    # heredoc bodies for instructions. Preserve all non-FROM source bytes.
    instructions = []
    pending = ""
    offset = start = 0
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"#\s*(syntax|escape|check)\s*=", stripped, re.I) or "<<" in line:
            raise ValueError("unsupported prepared Dockerfile syntax")
        if stripped and not stripped.startswith("#"):
            if not pending:
                start = offset
            continued = stripped.endswith("\\")
            pending += stripped[:-1] + " " if continued else stripped
            if not continued:
                match = re.fullmatch(r"([A-Za-z]+)\s+(.+)", pending)
                if not match:
                    raise ValueError("unsupported prepared Dockerfile instruction")
                instructions.append((match[1].upper(), match[2], start, offset + len(line)))
                pending = ""
        offset += len(line)
    if pending or not instructions or instructions[0][0] != "FROM":
        raise ValueError("prepared Dockerfile requires a leading FROM")
    if sum(item[0] == "FROM" for item in instructions) != 1 or platform not in BASE_IMAGES:
        raise ValueError("prepared builds require one pinned base image")
    _, value, start, end = instructions[0]
    match = re.fullmatch(r"(\S+)(?:\s+AS\s+([A-Za-z0-9_-]+))?", value, re.I)
    if not match:
        raise ValueError("unsupported prepared Dockerfile FROM")
    image = match[1]
    if image in ("python:3.12-slim", "python:3.12.16-slim-trixie"):
        if dependency_image is not None and not re.fullmatch("sha256:[a-f0-9]{64}", dependency_image):
            raise ValueError("invalid immutable dependency image")
        image = dependency_image or BASE_IMAGES[platform]
    elif not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("custom base image must be pinned by digest")
    alias = " AS " + match[2] if match[2] else ""
    return source[:start] + "FROM " + image + alias + "\n" + source[end:]


def check_package_versions(output):
    if sorted(output.strip().splitlines()) != sorted(key + "=" + value for key, value in PACKAGE_VERSIONS.items()):
        raise ValueError("prepared image package version mismatch")


LIVE_JOB_DEADLINE_ENV = "EA_LIVE_JOB_DEADLINE"
LIVE_EXECUTION_SECONDS = 2700
LIVE_HANDOFF_SECONDS = 60
PREPARATION_TIMEOUT_SECONDS = 900
_PREPARATION_DEADLINE: ContextVar[float | None] = ContextVar("prepared_image_deadline", default=None)
_PREPARATION_PHASE = ContextVar("prepared_image_phase", default="unknown")
PREPARATION_CATEGORIES = frozenset(
    {
        "docker_failed",
        "docker_unavailable",
        "deadline_exceeded",
        "filesystem_error",
        "validation_error",
        "contract_error",
        "unknown_error",
    }
)
PREPARATION_PHASES = frozenset(
    {
        "unknown",
        "budget",
        "contract",
        "binary_manifest",
        "platform",
        "staging",
        "dependencies",
        "task_source",
        "task_layer",
        "offline_probe",
        "publication",
    }
)
PREPARATION_OPERATIONS = frozenset({"none", "build", "inspect", "info", "run", "other"})


class PreparationError(ValueError):
    def __init__(self, category, phase, operation="none"):
        self.category = category if type(category) is str and category in PREPARATION_CATEGORIES else "unknown_error"
        self.phase = phase if type(phase) is str and phase in PREPARATION_PHASES else "unknown"
        self.operation = operation if type(operation) is str and operation in PREPARATION_OPERATIONS else "other"
        super().__init__(f"category={self.category} phase={self.phase} operation={self.operation}")


class PreparationTimeoutError(PreparationError, TimeoutError):
    pass


def preparation_failure(error):
    if type(error) not in (PreparationError, PreparationTimeoutError):
        return dict(category="unknown_error", phase="unknown", operation="none")
    safe = PreparationError(error.category, error.phase, error.operation)
    return dict(category=safe.category, phase=safe.phase, operation=safe.operation)


_PREPARATION_TIMING: ContextVar["PreparationTiming | None"] = ContextVar("preparation_timing", default=None)
PREPARATION_TIMING_MAX_EVENTS = 2048
PREPARATION_TIMING_MAX_BYTES = 1048576


class PreparationTiming:
    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.exists() or any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError("unsafe timing checkpoint")
        self.started = time.monotonic()
        self.operation_started = self.started
        self.events = []
        self.truncated = False
        self.image_ordinal = 0
        self.completed_images = 0
        self.image_started = None

    def emit(self, event, operation="none", category="none"):
        import tempfile

        allowed_events = {
            "started",
            "phase",
            "image_started",
            "image_completed",
            "operation_started",
            "operation_completed",
            "operation_failed",
            "completed",
            "failed",
        }
        if type(event) is not str or event not in allowed_events:
            raise PreparationError("validation_error", "unknown")
        safe = PreparationError(category, _PREPARATION_PHASE.get(), operation)
        now = time.monotonic()
        if event == "image_started":
            if self.image_ordinal >= 128:
                raise PreparationError("validation_error", safe.phase)
            self.image_ordinal += 1
            self.image_started = now
        elif event == "image_completed":
            self.completed_images += 1
        if event == "operation_started":
            self.operation_started = now
        deadline = _PREPARATION_DEADLINE.get()

        def seconds(value):
            return round(float(min(86400, max(0, value))), 3) if math.isfinite(value) else 0.0

        row = dict(
            event=event,
            phase=safe.phase,
            operation=safe.operation,
            category="none" if type(category) is str and category == "none" else safe.category,
            image_ordinal=self.image_ordinal,
            completed_images=self.completed_images,
            operation_elapsed_seconds=seconds(now - self.operation_started) if operation != "none" else 0.0,
            image_elapsed_seconds=seconds(now - self.image_started) if self.image_started is not None else 0.0,
            total_elapsed_seconds=seconds(now - self.started),
            remaining_deadline_seconds=seconds(min(PREPARATION_TIMEOUT_SECONDS, deadline - now))
            if deadline is not None
            else 0.0,
        )
        if any(
            type(value) is not int or not 0 <= value <= 128 for value in (self.image_ordinal, self.completed_images)
        ):
            raise PreparationError("validation_error", safe.phase)
        retained = len(self.events) < PREPARATION_TIMING_MAX_EVENTS
        if retained:
            self.events.append(row)
        else:
            self.truncated = True
        payload = (
            json.dumps(
                dict(
                    schema_version=1,
                    clock="monotonic",
                    measurement="host_operation_boundaries",
                    build_instruction_observed=False,
                    events_truncated=self.truncated,
                    latest=row,
                    events=self.events,
                ),
                sort_keys=True,
            )
            + "\n"
        )
        if len(payload.encode()) > PREPARATION_TIMING_MAX_BYTES:
            raise PreparationError("validation_error", safe.phase)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise PreparationError("filesystem_error", safe.phase) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if retained:
            print("preparation_progress " + json.dumps(row, sort_keys=True), flush=True)


def _timing_event(event, operation="none", category="none"):
    timing = _PREPARATION_TIMING.get()
    if timing is not None:
        timing.emit(event, operation, category)


def live_job_remaining(reserve=0):
    value = os.environ.get(LIVE_JOB_DEADLINE_ENV)
    if value is None:
        return math.inf
    deadline = float(value)
    remaining = deadline - time.monotonic() - reserve
    if not math.isfinite(deadline) or remaining <= 0:
        raise TimeoutError("insufficient live job budget; inference was not started")
    return remaining


def preparation_timeout(limit):
    deadline = _PREPARATION_DEADLINE.get()
    if deadline is None:
        return limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("aggregate image preparation deadline expired")
    return min(limit, remaining)


def _docker(args, *, timeout=30):
    operation = "inspect" if args[:2] == ["image", "inspect"] else args[0] if args else "other"
    operation = operation if operation in PREPARATION_OPERATIONS else "other"
    phase = _PREPARATION_PHASE.get()
    _timing_event("operation_started", operation)
    try:
        timeout = preparation_timeout(timeout)
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "TMPDIR") if key in os.environ}
        if operation == "build":
            # BuildKit resolves local config IDs as registry names, not immutable parents.
            # Require the classic builder; never fall back to mutable parent tags.
            environment["DOCKER_BUILDKIT"] = "0"
        result = subprocess.run(["docker", *args], env=environment, capture_output=True, text=True, timeout=timeout)
    except (TimeoutError, subprocess.TimeoutExpired):
        _timing_event("operation_failed", operation, "deadline_exceeded")
        raise PreparationTimeoutError("deadline_exceeded", phase, operation) from None
    except OSError:
        _timing_event("operation_failed", operation, "docker_unavailable")
        raise PreparationError("docker_unavailable", phase, operation) from None
    except Exception as error:
        category = "validation_error" if isinstance(error, ValueError) else "unknown_error"
        _timing_event("operation_failed", operation, category)
        raise PreparationError(category, phase, operation) from None
    if result.returncode:
        _timing_event("operation_failed", operation, "docker_failed")
        raise PreparationError("docker_failed", phase, operation) from None
    _timing_event("operation_completed", operation)
    return result.stdout.strip()


def docker_platform():
    platform = _docker(["info", "--format", "{{.OSType}}/{{.Architecture}}"])
    platform = {"linux/aarch64": "linux/arm64", "linux/x86_64": "linux/amd64"}.get(platform, platform)
    if platform not in ("linux/arm64", "linux/amd64"):
        raise ValueError("unsupported prepared-image platform")
    return platform


def inspect_image(tag):
    return json.loads(_docker(["image", "inspect", "--format", "{{json .}}", tag]))


def inspect_build_parent(tag, platform):
    image = inspect_image(tag)
    if not re.fullmatch("sha256:[a-f0-9]{64}", image["Id"]) or image["Os"] + "/" + image["Architecture"] != platform:
        raise ValueError("prepared source image identity mismatch")
    if image["Config"].get("OnBuild"):
        raise ValueError("prepared layering does not support inherited ONBUILD")
    return image


def check_prepared_image(identity, record, platform, *, probe=False, binary=None):
    image = inspect_image(record["tag"])
    if (
        image["Id"] != record["image_id"]
        or image["Os"] + "/" + image["Architecture"] != platform
        or image["Config"].get("Labels", {}).get(IMAGE_LABEL) != identity
    ):
        raise ValueError("prepared image identity mismatch")
    if probe:
        command = DEPENDENCY_CHECK + " && node --version >/dev/null && npm --version >/dev/null && " + PACKAGE_QUERY
        mounts = []
        if binary is not None:
            mounts = ["--mount", "type=bind,src=" + binary["binary_path"] + ",dst=/ea-binary,readonly"]
            command += " && /ea-binary --version && sha256sum /ea-binary"
        versions = _docker(
            [
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--cpus=1",
                "--memory=512m",
                "--read-only",
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m",
                "--env=HOME=/tmp/ea-home",
                "--user=root",
                "--entrypoint=/bin/sh",
                *mounts,
                record["image_id"],
                "-ec",
                command,
            ],
            timeout=60,
        )
        if binary is not None:
            lines = versions.splitlines()
            if lines[-2:] != [EXPECTED_VERSION, binary["sha256"] + "  /ea-binary"]:
                raise ValueError("prepared image binary compatibility mismatch")
            versions = "\n".join(lines[:-2])
        check_package_versions(versions)


def prepare_images(skill, output, binary_manifest, *, timing_checkpoint=None):
    phase_token = _PREPARATION_PHASE.set("budget")
    token = _PREPARATION_DEADLINE.set(None)
    timing_token = _PREPARATION_TIMING.set(None)
    started = time.monotonic()
    try:
        if timing_checkpoint is not None:
            _PREPARATION_TIMING.set(PreparationTiming(timing_checkpoint))
        _timing_event("started")
        budget = min(PREPARATION_TIMEOUT_SECONDS, live_job_remaining(LIVE_EXECUTION_SECONDS + LIVE_HANDOFF_SECONDS))
        _PREPARATION_DEADLINE.set(started + budget)
        result = _prepare_images(skill, output, binary_manifest)
        _timing_event("completed")
        return result
    except Exception as error:
        if isinstance(error, PreparationError):
            safe = error
        elif isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
            safe = PreparationTimeoutError("deadline_exceeded", _PREPARATION_PHASE.get())
        else:
            if isinstance(error, OSError):
                category = "filesystem_error"
            elif isinstance(error, ValueError):
                category = "validation_error"
            else:
                category = "contract_error" if _PREPARATION_PHASE.get() == "contract" else "unknown_error"
            safe = PreparationError(category, _PREPARATION_PHASE.get())
        details = preparation_failure(safe)
        _timing_event("failed", details["operation"], details["category"])
        raise safe from None
    finally:
        _PREPARATION_TIMING.reset(timing_token)
        _PREPARATION_DEADLINE.reset(token)
        _PREPARATION_PHASE.reset(phase_token)


def _preparation_phase(phase):
    _PREPARATION_PHASE.set(phase)
    _timing_event("phase")


def _prepare_images(skill, output, binary_manifest):
    _preparation_phase("contract")
    verify_contract()
    _preparation_phase("binary_manifest")
    bound = load_manifest(binary_manifest)
    _preparation_phase("platform")
    platform = docker_platform()
    _preparation_phase("staging")
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    tasks = stage_preparation(skill, output / "tasks")
    data: dict = dict(
        schema_version=1,
        platform=platform,
        binary_sha256=bound["sha256"],
        runtime_sha256=sha256(Path(__file__).resolve()),
        skill_path=str(skill),
        source_identity=preparation_identity(skill, platform, bound["sha256"]),
        tasks={},
        images={},
        packages=PACKAGE_VERSIONS,
    )
    dependency_image = None
    for arm, paths in tasks.items():
        data["tasks"][arm] = []
        for task in paths:
            _preparation_phase("task_source")
            context = task / "environment"
            identity = prepared_task_identity(task, platform, bound["sha256"])
            data["tasks"][arm].append(identity)
            if identity in data["images"]:
                continue
            _timing_event("image_started")
            source_tag = "ea-live-source:" + identity
            tag = "ea-live-prepared:" + identity
            pinned_source = output / (identity + ".Dockerfile")
            original = (context / "Dockerfile").read_text()
            pinned = pinned_dockerfile(original, platform)
            uses_default = pinned != pinned_dockerfile(original, platform, dependency_image="sha256:" + "0" * 64)
            if uses_default and dependency_image is None:
                _preparation_phase("dependencies")
                dependencies = output / "dependencies"
                dependencies.mkdir(mode=0o700)
                recipe = (
                    "FROM "
                    + BASE_IMAGES[platform]
                    + "\nRUN "
                    + json.dumps(["/bin/sh", "-ec", dependency_install_command(default_sources=True)])
                    + "\n"
                )
                (dependencies / "Dockerfile").write_text(recipe)
                dependency_tag = "ea-live-deps:" + hashlib.sha256((platform + recipe).encode()).hexdigest()
                _docker(["build", "--platform", platform, "-t", dependency_tag, str(dependencies)], timeout=600)
                dependency_image = inspect_build_parent(dependency_tag, platform)["Id"]
            _preparation_phase("task_source")
            pinned_source.write_text(pinned_dockerfile(original, platform, dependency_image=dependency_image))
            _docker(
                ["build", "--platform", platform, "-f", str(pinned_source), "-t", source_tag, str(context)], timeout=600
            )
            source = inspect_build_parent(source_tag, platform)
            user = source["Config"].get("User") or "root"
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", user):
                raise ValueError("unsupported prepared-image user")
            layer = output / identity
            _preparation_phase("task_layer")
            layer.mkdir(mode=0o700)
            recipe = f"FROM {source['Id']}\n"
            if not uses_default:
                recipe += "USER root\nRUN " + json.dumps(["/bin/sh", "-ec", dependency_install_command()])
                recipe += "\n"
            recipe += f"LABEL {IMAGE_LABEL}={identity}\n"
            if not uses_default:
                recipe += f"USER {user}\n"
            (layer / "Dockerfile").write_text(recipe)
            _docker(["build", "--platform", platform, "-t", tag, str(layer)], timeout=600)
            image = inspect_image(tag)
            record = dict(tag=tag, image_id=image["Id"], source_image_id=source["Id"])
            _preparation_phase("offline_probe")
            check_prepared_image(identity, record, platform, probe=True, binary=bound)
            data["images"][identity] = record
            _timing_event("image_completed")
    _preparation_phase("publication")
    if data["source_identity"] != preparation_identity(skill, platform, bound["sha256"]):
        raise ValueError("preparation source changed during build")
    preparation_timeout(1)
    path = output / "prepared-images.json"
    with path.open("x") as stream:
        json.dump(data, stream, sort_keys=True)
    path.chmod(0o600)
    return path


def load_preparation(path, binary_manifest):
    path = regular_file(path)
    if path.stat().st_size > 262144:
        raise ValueError("prepared-image manifest too large")
    data = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    if (
        not isinstance(data, dict)
        or set(data)
        != {
            "schema_version",
            "platform",
            "binary_sha256",
            "runtime_sha256",
            "skill_path",
            "source_identity",
            "tasks",
            "images",
            "packages",
        }
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["runtime_sha256"] != sha256(Path(__file__).resolve())
        or data["binary_sha256"] != load_manifest(binary_manifest)["sha256"]
        or data["platform"] != docker_platform()
        or data["packages"] != PACKAGE_VERSIONS
    ):
        raise ValueError("prepared-image manifest binding mismatch")
    if data["source_identity"] != preparation_identity(
        Path(data["skill_path"]), data["platform"], data["binary_sha256"]
    ):
        raise ValueError("prepared-image source mismatch")
    tasks, images = data["tasks"], data["images"]
    if (
        not isinstance(tasks, dict)
        or set(tasks) != {"with", "without"}
        or not isinstance(images, dict)
        or not images
        or len(images) > 128
    ):
        raise ValueError("invalid prepared-image coverage")
    covered = set()
    for entries in tasks.values():
        if not isinstance(entries, list) or not entries or len(entries) > 128:
            raise ValueError("invalid prepared-image task list")
        for identity in entries:
            if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{64}", identity):
                raise ValueError("invalid prepared-image identity")
            covered.add(identity)
    if covered != set(images):
        raise ValueError("incomplete prepared-image coverage")
    for identity, record in data["images"].items():
        if (
            not isinstance(record, dict)
            or set(record) != {"tag", "image_id", "source_image_id"}
            or record["tag"] != "ea-live-prepared:" + identity
            or any(
                not isinstance(record[key], str) or not re.fullmatch("sha256:[a-f0-9]{64}", record[key])
                for key in ("image_id", "source_image_id")
            )
        ):
            raise ValueError("invalid prepared-image record")
        check_prepared_image(identity, record, data["platform"])
    return data


def validate_prepared_run(argv):
    import tempfile

    verify_contract()
    expected = {
        "--agents": "opencode",
        "--env-mode": "docker",
        "--n-attempts": "1",
        "--n-concurrent": "1",
        "--max-agents": "1",
        "--grading-mode": "default",
        "--skill-workspace-mode": "isolated",
        "--progress": "off",
    }
    switches = {"--agent-runtime-preflight", "--harbor-keep-jobs"}
    values = {}
    if len(argv) < 3 or argv[:2] != ["tier3", "evaluate"]:
        raise ValueError("unsupported prepared evaluation command")
    tokens = iter(argv[3:])
    for token in tokens:
        if token in values or token not in {*expected, *switches, "--agent-model", "--results-dir"}:
            raise ValueError("unsupported prepared evaluation option")
        values[token] = True if token in switches else next(tokens, None)
    if any(values.get(key) != value for key, value in expected.items()) or not all(
        values.get(key) for key in (*switches, "--agent-model", "--results-dir")
    ):
        raise ValueError("prepared evaluation options do not match preparation")
    bound = load_preparation(os.environ[PREPARATION_ENV], os.environ[MANIFEST_ENV])
    skill = Path(argv[2]).resolve()
    if str(skill) != bound["skill_path"]:
        raise ValueError("prepared evaluation source does not match")
    with tempfile.TemporaryDirectory(prefix="ea-prepared-check-") as temporary:
        staged = stage_preparation(skill, Path(temporary) / "tasks")
        actual = {
            arm: [prepared_task_identity(task, bound["platform"], bound["binary_sha256"]) for task in paths]
            for arm, paths in staged.items()
        }
        if actual != bound["tasks"]:
            raise ValueError("staged tasks differ from prepared images")
    for identity, record in bound["images"].items():
        check_prepared_image(
            identity, record, bound["platform"], probe=True, binary=load_manifest(os.environ[MANIFEST_ENV])
        )


async def prepared_install_check(agent, environment):
    result = await environment.exec(command=DEPENDENCY_CHECK, user="root")
    if result.return_code != 0:
        raise ValueError("prepared image is missing required system dependencies; refusing repository fallback")
    result = await environment.exec(command=PACKAGE_QUERY, user="root")
    if result.return_code != 0:
        raise ValueError("prepared image package query failed")
    check_package_versions(result.stdout or "")


def prepared_activation(binary_manifest):
    required = os.environ.get(REQUIRE_PREPARATION_ENV, "0")
    if required not in ("0", "1"):
        raise ValueError("invalid required preparation activation")
    path = os.environ.get(PREPARATION_ENV)
    if not path:
        if required == "1":
            raise ValueError("required image preparation is missing")
        return None
    bound = load_preparation(path, binary_manifest)
    Secure = import_module(
        "skillevaluator.tier3.harbor.secure_docker_environment"
    ).SkillEvaluatorSecureDockerEnvironment
    original_start = Secure.start
    original_compose = Secure._run_docker_compose_command

    # Native prebuild runs after credential probes and permits fallbacks. Keep
    # its task staging and SecureDocker lifecycle, but require a local image ID.
    async def compose(self, command, *args, **kwargs):
        if command and command[0] in ("build", "pull"):
            raise ValueError("prepared images forbid runtime builds and pulls")
        if command and command[0] == "up":
            command = [*command, "--pull", "never", "--no-build"]
        return await original_compose(self, command, *args, **kwargs)

    async def start(self, force_build):
        self._ea_prepared_image = None
        if (
            type(self) is not Secure
            or force_build
            or self._uses_compose
            or self.stream_enabled
            or self.extra_docker_compose_paths
            or self._enable_egress_control
            or any(policy.network_mode != "public" for policy in [self._network_policy, *self._phase_network_policies])
            or self.task_env_config.docker_image
        ):
            raise ValueError("unsupported prepared environment activation")
        if load_preparation(path, binary_manifest) != bound:
            raise ValueError("prepared-image manifest changed")
        task = self.environment_dir.parent
        config = prepared_task_config(task)
        has_skill = config.metadata.get("has_skill")
        if (
            self.environment_dir.name != "environment"
            or type(has_skill) is not bool
            or config.task is None
            or self.environment_name != config.task.short_name
            or self.task_env_config.model_dump(mode="json") != config.environment.model_dump(mode="json")
        ):
            raise ValueError("prepared task configuration or arm mismatch")
        identity = prepared_task_identity(task, bound["platform"], bound["binary_sha256"])
        arm = "with" if has_skill else "without"
        if identity not in bound["tasks"][arm] or identity not in bound["images"]:
            raise ValueError("task environment was not prepared")
        record = bound["images"][identity]
        check_prepared_image(identity, record, bound["platform"])
        self.task_env_config = self.task_env_config.model_copy(update={"docker_image": record["image_id"]})
        self._env_vars.prebuilt_image_name = record["image_id"]
        await original_start(self, force_build=False)
        result = await original_compose(self, ["ps", "-q", "main"])
        container = (result.stdout or "").strip()
        if (
            not re.fullmatch("[a-f0-9]{64}", container)
            or _docker(["inspect", "--format", "{{.Image}}", container]) != record["image_id"]
        ):
            raise ValueError("running prepared image identity mismatch")
        self._ea_prepared_image = record["image_id"]

    async def check(agent, environment):
        if load_preparation(path, binary_manifest) != bound or not getattr(environment, "_ea_prepared_image", None):
            raise ValueError("prepared environment was not verified")
        await prepared_install_check(agent, environment)

    Secure.start = start
    Secure._run_docker_compose_command = compose
    return check


def prepared_task_config(task):
    import tomllib

    TaskConfig = import_module("harbor.models.task.config").TaskConfig
    return TaskConfig.model_validate(tomllib.loads(regular_file(task / "task.toml").read_text()), extra="forbid")


def prepared_task_identity(task, platform, binary_sha256):
    config = prepared_task_config(task)
    normalized = config.model_dump(mode="json")
    # Only the pinned gateway's native, same-name host placeholders vary
    # between keyless preparation and credentialed staging. All other fields,
    # including literal values and unknown environment names, remain bound.
    for role, names in {
        "environment": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        "verifier": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "SKILL_EVAL_LLM_PROVIDER", "SKILL_EVAL_LLM_MODEL"),
    }.items():
        for name in names:
            if normalized[role]["env"].get(name) == "${" + name + "}":
                del normalized[role]["env"][name]
    payload = [task.name, normalized, preparation_identity(task / "environment", platform, binary_sha256)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def preparation_identity(context, platform, binary_sha256):
    digest = hashlib.sha256()
    digest.update(json.dumps([platform, binary_sha256, sha256(Path(__file__).resolve())]).encode())
    context = Path(context)
    if not context.is_absolute() or any(p.is_symlink() for p in (context, *context.parents)):
        raise ValueError("unsafe image context")
    for path in sorted(context.rglob("*")):
        metadata = path.lstat()
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise ValueError("unsafe image context entry")
        digest.update(json.dumps([path.relative_to(context).as_posix(), metadata.st_mode]).encode())
        if stat.S_ISREG(metadata.st_mode):
            digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def stage_preparation(skill, output):
    adapter = import_module("skillevaluator.tier3.harbor.adapter")
    generate_harbor_tasks = adapter.generate_harbor_tasks
    custom = Path(skill) / "evals/environment/Dockerfile"
    if custom.exists():
        if adapter._validate_custom_dockerfile(custom) is not None:
            raise ValueError("unsupported original custom Dockerfile")
        pinned_dockerfile(custom.read_text(), "linux/amd64")
    load_evals_config = import_module("skillevaluator.tier3.harbor.runner").load_evals_config

    config, _ = load_evals_config(skill)
    harbor = config.get("harbor", {})
    allowed = {
        "task_source",
        "n_attempts",
        "n_concurrent",
        "max_agents",
        "agent_runtime_preflight",
        "resources",
        "agents",
        "agent_workdir",
        "custom_dockerfile_mode",
    }
    if (
        set(harbor) - allowed
        or harbor.get("task_source", "evals_json") != "evals_json"
        or config.get("skill_workspace", {}).get("mode", "isolated") != "isolated"
        or set(config.get("skill_workspace", {})) - {"mode"}
    ):
        raise ValueError("unsupported prepared-image configuration")
    tasks = {}
    for arm in ("with", "without"):
        tasks[arm] = generate_harbor_tasks(
            skill,
            output / arm,
            with_skill=arm == "with",
            workspace_mode="isolated",
            grading_mode="default",
            base_image="",
            copy_repo=False,
            custom_dockerfile_mode=harbor.get("custom_dockerfile_mode", "rebase"),
            task_resources=harbor.get("resources", {}),
            agent_workdir=harbor.get("agent_workdir"),
            arm_suffix="-" + arm + "-skill",
        )
        if not tasks[arm]:
            raise ValueError("empty prepared-image dataset")
        for task in tasks[arm]:
            environment = task / "environment"
            if not (environment / "Dockerfile").is_file() or any(
                (environment / name).exists() for name in ("docker-compose.yaml", "docker-compose.yml")
            ):
                raise ValueError("prepared images require a Dockerfile-only task environment")
    return tasks


def activation_environment(manifest_path):
    environment = {
        MANIFEST_ENV: str(manifest_path),
        TOKEN_ENV: TOKEN,
        DIGEST_ENV: sha256(Path(__file__).resolve()),
        MANIFEST_DIGEST_ENV: sha256(manifest_path),
        "PYTHONPATH": str(HOOK_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if PREPARATION_ENV in os.environ:
        environment.update(
            {
                PREPARATION_ENV: os.environ[PREPARATION_ENV],
                PREPARATION_DIGEST_ENV: sha256(os.environ[PREPARATION_ENV]),
                REQUIRE_PREPARATION_ENV: "1",
            }
        )
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
    for key in (
        MANIFEST_ENV,
        TOKEN_ENV,
        DIGEST_ENV,
        MANIFEST_DIGEST_ENV,
        "PYTHONPATH",
        PREPARATION_ENV,
        PREPARATION_DIGEST_ENV,
    ):
        if key in current and current[key] != expected.get(key):
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
        "skillevaluator.tier3.harbor.secure_docker_environment": [
            "SkillEvaluatorDockerEnvironment.start",
            "SkillEvaluatorDockerEnvironment._run_docker_compose_command",
        ],
        "harbor.environments.docker.docker": ["DockerEnvironment.start", "DockerEnvironment.__init__"],
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
    secure = modules["skillevaluator.tier3.harbor.secure_docker_environment"]
    if (
        opencode.setup is not base.setup
        or gateway.setup is not base.setup
        or opencode._exec is not base._exec
        or gateway._exec is not base._exec
        or gateway.run is not opencode.run
        or gateway.install is not opencode.install
        or preflight._harbor_launch_environment is not runner._harbor_launch_environment
        or secure.SkillEvaluatorSecureDockerEnvironment.start is not secure.SkillEvaluatorDockerEnvironment.start
        or secure.SkillEvaluatorSecureDockerEnvironment._run_docker_compose_command
        is not secure.SkillEvaluatorDockerEnvironment._run_docker_compose_command
    ):
        raise RuntimeError("patched OpenCode inherited function contract changed")


def activate(manifest_path):
    import shlex

    verify_contract()
    manifest_path = regular_file(manifest_path)
    bound = load_manifest(manifest_path)
    activation = activation_environment(manifest_path)
    prepared_check = prepared_activation(manifest_path)
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
        if prepared_check is None:
            await self.ensure_system_dependencies(environment, SYSTEM_DEPENDENCIES)
        else:
            await prepared_check(self, environment)
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
