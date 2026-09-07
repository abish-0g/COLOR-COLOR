"""
Telegram bot with 2 games:
1. Color-Color -> one player picks a secret color, everyone else guesses it via emoji buttons.
2. Truth & Dare -> users submit their own truths/dares, then the bot randomly assigns one
   to a specific target player (chosen by replying to their message with /td).

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
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# DATA_DIR can be overridden (e.g. point it at a Railway Volume mount path like
# "/data") so truths/dares survive redeploys. Defaults to the app folder.
DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "data.json")

# ---------------------------------------------------------------------------
# Persistent storage for Truth & Dare (survives bot restarts)
# ---------------------------------------------------------------------------

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("data.json corrupt/unreadable, starting fresh.")
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
        data[key] = {"truths": [], "dares": []}
    return data[key]


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

COLOR_NAMES = {
    "red": "Red", "orange": "Orange", "yellow": "Yellow", "green": "Green",
    "blue": "Blue", "purple": "Purple", "brown": "Brown", "black": "Black", "white": "White",
}

# chat_id -> game state dict
color_games = {}


def build_color_pick_keyboard():
    """Keyboard shown ONLY meant for the giver, to secretly pick a color name."""
    names = list(COLOR_NAMES.keys())
    random.shuffle(names)
    rows = []
    row = []
    for name in names:
        row.append(InlineKeyboardButton(COLOR_NAMES[name], callback_data=f"cg_color:{name}"))
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


def esc(text: str) -> str:
    """Escape user-supplied text for safe use inside HTML parse_mode messages."""
    return html.escape(str(text))


async def colorgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    existing = color_games.get(chat_id)
    if existing and existing["phase"] != "done":
        await update.message.reply_text(
            "⚠️ A Color-Color game is already running in this chat.\n"
            "Use /endcolorgame to stop it."
        )
        return

    color_games[chat_id] = {"phase": "claim", "giver_id": None, "giver_name": None,
                             "theme": None, "color": None}

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🙋 I'll pick a color!", callback_data="cg_claim")]]
    )
    await update.message.reply_text(
        "🎨 <b>Color-Color game started!</b>\n\n"
        "One player will pick a secret color, and everyone else will try to guess its emoji.\n"
        "Who wants to give the color?",
        reply_markup=kb,
        parse_mode="HTML",
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
            await query.answer("Someone already claimed this role!", show_alert=True)
            return
        game["giver_id"] = user.id
        game["giver_name"] = user.first_name
        game["theme"] = random.choice(COLOR_THEMES)
        game["phase"] = "picking"
        await query.answer("Pick your secret color from the buttons below (only you can use these) 🤫", show_alert=True)
        await query.edit_message_text(
            f"🎨 <b>{esc(user.first_name)}</b> is thinking of a secret color... 🤔\n"
            f"(Only {esc(user.first_name)} can use these buttons)",
            reply_markup=build_color_pick_keyboard(),
            parse_mode="HTML",
        )
        return

    # --- Step 2: giver secretly picks a color ---
    if data.startswith("cg_color:"):
        if game["phase"] != "picking":
            await query.answer("This step is already over.", show_alert=True)
            return
        if user.id != game["giver_id"]:
            await query.answer("⛔ It's not your turn — only the color-giver can pick!", show_alert=True)
            return
        color_name = data.split(":", 1)[1]
        game["color"] = color_name
        game["phase"] = "guessing"
        await query.answer(f"You picked '{COLOR_NAMES[color_name]}' ✅ (stays secret)", show_alert=True)
        await query.edit_message_text(
            f"🎨 <b>{esc(game['giver_name'])}</b> has picked their secret color!\n\n"
            f"👇 Everyone else, guess the right emoji (the color-giver can't guess):",
            reply_markup=build_guess_keyboard(game["theme"]),
            parse_mode="HTML",
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
        guess_name = data.split(":", 1)[1]
        if guess_name == game["color"]:
            emoji = game["theme"][game["color"]]
            await query.answer("🎉 Correct answer!", show_alert=False)
            await query.edit_message_text(
                f"🎉 <b>{esc(user.first_name)}</b> guessed it right!\n\n"
                f"The secret color was: <b>{COLOR_NAMES[game['color']]}</b> {emoji}\n\n"
                f"Send /colorgame to start a new round.",
                parse_mode="HTML",
            )
            game["phase"] = "done"
            color_games.pop(chat_id, None)
        else:
            await query.answer("❌ Wrong! Try again.", show_alert=False)
        return


# ---------------------------------------------------------------------------
# Truth & Dare game (user-submitted content)
# ---------------------------------------------------------------------------

# (chat_id, message_id) -> {"target_id": int, "target_mention": str}
# Tracks who a /td challenge message is actually for, so only that person
# can press the buttons and the reveal always names the right player.
td_games = {}


async def addtruth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Use it like this:\n<code>/addtruth What's the most embarrassing thing that's happened to you?</code>",
            parse_mode="HTML",
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
            "Use it like this:\n<code>/adddare Dance non-stop for 1 minute</code>",
            parse_mode="HTML",
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
    await update.message.reply_text(
        f"🧐 <b>Truth</b> for {update.effective_user.mention_html()}:\n{esc(pick)}",
        parse_mode="HTML",
    )


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    if not bucket["dares"]:
        await update.message.reply_text("No dares added yet. Use /adddare <task> first.")
        return
    pick = random.choice(bucket["dares"])
    await update.message.reply_text(
        f"🔥 <b>Dare</b> for {update.effective_user.mention_html()}:\n{esc(pick)}",
        parse_mode="HTML",
    )


async def td_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /td — challenge someone to Truth or Dare.

    - Reply to someone's message with /td to challenge THEM.
    - Send /td with no reply to challenge yourself.

    Only the targeted person can press the buttons, and the reveal always
    mentions them, so it's always clear who's answering what.
    """
    replied = update.message.reply_to_message
    if replied and replied.from_user and not replied.from_user.is_bot:
        target_user = replied.from_user
    else:
        target_user = update.effective_user

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧐 Truth", callback_data="td_pick:truth"),
                InlineKeyboardButton("🔥 Dare", callback_data="td_pick:dare"),
            ]
        ]
    )
    sent = await update.message.reply_text(
        f"🎲 {target_user.mention_html()}, Truth or Dare?",
        reply_markup=kb,
        parse_mode="HTML",
    )
    td_games[(sent.chat_id, sent.message_id)] = {
        "target_id": target_user.id,
        "target_mention": target_user.mention_html(),
    }


