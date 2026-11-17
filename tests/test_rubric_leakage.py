# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_leakage import scan_task  # noqa: E402

CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"
EXPECTED = '{"invoice": "INV-20931", "total": 4213.75, "currency": "EUR"}\n'
TEST_CODE = (
    "import json\n\n\n"
    "def test_total_matches_ledger():\n"
    "    data = json.load(open('/app/out.json'))\n"
    "    assert data == json.load(open('/tests/expected.json'))\n"
)


def write_task(root: Path, files: dict[str, str | bytes]) -> None:
    base = {
        "task.toml": "",
        "instruction.md": "Read /app/invoice.txt and write its fields to /app/out.json.\n",
        "environment/Dockerfile": "FROM python:3.12\nCOPY invoice.txt /app/invoice.txt\n",
        "environment/invoice.txt": "Invoice INV-20931\nAmount due: 4213.75 EUR\n",
        "tests/expected.json": EXPECTED,
        "tests/test_outputs.py": TEST_CODE,
        "solution/solve.sh": "python3 /app/parse.py > /app/out.json\n",
    }
    for relative, content in {**base, **files}.items():
        path = root / "task" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def unallowed(result: dict) -> list[tuple[str, str, str, str]]:
    return [
        (row["kind"], row["method"], row["private"], row["public"]) for row in result["findings"] if not row["allowed"]
    ]


def test_clean_task_only_reports_hints_from_the_intended_input(tmp_path: Path) -> None:
    write_task(tmp_path, {})

    result = scan_task(tmp_path, {"task": "task"})

    # The invoice legitimately contains the values the agent must extract.
    assert {row[:2] for row in unallowed(result)} == {("hint", "json-value")}
    assert result["counts"]["full"] == 0
    assert result["status"] == "unknown"


def test_allowed_overlap_clears_intended_matches(tmp_path: Path) -> None:
    write_task(tmp_path, {})
    selection = {
        "task": "task",
        "allowed_overlap": [
            {
                "private": "tests/expected.json",
                "public": "environment/invoice.txt",
                "reason": "The task extracts these.",
            }
        ],
    }

    result = scan_task(tmp_path, selection)

    assert result["status"] == "pass"
    assert unallowed(result) == []
    assert result["counts"]["allowed"] == len(result["findings"]) > 0
    assert {row["allowed_reason"] for row in result["findings"]} == {"The task extracts these."}


@pytest.mark.parametrize(
    ("leaked", "method"),
    [
        (EXPECTED, "exact"),
        ('{\n  "invoice":  "INV-20931",\n  "total": 4213.75, "currency": "EUR"}\n', "json-canonical"),
        (EXPECTED.upper(), "normalized"),
    ],
)
def test_full_answer_in_the_environment_fails(tmp_path: Path, leaked: str, method: str) -> None:
    write_task(tmp_path, {"environment/cache/answer.json": leaked})

    result = scan_task(tmp_path, {"task": "task"})

    assert ("full", method, "task/tests/expected.json", "task/environment/cache/answer.json") in unallowed(result)
    assert result["status"] == "fail"
    assert {"code": "leakage.full", "subject": "task/environment/cache/answer.json", "status": "fail"} in result[
        "diagnostics"
    ]


