# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the sandboxed omp review output and post it as one COMMENT review (host side; never runs PR code)."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import omp_review_github as gh

REVIEW_KEYS = {"version", "head_sha", "overall_correctness", "explanation", "confidence", "findings"}
FINDING_KEYS = {"title", "body", "priority", "confidence", "file_path", "line_start", "line_end"}
MAX_FINDINGS = 50
MAX_SPAN = 9
MAX_BODY = 60000
TRUNCATED = "\n\n(truncated)"
# Sandbox phases recorded by tools/omp-review/run.sh.
PHASES = ("setup", "selftest", "review")

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_MENTION = re.compile(r"@(?=[A-Za-z0-9])")
_ENTITY = re.compile(r"&(?=#|[A-Za-z])")
_HTML = re.compile(r"<(?=[A-Za-z/!?])")
_FENCE_OPEN = re.compile(r"(`{3,})[^`]*|(~{3,}).*")


@dataclass(frozen=True)
class RunStatus:
    """What run.sh recorded about the sandbox run; written on the host, outside the sandbox's reach."""

    phase: str
    exit_code: int
    seconds: int
    timed_out: bool
    spent_usd: float | None
    budget_usd: float | None
    refused: int | None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_unit(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _is_dollars(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _check_finding(index: int, finding: Any) -> None:
    where = f"findings[{index}]"
    if not isinstance(finding, dict) or set(finding) != FINDING_KEYS:
        raise ValueError(f"{where} must have exactly the keys {sorted(FINDING_KEYS)}")
    title, body, path = finding["title"], finding["body"], finding["file_path"]
    if not isinstance(title, str) or not 1 <= len(title) <= 80 or "\n" in title or "\r" in title:
        raise ValueError(f"{where}.title must be a single line of 1-80 characters")
    if not isinstance(body, str) or not 1 <= len(body) <= 4000:
        raise ValueError(f"{where}.body must be 1-4000 characters")
    if not _is_int(finding["priority"]) or not 0 <= finding["priority"] <= 3:
        raise ValueError(f"{where}.priority must be an integer 0-3")
    if not _is_unit(finding["confidence"]):
        raise ValueError(f"{where}.confidence must be a number in [0, 1]")
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError(f"{where}.file_path must be a repository-relative path")
    start, end = finding["line_start"], finding["line_end"]
    if not _is_int(start) or start < 1:
        raise ValueError(f"{where}.line_start must be an integer >= 1")
    if not _is_int(end) or not start <= end <= start + MAX_SPAN:
        raise ValueError(f"{where}.line_end must be within line_start..line_start+{MAX_SPAN}")


def load_review(path: Path, head_sha: str) -> dict[str, Any]:
    """Parse and validate review.json; every way it can be wrong raises ValueError so a failure review is posted."""
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("review.json is missing") from None
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError(f"review.json is not valid JSON ({type(error).__name__})") from None
    try:
        _check_review(review, head_sha)
    except ValueError:
        raise
    except Exception as error:  # review.json is untrusted sandbox output; any validation crash means it is invalid.
        raise ValueError(f"review.json failed validation ({type(error).__name__})") from None
    return review


def _check_review(review: Any, head_sha: str) -> None:
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise ValueError(f"review.json must have exactly the keys {sorted(REVIEW_KEYS)}")
    if review["version"] != 1 or isinstance(review["version"], bool):
        raise ValueError("review.json version must be 1")
    if review["head_sha"] != head_sha:
        raise ValueError("review.json head_sha does not match the reviewed commit")
    if review["overall_correctness"] not in ("correct", "incorrect"):  # a tuple, so unhashable values compare unequal
        raise ValueError("overall_correctness must be correct or incorrect")
    if not isinstance(review["explanation"], str) or not 1 <= len(review["explanation"]) <= 1000:
        raise ValueError("explanation must be 1-1000 characters")
    if not _is_unit(review["confidence"]):
        raise ValueError("confidence must be a number in [0, 1]")
    findings = review["findings"]
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ValueError(f"findings must be a list of at most {MAX_FINDINGS}")
    for index, finding in enumerate(findings):
        _check_finding(index, finding)


def load_status(path: Path | None) -> RunStatus | None:
    """The sandbox run status, or None when run.sh never wrote one (killed, timed out, or never started)."""
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    phase, code, seconds, timed_out = (
        data.get("phase"),
        data.get("exit_code"),
        data.get("seconds"),
        data.get("timed_out"),
    )
    spent, budget, refused = data.get("spent_usd"), data.get("budget_usd"), data.get("refused")
    if phase not in PHASES or not _is_int(code) or not 0 <= code <= 255 or not _is_int(seconds) or seconds < 0:
        return None
    if not isinstance(timed_out, bool) or not all(value is None or _is_dollars(value) for value in (spent, budget)):
        return None
    if refused is not None and (not _is_int(refused) or refused < 0):
        return None
    return RunStatus(phase, code, seconds, timed_out, spent, budget, refused)


def status_line(status: RunStatus | None) -> str:
    if status is None:
        return (
            "No sandbox status: the review job stopped before the sandbox finished (failure, timeout, or cancellation)."
        )
    parts = [f"sandbox phase {status.phase}", f"exit {status.exit_code}", f"{status.seconds // 60} min"]
    if status.timed_out:
        parts.append("time limit reached")
    if status.spent_usd is not None and status.budget_usd is not None:
        parts.append(f"model spend ${status.spent_usd:.2f} of ${status.budget_usd:.2f}")
    if status.refused:
        parts.append(f"{status.refused} model calls refused at the budget")
    return " · ".join(parts)


def verdict(findings: list[dict[str, Any]]) -> str:
    return "incorrect" if any(finding["priority"] <= 2 for finding in findings) else "correct"


def hunk_lines(patch: str) -> list[set[int]]:
    hunks: list[set[int]] = []
    new = 0
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match:
            new = int(match.group(1))
            hunks.append(set())
        elif hunks and line[:1] in {" ", "+"}:
            hunks[-1].add(new)
            new += 1
    return hunks


def placeable(finding: dict[str, Any], files: dict[str, list[set[int]]]) -> bool:
    wanted = set(range(finding["line_start"], finding["line_end"] + 1))
    return any(wanted <= hunk for hunk in files.get(finding["file_path"], []))


def diff_files(repo: str, base_sha: str, head_sha: str, token: str) -> dict[str, list[set[int]]]:
    """Map each changed file to its hunk line sets; files absent from a truncated compare are not placeable."""
    status, compare = gh.api("GET", f"/repos/{repo}/compare/{base_sha}...{head_sha}", token)
    if status != 200 or not isinstance(compare, dict):
        raise RuntimeError(f"GitHub compare {base_sha[:7]}...{head_sha[:7]}: {status}")
    return {entry["filename"]: hunk_lines(entry.get("patch") or "") for entry in compare.get("files") or []}


def neutralize(text: str) -> str:
    """Defang mentions (including entity-encoded ones), images, and raw HTML such as comment markers."""
    text = _MENTION.sub("@\u200b", text)
    text = _ENTITY.sub("&\u200b", text)
    text = _HTML.sub("<\u200b", text)
    return text.replace("![", "!\u200b[")


def sanitize(text: str) -> str:
    """Neutralize model markdown except fenced code, so ```suggestion blocks apply verbatim.

    Only fences opened at column 0 are exempt: GitHub renders those as code whatever precedes them, whereas an
    indented fence can end with its list item and expose its content. An unclosed fence is closed so it cannot
    swallow, or be closed by, markdown that follows this text.
    """
    out: list[str] = []
    closing: re.Pattern[str] | None = None
    marker = ""
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if closing is not None:
            out.append(line)
            if closing.fullmatch(line):
                closing = None
        elif opening := _FENCE_OPEN.fullmatch(line):
            marker = opening.group(1) or opening.group(2)
            closing = re.compile(rf" {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*")
            out.append(line)
        else:
            out.append(neutralize(line))
    if closing is not None:
        out.append(marker)
    return "\n".join(out)


def comment_body(finding: dict[str, Any]) -> str:
    return (
        f"**[P{finding['priority']}] {neutralize(finding['title'])}**\n\n"
        f"{sanitize(finding['body'])}\n\n"
        f"<sub>omp review · confidence {finding['confidence']:.2f}</sub>\n"
        f"{gh.FINDING_MARKER}"
    )


def _finding_entry(finding: dict[str, Any]) -> str:
    location = neutralize(f"{finding['file_path']}:{finding['line_start']}-{finding['line_end']}")
    body = "\n".join(f"  {line}" if line else "" for line in sanitize(finding["body"]).split("\n"))
    return f"- **[P{finding['priority']}] {neutralize(finding['title'])}** — `{location}`\n\n{body}"


def _with_marker(content: str, head_sha: str) -> str:
    marker = f"\n{gh.REVIEW_MARKER}{head_sha} -->"
    if len(content) + len(marker) > MAX_BODY:
        content = content[: MAX_BODY - len(TRUNCATED) - len(marker)] + TRUNCATED
    return content + marker


def review_body(
    review: dict[str, Any],
    listed: list[dict[str, Any]],
    heading: str,
    head_sha: str,
    run_url: str,
    status: RunStatus | None = None,
) -> str:
    findings = review["findings"]
    counts = " · ".join(f"P{p} {sum(f['priority'] == p for f in findings)}" for p in range(4))
    spend = f" · model spend ${status.spent_usd:.2f}" if status and status.spent_usd is not None else ""
    parts = [
        f"### omp review: {verdict(findings).capitalize()}",
        "",
        sanitize(review["explanation"]),
        "",
        f"Confidence {review['confidence']:.2f} · {counts} · reviewed {head_sha[:7]}{spend} · [run]({run_url})",
    ]
    if listed:
        parts += ["", heading, ""] + [_finding_entry(finding) for finding in listed]
    return _with_marker("\n".join(parts), head_sha)


def inline_comment(finding: dict[str, Any]) -> dict[str, Any]:
    comment: dict[str, Any] = {
        "path": finding["file_path"],
        "body": comment_body(finding),
        "line": finding["line_end"],
        "side": "RIGHT",
    }
    if finding["line_start"] < finding["line_end"]:
        comment["start_line"] = finding["line_start"]
        comment["start_side"] = "RIGHT"
    return comment


def review_request(
    review: dict[str, Any],
    files: dict[str, list[set[int]]],
    head_sha: str,
    run_url: str,
    status: RunStatus | None = None,
) -> dict[str, Any]:
    placed = [finding for finding in review["findings"] if placeable(finding, files)]
    unplaced = [finding for finding in review["findings"] if not placeable(finding, files)]
    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": review_body(review, unplaced, "#### Findings outside the diff", head_sha, run_url, status),
        "comments": [inline_comment(finding) for finding in placed],
    }


def fallback_request(
    review: dict[str, Any], head_sha: str, run_url: str, status: RunStatus | None = None
) -> dict[str, Any]:
    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": review_body(review, review["findings"], "#### Findings", head_sha, run_url, status),
        "comments": [],
    }


