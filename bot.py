import os
import logging
import subprocess
import threading
import asyncio
import shutil
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from dotenv import dotenv_values

# --- CONFIGURATION ---
TOKEN = os.getenv("8291407561:AAGjhzrpwokeNkHz3_Fh9mMBHegvfAnqXpQ")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 5631512980

# Persistent storage folder
STORAGE_DIR = "./hosted_scripts"

# --- STATES ---
UPLOAD_PY, CHECK_REQ, UPLOAD_REQ, CHECK_ENV, UPLOAD_ENV = range(5)

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- FLASK KEEP-ALIVE ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Running"
def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- HELPER: PATH MANAGEMENT ---
def get_script_dir(user_id, script_name):
    """
    Creates a dedicated folder: ./hosted_scripts/USER_ID/script_name/
    """
    clean_name = script_name.replace(" ", "_")
    # Remove .py extension for the folder name
    folder_name = clean_name[:-3] if clean_name.endswith('.py') else clean_name
    
    path = os.path.join(STORAGE_DIR, str(user_id), folder_name)
    if not os.path.exists(path):
        os.makedirs(path)
    return path, clean_name

# --- PERMISSIONS ---
async def restricted(update: Update):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access Denied.")
        return False
    return True

# ==============================================================================
# 🔄 1. INTERACTIVE SETUP (/runpy)
# ==============================================================================

async def start_runpy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update): return ConversationHandler.END
    
    await update.message.reply_text(
        "🐍 **Host New Script**\n\n"
        "Please upload your `.py` file.\n"
        "I will save it so you can use `/bpy` later."
    )
    return UPLOAD_PY

