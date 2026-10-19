#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Package validated semantic baselines and configure unattended GitHub checks.

The default only writes a private ZIP. --apply configures the main-only
semantic-regression environment, uploads the private bundle and inference key,
and records the bundle digest. No public storage or signed download URL is needed.
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import semantic_regression as semantic
from fetch_semantic_bundle import PART_COUNT, PART_SIZE, unpack

ENVIRONMENT = "semantic-regression"


def bundle(suite_path, runtime):
    suite = semantic.load(suite_path)
    semantic.validate_suite(suite)
    files = {}
    for case in suite["cases"]:
        lock_path = semantic.contained(suite_path.parent, case["baseline"])
        lock = semantic.load(lock_path)
        baseline_path = semantic.contained(lock_path.parent, "baseline.json")
        if (
            lock["schema"] != "nemo.semantic_lock.v1"
            or semantic.normalize_binding(lock["binding"]) != semantic.binding(suite, case, runtime)
            or lock["baseline_digest"] != semantic.sha(baseline_path.read_bytes())
            or not re.fullmatch(r"[0-9a-f]{64}", lock["reviewed_mapping_digest"])
            or semantic.load(baseline_path)["N"] != suite["runs"]
        ):
            raise ValueError("baseline does not match the configured suite and runtime")
        semantic.command(runtime, "selftest.py", ["--baseline", baseline_path], lock_path.parent)
        case["baseline"] = f"baselines/{case['id']}/lock.json"
        files[case["baseline"]] = lock_path.read_bytes()
        files[f"baselines/{case['id']}/baseline.json"] = baseline_path.read_bytes()
    files["suite.json"] = (json.dumps(suite, indent=2) + "\n").encode()
    for path in sorted(runtime.rglob("*")):
        relative = path.relative_to(runtime)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("runtime symlinks are not supported")
        if path.is_file() and path.suffix in (".py", ".md", ".json"):
            files["ratchet/" + relative.as_posix()] = path.read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    data = output.getvalue()
    # Exercise the same admission checks CI uses before configuring anything.
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "bundle"
        unpack(data, semantic.sha(data), destination)
        if semantic.tree_digest(destination / "ratchet") != semantic.tree_digest(runtime):
            raise ValueError("packaged runtime differs from the calibrated runtime")
    return data


def gh(args, data=None):
    result = subprocess.run(["gh", *args], input=data, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        # Do not echo provider diagnostics, request bodies or credentials.
        raise ValueError("GitHub configuration failed; check gh authentication and repository administration access")
    return json.loads(result.stdout) if result.stdout.strip().startswith(("{", "[")) else None


def configure(repo, data, key):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not key:
        raise ValueError("repository and inference credential required")
    encoded = base64.b64encode(data).decode()
    if len(encoded) > PART_SIZE * PART_COUNT:
        raise ValueError("bundle exceeds secret storage; use the existing HTTPS bundle provisioning instead")
    # Check authentication before creating or changing an environment.
    gh(["api", "user"])
    endpoint = f"repos/{repo}/environments/{ENVIRONMENT}"
    gh(
        ["api", "--method", "PUT", endpoint, "--input", "-"],
        json.dumps(
            {
                "wait_timer": 0,
                "reviewers": [],
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            }
        ),
    )
    policies = gh(["api", endpoint + "/deployment-branch-policies"])["branch_policies"]
    for policy in policies:
        if policy["name"] != "main" or policy.get("type", "branch") != "branch":
            raise ValueError("semantic environment has an unexpected branch policy; restrict it to main before setup")
    if not policies:
        gh(
            ["api", "--method", "POST", endpoint + "/deployment-branch-policies", "--input", "-"],
            json.dumps({"name": "main", "type": "branch"}),
        )
    existing_secrets = {
        entry["name"] for entry in gh(["secret", "list", "--repo", repo, "--env", ENVIRONMENT, "--json", "name"])
    }
    for i in range(PART_COUNT):
        part = encoded[i * PART_SIZE : (i + 1) * PART_SIZE]
        name = f"SEMANTIC_BUNDLE_PART_{i + 1}"
        if part:
            gh(["secret", "set", name, "--repo", repo, "--env", ENVIRONMENT], part)
        elif name in existing_secrets:
            gh(["secret", "delete", name, "--repo", repo, "--env", ENVIRONMENT])
    gh(["secret", "set", "INFERENCE_HUB_API_KEY", "--repo", repo, "--env", ENVIRONMENT], key)
    # Set the digest last. Interrupted updates fail closed against the old digest.
    gh(
        [
            "variable",
            "set",
            "SEMANTIC_BUNDLE_SHA256",
            "--repo",
            repo,
            "--env",
            ENVIRONMENT,
            "--body",
            semantic.sha(data),
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", default="NVIDIA-NeMo/eval-author")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        data = bundle(args.suite.resolve(), args.runtime.resolve())
        with args.output.open("xb") as output:
            os.chmod(args.output, 0o600)
            output.write(data)
        print(f"Validated private bundle: {len(data)} bytes; SHA-256 {semantic.sha(data)}")
        if args.apply:
            key = os.environ.get("INFERENCE_HUB_API_KEY") or os.environ.get("NVIDIA_INFERENCE_HUB_API_KEY")
            configure(args.repo, data, key)
            print("Configured semantic checks on main pushes and daily; no per-run reviewer approval.")
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit(
            "Semantic setup incomplete. Check the suite, baseline locks, runtime and GitHub access."
        ) from None


if __name__ == "__main__":
    main()
