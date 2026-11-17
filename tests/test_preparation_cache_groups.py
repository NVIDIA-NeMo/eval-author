# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import subprocess
import sys
import threading

import pytest
from test_preparation_concurrency import wait_path

pytest_plugins = ["test_preparation_concurrency"]


@pytest.mark.parametrize("controlled", [8], indirect=True)
@pytest.mark.parametrize(
    ("workers", "overlap_image_write"),
    [
        pytest.param(None, False, id="None"),
        pytest.param(1, False, id="1"),
        pytest.param(2, False, id="2"),
        pytest.param(4, False, id="4"),
        pytest.param(8, False, id="8"),
        pytest.param(8, True, id="partial-image-write"),
    ],
)
def test_cache_groups_settle_in_order_overlap_and_publish_all_paired_images(
    runtime, controlled, tmp_path, monkeypatch, workers, overlap_image_write
):
    skill, output, state, _, tasks = controlled
    paths = [*tasks["with"], *tasks["without"][1:]]
    tasks["with"] = paths[:4]
    tasks["without"] = paths[4:]
    for n, task in enumerate(paths):
        (task / "environment/Dockerfile").write_text(
            f"FROM python:3.12-slim\nRUN echo native && \\\n"
            f"    echo dependency\nRUN pip install arm{n // 4}\nCOPY input{n} /app/\n"
        )
    binary = runtime.load_manifest(None)
    ids = [runtime.prepared_task_identity(p, "linux/amd64", binary["sha256"]) for p in paths]
    if overlap_image_write:
        # Pause a real sibling build after opening its image JSON, before it is
        # complete. The other group's probe must not read this unrelated file.
        fake_docker = tmp_path / "docker"
        source = fake_docker.read_text()
        write_image = (
            "    (state / ('image-' + identity + ('-source' if source else ''))).write_text(json.dumps(image))\n"
        )
        assert source.count(write_image) == 1
        fake_docker.write_text(
            source.replace(
                write_image,
                f"    if not source and identity == {ids[1]!r}:\n"
                "        with (state / ('image-' + identity)).open('w') as stream:\n"
                "            stream.write('{'); stream.flush()\n"
                "            (state / 'image-write-paused').touch()\n"
                "            until = time.monotonic() + 5\n"
                "            while not (state / 'allow-image-write').exists():\n"
                "                if time.monotonic() >= until: sys.exit(23)\n"
                "                time.sleep(.01)\n"
                "            stream.write(json.dumps(image)[1:])\n"
                "        (state / 'image-write-completed').touch()\n"
                "    else:\n" + "    " + write_image,
            )
        )
    script = tmp_path / "cache-guard.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "state, group, identity = map(str, sys.argv[1:])\n"
        "state = pathlib.Path(state)\n"
        "guard = state / ('guard-' + group)\n"
        "try: guard.mkdir()\n"
        "except FileExistsError: sys.exit(17)\n"
        "try:\n"
        "    (state / ('entered-' + identity)).touch()\n"
        "    until = time.monotonic() + 5\n"
        "    while not (state / 'release').exists():\n"
        "        if time.monotonic() >= until: sys.exit(19)\n"
        "        time.sleep(.01)\n"
        "finally: guard.rmdir()\n"
    )
    # Match the fake Docker's tag-derived IDs without opening sibling build
    # records: an existing image file may still be in the middle of a write.
    identity_by_image_id = {
        "sha256:" + hashlib.sha256(("ea-live-prepared:" + identity).encode()).hexdigest(): identity for identity in ids
    }
    original = runtime._docker

    def docker(args, **kwargs):
        if args[0] == "build" and "-f" in args:
            identity = args[args.index("-t") + 1].split(":")[1]
            group = ids.index(identity) // 4
            subprocess.run([sys.executable, str(script), str(state), str(group), identity], check=True, timeout=7)
            (state / ("release-" + identity)).touch(exist_ok=True)
        if overlap_image_write and args[0] == "build" and "ea-live-prepared:" + ids[1] in args:
            wait_path(state / ("probe-" + ids[4]))
        result = original(args, **kwargs)
        if args[0] == "run":
            identity = identity_by_image_id[args[args.index("-ec") - 1]]
            blocked_probe = overlap_image_write and identity == ids[5]
            try:
                if blocked_probe:
                    wait_path(state / "image-write-paused")
                    assert (state / ("image-" + ids[1])).read_text() == "{"
                (state / ("probe-" + identity)).touch(exist_ok=False)
            finally:
                if blocked_probe:
                    (state / "allow-image-write").touch()
        return result

    monkeypatch.setattr(runtime, "_docker", docker)
    errors = []

    def release():
        try:
            wait_path(state / ("entered-" + ids[0]))
            if workers != 1:
                wait_path(state / ("entered-" + ids[4]))
            assert len(list(state.glob("entered-*"))) == (1 if workers == 1 else 2)
        except BaseException as error:
            errors.append(error)
        finally:
            (state / "release").touch()

    controller = threading.Thread(target=release)
    controller.start()
    checkpoint = tmp_path / "timing.json"
    try:
        manifest = runtime.prepare_images(
            skill,
            output,
            tmp_path / "binary.json",
            timing_checkpoint=checkpoint,
            **({} if workers is None else {"workers": workers}),
        )
    finally:
        controller.join(8)
    assert not errors and not controller.is_alive()
    if overlap_image_write:
        assert (state / "image-write-completed").exists()
        assert json.loads((state / ("image-" + ids[1])).read_text())["Id"] in identity_by_image_id
    data = json.loads(manifest.read_text())
    assert data["tasks"] == {"with": ids[:4], "without": ids[4:]}
    assert set(data["images"]) == set(ids)
    assert len(list(state.glob("probe-*"))) == 8
    assert len(list(state.glob("built-*"))) == 9
    rows = runtime.validate_preparation_timing(json.loads(checkpoint.read_text()))["events"]
    completed = [r["image_ordinal"] for r in rows if r["event"] == "image_completed"]
    assert [n for n in completed if n <= 4] == [1, 2, 3, 4]
    assert [n for n in completed if n > 4] == [5, 6, 7, 8]
    assert [r["completed_images"] for r in rows if r["event"] == "image_completed"] == list(range(1, 9))
    prefixes = [(output / (identity + ".Dockerfile")).read_text().split("COPY", 1)[0] for identity in ids]
    assert len(set(prefixes[:4])) == len(set(prefixes[4:])) == 1
    assert prefixes[0] != prefixes[4] and prefixes[0].startswith("FROM sha256:")
    assert runtime._PREPARATION_WORKERS.get() == 2


