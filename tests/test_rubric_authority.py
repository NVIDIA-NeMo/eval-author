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
from rubric_authority import check_authority  # noqa: E402

ETHOS = b"---\nname: x\n---\n\n## Purpose & Outcomes\n\nTotals use two decimals.\n\n## Constraints\n\nNo writes.\n"
INSTRUCTION = b"Compute the total.\nWrite it to total.txt.\n"
FIXTURE = b'{"orders": [{"amount": 120.25}]}'
# CRLF with a BOM, front matter, setext and formatted headings, a repeat, and a fenced comment.
GUIDE = (
    (
        "\ufeff---\ntitle: Guide\n---\n\n# Totals\n\nSee [the rules](rules.md)\n=====\n\n"
        "## Use `round` here\n\n## Totals\n\n```bash\n# Install deps\n```\n"
    )
    .replace("\n", "\r\n")
    .encode()
)


def record(root, ident, role, path, content, digest=None):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(content)
    actual = hashlib.sha256(content).hexdigest()
    return {
        "id": ident,
        "role": role,
        "path": path,
        "digest": digest or actual,
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "fixture",
        "evidence": [{"path": path, "digest": actual}],
    }


def measure(root, references, *, checks=None, sources=None):
    requirements = [
        {"id": f"r{index}", "statement": "s", "authority": {"basis": basis, "reference": reference}}
        for index, (basis, reference) in enumerate(references)
    ]
    checks = checks if checks is not None else [{"id": "c", "requirements": [row["id"] for row in requirements]}]
    plan = json.dumps({"requirements": requirements, "checks": checks}).encode()
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/decisions.md").write_bytes(b"## Totals\n")
    artifacts = sources or [
        record(root, "ethos", "ethos", "ETHOS.md", ETHOS),
        record(root, "instruction", "task", "tasks/t/instruction.md", INSTRUCTION),
        record(root, "fixture", "task", "tasks/t/orders.json", FIXTURE),
        record(root, "guide", "task", "docs/guide.md", GUIDE),
        record(root, "license", "task", "LICENSE", INSTRUCTION),
    ]
    artifacts = artifacts + [record(root, "plan", "construction", ".eval-author/.working/construction.json", plan)]
    return check_authority(root, artifacts, {"plan_artifact_id": "plan"})


def run_authority_cli(root, authority):
    request = root / "selection.json"
    request.write_text(json.dumps({"artifacts": authority[0], "authority": authority[1]}))
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "skills/eval-author-rubric/scripts/measure.py"),
            "--root",
            str(root),
            "--selection",
            str(request),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stderr == ""
    return completed.returncode, json.loads(completed.stdout)


def observation_selection(root, *, pointer="/checks/total", content=b'{"checks":{"total":"FAIL"}}'):
    plan = {
        "requirements": [{"id": "r0", "authority": {"basis": "specification", "reference": "rules.md#L1"}}],
        "checks": [{"id": "total", "requirements": ["r0"], "success": {"pointer": pointer, "equals": "PASS"}}],
    }
    artifacts = [
        record(root, "plan", "construction", "plan.json", json.dumps(plan).encode()),
        record(root, "rules", "task", "rules.md", b"Use totals.\n"),
        record(root, "result", "result", "result.json", content),
    ]
    return artifacts, {"plan_artifact_id": "plan", "result_artifact_id": "result"}


@pytest.mark.parametrize("content", [b'{"checks":{"total":"FAIL"}}', b'{"checks":{"total":null}}'])
def test_cli_observes_emission_without_judging_expected_correctness(tmp_path, content):
    selection = observation_selection(tmp_path, content=content)
    _, report = run_authority_cli(tmp_path, selection)
    assert report.get("status") != "invalid", report
    result = report["measurements"]["authority"]
    assert result["result_artifact_id"] == "result"
    assert result["check_observation_scope"] == "emission_only"
    assert result["checks"] == [{"id": "total", "pointer": "/checks/total", "status": "observed"}]
    assert (result["status"], codes(result)) == ("pass", [])


