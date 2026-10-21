# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load, bound, scrub, and index the Compass Trace behind one trace-environment workspace."""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trace_ingest import Span, Trace, Turn

_CORE_SCRIPTS = Path(__file__).resolve().parents[2] / "eval-author/scripts"

IMAGE_MARKER = "[image omitted]"
_IMAGE_OBJECT_START = re.compile(r'\{\s*"type"\s*:\s*"image"')
# Loader-assigned ids built only from these segments are positions, not source data.
_POSITIONAL_SEGMENT = r"(?:[0-9]+|trajectory|subagent|step|tool|turn)"
_POSITIONAL_ID = re.compile(rf"{_POSITIONAL_SEGMENT}(?:/{_POSITIONAL_SEGMENT})*")
# Identifier-like keys inside free-form source JSON (tool inputs, outputs, and extensions).
_IDENTIFIER_KEYS = frozenset(
    {"parent_span_id", "session_id", "source_call_id", "span_id", "tool_call_id", "trace_id", "trajectory_id"}
)
# Compass marks identifier fields with ``x-identifier`` (``trace_ingest.identifier_fields()``).
# Marked span and turn ids keep loader positions, marked links become opaque file URLs, and
# every other marked field is pseudonymized, so newly marked fields are never missed.
_SPAN_ID_FIELDS = {
    "Span": frozenset({"id"}),
    "Turn": frozenset({"id", "llm_span_ids", "dispatch_span_id", "tool_call_span_ids"}),
    "Loss": frozenset({"span_id", "turn_id"}),
    "SubagentRef": frozenset({"span_id"}),
    "RunInfo": frozenset({"subagent_span_ids"}),
}
_URL_FIELDS = frozenset({"source_url"})
_SCRUBBED_MODELS = frozenset({"Trace", "Span", "Turn", "ToolCall", "ToolResult", "SubagentRef", "RunInfo", "Loss"})
# Loss codes eval-author writes itself; their details hold only counts.
OWN_LOSS_CODES = frozenset({"provider_redaction_quote_escaped", "image_data_omitted"})
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{6,}\d)(?!\w)")
_HOME_PATH = re.compile(r"(?<![\w/])/(?:home|Users)/[^/\s]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)")
_INTERNAL_HOST = re.compile(r"(?i)\b[a-z0-9.-]+\.svc\.cluster\.local\b")


class TraceSourceError(ValueError):
    """Source evidence cannot become, or no longer is, a valid workspace Trace."""


def trace_loading() -> ModuleType:
    """Import eval-author's single trace-loading boundary from the sibling core skill."""
    if str(_CORE_SCRIPTS) not in sys.path:
        sys.path.append(str(_CORE_SCRIPTS))
    try:
        import trace_loading as module
    except ModuleNotFoundError as error:
        if error.name != "trace_loading":
            raise
        raise SystemExit(
            "Incomplete skill installation: install the sibling eval-author skill alongside "
            "eval-author-trace-environment."
        ) from error
    return module


def canonical_bytes(trace: Trace) -> bytes:
    """Serialize a Trace in the one canonical form that workspace digests bind."""
    payload = trace.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def _describe(error: Exception) -> str:
    """Name an error by type and location only; source values never reach messages."""
    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        locations = [".".join(str(part) for part in item["loc"]) or "<root>" for item in error.errors()[:5]]
        return f"{error.error_count()} validation error(s) at {', '.join(locations)}"
    if isinstance(error, json.JSONDecodeError):
        return f"invalid JSON at line {error.lineno} column {error.colno}"
    return type(error).__name__


def validate_payload(payload: Any, *, label: str) -> Trace:
    from pydantic import ValidationError
    from trace_ingest import Trace

    try:
        return Trace.model_validate(payload)
    except ValidationError as error:
        raise TraceSourceError(f"{label} is not a valid Compass Trace: {_describe(error)}") from error


def parse_trace(data: bytes, *, label: str = "safe trace") -> Trace:
    """Read workspace Trace bytes; anything but the canonical serialization is rejected."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TraceSourceError(f"could not read {label} as UTF-8 JSON: {_describe(error)}") from error
    trace = validate_payload(payload, label=label)
    if canonical_bytes(trace) != data:
        raise TraceSourceError(f"{label} is not in canonical form")
    return trace


def load_trace(
    path: Path,
    kind: str,
    *,
    row: int | None = None,
    spans: Path | None = None,
    workspace: str | None = None,
) -> Trace:
    """Load exactly one trace through eval-author's single Compass loading boundary."""
    loading = trace_loading()
    try:
        # Loader diagnostics may echo source values; keep command output content-free.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            traces = loading.load_traces(path, kind, max_traces=1, spans=spans, workspace=workspace, row=row)
    except loading.TraceLoadError as error:
        if "more than max_traces" in str(error):
            raise TraceSourceError("prepare requires a source that holds exactly one trace") from error
        raise TraceSourceError(f"could not load the source as {kind}: {error}") from error
    except (ValueError, OSError) as error:
        raise TraceSourceError(f"could not load the source as {kind}: {_describe(error)}") from error
    return traces[0]


