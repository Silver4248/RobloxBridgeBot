import os
from dotenv import load_dotenv  # Add this import

# Add this line near the top, before any os.getenv() calls
load_dotenv()

import random
import string
import logging
import asyncio
from datetime import datetime, timezone
from aiohttp import web
import ssl
import json

logger = logging.getLogger('web_service')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

def utcnow():
    """Return the current UTC datetime with timezone information"""
    return datetime.now(timezone.utc)

class WebService:
    def __init__(self):
        self.services = {}  # {service_id: service_data}
        self.runners = {}   # {service_id: web.AppRunner}
        self.base_port = 8080
        self.ssl_context = self._create_ssl_context()
        
    def _create_ssl_context(self):
        """Create SSL context for HTTPS"""
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        
        cert_path = os.getenv('SSL_CERT_PATH', '/path/to/cert.pem')
        key_path = os.getenv('SSL_KEY_PATH', '/path/to/key.pem')
        
        if os.path.exists(cert_path) and os.path.exists(key_path):
            context.load_cert_chain(cert_path, key_path)
            logger.info("SSL certificate loaded successfully")
        else:
            logger.warning("SSL certificate files not found - using HTTP only")
            return None
            
        return context
    
    def _get_available_port(self):
        """Find an available port for a new service"""
        used_ports = {s.get('port') for s in self.services.values()}
        port = self.base_port
        while port in used_ports:
            port += 1
        return port
    
    async def create_service(self, guild_id: int, user_id: int, service_name: str) -> dict:
        """Create a new web service for command synchronization"""
        api_key = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        secret_token = ''.join(random.choices(string.ascii_letters + string.digits, k=64))
        
        service_id = f"{guild_id}-{user_id}-{service_name}"
        port = self._get_available_port()
        
        # Create service URL
        use_ssl = self.ssl_context is not None
        protocol = "https" if use_ssl else "http"
        domain = os.getenv('SERVICE_DOMAIN', 'localhost')
        service_url = f"{protocol}://{domain}:{port}"
        
        # Initialize service data
        self.services[service_id] = {
            "guild_id": guild_id,
            "user_id": user_id,
            "service_name": service_name,
            "api_key": api_key,
            "secret_token": secret_token,
            "port": port,
            "commands": [],
            "triggered_commands": {},
            "command_queue": [],
            "created_at": utcnow().timestamp(),
            "url": service_url,
            "active": True
        }
        
        # Create and start the web application
        app = web.Application()
        
        # Add routes with service_id in app state
        app['service_id'] = service_id
        app.router.add_get('/commands', self.handle_get_commands)
        app.router.add_post('/trigger', self.handle_trigger)
        app.router.add_get('/triggered', self.handle_get_triggered)
        app.router.add_post('/acknowledge', self.handle_acknowledge)
        app.router.add_get('/health', self.handle_health)
        app.router.add_options('/{tail:.*}', self.handle_options)
        
        # Add CORS middleware
        app.middlewares.append(self.cors_middleware)
        
        # Start the runner
        runner = web.AppRunner(app)
        await runner.setup()
        
        if use_ssl:
            site = web.TCPSite(runner, '0.0.0.0', port, ssl_context=self.ssl_context)
        else:
            site = web.TCPSite(runner, '0.0.0.0', port)
        
        await site.start()
        
        self.runners[service_id] = runner
        
        logger.info(f"Service created: {service_id} on port {port}")
        
        return {
            "api_key": api_key,
            "secret_token": secret_token,
            "url": service_url,
            "port": port
        }
    
    @web.middleware
    async def cors_middleware(self, request, handler):
        """Add CORS headers to all responses"""
        if request.method == "OPTIONS":
            return web.Response(
                headers={
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Api-Key',
                    'Access-Control-Max-Age': '3600'
                }
            )
        
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Api-Key'
        return response
    
    async def handle_options(self, request):
        """Handle CORS preflight requests"""
        return web.Response(
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Api-Key',
                'Access-Control-Max-Age': '3600'
            }
        )
    
    def _verify_api_key(self, request, service_id: str) -> bool:
        """Verify API key from request"""
        auth_header = request.headers.get('Authorization', '')
        api_key_header = request.headers.get('X-Api-Key', '')
        
        if service_id not in self.services:
            return False
        
        expected_key = self.services[service_id]['api_key']
        
        # Support both Authorization: Bearer and X-Api-Key headers
        if auth_header.startswith('Bearer '):
            return auth_header[7:] == expected_key
        elif api_key_header:
            return api_key_header == expected_key
        
        return False
    
    async def handle_health(self, request):
        """Health check endpoint"""
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"status": "not_found"}, status=404)
        
        service = self.services[service_id]
        
        return web.json_response({
            "status": "healthy",
            "service_name": service['service_name'],
            "active": service['active'],
            "uptime": utcnow().timestamp() - service['created_at'],
            "commands_count": len(service['commands']),
            "queue_length": len(service['command_queue'])
        })
    
    async def handle_get_commands(self, request):
        """GET /commands - Roblox requests list of available commands"""
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        if not self._verify_api_key(request, service_id):
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        service = self.services[service_id]
        
        # Return all active commands
        commands_list = []
        for cmd in service['commands']:
            if cmd.get('active', True):
                commands_list.append({
                    "command": cmd['command_name'],
                    "full_command": cmd['full_command'],
                    "parameters": cmd['params'],
                    "roblox_id": cmd['roblox_id'],
                    "has_params": cmd['has_params'],
                    "created_at": cmd['created_at']
                })
        
        return web.json_response({
            "commands": commands_list,
            "total": len(commands_list),
            "timestamp": utcnow().timestamp()
        })
    
    async def handle_trigger(self, request):
        """POST /trigger - Discord sends triggered command to be queued"""
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        if not self._verify_api_key(request, service_id):
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        
        service = self.services[service_id]
        
        # Create command entry
        command_id = f"{data['command']}_{int(utcnow().timestamp() * 1000)}"
        command_entry = {
            "command_id": command_id,
            "command": data['command'],
            "parameters": data.get('parameters', []),
            "full_command": data.get('full_command', data['command']),
            "triggered_by": data.get('triggered_by'),
            "triggered_at": utcnow().timestamp(),
            "acknowledged": False
        }
        
        # Add to both queue and triggered_commands
        service['command_queue'].append(command_entry)
        service['triggered_commands'][command_id] = command_entry
        
        # Clean up old commands
        await self._cleanup_commands(service_id)
        
        logger.info(f"Command triggered: {command_id} on service {service_id}")
        
        return web.json_response({
            "status": "success",
            "command_id": command_id,
            "queued": True
        })
    
    async def handle_get_triggered(self, request):
        """GET /triggered - Roblox polls for new commands"""
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        if not self._verify_api_key(request, service_id):
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        service = self.services[service_id]
        
        # Clean up old commands first
        await self._cleanup_commands(service_id)
        
        # Return unacknowledged commands
        pending_commands = [
            cmd for cmd in service['command_queue']
            if not cmd.get('acknowledged', False)
        ]
        
        return web.json_response({
            "commands": pending_commands,
            "count": len(pending_commands),
            "timestamp": utcnow().timestamp()
        })
    
    async def handle_acknowledge(self, request):
        """POST /acknowledge - Roblox acknowledges command receipt"""
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        if not self._verify_api_key(request, service_id):
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        try:
            data = await request.json()
            command_id = data.get('command_id')
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        
        if not command_id:
            return web.json_response({"error": "command_id required"}, status=400)
        
        service = self.services[service_id]
        
        # Mark command as acknowledged
        if command_id in service['triggered_commands']:
            service['triggered_commands'][command_id]['acknowledged'] = True
            service['triggered_commands'][command_id]['acknowledged_at'] = utcnow().timestamp()
            
            # Remove from queue
            service['command_queue'] = [
                cmd for cmd in service['command_queue']
                if cmd['command_id'] != command_id
            ]
            
            logger.info(f"Command acknowledged: {command_id} on service {service_id}")
            
            return web.json_response({
                "status": "acknowledged",
                "command_id": command_id
            })
        
        return web.json_response({"error": "Command not found"}, status=404)
    
    async def _cleanup_commands(self, service_id: str):
        """Clean up old commands (older than 60 seconds)"""
        if service_id not in self.services:
            return
        
        service = self.services[service_id]
        current_time = utcnow().timestamp()
        
        # Remove commands older than 60 seconds
        expired = [
            cmd_id for cmd_id, cmd in service['triggered_commands'].items()
            if current_time - cmd['triggered_at'] > 60
        ]
        
        for cmd_id in expired:
            service['triggered_commands'].pop(cmd_id, None)
            logger.info(f"Cleaned up expired command: {cmd_id}")
        
        # Clean up queue
        service['command_queue'] = [
            cmd for cmd in service['command_queue']
            if current_time - cmd['triggered_at'] <= 60
        ]
    
    def update_service_commands(self, service_id: str, commands: list):
        """Update the commands list for a service"""
        if service_id in self.services:
            self.services[service_id]['commands'] = commands
            logger.info(f"Updated commands for service {service_id}: {len(commands)} commands")
    
    async def stop_service(self, service_id: str):
        """Stop and clean up a service"""
        if service_id in self.runners:
            await self.runners[service_id].cleanup()
            del self.runners[service_id]
            logger.info(f"Stopped runner for service {service_id}")
        
        if service_id in self.services:
            del self.services[service_id]
            logger.info(f"Removed service {service_id}")
    
    async def start(self):
        """Initialize the web service"""
        logger.info("Web service manager initialized and ready")

# Singleton instance
web_service = WebService()