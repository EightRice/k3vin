"""
Hackathon database layer -- SQLite persistence for agents, leads, activities.

Tables mirror the entity dataclasses in entities/.
All registry logic (register, verify_token, claim_lead, etc.) lives here,
backed by SQLite instead of in-memory dicts.

Thread-safe: one connection per thread, WAL mode.
"""

import hashlib
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .entities.agent import Agent, AgentStatus, RegistrationError, NotWhitelisted, AlreadyRegistered
from .entities.lead import Lead, LeadStatus, ClaimResult, normalize_channel, normalize_id
from .entities.activity import Activity, ActivityType

DB_PATH = Path(os.environ.get("HACKATHON_DB_PATH", "./hackathon.db"))

_local = threading.local()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    """One connection per thread (SQLite is not thread-safe by default)."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id         TEXT PRIMARY KEY,
            discord_user_id  TEXT NOT NULL UNIQUE,
            name             TEXT NOT NULL,
            owner_address    TEXT NOT NULL DEFAULT '',
            api_token_hash   TEXT NOT NULL,
            thread_id        INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'registered',
            registered_at    TEXT NOT NULL,
            leads_claimed    INTEGER NOT NULL DEFAULT 0,
            leads_surfaced   INTEGER NOT NULL DEFAULT 0,
            tokens_used      INTEGER NOT NULL DEFAULT 0,
            messages_relayed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS leads (
            id              TEXT PRIMARY KEY,
            agent_id        TEXT NOT NULL REFERENCES agents(agent_id),
            channel         TEXT NOT NULL,
            identifier      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'claimed',
            claimed_at      TEXT NOT NULL,
            brief           TEXT NOT NULL DEFAULT '',
            surfaced_at     TEXT,
            converted       INTEGER NOT NULL DEFAULT 0,
            converted_at    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_leads_agent ON leads(agent_id);
        CREATE INDEX IF NOT EXISTS idx_leads_identifier ON leads(identifier);

        CREATE TABLE IF NOT EXISTS activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    TEXT NOT NULL REFERENCES agents(agent_id),
            type        TEXT NOT NULL,
            details     TEXT NOT NULL DEFAULT '',
            lead_id     TEXT NOT NULL DEFAULT '',
            tokens      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_activities_agent ON activities(agent_id);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _slugify(name: str) -> str:
    """Turn an agent name into a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or "agent"


def _unique_slug(base: str) -> str:
    """Ensure the slug is unique by appending a number if needed."""
    conn = _conn()
    row = conn.execute("SELECT 1 FROM agents WHERE agent_id = ?", (base,)).fetchone()
    if not row:
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        row = conn.execute("SELECT 1 FROM agents WHERE agent_id = ?", (candidate,)).fetchone()
        if not row:
            return candidate
    raise RegistrationError("Could not generate unique agent ID")


def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        discord_user_id=row["discord_user_id"],
        name=row["name"],
        agent_id=row["agent_id"],
        owner_address=row["owner_address"],
        api_token_hash=row["api_token_hash"],
        thread_id=row["thread_id"],
        status=AgentStatus(row["status"]),
        registered_at=_parse_dt(row["registered_at"]),
        leads_claimed=row["leads_claimed"],
        leads_surfaced=row["leads_surfaced"],
        tokens_used=row["tokens_used"],
        messages_relayed=row["messages_relayed"],
    )


def _row_to_lead(row: sqlite3.Row) -> Lead:
    return Lead(
        agent_id=row["agent_id"],
        channel=row["channel"],
        identifier=row["identifier"],
        status=LeadStatus(row["status"]),
        claimed_at=_parse_dt(row["claimed_at"]),
        brief=row["brief"],
        surfaced_at=_parse_dt(row["surfaced_at"]),
        converted=bool(row["converted"]),
        converted_at=_parse_dt(row["converted_at"]),
        id=row["id"],
    )


def _row_to_activity(row: sqlite3.Row) -> Activity:
    return Activity(
        agent_id=row["agent_id"],
        type=ActivityType(row["type"]),
        details=row["details"],
        lead_id=row["lead_id"],
        tokens=row["tokens"],
        created_at=_parse_dt(row["created_at"]),
        id=row["id"],
    )


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

_whitelist: set[str] = set()


def set_whitelist(discord_user_ids: list[str]) -> None:
    global _whitelist
    _whitelist = set(discord_user_ids)


