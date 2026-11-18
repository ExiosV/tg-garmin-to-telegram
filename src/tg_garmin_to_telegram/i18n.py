"""Two-language message catalog. The client UI language (`language_code`) is the proxy per the
owner's rule: Russian only for "ru*" clients, everyone else gets English. /lang pins an override.
New users default to Russian (users.lang = 'ru')."""
import logging

log = logging.getLogger("tg_garmin_to_telegram")

RU, EN = "ru", "en"

MESSAGES = {
    RU: {
        "start": (
            "🚴 <b>Garmin → Telegram</b>\n"
            "Бот присылает уведомление, когда у вас стартует активность с Garmin LiveTrack: письмо от "
            "Garmin приходит на почту, и анонс появляется в вашей группе или канале в Telegram — от "
            "имени вашего собственного бота.\n\n"
            "📸 В альфа-версии доступно автоматическое добавление фото поездки в группу или в "
            "обсуждение постов канала: пришлите фото боту во время поездки — альбом появится в треде "
            "после её завершения.\n\n"
            "<b>Настройки</b>\n"
            "/lang ru|en — язык сообщений\n"
            "/help — справка"
        ),
        "help": (
            "Garmin → Telegram: анонсы старта активности в вашей группе или канале.\n"
            "/start — как работает сервис\n"
            "/lang ru|en — язык сообщений\n"
            "/help — это сообщение"
        ),
        "lang_set": "Язык сообщений: {lang}.",
        "lang_show": "Язык сообщений: {lang} (/lang ru|en).",
        "unknown": "Не понял команду. /help — список команд.",
        "ride_started": "🚴 {name} начал активность\n\n📍 <a href=\"{link}\">Смотреть на карту</a>",
    },
    EN: {
        "start": (
            "🚴 <b>Garmin → Telegram</b>\n"
            "The bot announces the start of your Garmin LiveTrack activities: the Garmin email lands in "
            "the watched mailbox, and a notice appears in your own Telegram group or channel — sent as "
            "your own bot.\n\n"
            "📸 In the alpha version, automatic adding of ride photos to the group or to the discussion "
            "thread of channel posts is available: send the photos to the bot during the ride — the "
            "album appears in the thread after it ends.\n\n"
            "<b>Settings</b>\n"
            "/lang ru|en — message language\n"
            "/help — help"
        ),
        "help": (
            "Garmin → Telegram: activity-start announcements in your own group or channel.\n"
            "/start — how the service works\n"
            "/lang ru|en — message language\n"
            "/help — this message"
        ),
        "lang_set": "Message language: {lang}.",
        "lang_show": "Message language: {lang} (/lang ru|en).",
        "unknown": "Unknown command. /help lists them.",
        "ride_started": "🚴 {name} started an activity\n\n📍 <a href=\"{link}\">View on map</a>",
    },
}


def normalize(code) -> str:
    """Client UI language -> message language; Russian only for "ru*" clients, English otherwise."""
    return RU if str(code or "").lower().startswith("ru") else EN


def text(state_user_lang: str, key: str, code=None, **kw) -> str:
    """Format one message: the user's stored language wins, then this message's language_code,
    then English."""
    lang = state_user_lang if state_user_lang in (RU, EN) else normalize(code)
    tpl = MESSAGES.get(lang, MESSAGES[EN]).get(key) or MESSAGES[EN].get(key, key)
    return tpl.format(**kw) if kw else tpl
