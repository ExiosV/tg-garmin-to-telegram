import re

BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def redact(text) -> str:
    """Remove a Bot API token from a URL or an exception message before it is logged."""
    return BOT_TOKEN_RE.sub("bot<redacted>", str(text))
