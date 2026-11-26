# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paired synthetic environment trials and evidence-bound difficulty rows."""

from __future__ import annotations

import argparse
import copy
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from metric_benchmark.io import digest_file, json_bytes, read_json, write_json

ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENT = ROOT / "skills/eval-author-environment"
sys.path.insert(0, str(ENVIRONMENT / "scripts"))

from recovery_scenarios import Environment, Scenario, load_catalog  # noqa: E402


@dataclass(frozen=True)
class Budget:
    max_tool_calls: int = 48
    max_turns: int = 50
    wall_seconds: int = 120
    max_response_bytes: int = 65536

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise ValueError("budget limits must be positive integers")


class BudgetExceeded(Exception):
    pass


class Session:
    def __init__(self, environment: Environment, budget: Budget) -> None:
        self.environment = environment
        self.budget = budget
        self.started = time.monotonic()
        self.calls = 0
        self.turns = 0
        self.messages: list[dict[str, Any]] = []
        self.pending_delivery: list[int] = []
        self.external = False
        self.adapter_receipts: list[dict[str, Any]] = []

    def remaining(self) -> float:
        remaining = self.budget.wall_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("wall_seconds")
        return remaining

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.remaining()
        if not self.external:
            self.decision()
        if self.calls >= self.budget.max_tool_calls:
            raise BudgetExceeded("max_tool_calls")
        self.calls += 1
        result = self.environment.call(name, arguments)
        self.remaining()
        if self.environment.events:
            if self.external:
                self.pending_delivery.append(len(self.environment.events) - 1)
            else:
                self.environment.events[-1]["response_delivered"] = True
        self.messages.append({"tool": name, "arguments": arguments, "result": copy.deepcopy(result)})
        return result

    def decision(self) -> None:
        self.remaining()
        if self.turns >= self.budget.max_turns:
            raise BudgetExceeded("max_turns")
        self.turns += 1


def recover(session: Session) -> None:
    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        while True:
            result = session.call(name, arguments)
            if result.get("ok") is True:
                return result
            error = result.get("error", {})
            if error.get("code") not in {"timeout", "rate_limit", "rate_limited", "transient_error"}:
                raise ValueError("reference received an unsupported service error")
            ticks = error.get("retry_after_ticks")
            if ticks is not None:
                session.call("wait", {"ticks": ticks})

    page = 1
    account: str | None = None
    records: dict[str, dict[str, Any]] = {}
    while True:
        result = call("search_records", {"page": page})
        account = result["request"]["account"]
        for cached in result["records"]:
            record = call("read_record", {"id": cached["id"]})["record"]
            records[record["id"]] = record
        if result["complete"]:
            break
        page = result["next_page"]
    if account is None:
        raise ValueError("reference did not receive the public selection request")
    total = sum(record["value"] for record in records.values() if record["account"] == account and record["eligible"])
    while not call("prepare", {})["prepared"]:
        pass
    call("submit", {"total": total})


def naive(session: Session) -> None:
    result = session.call("search_records", {"page": 1})
    if result.get("ok") is not True:
        return
    # Deliberately ignores continuation, authority, prerequisites and selection.
    session.call("submit", {"total": sum(record["value"] for record in result["records"])})


def external(session: Session, command: list[str]) -> None:
    """Use a fresh decision process per turn with explicit complete history."""
    session.external = True
    for _ in range(session.budget.max_turns):
        session.decision()
        request = {
            "instruction": session.environment.instruction,
            "tools": session.environment.tools,
            "history": session.messages,
            "remaining_tool_calls": session.budget.max_tool_calls - session.calls,
            "response_contract": {"tool": "name", "arguments": {}, "or": {"finish": True}},
        }
        action = adapter_action(session, command, request)
        if isinstance(action, dict) and set(action) == {"action", "receipt"}:
            if not isinstance(action["receipt"], dict):
                raise ValueError("adapter receipt must be an object")
            session.adapter_receipts.append(action["receipt"])
            action = action["action"]
        for index in session.pending_delivery:
            session.environment.events[index]["response_delivered"] = True
        session.pending_delivery.clear()
        if not isinstance(action, dict):
            raise ValueError("target adapter must return an action object")
        if action == {"finish": True}:
            return
        if (
            set(action) != {"tool", "arguments"}
            or not isinstance(action["tool"], str)
            or not isinstance(action["arguments"], dict)
        ):
            raise ValueError("invalid target action")
        session.call(action["tool"], action["arguments"])
    raise BudgetExceeded("max_turns")


