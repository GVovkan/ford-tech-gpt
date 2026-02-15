import json
import os
import boto3
import urllib.request
import urllib.error

ssm = boto3.client("ssm")
OPENAI_URL = "https://api.openai.com/v1/responses"
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
    return _safe(data.get("model")) or os.environ.get("OPENAI_MODEL", "gpt-4.1")


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

    rules = "\n".join([t["base_rules"], t["mode_rules"][mode], t["output_rules"]]).strip()
    if comment:
        rules += "\nAdditional instruction (optional): " + comment + "\nStill obey ALL formatting rules above."

    return (rules + "\n\n" + t["section_inputs"][section_mode].format(**ctx)).strip()


def _build_warranty_simple_user_prompt(data: dict) -> str:
    vin = _safe(data.get("vin"))
    mileage = _safe(data.get("mileage"))
    diagnosis = _safe(data.get("diagnosis"))
    repair = _safe(data.get("repair"))
    parts = _safe(data.get("parts"))
    time = _safe(data.get("time"))
    notes_values = [_safe(data.get("notes")), _safe(data.get("comment")), _safe(data.get("extra"))]
    notes = " | ".join([value for value in notes_values if value])

    return (
        "Provide the fields exactly as:\n\n"
        f"VIN: {vin}\n"
        f"Mileage: {mileage}\n"
        f"Diagnosis (mandatory): {diagnosis}\n"
        f"Repair (optional): {repair}\n"
        f"Parts (optional): {parts}\n"
        f"Time (optional): {time}\n"
        f"Notes (optional): {notes}\n\n"
        "Now write the complete warranty RO story following all rules."
    )


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


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        body = event.get("body") or "{}"
        data = json.loads(body) if isinstance(body, str) else (body or {})
        mode = _safe(data.get("mode")) or "Warranty"

        api_key = _get_openai_key()
        model = _select_model(data)

        if mode == "Warranty":
            system_prompt = (
                "You are a professional Ford dealership technician in Canada writing a warranty repair story for an RO. "
                "Output plain text only, no markdown. No bullet points, no numbered lists. No section headers or labels like “Verification:” "
                "or “Diagnosis:”. Use hyphens only (-). Never write “Customer states”. Never write “Not provided”, “N/A”, “unknown”, "
                "or “missing”. If information is not supplied, generate reasonable professional generic wording instead. Use km only. "
                "Write one continuous story block in this order: verified concern -> diagnostic actions/findings -> Root cause line -> "
                "repair performed -> post-repair verification. Include exactly one root cause line that starts with: Root cause - . "
                "At the end include exactly these two lines with colons and nothing else in between:\n"
                "Causal Part: …\n"
                "Labor Op: …\n"
                "Do not invent torque values or WSM section numbers. You may say “torqued to specification” and “performed per "
                "workshop manual procedure”. Do not invent numeric part numbers or labor hours when not provided."
            )
            user_prompt = _build_warranty_simple_user_prompt(data)
            story = _openai_story_call(api_key, model, system_prompt, user_prompt)
        else:
            t = _get_templates()
            story = _openai_story_call(api_key, model, t["system_rules"], _build_prompt(data))

        return _resp(200, {"story": story})
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        return _resp(502, {"error": f"OpenAI HTTPError {e.code}", "details": err[:2000]})
    except Exception as e:
        return _resp(500, {"error": str(e)})
