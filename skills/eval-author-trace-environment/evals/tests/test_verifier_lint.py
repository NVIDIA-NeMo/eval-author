# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verifier review diagnostics do not execute source or alter proof authority."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "scripts"))
import verifier_lint as lint  # noqa: E402


def write_verifier(root, source, name="check.py"):
    path = root / "task/tests" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


@pytest.mark.parametrize(
    "source",
    [
        "import inspect\ninspect.getsource(target)",
        "import inspect as inspection\ninspection.getsourcelines(target)",
        "from inspect import getsource as source\nsource(target)",
        "open(target.__file__).read()",
        "open(file=str(target.__file__)).read()",
        "Path(target.__file__).read_text()",
        "pathlib.Path(target.__file__).read_bytes()",
    ],
)
def test_source_access_is_a_content_free_review_lead(tmp_path, source):
    write_verifier(tmp_path, source + "\n# private fixture body must not be emitted\n")
    rows = lint.verifier_advisories(tmp_path)
    assert len(rows) == 1
    assert rows[0]["code"] == "verifier_source_inspection"
    assert rows[0]["path"] == "task/tests/check.py"
    assert "private fixture body" not in json.dumps(rows)


@pytest.mark.parametrize(
    "source",
    [
        "assert target.compute(2) == 4",
        "Path(target.__file__).with_name('fixture.json').read_text()",
        "open(Path(target.__file__).parent / 'fixture.json').read()",
        "Path(__file__).parent.joinpath('fixture.json').read_bytes()",
        "# inspect.getsource(target)\ntext = 'open(target.__file__)'",
        "other.getsource(target)",
    ],
)
def test_behavior_and_fixture_discovery_stay_quiet(tmp_path, source):
    write_verifier(tmp_path, source)
    assert lint.verifier_advisories(tmp_path) == []


def test_nested_files_are_parsed_without_execution(tmp_path):
    marker = tmp_path / "executed"
    write_verifier(
        tmp_path,
        f"open({str(marker)!r}, 'w').write('bad')\nimport inspect\ninspect.getsource(target)",
        "helpers/check.py",
    )
    assert lint.verifier_advisories(tmp_path)[0]["path"] == "task/tests/helpers/check.py"
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "root"])
def test_symlinks_are_not_followed(tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("import inspect\ninspect.getsource(secret)")
    root = tmp_path / "task/tests"
    root.parent.mkdir()
    if kind == "root":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()
        (root / ("linked.py" if kind == "file" else "linked")).symlink_to(
            outside / "secret.py" if kind == "file" else outside, target_is_directory=kind == "directory"
        )
    rows = lint.verifier_advisories(tmp_path)
    assert rows and all(row["code"] == "verifier_scan_incomplete" for row in rows)


def test_malformed_source_is_incomplete_without_echoing_payload(tmp_path):
    write_verifier(tmp_path, "private_payload = (\n")
    rows = lint.verifier_advisories(tmp_path)
    assert rows[0]["code"] == "verifier_scan_incomplete"
    assert "private_payload" not in json.dumps(rows)


@pytest.mark.parametrize(
    "limit,value",
    [
        ("MAX_ENTRIES", 1),
        ("MAX_FILES", 1),
        ("MAX_FILE_BYTES", 1),
        ("MAX_TOTAL_BYTES", 1),
        ("MAX_FINDINGS", 1),
    ],
)
def test_limits_are_visible(tmp_path, monkeypatch, limit, value):
    write_verifier(tmp_path, "import inspect\ninspect.getsource(target)", "a.py")
    write_verifier(tmp_path, "import inspect\ninspect.getsource(target)", "b.py")
    monkeypatch.setattr(lint, limit, value)
    assert any(row["code"] == "verifier_scan_incomplete" for row in lint.verifier_advisories(tmp_path))


def test_unreadable_pending_directories_respect_finding_limit(tmp_path, monkeypatch):
    root = tmp_path / "task" / "tests"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
    original = lint.os.scandir

    def denied(path):
        if Path(path) != root:
            raise PermissionError("private detail")
        return original(path)

    monkeypatch.setattr(lint, "MAX_FINDINGS", 1)
    monkeypatch.setattr(lint.os, "scandir", denied)
    rows = lint.verifier_advisories(tmp_path)
    assert len(rows) == 2
    assert "finding limit reached" in rows[-1]["hint"]
    assert "private detail" not in json.dumps(rows)


def test_read_failure_is_incomplete(tmp_path, monkeypatch):
    path = write_verifier(tmp_path, "assert True")
    original = Path.open

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("private operating-system detail")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    rows = lint.verifier_advisories(tmp_path)
    assert rows[0]["code"] == "verifier_scan_incomplete"
    assert "private operating-system detail" not in json.dumps(rows)


def test_validate_task_exposes_advisory_without_blocking_or_changing_proof(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "trace_contract_test_helpers", SKILL.parents[1] / "tests/test_trace_environment.py"
    )
    assert spec is not None and spec.loader is not None
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    task_dir, _ = helpers._workspace(tmp_path)
    helpers._candidate(task_dir)
    helpers._ready_environment(task_dir, record_validation=False)
    write_verifier(task_dir, "import inspect\nassert 'private_implementation' in inspect.getsource(target)")
    code, result = helpers._run("validate-task", "--task-dir", str(task_dir))
    assert code == 0, result
    assert result["ok"] is True
    assert [row["code"] for row in result["advisories"]] == ["verifier_source_inspection"]
    assert not (task_dir / "validation.json").exists()
