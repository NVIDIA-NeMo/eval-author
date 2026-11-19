# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    sys.path.insert(0, str(ROOT / "tools"))
    import rubric_demo_common

    return rubric_demo_common


def test_shared_artifact_preserves_selected_digest_and_provenance(tmp_path: Path) -> None:
    common = helper()
    (tmp_path / "input.json").write_text("{}\n")
    actual = hashlib.sha256(b"{}\n").hexdigest()
    record = common.artifact_record(
        tmp_path,
        "input",
        "input.json",
        "task",
        selected_digest="0" * 64,
        revision=None,
        source="local-demo-fixture",
    )
    assert record == {
        "id": "input",
        "path": "input.json",
        "role": "task",
        "digest": "0" * 64,
        "revision": None,
        "access": "available",
        "source": "local-demo-fixture",
        "selection_origin": "explicit",
        "evidence": [{"path": "input.json", "digest": actual}],
    }


@pytest.mark.parametrize(
    "module,sorted_keys,exclusive",
    [
        ("rubric_demo_common", False, False),
        ("run_task_contract_demo", False, False),
        ("run_criterion_scoring_demo", True, False),
        ("run_rubric_provenance_demo", True, True),
        ("run_verification_integrity_demo", True, True),
    ],
)
def test_demo_json_writer_byte_contract(tmp_path: Path, module: str, sorted_keys: bool, exclusive: bool) -> None:
    helper()
    write = runpy.run_path(str(ROOT / "tools" / f"{module}.py"))["write_json"]
    path = tmp_path / "value.json"
    value = {"z": {"y": "é", "a": None}, "a": [True, 2]}
    expected = (
        b'{\n  "a": [\n    true,\n    2\n  ],\n  "z": {\n    "a": null,\n    "y": "\\u00e9"\n  }\n}\n'
        if sorted_keys
        else b'{\n  "z": {\n    "y": "\\u00e9",\n    "a": null\n  },\n  "a": [\n    true,\n    2\n  ]\n}\n'
    )
    write(path, value)
    assert path.read_bytes() == expected
    if exclusive:
        with pytest.raises(FileExistsError):
            write(path, {})
        assert path.read_bytes() == expected
    else:
        write(path, {})
        assert path.read_bytes() == b"{}\n"
    for index, nonfinite in enumerate((float("nan"), float("inf"), float("-inf"))):
        with pytest.raises(ValueError):
            write(tmp_path / f"nonfinite-{index}.json", {"value": nonfinite})
    with pytest.raises(FileNotFoundError):
        write(tmp_path / "absent" / "value.json", value)


