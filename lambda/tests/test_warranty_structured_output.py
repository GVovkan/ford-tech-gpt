import unittest
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import types

mock_boto3 = types.SimpleNamespace(client=lambda *args, **kwargs: object())
sys.modules.setdefault("boto3", mock_boto3)

from lambda_function import (
    _validate_structured_payload,
    _format_warranty_story,
    _json_schema_for_section_mode,
)


class TestWarrantyStructuredOutput(unittest.TestCase):
    def test_diag_only_schema_validation(self):
        payload = {
            "verification": "Verified concern is present.",
            "diagnosis": "Performed pinpoint test and found open circuit.",
            "cause": "Harness open at connector C123.",
        }
        ok, errs = _validate_structured_payload("diag_only", payload)
        self.assertTrue(ok)
        self.assertEqual(errs, [])

    def test_repair_only_schema_validation_rejects_extra(self):
        payload = {
            "verification": "Verified concern.",
            "repair_performed": "Repaired terminal and sealed connector.",
            "post_repair_verification": "Confirmed operation restored.",
            "diagnosis": "extra",
        }
        ok, errs = _validate_structured_payload("repair_only", payload)
        self.assertFalse(ok)
        self.assertTrue(any("Unexpected keys" in e for e in errs))

    def test_diag_repair_schema_required_keys(self):
        payload = {
            "verification": "Verified concern.",
            "diagnosis": "Found DTC set.",
            "cause": "Failed module.",
            "repair_performed": "Replaced module.",
            "post_repair_verification": "Road tested and confirmed fix.",
        }
        ok, errs = _validate_structured_payload("diag_repair", payload)
        self.assertTrue(ok)
        self.assertEqual(errs, [])

    def test_formatted_story_order_and_labels_diag_repair(self):
        data = {"extra": "", "comment": "", "coverage": ""}
        payload = {
            "verification": "verified concern present",
            "diagnosis": "found open in circuit",
            "cause": "terminal spread at connector",
            "repair_performed": "repaired terminal fit and secured connector",
            "post_repair_verification": "confirmed no dtcs and proper operation",
        }
        story = _format_warranty_story("diag_repair", payload, data)
        expected_lines = [
            "Verification:",
            "Diagnosis:",
            "Root cause:",
            "Repair performed:",
            "Post-repair verification:",
        ]
        lines = story.split("\n")
        self.assertEqual(len(lines), 5)
        for idx, prefix in enumerate(expected_lines):
            self.assertTrue(lines[idx].startswith(prefix))

    def test_formatted_story_includes_conditional_warranty_lines(self):
        data = {
            "extra": "Warranty claim requested with labor op details",
            "comment": "",
            "coverage": "",
            "causalPart": "ABCD-1234",
            "laborOp": "12345A",
        }
        payload = {
            "verification": "Verified concern present.",
            "diagnosis": "Found failed actuator.",
            "cause": "Internal actuator fault.",
        }
        story = _format_warranty_story("diag_only", payload, data)
        self.assertIn("Causal Part: ABCD-1234", story)
        self.assertIn("Labor Op: 12345A", story)

    def test_formatted_story_filters_time_content(self):
        data = {"extra": "", "comment": "", "coverage": ""}
        payload = {
            "verification": "Verified concern after 2 hours.",
            "diagnosis": "Performed checks.",
            "cause": "Loose pin.",
        }
        story = _format_warranty_story("diag_only", payload, data)
        self.assertNotIn("hours", story.lower())

    def test_schema_definitions_exist_for_all_modes(self):
        for mode in ("diag_only", "repair_only", "diag_repair"):
            schema = _json_schema_for_section_mode(mode)
            self.assertEqual(schema["type"], "object")
            self.assertIn("required", schema)
            self.assertIn("properties", schema)


if __name__ == "__main__":
    unittest.main()
