import unittest
import sys
from pathlib import Path
from unittest.mock import patch

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
    def test_schema_definitions_exist_for_all_modes(self):
        for mode in ("diag_only", "repair_only", "diag_repair"):
            schema = _json_schema_for_section_mode(mode)
            self.assertEqual(schema["type"], "object")
            self.assertIn("required", schema)
            self.assertIn("properties", schema)

    def test_p0299_diag_repair_canada_format(self):
        data = {
            "vin": "1FTFW1E50NFA00001",
            "causalPart": "JL3Z-6C646-A",
            "laborOp": "12650D",
            "extra": "Vehicle at 85234 km with P0299.",
        }
        payload = {
            "verification": "Verified concern on road test at 85234 km. P0299 present.",
            "diagnosis": "Inspected charge air system and found CAC outlet hose disconnected.",
            "cause": "CAC outlet hose clamp not seated.",
            "repair_performed": "Reinstalled CAC hose and tightened clamp.",
            "post_repair_verification": "Cleared DTCs and road tested. No codes returned.",
        }
        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew"}):
            story = _format_warranty_story("diag_repair", payload, data)

        self.assertIn("Root cause -", story)
        self.assertIn("85234 km", story)
        self.assertIn("Causal Part: JL3Z-6C646-A", story)
        self.assertIn("Labor Op: 12650D", story)
        self.assertNotIn("Verification:", story)
        self.assertNotIn("Diagnosis:", story)
        self.assertNotRegex(story.lower(), r"\b(likely|possible|indicates|indicating|appears|concluded|suspect|seems)\b")

    def test_regular_cab_f150_blocks_rear_seat_claims(self):
        data = {
            "vin": "1FTEW1CP0NFA00002",
            "causalPart": "ABCD",
            "laborOp": "12345A",
            "repair": "Removed rear seat and inspected harness.",
        }
        payload = {
            "verification": "Verified concern from second row area.",
            "diagnosis": "Found noise at rear seat latch.",
            "cause": "Rear seat striker loose.",
        }
        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "regular cab", "cab": "regular"}):
            story = _format_warranty_story("diag_only", payload, data)

        self.assertNotIn("second row", story.lower())
        self.assertNotIn("rear seat", story.lower())

    def test_supercrew_allows_rear_seat_only_when_input_mentions(self):
        base_payload = {
            "verification": "Verified rattle from rear seat area.",
            "diagnosis": "Inspected rear seat latch operation.",
            "cause": "Rear seat latch adjustment needed.",
        }

        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew", "cab": "supercrew"}):
            story_without_input = _format_warranty_story(
                "diag_only",
                base_payload,
                {"vin": "1FTFW1E50NFA00003", "causalPart": "NA", "laborOp": "NA", "repair": "Adjusted latches."},
            )
            story_with_input = _format_warranty_story(
                "diag_only",
                base_payload,
                {
                    "vin": "1FTFW1E50NFA00003",
                    "causalPart": "NA",
                    "laborOp": "NA",
                    "repair": "Adjusted rear seat latches.",
                    "vehicle_features": ["rear seat"],
                },
            )

        self.assertNotIn("rear seat", story_without_input.lower())
        self.assertIn("rear seat", story_with_input.lower())

    def test_repair_only_uses_wsm_style_and_no_invented_torque_values(self):
        payload = {
            "verification": "Verified concern at 12345 km.",
            "repair_performed": "Replaced left front lower control arm and installed new fasteners.",
            "post_repair_verification": "Road tested and confirmed concern resolved.",
        }
        data = {
            "vin": "1FTFW1E50NFA00004",
            "causalPart": "LCA-100",
            "laborOp": "20421A",
        }
        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew"}):
            story = _format_warranty_story("repair_only", payload, data)

        self.assertRegex(story.lower(), r"\b(replaced|removed|installed|performed)\b")
        self.assertIn("torqued fasteners to specification", story.lower())
        self.assertNotRegex(story.lower(), r"torque\s*[:=]?\s*\d")

    def test_if_equipped_not_added_unless_in_input(self):
        payload = {
            "verification": "Verified operation.",
            "repair_performed": "Replaced switch.",
            "post_repair_verification": "Confirmed operation.",
        }

        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew"}):
            story_default = _format_warranty_story(
                "repair_only", payload, {"vin": "1FTFW1E50NFA00005", "causalPart": "X", "laborOp": "Y"}
            )
            story_allowed = _format_warranty_story(
                "repair_only",
                {
                    "verification": "Verified operation.",
                    "repair_performed": "Replaced switch if equipped.",
                    "post_repair_verification": "Confirmed operation.",
                },
                {"vin": "1FTFW1E50NFA00005", "causalPart": "X", "laborOp": "Y", "repair": "if equipped"},
            )

        self.assertNotIn("if equipped", story_default.lower())
        self.assertIn("if equipped", story_allowed.lower())

    def test_validate_structured_payload_rejects_extra(self):
        payload = {
            "verification": "Verified concern.",
            "repair_performed": "Repaired terminal and sealed connector.",
            "post_repair_verification": "Confirmed operation restored.",
            "diagnosis": "extra",
        }
        ok, errs = _validate_structured_payload("repair_only", payload)
        self.assertFalse(ok)
        self.assertTrue(any("Unexpected keys" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
