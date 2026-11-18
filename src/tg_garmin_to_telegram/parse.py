import base64
import html
import re
from typing import Optional

LINK_RE = re.compile(r"https?://livetrack\.garmin\.com/session/[A-Za-z0-9\-]+(?:/token/[A-Za-z0-9\-]+)?", re.I)
SESSION_RE = re.compile(r"https?://livetrack\.garmin\.com/session/([A-Za-z0-9\-]+)", re.I)


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _bodies(part: dict):
    if part.get("body", {}).get("data"):
        yield _b64(part["body"]["data"])
    for p in part.get("parts", []) or []:
        yield from _bodies(p)


def header(msg: dict, name: str) -> str:
    """All values of one header joined (a mail can carry several Delivered-To lines)."""
    values = [h["value"] for h in msg.get("payload", {}).get("headers", [])
              if h.get("name", "").lower() == name.lower()]
    return ", ".join(v for v in values if v)



def livetrack_link(msg: dict) -> Optional[str]:
    """First LiveTrack session URL in a Gmail API `messages.get(format=full)` payload."""
    for body in _bodies(msg.get("payload", {})):
        m = LINK_RE.search(html.unescape(body))
        if m:
            return m.group(0)
    return None


def session_id(link: str) -> Optional[str]:
    """The session uuid of a LiveTrack URL, without the token part."""
    m = SESSION_RE.search(link or "")
    return m.group(1) if m else None


def session_url(link: str) -> Optional[str]:
    """The LiveTrack URL with the token part removed, safe to log or persist."""
    sid = session_id(link)
    return f"https://livetrack.garmin.com/session/{sid}" if sid else None
