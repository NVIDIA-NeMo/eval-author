# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/eval-author-rubric"


def load(name):
    path = SKILL / (name + ".py")
    assert path.exists(), "missing T3 executable controls"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("family", ("refund", "interval-reservation", "dependency-release"))
def test_cue_flip_reaches_policy_candidate_without_changing_correctness_authority(family):
    controls = load("current_rubric_finish_controls")
    native = load("current_rubric_finish_native")
    raw = native._development_inputs()[(family, "template")]
    pair = controls.cue_pair(family, raw, "cue-only")
    traces = [controls.run_control(arm, "first-template") for arm in pair]
    assert traces[0]["candidate_sha256"] != traces[1]["candidate_sha256"]
    assert all(t["partition"] == "exposed_development" for t in traces)
    assert all(t["execution"] == "planned_not_submitted" for t in traces)
    assert all(t["actions"] for t in traces)
    assert all(t["reads"][0]["scope"] == "restricted_policy_projection" for t in traces)
    for arm in pair:
        assert json.loads(arm["authority_input"]) == json.loads(raw)
        assert arm["scorer_sha256"] == pair[0]["scorer_sha256"]
        view = json.loads(arm["policy_input"])
        if family == "refund":
            assert "merchandise_cents" not in view["sources"]["order"]
            assert all("amount_cents" not in r for r in view["sources"]["initial_state"]["refunds"])
        else:
            assert view["occupied" if family == "interval-reservation" else "states"] is None
            assert all(set(o) == {"id"} for o in view["options"])


@pytest.mark.parametrize("family", ("refund", "interval-reservation", "dependency-release"))
@pytest.mark.parametrize("cue_class", ("nuisance-swap", "misleading-cue", "style-only"))
def test_evidence_robustness_and_true_style_only(family, cue_class):
    c = load("current_rubric_finish_controls")
    n = load("current_rubric_finish_native")
    raw = n._development_inputs()[(family, "template")]
    arms = c.cue_pair(family, raw, cue_class)
    traces = [c.run_control(a, "evidence") for a in arms]
    assert traces[0]["candidate"] == traces[1]["candidate"]
    scorer = n._load(c.scorer_path(family))
    assert all(scorer.evaluate(json.loads(raw), t["candidate"])["accepted"] for t in traces)
    if cue_class == "style-only":
        assert arms[0]["policy_input"] != arms[1]["policy_input"]
        assert json.loads(arms[0]["policy_input"]) == json.loads(arms[1]["policy_input"])
        assert (
            c.run_control(arms[0], "first-template")["candidate"]
            == c.run_control(arms[1], "first-template")["candidate"]
        )


def test_prospective_selection_freezes_before_feedback_and_custody_reads(tmp_path):
    c = load("current_rubric_finish_controls")
    n = load("current_rubric_finish_native")
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    sealed = tmp_path / "development-not-holdout.json"
    sealed.write_bytes(b'{"partition":"exposed_development","cases":[]}')
    gateway = c.CustodyGateway(ledger, sealed, c.sha(sealed.read_bytes()))
    with pytest.raises(PermissionError, match="selection_not_frozen"):
        gateway.open_once()
    ledger.preregister()
    with pytest.raises(PermissionError, match="selection_not_frozen"):
        gateway.open_once()
    selected = ledger.measure_and_select()
    assert selected["selection"] == n.select_policy(n._development())
    assert len(selected["development"]) == 18
    assert gateway.open_once() == sealed.read_bytes()
    with pytest.raises(PermissionError, match="already_opened"):
        c.CustodyGateway(
            c.SelectionLedger(tmp_path / "selection.sqlite"), sealed, c.sha(sealed.read_bytes())
        ).open_once()
    with pytest.raises(PermissionError, match="already_frozen"):
        ledger.measure_and_select()
    with pytest.raises(PermissionError, match="already_registered"):
        ledger.preregister()
    assert [e["outcome"] for e in ledger.access_log()] == [
        "selection_not_frozen",
        "selection_not_frozen",
        "opened",
        "already_opened",
    ]


