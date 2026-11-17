# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scenario inputs preserve user knowledge without accepting execution configuration."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
RETAIL = ROOT / "tests/fixtures/user-sim/retail-return.yaml"


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("user_sim")


def minimal() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "return-headphones",
        "goal": "Return my headphones.",
        "start_prompt": "I'd like to return something.",
    }


def scenario_file(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "user-sim.yaml"
    path.write_text(
        "schema_version: 1\ntask_id: return-headphones\ngoal: Return my headphones.\n"
        "start_prompt: I'd like to return something.\n" + extra,
        encoding="utf-8",
    )
    return path


def run_cli(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "user_sim.py"), "validate", "--path", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_minimal_scenario_uses_neutral_defaults(helper: ModuleType) -> None:
    source = minimal()
    scenario = helper.validate_scenario(source)
    assert scenario.user_type == "neutral"
    assert scenario.user_facts == {}
    assert scenario.disclosure_rules == ()
    assert scenario.stopping_conditions == ()
    assert scenario.conversation_prompt is None
    assert source == minimal()
    assert helper.validate_scenario(scenario.to_dict()) == scenario


def test_retail_fixture_preserves_private_facts_and_adaptive_rules(helper: ModuleType) -> None:
    scenario = helper.load_scenario(RETAIL)
    assert scenario.user_facts["order_id"] == "order_2084"
    assert "order_2084" not in scenario.start_prompt
    assert "when the agent asks" in scenario.disclosure_rules[0]
    assert "Ask for correction" in scenario.conversation_prompt
    assert "reports" in scenario.stopping_conditions[0]


def test_facts_are_copied_without_coercing_values(helper: ModuleType) -> None:
    nested = {"items": [{"quantity": 2, "confirmed": False, "cost": 19.5, "date": "2026-11-17"}], "unknown": None}
    scenario = helper.validate_scenario({**minimal(), "user_facts": nested})
    assert scenario.user_facts == nested
    nested["items"][0]["quantity"] = 3
    assert scenario.user_facts["items"][0]["quantity"] == 2


@pytest.mark.parametrize("field", ["schema_version", "task_id", "goal", "start_prompt"])
def test_missing_required_field_is_rejected(helper: ModuleType, field: str) -> None:
    value = minimal()
    value.pop(field)
    with pytest.raises(helper.UserSimError, match=field):
        helper.validate_scenario(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("schema_version", 2),
        ("task_id", " "),
        ("goal", None),
        ("start_prompt", []),
        ("user_type", "impatient"),
        ("user_type", None),
        ("user_facts", []),
        ("user_facts", None),
        ("disclosure_rules", "Only when asked"),
        ("disclosure_rules", [""]),
        ("stopping_conditions", [42]),
        ("conversation_prompt", " "),
        ("conversation_prompt", None),
    ],
)
def test_field_types_are_explicit(helper: ModuleType, field: str, value: object) -> None:
    with pytest.raises(helper.UserSimError, match=field):
        helper.validate_scenario({**minimal(), field: value})


@pytest.mark.parametrize("field", ["model", "provider", "max_turns", "persona", "verifier", "variant_id", "extra"])
def test_unsupported_fields_are_rejected(helper: ModuleType, field: str) -> None:
    with pytest.raises(helper.UserSimError, match="unsupported top-level"):
        helper.validate_scenario({**minimal(), field: "not part of the scenario"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), date(2026, 11, 17), b"bytes", {"set"}])
def test_non_json_fact_values_are_rejected(helper: ModuleType, value: object) -> None:
    with pytest.raises(helper.UserSimError, match="user_facts"):
        helper.validate_scenario({**minimal(), "user_facts": {"nested": [value]}})


@pytest.mark.parametrize("extra", ["goal: Another goal\n", "user_facts:\n  order_id: first\n  order_id: second\n"])
def test_duplicate_keys_are_rejected(helper: ModuleType, tmp_path: Path, extra: str) -> None:
    with pytest.raises(helper.UserSimError, match="Duplicate mapping key"):
        helper.load_scenario(scenario_file(tmp_path, extra))


