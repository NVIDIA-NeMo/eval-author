#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Refresh an offline Harbor HTML report and its Markdown mirror without executing evaluations.

Python 3.11+, jsonschema, and markdown-it-py are required. Discovery is the shared file-only
search, not the readiness workflow. Only selected local files are read. The
report's embedded JSON retains reporting metadata; native evidence and task
README sections remain authoritative. No separate state file is written.
"""

from __future__ import annotations

import argparse
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
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

# Isolated Python invocations must resolve the bundled sibling, never cwd code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from search_tasks import search_tasks  # noqa: E402

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_README_BYTES = 256 * 1024
MARKER = '"__EVAL_AUTHOR_REPORT_DATA__"'
TEXT = {"type": "string", "maxLength": 2000}
PATH = {"type": "string", "minLength": 1, "maxLength": 2000}
NULL_TEXT = {"anyOf": [TEXT, {"type": "null"}]}
NUMBER = {"type": ["number", "null"]}


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
        "grading_kind": {"enum": ["deterministic", "model", "hybrid", "unknown"]},
        "grading_rule": TEXT,
        "source_refs": array(PATH),
    },
    [],
)
RUN_METADATA = obj(
    {
        "role": {"enum": ["agent", "control", "diagnostic", "unknown"]},
        "reward_key": TEXT,
        "pass_value": {"type": "number"},
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
        "path": PATH,
        "display_id": TEXT,
        "name": TEXT,
        "behavior": NULL_TEXT,
        "expected_outcome": NULL_TEXT,
        "grading_kind": TEXT,
        "grading_rule": NULL_TEXT,
        "readme_revision": NULL_TEXT,
        "readme_html": {"type": ["string", "null"], "maxLength": 2 * 1024 * 1024},
        "status": {"enum": ["found", "unverified"]},
        "issues": array(TEXT),
    }
)
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
        "viewer_root": PATH,
        "route": NULL_TEXT,
        "attempts": array(ATTEMPT_SCHEMA),
        "issues": array(TEXT),
    }
)
REPORT_SCHEMA = obj(
    {
        "schema_version": {"const": 1},
        "metadata": METADATA_SCHEMA,
        "tasks": array(TASK_SCHEMA),
        "runs": array(RUN_SCHEMA),
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


def previous_report(path: Path) -> tuple[dict | None, bytes | None]:
    if not path.exists():
        return None, None
    content = read_bytes(path, MAX_REPORT_BYTES)
    parser = EmbeddedData()
    parser.feed(content.decode("utf-8"))
    if len(parser.blocks) != 1:
        raise ReportError("Existing report must contain exactly one report-data block.")
    payload = decode_json(parser.blocks[0])
    # Early report prototypes stored authored product labels. Discard only that
    # retired field; sections now derive from discovered task paths.
    if isinstance(payload, dict) and payload.get("schema_version") == 1:
        for task in payload.get("tasks", []) if isinstance(payload.get("tasks"), list) else []:
            if isinstance(task, dict):
                task.pop("product_area", None)
                task.setdefault("readme_html", None)
        metadata = payload.get("metadata")
        tasks = metadata.get("tasks") if isinstance(metadata, dict) else None
        if isinstance(tasks, dict):
            for fields in tasks.values():
                if isinstance(fields, dict):
                    fields.pop("product_area", None)
    validate(payload, REPORT_SCHEMA)
    return payload, content


def readme_sections(text: str) -> dict[str, str | None]:
    """Read exact H2 sections; ignore headings in fenced code and reject duplicates."""
    names = ("Behavior under test", "Expected result")
    sections: dict[str, list[list[str]]] = {name: [] for name in names}
    current: list[str] | None = None
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            token = match[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[match.end() :].strip():
                fence = ""
            continue
        if fence:
            continue
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading and len(heading[1]) <= 2:
            current = None
            if len(heading[1]) == 2 and heading[2] in sections:
                current = []
                sections[heading[2]].append(current)
        elif current is not None:
            current.append(line)
    result = {}
    for name, values in sections.items():
        if not values:
            continue
        text = " ".join(" ".join(values[0]).split()) if len(values) == 1 else ""
        result[name] = text if text and len(text) <= 2000 else None
    return result


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
    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).render(source)


def collect_tasks(repo: Path, metadata: dict, max_depth: int, max_entries: int) -> tuple[list[dict], bool, list[str]]:
    found: dict[str, dict] = {}
    excluded: set[str] = set()
    complete = True
    for root_name in metadata["task_roots"]:
        root = local(repo, root_name)
        if not root.is_dir():
            complete = False
            continue
        search = search_tasks(root, provider="harbor", max_depth=max_depth, max_entries=max_entries)
        complete &= search["search"]["complete"]
        for exclusion in search["search"]["excluded"]:
            if exclusion["reason"] == "nested-repository":
                excluded.add((root / exclusion["path"]).relative_to(repo).as_posix())
        for item in search["candidates"]:
            directory = root / item["path"]
            path = directory.relative_to(repo).as_posix()
            # Also guard candidates returned by older installed scanners.
            if nested_checkout(root, directory):
                excluded.add(path)
                continue
            overrides = metadata["tasks"].get(path, {})
            issues = []
            sections: dict[str, str | None] = {}
            revision = None
            readme_html = None
            readme = directory / "README.md"
            if readme.exists():
                try:
                    content = read_bytes(local(repo, f"{path}/README.md"), MAX_README_BYTES)
                    sections = readme_sections(content.decode("utf-8"))
                    revision = hashlib.sha256(content).hexdigest()
                except (ReportError, OSError, UnicodeError):
                    issues.append("Task README could not be read.")
                else:
                    readme_html = render_readme(content.decode("utf-8"))
            native_name = directory.name
            try:
                config = tomllib.loads(read_bytes(local(repo, f"{path}/task.toml")).decode("utf-8"))
                name = mapping(config.get("task")).get("name") or config.get("name")
                if isinstance(name, str) and name.strip():
                    native_name = name
            except (ReportError, OSError, UnicodeError, ValueError):
                issues.append("Task configuration could not be parsed; native validation has not been run.")
            behavior = sections.get("Behavior under test")
            outcome = sections.get("Expected result")
            # Imported metadata is only a fallback; explicit README headings win.
            if "Behavior under test" not in sections:
                behavior = overrides.get("behavior")
            if "Expected result" not in sections:
                outcome = overrides.get("expected_outcome")
            if behavior is None or outcome is None:
                issues.append("Behavior under test or Expected result is not documented in unique nonempty sections.")
            found[path] = {
                "id": "harbor:" + path,
                "path": path,
                "display_id": overrides.get("display_id", directory.name),
                "name": native_name,
                "behavior": behavior,
                "expected_outcome": outcome,
                "grading_kind": overrides.get("grading_kind", "unknown"),
                "grading_rule": overrides.get("grading_rule"),
                "readme_revision": revision,
                "readme_html": readme_html,
                "status": "found",
                "issues": issues,
            }
    return (
        sorted(found.values(), key=task_sort),
        complete,
        sorted(excluded),
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
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        continue
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode) and entry.name not in excluded:
                        if (path / ".git").exists():
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


def score_run(attempts: list[dict], total: int | None, metadata: dict) -> dict:
    errors = sum(a["execution"] == "error" for a in attempts)
    invalid = sum(a["invalid_reason"] is not None and a["execution"] != "error" for a in attempts)
    passed = failed = known = 0
    key, target = metadata.get("reward_key"), metadata.get("pass_value")
    for attempt in attempts:
        reward = attempt["rewards"].get(key)
        if attempt["execution"] != "completed" or attempt["invalid_reason"] or reward is None:
            continue
        if key is not None and target is not None:
            known += 1
            passed += reward == target
            failed += reward != target
    denominator = total if total is not None else len(attempts)
    unknown = max(0, denominator - known - errors - invalid)
    if key is not None and target is not None:
        label = f"{passed}/{denominator} passed"
        if total is None:
            label += " (recorded attempts; total unknown)"
    else:
        values = [
            a["rewards"].get("reward") for a in attempts if a["execution"] == "completed" and not a["invalid_reason"]
        ]
        values = [v for v in values if v is not None]
        label = (
            f"reward: {sum(values) / len(values):.3g} mean (pass rule missing from report metadata)"
            if values
            else "Score unavailable"
        )
    return {
        "passed": passed if key is not None and target is not None else None,
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
        if total is not None and total < len(attempts):
            issues.append(
                "Native total is smaller than retained attempts; retries or conflicting evidence require review."
            )
            total = None
        ids = [a["id"] for a in attempts]
        duplicate_ids = len(ids) != len(set(ids))
        if duplicate_ids:
            issues.append("Duplicate native attempt IDs; score unavailable.")
            override = {k: v for k, v in override.items() if k not in {"reward_key", "pass_value"}}
        agents = {a["agent"] for a in attempts}
        role = override.get("role", "control" if agents and agents <= {"oracle", "nop"} else "unknown")
        date = timestamp(data.get("finished_at")) or timestamp(data.get("started_at"))
        if not date and attempts:
            dates = [a["finished_at"] or a["started_at"] for a in attempts]
            date = max((d for d in dates if d), default=None)
        finished = bool(timestamp(data.get("finished_at")))
        status = "completed" if finished and total == len(attempts) else "incomplete"
        if not data:
            status = "unknown"
        parent = PurePosixPath(path).parent.as_posix()
        route = "jobs/" + quote(PurePosixPath(path).name, safe="") if is_job else None
        viewer_root = parent
        if not is_job and attempts and attempts[0]["route"]:
            # Native viewer also treats a directory of standalone trials as a job.
            viewer_root = PurePosixPath(parent).parent.as_posix()
            route = "jobs/" + quote(PurePosixPath(parent).name, safe="") + "/" + attempts[0]["route"]
        score = score_run(attempts, total, override)
        if duplicate_ids:
            score["score_label"] = "Score unavailable (duplicate attempt IDs)"
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
        previous["metadata"]
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
        if ("reward_key" in fields) != ("pass_value" in fields):
            raise ReportError("A run pass rule requires both reward_key and pass_value.")
    for fields in metadata["tasks"].values():
        if (fields.get("behavior") or fields.get("expected_outcome")) and not fields.get("source_refs"):
            raise ReportError("Imported task descriptions require source_refs.")
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
                    "| Test case / grading | Behavior | Expected outcome | Actions |",
                    "| --- | --- | --- | --- |",
                ]
            )
        grading = "Verifier not documented" if task["grading_kind"] == "unknown" else task["grading_kind"]
        name = [task["name"], grading + " · " + (task["grading_rule"] or "Grading not documented")]
        if task["status"] == "unverified":
            name.append("Inventory unverified")
        name.extend([task["path"].removeprefix(".eval-author/"), *task["issues"]])
        if task["status"] == "found":
            route = "task-definitions/" + quote(PurePosixPath(task["path"]).name, safe="")
            actions = [f"[View task](http://127.0.0.1:8081/{route})"]
        else:
            actions = [artifact_link("Review saved artifacts", task["path"])]
        if task["readme_html"] is None:
            actions.append("No readable README was available when this report was generated.")
        elif task["status"] == "unverified":
            actions.append("[Saved Task README snapshot](report.html#cases)")
        else:
            actions.append(artifact_link("Task README", task["path"] + "/README.md"))
        cells = [
            "<br>".join(markdown_text(part) for part in name),
            markdown_text(task["behavior"] or "Not documented"),
            markdown_text(task["expected_outcome"] or "Not documented"),
            "<br>".join(actions),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if data["tasks"]:
        lines.append("")
    if not data["tasks"]:
        lines.extend(["No tasks match.", ""])
    lines.extend(["## Runs", ""])
    if data["runs"]:
        lines.extend(["| Date / time | Run | Score | Actions |", "| --- | --- | --- | --- |"])
    for run in data["runs"]:
        labels = ["Role not documented" if run["role"] == "unknown" else run["role"], run["status"]]
        for key, label in (
            ("errors", "execution errors"),
            ("invalid", "invalid"),
            ("unknown", "pending or unclassified"),
        ):
            if run[key]:
                labels.append(f"{run[key]} {label}")
        action = artifact_link("Review saved artifacts", run["path"])
        if run["route"] and run["status"] != "unverified":
            action = f"[Harbor viewer](http://127.0.0.1:8080/{run['route']})"
        cells = [
            markdown_text(run["date"].replace("T", "\n") if run["date"] else "Date not recorded"),
            "<br>".join(markdown_text(part) for part in [run["name"], " · ".join(labels), *run["issues"]]),
            markdown_text("Inventory unverified" if run["status"] == "unverified" else run["score_label"]),
            action,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if not data["runs"]:
        lines.append("No recorded evaluation runs.")
    return "\n".join(lines).rstrip() + "\n"


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
    lock = local(repo, ".eval-author/.report.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReportError(
            "Report refresh already running; inspect .eval-author/.report.lock if it was interrupted."
        ) from exc
    os.close(descriptor)
    temporary_files = []
    try:
        previous, old_bytes = previous_report(output)
        old_markdown = read_bytes(markdown_output, MAX_REPORT_BYTES) if markdown_output.exists() else None
        metadata = metadata_for(previous, patch or {}, task_roots, jobs_roots)
        tasks, tasks_complete, excluded = collect_tasks(repo, metadata, max_depth, max_entries)
        runs, runs_complete = collect_runs(repo, metadata, max_depth, max_entries)
        warnings = []
        for records, key, complete in [(tasks, "tasks", tasks_complete), (runs, "runs", runs_complete)]:
            if not complete:
                warnings.append(f"{key.capitalize()} inventory is incomplete; retained entries may be unverified.")
            current = {r["path"] for r in records}
            roots = metadata["task_roots" if key == "tasks" else "jobs_roots"]
            for old in (previous or {}).get(key, []):
                in_scope = any(PurePosixPath(old["path"]).is_relative_to(PurePosixPath(root)) for root in roots)
                if old["path"] not in current and in_scope:
                    issues = sorted(set(old["issues"] + ["Source artifacts were not found in this scan."]))
                    records.append({**old, "status": "unverified", "issues": issues})
        tasks.sort(key=task_sort)
        runs.sort(key=run_sort)
        data = {
            "schema_version": 1,
            "metadata": metadata,
            "tasks": tasks,
            "runs": runs,
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
        markdown_content = render_markdown(data).encode("utf-8")
        if max(len(content), len(markdown_content)) > MAX_REPORT_BYTES:
            raise ReportError("Report exceeds the bounded output size; select smaller artifact roots.")
        outputs = [(markdown_output, markdown_content, old_markdown), (output, content, old_bytes)]
        # Prepare both projections before publishing either; a later refresh repairs
        # an interrupted pair even when the HTML itself has not changed.
        for destination, rendered, old in outputs:
            if rendered == old:
                continue
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=".report-", suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                temporary_files.append((temporary, destination))
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
        for destination, _, old in outputs:
            if (read_bytes(destination, MAX_REPORT_BYTES) if destination.exists() else None) != old:
                raise ReportError("Report changed during refresh; retry without overwriting concurrent edits.")
        for temporary, destination in temporary_files:
            os.replace(temporary, destination)
        return data
    finally:
        for temporary, _ in temporary_files:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


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
