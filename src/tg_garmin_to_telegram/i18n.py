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
        "ride_started": "🚴 {name}: активность началась\n\n📍 <a href=\"{link}\">Открыть карту</a>",
        "default_name": "велосипедист",
        "menu_address": "📎 Мой адрес LiveTrack",
        "menu_own_bot": "🤖 Свой бот",
        "menu_service_bot": "✅ Наш бот в группе",
        "menu_status": "📊 Статус",
        "menu_site": "🌐 Сайт",
        "menu_garmin_key": "🔑 Ключ Garmin",
        "address": (
            "📎 <b>Твой адрес LiveTrack</b>\n"
            "<code>{email_addr}</code>\n\n"
            "Добавь его как контакт LiveTrack в Garmin Connect (Настройки → LiveTrack → контакты). "
            "Когда активность начнётся — анонс придёт в твой чат автоматически."
        ),
        "own_bot_guide": (
            "🤖 <b>Свой бот</b>\n"
            "1. Создай бота у @BotFather (/newbot) и пришли мне его токен сообщением — "
            "оно удалится сразу после чтения.\n"
            "2. Добавь этого бота администратором в свою группу или канал.\n"
            "3. Я увижу чат сам и предложу подтвердить."
        ),
        "own_bot_token_ok": "✅ Токен принят: бот @{bot_name}. Добавь его администратором в свою группу или канал — я увижу чат и предложу подтвердить.",
        "own_bot_token_bad": "❌ Токен не работает ({error}). Проверь и пришли заново.",
        "chat_confirm": "👀 Вижу чат «{title}» ({kind}). Анонсы будут приходить сюда?",
        "chat_confirm_yes": "✅ Это тот чат",
        "target_set": "✅ Чат «{title}» подключён. Пришли тестовый анонс…",
        "test_sent": "🧪 Тестовый анонс отправлен в «{title}». Теперь пришли фото поездки боту — в альфе они лягут в тред после её завершения.",
        "service_bot_guide": (
            "✅ <b>Наш бот в твоей группе</b>\n"
            "Добавь @garmin_start_bot администратором в свою группу или канал — "
            "я увижу чат и предложу подтвердить."
        ),
        "garmin_key_guide": (
            "🔑 <b>Ключ Garmin Personal API</b>\n"
            "Пришли ключ сообщением — оно удалится сразу. Ключ нужен, чтобы определять конец "
            "поездки и собирать фото в альбом. Страница ключа: "
            "https://connect.garmin.com/app/settings/personalApiToken"
        ),
        "garmin_key_ok": "✅ Ключ Garmin принят (аккаунт: {name}).",
        "garmin_key_bad": "❌ Ключ не работает ({error}). Проверь и пришли заново.",
        "status": (
            "📊 <b>Статус</b>\n"
            "Состояние: {state}\n"
            "Доставка: {mode}\n"
            "Чат: {target}\n"
            "Адрес LiveTrack: <code>{email_addr}</code>\n"
            "Ключ Garmin: {garmin}"
        ),
        "state_setup": "настройка",
        "state_active": "активен",
        "mode_own": "свой бот",
        "mode_our": "наш бот",
        "not_set": "не задан",
        "no_garmin": "не привязан",
        "test_announce": "🧪 Тестовый анонс Garmin → Telegram: доставка работает!",
        "kind_group": "группа",
        "kind_channel": "канал",
        "dialog_cancelled": "Диалог отменён. /start — заново.",
        "no_active_ride": "Активной поездки сейчас нет — фото можно прислать только во время поездки.",
        "photo_saved": "✅ Фото сохранено — после завершения поездки попадёт в альбом.",
        "photo_too_big": "❌ Фото больше {mb} МБ — такое не сохранить. Пришли файл поменьше.",
        "photo_limit": "⚠️ Лимит — {n} фото на поездку. Это фото не сохранить.",
        "album_published": "🏁 Альбом поездки опубликован: {n} фото.",
        "ride_ended": (
            "🏁 {name}: поездка завершена — {km} км\n"
            "🔗 <a href=\"{link}\">Активность в Garmin</a>"
        ),
    },
    EN: {
        "start": (
            "🚴 <b>Garmin → Telegram</b>\n"
            "The bot announces the start of your Garmin LiveTrack activities: Garmin emails are read "
            "from a watched mailbox, and a notice appears in your own Telegram group or channel — "
            "sent as your own bot.\n\n"
            "📸 Alpha: ride photo albums are available — send the photos to the bot during the ride, "
            "and the album lands in the thread after the ride ends.\n\n"
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
        "ride_started": "🚴 {name}: the activity has started\n\n📍 <a href=\"{link}\">Open the map</a>",
        "default_name": "the rider",
        "menu_address": "📎 My LiveTrack address",
        "menu_own_bot": "🤖 My own bot",
        "menu_service_bot": "✅ Service bot in my group",
        "menu_status": "📊 Status",
        "menu_site": "🌐 Website",
        "menu_garmin_key": "🔑 Garmin key",
        "address": (
            "📎 <b>Your LiveTrack address</b>\n"
            "<code>{email_addr}</code>\n\n"
            "Add it as a LiveTrack contact in Garmin Connect (Settings → LiveTrack → contacts). "
            "When an activity starts, the announcement arrives in your chat automatically."
        ),
        "own_bot_guide": (
            "🤖 <b>My own bot</b>\n"
            "1. Create a bot with @BotFather (/newbot) and send me its token as a message — "
            "it is deleted right after I read it.\n"
            "2. Add that bot as an administrator to your group or channel.\n"
            "3. I will see the chat myself and offer a confirmation."
        ),
        "own_bot_token_ok": "✅ Token accepted: bot @{bot_name}. Add it as an administrator to your group or channel — I will see the chat and offer a confirmation.",
        "own_bot_token_bad": "❌ The token does not work ({error}). Check it and send it again.",
        "chat_confirm": "👀 I see the chat “{title}” ({kind}). Should announcements go there?",
        "chat_confirm_yes": "✅ This is the chat",
        "target_set": "✅ Chat “{title}” connected. Sending a test announcement…",
        "test_sent": "🧪 Test announcement sent to “{title}”. Send ride photos to the bot — in the alpha they land in the thread after the ride ends.",
        "service_bot_guide": (
            "✅ <b>Our bot in your group</b>\n"
            "Add @garmin_start_bot as an administrator to your group or channel — "
            "I will see the chat and offer a confirmation."
        ),
        "garmin_key_guide": (
            "🔑 <b>Garmin Personal API key</b>\n"
            "Send the key as a message — it is deleted immediately. The key lets me detect the end "
            "of a ride and collect photos into the album. Key page: "
            "https://connect.garmin.com/app/settings/personalApiToken"
        ),
        "garmin_key_ok": "✅ Garmin key accepted (account: {name}).",
        "garmin_key_bad": "❌ The key does not work ({error}). Check it and send it again.",
        "status": (
            "📊 <b>Status</b>\n"
            "State: {state}\n"
            "Delivery: {mode}\n"
            "Chat: {target}\n"
            "LiveTrack address: <code>{email_addr}</code>\n"
            "Garmin key: {garmin}"
        ),
        "state_setup": "setup",
        "state_active": "active",
        "mode_own": "own bot",
        "mode_our": "service bot",
        "not_set": "not set",
        "no_garmin": "not linked",
        "test_announce": "🧪 Garmin → Telegram test announcement: delivery works!",
        "kind_group": "group",
        "kind_channel": "channel",
        "dialog_cancelled": "Dialog cancelled. /start to begin again.",
        "no_active_ride": "No active ride right now — photos can only be sent during a ride.",
        "photo_saved": "✅ Photo saved — it will join the album when the ride ends.",
        "photo_too_big": "❌ The photo is larger than {mb} MB — it cannot be stored. Send a smaller one.",
        "photo_limit": "⚠️ The limit is {n} photos per ride. This one cannot be stored.",
        "album_published": "🏁 Ride album published: {n} photo(s).",
        "ride_ended": (
            "🏁 {name}: the ride is over — {km} km\n"
            "🔗 <a href=\"{link}\">Activity in Garmin</a>"
        ),
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
