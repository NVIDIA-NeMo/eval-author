# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compare frozen Gym observations with exact replay and a stateful HTTP service."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus-v1"
SCRIPTS = ROOT.parents[2] / "scripts"
FREEZE_HASH = "8748d7d046815048534a5e4e5e1a43cb4b83fbd71df9ec135c1b72bcd9762c52"
PROVENANCE_HASH = "bef7dc69c9288755e26b578fd80d6ccda06d0d2b4d9bd718635157cde9414362"
TOOLS = ("email_search_emails", "email_get_email_information_by_id", "email_reply_email")
FIELDS = {"email_id", "inbox/outbox", "sender/recipient", "subject", "sent_datetime", "body"}


SUPPORT_BOUNDARY = {
    "basis": "Preexisting documented bounded implementation, not observed service responses",
    "documentation": "examples/workplace/README.md#supported-behavior",
    "documentation_sha256": "13f6369302690d7d53c1565b3ddf479eb8007d49b78184272d665141ff72e249",
    "backend_contract_sha256": "6c9a53c2bdbad9e0ab501ae4472d8cf6eae06badad4942b47c4d871b9dc3786e",
    "rules": {
        "invalid_arguments": "Missing required keys, additional keys, or wrong JSON types in original upstream schemas",
        "page_size": "Search page_size is not positive",
        "date_format": "Nonempty search date limit is not a valid YYYY-MM-DD date",
        "date_ties": "Two query-matching pre-state rows have equal timestamps before date filters or pagination",
        "id_format": "Reply allocation's lexicographic maximum ID is not integer-convertible",
        "id_collision": "An otherwise valid reply would allocate an ID already present in pre-state",
    },
    "default": "Require matched output and observed matched after-state. Unsupported needs the prescribed code.",
    "tie_justification": "Original contract excludes equal matching timestamps because upstream sort order is unspecified. Predicate applies to pre-state, not case labels or actual responses.",
}


def allowed_unsupported(event: dict, schemas: list[dict]) -> str | None:
    spec = next(item["parameters"] for item in schemas if item["name"] == event["name"])
    arguments = event["arguments"]
    if set(spec["required"]) - arguments.keys() or arguments.keys() - spec["properties"].keys():
        return "invalid_arguments"
    for key, value in arguments.items():
        expected = str if spec["properties"][key]["type"] == "string" else int
        if type(value) is not expected:
            return "invalid_arguments"
    if event["name"] == "email_search_emails":
        if arguments.get("page_size", 5) <= 0:
            return "page_size"
        for key in ("date_min", "date_max"):
            value = arguments.get(key, "")
            if value:
                try:
                    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                        return "date_format"
                    date.fromisoformat(value)
                except ValueError:
                    return "date_format"
        words = arguments.get("query", "").lower().split()
        matching = [
            row
            for row in event["before_state"]
            if all(
                word in " ".join(row[field] for field in ("subject", "body", "sender/recipient")).lower()
                for word in words
            )
        ]
        timestamps = [datetime.fromisoformat(row["sent_datetime"]) for row in matching]
        if len(timestamps) != len(set(timestamps)):
            return "date_ties"
    elif event["name"] == "email_reply_email" and arguments["email_id"] and arguments["body"]:
        rows = event["before_state"]
        sources = [row for row in rows if row["email_id"] == arguments["email_id"]]
        if sources and "@" in sources[0]["sender/recipient"] and "." in sources[0]["sender/recipient"]:
            try:
                next_id = str(int(max(row["email_id"] for row in rows)) + 1)
            except ValueError:
                return "id_format"
            if next_id in {row["email_id"] for row in rows}:
                return "id_collision"
    return None


def after_failure(row: dict) -> bool:
    if row["state"] != "matched":
        return True
    if row["unsupported"]:
        reason = row["unsupported_reason"]
        return (
            row["allowed_unsupported_code"] is None
            or not isinstance(reason, dict)
            or reason.get("code") != row["allowed_unsupported_code"]
        )
    return any(row[key] != "matched" for key in ("output", "output_schema", "error_semantics"))


