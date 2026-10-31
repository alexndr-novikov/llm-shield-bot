import asyncio
import logging
import os
from dotenv import load_dotenv
from collections import OrderedDict

from openai import AsyncOpenAI
from typesafe_sdk import AsyncTypeSafeClient, Noul
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

# --- Configuration & Setup ---
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)
# Jev (TypeSafe) runs in shadow mode: it classifies every message alongside OpenAI,
# but decisions are still made by OpenAI. Disagreements are reported to the chat.
jev_client = AsyncTypeSafeClient(api_key=TYPESAFE_API_KEY, model="jev-latest") if TYPESAFE_API_KEY else None
JEV_SPAM_THRESHOLD = 0.5  # Noul probability at or above which Jev's verdict counts as "spam"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Constants & Caches ---
MAX_CACHE_SIZE = 100
ADMIN_USERNAME = "alexndrnovikov"  # Admin to notify for community-flagged messages
message_cache = OrderedDict()
checked_message_ids = set()

# Reaction tracking: {(chat_id, message_id): {emoji: count}}
reaction_cache = OrderedDict()
MAX_REACTION_CACHE_SIZE = 200

EXAMPLE_SPAM = [
    "Здравствуйте, возьму 3 человек, 170 долларов в день. Пишите плюс в личные сообщения, расскажу подробности",
    "Ищем пару ответственных людей (от 18 лет) для удаленной деятельности (легально). Подробности — в личные сообщения!",
    "Хочешь получать дополнительный доход? Можно зарабатывать до 19 000₽ в день без сложностей. Заинтересован? Пиши в личные сообщения",
    "Здравствуйте, 700 долларов за 3 дня. Пишите старт в личные сообщения, расскажу детали",
    "!!!! Всем привет, ищу партнеров для удаленного сотрудничества в сфере криптовалюты, 21+. За подробностями в лс",
    "Всем привет! Для тех, кто хочет зарабатывать дополнительно, есть возможность получить от 200 долларов в день. Пишите в личку, если заинтересованы!",
    "Нужны сотрудники на производство упаковки. Оплата от 18 000 рублей, в неделю",
    "Есть темка на 5.000, надо выйти на улицу кое-что сделать",
    "Извините что не в тему, помогите поменять дома лампочки , еще и накормлю , заплачу 3000р",
    "Пользуйтесь нашим новым VPN абсолютно бесплатно",
    "Спасибо!! [ссылка на VPN/прокси сервис]",
    "Помогло, спасибо большое! [ссылка на VPN/прокси сервис]",
    "🔥 ПОЧТИ 4 ЛЯМА ЗАЛЕТЕЛО, КРУТЬ!😎 ИГРАЮ👉 coinik.is-best.net",
    "Нужен человек выполнить небольшую задачу, вознаграждение 7000 рублей. Кому интересно — пишите",
    "Ищу того, кто сможет сделать работу за 150$, детали в личке",
]

SPAM_TRAITS = [
    "Promises of high income with little to no effort",
    "Vague job offers or business opportunities",
    "Encouragement to message privately (e.g., 'пишите в лс', 'пиши в личные сообщения')",
    "References to cryptocurrency or earnings in USD/RUB",
    "Excessive use of emojis or exclamation marks",
    "Artificial urgency (e.g., 'только сегодня', 'осталось 2 места')",
    "More than 12 emojis is *always* spam",
    "Short innocent-looking messages (e.g., 'Спасибо!!', 'Помогло!') that contain promotional links or link previews to VPN, proxy, or other services",
    "VPN, proxy, or free service promotions",
    "Casino, gambling, or betting promotions (e.g., fake winnings screenshots, links to gambling sites)",
    "Forwarded messages from unknown channels promoting products or services",
    "Messages with URLs to suspicious or promotional domains",
    "Any mention of cryptocurrency, tokens, blockchain, mining, NFT, DeFi, Web3, airdrops, or crypto earnings",
    "Any mention of casinos, gambling, betting, slots, poker, roulette, or betting platforms (1xbet, mostbet, pin-up, 1win, melbet, fonbet, etc.)",
    "Offers to do some task or job with a specific reward amount mentioned (e.g., 'сделать работу за 5000 рублей', 'вознаграждение 100$') — this is *always* spam",
]

