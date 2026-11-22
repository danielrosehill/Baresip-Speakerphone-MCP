"""MCP server (streamable HTTP, /mcp) for the speakerphone, plus GET /healthz."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import audio
from .config import load
from .numbers import normalise
from .phone import Phone

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
cfg = load()
phone = Phone(cfg)


mcp = MCPServer("speakerphone",
                instructions="Control the household speakerphone: who is calling, answer, reject, hang up, mute, "
                           "speaker volume, and place calls. Never format phone numbers yourself: pass what the "
                           "user said to phone_call and read back its 'say' text before dialling.")


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_req: Request) -> JSONResponse:
    ok = phone.bs.connected.is_set()
    return JSONResponse({"ok": ok, "registered": phone.registered}, status_code=200 if ok else 503)


@mcp.tool()
async def phone_status() -> dict:
    """Who is calling or who is on the line: call state (ringing/active/...), caller name or number, which line it
    came in on, duration, mute state and whether the phone is registered. Use for "who's calling?"."""
    st = phone.status()
    st["volume_percent"] = await audio.get_volume(cfg)
    return st


@mcp.tool()
async def phone_answer() -> dict:
    """Answer the ringing call on the speakerphone."""
    return await phone.answer()


@mcp.tool()
async def phone_reject() -> dict:
    """Reject (decline) the ringing call."""
    return await phone.reject()


@mcp.tool()
async def phone_hangup() -> dict:
    """Hang up / end the current call (also cancels an outgoing call that is still ringing)."""
    return await phone.hangup()


@mcp.tool()
async def phone_mute(muted: bool = True) -> dict:
    """Mute (muted=true) or unmute (muted=false) the speakerphone microphone on the active call."""
    return await phone.mute(muted)


@mcp.tool()
async def phone_volume(change: str = "up") -> dict:
    """Speaker volume: change = "up", "down", or a percentage like "60"."""
    step = {"up": "+10%", "down": "-10%", "louder": "+10%", "quieter": "-10%"}.get(change.lower().strip())
    if not step:
        digits = "".join(ch for ch in change if ch.isdigit())
        if not digits:
            return {"ok": False, "detail": "change must be up, down or a percentage"}
        step = f"{min(int(digits), 100)}%"
    return {"ok": True, "volume_percent": await audio.set_volume(cfg, step)}


@mcp.tool()
async def phone_call(number_or_contact: str, line: str | None = None, confirmed: bool = False) -> dict:
    """Place a call from the speakerphone. Pass the number exactly as the user said it (digits, spaces fine, e.g.
    "02 434 5678") or a contact name. The tool converts it to international format deterministically.
    First call with confirmed=false: it returns status needs_confirmation and a 'say' read-back; speak it, and only
    after the user says yes call again with confirmed=true. Emergency numbers (police, ambulance, fire) dial at once.
    line: which outgoing line (e.g. "home" or "work"); default line if omitted."""
    return await phone.dial(number_or_contact, line, confirmed)


@mcp.tool()
async def phone_check_number(number: str) -> dict:
    """Normalise a dictated phone number without dialling: international form, kind, and a spoken read-back."""
    n = normalise(number, cfg.number_locale, cfg.internal_pattern)
    return {"ok": n.ok, "dial": n.dial, "kind": n.kind, "readback": n.readback, "error": n.error}


def main() -> None:
    # baresip must run once for the life of the process (to receive calls), so it starts from the HTTP app's
    # lifespan, not the MCP server lifespan (which runs per session in streamable HTTP).
    app = mcp.streamable_http_app(host=cfg.mcp_host)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        await phone.bs.start()
        async with inner(a) as state:
            yield state

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host=cfg.mcp_host, port=cfg.mcp_port, log_level="warning")