@pytest.mark.parametrize("pointer", ["/checks/missing", "/checks/total/0", "/checks/01"])
def test_missing_emission_fails_through_cli(tmp_path, pointer):
    selection = observation_selection(tmp_path, pointer=pointer)
    exit_code, report = run_authority_cli(tmp_path, selection)
    result = report["measurements"]["authority"]
    assert exit_code == 1
    assert result["checks"] == [{"id": "total", "pointer": pointer, "status": "unobserved"}]
    assert (result["status"], codes(result)) == (
        "fail",
        [("authority.check_unobserved", "total", "fail")],
    )


@pytest.mark.parametrize("selected_result", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_unselected_result_or_legacy_success_is_unknown(tmp_path, selected_result, legacy):
    artifacts, selected = observation_selection(tmp_path)
    if not selected_result:
        selected.pop("result_artifact_id")
    if legacy:
        plan = json.loads((tmp_path / "plan.json").read_bytes())
        del plan["checks"][0]["success"]
        artifacts[0] = record(tmp_path, "plan", "construction", "plan.json", json.dumps(plan).encode())
    result = check_authority(tmp_path, artifacts, selected)
    unknown = legacy or not selected_result
    assert result["checks"] == [
        {
            "id": "total",
            "pointer": None if legacy else "/checks/total",
            "status": "unknown" if unknown else "observed",
        }
    ]
    assert result["status"] == ("unknown" if unknown else "pass")
    assert codes(result) == ([("authority.check_unknown", "total", "unknown")] if unknown else [])


@pytest.mark.parametrize(
    "success",
    [
        None,
        [],
        {},
        {"pointer": None},
        {"pointer": 3},
        {"pointer": "bad"},
        {"pointer": "/bad~2"},
        {"pointer": "/bad\u0000"},
    ],
)
def test_invalid_success_pointer_fails_selected_result(tmp_path, success):
    artifacts, selected = observation_selection(tmp_path)
    plan = json.loads((tmp_path / "plan.json").read_bytes())
    plan["checks"][0]["success"] = success
    artifacts[0] = record(tmp_path, "plan", "construction", "plan.json", json.dumps(plan).encode())
    result = check_authority(tmp_path, artifacts, selected)
    assert result["status"] == "fail"
    assert codes(result) == [("authority.check_pointer_invalid", "total", "fail")]
    assert result["checks"][0]["status"] == "unknown"


@pytest.mark.parametrize("content", [b"{bad", b'{"checks":0,"checks":1}', b'{"checks":NaN}', b"\xff", b"[" * 1200])
def test_malformed_result_fails_measurement_without_hiding_requirements(tmp_path, content):
    selection = observation_selection(tmp_path, content=content)
    _, report = run_authority_cli(tmp_path, selection)
    assert report.get("status") != "invalid", report
    result = report["measurements"]["authority"]
    assert result["status"] == "fail"
    assert ("authority.result_malformed", "result", "fail") in codes(result)
    assert statuses(result) == ["resolved"]
    assert result["checks"][0]["status"] == "unknown"


@pytest.mark.parametrize(
    "mutation,code,severity",
    [
        ("changed", "authority.result_changed", "fail"),
        ("symlink", "authority.result_invalid", "fail"),
        ("oversized", "authority.result_unreadable", "unknown"),
        ("denied", "authority.result_unreadable", "unknown"),
        ("missing", "authority.result_unreadable", "unknown"),
    ],
)
def test_result_read_boundary_and_digest_are_preserved(tmp_path, mutation, code, severity):
    artifacts, selected = observation_selection(tmp_path)
    path = tmp_path / "result.json"
    if mutation == "changed":
        artifacts[-1]["digest"] = "0" * 64
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to("plan.json")
    elif mutation == "oversized":
        path.write_bytes(b" " * 1_048_577)
    elif mutation == "denied":
        artifacts[-1]["access"] = "denied"
    else:
        path.unlink()
    result = check_authority(tmp_path, artifacts, selected)
    assert (code, "result", severity) in codes(result)
    assert result["status"] == severity
    assert result["checks"][0]["status"] == "unknown"


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("scorer.py", "implementation"),
        ("scorer.py#L1", "implementation"),
        ("scorer.py#L9", "broken"),
        ("scorer.py#/missing", "broken"),
    ],
)
def test_selected_scorer_is_implementation_not_requirement_authority(tmp_path, reference, expected):
    sources = [record(tmp_path, "scorer", "scorer", "scorer.py", b"assert total == 10\n")]
    result = measure(tmp_path, [("independent_reference", reference)], sources=sources)
    assert statuses(result) == [expected]
    assert result["status"] == "fail"
    assert ("authority.reference_" + expected, "r0", "fail") in codes(result)
    assert result["counts"]["by_status"][expected] == 1
    assert "question" not in result["requirements"][0]


