import os
import logging
import threading
from datetime import datetime, timedelta

from flask import Flask, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --------------------------
# CONFIGURATION & ENVIRONMENT
# --------------------------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PORT = int(os.getenv("PORT", 5000))

# --------------------------
# BOT SETTINGS & GLOBAL VARIABLES
# --------------------------
RULES = (
    "<b>Group Rules</b>:\n"
    "1. <i>Be respectful</i> to everyone.\n"
    "2. <i>No spamming</i> or disruptive messages.\n"
    "3. <i>No links to scams or harmful content</i>.\n"
    "4. <i>No sharing content that invades privacy</i>.\n"
    "<b>Note:</b> Admins are exempt from these rules."
)
BANNED_WORDS = ["spam", "scam", "badlink", "malware", "phishing"]
WARNING_THRESHOLD = 3
warnings_dict = {}  # Tracks user warnings
LOG_FILE = "group_messages.log"

def log_message(message):
    """Append each group message to a log file."""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.utcnow()} - Chat:{message.chat.id} - "
            f"{message.from_user.first_name}({message.from_user.id}): {message.text}\n"
        )

def check_rules(text: str):
    """Check if the message text violates rules; return a tuple (violated, reason)."""
    text_lower = text.lower()
    for word in BANNED_WORDS:
        if word in text_lower:
            return True, f"usage of banned word '{word}'"
    return False, ""

async def reply_without_profile(update: Update, reply_text: str):
    """Reply directly to a user's message without exposing extra profile details."""
    await update.message.reply_text(
        reply_text,
        reply_to_message_id=update.message.message_id,
        parse_mode="HTML"
    )

# --------------------------
# TELEGRAM BOT HANDLERS (ASYNC)
# --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start command in private chats: display a beautiful inline menu."""
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("💬 Send a Message", callback_data="send_message")],
        [InlineKeyboardButton("📜 View Group Rules", callback_data="rules")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"✨ Hello <b>{user.first_name}</b>, welcome to our beautiful bot!\n\n"
        "Please choose an option below:",
        reply_markup=markup,
        parse_mode="HTML",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show instructions on how to use the bot."""
    help_text = (
        "<b>Bot Instructions</b>:\n"
        "• Use /start in a private chat to see options.\n"
        "• Use the inline menu to forward messages or view group rules.\n"
        "• In groups, messages that violate rules will be deleted and warnings issued.\n"
        "• After 3 warnings, the user will be muted for 24 hours.\n"
        "• Replies are made by directly replying to your messages.\n"
        "Enjoy our community!"
    )
    await reply_without_profile(update, help_text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks from the menu."""
    query = update.callback_query
    await query.answer()
    if query.data == "send_message":
        await query.edit_message_text(
            "💌 <i>Please type your message to forward to the admin:</i>",
            parse_mode="HTML"
        )
        context.user_data["awaiting_message"] = True
    elif query.data == "rules":
        await query.edit_message_text(RULES, parse_mode="HTML")
    elif query.data == "help":
        await query.edit_message_text(
            "<b>Bot Instructions</b>:\n"
            "• Use /start in private chat for options.\n"
            "• Violating group rules results in deletion and warnings.\n"
            "• 3 warnings equals a 24‑hour mute for non-admins.",
            parse_mode="HTML"
        )

async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """In private chats, forward a user's message to the admin."""
    if context.user_data.get("awaiting_message", False):
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📨 <b>Message from {update.effective_user.first_name}</b> "
                f"({update.effective_user.id}):\n{update.message.text}"
            ),
            parse_mode="HTML"
        )
        await update.message.reply_text(
            "✅ Your message has been forwarded to the admin!",
            parse_mode="HTML"
        )
        context.user_data["awaiting_message"] = False
    else:
        await reply_without_profile(
            update, "👉 Please use /start to view the menu options."
        )

