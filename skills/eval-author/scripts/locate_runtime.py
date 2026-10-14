#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Locate existing runtimes without installing, activating, or running evals.

Only prerequisite/readiness flows invoke this command. It executes installed
Python/CLI probes, never project runners. Use only trusted runtime locations.
Every probe runs in a scratch cwd; Python uses isolated mode. No provider imports
occur in this process. Virtualenv Python paths deliberately remain unresolved.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

if sys.version_info < (3, 11):
    print(
        json.dumps(
            {
                "error": "Runtime locator requires Python 3.11 or later; rerun with python3.11+",
                "selection": {},
                "search": {"complete": False},
            }
        )
    )
    raise SystemExit(1)

import tomllib  # noqa: E402

from runtime_policy import load, satisfies  # noqa: E402

MAX_CANDIDATES = 24
PROVIDER_SECONDS = 390
PROBE_SECONDS = 10
PROBE_MARKER = "EVAL_AUTHOR_RUNTIME="
PROBE = """
import importlib, importlib.metadata, json, sys
package, symbols = json.loads(sys.argv[1])
result = {'python_version': '.'.join(map(str, sys.version_info[:3])),
          'python': sys.executable, 'prefix': sys.prefix, 'version': None, 'errors': []}
try:
    result['version'] = importlib.metadata.version(package)
    for symbol in symbols:
        module, _, attrs = symbol.partition(':')
        obj = importlib.import_module(module)
        for attr in attrs.split('.') if attrs else []:
            obj = getattr(obj, attr)
except Exception as exc:
    result['errors'].append(type(exc).__name__ + ': ' + str(exc))
print(MARKER + json.dumps(result))
""".replace("MARKER", repr(PROBE_MARKER))


def absolute(path: str | Path, root: Path) -> str:
    """Normalize dot segments, not symlinks: resolving venv Python loses identity."""
    value = Path(path).expanduser()
    return os.path.abspath(value if value.is_absolute() else root / value)


def same_launcher(first: str | None, second: str | None) -> bool:
    if first == second:
        return True
    if not first or not second:
        return False
    try:
        return Path(first).samefile(second)
    except OSError:
        return False


def same_python(first: str | None, second: str | None) -> bool:
    """Aliases within one environment are equal; different venvs stay distinct."""
    if first == second:
        return True
    if not first or not second:
        return False
    try:
        return Path(first).parent.resolve() == Path(second).parent.resolve() and Path(first).samefile(second)
    except OSError:
        return False


def path_matches(name: str) -> list[str]:
    found = []
    for directory in os.get_exec_path():
        path = Path(directory or ".") / name
        if path.is_file() or path.is_symlink():
            found.append(absolute(path, Path.cwd()))
    return list(dict.fromkeys(found))


