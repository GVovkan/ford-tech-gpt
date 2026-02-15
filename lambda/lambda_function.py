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
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"(?m)^\s*[\u2022\u2023\u25E6\u2043\u2219•\-]+\s+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?i)\bcustomer\s+states\b", "Customer reported", text)
    text = re.sub(r"(?i)\bnot\s+provided\b", "", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return "\n".join([ln.rstrip() for ln in text.split("\n")]).strip()


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
    lines = []
    vin = _safe(data.get("vin"))
    mileage = _safe(data.get("mileage"))
    diagnosis = _safe(data.get("diagnosis"))
    repair = _safe(data.get("repair"))
    parts = _safe(data.get("parts"))
    time = _safe(data.get("time"))
    notes = _safe(data.get("notes") or data.get("comment") or data.get("extra"))

    if vin:
        lines.append(f"VIN: {vin}")
    if mileage:
        lines.append(f"Mileage: {mileage} km")
    lines.append(f"Diagnosis: {diagnosis}")
    if repair:
        lines.append(f"Repair: {repair}")
    if parts:
        lines.append(f"Parts: {parts}")
    if time:
        lines.append(f"Time: {time}")
    if notes:
        lines.append(f"Notes: {notes}")
    lines.append("Write the complete warranty repair story now.")
    return "\n".join(lines)


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

        if mode == "Warranty" and not _safe(data.get("diagnosis")):
            return _resp(400, {"error": "Diagnosis is required", "details": "Provide diagnosis text."})

        api_key = _get_openai_key()
        model = _select_model(data)

        if mode == "Warranty":
            system_prompt = (
                "You are a professional Ford dealership technician in Canada writing a warranty repair story.\n"
                "Write in plain text only.\n"
                "No bullet points.\n"
                "No numbered lists.\n"
                "No section headers like Verification or Diagnosis.\n"
                "Use hyphen only.\n"
                "Never write 'Customer states'.\n"
                "Never write 'Not provided'.\n"
                "If information is missing, generate professional and realistic wording instead of saying it is missing.\n"
                "Use km for mileage.\n"
                "Write in clear, direct technician tone.\n"
                "Root cause must be written as: Root cause - <cause>.\n"
                "Include Causal Part and Labor Op lines at the end.\n"
                "If causal part or labor op are not provided by user, generate reasonable generic ones.\n"
                "Do not mention that information was missing."
            )
            user_prompt = _build_warranty_simple_user_prompt(data)
            story = _normalize_story(_openai_story_call(api_key, model, system_prompt, user_prompt))
        else:
            t = _get_templates()
            story = _normalize_story(_openai_story_call(api_key, model, t["system_rules"], _build_prompt(data)))

        return _resp(200, {"story": story})
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        return _resp(502, {"error": f"OpenAI HTTPError {e.code}", "details": err[:2000]})
    except Exception as e:
        return _resp(500, {"error": str(e)})
