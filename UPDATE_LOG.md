# Update Log

## v0.19-test
- Re-checked warranty formatter mileage behavior against current frontend/backend data contract.
- Updated Lambda mileage extraction to read mileage from structured keys (`mileage`, `odometer`, `km`) and from `extra` text patterns such as `Mileage: 73420`.
- Added unit-test coverage for km suffix insertion when mileage is only present in `extra` context.
- Bumped `/test` UI version and CSS cache-bust to `0.19`.

## v0.18-test
- Hardened warranty formatter in Lambda so final output no longer emits inline section headers like `Verification:` or `Diagnosis:`.
- Enforced root-cause output format as `Root cause - ...` and merged repair content into the same root-cause paragraph when applicable.
- Enforced warranty metadata block to always include `Causal Part:` and `Labor Op:` with `Not provided` fallback values.
- Added mileage suffix handling to append `km` when mileage is provided and not already labeled.
- Expanded warranty formatter tests for merged paragraph structure, metadata enforcement, and km suffix behavior.
- Bumped `/test` UI version and CSS cache-bust to `0.18`.
- Updated versioning rules to require versions always increase and every bump be logged.

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
