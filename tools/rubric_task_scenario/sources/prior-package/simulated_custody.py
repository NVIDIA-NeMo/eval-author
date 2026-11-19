# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trusted-controller custody simulation. NEVER an OS sandbox or independence proof.
Only virtual resources below are mediated. The host user can bypass every check.
Reuses the historical OneOpening durable burn/freeze engine unchanged; deliberately
never calls its independent-review finalize with a synthetic approval.
"""

import hashlib
import sqlite3
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PINS = {
    "adapter": "a75251d24a4cac07614dec7c7671c8b58a45bf12935bd316639fd9e48671b8f5",
    "custody": "0ccde8f0f8af8f5a2f71d0e1639bb1b09a3069628f626017cd590551b2536b82",
}
# Captured exact source, never timestamp-valid bytecode or an evidence-chosen loader.
_saved_adapter = sys.modules.get("adapter")
_modules = {}
try:
    for _name, _pin in PINS.items():
        _path = ROOT / "vendor" / (_name + ".py")
        _raw = _path.read_bytes()
        if hashlib.sha256(_raw).hexdigest() != _pin:
            raise RuntimeError("historical_component_changed")
        _module = types.ModuleType("_simulated_" + _name)
        _module.__file__ = str(_path)
        if _name == "adapter":
            sys.modules["adapter"] = _module
        exec(compile(_raw, str(_path), "exec"), _module.__dict__)
        _modules[_name] = _module
finally:
    if _saved_adapter is None:
        sys.modules.pop("adapter", None)
    else:
        sys.modules["adapter"] = _saved_adapter

_a = _modules["adapter"]
Refused, require, packed, strict, digest = _a.Refused, _a.require, _a.packed, _a.strict, _a.digest
exclusive, private_root = _a.exclusive, _a.private_root
OneOpening = _modules["custody"].OneOpening
SCOPE = {
    "evidence_kind": "SIMULATED_CONTROLLER_EXECUTION",
    "native_os_denial": False,
    "host_isolation_established": False,
    "independent_authorship_established": False,
    "independent_confirmation_established": False,
    "rubric_score": None,
}
PRINCIPALS = ("A", "K", "D", "S", "V", "I")


class Principal:
    """In-process role handle, not an authenticated person or OS identity."""

    def __init__(self, role):
        self.role = role


class Controller:
    """Single trusted controller; public methods enforce simulated roles/phases.

    No subprocess, model, network, account, real chmod boundary, or native launch.
    Source is a trusted controller-supplied bytes function `evaluate(view)`; source
    hashes bind those executed bytes, not sandbox them. Do not load untrusted code.
    Retain this instance for a run. After a crash inspect receipts; never reopen.
    """

    def __init__(self, root, *, input_kind="synthetic_nonholdout"):
        require(input_kind in ("synthetic_nonholdout", "externally_supplied_unqualified"), "input_kind")
        self.root = private_root(root)
        self.lock = threading.RLock()
        self.handles = {role: Principal(role) for role in PRINCIPALS}
        self.db = self.root / "controller.sqlite3"
        exclusive(self.db, b"")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE resources(name TEXT PRIMARY KEY, kind TEXT, body BLOB);
                CREATE TABLE events(seq INTEGER PRIMARY KEY, body BLOB, sha TEXT);
                CREATE TABLE partials(role_id TEXT PRIMARY KEY, body BLOB);
            """)
            db.execute("INSERT INTO meta VALUES(?,?)", ("phase", "draft"))
        self.input_kind = input_kind
        self.opening: Any = None
        self.active_job = None
        self.manifest: Any = None
        self.source: Any = None
        self.current_source: Any = None
        self.event("K", "initialize", "", "ALLOW", input_kind=input_kind)

    def connect(self):
        require(not self.db.is_symlink(), "controller_symlink")
        db = sqlite3.connect(self.db, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def phase(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM meta WHERE key='phase'").fetchone()[0]

    def _phase(self, value):
        with self.connect() as db:
            db.execute("UPDATE meta SET value=? WHERE key='phase'", (value,))

    def event(self, actor, operation, resource, decision, **detail):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute("SELECT seq,sha FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            body = dict(
                SCOPE,
                seq=last[0] + 1 if last else 1,
                previous_sha256=last[1] if last else None,
                monotonic_ns=time.monotonic_ns(),
                actor=actor,
                operation=operation,
                resource=resource,
                decision=decision,
                phase=self.phase(),
                **detail,
            )
            raw = packed(body)
            db.execute("INSERT INTO events VALUES(?,?,?)", (body["seq"], raw, digest(raw)))
            return body

    def actor(self, handle):
        for role, registered in self.handles.items():
            if handle is registered:
                return role
        self.event("UNKNOWN", "authenticate", "", "SIMULATED_EACCES", resource_exists=False)
        raise Refused("SIMULATED_EACCES")

    def _check(self, handle, permitted, operation, resource=""):
        actor = self.actor(handle)
        if actor not in permitted:
            self.event(actor, operation, resource, "SIMULATED_EACCES", resource_exists=self._exists(resource))
            raise Refused("SIMULATED_EACCES")
        return actor

    def _exists(self, name):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM resources WHERE name=?", (name,)).fetchone() is not None

    def put(self, handle, name, kind, raw):
        """Author input only in draft; solver/developer own scratch is separate."""
        with self.lock:
            actor = self.actor(handle)
            valid_name = (
                type(name) is str and bool(name) and len(name) <= 100 and all(c.isalnum() or c in "-_" for c in name)
            )
            require(valid_name and type(raw) is bytes, "resource_shape")
            allowed = (actor == "A" and self.phase() == "draft" and kind in ("view", "oracle", "author", "public")) or (
                actor in ("D", "S") and kind == "scratch-" + actor
            )
            if not allowed or self._exists(name):
                self.event(actor, "write", name, "SIMULATED_EACCES", resource_exists=self._exists(name))
                raise Refused("SIMULATED_EACCES")
            with self.connect() as db:
                db.execute("INSERT INTO resources VALUES(?,?,?)", (name, kind, raw))
            self.event(actor, "write", name, "ALLOW", content_sha256=digest(raw), resource_exists=True)

    def read(self, handle, name):
        with self.lock:
            actor = self.actor(handle)
            with self.connect() as db:
                row = db.execute("SELECT kind,body FROM resources WHERE name=?", (name,)).fetchone()
            if row is None:
                self.event(actor, "read", name, "SIMULATED_ENOENT", resource_exists=False)
                raise Refused("SIMULATED_ENOENT")
            kind, raw = row
            phase = self.phase()
            allowed = (
                kind == "public"
                or actor == "K"
                or (actor == "A" and phase == "draft")
                or (actor == "V" and phase in ("output_frozen", "released"))
                or (actor in ("D", "I") and kind == "release" and phase == "released")
                or (kind == "scratch-" + actor)
                or (actor == "S" and phase == "executing" and self.active_job == name)
            )
            if not allowed:
                self.event(actor, "read", name, "SIMULATED_EACCES", resource_exists=True)
                raise Refused("SIMULATED_EACCES")
            self.event(actor, "read", name, "ALLOW", resource_exists=True, content_sha256=digest(raw))
            return raw

    def seal(self, handle, cohort_id):
        with self.lock:
            actor = self._check(handle, {"K"}, "seal")
            require(self.phase() == "draft", "already_sealed")
            with self.connect() as db:
                rows = db.execute("SELECT name,kind,body FROM resources ORDER BY name").fetchall()
            require(any(k == "view" for _, k, _ in rows), "missing_views")
            require(
                sum(k == "oracle" for _, k, _ in rows) == 1 and sum(k == "author" for _, k, _ in rows) == 1,
                "missing_or_duplicate_lineage",
            )
            self.views = packed({n: b.decode("utf-8") for n, k, b in rows if k == "view"})
            self.seal_record = {
                "cohort_id": cohort_id,
                "views_sha256": digest(self.views),
                "oracle_sha256": digest(next(b for _, k, b in rows if k == "oracle")),
                "author_sha256": digest(next(b for _, k, b in rows if k == "author")),
            }
            exclusive(self.root / "seal.json", packed(dict(SCOPE, input_kind=self.input_kind, seal=self.seal_record)))
            self._phase("sealed")
            self.event(actor, "seal", "", "ALLOW", seal=self.seal_record)
            return dict(self.seal_record)

    def freeze(self, handle, source, manifest, selection, analysis, current_source, *, loader="captured-source-v1"):
        with self.lock:
            actor = self._check(handle, {"K"}, "freeze")
            require(self.phase() == "sealed", "not_sealed")
            require(loader == "captured-source-v1", "unknown_loader")
            require(type(source) is bytes and callable(current_source), "source_contract")
            require(type(manifest) is list and manifest, "manifest")
            require(
                all(
                    type(j) is dict
                    and set(j) == {"role_id", "view"}
                    and type(j["role_id"]) is str
                    and j["role_id"]
                    and j["view"] in strict(self.views)
                    for j in manifest
                ),
                "manifest",
            )
            require(len({j["role_id"] for j in manifest}) == len(manifest), "duplicate_role")
            require(type(selection) is bytes and type(analysis) is bytes, "freeze_bytes")
            self.source = bytes(source)
            self.current_source = current_source
            self.manifest = strict(packed(manifest))
            self.frozen = {
                "source_sha256": digest(source),
                "manifest_sha256": digest(packed(manifest)),
                "selection_sha256": digest(selection),
                "analysis_sha256": digest(analysis),
            }
            # Freeze actual bytes, not only digests; source code is trusted, not sandboxed.
            exclusive(self.root / "executor.py", source)
            exclusive(self.root / "manifest.json", packed(manifest))
            exclusive(self.root / "selection.bin", selection)
            exclusive(self.root / "analysis.bin", analysis)
            opening_root = self.root / "opening"
            opening_root.mkdir(mode=0o700)
            self.opening = OneOpening(opening_root, self.seal_record, self.frozen)
            self._phase("ready")
            self.event(actor, "freeze", "", "ALLOW", frozen=self.frozen, loader=loader)
            return dict(self.frozen)

    def _current(self):
        # Reject mutation of in-memory manifest/captured code or on-disk closure.
        require(digest(packed(self.manifest)) == self.frozen["manifest_sha256"], "manifest_changed")
        require(digest(self.source) == self.frozen["source_sha256"], "source_changed")
        for filename, field in (
            ("executor.py", "source_sha256"),
            ("manifest.json", "manifest_sha256"),
            ("selection.bin", "selection_sha256"),
            ("analysis.bin", "analysis_sha256"),
        ):
            require(digest((self.root / filename).read_bytes()) == self.frozen[field], "freeze_file_changed")
        return digest(self.current_source())

    def open_once(self, handle, requested):
        actor = self._check(handle, {"K"}, "open")
        require(self.opening is not None, "not_frozen")

        # Vendor transaction is the concurrency/burn authority, NOT the Python lock.
        def read_views():
            self._phase("executing")
            self.event(actor, "opening_burned_before_read", "", "ALLOW")
            with self.connect() as db:
                rows = db.execute("SELECT name,body FROM resources WHERE kind='view' ORDER BY name").fetchall()
            return packed({n: b.decode("utf-8") for n, b in rows})

        def run(views):
            namespace: dict[str, Any] = {"__name__": "frozen_trusted_executor"}
            exec(compile(self.source, "<frozen-trusted-executor>", "exec"), namespace)
            evaluate = namespace.get("evaluate")
            if not callable(evaluate):
                raise Refused("missing_evaluate")
            outputs = []
            for job in self.manifest:
                require(self._current() == self.frozen["source_sha256"], "source_changed_during_run")
                self.active_job = job["view"]
                view = self.read(self.handles["S"], job["view"])
                require(view.decode("utf-8") == strict(views)[job["view"]], "view_changed")
                raw = evaluate(view)
                require(type(raw) is bytes, "output_bytes")
                self.active_job = None
                with self.connect() as db:
                    db.execute("INSERT INTO partials VALUES(?,?)", (job["role_id"], raw))
                    db.execute("INSERT INTO resources VALUES(?,?,?)", ("partial-" + job["role_id"], "partial", raw))
                outputs.append({"role_id": job["role_id"], "output_hex": raw.hex(), "output_sha256": digest(raw)})
                self.event("S", "partial_output_held", job["role_id"], "ALLOW", output_sha256=digest(raw))
            require(self._current() == self.frozen["source_sha256"], "source_changed_before_freeze")
            return packed(dict(SCOPE, input_kind=self.input_kind, outputs=outputs))

        try:
            receipt = self.opening.open_once(requested, self._current, lambda _: True, read_views, run)
        except BaseException as exc:
            # A losing duplicate caller must not clear the winner's active view.
            if str(exc) != "opening_consumed":
                self.active_job = None
            with self.opening.connect() as db:
                row = db.execute("SELECT state FROM opening").fetchone()
            # A duplicate caller must not change the winner's controller phase.
            if row and row[0] == "burned" and str(exc) != "opening_consumed":
                self._phase("burned_failure")
            self.event(
                actor, "open", "", "REJECT", category=str(exc) if isinstance(exc, Refused) else type(exc).__name__
            )
            raise
        raw = (self.root / "opening/observations.bin").read_bytes()
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO resources VALUES(?,?,?)", ("observations", "observations", raw))
        self._phase("output_frozen")
        scoped = dict(SCOPE, **receipt)
        exclusive(self.root / "output-freeze.json", packed(scoped))
        self.event(actor, "whole_cohort_output_frozen", "", "ALLOW", receipt=scoped)
        return scoped

    def release_simulated(self, handle, review):
        """Release simulated-controller packet; never an independent-review receipt.
        Review can retain findings/dissent but cannot promote task merit.
        """
        with self.lock:
            actor = self._check(handle, {"V"}, "release")
            require(self.phase() == "output_frozen", "output_not_frozen")
            require(
                type(review) is dict
                and set(review) == {"observation_sha256", "findings", "dissent"}
                and type(review["findings"]) is list
                and type(review["dissent"]) is list,
                "review_schema",
            )
            raw = self.read(handle, "observations")
            frozen = strict((self.root / "output-freeze.json").read_bytes())
            require(digest(raw) == frozen["observation_sha256"] == review["observation_sha256"], "observations_changed")
            require(raw == (self.root / "opening/observations.bin").read_bytes(), "observations_changed")
            outputs = strict(raw)["outputs"]
            require([o["role_id"] for o in outputs] == [j["role_id"] for j in self.manifest], "incomplete_cohort")
            require(all(digest(bytes.fromhex(o["output_hex"])) == o["output_sha256"] for o in outputs), "output_digest")
            require(self._current() == self.frozen["source_sha256"], "source_changed_before_release")
            packet = dict(
                SCOPE,
                input_kind=self.input_kind,
                state="SIMULATED_REVIEWED_RELEASE",
                observation_sha256=digest(raw),
                observations=strict(raw),
                review=review,
                qualification="NOT_REQUESTED_NOT_GRANTED",
            )
            payload = packed(packet)
            exclusive(self.root / "simulated-release.json", payload)
            with self.connect() as db:
                db.execute("INSERT INTO resources VALUES(?,?,?)", ("release", "release", payload))
            self._phase("released")
            self.event(actor, "release_simulated", "release", "ALLOW", release_sha256=digest(payload))
            require((self.root / "simulated-release.json").read_bytes() == payload, "release_readback")
            return packet

    def export_receipt(self):
        """Trusted operator evidence export; not a D/S read API or proof of honesty."""
        with self.connect() as db:
            rows = db.execute("SELECT body,sha FROM events ORDER BY seq").fetchall()
            partials = db.execute("SELECT role_id,body FROM partials ORDER BY role_id").fetchall()
        previous = None
        events = []
        for index, (raw, sha) in enumerate(rows, 1):
            body = strict(raw)
            require(body["seq"] == index and body["previous_sha256"] == previous and digest(raw) == sha, "event_chain")
            require(body["native_os_denial"] is False, "mislabelled_native_receipt")
            previous = sha
            events.append(body)
        result = dict(
            SCOPE,
            input_kind=self.input_kind,
            phase=self.phase(),
            events=events,
            event_count=len(events),
            event_head_sha256=previous,
            partials=[{"role_id": r, "output_sha256": digest(b)} for r, b in partials],
        )
        exclusive(self.root / "execution-receipt.json", packed(result))
        return result
