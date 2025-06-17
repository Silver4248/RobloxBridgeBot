import os
import random
import string
import logging
import asyncio
import aiohttp
import discord
from typing import Optional, Dict, List
from discord.ext import commands
from discord import app_commands, ui, Embed, Color
from discord.utils import utcnow
from aiohttp import web
from aiohttp.web import Application
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('discord_bot')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Data stores
verification_codes = {}  # {user_id: {code, username, timestamp}}
verified_users = {}      # {user_id: {roblox_id, roblox_username}}
user_services = {}       # {user_id: {services: {service_name: {port, api_key, runner, commands}}}}

# ----------------- Utilities -----------------
async def get_roblox_id(username: str) -> Optional[int]:
    """Get Roblox user ID from username"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username]}
            ) as response:
                data = await response.json()
                return data["data"][0]["id"] if data["data"] else None
    except Exception as e:
        logger.error(f"Failed to get Roblox ID: {e}")
        return None

async def check_profile_blurb(user_id: int, code: str) -> bool:
    """Check if verification code exists in profile description"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as response:
                data = await response.json()
                return code in data.get("description", "")
    except Exception as e:
        logger.error(f"Failed to check profile: {e}")
        return False

async def cleanup_expired_params():
    """Periodically nullify parameters after 8 seconds"""
    while True:
        try:
            current_time = utcnow().timestamp()
            for user_id, user_data in user_services.items():
                for service_name, service in user_data["services"].items():
                    for cmd in service["commands"]:
                        # If command has parameters and is older than 8 seconds
                        if cmd.get("has_params", False) and current_time - cmd["created_at"] > 8:
                            # Nullify parameters but keep the command
                            parts = cmd["full_command"].split()
                            if len(parts) > 1:
                                cmd["full_command"] = f"!{parts[0].lstrip('!')}"  # Keep only the base command
                                cmd["has_params"] = False
        except Exception as e:
            logger.error(f"Error cleaning up parameters: {e}")
        await asyncio.sleep(5)  # Run every 5 seconds

