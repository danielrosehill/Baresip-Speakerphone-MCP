"""Run baresip as a child process and talk to it over ctrl_tcp (netstring-framed JSON).

Commands: {"command": "accept", "params": "", "token": "..."} -> {"response": true, "ok": ..., "data": ..., "token": ...}
Events:   {"event": true, "class": "call", "type": "CALL_INCOMING", "id": ..., "peeruri": ..., "peerdisplayname": ...}
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import struct
import wave
from pathlib import Path
from typing import Awaitable, Callable

from .config import Config

log = logging.getLogger("speakerphone.baresip")
SHARE = "/opt/baresip/share/baresip"


def _silence(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 800)


def write_config(cfg: Config, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "silence.wav").unlink(missing_ok=True)
    _silence(home / "silence.wav")
    play = f"pulse,{cfg.sink}" if cfg.sink else "pulse"
    src = f"pulse,{cfg.source}" if cfg.source else "pulse"
    modules = ["stdio", "g711", "opus", "auconv", "auresamp", "pulse", "aufile"]
    conf = [
        f"poll_method epoll",
        f"sip_listen {cfg.sip_listen}",
        f"call_local_timeout 120",
        f"call_max_calls 4",
        f"call_hold_other_calls yes",
        f"audio_player {play}",
        f"audio_source {src}",
        f"audio_alert {play}",
        f"audio_path {SHARE}",
        f"rtp_ports {cfg.rtp_ports}",
        f"audio_jitter_buffer_type adaptive",
        f"audio_jitter_buffer_ms 60-300",
        # The controller plays its own ring + spoken caller announcement, so baresip's ring is silent.
        f"ring_aufile {home / 'silence.wav'}",
        f"module_path /opt/baresip/lib/baresip/modules",
        *[f"module {m}.so" for m in modules],
        "module_app account.so",
        "module_app menu.so",
        "module_app ctrl_tcp.so",
        f"ctrl_tcp_listen 127.0.0.1:{cfg.ctrl_port}",
    ]
    (home / "config").write_text("\n".join(conf) + "\n")
    aor = cfg.sip_aor if cfg.sip_aor.startswith("sip:") else f"sip:{cfg.sip_aor}"
    # sip_autoanswer: answer pages/intercom carrying Call-Info answer-after / Alert-Info auto-answer.
    # sip_autoanswer_beep=off: with the beep on (default), baresip 4.13 answers only when the beep's playback
    # finishes, and on the pulse alert device that finish never fired, so pages rang forever (seen 2026-11-22).
    acc = (f'"{cfg.display_name}" <{aor};transport=udp>;auth_pass={cfg.sip_password};regint=300;'
           f"answermode=manual;sip_autoanswer=yes;sip_autoanswer_beep=off;audio_codecs=opus/48000/2,PCMU,PCMA\n")
    p = home / "accounts"
    p.write_text(acc)
    os.chmod(p, 0o600)
    (home / "contacts").write_text("")


class Baresip:
    def __init__(self, cfg: Config, on_event: Callable[[dict], Awaitable[None]], home: Path = Path("/tmp/baresip"),
                 on_connect: Callable[[], Awaitable[None]] | None = None):
        self.cfg, self.on_event, self.home, self.on_connect = cfg, on_event, home, on_connect
        self.proc: asyncio.subprocess.Process | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._tok = itertools.count(1)
        self.connected = asyncio.Event()

    async def start(self) -> None:
        write_config(self.cfg, self.home)
        self.proc = await asyncio.create_subprocess_exec(
            "baresip", "-f", str(self.home), "-v",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        asyncio.create_task(self._pump_log())
        asyncio.create_task(self._connect_loop())

    async def _pump_log(self) -> None:
        assert self.proc and self.proc.stdout
        async for line in self.proc.stdout:
            log.info("baresip: %s", line.decode(errors="replace").rstrip())
        code = await self.proc.wait()
        log.error("baresip exited with %s; exiting so the container restarts", code)
        os._exit(1)

    async def _connect_loop(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.cfg.ctrl_port)
            except OSError:
                await asyncio.sleep(0.5)
                continue
            self._writer = writer
            self.connected.set()
            log.info("ctrl_tcp connected")
            if self.on_connect:
                asyncio.create_task(self.on_connect())
            try:
                await self._read(reader)
            except (asyncio.IncompleteReadError, ConnectionError) as e:
                log.warning("ctrl_tcp lost: %s", e)
            self.connected.clear()
            await asyncio.sleep(1)

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while True:
            length = int((await reader.readuntil(b":"))[:-1])
            body = await reader.readexactly(length)
            await reader.readexactly(1)  # trailing ","
            msg = json.loads(body)
            if msg.get("response"):
                fut = self._pending.pop(str(msg.get("token")), None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif msg.get("event"):
                try:
                    await self.on_event(msg)
                except Exception:
                    log.exception("event handler failed for %s", msg)

    async def command(self, command: str, params: str = "", timeout: float = 5) -> dict:
        await asyncio.wait_for(self.connected.wait(), timeout)
        tok = str(next(self._tok))
        fut = asyncio.get_running_loop().create_future()
        self._pending[tok] = fut
        body = json.dumps({"command": command, "params": params, "token": tok}).encode()
        assert self._writer
        self._writer.write(str(len(body)).encode() + b":" + body + b",")
        await self._writer.drain()
        return await asyncio.wait_for(fut, timeout)
