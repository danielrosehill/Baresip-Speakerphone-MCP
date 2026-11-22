# Baresip-Speakerphone-MCP: agent context

Release tier: **small** (`v1.0`, `v1.1`, ...).

- Read `README.md` first (what it does, config keys, verified gotchas).
- Generic and public: no household details in code or docs. A user's values live in their own `speakerphone.json`.
- Test the number rules with `uv run --with pytest pytest -q`. Live tests need a SIP PBX; `scripts/mcp_call.py` drives the tools.
