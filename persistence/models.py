"""
Data models for bot presence and configuration.

These models capture the bot's view of Discord - what servers it's in,
what channels it can see, and what permissions it has.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class ChannelType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    CATEGORY = "category"
    NEWS = "news"
    STAGE = "stage"
    FORUM = "forum"
    THREAD = "thread"
    UNKNOWN = "unknown"


@dataclass
class ChannelPresence:
    """Bot's view of a channel."""
    channel_id: str
    guild_id: str
    name: str
    type: ChannelType = ChannelType.TEXT
    position: int = 0
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    # Bot permissions in this channel
    can_read: bool = False
    can_send: bool = False
    can_manage: bool = False
    can_add_reactions: bool = False
    # Timestamps
    last_indexed: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "name": self.name,
            "type": self.type.value if isinstance(self.type, ChannelType) else self.type,
            "position": self.position,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "can_read": self.can_read,
            "can_send": self.can_send,
            "can_manage": self.can_manage,
            "can_add_reactions": self.can_add_reactions,
            "last_indexed": self.last_indexed.isoformat() if self.last_indexed else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChannelPresence":
        last_indexed = data.get("last_indexed")
        if last_indexed and isinstance(last_indexed, str):
            last_indexed = datetime.fromisoformat(last_indexed)

        channel_type = data.get("type", "text")
        if isinstance(channel_type, str):
            try:
                channel_type = ChannelType(channel_type)
            except ValueError:
                channel_type = ChannelType.UNKNOWN

        return cls(
            channel_id=data["channel_id"],
            guild_id=data["guild_id"],
            name=data["name"],
            type=channel_type,
            position=data.get("position", 0),
            category_id=data.get("category_id"),
            category_name=data.get("category_name"),
            can_read=data.get("can_read", False),
            can_send=data.get("can_send", False),
            can_manage=data.get("can_manage", False),
            can_add_reactions=data.get("can_add_reactions", False),
            last_indexed=last_indexed,
        )


@dataclass
class GuildPresence:
    """Bot's view of a guild/server."""
    guild_id: str
    name: str
    icon_url: Optional[str] = None
    member_count: int = 0
    owner_id: Optional[str] = None
    # Bot's permissions integer at guild level
    bot_permissions: int = 0
    # Timestamps
    joined_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    left_at: Optional[datetime] = None  # Set when bot leaves, cleared on rejoin
    # Channels (populated separately)
    channels: list[ChannelPresence] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """True if bot is currently in this guild."""
        return self.left_at is None

    def to_dict(self, include_channels: bool = False) -> dict:
        data = {
            "guild_id": self.guild_id,
            "name": self.name,
            "icon_url": self.icon_url,
            "member_count": self.member_count,
            "owner_id": self.owner_id,
            "bot_permissions": self.bot_permissions,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "left_at": self.left_at.isoformat() if self.left_at else None,
        }
        if include_channels:
            data["channels"] = [c.to_dict() for c in self.channels]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "GuildPresence":
        def parse_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        return cls(
            guild_id=data["guild_id"],
            name=data["name"],
            icon_url=data.get("icon_url"),
            member_count=data.get("member_count", 0),
            owner_id=data.get("owner_id"),
            bot_permissions=data.get("bot_permissions", 0),
            joined_at=parse_dt(data.get("joined_at")),
            last_seen=parse_dt(data.get("last_seen")),
            left_at=parse_dt(data.get("left_at")),
        )


@dataclass
class BotPresence:
    """Top-level bot presence metadata."""
    bot_name: str  # kevin, auto, tuna
    app_id: str
    username: str  # K3V|N#0060, etc.
    last_sync: Optional[datetime] = None
    total_guilds: int = 0
    total_channels: int = 0
    # Guilds (populated separately)
    guilds: list[GuildPresence] = field(default_factory=list)

    def to_dict(self, include_guilds: bool = False) -> dict:
        data = {
            "bot_name": self.bot_name,
            "app_id": self.app_id,
            "username": self.username,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "total_guilds": self.total_guilds,
            "total_channels": self.total_channels,
        }
        if include_guilds:
            data["guilds"] = [g.to_dict(include_channels=True) for g in self.guilds]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BotPresence":
        last_sync = data.get("last_sync")
        if last_sync and isinstance(last_sync, str):
            last_sync = datetime.fromisoformat(last_sync)

        return cls(
            bot_name=data["bot_name"],
            app_id=data["app_id"],
            username=data["username"],
            last_sync=last_sync,
            total_guilds=data.get("total_guilds", 0),
            total_channels=data.get("total_channels", 0),
        )
