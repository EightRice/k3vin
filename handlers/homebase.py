#!/usr/bin/env python3
"""
Homebase support bot - Claude-powered support for the tezos-homebase web app.

Standalone Discord bot that listens in a single channel for messages from users
with the webapp-user role. Each user gets their own Discord thread with isolated
conversation history. Claude Sonnet answers via the ATN bridge with tool use
for doc lookups.

Usage:
    python handlers/homebase.py
"""
import asyncio
import fnmatch
import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import discord
from dotenv import load_dotenv

# ATN bridge provider
sys.path.insert(0, str(Path("C:/code/atn")))
from atn.providers.bridge import BridgeProvider

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Quiet noisy libraries
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
log = logging.getLogger("homebase")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = 857045126332153876
REQUIRED_ROLE = "webapp-user"
ESCALATION_USER_ID = 938049028757807135
MAX_HISTORY_TURNS = 50
DISCORD_MSG_LIMIT = 2000
MAX_FILE_CHARS = 12000

# Rate limiting: max turns per user in a rolling 24h window
MAX_TURNS_PER_DAY = 20
RATE_WINDOW_SECS = 86400

# Live status embed: min interval between edits (seconds)
STATUS_UPDATE_INTERVAL = 1.0
EMBED_COLOR = 0x5865F2       # Blurple
EMBED_COLOR_ERROR = 0xED4245  # Red

DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
REPOS = {
    "homebase-app": DOCS_ROOT / "homebase-app",
    "baseDAO": DOCS_ROOT / "baseDAO",
}

SYSTEM_PROMPT = """\
You are a support agent for Tezos Homebase (tezos-homebase.io), a web \
application for creating and managing DAOs on the Tezos blockchain. It is \
built on the BaseDAO smart contract framework.

There are three DAO templates: Treasury, Registry, and Lambda. The governance \
cycle has two phases: proposal period and voting period. Token holders freeze \
tokens to vote.

You have tools to search and read documentation from two repos:
- **homebase-app** - the web UI (React/TypeScript)
- **baseDAO** - the smart contracts (Haskell/LIGO) and their specs

Use these tools when you need specifics about how something works - contract \
error codes, governance parameters, DAO creation flow, etc. Don't guess \
at details you can look up.

You also have an **escalate** tool. Use it when:
- The user's issue requires human intervention (account problems, stuck \
transactions, suspected bugs you can't diagnose from docs alone).
- You've exhausted what you can help with and the user is still stuck.
- The user explicitly asks to speak to a human.
Do NOT escalate for questions you can answer from the docs.

Keep responses concise. This is a Discord support thread.

For bugs, direct users to: https://github.com/dOrgTech/homebase-app/issues
You cannot perform transactions or access wallets.
"""

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_docs",
        "description": (
            "Search across Homebase documentation and source code for a query "
            "string. Returns matching lines with file paths. Use this to find "
            "relevant files before reading them. Searches both homebase-app "
            "(UI) and baseDAO (contracts) repos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text or regex pattern to search for.",
                },
                "repo": {
                    "type": "string",
                    "enum": ["homebase-app", "baseDAO", "both"],
                    "description": "Which repo to search. Default: both.",
                },
                "file_pattern": {
                    "type": "string",
                    "description": (
                        "Optional glob to filter files, e.g. '*.md' for docs "
                        "only, '*.ts' for TypeScript. Default: all files."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a specific file from the homebase-app or baseDAO repo. "
            "Returns the file contents (truncated if very large). Use after "
            "search_docs to read files you've identified as relevant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["homebase-app", "baseDAO"],
                    "description": "Which repo the file is in.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Path relative to the repo root, e.g. 'docs/treasury.md' "
                        "or 'src/modules/home/utils/faq.md'."
                    ),
                },
            },
            "required": ["repo", "path"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files in a directory of the homebase-app or baseDAO repo. "
            "Useful for discovering what documentation or source files exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["homebase-app", "baseDAO"],
                    "description": "Which repo to list.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory path relative to repo root. "
                        "Use '.' or '' for the root."
                    ),
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "escalate",
        "description": (
            "Escalate the issue to a human support engineer. Use this when "
            "you cannot resolve the user's problem from documentation alone, "
            "when the issue requires human intervention, or when the user "
            "explicitly asks to talk to a person. Provide a brief summary "
            "of the issue and what you've already tried."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Brief summary of the issue for the human engineer, "
                        "including what the user needs and what you've already "
                        "looked into."
                    ),
                },
            },
            "required": ["reason"],
        },
    },
]


