# Changelog

## 0.1.0

First release.

- `lock`, `check`, `diff` and `inspect` commands.
- Sound, direction-aware JSON Schema compatibility engine (tri-state).
- Sources: MCP over stdio and Streamable HTTP, JSON files, lock files; OpenAI, Anthropic and Gemini tool formats.
- Rename detection, token budget (`--max-tokens`, `--max-increase`, `--max-tool-tokens`, `--ratchet`), semver check (`--check-version`).
- Integrity flags on new text; annotation escalation detection.
- Output: text, markdown (sticky PR comment marker), JSON, GitHub annotations, job summary.
