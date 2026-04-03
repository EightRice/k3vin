from core.auth import BaseAuthProvider
from core.types import Context


class DiscordAuthProvider(BaseAuthProvider):
    """Discord-specific authorization using platform attestation."""

    def __init__(
        self,
        authorized_roles: list[str] | None = None,
        super_admins: set[str] | None = None,
    ):
        """
        Args:
            authorized_roles: List of role names that grant authorization.
            super_admins: Set of user IDs with global access (works in DMs).
        """
        self.authorized_roles = authorized_roles or []
        self.super_admins = super_admins or set()

    def is_authorized(self, context: Context) -> bool:
        """
        Check authorization. Super admins first (works in DMs),
        then delegate to base class for guild-based checks.
        """
        # Super admins always authorized (even in DMs)
        if context.user.id in self.super_admins:
            return True

        # Fall back to base class (owner/admin/api_key/roles)
        return super().is_authorized(context)

    def is_platform_authorized(self, context: Context) -> bool:
        """
        Check Discord-specific authorization.
        User is authorized if they have any of the authorized roles.
        """
        if not self.authorized_roles:
            return False

        user_roles = set(context.user.roles)
        authorized = set(self.authorized_roles)

        return bool(user_roles & authorized)

    def get_auth_reason(self, context: Context) -> str:
        """Get the specific reason for authorization."""
        user = context.user

        # Check super admin first
        if user.id in self.super_admins:
            return "super_admin"

        if user.is_owner:
            return "server_owner"
        if user.is_admin:
            return "administrator"
        if context.user_has_api_key:
            return "has_api_key"

        # Check which role authorized them
        if self.authorized_roles:
            user_roles = set(user.roles)
            for role in self.authorized_roles:
                if role in user_roles:
                    return f"role:{role}"

        return "unauthorized"
