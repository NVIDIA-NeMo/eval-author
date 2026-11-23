# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "skills/eval-author-trace-environment/examples/workplace/runtime"


def load_runtime(name: str):
    path = RUNTIME / f"{name}.py"
    assert path.is_file(), f"Missing runtime module: {path.name}"
    spec = importlib.util.spec_from_file_location(f"workplace_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed():
    return [
        {
            "email_id": "100",
            "inbox/outbox": "inbox",
            "sender/recipient": "MIRA@Example.org",
            "subject": "Gate review",
            "sent_datetime": "2023-11-29 10:00:00",
            "body": "Review the cobalt schedule.",
        },
        {
            "email_id": "101",
            "inbox/outbox": "outbox",
            "sender/recipient": "lee@example.org",
            "subject": "Schedule",
            "sent_datetime": "2023-11-30 11:00:00",
            "body": "The gate is open.",
        },
    ]


def test_reply_is_visible_and_preserves_seed(seed):
    backend = load_runtime("backend").EmailBackend(seed)
    assert backend.call("email_reply_email", {"email_id": "100", "body": "New unrecorded reply"}) == {
        "output": "Email replied successfully.",
        "unsupported": False,
    }
    expected = {
        "email_id": "102",
        "inbox/outbox": "outbox",
        "sender/recipient": "mira@example.org",
        "subject": "RE: Gate review",
        "sent_datetime": "2023-11-30 23:59:00",
        "body": "New unrecorded reply",
    }
    assert backend.rows == [*seed, expected]
    assert backend.call("email_search_emails", {"query": "unrecorded"})["output"]["emails"] == [expected]
    assert backend.call("email_get_email_information_by_id", {"email_id": "102", "field": "body"})["output"] == {
        "body": "New unrecorded reply"
    }


@pytest.mark.parametrize(
    "arguments", [{"email_id": "missing", "body": "x"}, {"email_id": "", "body": "x"}, {"email_id": "100", "body": ""}]
)
def test_reply_domain_errors_do_not_write(seed, arguments):
    backend = load_runtime("backend").EmailBackend(seed)
    result = backend.call("email_reply_email", arguments)
    assert result == {
        "output": "Email not found." if arguments["email_id"] == "missing" else "Email ID or body not provided.",
        "unsupported": False,
    }
    assert backend.rows == seed


def test_lexicographic_id_and_collision(seed):
    backend_type = load_runtime("backend").EmailBackend
    seed[0]["email_id"], seed[1]["email_id"] = "9", "10"
    backend = backend_type(seed)
    result = backend.call("email_reply_email", {"email_id": "9", "body": "reply"})
    assert result["unsupported"] and result["error"]["code"] == "id_collision"
    assert backend.rows == seed
    seed[1]["email_id"] = "20"
    backend = backend_type(seed)
    assert not backend.call("email_reply_email", {"email_id": "9", "body": "reply"})["unsupported"]
    assert backend.rows[-1]["email_id"] == "10"


@pytest.mark.parametrize(
    "name,arguments,code",
    [
        ("email_search_emails", {"page": True}, "invalid_arguments"),
        ("email_search_emails", {"query": None}, "invalid_arguments"),
        ("email_search_emails", {"page_size": 1.0}, "invalid_arguments"),
        ("email_search_emails", {"unexpected": "x"}, "invalid_arguments"),
        ("email_search_emails", {"page_size": 0}, "page_size"),
        ("email_search_emails", {"date_min": "November 29, 2023"}, "date_format"),
        ("email_search_emails", {"date_min": "2023-02-30"}, "date_format"),
        ("email_reply_email", {"body": "x"}, "invalid_arguments"),
        ("reset", {}, "unknown_tool"),
    ],
)
def test_unsupported_is_separate_and_preserves_state(seed, name, arguments, code):
    backend = load_runtime("backend").EmailBackend(seed)
    result = backend.call(name, arguments)
    assert result["output"] is None and result["unsupported"] is True
    assert result["error"]["code"] == code
    assert backend.rows == seed


def test_pagination_and_domain_read_errors(seed):
    backend = load_runtime("backend").EmailBackend(seed)
    for page, expected in [(99, 2), (-8, 1)]:
        result = backend.call("email_search_emails", {"page": page, "page_size": 1})["output"]
        assert result["pagination"] == {"total_emails": 2, "page": expected, "page_size": 1, "total_pages": 2}
        assert result["emails"] == [seed[0] if expected == 2 else seed[1]]
    for arguments, expected in [
        ({"email_id": "", "field": "body"}, "Email ID not provided."),
        ({"email_id": "100", "field": ""}, "Field not provided."),
        ({"email_id": "absent", "field": "body"}, "Email not found."),
        (
            {"email_id": "100", "field": "absent"},
            "Field 'absent' not found. Available fields: email_id, inbox/outbox, sender/recipient, subject, sent_datetime, body",
        ),
    ]:
        assert backend.call("email_get_email_information_by_id", arguments) == {
            "output": expected,
            "unsupported": False,
        }


def request(url, name, arguments):
    data = json.dumps({"name": name, "arguments": arguments}).encode()
    with urllib.request.urlopen(
        urllib.request.Request(url + "/call", data=data, headers={"Content-Type": "application/json"}), timeout=3
    ) as response:
        return json.load(response)


@pytest.fixture
def live_service(seed, tmp_path):
    assert (RUNTIME / "service.py").is_file(), "Missing HTTP service"
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed))
    state_dir = tmp_path / "private"
    process = subprocess.Popen(
        [
            sys.executable,
            str(RUNTIME / "service.py"),
            "--seed",
            str(seed_path),
            "--state-dir",
            str(state_dir),
            "--port",
            "0",
            "--host",
            "127.0.0.1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stderr is not None
    address_path = state_dir / "address.json"
    try:
        for _ in range(100):
            if address_path.exists():
                break
            assert process.poll() is None, process.stderr.read()
            time.sleep(0.02)
        else:
            pytest.fail("Service did not start")
        url = json.loads(address_path.read_text())["url"]
        yield url, state_dir, seed_path
    finally:
        process.terminate()
        process.communicate(timeout=5)


def test_http_write_export_closure_and_admin_reset(live_service, seed, tmp_path):
    url, state_dir, seed_path = live_service
    with urllib.request.urlopen(url + "/health", timeout=3) as response:
        assert json.load(response)["status"] == "ready"
    assert request(url, "email_reply_email", {"email_id": "100", "body": "Transport reply"}) == {
        "output": "Email replied successfully.",
        "unsupported": False,
    }
    assert request(url, "email_search_emails", {"query": "Transport"})["output"]["emails"][0]["email_id"] == "102"
    output = tmp_path / "export" / "state.json"
    subprocess.run(
        [sys.executable, str(RUNTIME / "service.py"), "export", "--state-dir", str(state_dir), "--output", str(output)],
        check=True,
        capture_output=True,
    )
    snapshot = json.loads(output.read_text())
    assert snapshot == {
        "access_mode": "stateful_simulation",
        "initial_state_basis": "reconstructed",
        "state_basis": "observed",
        "initial_state": seed,
        "state": [
            *seed,
            {
                "email_id": "102",
                "inbox/outbox": "outbox",
                "sender/recipient": "mira@example.org",
                "subject": "RE: Gate review",
                "sent_datetime": "2023-11-30 23:59:00",
                "body": "Transport reply",
            },
        ],
        "closed": True,
    }
    with pytest.raises(urllib.error.HTTPError) as error:
        request(url, "email_reply_email", {"email_id": "100", "body": "Too late"})
    assert error.value.code == 409
    assert json.loads((state_dir / "state.json").read_text()) == snapshot
    reset = subprocess.run(
        [sys.executable, str(RUNTIME / "service.py"), "reset", "--state-dir", str(state_dir), "--seed", str(seed_path)],
        capture_output=True,
    )
    assert reset.returncode != 0
    for path in ("/reset", "/snapshot"):
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url + path, data=b"{}"), timeout=3)
        assert error.value.code == 404


