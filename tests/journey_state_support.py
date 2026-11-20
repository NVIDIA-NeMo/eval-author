# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib


def setup_ready(state, repo, *, journey="create", setup="gym"):
    state.initialize(repo, agent="assistant", journey=journey, setup=setup)
    return state.update(
        repo, {"prerequisites": {key: {"status": "confirmed", "source": "user reply 2"} for key in state.PREREQUISITES}}
    )


def ethos_ready(state, repo):
    (repo / "ETHOS.md").write_text("# Confirmed agent intent\n")
    return state.confirm_ethos(
        repo,
        reviewed_digest=state.ethos_digest(repo),
        source="user reviewed saved document in reply 4",
        validation="ethos-write parser passed",
    )


def expansion_start(modules, repo, *, handoff=False):
    state, expansion, _ = modules
    setup_ready(state, repo, journey="expand")
    saved = ethos_ready(state, repo)
    legacy = "Earlier requested additions"
    scope = {"legacy_context": legacy, "priorities": ["refunds"]}
    if handoff:
        audit = importlib.import_module("audit_state")
        lifecycle = audit.new_record()
        lifecycle["findings"] = {
            "report": {"path": ".eval-author/audit-coverage-report.md", "digest": "b" * 64},
            "content": {"path": ".eval-author/.working/audit/report-content.json", "digest": "c" * 64},
            "inputs": [],
            "source": "Reviewed audit findings",
        }
        lifecycle["next_action"] = {
            "kind": "improve",
            "recommendation_ids": ["R1", "R3"],
            "findings_digest": "a" * 64,
            "source": "User selected these audit recommendations",
        }
        audit.retain_handoff({"scopes": {}}, {"scopes": {"audit": {"lifecycle": lifecycle}, "expand": scope}})
    saved["scopes"]["expand"] = scope
    state.save(repo, saved)
    value = expansion.new_record(
        saved, question="Should the refund task cover both full and partial refunds?", source="User request"
    )
    return state.update(
        repo,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": value["pending"]["question"]},
        expected_revision=saved["revision"],
    )


def expansion_payload(modules, saved):
    state, _, _ = modules
    view = state.view(saved)
    return {
        "stage": "expansion_continue",
        "state": view["state"],
        "revision": saved["revision"],
        "facts": {"expansion_observations": view["expansion_observations"]},
    }


def expansion_scope_save(modules, repo, saved, value):
    return modules[0].update(
        repo,
        {
            "scopes": {"expand": {"lifecycle": value}},
            "pending_question": value["pending"]["question"] if value["pending"] else None,
        },
        expected_revision=saved["revision"],
    )


def expansion_scope_decide(modules, repo, saved, kind):
    value = copy.deepcopy(modules[1].record(saved))
    value["decision"] = {"kind": kind, "source": f"User reply: {kind}"}
    value["pending"] = (
        {"question": "Which refund scenario instead?", "source": "User requests revision"} if kind == "revise" else None
    )
    return expansion_scope_save(modules, repo, saved, value)
