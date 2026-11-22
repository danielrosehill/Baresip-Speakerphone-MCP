"""Settings: one JSON file (user data, mounted at /config/speakerphone.json) plus the SIP password from the environment.

Nothing user-specific lives in the code. See config.example.json for every key.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Line:
    name: str  # spoken, e.g. "the home line"
    match_prefix: str = ""  # caller-ID name prefix the PBX adds on inbound calls, e.g. "Res "
    dial_prefix: str = ""  # digits the PBX expects in front of an outbound number to use this line
    default: bool = False
    aliases: list[str] = field(default_factory=list)  # other words for it, e.g. ["business", "work"]


@dataclass
class Config:
    sip_aor: str  # e.g. sip:201@192.168.1.10
    sip_password: str
    display_name: str = "Speakerphone"
    sink: str = ""  # PulseAudio/PipeWire sink name ("" = default)
    source: str = ""
    sip_listen: str = "0.0.0.0:5070"
    rtp_ports: str = "10000-10100"
    ctrl_port: int = 4444
    tts_host: str = "127.0.0.1"
    tts_port: int = 10200
    tts_voice: str | None = None
    announce_template: str = "Call from {caller}{line_part}."
    announce_repeats: int = 6
    ring_seconds: float = 2.0
    number_locale: str = "IL"
    lines: list[Line] = field(default_factory=list)
    contacts: dict[str, str] = field(default_factory=dict)  # number (E.164 or extension) -> name
    emergency_numbers: list[str] = field(default_factory=lambda: ["100", "101", "102"])
    internal_pattern: str = r"^\d{3,5}$"  # numbers dialled as is on the PBX (extensions, short codes)
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8840

    def default_line(self) -> Line | None:
        return next((l for l in self.lines if l.default), self.lines[0] if self.lines else None)


def load(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("SPEAKERPHONE_CONFIG", "/config/speakerphone.json"))
    raw = json.loads(p.read_text())
    pw = os.environ.get("SIP_PASSWORD", "")
    pw_file = os.environ.get("SIP_PASSWORD_FILE")
    if not pw and pw_file:
        pw = Path(pw_file).read_text().strip()
    if not pw:
        raise SystemExit("SIP_PASSWORD or SIP_PASSWORD_FILE is required")
    raw["lines"] = [Line(**l) for l in raw.get("lines", [])]
    raw.pop("$comment", None)
    return Config(sip_password=pw, **raw)
