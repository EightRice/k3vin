"""
User and service access models.

Users are registered per-bot and can have access to various services
at different tiers. Credits can be used for usage-based billing.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class Platform(str, Enum):
    """Platform where user originated."""
    DISCORD = "discord"
    TELEGRAM = "telegram"
    WEB = "web"
    API = "api"


class AccessTier(str, Enum):
    """Standard access tiers. Services can define custom tiers too."""
    ADMIN = "admin"      # Full access, can manage
    USER = "user"        # Standard access
    BASIC = "basic"      # Limited access
    TRIAL = "trial"      # Time-limited trial


@dataclass
class ServiceAccess:
    """User's access to a specific service."""
    service_id: str
    user_id: str
    tier: str = "basic"
    granted_at: Optional[datetime] = None
    granted_by: Optional[str] = None  # Who granted access
    expires_at: Optional[datetime] = None
    usage_count: int = 0
    usage_limit: Optional[int] = None  # None = unlimited
    metadata: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if access is currently valid."""
        if self.is_expired:
            return False
        if self.usage_limit is not None and self.usage_count >= self.usage_limit:
            return False
        return True

    @property
    def remaining_uses(self) -> Optional[int]:
        if self.usage_limit is None:
            return None
        return max(0, self.usage_limit - self.usage_count)

    def to_dict(self) -> dict:
        return {
            "service_id": self.service_id,
            "user_id": self.user_id,
            "tier": self.tier,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "granted_by": self.granted_by,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "usage_count": self.usage_count,
            "usage_limit": self.usage_limit,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceAccess":
        def parse_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        return cls(
            service_id=data["service_id"],
            user_id=data["user_id"],
            tier=data.get("tier", "basic"),
            granted_at=parse_dt(data.get("granted_at")),
            granted_by=data.get("granted_by"),
            expires_at=parse_dt(data.get("expires_at")),
            usage_count=data.get("usage_count", 0),
            usage_limit=data.get("usage_limit"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class BotUser:
    """A user registered with this bot."""
    user_id: str  # Platform-specific ID (e.g., Discord user ID)
    platform: Platform = Platform.DISCORD
    username: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    # Credits for usage-based access
    credits: float = 0.0
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    # Service access (populated separately)
    access: dict[str, ServiceAccess] = field(default_factory=dict)
    # Arbitrary metadata
    metadata: dict = field(default_factory=dict)

    def has_access(self, service_id: str, min_tier: str = None) -> bool:
        """Check if user has valid access to a service."""
        if service_id not in self.access:
            return False
        access = self.access[service_id]
        if not access.is_valid:
            return False
        if min_tier:
            # Simple tier comparison - admin > user > basic > trial
            tier_order = ["trial", "basic", "user", "admin"]
            try:
                user_level = tier_order.index(access.tier)
                required_level = tier_order.index(min_tier)
                return user_level >= required_level
            except ValueError:
                # Custom tier, just check equality
                return access.tier == min_tier
        return True

    def get_tier(self, service_id: str) -> Optional[str]:
        """Get user's tier for a service, or None if no access."""
        if service_id not in self.access:
            return None
        access = self.access[service_id]
        if not access.is_valid:
            return None
        return access.tier

    def to_dict(self, include_access: bool = False) -> dict:
        data = {
            "user_id": self.user_id,
            "platform": self.platform.value if isinstance(self.platform, Platform) else self.platform,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "credits": self.credits,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "metadata": self.metadata,
        }
        if include_access:
            data["access"] = {k: v.to_dict() for k, v in self.access.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BotUser":
        def parse_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        platform = data.get("platform", "discord")
        if isinstance(platform, str):
            try:
                platform = Platform(platform)
            except ValueError:
                platform = Platform.DISCORD

        return cls(
            user_id=data["user_id"],
            platform=platform,
            username=data.get("username"),
            display_name=data.get("display_name"),
            email=data.get("email"),
            credits=data.get("credits", 0.0),
            created_at=parse_dt(data.get("created_at")),
            updated_at=parse_dt(data.get("updated_at")),
            last_seen=parse_dt(data.get("last_seen")),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Service:
    """A service offered by this bot."""
    service_id: str
    name: str
    description: str = ""
    # Available tiers for this service
    tiers: list[str] = field(default_factory=lambda: ["admin", "user", "basic"])
    default_tier: str = "basic"
    # Credit cost per use (None = free)
    credit_cost: Optional[float] = None
    # Is this service active?
    enabled: bool = True
    # Service-specific config
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "description": self.description,
            "tiers": self.tiers,
            "default_tier": self.default_tier,
            "credit_cost": self.credit_cost,
            "enabled": self.enabled,
            "config": self.config,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Service":
        return cls(
            service_id=data["service_id"],
            name=data["name"],
            description=data.get("description", ""),
            tiers=data.get("tiers", ["admin", "user", "basic"]),
            default_tier=data.get("default_tier", "basic"),
            credit_cost=data.get("credit_cost"),
            enabled=data.get("enabled", True),
            config=data.get("config", {}),
        )


# Pre-defined services (can be extended)
BUILTIN_SERVICES = {
    "engineer": Service(
        service_id="engineer",
        name="Engineer On-Call",
        description="Access to the Engineer on-call automation system",
        tiers=["admin", "user"],
        default_tier="user",
    ),
    "support": Service(
        service_id="support",
        name="Customer Support",
        description="Get help with products and services",
        tiers=["admin", "agent", "user"],
        default_tier="user",
        credit_cost=1.0,  # 1 credit per support interaction
    ),
    "admin": Service(
        service_id="admin",
        name="Bot Administration",
        description="Administrative access to the bot",
        tiers=["admin"],
        default_tier="admin",
    ),
}
