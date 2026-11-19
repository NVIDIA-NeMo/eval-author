# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small custody-side one-opening state machine; not a sandbox or a server.
Run ONLY on an independently administered custodian host with reviewed code.
Canaries exercise sequencing under the current UID, not protected storage.
"""

import os
import sqlite3

from adapter import digest, exclusive, packed, private_root, require, strict


def boundary_preflight(root, developer_uid):
    """Necessary local check only; cannot prove lack of sudo/VM/admin credentials.
    Deployment additionally requires independent administrative ownership and real
    denied-existing-file + permitted-canary observations for developer/solver.
    """
    root = private_root(root)
    require(type(developer_uid) is int and os.getuid() != developer_uid, "custody_not_separated")
    require(root.stat().st_uid != developer_uid, "custody_not_separated")
    return {
        "custodian_uid": os.getuid(),
        "developer_uid": developer_uid,
        "administrative_boundary": "requires_independent_verification",
    }


class OneOpening:
    """Callable local library, NOT remote unauthenticated API.
    read_views and run_frozen execute within custody; result bytes never go to the
    developer until finalize. Oracles have no getter and are not a read_views input.
    Neither hashes nor this class establish true independence or approve execution.
    """

    def __init__(self, root, seal, frozen):
        self.root = private_root(root)
        require(set(seal) == {"cohort_id", "views_sha256", "oracle_sha256", "author_sha256"}, "seal_fields")
        require(
            set(frozen) == {"source_sha256", "manifest_sha256", "selection_sha256", "analysis_sha256"}, "freeze_fields"
        )
        for value in [*list(seal.values())[1:], *frozen.values()]:
            require(
                type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value), "digest_format"
            )
        require(type(seal["cohort_id"]) is str and seal["cohort_id"], "cohort_id")
        self.seal, self.frozen = dict(seal), dict(frozen)
        self.db = self.root / "opening.sqlite3"
        exclusive(self.db, b"")
        with self.connect() as db:
            db.execute(
                "CREATE TABLE opening (id INT PRIMARY KEY CHECK(id=1), state TEXT, binding TEXT, result_sha TEXT)"
            )
        exclusive(
            self.root / "commitment.json",
            packed({"seal": seal, "freeze": frozen, "proof_scope": "commitment_only_not_independence_or_permission"}),
        )

    def connect(self):
        require(not self.db.is_symlink(), "ledger_symlink")
        db = sqlite3.connect(self.db, timeout=1)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def open_once(self, requested, current_source, authorize, read_views, run_frozen):
        # All validation happens before the first private view read.
        require(
            requested == self.frozen and current_source() == self.frozen["source_sha256"], "source_or_freeze_changed"
        )
        binding = digest(packed({"seal": self.seal, "freeze": self.frozen}))
        require(authorize(binding) is True, "not_authorized")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            require(not db.execute("SELECT 1 FROM opening").fetchone(), "opening_consumed")
            db.execute("INSERT INTO opening VALUES(1,?,?,NULL)", ("burned", binding))
        # Burn is committed durably before content read, including read failures.
        views = read_views()
        require(type(views) is bytes and digest(views) == self.seal["views_sha256"], "views_changed")
        require(current_source() == self.frozen["source_sha256"], "source_changed_after_open")
        raw = run_frozen(views)  # must use captured closure, not load editable code
        require(type(raw) is bytes, "observation_bytes")
        exclusive(self.root / "observations.bin", raw)
        with self.connect() as db:
            db.execute("UPDATE opening SET state=?,result_sha=? WHERE id=1", ("output_frozen", digest(raw)))
        receipt = {
            "cohort_id": self.seal["cohort_id"],
            "binding_sha256": binding,
            "observation_sha256": digest(raw),
            "state": "output_frozen",
        }
        exclusive(self.root / "freeze-receipt.json", packed(receipt))
        require(strict((self.root / "freeze-receipt.json").read_bytes()) == receipt, "receipt_readback")
        return receipt  # no views, oracle, outputs, paths or correctness feedback

    def finalize(self, review_bytes, validate_review):
        """K/V-only release after whole-cohort freeze. Retains dissent; no scores.
        validate_review is the owning trusted current validator, not selected code.
        It must enforce complete role accounting, frozen bindings, all errors and
        independent review. A mere success Boolean supplied by developer is invalid.
        """
        with self.connect() as db:
            row = db.execute("SELECT state,binding,result_sha FROM opening").fetchone()
        require(row and row[0] == "output_frozen", "output_not_frozen")
        raw = (self.root / "observations.bin").read_bytes()
        require(digest(raw) == row[2], "observations_changed")
        require(
            type(review_bytes) is bytes and validate_review(raw, review_bytes, self.seal, self.frozen) is True,
            "independent_review_not_admitted",
        )
        exclusive(self.root / "independent-review.bin", review_bytes)
        receipt = {
            "cohort_id": self.seal["cohort_id"],
            "binding_sha256": row[1],
            "observation_sha256": row[2],
            "review_sha256": digest(review_bytes),
            "state": "reviewed_output_release",
            "qualification": "not_requested",
        }
        exclusive(self.root / "release-receipt.json", packed(receipt))
        with self.connect() as db:
            db.execute("UPDATE opening SET state=? WHERE id=1", ("released",))
        require(strict((self.root / "release-receipt.json").read_bytes()) == receipt, "receipt_readback")
        return raw, review_bytes, receipt
