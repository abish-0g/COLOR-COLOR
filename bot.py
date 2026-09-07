"""
Telegram bot with 2 games:
1. Color-Color -> one player picks a secret color, everyone else guesses it via emoji buttons.
2. Truth & Dare -> users submit their own truths/dares, then challenge each other. The person
   being challenged picks Truth or Dare, and answers by replying directly to the bot's message.

Works in both group chats and private chats.

Setup:
    pip install -r requirements.txt
    export BOT_TOKEN="123456:ABC-your-token-from-BotFather"
    python bot.py
"""

import os
import json
import random
import logging
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import mention_html
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# DATA_DIR can be overridden (e.g. point it at a Railway Volume mount path like
# "/data") so truths/dares/scores survive redeploys. Defaults to the app folder.
DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "data.json")

# ---------------------------------------------------------------------------
# Persistent storage (truths, dares, scores) - survives bot restarts
# ---------------------------------------------------------------------------

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("data.json is corrupt/unreadable, starting fresh.")
            return {}
    return {}


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.exception("Failed to save data.json")


def get_chat_bucket(data, chat_id):
    key = str(chat_id)
    if key not in data:
        data[key] = {"truths": [], "dares": [], "scores": {}}
    data[key].setdefault("scores", {})  # backfill for buckets saved before scoring existed
    return data[key]


def update_score(chat_id, user_id, user_name, delta):
    """Add `delta` points for a user in a chat and persist it. Returns the new total."""
    data = load_data()
    bucket = get_chat_bucket(data, chat_id)
    key = str(user_id)
    entry = bucket["scores"].get(key, {"name": user_name, "score": 0})
    entry["name"] = user_name  # keep the display name fresh
    entry["score"] += delta
    bucket["scores"][key] = entry
    save_data(data)
    return entry["score"]


# ---------------------------------------------------------------------------
# Color-Color game
# ---------------------------------------------------------------------------

# Different "themes" so the emoji set changes round to round.
COLOR_THEMES = [
    {  # circles
        "red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢",
        "blue": "🔵", "purple": "🟣", "brown": "🟤", "black": "⚫", "white": "⚪",
    },
    {  # hearts
        "red": "❤️", "orange": "🧡", "yellow": "💛", "green": "💚",
        "blue": "💙", "purple": "💜", "brown": "🤎", "black": "🖤", "white": "🤍",
    },
    {  # squares
        "red": "🟥", "orange": "🟧", "yellow": "🟨", "green": "🟩",
        "blue": "🟦", "purple": "🟪", "brown": "🟫", "black": "⬛", "white": "⬜",
    },
]

COLOR_NAMES_EN = {
    "red": "Red", "orange": "Orange", "yellow": "Yellow", "green": "Green",
    "blue": "Blue", "purple": "Purple", "brown": "Brown", "black": "Black", "white": "White",
}

# chat_id -> game state dict
color_games = {}


