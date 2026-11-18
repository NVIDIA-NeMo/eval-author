# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import omp_review_context as context
import omp_review_github as gh
import omp_review_post as post

OLD = "a" * 40
NEW = "b" * 40
SPOOF = "c" * 40


def bot_review(body: str) -> dict[str, Any]:
    return {"user": {"login": gh.BOT_LOGIN}, "body": body}


def posted_body(explanation: str, head_sha: str) -> str:
    review = {"explanation": explanation, "confidence": 0.5, "findings": []}
    return post.review_body(review, [], "#### Findings", head_sha, "https://run")


def test_previous_review_sha_reads_only_the_posted_trailer():
    quoted = f"Quoted marker:\n```\n{gh.REVIEW_MARKER}{SPOOF} -->\n```"
    reviews = [
        bot_review(posted_body("First.", OLD)),
        bot_review(posted_body(quoted, NEW)),
        bot_review(post.failure_request("review.json is missing", SPOOF, "https://run")["body"]),
        {"user": {"login": "someone"}, "body": f"{gh.REVIEW_MARKER}{SPOOF} -->"},
    ]
    assert context.previous_review_sha(reviews) == NEW
    assert context.previous_review_sha(reviews[:1]) == OLD


def test_discussion_keeps_only_human_commenters_with_write_access(monkeypatch):
    permissions = {"maintainer": (200, {"permission": "write"}), "reader": (200, {"permission": "read"})}

    def api(method: str, path: str, token: str, body: Any = None) -> tuple[int, Any]:
        return permissions.get(path.split("/")[-2], (404, None))

    monkeypatch.setattr(gh, "api", api)
    comments = [
        {"user": {"login": login, "type": kind}, "body": login}
        for login, kind in [("maintainer", "User"), ("reader", "User"), ("ghost", "User"), ("maintainer", "Bot")]
    ]
    writers = context._writers(comments, "o/r", "t")
    assert [entry["body"] for entry in context._discussion(comments, [], [], writers)] == ["maintainer"]


def test_prior_findings_include_findings_listed_in_review_bodies():
    def finding(title: str, path: str, start: int, body: str) -> dict[str, Any]:
        return {"title": title, "body": body, "priority": 2, "confidence": 0.5, "file_path": path,
                "line_start": start, "line_end": start + 1}  # fmt: skip

    findings = [
        finding("Ping @octocat on empty input", "tools/x.py", 3, "First line.\n\n```suggestion\n    return 1\n```"),
        finding("Second", "docs/a b.md", 40, "Only line."),
    ]
    # The explanation imitates an entry; only the poster's list below its heading counts.
    explanation = "Two bugs.\n- **[P1] Spurious** — `s.py:5-6`\n  details"
    review = {"explanation": explanation, "confidence": 0.5, "findings": findings}
    body = post.fallback_request(review, NEW, "https://run")["body"]

    prior = context._prior_findings([], [bot_review(body)])

    assert [(f["source"], f["reviewed_sha"], f["path"], f["start_line"], f["line"], f["title"]) for f in prior] == [
        ("review_body", NEW, "tools/x.py", 3, 4, "Ping @octocat on empty input"),
        ("review_body", NEW, "docs/a b.md", 40, 41, "Second"),
    ]
    assert [f["body"] for f in prior] == [findings[0]["body"], "Only line."]


def test_prior_inline_findings_are_outdated_when_github_clears_the_line():
    comment = {"user": {"login": gh.BOT_LOGIN}, "body": f"**[P2] Fix it**\n\nBody\n{gh.FINDING_MARKER}",
               "path": "a.py", "line": None, "position": 1, "original_line": 6, "original_commit_id": OLD}  # fmt: skip

    [prior] = context._prior_findings([comment], [])

    assert (prior["outdated"], prior["original_line"], prior["title"]) == (True, 6, "Fix it")