def get_whitelist() -> set[str]:
    return _whitelist.copy()


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

def register(discord_user_id: str, name: str, owner_address: str = "") -> tuple[Agent, str]:
    """Register a new agent. Returns (Agent, plaintext_token).

    The plaintext token is shown once (DM'd to the user) and never stored.
    Only the hash is kept.

    Raises:
        NotWhitelisted: if the Discord user is not in the whitelist
        AlreadyRegistered: if this Discord user already has an agent
    """
    if _whitelist and discord_user_id not in _whitelist:
        raise NotWhitelisted(f"Discord user {discord_user_id} is not a whitelisted finalist")

    conn = _conn()
    existing = conn.execute(
        "SELECT agent_id FROM agents WHERE discord_user_id = ?", (discord_user_id,)
    ).fetchone()
    if existing:
        raise AlreadyRegistered(f"You already registered agent '{existing['agent_id']}'")

    agent_id = _unique_slug(_slugify(name))
    token = secrets.token_hex(32)
    token_hash = _hash_token(token)
    now = _now()

    conn.execute(
        "INSERT INTO agents (agent_id, discord_user_id, name, owner_address, "
        "api_token_hash, status, registered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_id, discord_user_id, name, owner_address, token_hash,
         AgentStatus.REGISTERED.value, now),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    return _row_to_agent(row), token


def verify_token(token: str) -> Agent | None:
    """Verify an API token. Returns the Agent or None."""
    h = _hash_token(token)
    row = _conn().execute(
        "SELECT * FROM agents WHERE api_token_hash = ?", (h,)
    ).fetchone()
    if not row:
        return None
    agent = _row_to_agent(row)
    if agent.status == AgentStatus.DISQUALIFIED:
        return None
    return agent


def get_agent(agent_id: str) -> Agent | None:
    row = _conn().execute(
        "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    return _row_to_agent(row) if row else None


def get_agent_by_discord_user(discord_user_id: str) -> Agent | None:
    row = _conn().execute(
        "SELECT * FROM agents WHERE discord_user_id = ?", (discord_user_id,)
    ).fetchone()
    return _row_to_agent(row) if row else None


def list_agents() -> list[Agent]:
    rows = _conn().execute(
        "SELECT * FROM agents ORDER BY registered_at"
    ).fetchall()
    return [_row_to_agent(r) for r in rows]


def list_active_agents() -> list[Agent]:
    rows = _conn().execute(
        "SELECT * FROM agents WHERE status NOT IN (?, ?)",
        (AgentStatus.DISQUALIFIED.value, AgentStatus.FINISHED.value),
    ).fetchall()
    return [_row_to_agent(r) for r in rows]


def set_agent_status(agent_id: str, status: AgentStatus) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE agents SET status = ? WHERE agent_id = ?",
        (status.value, agent_id),
    )
    conn.commit()


def set_agent_thread(agent_id: str, thread_id: int) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE agents SET thread_id = ? WHERE agent_id = ?",
        (thread_id, agent_id),
    )
    conn.commit()


