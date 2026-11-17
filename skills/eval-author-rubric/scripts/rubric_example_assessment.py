# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read a selected Harbor task without executing its code.

Select ``example_assessment = {"task_config_id": ID}`` beside ordinary artifact
records. Optional ``review_ids`` selects recorded source decisions. The explicit
``reference_scope = "task_directory"`` also permits bounded absence checks for
required references under the task directory. Omitted scope leaves unselected
references unresolved. New bytes without selected identities remain unresolved.
Paths derive from task.toml, Dockerfile COPY sources, the single literal Python verifier
command, and Python imports/literal file reads. Every read uses the shared
bounded reader and selection-wide access policy.

The adapter emits all populated criteria and their highest-anchor gates. It
supports task, route and dependency inventory plus declared configuration
companions. Run this script with --help to prepare a source-only Oracle/NOP
declaration for the supported refund task. The model choice retains missing
future-model settings as omissions. Recorded reviews retain exact source citations but cannot authenticate
human authority or independence. Unsupported checks remain explicit gaps.
No selected code is imported or executed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
import sys
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))

from rubric_configuration import compare_sourced_config
from rubric_identity import (
    MAX_FILES,
    MAX_SOURCE_BYTES,
    MAX_TOTAL_BYTES,
    SelectedAccess,
    SelectedReadError,
    identify,
    read_charge,
    read_selected,
)
from rubric_source_reviews import project as project_reviews

PREFIX = "provenance."
NAMES = ("task-artifact-access", "grading-path", "grading-dependency-access")
BOUNDARY = (
    "Read-only reviewer inspection of selected local Harbor sources. "
    "No native execution, solver permissions, grading correctness, human review or independence is established."
)


class Sources:
    def __init__(self, root: Path, records: list[dict[str, Any]]) -> None:
        self.root = root
        self.access = SelectedAccess(records)
        self.cache: dict[str, tuple[bytes | None, str | None]] = {}
        self.remaining = MAX_TOTAL_BYTES
        self.references: list[dict[str, Any]] = []
        self.destinations: dict[str, str] = {}
        self.closure: dict[str, list[dict[str, Any]]] = {"build": [], "calls": [], "paths": [], "settings": []}
        self.closure["branches"] = []
        self.generated: dict[str, dict[str, Any]] = {}
        self.reference_root: PurePosixPath | None = None
        self.identity = identify(root, records, hasher=self.digest, access=self.access)
        self.records = {
            record["path"]: record
            for record in sorted(self.identity["records"], key=lambda record: record["id"], reverse=True)
        }
        self.diagnostics = self.identity["diagnostics"]

    def read(self, path: str) -> tuple[bytes | None, str | None]:
        reason = self.access.reason(path)
        if reason:
            return None, reason
        if path not in self.cache:
            if len(self.cache) >= MAX_FILES:
                return None, "file_limit"
            limit = min(MAX_SOURCE_BYTES, self.remaining)
            raw, reason = read_selected(self.root, path, limit)
            self.remaining -= read_charge(raw, reason, limit)
            self.cache[path] = raw, reason
        return self.cache[path]

    def digest(self, path: Path) -> str:
        raw, reason = self.read(path.relative_to(self.root).as_posix())
        if raw is None or reason:
            raise SelectedReadError(reason)
        return hashlib.sha256(raw).hexdigest()

    def observation(self, path: str) -> dict[str, Any]:
        record = self.records.get(path)
        raw = None
        state = "unresolved"
        codes: list[str] = []
        if record:
            codes = [row["code"] for row in self.diagnostics if row["subject"] == record["id"]]
            if not codes:
                raw, reason = self.read(path)
                if not reason and raw is not None:
                    state = "accessible"
            elif "identity.artifact_missing" in codes and self.read(path)[1] == "artifact_missing":
                state = "missing"
            elif (
                "identity.access_denied" in codes
                and not self.access.reason(path)
                and self.read(path)[1] == "access_denied"
            ):
                state = "denied"
        elif self.reference_root is not None and PurePosixPath(path).is_relative_to(self.reference_root):
            # Scope permits checking a required reference, not trusting new bytes
            # that lack a selected identity. Never promote an omitted source.
            _, reason = self.read(path)
            if reason == "artifact_missing":
                state = "missing"
            elif reason == "access_denied":
                state = "denied"
            codes = ["example." + (reason or "unselected_source")]
        return {
            "path": path,
            "artifact_id": record["id"] if record else None,
            "access": state,
            "digest": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "diagnostics": codes or (["example.not_selected"] if record is None else []),
        }

    def source(self, path: str) -> bytes | None:
        return self.read(path)[0] if self.observation(path)["access"] == "accessible" else None


def _relative(path: str) -> str:
    value = PurePosixPath(path)
    if not path or value.is_absolute() or ".." in value.parts or value.as_posix() != path or path == ".":
        raise ValueError("invalid example source path")
    return path


def _inventory(sources: Sources, required: set[str], gaps: set[str]) -> dict[str, Any]:
    observed = [sources.observation(path) for path in sorted(required)]
    return {
        "required": sorted(required),
        "observations": observed,
        "unresolved_material": sorted(gaps),
        "counts": {
            "required": len(observed),
            **{
                state: sum(row["access"] == state for row in observed)
                for state in ("accessible", "missing", "denied", "unresolved")
            },
        },
    }