@pytest.mark.parametrize(
    "mutation",
    ["wrong_role", "missing_id", "duplicate_id", "null_id", "list_id", "empty_id", "unknown_field", "missing_plan"],
)
def test_result_selection_fails_closed_through_cli(tmp_path, mutation):
    artifacts, selected = observation_selection(tmp_path)
    if mutation == "wrong_role":
        artifacts[-1]["role"] = "scorer"
    elif mutation == "duplicate_id":
        artifacts.append(dict(artifacts[-1]))
    elif mutation == "unknown_field":
        selected["result_pointer"] = "DO-NOT-ECHO"
    elif mutation == "missing_plan":
        selected.pop("plan_artifact_id")
    else:
        selected["result_artifact_id"] = {"missing_id": "DO-NOT-ECHO", "null_id": None, "list_id": [], "empty_id": ""}[
            mutation
        ]
    exit_code, report = run_authority_cli(tmp_path, (artifacts, selected))
    assert exit_code == 2
    assert report == {
        "schema": "rubric-measurements-v4",
        "status": "invalid",
        "code": "selection.invalid",
    }


@pytest.mark.parametrize(
    "pointer,content",
    [
        ("/checks/a~1b~0c/0", b'{"checks":{"a/b~c":[false]}}'),
        ("/0", b"[0]"),
    ],
)
def test_pointer_escapes_and_array_members_observe_falsey_values(tmp_path, pointer, content):
    artifacts, selected = observation_selection(tmp_path, pointer=pointer, content=content)
    result = check_authority(tmp_path, artifacts, selected)
    assert result["checks"] == [{"id": "total", "pointer": pointer, "status": "observed"}]
    assert result["status"] == "pass"


def test_duplicate_result_path_cannot_override_selected_digest(tmp_path):
    artifacts, selected = observation_selection(tmp_path)
    artifacts[-1]["digest"] = "0" * 64
    artifacts.append(
        {
            **artifacts[-1],
            "id": "alias",
            "role": "task",
            "digest": hashlib.sha256((tmp_path / "result.json").read_bytes()).hexdigest(),
        }
    )
    with pytest.raises(ValueError, match="invalid authority selection"):
        check_authority(tmp_path, artifacts, selected)


def statuses(result):
    return [row["status"] for row in result["requirements"]]


def codes(result):
    return sorted((row["code"], row["subject"], row["status"]) for row in result["diagnostics"])


@pytest.mark.parametrize(
    "reference",
    [
        "ETHOS.md#Purpose & Outcomes",
        "ETHOS.md#purpose--outcomes",
        "tasks/t/instruction.md#L1-L2",
        "tasks/t/orders.json#/orders/0/amount",
        "tasks/t/instruction.md",
        "./ETHOS.md#Constraints",
        "LICENSE#L2",
        "docs/guide.md#Totals",
        "docs/guide.md#totals-1",
        "docs/guide.md#See the rules",
        "docs/guide.md#see-the-rules",
        "docs/guide.md#Use round here",
        "docs/guide.md#use-round-here",
        "docs/guide.md#L16",
    ],
)
def test_locators_naming_selected_content_resolve(tmp_path, reference):
    result = measure(tmp_path, [("specification", reference)])
    assert (result["status"], statuses(result)) == ("unknown", ["resolved"])


