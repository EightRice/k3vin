from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass


@dataclass
class UserData:
    """Persisted user data."""
    user_id: str
    platform: str
    api_key_hash: Optional[str] = None  # Hashed, never store plaintext
    tier: str = "free"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ChannelConfig:
    """Per-channel bot configuration."""
    channel_id: str
    platform: str
    enabled: bool = True
    trigger_prefix: Optional[str] = None  # e.g., "!" or "@bot"


class Store(ABC):
    """Abstract persistence layer."""

    @abstractmethod
    async def get_user(self, user_id: str, platform: str) -> Optional[UserData]:
        """Get user data by ID and platform."""
        pass

    @abstractmethod
    async def save_user(self, user: UserData) -> None:
        """Save or update user data."""
        pass

    @abstractmethod
    async def user_has_api_key(self, user_id: str, platform: str) -> bool:
        """Quick check if user has registered an API key."""
        pass

    @abstractmethod
    async def get_channel_config(self, channel_id: str, platform: str) -> Optional[ChannelConfig]:
        """Get channel configuration."""
        pass

    @abstractmethod
    async def save_channel_config(self, config: ChannelConfig) -> None:
        """Save channel configuration."""
        pass

    # Convenience method for auth context enrichment
    async def enrich_context(self, context: "Context") -> "Context":
        """Add persistence data to context."""
        from core.types import Context

        user_id = context.user.id
        platform = context.platform.value

        context.user_has_api_key = await self.user_has_api_key(user_id, platform)

        user_data = await self.get_user(user_id, platform)
        if user_data:
            context.user_tier = user_data.tier

        return context