def _docker(sources: Sources, path: str, required: set[str], gaps: set[str]) -> None:
    required.add(path)
    raw = sources.source(path)
    if raw is None:
        return
    entrypoints: list[tuple[int, list[str]]] = []
    for number, line in enumerate(raw.decode().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = shlex.split(line)
        if not parts:
            continue
        operation = parts[0].upper()
        evidence = {"source": path, "line": number, "operation": operation, "expression": line}
        if operation == "RUN":
            commands = [shlex.split(command) for command in line[4:].split("&&")]
            literal = not any(c in line for c in "$;|><`\\*?[]\n")
            for command in commands:
                if command[:2] == ["mkdir", "-p"]:
                    operands = command[2:]
                elif len(command) >= 3 and command[0] in {"chmod", "chown"}:
                    pattern = r"[0-7]{3,4}" if command[0] == "chmod" else r"[0-9]+(?::[0-9]+)?"
                    literal = literal and re.fullmatch(pattern, command[1]) is not None
                    operands = command[2:]
                else:
                    literal = False
                    operands = []
                literal = (
                    literal
                    and bool(operands)
                    and all(
                        operand.startswith("/") and ".." not in PurePosixPath(operand).parts for operand in operands
                    )
                )
            if literal:
                sources.closure["build"].append(evidence)
            else:
                gaps.add(f"{path}:{number}:unsupported-build-reference")
            continue
        if operation == "ENTRYPOINT":
            try:
                command = json.loads(line[len(parts[0]) :].strip())
            except ValueError:
                command = None
            if (
                isinstance(command, list)
                and all(isinstance(arg, str) for arg in command)
                and len(command) >= 2
                and command[0] in {"python", "python3"}
            ):
                tail = command[1:]
                while tail and tail[0] in {"-B", "-u"}:
                    tail = tail[1:]
                if len(tail) == 1 and tail[0].startswith("/"):
                    entrypoints.append((number, tail))
                    sources.closure["build"].append(evidence)
                    continue
            gaps.add(f"{path}:{number}:unsupported-build-reference")
            continue
        if operation in {"FROM", "WORKDIR", "USER", "ENV", "LABEL"}:
            sources.closure["build"].append(evidence)
            continue
        if operation != "COPY" or len(parts) < 3 or any(c in line for c in "$*?[\\") or parts[1].startswith("--"):
            gaps.add(f"{path}:{number}:unsupported-build-reference")
            continue
        for source in parts[1:-1]:
            try:
                target = (PurePosixPath(path).parent / _relative(source)).as_posix()
                required.add(target)
                destination = parts[-1]
                if destination.endswith("/"):
                    destination += PurePosixPath(source).name
                sources.destinations[destination] = target
                sources.references.append({"source": path, "target": target, "kind": "docker-copy", "line": number})
            except ValueError:
                gaps.add(f"{path}:{number}:unresolved-copy-source")
    for number, command in entrypoints:
        target = sources.destinations.get(command[0])
        if target is None:
            gaps.add(f"{path}:{number}:unresolved-entrypoint-source")
        else:
            required.add(target)
            sources.references.append({"source": path, "target": target, "kind": "service-entrypoint", "line": number})


@dataclass(frozen=True)
class PythonLaunch:
    path: str
    isolated: bool
    arguments: tuple[str, ...] = ()
    executable: str = ""
    flags: tuple[str, ...] = ()


def _entrypoint(sources: Sources, path: str, gaps: set[str], required: set[str]) -> PythonLaunch | None:
    raw = sources.source(path)
    if raw is None:
        return None
    commands = []
    for line in raw.decode().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in {"set -euo pipefail", "set -eu", "set -e"}:
            continue
        commands.append(shlex.split(line))
    if len(commands) != 1:
        gaps.add("shell-dispatch-not-a-single-literal-python-command")
        return None
    command = commands[0]
    if command and command[0] == "exec":
        command = command[1:]
    if not command or command[0] not in {
        "python",
        "python3",
        "/usr/local/bin/python",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    }:
        gaps.add("shell-dispatch-not-a-single-literal-python-command")
        return None
    executable = command[0]
    command = command[1:]
    isolated = False
    flags = []
    while command and command[0] in {"-I", "-B", "-S", "-u"}:
        flags.append(command[0])
        isolated = isolated or command[0] == "-I"
        command = command[1:]
    if not command or not command[0].startswith("/tests/") or any(c in " ".join(command) for c in "$;|&><`\\"):
        gaps.add("shell-dispatch-not-a-single-literal-python-command")
        return None
    relative = _relative(command[0].removeprefix("/tests/"))
    for argument in command[1:]:
        if argument.startswith("/tests/"):
            required.add((PurePosixPath(path).parent / _relative(argument.removeprefix("/tests/"))).as_posix())
        elif argument in sources.destinations:
            required.add(sources.destinations[argument])
    target = (PurePosixPath(path).parent / relative).as_posix()
    sources.references.append({"source": path, "target": target, "kind": "verifier-entrypoint", "line": None})
    return PythonLaunch(target, isolated, tuple(command[1:]), executable, tuple(flags))


def resolve_runtime_imports(
    sources: Sources, launch: PythonLaunch, required: set[str], gaps: set[str], native: dict[str, Any] | None
) -> None:
    from rubric_combined import runtime_observation

    native = runtime_observation(native)
    if not native:
        return
    observed = native["observations"]["root"]
    if launch.executable != observed["executable"] or set(launch.flags) != {"-I", "-S", "-B"}:
        return
    for path in sorted(required):
        raw = sources.source(path) if path.endswith(".py") else None
        if raw is None:
            continue
        tree = ast.parse(raw)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if isinstance(node, ast.ImportFrom) and not node.level else [])
            )
            if not names or any(name not in observed["origins"] for name in names):
                continue
            resolved = []
            for name in names:
                origin = observed["origins"][name]
                file = origin["file"]
                identity = observed["files"].get(file, {})
                if origin["origin"] in {"built-in", "frozen"} or identity.get("sha256"):
                    resolved.append(name)
                    sources.references.append(
                        {
                            "source": path,
                            "target": file or origin["origin"],
                            "kind": "observed-import-resolution",
                            "line": node.lineno,
                            "module": name,
                            "digest": identity.get("sha256"),
                            "scope": "Same-image probe plus source inspection; not an executed import trace.",
                        }
                    )
            if resolved == names:
                gaps.discard(f"{path}:{node.lineno}:unresolved-import")
        # The admitted observer binds this exact source and its resulting path.
        # Do not accept arbitrary sys.path calls or nested/conditional appends.
        for statement in tree.body:
            if (
                isinstance(statement, ast.Expr)
                and ast.unparse(statement) == "sys.path.append(str(Path(__file__).resolve().parent))"
            ):
                line = statement.lineno
                for reason in ("dynamic-code-or-process", "computed-file-reference", "code-dispatch-review-required"):
                    gaps.discard(f"{path}:{line}:{reason}")


def _python_dependencies(
    sources: Sources, launch: PythonLaunch, required: set[str], gaps: set[str], tests_root: PurePosixPath
) -> None:
    pending = [launch.path]
    import_root = PurePosixPath(launch.path).parent
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        required.add(path)
        raw = sources.source(path)
        if raw is None:
            continue
        try:
            tree = ast.parse(raw, filename=path)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("malformed example Python source") from exc
        _static_source(sources, path, tree, launch, required, gaps, tests_root)
        sibling_path_declared = any(
            isinstance(node, ast.Call) and ast.unparse(node) == "sys.path.append(str(Path(__file__).resolve().parent))"
            for node in ast.walk(tree)
        )
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name in {"system", "run", "Popen", "import_module"} for alias in node.names):
                    gaps.add(f"{path}:{node.lineno}:dynamic-code-or-process")
                if node.level:
                    gaps.add(f"{path}:{node.lineno}:relative-import")
                else:
                    modules = [node.module or ""]
            for module in modules:
                assert isinstance(node, (ast.Import, ast.ImportFrom))
                if launch.isolated:
                    gaps.add(f"{path}:{node.lineno}:unresolved-import")
                    if not sibling_path_declared:
                        continue
                # Startup hooks, PYTHONPATH, frozen modules and unselected files
                # can change resolution. Inventory candidates without claiming it.
                gaps.add(f"{path}:{node.lineno}:unresolved-import")
                stem = import_root / module.replace(".", "/")
                candidates = [stem.with_suffix(".py").as_posix(), (stem / "__init__.py").as_posix()]
                for parent in stem.parents:
                    if parent == import_root:
                        break
                    candidates.append((parent / "__init__.py").as_posix())
                targets = [target for target in candidates if target in sources.records]
                for target in targets:
                    sources.references.append(
                        {"source": path, "target": target, "kind": "python-import", "line": node.lineno}
                    )
                    pending.append(target)