def adapter_action(session: Session, command: list[str], request: dict[str, Any]) -> Any:
    # A process group bounds ordinary descendants. This is not a hostile-code sandbox.
    remaining = session.remaining()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    payload = memoryview(json_bytes(request))
    received = bytearray()
    error_bytes = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            for stream, events, label in (
                (process.stdin, selectors.EVENT_WRITE, "input"),
                (process.stdout, selectors.EVENT_READ, "output"),
                (process.stderr, selectors.EVENT_READ, "error"),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, events, label)
            deadline = time.monotonic() + remaining
            while selector.get_map():
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise BudgetExceeded("wall_seconds")
                for key, _ in selector.select(timeout):
                    stream = {"input": process.stdin, "output": process.stdout, "error": process.stderr}[key.data]
                    if key.data == "input":
                        try:
                            written = os.write(key.fd, payload[:8192])
                        except BrokenPipeError:
                            raise RuntimeError("target adapter closed its input") from None
                        payload = payload[written:]
                        if not payload:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                        elif key.data == "output":
                            received.extend(chunk)
                        else:
                            error_bytes.extend(chunk)
                        if len(received) + len(error_bytes) > session.budget.max_response_bytes:
                            raise RuntimeError("target adapter response exceeded byte limit")
            process.wait(timeout=session.remaining())
        if process.returncode != 0:
            try:
                failure = json.loads(error_bytes)
                if (
                    isinstance(failure, dict)
                    and set(failure) <= {"adapter_error", "receipt"}
                    and isinstance(failure.get("receipt"), dict)
                ):
                    session.adapter_receipts.append(failure["receipt"])
            except (ValueError, UnicodeDecodeError):
                pass
            raise RuntimeError("target adapter exited nonzero")
        return json.loads(received)
    except subprocess.TimeoutExpired:
        raise BudgetExceeded("wall_seconds") from None
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def grade(snapshot: dict[str, Any]) -> bool:
    account = snapshot["request"]["account"]
    expected = sum(row["value"] for row in snapshot["source_records"] if row["account"] == account and row["eligible"])
    submission = snapshot["submission"]
    return snapshot["prepared"] is True and submission is not None and submission.get("total") == expected


def failure_observation(
    scenario: Scenario, session: Session, passed: bool, status: str = "completed"
) -> dict[str, Any]:
    return retained_failure_observation(scenario, session.environment.snapshot(), passed, status)


def retained_failure_observation(
    scenario: Scenario, snapshot: dict[str, Any], passed: bool, status: str = "completed"
) -> dict[str, Any]:
    events = snapshot["events"]
    fired = [index for index, event in enumerate(events) if event.get("perturbation_applied") is True]
    delivered = [index for index in fired if events[index].get("response_delivered") is True]
    # Final failure after exposure is not sufficient to establish its mechanism.
    signature = None
    submits = [(index, event) for index, event in enumerate(events) if event["tool"] == "submit"]
    if delivered and not passed and status == "completed":
        if (
            scenario.category in {"timeout", "rate-limit"}
            and len(events) - 1 in delivered
            and events[-1]["response"].get("ok") is False
        ):
            signature = "abandoned_after_tool_error"
        elif submits:
            submit_index, submit = submits[-1]
            exposures = [events[index] for index in delivered if index < submit_index]
            searches = [
                event["response"]
                for event in exposures
                if event["tool"] == "search_records" and event["response"].get("ok") is True
            ]
            if scenario.category == "partial-result" and any(
                response.get("complete") is False
                and not any(
                    event["tool"] == "search_records"
                    and event["arguments"].get("page") == response["next_page"]
                    and event["response"].get("ok") is True
                    for event in events[:submit_index]
                )
                for response in searches
            ):
                signature = "submitted_without_continuation"
            elif (
                scenario.category == "prerequisite-dependency"
                and submit_index in delivered
                and submit["response"].get("error", {}).get("code") == "prepare_required"
            ):
                signature = "submitted_before_prerequisites"
            elif scenario.category == "distinguishable-distractor" and any(
                any(
                    record["account"] != response["request"]["account"] or not record["eligible"]
                    for record in response["records"]
                )
                and submit["arguments"].get("total") == sum(record["value"] for record in response["records"])
                for response in searches
            ):
                signature = "submitted_distractor_records"
            elif scenario.category == "stale-result":
                source = {record["id"]: record for record in snapshot["source_records"]}
                visible: dict[str, dict[str, Any]] = {}
                authoritative: set[str] = set()
                affected: set[str] = set()
                for index, event in enumerate(events[:submit_index]):
                    response = event["response"]
                    if event.get("response_delivered") is not True or response.get("ok") is not True:
                        continue
                    if index in delivered and event["tool"] == "search_records":
                        for record in response["records"]:
                            record_id = record["id"]
                            if record != source[record_id]:
                                affected.add(record_id)
                            if record_id not in authoritative:
                                visible[record_id] = record
                    elif event["tool"] == "read_record":
                        record = response["record"]
                        if record == source.get(record["id"]):
                            authoritative.add(record["id"])
                            visible[record["id"]] = record
                account = snapshot["request"]["account"]
                stale_total = sum(
                    record["value"]
                    for record in visible.values()
                    if record["account"] == account and record["eligible"]
                )
                if affected - authoritative and submit["arguments"].get("total") == stale_total:
                    signature = "submitted_without_authoritative_read"
    return {
        "perturbation_fired": bool(fired),
        "response_delivered": bool(delivered),
        "fired_event_indices": fired,
        "delivered_event_indices": delivered,
        "observed_signature": signature,
        "intended_failure_observed": signature == scenario.failure_signature
        if delivered and status == "completed"
        else None,
    }


