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
from typing import Tuple

ssm = boto3.client("ssm")
OPENAI_URL = "https://api.openai.com/v1/responses"
_TEMPLATES = None
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$")
FORBIDDEN_WORDS_RE = re.compile(r"\b(likely|possible|possibly|indicates|indicated|concluded|suspect|suspected|appears|maybe|perhaps)\b", re.IGNORECASE)
TIME_REFERENCES_RE = re.compile(r"\b(time|hour|hours|hr|hrs|justification)\b", re.IGNORECASE)

WARRANTY_JSON_SCHEMAS = {
    "diag_only": {
        "type": "object",
        "required": ["verification", "diagnosis", "cause"],
        "properties": {
            "verification": {"type": "string"},
            "diagnosis": {"type": "string"},
            "cause": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "repair_only": {
        "type": "object",
        "required": ["verification", "repair_performed", "post_repair_verification"],
        "properties": {
            "verification": {"type": "string"},
            "repair_performed": {"type": "string"},
            "post_repair_verification": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "diag_repair": {
        "type": "object",
        "required": ["verification", "diagnosis", "cause", "repair_performed", "post_repair_verification"],
        "properties": {
            "verification": {"type": "string"},
            "diagnosis": {"type": "string"},
            "cause": {"type": "string"},
            "repair_performed": {"type": "string"},
            "post_repair_verification": {"type": "string"},
        },
        "additionalProperties": False,
    },
}

SECTION_ORDER = {
    "diag_only": ["verification", "diagnosis", "cause"],
    "repair_only": ["verification", "repair_performed", "post_repair_verification"],
    "diag_repair": ["verification", "diagnosis", "cause", "repair_performed", "post_repair_verification"],
}

SECTION_LABELS = {
    "verification": "Verification",
    "diagnosis": "Diagnosis",
    "cause": "Root cause",
    "repair_performed": "Repair performed",
    "post_repair_verification": "Post-repair verification",
}


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


def _build_warranty_structured_prompt(data: dict) -> str:
    prompt = _build_prompt(data)
    return (
        prompt
        + "\n\nReturn only valid JSON for the required schema."
        + " Do not include markdown, prose, or keys outside the schema."
    )


def _extract_story(result: dict) -> str:
    story = ""
    for item in result.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                story += c.get("text", "")
    return story.strip()


def _extract_json_text(result: dict) -> str:
    buf = ""
    for item in result.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text"):
                buf += c.get("text", "")
    return buf.strip()


def _json_schema_for_section_mode(section_mode: str) -> dict:
    return WARRANTY_JSON_SCHEMAS.get(section_mode, WARRANTY_JSON_SCHEMAS["diag_repair"])


def _validate_structured_payload(section_mode: str, payload: dict) -> Tuple[bool, list]:
    schema = _json_schema_for_section_mode(section_mode)
    required = schema["required"]
    allowed = set(schema["properties"].keys())
    errs = []

    if not isinstance(payload, dict):
        return False, ["Payload is not a JSON object"]

    for key in required:
        val = payload.get(key)
        if not isinstance(val, str) or not val.strip():
            errs.append(f"Missing or empty required key: {key}")

    extra = sorted(set(payload.keys()) - allowed)
    if extra:
        errs.append("Unexpected keys: " + ", ".join(extra))

    for key in allowed.intersection(payload.keys()):
        if not isinstance(payload.get(key), str):
            errs.append(f"Key must be a string: {key}")

    return len(errs) == 0, errs


def _clean_sentence(text: str) -> str:
    text = _normalize_story(text or "")
    text = FORBIDDEN_WORDS_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(" -")
    if not text:
        return "Not provided."
    if text[-1] not in ".!?":
        text += "."
    return text


def _remove_time_lines(text: str) -> str:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    filtered = [ln for ln in lines if not TIME_REFERENCES_RE.search(ln)]
    return "\n".join(filtered)


def _warranty_metadata_lines(data: dict) -> list:
    causal_part = _safe(data.get("causalPart") or data.get("causal_part"))
    labor_op = _safe(data.get("laborOp") or data.get("labor_op"))
    return [
        f"Causal Part: {causal_part or 'Not provided'}",
        f"Labor Op: {labor_op or 'Not provided'}",
    ]


def _km_suffix(data: dict) -> str:
    mileage = _safe(data.get("mileage") or data.get("odometer") or data.get("km"))
    if mileage:
        if re.search(r"\bkm\b", mileage, flags=re.IGNORECASE):
            return mileage
        return f"{mileage} km"

    extra = _safe(data.get("extra"))
    if not extra:
        return ""

    with_km = re.search(r"\b(\d{3,7})\s*km\b", extra, flags=re.IGNORECASE)
    if with_km:
        return f"{with_km.group(1)} km"

    tagged = re.search(r"\b(?:mileage|odometer|km)\s*[:=-]?\s*(\d{3,7})\b", extra, flags=re.IGNORECASE)
    if tagged:
        return f"{tagged.group(1)} km"

    return ""


def _format_warranty_story(section_mode: str, payload: dict, data: dict) -> str:
    verification = _clean_sentence(payload.get("verification", ""))
    diagnosis = _clean_sentence(payload.get("diagnosis", "")) if section_mode in ("diag_only", "diag_repair") else ""
    cause = _clean_sentence(payload.get("cause", "")) if section_mode in ("diag_only", "diag_repair") else ""
    repair = _clean_sentence(payload.get("repair_performed", "")) if section_mode in ("repair_only", "diag_repair") else ""
    post_repair = _clean_sentence(payload.get("post_repair_verification", "")) if section_mode in ("repair_only", "diag_repair") else ""

    first_line = verification
    km_value = _km_suffix(data)
    if km_value and "km" not in verification.lower():
        first_line = first_line.rstrip(".") + f" at {km_value}."
    if diagnosis:
        first_line = f"{first_line} {diagnosis}"

    lines = [first_line.strip()]

    if cause:
        root_line = f"Root cause - {cause.rstrip('.')}"
        if repair:
            root_line += f" {repair}"
        if root_line[-1] not in ".!?":
            root_line += "."
        lines.append(root_line)
    elif repair:
        lines.append(repair)

    lines.extend(_warranty_metadata_lines(data))
    if post_repair:
        lines.append(post_repair)

    final = _remove_time_lines("\n".join(lines))
    final = final.replace("—", "-").replace("–", "-")
    return re.sub(r"\n{2,}", "\n", final).strip()


def _generate_structured_warranty_story(data: dict, api_key: str, model: str, t: dict) -> Tuple[str, list]:
    section_mode = _safe(data.get("sectionMode")) or "diag_repair"
    if section_mode not in ("diag_only", "repair_only", "diag_repair"):
        section_mode = "diag_repair"

    schema = _json_schema_for_section_mode(section_mode)
    prompt = _build_warranty_structured_prompt(data)
    last_errors = []

    for _ in range(2):
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": t["system_rules"]},
                {"role": "user", "content": prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"warranty_{section_mode}",
                    "schema": schema,
                    "strict": True,
                }
            },
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

        raw_json = _extract_json_text(result)
        try:
            structured = json.loads(raw_json)
        except json.JSONDecodeError:
            last_errors = ["Model output is not valid JSON"]
            continue

        valid, errs = _validate_structured_payload(section_mode, structured)
        if not valid:
            last_errors = errs
            continue

        merged_text = "\n".join(str(structured.get(k, "")) for k in SECTION_ORDER[section_mode])
        if FORBIDDEN_WORDS_RE.search(merged_text):
            last_errors = ["Model output contained forbidden interpretive words"]
            continue

        return _format_warranty_story(section_mode, structured, data), []

    return "", last_errors or ["Structured output validation failed"]


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        body = event.get("body") or "{}"
        data = json.loads(body) if isinstance(body, str) else (body or {})

        api_key = _get_openai_key()
        t = _get_templates()
        mode = _safe(data.get("mode")) or "Warranty"
        model = _select_model(data)

        if mode == "Warranty":
            story, errors = _generate_structured_warranty_story(data, api_key, model, t)
            if errors:
                return _resp(422, {"error": "Warranty output validation failed", "details": "; ".join(errors)})
        else:
            prompt = _build_prompt(data)
            payload = {
                "model": model,
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
