import os
from dotenv import load_dotenv
import random
import string
import logging
from datetime import datetime, timezone
from aiohttp import web

# Load environment variables
load_dotenv()

logger = logging.getLogger('web_service')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

def utcnow():
    return datetime.now(timezone.utc)

class WebService:
    def __init__(self):
        self.services = {}
        self.runners = {}
        self.base_port = 8080
        # Hardcoded public URL - change this to your Railway domain
        self.public_url = 'https://robloxbridgebot-production.up.railway.app'
        
    def _get_available_port(self):
        used_ports = {s.get('port') for s in self.services.values()}
        port = self.base_port
        while port in used_ports:
            port += 1
        return port
    
    async def create_service(self, guild_id: int, user_id: int, service_name: str) -> dict:
        api_key = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        service_id = f"{guild_id}-{user_id}-{service_name}"
        port = self._get_available_port()
        service_url = f"{self.public_url}:{port}"
        
        self.services[service_id] = {
            "guild_id": guild_id,
            "service_name": service_name,
            "api_key": api_key,
            "port": port,
            "commands": [],
            "triggered_commands": {},
            "command_queue": [],
            "created_at": utcnow().timestamp(),
            "url": service_url,
            "active": True
        }
        
        app = web.Application()
        app['service_id'] = service_id
        app.router.add_get('/commands', self.handle_get_commands)
        app.router.add_post('/trigger', self.handle_trigger)
        app.router.add_get('/triggered', self.handle_get_triggered)
        app.router.add_get('/health', self.handle_health)
        app.middlewares.append(self.cors_middleware)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        self.runners[service_id] = runner
        logger.info(f"Service created: {service_id} on port {port}")
        
        return {"api_key": api_key, "url": service_url, "port": port}
    
    @web.middleware
    async def cors_middleware(self, request, handler):
        if request.method == "OPTIONS":
            return web.Response(headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                'Access-Control-Max-Age': '3600'
            })
        
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response
    
    def _verify_api_key(self, request, service_id: str) -> bool:
        auth_header = request.headers.get('Authorization', '')
        
        if service_id not in self.services:
            return False
        
        expected_key = self.services[service_id]['api_key']
        
        if auth_header.startswith('Bearer '):
            return auth_header[7:] == expected_key
        
        return False
    
    async def handle_health(self, request):
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
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        if not self._verify_api_key(request, service_id):
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        service = self.services[service_id]
        
        commands_list = []
        for cmd in service['commands']:
            if cmd.get('active', True):
                commands_list.append({
                    "command": cmd['command_name'],
                    "full_command": cmd['full_command'],
                    "parameters": cmd['params'],
                    "created_at": cmd['created_at']
                })
        
        return web.json_response({
            "commands": commands_list,
            "total": len(commands_list),
            "timestamp": utcnow().timestamp()
        })
    
    async def handle_trigger(self, request):
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        if not self._verify_api_key(request, service_id):
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        try:
            data = await request.json()
        except:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        
        service = self.services[service_id]
        
        command_id = f"{data['command']}_{int(utcnow().timestamp() * 1000)}"
        command_entry = {
            "command_id": command_id,
            "command": data['command'],
            "parameters": data.get('parameters', []),
            "full_command": data.get('full_command', data['command']),
            "triggered_by": data.get('triggered_by'),
            "triggered_at": utcnow().timestamp()
        }
        
        service['command_queue'].append(command_entry)
        service['triggered_commands'][command_id] = command_entry
        
        await self._cleanup_commands(service_id)
        
        logger.info(f"Command triggered: {command_id}")
        
        return web.json_response({"status": "success", "command_id": command_id})
    
    async def handle_get_triggered(self, request):
        service_id = request.app['service_id']
        
        if service_id not in self.services:
            return web.json_response({"error": "Service not found"}, status=404)
        
        # Get API key from query string for Roblox compatibility
        api_key_param = request.query.get('api_key', '')
        auth_valid = False
        
        if api_key_param and service_id in self.services:
            auth_valid = api_key_param == self.services[service_id]['api_key']
        else:
            auth_valid = self._verify_api_key(request, service_id)
        
        if not auth_valid:
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        service = self.services[service_id]
        
        await self._cleanup_commands(service_id)
        
        pending_commands = service['command_queue']
        
        return web.json_response({
            "triggered_commands": pending_commands,
            "count": len(pending_commands),
            "timestamp": utcnow().timestamp()
        })
    
    async def _cleanup_commands(self, service_id: str):
        if service_id not in self.services:
            return
        
        service = self.services[service_id]
        current_time = utcnow().timestamp()
        
        expired = [
            cmd_id for cmd_id, cmd in service['triggered_commands'].items()
            if current_time - cmd['triggered_at'] > 60
        ]
        
        for cmd_id in expired:
            service['triggered_commands'].pop(cmd_id, None)
        
        service['command_queue'] = [
            cmd for cmd in service['command_queue']
            if current_time - cmd['triggered_at'] <= 60
        ]
    
    def update_service_commands(self, service_id: str, commands: list):
        if service_id in self.services:
            self.services[service_id]['commands'] = commands
            logger.info(f"Updated commands for {service_id}: {len(commands)} commands")
    
    async def stop_service(self, service_id: str):
        if service_id in self.runners:
            await self.runners[service_id].cleanup()
            del self.runners[service_id]
            logger.info(f"Stopped runner for {service_id}")
        
        if service_id in self.services:
            del self.services[service_id]
            logger.info(f"Removed service {service_id}")

web_service = WebService()