# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return importlib.import_module("patched_opencode_runtime")


@pytest.fixture
def controlled(runtime, tmp_path, monkeypatch, request):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "input").write_text("synthetic source")
    stage = tmp_path / "stage"
    paths = []
    count = getattr(request, "param", 4)
    for n in range(count):
        task = stage / str(n)
        (task / "environment").mkdir(parents=True)
        (task / "task.toml").write_text("")
        (task / "environment/Dockerfile").write_text("FROM python:3.12-slim\n")
        paths.append(task)
    tasks = {"with": paths[:2], "without": [paths[0], *paths[2:]]}
    monkeypatch.setattr(runtime, "verify_contract", lambda: None)
    monkeypatch.setattr(runtime, "stage_preparation", lambda *a: tasks)
    binary = {"sha256": "a" * 64, "binary_path": str(tmp_path / "binary")}
    monkeypatch.setattr(runtime, "load_manifest", lambda *a: binary)
    ids = [runtime.prepared_task_identity(p, "linux/amd64", binary["sha256"]) for p in paths]
    state = tmp_path / "state"
    state.mkdir()
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, pathlib, sys, time\n"
        f"state = pathlib.Path({str(state)!r})\n"
        f"ids = {ids!r}\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'info': print('linux/amd64')\n"
        "elif args[0] == 'build':\n"
        "    tag = args[args.index('-t') + 1]\n"
        "    identity = tag.split(':')[1]\n"
        "    source = tag.startswith('ea-live-source:')\n"
        "    name = ('source-' if source else 'built-') + identity\n"
        "    (state / name).touch(exist_ok=False)\n"
        f"    if source and identity in ids[:{8 if count == 10 else 2}]:\n"
        "        until = time.monotonic() + 5\n"
        "        while not (state / ('release-' + identity)).exists():\n"
        "            if time.monotonic() >= until: sys.exit(9)\n"
        "            time.sleep(.01)\n"
        "    if source and (state / ('fail-' + identity)).exists(): sys.exit(7)\n"
        "    image = dict(Id='sha256:' + hashlib.sha256(tag.encode()).hexdigest(), Os='linux', Architecture='amd64', Config=dict(User='root', Labels={'org.eval-author.preparation': identity}))\n"
        "    (state / ('image-' + identity + ('-source' if source else ''))).write_text(json.dumps(image))\n"
        "elif args[:2] == ['image', 'inspect']:\n"
        "    tag = args[-1]; identity = tag.split(':')[1]\n"
        "    print((state / ('image-' + identity + ('-source' if 'source' in tag else ''))).read_text())\n"
        "elif args[0] == 'run':\n"
        "    image_id = args[args.index('-ec') - 1]\n"
        "    for identity in ids:\n"
        "        image = state / ('image-' + identity)\n"
        "        if image.exists() and json.loads(image.read_text())['Id'] == image_id:\n"
        "            (state / ('probe-' + identity)).touch(exist_ok=False)\n"
        f"    print({chr(10).join(k + '=' + v for k, v in runtime.PACKAGE_VERSIONS.items())!r})\n"
        f"    print({runtime.EXPECTED_VERSION!r})\n"
        f"    print({binary['sha256'] + '  /ea-binary'!r})\n"
    )
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv(runtime.LIVE_JOB_DEADLINE_ENV, raising=False)
    return skill, tmp_path / "out", state, ids, tasks


def wait_path(path):
    until = time.monotonic() + 6
    while not path.exists():
        if time.monotonic() >= until:
            raise AssertionError("controlled subprocess barrier not reached")
        time.sleep(0.01)


@pytest.mark.parametrize("controlled", [10], indirect=True)
@pytest.mark.parametrize("workers", [None, 1, 2, 4, 8])
def test_real_subprocess_admission_matches_default_or_explicit_bound(runtime, controlled, tmp_path, workers):
    skill, output, state, ids, _ = controlled
    bound = 2 if workers is None else workers
    errors = []

    def release():
        try:
            for identity in ids[:bound]:
                wait_path(state / ("source-" + identity))
            assert len(list(state.glob("source-*"))) == bound
        except Exception as error:
            errors.append(error)
        finally:
            for identity in ids[:8]:
                (state / ("release-" + identity)).touch(exist_ok=True)

    controller = threading.Thread(target=release)
    controller.start()
    try:
        path = runtime.prepare_images(
            skill, output, tmp_path / "binary.json", **({} if workers is None else {"workers": workers})
        )
    finally:
        controller.join(8)
    assert not controller.is_alive() and not errors
    assert len(json.loads(path.read_text())["images"]) == 10
    assert runtime._PREPARATION_WORKERS.get() == 2