def test_native_multiline_prefix_is_exact_not_physical_copy_match(runtime):
    prefix = (
        "FROM python:3.12-slim\n\n"
        "RUN apt-get update && \\\n"
        "    apt-get install -y bash curl git jq ripgrep \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
        "RUN printf '%s' COPY && \\\n"
        "    echo ready\n"
    )
    pinned = runtime.pinned_dockerfile(prefix + "COPY input /workspace/input\n", "linux/amd64")
    assert runtime._preparation_cache_prefix(pinned) == runtime.pinned_dockerfile(prefix, "linux/amd64")


@pytest.mark.parametrize(
    "command",
    [
        "RUN \\\n    --mount=type=bind,target=/input cat /input/a\n",
        "RUN \\\n    \\\n    --mount=type=bind,target=/input cat /input/a\n",
        "RUN \\\n    --network=none \\\n    --mount=type=bind,target=/input cat /input/a\n",
        "RUN \\\n    --mount \\\n    cat /input/a\n",
        "RUN \\\n    \\\n    \n",
    ],
)
def test_logical_run_flags_or_malformed_command_have_no_shared_group(runtime, command):
    assert runtime._preparation_cache_prefix("FROM python:3.12-slim\n" + command + "COPY input /app\n") is None


@pytest.mark.parametrize(
    "command",
    [
        "RUN \\\n    apt-get update && \\\n    apt-get install -y curl\n",
        "RUN \\\n    pip install \\\n    PyYAML jsonschema\n",
    ],
)
def test_logical_native_multiline_run_retains_exact_shared_prefix(runtime, command):
    prefix = "FROM sha256:" + "b" * 64 + "\n" + command
    assert runtime._preparation_cache_prefix(prefix + "COPY input /app\n") == prefix