async def td_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    user = query.from_user

    info = td_games.get((chat_id, message_id))
    if not info:
        await query.answer("This challenge has expired. Use /td to start a new one.", show_alert=True)
        return

    if user.id != info["target_id"]:
        await query.answer("⛔ This challenge isn't for you!", show_alert=True)
        return

    await query.answer()

    _, kind = query.data.split(":", 1)
    data = load_data()
    bucket = get_chat_bucket(data, chat_id)
    pool = bucket["truths"] if kind == "truth" else bucket["dares"]
    if not pool:
        cmd = "/addtruth" if kind == "truth" else "/adddare"
        await query.edit_message_text(
            f"No {kind}s added yet. Use {cmd} <text> first."
        )
        td_games.pop((chat_id, message_id), None)
        return

    pick = random.choice(pool)
    emoji = "🧐" if kind == "truth" else "🔥"
    await query.edit_message_text(
        f"{emoji} <b>{kind.title()}</b> for {info['target_mention']}:\n{esc(pick)}",
        parse_mode="HTML",
    )
    td_games.pop((chat_id, message_id), None)


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
        "🎨 <b>Color-Color</b>\n"
        "/colorgame - start a new round\n"
        "/endcolorgame - stop the round\n\n"
        "🎲 <b>Truth &amp; Dare</b> (add your own questions/dares)\n"
        "/addtruth &lt;question&gt;\n"
        "/adddare &lt;task&gt;\n"
        "/truth - random truth for you\n"
        "/dare - random dare for you\n"
        "/td - reply to someone's message with /td to challenge them "
        "(or send it alone to challenge yourself)\n"
        "/tdcount - how many truths/dares are saved\n\n"
        "Both games work in groups and private chats!",
        parse_mode="HTML",
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
    app.add_handler(CallbackQueryHandler(color_callback, pattern="^cg_"))

    app.add_handler(CommandHandler("addtruth", addtruth_cmd))
    app.add_handler(CommandHandler("adddare", adddare_cmd))
    app.add_handler(CommandHandler("truth", truth_cmd))
    app.add_handler(CommandHandler("dare", dare_cmd))
    app.add_handler(CommandHandler("td", td_cmd))
    app.add_handler(CommandHandler("tdcount", tdcount_cmd))
    app.add_handler(CallbackQueryHandler(td_callback, pattern="^td_pick:"))

    logger.info("Bot starting (polling)...")
    # drop_pending_updates avoids replaying a pile of stale updates after a
    # Railway redeploy/restart, and close_loop=False keeps shutdown clean.
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
