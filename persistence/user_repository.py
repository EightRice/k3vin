"""
User repository for managing bot users and their service access.
"""
import os
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_document import AsyncDocumentReference

from .users import BotUser, Service, ServiceAccess, Platform, BUILTIN_SERVICES

logger = logging.getLogger(__name__)


class UserRepository:
    """
    Async Firestore repository for bot users and services.

    Structure:
        bots/{bot_name}/
            users/{user_id}/
                user doc: {platform, username, credits, ...}
                access/{service_id}/
                    access doc: {tier, granted_at, usage_count, ...}
            services/{service_id}/
                service doc: {name, description, tiers, ...}
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
            self._db = AsyncClient(project="demo-kevin")
        else:
            self._db = AsyncClient()

        return self._db

    def _bot_ref(self, db: AsyncClient) -> AsyncDocumentReference:
        """Get reference to bot's document."""
        return db.collection("bots").document(self.bot_name)

    # ========== User Methods ==========

    async def get_user(self, user_id: str) -> Optional[BotUser]:
        """Get a user by ID, including their service access."""
        db = await self._get_db()
        ref = self._bot_ref(db).collection("users").document(user_id)
        doc = await ref.get()

        if not doc.exists:
            return None

        user = BotUser.from_dict(doc.to_dict())

        # Load service access
        access_collection = ref.collection("access")
        async for access_doc in access_collection.stream():
            access_data = access_doc.to_dict()
            access_data["user_id"] = user_id
            access_data["service_id"] = access_doc.id
            user.access[access_doc.id] = ServiceAccess.from_dict(access_data)

        return user

    async def get_or_create_user(
        self,
        user_id: str,
        platform: Platform = Platform.DISCORD,
        username: str = None,
        display_name: str = None,
    ) -> BotUser:
        """Get existing user or create a new one."""
        user = await self.get_user(user_id)
        if user:
            # Update last_seen
            user.last_seen = datetime.now(timezone.utc)
            await self.save_user(user)
            return user

        # Create new user
        now = datetime.now(timezone.utc)
        user = BotUser(
            user_id=user_id,
            platform=platform,
            username=username,
            display_name=display_name,
            credits=0.0,
            created_at=now,
            updated_at=now,
            last_seen=now,
        )
        await self.save_user(user)
        logger.info(f"Created new user: {user_id} ({username})")
        return user

    async def save_user(self, user: BotUser) -> None:
        """Save/update a user."""
        db = await self._get_db()
        user.updated_at = datetime.now(timezone.utc)
        ref = self._bot_ref(db).collection("users").document(user.user_id)
        await ref.set(user.to_dict(), merge=True)

    async def get_all_users(self, limit: int = 100) -> list[BotUser]:
        """Get all users (with optional limit)."""
        db = await self._get_db()
        collection = self._bot_ref(db).collection("users")
        query = collection.limit(limit)
        users = []
        async for doc in query.stream():
            user = BotUser.from_dict(doc.to_dict())
            users.append(user)
        return users

    async def add_credits(self, user_id: str, amount: float, reason: str = None) -> float:
        """Add credits to a user. Returns new balance."""
        user = await self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        user.credits += amount
        await self.save_user(user)
        logger.info(f"Added {amount} credits to {user_id} (reason: {reason}). New balance: {user.credits}")
        return user.credits

    async def deduct_credits(self, user_id: str, amount: float, reason: str = None) -> float:
        """Deduct credits from a user. Returns new balance. Raises if insufficient."""
        user = await self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        if user.credits < amount:
            raise ValueError(f"Insufficient credits: {user.credits} < {amount}")

        user.credits -= amount
        await self.save_user(user)
        logger.info(f"Deducted {amount} credits from {user_id} (reason: {reason}). New balance: {user.credits}")
        return user.credits

    # ========== Service Access Methods ==========

    async def grant_access(
        self,
        user_id: str,
        service_id: str,
        tier: str = "user",
        granted_by: str = None,
        expires_at: datetime = None,
        usage_limit: int = None,
    ) -> ServiceAccess:
        """Grant a user access to a service."""
        db = await self._get_db()
        now = datetime.now(timezone.utc)

        access = ServiceAccess(
            service_id=service_id,
            user_id=user_id,
            tier=tier,
            granted_at=now,
            granted_by=granted_by,
            expires_at=expires_at,
            usage_limit=usage_limit,
        )

        ref = (
            self._bot_ref(db)
            .collection("users").document(user_id)
            .collection("access").document(service_id)
        )
        await ref.set(access.to_dict())
        logger.info(f"Granted {tier} access to {service_id} for user {user_id}")
        return access

    async def revoke_access(self, user_id: str, service_id: str) -> bool:
        """Revoke a user's access to a service."""
        db = await self._get_db()
        ref = (
            self._bot_ref(db)
            .collection("users").document(user_id)
            .collection("access").document(service_id)
        )
        doc = await ref.get()
        if not doc.exists:
            return False

        await ref.delete()
        logger.info(f"Revoked access to {service_id} for user {user_id}")
        return True

    async def get_access(self, user_id: str, service_id: str) -> Optional[ServiceAccess]:
        """Get a user's access to a specific service."""
        db = await self._get_db()
        ref = (
            self._bot_ref(db)
            .collection("users").document(user_id)
            .collection("access").document(service_id)
        )
        doc = await ref.get()
        if not doc.exists:
            return None

        data = doc.to_dict()
        data["user_id"] = user_id
        data["service_id"] = service_id
        return ServiceAccess.from_dict(data)

    async def check_access(
        self,
        user_id: str,
        service_id: str,
        min_tier: str = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if user has valid access to a service.
        Returns (has_access, tier_or_reason).
        """
        access = await self.get_access(user_id, service_id)
        if not access:
            return False, "no_access"
        if access.is_expired:
            return False, "expired"
        if access.usage_limit is not None and access.usage_count >= access.usage_limit:
            return False, "usage_limit_exceeded"

        if min_tier:
            tier_order = ["trial", "basic", "user", "agent", "admin"]
            try:
                user_level = tier_order.index(access.tier)
                required_level = tier_order.index(min_tier)
                if user_level < required_level:
                    return False, f"tier_too_low:{access.tier}"
            except ValueError:
                if access.tier != min_tier:
                    return False, f"tier_mismatch:{access.tier}"

        return True, access.tier

    async def increment_usage(self, user_id: str, service_id: str) -> int:
        """Increment usage count for a service. Returns new count."""
        db = await self._get_db()
        ref = (
            self._bot_ref(db)
            .collection("users").document(user_id)
            .collection("access").document(service_id)
        )

        doc = await ref.get()
        if not doc.exists:
            raise ValueError(f"No access record for {user_id}/{service_id}")

        data = doc.to_dict()
        new_count = data.get("usage_count", 0) + 1
        await ref.update({"usage_count": new_count})
        return new_count

    # ========== Service Definition Methods ==========

    async def get_service(self, service_id: str) -> Optional[Service]:
        """Get a service definition."""
        # Check built-in services first
        if service_id in BUILTIN_SERVICES:
            return BUILTIN_SERVICES[service_id]

        db = await self._get_db()
        ref = self._bot_ref(db).collection("services").document(service_id)
        doc = await ref.get()
        if not doc.exists:
            return None
        return Service.from_dict(doc.to_dict())

    async def save_service(self, service: Service) -> None:
        """Save/update a service definition."""
        db = await self._get_db()
        ref = self._bot_ref(db).collection("services").document(service.service_id)
        await ref.set(service.to_dict())

    async def get_all_services(self) -> list[Service]:
        """Get all services (built-in + custom)."""
        services = list(BUILTIN_SERVICES.values())

        db = await self._get_db()
        collection = self._bot_ref(db).collection("services")
        async for doc in collection.stream():
            if doc.id not in BUILTIN_SERVICES:
                services.append(Service.from_dict(doc.to_dict()))

        return services

    # ========== Convenience Methods ==========

    async def get_users_with_access(self, service_id: str) -> list[tuple[BotUser, ServiceAccess]]:
        """Get all users who have access to a service."""
        db = await self._get_db()
        users_collection = self._bot_ref(db).collection("users")
        results = []

        async for user_doc in users_collection.stream():
            user_id = user_doc.id
            access_ref = user_doc.reference.collection("access").document(service_id)
            access_doc = await access_ref.get()
            if access_doc.exists:
                user = BotUser.from_dict(user_doc.to_dict())
                access_data = access_doc.to_dict()
                access_data["user_id"] = user_id
                access_data["service_id"] = service_id
                access = ServiceAccess.from_dict(access_data)
                if access.is_valid:
                    results.append((user, access))

        return results

    async def ensure_builtin_services(self) -> None:
        """Ensure built-in service definitions exist in Firestore."""
        for service in BUILTIN_SERVICES.values():
            await self.save_service(service)
        logger.info(f"Ensured {len(BUILTIN_SERVICES)} built-in services exist")