@pytest.mark.parametrize("mutation", ["baseline", "candidate-replaced", "reconstruction-recovered"])
def test_provenance_selection_metadata_contract(tmp_path: Path, mutation: str) -> None:
    helper()
    demo = runpy.run_path(str(ROOT / "tools/run_rubric_provenance_demo.py"))
    selection = demo["build_selection"](tmp_path, mutation)
    records = {record["id"]: record for record in selection["artifacts"]}
    expected_paths = {
        "generator-config": ("generator-config-r4.json", "configuration", "r4"),
        "candidate-kept": (
            "candidate-kept-v2.json" if mutation == "candidate-replaced" else "candidate-kept-v1.json",
            "candidate",
            "v2" if mutation == "candidate-replaced" else "v1",
        ),
    }
    if mutation == "reconstruction-recovered":
        expected_paths["initial-file"] = (
            ".eval-author/.working/trace-environments/initial/private/ground-truth/file.json",
            "input",
            "v1",
        )
    for ident, (relative, role, revision) in expected_paths.items():
        digest = hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest()
        assert records[ident] == {
            "id": ident,
            "role": role,
            "path": relative,
            "digest": digest,
            "revision": revision,
            "access": "available",
            "selection_origin": "explicit",
            "source": "handcrafted:rubric-provenance-selection",
            "evidence": [{"path": relative, "digest": digest}],
        }
    path = tmp_path / "selection.json"
    demo["write_json"](path, selection)
    assert path.read_bytes() == (json.dumps(selection, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def test_integrity_selection_metadata_contract(tmp_path: Path) -> None:
    helper()
    demo = runpy.run_path(str(ROOT / "tools/run_verification_integrity_demo.py"))
    directory = tmp_path / "nested"
    directory.mkdir()
    paths = [directory / name for name in ("manifest.json", "receipt.json", "result.json")]
    expected = []
    for path, role in zip(paths, ("manifest", "receipt", "result"), strict=True):
        data = (role + "\n").encode()
        path.write_bytes(data)
        relative = "nested/" + path.name
        digest = hashlib.sha256(data).hexdigest()
        expected.append(
            {
                "id": role,
                "role": role,
                "path": relative,
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "synthetic native fixture; real task_evidence producer",
                "evidence": [{"path": relative, "digest": digest}],
            }
        )
    selected = demo["selection"](tmp_path, paths[0], [(paths[1], paths[2])])
    document = {
        "artifacts": expected,
        "native": {"manifest_ids": ["manifest"], "receipt_ids": ["receipt"], "result_ids": ["result"], "trace_ids": []},
    }
    assert selected == tmp_path / "selection.json"
    assert selected.read_bytes() == (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    with pytest.raises(FileExistsError):
        demo["selection"](tmp_path, paths[0], [(paths[1], paths[2])])


@pytest.mark.parametrize(
    "module,utility",
    [
        ("run_task_contract_demo", "write_json"),
        ("run_criterion_scoring_demo", "write_json"),
        ("run_rubric_provenance_demo", "artifact_record"),
        ("run_verification_integrity_demo", "artifact_record"),
    ],
)
def test_demo_uses_owning_utility(tmp_path: Path, monkeypatch, module: str, utility: str) -> None:
    common = helper()
    original = getattr(common, utility)
    calls = []

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(common, utility, observe)
    demo = runpy.run_path(str(ROOT / "tools" / f"{module}.py"))
    if utility == "write_json":
        demo["write_json"](tmp_path / "value.json", {"z": 1, "a": 2})
    elif module == "run_rubric_provenance_demo":
        demo["build_selection"](tmp_path, "baseline")
    else:
        manifest = tmp_path / "manifest.json"
        manifest.write_bytes(b"{}\n")
        demo["selection"](tmp_path, manifest, [])
    assert calls, f"{module} must reuse rubric_demo_common.{utility}"


@pytest.mark.parametrize("record_invocation", [False, True])
def test_shared_measure_preserves_log_and_replay_modes(tmp_path: Path, record_invocation: bool) -> None:
    common = helper()
    (tmp_path / "selection.json").write_text('{"artifacts": []}\n')
    report, code = common.run_measure(tmp_path, prefix="baseline", record_invocation=record_invocation)
    assert code == 1
    assert report["schema"] == "rubric-measurements-v3"
    retained = tmp_path / "baseline-report.json"
    assert json.loads(retained.read_text()) == report
    before = retained.read_bytes()
    replay, replay_code = common.run_measure(
        tmp_path,
        prefix="baseline",
        verify=True,
        record_invocation=record_invocation,
    )
    assert (replay, replay_code) == (report, code)
    assert retained.read_bytes() == before
    for log in ("baseline", "baseline-verify"):
        assert (tmp_path / f"{log}.stdout.json").is_file()
        assert (tmp_path / f"{log}.stderr.txt").is_file()
        assert (tmp_path / f"{log}-invocation.json").exists() is record_invocation


def test_shared_measure_rejects_invalid_cli_output_with_retained_logs(tmp_path: Path) -> None:
    common = helper()
    (tmp_path / "selection.json").write_text('{"unsupported": true}\n')
    with pytest.raises(ValueError, match="caller-specific error"):
        common.run_measure(tmp_path, record_invocation=True, error_message="caller-specific error")
    assert not (tmp_path / "measure-report.json").exists()
    assert json.loads((tmp_path / "measure-invocation.json").read_text())["returncode"] == 2
