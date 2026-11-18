from tg_garmin_to_telegram.redact import redact


def test_bot_token_in_url_is_redacted():
    out = redact("POST https://api.telegram.org/bot123456789:AAE-bot_TOKENx9/sendMessage failed")
    assert "123456789:AAE-bot_TOKENx9" not in out
    assert "bot<redacted>" in out


def test_token_inside_exception_text_is_redacted():
    out = redact(RuntimeError("sendMessage: chat not found (bot42:abcDEF_-123)"))
    assert "bot42:abcDEF" not in out
    assert "bot<redacted>" in out


def test_plain_text_is_untouched():
    assert redact("chat -100123: everything fine") == "chat -100123: everything fine"
    assert redact(None) == "None"
