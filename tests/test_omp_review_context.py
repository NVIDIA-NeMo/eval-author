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