async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log every group message, check for rule violations, warn users, and mute if necessary."""
    message = update.message
    log_message(message)
    if not message.text:
        return

    violated, reason = check_rules(message.text)
    if not violated:
        return

    # Check if sender is admin; if so, no action is needed.
    member = await context.bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status in ["administrator", "creator"]:
        return

    try:
        await context.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logging.error(f"Failed to delete message: {e}")

    user_id = message.from_user.id
    warnings_dict[user_id] = warnings_dict.get(user_id, 0) + 1
    warn_text = (
        f"⚠️ <b>{message.from_user.first_name}</b>, your message broke the rules "
        f"({reason}). This is warning <b>{warnings_dict[user_id]}/{WARNING_THRESHOLD}</b>.\n"
        "Please adhere to our community guidelines."
    )
    await context.bot.send_message(chat_id=message.chat.id, text=warn_text, parse_mode="HTML")

    if warnings_dict[user_id] >= WARNING_THRESHOLD:
        until_date = datetime.now() + timedelta(hours=24)
        try:
            await context.bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                ),
                until_date=until_date
            )
            ban_text = (
                f"⏳ <b>{message.from_user.first_name}</b> has been muted for 24 hours due to repeated violations."
            )
            await context.bot.send_message(chat_id=message.chat.id, text=ban_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error restricting user: {e}")
        warnings_dict[user_id] = 0

async def welcome_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a dynamic welcome message to new group members."""
    for member in update.message.new_chat_members:
        welcome_text = (
            f"🌟 <b>Welcome, {member.first_name}!</b>\n\n"
            "We're thrilled to have you join our community. Please read the rules below:\n\n"
            f"{RULES}\n\nEnjoy your stay and have fun! 😊"
        )
        await update.message.reply_text(welcome_text, parse_mode="HTML")

# --------------------------
# TELEGRAM BOT SETUP & HANDLERS
# --------------------------
application = ApplicationBuilder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_private))
application.add_handler(MessageHandler((filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & filters.TEXT & ~filters.COMMAND, handle_group))
application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new))

def run_bot():
    """Run the Telegram bot polling in a separate thread."""
    application.run_polling()

# --------------------------
# FLASK WEB SERVER FOR ABOUT PAGE
# --------------------------
flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    html_content = """
    <!doctype html>
    <html lang="en">
    <head>
      <title>About This Telegram Bot</title>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
         body { font-family: Arial, sans-serif; background-color: #f2f2f2; margin: 0; padding: 20px; }
         .container { max-width: 800px; margin: auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
         h1 { color: #333; }
         p { color: #555; line-height: 1.6; }
         .footer { font-size: 0.9em; color: #888; margin-top: 20px; }
      </style>
    </head>
    <body>
      <div class="container">
         <h1>About This Telegram Bot</h1>
         <p>This bot is designed to manage Telegram groups beautifully by:</p>
         <ul>
           <li>Welcoming new members with dynamic messages and clear rules.</li>
           <li>Logging every message and checking for violations.</li>
           <li>Issuing warnings and auto-muting users (for 24 hours) after repeated violations.</li>
           <li>Forwarding direct messages from users to the admin via an inline menu.</li>
         </ul>
         <p><b>Commands & Instructions:</b></p>
         <ul>
           <li><code>/start</code> – View the inline menu with options.</li>
           <li><code>/help</code> – Get instructions on how to use the bot.</li>
         </ul>
         <p>This service is hosted on Render.com, ensuring a stable and reliable experience. Feel free to interact with the bot in your groups or via private chat.</p>
         <div class="footer">
            &copy; 2025 Your Bot Name. All Rights Reserved.
         </div>
      </div>
    </body>
    </html>
    """
    return render_template_string(html_content)

# --------------------------
# START THE BOT & WEB SERVER
# --------------------------
if __name__ == "__main__":
    # Start the Telegram bot in a separate thread.
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    # Start the Flask web server.
    flask_app.run(host="0.0.0.0", port=PORT)
