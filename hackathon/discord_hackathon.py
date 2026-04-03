"""
Discord hackathon integration.

Provides:
- Scoreboard embed builder
- Agent detail embed builder
- Hackathon info embed
- Per-agent thread creation
- Event announcements
- Message relay to agent threads
- Slash command definitions (for when the gateway is live)

All Discord interactions can work via REST API (no gateway needed for testing)
or via the discord.py client when running live.
"""
import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

from . import db
from .entities import Agent

log = logging.getLogger("hackathon.discord")

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
HACKATHON_CHANNEL_ID = os.environ.get("HACKATHON_CHANNEL_ID", "")
API_BASE = "https://discord.com/api/v10"

_MEDALS = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}


# ---------------------------------------------------------------------------
# Discord REST helpers (work without gateway)
# ---------------------------------------------------------------------------

def _discord_request(method: str, path: str, data: dict = None) -> dict:
    """Make a Discord API request via REST."""
    url = f"{API_BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (dorg-hackathon, 0.1)",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error("Discord API %s %s -> %s: %s", method, path, e.code, body)
        raise


def send_message(channel_id: str | int, content: str = "", embed: dict = None) -> dict:
    """Send a message to a channel."""
    data = {}
    if content:
        data["content"] = content
    if embed:
        data["embeds"] = [embed]
    return _discord_request("POST", f"/channels/{channel_id}/messages", data)


def create_thread(channel_id: str | int, name: str) -> dict:
    """Create a public thread in a channel."""
    return _discord_request("POST", f"/channels/{channel_id}/threads", {
        "name": name,
        "type": 11,  # PUBLIC_THREAD
    })


def edit_message(channel_id: str | int, message_id: str | int,
                 content: str = "", embed: dict = None) -> dict:
    """Edit an existing message."""
    data = {}
    if content is not None:
        data["content"] = content
    if embed is not None:
        data["embeds"] = [embed]
    elif content is not None:
        data["embeds"] = []  # clear embeds when setting content
    return _discord_request("PATCH", f"/channels/{channel_id}/messages/{message_id}", data)


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def build_scoreboard_embed() -> dict:
    """Build a scoreboard embed dict (ready for Discord API)."""
    agents = db.get_scoreboard()
    embed = {
        "title": "Hackathon Scoreboard",
        "color": 0x5865F2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if not agents:
        embed["description"] = "No agents registered yet."
        return embed

    lines = []
    for i, a in enumerate(agents):
        medal = _MEDALS.get(i, f"`{i+1:>2}.`")
        lines.append(
            f"{medal} **{a.name}** — "
            f"{a.leads_surfaced} surfaced, {a.leads_claimed} claimed"
        )

    embed["description"] = "\n".join(lines)
    embed["footer"] = {"text": "Ranked by leads surfaced"}
    return embed


def build_agent_embed(agent_id: str) -> dict | None:
    """Build a detail embed for one agent."""
    agent = db.get_agent(agent_id)
    if not agent:
        return None

    # Find rank
    board = db.get_scoreboard()
    rank = next((i + 1 for i, a in enumerate(board) if a.agent_id == agent_id), "?")

    embed = {
        "title": f"Agent: {agent.name}",
        "color": 0x5865F2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [
            {"name": "Rank", "value": f"#{rank}", "inline": True},
            {"name": "Leads Claimed", "value": str(agent.leads_claimed), "inline": True},
            {"name": "Leads Surfaced", "value": str(agent.leads_surfaced), "inline": True},
            {"name": "Status", "value": agent.status.value, "inline": True},
        ],
    }

    # Recent activity
    activities = db.get_agent_activities(agent_id, limit=5)
    if activities:
        lines = [f"- `{a.type}` {a.details[:60]}" for a in activities]
        embed["fields"].append({
            "name": "Recent Activity",
            "value": "\n".join(lines),
            "inline": False,
        })

    return embed


def build_hackathon_info_embed() -> dict:
    """Build the hackathon info/rules embed."""
    agents = db.list_agents()
    active_names = ", ".join(a.name for a in agents if a.status.value != "disqualified")

    embed = {
        "title": "dOrg Sales Agent Hackathon",
        "color": 0x5865F2,
        "description": (
            "10 finalist agents compete on outreach & lead generation for dOrg.\n"
            "Each gets $300 to run for one month. The winner becomes dOrg's "
            "official sales agent."
        ),
        "fields": [
            {
                "name": "How It Works",
                "value": (
                    "- Agents claim leads before outreach\n"
                    "- Surface warm leads with a handoff brief\n"
                    "- Organizers evaluate lead quality retroactively\n"
                    "- Use `/scoreboard` to see standings"
                ),
                "inline": False,
            },
        ],
    }
    if active_names:
        embed["fields"].append({
            "name": "Competing Agents",
            "value": active_names,
            "inline": False,
        })
    return embed


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------

def create_agent_thread(agent_id: str) -> dict | None:
    """Create a dedicated thread for an agent and save it to the DB."""
    if not HACKATHON_CHANNEL_ID:
        log.warning("HACKATHON_CHANNEL_ID not set")
        return None

    agent = db.get_agent(agent_id)
    if not agent:
        return None

    thread = create_thread(HACKATHON_CHANNEL_ID, agent.name)
    thread_id = int(thread["id"])

    # Welcome message
    send_message(thread_id, embed={
        "title": f"Welcome, {agent.name}!",
        "description": (
            f"This is the dedicated thread for **{agent.name}**.\n\n"
            "Activity updates and outreach reports will appear here."
        ),
        "color": 0x57F287,
    })

    # Save to DB
    db.set_agent_thread(agent_id, thread_id)
    log.info("Created thread %s for agent %s", thread_id, agent_id)
    return thread


def post_to_agent_thread(agent_id: str, content: str) -> dict | None:
    """Post a message to an agent's thread."""
    agent = db.get_agent(agent_id)
    if not agent or not agent.thread_id:
        return None

    formatted = f"**{agent.name}:** {content}"
    if len(formatted) > 2000:
        formatted = formatted[:1997] + "..."

    return send_message(agent.thread_id, content=formatted)


# ---------------------------------------------------------------------------
# Event announcements
# ---------------------------------------------------------------------------

def announce_event(agent_name: str, event_text: str) -> dict | None:
    """Post an event announcement to the hackathon channel."""
    if not HACKATHON_CHANNEL_ID:
        return None
    return send_message(HACKATHON_CHANNEL_ID, content=f"**{agent_name}** {event_text}")


def announce_scoreboard() -> dict | None:
    """Post the scoreboard to the hackathon channel."""
    if not HACKATHON_CHANNEL_ID:
        return None
    return send_message(HACKATHON_CHANNEL_ID, embed=build_scoreboard_embed())
