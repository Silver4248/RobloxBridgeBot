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
import sys
import traceback
from aiohttp import web

# Add this import at the top of the file
from datetime import datetime, timezone

# Define the utcnow function at the module level
def utcnow():
    """Return the current UTC datetime with timezone information"""
    return datetime.now(timezone.utc)

# Configure logging
logger = logging.getLogger('bot')
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Enable members intent for guild member operations
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="for Roblox commands"
    )
)

# Data stores - SERVER-SPECIFIC for security
verification_codes = {}  # {user_id: {code, username, timestamp}} - Global (verification is cross-server)
verified_users = {}      # {user_id: {roblox_id, roblox_username}} - Global (verification is cross-server)
user_services = {}       # {guild_id: {user_id: {services: {service_name: {port, api_key, runner, commands}}}}} - SERVER-SPECIFIC
shared_access = {}       # {guild_id: {owner_id: {user_id: {"type": "full/commands", "granted_at": timestamp}}}} - SERVER-SPECIFIC

# ----------------- Utilities -----------------
def has_access(guild_id: int, owner_id: int, user_id: int, required_type: str = None) -> bool:
    """Check if a user has access to another user's services in a specific server"""
    # Always allow access to your own services
    if owner_id == user_id:
        return True

    # Check shared access for this specific server
    if not shared_access:
        return False

    if guild_id not in shared_access:
        return False

    if owner_id not in shared_access[guild_id]:
        return False

    if user_id not in shared_access[guild_id].get(owner_id, {}):
        return False

    # Get the access type
    access_type = shared_access[guild_id].get(owner_id, {}).get(user_id, {}).get("type")

    # If no specific access type is required, any access is sufficient
    if not required_type:
        return True

    # Check if the user has the required access type
    if access_type == required_type:
        return True

    # Full access includes command access
    if required_type == "commands" and access_type == "full":
        return True

    return False

async def get_roblox_id(username: str) -> Optional[int]:
    """Get Roblox user ID from username"""
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
                logger.error(f"Roblox API responded with status {response.status}")
                return None
    except Exception as e:
        logger.error(f"Failed to get Roblox ID: {str(e)}")
        return None

