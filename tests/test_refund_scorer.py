# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixture-only refund controls; no native or model execution."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals/scorer-controls"


def scorer():
    path = FIXTURE / "refund_scorer.py"
    if not path.is_file():
        raise AssertionError("refund scorer implementation is missing")
    spec = importlib.util.spec_from_file_location("fixture_refund_scorer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RefundScorerTests(unittest.TestCase):
    def test_invalid_contracts_are_health_errors(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        candidate = {"events": [], "success": True}
        malformed = [None, {}, {**contract, "scoring": {}}]
        for path, value in [
            (("scoring", "weights"), {}),
            (("scoring", "weights", "amount"), True),
            (("scoring", "weights", "unknown"), 1),
            (("scoring", "mandatory"), []),
            (("scoring", "mandatory"), ["unknown"]),
            (("sources", "policy", "approvers"), []),
            (("sources", "initial_state", "refunds"), []),
            (("sources", "order", "tax_cents"), True),
            (("sources", "order", "customer_id"), "wrong"),
            (("request", "destination"), ""),
        ]:
            broken = copy.deepcopy(contract)
            target = broken
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            malformed.append(broken)
        expected = {
            "health": "error",
            "reason": "invalid_contract",
            "checks": {},
            "raw_credit": None,
            "max_credit": None,
            "total_credit": None,
            "accepted": None,
        }
        for broken in malformed:
            with self.subTest(contract=broken):
                self.assertEqual(module.evaluate(broken, candidate), expected)
        with self.assertRaisesRegex(ValueError, "^invalid_contract$"):
            module.load_contract(FIXTURE / "missing-contract.json")

    def test_shipping_refund_policy_contradicts_fixed_task(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        candidate = json.loads((FIXTURE / "cases.json").read_text())["cases"][0]["candidate"]
        candidate["events"][0]["amount_cents"] = 8700
        candidate["events"][1]["refund"]["amount_cents"] = 8700
        candidate["events"][2]["entries"][0]["amount_cents"] = 8700
        contract["sources"]["policy"]["refund_shipping"] = True
        for arm in ("baseline", "reviewed"):
            with self.subTest(arm=arm):
                result = module.evaluate(contract, candidate, arm)
                self.assertEqual(result["reason"], "invalid_contract")
                self.assertEqual(result["health"], "error")
                self.assertIsNone(result["accepted"])
                self.assertIsNone(result["total_credit"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(ValueError, "^invalid_contract$"):
                module.load_contract(path)
        base = module.load_contract(FIXTURE / "contract.json")
        with self.assertRaisesRegex(ValueError, "^invalid_contract$"):
            module.contract_for_case(base, {"contract_variant": {"sources": {"policy": {"refund_shipping": True}}}})

    def test_declared_contract_variants(self):
        module = scorer()
        self.assertTrue(hasattr(module, "contract_for_case"), "missing contract variant API")
        base = module.load_contract(FIXTURE / "contract.json")
        original = copy.deepcopy(base)
        cases = json.loads((FIXTURE / "cases.json").read_text())["cases"]
        for case in cases:
            if "contract_variant" not in case:
                continue
            variant = module.contract_for_case(base, case)
            for arm in ("baseline", "reviewed"):
                with self.subTest(case=case["id"], arm=arm):
                    self.assertEqual(module.evaluate(variant, case["candidate"], arm), case["expected"][arm])
        self.assertEqual(base, original)
        with self.assertRaisesRegex(ValueError, "^invalid_contract$"):
            module.contract_for_case(base, {"contract_variant": {"scoring": {"mandatory": []}}})

    def test_operational_errors_are_not_semantic_rejections(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        cases = json.loads((FIXTURE / "cases.json").read_text())["cases"]
        for case in cases:
            if case["role"] != "operational":
                continue
            for arm in ("baseline", "reviewed"):
                with self.subTest(case=case["id"], arm=arm):
                    self.assertEqual(module.evaluate(contract, case["candidate"], arm), case["expected"][arm])
        expected = {
            "health": "error",
            "reason": "invalid_candidate",
            "checks": {},
            "raw_credit": None,
            "max_credit": None,
            "total_credit": None,
            "accepted": None,
        }
        malformed = [
            None,
            {},
            {"events": {}, "success": True},
            {"events": [], "success": 1},
            {"events": [None], "success": True},
            {"events": [], "success": True, "state": {}},
            {"events": [{"kind": "ledger", "entries": []}], "success": True},
        ]
        for kind, field in [("refund", "amount_cents"), ("approve", "amount_cents")]:
            candidate = copy.deepcopy(cases[0]["candidate"])
            event = next(event for event in candidate["events"] if event["kind"] == kind)
            target = event["refund"] if kind == "refund" else event
            target[field] = True
            malformed.append(candidate)
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assertEqual(module.evaluate(contract, candidate), expected)
        self.assertEqual(
            module.evaluate(contract, cases[0]["candidate"], "unrecognized"), {**expected, "reason": "unknown_grader"}
        )

    def test_missing_order_evidence_remains_unassessed(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        candidate = {"events": [{"kind": "ordering_evidence_unavailable"}], "success": True}
        expected = {
            "health": "error",
            "reason": "insufficient_evidence",
            "checks": {},
            "raw_credit": None,
            "max_credit": None,
            "total_credit": None,
            "accepted": None,
        }
        for arm in ("baseline", "reviewed"):
            self.assertEqual(module.evaluate(contract, candidate, arm), expected)

    def test_json_input_rejects_duplicate_keys_nonfinite_and_objects(self):
        module = scorer()
        original = (FIXTURE / "contract.json").read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            for text in [
                original.replace('"schema":', '"schema":"duplicate", "schema":', 1),
                original.replace('"account_status": "active"', '"account_status": NaN'),
            ]:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, "^invalid_contract$"):
                    module.load_contract(path)
        contract = module.load_contract(FIXTURE / "contract.json")
        for value in [object(), float("nan"), ("tuple",), {1: "non-string-key"}]:
            candidate = {"events": [{"kind": "change_protected", "value": {"nested": value}}], "success": True}
            result = module.evaluate(contract, candidate)
            self.assertEqual(result["reason"], "invalid_candidate")
            self.assertIsNone(result["accepted"])

    def test_contract_variants_cannot_rewrite_scoring_or_requirements(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        for override in (
            {"requirements": {"amount": "Refund anything."}},
            {"scoring": {"weights": {"amount": 4}}},
        ):
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, "^invalid_contract$"):
                module.contract_for_case(contract, {"contract_variant": override})

    def test_contract_variants_reject_non_json_values(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        with self.assertRaisesRegex(ValueError, "^invalid_contract$"):
            module.contract_for_case(
                contract, {"contract_variant": {"sources": {"initial_state": {"protected": {"extra": object()}}}}}
            )

    def test_credit_breakdown_retains_partial_credit(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        case = json.loads((FIXTURE / "cases.json").read_text())["cases"][0]
        candidate = copy.deepcopy(case["candidate"])
        candidate["success"] = False
        result = module.evaluate(contract, candidate)
        self.assertEqual(
            result,
            {
                "health": "ok",
                "reason": None,
                "checks": {
                    "completion_signal": 0,
                    "association": 1,
                    "amount": 1,
                    "destination": 1,
                    "approval_present": 1,
                    "approval_before_effect": 1,
                    "ledger": 1,
                    "protected_state": 1,
                    "fresh_effect": 1,
                },
                "raw_credit": 8,
                "max_credit": 9,
                "total_credit": 8,
                "accepted": True,
            },
        )
        candidate["events"][1]["refund"]["destination"] = "wrong"
        result = module.evaluate(contract, candidate)
        self.assertEqual(result["raw_credit"], 7)
        self.assertEqual(result["max_credit"], 9)
        self.assertEqual(result["total_credit"], 0)
        self.assertIs(result["accepted"], False)

    def test_frozen_panel_repeatability_and_reject_all_diagnostic(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        panel = json.loads((FIXTURE / "cases.json").read_text())
        self.assertEqual(len(panel["cases"]), 21)
        self.assertEqual(len({case["id"] for case in panel["cases"]}), 21)
        original = copy.deepcopy((contract, panel))
        expected_fields = {"health", "reason", "checks", "raw_credit", "max_credit", "total_credit", "accepted"}
        for case in panel["cases"]:
            selected = module.contract_for_case(contract, case)
            for arm in ("baseline", "reviewed"):
                expected = case["expected"][arm]
                self.assertEqual(set(expected), expected_fields)
                for _ in range(3):
                    with self.subTest(case=case["id"], arm=arm):
                        result = module.evaluate(selected, case["candidate"], arm)
                        self.assertEqual(result, expected)
                        self.assertTrue(all(type(value) is int for value in result["checks"].values()))
        self.assertEqual((contract, panel), original)
        # Diagnostic comparator: a reject-everything grader cannot pass a positive control.
        reference = panel["cases"][0]["expected"]["reviewed"]
        reject_all = {
            "health": "ok",
            "reason": None,
            "checks": {name: 0 for name in module.CHECKS},
            "raw_credit": 0,
            "max_credit": 9,
            "total_credit": 0,
            "accepted": False,
        }
        self.assertNotEqual(reject_all, reference)

    def test_authorization_and_preservation_neighbors(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        cases = json.loads((FIXTURE / "cases.json").read_text())["cases"]
        reference = cases[0]["candidate"]
        split = next(case["candidate"] for case in cases if case["id"] == "split-alternative")
        candidate = copy.deepcopy(split)
        candidate["events"][0], candidate["events"][1] = candidate["events"][1], candidate["events"][0]
        self.assertEqual(module.evaluate(contract, candidate)["checks"]["approval_before_effect"], 0)
        for field, value in [
            ("actor", "unauthorized"),
            ("run_id", "run-old"),
            ("amount_cents", 3000),
            ("order_id", "order-B"),
            ("customer_id", "customer-B"),
        ]:
            candidate = copy.deepcopy(reference)
            candidate["events"][0][field] = value
            result = module.evaluate(contract, candidate)
            self.assertEqual(result["checks"]["approval_present"], 0)
            self.assertEqual(result["total_credit"], 0)
        candidate = copy.deepcopy(reference)
        candidate["events"].extend(
            [
                {"kind": "change_protected", "value": {"account_status": "closed"}},
                {"kind": "change_protected", "value": contract["sources"]["initial_state"]["protected"]},
            ]
        )
        self.assertEqual(module.evaluate(contract, candidate)["checks"]["protected_state"], 0)
        candidate = copy.deepcopy(reference)
        candidate["events"][1]["refund"]["id"] = "old-refund"
        candidate["events"][2]["entries"][0]["refund_id"] = "old-refund"
        self.assertEqual(module.evaluate(contract, candidate)["checks"]["fresh_effect"], 0)
        candidate = copy.deepcopy(split)
        candidate["events"][2]["refund"]["id"] = "new-0"
        candidate["events"][3]["entries"][1]["refund_id"] = "new-0"
        self.assertEqual(module.evaluate(contract, candidate)["checks"]["fresh_effect"], 0)
        weighted = copy.deepcopy(contract)
        weighted["scoring"]["weights"]["amount"] = 4
        result = module.evaluate(weighted, reference)
        self.assertEqual((result["checks"]["amount"], result["raw_credit"], result["max_credit"]), (4, 12, 12))

    def test_reference_refund_panel(self):
        module = scorer()
        contract = module.load_contract(FIXTURE / "contract.json")
        panel = json.loads((FIXTURE / "cases.json").read_text())
        self.assertTrue(panel["cases"])
        for case in panel["cases"]:
            if "contract_variant" in case or case["role"] == "operational":
                continue
            for arm in ("baseline", "reviewed"):
                with self.subTest(case=case["id"], arm=arm):
                    self.assertEqual(module.evaluate(contract, case["candidate"], arm), case["expected"][arm])


if __name__ == "__main__":
    unittest.main()
