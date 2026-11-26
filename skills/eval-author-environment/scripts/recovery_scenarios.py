# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import random
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

ENVIRONMENT_VERSION = "recovery-environment-v1"
TASK_VERSION = "recovery-totals-v1"
TASK_IDS = ("invoice-total", "stock-total")
CATEGORIES = (
    "timeout",
    "rate-limit",
    "partial-result",
    "stale-result",
    "prerequisite-dependency",
    "distinguishable-distractor",
)


def _integer(value: object, name: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    severity: int
    seed: int
    trigger: dict[str, Any]
    schedule: tuple[int, ...]
    expected_behavior: str
    failure_signature: str
    provenance: str = "synthetic"
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, "id")
        if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.id) is None:
            raise ValueError("id must be a lowercase scenario identifier")
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category!r}")
        if _integer(self.severity, "severity", 1) > 3:
            raise ValueError("severity must be between 1 and 3")
        _integer(self.seed, "seed")
        if not isinstance(self.trigger, dict) or set(self.trigger) != {"tool"}:
            raise ValueError("trigger must contain only tool")
        allowed = (
            {"search_records", "read_record"} if self.category in {"timeout", "rate-limit"} else {"search_records"}
        )
        if not isinstance(self.trigger["tool"], str) or self.trigger["tool"] not in allowed:
            raise ValueError(f"trigger.tool must be one of {sorted(allowed)}")
        if type(self.schedule) is not tuple or not self.schedule:
            raise ValueError("schedule must be a nonempty tuple")
        for ordinal in self.schedule:
            _integer(ordinal, "schedule ordinal", 1)
        if tuple(sorted(set(self.schedule))) != self.schedule:
            raise ValueError("schedule ordinals must be unique and increasing")
        _text(self.expected_behavior, "expected_behavior")
        _text(self.failure_signature, "failure_signature")
        if self.provenance not in {"synthetic", "trace-observed"}:
            raise ValueError("unknown provenance")
        if type(self.source_refs) is not tuple:
            raise ValueError("source_refs must be a tuple")
        for source_ref in self.source_refs:
            _text(source_ref, "source_refs entry")
        if self.provenance == "trace-observed" and not self.source_refs:
            raise ValueError("trace-observed scenarios require source_refs")
        object.__setattr__(self, "trigger", copy.deepcopy(self.trigger))

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schedule"] = list(self.schedule)
        result["source_refs"] = list(self.source_refs)
        return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_catalog(path: Path) -> list[Scenario]:
    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(raw, list) or not raw:
        raise ValueError("catalog must be a nonempty list")
    allowed = {field.name for field in fields(Scenario)}
    required = allowed - {"provenance", "source_refs"}
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("scenario must be an object")
        if set(item) - allowed or required - set(item):
            raise ValueError("scenario has unknown or missing fields")
        values = dict(item)
        for key in ("schedule", "source_refs"):
            if key in values:
                if not isinstance(values[key], list):
                    raise ValueError(f"{key} must be a JSON array")
                values[key] = tuple(values[key])
        scenario = Scenario(**values)
        if scenario.id in seen:
            raise ValueError(f"duplicate scenario id: {scenario.id}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    return scenarios


@dataclass(frozen=True)
class Record:
    id: str
    account: str
    eligible: bool
    value: int
    version: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _schema(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


class Environment:
    def __init__(self, task_id: str, scenario: Scenario | None, seed: int) -> None:
        if task_id not in TASK_IDS:
            raise ValueError(f"unknown task_id: {task_id!r}")
        _integer(seed, "seed")
        if scenario is not None and not isinstance(scenario, Scenario):
            raise ValueError("scenario must be a Scenario or None")
        self.task_id = task_id
        self.seed = seed
        # Copy the mutable trigger so external configuration edits cannot alter a run.
        self.scenario = copy.deepcopy(scenario)
        if self.scenario is not None:
            self.scenario.__post_init__()
        self.events: list[dict[str, Any]] = []
        self._call_index = 0
        self._tick = 0
        self._ready_tick = 0
        self._remaining_faults = 0
        self._active = False
        self._submission: dict[str, int] | None = None
        self._account = "account-104" if task_id == "invoice-total" else "location-104"
        self._prepare_remaining = 0
        rng = random.Random(f"{TASK_VERSION}:{task_id}:{seed}")
        maximum = 900 if task_id == "invoice-total" else 90
        records = [
            Record(f"record-{index + 1}", self._account, True, rng.randint(10, maximum), 2) for index in range(4)
        ]

        if scenario and scenario.category == "distinguishable-distractor":
            distractor_rng = random.Random(f"distractors:{scenario.seed}:{seed}")
            for index in range(scenario.severity * 2):
                records.append(
                    Record(
                        f"distractor-{index + 1}",
                        self._account + "-archive" if index % 2 == 0 else self._account,
                        index % 2 == 0,
                        distractor_rng.randint(10, maximum),
                        2,
                    )
                )
        self._source_records = tuple(records)
        self._by_id = {record.id: record for record in records}
        self._stale_ids: set[str] = set()
        if scenario and scenario.category == "stale-result":
            stale_rng = random.Random(f"stale:{scenario.seed}:{seed}")
            self._stale_ids = set(stale_rng.sample([record.id for record in records[:4]], scenario.severity))
        value_description = "invoice amounts in whole dollars" if task_id == "invoice-total" else "stock quantities"
        self.instruction = (
            f"Submit the sum of {value_description} for account {self._account}. "
            "Include only records whose account matches exactly and eligible is true. "
            "Search provides continuation and cache metadata. read_record is authoritative. "
            "The service reports whether submission is prepared and how many preparation steps remain. "
            "Use the available operations to submit the correct integer total. "
            "Transient timeout and rate-limit responses are simulated, not real latency or provider faults. "
            "For rate limits, use wait with the reported retry_after_ticks before retrying."
        )
        self.tools = [
            _schema(
                "search_records",
                "Discover records. Start at page 1 and follow next_page until complete.",
                {"page": {"type": "integer", "minimum": 1}},
            ),
            _schema(
                "read_record",
                "Read the current authoritative record and version.",
                {"id": {"type": "string", "minLength": 1}},
            ),
            _schema(
                "prepare", "Prepare submission. Repeat until prepared is true; required_steps gives remaining work.", {}
            ),
            _schema(
                "submit",
                "Store the integer total after preparation. This operation does not verify the total.",
                {"total": {"type": "integer"}},
            ),
            _schema(
                "wait",
                "Advance simulated logical time without sleeping. Use retry_after_ticks from a rate limit.",
                {"ticks": {"type": "integer", "minimum": 1, "maximum": 24}},
            ),
        ]

    def _error(self, code: str, **details: Any) -> dict[str, Any]:
        return {"ok": False, "error": {"code": code, **details}}

    def _validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        expected = {
            "search_records": {"page"},
            "read_record": {"id"},
            "prepare": set(),
            "submit": {"total"},
            "wait": {"ticks"},
        }
        if name not in expected:
            raise ValueError("unknown tool")
        if not isinstance(arguments, dict) or set(arguments) != expected[name]:
            raise ValueError("unknown or missing arguments")
        if name == "search_records":
            _integer(arguments["page"], "page", 1)
        elif name == "read_record":
            _text(arguments["id"], "id")
        elif name == "submit":
            _integer(arguments["total"], "total")
        elif name == "wait" and _integer(arguments["ticks"], "ticks", 1) > 24:
            raise ValueError("ticks must be at most 24")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._call_index += 1
        applied = False
        failure: str | None = None
        try:
            self._validate_arguments(name, arguments)
        except (ValueError, TypeError):
            response = self._error(
                "invalid_arguments"
                if isinstance(name, str) and name in {"search_records", "read_record", "prepare", "submit", "wait"}
                else "unknown_tool"
            )
        else:
            scenario = self.scenario
            if scenario and name == scenario.trigger["tool"] and self._call_index in scenario.schedule:
                self._active = True
                applied = True
                if scenario.category in {"timeout", "rate-limit"}:
                    self._remaining_faults += scenario.severity
                elif scenario.category == "prerequisite-dependency":
                    self._prepare_remaining = scenario.severity
            category = scenario.category if scenario else None
            if (
                scenario
                and category == "rate-limit"
                and name == scenario.trigger["tool"]
                and self._tick < self._ready_tick
            ):
                response = self._error("rate_limit", retry_after_ticks=self._ready_tick - self._tick)
                failure = "premature_retry"
                applied = True
            elif self._remaining_faults and scenario and name == scenario.trigger["tool"]:
                self._remaining_faults -= 1
                applied = True
                if category == "timeout":
                    response = self._error("timeout")
                else:
                    self._ready_tick = self._tick + scenario.severity
                    response = self._error("rate_limit", retry_after_ticks=scenario.severity)
            else:
                dependency_pending = self._prepare_remaining > 0
                response = self._dispatch(name, arguments)
                if (
                    name == "search_records"
                    and self._active
                    and category in {"partial-result", "stale-result", "distinguishable-distractor"}
                ):
                    applied = True
                if category == "prerequisite-dependency" and name in {"prepare", "submit"} and dependency_pending:
                    applied = True
                if name == "submit" and not response["ok"]:
                    failure = "submitted_before_prerequisites"
                    applied = category == "prerequisite-dependency"
        event: dict[str, Any] = {
            "tool": name,
            "arguments": copy.deepcopy(arguments),
            "response": copy.deepcopy(response),
            "call_index": self._call_index,
            "perturbation_applied": applied,
            "response_delivered": False,
            "logical_tick": self._tick,
        }
        if failure is not None:
            event["failure_signature"] = failure
        self.events.append(event)
        return copy.deepcopy(response)

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "wait":
            self._tick += arguments["ticks"]
            return {"ok": True, "logical_tick": self._tick}
        if name == "prepare":
            self._prepare_remaining = max(0, self._prepare_remaining - 1)
            return {"ok": True, "prepared": self._prepare_remaining == 0, "required_steps": self._prepare_remaining}
        if name == "submit":
            if self._prepare_remaining:
                return self._error("prepare_required", required_steps=self._prepare_remaining)
            self._submission = {"total": arguments["total"]}
            return {"ok": True, "submitted": True}
        if name == "read_record":
            record = self._by_id.get(arguments["id"])
            return {"ok": True, "record": record.as_dict()} if record else self._error("record_not_found")
        page = arguments["page"]
        scenario = self.scenario
        category = scenario.category if scenario and self._active else None
        visible = [
            record
            for record in self._source_records
            if category == "distinguishable-distractor" or not record.id.startswith("distractor-")
        ]
        width = 4 - scenario.severity if category == "partial-result" and scenario else len(visible)
        pages = (len(visible) + width - 1) // width
        if page > pages:
            return self._error("page_out_of_range")
        rows = [record.as_dict() for record in visible[(page - 1) * width : page * width]]
        if category == "stale-result":
            for row in rows:
                if row["id"] in self._stale_ids:
                    row["value"] += 7
                    row["version"] = 1
        return {
            "ok": True,
            "records": rows,
            "request": {"account": self._account},
            "complete": page == pages,
            "next_page": page + 1 if page < pages else None,
            "cache_status": "stale" if category == "stale-result" else "current",
            "authoritative_version": 2,
            "prepared": self._prepare_remaining == 0,
            "dependencies_remaining": self._prepare_remaining,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_version": TASK_VERSION,
            "environment_version": ENVIRONMENT_VERSION,
            "seed": self.seed,
            "scenario": self.scenario.as_dict() if self.scenario else None,
            "request": {"account": self._account},
            "source_records": [record.as_dict() for record in self._source_records],
            "submission": copy.deepcopy(self._submission),
            "prepared": self._prepare_remaining == 0,
            "dependencies_remaining": self._prepare_remaining,
            "logical_tick": self._tick,
            "events": copy.deepcopy(self.events),
        }