async def tool_executor(name: str, args: dict) -> dict:
    """Execute a tool call and return the result."""
    log.debug("TOOL CALL: %s(%s)", name, args)
    try:
        if name == "search_docs":
            result = _search_docs(
                args["query"],
                args.get("repo", "both"),
                args.get("file_pattern"),
            )
        elif name == "read_file":
            result = _read_file(args["repo"], args["path"])
        elif name == "list_files":
            result = _list_files(args["repo"], args.get("path", "."))
        else:
            result = {"error": f"Unknown tool: {name}"}
        # Log a preview of the result
        preview = str(result)[:200]
        log.debug("TOOL RESULT: %s -> %s", name, preview)
        return result
    except Exception as e:
        log.exception("Tool %s failed", name)
        return {"error": str(e)}


# Extensions to skip during search (binary / generated)
_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".mp4", ".mp3", ".zip", ".gz", ".tar", ".lock",
    ".map", ".min.js", ".min.css", ".pyc", ".pyo", ".exe", ".dll",
    ".so", ".dylib", ".class", ".jar", ".bin", ".dat",
}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist", "build"}
_MAX_MATCHES_PER_REPO = 30
_MAX_LINE_LEN = 200


def _search_docs(query: str, repo: str, file_pattern: str | None) -> dict:
    """Pure-Python recursive search -- no rg/grep dependency."""
    repos_to_search = list(REPOS.keys()) if repo == "both" else [repo]
    all_matches: list[str] = []

    # Try regex first; fall back to case-insensitive literal
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    for repo_name in repos_to_search:
        repo_path = REPOS.get(repo_name)
        if not repo_path or not repo_path.exists():
            continue

        matches_this_repo = 0
        for filepath in repo_path.rglob("*"):
            if matches_this_repo >= _MAX_MATCHES_PER_REPO:
                break

            # Skip directories, hidden dirs, binary extensions
            if filepath.is_dir():
                continue
            parts = filepath.parts
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if filepath.suffix.lower() in _SKIP_EXTS:
                continue

            # Apply optional glob filter
            if file_pattern and not fnmatch.fnmatch(filepath.name, file_pattern):
                continue

            try:
                text = filepath.read_text(encoding="utf-8", errors="ignore")
            except (OSError, PermissionError):
                continue

            hits_in_file = 0
            for line_no, line in enumerate(text.split("\n"), start=1):
                if hits_in_file >= 5:
                    break
                if pattern.search(line):
                    rel = str(filepath.relative_to(DOCS_ROOT)).replace("\\", "/")
                    snippet = line.strip()[:_MAX_LINE_LEN]
                    all_matches.append(f"{rel}:{line_no}: {snippet}")
                    hits_in_file += 1
                    matches_this_repo += 1
                    if matches_this_repo >= _MAX_MATCHES_PER_REPO:
                        break

    if not all_matches:
        return {"results": "No matches found."}
    return {"results": "\n".join(all_matches)}


def _read_file(repo: str, path: str) -> dict:
    repo_path = REPOS.get(repo)
    if not repo_path:
        return {"error": f"Unknown repo: {repo}"}

    file_path = repo_path / path
    try:
        file_path.resolve().relative_to(repo_path.resolve())
    except ValueError:
        return {"error": "Path traversal not allowed."}

    if not file_path.is_file():
        return {"error": f"File not found: {path}"}

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": f"Could not read file: {e}"}

    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + f"\n\n[truncated at {MAX_FILE_CHARS} chars]"
    return {"content": content}


