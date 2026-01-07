-- Discord Command Handler for Roblox Studio
-- Place this script in ServerScriptService
-- Make sure HTTP requests are enabled in your game settings

local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

-- Configuration
local DISCORD_SERVICE_CONFIG = {
    port = 8080,  -- Change this to match your Discord bot service port
    api_key = "YOUR_API_KEY_HERE",  -- Replace with your actual API key from Discord bot
    check_interval = 2,  -- Check for commands every 2 seconds
    base_url = "http://localhost"
}

-- Construct the full URL
local TRIGGERED_COMMANDS_URL = DISCORD_SERVICE_CONFIG.base_url .. ":" .. DISCORD_SERVICE_CONFIG.port .. "/triggered?api_key=" .. DISCORD_SERVICE_CONFIG.api_key

-- Command handlers
local CommandHandlers = {}

-- Kick command handler
function CommandHandlers.kick(parameters, triggeredBy, triggeredAt)
    if #parameters < 1 then
        warn("Kick command requires at least a player name")
        return false
    end
    
    local targetPlayerName = parameters[1]
    local reason = "No reason provided"
    
    -- Combine remaining parameters as reason
    if #parameters > 1 then
        local reasonParts = {}
        for i = 2, #parameters do
            table.insert(reasonParts, parameters[i])
        end
        reason = table.concat(reasonParts, " ")
    end
    
    -- Find the player
    local targetPlayer = nil
    for _, player in pairs(Players:GetPlayers()) do
        if string.lower(player.Name) == string.lower(targetPlayerName) or 
           string.lower(player.DisplayName) == string.lower(targetPlayerName) then
            targetPlayer = player
            break
        end
    end
    
    if not targetPlayer then
        warn("Player '" .. targetPlayerName .. "' not found in the game")
        return false
    end
    
    -- Kick the player
    print("🦵 KICKING PLAYER: " .. targetPlayer.Name .. " | Reason: " .. reason)
    targetPlayer:Kick("You have been kicked from the game.\nReason: " .. reason)
    
    return true
end

-- Ban command handler (example)
function CommandHandlers.ban(parameters, triggeredBy, triggeredAt)
    if #parameters < 1 then
        warn("Ban command requires at least a player name")
        return false
    end
    
    local targetPlayerName = parameters[1]
    local reason = "No reason provided"
    
    if #parameters > 1 then
        local reasonParts = {}
        for i = 2, #parameters do
            table.insert(reasonParts, parameters[i])
        end
        reason = table.concat(reasonParts, " ")
    end
    
    -- Find the player
    local targetPlayer = nil
    for _, player in pairs(Players:GetPlayers()) do
        if string.lower(player.Name) == string.lower(targetPlayerName) or 
           string.lower(player.DisplayName) == string.lower(targetPlayerName) then
            targetPlayer = player
            break
        end
    end
    
    if not targetPlayer then
        warn("Player '" .. targetPlayerName .. "' not found in the game")
        return false
    end
    
    -- For this example, we'll just kick them with a ban message
    -- In a real game, you'd want to store the ban in a datastore
    print("🔨 BANNING PLAYER: " .. targetPlayer.Name .. " | Reason: " .. reason)
    targetPlayer:Kick("You have been banned from the game.\nReason: " .. reason .. "\n\nIf you believe this is a mistake, contact the server administrators.")
    
    return true
end

-- Give item command handler (example)
function CommandHandlers.give(parameters, triggeredBy, triggeredAt)
    if #parameters < 2 then
        warn("Give command requires player name and item")
        return false
    end
    
    local targetPlayerName = parameters[1]
    local itemName = parameters[2]
    local amount = tonumber(parameters[3]) or 1
    
    -- Find the player
    local targetPlayer = nil
    for _, player in pairs(Players:GetPlayers()) do
        if string.lower(player.Name) == string.lower(targetPlayerName) or 
           string.lower(player.DisplayName) == string.lower(targetPlayerName) then
            targetPlayer = player
            break
        end
    end
    
    if not targetPlayer then
        warn("Player '" .. targetPlayerName .. "' not found in the game")
        return false
    end
    
    print("🎁 GIVING ITEM: " .. itemName .. " x" .. amount .. " to " .. targetPlayer.Name)
    
    -- Example: Give leaderstats currency
    local leaderstats = targetPlayer:FindFirstChild("leaderstats")
    if leaderstats then
        local currency = leaderstats:FindFirstChild(itemName)
        if currency and currency:IsA("IntValue") then
            currency.Value = currency.Value + amount
            print("✅ Successfully gave " .. amount .. " " .. itemName .. " to " .. targetPlayer.Name)
            return true
        end
    end
    
    warn("Could not give item '" .. itemName .. "' to player '" .. targetPlayerName .. "'")
    return false
end

-- Function to check for triggered commands
local function checkForTriggeredCommands()
    local success, response = pcall(function()
        return HttpService:GetAsync(TRIGGERED_COMMANDS_URL)
    end)
    
    if not success then
        -- Silently fail - don't spam console if Discord bot is offline
        return
    end
    
    local data
    local parseSuccess, parseResult = pcall(function()
        return HttpService:JSONDecode(response)
    end)
    
    if not parseSuccess then
        warn("Failed to parse Discord command response: " .. tostring(parseResult))
        return
    end
    
    data = parseResult
    
    if data.count and data.count > 0 then
        print("📨 Received " .. data.count .. " command(s) from Discord")
        
        for _, command in pairs(data.triggered_commands) do
            local commandName = command.command
            local parameters = command.parameters or {}
            local triggeredBy = command.triggered_by
            local triggeredAt = command.triggered_at
            
            print("🎮 Processing command: " .. commandName .. " with parameters: [" .. table.concat(parameters, ", ") .. "]")
            
            -- Execute the command
            if CommandHandlers[commandName] then
                local success, result = pcall(function()
                    return CommandHandlers[commandName](parameters, triggeredBy, triggeredAt)
                end)
                
                if success and result then
                    print("✅ Command '" .. commandName .. "' executed successfully")
                else
                    warn("❌ Command '" .. commandName .. "' failed to execute")
                end
            else
                warn("⚠️ Unknown command: " .. commandName)
            end
        end
    end
end

-- Main loop
local lastCheck = 0
local connection

connection = RunService.Heartbeat:Connect(function()
    local currentTime = tick()
    if currentTime - lastCheck >= DISCORD_SERVICE_CONFIG.check_interval then
        lastCheck = currentTime
        checkForTriggeredCommands()
    end
end)

-- Cleanup when script is removed
script.AncestryChanged:Connect(function()
    if not script.Parent then
        if connection then
            connection:Disconnect()
        end
    end
end)

print("🚀 Discord Command Handler started!")
print("📡 Listening for commands on: " .. TRIGGERED_COMMANDS_URL)
print("⏱️ Check interval: " .. DISCORD_SERVICE_CONFIG.check_interval .. " seconds")
print("📋 Available commands: kick, ban, give")