def capture_runtime(service: Path, scratch: Path) -> tuple[Path, dict]:
    """Capture runtime-local files once, without bytecode or symlinks."""
    source = service.resolve().parent
    paths = sorted(
        path
        for path in source.rglob("*")
        if "__pycache__" not in path.parts and path.suffix != ".pyc" and path.is_file()
    )
    if service.resolve() not in paths:
        raise ValueError("Service entrypoint does not exist")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Runtime closure must not contain symlinks")
    captured = {path.relative_to(source).as_posix(): path.read_bytes() for path in paths}
    if any(path.read_bytes() != captured[path.relative_to(source).as_posix()] for path in paths):
        raise ValueError("Runtime changed during capture")
    destination = Path(tempfile.mkdtemp(prefix="runtime-frozen-", dir=scratch))
    hashes = {}
    for relative, data in captured.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o400)
        hashes[relative] = hashlib.sha256(data).hexdigest()
    receipt = {
        "source_directory": str(source),
        "snapshot_directory": str(destination),
        "files": hashes,
        "closure_sha256": hashlib.sha256(canonical(hashes).encode()).hexdigest(),
        "captured_before_execution": True,
    }
    save(destination / "snapshot-manifest.json", receipt)
    for directory in sorted((path for path in destination.rglob("*") if path.is_dir()), reverse=True):
        directory.chmod(0o500)
    destination.chmod(0o500)
    return destination / service.name, receipt


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def checked_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Manifest path escapes corpus")
    return path


def validate_corpus(root: Path = CORPUS) -> dict:
    if sha(root / "freeze.json") != FREEZE_HASH:
        raise ValueError("Frozen manifest hash mismatch")
    if sha(root / "provenance.json") != PROVENANCE_HASH:
        raise ValueError("Observation provenance hash mismatch")
    manifest = load(root / "freeze.json")
    provenance = load(root / "provenance.json")
    if provenance["freeze_sha256"] != FREEZE_HASH:
        raise ValueError("Observation provenance does not bind frozen inputs")
    checks = {**manifest["files"], **provenance["observations"], "schemas.json": provenance["schemas_sha256"]}
    for relative, expected in checks.items():
        if sha(checked_path(root, relative)) != expected:
            raise ValueError(f"Corpus tampering: {relative}")
    identities: set[str] = set()
    seeds: set[str] = set()
    ids: set[str] = set()
    for item in manifest["sessions"]:
        session = load(checked_path(root, item["inputs"]))
        if item["split"] not in ("development", "heldout") or not item["inputs"].startswith(item["split"] + "/"):
            raise ValueError("Split path mismatch")
        if session["session_id"] != item["session_id"] or session["split"] != item["split"]:
            raise ValueError("Session identity mismatch")
        seed_hash = hashlib.sha256(json.dumps(session["seed"], sort_keys=True).encode()).hexdigest()
        if seed_hash != item["seed_sha256"]:
            raise ValueError("Seed identity mismatch")
        session_ids = {row["email_id"] for row in session["seed"]}
        if item["session_id"] in identities or seed_hash in seeds or session_ids & ids:
            raise ValueError("Session or seed leakage")
        identities.add(item["session_id"])
        seeds.add(seed_hash)
        ids.update(session_ids)
        if any(set(row) != FIELDS or not row["email_id"].isdigit() for row in session["seed"]):
            raise ValueError("Invalid seed row")
        observation = load(root / item["inputs"].replace(".inputs.json", ".observations.json"))
        if observation["session_id"] != session["session_id"] or observation["split"] != session["split"]:
            raise ValueError("Observation split mismatch")
        if len(observation["events"]) != len(session["calls"]):
            raise ValueError("Observation count mismatch")
        previous = session["seed"]
        for index, (call, event) in enumerate(zip(session["calls"], observation["events"], strict=True)):
            if any(event[key] != value for key, value in call.items()) or event["index"] != index:
                raise ValueError("Observation call mismatch")
            if previous != event["before_state"]:
                raise ValueError("Observation state chain mismatch")
            previous = event["after_state"]
    return manifest


