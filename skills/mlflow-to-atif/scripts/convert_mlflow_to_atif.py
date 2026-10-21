#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Convert MLflow traces into ATIF trajectory files through the pinned NeMo Compass exporter."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from enum import Enum
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if callable(model_dump := getattr(value, "model_dump", None)):
        return _jsonable(model_dump(mode="json"))
    if callable(to_dict := getattr(value, "to_dict", None)):
        return _jsonable(to_dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    raise TypeError(f"Cannot serialize {type(value).__name__} as JSON")


def _parse_bound(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("time bounds must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _trace_loading() -> Any:
    """Import the shared loading module from the sibling eval-author skill."""
    sys.path.append(str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
    try:
        import trace_loading
    except ModuleNotFoundError as exc:
        if exc.name != "trace_loading":
            raise
        raise SystemExit(
            "Incomplete skill installation: install the sibling eval-author skill alongside mlflow-to-atif."
        ) from None
    return trace_loading


def _trace_count(payload: Any) -> int:
    """Count traces in a parsed export shape; unsupported shapes are left to the loader to reject."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("traces"), list):
        return len(payload["traces"])
    return 1


def convert_export(payload: bytes | dict[str, Any], *, agent_name: str, agent_version: str) -> list[dict[str, Any]]:
    """Load a complete MLflow export through NeMo Compass and export one ATIF trajectory per trace."""
    trace_loading = _trace_loading()
    if isinstance(payload, bytes):
        data = payload
        try:
            count = _trace_count(json.loads(data))
        except (UnicodeDecodeError, json.JSONDecodeError):
            count = sum(1 for line in data.splitlines() if line.strip())
    else:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        count = _trace_count(payload)
    with tempfile.TemporaryDirectory(prefix="mlflow-to-atif-") as staging:
        source = Path(staging) / "export.json"
        with source.open("xb") as stream:
            os.chmod(stream.name, 0o600)
            stream.write(data)
        traces = trace_loading.load_traces(source, "mlflow", max_traces=max(count, 1))
    return [trace_loading.export_atif(trace, agent_name=agent_name, agent_version=agent_version) for trace in traces]


def _load_input(path: Path) -> bytes:
    if str(path) == "-":
        return sys.stdin.buffer.read()
    return path.read_bytes()


def _validate_mlflow_transport(tracking_uri: str | None) -> None:
    """Reject remote cleartext or TLS-bypass configuration before MLflow can send credentials."""
    if os.environ.get("MLFLOW_TRACKING_INSECURE_TLS", "").strip().lower() == "true":
        raise ValueError("MLFLOW_TRACKING_INSECURE_TLS=true is not allowed for live conversion")
    if not tracking_uri:
        return
    try:
        parsed = urlparse(tracking_uri)
        hostname = parsed.hostname
    except ValueError as error:
        raise ValueError("MLflow tracking URI is invalid") from error
    if parsed.scheme.lower() != "http":
        if parsed.scheme.lower() == "https" and not hostname:
            raise ValueError("HTTPS MLflow tracking URI requires a hostname")
        return
    if not hostname:
        raise ValueError("HTTP MLflow tracking URI requires a loopback hostname")
    try:
        is_loopback = ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        raise ValueError("remote MLflow tracking URI must use HTTPS")


def _fetch_mlflow(args: argparse.Namespace) -> dict[str, Any]:
    """Fetch every page in the bounded trace query without allowing credential-bearing redirects."""
    _validate_mlflow_transport(args.tracking_uri)
    previous_redirect_setting = os.environ.get("MLFLOW_ALLOW_HTTP_REDIRECTS")
    os.environ["MLFLOW_ALLOW_HTTP_REDIRECTS"] = "false"
    try:
        if importlib.util.find_spec("mlflow") is None:
            raise RuntimeError("live conversion requires MLflow in the selected Python environment")
        mlflow = importlib.import_module("mlflow")
        lower_ms = int(args.since.timestamp() * 1000)
        upper_ms = int(args.until.timestamp() * 1000)
        client = mlflow.MlflowClient(tracking_uri=args.tracking_uri)
        traces: list[Any] = []
        page_token: str | None = None
        while True:
            page = client.search_traces(
                experiment_ids=[args.experiment_id],
                filter_string=f"timestamp_ms >= {lower_ms} AND timestamp_ms < {upper_ms}",
                page_token=page_token,
            )
            traces.extend(page)
            page_token = getattr(page, "token", None)
            if not page_token:
                break
    finally:
        if previous_redirect_setting is None:
            os.environ.pop("MLFLOW_ALLOW_HTTP_REDIRECTS", None)
        else:
            os.environ["MLFLOW_ALLOW_HTTP_REDIRECTS"] = previous_redirect_setting
    return {"traces": [_jsonable(trace) for trace in traces]}


def _validate_with_harbor(trajectories: list[dict[str, Any]]) -> None:
    if importlib.util.find_spec("harbor") is None:
        raise RuntimeError("--validate-with-harbor requires Harbor in the selected Python environment")
    module = importlib.import_module("harbor.models.trajectories")
    trajectory_model = module.Trajectory
    for trajectory in trajectories:
        trajectory_model.model_validate(trajectory)


def _write_trajectories(trajectories: list[dict[str, Any]], *, output_dir: Path, overwrite: bool) -> list[Path]:
    """Stage private files and publish them atomically, with no-clobber semantics by default."""
    safe_filename = _trace_loading().safe_filename
    output_existed = output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_existed:
        if not output_dir.is_dir():
            raise NotADirectoryError(f"ATIF output is not a directory: {output_dir}")
        if os.name == "posix" and output_dir.stat().st_mode & 0o077:
            raise PermissionError(f"ATIF output directory must not be accessible by group or other users: {output_dir}")
    else:
        output_dir.chmod(0o700)
    targets = [output_dir / safe_filename(str(item["trajectory_id"])) for item in trajectories]
    if len(targets) != len(set(targets)):
        raise ValueError("MLflow trace IDs resolve to duplicate ATIF output paths")
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing ATIF output: {existing[0]}")
    staged: list[tuple[Path, Path]] = []
    try:
        for trajectory, target in zip(trajectories, targets, strict=True):
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=output_dir)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(trajectory, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                temporary.chmod(0o600)
                staged.append((temporary, target))
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        for temporary, target in staged:
            if overwrite:
                temporary.replace(target)
            else:
                os.link(temporary, target)
                temporary.unlink()
    except Exception:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
        raise
    return targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="MLflow Trace.to_dict() JSON, or '-' for standard input")
    parser.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI"))
    parser.add_argument("--experiment-id", help="MLflow experiment ID for live conversion")
    parser.add_argument("--since", type=_parse_bound, help="Inclusive live-query lower bound")
    parser.add_argument("--until", type=_parse_bound, help="Exclusive live-query upper bound")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--agent-name", required=True, help="Stable agent name recorded in ATIF")
    parser.add_argument("--agent-version", default="unknown")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--validate-with-harbor", action="store_true")
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.input is None:
        if not args.experiment_id or args.since is None or args.until is None:
            parser.error("live conversion requires --experiment-id, --since, and --until")
        if args.since >= args.until:
            parser.error("--since must be earlier than --until")
    elif any(value is not None for value in (args.experiment_id, args.since, args.until)):
        parser.error("--input cannot be combined with --experiment-id, --since, or --until")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _validate_args(parser, args)
    try:
        payload = _load_input(args.input) if args.input is not None else _fetch_mlflow(args)
        trajectories = convert_export(
            payload,
            agent_name=args.agent_name,
            agent_version=args.agent_version,
        )
        if args.validate_with_harbor:
            _validate_with_harbor(trajectories)
        paths = _write_trajectories(trajectories, output_dir=args.output_dir, overwrite=args.overwrite)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    print(
        json.dumps(
            {
                "converted": len(paths),
                "files": [str(path) for path in paths],
                "schema_version": trajectories[0]["schema_version"],
                "validated_with_harbor": bool(args.validate_with_harbor),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