def _service_inputs(sources: Sources, required: set[str], gaps: set[str]) -> None:
    for reference in list(sources.references):
        if reference["kind"] != "service-entrypoint":
            continue
        path = reference["target"]
        raw = sources.source(path)
        if raw is None:
            continue
        tree = ast.parse(raw)
        defaults: dict[str, str] = {}
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and (
                isinstance(call.func, ast.Name)
                and call.func.id in {"__import__", "eval", "exec", "open"}
                or isinstance(call.func, ast.Attribute)
                and call.func.attr in {"import_module", "system", "Popen", "run"}
            ):
                gaps.add(f"{path}:{call.lineno}:unresolved-service-dispatch:{ast.unparse(call)}")
        imports = {
            alias.asname or alias.name: (node.module, alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                default = next((key.value for key in node.keywords if key.arg == "default"), None)
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and isinstance(default, ast.Call)
                    and isinstance(default.func, ast.Name)
                    and imports.get(default.func.id) == ("pathlib", "Path")
                    and len(default.args) == 1
                    and isinstance(default.args[0], ast.Constant)
                    and isinstance(default.args[0].value, str)
                ):
                    defaults[node.args[0].value.removeprefix("--")] = default.args[0].value
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in imports:
                continue
            module, class_name = imports[node.func.id]
            if not module:
                continue
            local = (PurePosixPath(path).parent / (module.replace(".", "/") + ".py")).as_posix()
            if local not in sources.destinations.values():
                continue
            required.add(local)
            class_raw = sources.source(local)
            if class_raw is None:
                continue
            for cls in ast.parse(class_raw).body:
                if not isinstance(cls, ast.ClassDef) or cls.name != class_name:
                    continue
                init = next((f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == "__init__"), None)
                if init is None:
                    continue
                for parameter, argument in zip(init.args.args[1:], node.args):
                    if (
                        not isinstance(argument, ast.Attribute)
                        or not isinstance(argument.value, ast.Name)
                        or argument.value.id != "args"
                        or argument.attr not in defaults
                    ):
                        continue
                    destination = defaults[argument.attr]
                    attributes = [
                        ast.unparse(target)
                        for assignment in ast.walk(init)
                        if isinstance(assignment, ast.Assign)
                        and isinstance(assignment.value, ast.Name)
                        and assignment.value.id == parameter.arg
                        for target in assignment.targets
                        if isinstance(target, ast.Attribute)
                    ]
                    for attribute in attributes:
                        # An atomic replace publishes a file to the constructor's
                        # declared path. This identifies a producer, not its health.
                        publishes = [
                            call
                            for call in ast.walk(cls)
                            if isinstance(call, ast.Call)
                            and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "replace"
                            and len(call.args) == 1
                            and ast.unparse(call.args[0]) == attribute
                        ]
                        if not publishes:
                            continue
                        evidence = {
                            "producer": local,
                            "producer_digest": sources.records[local]["digest"],
                            "producer_line": publishes[0].lineno,
                            "declaration": path,
                            "declaration_line": node.lineno,
                        }
                        sources.generated[destination] = evidence
                        for call in ast.walk(cls):
                            if (
                                isinstance(call, ast.Call)
                                and isinstance(call.func, ast.Attribute)
                                and call.func.attr == "with_suffix"
                                and ast.unparse(call.func.value) == attribute
                                and len(call.args) == 1
                                and isinstance(call.args[0], ast.Constant)
                                and isinstance(call.args[0].value, str)
                            ):
                                sources.generated[str(PurePosixPath(destination).with_suffix(call.args[0].value))] = (
                                    evidence
                                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"read_bytes", "read_text", "open"}:
                continue
            receiver = node.func.value
            resolved = None
            if (
                isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "args"
            ):
                resolved = defaults.get(receiver.attr)
            elif (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Name)
                and imports.get(receiver.func.id) == ("pathlib", "Path")
                and len(receiver.args) == 1
                and isinstance(receiver.args[0], ast.Constant)
                and isinstance(receiver.args[0].value, str)
            ):
                resolved = receiver.args[0].value
            if resolved in sources.destinations:
                required.add(sources.destinations[resolved])
            else:
                gaps.add(f"{path}:{node.lineno}:unresolved-service-input:{resolved or ast.unparse(receiver)}")
        pending = [path]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            content = sources.source(current)
            if content is None:
                continue
            for node in ast.walk(ast.parse(content)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                )
                for module in modules:
                    assert isinstance(node, (ast.Import, ast.ImportFrom))
                    local = (PurePosixPath(path).parent / (module.replace(".", "/") + ".py")).as_posix()
                    if local in sources.destinations.values():
                        required.add(local)
                        pending.append(local)
                    elif module.split(".")[0] not in sys.stdlib_module_names:
                        gaps.add(f"{current}:{node.lineno}:unresolved-service-import:{module}")
                if not isinstance(node, ast.Call):
                    continue
                name = ast.unparse(node.func).rsplit(".", 1)[-1]
                if name in {"eval", "exec", "__import__", "import_module", "system", "Popen", "run"}:
                    gaps.add(f"{current}:{node.lineno}:unresolved-service-dispatch:{ast.unparse(node)}")
                if (
                    current == path
                    or not isinstance(node.func, ast.Attribute)
                    or name not in {"read_text", "read_bytes"}
                ):
                    continue
                receiver = node.func.value
                if (
                    isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    and receiver.func.id == "Path"
                    and len(receiver.args) == 1
                    and isinstance(receiver.args[0], ast.Constant)
                    and isinstance(receiver.args[0].value, str)
                ):
                    target = receiver.args[0].value
                    if target in sources.destinations:
                        required.add(sources.destinations[target])
                    else:
                        gaps.add(f"{current}:{node.lineno}:unresolved-service-input:{target}")
                else:
                    gaps.add(f"{current}:{node.lineno}:unresolved-service-input:{ast.unparse(receiver)}")


