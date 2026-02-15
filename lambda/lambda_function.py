# lambda/lambda_function.py (v0.06) - FULL FILE
# - Loads prompt/rules from lambda/prompts/*.txt
# - Uses SSM Parameter Store for OpenAI key (free-ish vs Secrets Manager)
# - Calls OpenAI Responses API
# - Enforces strict output formatting:
#   - plain text
#   - no bullets / no numbering
#   - no blank lines
#   - no section headers
#   - hyphens only
#   - never "Customer states"
# - Returns: {"story": "..."} or {"error": "...", "details": "..."}

import json
import os
import re
import boto3
import urllib.request
import urllib.error

ssm = boto3.client("ssm")
OPENAI_URL = "https://api.openai.com/v1/responses"
_TEMPLATES = None
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$")


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
            "Warranty": _load_text(os.path.join(pdir, "mode_warranty.txt")).strip(),
            "CP": _load_text(os.path.join(pdir, "mode_cp.txt")).strip(),
        },
        "section_inputs": {
            "diag_only": _load_text(os.path.join(pdir, "section_diag_only.txt")).strip(),
            "repair_only": _load_text(os.path.join(pdir, "section_repair_only.txt")).strip(),
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
    requested = _safe(data.get("model"))
    if requested and MODEL_RE.fullmatch(requested):
        return requested
    return os.environ.get("OPENAI_MODEL", "gpt-4.1")


def _normalize_story(text: str) -> str:
    if not text:
        return ""

    # normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # hyphens only
    text = text.replace("—", "-").replace("–", "-")

    # kill common bullets at start-of-line
    text = re.sub(r"(?m)^\s*[\u2022\u2023\u25E6\u2043\u2219•]+\s+", "", text)
    # if the model used "-" as bullets, remove only when it looks like list bullet
    text = re.sub(r"(?m)^\s*-\s+", "", text)

    # kill numbered list patterns at start-of-line
    text = re.sub(r"(?m)^\s*\d+\)\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "", text)

    # remove section header prefixes if they appear
    text = re.sub(
        r"(?mi)^\s*(vin|concern|verification|diagnosis|repair|parts used|parts|time spent|time|extra notes|extra)\s*:\s*",
        "",
        text,
    )

    # no blank lines
    text = re.sub(r"\n{2,}", "\n", text)

    # strip right spaces
    text = "\n".join([ln.rstrip() for ln in text.split("\n")]).strip()

    # enforce "Customer states" ban (remove phrase if it slips)
    text = re.sub(r"(?i)\bcustomer\s+states\b", "Customer reported", text)

    return text


def _build_prompt(data: dict) -> str:
    t = _get_templates()

    mode = _safe(data.get("mode")) or "Warranty"
    if mode not in ("Warranty", "CP"):
        mode = "Warranty"

    section_mode = _safe(data.get("sectionMode")) or "diag_repair"
    if section_mode not in ("diag_only", "repair_only", "diag_repair"):
        section_mode = "diag_repair"

    comment = _safe(data.get("comment"))

    ctx = {
        "mode": mode,
        "vin": _safe(data.get("vin")),
        "concern": _safe(data.get("concern")),
        "diagnosis": _safe(data.get("diagnosis")),
        "repair": _safe(data.get("repair")),
        "parts": _safe(data.get("parts")),
        "time": _safe(data.get("time")),
        "extra": _safe(data.get("extra")),
        "comment": comment,
    }

    rules = "\n".join([
        t["base_rules"],
        t["mode_rules"][mode],
        t["output_rules"],
    ]).strip()

    if comment:
        rules += (
            "\nAdditional instruction (optional): " + comment +
            "\nStill obey ALL formatting rules above."
        )

    inputs_block = t["section_inputs"][section_mode].format(**ctx)

    # inputs are context only - model must NOT repeat labels
    return (rules + "\n\n" + inputs_block).strip()


def _extract_story(result: dict) -> str:
    story = ""
    for item in result.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                story += c.get("text", "")
    return story.strip()


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        body = event.get("body") or "{}"
        data = json.loads(body) if isinstance(body, str) else (body or {})

        api_key = _get_openai_key()
        t = _get_templates()
        prompt = _build_prompt(data)

        payload = {
            "model": _select_model(data),
            "input": [
                {"role": "system", "content": t["system_rules"]},
                {"role": "user", "content": prompt},
            ],
        }

        req = urllib.request.Request(
            OPENAI_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode("utf-8"))

        story = _normalize_story(_extract_story(result))
        return _resp(200, {"story": story})

    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        return _resp(502, {"error": f"OpenAI HTTPError {e.code}", "details": err[:2000]})
    except Exception as e:
        return _resp(500, {"error": str(e)})
