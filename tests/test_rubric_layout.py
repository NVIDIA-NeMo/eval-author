# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_layout import label_task  # noqa: E402

CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"


def write_task(root: Path, files: dict[str, str]) -> Path:
    task = root / "tasks" / "add"
    for relative, content in {"task.toml": "", "instruction.md": "Add two numbers.\n", **files}.items():
        path = task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return task


def labels(result: dict) -> dict[str, str]:
    return {record["path"]: record["visibility"] for record in result["files"]}


def test_labels_files_from_the_harbor_layout(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        {
            "environment/Dockerfile": "FROM python:3.12\n",
            "environment/data/input.csv": "1,2\n",
            "tests/test.sh": "pytest\n",
            "solution/solve.sh": "echo 3\n",
            "README.md": "notes\n",
        },
    )

    result = label_task(tmp_path, {"task": "tasks/add"})

    assert result["status"] == "pass"
    assert result["format"] == "harbor"
    assert labels(result) == {
        "tasks/add/README.md": "unlabeled",
        "tasks/add/environment/Dockerfile": "public",
        "tasks/add/environment/data/input.csv": "public",
        "tasks/add/instruction.md": "public",
        "tasks/add/solution/solve.sh": "private",
        "tasks/add/task.toml": "configuration",
        "tasks/add/tests/test.sh": "private",
    }
    assert result["counts"] == {"public": 3, "private": 2, "configuration": 1, "unlabeled": 1, "unresolved": 0}
    digests = {record["path"]: record["digest"] for record in result["files"]}
    assert digests["tasks/add/tests/test.sh"] == hashlib.sha256(b"pytest\n").hexdigest()
    assert digests["tasks/add/task.toml"] == hashlib.sha256(b"").hexdigest()


def test_a_directory_outside_the_harbor_format_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "eval.py").write_text("print('score')\n", encoding="utf-8")

    result = label_task(tmp_path, {"task": "evals"})

    assert result["status"] == "unknown"
    assert result["format"] is None
    assert result["files"] == []
    assert [row["code"] for row in result["diagnostics"]] == ["layout.not_harbor"]


def test_environment_build_file_naming_private_directories_is_a_candidate(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        {"environment/docker-compose.yaml": "services:\n  main:\n    volumes:\n      - ../tests:/opt/tests\n"},
    )

    result = label_task(tmp_path, {"task": "tasks/add"})

    assert result["status"] == "unknown"
    assert result["diagnostics"] == [
        {
            "code": "layout.private_path_in_environment",
            "subject": "tasks/add/environment/docker-compose.yaml",
            "status": "unknown",
        }
    ]


def test_ordinary_environment_files_naming_tests_are_not_flagged(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/notes.txt": "see tests/ for grading\n"})

    assert label_task(tmp_path, {"task": "tasks/add"})["status"] == "pass"


def test_symlinks_stay_unresolved(tmp_path: Path) -> None:
    task = write_task(tmp_path, {"tests/expected.json": "{}\n"})
    (task / "environment").mkdir()
    (task / "environment" / "expected.json").symlink_to(task / "tests" / "expected.json")
    (task / "environment" / "linked").symlink_to(task / "tests", target_is_directory=True)

    result = label_task(tmp_path, {"task": "tasks/add"})

    assert result["status"] == "unknown"
    assert labels(result)["tasks/add/environment/expected.json"] == "unresolved"
    assert labels(result)["tasks/add/environment/linked"] == "unresolved"
    assert result["counts"]["unresolved"] == 2


@pytest.mark.parametrize(
    "selected", [{"task": "../x"}, {"task": "/abs"}, {"task": ""}, {"task": "tasks/add", "x": 1}, []]
)
def test_rejects_invalid_selections(tmp_path: Path, selected: object) -> None:
    write_task(tmp_path, {})

    with pytest.raises(ValueError):
        label_task(tmp_path, selected)


def test_measure_reports_layout_labels(tmp_path: Path) -> None:
    write_task(tmp_path, {"tests/test.sh": "pytest\n"})
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"layout": {"task": "tasks/add"}}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(selection)],
        capture_output=True,
        text=True,
        check=False,
    )

    report = json.loads(completed.stdout)
    assert report["measurements"]["layout"]["counts"]["private"] == 1
    assert report["measurements"]["layout"]["status"] == "pass"


def test_trajectory_and_declared_steps_are_labeled(tmp_path: Path) -> None:
    task = tmp_path / "tasks" / "add"
    files = {
        "task.toml": '[[steps]]\nname = "first"\n',
        "trajectory.json": "{}",
        "steps/first/instruction.md": "Step one.\n",
        "steps/first/trajectory.json": "{}",
        "steps/first/workdir/seed.txt": "1\n",
        "steps/first/tests/test.sh": "pytest\n",
        "steps/first/solution/solve.sh": "echo 1\n",
        "steps/first/notes.md": "x\n",
    }
    for relative, content in files.items():
        (task / relative).parent.mkdir(parents=True, exist_ok=True)
        (task / relative).write_text(content, encoding="utf-8")

    result = label_task(tmp_path, {"task": "tasks/add"})

    assert result["format"] == "harbor"
    assert labels(result) == {
        "tasks/add/task.toml": "configuration",
        "tasks/add/trajectory.json": "public",
        "tasks/add/steps/first/instruction.md": "public",
        "tasks/add/steps/first/trajectory.json": "public",
        "tasks/add/steps/first/workdir/seed.txt": "public",
        "tasks/add/steps/first/tests/test.sh": "private",
        "tasks/add/steps/first/solution/solve.sh": "private",
        "tasks/add/steps/first/notes.md": "unlabeled",
    }


def test_task_without_instruction_or_steps_is_not_harbor(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text('[metadata]\nname = "x"\n', encoding="utf-8")

    assert label_task(tmp_path, {"task": "."})["format"] is None


def test_task_at_the_selected_root_uses_plain_paths(tmp_path: Path) -> None:
    task = write_task(tmp_path, {"tests/test.sh": "pytest\n"})

    result = label_task(task, {"task": "."})

    assert result["status"] == "pass"
    assert labels(result)["tests/test.sh"] == "private"
    assert {record["relative"] for record in result["files"]} == set(labels(result))


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs an unprivileged POSIX user")
def test_unreadable_directory_is_unknown(tmp_path: Path) -> None:
    task = write_task(tmp_path, {"tests/test.sh": "pytest\n"})
    (task / "tests").chmod(0)
    try:
        result = label_task(tmp_path, {"task": "tasks/add"})
    finally:
        (task / "tests").chmod(0o755)

    assert result["status"] == "unknown"
    assert {"code": "layout.directory_unreadable", "subject": "tasks/add/tests", "status": "unknown"} in result[
        "diagnostics"
    ]
    assert result["counts"]["private"] == 0
