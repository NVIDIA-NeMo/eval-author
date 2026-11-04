# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public Tier 3 facts from bounded, private Harbor receipts; never log text."""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import contextmanager
from itertools import islice
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MAX_FILES = 64
MAX_ENTRIES = 64
MAX_EVENTS = 32
MAX_TOTAL_BYTES = 8_000_000
SCHEMA = "nemo.eval_author.agent_diagnostics.v1"
EXCEPTIONS = {
    "NonZeroAgentExitCodeError": "agent_command_failed",
    "AgentSetupTimeoutError": "agent_install_timeout",
    "AgentTimeoutError": "agent_execution_timeout",
    "VerifierTimeoutError": "verifier_timeout",
    "CancelledError": "cancelled",
}
ERROR_NAMES = {"APIError", "ProviderAuthError", "UnknownError", "MessageOutputLengthError", "ContextOverflowError"}
HTTP_CATEGORIES = {
    400: "invalid_request",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    408: "request_timeout",
    413: "request_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "provider_unavailable",
    502: "provider_unavailable",
    503: "provider_unavailable",
    504: "request_timeout",
}


@contextmanager
def directory(path):
    # Open every ancestor without following links, including above the owned root.
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def directories(path):
    with directory(path) as fd, os.scandir(fd) as entries:
        children = list(islice(entries, MAX_ENTRIES + 1))
        if len(children) > MAX_ENTRIES:
            raise ValueError("directory limit")
        return sorted(path / entry.name for entry in children if entry.is_dir(follow_symlinks=False))


def native_run(target, skill):
    root = target / "results" / skill
    runs = directories(root)
    if len(runs) != 1:
        raise ValueError("ambiguous native run")
    return runs[0]


def read_regular(path, limit=MAX_FILE_BYTES):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError("unsafe or oversized receipt")
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("receipt grew beyond limit")
            return raw


def object_value(value):
    return value if isinstance(value, dict) else {}


def error_event(event):
    if not isinstance(event, dict) or event.get("type") != "error":
        return None
    error = object_value(event.get("error"))
    name = error.get("name")
    safe = {"category": "unknown"}
    if isinstance(name, str) and name in ERROR_NAMES:
        safe["name"] = name
    data = object_value(error.get("data"))
    # Only APIError's typed status/retryability describes a provider response.
    if name == "APIError":
        status = data.get("statusCode")
        if type(status) is int and 100 <= status <= 599:
            safe["status_code"] = status
            safe["category"] = HTTP_CATEGORIES.get(status, "unknown")
        if type(data.get("isRetryable")) is bool:
            safe["retryable"] = data["isRetryable"]
    elif name == "ProviderAuthError":
        safe["category"] = "authentication_failed"
    elif name in ("MessageOutputLengthError", "ContextOverflowError"):
        safe["category"] = "context_limit"
    return safe


def observed_epipe(raw):
    # Match whole non-JSON blocks only. This is a signature, not authenticated
    # runtime telemetry: identical raw tool output cannot be distinguished here.
    pattern = (
        r"(?:[0-9]{1,6} \|[^\n]*\n)*"
        r"[0-9]{1,6} \| +throw er;\n"
        r" +\^\nerror: EPIPE: broken pipe, write\n"
        r" +at emitError \(node:events:[0-9]{1,6}:[0-9]{1,6}\)\n"
        r" +at emitErrorNT \(internal:streams/destroy:[0-9]{1,6}:[0-9]{1,6}\)\n"
        r" +at emitErrorCloseNT \(internal:streams/destroy:[0-9]{1,6}:[0-9]{1,6}\)\n?"
    )
    decoder = json.JSONDecoder()
    position = 0
    block = []
    while position < len(raw):
        end = raw.find("\n", position)
        end = len(raw) if end < 0 else end + 1
        line = raw[position:end]
        stripped = line.lstrip()
        try:
            _, consumed = decoder.raw_decode(raw, position + len(line) - len(stripped))
            if consumed < end and raw[consumed:end].strip():
                raise ValueError("not a JSON physical line")
        except (ValueError, RecursionError):
            # Do not interpret malformed/multiline JSON contents as raw output.
            if stripped.startswith(("{", "[", '"')):
                return False
            block.append(line)
        else:
            if re.fullmatch(pattern, "".join(block)):
                return True
            block.clear()
            newline = raw.find("\n", consumed)
            end = len(raw) if newline < 0 else newline + 1
        position = end
    return re.fullmatch(pattern, "".join(block)) is not None


def execution_facts(report):
    report = object_value(report)
    agent = object_value(object_value(report.get("agents")).get("opencode"))
    conditions = object_value(agent.get("conditions"))
    result = {}
    for label, item in (
        ("report", report),
        ("opencode", agent),
        ("with_skill", conditions.get("with_skill")),
        ("without_skill", conditions.get("without_skill")),
    ):
        item = object_value(item)
        fields = {}
        if item.get("execution_status") in ("succeeded", "failed", "incomplete", "not_run", "skipped"):
            fields["execution_status"] = item["execution_status"]
        for key in ("expected_attempts", "scored_attempts"):
            if type(item.get(key)) is int and 0 <= item[key] <= 1000:
                fields[key] = item[key]
        if fields:
            result[label] = fields
    return result


