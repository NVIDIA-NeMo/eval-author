# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import re
from datetime import date, datetime
from typing import Any, cast

# Independently implemented from Gym email.py at this revision, without held-out input access.
UPSTREAM_REVISION = "0c5cfb71dd8615008fc69b63cdc98c2775278aa5"
FIELDS = ("email_id", "inbox/outbox", "sender/recipient", "subject", "sent_datetime", "body")
FROZEN_TIME = "2023-11-30 23:59:00"


def schema(name: str, description: str, properties: dict[str, str], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {key: {"type": value} for key, value in properties.items()},
            "required": required,
            "additionalProperties": False,
        },
    }


TOOLS = [
    schema(
        "email_search_emails",
        "Search all query words across subject, body, and sender/recipient. Date limits are inclusive YYYY-MM-DD. Positive page_size only.",
        {"query": "string", "date_min": "string", "date_max": "string", "page": "integer", "page_size": "integer"},
        [],
    ),
    schema(
        "email_get_email_information_by_id",
        "Retrieve one field by email ID. Fields: " + ", ".join(FIELDS),
        {"email_id": "string", "field": "string"},
        ["email_id", "field"],
    ),
    schema(
        "email_reply_email",
        "Reply to an email by ID in the bounded stateful simulation.",
        {"email_id": "string", "body": "string"},
        ["email_id", "body"],
    ),
]


class Unsupported(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def unsupported(code: str, message: str) -> dict[str, Any]:
    return {"output": None, "unsupported": True, "error": {"code": code, "message": message}}


def validate_call(name: object, arguments: object) -> None:
    tool = next((tool for tool in TOOLS if tool["name"] == name), None)
    if tool is None:
        raise Unsupported("unknown_tool", "Only the three declared email tools are available.")
    if type(arguments) is not dict:
        raise Unsupported("invalid_arguments", "Arguments must be an object.")
    spec = tool["inputSchema"]
    if set(arguments) - set(spec["properties"]) or set(spec["required"]) - set(arguments):
        raise Unsupported("invalid_arguments", "Required or additional argument violation.")
    for key, value in arguments.items():
        expected = str if spec["properties"][key]["type"] == "string" else int
        if type(value) is not expected:
            raise Unsupported("invalid_arguments", f"Invalid type for {key}.")


def timestamp(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value):
        raise ValueError("Seed timestamps must be timezone-free ISO timestamps.")
    return datetime.fromisoformat(value)


def validate_rows(rows: object) -> list[dict[str, str]]:
    if type(rows) is not list:
        raise ValueError("Seed must be an array.")
    result = []
    ids = set()
    for row in rows:
        if type(row) is not dict or set(row) != set(FIELDS) or any(type(v) is not str for v in row.values()):
            raise ValueError("Each email must contain exactly the six string fields.")
        checked = cast(dict[str, str], row)
        if checked["email_id"] in ids:
            raise ValueError("Duplicate email IDs are unsupported.")
        timestamp(checked["sent_datetime"])
        ids.add(checked["email_id"])
        result.append({field: checked[field] for field in FIELDS})
    return result


class EmailBackend:
    def __init__(self, rows: object):
        self.rows = validate_rows(rows)

    def call(self, name: object, arguments: object) -> dict[str, Any]:
        try:
            validate_call(name, arguments)
            assert isinstance(arguments, dict)
            if name == "email_search_emails":
                output = self.search(**arguments)
            elif name == "email_get_email_information_by_id":
                output = self.get(**arguments)
            else:
                output = self.reply(**arguments)
            return {"output": copy.deepcopy(output), "unsupported": False}
        except Unsupported as error:
            return unsupported(error.code, str(error))

    def reply(self, email_id: str, body: str) -> str:
        if not email_id or not body:
            return "Email ID or body not provided."
        source = next((row for row in self.rows if row["email_id"] == email_id), None)
        if source is None:
            return "Email not found."
        recipient = source["sender/recipient"]
        if not recipient:
            return "Email replied successfully."
        if "@" not in recipient or "." not in recipient:
            return "Invalid recipient email address."
        try:
            new_id = str(int(max(row["email_id"] for row in self.rows)) + 1)
        except ValueError:
            raise Unsupported("id_format", "Lexicographic maximum email ID must convert to an integer.") from None
        if any(row["email_id"] == new_id for row in self.rows):
            raise Unsupported("id_collision", "The upstream ID allocation would collide with an existing email.")
        self.rows.append(
            {
                "email_id": new_id,
                "inbox/outbox": "outbox",
                "sender/recipient": recipient.lower(),
                "subject": f"RE: {source['subject']}",
                "sent_datetime": FROZEN_TIME,
                "body": body,
            }
        )
        return "Email replied successfully."

    def get(self, email_id: str, field: str) -> Any:
        if not email_id:
            return "Email ID not provided."
        if not field:
            return "Field not provided."
        row = next((row for row in self.rows if row["email_id"] == email_id), None)
        if row is None:
            return "Email not found."
        if field not in row:
            return f"Field '{field}' not found. Available fields: {', '.join(FIELDS)}"
        return {field: row[field]}

    def search(self, query: str = "", date_min: str = "", date_max: str = "", page: int = 1, page_size: int = 5) -> Any:
        if page_size <= 0:
            raise Unsupported("page_size", "Only positive page_size is supported.")
        limits: list[date | None] = []
        for value in (date_min, date_max):
            if not value:
                limits.append(None)
                continue
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                limits.append(date.fromisoformat(value))
            except ValueError:
                raise Unsupported("date_format", "Date limits must be valid YYYY-MM-DD dates.") from None
        words = query.lower().split()
        rows = [
            row
            for row in self.rows
            if all(
                word in " ".join(row[key] for key in ("subject", "body", "sender/recipient")).lower() for word in words
            )
        ]
        dates = [timestamp(row["sent_datetime"]) for row in rows]
        if len(dates) != len(set(dates)):
            raise Unsupported("date_ties", "Equal matching timestamps have unspecified upstream sort order.")
        rows.sort(key=lambda row: timestamp(row["sent_datetime"]), reverse=True)
        rows = [
            row
            for row in rows
            if (limits[0] is None or timestamp(row["sent_datetime"]).date() >= limits[0])
            and (limits[1] is None or timestamp(row["sent_datetime"]).date() <= limits[1])
        ]
        if not rows:
            return "No emails found."
        pages = (len(rows) + page_size - 1) // page_size
        page = max(1, min(page, pages))
        return {
            "emails": rows[(page - 1) * page_size : page * page_size],
            "pagination": {"total_emails": len(rows), "page": page, "page_size": page_size, "total_pages": pages},
        }
