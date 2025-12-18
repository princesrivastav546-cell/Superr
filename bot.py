import telebot
import requests

TOKEN = "7866384372:AAF2X-5RgiulWFm0FSRQc_87EGg8Wh7vpow"
API_URL = "https://nggemini.tiiny.io/?prompt="

bot = telebot.TeleBot(TOKEN)

# Start Command
@bot.message_handler(commands=["start"])
def start(message):
    text = "👋 Welcome! Use the following commands:\n\n"
    text += "🔹 /ask <question> - Get AI-generated response\n"
    text += "🔹 /help - Get support\n"
    text += "🔹 /admin - Contact Admin\n"
    text += "🔹 /live - View live members count"
    bot.send_message(message.chat.id, text)

# Ask Command (Fetch from API)
@bot.message_handler(commands=["ask"])
def ask(message):
    query = message.text.replace("/ask", "").strip()
    if not query:
        bot.send_message(message.chat.id, "❌ Please enter a question after /ask")
        return

    response = requests.get(API_URL + query)
    bot.send_message(message.chat.id, "🤖 AI Response:\n" + response.text)

# Help Command
@bot.message_handler(commands=["help"])
def help_command(message):
    text = "Need help? Click below to DM me 👇"
    keyboard = telebot.types.InlineKeyboardMarkup()
    keyboard.add(telebot.types.InlineKeyboardButton("💬 Contact Devloper", url="https://t.me/NGYT777GG"))
    bot.send_message(message.chat.id, text, reply_markup=keyboard)

# Admin Command
@bot.message_handler(commands=["admin"])
def admin(message):
    bot.send_message(message.chat.id, "👤 Admin: @GOAT_NG")

# Live Command (Show Bot Members Count)
@bot.message_handler(commands=["live"])
def live(message):
    bot_info = bot.get_me()
    chat_info = bot.get_chat(bot_info.id)
    bot.send_message(message.chat.id, f"📊 Total Members: {chat_info.members_count}")

# Run Bot
bot.polling()
