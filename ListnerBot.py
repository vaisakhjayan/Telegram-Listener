from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import subprocess
import psutil
import os
import json
import asyncio

# === LOAD CONFIG ===
def load_config():
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ config.json not found! Please create it first.")
        exit(1)
    except json.JSONDecodeError:
        print("❌ Invalid JSON in config.json!")
        exit(1)

def save_config(config):
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=2)

# Load configuration
config = load_config()
BOT_TOKEN = config["bot_token"]
AUTHORIZED_USER_IDS = config["authorized_user_ids"]

# Track running processes
running_processes = {}

# === HELPER FUNCTIONS ===
def get_group_config(chat_id):
    """Get configuration for a specific group"""
    groups = config.get("groups", {})
    return groups.get(str(chat_id), None)

def get_scripts_for_group(chat_id):
    """Get scripts configured for a specific group"""
    group_config = get_group_config(chat_id)
    if not group_config:
        return {}
    
    scripts = {}
    for script in group_config.get("scripts", []):
        scripts[script["name"]] = script["path"]
    return scripts

async def create_control_panel(chat_id):
    """Create the control panel keyboard for a specific group"""
    scripts = get_scripts_for_group(chat_id)
    if not scripts:
        return None
    
    keyboard = []
    for script_name, script_path in scripts.items():
        is_running = script_name in running_processes and running_processes[script_name].poll() is None
        
        if is_running:
            # Show stop button if running
            keyboard.append([
                InlineKeyboardButton(f"🔴 Stop {script_name}", callback_data=f"stop_{script_name}"),
                InlineKeyboardButton(f"✅ Running", callback_data="status")
            ])
        else:
            # Show start button if not running
            keyboard.append([
                InlineKeyboardButton(f"🟢 Start {script_name}", callback_data=f"start_{script_name}"),
                InlineKeyboardButton(f"⭕ Stopped", callback_data="status")
            ])
    
    # Add utility buttons
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh Status", callback_data="refresh"),
        InlineKeyboardButton("⚙️ Settings", callback_data="settings")
    ])
    
    return InlineKeyboardMarkup(keyboard)

