"""Generate a public static page from a private Google Sheet."""

from __future__ import annotations

import html
import json
import os
import re
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
ID_HEADER = "ID"
VISIBILITY_HEADER = "Leer"
REQUIRED_HEADERS = (ID_HEADER, *DISPLAY_HEADERS, VISIBILITY_HEADER)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "public"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


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


def visible_beers(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        raise ValueError("The sheet is empty; the first row must contain the column headers.")

    header_row = rows[0]
    missing = [name for name in REQUIRED_HEADERS if name not in header_row]
    if missing:
        raise ValueError(
            "Could not find expected header(s) in the first row: " + ", ".join(missing)
        )

    id_index = header_row.index(ID_HEADER)
    column_indexes = {name: header_row.index(name) for name in DISPLAY_HEADERS}
    visibility_index = header_row.index(VISIBILITY_HEADER)
    beers = []
    seen_ids: dict[str, int] = {}
    for row_number, row in enumerate(rows[1:], start=2):  # Row 1 is the header.
        visibility = row[visibility_index].strip() if visibility_index < len(row) else ""
        if visibility != "0":
            continue
        beer_id = row[id_index].strip() if id_index < len(row) else ""
        if not beer_id:
            raise ValueError(f"Visible beer on sheet row {row_number} has no ID.")
        if not ID_PATTERN.fullmatch(beer_id):
            raise ValueError(
                f"ID {beer_id!r} on sheet row {row_number} is invalid. "
                "Use only letters, numbers, hyphens, and underscores; start with a letter or number."
            )
        if beer_id in seen_ids:
            raise ValueError(
                f"Duplicate visible beer ID {beer_id!r} on sheet rows "
                f"{seen_ids[beer_id]} and {row_number}."
            )
        seen_ids[beer_id] = row_number

        values = {
            name: row[index] if index < len(row) else ""
            for name, index in column_indexes.items()
        }
        if any(value.strip() for value in values.values()):
            beers.append({ID_HEADER: beer_id, **values})
    return beers


def render_beer_index(beers: list[dict[str, str]]) -> str:
    if beers:
        option_rows = []
        for beer in beers:
            label = " — ".join(
                value for value in (beer["Biername"], beer["Sud"]) if value
            )
            option_rows.append(
                f'<option value="{escape(beer[ID_HEADER])}/">{escape(label)}</option>'
            )
        options = "\n".join(option_rows)
        content = f"""<form action="#" method="get" onsubmit="if (this.beer.value) {{ window.location.href = this.beer.value; }} return false;">
    <label for="beer">Bier auswählen</label>
    <div class="selector-row">
      <select id="beer" name="beer" required>
        <option value="">Bitte auswählen …</option>
{options}
      </select>
      <button type="submit">Details anzeigen</button>
    </div>
  </form>"""
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
    form {{ max-width: 620px; }}
    label {{ display: block; margin-bottom: .5rem; font-weight: 650; }}
    .selector-row {{ display: flex; gap: .75rem; }}
    select, button {{ min-height: 2.8rem; padding: .65rem .8rem; border: 1px solid #cbd2c8; border-radius: .45rem; font: inherit; }}
    select {{ flex: 1; min-width: 0; background: white; }}
    button {{ border: 0; background: #31513b; color: white; font-weight: 650; cursor: pointer; }}
    button:hover, button:focus-visible {{ background: #243e2d; }}
    .empty {{ padding: 1rem; background: white; border: 1px solid #dfe3dc; border-radius: .75rem; }}
    footer {{ margin-top: 1rem; color: #626a60; font-size: .875rem; }}
    @media (max-width: 520px) {{ .selector-row {{ flex-direction: column; }} }}
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


def render_beer_detail(beer: dict[str, str]) -> str:
    details = "\n".join(
        f'<div class="detail"><dt>{escape(name)}</dt><dd>{escape(beer[name]) or "—"}</dd></div>'
        for name in DISPLAY_HEADERS
        if name != "Biername"
    )
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Details zu {escape(beer['Biername'])}.">
  <title>{escape(beer['Biername'])} | Brausau</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; color: #20251f; background: #f5f6f2; }}
    body {{ margin: 0; }}
    main {{ width: min(760px, calc(100% - 2rem)); margin: 3rem auto; }}
    nav {{ display: flex; gap: 1rem; margin-bottom: 2rem; }}
    nav a {{ color: #31513b; }}
    h1 {{ margin-bottom: 1.5rem; }}
    dl {{ margin: 0; overflow: hidden; background: white; border: 1px solid #dfe3dc; border-radius: .75rem; }}
    .detail {{ display: grid; grid-template-columns: minmax(9rem, 1fr) 2fr; gap: 1rem; padding: .85rem 1rem; border-bottom: 1px solid #e8ebe5; }}
    .detail:last-child {{ border-bottom: 0; }}
    dt {{ font-weight: 650; }}
    dd {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
    @media (max-width: 520px) {{ .detail {{ grid-template-columns: 1fr; gap: .25rem; }} }}
  </style>
</head>
<body>
<main>
  <nav><a href="../../">Home</a><a href="../">Zur Bierliste</a></nav>
  <h1>{escape(beer['Biername'])}</h1>
  <dl>
{details}
  </dl>
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

    beers = visible_beers(sheet_values())
    beers_dir = OUTPUT_DIR / "beers"
    if beers_dir.exists():
        shutil.rmtree(beers_dir)
    beers_dir.mkdir(parents=True)

    index_path = beers_dir / "index.html"
    index_path.write_text(render_beer_index(beers), encoding="utf-8")
    detail_paths = []
    for beer in beers:
        detail_dir = beers_dir / beer[ID_HEADER]
        detail_dir.mkdir()
        detail_path = detail_dir / "index.html"
        detail_path.write_text(render_beer_detail(beer), encoding="utf-8")
        detail_paths.append(detail_path)
    print(
        f"Copied home page to {OUTPUT_DIR / 'index.html'}, generated {index_path}, "
        f"and generated {len(detail_paths)} beer detail page(s)."
    )


if __name__ == "__main__":
    main()