def test_client_and_mcp_proxy(live_service, tmp_path):
    url, _, _ = live_service
    client = subprocess.run(
        [
            sys.executable,
            str(RUNTIME / "client.py"),
            "--url",
            url,
            "--name",
            "email_search_emails",
            "--arguments",
            '{"query":"cobalt"}',
        ],
        capture_output=True,
        text=True,
    )
    assert client.returncode == 0, client.stderr
    assert json.loads(client.stdout)["output"]["emails"][0]["email_id"] == "100"
    audit = tmp_path / "audit.jsonl"
    messages: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "email_reply_email",
                "arguments": {"email_id": "101", "body": "Private unrecorded body"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "email_get_email_information_by_id", "arguments": {"email_id": "102", "field": "body"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "email_search_emails", "arguments": {"page_size": 0}},
        },
    ]
    process = subprocess.run(
        [sys.executable, str(RUNTIME / "mcp_server.py"), "--url", url, "--audit-log", str(audit)],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(responses) == 5
    assert responses[0]["result"]["serverInfo"]["name"] == "workplace-email-stateful-simulation"
    assert responses[1]["result"]["tools"] == load_runtime("backend").TOOLS
    assert json.loads(responses[2]["result"]["content"][0]["text"]) == "Email replied successfully."
    assert json.loads(responses[3]["result"]["content"][0]["text"]) == {"body": "Private unrecorded body"}
    assert responses[4]["result"]["isError"] is True
    assert responses[4]["result"]["structuredContent"]["unsupported"] is True
    assert "Private" not in audit.read_text()
    assert [json.loads(line) for line in audit.read_text().splitlines()] == [
        {
            "method": message["method"],
            **({"tool": message["params"]["name"]} if message["method"] == "tools/call" else {}),
        }
        for message in messages
    ]


def test_transport_failure_is_not_domain_success(tmp_path):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "email_search_emails", "arguments": {}},
    }
    result = subprocess.run(
        [sys.executable, str(RUNTIME / "mcp_server.py"), "--url", url],
        input=json.dumps(message) + "\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["error"]["code"] == -32001
    assert "result" not in response
    result = subprocess.run(
        [
            sys.executable,
            str(RUNTIME / "client.py"),
            "--url",
            url,
            "--name",
            "email_search_emails",
            "--arguments",
            "{}",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0 and not result.stdout
    assert "transport_error" in result.stderr


def test_persistent_failure_and_reset(seed, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(RUNTIME))
    service = load_runtime("service")
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed))
    directory = tmp_path / "private"
    store = service.StateStore(directory)
    store.initialize(seed_path)
    original = (directory / "state.json").read_bytes()
    with monkeypatch.context() as scoped:

        def fail_replace(*args):
            raise OSError("Injected replace failure")

        scoped.setattr(service.os, "replace", fail_replace)
        with pytest.raises(OSError):
            store.call("email_reply_email", {"email_id": "100", "body": "Not persisted"})
    assert (directory / "state.json").read_bytes() == original
    assert not list(directory.glob(".state-*"))
    store.call("email_reply_email", {"email_id": "100", "body": "Persisted"})
    reopened = service.StateStore(directory)
    assert reopened.read()["state"][-1]["body"] == "Persisted"
    output = tmp_path / "export.json"
    reopened.export(output)
    with pytest.raises(service.StateClosed):
        store.call("email_search_emails", {})
    reset = subprocess.run(
        [sys.executable, str(RUNTIME / "service.py"), "reset", "--state-dir", str(directory), "--seed", str(seed_path)],
        capture_output=True,
        text=True,
    )
    assert reset.returncode == 0, reset.stderr
    assert reopened.read()["state"] == seed
    assert reopened.read()["closed"] is False


def test_calls_and_export_serialize(live_service, tmp_path, seed):
    from concurrent.futures import ThreadPoolExecutor

    url, state_dir, _ = live_service
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: request(url, "email_reply_email", {"email_id": "100", "body": f"Reply {index}"}), range(8)
            )
        )
    assert all(result == {"output": "Email replied successfully.", "unsupported": False} for result in results)
    output = tmp_path / "export.json"
    subprocess.run(
        [sys.executable, str(RUNTIME / "service.py"), "export", "--state-dir", str(state_dir), "--output", str(output)],
        check=True,
        capture_output=True,
    )
    document = json.loads(output.read_text())
    assert document["initial_state"] == seed
    assert len(document["state"]) == 10
    assert {row["email_id"] for row in document["state"]} == {str(value) for value in range(100, 110)}
    assert {row["body"] for row in document["state"][2:]} == {f"Reply {index}" for index in range(8)}


