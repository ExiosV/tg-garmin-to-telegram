import logging

from tg_botkit import Bot

log = logging.getLogger("tg_garmin_to_telegram")


class Telegram:
    """The service bot's own calls (getMe, self-check, operator notices); transport is tg_botkit.Bot.
    Per-user deliveries go through MultiBot instead: each user's announcements are sent as their own bot."""

    def __init__(self, token: str, api_base: str, channel: str = ""):
        # channel is the optional self-check chat (TELEGRAM_CHANNEL_ID); delivery never uses a fixed default
        self.bot = Bot(token, api_base, channel=channel or None)

    def send_message(self, chat_id, text: str, silent: bool = False) -> int:
        """Send one text message to an arbitrary chat (a user's own group or channel)."""
        return self.bot.send(text, chat_id=chat_id, silent=silent)

    def get_chat_title(self, chat_id) -> str:
        return self.bot.chat_title(chat_id)

    def delete_message(self, chat_id, message_id: int) -> bool:
        return self.bot.delete(message_id, chat_id=chat_id)

    def whoami(self) -> dict:
        return self.bot.whoami()

    def my_status(self, chat_id=None) -> str:
        """The bot's own role in a chat: administrator, member, left, ..."""
        return self.bot.my_status(chat_id)


class MultiBot:
    """Builds a Bot for ANY user's token: deliveries go out as the user's own bot, not as the service
    bot. Bots are cached per token; a user's row always carries the token it announced with."""

    def __init__(self, api_base: str):
        self.api_base = api_base
        self._bots: dict[str, Bot] = {}

    def bot_for(self, token: str) -> Bot:
        token = str(token or "")
        if not token:
            raise ValueError("empty bot token")
        bot = self._bots.get(token)
        if bot is None:
            bot = Bot(token, self.api_base)
            self._bots[token] = bot
        return bot
