# Roblox Studio Setup Guide for Discord Commands

## 📋 Prerequisites

1. **Enable HTTP Requests** in your Roblox game:
   - Go to Game Settings → Security
   - Enable "Allow HTTP Requests"
   - This is required for the script to communicate with your Discord bot

2. **Have your Discord bot running** with a service created

## 🛠️ Setup Instructions

### Step 1: Get Your Service Information

1. Run `/get_services` in Discord
2. Note down your:
   - **Port number** (e.g., 8080)
   - **API Key** (long string of letters and numbers)

### Step 2: Configure the Script

1. Open `DiscordCommandHandler.lua` in Roblox Studio
2. Update the configuration at the top:

```lua
local DISCORD_SERVICE_CONFIG = {
    port = 8080,  -- Replace with YOUR port number
    api_key = "YOUR_API_KEY_HERE",  -- Replace with YOUR API key
    check_interval = 2,  -- How often to check for commands (seconds)
    base_url = "http://localhost"
}
```

### Step 3: Place the Script

1. In Roblox Studio, go to **ServerScriptService**
2. Create a new **Script** (not LocalScript)
3. Copy and paste the entire `DiscordCommandHandler.lua` content
4. Save the script

### Step 4: Test the Setup

1. **Start your Discord bot** (make sure the service is running)
2. **Run your Roblox game** in Studio
3. Check the **Output window** for startup messages:
   ```
   🚀 Discord Command Handler started!
   📡 Listening for commands on: http://localhost:8080/triggered?api_key=...
   ⏱️ Check interval: 2 seconds
   📋 Available commands: kick, ban, give
   ```

4. **Test a command** in Discord: `!kick YourUsername test`
5. You should see in Roblox Output:
   ```
   📨 Received 1 command(s) from Discord
   🎮 Processing command: kick with parameters: [YourUsername, test]
   🦵 KICKING PLAYER: YourUsername | Reason: test
   ✅ Command 'kick' executed successfully
   ```

## 🎮 Available Commands

### `!kick player reason`
- **Example**: `!kick BadPlayer123 breaking rules`
- **Effect**: Kicks the player from the game with the specified reason

### `!ban player reason`
- **Example**: `!ban Cheater456 using exploits`
- **Effect**: Kicks the player with a ban message (you can extend this to save to datastore)

### `!give player item amount`
- **Example**: `!give GoodPlayer123 Coins 1000`
- **Effect**: Gives the player currency/items (requires leaderstats setup)

## 🔧 Customization

### Adding New Commands

1. Add a new function to `CommandHandlers`:

```lua
function CommandHandlers.teleport(parameters, triggeredBy, triggeredAt)
    if #parameters < 2 then
        warn("Teleport command requires player name and location")
        return false
    end
    
    local playerName = parameters[1]
    local location = parameters[2]
    
    -- Your teleport logic here
    
    return true
end
```

2. Create the command in Discord with `/create_command`

### Modifying Existing Commands

- Edit the functions in `CommandHandlers`
- Add permission checks, logging, datastore integration, etc.

## 🚨 Troubleshooting

### "HTTP requests are not enabled"
- Enable HTTP requests in Game Settings → Security

### "Failed to parse Discord command response"
- Check that your API key is correct
- Make sure your Discord bot service is running

### "Player not found"
- The script searches by both Username and DisplayName
- Make sure the player is actually in the game

### Commands not triggering
- Check the Output window for error messages
- Verify your port and API key are correct
- Make sure the Discord bot service is running

## 🔒 Security Notes

- Keep your API key secure - don't share it publicly
- The script only works when the Discord bot is running
- Commands expire after 15 seconds automatically
- Only works with localhost (your computer)

## 📝 Example Usage

1. **In Discord**: `!kick TestPlayer123 spamming chat`
2. **In Roblox**: Player "TestPlayer123" gets kicked with reason "spamming chat"
3. **Output shows**: 
   ```
   🦵 KICKING PLAYER: TestPlayer123 | Reason: spamming chat
   ✅ Command 'kick' executed successfully
   ```

Your Roblox game is now connected to Discord commands! 🎉
