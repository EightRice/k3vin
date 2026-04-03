from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Platform(Enum):
    DISCORD = "discord"
    TELEGRAM = "telegram"


@dataclass
class User:
    """Platform-agnostic user representation."""
    id: str
    username: str
    display_name: Optional[str] = None
    is_bot: bool = False
    platform: Platform = Platform.DISCORD

    # Platform-attested permissions (populated by adapter)
    is_owner: bool = False
    is_admin: bool = False
    roles: list[str] = None

    def __post_init__(self):
        if self.roles is None:
            self.roles = []


@dataclass
class Channel:
    """Platform-agnostic channel/chat representation."""
    id: str
    name: str
    platform: Platform = Platform.DISCORD

    # Container info (guild in Discord, group in Telegram)
    container_id: Optional[str] = None
    container_name: Optional[str] = None

    is_dm: bool = False


@dataclass
class Message:
    """Platform-agnostic message representation."""
    id: str
    content: str
    author: User
    channel: Channel
    timestamp: datetime
    platform: Platform = Platform.DISCORD

    # For replies
    reply_to_id: Optional[str] = None

    # Raw platform object for adapter-specific operations
    _raw: Optional[object] = None


@dataclass
class Context:
    """Context for authorization and handling decisions."""
    user: User
    channel: Channel
    platform: Platform

    # Populated by persistence layer
    user_has_api_key: bool = False
    user_tier: Optional[str] = None  # "free", "pro", etc.