# --- Core Functionalities ---
def extract_message_context(msg) -> dict:
    """Extract all relevant context from a message for spam analysis."""
    text = msg.text or msg.caption or ""
    urls = []
    if msg.entities:
        for ent in msg.entities:
            if ent.type in ("url", "text_link"):
                if ent.type == "text_link":
                    urls.append(ent.url)
                else:
                    urls.append(text[ent.offset:ent.offset + ent.length])
    if msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.type in ("url", "text_link"):
                if ent.type == "text_link":
                    urls.append(ent.url)
                else:
                    urls.append((msg.caption or "")[ent.offset:ent.offset + ent.length])

    is_forwarded = msg.forward_origin is not None
    forward_info = ""
    if is_forwarded and hasattr(msg.forward_origin, 'chat'):
        forward_info = f"forwarded from channel: {msg.forward_origin.chat.title or msg.forward_origin.chat.username or 'unknown'}"
    elif is_forwarded and hasattr(msg.forward_origin, 'sender_user_name'):
        forward_info = f"forwarded from user: {msg.forward_origin.sender_user_name}"

    return {
        "text": text,
        "urls": urls,
        "is_forwarded": is_forwarded,
        "forward_info": forward_info,
        "has_link_preview": msg.link_preview_options is not None if hasattr(msg, 'link_preview_options') else False,
    }


def context_parts(context: dict = None) -> list:
    """Human-readable extra facts about a message (URLs, forward origin)."""
    parts = []
    if context:
        if context.get("urls"):
            parts.append(f"URLs in message: {', '.join(context['urls'])}")
        if context.get("is_forwarded"):
            parts.append(f"Message is {context.get('forward_info', 'forwarded from another source')}")
    return parts


def build_prompt(message_text: str, context: dict = None) -> str:
    examples = "\n".join(f"- {ex}" for ex in EXAMPLE_SPAM)
    traits = "\n".join(f"- {t}" for t in SPAM_TRAITS)

    context_section = ""
    parts = context_parts(context)
    if parts:
        context_section = f"\nAdditional context about this message:\n" + "\n".join(f"- {p}" for p in parts) + "\n"

    return (
        f"You are a Telegram group moderation bot trained to detect and filter spam messages.\n"
        f"The following messages are real examples of spam banned from the group:\n"
        f"{examples}\n\n"
        f"Spam in this group typically includes messages with one or more of the following traits:\n"
        f"{traits}\n\n"
        f"{context_section}"
        f"Determine whether the following message is spam:\n\"{message_text}\"\n\n"
        f"Reply with a single word: YES if it is spam, or NO if it is not."
    )

async def call_chatgpt(prompt: str) -> str:
    try:
        response = await client.chat.completions.create(
            model="gpt-5.6-luna",
            # reasoning model: disable reasoning tokens, answer is a single YES/NO word
            reasoning_effort="none",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"ChatGPT API call failed: {e}")
        return "NO"


async def call_jev(message_text: str, context: dict = None):
    """Ask Jev (TypeSafe System One) for the probability that the message is spam.

    Returns a float in [0, 1], or None when Jev is not configured or the call failed.
    Shadow mode only: the result never affects moderation decisions.
    """
    if jev_client is None:
        return None
    try:
        question = Noul(
            instructions={
                "question": "Is this Telegram group message spam that should be removed and its author banned?",
                "context": "The group is a Russian-speaking Telegram community. Spam in this group typically has one or more of these traits.",
                "spam_traits": SPAM_TRAITS,
                "real_spam_examples_banned_from_the_group": EXAMPLE_SPAM,
                "additional_facts_about_this_message": context_parts(context),
            },
            criteria={
                "true": "The message is spam: unsolicited advertising, job/earnings scams, crypto, gambling, VPN promotion, or a paid-task offer.",
                "false": "The message is a legitimate contribution to the group conversation.",
            },
        )
        response = await jev_client.system_one(state=message_text, questions={"spam": question})
        return float(response.nouls["spam"].noul)
    except Exception as e:
        logger.error(f"Jev API call failed: {e}")
        return None

