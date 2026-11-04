# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load, bound and scrub the one Compass ``Trace`` behind a trace workspace.

Loading goes only through ``trace_loading.py``. The safe copy is a scrubbed ``Trace``
that still validates: media becomes placeholders, source-specific attributes are
dropped, identifiers are pseudonymized consistently, and text is redacted. Which field
is which comes from ``trace_view.FIELD_ROLES``.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .trace_view import FIELD_ROLES, TraceContractDrift, check_field_roles, iter_turns, turn_text

if TYPE_CHECKING:
    from trace_ingest import Trace

IMAGE_MARKER = "[image omitted]"
AUDIO_MARKER = "[audio omitted]"
# Ids Compass loaders assign from positions (ATIF and Gym Responses) carry no source data.
_POSITIONAL_ID = re.compile(r"(?:trajectory|gym/response)(?:/(?:subagent|step|tool|turn)/[0-9]+|/model)*")
_POSITIONAL_SUFFIX = re.compile(r"(.+)(/(?:subagent|step|tool|turn)/[0-9]+)")
# Identifier-like keys inside free-form source JSON (tool inputs, outputs and extensions).
IDENTIFIER_KEYS = frozenset(
    {
        "call_id",
        "parent_span_id",
        "previous_response_id",
        "request_id",
        "response_id",
        "rollout_id",
        "session_id",
        "source_call_id",
        "span_id",
        "tool_call_id",
        "trace_id",
        "trajectory_id",
    }
)
# Identifier values this short are too likely to be ordinary words or replayed values to replace
# outside the fields and keys that name identifiers.
_MIN_EMBEDDED_IDENTIFIER = 8
# Loss codes Eval Author writes itself; their details hold only counts.
OWN_LOSS_CODES = frozenset({"provider_redaction_quote_escaped", "image_data_omitted"})
_IMAGE_OBJECT_START = re.compile(r'\{\s*"type"\s*:\s*"image"')
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
SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)")
_INTERNAL_HOST = re.compile(r"(?i)\b[a-z0-9.-]+\.svc\.cluster\.local\b")


class SafeTraceError(ValueError):
    """Source evidence cannot become, or no longer is, a valid workspace Trace."""


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
        raise SafeTraceError(f"{label} is not a valid Compass Trace: {_describe(error)}") from error


def parse_trace(data: bytes, *, label: str = "safe trace") -> Trace:
    """Read workspace Trace bytes; anything but the canonical serialization is rejected."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SafeTraceError(f"could not read {label} as UTF-8 JSON: {_describe(error)}") from error
    trace = validate_payload(payload, label=label)
    if canonical_bytes(trace) != data:
        raise SafeTraceError(f"{label} is not in canonical form")
    return trace


def load_trace(path: Path, kind: str, *, row: int | None = None) -> Trace:
    """Load exactly one trace through Eval Author's single Compass loading boundary."""
    import trace_loading

    try:
        # Loader diagnostics may echo source values; keep command output content-free.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            traces = trace_loading.load_traces(path, kind, max_traces=1, row=row)
    except trace_loading.TraceLimitError as error:
        raise SafeTraceError("prepare requires a source that holds exactly one trace") from error
    except trace_loading.TraceLoadError as error:
        raise SafeTraceError(f"could not load the source as {kind}: {error}") from error
    except (ValueError, OSError) as error:
        raise SafeTraceError(f"could not load the source as {kind}: {_describe(error)}") from error
    return traces[0]


def retarget_source_urls(payload: dict[str, Any], loaded: Path, original: Path) -> None:
    """Point source links at the original file when the loader read a repaired copy."""
    loaded_uri, original_uri = loaded.resolve().as_uri(), original.resolve().as_uri()

    def retarget(value: Any) -> Any:
        if isinstance(value, str) and (value == loaded_uri or value.startswith(f"{loaded_uri}#")):
            return original_uri + value[len(loaded_uri) :]
        return value

    if "source_url" in payload:
        payload["source_url"] = retarget(payload["source_url"])
    pending = list(payload["root_spans"])
    while pending:
        span = pending.pop()
        if "source_url" in span:
            span["source_url"] = retarget(span["source_url"])
        pending.extend(span["children"])


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
    """Require a root-agent user turn with text; return the user turns that hold only images."""
    from trace_ingest import UNSET, ImagePart, SpanKind, TurnRole

    root = next((span for span in trace.root_spans if span.kind is SpanKind.AGENT), None)
    if root is None or root.turns is UNSET:
        raise SafeTraceError(f"source kind {kind} does not record turns yet")
    if not any(turn.role is TurnRole.USER and (turn_text(turn) or "").strip() for turn in root.turns):
        raise SafeTraceError("trace must contain at least one root-agent user turn with text")
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