def _static_source(
    sources: Sources,
    path: str,
    tree: ast.Module,
    launch: PythonLaunch,
    required: set[str],
    gaps: set[str],
    tests_root: PurePosixPath,
) -> None:
    """Resolve source references, without asserting runtime module origins."""
    imports: dict[str, str] = {}
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update({alias.asname or alias.name: alias.name for alias in node.names})
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.update({alias.asname or alias.name: node.module + "." + alias.name for alias in node.names})
    dangerous = {
        "exec",
        "eval",
        "__import__",
        "import_module",
        "run",
        "Popen",
        "system",
        "getattr",
        "setattr",
        "globals",
        "locals",
        "compile",
    }
    builtins = {
        "print",
        "str",
        "int",
        "bool",
        "dict",
        "list",
        "tuple",
        "set",
        "len",
        "type",
        "all",
        "any",
        "sum",
        "sorted",
        "enumerate",
        "isinstance",
        "ValueError",
        "RuntimeError",
        "PermissionError",
        "FileNotFoundError",
        "SystemExit",
        "range",
        "zip",
        "min",
        "max",
        "open",
    }
    methods = {
        "strip",
        "items",
        "values",
        "keys",
        "get",
        "append",
        "extend",
        "encode",
        "decode",
        "read_text",
        "read_bytes",
        "write_text",
        "open",
        "mkdir",
        "unlink",
        "lstat",
        "with_suffix",
        "add_argument",
        "parse_args",
    }
    library_calls = {
        "json.loads",
        "json.dumps",
        "copy.deepcopy",
        "pathlib.Path",
        "argparse.ArgumentParser",
        "fcntl.flock",
        "os.open",
        "os.close",
        "os.geteuid",
        "os.fchown",
        "os.fstat",
        "os.fchmod",
        "os.stat",
        "os.unlink",
        "shutil.rmtree",
        "stat.S_ISDIR",
        "stat.S_ISREG",
    }
    values: dict[str, Any] = {}
    unknown = object()
    conflicting: set[str] = set()

    def bind(name: str, candidate: Any, line: int) -> None:
        if name in values and (type(values[name]) is not type(candidate) or values[name] != candidate):
            conflicting.add(name)
            gaps.add(f"{path}:{line}:conflicting-call-binding")
        values[name] = candidate

    def value(node: ast.AST | None) -> Any:
        if node is None:
            return unknown
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.Name, ast.Attribute)):
            return values.get(ast.unparse(node), unknown)
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if imports.get(name, name) == "pathlib.Path" and node.args:
                return value(node.args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr == "with_suffix" and node.args:
                parent, suffix = value(node.func.value), value(node.args[0])
                if isinstance(parent, str) and isinstance(suffix, str):
                    return str(PurePosixPath(parent).with_suffix(suffix))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = value(node.left), value(node.right)
            if isinstance(left, str) and isinstance(right, str):
                return str(PurePosixPath(left) / right)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for child in node.values:
                candidate = value(child)
                if candidate is unknown or candidate:
                    return candidate
            return None
        return unknown

    # argparse defaults and the literal launch argv are source declarations.
    arguments = dict(zip(launch.arguments[::2], launch.arguments[1::2], strict=False))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                option = node.args[0].value
                default = next((value(k.value) for k in node.keywords if k.arg == "default"), None)
                values["args." + option.removeprefix("--").replace("-", "_")] = arguments.get(option, default)
    # Carry literal arguments into imported local functions. Bind only a single
    # unambiguous path argument; other values retain their source expression.
    entry_raw = sources.source(launch.path)
    if entry_raw is not None and path != launch.path:
        entry = ast.parse(entry_raw)
        entry_options = dict(zip(launch.arguments[::2], launch.arguments[1::2], strict=False))
        entry_constants: dict[str, Any] = {}
        for assignment in entry.body:
            if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant):
                for target in assignment.targets:
                    if isinstance(target, ast.Name):
                        entry_constants[target.id] = assignment.value.value
        for call in ast.walk(entry):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in functions:
                for keyword in call.keywords:
                    if keyword.arg and isinstance(keyword.value, ast.Name) and keyword.value.id in entry_constants:
                        bind(keyword.arg, entry_constants[keyword.value.id], call.lineno)
                    elif keyword.arg and isinstance(keyword.value, ast.Constant):
                        bind(keyword.arg, keyword.value.value, call.lineno)
                    elif keyword.arg:
                        bind(keyword.arg, unknown, call.lineno)
                for parameter, argument in zip(functions[call.func.id].args.args, call.args):
                    if (
                        isinstance(argument, ast.Attribute)
                        and isinstance(argument.value, ast.Name)
                        and argument.value.id == "args"
                    ):
                        option = "--" + argument.attr.replace("_", "-")
                        if option in entry_options:
                            bind(parameter.arg, entry_options[option], call.lineno)
        for name in conflicting:
            values[name] = unknown
    for _ in range(4):
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                result = value(node.value)
                for target in targets:
                    if isinstance(target, ast.Name):
                        values[target.id] = result
    callable_names = set(functions) | set(imports) | builtins
    for function in functions.values():
        for parameter in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs):
            if parameter.arg in callable_names:
                gaps.add(f"{path}:{function.lineno}:dynamic-code-or-process")
    # A literal tuple of local functions/lambdas is a finite dispatch table.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Tuple)
            and isinstance(node.iter, (ast.Tuple, ast.List))
        ):
            for index, target in enumerate(node.target.elts):
                candidates = [
                    row.elts[index]
                    for row in node.iter.elts
                    if isinstance(row, (ast.Tuple, ast.List)) and len(row.elts) > index
                ]
                if (
                    isinstance(target, ast.Name)
                    and len(candidates) == len(node.iter.elts)
                    and candidates
                    and all(
                        isinstance(item, ast.Lambda) or isinstance(item, ast.Name) and item.id in functions
                        for item in candidates
                    )
                ):
                    callable_names.add(target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if left is not unknown and right is not unknown and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
                sources.closure["branches"].append(
                    {
                        "source": path,
                        "line": node.lineno,
                        "expression": ast.unparse(node),
                        "selected": (left == right) if isinstance(node.ops[0], ast.Eq) else (left != right),
                        "scope": "literal source argument; runtime import identity remains separate",
                    }
                )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                text = ast.unparse(target)
                if (
                    isinstance(target, ast.Name)
                    and target.id in callable_names
                    or isinstance(target, ast.Attribute)
                    and text.split(".")[0] in imports
                ):
                    gaps.add(f"{path}:{node.lineno}:dynamic-code-or-process")
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.ClassDef)):
            gaps.add(f"{path}:{node.lineno}:code-dispatch-review-required")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            gaps.add(f"{path}:{node.lineno}:dynamic-code-or-process")
        if not isinstance(node, ast.Call):
            continue
        text = ast.unparse(node.func)
        leaf = text.rsplit(".", 1)[-1]
        binding = imports.get(text, text)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            binding = imports.get(node.func.value.id, node.func.value.id) + "." + node.func.attr
        local_import = text in imports and imports[text].split(".")[0] not in sys.stdlib_module_names
        supported = (
            isinstance(node.func, ast.Name)
            and text in callable_names
            or binding in library_calls
            or isinstance(node.func, ast.Attribute)
            and leaf in methods
        )
        if leaf in dangerous or any(token in text for token in ("sys.path", "sys.modules", "__builtins__")):
            gaps.add(f"{path}:{node.lineno}:dynamic-code-or-process")
        elif not supported:
            gaps.add(f"{path}:{node.lineno}:code-dispatch-review-required")
        else:
            sources.closure["calls"].append(
                {
                    "source": path,
                    "line": node.lineno,
                    "callee": text,
                    "caller": next(
                        (
                            function.name
                            for function in sorted(functions.values(), key=lambda item: item.lineno, reverse=True)
                            if function.lineno <= node.lineno <= (function.end_lineno or function.lineno)
                        ),
                        "<module>",
                    ),
                    "binding": binding if local_import or binding != text else text,
                    "expression": ast.unparse(node),
                    "literal_arguments": {
                        key.arg: value(key.value)
                        for key in node.keywords
                        if key.arg and value(key.value) is not unknown
                    },
                    "basis": "literal-import"
                    if binding != text
                    else "local-definition"
                    if text in functions
                    else "static-call",
                }
            )
        path_node = None
        if binding in {"pathlib.Path", "open", "os.open"} and node.args:
            path_node = node.args[0]
        elif isinstance(node.func, ast.Attribute) and leaf in {
            "open",
            "read_text",
            "read_bytes",
            "write_text",
            "lstat",
            "with_suffix",
        }:
            path_node = node if leaf == "with_suffix" else node.func.value
        if path_node is not None:
            resolved = value(path_node)
            if not isinstance(resolved, str):
                gaps.add(f"{path}:{node.lineno}:computed-file-reference")
                continue
            if resolved in sources.destinations:
                target = sources.destinations[resolved]
                required.add(target)
                kind = "copied-input"
            elif resolved.startswith("/tests/"):
                target = (tests_root / _relative(resolved.removeprefix("/tests/"))).as_posix()
                required.add(target)
                kind = "test-input"
            else:
                target = resolved
                kind = "generated-state" if resolved in sources.generated else "runtime-file"
                if resolved not in sources.generated and not resolved.startswith(("/logs/", "/work/", "/workspace/")):
                    gaps.add(f"{path}:{node.lineno}:runtime-file-origin:{resolved}")
            sources.closure["paths"].append(
                {
                    "source": path,
                    "line": node.lineno,
                    "expression": ast.unparse(node),
                    "resolved": resolved,
                    "target": target,
                    "kind": kind,
                    **sources.generated.get(resolved, {}),
                }
            )


CONFIG_PATHS = {
    "/environment/cpus": "/environment/cpus",
    "/environment/memory_mb": "/environment/memory_mb",
    "/environment/storage_mb": "/environment/storage_mb",
    "/environment/network_mode": "/environment/network_mode",
    "/runtime_pins/docker_image": "/environment/docker_image",
    "/run/verifier_timeout_sec": "/verifier/timeout_sec",
    "/run/agent_timeout_sec": "/agent/timeout_sec",
    "/tool_permissions/verifier_user": "/verifier/user",
    "/tool_permissions/agent_user": "/agent/user",
    "/tool_permissions/agent_network_mode": "/agent/network_mode",
    "/tool_permissions/verifier_network_mode": "/verifier/network_mode",
    "/environment/build_timeout_sec": "/environment/build_timeout_sec",
}


def _configuration(sources: Sources, config_path: str, config: dict[str, Any]) -> dict[str, Any]:
    # The task manifest cannot identify the agent/model or the installed runtime.
    declared: dict[str, Any] = {
        path: {"state": "omitted", "reason": "field_not_declared"}
        for path in ("/agent/model", "/agent/prompt", "/agent/sampling", "/runtime_pins/harness")
    }
    record = sources.records.get(config_path)
    if record:
        for label, pointer in CONFIG_PATHS.items():
            section, field = pointer.strip("/").split("/")
            if isinstance(config.get(section), dict) and field in config[section]:
                value = config[section][field]
                numeric = field in {"cpus", "memory_mb", "storage_mb", "timeout_sec", "build_timeout_sec"}
                if numeric:
                    if type(value) not in (int, float) or not 0 < value < float("inf"):
                        raise ValueError("unsupported example configuration value")
                elif not isinstance(value, str) or not value.strip():
                    raise ValueError("unsupported example configuration value")
                declared[label] = {"artifact_id": "selected-task-configuration", "pointer": pointer}
    # The configuration helper uses a narrower selector-ID syntax than identity.
    # Alias only its internal lookup; restore the exact selected ID in provenance.
    records = [
        {**item, "id": "selected-task-configuration" if item["path"] == config_path else f"source-{index}"}
        for index, item in enumerate(sources.identity["records"])
    ]
    result = compare_sourced_config(sources.root, records, declared, {})
    for field in result["fields"].values():
        provenance = field["declared"]["provenance"]
        if provenance is not None and record is not None:
            provenance["artifact_id"] = record["id"]
    base = PurePosixPath(config_path).parent
    for path in sorted(sources.records):
        if (
            PurePosixPath(path).is_relative_to(base)
            and path.endswith(".json")
            and path in sources.destinations.values()
        ):
            raw_json = sources.source(path)
            if raw_json is not None:
                document = json.loads(raw_json)
                if isinstance(document, dict):
                    for section, value in document.items():
                        if section == "scoring" and isinstance(value, dict):
                            for key in sorted(value):
                                sources.closure["settings"].append(
                                    {
                                        "source": path,
                                        "digest": sources.records[path]["digest"],
                                        "kind": "json-setting",
                                        "pointer": "/scoring/" + key,
                                        "name": key,
                                        "claim_limit": "Declared scoring setting; semantic authority is not adjudicated.",
                                    }
                                )
        if not PurePosixPath(path).is_relative_to(base) or not path.endswith(".py"):
            continue
        raw = sources.source(path)
        if raw is None:
            continue
        tree = ast.parse(raw)
        for node in ast.walk(tree):
            kind = None
            name = None
            expression = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id.isupper():
                    kind, name, expression = "constant", node.targets[0].id, node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "add_argument" and node.args and isinstance(node.args[0], ast.Constant):
                    default = next((key.value for key in node.keywords if key.arg == "default"), None)
                    if default is not None:
                        kind, name, expression = "argument-default", node.args[0].value, default
                elif node.func.attr in {"settimeout", "chmod"}:
                    kind, name, expression = "runtime-setting", node.func.attr, node
            elif isinstance(node, ast.Compare) and any(
                isinstance(child, ast.Constant) and type(child.value) in (int, float) for child in ast.walk(node)
            ):
                kind, name, expression = "source-bound", "comparison", node
            if kind is not None and expression is not None:
                sources.closure["settings"].append(
                    {
                        "source": path,
                        "digest": sources.records[path]["digest"],
                        "line": expression.lineno,
                        "kind": kind,
                        "name": name,
                        "expression": ast.unparse(expression),
                        "claim_limit": "Source expression only; runtime overrides and enforcement are not observed.",
                    }
                )
    result["provider_hidden"] = []
    result["source_limits"] = [
        "The task does not select an evaluated agent, model, sampling or token budget. These are omitted, not provider-hidden.",
        "Provider configuration defaults and harness identity need selected provider source or a bound run declaration.",
        "Docker FROM identifies a dependency reference, not the installed interpreter or startup files.",
    ]
    return result


