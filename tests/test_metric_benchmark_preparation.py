# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import functools
import json
import os
import runpy
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from metric_benchmark import preparation
from metric_benchmark.config import TaskManifest, load_benchmark, read_yaml
from metric_benchmark.io import digest_bytes, file_manifest, input_digest, read_json
from metric_benchmark.pipeline import run_experiment
from metric_benchmark.preparation import GitSource, download_harbor_tasks, download_registry
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
    receipt = download_harbor_tasks(source, output)
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
    assert download_harbor_tasks(source, output, cached_only=True) == receipt
    assert download_harbor_tasks(source, output) == receipt
    assert file_manifest(output) == before
    (output / manifest.tasks[0].path / "instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="prepared contents changed"):
        download_harbor_tasks(source, output, cached_only=True)


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
        [
            sys.executable,
            str(LAB / "download_harbor_tasks.py"),
            "tasks",
            "--source",
            str(source),
            "--output",
            str(output),
        ],
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
    receipt = download_harbor_tasks(source, output, benchmark=True)
    assert file_manifest(source.parent) == before
    check_benchmark(output / source.name)
    assert download_harbor_tasks(source, output, benchmark=True, cached_only=True) == receipt
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


@pytest.mark.parametrize(
    "change,message",
    [
        ("missing-task", "Harbor task download failed.*does-not-exist"),
        ("duplicate-id", "duplicate task IDs"),
        ("unknown-version", "dataset not found"),
        ("local-registry-source", "must have Git sources"),
    ],
)
def test_failed_dataset_preparation_does_not_publish(tmp_path, registry, change, message):
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
    with pytest.raises(ValueError, match=message):
        download_harbor_tasks(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".prepare-*"))


@pytest.mark.parametrize(
    "change,message",
    [
        ("wrong-content-pin", "input digest differs"),
        ("floating-commit", "String should match pattern"),
        ("traversal", "without traversal"),
        ("extra-field", "Extra inputs are not permitted"),
    ],
)
def test_invalid_direct_sources_rejected(tmp_path, registry, change, message):
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
    with pytest.raises(ValueError, match=message):
        download_harbor_tasks(source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_existing_output_and_changed_authored_inputs(tmp_path, registry):
    _, _, url, _, _ = registry
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="cached-only"):
        download_harbor_tasks(source, output, cached_only=True)
    download_harbor_tasks(source, output)
    source.write_text(source.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="prepared request changed"):
        download_harbor_tasks(source, output)
    with pytest.raises(ValueError, match="outside the source bundle"):
        download_harbor_tasks(source, source.parent / "prepared")


def test_stale_authored_pin_is_not_resigned(tmp_path, registry):
    source, _ = experiment(tmp_path)
    (source.parent / "analysis.yml").write_text("changed: true\n")
    with pytest.raises(ValueError, match="input digest differs"):
        download_harbor_tasks(source, tmp_path / "prepared", benchmark=True)
    assert not (tmp_path / "prepared").exists()


def test_symlinks_are_not_copied_into_prepared_benchmark(tmp_path):
    source, _ = experiment(tmp_path)
    (source.parent / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="ordinary files"):
        download_harbor_tasks(source, tmp_path / "prepared", benchmark=True)


def test_direct_source_requires_plain_url():
    with pytest.raises(ValueError, match="without embedded credentials"):
        GitSource(git_url="https://secret@example.com/repo", git_commit_id="a" * 40, path="task")


@pytest.mark.parametrize("alias", ["tasks/workspace/tasks.yml", "./tasks/workspace/tasks.yml"])
def test_shared_task_manifest_prepared_once(tmp_path, registry, alias):
    _, _, url, _, _ = registry
    source, _ = experiment(tmp_path, two_cases=True)
    write_yaml(source.parent / "tasks/workspace/tasks.yml", dataset(url))
    cases_path = source.parent / "cases.yml"
    cases = read_yaml(cases_path)
    assert isinstance(cases, list)
    cases[1]["tasks"] = {**cases[1]["tasks"], "path": alias}
    write_yaml(cases_path, cases)
    remove_pins(source.parent)
    output = tmp_path / "prepared"
    receipt = download_harbor_tasks(source, output, benchmark=True)
    check_benchmark(output / source.name)
    assert list(receipt["origins"]) == ["tasks/workspace/tasks.yml"]
    assert len(list((output / "prepared-tasks").iterdir())) == 1