def trial(
    task: str,
    scenario: Scenario,
    arm: str,
    seed: int,
    budget: Budget,
    target: str,
    command: list[str],
    repetition: int | None = None,
) -> dict[str, Any]:
    if arm not in {"baseline", "perturbed"}:
        raise ValueError("unknown trial arm")
    if target not in {"reference", "naive", "external"}:
        raise ValueError("unknown target")
    environment = Environment(task, scenario if arm == "perturbed" else None, seed)
    session = Session(environment, budget)
    initial = copy.deepcopy(environment.snapshot())
    error = None
    status = "completed"
    try:
        if target == "reference":
            recover(session)
            session.decision()
        elif target == "naive":
            naive(session)
            session.decision()
        else:
            external(session, command)
    except BudgetExceeded as exc:
        status, error = "budget_exhausted", str(exc)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        status, error = "execution_error", type(exc).__name__
    final = environment.snapshot()
    if initial["source_records"] != final["source_records"] or initial["request"] != final["request"]:
        status, error = "execution_error", "authoritative_state_changed"
    passed = grade(final)
    return {
        "task_id": task,
        "task_version": initial["task_version"],
        "environment_version": initial["environment_version"],
        "scenario": scenario.as_dict(),
        "arm": arm,
        "repetition": repetition,
        "seed": seed,
        "target": target,
        "evidence_kind": "scripted-control" if target in {"reference", "naive"} else "external-agent",
        "budget": asdict(budget),
        "tool_calls": session.calls,
        "turns": session.turns,
        "elapsed_seconds": time.monotonic() - session.started,
        "status": status,
        "error": error,
        "task_passed": passed if status == "completed" else False if status == "budget_exhausted" else None,
        "instruction": environment.instruction,
        "tools": environment.tools,
        "initial_state": initial,
        "final_state": final,
        "history": session.messages,
        "adapter_receipts": session.adapter_receipts,
        "observation": failure_observation(scenario, session, passed, status) if arm == "perturbed" else None,
    }


