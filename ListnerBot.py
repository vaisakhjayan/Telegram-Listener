from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import subprocess
import psutil
import os
import json
import asyncio
import platform
import socket
import telegram.error

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

# === DEVICE DETECTION ===
def get_current_device():
    """Detect current device based on config or system info"""
    # First try to get from config
    if "current_device" in config:
        return config["current_device"].lower()
    
    # Auto-detect based on system info
    system = platform.system().lower()
    
    # You can customize this logic based on your device names
    if system == "darwin":  # macOS
        return "mac"
    elif system == "windows":
        return "pc"
    else:
        return "unknown"

# Load configuration
config = load_config()
BOT_TOKEN = config["bot_token"]
AUTHORIZED_USER_IDS = config["authorized_user_ids"]
CURRENT_DEVICE = get_current_device()

# Track running processes (now with device context)
running_processes = {}

# === HELPER FUNCTIONS ===
def get_group_config(chat_id):
    """Get configuration for a specific group"""
    groups = config.get("groups", {})
    return groups.get(str(chat_id), None)

def get_selected_device(chat_id):
    """Get the selected device for a specific group"""
    group_config = get_group_config(chat_id)
    if not group_config:
        return CURRENT_DEVICE
    return group_config.get("selected_device", CURRENT_DEVICE)

def set_selected_device(chat_id, device):
    """Set the selected device for a specific group"""
    groups = config.get("groups", {})
    if str(chat_id) in groups:
        groups[str(chat_id)]["selected_device"] = device
        save_config(config)

def get_scripts_for_group(chat_id):
    """Get scripts configured for a specific group"""
    group_config = get_group_config(chat_id)
    if not group_config:
        return {}
    
    scripts = {}
    for script in group_config.get("scripts", []):
        # Only use device-specific paths
        devices = script.get("devices", {})
        for device_name, device_config in devices.items():
            if isinstance(device_config, dict):
                script_key = script["name"]
                scripts[script_key] = {
                    "path": device_config.get("path", ""),
                    "device": device_name,
                    "description": device_config.get("description", script.get("description", "")),
                    "python_cmd": device_config.get("python_cmd", "python3")
                }
    
    return scripts

def get_device_emoji(device_name):
    """Get emoji for device type"""
    device_emojis = {
        "mac": "🍎",
        "pc": "🖥️",
        "windows": "🖥️",
        "linux": "🐧",
        "legacy": "💻"
    }
    return device_emojis.get(device_name.lower(), "💻")

