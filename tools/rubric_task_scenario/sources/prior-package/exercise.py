# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Focused synthetic custody controls; all artifacts retained, no native claims."""

import concurrent.futures
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
m = types.ModuleType("simulated_custody")
m.__file__ = str(ROOT / "simulated_custody.py")
exec(compile((ROOT / "simulated_custody.py").read_bytes(), m.__file__, "exec"), m.__dict__)
Controller, Refused, packed, digest, exclusive = m.Controller, m.Refused, m.packed, m.digest, m.exclusive
SOURCE = b"def evaluate(view):\n    return view.upper()\n"


def exercise(root):
    root.mkdir(mode=0o700)
    checks = []
    controllers = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append({"name": name, "passed": True})

    def refusal(name, code, fn):
        try:
            fn()
        except Refused as e:
            check(name, str(e) == code)
        else:
            raise AssertionError(name + " not refused")

    def setup(name, source=SOURCE, current=None):
        path = root / name
        path.mkdir(mode=0o700)
        c = Controller(path)
        controllers.append(c)
        a, k = c.handles["A"], c.handles["K"]
        for name, kind, raw in [
            ("one", "view", b"canary-one"),
            ("two", "view", b"canary-two"),
            ("oracle", "oracle", b"NOT A SECRET: SYNTHETIC ORACLE"),
            ("author", "author", b"SYNTHETIC EXPOSED BUILDER"),
            ("public", "public", b"PUBLIC"),
        ]:
            c.put(a, name, kind, raw)
        c.seal(k, "SYNTHETIC-NONHOLDOUT-" + path.name)
        f = c.freeze(
            k,
            source,
            [{"role_id": "one", "view": "one"}, {"role_id": "two", "view": "two"}],
            b"FIXED SYNTHETIC POLICY",
            b"ALL OUTPUTS BEFORE RELEASE",
            current or (lambda: source),
        )
        return c, f

    c, f = setup("normal")
    a, k, d, s, v, i = (c.handles[r] for r in m.PRINCIPALS)
    for actor in (d, s):
        check("permitted-public-" + actor.role, c.read(actor, "public") == b"PUBLIC")
        refusal("private-denial-" + actor.role, "SIMULATED_EACCES", lambda actor=actor: c.read(actor, "oracle"))
        refusal(
            "private-write-" + actor.role,
            "SIMULATED_EACCES",
            lambda actor=actor: c.put(actor, "oracle", "oracle", b"changed"),
        )
        c.put(actor, "scratch-" + actor.role, "scratch-" + actor.role, b"allowed")
        check("permitted-write-" + actor.role, c.read(actor, "scratch-" + actor.role) == b"allowed")
    refusal("missing-not-denial", "SIMULATED_ENOENT", lambda: c.read(s, "missing"))
    refusal("identity-spoof", "SIMULATED_EACCES", lambda: c.read(m.Principal("K"), "oracle"))
    refusal("wrong-opening-role", "SIMULATED_EACCES", lambda: c.open_once(d, f))
    refusal("sealed-author-write", "SIMULATED_EACCES", lambda: c.put(a, "new", "view", b"bad"))
    refusal("early-review", "output_not_frozen", lambda: c.release_simulated(v, {}))
    refusal("solver-before-opening", "SIMULATED_EACCES", lambda: c.read(s, "one"))

    # Real concurrent callers; durable vendor SQLite state admits only one reader.
    def attempt(_):
        try:
            return c.open_once(k, f)
        except Refused as e:
            return str(e)

    import threading

    reached = threading.Event()
    resume = threading.Event()
    original_read = c.read

    def paused_read(handle, name):
        if handle is s and name == "one":
            reached.set()
            if not resume.wait(5):
                raise AssertionError("concurrent winner timed out")
        return original_read(handle, name)

    c.read = paused_read
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(attempt, 0)
        assert reached.wait(5)
        loser = pool.submit(attempt, 1)
        losing_result = loser.result(timeout=5)
        resume.set()
        results = [winner.result(timeout=5), losing_result]
    c.read = original_read
    check(
        "concurrent-exactly-one",
        sum(isinstance(x, dict) for x in results) == 1 and results.count("opening_consumed") == 1,
    )
    refusal("partial-no-developer-feedback", "SIMULATED_EACCES", lambda: c.read(d, "partial-one"))
    refusal("frozen-no-developer-feedback", "SIMULATED_EACCES", lambda: c.read(d, "observations"))
    refusal("solver-after-opening", "SIMULATED_EACCES", lambda: c.read(s, "two"))
    observed = c.read(v, "observations")
    refusal(
        "review-binding",
        "observations_changed",
        lambda: c.release_simulated(v, {"observation_sha256": "0" * 64, "findings": [], "dissent": []}),
    )
    release = c.release_simulated(
        v,
        {
            "observation_sha256": digest(observed),
            "findings": ["Synthetic controls only"],
            "dissent": ["This is not independent review."],
        },
    )
    check("release-readback", c.read(i, "release") == packed(release) == c.read(d, "release"))
    check("dissent-preserved", bool(release["review"]["dissent"]))
    check("no-score", release["rubric_score"] is None)
    refusal("duplicate-release", "output_not_frozen", lambda: c.release_simulated(v, {}))
    refusal("duplicate-open", "opening_consumed", lambda: c.open_once(k, f))
    drift, f = setup("drift", current=lambda: b"changed")
    refusal("drift-before-read", "source_or_freeze_changed", lambda: drift.open_once(drift.handles["K"], f))
    with drift.opening.connect() as db:
        check("drift-not-consumed", db.execute("SELECT * FROM opening").fetchall() == [])
    fail, f = setup("failed", source=b'def evaluate(view):\n    raise ValueError("synthetic failure")\n')
    try:
        fail.open_once(fail.handles["K"], f)
    except ValueError:
        check("execution-failure-retained", fail.phase() == "burned_failure")
    else:
        raise AssertionError("failed executor accepted")
    refusal("failed-opening-burned", "opening_consumed", lambda: fail.open_once(fail.handles["K"], f))
    refusal("failed-no-release", "output_not_frozen", lambda: fail.release_simulated(fail.handles["V"], {}))
    tamper, f = setup("tampered-view")
    with tamper.connect() as db:
        db.execute("UPDATE resources SET body=? WHERE name='one'", (b"CHANGED",))
    refusal("view-hash-reject", "views_changed", lambda: tamper.open_once(tamper.handles["K"], f))
    refusal("bad-view-burned", "opening_consumed", lambda: tamper.open_once(tamper.handles["K"], f))
    # Unknown loader must stop before creating an opening.
    p = root / "prefreeze"
    p.mkdir(mode=0o700)
    pre = Controller(p)
    controllers.append(pre)
    refusal("prefreeze", "not_frozen", lambda: pre.open_once(pre.handles["K"], {}))
    for name, kind in [("one", "view"), ("oracle", "oracle"), ("author", "author")]:
        pre.put(pre.handles["A"], name, kind, b"canary")
    pre.seal(pre.handles["K"], "SYNTHETIC-PREFREEZE")
    refusal(
        "unknown-loader",
        "unknown_loader",
        lambda: pre.freeze(
            pre.handles["K"], SOURCE, [{"role_id": "one", "view": "one"}], b"", b"", lambda: SOURCE, loader="pyc"
        ),
    )
    receipts = [c.export_receipt() for c in controllers]
    denials = [e for r in receipts for e in r["events"] if e["decision"] == "SIMULATED_EACCES"]
    check("denials-unmistakable", bool(denials) and all(e["native_os_denial"] is False for e in denials))
    check("existing-private-not-enoent", all(e["resource_exists"] for e in denials if e["resource"] == "oracle"))
    result = dict(
        m.SCOPE,
        checks=checks,
        passed=len(checks),
        failed=0,
        retained_controllers=len(receipts),
        simulated_denials=len(denials),
    )
    exclusive(root / "verification.json", packed(result))
    return result


if __name__ == "__main__":
    print(packed(exercise(Path(sys.argv[1]))).decode())
