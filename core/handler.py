from abc import ABC, abstractmethod
from typing import Optional, Callable, Awaitable

from .types import Message, Context
from .auth import AuthProvider


class MessageHandler(ABC):
    """Abstract message handler - implement your bot logic here."""

    def __init__(self, auth_provider: AuthProvider):
        self.auth = auth_provider

    @abstractmethod
    async def handle(self, message: Message, context: Context) -> Optional[str]:
        """
        Process a message and return optional response.

        Returns:
            Response text to send, or None to not respond.
        """
        pass

    async def process(self, message: Message, context: Context) -> Optional[str]:
        """
        Main entry point - checks auth then delegates to handle().
        Override this if you need custom pre/post processing.
        """
        # Skip bot messages
        if message.author.is_bot:
            return None

        # Check authorization
        if not self.auth.is_authorized(context):
            return None  # Silent ignore for unauthorized users

        return await self.handle(message, context)


class EchoHandler(MessageHandler):
    """Simple echo handler for testing."""

    async def handle(self, message: Message, context: Context) -> Optional[str]:
        # Only respond to messages starting with !echo
        if message.content.startswith("!echo "):
            return message.content[6:]

        # Respond to !ping
        if message.content == "!ping":
            return f"pong! (authorized via: {self.auth.get_auth_reason(context)})"

        return None