_PATH_REF_LOSSES = frozenset({"unresolved_subagent_ref", "invalid_subagent_trajectory"})


def unresolved_trajectory_paths(trace: Trace) -> bool:
    """Whether the loader could not follow a subagent reference that names a trajectory file."""
    return any(
        loss.code in _PATH_REF_LOSSES
        and isinstance(loss.detail.get("ref"), dict)
        and bool(loss.detail["ref"].get("trajectory_path"))
        for loss in trace.losses
    )


def retarget_source_urls(payload: dict[str, Any], loaded: Path, original: Path) -> None:
    """Point source links at the original file when the loader read a repaired copy."""
    loaded_uri, original_uri = loaded.resolve().as_uri(), original.resolve().as_uri()

    def retarget(value: Any) -> Any:
        if isinstance(value, str) and (value == loaded_uri or value.startswith(f"{loaded_uri}#")):
            return original_uri + value[len(loaded_uri) :]
        return value

    payload["source_url"] = retarget(payload.get("source_url"))
    pending = list(payload["root_spans"])
    while pending:
        span = pending.pop()
        if "source_url" in span:
            span["source_url"] = retarget(span["source_url"])
        pending.extend(span["children"])
    if payload["source_url"] is None:
        del payload["source_url"]


def bound_images(payload: dict[str, Any]) -> dict[str, int]:
    """Omit encoded image bytes from a dumped Trace, in place, keeping every other value.

    Turn image parts reference images by path; a ``data:`` path is encoded bytes. Tool
    results and other free-form JSON may carry raw image parts or image JSON inside text.
    """
    counts = {
        "count": 0,
        "omitted_encoded_characters": 0,
        "metadata_field_count": 0,
        "metadata_omitted_characters": 0,
    }

    def convert(content: str) -> list[dict[str, Any]] | None:
        decoder = json.JSONDecoder()
        parts: list[dict[str, Any]] = []
        search_cursor = emitted_cursor = converted = characters = 0
        while match := _IMAGE_OBJECT_START.search(content, search_cursor):
            start = match.start()
            try:
                part, end = decoder.raw_decode(content, start)
            except json.JSONDecodeError:
                search_cursor = match.end()
                continue
            source = part.get("source") if isinstance(part, dict) else None
            if part.get("type") != "image" or not isinstance(source, dict) or not isinstance(source.get("data"), str):
                search_cursor = end
                continue
            if start > emitted_cursor:
                parts.append({"type": "text", "text": content[emitted_cursor:start]})
            characters += len(source["data"])
            parts.append({**part, "source": {**source, "data": ""}})
            converted += 1
            search_cursor = emitted_cursor = end
        if not converted:
            return None
        if emitted_cursor < len(content):
            parts.append({"type": "text", "text": content[emitted_cursor:]})
        counts["count"] += converted
        counts["omitted_encoded_characters"] += characters
        return parts

    def bound_metadata(value: Any, *, image_context: bool = False) -> Any:
        if isinstance(value, dict):
            image_like = (
                image_context
                or (isinstance(value.get("type"), str) and value["type"] in {"image", "base64"})
                or (isinstance(value.get("media_type"), str) and value["media_type"].startswith("image/"))
            )
            for key, nested in value.items():
                if (
                    isinstance(nested, str)
                    and nested
                    and ((key in {"base64", "data"} and image_like) or (key == "base64" and len(nested) > 100_000))
                ):
                    counts["metadata_field_count"] += 1
                    counts["metadata_omitted_characters"] += len(nested)
                    value[key] = ""
                else:
                    value[key] = bound_metadata(nested, image_context=image_like)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                value[index] = bound_metadata(nested, image_context=image_context)
        elif isinstance(value, str):
            # Text may repeat image metadata as JSON rather than as an image part.
            # Keep surrounding prose and nonbinary metadata; never decode encoded bytes.
            decoder = json.JSONDecoder()
            fragments: list[str] = []
            search_cursor = emitted_cursor = 0
            while match := _IMAGE_OBJECT_START.search(value, search_cursor):
                try:
                    metadata, end = decoder.raw_decode(value, match.start())
                except json.JSONDecodeError:
                    search_cursor = match.end()
                    continue
                previous = counts["metadata_field_count"]
                bound_metadata(metadata)
                if counts["metadata_field_count"] != previous:
                    fragments.extend((value[emitted_cursor : match.start()], json.dumps(metadata, ensure_ascii=False)))
                    emitted_cursor = end
                search_cursor = end
            if fragments:
                fragments.append(value[emitted_cursor:])
                return "".join(fragments)
        return value

    def bound_image_parts(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "image":
                source = value.get("source")
                encoded = source.get("data") if isinstance(source, dict) else None
                if isinstance(encoded, str) and encoded:
                    counts["count"] += 1
                    counts["omitted_encoded_characters"] += len(encoded)
                    source["data"] = ""
                path = value.get("path")
                if isinstance(path, str) and path.startswith("data:"):
                    counts["count"] += 1
                    counts["omitted_encoded_characters"] += len(path)
                    value["path"] = ""
            for nested in value.values():
                bound_image_parts(nested)
        elif isinstance(value, list):
            for nested in value:
                bound_image_parts(nested)

    for result in _dumped_results(payload):
        if isinstance(result.get("content"), str) and (parts := convert(result["content"])) is not None:
            result["content"] = parts
    bound_image_parts(payload)
    bound_metadata(payload)
    return counts


def _dumped_results(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    pending = list(payload["root_spans"])
    while pending:
        span = pending.pop()
        pending.extend(span["children"])
        call = span.get("tool_call")
        if isinstance(call, dict):
            yield from call.get("results", [])
        for turn in span.get("turns", []):
            yield from turn.get("unlinked_results", [])


def image_only_user_turns(trace: Trace, kind: str) -> list[str]:
    """Require a root-agent user instruction with text; return user turns that hold only images."""
    from trace_ingest import UNSET, ImagePart, SpanKind, TurnRole
    from trace_ingest.turns import iter_turns, turn_text

    root = next((span for span in trace.root_spans if span.kind is SpanKind.AGENT), None)
    if root is None or root.turns is UNSET:
        raise TraceSourceError(f"source kind {kind} does not record turns yet")
    if not any(turn.role is TurnRole.USER and (turn_text(turn) or "").strip() for turn in root.turns):
        raise TraceSourceError("trace must contain at least one root-agent user turn with text")
    return [
        turn.id
        for _, turn in iter_turns(trace)
        if turn.role is TurnRole.USER
        and isinstance(turn.content, list)
        and any(isinstance(part, ImagePart) for part in turn.content)
        and not (turn_text(turn) or "").strip()
    ]


def _replace(pattern: re.Pattern[str], value: str, marker: str, counts: dict[str, int], kind: str) -> str:
    replaced, count = pattern.subn(marker, value)
    counts[kind] = counts.get(kind, 0) + count
    return replaced


def _redact_ipv4(value: str, counts: dict[str, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            ipaddress.ip_address(match.group(0))
        except ValueError:
            return match.group(0)
        counts["ipv4"] = counts.get("ipv4", 0) + 1
        return "<redacted:ipv4>"

    return _IPV4.sub(replace, value)


def _redact_phone(value: str, counts: dict[str, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            return candidate
        digits = sum(character.isdigit() for character in candidate)
        if not 7 <= digits <= 15:
            return candidate
        counts["phone"] = counts.get("phone", 0) + 1
        return "<redacted:phone>"

    return _PHONE.sub(replace, value)


def redact_text(value: str, counts: dict[str, int]) -> str:
    value = _replace(_PRIVATE_KEY, value, "<redacted:private-key>", counts, "private_key")
    value = _replace(_BEARER, value, "<redacted:bearer-token>", counts, "bearer_token")
    value = _replace(_EMAIL, value, "<redacted:email>", counts, "email")
    value = _replace(_SSN, value, "<redacted:ssn>", counts, "ssn")
    value = _replace(_HOME_PATH, value, "/home/<redacted:user>", counts, "home_path")
    value = _replace(_INTERNAL_HOST, value, "<redacted:internal-host>", counts, "internal_host")
    value = _redact_ipv4(value, counts)
    return _redact_phone(value, counts)


def _digest16(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def safe_id(value: str) -> str:
    """Map a span or turn id to its safe-copy id: positions stay, anything else is pseudonymized."""
    return value if _POSITIONAL_ID.fullmatch(value) else f"anon-{_digest16(value)}"


_KEEP: Callable[[Any], Any] = lambda value: value  # noqa: E731 - a named no-op handler reads clearer in tables


class _Scrubber:
    """Scrub a dumped Trace field by field so the result still validates as a Trace."""

    def __init__(self) -> None:
        from trace_ingest import identifier_fields

        self.counts: dict[str, int] = {}
        self.identifiers = identifier_fields()
        unvisited = sorted(set(self.identifiers) - _SCRUBBED_MODELS)
        if unvisited:
            raise TraceSourceError(f"the safe-trace scrubber does not visit Compass identifier models: {unvisited}")

    def _count(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def pseudonym(self, value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        self._count("identifier")
        return f"anon-{_digest16(value)}"

    def span_id(self, value: Any) -> Any:
        if not isinstance(value, str) or _POSITIONAL_ID.fullmatch(value):
            return value
        self._count("identifier")
        return safe_id(value)

    def url(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        self._count("identifier")
        return f"file:///anon-{_digest16(value)}"

    def text(self, value: Any) -> Any:
        return redact_text(value, self.counts) if isinstance(value, str) else self.json(value)

    def identifier(self, model: str, name: str) -> Callable[[Any], Any]:
        """The handler for one field Compass marks as an identifier."""
        if name in _URL_FIELDS:
            one = self.url
        elif name in _SPAN_ID_FIELDS.get(model, ()):
            one = self.span_id
        else:
            one = self.pseudonym

        def handle(value: Any) -> Any:
            if isinstance(value, list):
                return [one(item) for item in value]
            if value is None or isinstance(value, str):
                return one(value)
            raise TraceSourceError(f"{model}.{name} holds an identifier the scrubber cannot pseudonymize")

        return handle

    def json(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            if value.get("type") == "image" and (isinstance(value.get("source"), dict) or "path" in value):
                self._count("image")
                return {"type": "text", "text": IMAGE_MARKER}
            return {name: self.json(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [self.json(item, key) for item in value]
        if isinstance(value, str):
            if key in _IDENTIFIER_KEYS:
                return self.pseudonym(value)
            if key is not None and _SECRET_KEY.search(key):
                if value:
                    self._count("secret_field")
                    return "<redacted:secret>"
                return value
            return redact_text(value, self.counts)
        return value

    def fields(self, value: dict[str, Any], handlers: dict[str, Callable[[Any], Any]]) -> dict[str, Any]:
        return {key: handlers[key](item) if key in handlers else self.json(item, key) for key, item in value.items()}

    def model(self, model: str, value: dict[str, Any], handlers: dict[str, Callable[[Any], Any]]) -> dict[str, Any]:
        """Scrub one Compass model; its marked identifier fields override any other handling."""
        marked = {name: self.identifier(model, name) for name in self.identifiers.get(model, ())}
        return self.fields(value, {**handlers, **marked})

    def each(self, handler: Callable[[Any], Any]) -> Callable[[list[Any]], list[Any]]:
        return lambda items: [handler(item) for item in items]

    def trace(self, value: dict[str, Any]) -> dict[str, Any]:
        def attributes(items: dict[str, Any]) -> dict[str, Any]:
            # Source-specific attributes are dropped; only the shared case key survives, pseudonymized.
            case = items.get("logical_case_id")
            return {"logical_case_id": self.pseudonym(case)} if isinstance(case, str) else {}

        return self.model(
            "Trace",
            value,
            {
                "schema_version": _KEEP,
                "root_spans": self.each(self.span),
                "attributes": attributes,
                "losses": self.each(self.loss),
            },
        )

    def loss(self, value: dict[str, Any]) -> dict[str, Any]:
        code = value.get("code")
        return self.model("Loss", value, {"code": _KEEP, "detail": lambda detail: self.loss_detail(code, detail)})

    def loss_detail(self, code: Any, detail: Any) -> Any:
        """Keep loss details as structured data with each identifier pseudonymized like its trace field."""
        if code in OWN_LOSS_CODES or not isinstance(detail, dict):
            return self.json(detail)
        scrubbed: dict[str, Any] = {}
        for key, item in detail.items():
            if key == "ref" and isinstance(item, dict):
                # ``extra`` is opaque source JSON.
                scrubbed[key] = self.subagent_ref({name: part for name, part in item.items() if name != "extra"})
            elif key == "continued_trajectory_ref":
                scrubbed[key] = self.identifier("RunInfo", "continued_trajectory_ref")(item)
            elif key == "tool_call_id":
                scrubbed[key] = self.identifier("ToolCall", "call_id")(item)
            elif key == "llm_call_count" and type(item) is int:
                scrubbed[key] = item
            # ``definition`` and any other key is opaque source JSON and is dropped.
        return scrubbed

    def span(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.model(
            "Span",
            value,
            {
                "kind": _KEEP,
                "children": self.each(self.span),
                "start_time": _KEEP,
                "end_time": _KEEP,
                "tool_call": lambda call: None if call is None else self.tool_call(call),
                "attributes": lambda _: {},
                "run": self.run,
                "turns": self.each(self.turn),
            },
        )

    def run(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.model("RunInfo", value, {"schema_version": _KEEP})

    def tool_call(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.model(
            "ToolCall",
            value,
            {
                "results": self.each(self.result),
                "initiator": _KEEP,
            },
        )

    def result(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.model(
            "ToolResult",
            value,
            {
                "error_status": _KEEP,
                "subagent_refs": self.each(self.subagent_ref),
                "attributes": lambda _: {},
            },
        )

    def subagent_ref(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.model("SubagentRef", value, {})

    def turn(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.model(
            "Turn",
            value,
            {
                "role": _KEEP,
                "authorship": _KEEP,
                "content": self.content,
                "timestamp": _KEEP,
                "unlinked_results": self.each(self.result),
            },
        )

    def content(self, value: Any) -> Any:
        if not isinstance(value, list):
            return self.text(value)
        parts = []
        for part in value:
            if isinstance(part, dict) and part.get("type") == "image":
                self._count("image")
                parts.append({"type": "text", "text": IMAGE_MARKER})
            elif isinstance(part, dict):
                parts.append(self.fields(part, {"type": _KEEP, "text": self.text}))
            else:
                parts.append(self.json(part))
        return parts


def scrub(trace: Trace) -> tuple[Trace, dict[str, int]]:
    """Return the text-only, pseudonymized safe copy and its deterministic redaction counts."""
    scrubber = _Scrubber()
    payload = scrubber.trace(trace.model_dump(mode="json"))
    return validate_payload(payload, label="scrubbed trace"), dict(sorted(scrubber.counts.items()))


def _walk(trace: Trace) -> Iterator[tuple[Span, str | None]]:
    """Yield every span depth-first with the id of its nearest enclosing AGENT span."""
    from trace_ingest import SpanKind

    pending: list[tuple[Span, str | None]] = [(span, None) for span in reversed(trace.root_spans)]
    while pending:
        span, owner = pending.pop()
        yield span, owner
        inner = span.id if span.kind is SpanKind.AGENT else owner
        pending.extend((child, inner) for child in reversed(span.children))


def tool_spans(trace: Trace) -> list[tuple[str | None, Span]]:
    """TOOL spans depth-first, each with its owning AGENT span id (its tool scope)."""
    from trace_ingest import SpanKind

    return [(owner, span) for span, owner in _walk(trace) if span.kind is SpanKind.TOOL]


class TraceIndex:
    """Turn and span lookups that citations and construction pointers resolve against."""

    def __init__(self, trace: Trace) -> None:
        from trace_ingest import turns_in_execution_order

        self.trace = trace
        self.turns: dict[str, tuple[Span, Turn]] = {}
        # Positions follow execution: a subagent's turns sit right after the turn that ran it,
        # so cutoff and reversed-edit comparisons hold across agents.
        self.position: dict[str, int] = {}
        self.span_turn: dict[str, str] = {}
        for position, (agent, turn) in enumerate(turns_in_execution_order(trace)):
            self.turns[turn.id] = (agent, turn)
            self.position[turn.id] = position
            dispatch = [turn.dispatch_span_id] if turn.dispatch_span_id is not None else []
            for span_id in (*turn.llm_span_ids, *dispatch, *turn.tool_call_span_ids):
                self.span_turn.setdefault(span_id, turn.id)
        self.spans: dict[str, Span] = {span.id: span for span, _ in _walk(trace)}

    def document(self, reference: dict[str, Any]) -> tuple[Any, int]:
        """Return the JSON of the cited turn or span (without children) and its turn position."""
        if "turn_id" in reference:
            entry = self.turns.get(reference["turn_id"])
            if entry is None:
                raise TraceSourceError("source turn is not in the trace")
            return entry[1].model_dump(mode="json"), self.position[entry[1].id]
        span = self.spans.get(reference["span_id"])
        if span is None:
            raise TraceSourceError("source span is not in the trace")
        turn_id = self.span_turn.get(span.id)
        if turn_id is None:
            raise TraceSourceError("source span belongs to no trace turn")
        return span.model_dump(mode="json", exclude={"children"}), self.position[turn_id]
