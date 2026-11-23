# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Freeze synthetic inputs, then execute the pinned upstream EmailTool."""

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus-v1"
REVISION = "0c5cfb71dd8615008fc69b63cdc98c2775278aa5"
BASE_URL = f"https://raw.githubusercontent.com/NVIDIA-NeMo/Gym/{REVISION}/"
SOURCE_PATH = "resources_servers/workplace_assistant/workplace_assistant_tools/email.py"
SOURCE_HASH = "2c710f6cc5d6d7d3b2830b4567dec8ab34bb04b2249765152bc41f2864a64ac5"
TOOLS = ["email_search_emails", "email_get_email_information_by_id", "email_reply_email"]
FIELDS = ["email_id", "inbox/outbox", "sender/recipient", "subject", "sent_datetime", "body"]
DISCLOSURE = (
    "Newly executed pinned Gym synthetic-source corpus. Not the requested Oct30 corpus. "
    "Not live user service observations. Independent synthetic seeds and session identities."
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


def call(tool: str, label: str, category: str = "supported", /, **arguments: object) -> dict:
    return {"name": f"email_{tool}", "arguments": arguments, "label": label, "category": category}


def seed(number: int) -> list[dict]:
    subjects = [
        "Cedar release",
        "Cedar budget",
        "Other notice",
        "RE: Cedar release",
        "Quarterly release",
        "Cedar notes",
    ]
    bodies = [
        "Prepare the amber checklist.",
        "Amber costs await review.",
        "No action requested.",
        "Amber followup.",
        "Release archive.",
        "Additional amber evidence.",
    ]
    return [
        dict(
            zip(
                FIELDS,
                [
                    str(number + i),
                    "outbox" if i == 3 else "inbox",
                    f"Person{i}.{number}@EXAMPLE.test",
                    subject,
                    f"2023-11-{20 + i:02d}T12:30:00",
                    bodies[i],
                ],
            )
        )
        for i, subject in enumerate(subjects)
    ]


def cases(kind: str, number: int) -> list[dict]:
    def search(label: str, **kw: object) -> dict:
        return call("search_emails", label, **kw)

    def read(label: str, **kw: object) -> dict:
        return call("get_email_information_by_id", label, **kw)

    def reply(label: str, **kw: object) -> dict:
        return call("reply_email", label, **kw)

    eid = str(number)
    if kind == "search":
        return [
            search("lowercase", query="cedar"),
            search("uppercase", query="CEDAR"),
            search("mixed-case", query="CeDaR"),
            search("all-words-across-fields", query="cedar amber"),
            search("substring", query="eda"),
            search("no-match", query="absent-unique-term"),
            search("empty-query", query=""),
            search("defaults"),
            search("first-page", page=1, page_size=2),
            search("later-page", page=2, page_size=2),
            search("high-clamp", page=999, page_size=2),
            search("low-clamp", page=-3, page_size=2),
            search("inclusive-date", date_min="2023-11-20", date_max="2023-11-20"),
            search("inclusive-range", date_min="2023-11-21", date_max="2023-11-23"),
            search("date-empty", date_min="2023-12-01"),
        ]
    if kind == "read":
        return [read(f"field-{field}", email_id=eid, field=field) for field in FIELDS] + [
            read("unknown-id", email_id="999999", field="body"),
            read("unknown-field", email_id=eid, field="missing"),
            call("get_email_information_by_id", "missing-id", "malformed", field="body"),
            call("get_email_information_by_id", "missing-field", "malformed", email_id=eid),
            read("empty-id", email_id="", field="body"),
            read("empty-field", email_id=eid, field=""),
        ]
    if kind == "reply":
        return [
            read("before-reply", email_id=eid, field="body"),
            reply("first-reply", email_id=eid, body="Acknowledged amber checklist."),
            read("new-reply-body", email_id=str(number + 6), field="body"),
            read("new-reply-subject", email_id=str(number + 6), field="subject"),
            read("new-reply-time", email_id=str(number + 6), field="sent_datetime"),
            read("new-reply-recipient", email_id=str(number + 6), field="sender/recipient"),
            reply("repeat-reply", email_id=eid, body="Acknowledged amber checklist."),
            read("repeat-new-id", email_id=str(number + 7), field="email_id"),
            reply("reply-to-reply", email_id=str(number + 6), body="Second round."),
            read("nested-subject", email_id=str(number + 8), field="subject"),
            reply("reply-outbox", email_id=str(number + 3), body="Outbox response."),
            search("after-replies", query="", page_size=20),
            reply("unknown-reply-id", email_id="999999", body="Nothing."),
            reply("empty-body", email_id=eid, body=""),
        ]
    if kind == "alternate":
        return [
            search("search-first", query="cedar"),
            reply("reply-outbox-first", email_id=str(number + 3), body="Alternate sequence."),
            search("search-new-body", query="alternate"),
            read("read-new-first", email_id=str(number + 6), field="body"),
            reply("reply-inbox-second", email_id=eid, body="Alternate second."),
            read("read-new-second", email_id=str(number + 7), field="subject"),
            search("search-after-two", query="RE:", page_size=20),
        ]
    return [
        call("search_emails", "null-query", "malformed", query=None),
        call("search_emails", "zero-page-size", "malformed", page_size=0),
        call("search_emails", "bad-date", "malformed", date_min="not-a-date"),
        call("search_emails", "string-page", "malformed", page="two"),
        call("get_email_information_by_id", "numeric-id", "malformed", email_id=number, field="body"),
        call("reply_email", "missing-body", "malformed", email_id=eid),
        call("reply_email", "missing-id", "malformed", body="No ID"),
        call("search_emails", "unknown-argument", "unsupported", unknown=True),
    ]


def freeze() -> None:
    if (CORPUS / "freeze.json").exists():
        raise ValueError("Corpus is already frozen. Do not rewrite inputs.")
    files = {}
    sessions = []
    for split, kinds, start in [
        ("development", ["search", "read", "reply", "alternate", "malformed"], 100),
        ("heldout", ["search", "read", "reply", "alternate", "malformed"], 600),
    ]:
        for index, kind in enumerate(kinds):
            number = start + index * 100
            sid = f"{split}-{kind}-v1"
            relative = f"{split}/{sid}.inputs.json"
            write_new(
                CORPUS / relative,
                {"session_id": sid, "split": split, "seed": seed(number), "calls": cases(kind, number)},
            )
            files[relative] = digest(CORPUS / relative)
            sessions.append(
                {
                    "session_id": sid,
                    "split": split,
                    "inputs": relative,
                    "seed_sha256": hashlib.sha256(json.dumps(seed(number), sort_keys=True).encode()).hexdigest(),
                }
            )
    write_new(
        CORPUS / "freeze.json",
        {
            "corpus": "gym-synthetic-source-v1",
            "disclosure": DISCLOSURE,
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "clock": "2023-11-30 23:59:00",
            "source_revision": REVISION,
            "files": files,
            "sessions": sessions,
            "agent_image_policy": "Exclude the complete conformance directory from agent images. Supply development observations only to builders.",
        },
    )
    print(
        json.dumps(
            {"freeze": str(CORPUS / "freeze.json"), "sha256": digest(CORPUS / "freeze.json"), "sessions": len(sessions)}
        )
    )


def observe(scratch: Path) -> None:
    try:
        pd = importlib.import_module("pandas")
    except ModuleNotFoundError as exc:
        raise RuntimeError("Source capture requires pandas in an isolated capture environment.") from exc

    manifest = json.loads((CORPUS / "freeze.json").read_text())
    for relative, expected in manifest["files"].items():
        if digest(CORPUS / relative) != expected:
            raise ValueError(f"Input tampering: {relative}")
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    provenance = {}
    for name, relative in [("email.py", SOURCE_PATH), ("LICENSE", "LICENSE")]:
        url = BASE_URL + relative
        path = scratch / name
        if not path.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
        provenance[name] = {"url": url, "sha256": digest(path)}
    if provenance["email.py"]["sha256"] != SOURCE_HASH:
        raise ValueError("Pinned upstream source hash mismatch")
    spec = importlib.util.spec_from_file_location("pinned_gym_email", scratch / "email.py")
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load pinned source module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if str(module.HARDCODED_CURRENT_TIME) != manifest["clock"]:
        raise ValueError("Upstream clock changed")
    schemas = [schema for schema in module.email_tool_schemas if schema["name"] in TOOLS]
    write_new(CORPUS / "schemas.json", schemas)
    observations = {}
    for item in manifest["sessions"]:
        session = json.loads((CORPUS / item["inputs"]).read_text())

        class SyntheticEmailTool(module.EmailTool):
            def reset_state(self) -> None:
                self._emails = pd.DataFrame(session["seed"], columns=FIELDS, dtype=str)

        tool = SyntheticEmailTool()
        events = []
        for index, case in enumerate(session["calls"]):
            before = tool._emails.to_dict(orient="records")
            try:
                output = getattr(tool, case["name"].removeprefix("email_"))(**case["arguments"])
                exception = None
            except Exception as error:
                output = None
                exception = {"type": type(error).__name__, "message": str(error)}
            events.append(
                {
                    "index": index,
                    **case,
                    "output": output,
                    "exception": exception,
                    "before_state": before,
                    "after_state": tool._emails.to_dict(orient="records"),
                }
            )
        relative = item["inputs"].replace(".inputs.json", ".observations.json")
        write_new(CORPUS / relative, {"session_id": session["session_id"], "split": session["split"], "events": events})
        observations[relative] = digest(CORPUS / relative)
    write_new(
        CORPUS / "provenance.json",
        {
            "disclosure": DISCLOSURE,
            "revision": REVISION,
            "source": provenance,
            "license": "Apache-2.0",
            "license_url": BASE_URL + "LICENSE",
            "upstream_source_retained_in_repository": False,
            "reset_override": "Only reset_state supplies synthetic six-field pandas rows. All operations and clock are unmodified upstream code.",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(),
            "executable": sys.executable,
            "pandas": pd.__version__,
            "freeze_sha256": digest(CORPUS / "freeze.json"),
            "schemas_sha256": digest(CORPUS / "schemas.json"),
            "observations": observations,
        },
    )
    print(json.dumps({"observed_sessions": len(observations), "provenance_sha256": digest(CORPUS / "provenance.json")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["freeze", "observe"])
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        freeze()
    elif args.scratch is None:
        parser.error("observe requires --scratch outside the repository")
    else:
        if ROOT.parents[4] == args.scratch.resolve() or ROOT.parents[4] in args.scratch.resolve().parents:
            parser.error("Raw upstream source must remain outside the repository")
        observe(args.scratch)