def update_agent_counters(agent_id: str, **kwargs: int) -> None:
    """Update scoring/observability counters on an agent.

    Accepted keyword args: leads_claimed, leads_surfaced, tokens_used, messages_relayed.
    """
    allowed = {
        "leads_claimed", "leads_surfaced",
        "tokens_used", "messages_relayed",
    }
    updates = []
    params = []
    for key, val in kwargs.items():
        if key not in allowed:
            raise ValueError(f"Unknown counter: {key}")
        updates.append(f"{key} = ?")
        params.append(val)
    if not updates:
        return
    params.append(agent_id)
    conn = _conn()
    conn.execute(
        f"UPDATE agents SET {', '.join(updates)} WHERE agent_id = ?", params
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

def claim_lead(agent_id: str, identifier: str, channel: str) -> ClaimResult:
    """Claim a lead. Takes identifier + the channel you intend to use.

    Returns a ClaimResult:
        - claimed=True, lead_id set -> you got it
        - claimed=False, same_channel=True -> someone else has this exact contact, back off
        - claimed=False, same_channel=False, existing_channel set -> this person is being
          contacted on a different channel, be aware
    """
    norm_id = normalize_id(identifier)
    norm_channel = normalize_channel(channel)
    conn = _conn()

    # Find existing leads with the same normalized identifier by a different agent
    rows = conn.execute(
        "SELECT * FROM leads WHERE LOWER(TRIM(identifier)) = ?", (norm_id,)
    ).fetchall()

    for row in rows:
        if row["agent_id"] == agent_id:
            continue  # you already claimed this person
        existing_norm = normalize_channel(row["channel"])
        if existing_norm == norm_channel:
            return ClaimResult(claimed=False, same_channel=True, existing_channel=row["channel"])
        else:
            return ClaimResult(claimed=False, same_channel=False, existing_channel=row["channel"])

    # No conflict -- insert
    lead_id = secrets.token_hex(16)
    now = _now()
    conn.execute(
        "INSERT INTO leads (id, agent_id, channel, identifier, status, claimed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, agent_id, channel, identifier, LeadStatus.CLAIMED.value, now),
    )
    conn.execute(
        "UPDATE agents SET leads_claimed = leads_claimed + 1 WHERE agent_id = ?",
        (agent_id,),
    )
    conn.commit()

    return ClaimResult(claimed=True, lead_id=lead_id)


def surface_lead(agent_id: str, lead_id: str, brief: str) -> Lead | None:
    """Hand off a lead with a brief. Returns Lead or None if not yours."""
    conn = _conn()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row or row["agent_id"] != agent_id:
        return None

    now = _now()
    conn.execute(
        "UPDATE leads SET status = ?, brief = ?, surfaced_at = ? WHERE id = ?",
        (LeadStatus.SURFACED.value, brief, now, lead_id),
    )
    conn.execute(
        "UPDATE agents SET leads_surfaced = leads_surfaced + 1 WHERE agent_id = ?",
        (agent_id,),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row)


def get_lead(lead_id: str) -> Lead | None:
    row = _conn().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def get_leads_by_agent(agent_id: str) -> list[Lead]:
    rows = _conn().execute(
        "SELECT * FROM leads WHERE agent_id = ? ORDER BY claimed_at DESC",
        (agent_id,),
    ).fetchall()
    return [_row_to_lead(r) for r in rows]


def get_surfaced_leads() -> list[Lead]:
    """The common pool -- all leads handed off by agents."""
    rows = _conn().execute(
        "SELECT * FROM leads WHERE status IN (?, ?)",
        (LeadStatus.SURFACED.value, LeadStatus.CONVERTED.value),
    ).fetchall()
    return [_row_to_lead(r) for r in rows]


def get_all_leads() -> list[Lead]:
    rows = _conn().execute("SELECT * FROM leads ORDER BY claimed_at DESC").fetchall()
    return [_row_to_lead(r) for r in rows]


def mark_lead_converted(lead_id: str) -> Lead | None:
    """Organizer marks a lead as converted."""
    conn = _conn()
    now = _now()
    conn.execute(
        "UPDATE leads SET status = ?, converted = 1, converted_at = ? WHERE id = ?",
        (LeadStatus.CONVERTED.value, now, lead_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

def record_activity(
    agent_id: str,
    activity_type: str | ActivityType,
    details: str = "",
    lead_id: str = "",
    tokens: int = 0,
) -> Activity:
    """Record an agent activity. Returns the Activity."""
    conn = _conn()
    now = _now()
    type_val = activity_type.value if isinstance(activity_type, ActivityType) else activity_type
    conn.execute(
        "INSERT INTO activities (agent_id, type, details, lead_id, tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, type_val, details, lead_id, tokens, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM activities WHERE rowid = last_insert_rowid()"
    ).fetchone()
    return _row_to_activity(row)


def get_agent_activities(agent_id: str, limit: int = 50) -> list[Activity]:
    rows = _conn().execute(
        "SELECT * FROM activities WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
        (agent_id, limit),
    ).fetchall()
    return [_row_to_activity(r) for r in rows]


def get_all_activities(limit: int = 100) -> list[Activity]:
    rows = _conn().execute(
        "SELECT * FROM activities ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_activity(r) for r in rows]


# ---------------------------------------------------------------------------
# Scoring / leaderboard
# ---------------------------------------------------------------------------

def get_scoreboard() -> list[Agent]:
    """All agents ordered by leads_surfaced descending."""
    rows = _conn().execute(
        "SELECT * FROM agents ORDER BY leads_surfaced DESC, leads_claimed DESC"
    ).fetchall()
    return [_row_to_agent(r) for r in rows]