def build_color_pick_keyboard():
    """Keyboard shown only for the giver, to secretly pick a color name."""
    names = list(COLOR_NAMES_EN.keys())
    random.shuffle(names)
    rows = []
    row = []
    for name in names:
        row.append(InlineKeyboardButton(COLOR_NAMES_EN[name], callback_data=f"cg_color:{name}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def build_guess_keyboard(theme):
    items = list(theme.items())
    random.shuffle(items)
    rows = []
    row = []
    for name, emoji in items:
        row.append(InlineKeyboardButton(emoji, callback_data=f"cg_guess:{name}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def colorgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    existing = color_games.get(chat_id)
    if existing and existing["phase"] != "done":
        await update.message.reply_text(
            "⚠️ A Color-Color game is already running in this chat.\n"
            "Use /endcolorgame to stop it first."
        )
        return

    color_games[chat_id] = {
        "phase": "claim", "giver_id": None, "giver_name": None,
        "theme": None, "color": None, "attempted": set(),
    }

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🙋 I'll pick a color!", callback_data="cg_claim")]]
    )
    await update.message.reply_text(
        "🎨 *Color-Color game started!*\n\n"
        "One player will pick a secret color, and everyone else has to guess its emoji.\n"
        "Who wants to go first?",
        reply_markup=kb,
        parse_mode="Markdown",
    )


async def endcolorgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in color_games:
        del color_games[chat_id]
        await update.message.reply_text("🛑 Color-Color game stopped.")
    else:
        await update.message.reply_text("There's no Color-Color game running in this chat.")


async def color_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    game = color_games.get(chat_id)

    if not game:
        await query.answer("This game isn't active anymore. Start a new one with /colorgame.", show_alert=True)
        return

    data = query.data

    # --- Step 1: someone claims the "giver" role ---
    if data == "cg_claim":
        if game["phase"] != "claim":
            await query.answer("Someone already claimed that role!", show_alert=True)
            return
        game["giver_id"] = user.id
        game["giver_name"] = user.first_name
        game["theme"] = random.choice(COLOR_THEMES)
        game["phase"] = "picking"
        await query.answer("Pick your secret color from the buttons below (only you can see/use them) 🤫", show_alert=True)
        await query.edit_message_text(
            f"🎨 *{user.first_name}* is thinking of a secret color... 🤔\n"
            f"(Only {user.first_name} can use these buttons)",
            reply_markup=build_color_pick_keyboard(),
            parse_mode="Markdown",
        )
        return

    # --- Step 2: giver secretly picks a color ---
    if data.startswith("cg_color:"):
        if game["phase"] != "picking":
            await query.answer("This step is already over.", show_alert=True)
            return
        if user.id != game["giver_id"]:
            await query.answer("⛔ It's not your turn - only the color-giver can pick!", show_alert=True)
            return
        color_name = data.split(":", 1)[1]
        game["color"] = color_name
        game["phase"] = "guessing"
        await query.answer(f"You picked '{COLOR_NAMES_EN[color_name]}' ✅ (kept secret)", show_alert=True)
        await query.edit_message_text(
            f"🎨 *{game['giver_name']}* has picked a secret color!\n\n"
            f"👇 Everyone else, guess the right emoji (the color-giver can't guess):",
            reply_markup=build_guess_keyboard(game["theme"]),
            parse_mode="Markdown",
        )
        return

    # --- Step 3: others try to guess ---
    if data.startswith("cg_guess:"):
        if game["phase"] != "guessing":
            await query.answer("This round is already over.", show_alert=True)
            return
        if user.id == game["giver_id"]:
            await query.answer("😅 You can't guess your own color!", show_alert=True)
            return
        if user.id in game["attempted"]:
            await query.answer("⛔ You've already used your guess for this round!", show_alert=True)
            return

        # Using up the guess now, whether it turns out right or wrong.
        game["attempted"].add(user.id)

        guess_name = data.split(":", 1)[1]
        if guess_name == game["color"]:
            emoji = game["theme"][game["color"]]
            new_score = update_score(chat_id, user.id, user.first_name, 2)
            await query.answer("🎉 Correct answer! (+2 points)", show_alert=False)
            await query.edit_message_text(
                f"🎉 *{user.first_name}* guessed it right! (+2 points, total: {new_score})\n\n"
                f"The secret color was: *{COLOR_NAMES_EN[game['color']]}* {emoji}\n\n"
                f"Send /colorgame to start a new round.",
                parse_mode="Markdown",
            )
            game["phase"] = "done"
            color_games.pop(chat_id, None)
        else:
            new_score = update_score(chat_id, user.id, user.first_name, -1)
            await query.answer("❌ Wrong! You've used your guess for this round.", show_alert=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ *{user.first_name}* guessed wrong! (-1 point, total: {new_score})\n"
                    f"They're out for this round - others can still try."
                ),
                parse_mode="Markdown",
            )
        return


async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    scores = bucket.get("scores", {})
    if not scores:
        await update.message.reply_text("No Color-Color scores yet in this chat.")
        return
    ranked = sorted(scores.values(), key=lambda e: e["score"], reverse=True)
    lines = [f"{i + 1}. {e['name']} — {e['score']} pts" for i, e in enumerate(ranked)]
    await update.message.reply_text("🏆 *Color-Color Scoreboard*\n\n" + "\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Truth & Dare game (user-submitted content)
# ---------------------------------------------------------------------------

# Two in-memory stages for a /td challenge, keyed by (chat_id, message_id):
#   td_pending    -> waiting for the target to press Truth/Dare
#   td_challenges -> waiting for the target to reply with their answer
td_pending = {}
td_challenges = {}


async def addtruth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Use it like this:\n`/addtruth What's your most embarrassing moment?`",
            parse_mode="Markdown",
        )
        return
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    bucket["truths"].append(text)
    save_data(data)
    await update.message.reply_text(f"✅ Truth added! (Total: {len(bucket['truths'])})")


async def adddare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Use it like this:\n`/adddare Dance non-stop for 1 minute`",
            parse_mode="Markdown",
        )
        return
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    bucket["dares"].append(text)
    save_data(data)
    await update.message.reply_text(f"✅ Dare added! (Total: {len(bucket['dares'])})")


async def truth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    if not bucket["truths"]:
        await update.message.reply_text("No truths added yet. Use /addtruth <question> first.")
        return
    pick = random.choice(bucket["truths"])
    await update.message.reply_text(f"🧐 *Truth* for {update.effective_user.first_name}:\n{pick}", parse_mode="Markdown")


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    if not bucket["dares"]:
        await update.message.reply_text("No dares added yet. Use /adddare <task> first.")
        return
    pick = random.choice(bucket["dares"])
    await update.message.reply_text(f"🔥 *Dare* for {update.effective_user.first_name}:\n{pick}", parse_mode="Markdown")


async def td_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Challenge someone to Truth or Dare.
    Usage: reply to the target person's message with /td
    This makes it explicit WHO is being challenged, lets the bot properly tag
    them, and ensures only they can pick Truth/Dare and answer it.
    """
    replied = update.message.reply_to_message
    if not replied or not replied.from_user or replied.from_user.is_bot:
        await update.message.reply_text(
            "To challenge someone, reply to one of their messages with /td.\n"
            "Example: reply to their message, then send /td"
        )
        return

    target = replied.from_user
    if target.id == update.effective_user.id:
        await update.message.reply_text("You can't challenge yourself! Reply to someone else's message.")
        return

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧐 Truth", callback_data="td_pick:truth"),
                InlineKeyboardButton("🔥 Dare", callback_data="td_pick:dare"),
            ]
        ]
    )
    target_mention = mention_html(target.id, target.first_name)
    sent = await update.message.reply_text(
        f"🎲 {target_mention}, Truth or Dare?\n(Only you can choose - tap a button below)",
        reply_markup=kb,
        parse_mode="HTML",
    )
    td_pending[(update.effective_chat.id, sent.message_id)] = {
        "target_id": target.id,
        "target_name": target.first_name,
    }


async def td_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    pending = td_pending.get((chat_id, message_id))

    if not pending:
        await query.answer("This challenge isn't active anymore.", show_alert=True)
        return
    if query.from_user.id != pending["target_id"]:
        await query.answer("This challenge isn't for you!", show_alert=True)
        return

    kind = query.data.split(":", 1)[1]
    data = load_data()
    bucket = get_chat_bucket(data, chat_id)
    pool = bucket["truths"] if kind == "truth" else bucket["dares"]

    await query.answer()

    if not pool:
        cmd = "/addtruth" if kind == "truth" else "/adddare"
        await query.edit_message_text(f"No {kind}s have been added yet. Add one first with {cmd} <text>.")
        td_pending.pop((chat_id, message_id), None)
        return

    pick = random.choice(pool)
    emoji = "🧐" if kind == "truth" else "🔥"
    target_mention = mention_html(pending["target_id"], pending["target_name"])

    await query.edit_message_text(
        f"{emoji} {kind.title()} for {target_mention}:\n{escape(pick)}\n\n"
        f"👉 {escape(pending['target_name'])}, reply to THIS message with your answer!",
        parse_mode="HTML",
    )

    td_challenges[(chat_id, message_id)] = {
        "target_id": pending["target_id"],
        "target_name": pending["target_name"],
        "kind": kind,
        "text": pick,
    }
    td_pending.pop((chat_id, message_id), None)


async def td_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Catches replies to an active Truth/Dare challenge message and posts a
    clean, formatted answer mentioning the target and quoting what they were given.
    Ignored if it's not a reply to an active challenge, or the replier isn't the target.
    """
    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    chat_id = update.effective_chat.id
    parent_id = msg.reply_to_message.message_id
    challenge = td_challenges.get((chat_id, parent_id))
    if not challenge:
        return
    if msg.from_user.id != challenge["target_id"]:
        return  # only the target's answer counts

    answer_text = msg.text or msg.caption or "[non-text answer]"
    emoji = "🧐" if challenge["kind"] == "truth" else "🔥"
    target_mention = mention_html(challenge["target_id"], challenge["target_name"])

    await msg.reply_text(
        f"{emoji} {target_mention} answered the {challenge['kind']}:\n"
        f"“{escape(challenge['text'])}”\n\n"
        f"💬 {escape(answer_text)}",
        parse_mode="HTML",
    )
    td_challenges.pop((chat_id, parent_id), None)


async def tdcount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    await update.message.reply_text(
        f"📊 In this chat:\nTruths: {len(bucket['truths'])}\nDares: {len(bucket['dares'])}"
    )


# ---------------------------------------------------------------------------
# General commands
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hey! I can run 2 games:\n\n"
        "🎨 *Color-Color*\n"
        "/colorgame - start a new round\n"
        "/endcolorgame - stop the round\n"
        "/score - see this chat's scoreboard\n\n"
        "🎲 *Truth & Dare* (add your own questions/dares)\n"
        "/addtruth <question>\n"
        "/adddare <task>\n"
        "/truth - random truth for you\n"
        "/dare - random dare for you\n"
        "/td - reply to someone's message with this to challenge them\n"
        "/tdcount - how many truths/dares are saved\n\n"
        "Both games work in groups and in private chats!",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # Log the error instead of letting it crash the whole bot process.
    logger.error("Unhandled exception while processing update", exc_info=context.error)


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "❌ BOT_TOKEN environment variable is not set.\n"
            "On Railway: Project -> Variables -> add BOT_TOKEN.\n"
            "Locally: export BOT_TOKEN='your-token-from-botfather'"
        )

    app = ApplicationBuilder().token(token).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CommandHandler("colorgame", colorgame_cmd))
    app.add_handler(CommandHandler("endcolorgame", endcolorgame_cmd))
    app.add_handler(CommandHandler("score", score_cmd))
    app.add_handler(CallbackQueryHandler(color_callback, pattern="^cg_"))

    app.add_handler(CommandHandler("addtruth", addtruth_cmd))
    app.add_handler(CommandHandler("adddare", adddare_cmd))
    app.add_handler(CommandHandler("truth", truth_cmd))
    app.add_handler(CommandHandler("dare", dare_cmd))
    app.add_handler(CommandHandler("td", td_cmd))
    app.add_handler(CommandHandler("tdcount", tdcount_cmd))
    app.add_handler(CallbackQueryHandler(td_callback, pattern="^td_pick:"))
    # Catches the target's reply to an active Truth/Dare challenge message.
    app.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, td_reply_handler))

    logger.info("Bot starting (polling)...")
    # drop_pending_updates avoids replaying a pile of stale updates after a
    # Railway redeploy/restart.
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