async def check_profile_blurb(user_id: int, code: str) -> bool:
    """Check if verification code exists in profile description"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://users.roblox.com/v1/users/{user_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return code in data.get("description", "")
                logger.error(f"Roblox profile check failed with status {response.status}")
                return False
    except Exception as e:
        logger.error(f"Failed to check profile: {str(e)}")
        return False

async def cleanup_expired_params():
    """Periodically clean up expired services and old verification codes"""
    while True:
        try:
            current_time = utcnow().timestamp()

            # Clean up old verification codes (older than 1 hour)
            expired_codes = []
            for user_id, data in verification_codes.items():
                if current_time - data["timestamp"] > 3600:  # 1 hour
                    expired_codes.append(user_id)

            for user_id in expired_codes:
                verification_codes.pop(user_id, None)
                logger.info(f"Cleaned up expired verification code for user {user_id}")

            # Clean up empty services (only if they have no commands AND are older than 1 hour)
            for guild_id, guild_data in list(user_services.items()):
                for user_id, user_data in list(guild_data.items()):
                    for service_name, service in list(user_data["services"].items()):
                        # Only clean up services that are empty AND old
                        if not service["commands"] and current_time - service["created_at"] > 3600:
                            logger.info(f"Cleaning up empty service '{service_name}' for user {user_id} in guild {guild_id}")
                            await shutdown_service(guild_id, user_id, service_name)

                    # Clean up empty users
                    if not user_data["services"]:
                        guild_data.pop(user_id, None)
                        logger.info(f"Cleaned up empty user data for user {user_id} in guild {guild_id}")

                # Clean up empty guilds
                if not guild_data:
                    user_services.pop(guild_id, None)
                    logger.info(f"Cleaned up empty guild data for guild {guild_id}")

        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}")
        await asyncio.sleep(300)  # Run every 5 minutes instead of every minute

async def create_web_service(guild_id: int, user_id: int, service_name: str) -> Optional[Dict]:
    """Create a new hosted web service"""
    try:
        # Create the service through our WebService class
        service_info = await web_service.create_service(guild_id, user_id, service_name)
        
        if not service_info:
            raise Exception("Failed to create service")
            
        # Store service info (server-specific)
        if guild_id not in user_services:
            user_services[guild_id] = {}

        if user_id not in user_services[guild_id]:
            user_services[guild_id][user_id] = {"services": {}}

        user_services[guild_id][user_id]["services"][service_name] = {
            "api_key": service_info["api_key"],
            "secret_token": service_info["secret_token"],
            "commands": [],
            "created_at": utcnow().timestamp(),
            "url": service_info["url"]
        }

        return service_info
        
    except Exception as e:
        logger.error(f"Service creation failed: {str(e)}")
        return None

async def shutdown_service(guild_id: int, user_id: int, service_name: str) -> bool:
    """Shut down a web service"""
    if (guild_id not in user_services or
        user_id not in user_services[guild_id] or
        service_name not in user_services[guild_id][user_id]["services"]):
        return False

    try:
        # Remove the service from our data structures
        user_services[guild_id][user_id]["services"].pop(service_name)

        # Clean up if user has no more services
        if not user_services[guild_id][user_id]["services"]:
            user_services[guild_id].pop(user_id)

        # Clean up if guild has no more users
        if not user_services[guild_id]:
            user_services.pop(guild_id)

        return True
    except Exception as e:
        logger.error(f"Failed to shutdown service: {str(e)}")
        return False

# ----------------- Verification System -----------------
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
            # Store verification in global dictionary
            global verified_users
            verified_users[self.user_id] = {
                "roblox_id": roblox_id,
                "roblox_username": data["username"],
                "verified_at": utcnow().timestamp()
            }
            verification_codes.pop(self.user_id, None)
            
            logger.info(f"User {self.user_id} verified as Roblox user {roblox_id} ({data['username']})")
            logger.info(f"Current verified_users: {list(verified_users.keys())}")
            
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
@app_commands.describe(roblox_username="Your exact Roblox username")
async def verify(interaction: discord.Interaction, roblox_username: str):
    """Start the account verification process"""
    roblox_username = roblox_username.strip()
    
    if not (3 <= len(roblox_username) <= 20):
        return await interaction.response.send_message(
            "❌ Username must be 3-20 characters",
            ephemeral=True
        )
    
    # Check if already verified
    if interaction.user.id in verified_users:
        embed = Embed(
            title="Already Verified",
            description=f"You're already verified as **{verified_users[interaction.user.id]['roblox_username']}**",
            color=Color.blue()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Generate verification code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    verification_codes[interaction.user.id] = {
        "username": roblox_username,
        "code": code,
        "timestamp": utcnow().timestamp()
    }
    
    logger.info(f"Generated verification code for {interaction.user.id}: {code}")
    
    # Create verification embed
    embed = Embed(
        title="🔐 Verify Your Roblox Account",
        description="To verify, add the code below to your Roblox profile description and click Verify Now",
        color=Color.blue()
    )
    embed.add_field(name="Step 1", value="Go to your [Roblox profile](https://www.roblox.com/users/profile)")
    embed.add_field(name="Step 2", value=f"Add this code to your **Description**:\n```\n{code}\n```")
    embed.add_field(name="Step 3", value="Click the Verify Now button below")
    embed.set_footer(text="The code must be visible in your public profile!")
    
    view = VerificationView(interaction.user.id, code)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ----------------- Service Management -----------------
class ServiceManagementView(ui.View):
    def __init__(self, user_id: int, service_name: str, commands: list):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.service_name = service_name
        self.commands = commands

    @ui.button(label="Manage Commands", style=discord.ButtonStyle.primary, row=0)
    async def manage_commands(self, interaction: discord.Interaction, button: ui.Button):
        """Open command management interface"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)

        # Get updated service data
        if self.user_id in user_services and self.service_name in user_services[self.user_id]["services"]:
            service = user_services[self.user_id]["services"][self.service_name]
            commands = service.get("commands", [])

            # Create command management embed
            embed = Embed(
                title=f"🛠 Command Management - {self.service_name}",
                color=Color.blue()
            )

            if commands:
                active_commands = [c for c in commands if c.get("active", True)]
                inactive_commands = [c for c in commands if not c.get("active", True)]

                if active_commands:
                    active_list = "\n".join(f"`{cmd['full_command']}`" for cmd in active_commands[:10])
                    embed.add_field(
                        name=f"✅ Active Commands ({len(active_commands)})",
                        value=active_list,
                        inline=False
                    )

                if inactive_commands:
                    inactive_list = "\n".join(f"`{cmd['full_command']}`" for cmd in inactive_commands[:10])
                    embed.add_field(
                        name=f"❌ Inactive Commands ({len(inactive_commands)})",
                        value=inactive_list,
                        inline=False
                    )
            else:
                embed.add_field(
                    name="Commands",
                    value="No commands created yet. Use the 'Add Command' button to create commands.",
                    inline=False
                )

            # Create command management view
            command_view = CommandManagementView(self.user_id, self.service_name, commands)
            await interaction.response.send_message(embed=embed, view=command_view, ephemeral=True)
        else:
            embed = Embed(
                title="❌ Service Not Found",
                description=f"Service '{self.service_name}' no longer exists",
                color=Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Service Info", style=discord.ButtonStyle.secondary, row=0)
    async def service_info(self, interaction: discord.Interaction, button: ui.Button):
        """Show detailed service information"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You don't have permission to view this service", ephemeral=True)

        # Get service data
        if self.user_id in user_services and self.service_name in user_services[self.user_id]["services"]:
            service = user_services[self.user_id]["services"][self.service_name]

            embed = Embed(
                title=f"📊 Service Information - {self.service_name}",
                color=Color.blue()
            )
            embed.add_field(
                name="Connection Details",
                value=f"**Port:** `{service['port']}`\n"
                      f"**API Key:** `{service['api_key']}`\n"
                      f"**URL:** `http://localhost:{service['port']}/`",
                inline=False
            )
            embed.add_field(
                name="Service Stats",
                value=f"**Created:** <t:{int(service['created_at'])}:R>\n"
                      f"**Total Commands:** {len(service.get('commands', []))}\n"
                      f"**Active Commands:** {len([c for c in service.get('commands', []) if c.get('active', True)])}",
                inline=False
            )
            embed.set_footer(text="⚠️ Keep your API key secure - don't share it publicly!")

            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = Embed(
                title="❌ Service Not Found",
                description=f"Service '{self.service_name}' no longer exists",
                color=Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Delete Service", style=discord.ButtonStyle.danger, row=1)
    async def delete_service(self, interaction: discord.Interaction, button: ui.Button):
        """Delete the entire service"""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)

        # Confirm deletion
        confirm_view = ui.View(timeout=60)

        @ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
        async def confirm_button(confirm_interaction: discord.Interaction, _):
            try:
                if await shutdown_service(interaction.guild.id, self.user_id, self.service_name):
                    embed = Embed(
                        title="✅ Service Deleted",
                        description=f"Service '{self.service_name}' has been permanently deleted",
                        color=Color.green()
                    )
                    await confirm_interaction.response.send_message(embed=embed, ephemeral=True)

                    # Update the original message
                    original_embed = Embed(
                        title="🗑️ Service Deleted",
                        description=f"Service '{self.service_name}' has been deleted",
                        color=Color.red()
                    )
                    await interaction.edit_original_response(embed=original_embed, view=None)
                else:
                    await confirm_interaction.response.send_message(
                        "❌ Failed to delete service",
                        ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Error in delete_service: {str(e)}")
                await confirm_interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)

        @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel_button(cancel_interaction: discord.Interaction, _):
            await cancel_interaction.response.send_message("Service deletion cancelled", ephemeral=True)

        confirm_view.add_item(confirm_button)
        confirm_view.add_item(cancel_button)

        await interaction.response.send_message(
            f"⚠️ **WARNING:** Are you sure you want to delete service '{self.service_name}'?\n\n"
            f"This will permanently delete:\n"
            f"• All {len(self.commands)} commands\n"
            f"• Service configuration\n"
            f"• API access\n\n"
            f"**This action cannot be undone!**",
            view=confirm_view,
            ephemeral=True
        )

class ServiceCreationModal(ui.Modal, title="Create New Service"):
    def __init__(self):
        super().__init__()
        self.service_name = ui.TextInput(
            label="Service Name",
            placeholder="MyGameCommands",
            min_length=3,
            max_length=20,
            required=True
        )
        self.add_item(self.service_name)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        
        # Check if service exists (server-specific)
        guild_id = interaction.guild.id
        if (guild_id in user_services and
            user_id in user_services[guild_id] and
            self.service_name.value in user_services[guild_id][user_id]["services"]):
            embed = Embed(
                title="❌ Service Exists",
                description=f"You already have a service named '{self.service_name.value}' in this server",
                color=Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # Enforce service limit (server-specific)
        if (guild_id in user_services and
            user_id in user_services[guild_id] and
            len(user_services[guild_id][user_id]["services"]) >= 5):
            embed = Embed(
                title="❌ Too Many Services",
                description="You can only have up to 5 active services at a time",
                color=Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Create the service
        try:
            service_info = await create_web_service(interaction.guild.id, user_id, self.service_name.value)
            
            if not service_info:
                raise Exception("Failed to create service")
            
            embed = Embed(
                title="🚀 Service Created!",
                description=f"Your service '{self.service_name.value}' is ready!",
                color=Color.green()
            )
            embed.add_field(
                name="API Endpoint", 
                value=f"`http://localhost:{service_info['port']}/`", 
                inline=False
            )
            embed.add_field(
                name="API Key", 
                value=f"`{service_info['api_key']}`", 
                inline=False
            )
            embed.set_footer(text="⚠️ Save this information - it won't be shown again!")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Service creation failed: {str(e)}")
            embed = Embed(
                title="❌ Service Creation Failed",
                description="An error occurred while creating your service. Please try again.",
                color=Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="create_service", description="Create a new command service")
async def create_service(interaction: discord.Interaction):
    """Create a new service for Roblox commands"""
    # Check if user is server owner
    is_owner = interaction.guild and interaction.user.id == interaction.guild.owner_id
    
    if not is_owner:
        embed = Embed(
            title="❌ Permission Denied",
            description="Only the server owner can create services",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    if interaction.user.id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="You must verify your Roblox account first with `/verify`",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Limit to 1 service per user per server
    guild_id = interaction.guild.id
    if (guild_id in user_services and
        interaction.user.id in user_services[guild_id] and
        user_services[guild_id][interaction.user.id]["services"]):
        embed = Embed(
            title="❌ Service Limit Reached",
            description="You can only have 1 active service at a time per server",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # Show service creation modal
    await interaction.response.send_modal(ServiceCreationModal())

class CommandModal(ui.Modal, title="Create Command"):
    def __init__(self, service_name, owner_id=None):
        super().__init__()
        self.service_name = service_name
        self.owner_id = owner_id if owner_id is not None else None
        
        self.command_name = ui.TextInput(
            label="Command Name",
            placeholder="e.g., ban, kick, give",
            min_length=1,
            max_length=20,
            required=True
        )
        self.add_item(self.command_name)
        
        self.params = ui.TextInput(
            label="Parameters (optional, space separated)",
            placeholder="e.g., player reason",
            required=False,
            max_length=100
        )
        self.add_item(self.params)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        command_name = self.command_name.value.strip().lower()
        params = self.params.value.strip().split() if self.params.value.strip() else []
        
        # If owner_id is None, the command is for the user's own service
        actual_owner_id = self.owner_id if self.owner_id is not None else user_id
        
        # Validate command name
        if not command_name.isalnum():
            embed = Embed(
                title="❌ Invalid Command Name",
                description="Command name must contain only letters and numbers",
                color=Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Check if user has access to this service
        if actual_owner_id != user_id:  # Only check access if not own service
            if not has_access(interaction.guild.id, actual_owner_id, user_id, "full"):
                embed = Embed(
                    title="❌ Access Denied",
                    description="You don't have permission to manage this service",
                    color=Color.red()
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Check if service exists (server-specific)
        guild_id = interaction.guild.id
        if (guild_id not in user_services or
            actual_owner_id not in user_services[guild_id] or
            self.service_name not in user_services[guild_id][actual_owner_id]["services"]):
            embed = Embed(
                title="❌ Service Not Found",
                description="The selected service doesn't exist in this server",
                color=Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Get existing commands (server-specific)
        existing_commands = user_services[guild_id][actual_owner_id]["services"][self.service_name]["commands"]

        # Debug logging
        logger.info(f"Creating command '{command_name}' for service '{self.service_name}' (owner: {actual_owner_id}, creator: {user_id})")
        logger.info(f"Existing commands: {[cmd.get('command_name') for cmd in existing_commands]}")

        # Check command limit (5 commands max)
        active_commands = [cmd for cmd in existing_commands if cmd.get("active", True)]

        # Check if command already exists (case-insensitive)
        command_exists = False
        existing_command = None
        for cmd in existing_commands:
            if cmd.get("command_name", "").lower() == command_name.lower():
                command_exists = True
                existing_command = cmd
                break

        # Don't allow duplicate command names
        if command_exists:
            embed = Embed(
                title="❌ Command Already Exists",
                description=f"A command named `{command_name}` already exists in this service.\n\n"
                           f"**Existing command:** `!{existing_command['full_command']}`\n"
                           f"**Created by:** <@{existing_command['created_by']}>\n\n"
                           f"Use a different name or delete the existing command first.",
                color=Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Check command limit
        if len(active_commands) >= 5:
            embed = Embed(
                title="❌ Command Limit Reached",
                description="You can only have up to 5 active commands per service",
                color=Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Create full command string
        full_command = command_name
        if params:
            full_command += " " + " ".join(params)
        
        # Add to service (server-specific)
        user_services[guild_id][actual_owner_id]["services"][self.service_name]["commands"].append({
            "full_command": full_command,
            "command_name": command_name,
            "params": params,
            "created_at": utcnow().timestamp(),
            "created_by": user_id,
            "roblox_id": verified_users[user_id]["roblox_id"],
            "active": True,
            "has_params": len(params) > 0
        })

        # Debug logging
        updated_commands = user_services[guild_id][actual_owner_id]["services"][self.service_name]["commands"]
        logger.info(f"Command '{command_name}' added successfully. Total commands now: {len(updated_commands)}")
        logger.info(f"All commands: {[cmd.get('command_name') for cmd in updated_commands]}")
        
        # Success message
        embed = Embed(
            title="✅ Command Created",
            description=f"Command `!{full_command}` has been created for service **{self.service_name}**",
            color=Color.green()
        )
        
        # Add usage instructions
        embed.add_field(
            name="Usage",
            value=f"Type `!{full_command}` in any channel to trigger this command",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ServiceSelectView(ui.View):
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
    
    # Find all services the user can access (server-specific)
    accessible_services = []
    guild_id = interaction.guild.id

    # Own services
    if guild_id in user_services and user_id in user_services[guild_id]:
        for service_name in user_services[guild_id][user_id]["services"]:
            accessible_services.append((user_id, service_name))

    # Services with full access (server-specific)
    if guild_id in shared_access:
        for owner_id, users in shared_access[guild_id].items():
            if user_id in users and users[user_id].get("type") in ["full"]:
                if guild_id in user_services and owner_id in user_services[guild_id]:
                    for service_name in user_services[guild_id][owner_id]["services"]:
                        accessible_services.append((owner_id, service_name))
    
    if not accessible_services:
        embed = Embed(
            title="❌ No Services",
            description="Create a service first with `/create_service` or get full access from a service owner",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    # If user has access to only one service, use that
    if len(accessible_services) == 1:
        owner_id, service_name = accessible_services[0]
        await interaction.response.send_modal(CommandModal(service_name, owner_id))
    # If user has access to multiple services, show a selection
    else:
        # Create select menu for services
        options = []
        for owner_id, service_name in accessible_services:
            owner_name = f"You" if owner_id == user_id else f"User {owner_id}"
            try:
                if owner_id != user_id:
                    owner = await interaction.guild.fetch_member(owner_id)
                    owner_name = owner.display_name
            except:
                pass
            
            options.append(discord.SelectOption(
                label=f"{service_name} ({owner_name})",
                value=f"{owner_id}:{service_name}",
                description=f"Service owned by {owner_name}"
            ))
        
        select = ui.Select(
            placeholder="Select a service to add command to",
            options=options[:25],
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction):
            value = select_interaction.data["values"][0]
            owner_id, service_name = value.split(":", 1)
            await select_interaction.response.send_modal(CommandModal(service_name, int(owner_id)))
        
        select.callback = select_callback
        
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select a service to add a command to:", view=view, ephemeral=True)

class CommandManagementView(ui.View):
    def __init__(self, user_id: int, service_name: str, commands: list):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.service_name = service_name
        self.commands = commands
        
        # Discord UI has a limit of 5 items per row, so we need to be careful with button layout
        # We'll add buttons in a way that respects this limit
    
    @ui.button(label="Add Command", style=discord.ButtonStyle.green, row=0)
    async def add_command(self, interaction: discord.Interaction, button: ui.Button):
        """Add a new command to the service"""
        # Check if user has permission to manage this service
        if interaction.user.id != self.user_id and not has_access(interaction.guild.id, self.user_id, interaction.user.id, "full"):
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)

        # Get current commands from the actual service data (server-specific)
        guild_id = interaction.guild.id
        if (guild_id in user_services and
            self.user_id in user_services[guild_id] and
            self.service_name in user_services[guild_id][self.user_id]["services"]):
            current_commands = user_services[guild_id][self.user_id]["services"][self.service_name]["commands"]
            active_commands = [cmd for cmd in current_commands if cmd.get("active", True)]

            # Check command limit
            if len(active_commands) >= 5:
                return await interaction.response.send_message(
                    "❌ You can only have up to 5 active commands per service",
                    ephemeral=True
                )
        else:
            return await interaction.response.send_message(
                "❌ Service not found",
                ephemeral=True
            )

        # Pass the service owner's ID (self.user_id) as the owner_id to the modal
        await interaction.response.send_modal(CommandModal(self.service_name, self.user_id))
    
    @ui.button(label="Toggle Command", style=discord.ButtonStyle.primary, row=0)
    async def toggle_command(self, interaction: discord.Interaction, button: ui.Button):
        """Toggle a command active/inactive"""
        # Check if user has permission to manage this service
        if interaction.user.id != self.user_id and not has_access(interaction.guild.id, self.user_id, interaction.user.id, "full"):
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)

        # Get current commands from the actual service data (server-specific)
        guild_id = interaction.guild.id
        if (guild_id in user_services and
            self.user_id in user_services[guild_id] and
            self.service_name in user_services[guild_id][self.user_id]["services"]):
            current_commands = user_services[guild_id][self.user_id]["services"][self.service_name]["commands"]
        else:
            return await interaction.response.send_message("Service not found", ephemeral=True)

        if not current_commands:
            return await interaction.response.send_message("You don't have any commands to toggle", ephemeral=True)

        # Create select menu with commands
        options = []
        for i, cmd in enumerate(current_commands):
            status = "✅ Active" if cmd.get("active", True) else "❌ Inactive"
            options.append(discord.SelectOption(
                label=f"{cmd['command_name']}",
                description=f"{status}",
                value=str(i)
            ))
        
        select = ui.Select(
            placeholder="Select a command to toggle",
            options=options,
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            try:
                index = int(select_interaction.data["values"][0])

                # Get current commands from service data (server-specific)
                guild_id = select_interaction.guild.id
                if (guild_id in user_services and
                    self.user_id in user_services[guild_id] and
                    self.service_name in user_services[guild_id][self.user_id]["services"]):
                    service_commands = user_services[guild_id][self.user_id]["services"][self.service_name]["commands"]

                    if index < 0 or index >= len(service_commands):
                        return await select_interaction.response.send_message(
                            "Command selection is no longer valid. Please try again.",
                            ephemeral=True
                        )

                    cmd = service_commands[index]

                    # Toggle active state
                    cmd["active"] = not cmd.get("active", True)

                    # Update service (already updated since cmd is a reference)
                    status = "activated" if cmd["active"] else "deactivated"
                    await select_interaction.response.send_message(
                        f"✅ Command `{cmd['full_command']}` has been {status}",
                        ephemeral=True
                    )
                else:
                    await select_interaction.response.send_message("Service not found", ephemeral=True)
            except Exception as e:
                await select_interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)
        
        select.callback = select_callback
        
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select a command to toggle:", view=view, ephemeral=True)
    
    @ui.button(label="Delete Command", style=discord.ButtonStyle.danger, row=0)
    async def delete_command(self, interaction: discord.Interaction, button: ui.Button):
        """Delete a command from the service"""
        # Check if user has permission to manage this service
        if interaction.user.id != self.user_id and not has_access(interaction.guild.id, self.user_id, interaction.user.id, "full"):
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)

        # Get current commands from the actual service data (server-specific)
        guild_id = interaction.guild.id
        if (guild_id in user_services and
            self.user_id in user_services[guild_id] and
            self.service_name in user_services[guild_id][self.user_id]["services"]):
            current_commands = user_services[guild_id][self.user_id]["services"][self.service_name]["commands"]
        else:
            return await interaction.response.send_message("Service not found", ephemeral=True)

        if not current_commands:
            return await interaction.response.send_message("No commands to delete", ephemeral=True)

        # Create select menu for commands
        options = []
        for i, cmd in enumerate(current_commands):
            status = "✅ " if cmd.get("active", True) else "❌ "
            options.append(discord.SelectOption(
                label=f"{status}{cmd['full_command'][:80]}",
                value=str(i)
            ))
        
        # Split options into chunks of 25 (Discord's limit)
        option_chunks = [options[i:i+25] for i in range(0, len(options), 25)]
        
        if not option_chunks:
            return await interaction.response.send_message("No commands to delete", ephemeral=True)
        
        # Create select menu with first 25 options
        select = ui.Select(
            placeholder="Select command to delete",
            options=option_chunks[0] if option_chunks else []
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            try:
                index = int(select_interaction.data["values"][0])

                # Get current commands from service data (server-specific)
                guild_id = select_interaction.guild.id
                if (guild_id in user_services and
                    self.user_id in user_services[guild_id] and
                    self.service_name in user_services[guild_id][self.user_id]["services"]):
                    service_commands = user_services[guild_id][self.user_id]["services"][self.service_name]["commands"]

                    # Verify the index is still valid
                    if index < 0 or index >= len(service_commands):
                        return await select_interaction.response.send_message(
                            "Command selection is no longer valid. Please try again.",
                            ephemeral=True
                        )

                    # Remove command safely
                    deleted_cmd = service_commands.pop(index)

                    await select_interaction.response.send_message(
                        f"✅ Command `{deleted_cmd['full_command']}` has been deleted",
                        ephemeral=True
                    )
                else:
                    await select_interaction.response.send_message(
                        "Service not found",
                        ephemeral=True
                    )
            except ValueError:
                await select_interaction.response.send_message("Invalid selection", ephemeral=True)
            except IndexError:
                await select_interaction.response.send_message(
                    "Command index is out of range. The command list may have changed.",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error in delete_command: {str(e)}")
                await select_interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)
        
        select.callback = select_callback
        
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select a command to delete:", view=view, ephemeral=True)
    
    @ui.button(label="Delete Service", style=discord.ButtonStyle.danger, row=1)
    async def delete_service(self, interaction: discord.Interaction, button: ui.Button):
        """Delete the entire service"""
        # Check if user has permission to manage this service
        if interaction.user.id != self.user_id and not has_access(interaction.guild.id, self.user_id, interaction.user.id, "full"):
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)
        
        # Confirm deletion
        confirm_view = ui.View(timeout=60)
        
        @ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
        async def confirm_button(confirm_interaction: discord.Interaction, _):
            try:
                if await shutdown_service(confirm_interaction.guild.id, self.user_id, self.service_name):
                    embed = Embed(
                        title="✅ Service Deleted",
                        description=f"Service '{self.service_name}' has been deleted",
                        color=Color.green()
                    )
                    await confirm_interaction.response.send_message(embed=embed, ephemeral=True)
                    
                    # Update the original message
                    original_embed = Embed(
                        title="🛠 Service Management",
                        description=f"Service '{self.service_name}' has been deleted",
                        color=Color.blue()
                    )
                    await interaction.edit_original_response(embed=original_embed, view=None)
                else:
                    await confirm_interaction.response.send_message(
                        "❌ Failed to delete service",
                        ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Error in delete_service: {str(e)}")
                await confirm_interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)
        
        @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel_button(cancel_interaction: discord.Interaction, _):
            await cancel_interaction.response.send_message("Service deletion cancelled", ephemeral=True)
        
        confirm_view.add_item(confirm_button)
        confirm_view.add_item(cancel_button)
        
        await interaction.response.send_message(
            f"⚠️ Are you sure you want to delete service '{self.service_name}'? This cannot be undone.",
            view=confirm_view,
            ephemeral=True
        )
    
    @ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: ui.Button):
        """Refresh the service details"""
        # Check if user has permission to manage this service
        if interaction.user.id != self.user_id and not has_access(interaction.guild.id, self.user_id, interaction.user.id, "full"):
            return await interaction.response.send_message("You don't have permission to manage this service", ephemeral=True)
        
        try:
            # Get updated service data
            if self.user_id in user_services and self.service_name in user_services[self.user_id]["services"]:
                service = user_services[self.user_id]["services"][self.service_name]
                self.commands = service["commands"]
                
                # Create service details embed
                embed = Embed(
                    title=f"Service: {self.service_name}",
                    color=Color.blue()
                )
                embed.add_field(
                    name="Connection Info",
                    value=f"**Port:** `{service['port']}`\n"
                          f"**API Key:** `{service['api_key']}`\n"
                          f"**Created:** <t:{int(service['created_at'])}:R>",
                    inline=False
                )
                
                # Add commands info
                if service["commands"]:
                    active_commands = [c for c in service["commands"] if c.get("active", True)]
                    inactive_commands = [c for c in service["commands"] if not c.get("active", True)]
                    
                    if active_commands:
                        active_list = "\n".join(f"`{cmd['full_command']}`" for cmd in active_commands[:10])
                        embed.add_field(
                            name=f"✅ Active Commands ({len(active_commands)})",
                            value=active_list,
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name="✅ Active Commands",
                            value="No active commands",
                            inline=False
                        )
                    
                    if inactive_commands:
                        inactive_list = "\n".join(f"`{cmd['full_command']}`" for cmd in inactive_commands[:10])
                        embed.add_field(
                            name=f"❌ Inactive Commands ({len(inactive_commands)})",
                            value=inactive_list,
                            inline=False
                        )
                    
                    if len(active_commands) + len(inactive_commands) > 20:
                        embed.set_footer(text="Showing first 20 commands in management view")
                else:
                    embed.add_field(
                        name="Commands",
                        value="No commands created yet. Use `/create_command` to add commands.",
                        inline=False
                    )
                
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                embed = Embed(
                    title="❌ Service Not Found",
                    description=f"Service '{self.service_name}' no longer exists",
                    color=Color.red()
                )
                await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            logger.error(f"Error in refresh: {str(e)}")
            await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)

@bot.tree.command(name="get_services", description="View and manage your services")
async def get_services(interaction: discord.Interaction):
    """View and manage your services"""
    user_id = interaction.user.id

    if user_id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="You must verify your Roblox account first with `/verify`",
            color=Color.red()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    # Find all services the user can access with full permissions (server-specific)
    accessible_services = []
    guild_id = interaction.guild.id

    # Own services
    if guild_id in user_services and user_id in user_services[guild_id] and user_services[guild_id][user_id]["services"]:
        for service_name in user_services[guild_id][user_id]["services"]:
            accessible_services.append((user_id, service_name))

    # Services with full access (server-specific)
    if guild_id in shared_access:
        for owner_id, users in shared_access[guild_id].items():
            if user_id in users and users[user_id].get("type") == "full":
                if guild_id in user_services and owner_id in user_services[guild_id]:
                    for service_name in user_services[guild_id][owner_id]["services"]:
                        accessible_services.append((owner_id, service_name))

    if not accessible_services:
        embed = Embed(
            title="No Accessible Services",
            description="You don't have any services or full access to any services yet.\n\n"
                       "• Create your own service with `/create_service`\n"
                       "• Ask a service owner to grant you full access with `/manage_perms`",
            color=Color.blue()
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    # If user has access to only one service, show it directly
    if len(accessible_services) == 1:
        owner_id, service_name = accessible_services[0]
        service = user_services[guild_id][owner_id]["services"][service_name]

        # Create embed
        embed = Embed(
            title=f"Service: {service_name}",
            color=Color.blue()
        )

        # Add ownership info
        if owner_id == user_id:
            embed.description = f"**Your Service**\nAPI Key: `{service['api_key']}`"
        else:
            try:
                owner = await interaction.guild.fetch_member(owner_id)
                owner_name = owner.display_name
            except:
                owner_name = f"User {owner_id}"
            embed.description = f"**Owned by {owner_name}** (You have full access)\nAPI Key: `{service['api_key']}`"

        # Add commands
        commands = service.get("commands", [])
        active_commands = [cmd for cmd in commands if cmd.get("active", True)]

        if active_commands:
            command_list = []
            for i, cmd in enumerate(active_commands):
                command_list.append(f"{i+1}. `!{cmd['full_command']}`")

            embed.add_field(
                name=f"Commands ({len(active_commands)}/5)",
                value="\n".join(command_list),
                inline=False
            )
        else:
            embed.add_field(
                name="Commands (0/5)",
                value="No commands yet. Create one with `/create_command`",
                inline=False
            )

        # Create view with buttons (pass owner_id so permissions work correctly)
        view = ServiceManagementView(owner_id, service_name, commands)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # If user has access to multiple services, show a selection
    else:
        embed = Embed(
            title="🛠 Your Accessible Services",
            description="Select a service to view and manage:",
            color=Color.blue()
        )

        # Create select menu for services
        options = []
        for owner_id, service_name in accessible_services:
            if owner_id == user_id:
                owner_name = "You"
                description = "Your service"
            else:
                try:
                    owner = await interaction.guild.fetch_member(owner_id)
                    owner_name = owner.display_name
                except:
                    owner_name = f"User {owner_id}"
                description = f"Full access to {owner_name}'s service"

            options.append(discord.SelectOption(
                label=f"{service_name} ({owner_name})",
                value=f"{owner_id}:{service_name}",
                description=description
            ))

        select = ui.Select(
            placeholder="Select a service to manage",
            options=options[:25],
            min_values=1,
            max_values=1
        )

        async def select_callback(select_interaction):
            value = select_interaction.data["values"][0]
            owner_id, service_name = value.split(":", 1)
            owner_id = int(owner_id)

            service = user_services[guild_id][owner_id]["services"][service_name]

            # Create embed for selected service
            embed = Embed(
                title=f"Service: {service_name}",
                color=Color.blue()
            )

            # Add ownership info
            if owner_id == user_id:
                embed.description = f"**Your Service**\nAPI Key: `{service['api_key']}`"
            else:
                try:
                    owner = await interaction.guild.fetch_member(owner_id)
                    owner_name = owner.display_name
                except:
                    owner_name = f"User {owner_id}"
                embed.description = f"**Owned by {owner_name}** (You have full access)\nAPI Key: `{service['api_key']}`"

            # Add commands
            commands = service.get("commands", [])
            active_commands = [cmd for cmd in commands if cmd.get("active", True)]

            if active_commands:
                command_list = []
                for i, cmd in enumerate(active_commands):
                    command_list.append(f"{i+1}. `!{cmd['full_command']}`")

                embed.add_field(
                    name=f"Commands ({len(active_commands)}/5)",
                    value="\n".join(command_list),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Commands (0/5)",
                    value="No commands yet. Create one with `/create_command`",
                    inline=False
                )

            # Create view with buttons
            view = ServiceManagementView(owner_id, service_name, commands)

            await select_interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        select.callback = select_callback

        view = ui.View(timeout=60)
        view.add_item(select)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ----------------- Command Handling -----------------
@bot.event
async def on_message(message: discord.Message):
    """Handle command messages starting with !"""
    # Ignore bot messages and non-command messages
    if message.author.bot or not message.content.startswith("!"):
        return
    
    user_id = message.author.id
    
    # Check if user is verified
    if user_id not in verified_users:
        embed = Embed(
            title="❌ Not Verified",
            description="You must verify your Roblox account first with `/verify`",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    # Parse command
    parts = message.content[1:].split()
    if not parts:
        return
    
    command_name = parts[0].lower()
    params = parts[1:] if len(parts) > 1 else []
    
    # Find all services the user can access (server-specific)
    accessible_services = {}
    guild_id = message.guild.id

    # Own services
    if guild_id in user_services and user_id in user_services[guild_id]:
        for service_name, service in user_services[guild_id][user_id]["services"].items():
            accessible_services[f"{user_id}:{service_name}"] = {
                "owner_id": user_id,
                "service_name": service_name,
                "service": service
            }

    # Shared services - both full and command access can use commands (server-specific)
    if guild_id in shared_access:
        for owner_id, users in shared_access[guild_id].items():
            if user_id in users:
                # User has some form of access to this owner's services
                if guild_id in user_services and owner_id in user_services[guild_id]:
                    for service_name, service in user_services[guild_id][owner_id]["services"].items():
                        accessible_services[f"{owner_id}:{service_name}"] = {
                            "owner_id": owner_id,
                            "service_name": service_name,
                            "service": service
                        }
    
    if not accessible_services:
        embed = Embed(
            title="❌ No Accessible Services",
            description="You don't have access to any services. Create one with `/create_service` or get access from a service owner.",
            color=Color.red()
        )
        return await message.channel.send(embed=embed)
    
    # Check if command exists in any service
    command_found = False
    for service_key, service_info in accessible_services.items():
        service = service_info["service"]
        owner_id = service_info["owner_id"]
        service_name = service_info["service_name"]
        
        for cmd in service["commands"]:
            if cmd.get("command_name") == command_name and cmd.get("active", True):
                # Command found, execute it
                command_found = True

                # Get the service info to find the port and API key
                service_port = service.get("port")
                service_api_key = service.get("api_key")

                if service_port and service_api_key:
                    # Trigger the command endpoint with a 10-second window
                    try:
                        # Send command data to the service
                        command_data = {
                            "command": command_name,
                            "parameters": params,
                            "full_command": message.content[1:],  # Remove the ! prefix
                            "triggered_by": message.author.id,
                            "triggered_at": utcnow().timestamp()
                        }

                        # Make HTTP request to the service
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            try:
                                async with session.post(
                                    f"http://localhost:{service_port}/trigger",
                                    json=command_data,
                                    headers={"Authorization": f"Bearer {service_api_key}"},
                                    timeout=aiohttp.ClientTimeout(total=5)
                                ) as response:
                                    if response.status == 200:
                                        logger.info(f"Command '{command_name}' successfully sent to service on port {service_port}")
                                    else:
                                        logger.warning(f"Service responded with status {response.status}")
                            except asyncio.TimeoutError:
                                logger.warning(f"Timeout when sending command to service on port {service_port}")
                            except Exception as e:
                                logger.error(f"Error sending command to service: {str(e)}")
                    except Exception as e:
                        logger.error(f"Failed to trigger command: {str(e)}")

                # Create success response
                embed = Embed(
                    title="⚡ COMMAND TRIGGERED",
                    color=Color.green()
                )
                embed.add_field(
                    name="Command",
                    value=f"`{command_name}`",
                    inline=True
                )
                embed.add_field(
                    name="Parameters",
                    value=f"`{' '.join(params)}`" if params else "None",
                    inline=True
                )
                embed.add_field(
                    name="Service",
                    value=f"{service_name}",
                    inline=True
                )
                embed.add_field(
                    name="Full Command",
                    value=f"```\n{message.content}\n```",
                    inline=False
                )
                embed.add_field(
                    name="Status",
                    value="✅ Command sent to Roblox service",
                    inline=False
                )
                embed.set_footer(text="The command data is available for 10 seconds")

                await message.channel.send(embed=embed)
                break
        
        if command_found:
            break
    
    if not command_found:
        embed = Embed(
            title="❌ INVALID COMMAND",
            description="This command doesn't exist in any of your services. Create it first with `/create_command`",
            color=Color.red()
        )
        await message.channel.send(embed=embed)

# ----------------- Access Management -----------------
class PermissionManagementView(ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id

    @ui.button(label="Grant Full Access", style=discord.ButtonStyle.primary, row=0)
    async def grant_full_access(self, interaction: discord.Interaction, button: ui.Button):
        """Button to grant full access to a user"""
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
        
        # Try to fetch all guild members to ensure we have the latest data
        try:
            await interaction.guild.chunk()
        except Exception as e:
            logger.warning(f"Could not fetch all guild members: {e}. Proceeding with cached member data.")
        
        # Get all verified users
        options = []
        
        # Create options for all verified users except self
        for user_id in verified_users:
            if user_id != self.owner_id:
                # Try to get member from guild
                try:
                    # Try to fetch the member directly
                    member = await interaction.guild.fetch_member(int(user_id))
                    
                    # Check if already has access (server-specific)
                    has_existing = False
                    access_type = None
                    guild_id = interaction.guild.id
                    if (guild_id in shared_access and
                        self.owner_id in shared_access[guild_id] and
                        user_id in shared_access[guild_id][self.owner_id]):
                        has_existing = True
                        access_type = shared_access[guild_id][self.owner_id][user_id].get("type", "unknown")
                    
                    options.append(discord.SelectOption(
                        label=f"{member.display_name[:80]}",
                        value=str(user_id),
                        description=f"{'Has ' + access_type + ' access' if has_existing else 'Verified user'}"
                    ))
                except discord.errors.NotFound:
                    continue
                except Exception as e:
                    print(f"Error fetching member {user_id}: {e}")
                    # Fall back to using user ID as label
                    options.append(discord.SelectOption(
                        label=f"User {user_id}",
                        value=str(user_id),
                        description="Verified user"
                    ))
        
        if not options:
            return await interaction.response.send_message("No verified users available to grant access to. Users need to use `/verify` first and be in this server.", ephemeral=True)
        
        # Create select menu with first 25 options (Discord limit)
        select = ui.Select(
            placeholder="Select user to grant full access",
            options=options[:25],
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction):
            user_id = int(select_interaction.data["values"][0])
            
            try:
                # Try to fetch the member directly
                member = await interaction.guild.fetch_member(int(user_id))
            except discord.errors.NotFound:
                return await select_interaction.response.send_message("User not found in this server.", ephemeral=True)
            except Exception as e:
                print(f"Error fetching member for access grant: {e}")
                # Fall back to using user ID
                member = None
            
            # Initialize shared_access if needed (server-specific)
            global shared_access
            if shared_access is None:
                shared_access = {}

            guild_id = interaction.guild.id
            if guild_id not in shared_access:
                shared_access[guild_id] = {}

            if self.owner_id not in shared_access[guild_id]:
                shared_access[guild_id][self.owner_id] = {}

            # Grant access
            shared_access[guild_id][self.owner_id][user_id] = {
                "type": "full",
                "granted_at": utcnow().timestamp()
            }

            # Debug logging
            logger.info(f"Granted full access: owner={self.owner_id}, user={user_id}")
            logger.info(f"Current shared_access: {shared_access}")

            # Create mention or fallback
            user_mention = member.mention if member else f"<@{user_id}>"

            embed = Embed(
                title="✅ Access Granted",
                description=f"Full access granted to {user_mention}",
                color=Color.green()
            )
            await select_interaction.response.send_message(embed=embed, ephemeral=True)
        
        select.callback = select_callback
        
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select a user to grant full access:", view=view, ephemeral=True)

    @ui.button(label="Grant Command Access", style=discord.ButtonStyle.secondary, row=0)
    async def grant_command_access(self, interaction: discord.Interaction, button: ui.Button):
        """Button to grant command-only access to a user"""
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
        
        # Try to fetch all guild members to ensure we have the latest data
        try:
            await interaction.guild.chunk()
        except Exception as e:
            logger.warning(f"Could not fetch all guild members: {e}. Proceeding with cached member data.")
        
        # Get all verified users
        options = []
        
        # Create options for all verified users except self
        for user_id in verified_users:
            if user_id != self.owner_id:
                # Try to get member from guild
                try:
                    # Try to fetch the member directly
                    member = await interaction.guild.fetch_member(int(user_id))
                    
                    # Check if already has access (server-specific)
                    has_existing = False
                    access_type = None
                    guild_id = interaction.guild.id
                    if (guild_id in shared_access and
                        self.owner_id in shared_access[guild_id] and
                        user_id in shared_access[guild_id][self.owner_id]):
                        has_existing = True
                        access_type = shared_access[guild_id][self.owner_id][user_id].get("type", "unknown")
                    
                    options.append(discord.SelectOption(
                        label=f"{member.display_name[:80]}",
                        value=str(user_id),
                        description=f"{'Has ' + access_type + ' access' if has_existing else 'Verified user'}"
                    ))
                except discord.errors.NotFound:
                    continue
                except Exception as e:
                    print(f"Error fetching member {user_id}: {e}")
                    # Fall back to using user ID as label
                    options.append(discord.SelectOption(
                        label=f"User {user_id}",
                        value=str(user_id),
                        description="Verified user"
                    ))
        
        if not options:
            return await interaction.response.send_message("No verified users available to grant access to. Users need to use `/verify` first and be in this server.", ephemeral=True)
        
        # Create select menu with first 25 options (Discord limit)
        select = ui.Select(
            placeholder="Select user to grant command access",
            options=options[:25],
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction):
            user_id = int(select_interaction.data["values"][0])
            
            try:
                # Try to fetch the member directly
                member = await interaction.guild.fetch_member(int(user_id))
            except discord.errors.NotFound:
                return await select_interaction.response.send_message("User not found in this server.", ephemeral=True)
            except Exception as e:
                print(f"Error fetching member for access grant: {e}")
                # Fall back to using user ID
                member = None
            
            # Initialize shared_access if needed (server-specific)
            global shared_access
            if shared_access is None:
                shared_access = {}

            guild_id = interaction.guild.id
            if guild_id not in shared_access:
                shared_access[guild_id] = {}

            if self.owner_id not in shared_access[guild_id]:
                shared_access[guild_id][self.owner_id] = {}

            # Grant access
            shared_access[guild_id][self.owner_id][user_id] = {
                "type": "commands",
                "granted_at": utcnow().timestamp()
            }

            # Debug logging
            logger.info(f"Granted command access: owner={self.owner_id}, user={user_id}")
            logger.info(f"Current shared_access: {shared_access}")

            # Create mention or fallback
            user_mention = member.mention if member else f"<@{user_id}>"

            embed = Embed(
                title="✅ Access Granted",
                description=f"Command access granted to {user_mention}",
                color=Color.green()
            )
            await select_interaction.response.send_message(embed=embed, ephemeral=True)
        
        select.callback = select_callback
        
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select a user to grant command access:", view=view, ephemeral=True)

    @ui.button(label="Revoke Access", style=discord.ButtonStyle.danger, row=1)
    async def revoke_access(self, interaction: discord.Interaction, button: ui.Button):
        """Button to revoke access from a user"""
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
        
        # Check if there are any users with access (server-specific)
        guild_id = interaction.guild.id
        if (guild_id not in shared_access or
            self.owner_id not in shared_access[guild_id] or
            not shared_access[guild_id][self.owner_id]):
            return await interaction.response.send_message("You haven't granted access to anyone yet in this server.", ephemeral=True)
        
        # Try to fetch all guild members to ensure we have the latest data
        try:
            await interaction.guild.chunk()
        except Exception as e:
            logger.warning(f"Could not fetch all guild members: {e}. Proceeding with cached member data.")
        
        # Create select menu options (server-specific)
        options = []
        for user_id, access_data in shared_access[guild_id][self.owner_id].items():
            try:
                # Try to fetch the member directly
                member = await interaction.guild.fetch_member(int(user_id))
                access_type = access_data.get("type", "unknown")
                
                options.append(discord.SelectOption(
                    label=f"{member.display_name[:80]}",
                    value=str(user_id),
                    description=f"{access_type.capitalize()} access"
                ))
            except discord.errors.NotFound:
                # Include users not in the guild with a special label
                options.append(discord.SelectOption(
                    label=f"User {user_id} (not in server)",
                    value=str(user_id),
                    description=f"{access_data.get('type', 'unknown').capitalize()} access"
                ))
            except Exception as e:
                print(f"Error fetching member {user_id} for revoke: {e}")
                # Fall back to using user ID as label
                options.append(discord.SelectOption(
                    label=f"User {user_id}",
                    value=str(user_id),
                    description=f"{access_data.get('type', 'unknown').capitalize()} access"
                ))
        
        if not options:
            return await interaction.response.send_message("No users to revoke access from.", ephemeral=True)
        
        # Create select menu
        select = ui.Select(
            placeholder="Select user to revoke access",
            options=options[:25],  # Discord limit
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction):
            user_id = int(select_interaction.data["values"][0])
            
            try:
                # Try to fetch the member directly
                member = await interaction.guild.fetch_member(int(user_id))
                user_mention = member.mention
            except Exception:
                # Fall back to using user ID as mention
                user_mention = f"<@{user_id}>"
            
            # Revoke access (server-specific)
            guild_id = select_interaction.guild.id
            if (guild_id in shared_access and
                self.owner_id in shared_access[guild_id] and
                user_id in shared_access[guild_id][self.owner_id]):

                access_type = shared_access[guild_id][self.owner_id][user_id].get("type", "unknown")
                del shared_access[guild_id][self.owner_id][user_id]

                # Debug logging
                logger.info(f"Revoked {access_type} access: guild={guild_id}, owner={self.owner_id}, user={user_id}")

                # Clean up if no more shared access
                if not shared_access[guild_id][self.owner_id]:
                    del shared_access[guild_id][self.owner_id]
                    logger.info(f"Cleaned up empty shared_access for owner {self.owner_id} in guild {guild_id}")

                # Clean up if no more owners in guild
                if not shared_access[guild_id]:
                    del shared_access[guild_id]
                    logger.info(f"Cleaned up empty shared_access for guild {guild_id}")

                logger.info(f"Current shared_access after revoke: {shared_access}")

                embed = Embed(
                    title="✅ Access Revoked",
                    description=f"Access revoked from {user_mention}",
                    color=Color.green()
                )
                await select_interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await select_interaction.response.send_message("User not found in access list.", ephemeral=True)
        
        select.callback = select_callback
        
        view = ui.View(timeout=60)
        view.add_item(select)
        
        await interaction.response.send_message("Select a user to revoke access:", view=view, ephemeral=True)

@bot.tree.command(name="manage_perms", description="View and manage access permissions")
async def manage_perms(interaction: discord.Interaction):
    """Manage access permissions for your services"""
    await interaction.response.defer(ephemeral=True)

    if interaction.user.id not in verified_users:
        return await interaction.followup.send(
            embed=Embed(
                title="❌ Not Verified",
                description="You must verify your account first!",
                color=Color.red()
            ),
            ephemeral=True
        )

    # Check if user owns any services (server-specific)
    guild_id = interaction.guild.id
    if (guild_id not in user_services or
        interaction.user.id not in user_services[guild_id] or
        not user_services[guild_id][interaction.user.id]["services"]):
        return await interaction.followup.send(
            embed=Embed(
                title="❌ No Services",
                description="You must own a service to manage permissions. Create one with `/create_service` first.",
                color=Color.red()
            ),
            ephemeral=True
        )
    
    embed = Embed(
        title="🔐 Access Permissions",
        color=Color.blue()
    )
    
    # Show users who have access to your services (server-specific)
    if (guild_id in shared_access and
        interaction.user.id in shared_access[guild_id] and
        shared_access[guild_id][interaction.user.id]):
        users_with_access = []
        for user_id, access_data in shared_access[guild_id][interaction.user.id].items():
            member = interaction.guild.get_member(int(user_id))
            if member:
                access_type = access_data.get("type", "unknown")
                granted_at = access_data.get("granted_at", 0)
                users_with_access.append(
                    f"{member.mention} - **{access_type}** access (granted <t:{int(granted_at)}:R>)"
                )
        
        embed.description = "You have granted access to the following users:"
        embed.add_field(
            name="Users With Access",
            value="\n".join(users_with_access) if users_with_access else "None in this server",
            inline=False
        )
    else:
        embed.description = "You haven't granted access to anyone yet."
    
    # Show who has granted you access (server-specific)
    users_granted_you = []
    if guild_id in shared_access:
        for owner_id, users in shared_access[guild_id].items():
            if interaction.user.id in users:
                owner = interaction.guild.get_member(int(owner_id))
                if owner:
                    access_type = users[interaction.user.id].get("type", "unknown")
                    granted_at = users[interaction.user.id].get("granted_at", 0)
                    users_granted_you.append(
                        f"{owner.mention} - **{access_type}** access (granted <t:{int(granted_at)}:R>)"
                    )
    
    if users_granted_you:
        embed.add_field(
            name="Access Granted To You By:",
            value="\n".join(users_granted_you),
            inline=False
        )
    
    # Add explanation of access types
    embed.add_field(
        name="Access Types",
        value="**Full Access**: Can manage services and commands\n"
              "**Command Access**: Can only use commands",
        inline=False
    )
    
    # Only show management buttons if you've verified
    view = PermissionManagementView(interaction.user.id)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# ----------------- Startup -----------------
@bot.event
async def on_ready():
    try:
        # Initialize global data structures if they don't exist
        global verification_codes, verified_users, user_services, shared_access
        if verification_codes is None:
            verification_codes = {}
        if verified_users is None:
            verified_users = {}
        if user_services is None:
            user_services = {}
        if shared_access is None:
            shared_access = {}
        
        # Log the state of data structures
        print(f"Bot initialized with {len(verified_users)} verified users")
        print(f"Bot initialized with {len(user_services)} user services")
        print(f"Bot initialized with {len(shared_access)} shared access entries")
            
        synced = await bot.tree.sync()
        print(f"✅ Bot is ready as {bot.user}")
        print(f"Synced {len(synced)} commands")
        bot.loop.create_task(cleanup_expired_params())
    except Exception as e:
        print(f"Failed to sync commands: {str(e)}\n{traceback.format_exc()}")

# Add global error handler
@bot.event
async def on_error(event, *args, **kwargs):
    error = sys.exc_info()[1]
    logger.error(f"Error in event {event}: {error}\n{traceback.format_exc()}")

if __name__ == "__main__":
    # Use environment variable for token in production
    TOKEN = os.getenv("DISCORD_BOT_TOKEN") or "MTM2OTY0MzYxNjMzODcxMDU0OA.GIm8gI.-j0lkgM6KR0TxxLQPIzXgytEjy_WvhtEF1XOhE"
    if not TOKEN:
        logger.error("No Discord bot token found in environment variables")
        sys.exit(1)
    bot.run(TOKEN)