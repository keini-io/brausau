"""Generate a public static page from a private Google Sheet."""

from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account


SHEET_ID = os.environ.get(
    "GOOGLE_SHEET_ID", "1Vid837zHDbtGlQy9gt4F6RzCQuFDb4-6EhgV3y-iazQ"
)
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "biere")
DISPLAY_HEADERS = (
    "Sud",
    "Biername",
    "Bierstil",
    "Gebraut am",
    "Alc",
    "Stammwürze",
    "IBU",
    "Trinktemperatur",
    "Best before",
    "Passt gut zu",
    "Info",
    "Zutaten",
)
VISIBILITY_HEADER = "Leer"
REQUIRED_HEADERS = (*DISPLAY_HEADERS, VISIBILITY_HEADER)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "public"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def sheet_values() -> list[list[str]]:
    """Read the sheet using a service-account key supplied through the environment."""
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON to the service-account JSON.")

    credentials_info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
    session = AuthorizedSession(credentials)
    escaped_tab = SHEET_TAB.replace("'", "''")
    range_name = f"'{escaped_tab}'!A:ZZ"
    url = (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        f"{quote(SHEET_ID, safe='')}/values/{quote(range_name, safe='')}"
        "?valueRenderOption=FORMATTED_VALUE"
    )
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.json().get("values", [])


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def render(rows: list[list[str]]) -> str:
    if not rows:
        raise ValueError("The sheet is empty; the first row must contain the column headers.")

    header_row = rows[0]
    missing = [name for name in REQUIRED_HEADERS if name not in header_row]
    if missing:
        raise ValueError(
            "Could not find expected header(s) in the first row: " + ", ".join(missing)
        )

    column_indexes = [header_row.index(name) for name in DISPLAY_HEADERS]
    visibility_index = header_row.index(VISIBILITY_HEADER)
    table_headers = "".join(
        f'<th scope="col">{escape(name)}</th>' for name in DISPLAY_HEADERS
    )
    records = []
    for row in rows[1:]:  # The first row contains headers, never display it as data.
        visibility = row[visibility_index].strip() if visibility_index < len(row) else ""
        if visibility != "0":
            continue
        values = [row[index] if index < len(row) else "" for index in column_indexes]
        if any(value.strip() for value in values):
            records.append(values)

    if records:
        table_body = "\n".join(
            "      <tr>"
            + "".join(f"<td>{escape(value)}</td>" for value in record)
            + "</tr>"
            for record in records
        )
        content = f"""<div class="table-wrap">
    <table>
      <thead><tr>{table_headers}</tr></thead>
      <tbody>
{table_body}
      </tbody>
    </table>
  </div>"""
    else:
        content = '<p class="empty">No data available.</p>'

    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Die Brausau-Bierliste.">
  <title>Bierliste | Brausau</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; color: #20251f; background: #f5f6f2; }}
    body {{ margin: 0; }}
    main {{ width: min(900px, calc(100% - 2rem)); margin: 3rem auto; }}
    nav {{ margin-bottom: 2rem; }}
    nav a {{ color: #31513b; }}
    h1 {{ margin-bottom: 1.5rem; }}
    .table-wrap {{ overflow-x: auto; background: white; border: 1px solid #dfe3dc; border-radius: .75rem; }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; }}
    th, td {{ padding: .85rem 1rem; border-bottom: 1px solid #e8ebe5; }}
    th {{ background: #edf1e9; }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    .empty {{ padding: 1rem; background: white; border: 1px solid #dfe3dc; border-radius: .75rem; }}
    footer {{ margin-top: 1rem; color: #626a60; font-size: .875rem; }}
  </style>
</head>
<body>
<main>
  <nav><a href="../">Home</a></nav>
  <h1>Bierliste</h1>
  {content}
  <footer>Die Daten werden ungefähr stündlich aktualisiert.</footer>
</main>
</body>
</html>
"""


def main() -> None:
    source_dir = Path("site")
    if not (source_dir / "index.html").is_file():
        raise FileNotFoundError("Missing site/index.html home page template.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, OUTPUT_DIR, dirs_exist_ok=True)

    beers_path = OUTPUT_DIR / "beers" / "index.html"
    beers_path.parent.mkdir(parents=True, exist_ok=True)
    beers_path.write_text(render(sheet_values()), encoding="utf-8")
    print(f"Copied home page to {OUTPUT_DIR / 'index.html'} and generated {beers_path}")


if __name__ == "__main__":
    main()
