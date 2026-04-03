from datetime import datetime, timezone
from typing import Optional

import discord

from core.types import User, Message, Channel, Context, Platform


class DiscordAdapter:
    """Converts Discord objects to platform-agnostic types."""

    @staticmethod
    def adapt_user(member: discord.Member | discord.User, guild: Optional[discord.Guild] = None) -> User:
        """Convert Discord member/user to core User type."""
        is_owner = False
        is_admin = False
        roles = []

        if guild and isinstance(member, discord.Member):
            is_owner = guild.owner_id == member.id
            is_admin = member.guild_permissions.administrator
            roles = [role.name for role in member.roles if role.name != "@everyone"]

        return User(
            id=str(member.id),
            username=member.name,
            display_name=member.display_name,
            is_bot=member.bot,
            platform=Platform.DISCORD,
            is_owner=is_owner,
            is_admin=is_admin,
            roles=roles,
        )

    @staticmethod
    def adapt_channel(channel: discord.abc.Messageable, guild: Optional[discord.Guild] = None) -> Channel:
        """Convert Discord channel to core Channel type."""
        is_dm = isinstance(channel, discord.DMChannel)

        container_id = None
        container_name = None
        channel_name = "DM"

        if guild:
            container_id = str(guild.id)
            container_name = guild.name

        if hasattr(channel, "name") and channel.name:
            channel_name = channel.name

        return Channel(
            id=str(channel.id),
            name=channel_name,
            platform=Platform.DISCORD,
            container_id=container_id,
            container_name=container_name,
            is_dm=is_dm,
        )

    @staticmethod
    def adapt_message(message: discord.Message) -> Message:
        """Convert Discord message to core Message type."""
        guild = message.guild

        user = DiscordAdapter.adapt_user(message.author, guild)
        channel = DiscordAdapter.adapt_channel(message.channel, guild)

        reply_to_id = None
        if message.reference and message.reference.message_id:
            reply_to_id = str(message.reference.message_id)

        return Message(
            id=str(message.id),
            content=message.content,
            author=user,
            channel=channel,
            timestamp=message.created_at.replace(tzinfo=timezone.utc),
            platform=Platform.DISCORD,
            reply_to_id=reply_to_id,
            _raw=message,
        )

    @staticmethod
    def create_context(message: Message) -> Context:
        """Create auth context from message."""
        return Context(
            user=message.author,
            channel=message.channel,
            platform=message.platform,
        )

    @staticmethod
    def extract_presence_data(client: discord.Client) -> list[dict]:
        """
        Extract guild/channel presence data from Discord client.

        This reads from discord.py's internal cache (populated on connect)
        and does NOT make any additional API calls.

        Returns:
            List of guild dicts with nested channel data
        """
        guilds_data = []

        for guild in client.guilds:
            # Get bot's member object in this guild
            bot_member = guild.me

            # Extract channel data
            channels_data = []
            for channel in guild.channels:
                # Determine channel type
                if isinstance(channel, discord.TextChannel):
                    ch_type = "text"
                elif isinstance(channel, discord.VoiceChannel):
                    ch_type = "voice"
                elif isinstance(channel, discord.CategoryChannel):
                    ch_type = "category"
                elif isinstance(channel, discord.StageChannel):
                    ch_type = "stage"
                elif isinstance(channel, discord.ForumChannel):
                    ch_type = "forum"
                else:
                    ch_type = "unknown"

                # Get bot's permissions in this channel
                perms = channel.permissions_for(bot_member) if bot_member else None

                # Get category info
                category_id = None
                category_name = None
                if hasattr(channel, "category") and channel.category:
                    category_id = str(channel.category.id)
                    category_name = channel.category.name

                channels_data.append({
                    "channel_id": str(channel.id),
                    "name": channel.name,
                    "type": ch_type,
                    "position": channel.position,
                    "category_id": category_id,
                    "category_name": category_name,
                    "can_read": perms.read_messages if perms else False,
                    "can_send": perms.send_messages if perms else False,
                    "can_manage": perms.manage_channels if perms else False,
                    "can_add_reactions": perms.add_reactions if perms else False,
                })

            # Guild-level data
            guilds_data.append({
                "guild_id": str(guild.id),
                "name": guild.name,
                "icon_url": str(guild.icon.url) if guild.icon else None,
                "member_count": guild.member_count or 0,
                "owner_id": str(guild.owner_id) if guild.owner_id else None,
                "bot_permissions": bot_member.guild_permissions.value if bot_member else 0,
                "channels": channels_data,
            })

        return guilds_data
