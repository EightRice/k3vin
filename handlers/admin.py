import logging
import os
from datetime import datetime
from typing import Optional

import discord

from core.handler import MessageHandler
from core.auth import AuthProvider
from core.types import Message, Context
from persistence.presence import PresenceRepository
from persistence.user_repository import UserRepository
from .engineer import EngineerHandler
from .support import SupportHandler

logger = logging.getLogger(__name__)

# Max dump size (~2MB, War and Peace is ~3.2MB)
MAX_DUMP_SIZE = 2_000_000
DUMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dumps")

# Discord permission flags for the bot
PERMISSIONS = {
    "VIEW_CHANNEL": 1 << 10,        # 1024
    "MANAGE_CHANNELS": 1 << 4,       # 16 - create/edit/delete channels
    "SEND_MESSAGES": 1 << 11,        # 2048
    "MANAGE_MESSAGES": 1 << 13,      # 8192 - delete messages, pin
    "EMBED_LINKS": 1 << 14,          # 16384
    "ATTACH_FILES": 1 << 15,         # 32768
    "READ_MESSAGE_HISTORY": 1 << 16, # 65536
    "ADD_REACTIONS": 1 << 6,         # 64
    "USE_EXTERNAL_EMOJIS": 1 << 18,  # 262144
    "MANAGE_THREADS": 1 << 34,       # For thread management
}

# Calculate total permissions
BOT_PERMISSIONS = sum(PERMISSIONS.values())


