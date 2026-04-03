"""
Deterministic menu commands for the hackathon Discord integration.

Parses !hackathon (or !h) prefix commands and returns response dicts.
Pure logic -- no Discord API calls. The caller sends the actual messages.

Returns:
    {"content": "text"}           -- plain text reply
    {"embed": {...}}              -- embed reply
    {"dm": "text", "reply": "text"} -- DM the author + reply publicly
    None                          -- not a hackathon command
"""

from . import db
from .discord_hackathon import (
    build_scoreboard_embed,
    build_agent_embed,
    build_hackathon_info_embed,
)
from .sheets_sync import report_conversion, is_organizer
from .entities.agent import NotWhitelisted, AlreadyRegistered


HELP_TEXT = (
    "**Hackathon Commands**\n"
    "`!h scoreboard` -- current standings\n"
    "`!h agent <name>` -- agent details\n"
    "`!h leads <agent>` -- leads for an agent\n"
    "`!h status` -- competition stats\n"
    "`!h register <agent_name>` -- register your agent\n"
    "`!h convert <lead_id> [note]` -- mark lead converted (organizers)\n"
    "`!h help` -- this message"
)


def handle_hackathon_command(
    message_content: str,
    author_id: str,
    channel_id: str,
) -> dict | None:
    """Parse a hackathon command and return a response dict, or None.

    Args:
        message_content: The raw message text.
        author_id: Discord user ID of the message author.
        channel_id: Discord channel ID where the message was sent.

    Returns:
        A response dict or None if not a hackathon command.
    """
    text = message_content.strip()

    # Check for command prefix
    if text.lower().startswith("!hackathon "):
        body = text[len("!hackathon "):].strip()
    elif text.lower().startswith("!hackathon"):
        body = text[len("!hackathon"):].strip()
    elif text.lower().startswith("!h "):
        body = text[len("!h "):].strip()
    elif text.lower() == "!h":
        body = ""
    else:
        return None

    # Parse subcommand + args
    parts = body.split(None, 1)
    subcommand = parts[0].lower() if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""

    # Route to handler
    if subcommand in ("scoreboard", "scores"):
        return _cmd_scoreboard()
    elif subcommand == "agent":
        return _cmd_agent(args)
    elif subcommand == "leads":
        return _cmd_leads(args)
    elif subcommand in ("info", "help", ""):
        return _cmd_help()
    elif subcommand == "register":
        return _cmd_register(args, author_id)
    elif subcommand == "convert":
        return _cmd_convert(args, author_id)
    elif subcommand == "status":
        return _cmd_status()
    else:
        return {"content": f"Unknown command `{subcommand}`. Use `!h help` for options."}


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_scoreboard() -> dict:
    return {"embed": build_scoreboard_embed()}


def _cmd_agent(args: str) -> dict:
    if not args:
        return {"content": "Usage: `!h agent <name>`"}

    agent_id = args.lower().strip()
    embed = build_agent_embed(agent_id)
    if embed:
        return {"embed": embed}

    # Try fuzzy match: search by name among all agents
    agents = db.list_agents()
    query = args.lower()
    matches = [a for a in agents if query in a.name.lower() or query in a.agent_id]
    if len(matches) == 1:
        embed = build_agent_embed(matches[0].agent_id)
        if embed:
            return {"embed": embed}
    elif len(matches) > 1:
        names = ", ".join(f"`{a.agent_id}`" for a in matches)
        return {"content": f"Multiple matches: {names}. Be more specific."}

    return {"content": f"Agent `{args}` not found. Use `!h scoreboard` to see all agents."}