def test_two_real_subprocess_chains_overlap_dedup_and_publish_in_arm_order(runtime, controlled, tmp_path):
    skill, output, state, ids, tasks = controlled
    errors = []

    def release():
        try:
            for identity in ids[:2]:
                wait_path(state / ("source-" + identity))
            (state / ("release-" + ids[1])).touch()
            wait_path(state / ("probe-" + ids[1]))
        except Exception as error:
            errors.append(error)
        finally:
            for identity in ids[:2]:
                (state / ("release-" + identity)).touch(exist_ok=True)

    controller = threading.Thread(target=release)
    controller.start()
    checkpoint = tmp_path / "timing.json"
    try:
        manifest = runtime.prepare_images(
            skill, output, tmp_path / "binary.json", workers=2, timing_checkpoint=checkpoint
        )
    finally:
        controller.join(8)
    assert not controller.is_alive() and not errors
    data = json.loads(manifest.read_text())
    assert data["tasks"] == {"with": ids[:2], "without": [ids[0], *ids[2:]]}
    assert list(data["images"]) == sorted(ids)
    assert len(list(state.glob("source-*"))) == len(list(state.glob("probe-*"))) == 4
    assert len(list(state.glob("built-*"))) == 5  # Shared dependencies exactly once.
    for identity in ids:
        recipe = (output / (identity + ".Dockerfile")).read_text()
        assert recipe.startswith("FROM sha256:")
    timing = runtime.validate_preparation_timing(json.loads(checkpoint.read_text()))
    completed = [r for r in timing["events"] if r["event"] == "image_completed"]
    assert completed[0]["image_ordinal"] == 2
    assert sorted(r["image_ordinal"] for r in completed) == [1, 2, 3, 4]
    assert [r["completed_images"] for r in completed] == [1, 2, 3, 4]
    assert timing["latest"]["event"] == "completed"
    assert timing["latest"]["completed_images"] == 4
    assert all(r["remaining_deadline_seconds"] > 0 for r in completed)


@pytest.mark.parametrize("failure", ["docker", "deadline"])
def test_failure_stops_admission_settles_active_clients_and_never_publishes(
    runtime, controlled, tmp_path, monkeypatch, failure
):
    skill, output, state, ids, _ = controlled
    errors = []
    if failure == "docker":
        (state / ("fail-" + ids[1])).touch()
    else:
        monkeypatch.setattr(runtime, "PREPARATION_TIMEOUT_SECONDS", 2)

    def release():
        try:
            for identity in ids[:2]:
                wait_path(state / ("source-" + identity))
            if failure == "docker":
                (state / ("release-" + ids[1])).touch()
                wait_path(tmp_path / "private.json")
                # Allow the failing worker to set its stop flag while its sibling remains blocked.
                time.sleep(0.1)
                assert not any((state / ("source-" + identity)).exists() for identity in ids[2:])
        except Exception as error:
            errors.append(error)
        finally:
            if failure == "docker":
                (state / ("release-" + ids[0])).touch()

    controller = threading.Thread(target=release)
    controller.start()
    checkpoint = tmp_path / "timing.json"
    try:
        with pytest.raises(runtime.PreparationError) as caught:
            runtime.prepare_images(
                skill,
                output,
                tmp_path / "binary.json",
                workers=2,
                timing_checkpoint=checkpoint,
                private_diagnostics=tmp_path / "private.json",
            )
    finally:
        controller.join(8)
    assert not controller.is_alive() and not errors
    assert caught.value.category == ("docker_failed" if failure == "docker" else "deadline_exceeded")
    assert caught.value.phase == "task_source" and caught.value.operation == "build"
    assert not (output / "prepared-images.json").exists()
    assert len(list(state.glob("source-*"))) == 2
    assert not list(state.glob("probe-*"))
    assert not any((state / ("built-" + identity)).exists() for identity in ids)
    timing = runtime.validate_preparation_timing(json.loads(checkpoint.read_text()))
    assert timing["latest"]["event"] == "failed"
    assert timing["latest"]["completed_images"] == 0
    assert runtime._PREPARATION_DEADLINE.get() is None
    assert runtime._PREPARATION_STOP.get() is None


@pytest.mark.parametrize("workers", [0, 3, 5, 16, True, "2", 2.0])
def test_worker_bound_rejects_before_any_side_effect(runtime, tmp_path, workers):
    with pytest.raises(runtime.PreparationError, match="validation_error"):
        runtime.prepare_images(tmp_path, tmp_path / "out", tmp_path / "binary", workers=workers)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("failure", ["serialization", "source_drift", "binary_drift"])