def launcher_python(cli: str) -> str | None:
    """Read a Python shebang. Never execute launcher text to discover its Python.

    uv's shell trampoline and platform-native launchers need an explicit Python
    path. /usr/bin/env Python follows the same PATH lookup the launcher would use.
    """
    try:
        with Path(cli).open("rb") as source:
            first = source.readline(4096).decode("utf-8")
            second = source.readline(4096).decode("utf-8")
        if not first.startswith("#!"):
            return None
        if first.strip() == "#!/bin/sh" and second.startswith("'''exec'"):
            words = shlex.split(second[len("'''exec'") :].strip())
            if len(words) == 3 and words[1:] == ["$0", "$@"] and Path(words[0]).is_absolute():
                return absolute(words[0], Path.cwd())
            return None
        words = shlex.split(first[2:].strip())
        if not words:
            return None
        if Path(words[0]).name == "env":
            if len(words) != 2:
                return None
            executable = shutil.which(words[1])
        else:
            executable = words[0] if len(words) == 1 else None
        if executable and Path(executable).name.startswith("python") and Path(executable).is_absolute():
            return absolute(executable, Path.cwd())
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def invoke(argv: list[str], deadline: float, timeout: float = PROBE_SECONDS) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"ok": False, "error": "total probe budget exhausted", "budget_exhausted": True, "incomplete": True}
    try:
        with tempfile.TemporaryDirectory(prefix="eval-author-runtime-") as scratch:
            result = subprocess.run(
                argv,
                cwd=scratch,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=min(timeout, remaining),
                check=False,
            )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": None if result.returncode == 0 else f"command exited {result.returncode}: {result.stderr[-2000:]}",
        }
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "error": f"TimeoutExpired: {exc}", "incomplete": True}
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def inspect(candidate: dict, flow: str, deadline: float, policy: dict) -> dict:
    """Verify an exact Python/CLI pair and preserve failed checks."""
    result = {**candidate, "status": "unresolved", "version": None, "python_version": None, "checks": []}
    provider = policy["providers"][candidate["provider"]]
    result.update(supported_range=provider["supported"][flow], required_python=provider["python"])
    python, cli = candidate.get("python"), candidate.get("cli")
    if not python:
        result["reason"] = "Cannot identify launcher interpreter; supply its exact Python path"
        return result
    if not cli:
        cli = str(Path(python).parent / provider["cli"])
        result["cli"] = cli
    if not Path(python).is_file() or not Path(cli).is_file():
        result.update(status="missing", reason="Python or CLI path does not exist")
        return result
    launcher = launcher_python(cli)
    same_environment = launcher == python
    if launcher and not same_environment:
        try:
            # Resolve only the parent directory: python/python3 aliases in one
            # venv are equivalent, but aliases in two venvs are not.
            same_environment = Path(launcher).parent.resolve() == Path(python).parent.resolve() and Path(
                launcher
            ).samefile(python)
        except OSError:
            pass
    if not same_environment:
        result["reason"] = (
            "CLI interpreter cannot be paired with selected Python; inspect launcher or supply matching paths"
        )
        return result
    capabilities = provider["capabilities"][flow]
    command = [python, "-I", "-c", PROBE, json.dumps([provider["package"], capabilities["imports"]])]
    outcome = invoke(command, deadline, provider["probe_seconds"])
    result["checks"].append({"name": "python-imports", "ok": outcome["ok"], "error": outcome.get("error")})
    if not outcome["ok"]:
        result.update(
            status="unresolved" if outcome.get("incomplete") else "broken",
            reason=outcome["error"],
            incomplete=outcome.get("incomplete", False),
        )
        return result
    try:
        rows = [
            line.removeprefix(PROBE_MARKER) for line in outcome["stdout"].splitlines() if line.startswith(PROBE_MARKER)
        ]
        info = json.loads(rows[-1])
        result.update({key: info[key] for key in ("version", "python_version", "prefix")})
        if info["errors"]:
            result["checks"][-1]["ok"] = False
            result["checks"][-1]["error"] = "; ".join(info["errors"])
            reason = "; ".join(info["errors"])
            try:
                parent_visible = (
                    same_python(python, sys.executable)
                    and importlib.util.find_spec("nemo_gym" if candidate["provider"] == "gym" else "harbor") is not None
                )
            except (ImportError, ValueError):
                parent_visible = False
            if parent_visible:
                reason += (
                    "; package is visible in the invoking process but the isolated probe failed. "
                    "Isolated probes exclude user site-packages and PYTHONPATH; use a self-contained "
                    "virtualenv and inspect the import error above."
                )
            result.update(status="broken", reason=reason)
            return result
        constraints = [(info["version"], provider["supported"][flow]), (info["python_version"], provider["python"])]
        constraints.extend((info["version"], requirement) for requirement in candidate.get("requirements", []))
        if not all(satisfies(value, constraint) for value, constraint in constraints):
            result.update(
                status="incompatible", reason="Installed package/Python does not meet policy or project requirements"
            )
            return result
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        result.update(status="unresolved", reason=str(exc))
        return result
    for args in [["--version"], *[list(parts) + ["--help"] for parts in capabilities["help"]]]:
        outcome = invoke([cli, *args], deadline, provider["probe_seconds"])
        ok = outcome["ok"]
        if ok and args == ["--version"]:
            pattern = (
                r"NeMo Gym v?(\d+\.\d+\.\d+)(?![\w.+-])"
                if candidate["provider"] == "gym"
                else r"(?<![\w.])(\d+\.\d+\.\d+)(?![\w.+-])"
            )
            match = re.search(pattern, outcome["stdout"])
            ok = bool(match and match.group(1) == result["version"])
        result["checks"].append(
            {"name": "cli " + " ".join(args), "ok": ok, "error": outcome.get("error") if not outcome["ok"] else None}
        )
        if not ok:
            result.update(
                status="unresolved" if outcome.get("incomplete") else "broken",
                reason=outcome.get("error") or "CLI identity/version disagrees with package metadata",
                incomplete=outcome.get("incomplete", False),
            )
            return result
    result.update(status="compatible", reason="Required version, Python, imports and CLI checks passed")
    result["matching_package_version_profiles"] = [
        name for name, profile in provider["test_profiles"].items() if profile["version"] == result["version"]
    ]
    return result