@pytest.mark.parametrize(
    "recipient,output,added",
    [
        ("invalid", "Invalid recipient email address.", False),
        ("", "Email replied successfully.", False),
        ("Some@Example.org", "Email replied successfully.", True),
    ],
)
def test_reply_recipient_handling(seed, recipient, output, added):
    seed[1]["sender/recipient"] = recipient
    backend = load_runtime("backend").EmailBackend(seed)
    assert backend.call("email_reply_email", {"email_id": "101", "body": "reply"}) == {
        "output": output,
        "unsupported": False,
    }
    assert backend.rows[:2] == seed
    assert len(backend.rows) == (3 if added else 2)
    if added:
        assert backend.rows[-1]["sender/recipient"] == "some@example.org"


def test_empty_seed_and_unsupported_ties(seed):
    backend_type = load_runtime("backend").EmailBackend
    assert backend_type([]).call("email_search_emails", {}) == {"output": "No emails found.", "unsupported": False}
    seed[1]["sent_datetime"] = seed[0]["sent_datetime"]
    result = backend_type(seed).call("email_search_emails", {})
    assert result["unsupported"] and result["error"]["code"] == "date_ties"


def test_mcp_rejects_malformed_values_without_crashing(live_service, tmp_path):
    url, _, _ = live_service
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": []}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": [], "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]
    process = subprocess.run(
        [sys.executable, str(RUNTIME / "mcp_server.py"), "--url", url, "--audit-log", str(tmp_path / "audit.jsonl")],
        input="\n".join(json.dumps(value) for value in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(responses) == 3
    assert responses[1]["error"]["code"] == -32602
    assert len(responses[2]["result"]["tools"]) == 3


def test_search_and_field_read(seed):
    backend = load_runtime("backend").EmailBackend(seed)
    assert backend.call(
        "email_search_emails", {"query": "COBALT mira", "date_min": "2023-11-29", "date_max": "2023-11-29"}
    ) == {
        "output": {"emails": [seed[0]], "pagination": {"total_emails": 1, "page": 1, "page_size": 5, "total_pages": 1}},
        "unsupported": False,
    }
    assert backend.call("email_get_email_information_by_id", {"email_id": "101", "field": "body"}) == {
        "output": {"body": "The gate is open."},
        "unsupported": False,
    }
    assert backend.call("email_search_emails", {"query": "never seen"}) == {
        "output": "No emails found.",
        "unsupported": False,
    }
