# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

from src.backends.contracts import IncompatibleBackend


def require_backend_spec(artifact: dict) -> dict:
    from src.common.domain_config import INPUT_SCHEMA

    spec = artifact.get("backend_spec")
    if (
        not isinstance(spec, dict)
        or spec.get("schema") != INPUT_SCHEMA
        or "backend" in spec
        or not all(isinstance(spec.get(key), str) and spec[key] for key in ("domain", "resources_dir"))
    ):
        raise IncompatibleBackend(
            "Artifact lacks a prepared input binding; prepare a fresh data bundle "
            "and regenerate the checkpoint or clusters"
        )
    return spec


def require_same_backend(selected: dict, saved: dict) -> None:
    from src.common.domain_config import DomainConfig

    if DomainConfig.from_spec(selected).to_spec() != DomainConfig.from_spec(saved).to_spec():
        raise IncompatibleBackend("Prepared inputs changed; use a fresh run and compatible checkpoint")


def bind_run(
    directory: str, spec: dict, length_policy: dict | None, *, resume: bool, settings: dict | None = None
) -> None:
    require_backend_spec({"backend_spec": spec})
    path = Path(directory) / "backend.json"
    value = {"backend_spec": spec, "length_distribution": length_policy}
    if settings is not None:
        value["run_settings"] = settings
    if path.exists():
        if not resume or json.loads(path.read_text()) != value:
            raise IncompatibleBackend(
                "Run exists or prepared inputs/length/settings changed; select a fresh run directory"
            )
    elif resume and (Path(directory) / "progress.jsonl").exists():
        raise IncompatibleBackend("Legacy run has no prepared input binding; regenerate it in a fresh directory")
    else:
        path.write_text(json.dumps(value, indent=2) + "\n")
