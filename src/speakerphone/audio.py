"""Speech and sounds on the speakerphone: Wyoming TTS to a WAV, played with paplay; sink volume via pactl."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import wave
from pathlib import Path

from .config import Config

log = logging.getLogger("speakerphone.audio")
RING = "/opt/baresip/share/baresip/ring.wav"


async def synthesize(cfg: Config, text: str) -> Path:
    """Wyoming protocol: JSON header line (+ data/payload by length); synthesize -> audio-start/chunk*/stop."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(cfg.tts_host, cfg.tts_port), 5)
    data: dict = {"text": text}
    if cfg.tts_voice:
        data["voice"] = {"name": cfg.tts_voice}
    body = json.dumps(data).encode()
    writer.write(json.dumps({"type": "synthesize", "data_length": len(body)}).encode() + b"\n" + body)
    await writer.drain()
    rate, width, channels, frames = 22050, 2, 1, bytearray()
    try:
        while True:
            header = json.loads(await asyncio.wait_for(reader.readline(), 30))
            hdata = header.get("data") or {}
            if header.get("data_length"):
                hdata.update(json.loads(await reader.readexactly(header["data_length"])))
            payload = await reader.readexactly(header["payload_length"]) if header.get("payload_length") else b""
            t = header.get("type")
            if t == "audio-start":
                rate, width, channels = hdata.get("rate", rate), hdata.get("width", width), hdata.get("channels", channels)
            elif t == "audio-chunk":
                frames += payload
            elif t == "audio-stop":
                break
    finally:
        writer.close()
    out = Path(tempfile.mkstemp(suffix=".wav", prefix="announce-")[1])
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out


async def play(cfg: Config, path: str | Path, seconds: float | None = None) -> None:
    args = ["paplay"] + ([f"--device={cfg.sink}"] if cfg.sink else []) + [str(path)]
    proc = await asyncio.create_subprocess_exec(*args)
    try:
        await asyncio.wait_for(proc.wait(), seconds if seconds else 60)
    except asyncio.TimeoutError:
        proc.terminate()
    except asyncio.CancelledError:
        proc.terminate()
        raise


async def _pactl(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec("pactl", *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return out.decode()


async def get_volume(cfg: Config) -> int | None:
    out = await _pactl("get-sink-volume", cfg.sink or "@DEFAULT_SINK@")
    m = re.search(r"(\d+)%", out)
    return int(m.group(1)) if m else None


async def set_volume(cfg: Config, change: str) -> int | None:
    """change: '+10%', '-10%' or '70%'. Capped at 100%."""
    await _pactl("set-sink-volume", cfg.sink or "@DEFAULT_SINK@", change)
    v = await get_volume(cfg)
    if v is not None and v > 100:
        await _pactl("set-sink-volume", cfg.sink or "@DEFAULT_SINK@", "100%")
        v = 100
    return v