def test_gateway_rejects_changed_sources_before_read(tmp_path, monkeypatch):
    c = load("current_rubric_finish_controls")
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    ledger.preregister()
    ledger.measure_and_select()
    sealed = tmp_path / "development-not-holdout.json"
    sealed.write_bytes(b"{}")
    gateway = c.CustodyGateway(ledger, sealed, c.sha(b"{}"))
    monkeypatch.setattr(c, "source_bindings", lambda: {"changed": True})
    with pytest.raises(PermissionError, match="source_changed"):
        gateway.open_once()
    assert ledger.access_log()[-1]["outcome"] == "source_changed"
    assert not ledger.opening_consumed()


def test_actual_policy_submission_and_native_command(tmp_path):
    import shlex
    import subprocess

    c = load("current_rubric_finish_controls")
    n = load("current_rubric_finish_native")
    arm = c.cue_pair("interval-reservation", n._development_inputs()[("interval-reservation", "template")], "cue-only")[
        1
    ]
    path = tmp_path / "policy.json"
    path.write_bytes(arm["policy_input"])
    candidate = tmp_path / "candidate.json"
    trace = tmp_path / "trace.json"
    argv = c.policy_argv(
        "first-template",
        "interval-reservation",
        path,
        trace,
        candidate_path=candidate,
        interpreter=sys.executable,
        controls_path=SKILL / "current_rubric_finish_controls.py",
    )
    completed = subprocess.run(argv, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(trace.read_bytes())
    assert actual["execution"] == "submitted"
    assert json.loads(candidate.read_bytes()) == c.run_control(arm, "first-template")["candidate"]
    assert shlex.split(c.native_policy_command("first-template", "interval-reservation"))[0:4] == [
        "/usr/local/bin/python",
        "-I",
        "-S",
        "-B",
    ]
    assert argv == [
        sys.executable,
        "-I",
        "-S",
        "-B",
        str(SKILL / "current_rubric_finish_controls.py"),
        "policy",
        "first-template",
        "interval-reservation",
        "--input",
        str(path),
        "--trace",
        str(trace),
        "--candidate",
        str(candidate),
    ]


def test_custody_pre_freeze_never_opens_file_and_consumes_failed_open(tmp_path, monkeypatch):
    c = load("current_rubric_finish_controls")
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    sealed = tmp_path / "development-not-holdout.json"
    gateway = c.CustodyGateway(ledger, sealed, c.sha(b"{}"))
    reads = []
    original = c.read_regular

    def spy(path, limit=1048576):
        if path == sealed:
            reads.append(path)
        return original(path, limit)

    monkeypatch.setattr(c, "read_regular", spy)
    with pytest.raises(PermissionError):
        gateway.open_once()
    assert reads == []
    ledger.preregister()
    ledger.measure_and_select()
    with pytest.raises(FileNotFoundError):
        gateway.open_once()
    assert reads == [sealed]
    sealed.write_bytes(b"{}")
    with pytest.raises(PermissionError, match="already_opened"):
        gateway.open_once()
    assert reads == [sealed]


def test_finite_manifest_missing_cases_and_no_launch(tmp_path):
    c = load("current_rubric_finish_controls")
    manifest = c.manifest_template()
    assert manifest["authorization"] is None
    assert manifest["budgets"]["native_remaining"] == 0
    assert manifest["budgets"]["models"] == 0
    assert len(manifest["slots"]) == len({s["id"] for s in manifest["slots"]}) == 81
    assert all(s["independent_case"] is None for s in manifest["slots"])
    assert all(s["status"] == "missing_independent_case" for s in manifest["slots"])
    assert "expected" not in json.dumps(manifest)
    with pytest.raises(PermissionError, match="missing_independent"):
        c.admit_manifest(manifest)


def test_custody_seal_cannot_be_substituted(tmp_path):
    c = load("current_rubric_finish_controls")
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    first = tmp_path / "development-one.json"
    second = tmp_path / "development-two.json"
    first.write_bytes(b"{}")
    second.write_bytes(b"[]")
    c.CustodyGateway(ledger, first, c.sha(b"{}"))
    ledger.preregister()
    ledger.measure_and_select()
    with pytest.raises(PermissionError, match="custody_binding_changed"):
        c.CustodyGateway(ledger, second, c.sha(b"[]"))


def test_concurrent_openings_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    c = load("current_rubric_finish_controls")
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    sealed = tmp_path / "development-not-holdout.json"
    sealed.write_bytes(b"{}")
    gateway = c.CustodyGateway(ledger, sealed, c.sha(b"{}"))
    ledger.preregister()
    ledger.measure_and_select()

    def attempt():
        try:
            return gateway.open_once()
        except PermissionError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results.count(b"{}") == 1
    assert results.count("already_opened") == 1


@pytest.mark.parametrize("family", ("refund", "interval-reservation", "dependency-release"))
def test_identity_renaming_and_order_transforms_are_semantic(family):
    c = load("current_rubric_finish_controls")
    n = load("current_rubric_finish_native")
    original = json.loads(n._development_inputs()[(family, "template")])
    changed, mapping = c.rename_identities(family, original)
    assert changed != original
    raw = json.dumps(changed).encode()
    policy = load("current_rubric_finish_policies")
    candidate = policy.run_policy("evidence", family, raw)["candidate"]
    assert n._load(c.scorer_path(family)).evaluate(changed, candidate)["accepted"]
    if family == "refund":
        assert changed["request"]["order_id"] == changed["sources"]["order"]["id"]
        assert changed["sources"]["initial_state"]["refunds"][0]["order_id"] == changed["request"]["order_id"]
    else:
        original_choice = policy.run_policy("lexical", family, json.dumps(original).encode())["candidate"]["choice"]
        changed_choice = policy.run_policy("lexical", family, raw)["candidate"]["choice"]
        assert mapping[original_choice] != changed_choice


def test_physical_dedup_preserves_full_identity_and_roles():
    c = load("current_rubric_finish_controls")
    keys = c.PHYSICAL_IDENTITY_FIELDS
    one = {key: c.sha(key.encode()) for key in keys}
    rows = [
        {**one, "id": "valid", "role": "viability"},
        {**one, "id": "positive", "role": "cue-positive"},
        {**one, "policy_sha256": c.sha(b"different"), "id": "different", "role": "attack"},
    ]
    result = c.deduplicate_physical(rows)
    assert len(result) == 2
    assert sorted(len(r["members"]) for r in result) == [1, 2]
    with pytest.raises(ValueError, match="incomplete_physical_identity"):
        c.deduplicate_physical([{"id": "empty"}])


def test_refund_policy_reaches_actual_action_service(tmp_path):
    import socket
    import subprocess
    import tempfile
    import time

    c = load("current_rubric_finish_controls")
    n = load("current_rubric_finish_native")
    raw = n._development_inputs()[("refund", "template")]
    with tempfile.TemporaryDirectory(dir=Path("/tmp").resolve(), prefix="t3-") as short:
        short = Path(short)
        authority = short / "contract.json"
        authority.write_bytes(raw)
        record = short / "record.json"
        sock = short / "s"
        env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(short)}
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(SKILL / "examples/refund/base/environment/server.py"),
                "--contract",
                str(authority),
                "--record",
                str(record),
                "--socket",
                str(sock),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while not sock.exists() and time.monotonic() < deadline and server.poll() is None:
                time.sleep(0.01)
            assert sock.exists(), server.communicate(timeout=1)
            arm = c.cue_pair("refund", raw, "cue-only")[0]
            source = short / "policy.json"
            source.write_bytes(arm["policy_input"])
            trace = c.execute_projected("first-template", "refund", source, short / "trace.json", socket_path=sock)
            assert trace["execution"] == "submitted"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(sock))
                client.sendall(b'{"operation":"read"}\n')
                response = json.loads(client.makefile("rb").readline())
            assert response["ok"]
            assert all(r["response"] == {"ok": True} for r in trace["action_receipts"])
            assert json.loads(record.read_bytes())["candidate"] == trace["candidate"]
        finally:
            server.terminate()
            server.communicate(timeout=5)