async def _escalate(channel: discord.abc.Messageable, reason: str) -> dict:
    """Ping the human support engineer in the thread."""
    mention = f"<@{ESCALATION_USER_ID}>"
    escalation_msg = f"{mention} **Escalation** -- {reason}"
    try:
        await channel.send(escalation_msg)
        log.info("Escalated to %s: %s", ESCALATION_USER_ID, reason[:80])
        return {"status": "escalated", "message": "A human engineer has been notified in this thread."}
    except Exception as e:
        log.exception("Failed to send escalation")
        return {"error": f"Failed to notify engineer: {e}"}


def _list_files(repo: str, path: str) -> dict:
    repo_path = REPOS.get(repo)
    if not repo_path:
        return {"error": f"Unknown repo: {repo}"}

    dir_path = repo_path / (path or ".")
    try:
        dir_path.resolve().relative_to(repo_path.resolve())
    except ValueError:
        return {"error": "Path traversal not allowed."}

    if not dir_path.is_dir():
        return {"error": f"Directory not found: {path}"}

    entries: list[str] = []
    for item in sorted(dir_path.iterdir()):
        if item.name.startswith("."):
            continue
        suffix = "/" if item.is_dir() else ""
        entries.append(f"{item.name}{suffix}")

    return {"entries": entries}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def split_message(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Live status embed
# ---------------------------------------------------------------------------

TOOL_LABELS = {
    "search_docs": "Searching docs",
    "read_file": "Reading file",
    "list_files": "Listing files",
    "escalate": "Escalating to human",
}


class StatusEmbed:
    """Live-updating Discord embed that shows what the bot is doing."""

    def __init__(self, channel: discord.abc.Messageable) -> None:
        self._channel = channel
        self._message: discord.Message | None = None
        self._tool_calls: list[str] = []
        self._current_tool: str | None = None
        self._current_detail: str | None = None
        self._last_update: float = 0.0

    async def send_initial(self) -> None:
        """Send the initial 'Thinking...' embed."""
        embed = discord.Embed(title="Thinking...", color=EMBED_COLOR)
        try:
            self._message = await self._channel.send(embed=embed)
        except Exception:
            log.debug("Failed to send status embed", exc_info=True)

    async def set_tool(self, name: str, detail: str | None = None) -> None:
        """Update to show a tool being executed."""
        label = TOOL_LABELS.get(name, name)
        self._tool_calls.append(label)
        self._current_tool = label
        self._current_detail = detail
        await self._maybe_update()

    async def clear_tool(self) -> None:
        """Tool finished -- go back to thinking state."""
        self._current_tool = None
        self._current_detail = None
        await self._maybe_update()

    async def finalize(self, text: str, tool_summary: bool = True) -> None:
        """Replace the embed with the final response text."""
        log.debug("finalize() called, text length: %d, embed msg id: %s",
                  len(text), self._message.id if self._message else "none")
        # Build the final message
        if self._tool_calls and tool_summary:
            # Dedupe while preserving order
            seen: set[str] = set()
            unique: list[str] = []
            for t in self._tool_calls:
                if t not in seen:
                    seen.add(t)
                    unique.append(t)
            summary = ", ".join(unique)
            final = f"-# {summary}\n{text}"
        else:
            final = text

        # Try to edit the embed message into the final response
        if self._message:
            chunks = split_message(final)
            try:
                await self._message.edit(content=chunks[0], embed=None)
                log.debug("finalize: edited embed msg %s into response (%d chunks)",
                          self._message.id, len(chunks))
                # Send overflow chunks as separate messages
                for chunk in chunks[1:]:
                    await self._channel.send(chunk)
                return
            except Exception:
                log.debug("Failed to edit status into final", exc_info=True)
                # Fallback: delete embed and send fresh
                try:
                    await self._message.delete()
                except Exception:
                    pass

        # Fallback
        log.debug("finalize: fallback send (%d chunks)", len(split_message(final)))
        for chunk in split_message(final):
            await self._channel.send(chunk)

    async def _maybe_update(self) -> None:
        """Update the embed, rate-limited."""
        now = time.monotonic()
        if now - self._last_update < STATUS_UPDATE_INTERVAL:
            return
        self._last_update = now
        await self._update()

    async def _update(self) -> None:
        if not self._message:
            return

        if self._current_tool:
            title = f"Working... {self._current_tool}"
            desc = self._current_detail or None
        else:
            title = "Thinking..."
            desc = None

        embed = discord.Embed(title=title, color=EMBED_COLOR)
        if desc:
            embed.description = desc

        if self._tool_calls:
            # Show last 8
            display = self._tool_calls[-8:]
            embed.add_field(
                name="Steps so far",
                value=", ".join(display),
                inline=False,
            )

        try:
            await self._message.edit(embed=embed)
        except Exception:
            log.debug("Failed to update status embed", exc_info=True)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Rolling-window turn counter per user."""

    def __init__(self, max_turns: int = MAX_TURNS_PER_DAY,
                 window_secs: int = RATE_WINDOW_SECS):
        self.max_turns = max_turns
        self.window_secs = window_secs
        # user_id -> list of timestamps
        self._usage: dict[str, list[float]] = defaultdict(list)

    def _prune(self, user_id: str) -> None:
        cutoff = time.time() - self.window_secs
        self._usage[user_id] = [
            t for t in self._usage[user_id] if t > cutoff
        ]

    def remaining(self, user_id: str) -> int:
        self._prune(user_id)
        return max(0, self.max_turns - len(self._usage[user_id]))

    def record(self, user_id: str) -> None:
        self._usage[user_id].append(time.time())


# ---------------------------------------------------------------------------
# Per-thread conversation state
# ---------------------------------------------------------------------------

class ThreadState:
    """Conversation history for a single support thread."""

    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []

    def add_user(self, name: str, content: str) -> None:
        self.history.append({"role": "user", "name": name, "content": content})
        if len(self.history) > MAX_HISTORY_TURNS:
            del self.history[: len(self.history) - MAX_HISTORY_TURNS]

    def add_assistant(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})

    def format(self) -> str:
        parts: list[str] = []
        for turn in self.history:
            if turn["role"] == "user":
                parts.append(f"[{turn['name']}]: {turn['content']}")
            else:
                parts.append(f"[Support Agent]: {turn['content']}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

provider = BridgeProvider(model="sonnet")
rate_limiter = RateLimiter()
threads: dict[int, ThreadState] = {}      # thread_id -> state
thread_locks: dict[int, asyncio.Lock] = {}  # thread_id -> lock
_processed_messages: set[int] = set()        # message IDs already handled


def has_role(member: discord.Member, role_name: str) -> bool:
    return any(r.name == role_name for r in member.roles)


def get_thread_state(thread_id: int) -> ThreadState:
    if thread_id not in threads:
        threads[thread_id] = ThreadState()
    return threads[thread_id]


def get_thread_lock(thread_id: int) -> asyncio.Lock:
    if thread_id not in thread_locks:
        thread_locks[thread_id] = asyncio.Lock()
    return thread_locks[thread_id]


@client.event
async def on_ready():
    log.info("Connected as %s", client.user)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Deduplicate: Discord can fire on_message twice for thread starters
    if message.id in _processed_messages:
        return
    _processed_messages.add(message.id)
    # Keep the set from growing unbounded
    if len(_processed_messages) > 1000:
        _processed_messages.clear()

    # Debug: log every non-bot message we see
    ch = message.channel
    ch_label = f"thread:{ch.id}(parent:{ch.parent_id})" if isinstance(ch, discord.Thread) else f"ch:{ch.id}"
    log.debug(
        "MSG %s | %s | author:%s (id:%s) roles:%s | %s",
        ch_label,
        getattr(ch, "name", "?"),
        message.author.display_name,
        message.author.id,
        [r.name for r in message.author.roles] if isinstance(message.author, discord.Member) else "n/a",
        message.content[:60],
    )

    if not isinstance(message.author, discord.Member):
        log.debug("  -> skip: not a Member")
        return
    if not has_role(message.author, REQUIRED_ROLE):
        log.debug("  -> skip: missing role '%s'", REQUIRED_ROLE)
        return

    channel = message.channel
    user_id = str(message.author.id)
    username = message.author.display_name or message.author.name
    user_text = message.content.strip()
    if not user_text:
        return

    # --- New question in the support channel -> create a thread ---
    if channel.id == CHANNEL_ID:
        # Check rate limit before creating the thread
        remaining = rate_limiter.remaining(user_id)
        if remaining <= 0:
            await message.reply(
                "You've reached the daily limit for support questions. "
                "Please try again tomorrow."
            )
            return

        # Create a thread from the user's message
        thread_name = f"{username}: {user_text[:50]}"
        thread = await message.create_thread(name=thread_name)

        # Process the first turn in the new thread
        await _handle_turn(thread, message, user_id, username, user_text,
                           thread.id)
        return

    # --- Follow-up inside an existing support thread ---
    if isinstance(channel, discord.Thread) and channel.parent_id == CHANNEL_ID:
        # Check rate limit
        remaining = rate_limiter.remaining(user_id)
        if remaining <= 0:
            await channel.send(
                "You've reached the daily limit for support questions. "
                "Please try again tomorrow."
            )
            return

        await _handle_turn(channel, message, user_id, username, user_text,
                           channel.id)
        return


async def _handle_turn(
    channel: discord.Thread | discord.abc.Messageable,
    message: discord.Message,
    user_id: str,
    username: str,
    user_text: str,
    thread_id: int,
) -> None:
    """Process a single conversational turn."""
    state = get_thread_state(thread_id)
    lock = get_thread_lock(thread_id)

    log.info("[thread:%s] [%s] %s", thread_id, username, user_text[:80])

    state.add_user(username, user_text)
    formatted = state.format()

    # Live status embed for this turn
    status = StatusEmbed(channel)

    # Build a tool executor that updates the embed on each call
    async def _executor(name: str, args: dict) -> dict:
        # Show what we're about to do
        detail = _tool_detail(name, args)
        await status.set_tool(name, detail)

        if name == "escalate":
            result = await _escalate(channel, args.get("reason", ""))
        else:
            result = await tool_executor(name, args)

        await status.clear_tool()
        return result

    async with lock:
        await status.send_initial()
        try:
            log.debug("Sending to bridge (history turns: %d)", len(state.history))
            response = await provider.send_orchestrate(
                message=formatted,
                system=SYSTEM_PROMPT,
                model="sonnet",
                tools=TOOLS,
                max_turns=20,
                tool_executor=_executor,
            )
            reply = response.text.strip() if response.text else ""
            log.info("REPLY (%d chars): %s", len(reply), reply[:120])
            if hasattr(response, "usage") and response.usage:
                log.debug("USAGE: %s", response.usage)

            # Handle empty reply (e.g. max_turns exhausted)
            if not reply:
                log.warning("Empty reply from bridge (stop_reason: %s)",
                            getattr(response, "stop_reason", "unknown"))
                reply = (
                    "I investigated your question but wasn't able to put together "
                    "a complete answer. Could you try rephrasing, or would you "
                    "like me to escalate this to a human?"
                )
        except Exception:
            log.exception("Bridge call failed")
            reply = (
                "Sorry, I ran into a problem processing your question. "
                "Please try again in a moment."
            )

    rate_limiter.record(user_id)
    state.add_assistant(reply)

    remaining = rate_limiter.remaining(user_id)
    if remaining == 0:
        reply += "\n\n*You've reached your daily question limit. Your allocation resets in 24 hours.*"
    elif remaining <= 3:
        reply += f"\n\n*({remaining} questions remaining today)*"

    await status.finalize(reply)


def _tool_detail(name: str, args: dict) -> str | None:
    """Build a short human-readable detail string for a tool call."""
    if name == "search_docs":
        q = args.get("query", "")
        repo = args.get("repo", "both")
        return f'`{q}` in {repo}'
    if name == "read_file":
        return f'`{args.get("path", "")}`'
    if name == "list_files":
        return f'`{args.get("repo", "")}/{args.get("path", ".")}`'
    if name == "escalate":
        return None
    return None


if __name__ == "__main__":
    try:
        client.run(TOKEN)
    except KeyboardInterrupt:
        log.info("Shutting down")
