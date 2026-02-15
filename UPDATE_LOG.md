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
