# lambda/lambda_function.py (v0.08) - FULL FILE
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
FORBIDDEN_WORDS_RE = re.compile(r"\b(likely|possible|possibly|indicates|indicated|indicating|concluded|suspect|suspected|appears|seems|maybe|perhaps)\b", re.IGNORECASE)
TIME_REFERENCES_RE = re.compile(r"\b(time|hour|hours|hr|hrs|justification)\b", re.IGNORECASE)
MILEAGE_RE = re.compile(r"\b(\d{3,7})\s*(km|kms|kilometers?|kilometres?)?\b", re.IGNORECASE)
CONCERN_LINE_RE = re.compile(r"(?im)^\s*concern\s*[:\-]\s*(.+?)\s*$")
NO_DTCS_RE = re.compile(r"(?i)\bno\s*(?:stored\s*)?(?:dtcs?|codes?)\b")
PLACEHOLDER_RE = re.compile(r"^\s*(not provided|n/?a|na|none provided|unknown)\s*\.?\s*$", re.IGNORECASE)
VIN_DECODE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"
_VIN_CACHE = {}

RISKY_CLAIMS = [
    "second row",
    "third row",
    "rear seat",
    "rear seat removal",
    "power running boards",
    "sunroof",
    "tow package",
    "max tow",
    "dual alternator",
    "hdpp",
    "rear ac",
    "captain chairs",
    "supercrew rear doors",
]

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


def _preprocess_inputs(data: dict) -> dict:
    diagnosis = _safe(data.get("diagnosis"))
    concern = _safe(data.get("concern"))
    extracted_no_dtcs = []

    if not concern and diagnosis:
        match = CONCERN_LINE_RE.search(diagnosis)
        if match:
            concern = match.group(1).strip()
            diagnosis = CONCERN_LINE_RE.sub("", diagnosis).strip()

    if concern and NO_DTCS_RE.search(concern):
        extracted_no_dtcs = NO_DTCS_RE.findall(concern)
        concern = NO_DTCS_RE.sub("", concern)
        concern = re.sub(r"\s{2,}", " ", concern).strip(" .,-")

    if extracted_no_dtcs:
        no_dtcs_line = "No DTCs"
        if not NO_DTCS_RE.search(diagnosis):
            diagnosis = f"{diagnosis}\n{no_dtcs_line}".strip() if diagnosis else no_dtcs_line

    data["concern"] = concern
    data["diagnosis"] = diagnosis
    return data


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
            continue
        if PLACEHOLDER_RE.match(val):
            errs.append(f"Required key contains placeholder text: {key}")

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


def _decode_vin(vin: str) -> dict:
    vin = _safe(vin).upper()
    if len(vin) != 17:
        return {}
    if vin in _VIN_CACHE:
        return _VIN_CACHE[vin]

    req = urllib.request.Request(VIN_DECODE_URL.format(vin=vin), method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))

    row = (data.get("Results") or [{}])[0]
    decoded = {
        "year": _safe(row.get("ModelYear")),
        "make": _safe(row.get("Make")).lower(),
        "model": _safe(row.get("Model")).lower(),
        "trim": _safe(row.get("Trim")).lower(),
        "body": _safe(row.get("BodyClass")).lower(),
        "series": _safe(row.get("Series")).lower(),
        "cab": _safe(row.get("CabType")).lower(),
        "engine": _safe(row.get("EngineModel")).lower(),
    }
    _VIN_CACHE[vin] = decoded
    return decoded


def _vin_capabilities(decoded: dict) -> dict:
    combined = " ".join([decoded.get("body", ""), decoded.get("series", ""), decoded.get("trim", ""), decoded.get("cab", "")])
    is_regular = "regular" in combined
    is_super = "supercrew" in combined or "super cab" in combined or "supercab" in combined
    model = decoded.get("model", "")
    return {
        "model": model,
        "regular_cab": is_regular,
        "rear_seat_possible": is_super,
        "third_row_allowed": model == "explorer",
    }


def _input_mentions_feature(data: dict, claim: str) -> bool:
    haystack = " ".join([
        _safe(data.get("concern")),
        _safe(data.get("diagnosis")),
        _safe(data.get("repair")),
        _safe(data.get("comment")),
        _safe(data.get("extra")),
    ]).lower()
    features = data.get("vehicle_features") or []
    feature_text = " ".join([str(v).lower() for v in features])
    return claim in haystack or claim in feature_text


def _claim_allowed(claim: str, vin_caps: dict, data: dict) -> bool:
    model = vin_caps.get("model", "")
    mentioned = _input_mentions_feature(data, claim)

    if claim == "third row":
        if model == "f-150":
            return False
        return vin_caps.get("third_row_allowed") and mentioned

    if claim in ("second row", "rear seat", "rear seat removal", "supercrew rear doors"):
        if vin_caps.get("regular_cab"):
            return False
        return vin_caps.get("rear_seat_possible") and mentioned

    return mentioned


def _filter_unvalidated_claims(text: str, data: dict, vin_caps: dict) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", _normalize_story(text))
    kept = []
    for sentence in sentences:
        line = sentence.strip()
        if not line:
            continue
        lower = line.lower()
        blocked = False
        for claim in RISKY_CLAIMS:
            if claim in lower and not _claim_allowed(claim, vin_caps, data):
                blocked = True
                break
        if not blocked:
            kept.append(line)
    return " ".join(kept).strip()


def _enforce_km_units(text: str) -> str:
    text = re.sub(r"(?i)\b(miles|mile|mi)\b", "km", text)
    return text