def declarations(repo: Path, policy: dict) -> tuple[dict, dict, dict]:
    path = repo / "pyproject.toml"
    if not path.exists():
        return {}, {}, {}
    data = tomllib.loads(path.read_text())
    tool = data.get("tool", {})
    project = data.get("project", {})
    if not isinstance(tool, dict) or not isinstance(project, dict):
        raise ValueError("project and tool must be TOML tables")
    author = tool.get("eval-author", {})
    if not isinstance(author, dict):
        raise ValueError("tool.eval-author must be a TOML table")
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
        raise ValueError("project.dependencies must be an array of strings")
    declared = author.get("runtimes", {})
    if not isinstance(declared, dict) or set(declared) - {"harbor", "gym"}:
        raise ValueError("Invalid tool.eval-author.runtimes table")
    for name, entry in declared.items():
        if not isinstance(entry, dict) or set(entry) - {"python", "cli", "requirement"}:
            raise ValueError(f"Invalid runtime declaration: {name}")
        if any(not isinstance(value, str) or not value.strip() for value in entry.values()):
            raise ValueError(f"Invalid runtime declaration values: {name}")
        if "cli" in entry and "python" not in entry:
            raise ValueError(f"Declared {name} CLI requires its Python path")
    requirements: dict[str, list[str]] = {"harbor": [], "gym": []}
    errors: dict[str, list[str]] = {name: [] for name in policy["providers"]}

    def requirement(name: str, suffix: str, original: str) -> None:
        if not suffix:
            return
        try:
            satisfies("0.0.0", suffix)
        except ValueError as exc:
            errors[name].append(f"{original!r}: {exc}")
        else:
            requirements[name].append(suffix)

    for dependency in dependencies:
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$", dependency)
        if match is None:
            continue
        package, suffix = match.groups()
        normalized = re.sub(r"[-_.]+", "-", package).lower()
        for name, entry in policy["providers"].items():
            if normalized == re.sub(r"[-_.]+", "-", entry["package"]).lower():
                requirement(name, suffix.strip(), dependency)
    for name, entry in declared.items():
        if "requirement" in entry:
            requirement(name, entry["requirement"], entry["requirement"])
    return declared, requirements, errors


def select(candidates: list[dict], provider: str, incomplete_priorities: list[int]) -> dict:
    items = [item for item in candidates if item["provider"] == provider]
    chosen_tier = min((item["priority"] for item in items), default=99)
    # Explicit and project-declared paths are authoritative even when broken.
    eligible = [
        item
        for item in items
        if item["status"] == "compatible" and (chosen_tier > 1 or item["priority"] == chosen_tier)
    ]
    if not eligible:
        return {"status": "unresolved", "reason": "No compatible runtime under selection policy", "selected": None}
    priority = min(item["priority"] for item in eligible)
    if any(unprobed <= priority for unprobed in incomplete_priorities):
        return {
            "status": "unresolved",
            "reason": "Search incomplete at an equal or higher priority; inspect limits/errors",
            "selected": None,
        }
    best = [item for item in eligible if item["priority"] == priority]
    # Two different venvs sharing a base binary must remain distinct.
    identities = {(item["prefix"], item["version"]) for item in best}
    if len(identities) != 1:
        return {
            "status": "ambiguous",
            "reason": "Multiple compatible environments at equal priority; choose a path",
            "selected": None,
        }
    return {"status": "selected", "reason": "Highest-priority compatible environment", "selected": best[0]}


