from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AgentStatus(Enum):
    REGISTERED = "registered"       # signed up, not yet active
    ACTIVE = "active"               # competition is live, agent is running
    PAUSED = "paused"               # voluntarily paused by participant
    DISQUALIFIED = "disqualified"   # rule violation (e.g. contacted claimed lead)
    FINISHED = "finished"           # competition ended


class RegistrationError(Exception):
    pass


class NotWhitelisted(RegistrationError):
    pass


class AlreadyRegistered(RegistrationError):
    pass


@dataclass
class Agent:
    """A competing sales agent in the hackathon.

    Registration flow:
    1. Organizers whitelist 10 Discord user IDs (the finalists)
    2. Finalist DMs Kevin or uses a command to register their agent
    3. Kevin DMs back a one-time API token
    4. Finalist provides the token to their agent for all API/websocket auth

    One agent per whitelisted Discord user.
    """
    # Identity -- the human owner
    discord_user_id: str        # whitelisted Discord user ID (primary identity)
    name: str                   # agent display name (e.g. "SalesBot 3000")
    agent_id: str = ""          # slug for API/URLs, derived from name if empty
    owner_address: str = ""     # 0x address for payment/attribution (not for auth)

    # Auth
    api_token_hash: str = ""    # hashed bearer token, generated at registration

    # Discord
    thread_id: int = 0          # dedicated Discord thread for this agent

    # Status
    status: AgentStatus = AgentStatus.REGISTERED
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Counters (denormalized for quick scoreboard reads) ---
    leads_claimed: int = 0          # total unique leads reserved
    leads_surfaced: int = 0         # leads handed off with a brief

    # --- Observability (tracked, not scored) ---
    tokens_used: int = 0            # total LLM tokens consumed
    messages_relayed: int = 0       # messages posted to Discord thread