def test_registry_rejects_ambiguous_branch_and_annotated_tag(tmp_path, registry):
    repo, commit, url, served, rows = registry
    git(repo, "tag", "-a", "release", "-m", "release", commit)
    git(repo, "commit", "--allow-empty", "-qm", "another revision")
    git(repo, "branch", "release")
    for task in rows[0]["tasks"]:
        task["git_commit_id"] = "release"
    (served / "registry.json").write_text(json.dumps(rows))
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))
    with pytest.raises(ValueError, match="exactly one"):
        download_harbor_tasks(source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("ref", ["release", "refs/tags/release"])
def test_registry_resolves_annotated_tag(tmp_path, registry, ref):
    repo, commit, url, served, rows = registry
    git(repo, "tag", "-a", "release", "-m", "release", commit)
    for task in rows[0]["tasks"]:
        task["git_commit_id"] = ref
    (served / "registry.json").write_text(json.dumps(rows))
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))
    receipt = download_harbor_tasks(source, tmp_path / "prepared")
    assert {item["source"]["git_commit_id"] for item in receipt["origins"]["tasks.yml"]["sources"]} == {commit}


@pytest.mark.parametrize("benchmark", [False, True])
def test_changed_copy_is_not_signed_as_the_authored_input(tmp_path, monkeypatch, benchmark):
    source, _ = experiment(tmp_path)
    remove_pins(source.parent)
    if not benchmark:
        source = write_yaml(
            source.parent / "local.yml",
            {"tasks": [{"task_id": "local", "revision": 1, "path": "tasks/workspace/inspect-files"}]},
        )
    before = file_manifest(source.parent)
    copytree = shutil.copytree

    def changed_copy(src, dst, *args, **kwargs):
        result = copytree(src, dst, *args, **kwargs)
        for instruction in Path(result).rglob("instruction.md"):
            instruction.write_text("Changed while copying")
        return result

    monkeypatch.setattr("metric_benchmark.preparation.shutil.copytree", changed_copy)
    with pytest.raises(ValueError, match="copied.*differ"):
        download_harbor_tasks(source, tmp_path / "prepared", benchmark=benchmark)
    assert file_manifest(source.parent) == before
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("size", [7, 8, 9, 32])
def test_registry_response_size_boundary(tmp_path, monkeypatch, size):
    limit = 8
    consumed = []

    class Response(BytesIO):
        def read(self, size=-1):
            data = super().read(size)
            consumed.append(len(data))
            return data

    monkeypatch.setattr("metric_benchmark.preparation.MAX_REGISTRY_BYTES", limit)
    monkeypatch.setattr("metric_benchmark.preparation.urlopen", lambda *a, **kw: Response(b"[]" + b" " * (size - 2)))
    output = tmp_path / "registry.json"
    if size <= limit:
        assert download_registry("https://example.org/registry.json", output) == []
        assert read_json(output) == []
    else:
        output.write_bytes(b"previous snapshot")
        with pytest.raises(ValueError, match="registry response exceeds"):
            download_registry("https://example.org/registry.json", output)
        assert output.read_bytes() == b"previous snapshot"
    assert sum(consumed) == min(size, limit + 1)
    assert list(tmp_path.iterdir()) == [output]


