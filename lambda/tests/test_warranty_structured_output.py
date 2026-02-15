import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))
mock_boto3 = types.SimpleNamespace(client=lambda *args, **kwargs: object())
sys.modules.setdefault("boto3", mock_boto3)

from lambda_function import _build_warranty_simple_user_prompt, lambda_handler


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
    def test_warranty_prompt_uses_exact_field_template(self):
        prompt = _build_warranty_simple_user_prompt(
            {
                "vin": "1FTFW1E50NFA00001",
                "mileage": "73420 km",
                "diagnosis": "Latch spring broken.",
                "repair": "Replaced latch assembly.",
                "parts": "Latch assembly",
                "time": "0.6",
                "comment": "Verified operation.",
                "extra": "Road test complete.",
            }
        )
        self.assertIn("Provide the fields exactly as:", prompt)
        self.assertIn("VIN: 1FTFW1E50NFA00001", prompt)
        self.assertIn("Mileage: 73420 km", prompt)
        self.assertIn("Diagnosis (mandatory): Latch spring broken.", prompt)
        self.assertIn("Repair (optional): Replaced latch assembly.", prompt)
        self.assertIn("Parts (optional): Latch assembly", prompt)
        self.assertIn("Time (optional): 0.6", prompt)
        self.assertIn("Notes (optional): Verified operation. | Road test complete.", prompt)

    def test_warranty_calls_openai_once_and_returns_raw_text(self):
        event = {
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"mode": "Warranty", "diagnosis": "Found broken latch spring."}),
        }
        model_text = "• Customer states latch failed."
        fake_result = {"output": [{"content": [{"type": "output_text", "text": model_text}]}]}

        with patch("lambda_function._get_openai_key", return_value="k"), patch(
            "lambda_function.urllib.request.urlopen", return_value=_Resp(fake_result)
        ) as mocked_urlopen:
            response = lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(mocked_urlopen.call_count, 1)
        self.assertEqual(json.loads(response["body"])["story"], model_text)


if __name__ == "__main__":
    unittest.main()