def run(args: argparse.Namespace) -> None:
    catalog = load_catalog(args.catalog)
    selected = catalog if not args.scenario else [item for item in catalog if item.id in args.scenario]
    if not selected or (args.scenario and set(args.scenario) != {item.id for item in selected}):
        raise ValueError("unknown or empty scenario selection")
    if args.repetitions < 2:
        raise ValueError("comparisons require at least two repetitions")
    if len(set(args.task)) != len(args.task) or (args.scenario and len(set(args.scenario)) != len(args.scenario)):
        raise ValueError("duplicate task or scenario selection")
    command = json.loads(args.command) if args.command else []
    if args.target == "external" and (
        not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command)
    ):
        raise ValueError("external target requires a JSON command array")
    if args.target == "external" and args.target_identity == "scripted-control":
        raise ValueError("external target requires an explicit target identity")
    if args.output.exists():
        raise ValueError("use a fresh output directory; retained trials are never replaced")
    budget = Budget(args.max_tool_calls, args.max_turns, args.wall_seconds)
    args.output.mkdir(parents=True, mode=0o700)
    identity = {
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "catalog_sha256": digest_file(args.catalog),
        "catalog": [item.as_dict() for item in catalog],
        "runtime_sha256": digest_file(ENVIRONMENT / "scripts/recovery_scenarios.py"),
        "runner_sha256": digest_file(Path(__file__)),
        "verifier": "grade in runner_sha256",
        "target": args.target,
        "target_identity": args.target_identity,
        "command": command,
        "command_files": {
            str(Path(part).resolve()): digest_file(Path(part)) for part in command if Path(part).is_file()
        },
        "budget": asdict(budget),
        "repetitions": args.repetitions,
        "scenario_ids": [item.id for item in selected],
        "tasks": args.task,
        "seed": args.seed,
        "combination_policy": "isolated-only",
        "token_budget": None,
        "provider_revision": None,
        "model_sampling_seed": None,
    }
    write_json(args.output / "inputs.json", identity)
    rows = []
    for task in args.task:
        for scenario in selected:
            controls = []
            for arm in ("baseline", "perturbed"):
                control = trial(task, scenario, arm, args.seed, budget, "reference", [])
                path = Path("controls") / task / scenario.id / f"{arm}.json"
                write_json(args.output / path, control)
                controls.append({"path": str(path), "sha256": digest_file(args.output / path)})
                if control["status"] != "completed" or control["task_passed"] is not True:
                    raise ValueError(f"reference failed for {task}/{scenario.id}/{arm}; target trials not qualified")
            pairs = []
            for repetition in range(args.repetitions):
                pair: dict[str, Any] = {"repetition": repetition, "seed": args.seed}
                # Alternate order without changing task seed or target configuration.
                arms = ("baseline", "perturbed") if repetition % 2 == 0 else ("perturbed", "baseline")
                for arm in arms:
                    result = trial(task, scenario, arm, args.seed, budget, args.target, command, repetition)
                    path = Path("trials") / task / scenario.id / str(repetition) / f"{arm}.json"
                    write_json(args.output / path, result)
                    if args.target == "external" and result["status"] == "execution_error":
                        write_json(
                            args.output / "interrupted.json",
                            {
                                "reason": "external_execution_error",
                                "trial_path": str(path),
                                "trial_sha256": digest_file(args.output / path),
                            },
                        )
                        raise ValueError("external execution failed; evidence retained and queued trials stopped")
                    pair[arm] = {
                        "path": str(path),
                        "sha256": digest_file(args.output / path),
                        "status": result["status"],
                        "task_passed": result["task_passed"],
                        "observation": result["observation"],
                    }
                pairs.append(pair)
            measured = [p for p in pairs if all(p[arm]["task_passed"] is not None for arm in ("baseline", "perturbed"))]
            rows.append(
                {
                    "task_id": task,
                    "scenario_id": scenario.id,
                    "category": scenario.category,
                    "severity": scenario.severity,
                    "provenance": scenario.provenance,
                    "reference_controls": controls,
                    "pairs": pairs,
                    "paired_trials": len(measured),
                    "unmeasured_pairs": len(pairs) - len(measured),
                    "baseline_passes": sum(p["baseline"]["task_passed"] for p in measured),
                    "perturbed_passes": sum(p["perturbed"]["task_passed"] for p in measured),
                    "pass_rate_difference": sum(
                        int(p["perturbed"]["task_passed"]) - int(p["baseline"]["task_passed"]) for p in measured
                    )
                    / len(measured)
                    if measured
                    else None,
                    "intended_failures_observed": sum(
                        p["perturbed"]["observation"]["intended_failure_observed"] is True for p in pairs
                    ),
                    "rubric_score": None,
                }
            )
    report = {
        "inputs_sha256": digest_file(args.output / "inputs.json"),
        "evidence_kind": "scripted-control" if args.target != "external" else "external-agent",
        "difficulty_matrix": rows,
    }
    write_json(args.output / "difficulty-matrix.json", report)
    verify(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(rows),
                "target_trials": len(rows) * args.repetitions * 2,
                "evidence_kind": report["evidence_kind"],
            }
        )
    )


