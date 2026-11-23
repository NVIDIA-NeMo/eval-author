# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


class TransportError(RuntimeError):
    pass


def call_http(url: str, name: str, arguments: Any) -> dict[str, Any]:
    request = urllib.request.Request(
        url.rstrip("/") + "/call",
        data=json.dumps({"name": name, "arguments": arguments}, allow_nan=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.load(response)
    except (OSError, ValueError) as error:
        raise TransportError(f"HTTP service request failed ({type(error).__name__}).") from error
    if type(value) is not dict or "output" not in value or type(value.get("unsupported")) is not bool:
        raise TransportError("Invalid HTTP service response.")
    if value["unsupported"] and (value["output"] is not None or type(value.get("error")) is not dict):
        raise TransportError("Invalid unsupported response.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the Workplace email simulation")
    parser.add_argument("--url", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--arguments", required=True)
    args = parser.parse_args()
    try:
        result = call_http(args.url, args.name, json.loads(args.arguments))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 2 if result["unsupported"] else 0
    except (TransportError, ValueError) as error:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "transport_error" if isinstance(error, TransportError) else "invalid_arguments",
                        "message": str(error),
                    }
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
