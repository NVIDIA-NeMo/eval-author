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


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    task = tmp_path / "tasks" / "invoice-total"
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text("", encoding="utf-8")
    (task / "instruction.md").write_text("Total the invoice.\n", encoding="utf-8")
    (task / "tests" / "expected.json").write_text('{"invoice": "INV-20931", "total": 4213.75}\n', encoding="utf-8")
    (task / "tests" / "test_outputs.py").write_text(TEST_CODE, encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "SKILL.md").write_text("# Invoices\n\nRead every line item.\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def edit_skill(repo: Path, added: str) -> None:
    path = repo / "skills" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + added, encoding="utf-8")


def selection(**overrides: object) -> dict:
    return {"tasks": ["tasks/invoice-total"], "base": "HEAD", "head": None, "paths": ["skills"], **overrides}


def kinds(result: dict) -> list[tuple[str, str, int]]:
    return [(row["kind"], row["changed"], row["line"]) for row in result["findings"]]


def test_skill_change_with_an_expected_value_fails(repo: Path) -> None:
    edit_skill(repo, "\nIf the invoice is INV-20931, report the total as 4213.75.\n")

    result = check_reuse(repo, selection())

    assert kinds(result) == [("expected-value", "skills/SKILL.md", 5)]
    assert result["status"] == "fail"


def test_unrelated_skill_change_passes(repo: Path) -> None:
    edit_skill(repo, "\nCheck the currency before adding amounts.\n")

    result = check_reuse(repo, selection())

    assert result["status"] == "pass"
    assert result["findings"] == []
    assert result["counts"]["added_lines"] == 2


def test_copied_grader_line_and_test_name(repo: Path) -> None:
    edit_skill(
        repo, "\nMake test_total_matches_ledger pass:\n  assert json.load(open('/app/out.json'))['total'] == 4213.75\n"
    )

    result = check_reuse(repo, selection())

    found = {row[0] for row in kinds(result)}
    assert {"test-name", "private-line"} <= found
    assert result["status"] == "fail"


def test_task_id_alone_is_unknown(repo: Path) -> None:
    edit_skill(repo, "\nThis helps with invoice-total.\n")

    result = check_reuse(repo, selection())

    assert kinds(result) == [("task-id", "skills/SKILL.md", 5)]
    assert result["status"] == "unknown"


def test_committed_change_between_revisions(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    edit_skill(repo, "\nThe answer for INV-20931 is known.\n")
    git(repo, "commit", "-q", "-am", "tune skill")

    result = check_reuse(repo, selection(base=base, head="HEAD"))

    assert kinds(result) == [("expected-value", "skills/SKILL.md", 5)]


def test_changes_inside_the_task_are_ignored(repo: Path) -> None:
    path = repo / "tasks" / "invoice-total" / "instruction.md"
    path.write_text("Total invoice INV-20931.\n", encoding="utf-8")

    result = check_reuse(repo, selection(paths=["."]))

    assert result["findings"] == []


def test_findings_never_copy_private_content(repo: Path) -> None:
    edit_skill(repo, "\nINV-20931 totals 4213.75; see test_total_matches_ledger.\n")

    serialized = json.dumps(check_reuse(repo, selection()))

    for secret in ("INV-20931", "4213.75", "test_total_matches_ledger"):
        assert secret not in serialized


def test_unknown_revision_is_unknown(repo: Path) -> None:
    result = check_reuse(repo, selection(base="no-such-branch"))

    assert result["status"] == "unknown"
    assert [row["code"] for row in result["diagnostics"]] == ["reuse.revision_unresolved"]


@pytest.mark.parametrize(
    "overrides",
    [{"tasks": []}, {"paths": []}, {"paths": ["--output=x"]}, {"base": "--all"}, {"base": ""}],
)
def test_rejects_invalid_selections(repo: Path, overrides: dict) -> None:
    with pytest.raises(ValueError):
        check_reuse(repo, selection(**overrides))


def test_measure_reports_reuse(repo: Path) -> None:
    edit_skill(repo, "\nReport 4213.75.\n")
    selected = repo.parent / "selection.json"
    selected.write_text(json.dumps({"reuse": selection()}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "--root", str(repo), "--selection", str(selected)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert json.loads(completed.stdout)["measurements"]["reuse"]["counts"]["expected-value"] == 1
