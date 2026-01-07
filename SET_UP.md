# Discord Bot Setup Guide

## 📋 Prerequisites

- Python 3.8+
- Discord Bot Token
- Roblox Studio

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install discord.py aiohttp python-dotenv
```

### 2. Create `.env` File

Create a `.env` file in the project directory:

```env
DISCORD_BOT_TOKEN=your_bot_token_here
```

### 3. Start the Bot

```bash
python bot.py
```

## 🎮 Discord Commands

### For Server Owners Only

#### `/verify <username>`
Verify your Roblox account by adding a code to your profile description.

**Example:**
```
/verify YourRobloxUsername
```

#### `/create_service <name> [description]`
Create a command service (1 per server).

**Example:**
```
/create_service MyGame Controls for my game
```

#### `/create_command`
Create a new command (max 10 per service, max 5 parameters).

Opens a modal where you input:
- Command name (e.g., "kick")
- Parameters (e.g., "player reason")

#### `/mod_panel`
Manage who can execute commands.

- Add User: Authorize verified users
- Remove User: Revoke authorization

## 🎯 Using Commands

### Authorization

Only these users can execute commands:
1. **Server Owner** (always authorized)
2. **Users added via `/mod_panel`** (must be verified)

### Executing Commands

Use `!` prefix in any channel:

```
!kick BadPlayer123 spamming
!ban Cheater456 exploiting
!give GoodPlayer Coins 1000
```

## 🔧 Roblox Setup

### 1. Enable HTTP Requests

- Game Settings → Security
- Enable "Allow HTTP Requests"

### 2. Configure Script

Open `DiscordCommandHandler.lua` and update:

```lua
local CONFIG = {
    port = 8080,              -- Your service port
    api_key = "YOUR_KEY",     -- Your API key from /create_service
    check_interval = 2,
    base_url = "http://localhost"
}
```

### 3. Add to ServerScriptService

1. Open Roblox Studio
2. Go to ServerScriptService
3. Create new Script
4. Paste `DiscordCommandHandler.lua` content
5. Save

### 4. Test

1. Start Discord bot
2. Run Roblox game in Studio
3. Check Output window for startup message
4. Try a command in Discord: `!kick TestPlayer test`

## 🛠️ Adding Custom Commands

### In CommandHandlers (Lua):

```lua
function CommandHandlers.teleport(params)
    if #params < 2 then
        warn("Teleport requires player and location")
        return false
    end
    
    local playerName = params[1]
    local location = params[2]
    
    -- Your teleport logic
    
    return true
end
```

### Create in Discord:

Use `/create_command` and input:
- Command: `teleport`
- Parameters: `player location`

## 🔒 Security

- ✅ Only server owner can create services/commands
- ✅ Only server owner can authorize users
- ✅ Users must be verified to execute commands
- ✅ API keys secure service access
- ✅ Commands expire after 60 seconds

## 🚨 Troubleshooting

### Bot Not Responding
- Check bot is online
- Verify bot has proper permissions
- Check console for errors

### Roblox Not Receiving Commands
- Verify HTTP requests enabled
- Check API key matches
- Ensure service is running
- Check Roblox Output for errors

### "Not Verified" Error
- Use `/verify` command first
- Add verification code to Roblox profile
- Click "Verify Now" button

### "Unauthorized" Error
- Server owner must add you via `/mod_panel`
- Ensure you're verified

## 📊 Service Limits

- **1 service per server**
- **10 commands per service**
- **5 parameters per command**
- **Commands expire after 60 seconds**

## 🎯 Example Workflow

1. **Server Owner Setup:**
```
/verify MyUsername
/create_service GameControl My awesome game
/create_command (create "kick" with params "player reason")
```

2. **Authorize Moderator:**
```
/mod_panel
Click "Add User"
Select moderator
```

3. **Moderator Usage:**
```
/verify TheirUsername
!kick BadPlayer breaking rules
```

## 📝 Command Examples

```
!kick Player123 spamming
!ban Exploiter456 using hacks
!give VIPPlayer Coins 5000
!teleport Player123 Spawn
!announce Hello everyone!
```

Your Discord-Roblox command system is ready! 🎉