def pseudonym(value: str) -> str:
    return f"anon-{_digest16(value)}"


_LOST_CONTENT = re.compile(
    "|".join(
        (re.escape("<redacted:"), re.escape(IMAGE_MARKER), re.escape(AUDIO_MARKER), r"(?<![\w-])anon-[0-9a-f]{16}\b")
    )
)


def lost_source_content(value: str) -> bool:
    """Whether a safe-copy string was rewritten: a redaction, a media placeholder or a pseudonym."""
    return _LOST_CONTENT.search(value) is not None


def safe_id(value: str) -> str:
    """The safe-copy id of a span or turn id: loader positions stay, anything else is pseudonymized.

    A positional suffix (``/turn/<n>`` and the like) stays on a pseudonymized prefix, so a
    turn id still names its agent's safe id.
    """
    if _POSITIONAL_ID.fullmatch(value):
        return value
    if match := _POSITIONAL_SUFFIX.fullmatch(value):
        return safe_id(match.group(1)) + match.group(2)
    return pseudonym(value)


def _source_ids(value: str) -> list[str]:
    """The raw source identifiers inside one span or turn id."""
    if _POSITIONAL_ID.fullmatch(value):
        return []
    if match := _POSITIONAL_SUFFIX.fullmatch(value):
        return _source_ids(match.group(1))
    return [value]


