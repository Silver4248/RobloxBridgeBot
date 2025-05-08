import os
import random
import string
import aiohttp
import discord
from discord.ext import commands
from discord.ui import Button, View, Select
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY")  # For private game access

# Initialize bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Data storage
verification_codes = {}  # {discord_id: {code: str, roblox_username: str}}
connected_games = {}      # {discord_id: [game_data]}

# Helper functions
async def get_roblox_id(username: str) -> int:
    """Get Roblox user ID from username"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username]}
        ) as resp:
            data = await resp.json()
            return data["data"][0]["id"]

async def check_profile_blurb(user_id: int, code: str) -> bool:
    """Check if verification code exists in profile description"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp:
            profile = await resp.json()
            return code in profile.get("description", "")

async def get_user_groups(user_id: int) -> list:
    """Get all groups a user belongs to"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://groups.roblox.com/v1/users/{user_id}/groups/roles") as resp:
            data = await resp.json()
            return data.get("data", [])

async def get_group_games(group_id: int) -> list:
    """Get all games (published and private) for a group"""
    async with aiohttp.ClientSession() as session:
        # Get published games
        published = []
        async with session.get(f"https://games.roblox.com/v2/groups/{group_id}/games") as resp:
            if resp.status == 200:
                data = await resp.json()
                published = [{
                    "id": game["id"],
                    "name": game["name"],
                    "published": True
                } for game in data.get("data", [])]

        # Get private games (requires API key)
        private = []
        if ROBLOX_API_KEY:
            headers = {"x-api-key": ROBLOX_API_KEY}
            async with session.get(
                f"https://apis.roblox.com/cloud/v2/groups/{group_id}/games",
                headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    private = [{
                        "id": game["id"],
                        "name": game.get("name", "Unnamed Game"),
                        "published": False
                    } for game in data.get("games", [])]

        return published + private

# Bot events
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ {bot.user} is ready!")

# Commands
@tree.command(name="verify", description="Verify your Roblox account")
async def verify(interaction: discord.Interaction, roblox_username: str):
    """Verify your Roblox account by placing a code in your profile"""
    # Generate a 6-digit verification code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    verification_codes[interaction.user.id] = {
        "code": code,
        "roblox_username": roblox_username
    }

    # Create verification button
    verify_button = Button(
        label="I've placed the code", 
        style=discord.ButtonStyle.green,
        emoji="✅"
    )

    async def verify_callback(button_interaction: discord.Interaction):
        # Ensure it's the same user
        if button_interaction.user.id != interaction.user.id:
            return await button_interaction.response.send_message(
                "❌ This isn't your verification!", 
                ephemeral=True
            )

        # Defer the response
        await button_interaction.response.defer(ephemeral=True)

        # Get stored verification data
        user_data = verification_codes.get(interaction.user.id)
        if not user_data:
            return await button_interaction.followup.send(
                "❌ Verification expired. Please run /verify again",
                ephemeral=True
            )

        try:
            # Get Roblox ID and verify code
            roblox_id = await get_roblox_id(user_data["roblox_username"])
            if await check_profile_blurb(roblox_id, user_data["code"]):
                await button_interaction.followup.send(
                    f"✅ Successfully verified as {user_data['roblox_username']}!",
                    ephemeral=True
                )
            else:
                await button_interaction.followup.send(
                    "❌ Verification failed. Make sure you added the code to your Roblox profile description exactly as shown.",
                    ephemeral=True
                )
        except Exception as e:
            await button_interaction.followup.send(
                f"❌ Error during verification: {str(e)}",
                ephemeral=True
            )

    verify_button.callback = verify_callback

    # Create and send view
    view = View()
    view.add_item(verify_button)
    
    await interaction.response.send_message(
        f"**Verification Steps:**\n"
        f"1. Go to your [Roblox profile](https://www.roblox.com/my/account)\n"
        f"2. Add this code to your **Description**: `{code}`\n"
        f"3. Click the button below when done\n\n"
        f"⚠️ The code must be visible in your profile description!",
        view=view,
        ephemeral=True
    )

@tree.command(name="connect", description="Connect your Roblox games")
async def connect(interaction: discord.Interaction):
    """Connect games from groups you own"""
    # Defer the response immediately
    await interaction.response.defer(ephemeral=True)

    # Check verification first
    user_data = verification_codes.get(interaction.user.id)
    if not user_data:
        return await interaction.followup.send(
            "❌ Please verify your account with /verify first",
            ephemeral=True
        )

    try:
        # Get user's Roblox ID
        roblox_id = await get_roblox_id(user_data["roblox_username"])
        
        # Get all groups the user is in
        groups = await get_user_groups(roblox_id)
        
        # Filter for groups where user is owner (rank 255)
        owned_groups = [g for g in groups if g["role"]["rank"] == 255]
        
        if not owned_groups:
            return await interaction.followup.send(
                "❌ You don't own any Roblox groups",
                ephemeral=True
            )

        # Get all games from owned groups
        all_games = []
        for group in owned_groups:
            group_id = group["group"]["id"]
            games = await get_group_games(group_id)
            for game in games:
                game["group_name"] = group["group"]["name"]
                all_games.append(game)

        if not all_games:
            return await interaction.followup.send(
                "❌ No games found in your groups",
                ephemeral=True
            )

        # Create select menu with games
        select = Select(
            placeholder="Select a game to connect...",
            options=[
                discord.SelectOption(
                    label=f"{game['name']} ({'Published' if game['published'] else 'Private'})",
                    value=f"{game['id']}_{game['group_name']}",
                    description=f"From {game['group_name']}"
                ) for game in all_games[:25]  # Discord limits to 25 options
            ]
        )

        async def select_callback(select_interaction: discord.Interaction):
            # Ensure it's the same user
            if select_interaction.user.id != interaction.user.id:
                return await select_interaction.response.send_message(
                    "❌ This isn't your connection!", 
                    ephemeral=True
                )

            # Defer the response
            await select_interaction.response.defer(ephemeral=True)

            # Get selected game data
            game_id, group_name = select.values[0].split("_", 1)
            selected_game = next(
                g for g in all_games 
                if str(g["id"]) == game_id and g["group_name"] == group_name
            )

            # Store connection
            if interaction.user.id not in connected_games:
                connected_games[interaction.user.id] = []
            
            connected_games[interaction.user.id].append({
                "id": int(game_id),
                "name": selected_game["name"],
                "group": group_name,
                "published": selected_game["published"]
            })

            await select_interaction.followup.send(
                f"✅ Successfully connected to {selected_game['name']} "
                f"({'Published' if selected_game['published'] else 'Private'}) "
                f"from {group_name}!",
                ephemeral=True
            )

        select.callback = select_callback

        # Create and send view
        view = View()
        view.add_item(select)

        await interaction.followup.send(
            f"Found {len(all_games)} games across {len(owned_groups)} groups you own:",
            view=view,
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(
            f"❌ Error connecting games: {str(e)}",
            ephemeral=True
        )

@tree.command(name="mygames", description="View your connected games")
async def mygames(interaction: discord.Interaction):
    """List all connected games"""
    if interaction.user.id not in connected_games or not connected_games[interaction.user.id]:
        return await interaction.response.send_message(
            "❌ You haven't connected any games yet",
            ephemeral=True
        )

    # Create embed with connected games
    embed = discord.Embed(
        title="Your Connected Games",
        color=discord.Color.green()
    )

    for game in connected_games[interaction.user.id]:
        embed.add_field(
            name=f"{game['name']} ({'Published' if game['published'] else 'Private'})",
            value=f"Group: {game['group']}\nGame ID: {game['id']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)