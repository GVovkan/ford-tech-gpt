import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))
mock_boto3 = types.SimpleNamespace(client=lambda *args, **kwargs: object())
sys.modules.setdefault("boto3", mock_boto3)

from lambda_function import _build_warranty_simple_user_prompt, _normalize_story, lambda_handler


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestWarrantySimpleFlow(unittest.TestCase):
    def test_warranty_prompt_includes_required_and_optional_fields(self):
        prompt = _build_warranty_simple_user_prompt(
            {
                "vin": "1FTFW1E50NFA00001",
                "mileage": "73420",
                "diagnosis": "Latch spring broken.",
                "repair": "Replaced latch assembly.",
                "parts": "Latch assembly",
                "time": "0.6",
                "notes": "Verified operation.",
            }
        )
        self.assertIn("VIN: 1FTFW1E50NFA00001", prompt)
        self.assertIn("Mileage: 73420 km", prompt)
        self.assertIn("Diagnosis: Latch spring broken.", prompt)
        self.assertIn("Repair: Replaced latch assembly.", prompt)
        self.assertIn("Parts: Latch assembly", prompt)
        self.assertIn("Time: 0.6", prompt)
        self.assertIn("Notes: Verified operation.", prompt)

    def test_normalization_is_minimal_and_replaces_disallowed_terms(self):
        text = "• Customer states latch failed.\n\nNot provided\nLine two"
        normalized = _normalize_story(text)
        self.assertNotIn("Customer states", normalized)
        self.assertNotIn("Not provided", normalized)
        self.assertNotIn("•", normalized)
        self.assertIn("Customer reported", normalized)

    def test_warranty_requires_diagnosis(self):
        event = {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"mode": "Warranty"})}
        response = lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 400)

    def test_warranty_single_openai_call(self):
        event = {
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"mode": "Warranty", "diagnosis": "Found broken latch spring."}),
        }
        fake_result = {"output": [{"content": [{"type": "output_text", "text": "Root cause - broken latch spring.\\nCausal Part: Generic latch\\nLabor Op: 12650A"}]}]}

        with patch("lambda_function._get_openai_key", return_value="k"), patch(
            "lambda_function.urllib.request.urlopen", return_value=_Resp(fake_result)
        ) as mocked_urlopen:
            response = lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(mocked_urlopen.call_count, 1)
        self.assertIn("story", json.loads(response["body"]))


if __name__ == "__main__":
    unittest.main()