def test_oversized_registry_does_not_download_tasks_or_publish(tmp_path, registry, monkeypatch):
    _, _, url, _, _ = registry
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))
    monkeypatch.setattr("metric_benchmark.preparation.MAX_REGISTRY_BYTES", 8)
    monkeypatch.setattr("metric_benchmark.preparation.TaskClient.download_tasks", forbidden)
    with pytest.raises(ValueError, match="registry response exceeds"):
        download_harbor_tasks(source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    assert not list(tmp_path.glob(".prepare-*"))


def nested_experiment(tmp_path):
    source, _ = experiment(tmp_path)
    cases_root = source.parent / "cases"
    cases_root.mkdir()
    for child in source.parent.iterdir():
        if child not in (source, cases_root):
            child.rename(cases_root / child.name)
    definition = read_yaml(source)
    assert isinstance(definition, dict)
    for name in ("cases", "execution", "analysis"):
        definition[name]["path"] = "cases/" + definition[name]["path"]
    return write_yaml(source, definition)


@pytest.mark.parametrize("reserved", ["cases/prepared-tasks", "cases/prepared-tasks/set-0000", "preparation.json"])
def test_nested_cases_reject_reserved_paths(tmp_path, monkeypatch, reserved):
    source = nested_experiment(tmp_path)
    path = source.parent / reserved
    if path.suffix == ".json":
        path.write_text("{}")
    else:
        path.mkdir(parents=True)
    before = file_manifest(source.parent)
    monkeypatch.setattr("metric_benchmark.preparation.urlopen", forbidden)
    monkeypatch.setattr("metric_benchmark.preparation.TaskClient.download_tasks", forbidden)
    with pytest.raises(ValueError, match="reserved preparation path"):
        download_harbor_tasks(source, tmp_path / "prepared", benchmark=True)
    assert file_manifest(source.parent) == before
    assert not (tmp_path / "prepared").exists()
    assert not list(tmp_path.glob(".prepare-*"))


def test_nested_cases_reserve_only_generated_locations(tmp_path):
    source = nested_experiment(tmp_path)
    (source.parent / "prepared-tasks").mkdir()
    (source.parent / "prepared-tasks/notes.txt").write_text("Unrelated authored content")
    (source.parent / "cases/preparation.json").write_text("{}")
    before = file_manifest(source.parent)
    output = tmp_path / "prepared"
    receipt = download_harbor_tasks(source, output, benchmark=True)
    check_benchmark(output / source.name)
    assert (output / "prepared-tasks/notes.txt").read_text() == "Unrelated authored content"
    assert (output / "cases/preparation.json").read_text() == "{}"
    assert (output / "cases/prepared-tasks/set-0000").is_dir()
    assert download_harbor_tasks(source, output, benchmark=True, cached_only=True) == receipt
    assert file_manifest(source.parent) == before


@pytest.mark.parametrize("benchmark", [False, True])
def test_missing_task_environment_is_not_published(tmp_path, benchmark):
    source, _ = experiment(tmp_path)
    remove_pins(source.parent)
    shutil.rmtree(source.parent / "tasks/workspace/inspect-files/environment")
    if not benchmark:
        source = write_yaml(
            source.parent / "local.yml",
            {"tasks": [{"task_id": "local", "revision": 1, "path": "tasks/workspace/inspect-files"}]},
        )
    before = file_manifest(source.parent)
    with pytest.raises(ValueError, match="invalid Harbor task directory"):
        download_harbor_tasks(source, tmp_path / "prepared", benchmark=benchmark)
    assert file_manifest(source.parent) == before
    assert not (tmp_path / "prepared").exists()
    assert not list(tmp_path.glob(".prepare-*"))


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_unrelated_output_has_actionable_error(tmp_path, monkeypatch, kind):
    source = write_yaml(tmp_path / "source/tasks.yml", dataset("https://example.org/registry.json"))
    output = tmp_path / "prepared"
    if kind == "directory":
        output.mkdir()
    else:
        output.write_text("unrelated")
    monkeypatch.setattr(preparation, "urlopen", forbidden)
    with pytest.raises(ValueError, match="output exists and is not a prepared bundle; choose a new directory"):
        download_harbor_tasks(source, output)
    if kind == "directory":
        assert list(output.iterdir()) == []
    else:
        assert output.read_text() == "unrelated"


@pytest.mark.parametrize(
    "field,value,message",
    [("schema_version", 2, "schema_version"), ("origins", {}, "tasks_sha256"), ("files", "not-a-digest", "sha256")],
)
def test_cached_receipt_schema_is_validated(tmp_path, monkeypatch, field, value, message):
    source, _ = experiment(tmp_path)
    output = tmp_path / "prepared"
    receipt = download_harbor_tasks(source, output, benchmark=True)
    if field == "origins":
        receipt[field][next(iter(receipt[field]))] = value
    elif field == "files":
        receipt[field][next(iter(receipt[field]))] = value
    else:
        receipt[field] = value
    (output / "preparation.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(preparation, "urlopen", forbidden)
    with pytest.raises(ValueError, match=message):
        download_harbor_tasks(source, output, benchmark=True, cached_only=True)


@pytest.mark.parametrize("operation", ["resolve", "download"])
@pytest.mark.parametrize("stderr", ["remote: Repository not found.\n", b"remote: Repository not found.\n"])
def test_git_failures_preserve_stderr(tmp_path, registry, monkeypatch, operation, stderr):
    _, _, url, _, _ = registry
    source = write_yaml(tmp_path / "source/tasks.yml", dataset(url))

    def fail_git(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "ls-remote"], stderr=stderr)

    async def fail_download(*args, **kwargs):
        fail_git()

    if operation == "resolve":
        monkeypatch.setattr(preparation.subprocess, "run", fail_git)
        prefix = "Git source resolution failed"
    else:
        monkeypatch.setattr(preparation.TaskClient, "download_tasks", fail_download)
        prefix = "Harbor task download failed"
    with pytest.raises(ValueError, match=f"{prefix}.*Repository not found"):
        download_harbor_tasks(source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
    assert not list(tmp_path.glob(".prepare-*"))


def test_git_resolution_timeout_is_a_value_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "ls-remote"], 60, stderr=b"connection stalled")

    monkeypatch.setattr(preparation.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="Git source resolution failed.*connection stalled"):
        preparation._resolve_git("https://example.org/repo.git", "HEAD")