async def handle_py(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document
    filename = file.file_name
    if not filename.endswith('.py'):
        await update.message.reply_text("❌ Please upload a file ending in `.py`")
        return UPLOAD_PY

    user_id = update.effective_user.id
    script_dir, clean_name = get_script_dir(user_id, filename)
    
    # Save info to context for next steps
    context.user_data['script_dir'] = script_dir
    context.user_data['script_name'] = clean_name
    
    # Download file
    file_path = os.path.join(script_dir, clean_name)
    new_file = await context.bot.get_file(file.file_id)
    await new_file.download_to_drive(file_path)
    
    # Ask for requirements
    keyboard = [[InlineKeyboardButton("Yes", callback_data="req_yes"), InlineKeyboardButton("No", callback_data="req_no")]]
    await update.message.reply_text(
        f"✅ Saved `{clean_name}`.\n\n**Do you have a requirements.txt file?**", 
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
    )
    return CHECK_REQ

async def check_req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "req_yes":
        await query.edit_message_text("📤 Please upload `requirements.txt`.")
        return UPLOAD_REQ
    return await ask_env(query, context)

async def handle_req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    script_dir = context.user_data['script_dir']
    file = update.message.document
    
    # Save requirements.txt
    file_path = os.path.join(script_dir, "requirements.txt")
    new_file = await context.bot.get_file(file.file_id)
    await new_file.download_to_drive(file_path)
    
    return await ask_env_msg(update, context)

async def ask_env(query, context):
    keyboard = [[InlineKeyboardButton("Yes", callback_data="env_yes"), InlineKeyboardButton("No", callback_data="env_no")]]
    await query.edit_message_text("**Do you have a .env file?**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return CHECK_ENV

async def ask_env_msg(update, context):
    keyboard = [[InlineKeyboardButton("Yes", callback_data="env_yes"), InlineKeyboardButton("No", callback_data="env_no")]]
    await update.message.reply_text("**Do you have a .env file?**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return CHECK_ENV

async def check_env(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "env_yes":
        await query.edit_message_text("📤 Please upload `.env`.")
        return UPLOAD_ENV
    
    await query.edit_message_text("🚀 Setup complete. Running script...")
    return await trigger_execution(update, context, is_callback=True)

async def handle_env(update: Update, context: ContextTypes.DEFAULT_TYPE):
    script_dir = context.user_data['script_dir']
    
    file_path = os.path.join(script_dir, ".env")
    new_file = await context.bot.get_file(update.message.document.file_id)
    await new_file.download_to_drive(file_path)
    
    await update.message.reply_text("🚀 Setup complete. Running script...")
    return await trigger_execution(update, context, is_callback=False)

async def trigger_execution(update, context, is_callback):
    script_dir = context.user_data['script_dir']
    script_name = context.user_data['script_name']
    
    await run_logic(update, context, script_dir, script_name, is_callback)
    return ConversationHandler.END

# ==============================================================================
# 🚀 2. EXECUTION ENGINE
# ==============================================================================

async def run_logic(update, context, script_dir, script_name, is_callback=False):
    """
    Handles installation of requirements and running the script.
    """
    chat_id = update.effective_chat.id
    
    async def send(text):
        if len(text) > 4000: text = text[:4000] + "... (truncated)"
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')

    # Paths
    req_path = os.path.join(script_dir, "requirements.txt")
    env_path = os.path.join(script_dir, ".env")
    lib_path = os.path.join(script_dir, "libs")
    script_path = os.path.join(script_dir, script_name)

    # 1. Install Requirements (if needed)
    # We check if 'libs' exists. If it exists, we assume deps are already installed to save time.
    if os.path.exists(req_path) and not os.path.exists(lib_path):
        await send("📦 First time setup: Installing requirements...")
        try:
            subprocess.check_call(
                ["pip", "install", "-r", "requirements.txt", "--target", "libs"],
                cwd=script_dir
            )
        except Exception as e:
            await send(f"❌ Failed to install requirements: {str(e)}")
            return

    # 2. Prepare Environment
    run_env = os.environ.copy()
    
    # Add local libs to path
    if os.path.exists(lib_path):
        run_env["PYTHONPATH"] = lib_path + os.pathsep + run_env.get("PYTHONPATH", "")
    
    # Load .env
    if os.path.exists(env_path):
        config = dotenv_values(env_path)
        for k, v in config.items():
            if v: run_env[k] = str(v)

    # 3. Execute
    await send(f"▶️ Executing `{script_name}`...")
    
    def run_process():
        return subprocess.run(
            ["python", script_name],
            cwd=script_dir,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=60 # 60 second timeout
        )

    try:
        # Run blocking code in thread
        result = await asyncio.get_running_loop().run_in_executor(None, run_process)
        
        output = result.stdout
        error = result.stderr
        
        msg = f"📝 **Output for {script_name}:**\n"
        if output: msg += f"```\n{output}\n```"
        if error: msg += f"\n**Errors:**\n```\n{error}\n```"
        if not output and not error: msg += "✅ (No Output)"
        
        await send(msg)
        
    except subprocess.TimeoutExpired:
        await send(f"🛑 **Timeout:** `{script_name}` ran longer than 60s.")
    except Exception as e:
        await send(f"❌ **Error:** {str(e)}")

# ==============================================================================
# 🎯 3. SHORTCUT COMMAND (/bpy)
# ==============================================================================

async def bpy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update): return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/bpy filename.py`\nUse `/list` to see your files.")
        return

    target_name = context.args[0]
    user_id = update.effective_user.id
    
    # Logic to find the folder
    # We stored folders as the filename without extension (to keep them clean)
    # But user might type "script.py" or just "script"
    
    search_name = target_name
    script_dir, clean_name = get_script_dir(user_id, search_name)
    
    # Check if the script file actually exists
    full_path = os.path.join(script_dir, clean_name)
    
    if not os.path.exists(full_path):
        await update.message.reply_text(f"❌ File `{clean_name}` not found in your storage.\nDid you upload it via `/runpy`?")
        return

    # Run it
    await run_logic(update, context, script_dir, clean_name)

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists saved scripts"""
    if not await restricted(update): return
    
    user_id = update.effective_user.id
    user_base = os.path.join(STORAGE_DIR, str(user_id))
    
    if not os.path.exists(user_base):
        await update.message.reply_text("📂 No scripts hosted yet.")
        return
        
    folders = os.listdir(user_base)
    if not folders:
        await update.message.reply_text("📂 No scripts hosted yet.")
        return
        
    msg = "📂 **Hosted Scripts:**\n\n"
    for folder in folders:
        # Assuming script name matches folder name + .py usually, 
        # or we check inside the folder
        msg += f"• `/bpy {folder}.py`\n"
        
    await update.message.reply_text(msg, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# --- MAIN ---
if __name__ == '__main__':
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

    if not TOKEN:
        print("❌ TOKEN missing.")
        exit(1)

    if not os.path.exists(STORAGE_DIR): os.makedirs(STORAGE_DIR)
    
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
    
    app.add_handler(CommandHandler("start", start_runpy)) # Mapping start to runpy flow for ease
    app.add_handler(CommandHandler("bpy", bpy_command))
    app.add_handler(CommandHandler("list", list_files))
    app.add_handler(conv)
    
    print("✅ Bot is polling...")
    app.run_polling()
