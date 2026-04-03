from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ActivityType(Enum):
    """What the agent did. Logged for observability, not all types are scored."""
    # Lead lifecycle (scored)
    LEAD_CLAIMED = "lead_claimed"
    LEAD_CONTACTED = "lead_contacted"
    LEAD_RESPONDED = "lead_responded"
    LEAD_MEETING = "lead_meeting"           # organizer-verified
    LEAD_CONVERTED = "lead_converted"       # organizer-verified

    # Agent output (observed, not scored)
    MESSAGE_RELAYED = "message_relayed"     # posted to Discord thread
    THOUGHT = "thought"                     # working thought shared in thread

    # System events
    AGENT_STARTED = "agent_started"
    AGENT_PAUSED = "agent_paused"
    AGENT_RESUMED = "agent_resumed"
    DISQUALIFIED = "disqualified"


@dataclass
class Activity:
    """A single logged action by an agent. Append-only."""
    agent_id: str
    type: ActivityType
    details: str = ""           # human-readable description
    lead_id: str = ""           # if related to a specific lead
    tokens: int = 0             # LLM tokens consumed for this action (if applicable)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Auto-generated
    id: int = 0                 # auto-increment, set by DB
