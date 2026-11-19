import re

BOT_TOKEN_URL_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
BOT_TOKEN_BARE_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")


def redact(text) -> str:
    """Remove Bot API tokens from a URL or an exception message before it is logged. Both forms
    are covered: the URL form (bot<digits>:...) and a bare token pasted into an error text."""
    return BOT_TOKEN_BARE_RE.sub("bot<redacted>",
                                 BOT_TOKEN_URL_RE.sub("bot<redacted>", str(text)))
