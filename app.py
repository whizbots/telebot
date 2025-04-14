import os
import logging
import threading
from datetime import datetime, timedelta

from flask import Flask, render_template_string
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    CallbackContext,
)

# ---------------------------
# CONFIGURATION & ENVIRONMENT
# ---------------------------

# Logging configuration for both Telegram bot and web server.
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Load environment variables.
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PORT = int(os.getenv("PORT", 5000))

# ---------------------------
# BOT SETTINGS & GLOBALS
# ---------------------------

# Group rules (non-admin users must comply)
RULES = (
    "<b>Group Rules</b>:\n"
    "1. <i>Be respectful</i> to everyone.\n"
    "2. <i>No spamming</i> or disruptive messages.\n"
    "3. <i>No links to scams or harmful content</i>.\n"
    "4. <i>No sharing content that invades privacy</i>.\n"
    "<b>Note:</b> Admins are exempt from these rules."
)

# List of banned words/phrases.
BANNED_WORDS = ["spam", "scam", "badlink", "malware", "phishing"]

# Warning threshold before muting (24‑hour ban).
WARNING_THRESHOLD = 3

# Tracking warnings per user in a dictionary {user_id: count}
warnings = {}

# Log file for recording all group messages.
LOG_FILE = "group_messages.log"

# ---------------------------
# HELPER FUNCTIONS
# ---------------------------

def log_message(message):
    """Append each group message to a log file with a timestamp."""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.utcnow()} - Chat:{message.chat.id} - "
            f"{message.from_user.first_name}({message.from_user.id}): {message.text}\n"
        )

def check_rules(text: str):
    """Return a tuple indicating if text breaks rules and the offending reason."""
    text_lower = text.lower()
    for word in BANNED_WORDS:
        if word in text_lower:
            return True, f"usage of banned word '{word}'"
    return False, ""

def reply_without_exposing_profile(update: Update, reply_text: str, parse_mode="HTML"):
    """Reply directly to the user message without revealing any extra profile details."""
    update.message.reply_text(
        reply_text, reply_to_message_id=update.message.message_id, parse_mode=parse_mode
    )

# ---------------------------
# TELEGRAM BOT HANDLERS
# ---------------------------

def start(update: Update, context: CallbackContext):
    """Handle /start command in private chats using a beautiful inline menu."""
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("💬 Send a Message", callback_data="send_message")],
        [InlineKeyboardButton("📜 View Group Rules", callback_data="rules")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        f"✨ Hello <b>{user.first_name}</b>, welcome to our beautiful bot!\n\n"
        "Please choose an option below:",
        reply_markup=markup,
        parse_mode="HTML",
    )

def help_command(update: Update, context: CallbackContext):
    """Provides instructions on how to use the bot."""
    help_text = (
        "<b>Bot Instructions</b>:\n"
        "• Use /start in a private chat to see options.\n"
        "• Use the inline menu to forward messages or view group rules.\n"
        "• In groups, messages that violate the rules will be deleted, and warnings issued.\n"
        "• After 3 warnings, the user will be muted for 24 hours.\n"
        "• Replies are made by directly replying to your messages.\n"
        "Enjoy our community!"
    )
    reply_without_exposing_profile(update, help_text)

def button_handler(update: Update, context: CallbackContext):
    """Process inline button callbacks from the menu."""
    query = update.callback_query
    query.answer()
    if query.data == "send_message":
        query.edit_message_text(
            "💌 <i>Please type your message to forward to the admin:</i>", parse_mode="HTML"
        )
        context.user_data["awaiting_message"] = True
    elif query.data == "rules":
        query.edit_message_text(RULES, parse_mode="HTML")
    elif query.data == "help":
        query.edit_message_text(
            "<b>Bot Instructions</b>:\n"
            "• Use /start in private chat for options.\n"
            "• Violating group rules results in deletion and warnings.\n"
            "• 3 warnings equals a 24‑hour mute for non-admins.",
            parse_mode="HTML"
        )

