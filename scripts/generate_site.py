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
COLOR_HEADER = "Farbe"
REQUIRED_HEADERS = (ID_HEADER, *DISPLAY_HEADERS, COLOR_HEADER, VISIBILITY_HEADER)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "public"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


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
    color_index = header_row.index(COLOR_HEADER)
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
        color = row[color_index].strip() if color_index < len(row) else ""
        if not COLOR_PATTERN.fullmatch(color):
            raise ValueError(
                f"Invalid or missing Farbe value on sheet row {row_number}: {color!r}. "
                "Use a six-digit hex color such as #D64B32."
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
            beers.append({ID_HEADER: beer_id, COLOR_HEADER: color, **values})
    return beers


def render_beer_index(beers: list[dict[str, str]]) -> str:
    if beers:
        cards = []
        for beer in beers:
            cards.append(
                f'<a class="beer-card" href="{escape(beer[ID_HEADER])}/" '
                f'aria-label="{escape(beer["Biername"] or beer[ID_HEADER])}" '
                f'style="--beer-color: {beer[COLOR_HEADER]}">'
                f'<span class="cap" aria-hidden="true"></span>'
                "</a>"
            )
        content = f'<div class="beer-grid" id="beer-grid">{chr(10).join(cards)}</div>'
    else:
        content = '<p class="empty">No data available.</p>'

    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Die Brausau-Bierliste.">
  <title>Bierliste | Brausau</title>
  <link rel="icon" type="image/svg+xml" href="../assets/favicon.svg">
  <link rel="icon" type="image/png" sizes="180x180" href="../assets/apple-touch-icon.png">
  <link rel="apple-touch-icon" href="../assets/apple-touch-icon.png">
  <style>
    :root {{ color-scheme: light; font-family: system-ui, sans-serif; color: #20251f; background: #f5f6f2; }}
    body {{ margin: 0; }}
    main {{ width: min(900px, calc(100% - 2rem)); margin: 3rem auto; }}
    nav {{ margin-bottom: 2rem; }}
    nav a {{ color: #31513b; }}
    h1 {{ margin-bottom: 1.5rem; }}
    .beer-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 1.25rem .75rem; align-items: center; }}
    .beer-card {{ min-width: 0; min-height: 130px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .4rem; padding: .5rem; border-radius: .75rem; color: inherit; text-align: center; text-decoration: none; transform: translate(var(--shift-x, 0), var(--shift-y, 0)) rotate(var(--tilt, 0deg)); transition: transform .18s ease, background-color .18s ease; }}
    .beer-card:hover, .beer-card:focus-visible {{ background: white; transform: translate(var(--shift-x, 0), var(--shift-y, 0)) rotate(0deg) scale(1.04); outline: 2px solid #31513b; outline-offset: 2px; }}
    .cap {{ width: 84px; aspect-ratio: 142.08 / 130.56; display: block; background-color: var(--beer-color); -webkit-mask: url("../assets/kronkorken.svg") center / contain no-repeat; mask: url("../assets/kronkorken.svg") center / contain no-repeat; filter: drop-shadow(0 3px 2px rgb(0 0 0 / 20%)); }}
    .empty {{ padding: 1rem; background: white; border: 1px solid #dfe3dc; border-radius: .75rem; }}
    footer {{ margin-top: 1rem; color: #626a60; font-size: .875rem; }}
    @media (prefers-reduced-motion: reduce) {{ .beer-card {{ transition: none; }} }}
  </style>
</head>
<body>
<main>
  <nav><a href="../">Home</a></nav>
  <h1>Bierliste</h1>
  {content}
</main>
<script>
  const grid = document.getElementById("beer-grid");
  if (grid) {{
    const cards = Array.from(grid.children);
    for (let i = cards.length - 1; i > 0; i--) {{
      const j = Math.floor(Math.random() * (i + 1));
      [cards[i], cards[j]] = [cards[j], cards[i]];
    }}
    cards.forEach((card) => {{
      card.style.setProperty("--tilt", `${{(Math.random() * 10 - 5).toFixed(1)}}deg`);
      card.style.setProperty("--shift-x", `${{(Math.random() * 10 - 5).toFixed(1)}}px`);
      card.style.setProperty("--shift-y", `${{(Math.random() * 8 - 4).toFixed(1)}}px`);
      grid.appendChild(card);
    }});
  }}
</script>
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
  <link rel="icon" type="image/svg+xml" href="../../assets/favicon.svg">
  <link rel="icon" type="image/png" sizes="180x180" href="../../assets/apple-touch-icon.png">
  <link rel="apple-touch-icon" href="../../assets/apple-touch-icon.png">
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
