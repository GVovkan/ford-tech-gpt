import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))
mock_boto3 = types.SimpleNamespace(client=lambda *args, **kwargs: object())
sys.modules.setdefault("boto3", mock_boto3)

from lambda_function import _build_prompt, _validate_story_output, lambda_handler


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestPromptDrivenFlow(unittest.TestCase):
    def test_build_prompt_uses_new_contract_fields(self):
        prompt = _build_prompt(
            {
                "job_type": "warranty",
                "mode": "diag_repair",
                "vehicle": "2022 F-150",
                "vin": "1FTFW1E50NFA00001",
                "mileage": "73420",
                "concern": "No crank at random",
                "codes_symptoms": "U0100",
                "diag_steps": "Checked power and ground at module",
                "repair_steps": "Repaired damaged harness section",
                "extra_instructions": "Keep concise",
            }
        )
        self.assertIn("job_type=warranty", prompt)
        self.assertIn("mode=diag_repair", prompt)
        self.assertIn("vehicle=2022 F-150", prompt)
        self.assertIn("codes_symptoms=U0100", prompt)
        self.assertIn("repair_steps=Repaired damaged harness section", prompt)

    def test_output_validator_detects_forbidden_patterns(self):
        self.assertFalse(_validate_story_output("• Invalid bullet"))
        self.assertFalse(_validate_story_output("1. Numbered"))
        self.assertFalse(_validate_story_output("Line 1\n\nLine 2"))
        self.assertFalse(_validate_story_output("Customer states concern"))
        self.assertFalse(_validate_story_output("VIN: 123"))
        self.assertFalse(_validate_story_output("Bad dash — value"))
        self.assertTrue(_validate_story_output("Verified concern and performed directed diagnostics"))

    def test_lambda_retries_on_failed_format_then_returns_valid_story(self):
        event = {
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"job_type": "warranty", "mode": "diag", "concern": "No start"}),
        }

        first = {"output": [{"content": [{"type": "output_text", "text": "• invalid"}]}]}
        second = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Verified concern and followed provided diagnostic steps with no additional assumptions",
                        }
                    ]
                }
            ]
        }

        with patch("lambda_function._get_openai_key", return_value="k"), patch(
            "lambda_function.urllib.request.urlopen", side_effect=[_Resp(first), _Resp(second)]
        ) as mocked_urlopen:
            response = lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(mocked_urlopen.call_count, 2)
        self.assertIn("Verified concern", json.loads(response["body"])["story"])


if __name__ == "__main__":
    unittest.main()