def _enforce_wsm_repair_language(repair_text: str, data: dict) -> str:
    text = _normalize_story(repair_text or "")
    if not _input_mentions_feature(data, "if equipped"):
        text = re.sub(r"(?i)\bif equipped\b", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    original_repair = _safe(data.get("repair")).lower()
    lines = [ln.strip() for ln in re.split(r"(?<=[.!?])\s+", text) if ln.strip()]
    filtered = []
    for ln in lines:
        ll = ln.lower()
        has_diag = any(k in ll for k in ("diagnos", "dtc", "pinpoint", "verified concern", "root cause"))
        if has_diag and ll not in original_repair:
            continue
        filtered.append(ln)

    rebuilt = " ".join(filtered).strip()
    if not rebuilt:
        rebuilt = "Removed and installed components as required."
    if not re.search(r"\b(removed|installed|replaced|repaired|performed)\b", rebuilt, re.IGNORECASE):
        rebuilt = "Performed repair procedure. " + rebuilt
    if not re.search(r"torqu\w*\s+.*spec", rebuilt, re.IGNORECASE):
        rebuilt += " Torqued fasteners to specification."

    wsm_ref = _safe(data.get("wsm_ref"))
    tsb_ref = _safe(data.get("tsb_ref"))
    if wsm_ref:
        rebuilt += f" Performed procedure per WSM {wsm_ref}."
    elif tsb_ref:
        rebuilt += f" Performed procedure per TSB {tsb_ref}."
    elif not re.search(r"\b(workshop manual|wsm)\b", rebuilt, re.IGNORECASE):
        rebuilt += " Performed procedure per workshop manual procedure."

    rebuilt = re.sub(r"\s{2,}", " ", rebuilt).strip()
    return _clean_sentence(rebuilt)




def _sanitize_non_metadata_text(text: str) -> str:
    return (text or "").replace(":", " -")


def _strip_inline_label_prefix(text: str, labels: tuple) -> str:
    cleaned = _normalize_story(text or "")
    if not cleaned:
        return ""
    pattern = r"(?i)^\s*(?:" + "|".join(re.escape(lbl) for lbl in labels) + r")\s*[:\-]\s*"
    return re.sub(pattern, "", cleaned).strip()


def _extract_mileage_value(data: dict) -> str:
    for source in (_safe(data.get("mileage")), _safe(data.get("extra"))):
        if not source:
            continue
        match = MILEAGE_RE.search(source)
        if match:
            return match.group(1)
    return ""


def _ensure_mileage_in_first_block(text: str, data: dict) -> str:
    mileage = _extract_mileage_value(data)
    if not mileage:
        return text
    if re.search(rf"\b{re.escape(mileage)}\s*km\b", text, re.IGNORECASE):
        return text
    return f"{text.rstrip('.')} at {mileage} km."

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


def _format_warranty_story(section_mode: str, payload: dict, data: dict) -> str:
    vin_caps = _vin_capabilities(_decode_vin(_safe(data.get("vin"))))

    verification_raw = _strip_inline_label_prefix(
        payload.get("verification", ""),
        ("verification", "verified concern", "concern", "customer concern"),
    )
    diagnosis_raw = _strip_inline_label_prefix(payload.get("diagnosis", ""), ("diagnosis", "inspection", "finding", "findings"))
    cause_raw = _strip_inline_label_prefix(payload.get("cause", ""), ("root cause", "cause", "causal part"))
    repair_raw = _strip_inline_label_prefix(payload.get("repair_performed", ""), ("repair", "repair performed", "action", "corrective action"))
    post_raw = _strip_inline_label_prefix(
        payload.get("post_repair_verification", ""),
        ("post repair verification", "verification", "verified repair", "final verification"),
    )

    verification = _sanitize_non_metadata_text(_clean_sentence(_filter_unvalidated_claims(verification_raw, data, vin_caps)))
    diagnosis = _sanitize_non_metadata_text(_clean_sentence(_filter_unvalidated_claims(diagnosis_raw, data, vin_caps)))
    cause = _sanitize_non_metadata_text(_clean_sentence(_filter_unvalidated_claims(cause_raw, data, vin_caps)))
    repair = _sanitize_non_metadata_text(_clean_sentence(_filter_unvalidated_claims(repair_raw, data, vin_caps)))
    post = _sanitize_non_metadata_text(_clean_sentence(_filter_unvalidated_claims(post_raw, data, vin_caps)))

    if section_mode in ("repair_only", "diag_repair"):
        repair = _enforce_wsm_repair_language(repair, data)

    block1_parts = []
    if section_mode in ("diag_only", "diag_repair", "repair_only"):
        if payload.get("verification"):
            block1_parts.append(verification)
    if section_mode in ("diag_only", "diag_repair") and payload.get("diagnosis"):
        block1_parts.append(diagnosis)
    block1 = " ".join(block1_parts).strip()
    if block1:
        block1 = _ensure_mileage_in_first_block(block1, data)

    block2_parts = []
    if section_mode in ("diag_only", "diag_repair") and payload.get("cause"):
        block2_parts.append(f"Root cause - {cause}")
    if section_mode in ("repair_only", "diag_repair") and payload.get("repair_performed"):
        block2_parts.append(repair)
    block2 = " ".join(block2_parts).strip()

    blocks = []
    if block1:
        blocks.append(block1)
    if block2:
        blocks.append(block2)

    blocks.append("\n".join(_warranty_metadata_lines(data)))

    if section_mode in ("repair_only", "diag_repair") and payload.get("post_repair_verification"):
        blocks.append(post)

    final = "\n".join([b for b in blocks if b.strip()])
    final = _remove_time_lines(_enforce_km_units(final))
    final = final.replace("—", "-").replace("–", "-")
    if not _input_mentions_feature(data, "if equipped"):
        final = re.sub(r"(?i)\bif equipped\b", "", final)
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
        data = _preprocess_inputs(data)

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