COMPANION_SCHEMA = "eval-author.declared-configuration.v1"
COMPANION_LIMIT = (
    "Declared configuration only. Image contents, interpreter startup, runtime overrides, "
    "Docker enforcement and observed agreement remain unverified. Independent source review is not established."
)


def _configuration_sources() -> dict[str, bytes]:
    from importlib.metadata import distribution

    dist = distribution("harbor")
    if dist.version != "0.24.0":
        raise ValueError("configuration companion requires Harbor 0.24.0")
    names = (
        "models/task/config.py",
        "models/trial/config.py",
        "models/agent/name.py",
        "models/environment_type.py",
        "agents/base.py",
        "agents/oracle.py",
        "agents/nop.py",
        "agents/factory.py",
        "cli/trials.py",
        "cli/utils.py",
        "models/task/paths.py",
        "environments/docker/docker.py",
        "environments/docker/docker_unix.py",
        "environments/docker/docker-compose-no-network.yaml",
        "environments/docker/docker-compose-build.yaml",
        "trial/trial.py",
        "verifier/verifier.py",
        "utils/scripts.py",
        "environments/base.py",
        "environments/docker/docker-compose-egress-control.yaml",
        "environments/docker/compose_env.py",
    )
    result = {}
    for name in names:
        path = Path(str(dist.locate_file("harbor/" + name)))
        raw, reason = read_selected(path.parent, path.name)
        if reason or raw is None:
            raise ValueError("configuration runtime source unavailable")
        result["harbor/" + name] = raw
    metadata = dist.read_text("METADATA")
    if metadata is None:
        raise ValueError("configuration metadata unavailable")
    result["harbor/METADATA"] = metadata.encode()
    result["rubric_native_collect.py"] = Path(__file__).with_name("rubric_native_collect.py").read_bytes()
    return result


