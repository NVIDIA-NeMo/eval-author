# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCALAR_CONSTANT = "CONTENT_DEDUP_MAX_SCALAR_COMPARISONS"
CHUNK_CONSTANT = "CONTENT_DEDUP_MAX_CHUNKS"
MODULE_PREFIX = "skillevaluator.deduplication.intra_skill."


@pytest.fixture
def wrapper(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("context_wrapper", ROOT / "tools/run_pinned_evaluator.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["run_pinned_evaluator.py", "context-optimization-check"])
    return module


@pytest.fixture
def upstream(wrapper, monkeypatch):
    origin = {"vcs_info": {"commit_id": wrapper.EVALUATOR_REVISION}}
    monkeypatch.setattr(wrapper, "distribution", lambda _: SimpleNamespace(read_text=lambda _: json.dumps(origin)))
    modules = {
        MODULE_PREFIX + name: SimpleNamespace(**{SCALAR_CONSTANT: 25_000_000, CHUNK_CONSTANT: 512})
        for name in ("intra_skill_validator", "semantic_clustering")
    }
    recognizer = Mock(return_value=False)
    modules["skillevaluator.spdx"] = SimpleNamespace(is_spdx_only_html_comment=recognizer)
    modules["skillevaluator.deduplication.utils.chunker"] = SimpleNamespace(is_spdx_only_html_comment=recognizer)
    modules["skillevaluator.cli"] = SimpleNamespace(cli=Mock())
    monkeypatch.setattr(wrapper, "import_module", modules.__getitem__)
    return modules


def test_real_chunker_preserves_content_and_lines(wrapper, upstream, monkeypatch):
    chunker = pytest.importorskip("skillevaluator.deduplication.utils.chunker")
    spdx = pytest.importorskip("skillevaluator.spdx")
    collector = pytest.importorskip("skillevaluator.deduplication.utils.skill_collector")
    monkeypatch.setattr(chunker, "is_spdx_only_html_comment", chunker.is_spdx_only_html_comment)
    upstream["skillevaluator.spdx"] = spdx
    upstream["skillevaluator.deduplication.utils.chunker"] = chunker
    files = collector.collect_files(ROOT / "skills/eval-author")
    before = [chunk for file in files for chunk in chunker.chunk_file(file)]
    from check_copyright_headers import _nvidia_header

    header = _nvidia_header("html").strip()
    expected = [chunk for chunk in before if chunk.text != header]
    assert len(expected) < len(before)
    wrapper.main()
    after = [chunk for file in files for chunk in chunker.chunk_file(file)]
    assert after == expected
    recognize = chunker.is_spdx_only_html_comment
    assert recognize(header)
    assert recognize(header.replace(" -->\n<!-- ", "\n"))
    for invalid in (
        header + "\nKeep this instruction.",
        header.replace("Apache-2.0", "MIT"),
        header.replace("NVIDIA", "OTHER"),
        header + "\n<!-- extra -->",
        header.replace("SPDX-License-Identifier:", "Keep this instruction. SPDX-License-Identifier:"),
    ):
        assert not recognize(invalid)


@pytest.mark.parametrize("module_name", ["intra_skill_validator", "semantic_clustering"])
@pytest.mark.parametrize(
    "constant,value",
    [(SCALAR_CONSTANT, value) for value in (24_000_000, 100_000_000, 25_000_000.0, None)]
    + [(CHUNK_CONSTANT, value) for value in (511, 640, 512.0, None)],
)
def test_upstream_contract_drift_fails_without_partial_mutation(wrapper, upstream, module_name, constant, value):
    target = upstream[MODULE_PREFIX + module_name]
    if value is None:
        delattr(target, constant)
    else:
        setattr(target, constant, value)
    before = {name: vars(module).copy() for name, module in upstream.items()}

    with pytest.raises(ValueError, match="upstream comparison contract changed"):
        wrapper.main()

    assert {name: vars(module) for name, module in upstream.items()} == before
    upstream["skillevaluator.cli"].cli.assert_not_called()


def test_both_allowances_are_applied_before_cli_dispatch(wrapper, upstream):
    before = {name: vars(module).copy() for name, module in upstream.items()}

    def cli():
        assert wrapper.CONTEXT_SCALAR_LIMIT == 450_000_000
        assert wrapper.CONTEXT_CHUNK_LIMIT == 640
        assert wrapper.CONTEXT_POLICY == "full-pairwise-640chunks-450m-v1"
        for name in ("intra_skill_validator", "semantic_clustering"):
            assert vars(upstream[MODULE_PREFIX + name]) == {
                **before[MODULE_PREFIX + name],
                SCALAR_CONSTANT: 450_000_000,
                CHUNK_CONSTANT: 640,
            }
        assert vars(upstream["skillevaluator.spdx"]) == before["skillevaluator.spdx"]

    upstream["skillevaluator.cli"].cli.side_effect = cli
    wrapper.main()
    upstream["skillevaluator.cli"].cli.assert_called_once_with()


def test_split_spdx_header_is_recognized_before_chunking(wrapper, upstream):
    from_header = (
        "<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->\n"
        "<!-- SPDX-License-Identifier: Apache-2.0 -->"
    )
    original = upstream["skillevaluator.spdx"].is_spdx_only_html_comment
    canonical = from_header.replace(" -->\n<!-- ", "\n")
    original.side_effect = lambda text: text == canonical
    wrapper.main()
    recognize = upstream["skillevaluator.deduplication.utils.chunker"].is_spdx_only_html_comment
    assert recognize(from_header)
    assert recognize(canonical)
    assert not recognize(from_header + "\nKeep this instruction.")
    assert not recognize(from_header.replace("Apache-2.0", "MIT"))
    assert not recognize(from_header.replace("NVIDIA", "OTHER"))
    assert not recognize(from_header + "\n<!-- extra -->")


def test_spdx_binding_drift_fails_without_partial_mutation(wrapper, upstream):
    upstream["skillevaluator.deduplication.utils.chunker"].is_spdx_only_html_comment = Mock()
    before = {name: vars(module).copy() for name, module in upstream.items()}
    with pytest.raises(ValueError, match="upstream SPDX contract changed"):
        wrapper.main()
    assert {name: vars(module) for name, module in upstream.items()} == before


@pytest.mark.parametrize("argv", [["wrapper"], ["wrapper", "validate"], ["wrapper", "similarity-check"]])
def test_non_context_commands_fail_before_imports(wrapper, upstream, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    imports = Mock(side_effect=AssertionError("unexpected upstream import"))
    monkeypatch.setattr(wrapper, "import_module", imports)
    with pytest.raises(ValueError, match="restricted to context checks"):
        wrapper.main()
    imports.assert_not_called()


@pytest.mark.parametrize("origin", [{}, {"vcs_info": {"commit_id": "unreviewed"}}])
def test_unpinned_origin_fails_before_imports(wrapper, monkeypatch, origin):
    monkeypatch.setattr(wrapper, "distribution", lambda _: SimpleNamespace(read_text=lambda _: json.dumps(origin)))
    imports = Mock(side_effect=AssertionError("unexpected upstream import"))
    monkeypatch.setattr(wrapper, "import_module", imports)
    with pytest.raises(ValueError, match="requires the pinned evaluator commit"):
        wrapper.main()
    imports.assert_not_called()


@pytest.fixture
def native(wrapper, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, Mock(side_effect=AssertionError("unexpected network request")))
    pytest.importorskip("skillevaluator", reason="requires the isolated pinned SkillEvaluator runtime")
    validator = wrapper.import_module(MODULE_PREFIX + "intra_skill_validator")
    clustering = wrapper.import_module(MODULE_PREFIX + "semantic_clustering")
    cli = wrapper.import_module("skillevaluator.cli")
    chunker = wrapper.import_module("skillevaluator.deduplication.utils.chunker")
    monkeypatch.setattr(chunker, "is_spdx_only_html_comment", chunker.is_spdx_only_html_comment)
    # Record the original values for teardown; main must perform the real override.
    for module in (validator, clustering):
        for constant in (SCALAR_CONSTANT, CHUNK_CONSTANT):
            monkeypatch.setattr(module, constant, getattr(module, constant))
    callback = Mock()
    monkeypatch.setattr(cli, "cli", callback)
    wrapper.main()  # Uses real installed direct_url.json, imports, and revision guard.
    callback.assert_called_once_with()
    monkeypatch.setattr(validator, "LLMClient", Mock(side_effect=AssertionError("unexpected LLM request")))
    return validator, clustering


@pytest.mark.parametrize(
    "count,dimension", [(200, 1536), (417, 2048), (512, 2048), (640, 2048), (640, 2200), (640, 2201)]
)
def test_native_pairwise_budget_reaches_clustering_and_stays_bounded(native, tmp_path, monkeypatch, count, dimension):
    validator, clustering = native

    scalar_work = count * (count - 1) // 2 * dimension
    assert scalar_work > 25_000_000

    # Real chunking and vector validation; bounded one-hot provider controls need no models.
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "\n\n".join(f"## Section {i}\n" + f"Content number {i}. " * 8 for i in range(count))
    )
    vectors = [[float(i == j) for j in range(dimension)] for i in range(count)]
    next_vector = iter(vectors)
    embed = Mock(side_effect=lambda texts: [next(next_vector) for _ in texts])
    monkeypatch.setattr(validator, "EmbeddingClient", Mock(return_value=SimpleNamespace(embed=embed)))

    result = validator.IntraSkillValidator().validate(skill)
    chunks = [chunk for file in validator.collect_files(skill) for chunk in validator.chunk_file(file)]
    assert len(chunks) == count
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector
    if scalar_work > 450_000_000:
        finding = next(f for f in result.findings if f.check_name == "scalar_comparison_limit")
        assert finding.metadata == {
            "pair_count": count * (count - 1) // 2,
            "vector_dimension": dimension,
            "scalar_work": scalar_work,
            "limit": 450_000_000,
        }
        assert embed.call_count == 1
        with pytest.raises(ValueError, match="Content scalar comparison work exceeds"):
            clustering.build_clusters(chunks)
    else:
        assert result.passed
        assert not result.incomplete_scans
        assert any(detail.check_name == "context_dedup" for detail in result.success_details)
        assert sum(len(call.args[0]) for call in embed.call_args_list) == count
        # Force a known pair to prove the real clustering consumer executes comparisons.
        chunks[1].embedding = chunks[0].embedding
        clusters = clustering.build_clusters(chunks)
        assert len(clusters) == 1
        assert clusters[0].members == chunks[:2]
        assert clusters[0].max_similarity == 1.0


@pytest.mark.parametrize(
    "bad_vector", [[float("nan"), 1.0], [float("inf"), 1.0], [0.0, 0.0], [True, 1.0], [1.0], [], [1.0] * 65537]
)
def test_native_vector_validation_is_not_bypassed(native, tmp_path, monkeypatch, bad_vector):
    validator, clustering = native
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("## First\n" + "First content. " * 8 + "\n\n## Second\n" + "Second content. " * 8)
    vectors = [[1.0, 0.0], bad_vector]
    embed = Mock(return_value=vectors)
    monkeypatch.setattr(validator, "EmbeddingClient", Mock(return_value=SimpleNamespace(embed=embed)))
    result = validator.IntraSkillValidator().validate(skill)
    assert "embedding-provider" in result.incomplete_scans
    chunks = [chunk for file in validator.collect_files(skill) for chunk in validator.chunk_file(file)]
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk.embedding = vector
    if bad_vector == [0.0, 0.0]:
        # Native clustering allows zero vectors, but provider admission rejects them.
        assert clustering.build_clusters(chunks) == []
    else:
        with pytest.raises(validator.SimilarityConfigError):
            clustering.build_clusters(chunks)


def test_native_chunk_limit_is_not_bypassed(native, tmp_path, monkeypatch):
    validator, clustering = native
    skill = tmp_path / "skill"
    skill.mkdir()
    count = validator.CONTENT_DEDUP_MAX_CHUNKS + 1
    assert count == 641
    (skill / "SKILL.md").write_text("\n\n".join(f"## Section {i}\n" + "Content. " * 12 for i in range(count)))
    embed = Mock(side_effect=AssertionError("chunk limit must precede provider construction"))
    monkeypatch.setattr(validator, "EmbeddingClient", embed)
    result = validator.IntraSkillValidator().validate(skill)
    assert any(f.check_name == "chunk_count_limit" for f in result.findings)
    embed.assert_not_called()
    chunks = [chunk for file in validator.collect_files(skill) for chunk in validator.chunk_file(file)]
    assert len(chunks) == count
    with pytest.raises(ValueError, match="Content chunk count exceeds"):
        clustering.build_clusters(chunks)


def test_native_current_tree_admits_every_chunk_and_compares_every_pair(native, monkeypatch):
    validator, clustering = native
    skill = ROOT / "skills/eval-author"
    chunks = [chunk for file in validator.collect_files(skill) for chunk in validator.chunk_file(file)]
    count = len(chunks)
    assert 2 <= count <= 640
    assert count * (count - 1) // 2 * 2048 <= 450_000_000
    vectors = iter([[float(i == j) for j in range(2048)] for i in range(count)])
    embed = Mock(side_effect=lambda texts: [next(vectors) for _ in texts])
    monkeypatch.setattr(validator, "EmbeddingClient", Mock(return_value=SimpleNamespace(embed=embed)))
    compare = clustering.unit_vector_similarity
    comparisons = 0

    def similarity(left, right):
        nonlocal comparisons
        comparisons += 1
        return compare(left, right)

    monkeypatch.setattr(clustering, "unit_vector_similarity", similarity)
    result = validator.IntraSkillValidator().validate(skill)
    assert result.passed
    assert not result.incomplete_scans
    assert any(detail.check_name == "context_dedup" for detail in result.success_details)
    assert [text for call in embed.call_args_list for text in call.args[0]] == [chunk.text for chunk in chunks]
    assert comparisons == count * (count - 1) // 2