class _Scrubber:
    """Scrub a dumped Trace field by field, by role, so the result still validates as a Trace."""

    def __init__(self, payload: dict[str, Any]) -> None:
        check_field_roles()
        self.counts: dict[str, int] = {}
        self.raw_ids: set[str] = set()
        self.kept: set[str] = set()
        self._collect("Trace", payload)
        # A value the safe copy keeps in clear elsewhere (an agent name reused as a trajectory id)
        # hides nothing when replaced in text; replacing it would only corrupt instructions and replay values.
        self.raw_ids -= self.kept
        embedded = sorted((item for item in self.raw_ids if len(item) >= _MIN_EMBEDDED_IDENTIFIER), key=len)
        self.embedded = (
            re.compile("(?<![\\w-])(?:" + "|".join(re.escape(item) for item in reversed(embedded)) + ")(?![\\w-])")
            if embedded
            else None
        )

    def _count(self, kind: str, amount: int = 1) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + amount

    # Collection: every identifier the typed fields hold, so free text can be scrubbed of it,
    # and every string the safe copy keeps verbatim.
    def _collect(self, model: str, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect(model, item)
            return
        if not isinstance(value, dict):
            return
        roles = FIELD_ROLES[model]
        for key, item in value.items():
            role = roles.get(key)
            if role == "identifier" or (model == "Loss" and key == "detail"):
                self._collect_values(item)
            elif role == "span_ref":
                for one in item if isinstance(item, list) else [item]:
                    if isinstance(one, str):
                        self.raw_ids.update(_source_ids(one))
            elif role == "keep":
                for one in item if isinstance(item, list) else [item]:
                    if isinstance(one, str):
                        self.kept.add(one)
            elif role in FIELD_ROLES:
                self._collect(role, item)

    def _collect_values(self, value: Any) -> None:
        if isinstance(value, str) and value:
            self.raw_ids.add(value)
        elif isinstance(value, list):
            for item in value:
                self._collect_values(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in IDENTIFIER_KEYS or key in {"trajectory_path", "continued_trajectory_ref"}:
                    self._collect_values(item)

    # Leaf handlers.
    def pseudonym(self, value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        self._count("identifier")
        return pseudonym(value)

    def span_ref(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        safe = safe_id(value)
        if safe != value:
            self._count("identifier")
        return safe

    def url(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        self._count("identifier")
        return f"file:///anon-{_digest16(value)}"

    def text(self, value: Any) -> Any:
        if not isinstance(value, str):
            return self.json(value, generic_id=True)
        if len(value) >= _MIN_EMBEDDED_IDENTIFIER and value in self.raw_ids:
            return self.pseudonym(value)
        value = redact_text(value, self.counts)
        if self.embedded is not None:
            value, count = self.embedded.subn(lambda match: pseudonym(match.group(0)), value)
            self._count("identifier", count)
        return value

    def json(self, value: Any, key: str | None = None, *, generic_id: bool) -> Any:
        if isinstance(value, dict):
            kind = value.get("type") if isinstance(value.get("type"), str) else None
            if kind in {"image", "input_image", "image_url"} and (
                isinstance(value.get("source"), dict) or "path" in value or "image_url" in value
            ):
                self._count("image")
                return {"type": "text", "text": IMAGE_MARKER}
            if kind in {"audio", "input_audio"}:
                self._count("audio")
                return {"type": "text", "text": AUDIO_MARKER}
            return {name: self.json(item, name, generic_id=generic_id) for name, item in value.items()}
        if isinstance(value, list):
            return [self.json(item, key, generic_id=generic_id) for item in value]
        if isinstance(value, str):
            if key in IDENTIFIER_KEYS or (generic_id and key == "id"):
                return self.pseudonym(value)
            if key is not None and SECRET_KEY.search(key):
                if value:
                    self._count("secret_field")
                    return "<redacted:secret>"
                return value
            return self.text(value)
        return value

    def content(self, value: Any) -> Any:
        if not isinstance(value, list):
            return self.text(value)
        parts = []
        for part in value:
            kind = part.get("type") if isinstance(part, dict) else None
            if kind == "image":
                self._count("image")
                parts.append({"type": "text", "text": IMAGE_MARKER})
            elif kind == "audio":
                self._count("audio")
                parts.append({"type": "text", "text": AUDIO_MARKER})
            else:
                parts.append(self.model("TextPart", part))
        return parts

    def result_attributes(self, value: Any) -> dict[str, Any]:
        """Keep only Compass's explicit error-status record; every other attribute is source-specific."""
        if not isinstance(value, dict):
            return {}
        kept: dict[str, Any] = {}
        if value.get("is_error_status") in ("invalid", "conflicting"):
            kept["is_error_status"] = value["is_error_status"]
        if "is_error_flags" in value:
            kept["is_error_flags"] = self.json(value["is_error_flags"], generic_id=True)
        return kept

    def loss_detail(self, code: Any, detail: Any) -> Any:
        """Keep loss details structured, each identifier pseudonymized like its trace field."""
        if code in OWN_LOSS_CODES or not isinstance(detail, dict):
            return self.json(detail, generic_id=True)
        scrubbed: dict[str, Any] = {}
        for key, item in detail.items():
            if key == "ref" and isinstance(item, dict):
                # ``extra`` is opaque source JSON.
                scrubbed[key] = self.model("SubagentRef", {k: v for k, v in item.items() if k != "extra"})
            elif key in {"continued_trajectory_ref", "tool_call_id"}:
                scrubbed[key] = self.pseudonym(item)
            elif key in {"agent_span_id", "span_id"}:
                scrubbed[key] = self.span_ref(item)
            elif key == "span_ids" and isinstance(item, list):
                scrubbed[key] = [self.span_ref(one) for one in item]
            elif key == "reason":
                scrubbed[key] = self.text(item)
            elif type(item) is int:
                scrubbed[key] = item
            # Any other key is opaque source JSON and is dropped.
        return scrubbed

    def field(self, model: str, key: str, role: str, value: Any, record: dict[str, Any]) -> Any:
        if role in FIELD_ROLES:
            if isinstance(value, list):
                return [self.model(role, item) if isinstance(item, dict) else item for item in value]
            return self.model(role, value) if isinstance(value, dict) else value
        if role == "keep":
            return value
        if role == "drop":
            return {}
        if role == "null":
            if value is not None:
                self._count("token_ids")
            return None
        if role == "text":
            return self.text(value)
        if role in {"json", "replay_json"}:
            return self.json(value, key, generic_id=role == "json")
        if role == "span_io":
            return self.json(value, key, generic_id=record.get("kind") != "TOOL")
        if role == "identifier":
            return [self.pseudonym(item) for item in value] if isinstance(value, list) else self.pseudonym(value)
        if role == "span_ref":
            return [self.span_ref(item) for item in value] if isinstance(value, list) else self.span_ref(value)
        if role == "url":
            return self.url(value)
        if role == "content":
            return self.content(value)
        if role == "loss_detail":
            return self.loss_detail(record.get("code"), value)
        if role == "result_attributes":
            return self.result_attributes(value)
        raise TraceContractDrift(f"{model}.{key} has role {role!r}, which the scrubber does not apply")

    def model(self, model: str, value: dict[str, Any]) -> dict[str, Any]:
        roles = FIELD_ROLES[model]
        unknown = sorted(set(value) - set(roles))
        if unknown:
            raise TraceContractDrift(f"{model} carries fields the identifier table does not classify: {unknown}")
        return {key: self.field(model, key, roles[key], item, value) for key, item in value.items()}


def scrub(trace: Trace) -> tuple[Trace, dict[str, int]]:
    """Return the text-only, pseudonymized safe copy and its deterministic redaction counts."""
    payload = trace.model_dump(mode="json")
    scrubber = _Scrubber(payload)
    safe = scrubber.model("Trace", payload)
    return validate_payload(safe, label="scrubbed trace"), dict(sorted(scrubber.counts.items()))
