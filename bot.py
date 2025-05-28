import os
import random
import string
import aiohttp
import discord
import logging
from typing import Optional
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from discord.utils import utcnow
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY")

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize bot
intents = discord.Intents.default()
intents.message_content = True  # Needed for command processing
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Data storage
verification_codes = {}
connected_services = {}  # {discord_id: [service_data]}
custom_commands = {}     # {command_name: {response: str, params: list}}

# Helper functions
async def get_roblox_id(username: str) -> Optional[int]:
    """Get Roblox user ID from username"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username]},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data["data"][0]["id"]
    except Exception as e:
        logger.error(f"Error getting Roblox ID: {e}")
        return None

async def check_profile_blurb(user_id: int, code: str) -> bool:
    """Check if verification code exists in profile description"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
            profile = await resp.json()
            return code in profile.get("description", "")

# Verification command (player-only)
@tree.command(name="verify", description="Verify your Roblox account")
async def verify(interaction: discord.Interaction, roblox_username: str):
    """Verify via Roblox profile description"""
    # Input validation
    if not (3 <= len(roblox_username) <= 20):
        return await interaction.response.send_message(
            "❌ Username must be 3-20 characters",
            ephemeral=True
        )

    # Generate 8-digit code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    verification_codes[interaction.user.id] = {
        "code": code,
        "roblox_username": roblox_username,
        "timestamp": utcnow().timestamp()
    }

    # Create verification view
    view = View(timeout=3600)
    verify_button = Button(label="Verify Now", style=discord.ButtonStyle.green)

    async def verify_callback(btn_interaction):
        await btn_interaction.response.defer(ephemeral=True)
        user_data = verification_codes.get(interaction.user.id)
        
        if not user_data:
            return await btn_interaction.followup.send(
                "❌ Verification expired. Please run /verify again",
                ephemeral=True
            )

        try:
            roblox_id = await get_roblox_id(user_data["roblox_username"])
            if not roblox_id:
                return await btn_interaction.followup.send(
                    "❌ User not found. Check the username and try again.",
                    ephemeral=True
                )

            if await check_profile_blurb(roblox_id, user_data["code"]):
                await btn_interaction.followup.send(
                    f"✅ Verified as {user_data['roblox_username']}!",
                    ephemeral=True
                )
            else:
                await btn_interaction.followup.send(
                    f"❌ Code not found in profile. Add `{code}` to your Roblox profile description.",
                    ephemeral=True
                )
        except Exception as e:
            await btn_interaction.followup.send(
                f"❌ Error: {str(e)}",
                ephemeral=True
            )

    verify_button.callback = verify_callback
    view.add_item(verify_button)

    await interaction.response.send_message(
        f"**Verification Steps:**\n"
        f"1. Go to your [Roblox profile](https://www.roblox.com/my/account)\n"
        f"2. Add this code to your **Description**:\n```\n{code}\n```\n"
        f"3. Click Verify Now below\n\n"
        f"⚠️ The code must be visible in your public profile!",
        view=view,
        ephemeral=True
    )

# Create Connection command
@tree.command(name="create_connection", description="Connect to external services")
async def create_connection(interaction: discord.Interaction):
    """Create a connection to external services"""
    # Check if user is verified
    if interaction.user.id not in verification_codes:
        return await interaction.response.send_message(
            "❌ You must verify your Roblox account first using `/verify`",
            ephemeral=True
        )
    
    """Create a connection to external services"""
    # Example implementation for Discord webhook connection
    modal = Modal(title="Create Service Connection")
    
    service_name = TextInput(
        label="Service Name",
        placeholder="e.g., Discord Webhook"
    )
    webhook_url = TextInput(
        label="Webhook URL",
        placeholder="https://discord.com/api/webhooks/..."
    )
    
    modal.add_item(service_name)
    modal.add_item(webhook_url)

    async def modal_callback(modal_interaction: discord.Interaction):
        await modal_interaction.response.defer(ephemeral=True)
        
        # Store connection
        if interaction.user.id not in connected_services:
            connected_services[interaction.user.id] = []
        
        connected_services[interaction.user.id].append({
            "name": service_name.value,
            "url": webhook_url.value,
            "type": "discord_webhook"
        })

        # Example: Send test message
        async with aiohttp.ClientSession() as session:
            try:
                await session.post(
                    webhook_url.value,
                    json={"content": f"Connection established by {interaction.user.name}"}
                )
                await modal_interaction.followup.send(
                    f"✅ Connected to {service_name.value} successfully!",
                    ephemeral=True
                )
            except Exception as e:
                await modal_interaction.followup.send(
                    f"❌ Failed to connect: {str(e)}",
                    ephemeral=True
                )

    modal.on_submit = modal_callback
    await interaction.response.send_modal(modal)

