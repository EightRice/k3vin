"""
Google Sheets sync -- keeps the spreadsheet in sync with the hackathon DB.

Two tabs:
    Scoreboard: agent rankings, updated on every sync
    Leads: all leads with status, briefs, conversion flags

Also provides the conversion reporting mechanism:
    organizers (whitelisted Discord users) can mark leads as converted.

Usage:
    from sheets_sync import sync_scoreboard, sync_leads, report_conversion
"""
import logging
import os

import db
from google_sheets import Sheet

log = logging.getLogger("hackathon.sheets")

SPREADSHEET_ID = os.environ.get(
    "HACKATHON_SPREADSHEET_ID",
    "1zDnq78KXdIihnd18YC-c3btvVrQ1Gr-oZeT2EUPJwVs",
)
HEADER_ROW = 5

# Organizer whitelist -- Discord user IDs allowed to report conversions
_organizer_whitelist: set[str] = set()


def set_organizer_whitelist(discord_user_ids: list[str]) -> None:
    global _organizer_whitelist
    _organizer_whitelist = set(discord_user_ids)


def get_organizer_whitelist() -> set[str]:
    return _organizer_whitelist.copy()


def is_organizer(discord_user_id: str) -> bool:
    return not _organizer_whitelist or discord_user_id in _organizer_whitelist


# ---------------------------------------------------------------------------
# Sync functions -- push DB state to sheets
# ---------------------------------------------------------------------------

def sync_scoreboard() -> int:
    """Overwrite the Scoreboard tab with current DB state. Returns row count."""
    sheet = Sheet(SPREADSHEET_ID, "Scoreboard", header_row=HEADER_ROW)
    agents = db.get_scoreboard()

    # Clear existing data rows
    existing = sheet.select()
    if existing:
        # Delete all by matching any row (brute force clear)
        for row in existing:
            try:
                sheet.delete(where={"Agent": row.get("Agent", "")})
            except Exception:
                pass

    # Insert fresh
    rows = []
    for i, a in enumerate(agents):
        conversions = len([
            l for l in db.get_leads_by_agent(a.agent_id)
            if l.converted
        ])
        rows.append({
            "Rank": str(i + 1),
            "Agent": a.name,
            "Owner": a.discord_user_id,
            "Leads Claimed": str(a.leads_claimed),
            "Leads Surfaced": str(a.leads_surfaced),
            "Conversions": str(conversions),
            "Status": a.status.value,
        })

    if rows:
        sheet.insert(rows)
    log.info("Synced scoreboard: %d agents", len(rows))
    return len(rows)


def sync_leads() -> int:
    """Overwrite the Leads tab with current DB state. Returns row count."""
    sheet = Sheet(SPREADSHEET_ID, "Leads", header_row=HEADER_ROW)
    leads = db.get_all_leads()

    # Clear existing
    existing = sheet.select()
    if existing:
        for row in existing:
            try:
                sheet.delete(where={"Lead ID": row.get("Lead ID", "")})
            except Exception:
                pass

    # Insert fresh
    rows = []
    for l in leads:
        agent = db.get_agent(l.agent_id)
        agent_name = agent.name if agent else l.agent_id
        rows.append({
            "Lead ID": l.id,
            "Agent": agent_name,
            "Channel": l.channel,
            "Identifier": l.identifier,
            "Status": l.status.value,
            "Claimed At": str(l.claimed_at) if l.claimed_at else "",
            "Brief": l.brief,
            "Surfaced At": str(l.surfaced_at) if l.surfaced_at else "",
            "Converted": "Yes" if l.converted else "",
        })

    if rows:
        sheet.insert(rows)
    log.info("Synced leads: %d leads", len(rows))
    return len(rows)


def sync_all() -> None:
    """Sync both tabs."""
    sync_scoreboard()
    sync_leads()


# ---------------------------------------------------------------------------
# Conversion reporting
# ---------------------------------------------------------------------------

def report_conversion(lead_id: str, reporter_discord_id: str, note: str = "") -> dict:
    """Mark a lead as converted. Only organizers can do this.

    Returns:
        {"ok": True, "lead": Lead, "agent_name": str} on success
        {"ok": False, "error": str} on failure
    """
    if not is_organizer(reporter_discord_id):
        return {"ok": False, "error": "Not authorized. Only organizers can report conversions."}

    lead = db.get_lead(lead_id)
    if not lead:
        return {"ok": False, "error": f"Lead {lead_id} not found."}

    if lead.converted:
        return {"ok": False, "error": f"Lead {lead_id} is already marked as converted."}

    if lead.status.value != "surfaced":
        return {"ok": False, "error": f"Lead must be surfaced before it can be converted. Current status: {lead.status.value}"}

    updated = db.mark_lead_converted(lead_id)
    if not updated:
        return {"ok": False, "error": "Failed to update lead."}

    agent = db.get_agent(lead.agent_id)
    agent_name = agent.name if agent else lead.agent_id

    # Sync sheets
    try:
        sync_all()
    except Exception:
        log.exception("Failed to sync sheets after conversion")

    return {"ok": True, "lead": updated, "agent_name": agent_name, "note": note}