def _cmd_leads(args: str) -> dict:
    if not args:
        return {"content": "Usage: `!h leads <agent_name>`"}

    agent_id = args.lower().strip()
    agent = db.get_agent(agent_id)

    # Fuzzy match if exact ID fails
    if not agent:
        agents = db.list_agents()
        query = args.lower()
        matches = [a for a in agents if query in a.name.lower() or query in a.agent_id]
        if len(matches) == 1:
            agent = matches[0]
        elif len(matches) > 1:
            names = ", ".join(f"`{a.agent_id}`" for a in matches)
            return {"content": f"Multiple matches: {names}. Be more specific."}

    if not agent:
        return {"content": f"Agent `{args}` not found."}

    leads = db.get_leads_by_agent(agent.agent_id)
    if not leads:
        return {"content": f"**{agent.name}** has no leads yet."}

    lines = []
    for lead in leads[:20]:  # cap at 20 to avoid message length issues
        status_icon = {
            "claimed": "\u2B55",      # hollow circle
            "surfaced": "\u2705",     # green check
            "converted": "\u2B50",    # star
        }.get(lead.status.value, "\u2753")
        brief_preview = f" -- {lead.brief[:50]}..." if lead.brief else ""
        lines.append(
            f"{status_icon} `{lead.id[:8]}` {lead.channel}/{lead.identifier}{brief_preview}"
        )

    header = f"**{agent.name}** -- {len(leads)} lead(s)"
    if len(leads) > 20:
        header += " (showing first 20)"

    return {"content": f"{header}\n" + "\n".join(lines)}


def _cmd_help() -> dict:
    return {"content": HELP_TEXT}


def _cmd_register(args: str, author_id: str) -> dict:
    if not args:
        return {"content": "Usage: `!h register <agent_name>`"}

    agent_name = args.strip()
    try:
        agent, token = db.register(author_id, agent_name)
    except NotWhitelisted:
        return {"content": "Registration failed: you are not a whitelisted finalist."}
    except AlreadyRegistered as e:
        return {"content": f"Registration failed: {e}"}

    return {
        "dm": (
            f"Your agent **{agent.name}** is registered!\n\n"
            f"**Agent ID:** `{agent.agent_id}`\n"
            f"**API Token:** `{token}`\n\n"
            "Save this token -- it will not be shown again. "
            "Provide it to your agent for API authentication."
        ),
        "reply": f"Agent **{agent.name}** registered. Check your DMs for your API token.",
    }


def _cmd_convert(args: str, author_id: str) -> dict:
    if not args:
        return {"content": "Usage: `!h convert <lead_id> [note]`"}

    parts = args.split(None, 1)
    lead_id = parts[0]
    note = parts[1] if len(parts) > 1 else ""

    if not is_organizer(author_id):
        return {"content": "Only organizers can mark leads as converted."}

    result = report_conversion(lead_id, author_id, note=note)
    if not result["ok"]:
        return {"content": f"Conversion failed: {result['error']}"}

    lead = result["lead"]
    agent_name = result["agent_name"]
    msg = f"Lead `{lead.id[:8]}` ({lead.identifier}) marked as **converted** for **{agent_name}**."
    if note:
        msg += f"\nNote: {note}"
    return {"content": msg}


def _cmd_status() -> dict:
    agents = db.list_agents()
    active = [a for a in agents if a.status.value not in ("disqualified", "finished")]
    all_leads = db.get_all_leads()
    surfaced = [l for l in all_leads if l.status.value in ("surfaced", "converted")]
    converted = [l for l in all_leads if l.converted]

    total_claimed = sum(a.leads_claimed for a in agents)
    total_surfaced = sum(a.leads_surfaced for a in agents)

    lines = [
        "**Hackathon Status**",
        f"Agents: **{len(active)}** active / **{len(agents)}** total",
        f"Leads claimed: **{total_claimed}**",
        f"Leads surfaced: **{total_surfaced}**",
        f"Leads converted: **{len(converted)}**",
    ]

    if active:
        top = sorted(active, key=lambda a: a.leads_surfaced, reverse=True)[:3]
        podium = ", ".join(f"**{a.name}** ({a.leads_surfaced})" for a in top)
        lines.append(f"Top agents: {podium}")

    return {"content": "\n".join(lines)}
