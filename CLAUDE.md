# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram anti-spam bot that combines community moderation (💩 emoji reactions) with LLM-based spam detection (OpenAI gpt-5.6-luna). Single-file Python application (`bot.py`).

## Commands

- **Run:** `python bot.py`
- **Install deps:** `pip install -r requirements.txt`
- **Deploy:** `make deploy` (runs `fly deploy` to Fly.io, region: ams)
- **Docker:** `docker build -t llm-shield-bot . && docker run --env-file .env llm-shield-bot`

No test suite or linter is configured.

## Environment Variables

Defined in `.env` (see `.env.sample`): `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, optional `TYPESAFE_API_KEY` (enables the Jev shadow classifier).

## Architecture

Everything lives in `bot.py`. The bot uses long-polling (not webhooks).

**Moderation paths:**
1. **Community path:** When a message accumulates 4+ 💩 reactions, it is deleted (user is NOT banned). Admin is notified for review.
2. **LLM path:** On the first 💩 reaction to a message, the bot sends the message text to GPT with a spam-detection prompt. If GPT says "YES", the message is deleted AND the user is banned.
3. **Jev shadow path (optional):** If `TYPESAFE_API_KEY` is set, Jev (TypeSafe System One, `Noul` question) is queried in parallel with GPT. It never affects the decision. When Jev's verdict (probability >= `JEV_SPAM_THRESHOLD`) differs from GPT's, `notify_jev_disagreement()` posts both verdicts to the chat.

**In-memory caches (OrderedDict, no persistence):**
- `message_cache` (max 100) — stores recent messages so reaction handlers can look up the original text.
- `reaction_cache` (max 200) — tracks cumulative 💩 reaction counts per message.
- `checked_message_ids` (set) — prevents re-checking the same message via LLM.

**Key functions:**
- `build_prompt()` — constructs the spam-detection prompt with hardcoded Russian-language spam examples.
- `call_chatgpt()` — async OpenAI API call; defaults to "NO" on error.
- `call_jev()` — async Jev call returning spam probability (0..1) or None if unconfigured/failed.
- `SPAM_TRAITS` / `EXAMPLE_SPAM` — shared by both the GPT prompt and the Jev question.
- `handle_new_message()` — caches every text/caption message.
- `handle_reaction()` — core logic: updates reaction counts, triggers LLM check or community removal.
- `handle_spam_action()` — deletes message and optionally bans user.

**Handlers registered:**
- `MessageHandler(filters.TEXT | filters.CAPTION)` → `handle_new_message`
- `MessageReactionHandler` → `handle_reaction`

## Git Conventions

- Do NOT add `Co-Authored-By` lines to commit messages.
