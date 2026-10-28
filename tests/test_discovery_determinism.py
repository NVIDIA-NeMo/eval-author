# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_report_facts as factual
import discovery_determinism as determinism
from test_skill_contract import _import_discover, _write_task


def capture(tmp_path, report, index, code=1, **identity):
    path = tmp_path / f"raw-{index}.json"
    path.write_text(json.dumps(report))
    provenance = {key: "frozen-fixture-v1" for key in determinism.IDENTITIES} | identity
    return determinism.capture(path, provenance, code)


@pytest.mark.parametrize("case", ["harbor", "gym", "mixed", "empty", "malformed", "ignored", "dropped", "missing-env"])
def test_actual_discovery_repeats(tmp_path, monkeypatch, capsys, case):
    """Run real Explore/Judge with installed Harbor and fixed host readiness; no Solve tasks."""
    import asyncio
    import importlib.metadata

    discover = _import_discover(monkeypatch)
    judge = importlib.import_module("providers.harbor._judge")
    # Runtime startup and backend availability are inputs, not repeatability guarantees.
    harbor_runtime = {
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "harbor_importable": True,
        "harbor_version": importlib.metadata.version("harbor"),
        "harbor_cli": None,
    }
    monkeypatch.setattr(discover._probe, "probe", lambda root: harbor_runtime.copy())
    monkeypatch.setattr(
        discover.gym_probe,
        "probe",
        lambda root, cli: {
            "gym_available": False,
            "gym_version": None,
            "gym_cli": None,
            "gym_reason": "NeMo Gym is unavailable in this fixture.",
        },
    )
    monkeypatch.setattr(
        judge,
        "backend_check",
        lambda *args: judge._check("backend", judge.FAIL, "Backend unavailable in this fixture."),
    )
    monkeypatch.setattr(judge, "_harbor_executable", lambda: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    if case not in {"gym", "empty"}:
        _write_task(repo / "dataset/a")
        (repo / "job.yaml").write_text("datasets:\n  - path: ./dataset\nagents:\n  - name: oracle\n")
    if case in {"gym", "mixed"}:
        manifest = repo / "environments/demo/manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("name: demo\nkind: environment\ndatasets:\n  - jsonl_fpath: data/test.jsonl\n")
    if case == "malformed":
        (repo / "job.yaml").write_text("datasets: [broken\n")
    if case == "dropped":
        _write_task(repo / "dataset/b")
        (repo / "dataset/b/task.toml").write_text("broken = = =")
    if case == "ignored":
        _write_task(repo / ".git/ignored")
        _write_task(repo / "nested/.git/ignored")
    if case == "missing-env":
        task = repo / "dataset/a/task.toml"
        task.write_text(task.read_text() + '\n[environment.env]\nDETERMINISM_ABSENT = "${DETERMINISM_ABSENT}"\n')
        monkeypatch.delenv("DETERMINISM_ABSENT", raising=False)
    records = []
    for index, cwd in enumerate([tmp_path, repo]):
        monkeypatch.chdir(cwd)
        code = asyncio.run(discover._main(["--repo", str(repo)]))
        report = json.loads(capsys.readouterr().out)
        assert report["proven"] is True
        assert report["task_count"] == (0 if case in {"gym", "empty"} else 2 if case == "dropped" else 1)
        if case not in {"gym", "empty"}:
            assert len(report["configs"]) == 1
            checks = {item["name"]: item["status"] for item in report["configs"][0]["checks"]}
            assert checks["schema"] == ("fail" if case == "malformed" else "pass")
            if case != "malformed":
                assert checks["resolution"] == "pass"
                assert checks["coverage"] == ("fail" if case == "dropped" else "pass")
        if case == "missing-env":
            required = report["configs"][0]["required_env_vars"]
            assert required == [{"name": "DETERMINISM_ABSENT", "default": None, "declared_in": "dataset/a/task.toml"}]
        records.append(capture(tmp_path, report, index, code))
    assert records[0]["decision"] == records[1]["decision"]
    assert determinism.compare(records)["passed"]
    if case == "mixed":
        assert records[0]["decision"]["gym_manifests"][0]["judge"]["status"] == "blocked"
        assert records[0]["exit_status"] == 1


def minimal():
    return {
        "schema_version": 1,
        "repo_root": "/fixture",
        "runtime": {"version": "1"},
        "phases": {"solve": "skipped"},
        "configs": [],
        "datasets": [],
        "gym_manifests": [],
        "checks": [{"name": "backend", "status": "fail", "severity": "required", "reason": "missing"}],
        "proven": False,
        "runnable": False,
        "task_count": 0,
        "sample": ["a", "c"],
        "discovered_at": "first",
    }


def test_discovery_excludes_internal_configs_and_legacy_copies(tmp_path, monkeypatch):
    discover = _import_discover(monkeypatch)
    current = tmp_path / ".eval-author/tasks/current"
    _write_task(current)
    for relative in (".working/trace-environments", "task-drafts", "archives"):
        root = tmp_path / ".eval-author" / relative
        _write_task(root / "copy")
        (root / "run.yaml").write_text("tasks:\n  - path: copy\n")
    scan = discover.scan_repository(tmp_path)
    assert scan.configs == []
    assert {item["path"] for item in scan.task_search["candidates"]} == {".eval-author/tasks/current"}
    selected = discover.scan_repository(tmp_path / ".eval-author/task-drafts")
    assert [item.path.name for item in selected.configs] == ["run.yaml"]
    assert {item["path"] for item in selected.task_search["candidates"]} == {"copy"}


def test_harbor_config_resolution_uses_selected_repository_for_scratch(tmp_path, monkeypatch):
    import asyncio
    import importlib

    pytest.importorskip("harbor")
    _import_discover(monkeypatch)
    judge = importlib.import_module("providers.harbor._judge")
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    repo = outer / "selected"
    _write_task(repo / "dataset/a")
    config_path = repo / "job.yaml"
    config_path.write_text("datasets:\n  - path: ./dataset\nagents:\n  - name: oracle\n")
    candidate = judge.ConfigCandidate(
        config_path, {"datasets": [{"path": "./dataset"}], "agents": [{"name": "oracle"}]}, True
    )
    outside = tmp_path / "launcher"
    outside.mkdir()
    monkeypatch.chdir(outside)
    create = judge.Job.create
    scratch_paths = []

    async def capture_scratch(config):
        scratch = Path(config.jobs_dir)
        assert scratch.is_dir()
        scratch_paths.append(scratch)
        return await create(config)

    monkeypatch.setattr(judge.Job, "create", capture_scratch)
    monkeypatch.setattr(judge, "_check_backend", lambda config, outcome: None)
    monkeypatch.setattr(judge, "_harbor_executable", lambda: None)

    outcome = asyncio.run(judge.judge_config(candidate, repo))

    assert next(check for check in outcome.checks if check.name == "resolution").status == "pass"
    assert len(scratch_paths) == 1
    assert scratch_paths[0].parent == repo / ".eval-author/.working/tmp"
    assert not scratch_paths[0].exists()
    assert not list((repo / ".eval-author/.working/tmp").iterdir())
    assert not (outer / ".eval-author").exists()
    assert not list(outside.iterdir())
    assert Path.cwd() == outside


def test_normalization_retains_samples_unknown_fields_and_duplicates(tmp_path):
    report = minimal()
    first = capture(tmp_path, report, 0)
    report["discovered_at"] = "second"
    second = capture(tmp_path, report, 1)
    assert determinism.compare([first, second])["passed"]
    for key, value in [
        ("sample", ["c", "a"]),
        ("runtime", {"version": "2"}),
        ("new_field", "not discarded"),
        ("checks", report["checks"] * 2),
    ]:
        changed = copy.deepcopy(report)
        changed[key] = value
        third = capture(tmp_path, changed, 2)
        assert not determinism.compare([first, third])["passed"]
        third = capture(tmp_path, changed, 2, fixture="changed")
        assert determinism.compare([first, third], mutation=True)["passed"]
    with pytest.raises(ValueError, match="at least two"):
        determinism.compare([first])
    Path(first["raw_report"]).write_text("{}")
    with pytest.raises(ValueError):
        determinism.compare([first, second])


def test_copied_discovery_report_does_not_count_as_another_attempt(tmp_path):
    first = capture(tmp_path, minimal(), 0)
    copied = capture(tmp_path, minimal(), 1)
    with pytest.raises(ValueError, match="copied reports"):
        determinism.compare([first, copied])


@pytest.mark.parametrize("mutation", ["omission", "contradiction", "unsupported", "stage", "source", "boolean"])
def test_factual_summary_rejects_changed_claims(tmp_path, mutation):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(minimal()))
    candidate = factual.ledger(source, "validation")
    assert factual.check(source, candidate, "validation")["passed"]
    assert factual.render(candidate) == factual.render(factual.ledger(source, "validation"))
    if mutation == "omission":
        del candidate["claims"]["/checks/0/status"]
    elif mutation == "contradiction":
        candidate["claims"]["/checks/0/status"] = "pass"
    elif mutation == "unsupported":
        candidate["claims"]["/all_tasks_passed"] = True
    elif mutation == "stage":
        candidate["stage"] = "acceptance"
    elif mutation == "source":
        source.write_text(source.read_text() + "\n")
    else:
        candidate["claims"]["/task_count"] = False
    assert not factual.check(source, candidate, "validation")["passed"]