class AdminHandler(MessageHandler):
    """Handler for admin commands via DM."""

    def __init__(self, auth_provider: AuthProvider, app_id: str, bot_client=None):
        super().__init__(auth_provider)
        self.app_id = app_id
        self.bot_client = bot_client  # Set after bot initialization
        self.presence: Optional[PresenceRepository] = None
        self.users: Optional[UserRepository] = None
        self.engineer = EngineerHandler()
        self.support: Optional[SupportHandler] = None  # Initialized when users is set

    def set_bot_client(self, client):
        """Set the Discord client reference for server operations."""
        self.bot_client = client

    def set_presence(self, presence: PresenceRepository):
        """Set the presence repository reference."""
        self.presence = presence

    def set_users(self, users: UserRepository):
        """Set the users repository reference and initialize dependent handlers."""
        self.users = users
        self.support = SupportHandler(users, super_admins=self.auth.super_admins)

    async def handle(self, message: Message, context: Context) -> Optional[str]:
        content = message.content.strip()

        # Command routing
        if content == "!ping":
            return f"pong! (authorized via: {self.auth.get_auth_reason(context)})"

        if content.startswith("!echo "):
            return content[6:]

        if content == "!help":
            return self._help_text(context)

        if content == "!invite":
            return self._invite_url()

        if content == "!servers":
            return await self._list_servers()

        if content.startswith("!leave "):
            server_id = content[7:].strip()
            return await self._leave_server(server_id)

        if content.startswith("!dump "):
            server_id = content[6:].strip()
            return await self._dump_server(server_id)

        if content == "!engineer" or content.startswith("!engineer "):
            args = content[10:] if content.startswith("!engineer ") else ""
            return await self.engineer.handle(args)

        if content == "!presence" or content.startswith("!presence "):
            args = content[10:] if content.startswith("!presence ") else ""
            return await self._presence(args)

        if content == "!support" or content.startswith("!support "):
            if not self.support:
                return "Support service not configured"
            args = content[9:] if content.startswith("!support ") else ""
            return await self.support.handle(
                user_id=message.author.id,
                username=message.author.username,
                args=args,
            )

        # No command matched
        return None

    def _help_text(self, context: Context) -> str:
        """Return help text based on auth level."""
        base_commands = """**Commands:**
`!ping` - Check bot status
`!echo <text>` - Echo text back
`!help` - Show this help"""

        admin_commands = """

**Admin Commands:**
`!invite` - Get bot invite URL
`!servers` - List connected servers
`!leave <server_id>` - Leave a server
`!dump <server_id>` - Export server history to .md file
`!engineer` - Engineer on-call system controls
`!presence` - View bot presence data from Firestore
`!support` - Customer support service (credit-based)"""

        if context.user.id in self.auth.super_admins:
            return base_commands + admin_commands

        return base_commands

    def _invite_url(self) -> str:
        """Generate OAuth2 invite URL with required permissions."""
        url = (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={self.app_id}"
            f"&permissions={BOT_PERMISSIONS}"
            f"&scope=bot"
        )

        perms_list = ", ".join(PERMISSIONS.keys())

        return f"""**Bot Invite URL:**
{url}

**Permissions requested:**
{perms_list}

Share this link with server admins to add the bot."""

    async def _list_servers(self) -> str:
        """List all servers the bot is connected to."""
        if not self.bot_client:
            return "Error: Bot client not available"

        guilds = self.bot_client.guilds
        if not guilds:
            return "Not connected to any servers."

        lines = ["**Connected Servers:**"]
        for guild in guilds:
            member_count = guild.member_count or "?"
            lines.append(f"• **{guild.name}** (id: `{guild.id}`, members: {member_count})")

        return "\n".join(lines)

    async def _leave_server(self, server_id: str) -> str:
        """Leave a server by ID."""
        if not self.bot_client:
            return "Error: Bot client not available"

        try:
            guild_id = int(server_id)
        except ValueError:
            return f"Invalid server ID: `{server_id}`"

        guild = self.bot_client.get_guild(guild_id)
        if not guild:
            return f"Not connected to server with ID `{server_id}`"

        guild_name = guild.name
        await guild.leave()

        return f"Left server: **{guild_name}** (`{server_id}`)"

    async def _dump_server(self, server_id: str) -> str:
        """Dump all visible message history from a server to .md file."""
        if not self.bot_client:
            return "Error: Bot client not available"

        try:
            guild_id = int(server_id)
        except ValueError:
            return f"Invalid server ID: `{server_id}`"

        guild = self.bot_client.get_guild(guild_id)
        if not guild:
            return f"Not connected to server with ID `{server_id}`"

        # Create dumps directory if needed
        os.makedirs(DUMP_DIR, exist_ok=True)

        # Start building the dump
        lines = []
        lines.append(f"# {guild.name}")
        lines.append(f"Server ID: {guild.id}")
        lines.append(f"Dumped: {datetime.now().isoformat()}")
        lines.append(f"Members: {guild.member_count}")
        lines.append("")
        lines.append("---")
        lines.append("")

        total_size = sum(len(line) for line in lines)
        truncated = False
        channel_count = 0
        message_count = 0

        # Iterate through text channels
        for channel in guild.text_channels:
            if truncated:
                break

            # Check if bot can read this channel
            perms = channel.permissions_for(guild.me)
            if not perms.read_messages or not perms.read_message_history:
                continue

            channel_header = [
                f"## #{channel.name}",
                f"Channel ID: {channel.id}",
                ""
            ]

            channel_messages = []

            try:
                # Fetch messages (limit per channel to avoid rate limits)
                async for msg in channel.history(limit=500, oldest_first=True):
                    timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                    author = msg.author.display_name

                    # Format message
                    content = msg.content or ""
                    if msg.attachments:
                        attachments = ", ".join(a.filename for a in msg.attachments)
                        content += f" [Attachments: {attachments}]"
                    if msg.embeds:
                        content += " [Embed]"

                    if content.strip():
                        line = f"**[{timestamp}] {author}:** {content}"
                        channel_messages.append(line)
                        message_count += 1

            except discord.Forbidden:
                channel_messages.append("*[No permission to read history]*")
            except Exception as e:
                channel_messages.append(f"*[Error reading channel: {e}]*")

            if channel_messages:
                # Check size before adding
                section = "\n".join(channel_header + channel_messages + ["", "---", ""])
                if total_size + len(section) > MAX_DUMP_SIZE:
                    lines.append(f"\n\n**[TRUNCATED - Max size {MAX_DUMP_SIZE:,} chars reached]**")
                    truncated = True
                    break

                lines.extend(channel_header)
                lines.extend(channel_messages)
                lines.append("")
                lines.append("---")
                lines.append("")
                total_size += len(section)
                channel_count += 1

        # Write to file
        safe_name = "".join(c if c.isalnum() or c in "- " else "_" for c in guild.name)
        filename = f"{safe_name}_{guild.id}.md"
        filepath = os.path.join(DUMP_DIR, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        status = "**[TRUNCATED]** " if truncated else ""
        return (
            f"{status}Dumped **{guild.name}** to `{filename}`\n"
            f"• Channels: {channel_count}\n"
            f"• Messages: {message_count}\n"
            f"• Size: {total_size:,} chars"
        )

    async def _presence(self, args: str) -> str:
        """Show bot presence data from Firestore."""
        if not self.presence:
            return "Presence repository not configured"

        args = args.strip()

        # No args - show summary
        if not args:
            try:
                bot = await self.presence.get_bot_metadata()
                if not bot:
                    return "No presence data found. Bot may not have synced yet."

                guilds = await self.presence.get_all_guilds()

                lines = [f"**Bot Presence: {bot.bot_name}**"]
                lines.append(f"• Username: `{bot.username}`")
                lines.append(f"• App ID: `{bot.app_id}`")
                if bot.last_sync:
                    lines.append(f"• Last sync: {bot.last_sync.strftime('%Y-%m-%d %H:%M:%S')}")
                lines.append(f"• Guilds: {bot.total_guilds}")
                lines.append(f"• Channels: {bot.total_channels}")
                lines.append("")
                lines.append("**Guilds:**")

                for guild in guilds:
                    status = "🟢" if guild.is_active else "🔴"
                    lines.append(f"{status} **{guild.name}** (`{guild.guild_id}`)")
                    lines.append(f"   Members: {guild.member_count}")

                lines.append("")
                lines.append("Use `!presence <guild_id>` for guild details")
                return "\n".join(lines)

            except Exception as e:
                logger.error(f"Presence query failed: {e}")
                return f"Error querying presence: {e}"

        # Guild ID provided - show guild details
        try:
            guild = await self.presence.get_guild(args)
            if not guild:
                return f"Guild `{args}` not found in presence data"

            channels = await self.presence.get_channels(args)

            lines = [f"**Guild: {guild.name}**"]
            lines.append(f"• ID: `{guild.guild_id}`")
            lines.append(f"• Members: {guild.member_count}")
            lines.append(f"• Owner ID: `{guild.owner_id}`")
            if guild.joined_at:
                lines.append(f"• Joined: {guild.joined_at.strftime('%Y-%m-%d')}")
            if guild.last_seen:
                lines.append(f"• Last seen: {guild.last_seen.strftime('%Y-%m-%d %H:%M')}")
            if guild.left_at:
                lines.append(f"• Left: {guild.left_at.strftime('%Y-%m-%d %H:%M')}")

            # Group channels by category
            categories = {}
            no_category = []
            for ch in channels:
                if ch.type == "category":
                    continue
                if ch.category_name:
                    if ch.category_name not in categories:
                        categories[ch.category_name] = []
                    categories[ch.category_name].append(ch)
                else:
                    no_category.append(ch)

            lines.append("")
            lines.append(f"**Channels ({len(channels)}):**")

            # Show uncategorized first
            for ch in sorted(no_category, key=lambda c: c.position):
                perms = self._format_channel_perms(ch)
                lines.append(f"  #{ch.name} {perms}")

            # Show by category
            for cat_name in sorted(categories.keys()):
                lines.append(f"  **{cat_name}**")
                for ch in sorted(categories[cat_name], key=lambda c: c.position):
                    perms = self._format_channel_perms(ch)
                    lines.append(f"    #{ch.name} {perms}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Presence query failed: {e}")
            return f"Error querying presence: {e}"

    def _format_channel_perms(self, channel) -> str:
        """Format channel permissions as icons."""
        icons = []
        if channel.can_read:
            icons.append("👁")
        if channel.can_send:
            icons.append("✍")
        if channel.can_manage:
            icons.append("⚙")
        return "".join(icons) if icons else "🚫"
