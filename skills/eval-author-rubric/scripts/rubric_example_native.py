# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Consume admitted native controls without executing retained code.

Historical panels retain their reviewed inventory pin. Current Oracle/NOP pairs
require the local collector registry and raw recorder/Harbor validation.
Human authority and independence remain absent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tomllib
import warnings
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
import audit_verifier_evidence as audit  # noqa: E402
from eval_author_shared import task_evidence as recorder  # noqa: E402
from rubric_identity import MAX_TOTAL_BYTES, read_selected  # noqa: E402

PANEL_INDEX_SHA256 = "b47cfcbf453b4066d8f6e114377ca078bf687aac5d8aa2b2467ed47a29c1a61d"
BASE = "skills/eval-author-rubric/examples/refund/base"
RUNS = {
    "oracle": ("oracle", "manifest.json", 1),
    "nop": ("nop", "manifest.json", 0),
    "alternative": ("alternative", "controls-manifest.json", 1),
    "wrong_amount": ("wrong_amount", "controls-manifest.json", 0),
    "boundaries": ("boundaries", "controls-manifest.json", 1),
    "predecessor": ("predecessor", "controls-manifest.json", 0),
    "fresh_reset": ("fresh_reset", "controls-manifest.json", None),
    "fresh-reset-unchanged": ("fresh_reset", "controls-manifest.json", 1),
    "late-closure": ("boundaries", "late-manifest.json", 1),
    "fresh-reset-retry": ("fresh_reset", "late-manifest.json", 1),
}


def _require(condition: object) -> None:
    if not condition:
        raise ValueError("native evidence is invalid or stale")


def _same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


