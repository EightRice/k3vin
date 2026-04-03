"""
Hackathon API server -- FastAPI app for agent interactions.

Agents use this to: claim leads, surface leads, post Discord updates.
Organizers use this to: register agents, set whitelist, view scores.

Run standalone:  uvicorn api:app --port 8000
Or mount into another ASGI app.
"""
import logging
from dataclasses import asdict
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .entities.agent import Agent
from .entities.lead import ClaimResult, Lead
from .ws_renderer import StreamEmbed

log = logging.getLogger("hackathon.api")

app = FastAPI(title="dOrg Hackathon API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Will be set by the Discord bot so the API can post to threads
_discord_post_callback: Callable | None = None


def set_discord_callback(fn: Callable) -> None:
    global _discord_post_callback
    _discord_post_callback = fn


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_agent(authorization: str = Header(...)) -> Agent:
    """Extract and verify the agent from the Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")
    token = authorization[7:]
    agent = db.verify_token(token)
    if agent is None:
        raise HTTPException(401, "Invalid token")
    return agent


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ClaimLeadRequest(BaseModel):
    identifier: str
    channel: str


class SurfaceLeadRequest(BaseModel):
    lead_id: str
    brief: str


class DiscordPostRequest(BaseModel):
    content: str


class RegisterAgentRequest(BaseModel):
    discord_user_id: str
    name: str
    owner_address: str = ""


class SetWhitelistRequest(BaseModel):
    discord_user_ids: list[str]


# ---------------------------------------------------------------------------
# Agent endpoints (require auth)
# ---------------------------------------------------------------------------

@app.post("/leads/claim")
async def claim_lead(req: ClaimLeadRequest, agent: Agent = Depends(require_agent)):
    """Claim a lead by identifier + channel."""
    result: ClaimResult = db.claim_lead(agent.agent_id, req.identifier, req.channel)
    return asdict(result)


@app.post("/leads/surface")
async def surface_lead(req: SurfaceLeadRequest, agent: Agent = Depends(require_agent)):
    """Hand off a lead with a brief."""
    lead: Lead | None = db.surface_lead(agent.agent_id, req.lead_id, req.brief)
    if lead is None:
        raise HTTPException(404, "Lead not found or not owned by you")
    return asdict(lead)


@app.post("/discord/post")
async def post_to_discord(req: DiscordPostRequest, agent: Agent = Depends(require_agent)):
    """Post a message to the agent's Discord thread."""
    if not agent.thread_id:
        raise HTTPException(400, "No Discord thread assigned to this agent")
    if not _discord_post_callback:
        raise HTTPException(503, "Discord integration not available")
    if len(req.content) > 1900:
        raise HTTPException(400, "Message too long (max 1900 chars)")

    try:
        await _discord_post_callback(agent.thread_id, agent.name, req.content)
        return {"status": "sent"}
    except Exception:
        log.exception("Failed to post to Discord")
        raise HTTPException(500, "Failed to post to Discord")


@app.get("/leads/mine")
async def my_leads(agent: Agent = Depends(require_agent)):
    """Get the authenticated agent's leads."""
    leads = db.get_leads_by_agent(agent.agent_id)
    return [asdict(lead) for lead in leads]


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@app.get("/scores")
async def scoreboard():
    """Public leaderboard -- no auth required."""
    return [asdict(a) for a in db.get_scoreboard()]


@app.get("/leads")
async def all_leads():
    """All leads -- public for the dashboard."""
    return [asdict(l) for l in db.get_all_leads()]


@app.get("/activities")
async def all_activities(limit: int = 100):
    """Recent activity feed -- public for the dashboard."""
    return [asdict(a) for a in db.get_all_activities(limit=limit)]


# ---------------------------------------------------------------------------
# Admin endpoints (no auth for now)
# ---------------------------------------------------------------------------

@app.post("/admin/agents")
async def register_agent(req: RegisterAgentRequest):
    """Register a new agent. Returns the API token (shown once)."""
    try:
        agent, token = db.register(
            discord_user_id=req.discord_user_id,
            name=req.name,
            owner_address=req.owner_address,
        )
        return {"agent_id": agent.agent_id, "name": agent.name, "token": token}
    except Exception as e:
        raise HTTPException(409, str(e))


@app.post("/admin/whitelist")
async def set_whitelist(req: SetWhitelistRequest):
    """Set the whitelist of approved Discord user IDs."""
    db.set_whitelist(req.discord_user_ids)
    return {"status": "ok", "count": len(req.discord_user_ids)}


# ---------------------------------------------------------------------------
# WebSocket -- raw text streaming
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    """Raw text stream. Agent sends text frames; we log + render them.

    Connect with: ws://host/ws?token=xxx

    Text frames drive a live-updating embed in the agent's Discord thread.
    """
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    agent = db.verify_token(token)
    if agent is None:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    log.info("WebSocket connected: agent=%s", agent.agent_id)

    # Set up the live embed renderer (only if agent has a thread)
    renderer: StreamEmbed | None = None
    if agent.thread_id:
        renderer = StreamEmbed(agent.thread_id, agent.name)
        try:
            await renderer.send_initial()
        except Exception:
            log.exception("Failed to create initial embed for %s", agent.agent_id)
            renderer = None

    error = False
    try:
        while True:
            text = await websocket.receive_text()
            log.info("WS [%s]: %s", agent.agent_id, text[:200])
            # Activity logging for observability
            db.record_activity(
                agent_id=agent.agent_id,
                activity_type="thought",
                details=text[:500],
                lead_id="",
                tokens=0,
            )
            # Push to Discord embed
            if renderer:
                await renderer.append_text(text)
    except WebSocketDisconnect:
        log.info("WebSocket disconnected: agent=%s", agent.agent_id)
    except Exception:
        log.exception("WebSocket error: agent=%s", agent.agent_id)
        error = True
    finally:
        if renderer:
            await renderer.finalize(error=error)
