# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep caller-verified artifact anchors separate from literal path characters."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def formatting(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"))
    return importlib.import_module("conversation_formatting")


def test_local_rerun_link_preserves_both_filename_hash_and_heading(formatting):
    assert formatting.link("/workspace/suite #1/README.md", "saved run instructions", fragment="rerun-the-suite") == (
        "[saved run instructions](</workspace/suite%20%231/README.md#rerun-the-suite>)"
    )
    assert formatting.link("/workspace/report.html", "Tasks view", fragment="cases") == (
        "[Tasks view](</workspace/report.html#cases>)"
    )


def test_url_and_label_escape_without_reading_artifacts(formatting):
    assert formatting.link("https://example.com/report", "[Tasks]", fragment="cases") == (
        "[\\[Tasks\\]](<https://example.com/report#cases>)"
    )
    assert formatting.link("https://example.com/report#cases", "Tasks") == (
        "[Tasks](<https://example.com/report#cases>)"
    )


@pytest.mark.parametrize("fragment", ["", "#rerun", "two words", "bad\nanchor", "<script>", False])
def test_reject_ambiguous_or_structural_fragments(formatting, fragment):
    with pytest.raises(ValueError):
        formatting.link("/workspace/README.md", "instructions", fragment=fragment)


def test_reject_two_competing_fragments(formatting):
    with pytest.raises(ValueError, match="separately"):
        formatting.link("https://example.com/report#old", "Tasks", fragment="cases")
