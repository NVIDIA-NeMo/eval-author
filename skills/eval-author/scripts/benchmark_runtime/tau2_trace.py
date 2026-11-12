# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline, hash-bound projection of the text-only Tau mini airline event protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from importlib.metadata import version
from pathlib import Path

from trace_ingest import Span, SpanKind, Trace
from trace_loading import load_snapshot
from working_paths import atomic_write, json_bytes

EVIDENCE_CLASSES = {"primary_unhinted", "hinted_feasibility", "reference_control", "diagnostic"}
RUNTIME_FILES = {"bootstrap.py", "client.py", "harness.py", "run.py"}
SCHEMA_VERSION = "3"
MANIFEST_HELP = """Input manifest (JSON; all paths absolute and SHA256 over exact source bytes):
  {"schema":"tau-mini-trace-input.v1", "run_id":"unique-run/task/seed/repeat",
   "evidence_class":"primary_unhinted", "task_sha256":"...",
   "result":{"path":"...", "sha256":"..."},
   "trace":{"path":"...", "sha256":"..."},
   "config":{"path":"...", "sha256":"..."},
   "selection":{"path":"...", "sha256":"..."},
   "runtime_sources":{"bootstrap.py":{"path":"...", "sha256":"..."},
                      "client.py":{"path":"...", "sha256":"..."},
                      "harness.py":{"path":"...", "sha256":"..."},
                      "run.py":{"path":"...", "sha256":"..."}}}
task_sha256 hashes working_paths.json_bytes(start_event["task"]): UTF-8 JSON,
indent=2, ensure_ascii=False, allow_nan=False, trailing newline, original key order.
config and selection are the mini runner's recorded files. Their hashes and the
explicit runtime source hashes are verified without importing/executing the mini.
Evidence classes: primary_unhinted, hinted_feasibility, reference_control, diagnostic.
Only primary_unhinted maps to audit evidence class agent_under_test; this records
the supplied selection, not a new admission/acceptance decision.
Supported slice: airline text events, no preloaded tools or simulator tools;
globally reused tool-call IDs and endpoint extra_body overrides are rejected.
Output: trace.jsonl (Compass Trace v3, validated through the shared loader), exact
source.{result,manifest,config,selection}.json and source.trace.jsonl, then
conversion-receipt.json. Output is private; raw trace retains private scenarios,
criteria and complete responses. Conversion makes no network calls or scores.
"""


class ConversionError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ConversionError(message)


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(payload):
    def invalid(value):
        raise ConversionError(f"Non-finite JSON number: {value}")

    return json.loads(payload, object_pairs_hook=_pairs, parse_constant=invalid)


def _history(messages, participant):
    own_role = "assistant" if participant == "agent" else "user"
    history = []
    for message in messages:
        if message["role"] == "tool":
            if message["requestor"] == own_role:
                history.append({"role": "tool", "tool_call_id": message["id"], "content": message["content"]})
            continue
        own = message["role"] == own_role
        if message.get("tool_calls") and not own:
            continue
        role = message["role"] if participant == "agent" else ("assistant" if own else "user")
        item = {"role": role, "content": message.get("content")}
        if message.get("tool_calls"):
            item["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
                }
                for c in message["tool_calls"]
            ]
        history.append(item)
    return history


def _message_step(message, line, *, initial=False, copied=False):
    require(message.get("role") in {"assistant", "user"}, f"trace line {line}: unsupported message role")
    require(
        message.get("content") is None or isinstance(message["content"], str),
        f"trace line {line}: text-only message required",
    )
    require(not message.get("is_audio"), f"trace line {line}: audio is unsupported")
    step = {
        "source": "agent" if message["role"] == "assistant" else "user",
        "message": message.get("content") or "",
        "extra": {"native_message": message, "event_line": line},
    }
    if message.get("timestamp"):
        step["timestamp"] = message["timestamp"]
    if initial:
        step.update(llm_call_count=0, is_copied_context=copied)
        step["extra"]["origin"] = "initial_history" if copied else "bootstrap_greeting"
    return step