def _configuration_manifest(
    sources: Sources,
    config_path: str,
    config: dict[str, Any],
    agent: str,
    runtime: dict[str, str],
    companion_path: str,
) -> dict[str, Any]:
    # Only installed Harbor models are executed. Selected Python is never imported.
    from harbor.models.task.config import TaskConfig
    from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TrialConfig
    from harbor.models.trial.config import TaskConfig as TrialTask
    from rubric_native_collect import native_command

    if agent not in {"oracle", "nop", "model"}:
        raise ValueError("unsupported declared agent selection")
    fields: dict[str, Any] = {}
    gaps: list[str] = []
    base = PurePosixPath(config_path).parent

    def provenance(path: str, **location: Any) -> dict[str, Any]:
        observation = sources.observation(path)
        if observation["access"] != "accessible":
            raise ValueError("configuration source unavailable")
        return {"artifact_id": observation["artifact_id"], "path": path, "digest": observation["digest"], **location}

    def model_fields(model: Any, prefix: str, supplied: dict[str, Any], source_path: str) -> None:
        for name in type(model).model_fields:
            value = getattr(model, name)
            pointer = prefix + "/" + name
            if hasattr(type(value), "model_fields"):
                model_fields(value, pointer, supplied.get(name, {}), source_path)
                continue
            if name in supplied:
                ref = provenance(config_path, pointer=pointer.removeprefix("/task"))
                state = "sourced"
            else:
                owner = next(cls for cls in type(model).__mro__ if name in cls.__dict__.get("__annotations__", {}))
                tree = ast.parse(sources.source(source_path) or b"")
                cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner.__name__)
                node = next(
                    node
                    for node in cls.body
                    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name
                )
                ref = provenance(
                    source_path, start_line=node.lineno, end_line=node.end_lineno, symbol=owner.__name__ + "." + name
                )
                state = "sourced_default"
            fields[pointer] = {
                "state": state,
                "provenance": ref,
                "claim_limit": "Source declaration; null and empty collections are explicit defaults, not runtime observations.",
            }
            if state == "sourced_default":
                from enum import Enum

                fields[pointer]["value"] = (
                    value.value if isinstance(value, Enum) else str(value) if isinstance(value, Path) else value
                )

    def reject_unknown(model: Any, supplied: dict[str, Any]) -> None:
        if supplied.keys() - type(model).model_fields.keys():
            gaps.append("Unsupported task configuration fields require source review.")
        for name, value in supplied.items():
            child = getattr(model, name, None)
            if hasattr(type(child), "model_fields") and isinstance(value, dict):
                reject_unknown(child, value)

    task = TaskConfig.model_validate(config)
    reject_unknown(task, config)
    model_fields(task, "/task", config, runtime["harbor/models/task/config.py"])
    default_task = TaskConfig()
    for name in config.keys() - {"schema_version", "metadata", "environment", "agent", "verifier", "solution"}:
        gaps.append("Unsupported top-level task selection: /" + name)
    supported = {pointer.removeprefix("/") for pointer in CONFIG_PATHS.values()} - {"environment/docker_image"}
    for section in ("environment", "agent", "verifier", "solution"):
        for name, value in config.get(section, {}).items():
            if section + "/" + name not in supported and value != getattr(
                getattr(default_task, section), name, object()
            ):
                gaps.append("Task override needs configuration closure: /" + section + "/" + name)
    fields["/task/verifier/resolved_environment_mode"] = {
        "state": "sourced_default",
        "value": task.verifier.environment_mode.value
        if task.verifier.environment_mode
        else "shared"
        if task.verifier.environment is None
        else "separate",
        "provenance": fields["/task/verifier/environment_mode"]["provenance"],
        "claim_limit": "Default implied by the selected verifier environment declaration.",
    }
    command = native_command(Path("declared-configuration"), agent if agent != "model" else "nop")
    # Decode only the collector's supported argv. New switches require an adapter update.
    options = command[command.index("start") + 1 :]
    if len(options) != 11 or options[::2] != ["-p", "-a", "--trial-name", "--trials-dir", "--env", "--delete"]:
        raise ValueError("unsupported native collector configuration")
    trial = TrialConfig(
        task=TrialTask(path=Path(base)),
        trial_name=options[5],
        trials_dir=Path(options[7]),
        agent=AgentConfig(name=options[3]),
        environment=EnvironmentConfig(type=options[9], delete=True),
    )
    model_fields(trial, "/trial", {}, runtime["harbor/models/trial/config.py"])
    command_ref = provenance(runtime["rubric_native_collect.py"], symbol="native_command")
    for key in (
        "/trial/task/path",
        "/trial/trial_name",
        "/trial/trials_dir",
        "/trial/agent/name",
        "/trial/environment/type",
        "/trial/environment/delete",
    ):
        fields[key] = {
            "state": "sourced",
            "provenance": command_ref,
            "claim_limit": "Collector declaration; no trial was launched.",
        }
    fields["/runtime_pins/harness"] = {
        "state": "sourced",
        "provenance": provenance(runtime["harbor/METADATA"]),
        "claim_limit": "Harbor 0.24.0 source metadata, not live runtime identity.",
    }
    for source_name, source_path in runtime.items():
        if not source_name.endswith(".py"):
            continue
        tree = ast.parse(sources.source(source_path) or b"")
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for function in cls.body:
                if not isinstance(function, ast.FunctionDef) or function.name != "__init__":
                    continue
                parameters = function.args.posonlyargs + function.args.args
                defaults = (
                    list(zip(parameters[-len(function.args.defaults) :], function.args.defaults))
                    if function.args.defaults
                    else []
                )
                defaults.extend(
                    (parameter, default)
                    for parameter, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
                    if default is not None
                )
                for parameter, default in defaults:
                    symbol = cls.name + ".__init__." + parameter.arg
                    fields["/provider/" + source_name + "/" + symbol] = {
                        "state": "sourced_default",
                        "provenance": provenance(
                            source_path,
                            symbol=symbol,
                            start_line=default.lineno,
                            end_line=default.end_lineno,
                            expression=ast.unparse(default),
                        ),
                        "claim_limit": "Constructor fallback source; task and trial declarations take precedence.",
                    }
        if source_name == "rubric_native_collect.py":
            for function in tree.body:
                if isinstance(function, ast.FunctionDef) and function.name in {
                    "private_environment",
                    "collect",
                    "native_command",
                }:
                    fields["/collector/" + function.name] = {
                        "state": "sourced",
                        "provenance": provenance(
                            source_path, symbol=function.name, start_line=function.lineno, end_line=function.end_lineno
                        ),
                        "claim_limit": "Declared host environment, argv and recorder budget; not executed by this companion.",
                    }
    agent_ref = provenance(runtime["harbor/agents/" + (agent if agent != "model" else "base") + ".py"])
    # These inspected built-ins have no model invocation. A release label alone
    # must not preserve that conclusion after their implementation changes.
    scripted_sources = {
        "harbor/agents/oracle.py": "9bebb320fd3909aea0fe7da174b4cb7c9b761591224d9db8010d73d1a002915c",
        "harbor/agents/nop.py": "21b6a9b32e5da3bac32c14c50af63435260ebd7455ebb693edb1005874c60157",
        "harbor/agents/factory.py": "11f843a9a9ff0187787267c72349b587c755019847470efbfcd0731d0fad6e1e",
    }
    if agent != "model" and any(
        provenance(runtime[name])["digest"] != digest for name, digest in scripted_sources.items()
    ):
        raise ValueError("unreviewed scripted agent implementation")
    for name in ("model", "prompt", "sampling", "token_budget"):
        fields["/agent/" + name] = {
            "state": "not_applicable" if agent != "model" else "omitted",
            "provenance": agent_ref,
            "claim_limit": "The selected built-in scripted agent makes no model calls."
            if agent != "model"
            else "Future model configuration is not selected. This is not provider-hidden.",
        }
    if agent == "model":
        gaps.append(
            "Select the future evaluated model, provider, prompt, sampling and token budgets from their sources."
        )
        fields["/trial/agent/name"] = {
            "state": "omitted",
            "provenance": agent_ref,
            "claim_limit": "No future model agent was selected. Scripted collector settings do not select it.",
        }
    trusted_base = Path(__file__).resolve().parent.parent / "examples/refund/base"
    task_sources = []
    for path in sorted(trusted_base.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == "task.toml":
            continue
        relative = path.relative_to(trusted_base).as_posix()
        selected_path = (base / relative).as_posix()
        raw = sources.source(selected_path)
        if raw is None or raw != path.read_bytes():
            gaps.append("Task source outside the supported refund configuration closure: " + relative)
        else:
            task_sources.append(provenance(selected_path))
    # New selected task files can introduce settings even when known files match.
    expected = {
        (base / path.relative_to(trusted_base).as_posix()).as_posix()
        for path in trusted_base.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    extras = [
        path
        for path, record in sources.records.items()
        if PurePosixPath(path).is_relative_to(base)
        and path not in expected
        and path not in runtime.values()
        and path != companion_path
        and record["role"] != "review"
    ]
    if extras:
        gaps.append("Additional task sources require configuration review.")
    task_only = Sources(sources.root, [row for row in sources.identity["records"] if row["path"] in expected])
    _docker(task_only, (base / "environment/Dockerfile").as_posix(), set(), set())
    _configuration(task_only, config_path, config)
    for index, setting in enumerate(task_only.closure["settings"]):
        if setting["source"] in {item["path"] for item in task_sources}:
            location = {key: setting[key] for key in ("line", "pointer", "expression") if key in setting}
            fields[f"/source/{index}"] = {
                "state": "sourced",
                "provenance": provenance(setting["source"], **location),
                "claim_limit": setting["claim_limit"],
            }
    for ref in task_sources:
        if ref["path"].endswith(("Dockerfile", "test.sh", "solve.sh", "instruction.md")):
            fields["/source/" + ref["path"]] = {
                "state": "sourced",
                "provenance": ref,
                "claim_limit": "Complete source declaration includes commands, permissions, image reference and input limits.",
            }
    return {
        "schema": COMPANION_SCHEMA,
        "agent": {"name": agent, "kind": "model" if agent == "model" else "scripted"},
        "scope": "refund task and reusable collector declaration",
        "fields": fields,
        "collector_argv": command if agent != "model" else None,
        "runtime_sources": [provenance(path) for _, path in sorted(runtime.items())],
        "task_sources": task_sources,
        "provider_hidden": [],
        "remaining_gaps": sorted(set(gaps)),
        "complete": not gaps,
        "boundary": COMPANION_LIMIT,
    }


def _companion(
    sources: Sources, selector: dict[str, Any], config_path: str, config: dict[str, Any]
) -> dict[str, Any] | None:
    ident = selector.get("configuration_id")
    if ident is None:
        return None
    if not isinstance(ident, str):
        raise ValueError("invalid configuration companion selection")
    records = [row for row in sources.identity["records"] if row["id"] == ident]
    if len(records) != 1:
        raise ValueError("select one configuration companion")
    raw = sources.source(records[0]["path"])
    if raw is None:
        return {"complete": False, "remaining_gaps": ["Selected configuration companion is unavailable."], "fields": {}}
    from eval_author_shared.task_evidence import loads

    document = loads(raw)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "task_config_id", "agent", "runtime_sources", "manifest"}
        or document["schema"] != COMPANION_SCHEMA
        or document["task_config_id"] != selector["task_config_id"]
    ):
        raise ValueError("invalid configuration companion")
    runtime = document["runtime_sources"]
    trusted = _configuration_sources()
    if not isinstance(runtime, dict) or runtime.keys() != trusted.keys():
        raise ValueError("incomplete configuration runtime inventory")
    for name, content in trusted.items():
        if not isinstance(runtime[name], str) or sources.source(runtime[name]) != content:
            raise ValueError("configuration runtime source changed or unavailable")
    manifest = _configuration_manifest(sources, config_path, config, document["agent"], runtime, records[0]["path"])
    if json.dumps(manifest, sort_keys=True) != json.dumps(document["manifest"], sort_keys=True):
        raise ValueError("configuration manifest does not reproduce from sources")
    return manifest


