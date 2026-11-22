"""Call state, built from baresip events, plus the actions the MCP tools call."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field

from . import audio
from .baresip import Baresip
from .config import Config, Line
from .numbers import _il_groups, normalise, spoken

log = logging.getLogger("speakerphone.phone")


@dataclass
class Call:
    id: str
    direction: str  # incoming | outgoing
    number: str
    caller: str  # contact name, else display name, else number
    line: str | None
    state: str = "ringing"  # ringing | dialling | remote_ringing | active | closed
    started: float = field(default_factory=time.time)
    answered: float | None = None
    auto_answered: bool = False


def _uri_user(uri: str) -> str:
    m = re.match(r"^(?:sips?:)?([^@;>]+)", uri.strip("<>"))
    return m.group(1) if m else uri


def speak_number(num: str) -> str:
    """Digits for TTS, in groups; Israeli numbers in their familiar local form (054 123 4567)."""
    if num.startswith("+972") and len(num) > 8:
        g = _il_groups(num[4:])
        return spoken(["0" + g[0]] + g[1:])
    if num.startswith("+") and len(num) > 8:
        return spoken([num[:4]] + [num[4 + i:7 + i] for i in range(0, len(num) - 4, 3)])
    return spoken([num])


class Phone:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bs = Baresip(cfg, self._on_event, on_connect=self.refresh_registration)
        self.calls: dict[str, Call] = {}
        self.last: Call | None = None
        self.registered: bool | None = None
        self.reg_detail = ""
        self.muted = False
        self._announcer: asyncio.Task | None = None
        self._by_number = {self._key(k): v for k, v in cfg.contacts.items()}

    # ---- lookup helpers ----
    @staticmethod
    def _key(num: str) -> str:
        n = normalise(num, "IL")
        return n.dial if n.ok else re.sub(r"\D", "", num)

    def contact_name(self, number: str) -> str | None:
        return self._by_number.get(self._key(number))

    def find_contact(self, name: str) -> tuple[str, str] | None:
        want = name.strip().lower()
        for num, who in self.cfg.contacts.items():
            if who.lower() == want:
                return who, num
        hits = [(w, n) for n, w in self.cfg.contacts.items() if want in w.lower()]
        return hits[0] if len(hits) == 1 else None

    def line_by_name(self, name: str | None) -> Line | None:
        if not name:
            return self.cfg.default_line()
        n = name.lower()
        return next((l for l in self.cfg.lines if n in l.name.lower() or n == l.match_prefix.strip().lower()
                     or any(n in a.lower() or a.lower() in n for a in l.aliases)), None)

    def current(self) -> Call | None:
        live = [c for c in self.calls.values() if c.state != "closed"]
        return live[-1] if live else None

    async def refresh_registration(self) -> None:
        """Registration can complete before ctrl_tcp connects, so read it once from reginfo."""
        await asyncio.sleep(1.5)
        try:
            r = await self.bs.command("reginfo")
        except Exception as e:
            log.warning("reginfo failed: %s", e)
            return
        data = re.sub(r"\x1b\[[;0-9]*m", "", r.get("data") or "")
        self.registered = bool(re.search(r"\bOK\b", data))
        self.reg_detail = data.strip().splitlines()[-1].strip() if data.strip() else ""

    # ---- events ----
    async def _on_event(self, ev: dict) -> None:
        t, cid = ev.get("type", ""), str(ev.get("id", ""))
        log.info("event %s %s %s %s", t, cid, ev.get("peeruri", ""), ev.get("peerdisplayname", ""))
        if t == "REGISTER_OK":
            self.registered, self.reg_detail = True, ev.get("param", "")
        elif t in ("REGISTER_FAIL", "UNREGISTERING"):
            self.registered, self.reg_detail = False, ev.get("param", "")
        elif t == "CALL_INCOMING":
            number = _uri_user(ev.get("peeruri", ""))
            display = ev.get("peerdisplayname", "") or ""
            line = None
            for l in self.cfg.lines:
                if l.match_prefix and display.startswith(l.match_prefix):
                    line, display = l.name, display[len(l.match_prefix):].strip()
                    break
            name = self.contact_name(number)
            if not name and display and re.sub(r"\D", "", display) != re.sub(r"\D", "", number):
                name = display
            c = Call(cid, "incoming", number, name or number, line)
            self.calls[cid] = c
            self.last = c
            self._start_announcer(c)
        elif t == "CALL_OUTGOING":
            c = self.calls.get(cid)
            if c is None:
                number = _uri_user(ev.get("peeruri", ""))
                c = Call(cid, "outgoing", number, self.contact_name(number) or number, None, "dialling")
                self.calls[cid] = c
            self.last = c
        elif t == "CALL_RINGING":
            if (c := self.calls.get(cid)) and c.direction == "outgoing":
                c.state = "remote_ringing"
        elif t in ("CALL_ESTABLISHED", "CALL_ANSWERED"):
            if c := self.calls.get(cid):
                if c.direction == "incoming" and c.state == "ringing" and time.time() - c.started < 1.5:
                    c.auto_answered = True
                c.state, c.answered = "active", c.answered or time.time()
            self._stop_announcer()
        elif t == "CALL_CLOSED":
            if c := self.calls.pop(cid, None):
                c.state = "closed"
                self.last = c
            if not self.current():
                self.muted = False
                self._stop_announcer()

    # ---- ring + announcement ----
    def _start_announcer(self, c: Call) -> None:
        self._stop_announcer()
        self._announcer = asyncio.create_task(self._announce(c))

    def _stop_announcer(self) -> None:
        if self._announcer and not self._announcer.done():
            self._announcer.cancel()
        self._announcer = None

    def announcement(self, c: Call) -> str:
        caller = c.caller if c.caller != c.number else speak_number(c.number)
        if not c.number or c.number.lower() in ("anonymous", "unknown", "restricted"):
            caller = "a withheld number"
        line_part = f" on {c.line}" if c.line else ""
        return self.cfg.announce_template.format(caller=caller, line_part=line_part, line=c.line or "")

    async def _announce(self, c: Call) -> None:
        await asyncio.sleep(0.6)  # auto-answered pages are established by now
        if c.state != "ringing":
            return
        wav = None
        try:
            text = self.announcement(c)
            log.info("announcing: %s", text)
            try:
                wav = await audio.synthesize(self.cfg, text)
            except Exception as e:  # TTS down: still ring
                log.warning("TTS failed: %s", e)
            deadline = time.time() + 60
            n = 0
            while c.state == "ringing" and time.time() < deadline:
                await audio.play(self.cfg, audio.RING, self.cfg.ring_seconds)
                if wav and n < self.cfg.announce_repeats and c.state == "ringing":
                    await audio.play(self.cfg, wav)
                    n += 1
                await asyncio.sleep(0.4)
        finally:
            if wav:
                wav.unlink(missing_ok=True)

    # ---- actions ----
    def status(self) -> dict:
        c = self.current() or self.last
        out: dict = {"registered": self.registered, "muted": self.muted}
        if self.registered is False:
            out["registration_error"] = self.reg_detail
        if c:
            d = asdict(c)
            d["seconds"] = round(time.time() - (c.answered or c.started))
            d["number_spoken"] = speak_number(c.number)
            out["call"] = d
        else:
            out["call"] = None
        return out

    async def _cmd(self, command: str, params: str = "") -> dict:
        r = await self.bs.command(command, params)
        return {"ok": bool(r.get("ok")), "detail": (r.get("data") or "").strip()[:300]}

    async def answer(self) -> dict:
        c = self.current()
        if not c or c.state != "ringing":
            return {"ok": False, "detail": "nothing is ringing"}
        self._stop_announcer()
        return await self._cmd("accept")

    async def reject(self) -> dict:
        c = self.current()
        if not c or c.state != "ringing":
            return {"ok": False, "detail": "nothing is ringing"}
        self._stop_announcer()
        return await self._cmd("hangup")

    async def hangup(self) -> dict:
        if not self.current():
            return {"ok": False, "detail": "no call in progress"}
        self._stop_announcer()
        return await self._cmd("hangup")

    async def mute(self, muted: bool) -> dict:
        c = self.current()
        if not c or c.state != "active":
            return {"ok": False, "detail": "no active call"}
        if muted == self.muted:
            return {"ok": True, "muted": self.muted, "detail": "already " + ("muted" if muted else "unmuted")}
        r = await self._cmd("mute")
        if r["ok"]:
            self.muted = muted
        return {**r, "muted": self.muted}

    async def dial(self, number_or_contact: str, line: str | None, confirmed: bool) -> dict:
        if self.current():
            return {"ok": False, "detail": "a call is already in progress; hang up first"}
        who = None
        target = number_or_contact
        if not re.search(r"\d", number_or_contact):
            hit = self.find_contact(number_or_contact)
            if not hit:
                return {"ok": False, "detail": f"no contact called {number_or_contact!r}; ask for the number"}
            who, target = hit
        n = normalise(target, self.cfg.number_locale, self.cfg.internal_pattern)
        if not n.ok:
            return {"ok": False, "detail": n.error}
        emergency = n.dial in self.cfg.emergency_numbers
        ln = None if n.kind == "extension" else self.line_by_name(line)
        if line and n.kind != "extension" and not ln:
            return {"ok": False, "detail": f"unknown line {line!r}; lines: {[l.name for l in self.cfg.lines]}"}
        if not confirmed and not emergency:
            on = f" on {ln.name}" if ln else ""
            return {"ok": True, "status": "needs_confirmation", "dial": n.dial, "kind": n.kind,
                    "contact": who, "line": ln.name if ln else None,
                    "say": f"Calling {who + ', ' if who else ''}{n.readback}{on}. Shall I dial?",
                    "next": "after a spoken yes, call phone_call again with the same number and confirmed=true"}
        host = self.cfg.sip_aor.split("@", 1)[1]
        prefix = ln.dial_prefix if ln else ""
        uri = f"sip:{prefix}{n.dial}@{host}"
        self.muted = False
        r = await self._cmd("dial", uri)
        return {**r, "status": "dialling" if r["ok"] else "failed", "dial": n.dial,
                "line": ln.name if ln else None, "emergency": emergency, "contact": who}
