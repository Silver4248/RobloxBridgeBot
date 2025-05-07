import os
import random
import string
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree  # for slash commands

verification_codes = {}

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Logged in as {bot.user} and synced commands.")

@tree.command(name="verify", description="Verify your Roblox account")
async def verify(interaction: discord.Interaction, roblox_username: str):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    verification_codes[interaction.user.id] = {
        "code": code,
        "roblox_username": roblox_username
    }

    await interaction.response.send_message(
        f"Paste this code in your Roblox **profile blurb**: `{code}`\nThen run `/confirm`."
    )

@tree.command(name="confirm", description="Confirm Roblox account verification")
async def confirm(interaction: discord.Interaction):
    user_data = verification_codes.get(interaction.user.id)
    if not user_data:
        await interaction.response.send_message("You need to run `/verify` first.")
        return

    username = user_data["roblox_username"]
    code = user_data["code"]

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.roblox.com/users/get-by-username?username={username}") as resp:
            if resp.status != 200:
                await interaction.response.send_message("Could not find that Roblox user.")
                return
            data = await resp.json()
            user_id = data.get("Id")

        async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
            if resp.status != 200:
                await interaction.response.send_message("Could not get profile.")
                return
            profile = await resp.json()
            blurb = profile.get("description", "")

    if code in blurb:
        await interaction.response.send_message(f"✅ Verified `{username}` successfully.")
    else:
        await interaction.response.send_message("❌ Verification failed. Check your profile blurb.")

bot.run(TOKEN)