async def create_control_panel(chat_id):
    """Create the control panel keyboard for a specific group"""
    scripts = get_scripts_for_group(chat_id)
    if not scripts:
        return None
    
    keyboard = []
    selected_device = get_selected_device(chat_id)
    
    # Add device selection buttons at the top
    device_buttons = []
    for device in ["mac", "pc"]:
        emoji = "✅" if device == selected_device else "⚪"
        device_buttons.append(
            InlineKeyboardButton(
                f"{emoji} {device.upper()}", 
                callback_data=f"select_device_{device}"
            )
        )
    keyboard.append(device_buttons)
    
    # Add script buttons for all devices
    for script_name, script_info in scripts.items():
        device = script_info['device']
        process_key = f"{script_name}_{device}"
        is_running = process_key in running_processes and running_processes[process_key].poll() is None
        
        # Show status based on which device is running the script
        device_indicator = "🟢" if device == selected_device else "⚪"
        if is_running:
            # Show stop button if running on this device
            if CURRENT_DEVICE == device:
                keyboard.append([
                    InlineKeyboardButton(f"🔴 Stop {script_name}", callback_data=f"stop_{script_name}_{device}"),
                    InlineKeyboardButton(f"✅ Running on {device.upper()}", callback_data=f"status_{script_name}_{device}")
                ])
            else:
                # Show status if running on another device
                keyboard.append([
                    InlineKeyboardButton(f"✅ Running on {device.upper()}", callback_data=f"status_{script_name}_{device}")
                ])
        else:
            # Show start button if this is the selected device and we're running on it
            if device == selected_device and CURRENT_DEVICE == selected_device:
                keyboard.append([
                    InlineKeyboardButton(f"🟢 Start {script_name}", callback_data=f"start_{script_name}_{device}"),
                    InlineKeyboardButton(f"⭕ Ready on {device.upper()}", callback_data=f"status_{script_name}_{device}")
                ])
            else:
                # Show unavailable status for other devices
                keyboard.append([
                    InlineKeyboardButton(f"⭕ Stopped on {device.upper()}", callback_data=f"status_{script_name}_{device}")
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
                
            selected_device = get_selected_device(group_id)
            device_emoji = get_device_emoji(selected_device)
            message = group_config.get("welcome_message", f"🤖 Script Control Panel\n\n{device_emoji} Selected device: **{selected_device.upper()}**")
            
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
    print(f"Current Device: {CURRENT_DEVICE}")

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

    device_emoji = get_device_emoji(CURRENT_DEVICE)
    welcome_message = group_config.get("welcome_message", f"🤖 **Script Control Panel** 🤖\n\n{device_emoji} Currently running on: **{CURRENT_DEVICE.upper()}**")
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
    
    device_emoji = get_device_emoji(CURRENT_DEVICE)
    config["groups"][chat_id] = {
        "name": chat_title,
        "welcome_message": f"🤖 **{chat_title} Control Panel** 🤖\n\nManage your scripts here!\n\n🟢 = Start Script\n🔴 = Stop Script\n📊 = Current Status\n\n{device_emoji} Device: **{CURRENT_DEVICE.upper()}**",
        "scripts": [
            {
                "name": "Example Script",
                "description": "Replace this with your actual script",
                "devices": {
                    "mac": {
                        "path": "/Users/your-username/path/to/script.py",
                        "python_cmd": "python3",
                        "description": "macOS version"
                    },
                    "pc": {
                        "path": "C:\\Users\\your-username\\path\\to\\script.py",
                        "python_cmd": "python",
                        "description": "Windows version"
                    }
                }
            }
        ],
        "auto_post": True
    }
    
    save_config(config)
    
    await update.message.reply_text(
        f"✅ **Group Setup Complete!**\n\n"
        f"📍 Group: {chat_title}\n"
        f"🆔 ID: `{chat_id}`\n"
        f"{device_emoji} Current Device: **{CURRENT_DEVICE.upper()}**\n\n"
        f"**Next Steps:**\n"
        f"1. Edit `config.json` to add your scripts\n"
        f"2. Configure paths for both Mac and PC\n"
        f"3. Restart the bot\n"
        f"4. Control panel will auto-appear here!\n\n"
        f"**Multi-Device Script Example:**\n"
        f"```json\n"
        f'{{\n'
        f'  \"name\": \"My Script\",\n'
        f'  \"description\": \"Script description\",\n'
        f'  \"devices\": {{\n'
        f'    \"mac\": {{\n'
        f'      \"path\": \"/Users/you/script.py\",\n'
        f'      \"python_cmd\": \"python3\"\n'
        f'    }},\n'
        f'    \"pc\": {{\n'
        f'      \"path\": \"C:\\\\Users\\\\you\\\\script.py\",\n'
        f'      \"python_cmd\": \"python\"\n'
        f'    }}\n'
        f'  }}\n'
        f'}}\n'
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
        print(f"   Device: {CURRENT_DEVICE}")
        print(f"   Use /setup_group in this chat to add it!")

# === BUTTON HANDLER ===
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if user_id not in AUTHORIZED_USER_IDS:
        await query.answer("❌ You are not authorized to use this bot.")
        return

    try:
        # Extract the command and parameters
        data = query.data.split('_')
        command = data[0]

        # Handle device selection
        if command == "select":
            if data[1] == "device":
                device = data[2]
                old_device = get_selected_device(chat_id)
                
                # Always update the device selection
                set_selected_device(chat_id, device)
                await query.answer(f"✅ Selected {device.upper()} device")
                
                # Update the control panel
                keyboard = await create_control_panel(chat_id)
                if keyboard:
                    group_config = get_group_config(chat_id)
                    device_emoji = get_device_emoji(device)
                    message = group_config.get("welcome_message", f"🤖 Script Control Panel\n\n{device_emoji} Selected device: **{device.upper()}**\n💻 Running on: **{CURRENT_DEVICE.upper()}**")
                    
                    await query.message.edit_text(
                        text=message,
                        reply_markup=keyboard,
                        parse_mode='Markdown'
                    )
                return

        # Handle script start
        if command == "start":
            script_name = '_'.join(data[1:-1])  # Handle script names with underscores
            device = data[-1]
            
            # Only start if we're running on the selected device
            if CURRENT_DEVICE != device:
                await query.answer(f"❌ This instance is running on {CURRENT_DEVICE.upper()}, can't start {device.upper()} scripts!")
                return
            
            # Get script info
            scripts = get_scripts_for_group(chat_id)
            script_info = scripts.get(script_name)
            
            if not script_info:
                await query.answer("❌ Script configuration not found!")
                return
            
            # Get the script path and verify it exists
            script_path = script_info["path"]
            if not os.path.exists(script_path):
                await query.answer(f"❌ Script file not found: {script_path}")
                return
            
            try:
                # Start the script
                process_key = f"{script_name}_{device}"
                python_cmd = script_info.get("python_cmd", "python3")
                process = subprocess.Popen([python_cmd, script_path])
                running_processes[process_key] = process
                
                # Update the control panel
                keyboard = await create_control_panel(chat_id)
                if keyboard:
                    await query.message.edit_reply_markup(reply_markup=keyboard)
                
                await query.answer(f"✅ Started {script_name} on {device.upper()}")
            except Exception as e:
                await query.answer(f"❌ Failed to start script: {str(e)}")
            return

        # Handle script stop
        elif command == "stop":
            script_name = '_'.join(data[1:-1])  # Handle script names with underscores
            device = data[-1]
            
            # Only stop if we're running on the correct device
            if CURRENT_DEVICE != device:
                await query.answer(f"❌ This instance is running on {CURRENT_DEVICE.upper()}, can't stop {device.upper()} scripts!")
                return
            
            process_key = f"{script_name}_{device}"
            if process_key in running_processes:
                process = running_processes[process_key]
                if process.poll() is None:  # Process is still running
                    process.terminate()
                    try:
                        process.wait(timeout=5)  # Wait up to 5 seconds for graceful termination
                    except subprocess.TimeoutExpired:
                        process.kill()  # Force kill if it doesn't terminate
                    
                    del running_processes[process_key]
                    
                    # Update the control panel
                    keyboard = await create_control_panel(chat_id)
                    if keyboard:
                        await query.message.edit_reply_markup(reply_markup=keyboard)
                    
                    await query.answer(f"✅ Stopped {script_name} on {device.upper()}")
                else:
                    del running_processes[process_key]
                    await query.answer(f"ℹ️ {script_name} was already stopped")
            else:
                await query.answer(f"⚠️ No running process found for {script_name}")
        
        # Handle refresh
        elif command == "refresh":
            keyboard = await create_control_panel(chat_id)
            if keyboard:
                selected_device = get_selected_device(chat_id)
                device_emoji = get_device_emoji(selected_device)
                message = get_group_config(chat_id).get("welcome_message", f"🤖 Script Control Panel\n\n{device_emoji} Selected device: **{selected_device.upper()}**\n💻 Running on: **{CURRENT_DEVICE.upper()}**")
                
                await query.message.edit_text(
                    text=message,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                await query.message.edit_text("⚠️ No scripts configured for this group.")
        
        # Handle settings
        elif command == "settings":
            group_config = get_group_config(chat_id)
            if not group_config:
                await query.answer("⚠️ Group not configured!")
                return
            
            selected_device = get_selected_device(chat_id)
            device_emoji = get_device_emoji(selected_device)
            
            settings_msg = f"⚙️ **Bot Settings**\n\n"
            settings_msg += f"👥 **Group:** {group_config.get('name', 'Unknown')}\n"
            settings_msg += f"{device_emoji} **Selected Device:** {selected_device.upper()}\n"
            settings_msg += f"💻 **Running On:** {CURRENT_DEVICE.upper()}\n"
            settings_msg += f"📝 **Scripts:** {len(get_scripts_for_group(chat_id))}\n"
            settings_msg += f"🔄 **Running:** {len([p for p in running_processes.values() if p.poll() is None])}\n"
            
            await query.answer(settings_msg, show_alert=True)
    except telegram.error.BadRequest as e:
        if "Message is not modified" in str(e):
            # Ignore this error, it's not a problem
            await query.answer("No changes needed")
        else:
            # Log other BadRequest errors
            print(f"❌ Telegram error: {str(e)}")
            await query.answer("❌ Failed to update message")
    except Exception as e:
        # Log any other errors
        print(f"❌ Error in button handler: {str(e)}")
        await query.answer("❌ An error occurred")

# === POST INIT HOOK ===
async def post_init(application):
    """Called after the bot starts"""
    device_emoji = get_device_emoji(CURRENT_DEVICE)
    print(f"🚀 Bot initialization complete!")
    print(f"{device_emoji} Running on device: {CURRENT_DEVICE.upper()}")
    
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

device_emoji = get_device_emoji(CURRENT_DEVICE)
print(f"🤖 Multi-Device Listener bot is starting...")
print(f"{device_emoji} Current Device: {CURRENT_DEVICE.upper()}")
print("💡 Control panels will auto-post to all configured groups!")
total_groups = len(config.get("groups", {}))
total_scripts = sum(len(get_scripts_for_group(group_id)) for group_id in config.get("groups", {}))
print(f"📋 Loaded {total_groups} group(s) with {total_scripts} total script(s)")
print("\n🔧 MULTI-DEVICE SETUP INSTRUCTIONS:")
print("1. Add bot to your group")
print("2. Type /setup_group in the group")
print("3. Edit config.json to add device-specific script paths")
print("4. Set 'current_device' in config.json (mac/pc)")
print("5. Restart bot - control panel will auto-appear!")
print("6. Deploy same config to other devices and restart there too!")
app.run_polling()