def test_final_publication_failure_never_leaves_manifest(runtime, controlled, tmp_path, monkeypatch, failure):
    skill, output, state, ids, tasks = controlled
    for identity in ids[:2]:
        (state / ("release-" + identity)).touch()
    original = runtime._prepare_chains

    def chains(*args):
        records = original(*args)
        if failure == "source_drift":
            (tasks["with"][0] / "task.toml").write_text("[metadata]\nchanged = true\n")
        elif failure == "binary_drift":
            monkeypatch.setattr(runtime, "load_manifest", lambda *a: {"sha256": "b" * 64})
        else:

            def failed_dump(data, stream, **kwargs):
                stream.write("partial")
                raise OSError("private serialization failure")

            monkeypatch.setattr(runtime.json, "dump", failed_dump)
        return records

    monkeypatch.setattr(runtime, "_prepare_chains", chains)
    with pytest.raises(runtime.PreparationError):
        runtime.prepare_images(skill, output, tmp_path / "binary.json", workers=2)
    assert not (output / "prepared-images.json").exists()
    assert not list(output.glob(".manifest-*"))


def test_terminal_timing_failure_removes_new_manifest(runtime, controlled, tmp_path, monkeypatch):
    skill, output, state, ids, _ = controlled
    for identity in ids[:2]:
        (state / ("release-" + identity)).touch()
    original = runtime.PreparationTiming.emit

    def emit(self, event, *args):
        if event == "completed":
            raise runtime.PreparationError("filesystem_error", "publication")
        return original(self, event, *args)

    monkeypatch.setattr(runtime.PreparationTiming, "emit", emit)
    with pytest.raises(runtime.PreparationError):
        runtime.prepare_images(
            skill, output, tmp_path / "binary.json", workers=2, timing_checkpoint=tmp_path / "timing.json"
        )
    assert not (output / "prepared-images.json").exists()


def test_failure_checkpoint_error_preserves_first_execution_failure(runtime, tmp_path, monkeypatch):
    def fail(*args):
        raise runtime.PreparationError("docker_failed", "task_layer", "build")

    original = runtime.PreparationTiming.emit

    def emit(self, event, *args):
        if event == "failed":
            raise runtime.PreparationError("filesystem_error", "publication")
        return original(self, event, *args)

    monkeypatch.setattr(runtime, "_prepare_images", fail)
    monkeypatch.setattr(runtime.PreparationTiming, "emit", emit)
    with pytest.raises(runtime.PreparationError) as caught:
        runtime.prepare_images(
            tmp_path, tmp_path / "out", tmp_path / "binary", timing_checkpoint=tmp_path / "timing.json"
        )
    assert runtime.preparation_failure(caught.value) == dict(
        category="docker_failed", phase="task_layer", operation="build"
    )


@pytest.mark.parametrize("workers", [None, 1, 2, 4, 8])
def test_keyless_cli_default_or_explicit_workers_reaches_production(runtime, tmp_path, monkeypatch, workers):
    cli = importlib.import_module("prepare_live_images")
    calls = []
    active_workers = []

    def prepare(*args, **kwargs):
        calls.append(kwargs)
        return runtime.prepare_images(*args, **kwargs)

    monkeypatch.setattr(cli, "prepare_images", prepare)
    monkeypatch.setattr(
        runtime, "_prepare_images", lambda *a: active_workers.append(runtime._PREPARATION_WORKERS.get())
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_live_images",
            "--skill",
            str(tmp_path),
            "--output",
            str(tmp_path / "out"),
            "--binary-manifest",
            str(tmp_path / "binary"),
            *([] if workers is None else ["--workers", str(workers)]),
        ],
    )
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin:/bin", "INFERENCE_HUB_API_KEY": "never-forward"})
    cli.main()
    bound = 2 if workers is None else workers
    assert calls[0]["workers"] == bound
    assert active_workers == [bound]
    assert "INFERENCE_HUB_API_KEY" not in os.environ


def test_worker_local_operation_clocks_and_atomic_global_counts(runtime, tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    timing = runtime.PreparationTiming(tmp_path / "timing.json")
    first, second = timing.worker(1), timing.worker(2)
    token = runtime._PREPARATION_DEADLINE.set(1000.0)
    try:
        first.emit("image_started")
        first.emit("operation_started", "build")
        clock[0] = 102.0
        second.emit("image_started")
        second.emit("operation_started", "build")
        clock[0] = 105.0
        second.emit("operation_completed", "build")
        second.emit("image_completed")
        clock[0] = 110.0
        first.emit("operation_completed", "build")
        first.emit("image_completed")
    finally:
        runtime._PREPARATION_DEADLINE.reset(token)
    rows = json.loads(timing.path.read_text())["events"]
    operations = [r for r in rows if r["event"] == "operation_completed"]
    assert [(r["image_ordinal"], r["operation_elapsed_seconds"], r["image_elapsed_seconds"]) for r in operations] == [
        (2, 3, 3),
        (1, 10, 10),
    ]
    assert [r["completed_images"] for r in rows if r["event"] == "image_completed"] == [1, 2]
    assert rows[-1]["total_elapsed_seconds"] == 10
    assert rows[-1]["remaining_deadline_seconds"] == 890
