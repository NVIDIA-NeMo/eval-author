# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import omp_review_github as gh
import omp_review_post as post

HEAD = "b" * 40
BASE = "a" * 40
PATCH = (
    "@@ -1,3 +1,4 @@\n ctx1\n-old\n+new2\n+new3\n ctx4\n@@ -20,2 +21,2 @@\n ctx21\n+new22\n\\ No newline at end of file"
)


def finding(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "title": "Handle empty input",
        "body": "Crashes on empty input.",
        "priority": 2,
        "confidence": 0.8,
        "file_path": "tools/x.py",
        "line_start": 2,
        "line_end": 3,
    }
    value.update(overrides)
    return value


def review(findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "head_sha": HEAD,
        "overall_correctness": "incorrect",
        "explanation": "One bug.",
        "confidence": 0.7,
        "findings": [finding()] if findings is None else findings,
    }


def write_review(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "review.json"
    path.write_text(json.dumps(document))
    return path


def test_hunk_lines_numbers_new_side_across_hunks():
    assert post.hunk_lines(PATCH) == [{1, 2, 3, 4}, {21, 22}]


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(1, 4, True), (21, 22, True), (4, 21, False), (22, 23, False)],
)
def test_placeable_requires_one_hunk(start: int, end: int, expected: bool):
    files = {"tools/x.py": post.hunk_lines(PATCH)}
    assert post.placeable(finding(line_start=start, line_end=end), files) is expected


def test_file_without_patch_is_not_placeable():
    assert not post.placeable(finding(), {"tools/x.py": post.hunk_lines("")})
    assert not post.placeable(finding(), {})


def test_load_review_accepts_minimal_document(tmp_path: Path):
    assert post.load_review(write_review(tmp_path, review([])), HEAD)["findings"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d.update(head_sha="c" * 40),
        lambda d: d["findings"][0].update(line_start=1, line_end=11),
        lambda d: d["findings"][0].update(file_path="tools/../secrets"),
        lambda d: d["findings"][0].update(priority=True),
        lambda d: d.update(findings=[finding()] * 51),
    ],
    ids=["extra-key", "head-mismatch", "span-too-long", "dotdot-path", "bool-priority", "too-many"],
)
def test_load_review_rejects_invalid_documents(tmp_path: Path, mutate):
    document = copy.deepcopy(review())
    mutate(document)
    with pytest.raises(ValueError):
        post.load_review(write_review(tmp_path, document), HEAD)


def test_verdict_counts_priority_two_as_incorrect():
    assert post.verdict([finding(priority=2)]) == "incorrect"
    assert post.verdict([finding(priority=3)]) == "correct"


@pytest.mark.parametrize(
    "text",
    ["@user", "&#64;user", "&commat;user", "![x](y)", '<img src="y">', "<!-- omp-review-finding -->"],
)
def test_sanitize_neutralizes_prose(text: str):
    sanitized = post.sanitize(f"See {text} here.")
    assert text not in sanitized
    assert sanitized.replace("\u200b", "") == f"See {text} here."


def test_sanitize_keeps_suggestion_blocks_applicable():
    suggestion = "```suggestion\n@pytest.fixture\n<!-- SPDX-License-Identifier: Apache-2.0 -->\n```"
    assert post.sanitize(f"Use a fixture @user.\n{suggestion}") == f"Use a fixture @\u200buser.\n{suggestion}"


def test_sanitize_neutralizes_fences_that_github_may_end_early():
    # The indented fence ends with its list item on GitHub, so "@user" renders as prose there.
    assert "@user" not in post.sanitize("- item\n  ```\n@user\n  ```")


def test_sanitize_closes_unclosed_fence():
    # Otherwise markdown that follows could close it and expose text treated here as code.
    assert post.sanitize("~~~~\n@user") == "~~~~\n@user\n~~~~"


def args_for(path: Path) -> list[str]:
    return [
        "--repo", "o/r", "--pr", "7", "--base-sha", BASE, "--head-sha", HEAD,
        "--review", str(path), "--run-url", "https://run",
    ]  # fmt: skip


def fake_api(responses: list[int], calls: list[tuple[str, str, Any]]):
    def api(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        calls.append((method, path, body))
        if method == "GET":
            return 200, {"files": [{"filename": "tools/x.py", "patch": PATCH}]}
        if method == "POST":
            return responses.pop(0), {}
        return 404, None

    return api


def test_unplaceable_inline_comments_fall_back_to_body(tmp_path: Path, monkeypatch):
    findings = [finding(), finding(title="Second", line_start=21, line_end=22)]
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(gh, "api", fake_api([422, 200], calls))
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    assert post.main(args_for(write_review(tmp_path, review(findings)))) == 0

    posts = [body for method, _, body in calls if method == "POST"]
    assert len(posts[0]["comments"]) == 2
    assert posts[1]["comments"] == []
    assert all(f"[P2] {f['title']}" in posts[1]["body"] for f in findings)
    assert ("DELETE", "/repos/o/r/issues/7/labels/omp-review", None) in calls


def test_missing_review_posts_failure_and_removes_label(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(gh, "api", fake_api([200], calls))
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    assert post.main(args_for(tmp_path / "absent.json")) == 1

    posts = [body for method, _, body in calls if method == "POST"]
    assert len(posts) == 1 and gh.FAILURE_MARKER in posts[0]["body"]
    assert ("DELETE", "/repos/o/r/issues/7/labels/omp-review", None) in calls