def _usage(response):
    usage = response.get("usage")
    if usage is None:
        return None
    require(isinstance(usage, dict), "Completion usage must be an object")
    metrics = {"extra": {"native_usage": usage}}
    for key in ("prompt_tokens", "completion_tokens"):
        if usage.get(key) is not None:
            require(type(usage[key]) is int and usage[key] >= 0, f"Invalid {key}")
            metrics[key] = usage[key]
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is not None:
        require(type(cached) is int and cached >= 0, "Invalid cached_tokens")
        metrics["cached_tokens"] = cached
    return metrics


def _expected_request(endpoint, messages, tools, seed):
    require(not endpoint.get("extra_body"), "Endpoint overrides require a separate reviewed adapter")
    request = {
        "model": endpoint["model"],
        "messages": messages,
        "temperature": endpoint.get("temperature", 0.0),
        endpoint.get("token_parameter", "max_tokens"): endpoint.get("max_tokens", 4096),
        "seed": seed,
    }
    if tools:
        request.update(tools=tools, tool_choice="auto")
    return request


def validate_event_projection(result, events, config):
    """Require the native event stream to reconstruct the exact persisted messages."""
    require(len(events) >= 3, "Expected start, initial_history and terminal result")
    start = events[0]
    require(
        start.get("event") == "start" and start.get("domain") == "airline", "Only airline start events are supported"
    )
    require(events[1].get("event") == "initial_history", "Missing initial_history at trace line 2")
    require(events[-1] == {"event": "result", **result}, "Terminal trace result differs from result file")
    for key in ("domain", "task_id", "seed"):
        require(start.get(key) == result.get(key), f"Start/result {key} mismatch")
    require(start["task"]["id"] == result["task_id"], "Native task identity mismatch")
    initial = events[1]["messages"]
    supplied_history = (start["task"].get("initial_state") or {}).get("message_history") or []
    if supplied_history:
        require(initial == supplied_history, "Initial history differs from native task")
    else:
        require(
            len(initial) == 1
            and initial[0].get("role") == "assistant"
            and initial[0].get("content") == "Hi! How can I help you today?",
            "Unrecognized bootstrap greeting",
        )
    steps, messages, mapping = [], list(initial), []
    for offset, message in enumerate(initial):
        require(
            message.get("role") != "tool" and not message.get("tool_calls"),
            "trace line 2: preloaded tool history is unsupported",
        )
        steps.append(_message_step(message, 2, initial=True, copied=bool(supplied_history)))
        mapping.append({"event_line": 2, "initial_message_index": offset, "step_index": len(steps) - 1})
    active = None
    completion = None
    call_steps, calls, outstanding = {}, {}, []
    auxiliary, unprojected = [], []
    agent_policy = None
    agent_tools = None
    last_step = -1
    tool_batch_step = None
    failed = False
    judge_request = None
    judging = False
    for line, event in enumerate(events[2:-1], 3):
        kind = event.get("event")
        where = f"trace line {line} ({kind})"
        require(not failed, f"{where}: event after terminal error")
        if kind == "request":
            require(not judging, f"{where}: dialogue resumed after scoring")
            require(
                active is None and not outstanding and judge_request is None,
                f"{where}: request before previous operation completed",
            )
            participant = event.get("participant")
            require(participant in {"agent", "user"}, f"{where}: unknown participant")
            require(
                type(event.get("step")) is int and event["step"] == last_step + 1,
                f"{where}: nonsequential generation step",
            )
            last_step = event["step"]
            history = event.get("messages")
            require(
                isinstance(history, list)
                and history
                and history[0].get("role") == "system"
                and isinstance(history[0].get("content"), str),
                f"{where}: missing system prompt",
            )
            require(
                history[1:] == _history(messages, participant), f"{where}: model history differs from recorded dialogue"
            )
            require(isinstance(event.get("tools"), list), f"{where}: tools must be a list")
            if participant == "agent":
                if agent_policy is None:
                    agent_policy, agent_tools = history[0]["content"], event["tools"]
                require(
                    (agent_policy, agent_tools) == (history[0]["content"], event["tools"]),
                    f"{where}: changing agent policy/tools unsupported",
                )
            else:
                require(not event["tools"], f"{where}: simulator tools are unsupported")
            active = (line, event)
        elif kind == "completion":
            if active is None or completion is not None:
                raise ConversionError(f"{where}: orphan or duplicate completion")
            request_line, request_event = active
            participant = request_event["participant"]
            require(
                (event.get("participant"), event.get("step")) == (participant, request_event["step"]),
                f"{where}: completion identity mismatch",
            )
            endpoint = config[participant]
            expected = _expected_request(endpoint, request_event["messages"], request_event["tools"], result["seed"])
            require(
                event.get("request") == expected,
                f"{where}: effective request differs from selected endpoint/config/history",
            )
            response = event.get("response")
            require(isinstance(response, dict), f"{where}: response must be an object")
            require(response.get("model", endpoint["model"]) == endpoint["model"], f"{where}: response model mismatch")
            _usage(response)
            completion = (line, event)
        elif kind == "message":
            if active is None or completion is None:
                raise ConversionError(f"{where}: message lacks request/completion")
            request_line, request_event = active
            completion_line, completed = completion
            participant = request_event["participant"]
            require(
                (event.get("participant"), event.get("step")) == (participant, request_event["step"]),
                f"{where}: message identity mismatch",
            )
            message = event["message"]
            require(
                message.get("role") == ("assistant" if participant == "agent" else "user"),
                f"{where}: actor/role mismatch",
            )
            choices = completed["response"].get("choices")
            require(
                isinstance(choices, list) and len(choices) == 1,
                f"{where}: expected exactly one native completion choice",
            )
            raw = choices[0]["message"]
            require(raw.get("content") == message.get("content"), f"{where}: completion/message content mismatch")
            raw_calls = raw.get("tool_calls") or []
            native_calls = message.get("tool_calls") or []
            require(len(raw_calls) == len(native_calls), f"{where}: completion/message tool count mismatch")
            require(participant == "agent" or not native_calls, f"{where}: simulator tool activity is unsupported")
            step = _message_step(message, line)
            step["llm_call_count"] = 1
            step["extra"].update(
                request_event_line=request_line,
                completion_event_line=completion_line,
                finish_reason=choices[0].get("finish_reason"),
            )
            for raw_call, call in zip(raw_calls, native_calls, strict=True):
                function = raw_call["function"]
                arguments = _json(function["arguments"])
                require(isinstance(arguments, dict), f"{where}: tool arguments must decode to an object")
                require(
                    call
                    == {
                        "id": raw_call["id"],
                        "name": function["name"],
                        "arguments": arguments,
                        "requestor": "assistant",
                    },
                    f"{where}: raw/native tool mismatch",
                )
                call_id = call["id"]
                require(
                    isinstance(call_id, str) and call_id and call_id not in calls,
                    f"{where}: duplicate/empty tool call ID",
                )
                require(isinstance(call["name"], str) and call["name"].strip(), f"{where}: empty tool name")
                calls[call_id] = call
                call_steps[call_id] = len(steps)
                outstanding.append(call_id)
                step.setdefault("tool_calls", []).append(
                    {
                        "tool_call_id": call_id,
                        "function_name": call["name"],
                        "arguments": arguments,
                        "extra": {"native_call": call, "raw_arguments": function["arguments"]},
                    }
                )
            if participant == "agent":
                step["model_name"] = completed["request"]["model"]
                metrics = _usage(completed["response"])
                if metrics is not None:
                    step["metrics"] = metrics
            else:
                step["extra"]["simulator"] = {
                    "model": completed["request"]["model"],
                    "usage": completed["response"].get("usage"),
                }
            messages.append(message)
            steps.append(step)
            mapping.append(
                {"event_line": line, "native_message_index": len(messages) - 1, "step_index": len(steps) - 1}
            )
            active = completion = None
        elif kind == "tool":
            require(active is None and outstanding, f"{where}: orphan tool result")
            call = event["call"]
            call_id = call.get("id")
            require(
                call_id == outstanding[0] and call == calls[call_id],
                f"{where}: tool call/result order or identity mismatch",
            )
            if tool_batch_step is None:
                require(
                    type(event.get("step")) is int and event["step"] == last_step + 1,
                    f"{where}: tool event must follow generation",
                )
                tool_batch_step = last_step = event["step"]
            require(event["step"] == tool_batch_step, f"{where}: inconsistent tool batch step")
            native_result = event["result"]
            require(
                native_result.get("role") == "tool"
                and native_result.get("requestor") == "assistant"
                and native_result.get("id") == call_id,
                f"{where}: tool result actor/ID mismatch",
            )
            require(
                isinstance(native_result.get("content"), str) and type(native_result.get("error")) is bool,
                f"{where}: malformed tool result",
            )
            target = steps[call_steps[call_id]]
            target.setdefault("observation", {"results": []})["results"].append(
                {
                    "source_call_id": call_id,
                    "content": native_result["content"],
                    "extra": {"native_result": native_result, "event_line": line, "error": native_result["error"]},
                }
            )
            outstanding.pop(0)
            if not outstanding:
                tool_batch_step = None
            messages.append(native_result)
            mapping.append(
                {
                    "event_line": line,
                    "native_message_index": len(messages) - 1,
                    "step_index": call_steps[call_id],
                    "source_call_id": call_id,
                }
            )
        elif kind in {"judge_request", "judge_completion"}:
            require(active is None and not outstanding, f"{where}: judge before dialogue completes")
            if kind == "judge_request":
                require(not judging, f"{where}: duplicate judge request")
                judging = True
                judge_request = event
            else:
                require(
                    judge_request is not None
                    and event["request"]
                    == _expected_request(config["user"], judge_request["messages"], [], result["seed"]),
                    f"{where}: orphan/mismatched judge completion",
                )
                require(
                    event["request"]["model"] == config["user"]["model"]
                    and event["response"].get("model", config["user"]["model"]) == config["user"]["model"],
                    f"{where}: judge model mismatch",
                )
                _usage(event["response"])
                judge_request = None
            auxiliary.append({"event_line": line, "kind": kind, "usage": event.get("response", {}).get("usage")})
        elif kind == "error":
            require(
                result.get("termination") == "error" and event.get("error") == result.get("error"),
                f"{where}: error/result mismatch",
            )
            if completion is not None:
                unprojected.append(
                    {
                        "event_line": completion[0],
                        "participant": completion[1]["participant"],
                        "reason": "Native completion was not parsed into a message; raw response retained without tool credit",
                        "usage": completion[1]["response"].get("usage"),
                    }
                )
            auxiliary.append({"event_line": line, "kind": kind, "native_error": event})
            active = completion = judge_request = None
            failed = True
        else:
            raise ConversionError(f"{where}: unsupported event")
    require(
        active is None and completion is None and judge_request is None,
        "Incomplete request/completion without a terminal native error",
    )
    require(result.get("termination") != "error" or failed, "Error result lacks error event")
    require(
        not outstanding or result.get("termination") in {"max_steps", "error", "too_many_errors"},
        "Unresolved calls in a normally completed dialogue",
    )
    require(messages == result.get("messages"), "Event projection differs from persisted native messages")
    if agent_policy is not None:
        steps.insert(
            0, {"source": "system", "message": agent_policy, "extra": {"origin": "observed_agent_system_prompt"}}
        )
        for entry in mapping:
            entry["step_index"] += 1
    for index, step in enumerate(steps, 1):
        step["step_id"] = index
    for entry in mapping:
        entry["step_id"] = entry.pop("step_index") + 1
    return steps, {
        "event_to_step": mapping,
        "live_agent_tool_calls": len(calls),
        "live_agent_tool_counts": dict(Counter(call["name"] for call in calls.values())),
        "unresolved_call_ids": outstanding,
        "unprojected_completions": unprojected,
        "auxiliary_events": auxiliary,
        "agent_tools": agent_tools,
        "native_message_count": len(messages),
        "event_count": len(events),
    }


