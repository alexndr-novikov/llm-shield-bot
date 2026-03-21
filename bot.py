import logging
import os
from dotenv import load_dotenv
from collections import OrderedDict

from openai import AsyncOpenAI
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

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

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


def build_prompt(message_text: str, context: dict = None) -> str:
    examples = "\n".join(f"- {ex}" for ex in EXAMPLE_SPAM)

    context_section = ""
    if context:
        parts = []
        if context.get("urls"):
            parts.append(f"URLs in message: {', '.join(context['urls'])}")
        if context.get("is_forwarded"):
            parts.append(f"Message is {context.get('forward_info', 'forwarded from another source')}")
        if parts:
            context_section = f"\nAdditional context about this message:\n" + "\n".join(f"- {p}" for p in parts) + "\n"

    return (
        f"You are a Telegram group moderation bot trained to detect and filter spam messages.\n"
        f"The following messages are real examples of spam banned from the group:\n"
        f"{examples}\n\n"
        f"Spam in this group typically includes messages with one or more of the following traits:\n"
        f"- Promises of high income with little to no effort\n"
        f"- Vague job offers or business opportunities\n"
        f"- Encouragement to message privately (e.g., 'пишите в лс', 'пиши в личные сообщения')\n"
        f"- References to cryptocurrency or earnings in USD/RUB\n"
        f"- Excessive use of emojis or exclamation marks\n"
        f"- Artificial urgency (e.g., 'только сегодня', 'осталось 2 места')\n"
        f"- More than 12 emojis is *always* spam\n"
        f"- Short innocent-looking messages (e.g., 'Спасибо!!', 'Помогло!') that contain promotional links or link previews to VPN, proxy, or other services\n"
        f"- VPN, proxy, or free service promotions\n"
        f"- Casino, gambling, or betting promotions (e.g., fake winnings screenshots, links to gambling sites)\n"
        f"- Forwarded messages from unknown channels promoting products or services\n"
        f"- Messages with URLs to suspicious or promotional domains\n\n"
        f"{context_section}"
        f"Determine whether the following message is spam:\n\"{message_text}\"\n\n"
        f"Reply with a single word: YES if it is spam, or NO if it is not."
    )

async def call_chatgpt(prompt: str) -> str:
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"ChatGPT API call failed: {e}")
        return "NO"

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

def get_reaction_count(chat_id: int, message_id: int, emoji: str) -> int:
    """Get current reaction count for a specific emoji"""
    key = (chat_id, message_id)
    return reaction_cache.get(key, {}).get(emoji, 0)

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

def is_suspicious_message(msg) -> bool:
    """Check if a message should be proactively scanned for spam."""
    # Forwarded from channels
    if msg.forward_origin is not None:
        return True
    # Message contains URLs
    if msg.entities:
        for ent in msg.entities:
            if ent.type in ("url", "text_link"):
                return True
    if msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.type in ("url", "text_link"):
                return True
    return False


async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    key = (msg.chat.id, msg.message_id)
    message_cache[key] = msg

    if len(message_cache) > MAX_CACHE_SIZE:
        message_cache.popitem(last=False)

    # Proactive scan for suspicious messages (forwarded or containing links)
    if is_suspicious_message(msg):
        checked_message_ids.add(key)
        msg_context = extract_message_context(msg)
        prompt = build_prompt(msg_context["text"], msg_context)
        result = await call_chatgpt(prompt)
        logger.info(f"Proactive scan result for message {msg.message_id}: {result}")

        if result.lower().startswith("yes"):
            await handle_spam_action(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                user_id=msg.from_user.id,
                context=context,
                ban_user=True,
            )

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

    # Forwarded messages: ban immediately on first 💩 reaction
    original_message = message_cache.get(key)
    if original_message and original_message.forward_origin is not None:
        logger.info(f"Forwarded message {reaction.message_id} got 💩 reaction — banning immediately")
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
        original_message = message_cache.get(key)
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
    original_message = message_cache.get(key)
    if not original_message:
        logger.warning("Original message not found in cache.")
        return

    msg_context = extract_message_context(original_message)
    prompt = build_prompt(msg_context["text"], msg_context)
    result = await call_chatgpt(prompt)
    logger.info(f"GPT result: {result}")

    if result.lower().startswith("yes"):
        user_id = original_message.from_user.id
        await handle_spam_action(
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            user_id=user_id,
            context=context,
            ban_user=True  # Ban user when AI detects spam
        )

# --- Bot Initialization ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_join_message))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION | filters.FORWARDED, handle_new_message))
    app.add_handler(MessageReactionHandler(handle_reaction))

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=["message", "message_reaction"])
