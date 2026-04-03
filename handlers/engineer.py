"""
Engineer integration handler.

Communicates with the Engineer on-call automation system via its HTTP API.
"""
import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

ENGINEER_BASE_URL = "http://localhost:8765"
TIMEOUT = aiohttp.ClientTimeout(total=10)


class EngineerClient:
    """HTTP client for Engineer API."""

    def __init__(self, base_url: str = ENGINEER_BASE_URL):
        self.base_url = base_url

    async def _get(self, path: str) -> dict | str | None:
        """Make GET request to Engineer API."""
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                async with session.get(f"{self.base_url}{path}") as resp:
                    if resp.content_type == "application/json":
                        return await resp.json()
                    return await resp.text()
        except aiohttp.ClientConnectorError:
            return None
        except Exception as e:
            logger.error(f"Engineer API error: {e}")
            return None

    async def is_running(self) -> bool:
        """Check if Engineer is running."""
        result = await self._get("/")
        return result is not None

    async def get_status(self) -> Optional[dict]:
        """Get Engineer status."""
        return await self._get("/")

    async def get_projects(self) -> Optional[dict]:
        """Get list of configured projects."""
        return await self._get("/projects")

    async def health_check(self, project: str) -> Optional[str]:
        """Trigger health check for a project."""
        return await self._get(f"/healthcheck/{project}")

    async def mute(self) -> Optional[str]:
        """Mute sound notifications."""
        return await self._get("/mute")

    async def unmute(self) -> Optional[str]:
        """Unmute sound notifications."""
        return await self._get("/unmute")

    async def toggle_sound(self) -> Optional[str]:
        """Toggle sound state."""
        return await self._get("/sound/toggle")


class EngineerHandler:
    """Handles !engineer commands."""

    def __init__(self):
        self.client = EngineerClient()

    async def handle(self, args: str) -> str:
        """
        Handle engineer subcommand.

        Args:
            args: The part after "!engineer " (e.g., "status", "health homebase-indexer")

        Returns:
            Response message
        """
        args = args.strip()

        # No args - show help/status
        if not args:
            return await self._help()

        # Parse subcommand
        parts = args.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""

        # Route to handler
        if cmd == "status":
            return await self._status()
        elif cmd == "projects":
            return await self._projects()
        elif cmd == "health":
            return await self._health(cmd_args)
        elif cmd == "mute":
            return await self._mute()
        elif cmd == "unmute":
            return await self._unmute()
        elif cmd == "help":
            return await self._help()
        else:
            return f"Unknown command: `{cmd}`. Try `!engineer help`"

    async def _help(self) -> str:
        """Show help and quick status."""
        data = await self.client.get_status()
        running = data is not None

        if running and isinstance(data, dict):
            instance_id = data.get("instance_id", "")
            instance_info = f" @ `{instance_id}`" if instance_id else ""
        else:
            instance_info = ""

        status_icon = "🟢" if running else "🔴"

        return f"""{status_icon} **Engineer**{instance_info} {'(running)' if running else '(offline)'}

**Commands:**
`!engineer status` - Detailed status
`!engineer projects` - List monitored projects
`!engineer health <project>` - Trigger health check
`!engineer mute` - Mute sound notifications
`!engineer unmute` - Unmute sound notifications"""

    async def _status(self) -> str:
        """Get detailed status."""
        data = await self.client.get_status()

        if not data:
            return "🔴 **Engineer is offline** - Cannot connect to localhost:8765"

        if isinstance(data, str):
            return f"🟢 **Engineer** - Raw response: {data}"

        instance_id = data.get("instance_id", "unknown")
        uptime = data.get("uptime", "unknown")
        alerts = data.get("alerts_received", data.get("alerts_handled", 0))
        sound = "🔊 on" if data.get("sound", True) else "🔇 muted"
        status = data.get("status", "unknown")

        return f"""🟢 **Engineer Status**
• Instance: `{instance_id}`
• Status: {status}
• Uptime: {uptime}
• Alerts handled: {alerts}
• Sound: {sound}"""

    async def _projects(self) -> str:
        """List configured projects."""
        data = await self.client.get_projects()

        if not data:
            return "🔴 **Engineer is offline**"

        if isinstance(data, str):
            return f"Projects: {data}"

        projects = data.get("projects", [])
        if not projects:
            return "No projects configured."

        lines = ["**Configured Projects:**"]
        for p in projects:
            if isinstance(p, dict):
                name = p.get("name", "unknown")
                source = p.get("source", "")
                lines.append(f"• `{name}` {f'({source})' if source else ''}")
            else:
                lines.append(f"• `{p}`")

        return "\n".join(lines)

    async def _health(self, project: str) -> str:
        """Trigger health check."""
        if not project:
            return "Usage: `!engineer health <project>`\nUse `!engineer projects` to see available projects."

        result = await self.client.health_check(project)

        if result is None:
            return "🔴 **Engineer is offline**"

        return f"🏥 Health check triggered for `{project}`\n{result}"

    async def _mute(self) -> str:
        """Mute sound."""
        result = await self.client.mute()

        if result is None:
            return "🔴 **Engineer is offline**"

        return "🔇 Sound muted"

    async def _unmute(self) -> str:
        """Unmute sound."""
        result = await self.client.unmute()

        if result is None:
            return "🔴 **Engineer is offline**"

        return "🔊 Sound unmuted"