def convert_native_rollout(result_path: Path, trace_path: Path, manifest_path: Path, out_dir: Path):
    """Convert one explicitly selected rollout into a new private directory."""
    require(not out_dir.exists() and not out_dir.is_symlink(), f"Output already exists: {out_dir}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = _json(manifest_bytes)
    require(manifest.get("schema") == "tau-mini-trace-input.v1", "Unsupported input manifest schema")
    require(manifest.get("evidence_class") in EVIDENCE_CLASSES, "Explicit evidence_class required")
    require(isinstance(manifest.get("run_id"), str) and manifest["run_id"].strip(), "Explicit run_id required")
    sources = {"run_manifest": {"path": str(manifest_path.resolve()), "sha256": sha(manifest_bytes)}}
    retained = {"source.manifest.json": manifest_bytes}
    for name, supplied in (("result", result_path), ("trace", trace_path), ("config", None), ("selection", None)):
        entry = manifest[name]
        path = Path(entry["path"])
        require(path.is_absolute(), f"{name} path must be absolute")
        require(supplied is None or supplied.resolve() == path.resolve(), f"{name} path differs from manifest")
        payload = path.read_bytes()
        require(sha(payload) == entry["sha256"], f"{name} source hash mismatch: {path}")
        sources[name] = {"path": str(path.resolve()), "sha256": sha(payload)}
        retained["source.trace.jsonl" if name == "trace" else f"source.{name}.json"] = payload
    runtime_sources = manifest["runtime_sources"]
    require(RUNTIME_FILES <= set(runtime_sources), "Runtime provenance requires bootstrap/client/harness/run hashes")
    for name, entry in runtime_sources.items():
        require(Path(entry["path"]).is_absolute(), f"Runtime source path must be absolute: {name}")
        require(sha(Path(entry["path"]).read_bytes()) == entry["sha256"], f"Runtime source hash mismatch: {name}")
    result = _json(retained["source.result.json"])
    events = [_json(line) for line in retained["source.trace.jsonl"].splitlines()]
    config = _json(retained["source.config.json"])
    selection = _json(retained["source.selection.json"])
    require(selection.get("seed") == result.get("seed"), "Selection seed mismatch")
    require(
        sum(row == {"domain": result.get("domain"), "id": result.get("task_id")} for row in selection["tasks"]) == 1,
        "Task must occur exactly once in selected task universe",
    )
    require(sha(json_bytes(events[0]["task"])) == manifest["task_sha256"], "Native task payload hash mismatch")
    steps, projection = validate_event_projection(result, events, config)
    identity = "tau-mini-" + sha((manifest["run_id"] + sources["trace"]["sha256"]).encode())
    spans = []
    for step in steps:
        children = []
        observations = {o.get("source_call_id"): o for o in step.get("observation", {}).get("results", [])}
        for call in step.get("tool_calls", []):
            observed = observations.get(call["tool_call_id"])
            fields = {"output": observed["content"]} if observed is not None else {}
            if observed is not None and observed.get("extra", {}).get("error"):
                fields["error"] = observed["content"]
            children.append(
                Span(
                    id=call["tool_call_id"],
                    kind=SpanKind.TOOL,
                    tool_name=call["function_name"],
                    input=call["arguments"],
                    tool_call={"call_id": call["tool_call_id"], "result_count": 1 if observed else 0},
                    attributes={"tool_call_id": call["tool_call_id"]},
                    **fields,
                )
            )
        spans.append(
            Span(
                id="step-" + str(step["step_id"]),
                kind=SpanKind.CHAIN,
                output=step["message"],
                children=children,
                attributes={"native_step": step, "participant": step["source"]},
            )
        )
    trajectory = Trace(
        id=identity,
        session_id=manifest["run_id"],
        session_basis="execution.run_id",
        root_spans=spans,
        aggregate={},
        attributes={
            "source_kind": "tau-mini-native-events",
            "model": config["agent"]["model"],
            "evidence_class": manifest["evidence_class"],
            "source_bindings": sources,
            "runtime_sources": runtime_sources,
            "native_result": {key: value for key, value in result.items() if key != "messages"},
            "task_sha256": manifest["task_sha256"],
            "projection": {"steps": steps, **{k: v for k, v in projection.items() if k != "agent_tools"}},
        },
    )
    encoded = (trajectory.model_dump_json() + "\n").encode()
    exported = [c for step in spans for c in step.children]
    require(len(exported) == projection["live_agent_tool_calls"], "Exported call count mismatch")
    require(
        dict(Counter(c.tool_name for c in exported)) == projection["live_agent_tool_counts"],
        "Exported call names mismatch",
    )
    for entry in [*sources.values(), *runtime_sources.values()]:
        require(
            sha(Path(entry["path"]).read_bytes()) == entry["sha256"],
            f"Input changed during conversion: {entry['path']}",
        )
    receipt = {
        "schema": "tau-mini-trace-conversion.v1",
        "status": "converted",
        "run_id": manifest["run_id"],
        "evidence_class": manifest["evidence_class"],
        "audit_evidence_class": "agent_under_test" if manifest["evidence_class"] == "primary_unhinted" else None,
        "evidence_class_mapping": "Explicit primary_unhinted selection maps to agent_under_test; other classes remain outside primary audit admission.",
        "sources": sources,
        "runtime_sources": runtime_sources,
        "converter": {"path": str(Path(__file__).resolve()), "sha256": sha(Path(__file__).read_bytes())},
        "validator": {
            "model": "trace_ingest.Trace",
            "version": version("nemo-compass"),
            "schema_version": SCHEMA_VERSION,
            "exact_serialized_bytes_validated": True,
        },
        "normalized_trace": {"path": "trace.jsonl", "sha256": sha(encoded)},
        "projection": projection,
        "retained_inputs": {name: sha(payload) for name, payload in retained.items()},
        "limitations": [
            "Airline text-only mini protocol; preloaded tool history and simulator tools rejected.",
            "Globally reused tool-call IDs and endpoint overrides are explicitly unsupported; repeated tool names are retained.",
            "Source traces include private task/simulator/scorer evidence and must remain private.",
            "Raw requests/responses and nonprojected fields remain in the exact retained source trace; not all are visible dialogue.",
            "Null dialogue content maps to empty display text; exact native message retained in attributes.",
            "Native reward, tool exercise, feasibility, coverage and EA acceptance are distinct; conversion grants none of them.",
            "No inferred timestamps, cost, hidden state, result success or missing observations.",
        ],
        "network_calls": 0,
        "native_execution": False,
    }
    out_dir.mkdir(mode=0o700, parents=False)
    for name, payload in retained.items():
        atomic_write(out_dir / name, payload)
    atomic_write(out_dir / "trace.jsonl", encoded)
    load_snapshot(out_dir / "trace.jsonl", "trace")
    atomic_write(out_dir / "conversion-receipt.json", json_bytes(receipt))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=MANIFEST_HELP, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True, help="New private directory; parent must exist")
    args = parser.parse_args(argv)
    try:
        receipt = convert_native_rollout(args.result, args.trace, args.run_manifest, args.out_dir)
    except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
        print(json.dumps({"status": "rejected", "error": str(error)}))
        return 1
    print(
        json.dumps(
            {"status": "converted", "output": str(args.out_dir), "trace_sha256": receipt["normalized_trace"]["sha256"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
