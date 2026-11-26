# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from metric_benchmark import recovery

CATALOG = Path(__file__).resolve().parents[1] / "skills/eval-author-environment/scenarios/recovery.json"


@pytest.fixture(scope="module")
def scenarios() -> dict[str, recovery.Scenario]:
    return {scenario.id: scenario for scenario in recovery.load_catalog(CATALOG)}


@pytest.mark.parametrize("severity", [1, 2, 3])
def test_dependency_waits_for_scheduled_search(scenarios: dict[str, recovery.Scenario], severity: int) -> None:
    scenario = replace(scenarios["prerequisite-dependency"], severity=severity, schedule=(4,))
    environment = recovery.Environment("invoice-total", scenario, 17)
    initial = environment.snapshot()
    assert initial["prepared"] is True
    assert initial["dependencies_remaining"] == 0
    search = environment.call("search_records", {"page": 1})
    assert search["prepared"] is True
    assert search["dependencies_remaining"] == 0
    assert environment.call("submit", {"total": 1262}) == {"ok": True, "submitted": True}
    assert environment.call("prepare", {}) == {"ok": True, "prepared": True, "required_steps": 0}
    assert all(not event["perturbation_applied"] for event in environment.events)

    search = environment.call("search_records", {"page": 1})
    assert search["prepared"] is False
    assert search["dependencies_remaining"] == severity
    assert environment.events[-1]["perturbation_applied"] is True
    assert environment.call("submit", {"total": 1262}) == {
        "ok": False,
        "error": {"code": "prepare_required", "required_steps": severity},
    }
    assert environment.events[-1]["failure_signature"] == "submitted_before_prerequisites"


@pytest.mark.parametrize("schedule", [(1,), (1, 7)])
def test_dependency_resets_only_at_explicit_later_schedule(
    scenarios: dict[str, recovery.Scenario], schedule: tuple[int, ...]
) -> None:
    scenario = replace(scenarios["prerequisite-dependency"], severity=2, schedule=schedule)
    environment = recovery.Environment("invoice-total", scenario, 17)
    assert environment.call("search_records", {"page": 1})["dependencies_remaining"] == 2
    assert environment.call("prepare", {}) == {"ok": True, "prepared": False, "required_steps": 1}
    assert environment.call("search_records", {"page": 1})["dependencies_remaining"] == 1
    assert environment.call("prepare", {}) == {"ok": True, "prepared": True, "required_steps": 0}
    assert environment.call("submit", {"total": 1262})["ok"] is True
    assert environment.events[-1]["perturbation_applied"] is False
    assert environment.call("search_records", {"page": 1})["prepared"] is True
    later = environment.call("search_records", {"page": 1})
    scheduled_again = schedule == (1, 7)
    assert later["dependencies_remaining"] == (2 if scheduled_again else 0)
    assert later["prepared"] is not scheduled_again
    assert environment.events[-1]["perturbation_applied"] is scheduled_again


def test_dependency_does_not_fire_when_scheduled_call_uses_another_tool(
    scenarios: dict[str, recovery.Scenario],
) -> None:
    environment = recovery.Environment("invoice-total", scenarios["prerequisite-dependency"], 17)
    assert environment.call("prepare", {}) == {"ok": True, "prepared": True, "required_steps": 0}
    assert environment.call("search_records", {"page": 1})["prepared"] is True
    assert environment.call("submit", {"total": 1262})["ok"] is True
    assert all(not event["perturbation_applied"] for event in environment.events)


@pytest.mark.parametrize("task", ["invoice-total", "stock-total"])
@pytest.mark.parametrize("severity,visible_count", [(1, 6), (2, 8), (3, 10)])
def test_distractors_preserve_baseline_pagination(
    scenarios: dict[str, recovery.Scenario], task: str, severity: int, visible_count: int
) -> None:
    scenario = replace(scenarios["distinguishable-distractor"], severity=severity, schedule=(2,))
    baseline = recovery.Environment(task, None, 17).call("search_records", {"page": 1})
    environment = recovery.Environment(task, scenario, 17)
    assert environment.call("search_records", {"page": 1}) == baseline
    assert environment.events[-1]["perturbation_applied"] is False

    result = environment.call("search_records", {"page": 1})
    assert result["complete"] is baseline["complete"] is True
    assert result["next_page"] is baseline["next_page"] is None
    assert len(result["records"]) == visible_count
    assert result["records"][:4] == baseline["records"]
    assert all(row["account"] != result["request"]["account"] or not row["eligible"] for row in result["records"][4:])
    assert environment.events[-1]["perturbation_applied"] is True
    assert environment.call("search_records", {"page": 1}) == result
    assert environment.call("search_records", {"page": 2}) == {
        "ok": False,
        "error": {"code": "page_out_of_range"},
    }
