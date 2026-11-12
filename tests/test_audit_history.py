# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-audit/scripts/audit_spec"


@pytest.fixture
def history(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("history", None)
    return importlib.import_module("history")


def kind(items, covered, basis="demonstrated", criteria="same"):
    return {"items": items, "covered": covered, "basis": basis, "criteria": criteria}


def make(history, audit_id, *, agent="support", at="2026-11-01T10:00:00+00:00", capability=None):
    kinds = {name: kind([], 0) for name in history.KINDS}
    kinds["capability"] = capability or kind(["a", "b"], 1)
    return {
        "version": history.VERSION,
        "audit_id": audit_id,
        "agent": agent,
        "recorded_at": at,
        "commit": "abc1234",
        "dirty": False,
        "content_sha256": audit_id,
        "report_sha256": "r" + audit_id,
        "runs": [],
        "kinds": kinds,
    }


def test_trend_arithmetic_uses_percentage_points_and_relative_counts(history):
    before = kind(list("abcdefghijklmnop"), 8)
    after = kind(list("abcdefghijklmnopqr"), 12)
    result = history.compare(after, before)
    assert result["status"] == "scope" and result["added"] == ["q", "r"]
    assert result["total_change"] == 2 and result["total_relative"] == pytest.approx(0.125)
    assert result["covered_change"] == 4 and result["covered_relative"] == pytest.approx(0.5)
    assert result["points"] == pytest.approx(16.666, abs=0.01)


def test_unchanged_scope_and_method_is_comparable(history):
    result = history.compare(kind(["a", "b"], 2), kind(["a", "b"], 1))
    assert result["status"] == "comparable" and result["covered_change"] == 1
    assert result["points"] == pytest.approx(50.0)


def test_method_change_wins_over_scope_change(history):
    result = history.compare(kind(["a"], 1), kind(["a", "b"], 1, "mapped"))
    assert result["status"] == "method"


def test_zero_denominators_have_no_ratio_or_relative_change(history):
    result = history.compare(kind(["a"], 0), kind([], 0))
    assert result["total_relative"] is None and result["covered_relative"] is None and result["points"] is None
    assert history.ratio(0, 0) is None


def test_summary_uses_runs_once_measured_and_mappings_before(history):
    items = [{"name": "a", "kind": "capability"}, {"name": "b", "kind": "capability"}, {"name": "t", "kind": "tool"}]
    kinds = history.summarize(items, covered={"a"}, measured={"capability"}, mapped={"a", "b", "t"})
    assert {key: kinds["capability"][key] for key in ("items", "covered", "basis")} == {
        "items": ["a", "b"],
        "covered": 1,
        "basis": "demonstrated",
    }
    assert (kinds["tool"]["items"], kinds["tool"]["covered"], kinds["tool"]["basis"]) == (["t"], 1, "mapped")
    assert (kinds["failure_case"]["items"], kinds["failure_case"]["covered"]) == ([], 0)


def test_saving_the_same_audit_twice_keeps_the_first_snapshot(history, tmp_path):
    first = make(history, "one")
    assert history.save(tmp_path, first)
    assert not history.save(tmp_path, {**first, "recorded_at": "2026-12-01T00:00:00+00:00"})
    assert history.load(tmp_path) == [first]


def test_unreadable_or_foreign_files_are_skipped(history, tmp_path):
    history.save(tmp_path, make(history, "one"))
    folder = tmp_path / history.HISTORY_DIR
    (folder / "broken.json").write_text("{")
    (folder / "old.json").write_text('{"version": 0}')
    assert [row["audit_id"] for row in history.load(tmp_path)] == ["one"]


def test_previous_matches_the_agent_and_skips_the_same_audit(history):
    rows = [
        make(history, "old", at="2026-10-01T00:00:00+00:00"),
        make(history, "newer", at="2026-10-15T00:00:00+00:00"),
        make(history, "other", agent="billing", at="2026-10-20T00:00:00+00:00"),
        make(history, "current", at="2026-11-01T00:00:00+00:00"),
    ]
    assert history.previous(rows, "support", "current")["audit_id"] == "newer"
    assert history.previous(rows[:1], "support", "old") is None


def test_snapshot_dates_come_from_the_recorded_time(history):
    value = history.snapshot(
        agent="support",
        kinds={},
        runs=["r2", "r1"],
        identity={"commit": "abc", "dirty": True},
        content_sha256="c",
        report_sha256="r",
        now=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
    )
    assert value["recorded_at"] == "2026-11-09T12:00:00+00:00" and value["runs"] == ["r1", "r2"]
    assert value["audit_id"] == "c" and value["dirty"] is True


def item(name, **fields):
    return {"name": name, "kind": "capability", "expected_behavior": "one source", **fields}


def test_changed_criteria_with_the_same_names_are_not_comparable(history):
    before = history.summarize([item("a")], {"a"}, {"capability"}, set())["capability"]
    after = history.summarize([item("a", expected_behavior="two independent sources")], {"a"}, {"capability"}, set())[
        "capability"
    ]
    result = history.compare(after, before)
    assert result["status"] == "scope" and result["added"] == result["removed"] == []


def test_provenance_notes_do_not_change_the_criteria(history):
    before = history.summarize([item("a")], set(), set(), set())["capability"]
    after = history.summarize([item("a", source_refs=["README.md"])], set(), set(), set())["capability"]
    assert history.compare(after, before)["status"] == "comparable"


@pytest.mark.parametrize(
    "damage",
    [
        lambda row: row.pop("commit"),
        lambda row: row.pop("runs"),
        lambda row: row.update(runs="r1"),
        lambda row: row.update(dirty="no"),
        lambda row: row["kinds"]["tool"].update(covered="2"),
        lambda row: row["kinds"]["tool"].update(basis="guessed"),
        lambda row: row["kinds"]["tool"].pop("criteria"),
    ],
)
def test_snapshots_missing_consumed_fields_are_skipped(history, tmp_path, damage):
    history.save(tmp_path, make(history, "one"))
    broken = make(history, "two")
    damage(broken)
    (tmp_path / history.HISTORY_DIR / "two.json").write_text(json.dumps(broken))
    assert [row["audit_id"] for row in history.load(tmp_path)] == ["one"]


def test_a_retained_audit_keeps_its_original_baseline(history):
    rows = [
        make(history, "a", at="2026-10-01T00:00:00+00:00"),
        make(history, "b", at="2026-10-01T00:00:01+00:00"),
    ]
    assert history.previous(rows, "support", "a") is None
    assert history.previous(rows, "support", "b")["audit_id"] == "a"
    assert history.previous(rows, "support", "new")["audit_id"] == "b"


def test_snapshot_counts_each_run_once(history):
    value = history.snapshot(
        agent="support",
        kinds={},
        runs=["same-run", "same-run"],
        identity={"commit": "abc", "dirty": False},
        content_sha256="c",
        report_sha256="r",
        now=datetime(2026, 11, 9, tzinfo=timezone.utc),
    )
    assert value["runs"] == ["same-run"]


def test_a_fresh_audit_setting_aside_the_audit_folder_keeps_the_snapshots(history, tmp_path):
    history.save(tmp_path, make(history, "one"))
    working = tmp_path / ".eval-author/.working"
    (working / "audit").mkdir(parents=True)
    (working / "audit/report-content.json").write_text("{}")
    working.joinpath("audit").rename(working / "audit-before-fresh-audit")
    assert [row["audit_id"] for row in history.load(tmp_path)] == ["one"]


def test_snapshots_saved_in_the_earlier_location_are_still_read(history, tmp_path):
    legacy = tmp_path / history.LEGACY_HISTORY_DIR
    legacy.mkdir(parents=True)
    (legacy / "old.json").write_text(json.dumps(make(history, "old", at="2026-10-01T00:00:00+00:00")))
    history.save(tmp_path, make(history, "new", at="2026-11-01T00:00:00+00:00"))
    rows = history.load(tmp_path)
    assert sorted(row["audit_id"] for row in rows) == ["new", "old"]
    assert history.previous(rows, "support", "new")["audit_id"] == "old"
    assert not history.save(tmp_path, make(history, "old"))
