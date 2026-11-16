# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_overlap import check_overlap  # noqa: E402

CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"
REQUIRED = ["development", "test"]


def write_task(
    root: Path, name: str, instruction: str, *, toml: str = "", dockerfile: str = "FROM python:3.12\n"
) -> str:
    task = root / "tasks" / name
    (task / "environment").mkdir(parents=True)
    (task / "task.toml").write_text(toml, encoding="utf-8")
    (task / "instruction.md").write_text(instruction, encoding="utf-8")
    (task / "environment" / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    return f"tasks/{name}"


def kinds(result: dict) -> list[tuple[str, str, list[str]]]:
    return [(row["kind"], row["method"], [ref["task"] for ref in row["tasks"]]) for row in result["findings"]]


def test_distinct_splits_pass(tmp_path: Path) -> None:
    dev = write_task(tmp_path, "add", "Add two numbers.\n")
    test = write_task(tmp_path, "sort", "Sort a list.\n")

    result = check_overlap(tmp_path, {"splits": {"development": [dev], "test": [test]}, "required": REQUIRED})

    assert result["status"] == "pass"
    assert result["findings"] == []
    assert result["splits"]["test"] == {"declared": True, "tasks": 1, "compared": 1, "with_origin": 0}


@pytest.mark.parametrize(
    ("second", "method"),
    [("Add two numbers.\n", "exact-instruction"), ("add   two\nNUMBERS.", "normalized-instruction")],
)
def test_same_task_in_both_splits_is_a_duplicate(tmp_path: Path, second: str, method: str) -> None:
    dev = write_task(tmp_path, "add", "Add two numbers.\n")
    test = write_task(tmp_path, "add-copy", second)

    result = check_overlap(tmp_path, {"splits": {"development": [dev], "test": [test]}, "required": REQUIRED})

    assert kinds(result) == [("duplicate", method, ["tasks/add", "tasks/add-copy"])]
    assert result["status"] == "fail"


def test_same_directory_listed_in_both_splits(tmp_path: Path) -> None:
    task = write_task(tmp_path, "add", "Add two numbers.\n")

    result = check_overlap(tmp_path, {"splits": {"development": [task], "test": [task]}, "required": REQUIRED})

    assert kinds(result) == [("duplicate", "same-directory", ["tasks/add", "tasks/add"])]


def test_siblings_from_metadata_share_repository_and_commit(tmp_path: Path) -> None:
    toml = '[metadata]\nrepo = "https://github.com/acme/parser.git"\nbase_commit = "ABC1234"\n'
    dev = write_task(tmp_path, "fix-a", "Fix the date parser.\n", toml=toml)
    test = write_task(tmp_path, "fix-b", "Fix the number parser.\n", toml=toml.replace(".git", "/"))

    result = check_overlap(tmp_path, {"splits": {"development": [dev], "test": [test]}, "required": REQUIRED})

    assert kinds(result) == [("sibling", "repository-commit", ["tasks/fix-a", "tasks/fix-b"])]


def test_siblings_from_the_environment_git_commands(tmp_path: Path) -> None:
    dockerfile = (
        "FROM python:3.12\n"
        "RUN git clone --depth 50 https://github.com/acme/parser /app\n"
        "RUN git -C /app checkout 0123abcd\n"
    )
    dev = write_task(tmp_path, "fix-a", "Fix the date parser.\n", dockerfile=dockerfile)
    test = write_task(tmp_path, "fix-b", "Fix the number parser.\n", dockerfile=dockerfile)
    other = write_task(
        tmp_path, "fix-c", "Fix the CSV parser.\n", dockerfile=dockerfile.replace("0123abcd", "fedc9876")
    )

    result = check_overlap(tmp_path, {"splits": {"development": [dev], "test": [test, other]}, "required": REQUIRED})

    assert kinds(result) == [("sibling", "repository-commit", ["tasks/fix-a", "tasks/fix-b"])]
    assert result["splits"]["test"]["with_origin"] == 2


def test_tasks_in_the_same_split_are_not_compared(tmp_path: Path) -> None:
    first = write_task(tmp_path, "add", "Add two numbers.\n")
    second = write_task(tmp_path, "add-copy", "Add two numbers.\n")

    result = check_overlap(tmp_path, {"splits": {"development": [first, second], "test": []}, "required": REQUIRED})

    assert result["findings"] == []


def test_missing_split_is_unknown_not_clean(tmp_path: Path) -> None:
    dev = write_task(tmp_path, "add", "Add two numbers.\n")

    result = check_overlap(tmp_path, {"splits": {"development": [dev]}, "required": REQUIRED})

    assert result["status"] == "unknown"
    assert result["splits"]["test"] == {"declared": False, "tasks": 0, "compared": 0, "with_origin": 0}
    assert result["diagnostics"] == [{"code": "overlap.split_missing", "subject": "test", "status": "unknown"}]


def test_unreadable_task_is_unknown(tmp_path: Path) -> None:
    dev = write_task(tmp_path, "add", "Add two numbers.\n")
    (tmp_path / "evals").mkdir()

    result = check_overlap(tmp_path, {"splits": {"development": [dev], "test": ["evals"]}, "required": REQUIRED})

    assert result["status"] == "unknown"
    assert result["splits"]["test"]["compared"] == 0
    assert [row["code"] for row in result["diagnostics"]] == ["overlap.task_not_harbor"]


def test_findings_name_tasks_not_their_content(tmp_path: Path) -> None:
    dev = write_task(tmp_path, "add", "The answer is 4213.\n")
    test = write_task(tmp_path, "add-copy", "The answer is 4213.\n")

    result = check_overlap(tmp_path, {"splits": {"development": [dev], "test": [test]}, "required": REQUIRED})

    assert "4213" not in json.dumps(result)


@pytest.mark.parametrize(
    "selected",
    [
        {"splits": {"development": []}},
        {"splits": {"development": []}, "required": ["development"]},
        {"splits": {"development": [""]}, "required": REQUIRED},
        {"splits": [], "required": REQUIRED},
    ],
)
def test_rejects_invalid_selections(tmp_path: Path, selected: dict) -> None:
    with pytest.raises(ValueError):
        check_overlap(tmp_path, selected)


def test_measure_reports_overlap(tmp_path: Path) -> None:
    dev = write_task(tmp_path, "add", "Add two numbers.\n")
    test = write_task(tmp_path, "add-copy", "Add two numbers.\n")
    selection = tmp_path / "selection.json"
    overlap = {"splits": {"development": [dev], "test": [test]}, "required": REQUIRED}
    selection.write_text(json.dumps({"overlap": overlap}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(selection)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert json.loads(completed.stdout)["measurements"]["overlap"]["counts"] == {"duplicate": 1, "sibling": 0}
