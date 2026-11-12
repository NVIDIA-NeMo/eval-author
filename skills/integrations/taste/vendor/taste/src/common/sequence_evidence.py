# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private, append-only evidence for sequence learning and selection."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class EvidenceWriteError(RuntimeError):
    pass


def error_metadata(error):
    # Provider exceptions can contain authenticated requests or response bodies.
    chain = []
    current = error
    while current is not None and len(chain) < 8:
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    status = getattr(error, "status_code", None)
    return {"classes": chain, "http_status": status if isinstance(status, int) else None}


class SequenceEvidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.path = self.directory / "events.jsonl"
        self.index = 0

    def record(self, event, **payload):
        record = {
            "schema": "taste.sequence-evidence.v1",
            "index": self.index,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        try:
            data = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception as error:
            raise EvidenceWriteError("Unable to retain sequence evidence") from error
        self.index += 1
        return record["index"]


def validation_payload(result):
    return {
        "valid": result.valid,
        "reason": result.reason,
        "problematic_indices": result.problematic_indices,
        "evidence_id": getattr(result, "evidence_id", None),
    }
