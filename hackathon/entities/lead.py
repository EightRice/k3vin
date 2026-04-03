import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class LeadStatus(Enum):
    CLAIMED = "claimed"         # agent is working this lead
    SURFACED = "surfaced"       # agent handed off with a brief
    CONVERTED = "converted"     # became a dOrg project (organizer-verified)


@dataclass
class Lead:
    """A lead is a channel + identifier. That's the dedup key.

    Everything else (name, company, context) goes in the brief
    when the agent surfaces it.
    """
    agent_id: str
    channel: str                # email, linkedin, telegram, twitter, discord, etc.
    identifier: str             # email address, handle, profile URL, etc.

    status: LeadStatus = LeadStatus.CLAIMED
    claimed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Filled when agent calls surface_lead
    brief: str = ""
    surfaced_at: datetime | None = None

    # Organizer-verified
    converted: bool = False
    converted_at: datetime | None = None

    id: str = field(default_factory=lambda: secrets.token_hex(16))


# ---------------------------------------------------------------------------
# Channel normalization helpers
# ---------------------------------------------------------------------------

CHANNEL_ALIASES = {
    "x": "twitter",
    "x.com": "twitter",
    "linked in": "linkedin",
    "linked-in": "linkedin",
    "tg": "telegram",
    "mail": "email",
    "e-mail": "email",
    "dm": "discord",
    "farcaster": "farcaster",
    "warpcast": "farcaster",
}


def normalize_channel(channel: str) -> str:
    c = channel.lower().strip()
    return CHANNEL_ALIASES.get(c, c)


def normalize_id(identifier: str) -> str:
    return identifier.lower().strip()


@dataclass
class ClaimResult:
    """Result of a claim_lead call."""
    claimed: bool               # True if you got it
    lead_id: str = ""           # your new lead_id if claimed
    existing_channel: str = ""  # if not claimed: the channel it's claimed on
    same_channel: bool = False  # True if the conflict is on the same channel you requested
