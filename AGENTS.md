# AGENTS.md - Ford Tech Story Creator rules

Scope: this file applies to the entire repository.

## Project overview
- Frontend: static HTML/CSS in `web/`
- Backend: AWS Lambda Python in `lambda/`
- AI prompt files: plain text in `lambda/prompts/`


## Test-first change workflow
- Experimental or innovative changes should be implemented in `web/test/index.html` first (served as `/test`).
- Keep `web/index.html` stable as production until user explicitly asks to transfer `/test` changes to main.

## Non-negotiable story output rules
Generated story output must always be:
- Plain text only
- No bullet points
- No numbered lists
- No blank lines (single newlines only)
- No section headers like `VIN:`, `Concern:`, `Diagnosis:`, `Repair:`, `Parts:`, `Time:`
- Hyphens only (`-`), never em/en dashes
- Never the phrase `Customer states`
- Professional technician tone for warranty/repair documentation
- No invented tests, measurements, parts, or steps not present in user input

If model output violates format, Lambda normalization must enforce these rules before returning.

## Prompt architecture rules
- Do not hardcode writing rules/templates in Python.
- Keep writing rules/templates in `lambda/prompts/*.txt`.
- Build prompt by stacking:
  - `base_rules.txt`
  - `mode_warranty.txt` or `mode_cp.txt` based on `mode`
  - `output_rules.txt`
  - optional user `comment` instruction (still constrained by formatting rules)
  - section template context file based on `sectionMode`
- Section templates are context-only and must not be echoed as labels in output.

## Lambda backend rules
- Lambda entry file path must be `lambda/lambda_function.py`.
- OpenAI API key source: AWS SSM Parameter Store (not Secrets Manager).
- Key env var: `OPENAI_PARAM_NAME`
- Model env var: `OPENAI_MODEL` default `gpt-4.1`
- Endpoint: OpenAI Responses API `POST https://api.openai.com/v1/responses`
- API route: `POST /generate`
- CORS headers:
  - `Access-Control-Allow-Origin` from `CORS_ORIGIN` (default `*`)
  - methods `POST, OPTIONS`
  - header `content-type`
- Response JSON:
  - success: `{"story":"..."}`
  - error: `{"error":"...","details":"..."}` with proper status code

## Mandatory normalization safety net
Lambda must normalize output by:
- replacing em/en dashes with hyphen
- removing bullets at line start
- removing numbered lists at line start
- removing common section header labels at line start
- collapsing multiple newlines to one
- trimming trailing spaces
- replacing `Customer states` with `Customer reported` or removing it

## Frontend rules (`web/`)
- Static HTML + CSS only (no frameworks)
- CSS in `web/styles.css`
- Keep version badge and CSS cache-bust query in sync
- Footer must be `(c) Vovkan`
- Theme toggle:
  - localStorage key `vovkan_theme`
  - write theme to `<html data-theme="...">`
- VIN field:
  - monospace + uppercase style
  - history key `vovkan_vins`
  - dropdown closes on outside click and Escape
  - clear history button
  - keep `:-webkit-autofill` fix for legibility
- Buttons:
  - Generate animated + disabled while loading + skeleton overlay
  - Copy uses clipboard API on HTTPS with textarea/execCommand fallback and status text
  - Clear empties inputs and output

## Data contract
Frontend request JSON:
- `mode`: `Warranty` | `CP`
- `sectionMode`: `diag_repair` | `diag_only` | `repair_only`
- `vin`, `concern`, `diagnosis`, `repair`, `comment`, `parts`, `time`, `extra`

Backend response JSON:
- `story`

## Do not
- Do not migrate prompt rules into Python constants
- Do not output markdown from story generation
- Do not add bullets/lists/formatting in story examples
- Do not change plain-text no-blank-lines output requirements
- Do not commit secrets/keys/.env files
- Do not change localStorage keys `vovkan_theme` or `vovkan_vins`

## Validation checklist for edits
When changing generation behavior, validate all section modes:
- `diag_only`
- `repair_only`
- `diag_repair`

Confirm output has:
- no blank lines
- no bullets
- no `VIN:`/label echoes
- hyphens only
- no `Customer states`