def verify_trial_replay(record: dict[str, Any], scenario: Scenario) -> None:
    environment = Environment(record["task_id"], scenario if record["arm"] == "perturbed" else None, record["seed"])
    if json_bytes(record["initial_state"]) != json_bytes(environment.snapshot()):
        raise ValueError("trial initial state differs from replay")
    if record["instruction"] != environment.instruction or json_bytes(record["tools"]) != json_bytes(environment.tools):
        raise ValueError("trial task contract differs from replay")
    final = record["final_state"]
    history = []
    for event in final["events"]:
        environment.call(event["tool"], event["arguments"])
        replayed = environment.events[-1]
        if type(event.get("response_delivered")) is not bool:
            raise ValueError("trial replay requires a boolean delivery claim")
        # Delivery is a retained adapter-boundary claim, not proof of model attendance.
        replayed["response_delivered"] = event["response_delivered"]
        if json_bytes(event) != json_bytes(replayed):
            raise ValueError("trial event differs from replay")
        history.append({"tool": event["tool"], "arguments": event["arguments"], "result": event["response"]})
    if json_bytes(final) != json_bytes(environment.snapshot()):
        raise ValueError("trial final state differs from replay")
    # A wall deadline can expire after execution but before Session records delivery.
    interrupted_call = (
        record["status"] == "budget_exhausted"
        and record["error"] == "wall_seconds"
        and bool(history)
        and final["events"][-1]["response_delivered"] is False
        and json_bytes(record["history"]) == json_bytes(history[:-1])
    )
    if not interrupted_call and json_bytes(record["history"]) != json_bytes(history):
        raise ValueError("trial history differs from replay")
    calls, turns = record["tool_calls"], record["turns"]
    if (
        type(calls) is not int
        or type(turns) is not int
        or calls != len(history)
        or not 0 <= calls <= record["budget"]["max_tool_calls"]
        or not calls <= turns <= min(calls + 1, record["budget"]["max_turns"])
        or (record["status"] == "completed" and turns != calls + 1)
    ):
        raise ValueError("trial counters differ from replay")


