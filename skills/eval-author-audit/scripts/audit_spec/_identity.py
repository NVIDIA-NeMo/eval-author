# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from _schema import AuditEnvironmentError


def identity_module() -> ModuleType:
    scripts = Path(__file__).resolve().parents[3] / "eval-author/scripts"
    message = "Install the sibling eval-author skill to validate selected identity evidence"
    if not (scripts / "rubric_identity.py").is_file():
        raise AuditEnvironmentError(message)
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module("rubric_identity")
    except ImportError as exc:
        raise AuditEnvironmentError(message) from exc
