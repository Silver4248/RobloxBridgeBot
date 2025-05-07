import discord
from discord.ext import commands
import random
import string
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

bot = commands.Bot(command_prefix="/", intents=discord.Intents.all())

# Store codes in memory for now; use a database in production
verification_codes = {}

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")

def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

@bot.command()
async def verify(ctx, roblox_username: str):
    code = generate_code()
    verification_codes[ctx.author.id] = {
        "code": code,
        "roblox_username": roblox_username
    }

    await ctx.send(f"To verify, paste this code in your Roblox **profile blurb**: `{code}`.\nThen type `/confirm`.")

@bot.command()
async def confirm(ctx):
    data = verification_codes.get(ctx.author.id)
    if not data:
        await ctx.send("You need to run `/verify <username>` first.")
        return

    roblox_username = data["roblox_username"]
    code = data["code"]

    async with aiohttp.ClientSession() as session:
        # Get user ID from username
        async with session.get(f"https://api.roblox.com/users/get-by-username?username={roblox_username}") as resp:
            if resp.status != 200:
                await ctx.send("Roblox username not found.")
                return
            roblox_data = await resp.json()
            user_id = roblox_data["Id"]

        # Get user profile info (blurb)
        async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
            if resp.status != 200:
                await ctx.send("Could not fetch profile.")
                return
            profile_data = await resp.json()
            blurb = profile_data.get("description", "")

    if code in blurb:
        await ctx.send(f"✅ Successfully verified! Your Roblox account `{roblox_username}` is now linked.")
        # Save to database in production
        # db[ctx.author.id] = user_id
    else:
        await ctx.send("❌ Verification failed. Make sure the code is in your **profile description (blurb)**.")

bot.run(DISCORD_TOKEN)