# Create Command command
@tree.command(name="create_command", description="Create a custom !command")
async def create_command(interaction: discord.Interaction):
    """Create a custom prefixed command"""
    modal = Modal(title="Create Custom Command")

    command_name = TextInput(
        label="Command Name (without !)",
        placeholder="e.g., kick",
        max_length=20
    )
    parameters = TextInput(
        label="Parameters (comma-separated)",
        placeholder="e.g., user, reason (max 3)",
        required=False,
        max_length=50
    )
    response = TextInput(
        label="Command Response (use {param} to reference)",
        placeholder="e.g., Kicking {user} for {reason}",
        style=discord.TextStyle.paragraph,
        max_length=400
    )

    modal.add_item(command_name)
    modal.add_item(parameters)
    modal.add_item(response)

    async def modal_callback(modal_interaction: discord.Interaction):
        await modal_interaction.response.defer(ephemeral=True)

        name = command_name.value.strip().lower()
        if name in bot.all_commands:
            return await modal_interaction.followup.send(
                "❌ This command name conflicts with a built-in command.",
                ephemeral=True
            )

        param_list = [
            p.strip() for p in parameters.value.split(",") if p.strip()
        ][:3] if parameters.value else []

        custom_commands[name] = {
            "response": response.value,
            "params": param_list
        }

        await modal_interaction.followup.send(
            f"✅ Custom command `!{name}` created!\n"
            f"Parameters: {', '.join(param_list) if param_list else 'None'}",
            ephemeral=True
        )

    modal.on_submit = modal_callback
    await interaction.response.send_modal(modal)

# Handle custom commands
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Process custom commands first
    if message.content.startswith("!"):
        cmd = message.content[1:].split()[0].lower()
        if cmd in custom_commands:
            try:
                # Delete the command message
                await message.delete()
            except discord.Forbidden:
                logger.warning(f"Missing permissions to delete message in {message.channel.name}")
            
            command_data = custom_commands[cmd]
            params = message.content.split()[1:]

            # Format response with parameters
            response = command_data["response"]
            for i, param in enumerate(command_data["params"]):
                if i < len(params):
                    response = response.replace(f"{{{param}}}", params[i])
                else:
                    response = response.replace(f"{{{param}}}", "undefined")

            # Send to webhook if available
            user_services = connected_services.get(message.author.id, [])
            webhook = next((s for s in user_services if s["type"] == "discord_webhook"), None)

            if webhook:
                try:
                    async with aiohttp.ClientSession() as session:
                        # Send as proper webhook payload
                        payload = {
                            "content": response,
                            "username": f"{message.author.name} (via bot)",
                            "avatar_url": str(message.author.avatar.url) if message.author.avatar else None
                        }
                        await session.post(webhook["url"], json=payload)
                except Exception as e:
                    logger.error(f"Webhook failed: {e}")
                    await message.channel.send("❌ Failed to send to webhook", delete_after=10)
            else:
                reply = await message.channel.send("❌ No webhook connection found. Use `/create_connection` first.")
                await reply.delete(delay=10)

            return  # Prevent further processing of this command

    await bot.process_commands(message)

# Bot events
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ {bot.user} is ready!")

if __name__ == "__main__":
    bot.run(TOKEN)