def test_same_capture_cannot_be_counted_twice(tmp_path):
    record = capture(tmp_path, minimal(), 0)
    with pytest.raises(ValueError, match="distinct"):
        determinism.compare([record, record])


def test_unknown_nested_collections_are_not_normalized():
    first = minimal() | {"extension": {"datasets": ["a", "b"]}}
    second = minimal() | {"extension": {"datasets": ["b", "a"]}}
    assert determinism.projection(first) != determinism.projection(second)


def test_free_prose_cannot_hide_outside_checked_claims(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(minimal()))
    candidate = factual.ledger(source, "validation") | {"summary": "All tasks passed."}
    assert not factual.check(source, candidate, "validation")["passed"]


def test_discovery_sample_survives_creation_and_concurrent_completion_order(tmp_path, monkeypatch, capsys):
    import asyncio
    import shutil
    import time

    discover = _import_discover(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "data.jsonl").write_text('{"row": 1}\n')
    names = list("abcdefg")
    monkeypatch.setattr(discover._probe, "probe", lambda root: {"harbor_cli": None})
    monkeypatch.setattr(discover._probe, "is_available", lambda runtime: False)
    monkeypatch.setattr(discover._probe, "probe_checks", lambda runtime: [])
    monkeypatch.setattr(discover.gym_probe, "probe", lambda root, cli: {"gym_cli": "fixture", "gym_available": True})
    monkeypatch.setattr(discover.gym_probe, "probe_checks", lambda *args, **kwargs: [])
    completed = []
    reverse = False

    def validate(cli, name, kind, root):
        index = names.index(name)
        time.sleep(0.01 * (6 - index if reverse else index))
        completed.append(name)
        return discover.gym_judge.GymRun(error="native-rejected" if name == "d" else None)

    monkeypatch.setattr(discover.gym_judge, "validate", validate)
    monkeypatch.setattr(
        discover.gym_solve, "run_fixture", lambda *args: discover.gym_judge.GymRun(report={"cases": []})
    )
    records, completion_orders = [], []
    for index, order in enumerate([names, list(reversed(names))]):
        reverse = bool(index)
        for name in order:
            folder = repo / "environments" / name
            folder.mkdir(parents=True)
            (folder / "manifest.yaml").write_text(
                f"name: {name}\nkind: environment\ndatasets:\n  - jsonl_fpath: data.jsonl\n"
            )
        monkeypatch.chdir(repo if index else tmp_path)
        code = asyncio.run(discover._main(["--repo", str(repo)]))
        report = json.loads(capsys.readouterr().out)
        records.append(capture(tmp_path, report, index, code))
        completion_orders.append(completed[:])
        completed.clear()
        if index == 0:
            shutil.rmtree(repo / "environments")
    assert completion_orders[0] != completion_orders[1]
    assert determinism.compare(records)["passed"]
    entries = records[0]["decision"]["gym_manifests"]
    assert [entry["name"] for entry in entries if entry["solve"]["status"] == "passed"] == ["a", "c", "e", "g"]
    assert sum(entry["solve"]["status"] == "not sampled" for entry in entries) == 2
    assert records[0]["decision"]["runnable"] is False  # good samples cannot hide rejected d
    assert records[0]["decision"]["phases"]["judge"] == "invalid"

    (repo / "environments/a/manifest.yaml").write_text(
        "name: a\nkind: environment\ndatasets:\n  - jsonl_fpath: absent.jsonl\n"
    )
    code = asyncio.run(discover._main(["--repo", str(repo)]))
    changed = capture(tmp_path, json.loads(capsys.readouterr().out), 2, code, fixture="changed-config")
    assert determinism.compare([records[0], changed], mutation=True)["passed"]
