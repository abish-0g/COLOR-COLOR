"""
Telegram bot with 2 games:
1. Color-Color  -> ek player secret color chunta hai, baaki players emoji/sticker se guess karte hain.
2. Truth & Dare -> users khud apne truths/dares add karte hain, phir bot random assign karta hai.

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

# Different "themes" so the emoji set changes round to round (the "ya kuch bhi" part)
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

COLOR_NAMES_HI = {
    "red": "Laal", "orange": "Orange", "yellow": "Peela", "green": "Hara",
    "blue": "Neela", "purple": "Baingani", "brown": "Brown", "black": "Kaala", "white": "Safed",
}

# chat_id -> game state dict
color_games = {}


def build_color_pick_keyboard():
    """Keyboard shown ONLY meant for the giver, to secretly pick a color name."""
    names = list(COLOR_NAMES_HI.keys())
    random.shuffle(names)
    rows = []
    row = []
    for name in names:
        row.append(InlineKeyboardButton(COLOR_NAMES_HI[name], callback_data=f"cg_color:{name}"))
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
            "⚠️ Is chat me pehle se ek Color Game chal raha hai.\n"
            "Rokne ke liye /endcolorgame use karo."
        )
        return

    color_games[chat_id] = {"phase": "claim", "giver_id": None, "giver_name": None,
                             "theme": None, "color": None}

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🙋 Main color dunga!", callback_data="cg_claim")]]
    )
    await update.message.reply_text(
        "🎨 *Color-Color Game shuru!*\n\n"
        "Ek player secret color chunega, baaki sab uska emoji guess karenge.\n"
        "Kaun apna color dega?",
        reply_markup=kb,
        parse_mode="Markdown",
    )


async def endcolorgame_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in color_games:
        del color_games[chat_id]
        await update.message.reply_text("🛑 Color Game rok diya gaya.")
    else:
        await update.message.reply_text("Is chat me koi Color Game chal hi nahi raha.")


async def color_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    game = color_games.get(chat_id)

    if not game:
        await query.answer("Ye game ab active nahi hai. /colorgame se naya shuru karo.", show_alert=True)
        return

    data = query.data

    # --- Step 1: someone claims the "giver" role ---
    if data == "cg_claim":
        if game["phase"] != "claim":
            await query.answer("Kisi ne pehle hi ye role le liya hai!", show_alert=True)
            return
        game["giver_id"] = user.id
        game["giver_name"] = user.first_name
        game["theme"] = random.choice(COLOR_THEMES)
        game["phase"] = "picking"
        await query.answer("Ab neeche diye buttons me se apna secret color chuno (sirf tum chun sakte ho) 🤫", show_alert=True)
        await query.edit_message_text(
            f"🎨 *{user.first_name}* apna secret color soch raha/rahi hai... 🤔\n"
            f"(Sirf {user.first_name} ye buttons use kar sakte hain)",
            reply_markup=build_color_pick_keyboard(),
            parse_mode="Markdown",
        )
        return

    # --- Step 2: giver secretly picks a color ---
    if data.startswith("cg_color:"):
        if game["phase"] != "picking":
            await query.answer("Ye step khatam ho chuka hai.", show_alert=True)
            return
        if user.id != game["giver_id"]:
            await query.answer("⛔ Ye tumhari baari nahi hai, sirf color-giver chun sakta hai!", show_alert=True)
            return
        color_name = data.split(":", 1)[1]
        game["color"] = color_name
        game["phase"] = "guessing"
        await query.answer(f"Tumne '{COLOR_NAMES_HI[color_name]}' chun liya ✅ (secret rahega)", show_alert=True)
        await query.edit_message_text(
            f"🎨 *{game['giver_name']}* ne apna secret color chun liya hai!\n\n"
            f"👇 Baaki sab, sahi emoji guess karo (color-giver guess nahi kar sakta):",
            reply_markup=build_guess_keyboard(game["theme"]),
            parse_mode="Markdown",
        )
        return

    # --- Step 3: others try to guess ---
    if data.startswith("cg_guess:"):
        if game["phase"] != "guessing":
            await query.answer("Ye round khatam ho chuka hai.", show_alert=True)
            return
        if user.id == game["giver_id"]:
            await query.answer("😅 Tum khud apna color guess nahi kar sakte!", show_alert=True)
            return
        guess_name = data.split(":", 1)[1]
        if guess_name == game["color"]:
            emoji = game["theme"][game["color"]]
            await query.answer("🎉 Sahi jawab!", show_alert=False)
            await query.edit_message_text(
                f"🎉 *{user.first_name}* ne sahi guess kiya!\n\n"
                f"Secret color tha: *{COLOR_NAMES_HI[game['color']]}* {emoji}\n\n"
                f"Naya round ke liye /colorgame bhejo.",
                parse_mode="Markdown",
            )
            game["phase"] = "done"
            color_games.pop(chat_id, None)
        else:
            await query.answer("❌ Galat! Dobara try karo.", show_alert=False)
        return


# ---------------------------------------------------------------------------
# Truth & Dare game (user-submitted content)
# ---------------------------------------------------------------------------

async def addtruth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Use like this:\n`/addtruth Tumhara sabse embarrassing moment kya tha?`", parse_mode="Markdown")
        return
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    bucket["truths"].append(text)
    save_data(data)
    await update.message.reply_text(f"✅ Truth add ho gaya! (Total: {len(bucket['truths'])})")


async def adddare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Use like this:\n`/adddare 1 minute tak bina ruke dance karo`", parse_mode="Markdown")
        return
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    bucket["dares"].append(text)
    save_data(data)
    await update.message.reply_text(f"✅ Dare add ho gaya! (Total: {len(bucket['dares'])})")


async def truth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    if not bucket["truths"]:
        await update.message.reply_text("Abhi koi truth add nahi hua. Pehle /addtruth <sawaal> use karo.")
        return
    pick = random.choice(bucket["truths"])
    await update.message.reply_text(f"🧐 *Truth* for {update.effective_user.first_name}:\n{pick}", parse_mode="Markdown")


async def dare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    if not bucket["dares"]:
        await update.message.reply_text("Abhi koi dare add nahi hua. Pehle /adddare <kaam> use karo.")
        return
    pick = random.choice(bucket["dares"])
    await update.message.reply_text(f"🔥 *Dare* for {update.effective_user.first_name}:\n{pick}", parse_mode="Markdown")


async def td_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_name = update.effective_user.first_name
    if context.args:
        target_name = " ".join(context.args)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🧐 Truth", callback_data=f"td_pick:truth:{target_name}"),
                InlineKeyboardButton("🔥 Dare", callback_data=f"td_pick:dare:{target_name}"),
            ]
        ]
    )
    await update.message.reply_text(f"🎲 {target_name}, Truth ya Dare?", reply_markup=kb)


async def td_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, kind, target_name = query.data.split(":", 2)
    data = load_data()
    bucket = get_chat_bucket(data, query.message.chat_id)
    pool = bucket["truths"] if kind == "truth" else bucket["dares"]
    if not pool:
        cmd = "/addtruth" if kind == "truth" else "/adddare"
        await query.edit_message_text(f"Abhi koi {kind} add nahi hua. Pehle {cmd} <text> use karo.")
        return
    pick = random.choice(pool)
    emoji = "🧐" if kind == "truth" else "🔥"
    await query.edit_message_text(f"{emoji} *{kind.title()}* for {target_name}:\n{pick}", parse_mode="Markdown")


async def tdcount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    bucket = get_chat_bucket(data, update.effective_chat.id)
    await update.message.reply_text(
        f"📊 Is chat me:\nTruths: {len(bucket['truths'])}\nDares: {len(bucket['dares'])}"
    )


# ---------------------------------------------------------------------------
# General commands
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Namaste! Main 2 games khila sakta hoon:\n\n"
        "🎨 *Color-Color*\n"
        "/colorgame - naya round shuru karo\n"
        "/endcolorgame - round rok do\n\n"
        "🎲 *Truth & Dare* (khud ke sawaal/dare add karo)\n"
        "/addtruth <sawaal>\n"
        "/adddare <kaam>\n"
        "/truth - random truth\n"
        "/dare - random dare\n"
        "/td [naam] - Truth ya Dare button\n"
        "/tdcount - kitne truths/dares saved hain\n\n"
        "Dono games group aur private, dono me chalte hain!",
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
            "❌ BOT_TOKEN environment variable set nahi hai.\n"
            "Railway par: Project -> Variables -> BOT_TOKEN add karo.\n"
            "Local par: export BOT_TOKEN='your-token-from-botfather'"
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