async def auto_post_control_panel(application):
    """Auto-post control panel to all configured groups"""
    if not config.get("auto_post_control_panel", False):
        return
    
    groups = config.get("groups", {})
    if not groups:
        print("⚠️ No groups configured in config.json")
        return
    
    for group_id, group_config in groups.items():
        if not group_config.get("auto_post", True):
            continue
            
        try:
            keyboard = await create_control_panel(group_id)
            if not keyboard:
                print(f"⚠️ No scripts configured for group {group_id}")
                continue
                
            message = group_config.get("welcome_message", "🤖 Script Control Panel is ready!")
            
            await application.bot.send_message(
                chat_id=int(group_id),
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            print(f"✅ Control panel sent to group {group_config.get('name', group_id)}")
        except Exception as e:
            print(f"❌ Failed to send control panel to group {group_id}: {e}")

# === COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return
    
    await menu(update, context)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    chat_id = update.effective_chat.id
    
    # 🔍 TEMPORARY: Print Group ID for setup
    print(f"Group ID: {chat_id}")
    print(f"Chat Type: {update.effective_chat.type}")
    print(f"Chat Title: {update.effective_chat.title}")

    group_config = get_group_config(chat_id)
    if not group_config:
        await update.message.reply_text(
            f"⚠️ **Group Not Configured**\n\n"
            f"This group (`{chat_id}`) is not set up yet.\n\n"
            f"**To add this group:**\n"
            f"1. Copy this Group ID: `{chat_id}`\n"
            f"2. Add it to your config.json\n"
            f"3. Configure scripts for this group\n"
            f"4. Restart the bot\n\n"
            f"Or use `/setup_group` to auto-configure!",
            parse_mode='Markdown'
        )
        return

    keyboard = await create_control_panel(chat_id)
    if not keyboard:
        await update.message.reply_text("⚠️ No scripts configured for this group.")
        return

    welcome_message = group_config.get("welcome_message", "🤖 Script Control Panel 🤖")
    await update.message.reply_text(
        welcome_message,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def setup_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to set up a new group automatically"""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    chat_id = str(update.effective_chat.id)
    chat_type = update.effective_chat.type
    chat_title = update.effective_chat.title or "Unknown Group"
    
    if chat_type not in ['group', 'supergroup']:
        await update.message.reply_text(
            "⚠️ This command should be used in a group chat, not in DMs.\n\n"
            f"Current chat type: {chat_type}\n"
            f"Chat ID: `{chat_id}`",
            parse_mode='Markdown'
        )
        return
    
    # Check if group already exists
    if chat_id in config.get("groups", {}):
        await update.message.reply_text(
            f"✅ **Group Already Configured!**\n\n"
            f"📍 Group: {chat_title}\n"
            f"🆔 ID: `{chat_id}`\n\n"
            f"This group is already set up. Use /menu to see the control panel!",
            parse_mode='Markdown'
        )
        return
    
    # Add new group with default configuration
    if "groups" not in config:
        config["groups"] = {}
    
    config["groups"][chat_id] = {
        "name": chat_title,
        "welcome_message": f"🤖 **{chat_title} Control Panel** 🤖\n\nManage your scripts here!\n\n🟢 = Start Script\n🔴 = Stop Script\n📊 = Current Status",
        "scripts": [
            {
                "name": "Example Script",
                "path": "/path/to/your/script.py",
                "description": "Replace this with your actual script"
            }
        ],
        "auto_post": True
    }
    
    save_config(config)
    
    await update.message.reply_text(
        f"✅ **Group Setup Complete!**\n\n"
        f"📍 Group: {chat_title}\n"
        f"🆔 ID: `{chat_id}`\n\n"
        f"**Next Steps:**\n"
        f"1. Edit `config.json` to add your scripts\n"
        f"2. Replace the example script with your real ones\n"
        f"3. Restart the bot\n"
        f"4. Control panel will auto-appear here!\n\n"
        f"**Example script entry:**\n"
        f"```json\n"
        f'\"name\": \"My Script\",\n'
        f'\"path\": \"/full/path/to/script.py\"\n'
        f"```",
        parse_mode='Markdown'
    )
    print(f"✅ New group added: {chat_title} ({chat_id})")

# === MESSAGE HANDLER (for debugging) ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any message to log chat info (for debugging)"""
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USER_IDS:
        return
    
    chat_id = str(update.effective_chat.id)
    chat_type = update.effective_chat.type
    chat_title = update.effective_chat.title or "Unknown"
    
    # Only log if it's a group and we haven't set it up yet
    if chat_type in ['group', 'supergroup'] and chat_id not in config.get("groups", {}):
        print(f"💡 HINT: Found unconfigured group!")
        print(f"   Title: {chat_title}")
        print(f"   ID: {chat_id}")
        print(f"   Use /setup_group in this chat to add it!")

# === BUTTON HANDLER ===
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    await query.answer()

    if user_id not in AUTHORIZED_USER_IDS:
        await query.edit_message_text("❌ You are not authorized to trigger this script.")
        return

    action_data = query.data
    scripts = get_scripts_for_group(chat_id)
    group_config = get_group_config(chat_id)
    
    if action_data == "refresh":
        # Refresh the menu
        keyboard = await create_control_panel(chat_id)
        if not keyboard:
            await query.edit_message_text("⚠️ No scripts configured for this group.")
            return
            
        welcome_message = group_config.get("welcome_message", "🤖 Script Control Panel 🤖")
        await query.edit_message_text(
            welcome_message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return
    
    elif action_data == "settings":
        group_name = group_config.get("name", "Unknown") if group_config else "Not Configured"
        settings_msg = f"⚙️ **Bot Settings for {group_name}:**\n\n"
        settings_msg += f"• Authorized Users: {len(AUTHORIZED_USER_IDS)}\n"
        settings_msg += f"• Configured Scripts: {len(scripts)}\n"
        settings_msg += f"• Running Processes: {len([p for p in running_processes.values() if p.poll() is None])}\n"
        settings_msg += f"• Auto-post Control Panel: {'✅' if config.get('auto_post_control_panel', False) else '❌'}\n"
        settings_msg += f"• Group Chat ID: `{chat_id}`\n"
        
        await query.answer(settings_msg, show_alert=True)
        return
    
    elif action_data == "status":
        status_msg = "📊 **Current Script Status:**\n\n"
        for script_name in scripts:
            is_running = script_name in running_processes and running_processes[script_name].poll() is None
            status = "✅ Running" if is_running else "⭕ Stopped"
            status_msg += f"• {script_name}: {status}\n"
        
        await query.answer(status_msg, show_alert=True)
        return
    
    elif action_data.startswith("start_"):
        script_name = action_data[6:]  # Remove "start_" prefix
        script_path = scripts.get(script_name)
        
        if script_path:
            # Check if already running
            if script_name in running_processes and running_processes[script_name].poll() is None:
                await query.edit_message_text(f"⚠️ Script '{script_name}' is already running!")
                return
            
            # Start the script
            try:
                process = subprocess.Popen(["python3", script_path])
                running_processes[script_name] = process
                await query.edit_message_text(f"✅ Script '{script_name}' has been started successfully!\n\nProcess ID: {process.pid}")
            except Exception as e:
                await query.edit_message_text(f"❌ Failed to start '{script_name}': {str(e)}")
        else:
            await query.edit_message_text("⚠️ Script not found.")
    
    elif action_data.startswith("stop_"):
        script_name = action_data[5:]  # Remove "stop_" prefix
        
        if script_name in running_processes:
            process = running_processes[script_name]
            if process.poll() is None:  # Process is still running
                try:
                    # Try to terminate gracefully first
                    process.terminate()
                    try:
                        process.wait(timeout=5)  # Wait up to 5 seconds
                    except subprocess.TimeoutExpired:
                        # Force kill if it doesn't terminate gracefully
                        process.kill()
                        process.wait()
                    
                    del running_processes[script_name]
                    await query.edit_message_text(f"🛑 Script '{script_name}' has been stopped successfully!")
                except Exception as e:
                    await query.edit_message_text(f"❌ Failed to stop '{script_name}': {str(e)}")
            else:
                await query.edit_message_text(f"⚠️ Script '{script_name}' is not running.")
        else:
            await query.edit_message_text(f"⚠️ No running process found for '{script_name}'.")

# === POST INIT HOOK ===
async def post_init(application):
    """Called after the bot starts"""
    print("🚀 Bot initialization complete!")
    
    # Auto-post control panel if enabled
    if config.get("auto_post_control_panel", False):
        print("📡 Auto-posting control panels to all groups...")
        await auto_post_control_panel(application)

# === APP SETUP ===
app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("menu", menu))
app.add_handler(CommandHandler("setup_group", setup_group))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("🤖 Multi-Group Listener bot is starting...")
print("💡 Control panels will auto-post to all configured groups!")
total_groups = len(config.get("groups", {}))
total_scripts = sum(len(group.get("scripts", [])) for group in config.get("groups", {}).values())
print(f"📋 Loaded {total_groups} group(s) with {total_scripts} total script(s)")
print("\n🔧 SETUP INSTRUCTIONS:")
print("1. Add bot to your group")
print("2. Type /setup_group in the group")
print("3. Edit config.json to add your scripts")
print("4. Restart bot - control panel will auto-appear!")
app.run_polling()