def locate(
    repo: Path, flow: str = "discovery", explicit: dict | None = None, providers: tuple[str, ...] = ("harbor", "gym")
) -> dict:
    repo = repo.resolve()
    policy = load()
    report: dict = {
        "schema_version": 1,
        "policy_version": policy["policy_version"],
        "repo": str(repo),
        "flow": flow,
        "candidates": [],
        "selection": {},
        "search": {
            "complete": True,
            "locations": [],
            "errors": [],
            "max_candidates": MAX_CANDIDATES,
            "total_seconds": PROBE_SECONDS + PROVIDER_SECONDS * len(providers),
            "per_provider_seconds": PROVIDER_SECONDS,
            "omitted_candidates": 0,
            "incomplete_priorities": {},
        },
    }
    if not repo.is_dir():
        raise ValueError(f"Not a directory: {repo}")
    declared, requirements, requirement_errors = declarations(repo, policy)
    report["requirement_errors"] = requirement_errors
    incomplete: dict[str, list[int]] = {name: [] for name in providers}
    deadline = time.monotonic() + PROBE_SECONDS
    budgets = {name: float(PROVIDER_SECONDS) for name in providers}
    candidates: dict[tuple, dict] = {}

    def add(name: str, python: str | None, cli: str | None, source: str, priority: int) -> None:
        cli = absolute(cli, repo) if cli else None
        python = absolute(python, repo) if python else (launcher_python(cli) if cli else None)
        cli = (
            absolute(cli, repo)
            if cli
            else (str(Path(python).parent / policy["providers"][name]["cli"]) if python else None)
        )
        for key, existing in candidates.items():
            if (
                existing["provider"] == name
                and same_launcher(existing["cli"], cli)
                and same_python(existing["python"], python)
            ):
                existing["sources"].append(source)
                existing["priority"] = min(priority, existing["priority"])
                return
        candidates[(name, python, cli)] = {
            "provider": name,
            "python": python,
            "cli": cli,
            "sources": [source],
            "priority": priority,
            "requirements": requirements.get(name, []),
        }

    # Enumerate all known sources first, regardless of which candidate succeeds.
    for name in providers:
        for source, priority, entry in [
            ("explicit", 0, (explicit or {}).get(name, {})),
            ("project-declared", 1, declared.get(name, {})),
        ]:
            if entry.get("python") or entry.get("cli"):
                add(name, entry.get("python"), entry.get("cli"), source, priority)
        for env in (".venv", "venv"):
            python = repo / env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            report["search"]["locations"].append(str(python))
            add(name, str(python), None, "project", 2)
        for cli in path_matches(policy["providers"][name]["cli"]):
            add(name, None, cli, "PATH", 3)
    report["search"]["locations"].extend(os.get_exec_path())
    uv = shutil.which("uv")
    if uv:
        outcome = invoke([uv, "tool", "dir"], deadline)
        root = outcome.get("stdout", "").strip()
        if outcome["ok"] and Path(root).is_absolute() and "\n" not in root:
            report["search"]["locations"].append(root)
            for name in providers:
                python = Path(root) / policy["providers"][name]["package"] / "bin/python"
                add(name, str(python), None, "uv-tool", 3)
        else:
            report["search"]["errors"].append("uv tool dir failed: " + str(outcome.get("error") or "invalid path"))
            report["search"]["complete"] = False
            for name in providers:
                incomplete[name].append(3)
    ordered = sorted(
        candidates.values(),
        key=lambda item: (item["priority"], item["provider"], item["python"] or "", item["cli"] or ""),
    )
    report["search"]["omitted_candidates"] = max(0, len(ordered) - MAX_CANDIDATES)
    if len(ordered) > MAX_CANDIDATES:
        report["search"]["complete"] = False
    for candidate in ordered[MAX_CANDIDATES:]:
        incomplete[candidate["provider"]].append(candidate["priority"])
    for candidate in ordered[:MAX_CANDIDATES]:
        name = candidate["provider"]
        started = time.monotonic()
        if budgets[name] <= 0:
            report["candidates"].append(
                {
                    **candidate,
                    "status": "unresolved",
                    "reason": "provider probe budget exhausted",
                    "incomplete": True,
                }
            )
        else:
            report["candidates"].append(inspect(candidate, flow, started + budgets[name], policy))
            budgets[name] -= time.monotonic() - started
    for candidate in report["candidates"]:
        if candidate.get("incomplete"):
            incomplete[candidate["provider"]].append(candidate["priority"])
            report["search"]["complete"] = False
    report["search"]["locations"] = list(dict.fromkeys(report["search"]["locations"]))
    report["search"]["incomplete_priorities"] = {name: sorted(set(values)) for name, values in incomplete.items()}
    for name in providers:
        report["selection"][name] = select(report["candidates"], name, incomplete[name])
        if requirement_errors.get(name):
            report["selection"][name] = {
                "status": "unresolved",
                "selected": None,
                "reason": "Unsupported project requirements: " + "; ".join(requirement_errors[name]),
            }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--flow", choices=["audit", "discovery", "authoring"], default="discovery")
    parser.add_argument("--provider", choices=["harbor", "gym", "both"], default="both")
    for name in ("harbor", "gym"):
        parser.add_argument(f"--{name}-python")
        parser.add_argument(f"--{name}-cli")
    args = parser.parse_args()
    explicit = {
        name: {"python": getattr(args, name + "_python"), "cli": getattr(args, name + "_cli")}
        for name in ("harbor", "gym")
    }
    try:
        report = locate(
            args.repo, args.flow, explicit, ("harbor", "gym") if args.provider == "both" else (args.provider,)
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "selection": {}, "search": {"complete": False}}))
        return 1
    print(json.dumps(report, indent=2))
    return int(any(item["status"] != "selected" for item in report["selection"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
