# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect pre-fetched PR context for the sandboxed omp review (host side, read-only token)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import omp_review_github as gh

TEMPLATE = Path(__file__).resolve().parent / "omp-review" / "orchestrator.md"
# Successful reviews end with this marker; failure reviews end with FAILURE_MARKER instead. Matching only the
# trailer ignores markers quoted inside model-written code blocks.
_REVIEW_TRAILER = re.compile(re.escape(gh.REVIEW_MARKER) + r"([0-9a-f]{40}) -->\s*\Z")


def _writers(items: list[Any], repo: str, token: str) -> set[str]:
    """Logins of human commenters with write access to the repository."""
    humans = {
        user["login"] for item in items if (user := item.get("user") or {}).get("type") == "User" and user.get("login")
    }
    return {login for login in humans if gh.has_write_access(repo, login, token)}


def _discussion(
    review_comments: list[Any], issue_comments: list[Any], reviews: list[Any], writers: set[str]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for kind, items in (("review_comment", review_comments), ("issue_comment", issue_comments), ("review", reviews)):
        for item in items:
            user = item.get("user") or {}
            if user.get("type") != "User" or user.get("login") not in writers:
                continue
            entries.append(
                {
                    "kind": kind,
                    "user": item["user"].get("login"),
                    "path": item.get("path"),
                    "line": item.get("line"),
                    "in_reply_to_id": item.get("in_reply_to_id"),
                    "body": item.get("body"),
                    "created_at": item.get("created_at") or item.get("submitted_at"),
                }
            )
    return entries


def _finding_title(body: str) -> str:
    first = body.split("\n", 1)[0]
    first = re.sub(r"^\*\*\[P\d\] ", "", first)
    return first.removesuffix("**")


def _prior_findings(review_comments: list[Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in review_comments:
        body = item.get("body") or ""
        if (item.get("user") or {}).get("login") != gh.BOT_LOGIN or gh.FINDING_MARKER not in body:
            continue
        findings.append(
            {
                "path": item.get("path"),
                "line": item.get("line"),
                "start_line": item.get("start_line"),
                "title": _finding_title(body),
                "body": body[:2000],
                "outdated": item.get("position") is None,
            }
        )
    return findings


def _check_runs(repo: str, head_sha: str, token: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        status, parsed = gh.api("GET", f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100&page={page}", token)
        if status != 200 or not isinstance(parsed, dict):
            raise RuntimeError(f"GitHub check-runs for {head_sha}: {status}")
        batch = parsed.get("check_runs") or []
        runs.extend(
            {"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion")} for r in batch
        )
        if len(batch) < 100:
            return runs
        page += 1


def previous_review_sha(reviews: list[Any]) -> str | None:
    """Head SHA recorded by the newest successful omp review, if any."""
    for review in reversed(reviews):
        if (review.get("user") or {}).get("login") != gh.BOT_LOGIN:
            continue
        match = _REVIEW_TRAILER.search(review.get("body") or "")
        if match:
            return match.group(1)
    return None


def render_prompt(template: str, pr_url: str, pr_number: int, previous_sha: str | None, head_sha: str) -> str:
    lines = template.splitlines(keepends=True)
    while lines and (lines[0].startswith("<!--") or not lines[0].strip()):
        lines.pop(0)
    findings = "the earlier review's findings are in /work/context/prior-findings.json."
    if previous_sha == head_sha:
        rereview = f" This is a re-review of the same commit as the earlier review; {findings}"
    elif previous_sha:
        rereview = f" This is a re-review: focus on changes since commit {previous_sha}; {findings}"
    else:
        rereview = ""
    return (
        "".join(lines)
        .replace("{{PR_URL}}", pr_url)
        .replace("{{PR_NUMBER}}", str(pr_number))
        .replace("{{REREVIEW}}", rereview)
    )


def collect(repo: str, pr: int, base_sha: str, head_sha: str, out: Path, token: str) -> None:
    status, pull = gh.api("GET", f"/repos/{repo}/pulls/{pr}", token)
    if status != 200 or not isinstance(pull, dict):
        raise RuntimeError(f"GitHub GET /repos/{repo}/pulls/{pr}: {status}")
    review_comments = gh.paginate(f"/repos/{repo}/pulls/{pr}/comments?per_page=100", token)
    issue_comments = gh.paginate(f"/repos/{repo}/issues/{pr}/comments?per_page=100", token)
    reviews = gh.paginate(f"/repos/{repo}/pulls/{pr}/reviews?per_page=100", token)

    metadata = {
        "number": pr,
        "title": pull.get("title"),
        "body": pull.get("body"),
        "author": (pull.get("user") or {}).get("login"),
        "author_association": pull.get("author_association"),
        "html_url": pull.get("html_url"),
        "base_ref": (pull.get("base") or {}).get("ref"),
        "base_sha": base_sha,
        "head_ref": (pull.get("head") or {}).get("ref"),
        "head_sha": head_sha,
        "labels": [label.get("name") for label in pull.get("labels") or []],
    }
    prompt = render_prompt(
        TEMPLATE.read_text(encoding="utf-8"), str(pull.get("html_url")), pr, previous_review_sha(reviews), head_sha
    )

    out.mkdir(parents=True, exist_ok=True)
    documents: dict[str, Any] = {
        "pr.json": metadata,
        "discussion.json": _discussion(
            review_comments,
            issue_comments,
            reviews,
            _writers(review_comments + issue_comments + reviews, repo, token),
        ),
        "prior-findings.json": _prior_findings(review_comments),
        "checks.json": _check_runs(repo, head_sha, token),
    }
    for name, value in documents.items():
        (out / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    (out / "prompt.md").write_text(prompt, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN is required", file=sys.stderr)
        return 2
    collect(args.repo, args.pr, args.base_sha, args.head_sha, args.out, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
