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
    _preprocess_inputs,
    _generate_structured_warranty_story,
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


    def test_formatter_strips_inline_labels_and_enforces_metadata_defaults(self):
        payload = {
            "verification": "Verification: verified concern with seatback not latching",
            "diagnosis": "Diagnosis: found failed latch spring",
            "cause": "Root cause: failed latch spring",
        }
        data = {
            "vin": "1FTFW1E50NFA00006",
            "extra": "Vehicle arrived at 73420",
        }

        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew"}):
            story = _format_warranty_story("diag_only", payload, data)

        self.assertNotIn("Verification:", story)
        self.assertNotIn("Diagnosis:", story)
        self.assertNotIn("Root cause:", story)
        self.assertIn("Root cause -", story)
        self.assertIn("73420 km", story)
        self.assertIn("Causal Part: Not provided", story)
        self.assertIn("Labor Op: Not provided", story)

    def test_preprocess_extracts_concern_from_diagnosis_and_preserves_no_dtcs_in_diagnosis(self):
        data = {
            "concern": "",
            "diagnosis": "concern: Liftgate will not latch closed\nFound broken liftgate latch spring. no DTCs present.",
            "repair": "Replaced liftgate latch assembly and verified latch operation.",
        }

        processed = _preprocess_inputs(data)

        self.assertEqual(processed["concern"], "Liftgate will not latch closed")
        self.assertNotIn("concern:", processed["diagnosis"].lower())
        self.assertIn("broken liftgate latch spring", processed["diagnosis"].lower())
        self.assertIn("no dtcs", processed["diagnosis"].lower())

    def test_screenshot_like_diag_repair_story_keeps_real_content(self):
        raw_data = {
            "mode": "Warranty",
            "sectionMode": "diag_repair",
            "vin": "1FTFW1E50NFA00008",
            "diagnosis": "concern: Liftgate does not latch closed\nFound broken liftgate latch spring. no DTCs present.",
            "repair": "Replaced liftgate latch assembly and verified proper latch operation.",
            "causalPart": "ML3Z-7843150-A",
            "laborOp": "50123A",
        }
        data = _preprocess_inputs(raw_data)
        payload = {
            "verification": "Verified concern with liftgate not latching closed.",
            "diagnosis": "Found broken liftgate latch spring and no DTCs present.",
            "cause": "Broken liftgate latch spring prevented proper latch engagement.",
            "repair_performed": "Replaced liftgate latch assembly and verified proper latch operation.",
            "post_repair_verification": "Confirmed liftgate latches and unlatches correctly.",
        }

        with patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew"}):
            story = _format_warranty_story("diag_repair", payload, data)

        self.assertIn("liftgate not latching closed", story.lower())
        self.assertIn("broken liftgate latch spring", story.lower())
        self.assertIn("replaced liftgate latch assembly", story.lower())
        self.assertNotIn("Not provided", story)

    def test_validate_structured_payload_rejects_placeholder_required_fields(self):
        payload = {
            "verification": "Verified concern with latch bind.",
            "diagnosis": "Found broken latch spring at latch mechanism.",
            "cause": "Not provided",
            "repair_performed": "Replaced latch assembly and torqued fasteners to specification.",
            "post_repair_verification": "Verified latch operation and concern resolved.",
        }

        ok, errs = _validate_structured_payload("diag_repair", payload)

        self.assertFalse(ok)
        self.assertTrue(any("placeholder text: cause" in e.lower() for e in errs))

    def test_structured_generation_retries_when_model_returns_placeholder(self):
        data = {
            "mode": "Warranty",
            "sectionMode": "diag_repair",
            "vin": "1FTFW1E50NFA00007",
            "diagnosis": "Found broken latch spring.",
            "repair": "Replaced latch spring and verified operation.",
            "causalPart": "ML3Z-7843150-A",
            "laborOp": "50123A",
        }
        t = {"system_rules": "rules"}

        first = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"verification":"Verified concern.","diagnosis":"Found broken latch spring.","cause":"Not provided","repair_performed":"Replaced latch spring.","post_repair_verification":"Verified repair."}',
                        }
                    ]
                }
            ]
        }
        second = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"verification":"Verified concern with liftgate not latching.","diagnosis":"Found broken latch spring.","cause":"Broken latch spring prevented latch engagement.","repair_performed":"Replaced latch spring and torqued fasteners to specification.","post_repair_verification":"Verified latch operation and concern resolved."}',
                        }
                    ]
                }
            ]
        }

        class _Resp:
            def __init__(self, payload):
                self.payload = payload
            def read(self):
                import json
                return json.dumps(self.payload).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False

        responses = iter([_Resp(first), _Resp(second)])

        with patch("lambda_function.urllib.request.urlopen", side_effect=lambda *a, **k: next(responses)), \
             patch("lambda_function._decode_vin", return_value={"model": "f-150", "body": "pickup", "series": "supercrew"}):
            story, errs = _generate_structured_warranty_story(data, "fake-key", "gpt-4.1", t)

        self.assertFalse(errs)
        self.assertIn("Root cause - Broken latch spring prevented latch engagement.", story)
        self.assertNotIn("Not provided", story)


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
