# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Strict JSON, content identities, and write-once artifact output."""

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from metric_benchmark.models import Model


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def read_json(path: Path) -> object:
    """Read JSON without silently dropping duplicate keys or accepting NaN."""
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_number
    )


def json_bytes(value: BaseModel | dict | list) -> bytes:
    """Canonical UTF-8 JSON; benchmark models must contain JSON values."""
    if isinstance(value, BaseModel):
        # Native Harbor models need JSON conversion for paths and timestamps.
        value = value.model_dump(mode="python" if isinstance(value, Model) else "json", warnings="error")
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: BaseModel | dict | list) -> None:
    """Publish a complete artifact; identical retries succeed, different bytes fail."""
    data = json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"output already exists with different contents: {path}") from None
    finally:
        temporary.unlink(missing_ok=True)


def cli(parser: argparse.ArgumentParser, operation: Callable[[argparse.Namespace], None]) -> None:
    """Run a stage with machine-readable files and a nonzero exit on failure."""
    args = parser.parse_args()
    try:
        operation(args)
    except (ValueError, OSError) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")
