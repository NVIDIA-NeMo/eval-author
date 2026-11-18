# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_reuse import check_reuse  # noqa: E402

CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"
TEST_CODE = (
    "import json\n\n\n"
    "def test_total_matches_ledger():\n"
    "    assert json.load(open('/app/out.json'))['total'] == 4213.75\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    task = tmp_path / "tasks" / "invoice-total"
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text("", encoding="utf-8")
    (task / "instruction.md").write_text("Total the invoice.\n", encoding="utf-8")
    (task / "tests" / "expected.json").write_text('{"invoice": "INV-20931", "total": 4213.75}\n', encoding="utf-8")
    (task / "tests" / "test_outputs.py").write_text(TEST_CODE, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "SKILL.md").write_text("# Invoices\n\nRead every line item.\n", encoding="utf-8")
    return tmp_path


def write_skill(repo: Path, added: str, name: str = "SKILL.md") -> None:
    path = repo / "skills" / name
    path.write_text("# Invoices\n\nRead every line item.\n" + added, encoding="utf-8")


def selection(**overrides: object) -> dict:
    return {"tasks": ["tasks/invoice-total"], "paths": ["skills"], **overrides}


def kinds(result: dict) -> list[tuple[str, str, int]]:
    return [(row["kind"], row["file"], row["line"]) for row in result["findings"]]


def test_skill_with_an_expected_value_fails(repo: Path) -> None:
    write_skill(repo, "\nIf the invoice is INV-20931, report the total as 4213.75.\n")

    result = check_reuse(repo, selection())

    assert kinds(result) == [("expected-value", "skills/SKILL.md", 5)]
    assert result["status"] == "fail"


def test_content_present_from_the_start_is_found(repo: Path) -> None:
    (repo / "skills" / "SKILL.md").write_text("Totals: INV-20931 is 4213.75.\n", encoding="utf-8")

    assert kinds(check_reuse(repo, selection())) == [("expected-value", "skills/SKILL.md", 1)]


def test_clean_skill_passes(repo: Path) -> None:
    result = check_reuse(repo, selection())

    assert result["status"] == "pass"
    assert result["findings"] == []
    assert result["counts"]["scanned_files"] == 1


def test_copied_grader_line_and_test_name(repo: Path) -> None:
    write_skill(
        repo,
        "\nMake test_total_matches_ledger pass:\n  assert json.load(open('/app/out.json'))['total'] == 4213.75\n",
    )

    result = check_reuse(repo, selection())

    assert {"test-name", "private-line"} <= {row[0] for row in kinds(result)}
    assert result["status"] == "fail"


def test_task_id_alone_is_unknown(repo: Path) -> None:
    write_skill(repo, "\nThis helps with invoice-total.\n")

    result = check_reuse(repo, selection())

    assert kinds(result) == [("task-id", "skills/SKILL.md", 5)]
    assert result["status"] == "unknown"


def test_selected_single_file_and_nested_directories(repo: Path) -> None:
    (repo / "harness").mkdir()
    (repo / "harness" / "prompt.txt").write_text("Answer 4213.75.\n", encoding="utf-8")
    (repo / "skills" / "nested").mkdir()
    (repo / "skills" / "nested" / "notes.md").write_text("INV-20931\n", encoding="utf-8")

    result = check_reuse(repo, selection(paths=["skills", "harness/prompt.txt"]))

    assert {row[1] for row in kinds(result)} == {"harness/prompt.txt", "skills/nested/notes.md"}


def test_files_inside_the_task_are_skipped(repo: Path) -> None:
    result = check_reuse(repo, selection(paths=["."]))

    assert all(not row["file"].startswith("tasks/") for row in result["findings"])


def test_findings_never_copy_private_content(repo: Path) -> None:
    write_skill(repo, "\nINV-20931 totals 4213.75; see test_total_matches_ledger.\n")

    serialized = json.dumps(check_reuse(repo, selection()))

    for secret in ("INV-20931", "4213.75", "test_total_matches_ledger"):
        assert secret not in serialized


def test_symlinked_private_tests_leave_the_result_unknown(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("outside")
    tests = repo / "tasks" / "invoice-total" / "tests"
    (outside / "expected.json").write_text((tests / "expected.json").read_text(), encoding="utf-8")
    for path in tests.iterdir():
        path.unlink()
    tests.rmdir()
    tests.symlink_to(outside, target_is_directory=True)

    result = check_reuse(repo, selection())

    assert result["status"] == "unknown"
    assert any(row["code"] == "reuse.layout_symlink" for row in result["diagnostics"])


def test_missing_and_binary_files_are_unknown(repo: Path) -> None:
    (repo / "skills" / "blob.bin").write_bytes(b"\xff\xfe")

    result = check_reuse(repo, selection(paths=["skills", "missing"]))

    codes = {row["code"] for row in result["diagnostics"]}
    assert {"reuse.path_unavailable", "reuse.file_unscanned"} <= codes
    assert result["status"] == "unknown"


@pytest.mark.parametrize(
    "overrides",
    [{"tasks": []}, {"paths": []}, {"paths": ["../outside"]}, {"paths": ["/abs"]}, {"base": "HEAD"}],
)
def test_rejects_invalid_selections(repo: Path, overrides: dict) -> None:
    with pytest.raises(ValueError):
        check_reuse(repo, selection(**overrides))


def test_root_cannot_be_the_task(repo: Path) -> None:
    with pytest.raises(ValueError):
        check_reuse(repo / "tasks" / "invoice-total", {"tasks": ["."], "paths": ["tests"]})


def test_measure_reports_reuse(repo: Path) -> None:
    write_skill(repo, "\nReport 4213.75.\n")
    selected = repo.parent / "selection.json"
    selected.write_text(json.dumps({"reuse": selection()}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "--root", str(repo), "--selection", str(selected)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert json.loads(completed.stdout)["measurements"]["reuse"]["counts"]["expected-value"] == 1
