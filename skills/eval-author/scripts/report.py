#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Refresh an offline evaluation report and its README section without executing evaluations.

Python 3.11+, jsonschema, and markdown-it-py are required. Discovery is the shared file-only
search, not the readiness workflow. Only selected local files are read. The
internal report snapshot retains reporting metadata and the last inventory;
HTML and Markdown are projections. Native evidence and task README sections
remain authoritative for their contents.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import heapq
import json
import math
import os
import posixpath
import re
import stat
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

# Isolated Python invocations must resolve the bundled sibling, never cwd code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from journey_state import load_state, task_lifecycle  # noqa: E402
from journey_state import state_path as journey_state_path
from search_tasks import search_tasks, within_task_scope  # noqa: E402
from working_paths import working_path  # noqa: E402

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_README_BYTES = 256 * 1024
MARKER = '"__EVAL_AUTHOR_REPORT_DATA__"'
README_BEGIN = b"<!-- BEGIN EVAL AUTHOR REPORT -->"
README_END = b"<!-- END EVAL AUTHOR REPORT -->"
TEXT = {"type": "string", "maxLength": 2000}
PATH = {"type": "string", "minLength": 1, "maxLength": 2000}
NULL_TEXT = {"anyOf": [TEXT, {"type": "null"}]}
NUMBER = {"type": ["number", "null"]}
GRADING_KINDS = ["Custom code", "LLM-as-judge", "Custom code + LLM-as-judge", "Not documented"]
LEGACY_GRADING_KINDS = {
    "deterministic": "Custom code",
    "model": "LLM-as-judge",
    "hybrid": "Custom code + LLM-as-judge",
    "unknown": "Not documented",
}
GRADING_SUMMARY = {"type": "string", "maxLength": 200, "pattern": r"^\S+(?: \S+){4,9}$"}
NO_GRADING_SUMMARY = "No verifier description documented in README."


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def array(items: dict) -> dict:
    return {"type": "array", "items": items, "maxItems": 50000}


TASK_METADATA = obj(
    {
        "display_id": TEXT,
        "behavior": TEXT,
        "expected_outcome": TEXT,
        "grading_kind": {"enum": GRADING_KINDS + list(LEGACY_GRADING_KINDS)},
        "grading_rule": TEXT,
        "grading_summary": GRADING_SUMMARY,
        "source_refs": array(PATH),
    },
    [],
)
RUN_METADATA = obj(
    {
        "role": {"enum": ["agent", "control", "diagnostic", "unknown"]},
        "reward_key": TEXT,
        "pass_value": {"type": "number"},
        "reward_min": {"type": "number"},
        "reward_max": {"type": "number"},
        "invalid_attempts": {"type": "object", "additionalProperties": TEXT},
    },
    [],
)
METADATA_SCHEMA = obj(
    {
        "task_roots": array(PATH),
        "jobs_roots": array(PATH),
        "tasks": {"type": "object", "additionalProperties": TASK_METADATA},
        "runs": {"type": "object", "additionalProperties": RUN_METADATA},
    }
)
TASK_SCHEMA = obj(
    {
        "id": TEXT,
        "provider": {"enum": ["harbor", "gym"]},
        "path": PATH,
        "display_id": TEXT,
        "name": TEXT,
        "behavior": NULL_TEXT,
        "expected_outcome": NULL_TEXT,
        "grading_kind": TEXT,
        "grading_rule": NULL_TEXT,
        "readme_revision": NULL_TEXT,
        "readme_path": PATH,
        "readme_html": {"type": ["string", "null"], "maxLength": 2 * 1024 * 1024},
        "status": {"enum": ["found", "unverified"]},
        "issues": array(TEXT),
    }
)
# Older v1 snapshots lack readiness. Refresh always derives it from journey state;
# retained HTML, report metadata, and inventory status cannot establish acceptance.
TASK_SCHEMA["properties"].update(
    readiness={"enum": ["draft", "accepted", "unknown"]},
    readiness_reason=TEXT,
)
# Keep new README-derived fields optional when reading older version-1 reports.
TASK_SCHEMA["properties"]["title"] = NULL_TEXT
# Accept the former split-title field when refreshing existing reports.
TASK_SCHEMA["properties"]["heading_id"] = TEXT
TASK_SCHEMA["properties"]["grading_summary"] = {"anyOf": [GRADING_SUMMARY, {"type": "null"}]}
ATTEMPT_SCHEMA = obj(
    {
        "id": TEXT,
        "path": PATH,
        "task_name": NULL_TEXT,
        "task_path": NULL_TEXT,
        "task_checksum": NULL_TEXT,
        "started_at": NULL_TEXT,
        "finished_at": NULL_TEXT,
        "agent": NULL_TEXT,
        "rewards": {"type": "object", "additionalProperties": {"type": "number"}},
        "execution": {"enum": ["completed", "error", "pending", "unknown"]},
        "invalid_reason": NULL_TEXT,
        "route": NULL_TEXT,
    }
)
RUN_SCHEMA = obj(
    {
        "id": TEXT,
        "path": PATH,
        "name": TEXT,
        "kind": {"enum": ["job", "standalone_trial", "unknown"]},
        "role": {"enum": ["agent", "control", "diagnostic", "unknown"]},
        "status": {"enum": ["completed", "incomplete", "unknown", "unverified"]},
        "date": NULL_TEXT,
        "total": {"type": ["integer", "null"], "minimum": 0},
        "passed": {"type": ["integer", "null"], "minimum": 0},
        "failed": {"type": "integer", "minimum": 0},
        "errors": {"type": "integer", "minimum": 0},
        "invalid": {"type": "integer", "minimum": 0},
        "unknown": {"type": "integer", "minimum": 0},
        "score_label": TEXT,
        "reward_key": TEXT,
        "reward_sum": NUMBER,
        "score_min": NUMBER,
        "score_max": NUMBER,
        "scored": {"type": "integer", "minimum": 0},
        "completed": {"type": "integer", "minimum": 0},
        "viewer_root": PATH,
        "route": NULL_TEXT,
        "attempts": array(ATTEMPT_SCHEMA),
        "issues": array(TEXT),
    }
)
JOB_SCHEMA = obj({**RUN_SCHEMA["properties"], "kind": {"const": "job"}})
STANDALONE_SCHEMA = obj({**RUN_SCHEMA["properties"], "kind": {"enum": ["standalone_trial", "unknown"]}})
REPORT_SCHEMA = obj(
    {
        "schema_version": {"const": 1},
        "metadata": METADATA_SCHEMA,
        "tasks": array(TASK_SCHEMA),
        "runs": array(JOB_SCHEMA),
        "standalone_trials": array(STANDALONE_SCHEMA),
        "warnings": array(TEXT),
        "inventory": obj({"complete": {"type": "boolean"}, "excluded": array(PATH)}),
    }
)


