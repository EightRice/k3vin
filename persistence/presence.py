"""
Presence repository for syncing bot state to Firestore.

Handles persistence of guild/channel data per bot instance.
"""
import os
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_document import AsyncDocumentReference

from .models import BotPresence, GuildPresence, ChannelPresence, ChannelType

logger = logging.getLogger(__name__)


class PresenceRepository:
    """
    Async Firestore repository for bot presence data.

    Structure:
        bots/{bot_name}/
            metadata doc: {app_id, username, last_sync, total_guilds}
            guilds/{guild_id}/
                guild doc: {name, member_count, permissions, timestamps...}
                channels/{channel_id}/
                    channel doc: {name, type, permissions...}
    """

    def __init__(self, bot_name: str, use_emulator: bool = True):
        self.bot_name = bot_name
        self.use_emulator = use_emulator
        self._db: Optional[AsyncClient] = None

    async def _get_db(self) -> AsyncClient:
        """Lazy async initialization of Firestore client."""
        if self._db is not None:
            return self._db

        if self.use_emulator:
            os.environ["FIRESTORE_EMULATOR_HOST"] = "localhost:8080"
            # Use async client directly for emulator
            self._db = AsyncClient(project="demo-kevin")
        else:
            # Production - credentials from env
            self._db = AsyncClient()

        return self._db

    def _bot_ref(self, db: AsyncClient) -> AsyncDocumentReference:
        """Get reference to bot's metadata document."""
        return db.collection("bots").document(self.bot_name)

    async def save_bot_metadata(self, presence: BotPresence) -> None:
        """Save/update bot metadata."""
        db = await self._get_db()
        ref = self._bot_ref(db)
        await ref.set(presence.to_dict(), merge=True)
        logger.debug(f"Saved bot metadata for {self.bot_name}")

    async def get_bot_metadata(self) -> Optional[BotPresence]:
        """Get bot metadata."""
        db = await self._get_db()
        ref = self._bot_ref(db)
        doc = await ref.get()
        if not doc.exists:
            return None
        return BotPresence.from_dict(doc.to_dict())

    async def save_guild(self, guild: GuildPresence) -> None:
        """Save/update a guild."""
        db = await self._get_db()
        ref = self._bot_ref(db).collection("guilds").document(guild.guild_id)
        await ref.set(guild.to_dict(), merge=True)
        logger.debug(f"Saved guild {guild.name} ({guild.guild_id})")

    async def get_guild(self, guild_id: str) -> Optional[GuildPresence]:
        """Get a guild by ID."""
        db = await self._get_db()
        ref = self._bot_ref(db).collection("guilds").document(guild_id)
        doc = await ref.get()
        if not doc.exists:
            return None
        return GuildPresence.from_dict(doc.to_dict())

    async def get_all_guilds(self, include_left: bool = False) -> list[GuildPresence]:
        """Get all guilds for this bot."""
        db = await self._get_db()
        collection = self._bot_ref(db).collection("guilds")
        docs = collection.stream()
        guilds = []
        async for doc in docs:
            guild = GuildPresence.from_dict(doc.to_dict())
            if include_left or guild.is_active:
                guilds.append(guild)
        return guilds

    async def mark_guild_left(self, guild_id: str) -> None:
        """Mark a guild as left (bot was removed)."""
        db = await self._get_db()
        ref = self._bot_ref(db).collection("guilds").document(guild_id)
        await ref.update({"left_at": datetime.now(timezone.utc).isoformat()})
        logger.info(f"Marked guild {guild_id} as left")

    async def save_channel(self, channel: ChannelPresence) -> None:
        """Save/update a channel."""
        db = await self._get_db()
        ref = (
            self._bot_ref(db)
            .collection("guilds").document(channel.guild_id)
            .collection("channels").document(channel.channel_id)
        )
        await ref.set(channel.to_dict(), merge=True)

    async def get_channels(self, guild_id: str) -> list[ChannelPresence]:
        """Get all channels for a guild."""
        db = await self._get_db()
        collection = (
            self._bot_ref(db)
            .collection("guilds").document(guild_id)
            .collection("channels")
        )
        docs = collection.stream()
        channels = []
        async for doc in docs:
            channels.append(ChannelPresence.from_dict(doc.to_dict()))
        return channels

    async def delete_channel(self, guild_id: str, channel_id: str) -> None:
        """Delete a channel (when it's removed from Discord)."""
        db = await self._get_db()
        ref = (
            self._bot_ref(db)
            .collection("guilds").document(guild_id)
            .collection("channels").document(channel_id)
        )
        await ref.delete()

    async def sync_from_discord(
        self,
        app_id: str,
        username: str,
        guilds: list[dict],
    ) -> BotPresence:
        """
        Sync presence data from Discord client state.

        Args:
            app_id: Discord application ID
            username: Bot username (e.g., K3V|N#0060)
            guilds: List of guild data dicts from Discord adapter

        Returns:
            Updated BotPresence
        """
        now = datetime.now(timezone.utc)
        total_channels = 0

        # Get existing guilds to detect leaves
        existing_guilds = {g.guild_id for g in await self.get_all_guilds()}
        seen_guilds = set()

        # Process each guild
        for guild_data in guilds:
            guild_id = guild_data["guild_id"]
            seen_guilds.add(guild_id)

            # Check if this is a rejoin
            existing = await self.get_guild(guild_id)
            joined_at = existing.joined_at if existing else now

            guild = GuildPresence(
                guild_id=guild_id,
                name=guild_data["name"],
                icon_url=guild_data.get("icon_url"),
                member_count=guild_data.get("member_count", 0),
                owner_id=guild_data.get("owner_id"),
                bot_permissions=guild_data.get("bot_permissions", 0),
                joined_at=joined_at,
                last_seen=now,
                left_at=None,  # Clear left_at on rejoin
            )
            await self.save_guild(guild)

            # Process channels
            for ch_data in guild_data.get("channels", []):
                channel = ChannelPresence(
                    channel_id=ch_data["channel_id"],
                    guild_id=guild_id,
                    name=ch_data["name"],
                    type=ChannelType(ch_data.get("type", "text")),
                    position=ch_data.get("position", 0),
                    category_id=ch_data.get("category_id"),
                    category_name=ch_data.get("category_name"),
                    can_read=ch_data.get("can_read", False),
                    can_send=ch_data.get("can_send", False),
                    can_manage=ch_data.get("can_manage", False),
                    can_add_reactions=ch_data.get("can_add_reactions", False),
                    last_indexed=now,
                )
                await self.save_channel(channel)
                total_channels += 1

        # Mark guilds we're no longer in
        left_guilds = existing_guilds - seen_guilds
        for guild_id in left_guilds:
            await self.mark_guild_left(guild_id)
            logger.info(f"Bot no longer in guild {guild_id}")

        # Update bot metadata
        bot = BotPresence(
            bot_name=self.bot_name,
            app_id=app_id,
            username=username,
            last_sync=now,
            total_guilds=len(guilds),
            total_channels=total_channels,
        )
        await self.save_bot_metadata(bot)

        logger.info(
            f"Synced presence for {self.bot_name}: "
            f"{len(guilds)} guilds, {total_channels} channels"
        )
        return bot
