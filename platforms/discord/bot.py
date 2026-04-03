import logging
import os
import sys
from typing import Optional

import discord
from discord.ext import commands

from core.handler import MessageHandler
from core.types import Message
from persistence.store import Store
from persistence.presence import PresenceRepository
from persistence.user_repository import UserRepository
from .adapter import DiscordAdapter

logger = logging.getLogger(__name__)

# Hackathon channel routing
HACKATHON_CHANNEL_ID = os.environ.get("HACKATHON_CHANNEL_ID", "")

# Lazy-load hackathon commands to avoid import errors if hackathon/ not present
_hackathon_commands = None

def _get_hackathon_commands():
    global _hackathon_commands
    if _hackathon_commands is None:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hackathon"))
            from hackathon.hackathon_commands import handle_hackathon_command
            _hackathon_commands = handle_hackathon_command
            logger.info("Hackathon commands loaded")
        except Exception as e:
            logger.warning(f"Hackathon commands not available: {e}")
            _hackathon_commands = False  # Mark as failed, don't retry
    return _hackathon_commands if _hackathon_commands else None

class DiscordBot:
    """Discord bot wrapper that uses core abstractions."""

    def __init__(
        self,
        token: str,
        handler: MessageHandler,
        store: Optional[Store] = None,
        bot_name: str = "kevin",
        app_id: str = "",
        use_emulator: bool = True,
    ):
        self.token = token
        self.handler = handler
        self.store = store
        self.bot_name = bot_name
        self.app_id = app_id
        self.adapter = DiscordAdapter()

        # Presence repository for syncing to Firestore
        self.presence = PresenceRepository(bot_name, use_emulator=use_emulator)

        # User repository for user/service management
        self.users = UserRepository(bot_name, use_emulator=use_emulator)

        # Set up intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True

        self.client = commands.Bot(
            command_prefix="!",
            intents=intents,
        )

        self._setup_events()

    def _setup_events(self):
        @self.client.event
        async def on_ready():
            logger.info(f"Bot connected as {self.client.user}")
            logger.info(f"Connected to {len(self.client.guilds)} guild(s)")
            for guild in self.client.guilds:
                logger.info(f"  - {guild.name} (id: {guild.id})")

            # Sync presence to Firestore
            try:
                guilds_data = self.adapter.extract_presence_data(self.client)
                username = str(self.client.user) if self.client.user else self.bot_name
                await self.presence.sync_from_discord(
                    app_id=self.app_id,
                    username=username,
                    guilds=guilds_data,
                )
                logger.info(f"Presence synced to Firestore for {self.bot_name}")
            except Exception as e:
                logger.warning(f"Failed to sync presence (continuing without): {e}")

        @self.client.event
        async def on_guild_join(guild: discord.Guild):
            logger.info(f"Joined guild: {guild.name} (id: {guild.id})")

        @self.client.event
        async def on_guild_remove(guild: discord.Guild):
            logger.info(f"Removed from guild: {guild.name} (id: {guild.id})")

        @self.client.event
        async def on_message(message: discord.Message):
            # Don't respond to ourselves
            if message.author == self.client.user:
                return

            # --- Channel-based routing ---

            channel_id = str(message.channel.id)

            # Hackathon channel: try hackathon commands first
            if HACKATHON_CHANNEL_ID and channel_id == HACKATHON_CHANNEL_ID:
                handler_fn = _get_hackathon_commands()
                if handler_fn:
                    try:
                        result = handler_fn(
                            message_content=message.content,
                            author_id=str(message.author.id),
                            channel_id=channel_id,
                        )
                        if result is not None:
                            if "embed" in result:
                                await message.channel.send(embed=discord.Embed.from_dict(result["embed"]))
                            elif "dm" in result:
                                try:
                                    await message.author.send(result["dm"])
                                except discord.Forbidden:
                                    await message.channel.send("Could not DM you. Please enable DMs from server members.")
                                    return
                                if "reply" in result:
                                    await message.channel.send(result["reply"])
                            elif "content" in result:
                                await message.channel.send(result["content"])
                            return
                    except Exception as e:
                        logger.exception(f"Hackathon command error: {e}")

            # --- Default: platform-agnostic handler ---

            core_message = self.adapter.adapt_message(message)
            context = self.adapter.create_context(core_message)

            logger.info(f"Message from {core_message.author.username} (id:{core_message.author.id}) in {core_message.channel.name}: {core_message.content[:50]}")

            # Enrich context with persistence data (non-blocking, failures ok)
            if self.store:
                try:
                    context = await self.store.enrich_context(context)
                except Exception as e:
                    logger.warning(f"Store enrichment failed (continuing without): {e}")

            # Process through handler
            try:
                response = await self.handler.process(core_message, context)
                if response:
                    await message.channel.send(response)
            except Exception as e:
                logger.exception(f"Error handling message: {e}")

    async def start(self):
        """Start the bot."""
        await self.client.start(self.token)

    def run(self):
        """Run the bot (blocking)."""
        self.client.run(self.token)
