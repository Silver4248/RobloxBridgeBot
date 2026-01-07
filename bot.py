import os
import random
import string
import logging
import asyncio
import aiohttp
import discord
from web_service import web_service
from typing import Optional, Dict, List
from discord.ext import commands
from discord import app_commands, ui, Embed, Color
from datetime import datetime, timezone

# Logging setup
logger = logging.getLogger('bot')
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    activity=discord.Activity(type=discord.ActivityType.watching, name="for Roblox commands")
)

def utcnow():
    return datetime.now(timezone.utc)

# Data stores
verification_codes = {}  # {user_id: {code, username, timestamp}}
verified_users = {}      # {user_id: {roblox_id, roblox_username}}
services = {}            # {guild_id: {port, api_key, commands, authorized_users}}

# Utilities
async def get_roblox_id(username: str) -> Optional[int]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username]},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["data"][0]["id"] if data["data"] else None
                return None
    except Exception as e:
        logger.error(f"Failed to get Roblox ID: {str(e)}")
        return None

async def check_profile_blurb(user_id: int, code: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return code in data.get("description", "")
                return False
    except Exception as e:
        logger.error(f"Failed to check profile: {str(e)}")
        return False

async def create_web_service(guild_id: int) -> Optional[Dict]:
    try:
        service_info = await web_service.create_service(guild_id, guild_id, f"service_{guild_id}")
        
        if not service_info:
            raise Exception("Failed to create service")
            
        services[guild_id] = {
            "api_key": service_info["api_key"],
            "port": service_info["port"],
            "url": service_info["url"],
            "commands": [],
            "authorized_users": set(),
            "created_at": utcnow().timestamp()
        }
        
        return service_info
        
    except Exception as e:
        logger.error(f"Service creation failed: {str(e)}")
        return None

async def shutdown_service(guild_id: int) -> bool:
    if guild_id not in services:
        return False
    
    try:
        service_id = f"{guild_id}-{guild_id}-service_{guild_id}"
        await web_service.stop_service(service_id)
        services.pop(guild_id)
        return True
    except Exception as e:
        logger.error(f"Failed to shutdown service: {str(e)}")
        return False

# Verification System
class VerificationView(ui.View):
    def __init__(self, user_id: int, code: str):
        super().__init__(timeout=3600)
        self.user_id = user_id
        self.code = code
        
    @ui.button(label="Verify Now", style=discord.ButtonStyle.green)
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        if self.user_id not in verification_codes:
            return await interaction.followup.send("❌ Verification expired. Please start again.", ephemeral=True)
        
        data = verification_codes[self.user_id]
        roblox_id = await get_roblox_id(data["username"])
        
        if not roblox_id:
            return await interaction.followup.send("❌ Roblox user not found", ephemeral=True)
        
        if await check_profile_blurb(roblox_id, data["code"]):
            verified_users[self.user_id] = {
                "roblox_id": roblox_id,
                "roblox_username": data["username"],
                "verified_at": utcnow().timestamp()
            }
            verification_codes.pop(self.user_id, None)
            
            logger.info(f"User {self.user_id} verified as {data['username']}")
            
            embed = Embed(
                title="✅ Verification Complete!",
                description=f"You're now verified as **{data['username']}**",
                color=Color.green()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            embed = Embed(
                title="❌ Verification Failed",
                description=f"Add this code to your Roblox profile description:\n```\n{self.code}\n```",
                color=Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="verify", description="Verify your Roblox account")
@app_commands.describe(username="Your exact Roblox username")
async def verify(interaction: discord.Interaction, username: str):
    username = username.strip()
    
    if not (3 <= len(username) <= 20):
        return await interaction.response.send_message("❌ Username must be 3-20 characters", ephemeral=True)
    
    if interaction.user.id in verified_users:
        embed = Embed(
            title="Already Verified",
            description=f"You're already verified as **{verified_users[interaction.user.id]['roblox_username']}**",
            color=Color.blue()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    verification_codes[interaction.user.id] = {
        "username": username,
        "code": code,
        "timestamp": utcnow().timestamp()
    }
    
    embed = Embed(
        title="🔐 Verify Your Roblox Account",
        description="Add the code below to your Roblox profile description",
        color=Color.blue()
    )
    embed.add_field(name="Step 1", value="Go to [Roblox Profile](https://www.roblox.com/users/profile)", inline=False)
    embed.add_field(name="Step 2", value=f"Add this to your **Description**:\n```\n{code}\n```", inline=False)
    embed.add_field(name="Step 3", value="Click Verify Now below", inline=False)
    
    view = VerificationView(interaction.user.id, code)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Service Management
@bot.tree.command(name="create_service", description="Create command service (Server Owner only)")
@app_commands.describe(
    name="Service name",
    description="Optional service description"
)
async def create_service(interaction: discord.Interaction, name: str, description: str = None):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("❌ Only the server owner can create services", ephemeral=True)
    
    if interaction.guild.id in services:
        return await interaction.response.send_message("❌ This server already has a service", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    service_info = await create_web_service(interaction.guild.id)
    
    if not service_info:
        return await interaction.followup.send("❌ Failed to create service", ephemeral=True)
    
    embed = Embed(
        title="🚀 Service Created!",
        description=f"Service **{name}** is ready" + (f"\n{description}" if description else ""),
        color=Color.green()
    )
    embed.add_field(name="Port", value=f"`{service_info['port']}`", inline=True)
    embed.add_field(name="API Key", value=f"`{service_info['api_key']}`", inline=False)
    embed.set_footer(text="⚠️ Keep this information secure!")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

# Command Management
class CommandModal(ui.Modal, title="Create Command"):
    def __init__(self):
        super().__init__()
        self.command_name = ui.TextInput(
            label="Command Name",
            placeholder="kick, ban, give, etc.",
            min_length=1,
            max_length=20,
            required=True
        )
        self.add_item(self.command_name)
        
        self.params = ui.TextInput(
            label="Parameters (space separated, max 5)",
            placeholder="player reason",
            required=False,
            max_length=100
        )
        self.add_item(self.params)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild.id not in services:
            return await interaction.response.send_message("❌ No service exists", ephemeral=True)
        
        command_name = self.command_name.value.strip().lower()
        params = self.params.value.strip().split() if self.params.value.strip() else []
        
        if not command_name.isalnum():
            return await interaction.response.send_message("❌ Command name must be alphanumeric", ephemeral=True)
        
        if len(params) > 5:
            return await interaction.response.send_message("❌ Maximum 5 parameters allowed", ephemeral=True)
        
        service = services[interaction.guild.id]
        
        if any(cmd['command_name'] == command_name for cmd in service['commands']):
            return await interaction.response.send_message(f"❌ Command `{command_name}` already exists", ephemeral=True)
        
        if len(service['commands']) >= 10:
            return await interaction.response.send_message("❌ Maximum 10 commands per service", ephemeral=True)
        
        full_command = command_name
        if params:
            full_command += " " + " ".join(params)
        
        service['commands'].append({
            "command_name": command_name,
            "full_command": full_command,
            "params": params,
            "created_at": utcnow().timestamp(),
            "created_by": interaction.user.id,
            "active": True
        })
        
        # Update web service
        service_id = f"{interaction.guild.id}-{interaction.guild.id}-service_{interaction.guild.id}"
        web_service.update_service_commands(service_id, service['commands'])
        
        embed = Embed(
            title="✅ Command Created",
            description=f"Command `!{full_command}` is ready",
            color=Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="create_command", description="Create a new command (Server Owner only)")
async def create_command(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("❌ Only the server owner can create commands", ephemeral=True)
    
    if interaction.guild.id not in services:
        return await interaction.response.send_message("❌ Create a service first with `/create_service`", ephemeral=True)
    
    await interaction.response.send_modal(CommandModal())

# Mod Panel
class ModPanelView(ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @ui.button(label="Add User", style=discord.ButtonStyle.green, row=0)
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Owner only", ephemeral=True)
        
        options = []
        for member in interaction.guild.members:
            if member.id in verified_users and member.id != interaction.guild.owner_id:
                if member.id not in services[self.guild_id]['authorized_users']:
                    options.append(discord.SelectOption(
                        label=member.display_name[:80],
                        value=str(member.id),
                        description=f"Verified as {verified_users[member.id]['roblox_username']}"
                    ))
        
        if not options:
            return await interaction.response.send_message("❌ No verified users to add", ephemeral=True)
        
        select = ui.Select(placeholder="Select user to authorize", options=options[:25])
        
        async def select_callback(select_interaction):
            user_id = int(select_interaction.data["values"][0])
            services[self.guild_id]['authorized_users'].add(user_id)
            
            member = interaction.guild.get_member(user_id)
            embed = Embed(
                title="✅ User Authorized",
                description=f"{member.mention} can now use commands",
                color=Color.green()
            )
            await select_interaction.response.send_message(embed=embed, ephemeral=True)
        
        select.callback = select_callback
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select user:", view=view, ephemeral=True)

    @ui.button(label="Remove User", style=discord.ButtonStyle.red, row=0)
    async def remove_user(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Owner only", ephemeral=True)
        
        authorized = services[self.guild_id]['authorized_users']
        
        if not authorized:
            return await interaction.response.send_message("❌ No authorized users", ephemeral=True)
        
        options = []
        for user_id in authorized:
            member = interaction.guild.get_member(user_id)
            if member:
                options.append(discord.SelectOption(
                    label=member.display_name[:80],
                    value=str(user_id)
                ))
        
        if not options:
            return await interaction.response.send_message("❌ No users to remove", ephemeral=True)
        
        select = ui.Select(placeholder="Select user to remove", options=options[:25])
        
        async def select_callback(select_interaction):
            user_id = int(select_interaction.data["values"][0])
            services[self.guild_id]['authorized_users'].discard(user_id)
            
            member = interaction.guild.get_member(user_id)
            embed = Embed(
                title="✅ User Removed",
                description=f"{member.mention} can no longer use commands",
                color=Color.green()
            )
            await select_interaction.response.send_message(embed=embed, ephemeral=True)
        
        select.callback = select_callback
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select user:", view=view, ephemeral=True)

@bot.tree.command(name="mod_panel", description="Manage command permissions (Server Owner only)")
async def mod_panel(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message("❌ Only the server owner can use this", ephemeral=True)
    
    if interaction.guild.id not in services:
        return await interaction.response.send_message("❌ No service exists", ephemeral=True)
    
    service = services[interaction.guild.id]
    
    embed = Embed(
        title="🛡️ Mod Panel",
        description="Manage who can execute commands",
        color=Color.blue()
    )
    
    authorized = service['authorized_users']
    if authorized:
        user_list = []
        for user_id in authorized:
            member = interaction.guild.get_member(user_id)
            if member:
                user_list.append(f"• {member.mention}")
        
        if user_list:
            embed.add_field(
                name=f"Authorized Users ({len(user_list)})",
                value="\n".join(user_list),
                inline=False
            )
    else:
        embed.add_field(name="Authorized Users", value="None", inline=False)
    
    embed.add_field(
        name="Commands",
        value=f"{len(service['commands'])} commands created",
        inline=False
    )
    
    view = ModPanelView(interaction.guild.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Command Execution
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.content.startswith("!"):
        return
    
    guild_id = message.guild.id
    user_id = message.author.id
    
    if guild_id not in services:
        return
    
    service = services[guild_id]
    
    # Check authorization
    is_owner = user_id == message.guild.owner_id
    is_authorized = user_id in service['authorized_users']
    
    if not (is_owner or is_authorized):
        embed = Embed(
            title="❌ Unauthorized",
            description="You don't have permission to use commands",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    if user_id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="Use `/verify` first",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    parts = message.content[1:].split()
    if not parts:
        return
    
    command_name = parts[0].lower()
    params = parts[1:] if len(parts) > 1 else []
    
    # Find command
    command_found = False
    for cmd in service['commands']:
        if cmd['command_name'] == command_name and cmd.get('active', True):
            command_found = True
            
            # Trigger command
            try:
                command_data = {
                    "command": command_name,
                    "parameters": params,
                    "full_command": message.content[1:],
                    "triggered_by": user_id,
                    "triggered_at": utcnow().timestamp()
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://localhost:{service['port']}/trigger",
                        json=command_data,
                        headers={"Authorization": f"Bearer {service['api_key']}"},
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status == 200:
                            logger.info(f"Command '{command_name}' sent to Roblox")
            except Exception as e:
                logger.error(f"Failed to trigger command: {str(e)}")
            
            embed = Embed(title="⚡ Command Triggered", color=Color.green())
            embed.add_field(name="Command", value=f"`{command_name}`", inline=True)
            embed.add_field(name="Parameters", value=f"`{' '.join(params)}`" if params else "None", inline=True)
            embed.add_field(name="Status", value="✅ Sent to Roblox", inline=False)
            
            await message.channel.send(embed=embed)
            break
    
    if not command_found:
        embed = Embed(
            title="❌ Invalid Command",
            description="Command not found",
            color=Color.red()
        )
        await message.channel.send(embed=embed)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        logger.info(f"✅ Bot ready as {bot.user}")
        logger.info(f"Synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"Failed to sync: {str(e)}")

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        logger.error("No token found")
    else:
        bot.run(TOKEN)