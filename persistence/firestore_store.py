import os
from typing import Optional
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore import AsyncClient

from .store import Store, UserData, ChannelConfig


class FirestoreStore(Store):
    """Firestore implementation of persistence layer."""

    def __init__(self, use_emulator: bool = False):
        self.use_emulator = use_emulator
        self._db: Optional[AsyncClient] = None
        self._initialized = False

    async def _init(self):
        """Lazy initialization of Firestore client."""
        if self._initialized:
            return

        if self.use_emulator:
            # Point to local emulator
            os.environ["FIRESTORE_EMULATOR_HOST"] = "localhost:8080"
            # Use a demo project for emulator
            if not firebase_admin._apps:
                firebase_admin.initialize_app(options={"projectId": "demo-kevin"})
        else:
            # Production - use service account from env
            cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if cred_path and not firebase_admin._apps:
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
            elif not firebase_admin._apps:
                # Default credentials (e.g., on GCP)
                firebase_admin.initialize_app()

        self._db = firestore.client()
        self._initialized = True

    def _user_doc_id(self, user_id: str, platform: str) -> str:
        return f"{platform}_{user_id}"

    def _channel_doc_id(self, channel_id: str, platform: str) -> str:
        return f"{platform}_{channel_id}"

    async def get_user(self, user_id: str, platform: str) -> Optional[UserData]:
        await self._init()

        doc_id = self._user_doc_id(user_id, platform)
        doc = self._db.collection("users").document(doc_id).get()

        if not doc.exists:
            return None

        data = doc.to_dict()
        return UserData(
            user_id=data.get("user_id", user_id),
            platform=data.get("platform", platform),
            api_key_hash=data.get("api_key_hash"),
            tier=data.get("tier", "free"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    async def save_user(self, user: UserData) -> None:
        await self._init()

        doc_id = self._user_doc_id(user.user_id, user.platform)
        now = datetime.now(timezone.utc).isoformat()

        doc_ref = self._db.collection("users").document(doc_id)
        existing = doc_ref.get()

        data = {
            "user_id": user.user_id,
            "platform": user.platform,
            "api_key_hash": user.api_key_hash,
            "tier": user.tier,
            "updated_at": now,
        }

        if not existing.exists:
            data["created_at"] = now

        doc_ref.set(data, merge=True)

    async def user_has_api_key(self, user_id: str, platform: str) -> bool:
        user = await self.get_user(user_id, platform)
        return user is not None and user.api_key_hash is not None

    async def get_channel_config(self, channel_id: str, platform: str) -> Optional[ChannelConfig]:
        await self._init()

        doc_id = self._channel_doc_id(channel_id, platform)
        doc = self._db.collection("channels").document(doc_id).get()

        if not doc.exists:
            return None

        data = doc.to_dict()
        return ChannelConfig(
            channel_id=data.get("channel_id", channel_id),
            platform=data.get("platform", platform),
            enabled=data.get("enabled", True),
            trigger_prefix=data.get("trigger_prefix"),
        )

    async def save_channel_config(self, config: ChannelConfig) -> None:
        await self._init()

        doc_id = self._channel_doc_id(config.channel_id, config.platform)

        self._db.collection("channels").document(doc_id).set({
            "channel_id": config.channel_id,
            "platform": config.platform,
            "enabled": config.enabled,
            "trigger_prefix": config.trigger_prefix,
        }, merge=True)