@pytest.mark.parametrize(
    "extra",
    [
        "user_facts: !!python/object/apply:os.system ['echo should-not-run']\n",
        "user_facts: {created: 2026-11-17}\n",
        "user_facts: {cost: .nan}\n",
        "user_facts: {1: number-key}\n",
        "user_facts: {yes: boolean-key}\n",
        "user_facts: {quantity: !!int not-a-number}\n",
        "user_facts: {created: !!timestamp 2026-99-99}\n",
        "user_facts: {<<: {order_id: hidden}}\n",
        "---\nschema_version: 1\n",
    ],
)
def test_unsupported_yaml_forms_are_rejected(helper: ModuleType, tmp_path: Path, extra: str) -> None:
    with pytest.raises(helper.UserSimError):
        helper.load_scenario(scenario_file(tmp_path, extra))


@pytest.mark.parametrize("facts", ["&facts {recursive: *facts}", "{items: &items [*items]}"])
def test_recursive_aliases_are_rejected(helper: ModuleType, tmp_path: Path, facts: str) -> None:
    with pytest.raises(helper.UserSimError, match="Recursive aliases"):
        helper.load_scenario(scenario_file(tmp_path, f"user_facts: {facts}\n"))


def test_nonrecursive_aliases_are_copied(helper: ModuleType, tmp_path: Path) -> None:
    scenario = helper.load_scenario(
        scenario_file(tmp_path, "user_facts: {first: &item {name: headphones}, second: *item}\n")
    )
    assert scenario.user_facts["first"] == scenario.user_facts["second"] == {"name": "headphones"}
    assert scenario.user_facts["first"] is not scenario.user_facts["second"]


def test_expanded_aliases_are_bounded(helper: ModuleType, tmp_path: Path) -> None:
    rows = ["user_facts:\n  a: &a [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]\n"]
    for current, previous in zip("bcde", "abcd", strict=True):
        rows.append(f"  {current}: &{current} [" + ", ".join([f"*{previous}"] * 10) + "]\n")
    with pytest.raises(helper.UserSimError, match="value limit"):
        helper.load_scenario(scenario_file(tmp_path, "".join(rows)))


def test_excessive_nesting_is_rejected(helper: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(helper.UserSimError, match="nesting"):
        helper.load_scenario(scenario_file(tmp_path, "user_facts: {items: " + "[" * 80 + "0" + "]" * 80 + "}\n"))


def test_oversized_file_is_rejected(helper: ModuleType, tmp_path: Path) -> None:
    path = scenario_file(tmp_path, "#" * helper.MAX_SCENARIO_BYTES)
    with pytest.raises(helper.UserSimError, match="exceeds"):
        helper.load_scenario(path)


def test_cli_reports_identity_without_disclosing_facts() -> None:
    result = run_cli(RETAIL)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"valid": True, "schema_version": 1, "task_id": "return-headphones"}
    assert result.stderr == ""
    assert "order_2084" not in result.stdout


def test_cli_failure_does_not_echo_yaml_source(tmp_path: Path) -> None:
    path = scenario_file(tmp_path, "user_facts: [PRIVATE-CUSTOMER-DETAIL\n")
    result = run_cli(path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["valid"] is False
    assert "line" in json.loads(result.stderr)["error"]
    assert "PRIVATE-CUSTOMER-DETAIL" not in result.stderr


@pytest.mark.parametrize("tag", ["int", "bool", "timestamp"])
def test_cli_scalar_conversion_failure_does_not_echo_facts(tmp_path: Path, tag: str) -> None:
    path = scenario_file(tmp_path, f"user_facts: {{quantity: !!{tag} PRIVATE-CUSTOMER-DETAIL}}\n")
    result = run_cli(path)
    assert result.returncode == 2
    assert json.loads(result.stderr)["valid"] is False
    assert "private-customer-detail" not in result.stderr.lower()


def test_cli_reports_missing_file(tmp_path: Path) -> None:
    result = run_cli(tmp_path / "missing.yaml")
    assert result.returncode == 2
    assert json.loads(result.stderr)["valid"] is False


def test_invalid_utf8_is_rejected(helper: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "user-sim.yaml"
    path.write_bytes(b"\xff")
    with pytest.raises(helper.UserSimError, match="UTF-8"):
        helper.load_scenario(path)
