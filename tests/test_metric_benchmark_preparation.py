# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import functools
import json
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from metric_benchmark.config import TaskManifest, load_benchmark, read_yaml
from metric_benchmark.io import file_manifest, input_digest, read_json
from metric_benchmark.pipeline import run_experiment
from metric_benchmark.preparation import GitSource, download_registry, prepare_inputs
from metric_benchmark.signing import check_benchmark
from test_metric_benchmark_config import LAB, TASK_IDS
from test_metric_benchmark_execution import fake_harbor
from test_metric_benchmark_pipeline import experiment
from test_metric_benchmark_signing import remove_pins


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-C", str(root), *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@pytest.fixture
def registry(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q")
    for name in TASK_IDS:
        shutil.copytree(LAB / "examples/smoke/tasks/smoke-tasks/answer", repo / name)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    commit = git(repo, "rev-parse", "HEAD")
    rows = [
        {
            "name": "fixture",
            "version": "1.0",
            "description": "Fixture tasks",
            "tasks": [
                {"name": name, "git_url": repo.as_uri(), "git_commit_id": "HEAD", "path": name} for name in TASK_IDS
            ],
        }
    ]
    served = tmp_path / "registry"
    served.mkdir()
    (served / "registry.json").write_text(json.dumps(rows))
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(served)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield repo, commit, f"http://127.0.0.1:{server.server_port}/registry.json", served, rows
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def dataset(url):
    return {"dataset": {"name": "fixture", "version": "1.0", "registry": {"url": url}}}


def forbidden(*args, **kwargs):
    pytest.fail("prepared replay must not download or execute")


def test_registry_download_dataset_and_verified_offline_replay(tmp_path, registry, monkeypatch):
    _, commit, url, served, _ = registry
    snapshot = tmp_path / "snapshot.json"
    assert len(download_registry(url, snapshot)) == 1
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))
    output = tmp_path / "prepared"
    receipt = prepare_inputs(source, output)
    manifest = TaskManifest.model_validate(read_yaml(output / "tasks.yml"))
    assert [t.task_id for t in manifest.tasks] == TASK_IDS
    for task in manifest.tasks:
        assert task.sha256 == input_digest(output / task.path)
        task.source().resolve(output)
    origin = receipt["origins"]["tasks.yml"]
    assert {row["source"]["git_commit_id"] for row in origin["sources"]} == {commit}
    assert origin["registry_entry"]["tasks"][0]["git_commit_id"] == "HEAD"
    before = file_manifest(output)
    (served / "registry.json").unlink()
    monkeypatch.setattr("metric_benchmark.preparation.urlopen", forbidden)
    monkeypatch.setattr("metric_benchmark.preparation.TaskClient.download_tasks", forbidden)
    assert prepare_inputs(source, output, cached_only=True) == receipt
    assert prepare_inputs(source, output) == receipt
    assert file_manifest(output) == before
    (output / manifest.tasks[0].path / "instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="prepared contents changed"):
        prepare_inputs(source, output, cached_only=True)


def test_direct_git_task_cli(tmp_path, registry):
    repo, commit, _, _, _ = registry
    source = write_yaml(
        tmp_path / "source/tasks.yml",
        {
            "tasks": [
                {
                    "task_id": "named-task",
                    "revision": 4,
                    "source": {"git_url": repo.as_uri(), "git_commit_id": commit, "path": TASK_IDS[0]},
                }
            ]
        },
    )
    output = tmp_path / "prepared"
    subprocess.run(
        [sys.executable, str(LAB / "prepare_inputs.py"), "tasks", "--source", str(source), "--output", str(output)],
        check=True,
    )
    tasks = TaskManifest.model_validate(read_yaml(output / "tasks.yml"))
    assert tasks.tasks[0].task_id == "named-task" and tasks.tasks[0].revision == 4
    assert (output / tasks.tasks[0].path / "instruction.md").is_file()
    receipt = read_json(output / "preparation.json")
    assert isinstance(receipt, dict)
    assert receipt["origins"]["tasks.yml"]["sources"][0]["source"]["git_commit_id"] == commit


def test_complete_benchmark_preparation_execution_and_offline_analysis(tmp_path, registry, monkeypatch):
    _, _, url, _, _ = registry
    source, _ = experiment(tmp_path)
    tasks_file = source.parent / "tasks/workspace/tasks.yml"
    write_yaml(tasks_file, dataset(url))
    for task_id in TASK_IDS:
        shutil.rmtree(tasks_file.parent / task_id)
    remove_pins(source.parent)
    before = file_manifest(source.parent)
    output = tmp_path / "prepared"
    receipt = prepare_inputs(source, output, benchmark=True)
    assert file_manifest(source.parent) == before
    check_benchmark(output / source.name)
    assert prepare_inputs(source, output, benchmark=True, cached_only=True) == receipt
    results = tmp_path / "results"
    completed = run_experiment(output / source.name, "run", results, runner=fake_harbor)
    assert set(completed["cases"]) == {"rho-agent"}
    monkeypatch.setattr("metric_benchmark.preparation.urlopen", forbidden)
    monkeypatch.setattr("metric_benchmark.preparation.TaskClient.download_tasks", forbidden)
    loaded = load_benchmark(output / source.name)
    for case in loaded.analysis.inputs.cases.values():
        for task in case.case.task_sources.values():
            shutil.rmtree(output / task.path)
    assert (
        run_experiment(
            output / source.name, "run", results, results_root=results / "execution", cached_only=True, runner=forbidden
        )
        == completed
    )


@pytest.mark.parametrize("change", ["missing-task", "duplicate-id", "unknown-version", "local-registry-source"])
def test_failed_dataset_preparation_does_not_publish(tmp_path, registry, change):
    _, _, url, served, rows = registry
    data = dataset(url)
    if change == "missing-task":
        rows[0]["tasks"][-1]["path"] = "does-not-exist"
    elif change == "duplicate-id":
        rows[0]["tasks"][1]["name"] = rows[0]["tasks"][0]["name"]
    elif change == "unknown-version":
        data["dataset"]["version"] = "missing"
    else:
        rows[0]["tasks"][0].pop("git_url")
    (served / "registry.json").write_text(json.dumps(rows))
    source = write_yaml(tmp_path / "source/tasks.yml", data)
    output = tmp_path / "prepared"
    with pytest.raises(ValueError):
        prepare_inputs(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".prepare-*"))