def test_findings_never_copy_private_content(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/cache/answer.json": EXPECTED})

    serialized = json.dumps(scan_task(tmp_path, {"task": "task"}))

    for secret in ("INV-20931", "4213.75", "test_total_matches_ledger"):
        assert secret not in serialized


def test_finding_reports_the_public_line_range(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/notes.md": "# Notes\n\nCached run:\n" + EXPECTED})

    finding = next(row for row in scan_task(tmp_path, {"task": "task"})["findings"] if row["kind"] == "full")

    assert finding["lines"] == [4, 4]


def test_copied_test_line_is_a_hint(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        {"environment/helper.py": "# borrowed\n    assert data == json.load(open('/tests/expected.json'))\n"},
    )

    result = scan_task(tmp_path, {"task": "task"})

    assert ("hint", "normalized-line", "task/tests/test_outputs.py", "task/environment/helper.py") in unallowed(result)


@pytest.mark.parametrize(
    "mention",
    ["Your output is checked by tests/test_outputs.py.", "Make test_total_matches_ledger pass.", "See expected.json."],
)
def test_instruction_naming_private_material_is_a_reference(tmp_path: Path, mention: str) -> None:
    write_task(tmp_path, {"instruction.md": f"Parse the invoice. {mention}\n"})

    result = scan_task(tmp_path, {"task": "task"})

    assert any(row[0] == "reference" and row[3] == "task/instruction.md" for row in unallowed(result))
    assert result["counts"]["reference"] >= 1


def test_generic_names_are_not_references(tmp_path: Path) -> None:
    write_task(tmp_path, {"instruction.md": "Write a solve.sh helper if you like. Results go in /app.\n"})

    result = scan_task(tmp_path, {"task": "task"})

    assert result["counts"]["reference"] == 0


def test_generated_reconstruction_file_cannot_be_allowed_overlap(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/cache/answer.json": EXPECTED})
    selection = {
        "task": "task",
        "allowed_overlap": [
            {"private": "tests/expected.json", "public": "environment/cache/answer.json", "reason": "x"}
        ],
    }
    generated = {
        "records": [{"declaration": "generated", "artifact_digest": hashlib.sha256(EXPECTED.encode()).hexdigest()}]
    }

    result = scan_task(tmp_path, selection, reconstruction=generated)

    assert ("full", "exact", "task/tests/expected.json", "task/environment/cache/answer.json") in unallowed(result)
    assert any(row["code"] == "leakage.generated_overlap_not_allowed" for row in result["diagnostics"])
    assert result["status"] == "fail"


def test_binary_files_are_reported_unscanned(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/image.bin": b"\xff\xfe\x00answer"})

    result = scan_task(tmp_path, {"task": "task"})

    assert result["unscanned"] == ["task/environment/image.bin"]
    assert any(row["code"] == "leakage.unscanned_files" for row in result["diagnostics"])


def test_non_harbor_directory_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "evals").mkdir()

    result = scan_task(tmp_path, {"task": "evals"})

    assert result["status"] == "unknown"
    assert result["findings"] == []
    assert [row["code"] for row in result["diagnostics"]] == ["leakage.layout_not_harbor"]


@pytest.mark.parametrize(
    "selected",
    [
        {"task": "task", "allowed_overlap": [{"private": "tests/expected.json", "public": ""}]},
        {"task": "task", "allowed_overlap": {}},
        {"task": "task", "other": 1},
    ],
)
def test_rejects_invalid_selections(tmp_path: Path, selected: dict) -> None:
    write_task(tmp_path, {})

    with pytest.raises(ValueError):
        scan_task(tmp_path, selected)


def test_measure_reports_leakage(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/cache/answer.json": EXPECTED})
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"leakage": {"task": "task"}}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(selection)],
        capture_output=True,
        text=True,
        check=False,
    )

    report = json.loads(completed.stdout)
    assert report["measurements"]["leakage"]["status"] == "fail"
    assert completed.returncode == 1


def test_answer_in_a_public_file_name_is_flagged(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        {
            "tests/expected.txt": "4213\n",
            "environment/data/expected_total_4213.csv": "id,amount\n",
            "environment/data/INV-20931/notes.txt": "notes\n",
            "environment/data/total_42130.csv": "id\n",
        },
    )

    result = scan_task(tmp_path, {"task": "task"})

    flagged = {(row["private"], row["public"]) for row in result["findings"] if row["kind"] == "filename"}
    assert flagged == {
        ("task/tests/expected.txt", "task/environment/data/expected_total_4213.csv"),
        ("task/tests/expected.json", "task/environment/data/INV-20931/notes.txt"),
    }
    assert {row["lines"] for row in result["findings"] if row["kind"] == "filename"} == {None}
    assert {
        "code": "leakage.filename",
        "subject": "task/environment/data/expected_total_4213.csv",
        "status": "unknown",
    } in result["diagnostics"]


def test_hidden_cases_are_protected_in_a_clean_layout(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/invoice.txt": "Invoice for a different customer\n"})

    assert scan_task(tmp_path, {"task": "task"})["hidden_cases"] == "protected"


def test_hidden_cases_are_exposed_by_a_full_copy(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/copy/test_outputs.py": TEST_CODE})

    assert scan_task(tmp_path, {"task": "task"})["hidden_cases"] == "exposed"


@pytest.mark.parametrize(
    "files",
    [
        {"environment/docker-compose.yaml": "services:\n  main:\n    volumes:\n      - ../tests:/tests\n"},
        {"environment/blob.bin": b"\xff\xfe"},
        {"instruction.md": "Make test_total_matches_ledger pass.\n"},
    ],
)
def test_hidden_cases_are_unknown_when_the_scan_cannot_settle_them(tmp_path: Path, files: dict) -> None:
    write_task(tmp_path, files)

    assert scan_task(tmp_path, {"task": "task"})["hidden_cases"] == "unknown"


def test_trajectory_and_step_files_are_scanned(tmp_path: Path) -> None:
    write_task(
        tmp_path,
        {
            "trajectory.json": '{"steps": [{"message": "' + EXPECTED.strip().replace('"', '\\"') + '"}]}\n',
            "steps/second/instruction.md": "Now check tests/test_outputs.py.\n",
            "steps/second/tests/expected.json": '{"step": "SECOND-ANSWER-77"}\n',
            "steps/second/workdir/seed.txt": "SECOND-ANSWER-77\n",
        },
    )

    result = scan_task(tmp_path, {"task": "task"})

    publics = {(row["kind"], row["public"]) for row in result["findings"] if not row["allowed"]}
    assert ("hint", "task/trajectory.json") in publics
    assert ("reference", "task/steps/second/instruction.md") in publics
    assert ("hint", "task/steps/second/workdir/seed.txt") in publics


def test_task_at_the_selected_root(tmp_path: Path) -> None:
    write_task(tmp_path, {"environment/cache/answer.json": EXPECTED})

    result = scan_task(tmp_path / "task", {"task": "."})

    assert ("full", "exact", "tests/expected.json", "environment/cache/answer.json") in unallowed(result)


def test_reports_whether_generated_files_were_checked(tmp_path: Path) -> None:
    write_task(tmp_path, {})

    assert scan_task(tmp_path, {"task": "task"})["generated_files_checked"] is False
    assert scan_task(tmp_path, {"task": "task"}, reconstruction={"records": []})["generated_files_checked"] is True