def update_reaction_count(chat_id: int, message_id: int, old_reactions: list, new_reactions: list):
    """Update reaction counts based on changes"""
    key = (chat_id, message_id)
    
    # Initialize if not exists
    if key not in reaction_cache:
        reaction_cache[key] = {}
    
    # Count old and new reactions by emoji
    old_counts = {}
    new_counts = {}
    
    for reaction in old_reactions:
        if reaction.emoji == "💩":
            old_counts["💩"] = old_counts.get("💩", 0) + 1
    
    for reaction in new_reactions:
        if reaction.emoji == "💩":
            new_counts["💩"] = new_counts.get("💩", 0) + 1
    
    # Calculate the difference
    old_poop = old_counts.get("💩", 0)
    new_poop = new_counts.get("💩", 0)
    
    # Update our cache
    current_total = reaction_cache[key].get("💩", 0)
    change = new_poop - old_poop
    reaction_cache[key]["💩"] = max(0, current_total + change)
    
    # Clean up cache if too large
    if len(reaction_cache) > MAX_REACTION_CACHE_SIZE:
        reaction_cache.popitem(last=False)
    
    return reaction_cache[key]["💩"]

async def handle_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-delete the default 'user joined the group' service messages."""
    msg = update.message
    if not msg:
        return
    try:
        await context.bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        logger.info(f"Deleted join message {msg.message_id} in chat {msg.chat.id}")
    except Exception as e:
        logger.error(f"Failed to delete join message: {e}")

def has_urls(msg) -> bool:
    """Check if a message contains any URLs."""
    if msg.entities:
        for ent in msg.entities:
            if ent.type in ("url", "text_link"):
                return True
    if msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.type in ("url", "text_link"):
                return True
    return False


def has_external_reply(msg) -> bool:
    """Check if a message quotes/replies to content from another chat (e.g. channel post)."""
    return getattr(msg, 'external_reply', None) is not None


def is_short_text_with_link(msg) -> bool:
    """Detect spam pattern: short filler text + URL/link preview.

    Examples: 'Спасиб' + link preview, 'Хоороший!!' + link preview.
    These are always spam — no LLM check needed.
    """
    text = msg.text or msg.caption or ""
    if not has_urls(msg):
        return False
    # Strip URLs from text to get just the "filler" part
    plain_text = text
    for ent in (msg.entities or []):
        if ent.type in ("url", "text_link"):
            url_text = text[ent.offset:ent.offset + ent.length]
            plain_text = plain_text.replace(url_text, "")
    plain_text = plain_text.strip()
    # Short filler text (< 40 chars) combined with a URL = spam
    return len(plain_text) < 40


async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    key = (msg.chat.id, msg.message_id)
    message_cache[key] = msg

    if len(message_cache) > MAX_CACHE_SIZE:
        message_cache.popitem(last=False)


async def notify_admin_about_removal(chat_id: int, poop_count: int, context: ContextTypes.DEFAULT_TYPE):
    """Notify admin when message is removed by community but user not banned"""
    try:
        notification_text = (
            f"🚨 Message removed by community ({poop_count} 💩 reactions)\n"
            f"📊 Reactions: {poop_count} 💩\n\n"
            f"@{ADMIN_USERNAME} Please review - ban user or improve detection?"
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=notification_text
        )
        
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

async def notify_jev_disagreement(chat_id: int, message_id: int, gpt_result: str, jev_prob: float,
                                   message_text: str, message_deleted: bool, context: ContextTypes.DEFAULT_TYPE):
    """Shadow mode: report to the chat when Jev's verdict differs from OpenAI's."""
    try:
        gpt_is_spam = gpt_result.lower().startswith("yes")
        gpt_verdict = "спам (сообщение удалено, пользователь забанен)" if gpt_is_spam else "не спам"
        jev_verdict = "спам" if jev_prob >= JEV_SPAM_THRESHOLD else "не спам"
        snippet = message_text.replace("\n", " ").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "…"
        text = (
            f"⚖️ Jev не согласен с вердиктом OpenAI по сообщению {message_id}.\n"
            f"OpenAI: {gpt_verdict}\n"
            f"Jev: {jev_verdict} (вероятность спама {jev_prob:.2f})\n"
            f"Текст: «{snippet}»"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=None if message_deleted else message_id,
        )
    except Exception as e:
        logger.error(f"Failed to notify about Jev disagreement: {e}")


async def handle_spam_action(chat_id: int, message_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, ban_user: bool = True, notify_admin: bool = False, original_message=None, poop_count: int = 0):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted message {message_id} from user {user_id}")

        if ban_user:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            logger.info(f"Banned user {user_id} from chat {chat_id}")
        else:
            logger.info(f"Message deleted but user {user_id} not banned (multiple reactions)")
            
            # Notify admin when message removed but user not banned
            if notify_admin:
                await notify_admin_about_removal(chat_id, poop_count, context)

    except Exception as e:
        logger.error(f"Failed to take spam action: {e}")

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not reaction:
        return

    # Check if this reaction change involves 💩 emoji
    old_has_poop = any(r.emoji == "💩" for r in reaction.old_reaction)
    new_has_poop = any(r.emoji == "💩" for r in reaction.new_reaction)
    
    if not old_has_poop and not new_has_poop:
        return  # No 💩 reactions involved

    key = (reaction.chat.id, reaction.message_id)

    # Update our reaction tracking system
    total_poop_count = update_reaction_count(
        reaction.chat.id, 
        reaction.message_id, 
        reaction.old_reaction, 
        reaction.new_reaction
    )
    
    logger.info(f"Message {reaction.message_id} now has {total_poop_count} 💩 reactions")

    original_message = message_cache.get(key)

    # Forwarded, external reply (channel quote), or short text + link: ban immediately on first 💩
    if original_message and (original_message.forward_origin is not None or has_external_reply(original_message) or is_short_text_with_link(original_message)):
        reason = "forwarded" if original_message.forward_origin else ("external reply" if has_external_reply(original_message) else "short text + link")
        logger.info(f"Message {reaction.message_id} ({reason}) got 💩 reaction — banning immediately")
        await handle_spam_action(
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            user_id=original_message.from_user.id,
            context=context,
            ban_user=True,
        )
        return

    # Check if we've reached the threshold
    if total_poop_count >= 4:
        if not original_message:
            logger.warning("Original message not found in cache.")
            return
        user_id = original_message.from_user.id
        await handle_spam_action(
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            user_id=user_id,
            context=context,
            ban_user=False,  # Only delete message, don't ban user for multiple reactions
            notify_admin=True,  # Notify admin for review
            original_message=original_message,
            poop_count=total_poop_count
        )
        return

    if key in checked_message_ids:
        return

    checked_message_ids.add(key)
    if not original_message:
        logger.warning("Original message not found in cache.")
        return

    msg_context = extract_message_context(original_message)
    prompt = build_prompt(msg_context["text"], msg_context)
    # OpenAI decides; Jev runs in parallel as a shadow classifier.
    result, jev_prob = await asyncio.gather(
        call_chatgpt(prompt),
        call_jev(msg_context["text"], msg_context),
    )
    logger.info(f"GPT result: {result}; Jev spam probability: {jev_prob}")

    gpt_is_spam = result.lower().startswith("yes")
    if gpt_is_spam:
        user_id = original_message.from_user.id
        await handle_spam_action(
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            user_id=user_id,
            context=context,
            ban_user=True  # Ban user when AI detects spam
        )

    if jev_prob is not None and (jev_prob >= JEV_SPAM_THRESHOLD) != gpt_is_spam:
        await notify_jev_disagreement(
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            gpt_result=result,
            jev_prob=jev_prob,
            message_text=msg_context["text"],
            message_deleted=gpt_is_spam,
            context=context,
        )

# --- Bot Initialization ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_join_message))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION | filters.FORWARDED, handle_new_message))
    app.add_handler(MessageReactionHandler(handle_reaction))

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=["message", "message_reaction"])
