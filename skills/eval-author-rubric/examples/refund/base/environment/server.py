# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import os
import socketserver
from pathlib import Path

from ledger import MAX_BYTES, Ledger, decode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path("/app/contract.json"))
    parser.add_argument("--record", type=Path, default=Path("/var/lib/refund/record.json"))
    parser.add_argument("--socket", type=Path, default=Path("/run/refund/ledger.sock"))
    args, tail = parser.parse_known_args()
    # Harbor 0.24.0 appends its keepalive command to the image entrypoint.
    if tail not in ([], ["sh", "-c", "sleep infinity"]):
        parser.error("unsupported_startup_arguments")
    ledger = Ledger(decode(args.contract.read_bytes()), args.record)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(2)
            try:
                request = decode(self.rfile.readline(MAX_BYTES + 1))
                if type(request) is not dict:
                    raise ValueError("invalid_request")
                if request == {"operation": "read"}:
                    response = {"ok": True, "value": ledger.read()}
                elif set(request) == {"operation", "event"} and request["operation"] == "act":
                    ledger.act(request["event"])
                    response = {"ok": True}
                elif set(request) == {"operation", "success"} and request["operation"] == "complete":
                    ledger.complete(request["success"])
                    response = {"ok": True}
                else:
                    raise ValueError("invalid_request")
            except (ValueError, TypeError, KeyError, RecursionError, TimeoutError):
                response = {"ok": False, "error": "invalid_request"}
            self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode())

    args.socket.parent.mkdir(parents=True, exist_ok=True)
    with socketserver.UnixStreamServer(str(args.socket), Handler) as server:
        os.chmod(args.socket, 0o666)
        server.serve_forever()


if __name__ == "__main__":
    main()
