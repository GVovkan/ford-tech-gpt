# Update Log

## v0.07-test
- Added `/test` Settings UI with model presets and custom model input.
- Saved test settings locally so selected model persists across sessions.
- Included selected `model` in `/generate` request payload from `/test`.
- Backend now accepts optional `model` from request with safe validation fallback to `OPENAI_MODEL`.
- Fixed VIN history dropdown arrow vertical alignment in Chrome.
- Improved light theme Generate button gradient so it is not dark/black.
