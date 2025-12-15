import os
import shutil
import logging
import subprocess
import uuid
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from dotenv import dotenv_values

# --- CONFIGURATION (LOAD FROM RENDER ENV) ---
TOKEN = os.getenv("8291407561:AAGjhzrpwokeNkHz3_Fh9mMBHegvfAnqXpQ")
# If ADMIN_ID is not set in Render, it defaults to 0 (nobody can access)
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 5631512980

TEMP_DIR = "./temp_runs"

# --- STATES ---
UPLOAD_PY, CHECK_REQ, UPLOAD_REQ, CHECK_ENV, UPLOAD_ENV = range(5)

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- KEEP ALIVE SERVER (REQUIRED FOR RENDER) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Alive"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- PERMISSION CHECK ---
async def restricted(update: Update):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ **Access Denied.**\nCheck ADMIN_ID in Render settings.")
        return False
    return True

# --- BOT LOGIC ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Render Python Runner is Online!**\n\n"
        "Use `/runpy` to execute a script.\n"
        "Use `/cancel` to stop a conversation."
    )

async def start_runpy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update): return ConversationHandler.END
    
    session_id = str(uuid.uuid4())[:8]
    session_path = os.path.abspath(os.path.join(TEMP_DIR, session_id))
    os.makedirs(session_path, exist_ok=True)
    
    context.user_data['session_path'] = session_path
    
    await update.message.reply_text(f"🐍 **Session Started** (`{session_id}`)\nStep 1: Upload your `.py` file.")
    return UPLOAD_PY

async def handle_py(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    if not file.file_name.endswith('.py'):
        await update.message.reply_text("❌ Send a .py file.")
        return UPLOAD_PY

    session_path = context.user_data['session_path']
    file_path = os.path.join(session_path, "script.py")
    
    new_file = await context.bot.get_file(file.file_id)
    await new_file.download_to_drive(file_path)
    
    keyboard = [[InlineKeyboardButton("Yes (.txt)", callback_data="req_yes"), InlineKeyboardButton("No (Skip)", callback_data="req_no")]]
    await update.message.reply_text("Step 2: Do you have `requirements.txt`?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECK_REQ

async def check_req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "req_yes":
        await query.edit_message_text("📤 Upload `requirements.txt`.")
        return UPLOAD_REQ
    return await ask_env(query, context)

async def handle_req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    session_path = context.user_data['session_path']
    await (await context.bot.get_file(file.file_id)).download_to_drive(os.path.join(session_path, "requirements.txt"))
    
    keyboard = [[InlineKeyboardButton("Yes (.env)", callback_data="env_yes"), InlineKeyboardButton("No (Run)", callback_data="env_no")]]
    await update.message.reply_text("Step 3: Do you have `.env`?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECK_ENV

async def ask_env(query, context):
    keyboard = [[InlineKeyboardButton("Yes (.env)", callback_data="env_yes"), InlineKeyboardButton("No (Run)", callback_data="env_no")]]
    await query.edit_message_text("Step 3: Do you have `.env`?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECK_ENV

async def check_env(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "env_yes":
        await query.edit_message_text("📤 Upload `.env`.")
        return UPLOAD_ENV
    await query.edit_message_text("🚀 Starting execution...")
    return await execute_script(update, context, is_callback=True)

async def handle_env(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_path = context.user_data['session_path']
    await (await context.bot.get_file(update.message.document.file_id)).download_to_drive(os.path.join(session_path, ".env"))
    await update.message.reply_text("🚀 Starting execution...")
    return await execute_script(update, context, is_callback=False)

async def execute_script(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback=False):
    session_path = context.user_data['session_path']
    req_path = os.path.join(session_path, "requirements.txt")
    env_path = os.path.join(session_path, ".env")
    lib_path = os.path.join(session_path, "libs")
    
    async def send(text):
        if is_callback:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, parse_mode='Markdown')

    # 1. Install Requirements
    if os.path.exists(req_path):
        await send("📦 Installing requirements...")
        try:
            subprocess.check_call(["pip", "install", "-r", "requirements.txt", "--target", "libs"], cwd=session_path)
        except Exception:
            await send("❌ Failed to install requirements.")
            shutil.rmtree(session_path)
            return ConversationHandler.END

    # 2. Setup Environment
    run_env = os.environ.copy()
    if os.path.exists(lib_path):
        run_env["PYTHONPATH"] = lib_path + os.pathsep + run_env.get("PYTHONPATH", "")
    
    if os.path.exists(env_path):
        for k, v in dotenv_values(env_path).items():
            if v: run_env[k] = str(v)

    # 3. Run
    try:
        result = subprocess.run(
            ["python", "script.py"], env=run_env, cwd=session_path,
            capture_output=True, text=True, timeout=45
        )
        
        output = result.stdout[:3000]
        error = result.stderr[:1000]
        
        msg = "📝 **Result:**\n"
        if output: msg += f"```\n{output}\n```"
        if error: msg += f"\n**Errors:**\n```\n{error}\n```"
        if not output and not error: msg += "✅ (No Output)"
        
        await send(msg)
    except subprocess.TimeoutExpired:
        await send("🛑 Script timed out (45s limit).")
    except Exception as e:
        await send(f"❌ Error: {str(e)}")

    shutil.rmtree(session_path, ignore_errors=True)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# --- MAIN ---
if __name__ == '__main__':
    # 1. Start Web Server in Thread (For Render)
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

    # 2. Check Token
    if not TOKEN:
        print("❌ Error: TOKEN not found in environment variables.")
        exit(1)

    # 3. Start Bot
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler('runpy', start_runpy)],
        states={
            UPLOAD_PY: [MessageHandler(filters.Document.FileExtension("py"), handle_py)],
            CHECK_REQ: [CallbackQueryHandler(check_req)],
            UPLOAD_REQ: [MessageHandler(filters.Document.FileExtension("txt"), handle_req)],
            CHECK_ENV: [CallbackQueryHandler(check_env)],
            UPLOAD_ENV: [MessageHandler(filters.Document.ALL, handle_env)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(conv)
    
    print("✅ Bot is polling...")
    app.run_polling()
