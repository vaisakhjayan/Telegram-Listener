from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import subprocess
import psutil
import os
import json
import asyncio
import platform
import socket

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
        return config["current_device"]
    
    # Auto-detect based on system info
    system = platform.system().lower()
    hostname = socket.gethostname().lower()
    
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

def get_scripts_for_group(chat_id):
    """Get scripts configured for a specific group and current device"""
    group_config = get_group_config(chat_id)
    if not group_config:
        return {}
    
    scripts = {}
    for script in group_config.get("scripts", []):
        # Handle both old format (single path) and new format (device-specific paths)
        if isinstance(script.get("path"), str):
            # Old format - single path
            scripts[script["name"]] = {
                "path": script["path"],
                "device": "legacy",
                "description": script.get("description", "")
            }
        else:
            # New format - device-specific paths
            devices = script.get("devices", {})
            for device_name, device_config in devices.items():
                if isinstance(device_config, str):
                    # Simple string path
                    script_key = f"{script['name']} ({device_name.upper()})"
                    scripts[script_key] = {
                        "path": device_config,
                        "device": device_name,
                        "description": script.get("description", ""),
                        "base_name": script["name"]
                    }
                else:
                    # Object with path and other configs
                    script_key = f"{script['name']} ({device_name.upper()})"
                    scripts[script_key] = {
                        "path": device_config.get("path", ""),
                        "device": device_name,
                        "description": device_config.get("description", script.get("description", "")),
                        "base_name": script["name"],
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
    
    # Group scripts by device for better organization
    device_scripts = {}
    for script_name, script_info in scripts.items():
        device = script_info["device"]
        if device not in device_scripts:
            device_scripts[device] = []
        device_scripts[device].append((script_name, script_info))
    
    # Create buttons for each device group
    for device, script_list in device_scripts.items():
        device_emoji = get_device_emoji(device)
        
        # Add device header if multiple devices
        if len(device_scripts) > 1:
            keyboard.append([
                InlineKeyboardButton(f"{device_emoji} {device.upper()}", callback_data=f"device_info_{device}")
            ])
        
        # Add script buttons for this device
        for script_name, script_info in script_list:
            # Create unique process key
            process_key = f"{script_name}_{device}"
            is_running = process_key in running_processes and running_processes[process_key].poll() is None
            
            # Only show buttons for current device or if it's a legacy script
            if device == CURRENT_DEVICE or device == "legacy":
                if is_running:
                    # Show stop button if running
                    keyboard.append([
                        InlineKeyboardButton(f"🔴 Stop {script_name}", callback_data=f"stop_{script_name}_{device}"),
                        InlineKeyboardButton(f"✅ Running", callback_data=f"status_{script_name}_{device}")
                    ])
                else:
                    # Show start button if not running
                    keyboard.append([
                        InlineKeyboardButton(f"🟢 Start {script_name}", callback_data=f"start_{script_name}_{device}"),
                        InlineKeyboardButton(f"⭕ Stopped", callback_data=f"status_{script_name}_{device}")
                    ])
            else:
                # Show info button for other devices
                keyboard.append([
                    InlineKeyboardButton(f"ℹ️ {script_name} (Other Device)", callback_data=f"info_{script_name}_{device}")
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
                
            device_emoji = get_device_emoji(CURRENT_DEVICE)
            message = group_config.get("welcome_message", f"🤖 Script Control Panel is ready!\n\n{device_emoji} Currently running on: **{CURRENT_DEVICE.upper()}**")
            
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
            
        device_emoji = get_device_emoji(CURRENT_DEVICE)
        welcome_message = group_config.get("welcome_message", f"🤖 **Script Control Panel** 🤖\n\n{device_emoji} Currently running on: **{CURRENT_DEVICE.upper()}**")
        await query.edit_message_text(
            welcome_message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return
    
    elif action_data == "settings":
        group_name = group_config.get("name", "Unknown") if group_config else "Not Configured"
        device_emoji = get_device_emoji(CURRENT_DEVICE)
        
        # Count scripts by device
        device_counts = {}
        for script_name, script_info in scripts.items():
            device = script_info["device"]
            device_counts[device] = device_counts.get(device, 0) + 1
        
        # Count running processes
        running_count = len([p for p in running_processes.values() if p.poll() is None])
        
        settings_msg = f"⚙️ **Bot Settings for {group_name}:**\n\n"
        settings_msg += f"{device_emoji} **Current Device:** {CURRENT_DEVICE.upper()}\n"
        settings_msg += f"👥 **Authorized Users:** {len(AUTHORIZED_USER_IDS)}\n"
        settings_msg += f"📝 **Total Scripts:** {len(scripts)}\n"
        for device, count in device_counts.items():
            emoji = get_device_emoji(device)
            settings_msg += f"   {emoji} {device.upper()}: {count}\n"
        settings_msg += f"🔄 **Running Processes:** {running_count}\n"
        settings_msg += f"📡 **Auto-post Control Panel:** {'✅' if config.get('auto_post_control_panel', False) else '❌'}\n"
        settings_msg += f"🆔 **Group Chat ID:** `{chat_id}`\n"
        
        await query.answer(settings_msg, show_alert=True)
        return
    
    elif action_data.startswith("device_info_"):
        device = action_data[12:]  # Remove "device_info_" prefix
        device_emoji = get_device_emoji(device)
        
        device_scripts = [name for name, info in scripts.items() if info["device"] == device]
        
        info_msg = f"{device_emoji} **{device.upper()} Device Info:**\n\n"
        info_msg += f"📝 **Scripts:** {len(device_scripts)}\n"
        info_msg += f"🔄 **Available:** {'✅' if device == CURRENT_DEVICE else '❌'}\n"
        
        if device == CURRENT_DEVICE:
            info_msg += f"💡 **Status:** This is the current device\n"
        else:
            info_msg += f"💡 **Status:** Scripts managed on other device\n"
        
        await query.answer(info_msg, show_alert=True)
        return
    
    elif action_data.startswith("status_"):
        # Extract script name and device from callback data
        parts = action_data[7:].split('_')  # Remove "status_" prefix
        if len(parts) >= 2:
            device = parts[-1]
            script_name = '_'.join(parts[:-1])
            
            script_info = scripts.get(script_name)
            if script_info:
                process_key = f"{script_name}_{device}"
                is_running = process_key in running_processes and running_processes[process_key].poll() is None
                
                device_emoji = get_device_emoji(device)
                status_msg = f"📊 **Script Status:**\n\n"
                status_msg += f"📝 **Name:** {script_name}\n"
                status_msg += f"{device_emoji} **Device:** {device.upper()}\n"
                status_msg += f"🔄 **Status:** {'✅ Running' if is_running else '⭕ Stopped'}\n"
                status_msg += f"📁 **Path:** `{script_info['path']}`\n"
                
                if script_info.get('description'):
                    status_msg += f"📋 **Description:** {script_info['description']}\n"
                
                await query.answer(status_msg, show_alert=True)
        return
    
    elif action_data.startswith("info_"):
        # Extract script name and device from callback data
        parts = action_data[5:].split('_')  # Remove "info_" prefix
        if len(parts) >= 2:
            device = parts[-1]
            script_name = '_'.join(parts[:-1])
            
            script_info = scripts.get(script_name)
            if script_info:
                device_emoji = get_device_emoji(device)
                info_msg = f"ℹ️ **Script Info:**\n\n"
                info_msg += f"📝 **Name:** {script_name}\n"
                info_msg += f"{device_emoji} **Device:** {device.upper()}\n"
                info_msg += f"📁 **Path:** `{script_info['path']}`\n"
                info_msg += f"💡 **Note:** This script runs on another device\n"
                
                if script_info.get('description'):
                    info_msg += f"📋 **Description:** {script_info['description']}\n"
                
                await query.answer(info_msg, show_alert=True)
        return
    
    elif action_data.startswith("start_"):
        # Extract script name and device from callback data
        parts = action_data[6:].split('_')  # Remove "start_" prefix
        if len(parts) >= 2:
            device = parts[-1]
            script_name = '_'.join(parts[:-1])
            
            script_info = scripts.get(script_name)
            if script_info and device == CURRENT_DEVICE:
                process_key = f"{script_name}_{device}"
                
                # Check if already running
                if process_key in running_processes and running_processes[process_key].poll() is None:
                    await query.edit_message_text(f"⚠️ Script '{script_name}' is already running on {device.upper()}!")
                    return
                
                # Start the script
                try:
                    python_cmd = script_info.get("python_cmd", "python3")
                    process = subprocess.Popen([python_cmd, script_info["path"]])
                    running_processes[process_key] = process
                    
                    device_emoji = get_device_emoji(device)
                    await query.edit_message_text(
                        f"✅ **Script Started Successfully!**\n\n"
                        f"📝 **Name:** {script_name}\n"
                        f"{device_emoji} **Device:** {device.upper()}\n"
                        f"🆔 **Process ID:** {process.pid}\n"
                        f"🐍 **Python Command:** {python_cmd}"
                    )
                except Exception as e:
                    await query.edit_message_text(f"❌ Failed to start '{script_name}' on {device.upper()}: {str(e)}")
            else:
                await query.edit_message_text("⚠️ Script not found or not available on current device.")
    
    elif action_data.startswith("stop_"):
        # Extract script name and device from callback data
        parts = action_data[5:].split('_')  # Remove "stop_" prefix
        if len(parts) >= 2:
            device = parts[-1]
            script_name = '_'.join(parts[:-1])
            
            process_key = f"{script_name}_{device}"
            
            if process_key in running_processes:
                process = running_processes[process_key]
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
                        
                        del running_processes[process_key]
                        
                        device_emoji = get_device_emoji(device)
                        await query.edit_message_text(
                            f"🛑 **Script Stopped Successfully!**\n\n"
                            f"📝 **Name:** {script_name}\n"
                            f"{device_emoji} **Device:** {device.upper()}\n"
                            f"✅ **Status:** Process terminated"
                        )
                    except Exception as e:
                        await query.edit_message_text(f"❌ Failed to stop '{script_name}' on {device.upper()}: {str(e)}")
                else:
                    await query.edit_message_text(f"⚠️ Script '{script_name}' is not running on {device.upper()}.")
            else:
                await query.edit_message_text(f"⚠️ No running process found for '{script_name}' on {device.upper()}.")

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