async def create_web_service(user_id: int, service_name: str) -> Optional[Dict]:
    # Check if port is available
    def is_port_available(port):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) != 0

    port = random.randint(10000, 60000)
    while not is_port_available(port):
        port = random.randint(10000, 60000)

    api_key = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    
    async def handle_request(request):
        """Handle incoming requests from Roblox"""
        try:
            data = await request.json()
            if data.get("api_key") != api_key:
                return web.json_response({"error": "Invalid API key"}, status=403)
            
            if user_id in user_services and service_name in user_services[user_id]["services"]:
                commands = []
                for cmd in user_services[user_id]["services"][service_name]["commands"]:
                    if cmd.get("active", True):
                        commands.append({
                            "command": cmd.get("full_command", ""),
                            "roblox_id": cmd.get("roblox_id", 0),
                            "timestamp": cmd.get("created_at", 0),
                            "has_params": cmd.get("has_params", False)
                        })
                return web.json_response({"commands": commands})
            return web.json_response({"commands": []})
        except Exception as e:
            logger.error(f"Request handling error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    # Setup web server with access logs disabled
    app = web.Application()
    app.router.add_post("/", handle_request)
    
    # Disable access logs
    app._debug = False  # Disable debug mode
    logging.getLogger('aiohttp.access').setLevel(logging.WARNING)  # Or logging.ERROR to suppress completely
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()

    # Store service info
    if user_id not in user_services:
        user_services[user_id] = {"services": {}}
    
    user_services[user_id]["services"][service_name] = {
        "port": port,
        "api_key": api_key,
        "runner": runner,
        "commands": [],
        "created_at": utcnow().timestamp()
    }

    return {
        "port": port,
        "api_key": api_key,
        "url": f"http://localhost:{port}/"
    }
    

async def shutdown_service(user_id: int, service_name: str) -> bool:
    """Shutdown a user's service"""
    if user_id not in user_services or service_name not in user_services[user_id]["services"]:
        return False
    
    try:
        await user_services[user_id]["services"][service_name]["runner"].cleanup()
        user_services[user_id]["services"].pop(service_name)
        
        # Remove user if no services left
        if not user_services[user_id]["services"]:
            user_services.pop(user_id)
        
        return True
    except Exception as e:
        logger.error(f"Failed to shutdown service: {e}")
        return False

# ----------------- Commands -----------------
class VerificationView(ui.View):
    """View for Roblox account verification"""
    def __init__(self, user_id: int, code: str):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.user_id = user_id
        self.code = code
        
    @ui.button(label="Verify Now", style=discord.ButtonStyle.green)
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        # Check if verification still exists
        if self.user_id not in verification_codes:
            return await interaction.followup.send("❌ Verification expired. Please start again.", ephemeral=True)
        
        data = verification_codes[self.user_id]
        
        # Get Roblox ID
        roblox_id = await get_roblox_id(data["username"])
        if not roblox_id:
            return await interaction.followup.send("❌ Roblox user not found", ephemeral=True)
        
        # Check profile description
        if await check_profile_blurb(roblox_id, data["code"]):
            verified_users[self.user_id] = {
                "roblox_id": roblox_id,
                "roblox_username": data["username"],
                "verified_at": utcnow().timestamp()
            }
            verification_codes.pop(self.user_id, None)
            embed = Embed(
                title="✅ Verification Complete!",
                description=f"You're now verified as **{data['username']}** on Roblox",
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
@app_commands.describe(roblox_username="Your Roblox username")
async def verify(interaction: discord.Interaction, roblox_username: str):
    """Verify your Roblox account by adding a code to your profile"""
    if not (3 <= len(roblox_username) <= 20):
        return await interaction.response.send_message("❌ Username must be 3-20 characters", ephemeral=True)
    
    # Generate verification code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    verification_codes[interaction.user.id] = {
        "username": roblox_username,
        "code": code,
        "timestamp": utcnow().timestamp()
    }
    
    # Create verification embed
    embed = Embed(
        title="🔐 Verify Your Roblox Account",
        description="To verify, add the code below to your Roblox profile description and click Verify Now",
        color=Color.blue()
    )
    embed.add_field(name="Step 1", value=f"Go to your [Roblox profile](https://www.roblox.com/my/account)")
    embed.add_field(name="Step 2", value=f"Add this code to your **Description**:\n```\n{code}\n```")
    embed.add_field(name="Step 3", value="Click the Verify Now button below")
    embed.set_footer(text="The code must be visible in your public profile!")
    
    # Send with verification button
    view = VerificationView(interaction.user.id, code)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ServiceCreationModal(ui.Modal, title="Create New Service"):
    """Modal for creating a new service"""
    def __init__(self):
        super().__init__()
        self.service_name = ui.TextInput(
            label="Service Name",
            placeholder="My Awesome Service",
            min_length=3,
            max_length=20
        )
        self.add_item(self.service_name)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        
        # Check if user already has this service name
        if user_id in user_services and self.service_name.value in user_services[user_id]["services"]:
            embed = Embed(
                title="❌ Service Exists",
                description=f"You already have a service named '{self.service_name.value}'",
                color=Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Limit number of services
        if user_id in user_services and len(user_services[user_id]["services"]) >= 3:
            embed = Embed(
                title="❌ Too Many Services",
                description="You can only have up to 3 active services at a time",
                color=Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Create the service
        try:
            service_info = await create_web_service(user_id, self.service_name.value)
            
            # Create success embed
            embed = Embed(
                title="🚀 Service Created!",
                description=f"Your service '{self.service_name.value}' is ready to use!",
                color=Color.green()
            )
            embed.add_field(name="API Endpoint", value=f"`http://localhost:{service_info['port']}/`", inline=False)
            embed.add_field(name="API Key", value=f"`{service_info['api_key']}`", inline=False)
            embed.set_footer(text="⚠️ Save this information - it won't be shown again!")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Service creation error: {e}")
            embed = Embed(
                title="❌ Service Creation Failed",
                description="An error occurred while creating your service. Please try again.",
                color=Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="create_service", description="Create a new command service")
async def create_service(interaction: discord.Interaction):
    """Create a new service for sending commands to Roblox"""
    if interaction.user.id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="You must verify your Roblox account first with `/verify`",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    await interaction.response.send_modal(ServiceCreationModal())

class CommandModal(ui.Modal, title="Create Command"):
    """Modal for creating a new command"""
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name
        self.command = ui.TextInput(label="Command", placeholder="kick", required=True)
        self.param1 = ui.TextInput(label="Parameter 1", required=False)
        self.param2 = ui.TextInput(label="Parameter 2", required=False)
        self.param3 = ui.TextInput(label="Parameter 3", required=False)
        self.add_item(self.command)
        self.add_item(self.param1)
        self.add_item(self.param2)
        self.add_item(self.param3)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        
        # Build command string
        parts = [self.command.value]
        for param in [self.param1.value, self.param2.value, self.param3.value]:
            if param and param.strip():
                parts.append(param.strip())
        
        full_command = f"!{' '.join(parts)}"
        
        # Add to service
        if user_id in user_services and self.service_name in user_services[user_id]["services"]:
            user_services[user_id]["services"][self.service_name]["commands"].append({
                "full_command": full_command,
                "created_at": utcnow().timestamp(),
                "roblox_id": verified_users[user_id]["roblox_id"],
                "active": True,
                "has_params": len(parts) > 1  # Track if command has parameters
            })
            
            embed = Embed(
                title="✅ Command Added",
                description=f"Command `{full_command}` was added to service `{self.service_name}`",
                color=Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            embed = Embed(
                title="❌ Service Not Found",
                description=f"Service '{self.service_name}' doesn't exist or was shut down",
                color=Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

class ServiceSelectView(ui.View):
    """View for selecting a service"""
    def __init__(self, services: List[str]):
        super().__init__()
        self.selected_service = None
        select = ui.Select(
            placeholder="Select a service...",
            options=[discord.SelectOption(label=name) for name in services]
        )
        select.callback = self.on_select
        self.add_item(select)
    
    async def on_select(self, interaction: discord.Interaction):
        self.selected_service = interaction.data["values"][0]
        await interaction.response.defer()
        self.stop()

@bot.tree.command(name="create_command", description="Create a new command for a service")
async def create_command(interaction: discord.Interaction):
    """Create a command to send to Roblox"""
    user_id = interaction.user.id
    
    if user_id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="You must verify your Roblox account first with `/verify`",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    if user_id not in user_services or not user_services[user_id]["services"]:
        embed = Embed(
            title="❌ No Services",
            description="Create a service first with `/create_service`",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    services = list(user_services[user_id]["services"].keys())
    
    # If only one service, skip selection
    if len(services) == 1:
        modal = CommandModal(services[0])
        await interaction.response.send_modal(modal)
    else:
        # Let user select which service to use
        embed = Embed(
            title="Select Service",
            description="Choose which service to add this command to:",
            color=Color.blue()
        )
        view = ServiceSelectView(services)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        await view.wait()
        
        if view.selected_service:
            modal = CommandModal(view.selected_service)
            await interaction.followup.send_modal(modal)
        else:
            embed = Embed(
                title="❌ No Service Selected",
                description="You didn't select a service",
                color=Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

class CommandDeleteView(ui.View):
    """View for managing commands"""
    def __init__(self, user_id: int, service_name: str, commands: List[Dict]):
        super().__init__(timeout=180)  # 3 minute timeout
        self.user_id = user_id
        self.service_name = service_name
        self.commands = commands
        
        # Add toggle buttons for each command
        for i, cmd in enumerate(commands):
            self.add_item(ui.Button(
                label=f"{'🔴' if cmd.get('active', True) else '🟢'} {cmd['full_command']}",
                style=discord.ButtonStyle.red if cmd.get('active', True) else discord.ButtonStyle.green,
                custom_id=f"toggle_{i}",
                row=i//4  # Organize buttons in rows of 4
            ))
        
        # Add delete all button
        self.add_item(ui.Button(
            label="🗑️ Delete ALL Commands",
            style=discord.ButtonStyle.danger,
            row=99
        ))
        
        # Add shutdown service button
        self.add_item(ui.Button(
            label="🔌 Shutdown Service",
            style=discord.ButtonStyle.danger,
            row=99
        ))
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Verify the interacting user is the command owner"""
        return interaction.user.id == self.user_id
    
    @discord.ui.button(label="Close", style=discord.ButtonStyle.grey, row=100)
    async def close_panel(self, interaction: discord.Interaction, button: ui.Button):
        """Close the management panel"""
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()
    
    async def on_timeout(self):
        """Clean up when view times out"""
        try:
            await self.message.delete()
        except:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: ui.Item):
        logger.error(f"Command management error: {error}")
        await interaction.response.send_message("❌ An error occurred", ephemeral=True)

@bot.tree.command(name="get_services", description="View and manage your services")
async def get_services(interaction: discord.Interaction):
    """View and manage your active services"""
    user_id = interaction.user.id
    
    if user_id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="You must verify your Roblox account first with `/verify`",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    if user_id not in user_services or not user_services[user_id]["services"]:
        embed = Embed(
            title="ℹ️ No Services",
            description="You don't have any active services. Create one with `/create_service`",
            color=Color.blue()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Create service selection menu
    embed = Embed(
        title="🛠 Your Services",
        description="Select a service to manage its commands",
        color=Color.blue()
    )
    
    options = []
    for service_name, service_data in user_services[user_id]["services"].items():
        active = sum(1 for cmd in service_data["commands"] if cmd.get("active", True))
        total = len(service_data["commands"])
        
        options.append(discord.SelectOption(
            label=service_name,
            description=f"{active}/{total} commands active • Port {service_data['port']}"
        ))
    
    view = ui.View()
    select = ui.Select(placeholder="Select service...", options=options)
    
    async def service_selected(interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        service = user_services[user_id]["services"][selected]
        
        # Create command management embed
        embed = Embed(
            title=f"📋 Command Management: {selected}",
            description=f"Port: `{service['port']}` • Created <t:{int(service['created_at'])}:R>",
            color=Color.blue()
        )
        
        if not service["commands"]:
            embed.description += "\n\nNo commands stored yet"
            await interaction.response.edit_message(embed=embed, view=None)
            return
        
        active = [cmd for cmd in service["commands"] if cmd.get("active", True)]
        inactive = [cmd for cmd in service["commands"] if not cmd.get("active", False)]
        
        if active:
            embed.add_field(
                name=f"✅ Active Commands ({len(active)})",
                value="\n".join(f"`{cmd['full_command']}`" for cmd in active),
                inline=False
            )
        
        if inactive:
            embed.add_field(
                name=f"❌ Inactive Commands ({len(inactive)})",
                value="\n".join(f"`{cmd['full_command']}`" for cmd in inactive),
                inline=False
            )
        
        # Create management view
        view = CommandDeleteView(user_id, selected, service["commands"])
        view.message = await interaction.response.edit_message(embed=embed, view=view)
    
    select.callback = service_selected
    view.add_item(select)
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.content.startswith("!"):
        return
    
    user_id = message.author.id
    
    # Verification check...
    if user_id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="Verify with `/verify` first",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    if not message.content.startswith("!") or len(message.content.split()) < 2:
        embed = Embed(
            title="❌ Invalid Command",
            description="Commands must start with ! and include an action",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    # Services check...
    if user_id not in user_services or not user_services[user_id]["services"]:
        embed = Embed(
            title="❌ No Services",
            description="Create a service with `/create_service` first",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    # Prepare command data
    command_data = {
        "full_command": message.content,
        "created_at": utcnow().timestamp(),
        "roblox_id": verified_users[user_id]["roblox_id"],
        "active": True,
        "has_params": len(message.content.split()) > 1  # Track if command has parameters
    }
    
    # Try all services
    command_added = False
    for service_name, service in user_services[user_id]["services"].items():
        try:
            service["commands"].append(command_data)
            command_added = True
            
            # Create trigger-style response
            embed = Embed(
                title="⚡ TRIGGER SENT TO SERVER",
                color=Color.green()
            )
            embed.add_field(
                name="Command Executed",
                value=f"```\n{message.content}\n```",
                inline=False
            )
            embed.add_field(
                name="Status",
                value="✅ Successfully triggered Roblox action",
                inline=False
            )
            embed.set_footer(text="Use /get_services to manage commands")
            
            await message.channel.send(embed=embed)
            break
            
        except Exception as e:
            logger.error(f"Failed to add command: {e}")
            continue
    
    if not command_added:
        embed = Embed(
            title="❌ TRIGGER FAILED",
            description="Could not send command to Roblox server",
            color=Color.red()
        )
        await message.channel.send(embed=embed)

# ----------------- Startup -----------------
@bot.event
async def on_ready():
    await bot.tree.sync()
    logger.info(f"✅ Bot is ready as {bot.user}")
    # Start the parameter cleanup task
    bot.loop.create_task(cleanup_expired_params())

if __name__ == "__main__":
    bot.run(TOKEN)