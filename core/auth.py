from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from .types import User, Context


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for platform-specific authorization."""

    def is_authorized(self, context: Context) -> bool:
        """Check if user is authorized to use the bot in this context."""
        ...

    def get_auth_reason(self, context: Context) -> str:
        """Get human-readable reason for auth decision."""
        ...


class BaseAuthProvider(ABC):
    """Base implementation with common auth logic."""

    @abstractmethod
    def is_platform_authorized(self, context: Context) -> bool:
        """Platform-specific authorization check."""
        pass

    def is_authorized(self, context: Context) -> bool:
        """
        Check authorization. Order:
        1. Platform-attested admin/owner -> always allowed
        2. User has registered API key -> allowed
        3. Platform-specific checks (roles, etc.)
        """
        user = context.user

        # Platform-attested authority
        if user.is_owner or user.is_admin:
            return True

        # User brought their own API key
        if context.user_has_api_key:
            return True

        # Delegate to platform-specific logic
        return self.is_platform_authorized(context)

    def get_auth_reason(self, context: Context) -> str:
        user = context.user

        if user.is_owner:
            return "server_owner"
        if user.is_admin:
            return "administrator"
        if context.user_has_api_key:
            return "has_api_key"
        if self.is_platform_authorized(context):
            return "platform_authorized"

        return "unauthorized"