def configuration_main(argv: list[str] | None = None) -> int:
    import argparse

    from eval_author_shared.task_evidence import loads

    parser = argparse.ArgumentParser(
        description="Prepare a source-backed declared configuration companion without launching a trial."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--agent", choices=("oracle", "nop", "model"), required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private directory under --root, outside task sources when possible",
    )
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        output = args.output.absolute()
        if (
            ".." in output.parts
            or output.exists()
            or not output.is_relative_to(root)
            or any(p.is_symlink() for p in (output, *output.parents))
        ):
            raise ValueError("unsafe configuration output")
        selection = loads(args.selection.read_bytes())
        project(root, selection)
        sources = Sources(root, selection["artifacts"])
        selector = selection["example_assessment"]
        if "configuration_id" in selector:
            raise ValueError("configuration already selected")
        record = next(row for row in sources.identity["records"] if row["id"] == selector["task_config_id"])
        raw = sources.source(record["path"])
        if raw is None:
            raise ValueError("task configuration unavailable")
        trusted = _configuration_sources()
        output.mkdir(mode=0o700, parents=True)

        def add(path: Path, content: bytes) -> str:
            relative = path.relative_to(root).as_posix()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
            path.chmod(0o600)
            digest = hashlib.sha256(content).hexdigest()
            ident = "configuration:" + hashlib.sha256(relative.encode()).hexdigest()
            selection["artifacts"].append(
                {
                    "id": ident,
                    "path": relative,
                    "role": "configuration",
                    "digest": digest,
                    "revision": None,
                    "access": "available",
                    "selection_origin": "declared configuration companion",
                    "source": record["path"],
                    "evidence": [{"path": relative, "digest": digest}],
                }
            )
            return ident

        runtime = {}
        for name, content in trusted.items():
            target = output / "sources" / name
            add(target, content)
            runtime[name] = target.relative_to(root).as_posix()
        manifest = _configuration_manifest(
            Sources(root, selection["artifacts"]),
            record["path"],
            tomllib.loads(raw.decode()),
            args.agent,
            runtime,
            (output / "declared-configuration.json").relative_to(root).as_posix(),
        )
        document = {
            "schema": COMPANION_SCHEMA,
            "task_config_id": selector["task_config_id"],
            "agent": args.agent,
            "runtime_sources": runtime,
            "manifest": manifest,
        }
        selector["configuration_id"] = add(
            output / "declared-configuration.json", json.dumps(document, sort_keys=True, indent=2).encode()
        )
        (output / "selection.json").write_text(json.dumps(selection, sort_keys=True, indent=2))
        print(
            json.dumps(
                {
                    "selection": str(output / "selection.json"),
                    "complete": manifest["complete"],
                    "native_execution": False,
                }
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError, StopIteration, ImportError):
        print("Configuration preparation failed; no trial was launched.", file=sys.stderr)
        return 2


def _apply_configuration(rows: dict[str, Any], measurement: dict[str, Any]) -> None:
    observed = measurement["configuration"]
    if not observed["counts"]["declared_sourced"]:
        return
    row = rows[PREFIX + "declared-configuration"]
    boundary = (
        "Task fields, Docker declarations and Python defaults identify the inspected source settings. "
        "The evaluated agent and provider defaults are not selected. Model settings are omitted, not provider-hidden. "
        "No native observations or independent configuration review are established."
    )
    refs = [
        field["declared"]["provenance"]
        for field in observed["fields"].values()
        if field["declared"]["state"] == "sourced"
    ]
    companion = observed.get("companion")
    complete = companion is not None and companion["complete"]
    if companion:
        refs.extend(companion.get("runtime_sources", []))
        boundary = COMPANION_LIMIT
    row.update(
        score=2 if complete else 1,
        status="ASSESSED",
        assessment_state="ASSESSED",
        execution_state="PARTIAL",
        evidence_strength="E2" if complete else "E1",
    )
    row["evidence_refs"].extend(deepcopy(refs))
    row["limitations"] = [boundary]
    row["remaining_gaps"] = (
        ["Independent source review has not reproduced this manifest."]
        if complete
        else [*(companion["remaining_gaps"] if companion else []), boundary]
    )
    for check in row["check_results"]:
        if check["method"] == "deterministic":
            check["evidence"].append(
                {
                    "helper": "example_assessment.configuration",
                    "value": {"measurement": "/measurements/example_assessment/configuration", "complete": complete}
                    if companion
                    else deepcopy(observed),
                }
            )
            check["evidence_refs"].extend(deepcopy(refs))
            check["outcome"] = "PASS" if complete else "PARTIAL"
            check["evidence_boundary"] = boundary
            check["remaining_gap"] = None if complete else boundary
        elif complete and check["id"].endswith("human-review-1"):
            check["outcome"] = "NOT_APPLICABLE"
            check["remaining_gap"] = None
            check["evidence_boundary"] = (
                "No unknown declared setting remains in this supported scripted scope. " + boundary
            )


def _qualification_gates() -> dict[str, Any]:
    from rubric_evidence_scoring import canonical_definitions

    definitions = canonical_definitions()
    no_native = {
        *(PREFIX + name for name in NAMES),
        PREFIX + "declared-configuration",
        PREFIX + "evidence-revision-consistency",
        PREFIX + "comparison-fairness",
        "task-contract.scoring-authority",
    }
    return {
        ident: {
            "target_score": 3,
            "anchor": definition["scoring"]["3"],
            "check_ids": [check["id"] for check in definition["checks"]],
            "human_adjudication_required": ident.startswith("task-contract."),
            "human_review_checks": [check["id"] for check in definition["checks"] if check["method"] == "human_review"],
            "independence_required": ident
            not in {
                PREFIX + "comparison-fairness",
                PREFIX + "evidence-revision-consistency",
            },
            "native_required": ident not in no_native,
            "qualification_status": "not_established",
            "scope": "Highest-anchor prerequisites, not performed controls or evidence of applicability.",
        }
        for ident, definition in definitions.items()
    }


def project(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    if {"route", "rubric_scope"} & selection.keys():
        raise ValueError("competing example assessment scope")
    selector = selection["example_assessment"]
    if (
        not isinstance(selector, dict)
        or "task_config_id" not in selector
        or selector.keys()
        - {
            "task_config_id",
            "review_ids",
            "reference_scope",
            "native_bundle_id",
            "native_bundle_ids",
            "configuration_id",
        }
        or not isinstance(selector["task_config_id"], str)
        or ("reference_scope" in selector and selector["reference_scope"] != "task_directory")
    ):
        raise ValueError("invalid example assessment selection")
    records = selection.get("artifacts")
    if not isinstance(records, list):
        raise ValueError("invalid example artifacts")
    sources = Sources(root, records)
    if any(
        row["code"]
        in {
            "identity.record_invalid",
            "identity.reference_invalid",
            "identity.duplicate",
            "identity.selection_limit",
            "identity.digest_mismatch",
        }
        for row in sources.diagnostics
    ):
        raise ValueError("invalid example identity")
    candidates = [record for record in sources.identity["records"] if record["id"] == selector["task_config_id"]]
    if len(candidates) != 1 or PurePosixPath(candidates[0]["path"]).name != "task.toml":
        raise ValueError("select one Harbor task.toml")
    config_path = candidates[0]["path"]
    base = PurePosixPath(config_path).parent
    if selector.get("reference_scope") == "task_directory":
        sources.reference_root = base
    task_required = {config_path, (base / "instruction.md").as_posix()}
    grading_required = {config_path, (base / "tests/test.sh").as_posix()}
    task_gaps: set[str] = set()
    route_gaps: set[str] = set()
    dependency_gaps: set[str] = set()
    raw = sources.source(config_path)
    from rubric_combined import native_measurement, project_panels

    native = native_measurement(project_panels(sources, selector, base.as_posix()))
    config: dict[str, Any] = {}
    scorer = None
    if raw is not None:
        config = tomllib.loads(raw.decode())
        if (
            config.get("schema_version") not in {"1.1", "1.3"}
            or not isinstance(config.get("environment"), dict)
            or not isinstance(config.get("verifier"), dict)
        ):
            raise ValueError("unsupported Harbor task configuration")
        if config.keys() - {"schema_version", "metadata", "environment", "agent", "verifier", "solution"} or config[
            "verifier"
        ].keys() - {"timeout_sec", "user", "network_mode", "env"}:
            route_gaps.add("Unrecognized task configuration may change grading selection.")
        if config["environment"].keys() - {
            "docker_image",
            "build_timeout_sec",
            "cpus",
            "memory_mb",
            "storage_mb",
            "network_mode",
        }:
            task_gaps.add("Unrecognized environment configuration may add task inputs.")
        if not config["environment"].get("docker_image"):
            _docker(sources, (base / "environment/Dockerfile").as_posix(), task_required, task_gaps)
            _service_inputs(sources, task_required, task_gaps)
        instruction = sources.source((base / "instruction.md").as_posix())
        if instruction:
            # Only explicit read references are classified as task inputs.
            for reference in re.findall(r"(?i)\bread\s+[`\"]?(/[^\s`\".,;]+(?:\.[^\s`\".,;]+)*)", instruction.decode()):
                if reference not in sources.destinations:
                    task_gaps.add("Public read reference has no identified build source: " + reference)
        launch = _entrypoint(sources, (base / "tests/test.sh").as_posix(), route_gaps, grading_required)
        if launch:
            scorer = launch.path
            _python_dependencies(sources, launch, grading_required, dependency_gaps, base / "tests")
            resolve_runtime_imports(sources, launch, grading_required, dependency_gaps, native)
            route_gaps.update(
                gap
                for gap in dependency_gaps
                if gap.endswith(
                    ("dynamic-code-or-process", "unresolved-import", "relative-import", "code-dispatch-review-required")
                )
            )
            route_gaps.update(
                gap
                for gap in dependency_gaps
                if "computed-file-reference" in gap or "runtime-file-origin" in gap or "conflicting-call-binding" in gap
            )
            route_gaps.update(
                path + ":unresolved-route-source"
                for path in grading_required
                if sources.observation(path)["access"] != "accessible"
            )
    else:
        task_gaps.add("Task configuration is unavailable.")
        route_gaps.add("Task configuration is unavailable.")
    task_inventory = _inventory(sources, task_required, task_gaps)
    dependency_inventory = _inventory(sources, grading_required, dependency_gaps)
    failed = any(
        inventory["counts"]["missing"] or inventory["counts"]["denied"]
        for inventory in (task_inventory, dependency_inventory)
    )
    configuration = _configuration(sources, config_path, config)
    companion = _companion(sources, selector, config_path, config)
    configuration["companion"] = companion
    if companion and companion["complete"]:
        for name in ("/agent/model", "/agent/prompt", "/agent/sampling", "/runtime_pins/harness"):
            configuration["fields"][name]["declared"] = {**companion["fields"][name], "reason": None}
        configuration["counts"]["declared_sourced"] = sum(
            field["declared"]["state"] == "sourced" for field in configuration["fields"].values()
        )
        configuration["source_limits"] = [COMPANION_LIMIT]
    return {
        **({"native": native} if native is not None else {}),
        "status": "fail" if failed else "unknown" if task_gaps or route_gaps or dependency_gaps else "pass",
        "scope": "selected-harbor-source-inspection",
        "task_config": config_path,
        "task_config_accessible": raw is not None,
        "configuration": configuration,
        "source_reviews": project_reviews(sources, selector),
        "qualification_gates": _qualification_gates(),
        "references": sources.references,
        "source_closure": sources.closure,
        "task_artifacts": task_inventory,
        "grading_dependencies": dependency_inventory,
        "scorer": scorer,
        "route_sources_accessible": bool(
            scorer
            and all(
                sources.source(path) is not None for path in (config_path, (base / "tests/test.sh").as_posix(), scorer)
            )
        ),
        "route_gaps": sorted(route_gaps),
        "native_execution": native is not None,
        "human_review": False,
        "boundary": BOUNDARY
        if native is None
        else BOUNDARY + " Separately selected native controls are recorded in native; their limits remain explicit.",
    }


def _ensure_rows(rows: dict[str, Any]) -> None:
    from rubric_evidence_scoring import canonical_definitions

    for ident, definition in canonical_definitions().items():
        if ident in rows:
            continue
        gap = (
            "No qualified task-contract adjudication is consumed. Source-bound recorded decisions "
            "remain separate from human authority, independent review and native controls."
            if ident.startswith("task-contract.")
            else "No owning Anticheat control evidence is selected for this task. "
            "Source inspection cannot establish solver boundaries, credit, consumer integrity or reset behavior."
        )
        rows[ident] = {
            "item_id": ident,
            **({"source": deepcopy(definition["source"])} if "source" in definition else {}),
            "status": "NOT_ASSESSED",
            "assessment_state": "NOT_ASSESSED",
            "execution_state": "NOT_RUN",
            "score": None,
            "evidence_strength": None,
            "subject_id": None,
            "subject_revision": None,
            "measurements": [],
            "evidence_refs": [],
            "findings": [],
            "approval_refs": [],
            "method_qualification_refs": [],
            "limitations": [gap],
            "remaining_gaps": [gap],
            "next_action": definition.get("on_gap", "Supply authorized, task-bound native and review evidence."),
            "check_results": [
                {
                    **deepcopy(check),
                    "check_id": check["id"],
                    "outcome": "NOT_RUN",
                    "evidence": [],
                    "evidence_refs": [],
                    "remaining_gap": gap,
                    "evidence_boundary": BOUNDARY,
                }
                for check in definition["checks"]
            ],
        }


def apply(rows: dict[str, Any], measurement: dict[str, Any]) -> None:
    from rubric_combined import runtime_observation

    observation = runtime_observation(measurement.get("native"))
    _ensure_rows(rows)
    for row in rows.values():
        if row.get("subject_id") is None and measurement["task_config_accessible"]:
            row["subject_id"] = measurement["task_config"]
            row["subject_revision"] = next(
                "sha256:" + observation["digest"]
                for observation in measurement["task_artifacts"]["observations"]
                if observation["path"] == measurement["task_config"]
            )
    _apply_configuration(rows, measurement)
    for name in NAMES:
        ident = PREFIX + name
        row = rows[ident]
        inventory = measurement["task_artifacts" if name == "task-artifact-access" else "grading_dependencies"]
        counts = inventory["counts"]
        gaps = inventory["unresolved_material"]
        score = None
        if name == "grading-path":
            if measurement["route_sources_accessible"]:
                score = 1 if measurement["route_gaps"] else 2
        elif counts["missing"] or counts["denied"]:
            score = 0
        elif counts["accessible"]:
            score = 1 if gaps or counts["unresolved"] else 2
        if name == "grading-dependency-access" and not measurement["route_sources_accessible"]:
            score = None
        if not measurement["task_config_accessible"]:
            score = None
        refs = inventory["observations"]
        row.update(
            score=score,
            status="ASSESSED" if score is not None else "NOT_ASSESSED",
            assessment_state="ASSESSED" if score is not None else "NOT_ASSESSED",
            execution_state="PARTIAL",
            evidence_strength="E2" if refs else "E0",
            evidence_refs=deepcopy(refs),
            subject_id=measurement["task_config"],
            subject_revision="sha256:"
            + hashlib.sha256(
                json.dumps(
                    {key: value for key, value in measurement.items() if key != "source_reviews"}, sort_keys=True
                ).encode()
            ).hexdigest(),
            limitations=[BOUNDARY, *(measurement["route_gaps"] if name == "grading-path" else gaps)],
            measurements=[
                {
                    "name": key,
                    "value": value,
                    "unit": "artifacts",
                    "denominator": counts["required"],
                    "missingness": "Unselected or unverified references remain unresolved.",
                    "uncertainty": BOUNDARY,
                }
                for key, value in counts.items()
                if key != "required"
            ],
        )
        for check in row["check_results"]:
            if (
                score == 2
                and name in {"grading-path", "grading-dependency-access"}
                and observation is not None
                and check["id"] == PREFIX + name + ".human-review-1"
            ):
                check.update(
                    outcome="NOT_APPLICABLE",
                    remaining_gap=None,
                    evidence_boundary="Source inspection and admitted same-image import observations leave no unclear reference or selection. Independent review for level 3 is still required.",
                    evidence_refs=[{"measurement": "example_assessment.native", "digest": observation["index_digest"]}],
                )
                continue
            if check["method"] != "deterministic" or check["id"] == PREFIX + "grading-path.deterministic-2":
                continue
            check.update(
                outcome="PARTIAL" if score in (None, 1) else "FAIL" if score == 0 else "PASS",
                evidence=[{"helper": "example_assessment", "value": deepcopy(inventory)}],
                evidence_refs=deepcopy(refs),
                evidence_boundary=BOUNDARY,
                remaining_gap=None if score == 2 else "Selected evidence does not establish the complete criterion.",
            )
        row["remaining_gaps"] = [
            *row["limitations"],
            *[check["remaining_gap"] for check in row["check_results"] if check["remaining_gap"]],
        ]


if __name__ == "__main__":
    raise SystemExit(configuration_main())
