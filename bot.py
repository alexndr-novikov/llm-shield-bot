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
message_cache = OrderedDict()
checked_message_ids = set()

EXAMPLE_SPAM = [
    "Здравствуйте, возьму 3 человек, 170 долларов в день. Пишите плюс в личные сообщения, расскажу подробности",
    "Ищем пару ответственных людей (от 18 лет) для удаленной деятельности (легально). Подробности — в личные сообщения!",
    "Хочешь получать дополнительный доход? Можно зарабатывать до 19 000₽ в день без сложностей. Заинтересован? Пиши в личные сообщения",
    "Здравствуйте, 700 долларов за 3 дня. Пишите старт в личные сообщения, расскажу детали",
    "!!!! Всем привет, ищу партнеров для удаленного сотрудничества в сфере криптовалюты, 21+. За подробностями в лс",
    "Всем привет! Для тех, кто хочет зарабатывать дополнительно, есть возможность получить от 200 долларов в день. Пишите в личку, если заинтересованы!",
    "Всем привет! Для тех, кто хочет зарабатывать дополнительно, есть возможность получить от 200 долларов в день. Пишите в личку, если заинтересованы!",
]

# --- Core Functionalities ---
def build_prompt(message_text: str) -> str:
    examples = "\n".join(f"- {ex}" for ex in EXAMPLE_SPAM)
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
        f"- More than 12 emojis is *always* spam\n\n"
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

async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    key = (msg.chat.id, msg.message_id)
    message_cache[key] = msg

    if len(message_cache) > MAX_CACHE_SIZE:
        message_cache.popitem(last=False)

async def handle_spam_action(chat_id: int, message_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Deleted message {message_id} from user {user_id}")

        # Uncomment to enforce ban
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(f"Banned user {user_id} from chat {chat_id}")

    except Exception as e:
        logger.error(f"Failed to take spam action: {e}")

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction = update.message_reaction
    if not (reaction and reaction.new_reaction):
        return

    if reaction.new_reaction[0].emoji != "💩":
        return

    key = (reaction.chat.id, reaction.message_id)

    # Count 💩 emojis
    # poop_count = sum(1 for r in reaction.new_reaction if r.emoji == "💩")
    # if poop_count >= 4:
    #     original_message = message_cache.get(key)
    #     if not original_message:
    #         logger.warning("Original message not found in cache.")
    #         return
    #     user_id = original_message.from_user.id
    #     await handle_spam_action(
    #         chat_id=reaction.chat.id,
    #         message_id=reaction.message_id,
    #         user_id=user_id,
    #         context=context
    #     )
    #     return

    if key in checked_message_ids:
        return

    checked_message_ids.add(key)
    original_message = message_cache.get(key)
    if not original_message:
        logger.warning("Original message not found in cache.")
        return

    prompt = build_prompt(original_message.text or original_message.caption or "")
    result = await call_chatgpt(prompt)
    logger.info(f"GPT result: {result}")

    if result.lower().startswith("yes"):
        user_id = original_message.from_user.id
        await handle_spam_action(
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            user_id=user_id,
            context=context
        )

# --- Bot Initialization ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_new_message))
    app.add_handler(MessageReactionHandler(handle_reaction))

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=["message", "message_reaction"])
