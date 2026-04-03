"""
Support service handler.

Example of a credit-based service where users can get help.
Demonstrates the user/service access model.
"""
import logging
from typing import Optional
from datetime import datetime, timezone

from persistence.user_repository import UserRepository
from persistence.users import Platform

logger = logging.getLogger(__name__)

# The service ID for support
SERVICE_ID = "support"


class SupportHandler:
    """
    Handles !support commands.

    Users need access to the "support" service to use this.
    Each support interaction costs credits (configurable).

    Tiers:
        - admin: Can manage support tickets, grant access
        - agent: Can respond to tickets
        - user: Can create tickets (costs credits)
    """

    def __init__(self, users: UserRepository, super_admins: set[str] = None):
        self.users = users
        self.super_admins = super_admins or set()

    async def handle(self, user_id: str, username: str, args: str) -> str:
        """
        Handle support subcommand.

        Args:
            user_id: The platform user ID
            username: Display name for logging
            args: The part after "!support " (e.g., "help", "ticket Hello")

        Returns:
            Response message
        """
        args = args.strip()

        # Ensure user exists
        user = await self.users.get_or_create_user(
            user_id=user_id,
            platform=Platform.DISCORD,
            username=username,
        )

        # No args - show help/status
        if not args:
            return await self._status(user_id)

        # Parse subcommand
        parts = args.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""

        # Route to handler
        if cmd == "status":
            return await self._status(user_id)
        elif cmd == "ask":
            return await self._ask(user_id, cmd_args)
        elif cmd == "grant":
            return await self._grant(user_id, cmd_args)
        elif cmd == "revoke":
            return await self._revoke(user_id, cmd_args)
        elif cmd == "credits":
            return await self._credits(user_id, cmd_args)
        elif cmd == "bootstrap":
            return await self._bootstrap(user_id, cmd_args)
        elif cmd == "help":
            return self._help()
        else:
            return f"Unknown command: `{cmd}`. Try `!support help`"

    def _help(self) -> str:
        """Show help text."""
        return """**Support Service**

**User Commands:**
`!support` - Show your support status
`!support ask <question>` - Ask a support question (costs 1 credit)

**Admin Commands:**
`!support grant <user_id> [tier]` - Grant support access
`!support revoke <user_id>` - Revoke support access
`!support credits <user_id> <amount>` - Add credits to user
`!support bootstrap` - Self-grant admin (super_admins only)"""

    async def _status(self, user_id: str) -> str:
        """Show user's support access status."""
        user = await self.users.get_user(user_id)
        if not user:
            return "You don't have an account yet. Use `!support ask` to get started."

        has_access, tier_or_reason = await self.users.check_access(user_id, SERVICE_ID)

        if has_access:
            access = await self.users.get_access(user_id, SERVICE_ID)
            lines = ["**Support Status**"]
            lines.append(f"• Access: {tier_or_reason}")
            lines.append(f"• Credits: {user.credits:.1f}")
            lines.append(f"• Questions asked: {access.usage_count}")
            if access.usage_limit:
                lines.append(f"• Remaining: {access.remaining_uses}")
            if access.expires_at:
                lines.append(f"• Expires: {access.expires_at.strftime('%Y-%m-%d')}")
            return "\n".join(lines)
        else:
            return f"""**Support Status**
• Access: None ({tier_or_reason})
• Credits: {user.credits:.1f}

You need support access to ask questions. Contact an admin."""

    async def _ask(self, user_id: str, question: str) -> str:
        """Ask a support question (costs credits)."""
        if not question:
            return "Usage: `!support ask <your question>`"

        # Check access
        has_access, reason = await self.users.check_access(user_id, SERVICE_ID)
        if not has_access:
            if reason == "no_access":
                return "You don't have support access. Contact an admin to get started."
            elif reason == "expired":
                return "Your support access has expired. Contact an admin to renew."
            elif reason == "usage_limit_exceeded":
                return "You've used all your support credits. Contact an admin for more."
            else:
                return f"Access denied: {reason}"

        # Get service to check credit cost
        service = await self.users.get_service(SERVICE_ID)
        credit_cost = service.credit_cost if service else 1.0

        # Check if user has enough credits (admins/agents are free)
        access = await self.users.get_access(user_id, SERVICE_ID)
        if access.tier not in ["admin", "agent"]:
            user = await self.users.get_user(user_id)
            if user.credits < credit_cost:
                return f"Insufficient credits. You have {user.credits:.1f}, need {credit_cost:.1f}."

            # Deduct credits
            await self.users.deduct_credits(user_id, credit_cost, reason="support question")

        # Increment usage
        usage_count = await self.users.increment_usage(user_id, SERVICE_ID)

        # In a real implementation, this would:
        # - Create a ticket
        # - Notify agents
        # - Maybe use AI to provide initial response

        logger.info(f"Support question from {user_id}: {question[:100]}")

        return f"""**Support Ticket #{usage_count}**

Your question has been received:
> {question}

A support agent will respond soon. You can ask follow-up questions.
{'(This question cost 1 credit)' if access.tier not in ['admin', 'agent'] else '(Free - staff tier)'}"""

    async def _grant(self, user_id: str, args: str) -> str:
        """Grant support access to a user. Admin only."""
        # Check if requester is admin
        has_access, tier = await self.users.check_access(user_id, SERVICE_ID, min_tier="admin")
        if not has_access or tier != "admin":
            return "Only support admins can grant access."

        parts = args.split()
        if not parts:
            return "Usage: `!support grant <user_id> [tier]`\nTiers: admin, agent, user"

        target_user_id = parts[0]
        tier = parts[1] if len(parts) > 1 else "user"

        if tier not in ["admin", "agent", "user"]:
            return f"Invalid tier: {tier}. Use: admin, agent, user"

        # Ensure target user exists
        await self.users.get_or_create_user(target_user_id)

        # Grant access
        await self.users.grant_access(
            user_id=target_user_id,
            service_id=SERVICE_ID,
            tier=tier,
            granted_by=user_id,
        )

        return f"Granted `{tier}` support access to user `{target_user_id}`"

    async def _revoke(self, user_id: str, args: str) -> str:
        """Revoke support access from a user. Admin only."""
        has_access, tier = await self.users.check_access(user_id, SERVICE_ID, min_tier="admin")
        if not has_access or tier != "admin":
            return "Only support admins can revoke access."

        target_user_id = args.strip()
        if not target_user_id:
            return "Usage: `!support revoke <user_id>`"

        revoked = await self.users.revoke_access(target_user_id, SERVICE_ID)
        if revoked:
            return f"Revoked support access from user `{target_user_id}`"
        else:
            return f"User `{target_user_id}` didn't have support access"

    async def _credits(self, user_id: str, args: str) -> str:
        """Add credits to a user. Admin only."""
        has_access, tier = await self.users.check_access(user_id, SERVICE_ID, min_tier="admin")
        if not has_access or tier != "admin":
            return "Only support admins can manage credits."

        parts = args.split()
        if len(parts) < 2:
            return "Usage: `!support credits <user_id> <amount>`"

        target_user_id = parts[0]
        try:
            amount = float(parts[1])
        except ValueError:
            return f"Invalid amount: {parts[1]}"

        # Ensure target user exists
        await self.users.get_or_create_user(target_user_id)

        new_balance = await self.users.add_credits(target_user_id, amount, reason=f"granted by {user_id}")
        return f"Added {amount:.1f} credits to user `{target_user_id}`. New balance: {new_balance:.1f}"

    async def _bootstrap(self, user_id: str, args: str) -> str:
        """Bootstrap self as support admin. Super_admins only."""
        if user_id not in self.super_admins:
            return "Only super_admins can use bootstrap."

        # Check if already has admin access
        has_access, tier = await self.users.check_access(user_id, SERVICE_ID)
        if has_access and tier == "admin":
            return "You already have support admin access."

        # Grant admin access
        await self.users.grant_access(
            user_id=user_id,
            service_id=SERVICE_ID,
            tier="admin",
            granted_by="bootstrap",
        )

        return "Bootstrapped support admin access for yourself."