class Panel(audit.Review):
    def __init__(self, sources: Any, index_path: str) -> None:
        self.repo = sources.root
        self.sources = sources
        self.bundle = self.repo / Path(index_path).parent
        self.evidence: dict[str, str] = {}
        self.manifests: dict[Path, dict] = {}
        self.cache: dict[Path, bytes] = {}
        self.remaining = MAX_TOTAL_BYTES
        raw = sources.source(index_path)
        _require(raw is not None and hashlib.sha256(raw).hexdigest() == PANEL_INDEX_SHA256)
        self.index = {self.bundle / row["path"]: row["sha256"] for row in recorder.loads(raw)}

    def path(self, value: str | Path) -> Path:
        path = Path(value)
        path = path if path.is_absolute() else self.repo / path
        _require(".." not in path.parts and path.is_relative_to(self.bundle))
        _require(not any(p.is_symlink() for p in (path, *path.parents)))
        return path

    def read(self, path: Path) -> bytes:
        path = self.path(path)
        _require(path in self.index)
        if path not in self.cache:
            relative = path.relative_to(self.repo).as_posix()
            _require(not self.sources.access.reason(relative))
            raw, reason = read_selected(self.repo, relative, min(1_048_576, self.remaining))
            _require(reason is None and raw is not None)
            self.remaining -= len(raw)
            _require(hashlib.sha256(raw).hexdigest() == self.index[path])
            self.cache[path] = raw
            self.evidence[relative] = self.index[path]
        return self.cache[path]

    def task_digest(self, task: Path) -> str:
        task = self.path(task)
        entries = []
        actual_files = set()
        for path in sorted(task.rglob("*")):
            _require(not path.is_symlink())
            relative = path.relative_to(task)
            raw = self.read(path) if path.is_file() else None
            if raw is not None:
                actual_files.add(path)
            entries.append(
                [
                    str(relative),
                    path.stat().st_mode & 0o777,
                    hashlib.sha256(raw).hexdigest() if raw is not None else None,
                ]
            )
        _require(actual_files == {p for p in self.index if p.is_relative_to(task)})
        return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def _trusted_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("native scorer is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PairPanel(Panel):
    directories = ("task", "receipts", "trials", "manifest.json.attempts")
    documents = ("manifest.json", "contract.json")

    def __init__(self, bundle: Path, document: dict[str, Any], sources: Any = None) -> None:
        self.repo = sources.root if sources is not None else bundle
        self.sources = sources
        self.bundle = bundle
        self.origin = Path(document["bundle"])
        self.evidence = {}
        self.manifests = {}
        self.cache = {}
        self.remaining = MAX_TOTAL_BYTES
        self.index = {bundle / row["path"]: row["sha256"] for row in document["files"]}
        _require(len(self.index) == len(document["files"]))
        from rubric_native_collect import inventory

        actual = []
        for name in self.directories:
            actual.extend({**row, "path": name + "/" + row["path"]} for row in inventory(bundle / name))
        for name in self.documents:
            path = bundle / name
            actual.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(self.read(path)).hexdigest(),
                    "mode": path.stat().st_mode & 0o777,
                }
            )
        _require(_same(sorted(actual, key=lambda r: r["path"]), sorted(document["files"], key=lambda r: r["path"])))

    def path(self, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute() and path.is_relative_to(self.origin):
            path = self.bundle / path.relative_to(self.origin)
        return super().path(path)

    def read(self, path: Path) -> bytes:
        path = self.path(path)
        _require(path in self.index)
        if self.sources is not None:
            return super().read(path)
        if path not in self.cache:
            relative = path.relative_to(self.repo).as_posix()
            raw, reason = read_selected(self.repo, relative, min(1_048_576, self.remaining))
            _require(reason is None and raw is not None)
            self.remaining -= len(raw)
            _require(hashlib.sha256(raw).hexdigest() == self.index[path])
            self.cache[path] = raw
            self.evidence[relative] = self.index[path]
        return self.cache[path]


class ObservationPanel(PairPanel):
    directories = ("task", "trials")
    documents = ()


def validate_observation(
    bundle: Path, document: dict[str, Any], sources: Any = None, task_path: str | None = None
) -> dict[str, Any]:
    from rubric_native_collect import (
        BASE as current_base,
    )
    from rubric_native_collect import (
        OBSERVATION_SCHEMA,
        inventory,
        observation_command,
        producer_identity,
    )

    _require(document.get("schema") == OBSERVATION_SCHEMA)
    _require(document["producer"] == producer_identity())
    panel = ObservationPanel(bundle, document, sources)
    _require(document["command"] == observation_command(panel.origin))
    expected = inventory(current_base)
    _require(inventory(bundle / "task") == expected)
    if sources is not None:
        selected = sources.root / task_path
        _require(inventory(selected) == expected)
        for row in expected:
            raw = sources.source((selected / row["path"]).relative_to(sources.root).as_posix())
            _require(raw is not None and hashlib.sha256(raw).hexdigest() == row["sha256"])
    from rubric_native_collect import observation_contract, provider_validation

    packet = observation_contract()
    _require(_same(document["contract"], packet))
    _require(_same(document["provider_validation"], provider_validation(bundle / "task")))
    cases = packet["state"]["relation"]
    _require(set((bundle / "trials").iterdir()) == {bundle / "trials" / c for c in cases})
    _require(document["commands"] == {c: observation_command(panel.origin, c) for c in cases})
    observations = {c: _validate_observation_trial(panel, c, r, expected) for c, r in cases.items()}
    environment = _validate_environment_observations(panel, document, observations)
    return {
        "scope": OBSERVATION_SCHEMA,
        "status": "unknown",
        "task_freshness": "current",
        "counts": {"attempts": len(cases), "completed": len(cases), "infrastructure_errors": 0},
        "native_execution": True,
        "human_review": False,
        "independence": False,
        "full_task_acceptance": False,
        "controls": [{"case": c, "status": "passed", "reward": r} for c, r in cases.items()],
        "observations": observations["nop"],
        "environment": environment,
        "configuration_comparison": environment["configuration_comparison"],
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }


def _validate_observation_trial(panel: Any, case: str, expected_reward: int, expected: Any) -> dict[str, Any]:
    from harbor.models.task.task import Task
    from harbor.models.trial.result import TrialResult
    from rubric_native_collect import BASE as current_base
    from rubric_native_collect import OBSERVATION_SCOPE, RUNTIME_PROBE

    bundle = panel.bundle
    native = panel.object(bundle / "trials" / case / "result.json")
    TrialResult.model_validate(native)
    config = panel.object(bundle / "trials" / case / "config.json")
    from harbor.models.trial.config import TrialConfig

    _require(TrialConfig.model_validate(native["config"]) == TrialConfig.model_validate(config))
    config = native["config"]
    _require(native["exception_info"] is None and native["trial_name"] == case)
    _require(config["task"]["path"] == str(panel.origin / "task"))
    _require(config["agent"]["name"] == "nop" and config["agent"]["model_name"] is None)
    _require(config["environment"]["type"] == "docker" and config["environment"]["delete"] is True)
    _require(config["verifier"]["import_path"] == "rubric_native_collect:RuntimeObserver")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        _require(native["task_checksum"] == Task(bundle / "task").checksum)
    observation = panel.object(bundle / "trials" / case / "verifier/runtime-observation.json")
    _require(observation["scope"] == OBSERVATION_SCOPE and observation["executed_import_trace"] is False)
    _require(observation["probe_sha256"] == hashlib.sha256(RUNTIME_PROBE.encode()).hexdigest())
    _require(observation["image_id"] == observation["container_image"])
    _require(_same(observation["reward"], native["verifier_result"]))
    _require(native["verifier_result"]["rewards"]["reward"] == expected_reward)
    scorer = _trusted_module(current_base / "tests/refund_scorer.py", "_observation_scorer")
    checks = scorer.evaluate(
        panel.object(bundle / "task/environment/contract.json"),
        observation["actions"]["after"]["value"]["candidate"],
        grader="reviewed",
    )
    _require(_same(panel.object(bundle / "trials" / case / "verifier/checks.json"), checks))
    _require(float(panel.read(bundle / "trials" / case / "verifier/reward.txt")) == expected_reward)
    root, solver = observation["root"], observation["solver"]
    _require(root["executable"] == "/usr/local/bin/python")
    _require(
        _same(
            root["flags"],
            {k: 1 for k in ("isolated", "no_site", "ignore_environment", "no_user_site", "dont_write_bytecode")},
        )
    )
    _require(_same(root["startup_modules_loaded"], {k: False for k in ("site", "sitecustomize", "usercustomize")}))
    _require(type(solver["uid"]) is int and solver["uid"] == 65534)
    _require(root["sys_path"] == [*root["initial_sys_path"], "/tests"])
    _require(root["initial_sys_path"] and all(p.startswith("/usr/local/lib/python") for p in root["initial_sys_path"]))
    _require(set(solver["access"]) == set(root["files"]))
    for path, info in root["files"].items():
        _require(path.startswith("/") and ".." not in Path(path).parts)
        access = solver["access"][path]
        _require(all(type(access[k]) is bool for k in ("readable", "writable", "searchable")))
        _require(access["writable"] is False)
        if info["exists"]:
            _require(type(info["uid"]) is int and type(info["gid"]) is int and type(info["mode"]) is int)
            _require(0 <= info["mode"] <= 0o7777)
            if not info["directory"]:
                _require(info["readable"] is True and len(info["sha256"]) == 64)
                _require(all(c in "0123456789abcdef" for c in info["sha256"]))
    for row in expected:
        if row["path"].startswith("tests/"):
            info = root["files"].get("/" + row["path"])
            _require(info is not None and info["sha256"] == row["sha256"])
    for name, module in root["origins"].items():
        if module["origin"] in ("built-in", "frozen") or name == "__main__":
            continue
        path = module["file"]
        if path is None and module["origin"] is None:
            continue  # Metadata aliases are retained, never used to resolve an import.
        _require(isinstance(path, str) and root["files"][path]["readable"] is True)
        _require(path.startswith("/usr/local/lib/python") or path == "/tests/refund_scorer.py")
    return observation


def _validate_environment_observations(
    panel: Any, document: dict[str, Any], observations: dict[str, Any]
) -> dict[str, Any]:
    from types import SimpleNamespace

    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial
    from rubric_native_collect import ACTION_PROBE, BASE, observation_contract

    packet = observation_contract()
    contract = panel.object(panel.bundle / "task/environment/contract.json")
    initial = {
        "ok": True,
        "value": {"candidate": {"events": [], "success": False}, "state": contract["sources"]["initial_state"]},
    }
    scorer = _trusted_module(BASE / "tests/refund_scorer.py", "_environment_scorer")
    declared = tomllib.loads(panel.read(panel.bundle / "task/task.toml").decode())
    comparison: list[dict[str, Any]] = []
    controls = []
    prior_finish = None
    ids, sessions, trials = set(), set(), set()
    order = document["execution_order"]
    _require([r["case"] for r in order] == list(packet["state"]["relation"]))
    for case, observation in observations.items():
        native = panel.object(panel.bundle / "trials" / case / "result.json")
        _require(native["id"] not in trials)
        trials.add(native["id"])
        if prior_finish is not None:
            _require(prior_finish <= native["started_at"])
        prior_finish = native["finished_at"]
        _require(observation["case"] == case)
        _require(observation["action_probe_sha256"] == hashlib.sha256(ACTION_PROBE.encode()).hexdigest())
        actions = observation["actions"]
        _require(type(actions["uid"]) is int and actions["uid"] == 65534 and actions["case"] == case)
        _require(_same(actions["before"], initial) and _same(actions["after_errors"], initial))
        _require(_same(actions["errors"], [{"ok": False, "error": "invalid_request"}] * 3))
        requests = actions["requests"]
        candidate = actions["after"]["value"]["candidate"]
        _require(actions["after"]["ok"] is True)
        if case == "actions":
            _require([r.get("event", {}).get("kind") for r in requests] == ["approve", "refund", "ledger", None])
            _require(requests[-1] == {"operation": "complete", "success": True})
            _require(all(r["operation"] == "act" for r in requests[:-1]))
            _require(_same(candidate, {"events": [r["event"] for r in requests[:-1]], "success": True}))
        else:
            _require(requests == [] and _same(actions["after"], initial))
        _require(_same(actions["replies"], [{"ok": True}] * len(requests)))
        state, _ = scorer._apply_events(contract["sources"]["initial_state"], candidate["events"])
        _require(_same(actions["after"]["value"]["state"], state))
        access = actions["permissions"]
        for path in ("/app/contract.json", "/app/ledger.py", "/app/server.py"):
            _require(access[path]["readable"] is True and access[path]["writable"] is False)
            source = "environment/" + Path(path).name
            _require(
                observation["root"]["files"][path]["sha256"]
                == hashlib.sha256(panel.read(panel.bundle / "task" / source)).hexdigest()
            )
        _require(access["/var/lib/refund/record.json"]["readable"] is False)
        _require(access["/var/lib/refund/record.json"]["writable"] is False)
        _require(access["/run/refund/ledger.sock"]["writable"] is True and access["/work"]["writable"] is True)
        container, image = observation["container"], observation["image"]
        _require(container["Id"] not in ids and observation["session_id"] not in sessions)
        ids.add(container["Id"])
        sessions.add(observation["session_id"])
        _require(container["Image"] == image["Id"] == observation["image_id"])
        base_image = observation["base_image"]
        pin = packet["state"]["image_pin"].removeprefix("FROM ")
        _require(pin in base_image["RepoDigests"])
        base_layers = base_image["RootFS"]["Layers"]
        _require(base_layers and image["RootFS"]["Layers"][: len(base_layers)] == base_layers)
        _require(isinstance(observation["docker_version"], str) and observation["docker_version"])
        _require(container["Config"]["Entrypoint"] == ["python", "-B", "/app/server.py"])
        _require(container["HostConfig"]["Privileged"] is False)
        _require(
            next(r for r in order if r["case"] == case)
            == {"case": case, "container_id": container["Id"], "absent_after_trial": True}
        )
        config = TrialConfig.model_validate(native["config"])
        context: Any = SimpleNamespace(
            config=config,
            task=Task(BASE),
            _resolve_timeout_sec=lambda **kwargs: Trial._resolve_timeout_sec(context, **kwargs),
        )
        effective = observation["effective"]
        computed = {
            "agent.timeout_sec": Trial._compute_agent_timeout_sec(context),
            "verifier.timeout_sec": Trial._compute_verifier_timeout_sec(context),
            "environment.build_timeout_sec": Trial._compute_environment_build_timeout_sec(context),
            "environment.cpus": container["HostConfig"]["NanoCpus"] / 1_000_000_000,
            "environment.memory_mb": container["HostConfig"]["Memory"] / 1024 / 1024,
        }
        for field, value in computed.items():
            _require(type(effective[field]) in (int, float) and effective[field] == value)
            section, key = field.split(".")
            _require(value == declared[section][key])
            comparison.append(
                {
                    "case": case,
                    "field": field,
                    "declared": declared[section][key],
                    "observed": value,
                    "status": "match",
                    "observation_kind": "provider-effective-policy" if "timeout" in key else "docker-inspect",
                }
            )
        _require(effective["egress_control"] is True)
        _require(effective["network_policy"]["network_mode"] == "no-network")
        _require(effective["network_policy"]["allowed_hosts"] == [])
        _require(container["HostConfig"]["NetworkMode"].startswith("container:"))
        for field, declared_value, value, kind in (
            ("solver.uid", declared["agent"]["user"], actions["uid"], "solver-process"),
            (
                "environment.network_mode",
                declared["environment"]["network_mode"],
                "no-network",
                "Harbor controlled-egress policy; no outbound request",
            ),
            ("agent.model", None, native["config"]["agent"]["model_name"], "scripted NOP; no model"),
            ("agent.prompt", None, None, "not applicable to scripted socket probes"),
            ("tools", "/run/refund/ledger.sock", "/run/refund/ledger.sock", "actual solver calls"),
            (
                "python.version",
                "pinned image",
                observation["root"]["version"],
                "binary observation; pin binds distribution, not a version string",
            ),
            ("scheduler.actual_deadline_enforcement", None, None, "unknown; no deliberate timeout probe"),
        ):
            comparison.append(
                {
                    "case": case,
                    "field": field,
                    "declared": declared_value,
                    "observed": value,
                    "status": "unknown" if field == "scheduler.actual_deadline_enforcement" else "match",
                    "observation_kind": kind,
                }
            )
        controls.append(
            {
                "case": case,
                "uid": actions["uid"],
                "errors_preserve_exact_state": True,
                "initial_state_matches": True,
                "event_count": len(candidate["events"]),
                "reward": native["verifier_result"]["rewards"]["reward"],
                "container_id": container["Id"],
                "session_id": observation["session_id"],
            }
        )
    components = deepcopy(packet["components"])
    for name, component in components.items():
        component["source_sha256"] = hashlib.sha256(panel.read(panel.bundle / "task" / component["source"])).hexdigest()
        component["observed_in"] = list(observations)
        component["observed_identities"] = {
            case: (
                {
                    "harbor_version": document["provider_validation"]["version"],
                    "docker_version": observed["docker_version"],
                    "image_id": observed["image_id"],
                }
                if name == "harbor-docker-adapter"
                else {
                    "sha256": observed["root"]["files"][
                        "/usr/local/bin/python"
                        if name == "python-stdlib"
                        else "/tests/test.sh"
                        if name == "shell"
                        else "/tests/" + Path(component["source"]).name
                        if component["source"].startswith("tests/")
                        else "/app/" + Path(component["source"]).name
                    ]["sha256"]
                }
            )
            for case, observed in observations.items()
        }
    for row in comparison:
        row["declared_ref"] = {
            "path": "task/task.toml" if row["field"] != "python.version" else "task/environment/Dockerfile",
            "field": row["field"],
        }
        row["observed_ref"] = {
            "path": "trials/" + row["case"] + "/verifier/runtime-observation.json",
            "field": row["field"],
        }
        if row["field"] == "python.version":
            row["status"] = "justified"
        if row["field"] in {"agent.model", "agent.prompt"}:
            row["status"] = "not_applicable"
    return {
        "contract": packet,
        "provider_validation": document["provider_validation"],
        "configuration_comparison": comparison,
        "action_controls": controls,
        "components": components,
        "state_relation": packet["state"],
        "owner_review": "pending",
        "independence": False,
    }


def validate_pair(
    bundle: Path, document: dict[str, Any], sources: Any = None, task_path: str | None = None
) -> dict[str, Any]:
    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult
    from rubric_native_collect import BASE as current_base
    from rubric_native_collect import SCHEMA, contract, inventory, native_command, producer_identity

    _require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    panel = PairPanel(bundle, document, sources)
    expected_task = inventory(current_base)
    _require(inventory(bundle / "task") == expected_task)
    refs = []
    if sources is not None:
        selected = sources.root / task_path
        _require(inventory(selected) == expected_task)
        for row in expected_task:
            path = (selected / row["path"]).relative_to(sources.root).as_posix()
            raw = sources.source(path)
            _require(raw is not None and hashlib.sha256(raw).hexdigest() == row["sha256"])
            refs.append({"path": path, "digest": row["sha256"]})
    manifest = panel.object(bundle / "manifest.json")
    _require(_same(manifest["contract"], contract()) and _same(panel.object(bundle / "contract.json"), contract()))
    recorder.validate_contract(manifest["contract"])
    _require(manifest["task_dir"] == str(panel.origin / "task"))
    _require(manifest["attempts"] == str(panel.origin / "manifest.json.attempts"))
    _require(manifest["construction"] == {"status": "unmeasured"} and manifest["repair_count"] == 0)
    _require(panel.task_digest(bundle / "task") == manifest["task_sha256"])
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Task.checksum will be deprecated soon.*", category=DeprecationWarning
        )
        _require(Task(bundle / "task").checksum == manifest["harbor_task_checksum"])
    _require(
        set((bundle / "manifest.json.attempts").iterdir())
        == {
            bundle / "manifest.json.attempts" / (hashlib.sha256(case.encode()).hexdigest() + ".json")
            for case in ("oracle", "nop")
        }
    )
    _require(
        set((bundle / "receipts").iterdir()) == {bundle / "receipts" / (case + ".json") for case in ("oracle", "nop")}
    )
    _require(set((bundle / "trials").iterdir()) == {bundle / "trials" / case for case in ("oracle", "nop")})
    scorer = _trusted_module(current_base / "tests/refund_scorer.py", "_pair_scorer")
    reference = _trusted_module(current_base / "solution/reference.py", "_pair_reference")
    task_contract = panel.object(bundle / "task/environment/contract.json")
    controls = []
    ids = set()
    for case, expected in (("oracle", 1), ("nop", 0)):
        receipt_path = bundle / "receipts" / (case + ".json")
        receipt = panel.object(receipt_path)
        _require(receipt["run_id"] == case and receipt["case"] == case)
        _require(receipt["command"] == native_command(panel.origin, case))
        _require(
            receipt["manifest"] == str(panel.origin / "manifest.json")
            and receipt["result"] == str(panel.origin / "trials" / case / "result.json")
        )
        _require(receipt["purpose"] == "closure" and receipt["trace"] is None)
        attempt = panel.object(
            bundle / "manifest.json.attempts" / (hashlib.sha256(case.encode()).hexdigest() + ".json")
        )
        _require(
            attempt
            == {
                "run_id": case,
                "case": case,
                "kind": contract()["cases"][case]["kind"],
                "purpose": "closure",
                "receipt": str(panel.origin / "receipts" / (case + ".json")),
                "manifest_sha256": hashlib.sha256(panel.read(bundle / "manifest.json")).hexdigest(),
            }
        )
        control = audit.inspect_receipt(panel, receipt_path, receipt, {bundle / "task"}, include_integrity=True)
        _require(control["integrity"]["freshness"] == "current" and control["integrity"]["status"] == "consistent")
        if receipt["status"] == "infrastructure_error":
            _require(control["integrity"]["execution"] == "execution_error")
            result_path = bundle / "trials" / case / "result.json"
            if result_path.exists():
                native = panel.object(result_path)
                TrialResult.model_validate(native)
                _require(native["exception_info"] is not None or receipt.get("returncode") != 0)
            controls.append(
                {"run_id": case, "case": case, "status": "infrastructure_error", "reward": None, "control": control}
            )
            continue
        native = panel.object(bundle / "trials" / case / "result.json")
        TrialResult.model_validate(native)
        config = native["config"]
        _require(
            TrialConfig.model_validate(config)
            == TrialConfig.model_validate(panel.object(bundle / "trials" / case / "config.json"))
        )
        _require(native["id"] not in ids and native["trial_name"] == case and native["exception_info"] is None)
        ids.add(native["id"])
        _require(config["task"]["path"] == manifest["task_dir"] and config["agent"]["name"] == case)
        _require(config["environment"]["type"] == "docker" and config["environment"]["delete"] is True)
        _require(config["agent"]["model_name"] is None and native["agent_info"]["model_info"] is None)
        candidate = reference.solve(task_contract) if case == "oracle" else {"events": [], "success": False}
        checks = panel.object(bundle / "trials" / case / "verifier/checks.json")
        _require(_same(checks, scorer.evaluate(task_contract, candidate, grader="reviewed")))
        reward = native["verifier_result"]["rewards"]["reward"]
        _require(type(reward) in (int, float) and reward == checks["total_credit"] / checks["max_credit"])
        _require(float(panel.read(bundle / "trials" / case / "verifier/reward.txt")) == reward)
        status = "passed" if reward == expected else "behavior_failed"
        _require(receipt["status"] == status and control["integrity"]["execution"] == "healthy")
        controls.append(
            {"run_id": case, "case": case, "status": status, "reward": reward, "checks": checks, "control": control}
        )
    return {
        "scope": SCHEMA,
        "status": "unknown",
        "task_freshness": "current",
        "counts": {
            "attempts": len(controls),
            "completed": len(ids),
            "infrastructure_errors": sum(c["status"] == "infrastructure_error" for c in controls),
        },
        "native_execution": True,
        "human_review": False,
        "independence": False,
        "full_task_acceptance": False,
        "controls": controls,
        "task_sources": refs,
        "observations": {
            "provider": "harbor",
            "validator_version": version("harbor"),
            "route": "docker",
            "model": "not applicable: scripted controls",
            "coverage": "Oracle/NOP only; no access, reset, forged-output or independent probes",
        },
        "configuration_comparison": [],
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }
