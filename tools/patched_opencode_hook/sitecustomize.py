# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private, narrow startup hook for Harbor children of the pinned wrapper."""

import os


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
    module.activate(manifest)


if "EA_PATCHED_OPENCODE_MANIFEST" in os.environ or "_EA_PATCHED_OPENCODE_HOOK" in os.environ:
    try:
        _activate()
    except BaseException:
        # CPython ordinarily prints and ignores sitecustomize exceptions.
        os.write(2, b"patched OpenCode activation failed; refusing execution\n")
        os._exit(78)