class ReportError(ValueError):
    """Invalid input or an unsafe/ambiguous report update."""


def validate(data: Any, schema: dict) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise ReportError("Report generation requires jsonschema; use the Eval Author Python environment.") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        # Avoid printing evidence values or credential-like contents in diagnostics.
        raise ReportError(f"Invalid report field at {'/'.join(map(str, error.path)) or '/'} ({error.validator}).")


def relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ReportError("Artifact paths must be project-relative, without traversal or drive prefixes.")
    return path.as_posix()


def local(repo: Path, value: str) -> Path:
    path = repo / relative(value)
    for part in [path, *path.parents]:
        if part == repo:
            break
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ReportError("Symlink or junction artifact paths are not supported.")
    return path


def read_bytes(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    if not path.is_file():
        raise ReportError("Expected a regular artifact file.")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ReportError("Artifact exceeds the report's bounded read limit.")
    return content


def decode_json(content: bytes | str) -> Any:
    def no_constant(_: str) -> None:
        raise ReportError("Non-finite JSON number.")

    try:
        return json.loads(content, parse_constant=no_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReportError("Malformed JSON artifact.") from exc


class EmbeddedData(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "report-data":
            if dict(attrs).get("type") != "application/json":
                raise ReportError("Report data must be an inert JSON block.")
            self.blocks.append("")
            self.active = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.active = False

    def handle_data(self, data: str) -> None:
        if self.active:
            self.blocks[-1] += data


def normalize_task_fields(payload: dict) -> None:
    """Upgrade legacy task display fields in HTML imports and internal snapshots."""
    for task in payload["tasks"]:
        task["grading_kind"] = LEGACY_GRADING_KINDS.get(task["grading_kind"], task["grading_kind"])
        if "heading_id" in task:
            if task["readme_html"] is not None:
                task["title"] = readme_title(task["readme_html"]) or task.get("title")
            del task["heading_id"]


def previous_report(path: Path) -> tuple[dict | None, bytes | None]:
    if not path.exists():
        return None, None
    content = read_bytes(path, MAX_REPORT_BYTES)
    parser = EmbeddedData()
    parser.feed(content.decode("utf-8"))
    if len(parser.blocks) != 1:
        raise ReportError("Existing report must contain exactly one report-data block.")
    payload = decode_json(parser.blocks[0])
    validate(payload, REPORT_SCHEMA)
    normalize_task_fields(payload)
    return payload, content


def projection_version(path: Path) -> bytes | tuple[int, int, int, int] | None:
    """Detect projection edits without loading an oversized disposable HTML file."""
    if not path.exists():
        return None
    info = path.stat()
    if info.st_size > MAX_REPORT_BYTES:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    return read_bytes(path, MAX_REPORT_BYTES)


def previous_snapshot(path: Path, output: Path) -> tuple[dict | None, bytes | None, bytes | tuple | None]:
    """Import legacy HTML once; subsequent refreshes trust only internal state."""
    if not path.exists():
        previous, html = previous_report(output)
        return previous, None, html
    raw = read_bytes(path, MAX_REPORT_BYTES)
    previous = decode_json(raw)
    validate(previous, REPORT_SCHEMA)
    normalize_task_fields(previous)
    return previous, raw, projection_version(output)


def readme_sections(text: str) -> dict[str, str | None]:
    """Read exact H2 sections; ignore headings in fenced code and reject duplicates."""
    names = ("Behavior under test", "Expected result")
    sections: dict[str, list[tuple[list[str], list[str]]]] = {name: [] for name in names}
    current: list[str] | None = None
    source: list[str] | None = None
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            if source is not None:
                source.append(line)
            token = match[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[match.end() :].strip():
                fence = ""
            continue
        if fence:
            if source is not None:
                source.append(line)
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading and len(heading[1]) <= 2:
            current = None
            source = None
            if len(heading[1]) == 2 and heading[2] in sections:
                current = []
                source = []
                sections[heading[2]].append((current, source))
        elif current is not None:
            current.append(line)
            assert source is not None
            source.append(line)
    result = {}
    for name, values in sections.items():
        if not values:
            continue
        text = ""
        if len(values) == 1:
            lines, source = values[0]
            text = "\n".join(line.rstrip() for line in lines).strip()
            if name == "Expected result":
                text = opening_readme_paragraph("\n".join(source)) or text
        result[name] = text if text and len(text) <= 2000 else None
    return result


def opening_readme_paragraph(source: str) -> str | None:
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).parse(source)
    if tokens and tokens[0].type == "paragraph_open":
        return tokens[1].content.replace("\n", " ") or None
    return None


def nested_checkout(repo: Path, path: Path) -> bool:
    for parent in [path, *path.parents]:
        if parent == repo:
            return False
        if (parent / ".git").exists():
            return True
    return False


def task_sort(task: dict) -> tuple[str, str, str]:
    return (PurePosixPath(task["path"]).parent.as_posix(), task["display_id"], task["path"])


def render_readme(source: str) -> str:
    """Render Markdown offline; embedded HTML is text, never executable markup."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise ReportError("README previews require markdown-it-py; use the Eval Author Python environment.") from exc
    renderer = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    # Keep link text visible without allowing navigation out of the offline preview.
    renderer.add_render_rule("link_open", lambda *_: '<a aria-disabled="true">')
    return renderer.render(source)


class ReadmeTitle(HTMLParser):
    """Read the first H1 from rendered Markdown, ignoring code and inline markup."""

    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.found = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h1" and not self.found:
            self.active = True
        elif tag == "br" and self.active:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self.active:
            self.active = False
            self.found = True

    def handle_data(self, data: str) -> None:
        if self.active:
            self.parts.append(data)


def readme_title(rendered: str) -> str | None:
    parser = ReadmeTitle()
    parser.feed(rendered)
    title = " ".join("".join(parser.parts).split())
    return title if title and len(title) <= 2000 else None


def readme_grading_sections(source: str) -> dict[str, list[list[str]]]:
    """Read grading prose, excluding code blocks and tables."""
    from markdown_it import MarkdownIt

    def plain(token: Any) -> str:
        return "".join(
            "\n" if child.type in {"softbreak", "hardbreak"} else child.content
            for child in token.children or []
            if child.type in {"text", "code_inline", "softbreak", "hardbreak"}
        ).strip()

    sections: dict[str, list[list[str]]] = {}
    current: list[str] | None = None
    tokens = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).parse(source)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in {"h1", "h2"}:
            current = None
            if token.tag == "h2":
                heading = re.sub(r"\s*\(.*\)\s*$", "", plain(tokens[index + 1])).casefold()
                if heading in {"grader summary", "grading summary"}:
                    key = "summary"
                elif heading in {"grading", "grader", "expected outcome and grading"}:
                    key = "grading"
                else:
                    continue
                current = []
                sections.setdefault(key, []).append(current)
        elif current is not None and token.type == "inline" and tokens[index - 1].type == "paragraph_open":
            paragraph = plain(token)
            if paragraph:
                current.append(paragraph)
    return sections


def readme_grading_kind(source: str) -> str | None:
    """Prefer an explicit README type; never infer a verifier from prose."""
    grading = readme_grading_sections(source).get("grading", [])
    declarations = [
        match.group(1).strip()
        for section in grading
        for paragraph in section
        for line in paragraph.splitlines()
        if (match := re.match(r"^Type\s*:\s*(.*)$", line, re.I))
    ]
    if not declarations:
        return None
    if len(grading) != 1 or len(declarations) != 1:
        return "Not documented"
    value = declarations[0]
    return LEGACY_GRADING_KINDS.get(value, value if value in GRADING_KINDS else "Not documented")


def readme_grading_summary(source: str, imported: str | None = None) -> str | None:
    """Read a short grading description without calling a model or executing code."""

    def excerpt(value: str) -> str | None:
        words = value.split()
        if len(words) < 5:
            return None
        summary = " ".join(words[:10])
        if len(words) > 10:
            summary = summary.rstrip(",;:.") + "…"
        return summary if len(summary) <= 200 else None

    sections = readme_grading_sections(source)
    for groups in sections.values():
        for paragraphs in groups:
            paragraphs[:] = [
                " ".join(
                    line
                    for line in paragraph.splitlines()
                    if not re.match(r"^(?:type|verifier|grader|tests|source|implementation)\s*:", line, re.I)
                )
                for paragraph in paragraphs
            ]
    summaries = sections.get("summary", [])
    if summaries:
        return excerpt(" ".join(summaries[0])) if len(summaries) == 1 else None
    if imported is not None:
        return imported
    grading = sections.get("grading", [])
    if len(grading) == 1:
        for paragraph in grading[0]:
            sentence = re.split(r"(?<=[.!?])\s+", paragraph, maxsplit=1)[0]
            summary = excerpt(sentence)
            if summary is not None:
                return summary
    return None


def collect_tasks(repo: Path, metadata: dict, max_depth: int, max_entries: int) -> tuple[list[dict], bool, list[str]]:
    found: dict[str, dict] = {}
    excluded: set[str] = set()
    complete = True
    for root_name in metadata["task_roots"]:
        root = local(repo, root_name)
        if not root.is_dir():
            complete = False
            continue
        search = search_tasks(root, provider="both", max_depth=max_depth, max_entries=max_entries)
        complete &= search["search"]["complete"]
        for exclusion in search["search"]["excluded"]:
            if exclusion["reason"] in {"nested-repository", "internal-or-legacy-artifacts"}:
                excluded.add((root / exclusion["path"]).relative_to(repo).as_posix())
        for item in search["candidates"]:
            source = root / item["path"]
            path = source.relative_to(repo).as_posix()
            directory = source if source.is_dir() else source.parent
            # Also guard candidates returned by older installed scanners.
            if nested_checkout(root, directory):
                excluded.add(path)
                continue
            overrides = metadata["tasks"].get(path, {})
            issues = []
            sections: dict[str, str | None] = {}
            revision = None
            readme_html = None
            grading_summary = None
            grading_kind = None
            readme = directory / "README.md"
            if item["provider"] == "gym":
                # Native NeMo Gym scaffolds keep the task README above environments/<name>.
                for parent in [directory, *directory.parents]:
                    if parent in {repo, repo / ".eval-author"}:
                        break
                    candidate = parent / "README.md"
                    if candidate.exists():
                        readme = candidate
                        break
                issues.append("NeMo Gym workload candidate; native validation has not been run.")
            readme_path = readme.relative_to(repo).as_posix()
            if readme.exists():
                try:
                    content = read_bytes(local(repo, readme_path), MAX_README_BYTES)
                    sections = readme_sections(content.decode("utf-8"))
                    revision = hashlib.sha256(content).hexdigest()
                except (ReportError, OSError, UnicodeError):
                    issues.append("Task README could not be read.")
                else:
                    readme_source = content.decode("utf-8")
                    readme_html = render_readme(readme_source)
                    imported_summary = overrides.get("grading_summary")
                    if imported_summary is not None and readme_path not in overrides.get("source_refs", []):
                        raise ReportError("Imported grader descriptions require the task README in source_refs.")
                    grading_summary = readme_grading_summary(readme_source, imported_summary)
                    grading_kind = readme_grading_kind(readme_source)
            native_name = directory.name
            if item["provider"] == "harbor":
                try:
                    config = tomllib.loads(read_bytes(local(repo, f"{path}/task.toml")).decode("utf-8"))
                    name = mapping(config.get("task")).get("name") or config.get("name")
                    if isinstance(name, str) and name.strip():
                        native_name = name
                except (ReportError, OSError, UnicodeError, ValueError):
                    issues.append("Task configuration could not be parsed; native validation has not been run.")
            elif source.is_file() and source.name not in {"manifest.yaml", "config.yaml"}:
                native_name = source.stem
            behavior = sections.get("Behavior under test")
            outcome = sections.get("Expected result")
            # Imported metadata is only a fallback; explicit README headings win.
            if "Behavior under test" not in sections:
                behavior = overrides.get("behavior")
            if "Expected result" not in sections:
                outcome = overrides.get("expected_outcome")
            if behavior is None or outcome is None:
                issues.append("Behavior under test or Expected result is not documented in unique nonempty sections.")
            title = readme_title(readme_html) if readme_html is not None else None
            found[path] = {
                "id": item["provider"] + ":" + path,
                "provider": item["provider"],
                "path": path,
                "display_id": overrides.get("display_id", directory.name),
                "name": native_name,
                "title": title,
                "behavior": behavior,
                "expected_outcome": outcome,
                "grading_kind": (
                    overrides["grading_kind"]
                    if overrides.get("grading_kind", "Not documented") != "Not documented"
                    else grading_kind or "Not documented"
                ),
                "grading_rule": overrides.get("grading_rule"),
                "grading_summary": grading_summary,
                "readme_revision": revision,
                "readme_path": readme_path,
                "readme_html": readme_html,
                "status": "found",
                "issues": issues,
            }
    return (
        sorted(found.values(), key=task_sort),
        complete,
        sorted(excluded),
    )


def apply_task_readiness(repo: Path, tasks: list[dict]) -> list[str]:
    """Project workflow acceptance without modifying task files or saved decisions."""
    if not tasks:
        return []
    state = None
    reason = "No task acceptance has been recorded."
    warnings = []
    try:
        path = journey_state_path(repo)
        if path.exists():
            state = load_state(repo)
    except (OSError, ValueError, TypeError, KeyError):
        reason = "Shared workflow state is unavailable or invalid; task readiness is unknown."
        warnings.append(reason)
    for task in tasks:
        try:
            result = (
                task_lifecycle(repo, state, task["path"])
                if state is not None
                else {"status": "unknown", "reason": reason}
            )
        except ValueError:
            # Inventory supports a root task ("."); lifecycle records do not.
            result = {"status": "unknown", "reason": "Task readiness is unavailable for this inventory path."}
        task["readiness"] = result["status"]
        task["readiness_reason"] = result["reason"]
    return warnings


def within_run_scope(path: Path, root: Path) -> bool:
    """Exclude private working evidence unless selected, retaining historical runs."""
    if not path.is_relative_to(root):
        return False
    return not any(
        parent.name == ".working"
        and parent.parent.name == ".eval-author"
        and parent != root
        and parent.is_relative_to(root)
        for parent in (path, *path.parents)
    )


def run_files(repo: Path, roots: list[str], max_depth: int, max_entries: int) -> tuple[list[Path], bool]:
    """Bounded result/config inventory; do not follow links or nested checkouts."""
    files: set[Path] = set()
    complete = True
    excluded = {".git", ".venv", "node_modules", "__pycache__"}
    for root_name in roots:
        root = local(repo, root_name)
        if not root.exists():
            continue
        pending = ["."]
        count = 0
        while pending:
            directory = root / heapq.heappop(pending)
            try:
                with os.scandir(directory) as listing:
                    entries = []
                    for entry in listing:
                        entries.append(entry)
                        if count + len(entries) > max_entries:
                            break
                if count + len(entries) > max_entries:
                    complete = False
                    break
                count += len(entries)
                for entry in sorted(entries, key=lambda e: e.name):
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        complete = False
                        continue
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        continue
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode) and entry.name not in excluded:
                        if (path / ".git").exists() or not within_run_scope(path, root):
                            continue
                        rel = path.relative_to(root)
                        if len(rel.parts) > max_depth:
                            complete = False
                        else:
                            heapq.heappush(pending, rel.as_posix())
                    elif stat.S_ISREG(info.st_mode) and entry.name in {"result.json", "config.json"}:
                        files.add(path)
            except OSError:
                complete = False
    return sorted(files), complete


def mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def text(value: Any) -> str | None:
    return value if isinstance(value, str) and len(value) <= 2000 else None


def timestamp(value: Any) -> str | None:
    value = text(value)
    if value:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return None


def trial_record(path: str, data: dict, config: dict) -> dict:
    agent = mapping(data.get("agent_info"))
    model = mapping(agent.get("model_info"))
    task = mapping(data.get("task_id")) or mapping(config.get("task"))
    rewards = mapping(mapping(data.get("verifier_result")).get("rewards"))
    rewards = {
        k: v for k, v in rewards.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    }
    execution = "unknown"
    steps = data.get("step_results")
    steps = steps if isinstance(steps, list) else []
    if data.get("exception_info") is not None or any(mapping(s).get("exception_info") is not None for s in steps):
        execution = "error"
    elif timestamp(data.get("finished_at")):
        execution = "completed"
    elif timestamp(data.get("started_at")):
        execution = "pending"
    task_name = text(data.get("task_name"))
    route = None
    if task_name and text(agent.get("name")):
        parts = [
            data.get("source") or "_",
            agent["name"],
            model.get("provider") or "_",
            model.get("name") or "_",
            task_name,
        ]
        if all(isinstance(v, str) for v in parts):
            route = "tasks/" + "/".join(quote(v, safe="") for v in parts) + "/trials/" + quote(Path(path).name, safe="")
    return {
        "id": text(data.get("id")) or path,
        "path": path,
        "task_name": task_name,
        "task_path": text(task.get("path")),
        "task_checksum": text(data.get("task_checksum")),
        "started_at": timestamp(data.get("started_at")),
        "finished_at": timestamp(data.get("finished_at")),
        "agent": text(agent.get("name")) or text(mapping(config.get("agent")).get("name")),
        "rewards": rewards,
        "execution": execution,
        "invalid_reason": None,
        "route": route,
    }


def score_run(
    attempts: list[dict], total: int | None, metadata: dict, blocked: str | None = None, *, partial: bool = False
) -> dict:
    """Sum one named reward; execution coverage is independent of pass rules."""
    errors = sum(a["execution"] == "error" for a in attempts)
    invalid = sum(a["invalid_reason"] is not None and a["execution"] != "error" for a in attempts)
    completed = sum(a["execution"] == "completed" for a in attempts)
    key, target = metadata.get("reward_key", "reward"), metadata.get("pass_value")
    values = [
        a["rewards"][key]
        for a in attempts
        if a["execution"] == "completed" and a["invalid_reason"] is None and key in a["rewards"]
    ]
    scored = len(values)
    denominator = total if total is not None else len(attempts)
    unknown = max(0, denominator - scored - errors - invalid)
    passed = sum(value == target for value in values) if target is not None and scored and not blocked else None
    failed = scored - passed if passed is not None else 0
    reward_sum = score_min = score_max = None
    if values and not blocked:
        try:
            reward_sum = math.fsum(values)
        except OverflowError:
            blocked = "reward total exceeds numeric limits"
        if reward_sum is not None and not math.isfinite(reward_sum):
            reward_sum = None
            blocked = "reward total exceeds numeric limits"
    label = f"Score unavailable ({blocked})" if blocked else "Score unavailable"
    if reward_sum is not None:
        label = f"{key}: {reward_sum:.6g} total"
        low, high = metadata.get("reward_min"), metadata.get("reward_max")
        if low is not None and high is not None and all(low <= value <= high for value in values):
            lower, upper = scored * low, scored * high
            if all(math.isfinite(value) for value in (lower, upper, upper - lower)) and lower < upper:
                score_min, score_max = lower, upper
                percent = 100 * (reward_sum - score_min) / (score_max - score_min)
                label = f"{reward_sum:.6g} / {score_max:.6g} {key} ({percent:.1f}%)"
                if low != 0:
                    label += f"; range {score_min:.6g} to {score_max:.6g}"
        elif low is not None and high is not None:
            label += " (outside declared range; graph unavailable)"
        else:
            label += " (reward range not documented)"
    coverage = f"{scored}/{total} scored" if total is not None else f"{scored} scored; total unknown"
    if partial or total is None or scored != total:
        coverage += "; partial"
    label += f" · {coverage}"
    return {
        "reward_key": key,
        "reward_sum": reward_sum,
        "score_min": score_min,
        "score_max": score_max,
        "scored": scored,
        "completed": completed,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "invalid": invalid,
        "unknown": unknown,
        "score_label": label,
    }


def run_sort(run: dict) -> tuple:
    if run["date"]:
        date = datetime.fromisoformat(run["date"].replace("Z", "+00:00"))
        # Naive timestamps retain their displayed form, not a fabricated zone.
        order = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
        return (0, -order.timestamp(), run["path"])
    return (1, 0, run["path"])


def collect_runs(repo: Path, metadata: dict, max_depth: int, max_entries: int) -> tuple[list[dict], bool]:
    paths, complete = run_files(repo, metadata["jobs_roots"], max_depth, max_entries)
    folders: dict[str, dict] = {}
    for file in paths:
        path = file.parent.relative_to(repo).as_posix()
        record = folders.setdefault(path, {"issues": [], "result": {}, "config": {}})
        try:
            data = decode_json(read_bytes(local(repo, file.relative_to(repo).as_posix())))
            if not isinstance(data, dict):
                raise ReportError("Native artifact must be an object.")
            record[file.stem] = data
        except (OSError, ReportError):
            record["issues"].append(f"Could not read {file.name}; no result inferred.")
    jobs = {
        path
        for path, r in folders.items()
        if "n_total_trials" in r["result"] or "stats" in r["result"] or "job_name" in r["config"]
    }
    groups: dict[str, list[dict]] = {path: [] for path in jobs}
    for path, record in folders.items():
        if path in jobs:
            continue
        owner = next((p.as_posix() for p in PurePosixPath(path).parents if p.as_posix() in jobs), None)
        groups.setdefault(owner or path, []).append(trial_record(path, record["result"], record["config"]))
        if owner:
            folders[owner]["issues"].extend(record["issues"])
    runs = []
    for path, attempts in sorted(groups.items()):
        record = folders[path]
        data = record["result"]
        override = metadata["runs"].get(path, {})
        for attempt in attempts:
            attempt["invalid_reason"] = override.get("invalid_attempts", {}).get(attempt["id"])
        is_job = path in jobs
        total = data.get("n_total_trials") if is_job else 1
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            total = None
        issues = list(record["issues"])
        blocked = None
        if total is not None and total < len(attempts):
            issues.append(
                "Native total is smaller than retained attempts; retries or conflicting evidence require review."
            )
            blocked = "conflicting trial total or retries"
            total = None
        ids = [a["id"] for a in attempts]
        duplicate_ids = len(ids) != len(set(ids))
        if duplicate_ids:
            issues.append("Duplicate native attempt IDs; score unavailable.")
            blocked = "duplicate attempt IDs"
        configured_name = text(record["config"].get("job_name"))
        if is_job and configured_name and configured_name != PurePosixPath(path).name:
            issues.append("Configured job_name differs from the on-disk directory name; displaying the directory name.")
        agents = {a["agent"] for a in attempts}
        role = override.get("role", "control" if agents and agents <= {"oracle", "nop"} else "unknown")
        date = timestamp(data.get("finished_at")) or timestamp(data.get("started_at"))
        if not date and attempts:
            dates = [a["finished_at"] or a["started_at"] for a in attempts]
            date = max((d for d in dates if d), default=None)
        finished = bool(timestamp(data.get("finished_at")))
        terminal = all(a["execution"] in {"completed", "error"} for a in attempts)
        status = "completed" if finished and total == len(attempts) and terminal else "incomplete"
        if not data:
            status = "unknown"
        parent = PurePosixPath(path).parent.as_posix()
        route = "jobs/" + quote(PurePosixPath(path).name, safe="") if is_job else None
        viewer_root = parent
        if not is_job and attempts and attempts[0]["route"]:
            # Native viewer also treats a directory of standalone trials as a job.
            viewer_root = PurePosixPath(parent).parent.as_posix()
            route = "jobs/" + quote(PurePosixPath(parent).name, safe="") + "/" + attempts[0]["route"]
        score = score_run(attempts, total, override, blocked, partial=status != "completed")
        runs.append(
            {
                "id": text(data.get("id")) or path,
                "path": path,
                "name": PurePosixPath(path).name,
                "kind": "job" if is_job else "standalone_trial",
                "role": role,
                "status": status,
                "date": date,
                "total": total,
                **score,
                "viewer_root": viewer_root,
                "route": route,
                "attempts": attempts,
                "issues": sorted(set(issues)),
            }
        )
    return sorted(runs, key=run_sort), complete


def metadata_for(
    previous: dict | None, patch: dict, task_roots: list[str] | None, jobs_roots: list[str] | None
) -> dict:
    metadata: dict[str, Any] = (
        deepcopy(previous["metadata"])
        if previous
        else {
            "task_roots": ["."],
            "jobs_roots": [".eval-author/jobs"],
            "tasks": {},
            "runs": {},
        }
    )
    validate(patch, obj(METADATA_SCHEMA["properties"], []))
    for key, value in patch.items():
        if key in {"tasks", "runs"}:
            for path, fields in value.items():
                metadata[key].setdefault(relative(path), {}).update(fields)
        else:
            metadata[key] = value
    if task_roots is not None:
        metadata["task_roots"] = task_roots
    if jobs_roots is not None:
        metadata["jobs_roots"] = jobs_roots
    for key in ("task_roots", "jobs_roots"):
        metadata[key] = sorted({relative(p) for p in metadata[key]})
    for key in ("tasks", "runs"):
        for path in metadata[key]:
            relative(path)
    for fields in metadata["runs"].values():
        if "pass_value" in fields and "reward_key" not in fields:
            raise ReportError("A run pass rule requires reward_key.")
        if ("reward_min" in fields) != ("reward_max" in fields):
            raise ReportError("A reward scale requires both reward_min and reward_max.")
        for key in ("pass_value", "reward_min", "reward_max"):
            if key in fields and not math.isfinite(fields[key]):
                raise ReportError("Reward metadata requires finite numbers.")
        if "reward_min" in fields and fields["reward_min"] >= fields["reward_max"]:
            raise ReportError("reward_min must be smaller than reward_max.")
    for fields in metadata["tasks"].values():
        if (
            fields.get("behavior") or fields.get("expected_outcome") or fields.get("grading_summary")
        ) and not fields.get("source_refs"):
            raise ReportError("Imported task descriptions require source_refs.")
        if "grading_kind" in fields:
            fields["grading_kind"] = LEGACY_GRADING_KINDS.get(fields["grading_kind"], fields["grading_kind"])
        for ref in fields.get("source_refs", []):
            relative(ref)
    validate(metadata, METADATA_SCHEMA)
    return metadata


def markdown_text(value: str) -> str:
    """Keep artifact text literal in Markdown headings and table cells."""
    value = escape(value, quote=False)
    value = re.sub(r"([\\`*_{}\[\]()#!|~])", r"\\\1", value)
    return "<br>".join(value.splitlines())


def artifact_link(label: str, path: str) -> str:
    # Both report outputs live in .eval-author; roots may be elsewhere in the repo.
    target = quote(posixpath.relpath(path, ".eval-author"), safe="/.")
    return f"[{label}]({target})"


def render_markdown(data: dict) -> str:
    """Mirror the HTML content tabs from their validated data, without authoring prose."""
    lines = [
        README_BEGIN.decode(),
        "<!-- Generated by Eval Author report.py; edit task documentation or report metadata, then refresh. -->",
        "# NeMo Eval Author Report",
        "",
        "Eval suite documentation, run results and test case directory.",
        "",
        "[Open HTML report](report.html)",
        "",
    ]
    for warning in data["warnings"]:
        lines.extend([f"> {markdown_text(warning)}", ""])
    lines.extend(["## Tasks", ""])
    directory = None
    for task in data["tasks"]:
        parent = PurePosixPath(task["path"]).parent.as_posix()
        if parent != directory:
            if directory is not None:
                lines.append("")
            directory = parent
            lines.extend(
                [
                    "### " + markdown_text(parent.removeprefix(".eval-author/")),
                    "",
                    "| Task | Status | Behavior | Expected outcome | Verifier | Actions |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
        grading = task["grading_kind"]
        name = []
        if task["status"] == "unverified":
            name.append("Inventory unverified")
        name.extend(task["issues"])
        if task["status"] == "found" and task["provider"] == "harbor":
            route = "task-definitions/" + quote(PurePosixPath(task["path"]).name, safe="")
            actions = [f"[View task](http://127.0.0.1:8081/{route})"]
        elif task["status"] == "found":
            actions = [artifact_link("NeMo Gym source", task["path"])]
        else:
            actions = [artifact_link("Review saved artifacts", task["path"])]
        if task["readme_html"] is None:
            actions.append("No readable README was available when this report was generated.")
        elif task["status"] == "unverified":
            actions.append("[Saved Task README snapshot](report.html#cases)")
        else:
            actions.append(artifact_link("Task README", task["readme_path"]))
        name_cell = [
            "<strong><code>" + markdown_text(task.get("title") or task["name"]) + "</code></strong>",
            *(markdown_text(part) for part in name),
            "<code>" + markdown_text(task["path"].removeprefix(".eval-author/")) + "</code>",
        ]
        cells = [
            "<br>".join(name_cell),
            markdown_text(task.get("readiness", "unknown").capitalize())
            + "<br>"
            + markdown_text(task.get("readiness_reason", "No task acceptance has been recorded.")),
            markdown_text(task["behavior"] or "Not documented"),
            markdown_text(task["expected_outcome"] or "Not documented"),
            markdown_text(grading) + "<br>" + markdown_text(task.get("grading_summary") or NO_GRADING_SUMMARY),
            "<br>".join(actions),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if data["tasks"]:
        lines.append("")
    if not data["tasks"]:
        lines.extend(["No tasks match.", ""])
    lines.extend(["## Runs", ""])
    lines.extend(markdown_runs(data["runs"], "Job"))
    if not data["runs"]:
        lines.append("No recorded evaluation jobs.")
    if data["standalone_trials"]:
        lines.extend(["", "### Legacy trials", "", "Standalone trials are not evaluation jobs.", ""])
        lines.extend(markdown_runs(data["standalone_trials"], "Trial"))
    lines.extend(["", README_END.decode()])
    return "\n".join(lines).rstrip() + "\n"


def update_markdown(existing: bytes | None, rendered: str) -> bytes:
    """Replace only the marked report section; preserve all surrounding bytes."""
    generated = rendered.encode("utf-8")
    if existing is None:
        return generated
    begins = list(re.finditer(rb"(?m)^" + re.escape(README_BEGIN) + rb"\r?$", existing))
    ends = list(re.finditer(rb"(?m)^" + re.escape(README_END) + rb"\r?$", existing))
    if not begins and not ends and README_BEGIN not in existing and README_END not in existing:
        separator = (
            b"" if not existing or existing.endswith(b"\n\n") else b"\n" if existing.endswith(b"\n") else b"\n\n"
        )
        return existing + separator + generated
    if (
        len(begins) != 1
        or len(ends) != 1
        or existing.count(README_BEGIN) != 1
        or existing.count(README_END) != 1
        or begins[0].end() >= ends[0].start()
    ):
        raise ReportError(
            "README report markers are incomplete or ambiguous; preserve the content and repair the markers."
        )
    # Leave the end marker's newline with the user-authored suffix, including CRLF.
    end = ends[0].end()
    if existing[end - 1 : end] == b"\r":
        end -= 1
    return existing[: begins[0].start()] + generated.rstrip(b"\n") + existing[end:]


@contextmanager
def report_lock(path: Path):
    """An OS lock releases on process exit; keep the file to avoid inode races."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ReportError("Report refresh already running; retry after it finishes.") from exc
            raise
        yield
    finally:
        os.close(descriptor)


def markdown_runs(runs: list[dict], identity: str) -> list[str]:
    if not runs:
        return []
    lines = [f"| Date / time | {identity} | Reward | Actions |", "| --- | --- | --- | --- |"]
    for run in runs:
        labels = ["Role not documented" if run["role"] == "unknown" else run["role"], run["status"]]
        labels.append(f"{run['completed']} completed")
        for key, label in (("errors", "execution errors"), ("invalid", "invalid"), ("unknown", "pending or unscored")):
            if run[key]:
                labels.append(f"{run[key]} {label}")
        if run["passed"] is not None:
            labels.append(f"{run['passed']}/{run['scored']} passed")
            labels.append(f"{run['failed']} failed")
        action = artifact_link("Review saved artifacts", run["path"])
        if run["route"] and run["status"] != "unverified":
            action = f"[Harbor viewer](http://127.0.0.1:8080/{run['route']})"
        cells = [
            markdown_text(run["date"].replace("T", "\n") if run["date"] else "Date not recorded"),
            "<br>".join(markdown_text(part) for part in [run["name"], run["path"], " · ".join(labels), *run["issues"]]),
            markdown_text("Inventory unverified" if run["status"] == "unverified" else run["score_label"]),
            action,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def refresh(
    repo: Path,
    *,
    task_roots: list[str] | None = None,
    jobs_roots: list[str] | None = None,
    patch: dict | None = None,
    max_depth: int = 12,
    max_entries: int = 50000,
) -> dict:
    repo = repo.resolve(strict=True)
    output = local(repo, ".eval-author/report.html")
    markdown_output = local(repo, ".eval-author/README.md")
    output.parent.mkdir(exist_ok=True)
    try:
        state_path = working_path(repo, "report", "state.json")
        lock = working_path(repo, "report", ".report.lock")
    except ValueError as exc:
        raise ReportError(str(exc)) from exc
    with report_lock(lock):
        temporary_files = []
        try:
            previous, old_state, old_bytes = previous_snapshot(state_path, output)
            old_markdown = read_bytes(markdown_output, MAX_REPORT_BYTES) if markdown_output.exists() else None
            metadata = metadata_for(previous, patch or {}, task_roots, jobs_roots)
            tasks, tasks_complete, excluded = collect_tasks(repo, metadata, max_depth, max_entries)
            collected, runs_complete = collect_runs(repo, metadata, max_depth, max_entries)
            runs = [run for run in collected if run["kind"] == "job"]
            standalone_trials = [run for run in collected if run["kind"] != "job"]
            job_paths = {run["path"] for run in runs}
            owned_trials = job_paths | {attempt["path"] for run in runs for attempt in run["attempts"]}
            warnings = []
            for directory in excluded:
                if Path(directory).name != "task-drafts" or any(
                    Path(root).is_relative_to(Path(directory)) for root in metadata["task_roots"]
                ):
                    continue
                warnings.append(
                    f"Legacy task directory {directory} is excluded. To review or resume its retained tasks "
                    f"without moving bound evidence, refresh with --task-root {directory}."
                )
            for records, key, complete in [
                (tasks, "tasks", tasks_complete),
                (runs, "runs", runs_complete),
                (standalone_trials, "standalone_trials", runs_complete),
            ]:
                if not complete:
                    label = "Legacy trials" if key == "standalone_trials" else key.capitalize()
                    warnings.append(f"{label} inventory is incomplete; retained entries may be unverified.")
                current = {r["path"] for r in records}
                roots = metadata["task_roots" if key == "tasks" else "jobs_roots"]
                within_scope = within_task_scope if key == "tasks" else within_run_scope
                for old in (previous or {}).get(key, []):
                    if key == "standalone_trials" and old["path"] in owned_trials:
                        continue
                    in_scope = any(within_scope(Path(old["path"]), Path(root)) for root in roots)
                    if old["path"] not in current and in_scope and not complete:
                        issues = sorted(set(old["issues"] + ["Source artifacts were not found in this scan."]))
                        records.append({**old, "status": "unverified", "issues": issues})
            warnings.extend(apply_task_readiness(repo, tasks))
            tasks.sort(key=task_sort)
            runs.sort(key=run_sort)
            standalone_trials.sort(key=run_sort)
            data = {
                "schema_version": 1,
                "metadata": metadata,
                "tasks": tasks,
                "runs": runs,
                "standalone_trials": standalone_trials,
                "warnings": warnings,
                "inventory": {"complete": tasks_complete and runs_complete, "excluded": excluded},
            }
            validate(data, REPORT_SCHEMA)
            payload = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            template = Path(__file__).with_name("report-template.html").read_text(encoding="utf-8")
            if template.count(MARKER) != 1:
                raise ReportError("The report template must have one data insertion marker.")
            content = template.replace(MARKER, payload).encode("utf-8")
            markdown_content = update_markdown(old_markdown, render_markdown(data))
            if max(len(content), len(markdown_content)) > MAX_REPORT_BYTES:
                raise ReportError("Report exceeds the bounded output size; select smaller artifact roots.")
            state_content = (
                json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"
            ).encode()
            if len(state_content) > MAX_REPORT_BYTES:
                raise ReportError("Report state exceeds the bounded output size; select smaller artifact roots.")
            outputs = [
                (state_path, state_content, old_state),
                (markdown_output, markdown_content, old_markdown),
                (output, content, old_bytes),
            ]
            if old_state is None and previous is not None:
                try:
                    imported_path = working_path(repo, "report", "legacy-import.json")
                except ValueError as exc:
                    raise ReportError(str(exc)) from exc
                if not imported_path.exists():
                    imported = (
                        json.dumps(previous, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode()
                    if len(imported) > MAX_REPORT_BYTES:
                        raise ReportError("Legacy report snapshot exceeds the bounded output size.")
                    outputs.insert(0, (imported_path, imported, None))
            # Prepare every file before publishing. Preserve any import, then commit
            # state before projections; a refresh repairs an interrupted publication.
            for destination, rendered, old in outputs:
                if rendered == old:
                    continue
                with tempfile.NamedTemporaryFile(
                    dir=state_path.parent, prefix=".report-", suffix=".tmp", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    temporary_files.append((temporary, destination))
                    stream.write(rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
            for destination, _, old in outputs:
                current = (
                    projection_version(destination)
                    if destination == output
                    else read_bytes(destination, MAX_REPORT_BYTES)
                    if destination.exists()
                    else None
                )
                if current != old:
                    raise ReportError("Report changed during refresh; retry without overwriting concurrent edits.")
            for temporary, destination in temporary_files:
                os.replace(temporary, destination)
            return data
        finally:
            for temporary, _ in temporary_files:
                temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["refresh"])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--task-root", action="append", help="Project-relative root; repeat to select several.")
    parser.add_argument("--jobs-root", action="append", help="Project-relative jobs/trials root; repeatable.")
    parser.add_argument("--metadata", type=Path, help="Optional JSON metadata patch; never task execution settings.")
    parser.add_argument("--max-depth", type=int, default=12)
    parser.add_argument("--max-entries", type=int, default=50000)
    args = parser.parse_args()
    try:
        if args.max_depth < 0 or args.max_entries < 1:
            raise ReportError("Scan bounds must be nonnegative depth and positive entry count.")
        patch = decode_json(read_bytes(args.metadata)) if args.metadata else {}
        data = refresh(
            args.repo,
            task_roots=args.task_root,
            jobs_roots=args.jobs_root,
            patch=patch,
            max_depth=args.max_depth,
            max_entries=args.max_entries,
        )
        print(
            json.dumps(
                {
                    "report": ".eval-author/report.html",
                    "readme": ".eval-author/README.md",
                    "tasks": len(data["tasks"]),
                    "runs": len(data["runs"]),
                    "standalone_trials": len(data["standalone_trials"]),
                    "complete": data["inventory"]["complete"],
                }
            )
        )
        return 0 if data["inventory"]["complete"] else 1
    except (ReportError, OSError, UnicodeError, RecursionError) as exc:
        print(f"Report not refreshed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