@pytest.mark.parametrize(
    "reference",
    [
        "ETHOS.md#Scope",
        "ETHSO.md#Constraints",
        "tasks/t/instruction.md#L2-L3",
        "tasks/t/orders.json#/orders/1",
        "docs/guide.md#Install deps",
        "docs/guide.md#title: Guide",
        "docs/guide.md#totals-2",
        "docs/guide.md#L17",
    ],
)
def test_missing_targets_are_broken_without_affecting_neighbors(tmp_path, reference):
    result = measure(tmp_path, [("specification", reference), ("specification", "ETHOS.md#Constraints")])
    assert statuses(result) == ["broken", "resolved"]
    assert codes(result) == [("authority.check_unknown", "c", "unknown"), ("authority.reference_broken", "r0", "fail")]


@pytest.mark.parametrize(
    ("basis", "reference", "status", "code"),
    [
        ("specification", "Reviewed total rule", "prose", "authority.reference_prose"),
        ("user_decision", "docs/decisions.md#Totals", "unselected", "authority.reference_unselected"),
        ("unresolved", "ETHOS.md#Constraints", "unresolved", "authority.unresolved"),
    ],
)
def test_uncheckable_references_stay_unknown(tmp_path, basis, reference, status, code):
    result = measure(tmp_path, [(basis, reference)])
    assert (result["status"], statuses(result)) == ("unknown", [status])
    assert result["requirements"][0]["question"] == {
        "requirement_id": "r0",
        "reference": reference,
        "decision_needed": {
            "prose": "Decision needed: replace the prose reference with a checkable authority locator.",
            "unresolved": "Decision needed: identify the authority that establishes this requirement.",
            "unselected": "Decision needed: select the cited authority artifact or correct the reference.",
        }[status],
    }
    assert codes(result) == [("authority.check_unknown", "c", "unknown"), (code, "r0", "unknown")]


def test_changed_source_bytes_fail_the_reference(tmp_path):
    sources = [record(tmp_path, "ethos", "ethos", "ETHOS.md", ETHOS, digest="0" * 64)]
    result = measure(tmp_path, [("specification", "ETHOS.md#Constraints")], sources=sources)
    assert codes(result) == [("authority.check_unknown", "c", "unknown"), ("authority.reference_changed", "r0", "fail")]


def test_symlinked_source_fails_the_reference(tmp_path):
    sources = [record(tmp_path, "ethos", "ethos", "ETHOS.md", ETHOS)]
    (tmp_path / "linked.md").symlink_to("ETHOS.md")
    sources.append({**sources[0], "id": "linked", "path": "linked.md"})
    result = measure(tmp_path, [("specification", "linked.md#Constraints")], sources=sources)
    assert codes(result) == [("authority.check_unknown", "c", "unknown"), ("authority.reference_invalid", "r0", "fail")]


def test_mapping_gaps_and_counts(tmp_path):
    references = [("specification", "ETHOS.md#Constraints"), ("specification", "Input rule")]
    result = measure(tmp_path, references, checks=[{"id": "c", "requirements": ["r0", "missing"]}])
    assert codes(result) == [
        ("authority.check_dangling", "c", "fail"),
        ("authority.check_unknown", "c", "unknown"),
        ("authority.reference_prose", "r1", "unknown"),
        ("authority.requirement_unmapped", "r1", "unknown"),
    ]
    counts = result["counts"]
    assert (counts["requirements"], counts["checks"], counts["mapped"]) == (2, 1, 1)
    assert counts["by_status"]["resolved"] == counts["by_status"]["prose"] == 1


@pytest.mark.parametrize(
    "checks",
    [
        [{"id": "c", "requirements": "r0"}],
        [{"id": "c", "requirements": [["r0"]]}],
        [{"id": "c", "requirements": [{"id": "r0"}]}],
    ],
)
def test_malformed_plan_fails_and_wrong_role_is_rejected(tmp_path, checks):
    requirements = [{"id": "r0", "authority": {"basis": "unresolved", "reference": "x"}}]
    content = json.dumps({"requirements": requirements, "checks": checks}).encode()
    plan = record(tmp_path, "plan", "construction", "plan.json", content)
    result = check_authority(tmp_path, [plan], {"plan_artifact_id": "plan"})
    assert codes(result) == [("authority.plan_malformed", "plan", "fail")]
    with pytest.raises(ValueError):
        check_authority(tmp_path, [{**plan, "role": "task"}], {"plan_artifact_id": "plan"})