@pytest.mark.parametrize(
    "body",
    [
        "RUN echo x \\\nCOPY hidden /app\n",
        "RUN echo x \\\n# comment\n    echo y\nCOPY input /app\n",
        "RUN echo x \\\n\n    echo y\nCOPY input /app\n",
        "RUN echo x \\\\\n    echo y\nCOPY input /app\n",
        "RUN --mount=type=bind,target=/input cat /input/a\nCOPY input /app\n",
        "ONBUILD COPY hidden /app\nCOPY input /app\n",
        "ARG thing\nCOPY input /app\n",
        "RUN <<EOF\nCOPY hidden /app\nEOF\nCOPY input /app\n",
        "RUN echo hello\n",
    ],
)
def test_uncertain_or_context_dependent_prefix_has_no_shared_group(runtime, body):
    assert runtime._preparation_cache_prefix("FROM python:3.12-slim\n" + body) is None


@pytest.mark.parametrize("controlled", [6], indirect=True)
@pytest.mark.parametrize("failure", ["docker", "deadline"])
def test_group_failure_stops_remaining_rows_and_settles_sibling(runtime, controlled, tmp_path, monkeypatch, failure):
    skill, output, state, _, tasks = controlled
    paths = [*tasks["with"], *tasks["without"][1:]]
    for n, task in enumerate(paths):
        (task / "environment/Dockerfile").write_text(
            f"FROM python:3.12-slim\nRUN echo group{n // 2}\nCOPY input{n} /app\n"
        )
    ids = [runtime.prepared_task_identity(p, "linux/amd64", "a" * 64) for p in paths]
    original = runtime._docker
    first_entered = threading.Event()
    release_first = threading.Event()
    stopped = []
    deadlines = []

    def docker(args, **kwargs):
        if args[0] == "build" and "-f" in args:
            identity = args[args.index("-t") + 1].split(":")[1]
            deadlines.append(runtime._PREPARATION_DEADLINE.get())
            (state / ("entered-" + identity)).touch()
            if identity == ids[0]:
                first_entered.set()
                assert release_first.wait(6)
                (state / "settled").touch()
            elif identity == ids[2]:
                assert first_entered.wait(6)
                stopped.append(runtime._PREPARATION_STOP.get())
                if failure == "deadline":
                    runtime._PREPARATION_DEADLINE.set(0)
                    runtime.preparation_timeout(1)
                raise runtime.PreparationError("docker_failed", "task_source", "build")
        return original(args, **kwargs)

    monkeypatch.setattr(runtime, "_docker", docker)
    errors = []

    def release():
        try:
            wait_path(state / ("entered-" + ids[2]))
            until = threading.Event()
            for _ in range(600):
                if stopped and stopped[0].is_set():
                    break
                until.wait(0.01)
            assert stopped and stopped[0].is_set()
            assert len(list(state.glob("entered-*"))) == 2
        except BaseException as error:
            errors.append(error)
        finally:
            release_first.set()

    controller = threading.Thread(target=release)
    controller.start()
    try:
        with pytest.raises(runtime.PreparationError) as caught:
            runtime.prepare_images(skill, output, tmp_path / "binary.json", workers=2)
    finally:
        controller.join(8)
    assert not errors and not controller.is_alive()
    assert caught.value.category == ("docker_failed" if failure == "docker" else "deadline_exceeded")
    assert (state / "settled").exists()
    assert len(list(state.glob("entered-*"))) == 2
    assert len(set(deadlines)) == 1 and deadlines[0] is not None
    assert not (output / "prepared-images.json").exists()
    assert runtime._PREPARATION_DEADLINE.get() is None


@pytest.mark.parametrize("boundary", ["COPY", "ADD", "copy", "add"])
def test_prefix_preserves_exact_bytes_including_base_and_requirements(runtime, boundary):
    prefix = "FROM sha256:" + "b" * 64 + '\nRUN ["pip", "install", "PyYAML"]\n'
    assert runtime._preparation_cache_prefix(prefix + boundary + " input /app\n") == prefix
    different = prefix.replace("PyYAML", "jsonschema")
    assert runtime._preparation_cache_prefix(different + boundary + " input /app\n") != prefix


def test_custom_digest_parent_does_not_share_uninspected_cache_group(runtime, tmp_path, monkeypatch):
    plan = {
        str(n): (tmp_path / str(n), "FROM custom@sha256:" + "c" * 64 + "\nCOPY input /app\n", False) for n in range(2)
    }
    barrier = threading.Barrier(2)

    def chain(identity, *args):
        subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=5)
        barrier.wait(timeout=5)
        return identity

    monkeypatch.setattr(runtime, "_prepare_chain", chain)
    assert runtime._prepare_chains(plan, tmp_path, "linux/amd64", {}, "sha256:" + "d" * 64) == {"0": "0", "1": "1"}
