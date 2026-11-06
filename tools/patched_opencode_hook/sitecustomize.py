# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private, narrow startup hook for Harbor children of the pinned wrapper."""

import os


def _record_failure(reason):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "live_agent_diagnostics.py"
    spec = importlib.util.spec_from_file_location("_ea_live_diagnostics", path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.record_runtime_failure("startup_hook", reason)


def _activate():
    import hashlib
    import importlib.util
    import sys
    from pathlib import Path

    if os.environ.get("_EA_PATCHED_OPENCODE_HOOK") != "pinned-v1":
        raise RuntimeError("missing activation token")
    # Only load our checked-in sibling, never a path supplied by the manifest or
    # environment. Do not add the tools directory (or repository) to sys.path.
    path = Path(__file__).resolve().parent.parent / "patched_opencode_runtime.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != os.environ.get("_EA_PATCHED_OPENCODE_RUNTIME_SHA256"):
        raise RuntimeError("runtime source changed")
    name = "_ea_patched_opencode_runtime"
    if name in sys.modules:
        raise RuntimeError("conflicting runtime module")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing runtime loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    manifest = os.environ["EA_PATCHED_OPENCODE_MANIFEST"]
    if module.sha256(manifest) != os.environ.get("_EA_PATCHED_OPENCODE_MANIFEST_SHA256"):
        raise RuntimeError("manifest binding changed")
    if "EA_PREPARED_IMAGES_MANIFEST" in os.environ:
        try:
            if module.sha256(os.environ["EA_PREPARED_IMAGES_MANIFEST"]) != os.environ.get("_EA_PREPARED_IMAGES_SHA256"):
                raise RuntimeError("prepared image manifest binding changed")
        except BaseException:
            _record_failure("prepared_image_mismatch")
            raise
    module.activate(manifest)


if any(
    key in os.environ
    for key in (
        "EA_PATCHED_OPENCODE_MANIFEST",
        "_EA_PATCHED_OPENCODE_HOOK",
        "EA_PREPARED_IMAGES_MANIFEST",
        "EA_REQUIRE_PREPARED_IMAGES",
    )
):
    try:
        _activate()
    except BaseException:
        # CPython ordinarily prints and ignores sitecustomize exceptions.
        try:
            _record_failure("patched_runtime_activation_failed")
        except BaseException:
            pass
        os.write(2, b"patched OpenCode activation failed; refusing execution\n")
        os._exit(78)
