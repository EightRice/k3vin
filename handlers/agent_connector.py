"""
Agent Connector Handler - File-based communication bridge for AI agents.

This handler enables AI agents to communicate through the tuna Discord bot
using a simple file-based protocol:

- Agent reads from: {folder}/inbox/{agent_id}/*.json  (messages from Discord)
- Agent writes to:  {folder}/outbox/*.json (responses to Discord)

Message routing:
- "webdev: fix the CSS" -> routes to inbox/webdev/
- "research: look up X"  -> routes to inbox/research/
- No prefix              -> routes to inbox/general/

Only authorized user IDs can communicate through this connector.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Tuple

from core.handler import MessageHandler
from core.auth import AuthProvider
from core.types import Message, Context

logger = logging.getLogger(__name__)

# Pattern to match agent prefix - requires explicit delimiter:
# "agentname: message" or "@agentname message"
AGENT_PREFIX_PATTERN = re.compile(r'^(?:@(\w+)\s+|(\w+):\s*)', re.IGNORECASE)
DEFAULT_AGENT = "general"


class AgentConnectorHandler(MessageHandler):
    """
    File-based communication bridge between Discord and AI agents.

    Messages from Discord are written to inbox/ as JSON files.
    Agent responses are read from outbox/ and sent to Discord.
    """

    def __init__(
        self,
        auth_provider: AuthProvider,
        connector_folder: str,
        allowed_user_ids: Set[str],
        poll_interval: float = 0.5,
    ):
        super().__init__(auth_provider)
        self.connector_folder = Path(connector_folder)
        self.allowed_user_ids = allowed_user_ids
        self.poll_interval = poll_interval

        # Channel mapping for responses
        self.last_channel_id: Optional[str] = None
        self.bot_client = None

        # Track processed outbox files
        self._processed_files: Set[str] = set()
        self._outbox_task: Optional[asyncio.Task] = None

        # Setup folders
        self._setup_folders()

    def _setup_folders(self):
        """Create the connector folder structure."""
        self.inbox_folder = self.connector_folder / "inbox"
        self.outbox_folder = self.connector_folder / "outbox"
        self.state_file = self.connector_folder / "state.json"

        self.inbox_folder.mkdir(parents=True, exist_ok=True)
        self.outbox_folder.mkdir(parents=True, exist_ok=True)

        # Create default agent inbox
        (self.inbox_folder / DEFAULT_AGENT).mkdir(exist_ok=True)

        logger.info(f"Agent connector folder: {self.connector_folder}")
        logger.info(f"  Inbox:  {self.inbox_folder}/{{agent_id}}/")
        logger.info(f"  Outbox: {self.outbox_folder}")

    def set_bot_client(self, client):
        """Set the Discord client for sending responses."""
        self.bot_client = client
        # Register on_ready handler to start outbox watcher when bot connects
        @client.event
        async def on_ready():
            if self._outbox_task is None or self._outbox_task.done():
                self._outbox_task = asyncio.create_task(self._watch_outbox())
                logger.info("Outbox watcher started")

    def _parse_agent_prefix(self, content: str) -> Tuple[str, str]:
        """
        Parse agent prefix from message content.

        Returns (agent_id, remaining_content).
        "webdev: fix CSS" -> ("webdev", "fix CSS")
        "@research look up X" -> ("research", "look up X")
        "hello" -> ("general", "hello")
        "so what's up" -> ("general", "so what's up")  # no colon = not a prefix
        """
        match = AGENT_PREFIX_PATTERN.match(content)
        if match:
            # Group 1 is @agent, Group 2 is agent:
            agent_id = (match.group(1) or match.group(2)).lower()
            remaining = content[match.end():].strip()
            return (agent_id, remaining)
        return (DEFAULT_AGENT, content)

    async def handle(self, message: Message, context: Context) -> Optional[str]:
        """Process incoming Discord messages."""
        # Only allow specific user IDs
        if message.author.id not in self.allowed_user_ids:
            return None

        # Update last channel for responses
        self.last_channel_id = message.channel.id

        # Parse agent prefix
        agent_id, content = self._parse_agent_prefix(message.content)

        # Write message to agent's inbox
        await self._write_to_inbox(message, agent_id, content)

        # Update state
        self._update_state(message, agent_id)

        # No immediate response - agent will respond via outbox
        return None

    async def _write_to_inbox(self, message: Message, agent_id: str, content: str):
        """Write a message to the agent's inbox folder."""
        # Ensure agent inbox exists
        agent_inbox = self.inbox_folder / agent_id
        agent_inbox.mkdir(exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}_{message.id}.json"
        filepath = agent_inbox / filename

        msg_data = {
            "id": message.id,
            "agent": agent_id,
            "content": content,  # Content with prefix stripped
            "raw_content": message.content,  # Original message
            "author": {
                "id": message.author.id,
                "username": message.author.username,
            },
            "channel": {
                "id": message.channel.id,
                "name": message.channel.name,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(msg_data, f, indent=2)

        logger.info(f"Wrote message to inbox/{agent_id}/: {filename}")

    def _update_state(self, message: Message, agent_id: str):
        """Update the state file with current conversation context."""
        state = {
            "last_message_id": message.id,
            "last_agent": agent_id,
            "last_channel_id": message.channel.id,
            "last_channel_name": message.channel.name,
            "last_author_id": message.author.id,
            "last_author_username": message.author.username,
            "updated_at": datetime.utcnow().isoformat(),
        }

        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    async def _watch_outbox(self):
        """Watch the outbox folder for agent responses."""
        logger.info("Starting outbox watcher...")

        while True:
            try:
                await self._process_outbox()
            except Exception as e:
                logger.error(f"Error processing outbox: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _process_outbox(self):
        """Process any new files in the outbox folder."""
        if not self.bot_client:
            return

        # Get sorted list of JSON files
        outbox_files = sorted(self.outbox_folder.glob("*.json"))

        for filepath in outbox_files:
            filename = filepath.name

            # Skip already processed files
            if filename in self._processed_files:
                continue

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    response_data = json.load(f)

                content = response_data.get("content", "")
                channel_id = response_data.get("channel_id", self.last_channel_id)

                if content and channel_id:
                    await self._send_response(channel_id, content)
                    logger.info(f"Sent response from outbox: {filename}")

                # Mark as processed and delete
                self._processed_files.add(filename)
                filepath.unlink()

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in outbox file {filename}: {e}")
                # Move bad file aside
                bad_path = filepath.with_suffix(".bad")
                filepath.rename(bad_path)
            except Exception as e:
                logger.error(f"Error processing outbox file {filename}: {e}")

    async def _send_response(self, channel_id: str, content: str):
        """Send a response to a Discord channel."""
        try:
            # Try cache first, then fetch (needed for DM channels)
            channel = self.bot_client.get_channel(int(channel_id))
            if not channel:
                try:
                    channel = await self.bot_client.fetch_channel(int(channel_id))
                except Exception as e:
                    logger.error(f"Could not fetch channel {channel_id}: {e}")
                    return

            # Split long messages (Discord limit is 2000 chars)
            if len(content) <= 2000:
                await channel.send(content)
            else:
                # Split into chunks
                chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
                for chunk in chunks:
                    await channel.send(chunk)
                    await asyncio.sleep(0.5)  # Rate limit protection
        except Exception as e:
            logger.error(f"Failed to send response to channel {channel_id}: {e}")

    def clear_inbox(self):
        """Clear all files from inbox (useful for fresh starts)."""
        # Clear files in all agent subfolders
        for f in self.inbox_folder.glob("**/*.json"):
            f.unlink()
        logger.info("Cleared inbox")

    def clear_outbox(self):
        """Clear all files from outbox."""
        for f in self.outbox_folder.glob("*.json"):
            f.unlink()
        self._processed_files.clear()
        logger.info("Cleared outbox")