@pytest.mark.parametrize("configured", [None, "1"])
def test_cli_sets_git_prompt_default(tmp_path, monkeypatch, configured):
    if configured is None:
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
        monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    else:
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", configured)
    observed = []

    def download(*args, **kwargs):
        observed.append(os.environ["GIT_TERMINAL_PROMPT"])

    monkeypatch.setattr(preparation, "download_harbor_tasks", download)
    monkeypatch.setattr(
        sys, "argv", ["download_harbor_tasks.py", "tasks", "--source", "tasks.yml", "--output", str(tmp_path)]
    )
    runpy.run_path(str(LAB / "download_harbor_tasks.py"), run_name="__main__")
    assert observed == [configured or "0"]


def test_registry_url_error_does_not_offer_file_urls(tmp_path):
    with pytest.raises(ValueError, match="HTTP\\(S\\)") as error:
        download_registry((tmp_path / "registry.json").as_uri(), tmp_path / "snapshot.json")
    assert "file://" not in str(error.value)
    with pytest.raises(ValueError, match="file:// for Git fixtures"):
        GitSource(git_url="ssh://example.org/repo", git_commit_id="a" * 40, path="task")


def test_benchmark_shares_registry_and_ref_resolution_per_invocation(tmp_path, registry, monkeypatch):
    repo, commit, url, served, rows = registry
    rows[0]["extra_registry_metadata"] = "must contribute to the raw hash"
    rows.append({**rows[0], "version": "2.0", "tasks": [{**task, "git_commit_id": None} for task in rows[0]["tasks"]]})
    raw_registry = json.dumps(rows, indent=4).encode() + b"\n"
    (served / "registry.json").write_bytes(raw_registry)
    source, _ = experiment(tmp_path, two_cases=True)
    cases_path = source.parent / "cases.yml"
    cases = read_yaml(cases_path)
    assert isinstance(cases, list)
    cases[1]["tasks"] = {**cases[1]["tasks"], "path": "second-dataset.yml"}
    write_yaml(cases_path, cases)
    for index, case in enumerate(cases, start=1):
        selector = dataset(url)
        selector["dataset"]["version"] = f"{index}.0"
        write_yaml(source.parent / case["tasks"]["path"], selector)
    remove_pins(source.parent)
    fetches = []
    resolutions = []
    urlopen = preparation.urlopen
    resolve_git = preparation._resolve_git
    download_tasks = preparation.TaskClient.download_tasks

    def fetch(*args, **kwargs):
        fetches.append(args[0])
        return urlopen(*args, **kwargs)

    def resolve(*args):
        resolutions.append(args)
        return resolve_git(*args)

    moved_commit = []

    async def download(self, *args, **kwargs):
        result = await download_tasks(self, *args, **kwargs)
        if not moved_commit:
            git(repo, "commit", "--allow-empty", "-qm", "upstream moved during download")
            moved_commit.append(git(repo, "rev-parse", "HEAD"))
            rows[0]["description"] = "registry changed during download"
            (served / "registry.json").write_text(json.dumps(rows))
        return result

    monkeypatch.setattr(preparation, "urlopen", fetch)
    monkeypatch.setattr(preparation, "_resolve_git", resolve)
    monkeypatch.setattr(preparation.TaskClient, "download_tasks", download)
    for index, expected_commit in enumerate([commit, None]):
        output = tmp_path / f"prepared-{index}"
        expected_raw = raw_registry if index == 0 else (served / "registry.json").read_bytes()
        receipt = download_harbor_tasks(source, output, benchmark=True)
        assert len(receipt["origins"]) == 2
        assert fetches == [url] * (index + 1)
        assert resolutions == [(repo.as_uri(), "HEAD")] * (index + 1)
        for origin in receipt["origins"].values():
            assert origin["registry_sha256"] == digest_bytes(expected_raw)
            assert "extra_registry_metadata" not in origin["registry_entry"]
            assert {item["source"]["git_commit_id"] for item in origin["sources"]} == {
                expected_commit or moved_commit[0]
            }
        snapshots = list(output.rglob("registry.json"))
        assert len(snapshots) == 1
        assert input_digest(snapshots[0]) != digest_bytes(expected_raw)
        assert receipt["files"][snapshots[0].relative_to(output).as_posix()] == input_digest(snapshots[0])
        check_benchmark(output / source.name)
