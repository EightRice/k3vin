"""
Google Sheets CRUD — treat a sheet tab like a database table.

Setup:
    1. Place credentials.json (OAuth client) and token.json in the same dir as this file.
    2. pip install google-auth google-auth-oauthlib google-api-python-client

Quick start:
    from google_sheets import Sheet

    SPREADSHEET_ID = "1zDnq78KXdIihnd18YC-c3btvVrQ1Gr-oZeT2EUPJwVs"

    # The spreadsheet has a decorative banner in rows 1-4.
    # Row 5 is the header row. Data starts at row 6.
    # Pass header_row=5 so CRUD skips the banner.

    db = Sheet(SPREADSHEET_ID, "Scoreboard", header_row=5)

    db.select()                                  # → list of dicts (all rows)
    db.select(where={"Team / Agent": "AlphaBot"})  # → filtered

    db.insert({"Team / Agent": "AlphaBot", "Score": "100"})
    db.insert([{"Team / Agent": "A"}, {"Team / Agent": "B"}])  # bulk

    db.update(where={"Team / Agent": "AlphaBot"}, set={"Score": "200"})

    db.delete(where={"Team / Agent": "AlphaBot"})

    # To write headers on a fresh tab (writes to the header_row):
    db.create_headers(["Rank", "Team / Agent", "Score"])

Notes:
    - All values are strings (Google Sheets API returns strings).
    - where= does exact string matching on one or more columns.
    - header_row defaults to 1 (no banner). Set it to 5 for the hackathon sheets.
"""

import os
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")


def _get_service():
    """Authenticate and return a Sheets API service instance.

    On first run, opens a browser for OAuth login and saves token.json.
    Subsequent runs reuse (and auto-refresh) that token.
    """
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


class Sheet:
    """SQL-like interface to a single tab in a Google Spreadsheet.

    Args:
        spreadsheet_id: The ID from the spreadsheet URL.
        sheet_name: Tab name (default "Sheet1").
        header_row: Which row (1-indexed) contains column headers.
                    Rows above it are ignored (banner area). Default 1.
    """

    def __init__(self, spreadsheet_id: str, sheet_name: str = "Sheet1", header_row: int = 1):
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.header_row = header_row  # 1-indexed
        self._service = _get_service()
        self._sheets = self._service.spreadsheets()

    # ── helpers ──────────────────────────────────────────────────────

    def _data_range(self) -> str:
        """Range string that starts at the header row (skips banner)."""
        return f"{self.sheet_name}!A{self.header_row}:{self.header_row + 100000}"

    def _read_all(self) -> list[list[str]]:
        """Read everything from header_row downward."""
        result = (
            self._sheets.values()
            .get(spreadsheetId=self.spreadsheet_id, range=self._data_range())
            .execute()
        )
        return result.get("values", [])

    def _headers(self) -> list[str]:
        r = f"{self.sheet_name}!{self.header_row}:{self.header_row}"
        result = (
            self._sheets.values()
            .get(spreadsheetId=self.spreadsheet_id, range=r)
            .execute()
        )
        return result.get("values", [[]])[0]

    @staticmethod
    def _matches(row: dict, where: dict) -> bool:
        return all(row.get(k) == v for k, v in where.items())

    def _row_to_dict(self, headers: list[str], row: list[str]) -> dict:
        padded = row + [""] * (len(headers) - len(row))
        return dict(zip(headers, padded))

    # ── CRUD ─────────────────────────────────────────────────────────

    def select(self, where: Optional[dict] = None) -> list[dict]:
        """Read rows, optionally filtered by where={"col": "value"}."""
        data = self._read_all()
        if not data:
            return []
        headers = data[0]
        rows = [self._row_to_dict(headers, r) for r in data[1:]]
        if where:
            rows = [r for r in rows if self._matches(r, where)]
        return rows

    def insert(self, row: dict | list[dict]) -> int:
        """Append one or more rows. Returns count inserted."""
        headers = self._headers()
        if not headers:
            raise ValueError("No header row found. Use create_headers() first.")

        rows = row if isinstance(row, list) else [row]
        values = [[r.get(h, "") for h in headers] for r in rows]

        self._sheets.values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A{self.header_row}:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        return len(values)

    def update(self, where: dict, set: dict) -> int:
        """Update matching rows. Returns count updated."""
        data = self._read_all()
        if len(data) < 2:
            return 0
        headers = data[0]
        updated = 0
        batch = []

        for i, raw_row in enumerate(data[1:], start=self.header_row + 1):
            row = self._row_to_dict(headers, raw_row)
            if self._matches(row, where):
                row.update(set)
                batch.append({
                    "range": f"{self.sheet_name}!A{i}",
                    "values": [[row.get(h, "") for h in headers]],
                })
                updated += 1

        if batch:
            self._sheets.values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": batch},
            ).execute()
        return updated

    def delete(self, where: dict) -> int:
        """Delete matching rows. Returns count deleted."""
        data = self._read_all()
        if len(data) < 2:
            return 0
        headers = data[0]

        # Collect 0-based sheet row indices for matching rows
        to_delete = []
        for i, raw_row in enumerate(data[1:], start=self.header_row):
            row = self._row_to_dict(headers, raw_row)
            if self._matches(row, where):
                to_delete.append(i)  # 0-based: header_row is already 0-based offset

        if not to_delete:
            return 0

        # Resolve the tab's numeric sheetId
        meta = self._sheets.get(spreadsheetId=self.spreadsheet_id).execute()
        sheet_id = None
        for s in meta["sheets"]:
            if s["properties"]["title"] == self.sheet_name:
                sheet_id = s["properties"]["sheetId"]
                break
        if sheet_id is None:
            raise ValueError(f"Tab '{self.sheet_name}' not found")

        # Delete bottom-up so indices don't shift
        requests = [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    }
                }
            }
            for idx in sorted(to_delete, reverse=True)
        ]

        self._sheets.batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ).execute()
        return len(to_delete)

    def create_headers(self, headers: list[str]):
        """Write column headers to the header row."""
        self._sheets.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.sheet_name}!A{self.header_row}",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
