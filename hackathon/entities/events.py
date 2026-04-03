"""
Websocket stream protocol for agent observability (optional).

Connection: ws://{host}/ws?token={api_token}

The agent streams raw text over the websocket. Any text -- working thoughts,
reasoning, status updates, whatever they want to share. We render it in a
live-updating Discord embed in their thread, like watching someone type.

This is the "full" integration level. The "basic" level is just the MCP
tools (claim_lead, check_lead, report_status) + optionally send_message.

Wire format: just text frames. No JSON structure required.
    "Researching Acme Corp..."
    "Found their CTO on LinkedIn, looks like they're exploring DAO tooling"
    "Drafting outreach email..."

We buffer incoming text and update the Discord embed periodically (every ~1s).
When the agent stops sending (timeout or explicit close), we finalize the embed.
"""
