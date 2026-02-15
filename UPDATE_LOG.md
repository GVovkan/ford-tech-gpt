# Update Log

## v0.17-test
- Redesigned `/test` layout to be simpler and match requested flow:
  top row now includes Story Mode, Job Type (CP/Warranty), VIN, and Mileage.
- Moved Settings (theme + GPT model) into a top Menu panel instead of inline form fields.
- Kept Diagnosis and Repair always visible side-by-side (no conditional hide/show).
- Simplified lower section to Parts, Time, and Notes.
- Added input guardrails to reduce screen errors:
  generation is blocked until at least Diagnosis, Repair, or Notes has content.
- Improved API error display text so failures are clearer and less noisy on screen.
- Added mileage into generation context via `extra` payload text.
- Bumped test UI version and cache-bust to `0.17`.
- Added documented versioning rule: always bump versions with major `+0.1` and minor `+0.01` steps.

## v0.18-test
- Hardened warranty formatter enforcement so label-style model output is normalized before final return.
- Strips inline headers like `Verification:`, `Diagnosis:`, and `Root cause:` from model sections.
- Enforces `Root cause -` label format in final warranty output.
- Preserves mandatory metadata block in warranty output with defaults when missing:
  `Causal Part: Not provided` and `Labor Op: Not provided`.
- Ensures mileage mentions are normalized to `km` and appended in first paragraph when provided in input context.
- Added backend unit test coverage for inline-label stripping, root-cause format enforcement, metadata defaults, and mileage normalization.

## v0.19-test
- Updated `/test` UI version markers to `v0.18-test` in HTML title, badge, and CSS cache-bust query.
- Bumped Lambda header version tag to `v0.08` in `lambda/lambda_function.py`.
- Extended README versioning rule to explicitly require HTML marker updates for frontend changes and Lambda header version tag bumps for backend changes.


## v0.19-gpt
- Added a new lightweight `/gpt` page at `web/gpt/index.html` for quick personal prompts.
- New page includes only Ask input, Generate button, and Generation output.
- Kept theme toggle with existing `vovkan_theme` localStorage key and `(c) Vovkan` footer.
- Added minimal supporting layout styles for the new Dash GPT page in `web/styles.css`.

## v0.20-gpt
- Reworked `/gpt` to be fully separated from shared app files.
- Moved Dash GPT page styling into standalone `web/gpt/styles.css`.
- Moved Dash GPT behavior into standalone `web/gpt/app.js`.
- Updated `/gpt` HTML version markers and asset links to `v0.20`.
- Removed Dash GPT-specific styles from shared `web/styles.css`.