def failure_request(reason: str, head_sha: str, run_url: str, status: RunStatus | None = None) -> dict[str, Any]:
    body = (
        "### omp review did not complete\n\n"
        f"{neutralize(reason)} · head {head_sha[:7]} · [run]({run_url})\n\n"
        f"{status_line(status)}\n"
        f"{gh.REVIEW_MARKER}{head_sha} -->\n"
        f"{gh.FAILURE_MARKER}"
    )
    return {"commit_id": head_sha, "event": "COMMENT", "body": body, "comments": []}


def _post(repo: str, pr: int, request: dict[str, Any], token: str) -> int:
    status, _ = gh.api("POST", f"/repos/{repo}/pulls/{pr}/reviews", token, request)
    return status


def run(args: argparse.Namespace, token: str) -> int:
    run_status = load_status(args.status)
    try:
        review = load_review(args.review, args.head_sha)
    except ValueError as error:
        request = failure_request(str(error), args.head_sha, args.run_url, run_status)
        if args.dry_run:
            print(json.dumps(request, indent=2))
        elif (status := _post(args.repo, args.pr, request, token)) not in {200, 201}:
            raise RuntimeError(f"posting the failure review returned {status}")
        print(f"omp review did not complete: {error}", file=sys.stderr)
        return 1

    files = diff_files(args.repo, args.base_sha, args.head_sha, token)
    request = review_request(review, files, args.head_sha, args.run_url, run_status)
    if args.dry_run:
        print(json.dumps(request, indent=2))
        return 0
    status = _post(args.repo, args.pr, request, token)
    if status == 422:
        status = _post(args.repo, args.pr, fallback_request(review, args.head_sha, args.run_url, run_status), token)
    if status not in {200, 201}:
        raise RuntimeError(f"posting the review returned {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--status", type=Path, help="run status JSON written by tools/omp-review/run.sh")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    try:
        return run(args, token)
    finally:
        if not args.dry_run:
            gh.api("DELETE", f"/repos/{args.repo}/issues/{args.pr}/labels/{gh.LABEL}", token)


if __name__ == "__main__":
    sys.exit(main())