def unavailable(report=None):
    return {
        "schema": SCHEMA,
        "capture_status": "unavailable",
        "files_read": 0,
        "events": [],
        "opencode_reported_versions": [],
        "execution": execution_facts(report),
    }


def capture(report, target, skill):
    result = unavailable(report)
    versions = set()
    total_bytes = 0
    partial = False

    def read(path):
        nonlocal total_bytes, partial
        try:
            if result["files_read"] >= MAX_FILES:
                raise ValueError("file limit")
            raw = read_regular(path, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total_bytes))
            total_bytes += len(raw)
            result["files_read"] += 1
            return raw.decode("utf-8")
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            partial = True
            return None

    def append(event):
        nonlocal partial
        if len(result["events"]) < MAX_EVENTS:
            result["events"].append(event)
        else:
            partial = True

    try:
        run = native_run(target, skill)
        jobs = run / "_harbor-jobs"
        for job, phase in (
            ("runtime-preflight-opencode", "preflight"),
            (f"{skill}-opencode-with", "with_skill"),
            (f"{skill}-opencode-without", "without_skill"),
        ):
            try:
                trials = directories(jobs / job)
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                partial = True
                continue
            for index, trial in enumerate(trials):
                provenance = {"phase": phase, "trial": index}
                failure = None
                raw = read(trial / "result.json")
                if raw is not None:
                    try:
                        data = json.loads(raw)
                        if not isinstance(data, dict):
                            raise ValueError("invalid trial receipt")
                        exception = object_value(data.get("exception_info"))
                        if exception:
                            name = exception.get("exception_type")
                            known = isinstance(name, str) and name in EXCEPTIONS
                            failure = {
                                "source": "harbor_trial",
                                **provenance,
                                "category": EXCEPTIONS[name] if known else "unknown",
                                **({"exception_type": name} if known else {}),
                            }
                            append(failure)
                        info = object_value(data.get("agent_info"))
                        version = info.get("version")
                        if (
                            info.get("name") == "opencode"
                            and isinstance(version, str)
                            and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", version)
                        ):
                            versions.add(version)
                    except (ValueError, RecursionError):
                        partial = True
                raw = read(trial / "agent/opencode.txt")
                if raw is not None:
                    if (
                        failure is not None
                        and failure.get("exception_type") == "NonZeroAgentExitCodeError"
                        and observed_epipe(raw)
                    ):
                        failure["observed_signature"] = "bun_node_epipe"
                    for line in raw.splitlines():
                        try:
                            event = error_event(json.loads(line))
                            if event is not None:
                                append({"source": "opencode_error_event", **provenance, **event})
                        except (ValueError, RecursionError):
                            partial = True
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        partial = True
    result["opencode_reported_versions"] = sorted(versions)
    result["capture_status"] = "partial" if partial else ("captured" if result["files_read"] else "unavailable")
    return result


def render(evidence):
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)


def public_projection(value):
    value = object_value(value)
    result = unavailable()
    if value.get("schema") != SCHEMA:
        return result
    if value.get("capture_status") in ("unavailable", "captured", "partial"):
        result["capture_status"] = value["capture_status"]
    if type(value.get("files_read")) is int and 0 <= value["files_read"] <= MAX_FILES:
        result["files_read"] = value["files_read"]
    versions = value.get("opencode_reported_versions")
    if isinstance(versions, list):
        result["opencode_reported_versions"] = sorted(
            {
                v
                for v in versions[:MAX_FILES]
                if isinstance(v, str) and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", v)
            }
        )
    execution = object_value(value.get("execution"))
    result["execution"] = execution_facts(
        {
            **object_value(execution.get("report")),
            "agents": {
                "opencode": {
                    **object_value(execution.get("opencode")),
                    "conditions": {arm: execution.get(arm) for arm in ("with_skill", "without_skill")},
                }
            },
        }
    )
    events = value.get("events")
    for event in events[:MAX_EVENTS] if isinstance(events, list) else []:
        event = object_value(event)
        if (
            event.get("phase") not in ("preflight", "with_skill", "without_skill")
            or type(event.get("trial")) is not int
            or not 0 <= event["trial"] < MAX_ENTRIES
        ):
            continue
        source = event.get("source")
        if source == "opencode_error_event":
            fields = error_event(
                {
                    "type": "error",
                    "error": {
                        "name": event.get("name"),
                        "data": {"statusCode": event.get("status_code"), "isRetryable": event.get("retryable")},
                    },
                }
            )
        elif source == "harbor_trial":
            name = event.get("exception_type")
            fields = {"category": "unknown"}
            if isinstance(name, str) and name in EXCEPTIONS:
                fields.update(category=EXCEPTIONS[name], exception_type=name)
                if name == "NonZeroAgentExitCodeError" and event.get("observed_signature") == "bun_node_epipe":
                    fields["observed_signature"] = "bun_node_epipe"
        else:
            continue
        result["events"].append({"source": source, "phase": event["phase"], "trial": event["trial"], **fields})
    return result