def compile_replay(root: Path, destination: Path) -> dict:
    """Read development only. Ambiguous input keys have no replay case."""
    from jsonschema import Draft202012Validator

    schemas = load(root / "schemas.json")
    indexed: dict[str, dict[str, list[dict]]] = {name: defaultdict(list) for name in TOOLS}
    sources = []
    for path in sorted((root / "development").glob("*.observations.json")):
        session = load(path)
        if session["split"] != "development" or not session["session_id"].startswith("development-"):
            raise ValueError("Heldout observations cannot train replay")
        sources.append({"path": str(path.relative_to(root)), "sha256": sha(path)})
        for event in session["events"]:
            indexed[event["name"]][canonical(event["arguments"])].append(event)
    tools = []
    conflicts = []
    excluded = []
    for schema in schemas:
        validator = Draft202012Validator(schema["parameters"])
        cases = []
        for arguments, events in indexed[schema["name"]].items():
            signatures = {canonical({"output": event["output"], "exception": event["exception"]}) for event in events}
            key = {"name": schema["name"], "arguments": json.loads(arguments)}
            if len(signatures) != 1:
                conflicts.append(key)
                continue
            if not validator.is_valid(json.loads(arguments)) or events[0]["exception"] is not None:
                excluded.append(key)
                continue
            cases.append(
                {
                    "arguments": json.loads(arguments),
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(events[0]["output"], ensure_ascii=True)}],
                        "isError": False,
                    },
                }
            )
        tools.append(
            {
                "definition": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "inputSchema": schema["parameters"],
                },
                "cases": cases,
            }
        )
    scenario = {
        "schema": "nemo.eval_author.trace_environment_mcp_scenario.v2",
        "unknown_call_policy": "error",
        "tools": tools,
    }
    save(destination, scenario)
    return {
        "training_split": "development",
        "training_sources": sources,
        "conflicts_unsupported": conflicts,
        "malformed_or_exception_excluded": excluded,
        "scenario_sha256": sha(destination),
        "runtime": str(SCRIPTS / "replay_mcp_server.py"),
        "runtime_sha256": sha(SCRIPTS / "replay_mcp_server.py"),
    }


class Replay:
    def __init__(self, scenario: Path):
        self.process = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "replay_mcp_server.py"), "--scenario", str(scenario)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.counter = 0

    def request(self, method: str, params: dict) -> dict:
        self.counter += 1
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}) + "\n"
        )
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Replay runtime stopped")
        return json.loads(line)["result"]

    def call(self, event: dict) -> dict:
        result = self.request("tools/call", {"name": event["name"], "arguments": event["arguments"]})
        if result.get("isError"):
            return {"output": None, "unsupported": True, "reason": result["content"]}
        return {"output": json.loads(result["content"][0]["text"]), "unsupported": False}

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=10)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


def http_json(url: str, payload: dict | None = None) -> Any:
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Service HTTP {error.code}: {error.read().decode()}") from error


