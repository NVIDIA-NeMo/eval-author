# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from backend import EmailBackend, validate_rows


class StateClosed(RuntimeError):
    pass


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = path.lstat()
    if path.is_symlink() or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise PermissionError("State directory must be owned by the service user with mode 0700.")


@contextlib.contextmanager
def file_lock(path: Path, *, nonblocking: bool = False) -> Iterator[None]:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(descriptor)


class StateStore:
    def __init__(self, directory: Path):
        private_directory(directory)
        self.directory = directory
        self.path = directory / "state.json"

    def initialize(self, seed_path: Path) -> None:
        rows = validate_rows(json.loads(seed_path.read_text(encoding="utf-8")))
        with file_lock(self.directory / "state.lock"):
            atomic_json(
                self.path,
                {
                    "access_mode": "stateful_simulation",
                    "initial_state_basis": "reconstructed",
                    "state_basis": "observed",
                    "initial_state": rows,
                    "state": rows,
                    "closed": False,
                },
            )

    def read(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if type(value) is not dict or set(value) != {
            "access_mode",
            "initial_state_basis",
            "state_basis",
            "initial_state",
            "state",
            "closed",
        }:
            raise ValueError("Invalid state document.")
        if (
            value["access_mode"] != "stateful_simulation"
            or value["initial_state_basis"] != "reconstructed"
            or value["state_basis"] != "observed"
            or type(value["closed"]) is not bool
        ):
            raise ValueError("Invalid state metadata.")
        validate_rows(value["initial_state"])
        validate_rows(value["state"])
        return value

    def call(self, name: object, arguments: object) -> dict[str, Any]:
        with file_lock(self.directory / "state.lock"):
            document = self.read()
            if document["closed"]:
                raise StateClosed("State has been finalized.")
            backend = EmailBackend(document["state"])
            result = backend.call(name, arguments)
            if backend.rows != document["state"]:
                document["state"] = backend.rows
                atomic_json(self.path, document)
            return result

    def export(self, output: Path) -> None:
        if output.resolve() == self.directory.resolve() or self.directory.resolve() in output.resolve().parents:
            raise ValueError("Export output must be outside the private state directory.")
        with file_lock(self.directory / "state.lock"):
            document = self.read()
            document["closed"] = True
            atomic_json(self.path, document)
            atomic_json(output, document)


class EmailHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: StateStore):
        self.store = store
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: EmailHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def respond(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.respond(404, {"error": {"code": "not_found"}})
            return
        try:
            with file_lock(self.server.store.directory / "state.lock"):
                closed = self.server.store.read()["closed"]
            self.respond(
                200, {"status": "closed" if closed else "ready", "server": "workplace-email-stateful-simulation"}
            )
        except (OSError, ValueError):
            self.respond(500, {"error": {"code": "state_failure"}})

    def do_POST(self) -> None:
        if self.path != "/call":
            self.respond(404, {"error": {"code": "not_found"}})
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_048_576:
                raise ValueError
            self.connection.settimeout(10)
            body = json.loads(self.rfile.read(length))
            if (
                type(body) is not dict
                or set(body) != {"name", "arguments"}
                or type(body["name"]) is not str
                or type(body["arguments"]) is not dict
            ):
                raise ValueError
        except (ValueError, OSError):
            self.respond(400, {"error": {"code": "invalid_request"}})
            return
        try:
            self.respond(200, self.server.store.call(body["name"], body["arguments"]))
        except StateClosed:
            self.respond(409, {"error": {"code": "state_closed"}})
        except (OSError, ValueError):
            self.respond(500, {"error": {"code": "state_failure"}})


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv.pop(0) if argv and argv[0] in {"export", "reset"} else "serve"
    parser = argparse.ArgumentParser(description="Bounded Workplace email stateful simulation")
    parser.add_argument("--state-dir", type=Path, required=True)
    if action == "export":
        parser.add_argument("--output", type=Path, required=True)
    else:
        parser.add_argument("--seed", type=Path, required=True)
    if action == "serve":
        parser.add_argument("--port", type=int, default=8080)
        parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)
    try:
        store = StateStore(args.state_dir)
        if action == "export":
            store.export(args.output)
        else:
            with file_lock(args.state_dir / "server.lock", nonblocking=True):
                if action == "reset":
                    store.initialize(args.seed)
                else:
                    with EmailHTTPServer((args.host, args.port), store) as server:
                        store.initialize(args.seed)
                        atomic_json(args.state_dir / "address.json", {"url": f"http://127.0.0.1:{server.server_port}"})
                        server.serve_forever()
        return 0
    except (OSError, ValueError) as error:
        print(f"Workplace service failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
