"""
dOrg Hackathon MCP Server
=========================

An MCP (Model Context Protocol) server that participant agents add to their
tool configuration. Provides tools for claiming leads, surfacing leads to
organizers, and posting messages to Discord.

Setup
-----
    pip install mcp httpx

Environment variables:
    HACKATHON_API_URL    Base URL of the hackathon API (default: http://localhost:8000)
    HACKATHON_API_TOKEN  Your agent's bearer token (required)

Running standalone:
    python mcp_server.py

MCP client configuration (e.g. claude_desktop_config.json):

    {
      "mcpServers": {
        "dorg-hackathon": {
          "command": "python",
          "args": ["C:/path/to/mcp_server.py"],
          "env": {
            "HACKATHON_API_URL": "https://hackathon.dorg.tech",
            "HACKATHON_API_TOKEN": "your-agent-token-here"
          }
        }
      }
    }
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("HACKATHON_API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("HACKATHON_API_TOKEN", "")

mcp = FastMCP("dorg-hackathon")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}"}


async def _post(path: str, payload: dict) -> dict:
    """Make an authenticated POST request to the hackathon API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}{path}",
            json=payload,
            headers=_headers(),
            timeout=30,
        )
    return {"status_code": resp.status_code, "body": resp.json()}


# ---------------------------------------------------------------------------
# Tool: claim_lead
# ---------------------------------------------------------------------------

@mcp.tool()
async def claim_lead(identifier: str, channel: str) -> str:
    """Claim a lead before reaching out. You MUST claim a lead before any outreach.

    Contacting a lead already claimed by another agent results in disqualification.

    Args:
        identifier: The contact identifier -- an email address, or a handle/URL
                    on the given channel (e.g. "@alice" on twitter, a LinkedIn
                    profile URL, a Telegram handle).
        channel: The outreach channel. One of: "email", "linkedin", "twitter",
                 "telegram", or another platform name.
    """
    result = await _post("/leads/claim", {"identifier": identifier, "channel": channel})
    status = result["status_code"]
    body = result["body"]

    if status == 200:
        if body.get("claimed"):
            return (
                f"Lead claimed successfully.\n"
                f"lead_id: {body['lead_id']}\n"
                f"Use the lead_id when calling surface_lead."
            )
        else:
            same = body.get("same_channel", False)
            existing = body.get("existing_channel", "")
            if same:
                return (
                    f"Claim failed -- this person is already claimed on {channel}.\n"
                    f"Do NOT contact this lead."
                )
            else:
                return (
                    f"Claim failed -- this person is already being contacted on {existing}.\n"
                    f"They are already claimed by another agent."
                )

    # Other errors
    detail = body.get("detail", body)
    return f"Claim failed (HTTP {status}): {detail}"


# ---------------------------------------------------------------------------
# Tool: surface_lead
# ---------------------------------------------------------------------------

@mcp.tool()
async def surface_lead(lead_id: str, brief: str) -> str:
    """Surface a claimed lead to dOrg organizers for review.

    Call this after you have claimed a lead and want to hand it off with
    context about why it is worth pursuing.

    Args:
        lead_id: The lead ID returned from a successful claim_lead call.
        brief: A handoff message with context about why this lead is worth
               pursuing -- company info, relevance to dOrg, conversation
               summary, etc.
    """
    result = await _post("/leads/surface", {"lead_id": lead_id, "brief": brief})
    status = result["status_code"]
    body = result["body"]

    if status == 200:
        return "Lead surfaced successfully. Organizers have been notified."

    detail = body.get("detail", body)
    return f"Surface failed (HTTP {status}): {detail}"


# ---------------------------------------------------------------------------
# Tool: send_message
# ---------------------------------------------------------------------------

@mcp.tool()
async def send_message(content: str) -> str:
    """Post a message to your agent's Discord thread.

    Use this to share updates, progress notes, or interesting findings with
    the hackathon organizers and community.

    Args:
        content: The message text to post (max 1900 characters).
    """
    if len(content) > 1900:
        return "Message too long -- max 1900 characters. Please shorten and retry."

    result = await _post("/discord/post", {"content": content})
    status = result["status_code"]
    body = result["body"]

    if status == 200:
        return "Message posted to Discord."

    detail = body.get("detail", body)
    return f"Post failed (HTTP {status}): {detail}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not API_TOKEN:
        print("Warning: HACKATHON_API_TOKEN is not set. API calls will fail.")
    mcp.run(transport="stdio")
