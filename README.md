# 💩 Telegram Anti-Spam Bot

This is a Telegram bot that helps keep chats clean by combining community moderation with LLM-based spam detection (OpenAI `gpt-5.6-luna`). Users flag suspicious messages by reacting with a 💩 emoji, and the bot takes moderation actions automatically.

## 🔍 Features

- 💩 A single 💩 reaction triggers a spam check on the message
- ⚡ Instant delete + ban (no LLM call) for obvious spam patterns: forwarded messages, channel quotes (external reply), and short filler text with a link
- 🧠 Other flagged messages are analyzed by OpenAI (`gpt-5.6-luna`) against known spam patterns — confirmed spam is deleted and the sender is banned
- 🤝 Community fallback: **4+ 💩 reactions** delete the message without banning, and the admin is notified for review
- 🧹 Auto-deletes "user joined the group" service messages
- 🗃 Keeps in-memory caches of recent messages and reaction counts (no persistence)

## 🛠 Setup

### 1. Clone the repo

```bash
git clone git@github.com:alexndr-novikov/llm-shield-bot.git
cd llm-shield-bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create a `.env` file

Copy `.env.sample` or create a new one with the following:

```
TELEGRAM_BOT_TOKEN=your-telegram-token
OPENAI_API_KEY=your-openai-api-key
```

### 4. Run the bot

```bash
python bot.py
```

## 💡 How it works

- Every text/caption message is cached by the bot (long-polling, no webhooks).
- On the **first** 💩 reaction:
  - Forwarded message, channel quote, or short text + link ➜ deleted and sender banned immediately.
  - Otherwise the message text is sent to GPT with a spam-detection prompt built from real spam examples. If GPT replies "YES" ➜ message deleted, sender banned.
- If a message accumulates **4+ 💩 reactions** ➜ it is deleted (sender is *not* banned) and the admin is pinged to review.
- Messages posted before the bot (re)started can't be LLM-checked — the cache is in-memory only.

## 📦 Deployment

Deployed to [Fly.io](https://fly.io) (region: `ams`):

```bash
make deploy
```

Or run anywhere with Docker:

```bash
docker build -t llm-shield-bot .
docker run --env-file .env llm-shield-bot
```

## ✍️ Configuration

You can adjust these constants in the code:

```python
MAX_CACHE_SIZE = 100        # Number of recent messages to remember
ADMIN_USERNAME = "..."      # Admin to ping for community-flagged messages
EXAMPLE_SPAM = [ ... ]      # Extend this list with your own spam patterns
```

## ⚠️ Disclaimer

This bot uses AI for moderation, but it may not be perfect. Always monitor its behavior before giving it full moderation powers in active communities.

---

Made with 💩 by [alexndr.novikov](https://github.com/alexndr-novikov)
