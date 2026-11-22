"""Deterministic phone-number normalisation. A dropped digit dials a stranger, so the model never formats numbers.

normalise(raw, locale) -> Number(ok, dial, kind, readback, error)
  dial      what to send to the PBX (E.164 "+9725...", or a short code / extension as is)
  readback  the number in spoken groups, digit by digit, for the confirm step

Locale "IL" (Israel):
  local 0-prefixed numbers -> +972 without the 0; mobile 05x and VoIP 07x are 10 digits, landline 02/03/04/08/09 is
  9 digits, 1-800/1-700 are 10 digits; +972 / 972 kept; 00 or + with another country code is international, as given;
  short codes (1xx such as 101) and star numbers (*2700) are dialled as is.
Other locales: E.164 input only (+ or 00 prefix); anything else is returned as an error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DIGIT_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
               "7": "seven", "8": "eight", "9": "nine", "*": "star", "#": "hash", "+": "plus"}


@dataclass
class Number:
    ok: bool
    dial: str = ""
    kind: str = ""  # mobile | landline | voip | toll_free | international | short_code | star | extension
    readback: str = ""
    error: str = ""


def spoken(groups: list[str]) -> str:
    return ", ".join(" ".join(DIGIT_WORDS.get(c, c) for c in g) for g in groups if g)


def _clean(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"[\s\-\.\(\)/]", "", s)
    return s


def _il_groups(national: str) -> list[str]:
    """national = digits after +972 (no leading 0)."""
    if national.startswith(("5", "7")) and len(national) == 9:  # 54 123 4567
        return [national[:2], national[2:5], national[5:]]
    if national.startswith("1") and len(national) == 9:  # 1800 123 456
        return [national[:4], national[4:7], national[7:]]
    return [national[:1], national[1:4], national[4:]]  # 2 434 5678


def _il_kind(national: str) -> tuple[str, int] | None:
    """Expected kind and national length (without the 0) for an Israeli number."""
    if re.match(r"^5\d", national):
        return "mobile", 9
    if re.match(r"^7[2-9]", national):
        return "voip", 9
    if re.match(r"^[23489]", national):
        return "landline", 8
    if re.match(r"^1(800|801|700|599)", national):
        return "toll_free", 9
    return None


def normalise(raw: str, locale: str = "IL", internal_pattern: str = r"^\d{3,5}$") -> Number:
    s = _clean(raw)
    if not s:
        return Number(False, error="empty number")
    if not re.fullmatch(r"\+?[\d\*#]+", s):
        return Number(False, error=f"not a phone number: {raw!r}")

    if s.startswith("*"):
        if re.fullmatch(r"\*\d{2,6}", s):
            return Number(True, s, "star", spoken([s[0], s[1:]]))
        return Number(False, error=f"star number looks wrong: {s}")

    if s.startswith("00"):
        s = "+" + s[2:]

    if locale == "IL":
        if s.startswith("+972") or (s.startswith("972") and len(s) >= 11):
            national = s.lstrip("+")[3:].lstrip("0")
            return _il_e164(national, raw)
        if s.startswith("+"):
            return _international(s)
        if re.fullmatch(r"1\d{2}", s):  # 100 police, 101 MDA, 102 fire, 1xx services
            return Number(True, s, "short_code", spoken([s]))
        if s.startswith("0"):
            return _il_e164(s[1:], raw)
        if re.fullmatch(internal_pattern, s):
            return Number(True, s, "extension", spoken([s]))
        if re.fullmatch(r"1(800|801|700|599)\d{6}", s):
            return _il_e164(s, raw)
        return Number(False, error=f"{raw!r} has no leading 0 or country code; ask for the full number")

    if s.startswith("+"):
        return _international(s)
    if re.fullmatch(internal_pattern, s):
        return Number(True, s, "extension", spoken([s]))
    return Number(False, error=f"locale {locale}: give the number in international format (+country code)")


def _il_e164(national: str, raw: str) -> Number:
    k = _il_kind(national)
    if not k:
        return Number(False, error=f"{raw!r} is not a valid Israeli number prefix")
    kind, length = k
    if len(national) != length:
        local_len = length + 1
        return Number(False, error=f"{raw!r} has {len(national) + 1} digits; an Israeli {kind} number has "
                                   f"{local_len} (with the leading 0). Ask for it again")
    return Number(True, "+972" + national, kind, spoken(["+972"] + _il_groups(national)))


def _international(s: str) -> Number:
    digits = s[1:]
    if not 8 <= len(digits) <= 15:
        return Number(False, error=f"{s} is not a valid international number (8-15 digits after +)")
    groups = [s[:4]] + [digits[3 + i:3 + i + 3] for i in range(0, len(digits) - 3, 3)]
    return Number(True, s, "international", spoken(groups))