def test_changed_candidate_bytes_rejected_by_real_binding(tmp_path, monkeypatch):
    import shutil

    c = load("current_rubric_finish_controls")
    isolated = tmp_path / "source"
    isolated.mkdir()
    for name in ("current_rubric_finish_native.py", "current_rubric_finish_policies.py"):
        shutil.copy2(SKILL / name, isolated / name)
    shutil.copytree(SKILL / "examples/finish-suite", isolated / "examples/finish-suite")
    shutil.copytree(SKILL / "examples/refund/base", isolated / "examples/refund/base")
    monkeypatch.setattr(c, "ROOT", isolated)
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    sealed = tmp_path / "development-not-holdout"
    sealed.write_bytes(b"{}")
    gateway = c.CustodyGateway(ledger, sealed, c.sha(b"{}"))
    ledger.preregister()
    ledger.measure_and_select()
    policy = isolated / "current_rubric_finish_policies.py"
    policy.write_text(policy.read_text().replace("amount = 8000", "amount = 8001"))
    with pytest.raises(PermissionError, match="source_changed"):
        gateway.open_once()
    assert not ledger.opening_consumed()


@pytest.fixture
def isolated_controls(tmp_path):
    import shutil

    isolated = tmp_path / "source"
    isolated.mkdir()
    for name in (
        "current_rubric_finish_controls.py",
        "current_rubric_finish_native.py",
        "current_rubric_finish_policies.py",
    ):
        shutil.copy2(SKILL / name, isolated / name)
    for name in ("examples/finish-suite", "examples/refund/base"):
        shutil.copytree(SKILL / name, isolated / name)
    path = isolated / "current_rubric_finish_controls.py"
    spec = importlib.util.spec_from_file_location("isolated_controls", path)
    assert spec is not None
    c = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), c.__dict__)
    return c