def handle_private(update: Update, context: CallbackContext):
    """Forward user messages to admin if awaiting a message."""
    if context.user_data.get("awaiting_message", False):
        context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📨 <b>Message from {update.effective_user.first_name}</b> "
                f"({update.effective_user.id}):\n{update.message.text}"
            ),
            parse_mode="HTML"
        )
        update.message.reply_text(
            "✅ Your message has been forwarded to the admin!", parse_mode="HTML"
        )
        context.user_data["awaiting_message"] = False
    else:
        reply_without_exposing_profile(
            update, "👉 Please use /start to view the menu options.", parse_mode="HTML"
        )

def handle_group(update: Update, context: CallbackContext):
    """
    In group chats, log every message, check for rule violations, 
    reply with warnings, and restrict users after repeated violations.
    """
    message = update.message
    log_message(message)
    if not message.text:
        return

    violated, reason = check_rules(message.text)
    if not violated:
        return

    member = context.bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status in ["administrator", "creator"]:
        return

    # Delete violating message.
    try:
        context.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logging.error(f"Failed to delete message: {e}")

    # Increment warnings.
    user_id = message.from_user.id
    warnings[user_id] = warnings.get(user_id, 0) + 1

    warn_text = (
        f"⚠️ <b>{message.from_user.first_name}</b>, your message broke the rules "
        f"({reason}). This is warning <b>{warnings[user_id]}/{WARNING_THRESHOLD}</b>.\n"
        "Please adhere to our community guidelines."
    )
    context.bot.send_message(chat_id=message.chat.id, text=warn_text, parse_mode="HTML")

    # Restrict user after exceeding threshold.
    if warnings[user_id] >= WARNING_THRESHOLD:
        until = datetime.now() + timedelta(hours=24)
        try:
            context.bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                ),
                until_date=until,
            )
            ban_text = (
                f"⏳ <b>{message.from_user.first_name}</b> has been muted for 24 hours due to repeated violations."
            )
            context.bot.send_message(chat_id=message.chat.id, text=ban_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error restricting user: {e}")
        warnings[user_id] = 0

def welcome_new(update: Update, context: CallbackContext):
    """Send a dynamic and beautiful welcome message to new group members."""
    for member in update.message.new_chat_members:
        welcome_text = (
            f"🌟 <b>Welcome, {member.first_name}!</b>\n\n"
            "We're thrilled to have you join our community. Please take a moment to read the rules below:\n\n"
            f"{RULES}\n\n"
            "Enjoy your stay and have fun! 😊"
        )
        update.message.reply_text(welcome_text, parse_mode="HTML")

# ---------------------------
# TELEGRAM BOT SETUP & THREAD
# ---------------------------

def run_telegram_bot():
    """Start the Telegram bot updater."""
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    # Private chat handlers.
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.private & Filters.text & ~Filters.command, handle_private))

    # Group chat handlers.
    dp.add_handler(MessageHandler(Filters.group & Filters.text & ~Filters.command, handle_group))
    dp.add_handler(MessageHandler(Filters.status_update.new_chat_members, welcome_new))

    # Start polling.
    updater.start_polling()
    updater.idle()

# ---------------------------
# FLASK WEB SERVER FOR ABOUT PAGE
# ---------------------------

app = Flask(__name__)

@app.route("/")
def about():
    """Display a beautiful HTML page with information about the bot and its instructions."""
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
           <li>Welcoming new members with a dynamic message and clear rules.</li>
           <li>Logging every message and checking for violations.</li>
           <li>Issuing warnings and auto-muting users (for 24 hours) after repeated violations.</li>
           <li>Forwarding direct messages from users to the admin with a simple inline menu.</li>
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

# ---------------------------
# START UP: RUN BOT & WEB SERVER
# ---------------------------

if __name__ == "__main__":
    # Run the Telegram bot in a separate thread.
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    # Start the Flask web server (this is what Render.com will serve).
    app.run(host="0.0.0.0", port=PORT)