def verify(root: Path) -> None:
    report = read_json(root / "difficulty-matrix.json")
    inputs = read_json(root / "inputs.json")
    if not isinstance(report, dict) or not isinstance(inputs, dict):
        raise ValueError("comparison documents must be objects")
    if report["inputs_sha256"] != digest_file(root / "inputs.json"):
        raise ValueError("comparison inputs changed")
    if inputs["target"] not in {"reference", "naive", "external"}:
        raise ValueError("unknown target")
    expected_kind = "external-agent" if inputs["target"] == "external" else "scripted-control"
    if report.get("evidence_kind") != expected_kind:
        raise ValueError("comparison evidence kind mismatch")
    catalog = {item["id"]: item for item in inputs["catalog"]}
    if len(catalog) != len(inputs["catalog"]) or not set(inputs["scenario_ids"]) <= catalog.keys():
        raise ValueError("comparison scenario catalog mismatch")
    expected = {(task, scenario) for task in inputs["tasks"] for scenario in inputs["scenario_ids"]}
    seen: set[tuple[str, str]] = set()
    paths: set[Path] = set()

    def record_at(
        ref: dict[str, Any], task: str, scenario: str, arm: str, target: str, repetition: int | None
    ) -> dict[str, Any]:
        name = ref["path"]
        path = root / name
        resolved = path.resolve()
        if resolved in paths or path.is_symlink() or not resolved.is_relative_to(root.resolve()):
            raise ValueError("duplicate or escaping trial evidence")
        paths.add(resolved)
        if digest_file(path) != ref["sha256"]:
            raise ValueError("trial evidence changed")
        record = read_json(path)
        if not isinstance(record, dict):
            raise ValueError("trial evidence must be an object")
        if (
            "repetition" not in record
            or type(record["repetition"]) is not type(repetition)
            or record["repetition"] != repetition
        ):
            raise ValueError("trial repetition identity mismatch")
        kind = "external-agent" if target == "external" else "scripted-control"
        if record.get("evidence_kind") != kind:
            raise ValueError("trial evidence kind mismatch")
        if json_bytes(record["scenario"]) != json_bytes(catalog[scenario]):
            raise ValueError("trial scenario differs from input catalog")
        if (record["task_id"], record["scenario"]["id"], record["arm"], record["target"]) != (
            task,
            scenario,
            arm,
            target,
        ):
            raise ValueError("trial identity mismatch")
        if record["budget"] != inputs["budget"] or record["seed"] != inputs["seed"]:
            raise ValueError("trial budget or seed mismatch")
        initial, final = record["initial_state"], record["final_state"]
        scenario_state = catalog[scenario] if arm == "perturbed" else None
        if any(
            json_bytes({"scenario": state["scenario"]}) != json_bytes({"scenario": scenario_state})
            for state in (initial, final)
        ):
            raise ValueError("trial state scenario differs from input catalog")
        if initial["source_records"] != final["source_records"] or initial["request"] != final["request"]:
            raise ValueError("authoritative state changed")
        status = record["status"]
        if status not in {"completed", "execution_error", "budget_exhausted"}:
            raise ValueError("unknown trial status")
        verdict = grade(final) if status == "completed" else False if status == "budget_exhausted" else None
        if record["task_passed"] is not verdict:
            raise ValueError("trial verdict does not match retained state")
        definition = dict(catalog[scenario])
        definition["schedule"] = tuple(definition["schedule"])
        definition["source_refs"] = tuple(definition["source_refs"])
        retained_scenario = Scenario(**definition)
        verify_trial_replay(record, retained_scenario)
        observation = (
            retained_failure_observation(retained_scenario, final, grade(final), status) if arm == "perturbed" else None
        )
        if json_bytes({"observation": record["observation"]}) != json_bytes({"observation": observation}):
            raise ValueError("trial observation differs from retained events")
        return record

    for row in report["difficulty_matrix"]:
        key = (row["task_id"], row["scenario_id"])
        if key not in expected or key in seen:
            raise ValueError("unexpected or duplicate difficulty row")
        seen.add(key)
        fields = ("category", "severity", "provenance")
        if json_bytes({field: row[field] for field in fields}) != json_bytes(
            {field: catalog[key[1]][field] for field in fields}
        ):
            raise ValueError("difficulty metadata differs from input catalog")
        if len(row["reference_controls"]) != 2:
            raise ValueError("both reference controls are required")
        controls = [
            record_at(ref, *key, arm, "reference", None)
            for ref, arm in zip(row["reference_controls"], ("baseline", "perturbed"), strict=True)
        ]
        if any(record["status"] != "completed" or record["task_passed"] is not True for record in controls):
            raise ValueError("reference qualification failed")
        if controls[0]["instruction"] != controls[1]["instruction"] or controls[0]["tools"] != controls[1]["tools"]:
            raise ValueError("paired task contract changed")
        if len(row["pairs"]) != inputs["repetitions"]:
            raise ValueError("incomplete repeated comparison")
        for repetition, pair in enumerate(row["pairs"]):
            if pair["repetition"] != repetition or pair["seed"] != inputs["seed"]:
                raise ValueError("repetition identity mismatch")
            for arm in ("baseline", "perturbed"):
                ref = pair[arm]
                record = record_at(ref, *key, arm, inputs["target"], repetition)
                if any(ref[field] != record[field] for field in ("status", "task_passed", "observation")):
                    raise ValueError("comparison projection differs from trial")
                control = controls[0 if arm == "baseline" else 1]
                if (
                    record["initial_state"] != control["initial_state"]
                    or record["instruction"] != control["instruction"]
                ):
                    raise ValueError("target did not receive qualified task state")
        measured = [
            pair
            for pair in row["pairs"]
            if all(pair[arm]["task_passed"] is not None for arm in ("baseline", "perturbed"))
        ]
        baseline = sum(pair["baseline"]["task_passed"] for pair in measured)
        perturbed = sum(pair["perturbed"]["task_passed"] for pair in measured)
        derived = {
            "paired_trials": len(measured),
            "unmeasured_pairs": inputs["repetitions"] - len(measured),
            "baseline_passes": baseline,
            "perturbed_passes": perturbed,
            "pass_rate_difference": (perturbed - baseline) / len(measured) if measured else None,
            "intended_failures_observed": sum(
                pair["perturbed"]["observation"]["intended_failure_observed"] is True for pair in row["pairs"]
            ),
            "rubric_score": None,
        }
        if any(row[field] != value for field, value in derived.items()):
            raise ValueError("difficulty aggregate differs from retained trials")
    if seen != expected:
        raise ValueError("missing difficulty rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ENVIRONMENT / "scenarios/recovery.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--task", action="append", choices=["invoice-total", "stock-total"])
    parser.add_argument("--target", choices=["reference", "naive", "external"], default="naive")
    parser.add_argument("--command", help="JSON argv for an approved external decision adapter")
    parser.add_argument("--target-identity", default="scripted-control")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--max-tool-calls", type=int, default=48)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--wall-seconds", type=int, default=120)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    args.task = args.task or ["invoice-total", "stock-total"]
    try:
        if args.verify:
            verify(args.output)
            print("retained comparison verified")
        else:
            run(args)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