@pytest.mark.parametrize("change", ["wrong-content-pin", "floating-commit", "traversal", "extra-field"])
def test_invalid_direct_sources_rejected(tmp_path, registry, change):
    repo, commit, _, _, _ = registry
    task = {
        "task_id": "task",
        "revision": 1,
        "source": {"git_url": repo.as_uri(), "git_commit_id": commit, "path": TASK_IDS[0]},
    }
    if change == "wrong-content-pin":
        task["sha256"] = "sha256:" + "0" * 64
    elif change == "floating-commit":
        task["source"]["git_commit_id"] = "HEAD"
    elif change == "traversal":
        task["source"]["path"] = "../outside"
    else:
        task["source"]["unexpected"] = "ignored?"
    source = write_yaml(tmp_path / "source/tasks.yml", {"tasks": [task]})
    with pytest.raises(ValueError):
        prepare_inputs(source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_existing_output_and_changed_authored_inputs(tmp_path, registry):
    _, _, url, _, _ = registry
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="cached-only"):
        prepare_inputs(source, output, cached_only=True)
    prepare_inputs(source, output)
    source.write_text(source.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="prepared request changed"):
        prepare_inputs(source, output)
    with pytest.raises(ValueError, match="outside the source bundle"):
        prepare_inputs(source, source.parent / "prepared")


def test_stale_authored_pin_is_not_resigned(tmp_path, registry):
    source, _ = experiment(tmp_path)
    (source.parent / "analysis.yml").write_text("changed: true\n")
    with pytest.raises(ValueError, match="input digest differs"):
        prepare_inputs(source, tmp_path / "prepared", benchmark=True)
    assert not (tmp_path / "prepared").exists()


def test_symlinks_are_not_copied_into_prepared_benchmark(tmp_path):
    source, _ = experiment(tmp_path)
    (source.parent / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="ordinary files"):
        prepare_inputs(source, tmp_path / "prepared", benchmark=True)


def test_direct_source_requires_plain_url():
    with pytest.raises(ValueError, match="without embedded credentials"):
        GitSource(git_url="https://secret@example.com/repo", git_commit_id="a" * 40, path="task")