def divergent_cache(path: Path, before: bytes, after: bytes) -> tuple[Path, bytes, int]:
    import os
    import py_compile

    original, metadata = path.read_bytes(), path.stat()
    assert original.count(before) == 1 and len(before) == len(after)
    path.write_bytes(original.replace(before, after))
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    cache_name = py_compile.compile(str(path), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    assert cache_name is not None
    path.write_bytes(original)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    return Path(cache_name), original, metadata.st_mtime_ns


def test_selection_ignores_timestamp_valid_policy_cache(tmp_path, isolated_controls):
    c = isolated_controls
    baseline = c.SelectionLedger(tmp_path / "baseline.sqlite")
    registration = baseline.preregister()
    normal = baseline.measure_and_select()
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    assert ledger.preregister() == registration
    sealed = tmp_path / "development-not-holdout.json"
    sealed.write_bytes(b'{"partition":"exposed_development"}')
    gateway = c.CustodyGateway(ledger, sealed, c.sha(sealed.read_bytes()))

    policy = c.ROOT / "current_rubric_finish_policies.py"
    cache, original, mtime_ns = divergent_cache(policy, b"amount = 8000", b"amount = 8001")
    cached_bytes = cache.read_bytes()
    assert policy.read_bytes() == original and policy.stat().st_mtime_ns == mtime_ns
    assert c.source_bindings() == registration["sources"]
    # Prove the fixture really changes import behavior, not just cache bytes.
    native = c.load("current_rubric_finish_native.py")
    cached = native._development()
    assert cached != normal["development"]
    row = next(
        r for r in cached if (r["family"], r["input_id"], r["policy"]) == ("refund", "template", "first-template")
    )
    assert row["trace"]["decisions"][0]["amount_cents"] == 8001
    assert row["result"]["accepted"] is False

    selected = ledger.measure_and_select()
    assert selected == normal
    with ledger.connect() as db:
        assert ledger.record(db, "selection") == selected
    assert gateway.open_once() == sealed.read_bytes()
    with pytest.raises(PermissionError, match="already_opened"):
        gateway.open_once()
    assert [r["outcome"] for r in ledger.access_log()] == ["opened", "already_opened"]
    assert c.source_bindings() == registration["sources"]
    assert policy.read_bytes() == original and policy.stat().st_mtime_ns == mtime_ns
    assert cache.read_bytes() == cached_bytes


@pytest.mark.parametrize("family", ("refund", "interval-reservation", "dependency-release"))
def test_selection_binds_transitive_scorer_buffers(tmp_path, isolated_controls, family):
    c = isolated_controls
    path = c.scorer_path(family)
    normal = c.load("current_rubric_finish_native.py")._development()
    before, after = (
        (b"- prior\n", b"+ prior\n")
        if family == "refund"
        else (b"correct = well_formed and choice == expected", b"correct = well_formed and choice != expected")
    )
    cache, original, mtime_ns = divergent_cache(path, before, after)
    cached_bytes = cache.read_bytes()
    # Scorer caches are inventoried: pre-existing divergent caches must not govern execution.
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    registration = ledger.preregister()
    cached = c.load("current_rubric_finish_native.py")._development()
    assert cached != normal
    selected = ledger.measure_and_select()
    assert selected["development"] == normal
    assert c.source_bindings() == registration["sources"]
    assert path.read_bytes() == original and path.stat().st_mtime_ns == mtime_ns
    assert cache.read_bytes() == cached_bytes
    sealed = tmp_path / "development-not-holdout"
    sealed.write_bytes(b"{}")
    assert c.CustodyGateway(ledger, sealed, c.sha(b"{}")).open_once() == b"{}"


def test_selection_loader_rejects_unbound_or_changed_source(tmp_path, isolated_controls):
    c = isolated_controls
    bindings = c.source_bindings()
    native = c.selection_native(bindings)
    with pytest.raises(PermissionError, match="unbound_development_source"):
        native._load(tmp_path / "unregistered.py")
    policy = c.ROOT / "current_rubric_finish_policies.py"
    expected = native._development()
    policy.write_bytes(policy.read_bytes().replace(b"amount = 8000", b"amount = 8001"))
    assert native._development() == expected
    with pytest.raises(PermissionError, match="source_changed"):
        c.selection_native(bindings)


def test_permission_probe_executes_os_boundary_without_content(tmp_path):
    import os
    import subprocess

    c = load("current_rubric_finish_controls")
    path = tmp_path / "development-private.json"
    path.write_text("do-not-print-this-content")
    denied = c.permission_probe_argv(path, os.geteuid(), interpreter=sys.executable)
    path.chmod(0)
    try:
        result = subprocess.run(denied, capture_output=True, timeout=5)
        assert result.returncode == 0
        receipt = json.loads(result.stdout)
        assert receipt["denied"] is True and receipt["errno"] == 13
        assert b"do-not-print" not in result.stdout
    finally:
        path.chmod(0o600)
    result = subprocess.run(denied, capture_output=True, timeout=5)
    assert result.returncode == 3
    assert json.loads(result.stdout)["denied"] is False


def test_native_command_enforces_actual_solver_uid():
    import shlex

    c = load("current_rubric_finish_controls")
    assert shlex.split(c.native_policy_command("evidence", "refund"))[-2:] == ["--required-uid", "65534"]


def test_custody_file_kernel_denial_is_not_a_label(tmp_path):
    import os

    c = load("current_rubric_finish_controls")
    if os.geteuid() == 0:
        pytest.skip("kernel mode denial requires nonroot test user")
    sealed = tmp_path / "development-not-holdout"
    sealed.write_bytes(b"{}")
    sealed.chmod(0)
    try:
        with pytest.raises(PermissionError):
            c.read_regular(sealed)
    finally:
        sealed.chmod(0o600)
    assert c.read_regular(sealed) == b"{}"


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "fifo", "oversize"))
def test_custody_unsafe_file_burns_opening(tmp_path, kind):
    import os

    c = load("current_rubric_finish_controls")
    ledger = c.SelectionLedger(tmp_path / "selection.sqlite")
    target = tmp_path / "target"
    target.write_bytes(b"{}")
    sealed = tmp_path / "development-not-holdout"
    if kind == "symlink":
        sealed.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, sealed)
    elif kind == "fifo":
        os.mkfifo(sealed)
    else:
        sealed.write_bytes(b"x" * 1048577)
    gateway = c.CustodyGateway(ledger, sealed, c.sha(b"{}"))
    ledger.preregister()
    ledger.measure_and_select()
    with pytest.raises((OSError, PermissionError)):
        gateway.open_once()
    assert ledger.opening_consumed()
    with pytest.raises(PermissionError, match="already_opened"):
        gateway.open_once()