@contextmanager
def service_session(service: Path, seed: list[dict], scratch: Path, python: str) -> Iterator[tuple[str, Path]]:
    with tempfile.TemporaryDirectory(prefix="session-", dir=scratch) as directory:
        work = Path(directory)
        seed_path = work / "seed.json"
        state = work / "state"
        state.mkdir(mode=0o700)
        save(seed_path, seed)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (work / "service.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    python,
                    "-B",
                    str(service),
                    "--host",
                    "127.0.0.1",
                    "--seed",
                    str(seed_path),
                    "--state-dir",
                    str(state),
                    "--port",
                    str(port),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(f"Service exited: {log.read()}")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Service startup timed out")
                yield f"http://127.0.0.1:{port}", state
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted({canonical(shape(item)) for item in value})
    return type(value).__name__


def compare(event: dict, response: dict, after_state: list[dict] | None) -> dict:
    if not isinstance(response, dict) or not isinstance(response.get("unsupported"), bool) or "output" not in response:
        raise ValueError("Service must return {output: ..., unsupported: bool}")
    unsupported = response["unsupported"]
    exception = response.get("exception")
    output_match = canonical(event["output"]) == canonical(response["output"]) and event["exception"] == exception
    state_match = None if after_state is None else canonical(event["after_state"]) == canonical(after_state)
    return {
        "tool": event["name"],
        "label": event["label"],
        "category": event["category"],
        "unsupported": unsupported,
        "output": "unsupported" if unsupported else ("matched" if output_match else "mismatch"),
        "output_schema": "unsupported"
        if unsupported
        else ("matched" if shape(event["output"]) == shape(response["output"]) else "mismatch"),
        "error_semantics": "unsupported"
        if unsupported
        else (
            "matched"
            if event["exception"] == exception
            and (not isinstance(event["output"], str) or event["output"] == response["output"])
            else "mismatch"
        ),
        "state": "unavailable" if state_match is None else ("matched" if state_match else "mismatch"),
        "expected_output": event["output"],
        "actual_output": response["output"],
        "expected_exception": event["exception"],
        "actual_exception": exception,
        "unsupported_reason": response.get("error", response.get("reason")),
        "expected_after_state": event["after_state"],
        "actual_after_state": after_state,
    }


def rates(rows: list[dict]) -> dict:
    report = {}
    for name in TOOLS:
        selected = [row for row in rows if row["tool"] == name]
        counts = {
            status: sum(row["output"] == status for row in selected)
            for status in ("matched", "mismatch", "unsupported")
        }
        denominator = len(selected)
        report[name] = {
            "denominator": denominator,
            **counts,
            "matched_rate": counts["matched"] / denominator if denominator else None,
            "dimensions": {
                dimension: {
                    status: sum(row[dimension] == status for row in selected)
                    for status in ("matched", "mismatch", "unsupported", "unavailable")
                }
                for dimension in ("output_schema", "error_semantics", "state")
            },
        }
    return report


def schema_semantics(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: schema_semantics(item) for key, item in value.items() if key != "description"}
    if isinstance(value, list):
        return [schema_semantics(item) for item in value]
    return value


def capture_mcp_schema(service: Path, python: str, expected: list[dict]) -> dict:
    mcp = service.with_name("mcp_server.py")
    if not mcp.exists():
        return {"status": "unavailable"}
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    result = subprocess.run(
        [python, "-B", str(mcp), "--url", "http://127.0.0.1:1"],
        input=json.dumps(request) + "\n",
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    tools = json.loads(result.stdout)["result"]["tools"]
    actual_index = {item["name"]: item for item in tools}
    expected_index = {item["name"]: item for item in expected}
    return {
        "status": "captured",
        "tools": tools,
        "exact_definition_match": actual_index == expected_index,
        "validation_semantics_match": schema_semantics(actual_index) == schema_semantics(expected_index),
        "note": "Descriptions remain in the exact comparison and are excluded only from validation semantics.",
    }


def run(root: Path, report_path: Path, scratch: Path, service: Path | None, python: str, state_file: str) -> int:
    manifest = validate_corpus(root)
    scratch.mkdir(parents=True, exist_ok=True)
    boundary_path = scratch / "support-boundary.json"
    if boundary_path.exists() and load(boundary_path) != SUPPORT_BOUNDARY:
        raise ValueError("Support boundary changed in an existing run directory")
    if not boundary_path.exists():
        save(boundary_path, SUPPORT_BOUNDARY)
    boundary_hash = sha(boundary_path)
    runner_hash = sha(Path(__file__))
    runtime_receipt = None
    if service is not None:
        service, runtime_receipt = capture_runtime(service, scratch)
    scenario_path = scratch / "development-replay.json"
    before_build = compile_replay(root, scenario_path)
    replay = Replay(scenario_path)
    before = []
    after = []
    schema_check = {"before": None, "after": {"status": "unavailable"}}
    schemas = load(root / "schemas.json")
    expected_tools = [
        {"name": item["name"], "description": item["description"], "inputSchema": item["parameters"]}
        for item in schemas
    ]
    try:
        schema_check["before"] = replay.request("tools/list", {})["tools"] == expected_tools
        if service is not None:
            schema_check["after"] = capture_mcp_schema(service, python, expected_tools)
        for item in manifest["sessions"]:
            if item["split"] != "heldout":
                continue
            session = load(root / item["inputs"])
            observed = load(root / item["inputs"].replace(".inputs.json", ".observations.json"))
            for event in observed["events"]:
                before.append({"session_id": item["session_id"], **compare(event, replay.call(event), session["seed"])})
            if service is not None:
                with service_session(service, session["seed"], scratch, python) as (url, state_dir):
                    for event in observed["events"]:
                        allowed_code = allowed_unsupported(event, schemas)
                        response = http_json(url + "/call", {"name": event["name"], "arguments": event["arguments"]})
                        path = state_dir / state_file
                        state = load(path) if path.exists() else None
                        if isinstance(state, dict):
                            state = state.get("state")
                        after.append(
                            {
                                "session_id": item["session_id"],
                                "allowed_unsupported_code": allowed_code,
                                "support_boundary": "required" if allowed_code is None else "documented_exclusion",
                                **compare(event, response, state),
                            }
                        )
    finally:
        replay.close()
    unexpected = [row for row in after if after_failure(row)]
    schema_failure = (
        isinstance(schema_check["after"], dict) and schema_check["after"].get("validation_semantics_match") is False
    )
    gaps = []
    if service is None:
        gaps.append("Stateful service was not supplied. HTTP and after-state comparisons were not executed.")
    else:
        if any(row["state"] == "unavailable" for row in after):
            gaps.append("Some persisted after-state observations were unavailable.")
        if schema_check["after"].get("status") != "captured":
            gaps.append("Runtime MCP schema was unavailable.")
        elif not schema_check["after"]["exact_definition_match"]:
            gaps.append("Runtime MCP descriptions differ from the captured upstream definitions.")
    report = {
        "disclosure": manifest["disclosure"],
        "freeze_sha256": FREEZE_HASH,
        "provenance_sha256": PROVENANCE_HASH,
        "before_build": before_build,
        "schema": schema_check,
        "before": rates(before),
        "after": rates(after) if service is not None else None,
        "category_results": {
            phase: {
                category: rates([row for row in rows if row["category"] == category])
                for category in ("supported", "malformed", "unsupported")
            }
            for phase, rows in (("before", before), ("after", after))
        },
        "before_cases": before,
        "after_cases": after,
        "unexpected_after_mismatches": len(unexpected),
        "gaps": gaps,
        "after_runtime": runtime_receipt["files"] if runtime_receipt else None,
        "runtime_snapshot": runtime_receipt,
        "support_boundary": {
            "definition": SUPPORT_BOUNDARY,
            "sha256": boundary_hash,
            "predicate_runner_sha256": runner_hash,
        },
        "split_scope": "Identity-heldout templates with disjoint seeds and session IDs, not independent novel scenarios",
        "required_support_results": rates([row for row in after if row["allowed_unsupported_code"] is None]),
        "documented_exclusions": [
            {
                "session_id": row["session_id"],
                "label": row["label"],
                "category": row["category"],
                "code": row["allowed_unsupported_code"],
            }
            for row in after
            if row["allowed_unsupported_code"] is not None
        ],
        "state_contract": state_file,
    }
    save(report_path, report)
    print(
        json.dumps(
            {
                "report": str(report_path),
                "before": report["before"],
                "after": report["after"],
                "unexpected_after_mismatches": len(unexpected),
            }
        )
    )
    return 1 if unexpected or schema_failure else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--service", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--state-file", default="state.json")
    args = parser.parse_args()
    return run(args.corpus, args.report, args.scratch, args.service, args.python, args.state_file)


if __name__ == "__main__":
    raise SystemExit(main())
