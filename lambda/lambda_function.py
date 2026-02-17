import json
import os
import re
import boto3
import urllib.request
import urllib.error

ssm = boto3.client("ssm")
OPENAI_URL = "https://api.openai.com/v1/responses"
MAX_GENERATION_ATTEMPTS = 3
_TEMPLATES = None


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_templates() -> dict:
    base_dir = os.path.dirname(__file__)
    pdir = os.path.join(base_dir, "prompts")
    return {
        "system_rules": _load_text(os.path.join(pdir, "system_rules.txt")).strip(),
        "base_rules": _load_text(os.path.join(pdir, "base_rules.txt")).strip(),
        "mode_rules": {
            "warranty": _load_text(os.path.join(pdir, "mode_warranty.txt")).strip(),
            "cp": _load_text(os.path.join(pdir, "mode_cp.txt")).strip(),
        },
        "section_inputs": {
            "diag": _load_text(os.path.join(pdir, "section_diag_only.txt")).strip(),
            "repair": _load_text(os.path.join(pdir, "section_repair_only.txt")).strip(),
            "diag_repair": _load_text(os.path.join(pdir, "section_diag_repair.txt")).strip(),
        },
        "output_rules": _load_text(os.path.join(pdir, "output_rules.txt")).strip(),
    }


def _get_templates() -> dict:
    global _TEMPLATES
    if _TEMPLATES is None:
        _TEMPLATES = _load_templates()
    return _TEMPLATES


def _resp(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def _get_openai_key() -> str:
    param_name = os.environ["OPENAI_PARAM_NAME"]
    resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _safe(v):
    return (v or "").strip()


def _select_model(data: dict) -> str:
    return _safe(data.get("model")) or os.environ.get("OPENAI_MODEL", "gpt-4.1")


def _normalized_job_type(data: dict) -> str:
    raw = _safe(data.get("job_type") or data.get("mode")).lower()
    if raw in ("warranty", "cp"):
        return raw
    return "warranty"


def _normalized_mode(data: dict) -> str:
    raw = _safe(data.get("mode") or data.get("sectionMode") or "diag_repair").lower()
    mode_map = {
        "diag_only": "diag",
        "repair_only": "repair",
        "diag_repair": "diag_repair",
        "diag": "diag",
        "repair": "repair",
    }
    return mode_map.get(raw, "diag_repair")


def _build_prompt(data: dict) -> str:
    t = _get_templates()
    job_type = _normalized_job_type(data)
    mode = _normalized_mode(data)

    # Supports new API contract while remaining backward compatible with existing frontend fields.
    ctx = {
        "job_type": job_type,
        "mode": mode,
        "vehicle": _safe(data.get("vehicle")),
        "vin": _safe(data.get("vin")),
        "mileage": _safe(data.get("mileage")),
        "concern": _safe(data.get("concern")),
        "codes_symptoms": _safe(data.get("codes_symptoms") or data.get("diagnosis")),
        "diag_steps": _safe(data.get("diag_steps") or data.get("diagnosis")),
        "repair_steps": _safe(data.get("repair_steps") or data.get("repair")),
        "extra_instructions": _safe(data.get("extra_instructions") or data.get("comment") or data.get("extra")),
    }

    rules = "\n".join([t["base_rules"], t["mode_rules"][job_type], t["output_rules"]]).strip()
    return (rules + "\n\n" + t["section_inputs"][mode].format(**ctx)).strip()


def _extract_story(result: dict) -> str:
    story = ""
    for item in result.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text"):
                story += c.get("text", "")
    return story.strip()


def _openai_story_call(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode("utf-8"))
    return _extract_story(result)


def _validate_story_output(story: str) -> bool:
    checks = [
        r"(?m)^\s*[-*•]\s+",  # bullets
        r"(?m)^\s*\d+[.)]\s+",  # numbered lists
        r"\n\s*\n",  # empty lines
        r"Customer states",  # forbidden phrase
        r"(?mi)^\s*(VIN|Diagnosis|Repair|Parts|Time)\s*:",  # labels
        r"[—–]",  # em/en dashes
    ]
    return not any(re.search(pattern, story) for pattern in checks)


def _generate_with_validation(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    prompt = user_prompt
    for _ in range(MAX_GENERATION_ATTEMPTS):
        story = _openai_story_call(api_key, model, system_prompt, prompt)
        if _validate_story_output(story):
            return story
        prompt = (
            user_prompt
            + "\n\nPrevious output violated formatting rules. Regenerate strictly."
        )
    raise ValueError("Model output failed formatting validation after retries")


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        body = event.get("body") or "{}"
        data = json.loads(body) if isinstance(body, str) else (body or {})

        api_key = _get_openai_key()
        model = _select_model(data)

        t = _get_templates()
        system_prompt = t["system_rules"]
        user_prompt = _build_prompt(data)
        story = _generate_with_validation(api_key, model, system_prompt, user_prompt)

        return _resp(200, {"story": story})
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        return _resp(502, {"error": f"OpenAI HTTPError {e.code}", "details": err[:2000]})
    except Exception as e:
        return _resp(500, {"error": str(e), "details": "Generation failed"})
