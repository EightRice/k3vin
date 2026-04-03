"""
WebSocket-to-Discord rendering pipeline.

Manages live-updating embeds in agent threads. When an agent streams text
frames over the /ws endpoint, this module:

1. Creates an initial "thinking" embed in the agent's Discord thread.
2. Updates the embed as text frames arrive (throttled to 1 edit / 2 sec).
3. Finalizes the embed when the websocket disconnects.

Uses the REST helpers from discord_hackathon.py -- no discord.py client needed.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from .discord_hackathon import send_message, edit_message

log = logging.getLogger("hackathon.ws_renderer")

# Discord rate-limit safety: at most 1 edit per this many seconds
THROTTLE_SECONDS = 2.0

# Show only the last N lines in the embed to keep it readable
MAX_DISPLAY_LINES = 10

# Embed colors
COLOR_ACTIVE = 0x5865F2     # Blurple -- session in progress
COLOR_FINISHED = 0x57F287   # Green -- session ended cleanly
COLOR_ERROR = 0xED4245      # Red -- session ended with error


class StreamEmbed:
    """Live-updating embed for one websocket session."""

    def __init__(self, thread_id: int, agent_name: str) -> None:
        self._thread_id = thread_id
        self._agent_name = agent_name
        self._message_id: str | None = None
        self._lines: list[str] = []
        self._last_edit: float = 0.0
        self._pending_update: bool = False
        self._update_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def send_initial(self) -> None:
        """Post the initial embed into the agent's thread."""
        embed = self._build_embed(
            title=f"{self._agent_name} is thinking...",
            description="*Waiting for output...*",
            color=COLOR_ACTIVE,
        )
        try:
            resp = await asyncio.to_thread(
                send_message, self._thread_id, "", embed
            )
            self._message_id = resp.get("id")
            log.debug("Initial embed sent: message_id=%s", self._message_id)
        except Exception:
            log.exception("Failed to send initial embed to thread %s", self._thread_id)

    async def append_text(self, text: str) -> None:
        """Buffer incoming text and schedule a throttled edit."""
        # Split multi-line frames into individual lines
        for line in text.splitlines():
            stripped = line.rstrip()
            if stripped:
                self._lines.append(stripped)

        self._pending_update = True
        await self._maybe_edit()

    async def finalize(self, error: bool = False) -> None:
        """Mark the session as ended and do one last edit."""
        # Cancel any pending delayed update
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        if not self._message_id:
            return

        color = COLOR_ERROR if error else COLOR_FINISHED
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        footer_text = f"Session ended at {now}"
        if error:
            footer_text = f"Session ended with error at {now}"

        body = self._format_body()
        if not body:
            body = "*No output received.*"

        embed = self._build_embed(
            title=f"{self._agent_name} -- session complete",
            description=body,
            color=color,
            footer=footer_text,
        )
        try:
            await asyncio.to_thread(
                edit_message, self._thread_id, self._message_id, "", embed
            )
            log.debug("Embed finalized: message_id=%s", self._message_id)
        except Exception:
            log.exception("Failed to finalize embed %s", self._message_id)

    # ------------------------------------------------------------------
    # Throttled editing
    # ------------------------------------------------------------------

    async def _maybe_edit(self) -> None:
        """Edit the embed if enough time has passed, otherwise schedule a delayed edit."""
        if not self._message_id:
            return

        now = time.monotonic()
        elapsed = now - self._last_edit

        if elapsed >= THROTTLE_SECONDS:
            await self._do_edit()
        else:
            # Schedule a delayed edit if one isn't already pending
            if self._update_task is None or self._update_task.done():
                delay = THROTTLE_SECONDS - elapsed
                self._update_task = asyncio.create_task(self._delayed_edit(delay))

    async def _delayed_edit(self, delay: float) -> None:
        """Wait then fire an edit, so the latest content always gets pushed."""
        await asyncio.sleep(delay)
        if self._pending_update:
            await self._do_edit()

    async def _do_edit(self) -> None:
        """Actually edit the Discord message."""
        self._pending_update = False
        self._last_edit = time.monotonic()

        body = self._format_body()
        embed = self._build_embed(
            title=f"{self._agent_name} is thinking...",
            description=body or "*Waiting for output...*",
            color=COLOR_ACTIVE,
        )
        try:
            await asyncio.to_thread(
                edit_message, self._thread_id, self._message_id, "", embed
            )
        except Exception:
            log.exception("Failed to edit embed %s", self._message_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_body(self) -> str:
        """Format the last N lines as a code block for display."""
        if not self._lines:
            return ""
        display = self._lines[-MAX_DISPLAY_LINES:]
        truncated = len(self._lines) > MAX_DISPLAY_LINES
        text = "\n".join(display)
        # Keep within Discord embed description limit (4096 chars)
        if len(text) > 3900:
            text = text[-3900:]
            truncated = True
        prefix = "...\n" if truncated else ""
        return f"```\n{prefix}{text}\n```"

    @staticmethod
    def _build_embed(
        title: str,
        description: str,
        color: int,
        footer: str | None = None,
    ) -> dict:
        """Build a raw embed dict for the Discord REST API."""
        embed: dict = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if footer:
            embed["footer"] = {"text": footer}
        return embed
