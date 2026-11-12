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
UNIT_SUFFIXES = {
    "Alc": " Vol. % Alc",
    "Stammwürze": "°P",
    "Trinktemperatur": "°C",
}
DISPLAY_LABELS = {
    "Trinktemperatur": "Optimale Trinktemperatur",
}
HEADER_ALIASES = {
    "Trinktemperatur": ("Optimale Trinktemperatur",),
}
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


def header_index(header_row: list[str], name: str) -> int | None:
    for candidate in (name, *HEADER_ALIASES.get(name, ())):
        if candidate in header_row:
            return header_row.index(candidate)
    return None


def visible_beers(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        raise ValueError("The sheet is empty; the first row must contain the column headers.")

    header_row = rows[0]
    missing = [name for name in REQUIRED_HEADERS if header_index(header_row, name) is None]
    if missing:
        raise ValueError(
            "Could not find expected header(s) in the first row: " + ", ".join(missing)
        )

    id_index = header_index(header_row, ID_HEADER)
    color_index = header_index(header_row, COLOR_HEADER)
    column_indexes = {name: header_index(header_row, name) for name in DISPLAY_HEADERS}
    visibility_index = header_index(header_row, VISIBILITY_HEADER)
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
  <title>Was trinkst du gerade? | Brausau</title>
  <link rel="icon" type="image/svg+xml" href="../assets/favicon.svg?v=2">
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
  <h1>Was trinkst du gerade?</h1>
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


def detail_value(name: str, raw: str) -> str:
    value = raw.strip()
    if not value:
        return "—"
    return f"{value}{UNIT_SUFFIXES.get(name, '')}"


def render_beer_detail(beer: dict[str, str]) -> str:
    details = "\n".join(
        f'<div class="detail detail-{escape(name.lower().replace(" ", "-"))}">'
        f'<dt>{escape(DISPLAY_LABELS.get(name, name))}</dt>'
        f'<dd>{escape(detail_value(name, beer[name]))}</dd></div>'
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
  <link rel="icon" type="image/svg+xml" href="../../assets/favicon.svg?v=2">
  <link rel="apple-touch-icon" href="../../assets/apple-touch-icon.png">
  <style>
    @font-face {{ font-family: Skranji; src: url("../../assets/fonts/Skranji-Bold.ttf") format("truetype"); font-weight: 700; font-display: swap; }}
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; color: #f5f0e5; background: #111713; font-synthesis: none; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; background: radial-gradient(ellipse at 75% 5%, color-mix(in srgb, var(--beer-color) 19%, transparent), transparent 34rem), #111713; }}
    main {{ width: min(1040px, calc(100% - 2.5rem)); margin: 0 auto; padding: 2rem 0 4rem; }}
    nav {{ margin-bottom: 1.5rem; }}
    nav a {{ display: inline-flex; align-items: center; gap: .55rem; color: #e6dfcf; text-decoration: none; font-size: .9rem; font-weight: 650; }}
    nav a::before {{ content: "←"; color: var(--beer-color); font-size: 1.2rem; transition: transform .2s ease; }}
    nav a:hover::before {{ transform: translateX(-4px); }}
    .beer-profile {{ overflow: hidden; border: 1px solid rgb(255 255 255 / 12%); border-radius: 1.5rem; background: linear-gradient(145deg, rgb(255 255 255 / 5%), rgb(255 255 255 / 1%) 50%), #171f1a; box-shadow: 0 24px 80px rgb(0 0 0 / 34%); }}
    .hero {{ position: relative; isolation: isolate; min-height: 300px; display: flex; align-items: center; padding: clamp(2rem, 7vw, 5rem); border-bottom: 1px solid rgb(255 255 255 / 10%); }}
    .hero::before {{ position: absolute; z-index: 0; right: 7%; top: 50%; width: clamp(190px, 25vw, 300px); aspect-ratio: 1; content: ""; border: 1px solid color-mix(in srgb, var(--beer-color) 40%, transparent); border-radius: 50%; background: radial-gradient(circle, color-mix(in srgb, var(--beer-color) 17%, transparent), transparent 68%); transform: translateY(-50%); }}
    .hero-copy {{ position: relative; z-index: 1; max-width: 68%; }}
    .eyebrow {{ display: flex; align-items: center; gap: .7rem; margin: 0 0 1rem; color: #c7bda9; font-size: .72rem; font-weight: 800; letter-spacing: .2em; text-transform: uppercase; }}
    .eyebrow::before {{ width: 1.6rem; height: 2px; content: ""; background: var(--beer-color); }}
    h1 {{ max-width: 720px; margin: 0; font-family: Skranji, Georgia, serif; font-size: clamp(2.8rem, 8vw, 6.5rem); font-weight: 700; letter-spacing: -.045em; line-height: .98; text-wrap: balance; }}
    .hero-mark {{ position: absolute; z-index: 1; right: clamp(2.5rem, 10vw, 7rem); top: 50%; width: clamp(88px, 15vw, 150px); height: auto; overflow: visible; transform: translateY(-50%) rotate(5deg); filter: drop-shadow(0 16px 20px rgb(0 0 0 / 35%)); }}
    .hero-mark .mug {{ fill: color-mix(in srgb, var(--beer-color) 78%, #d99432); stroke: #f3dfb7; stroke-width: 5; }}
    .hero-mark .foam {{ fill: #f3dfb7; }}
    .hero-mark .handle {{ fill: none; stroke: #f3dfb7; stroke-width: 9; }}
    .details-wrap {{ padding: clamp(.9rem, 2.5vw, 1.5rem); }}
    .section-heading {{ display: flex; align-items: center; gap: 1rem; margin: 0 0 .55rem; color: #9eaa9f; font-size: .72rem; font-weight: 800; letter-spacing: .18em; text-transform: uppercase; }}
    .section-heading::after {{ height: 1px; flex: 1; content: ""; background: rgb(255 255 255 / 12%); }}
    dl {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 1.1rem; margin: 0; }}
    .detail {{ min-width: 0; min-height: 0; display: flex; align-items: baseline; justify-content: flex-start; gap: .55rem; padding: .55rem .25rem; border: 0; border-bottom: 1px solid rgb(255 255 255 / 6%); border-radius: 0; background: transparent; }}
    .detail:hover {{ background: rgb(255 255 255 / 2%); }}
    dt {{ flex: 0 1 auto; color: #aab4aa; font-size: .72rem; font-weight: 700; letter-spacing: .035em; }}
    dd {{ min-width: 0; margin: 0; color: #f6f0e3; font-size: .95rem; font-weight: 650; line-height: 1.3; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .detail-info, .detail-zutaten {{ grid-column: span 3; display: block; padding: .65rem .25rem; }}
    .detail-info dd, .detail-zutaten dd {{ margin-top: .3rem; font-size: .92rem; font-weight: 450; text-align: left; }}
    @media (max-width: 720px) {{ dl {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .detail-info, .detail-zutaten {{ grid-column: span 2; }} }}
    @media (max-width: 600px) {{ main {{ width: calc(100% - 1rem); max-width: 1040px; padding: .75rem 0 2rem; }} nav {{ margin: 0 0 1rem .25rem; }} .beer-profile {{ border-radius: 1rem; }} .hero {{ min-height: 245px; padding: 2rem 1.35rem; }} .hero::before {{ right: -2.5rem; width: 210px; }} .hero-copy {{ max-width: 100%; }} .hero-mark {{ right: 1.3rem; top: auto; bottom: 1rem; width: 66px; transform: rotate(5deg); }} h1 {{ max-width: calc(100% - 1rem); font-size: clamp(2.6rem, 13vw, 4.2rem); }} .details-wrap {{ padding: .8rem .75rem .7rem; }} .section-heading {{ margin-bottom: .35rem; }} dl {{ grid-template-columns: minmax(0, 1fr); column-gap: 0; }} .detail {{ padding: .48rem .15rem; }} dd {{ font-size: .92rem; }} .detail-info, .detail-zutaten {{ grid-column: auto; padding: .55rem .15rem; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
<main style="--beer-color: {beer[COLOR_HEADER]}">
  <nav><a href="../">andere Farbe</a></nav>
  <article class="beer-profile">
    <header class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Brausau · Bierportrait</p>
        <h1>{escape(beer['Biername'])}</h1>
      </div>
      <svg class="hero-mark" viewBox="0 0 120 140" aria-hidden="true" focusable="false">
        <path class="handle" d="M91 48h10a14 14 0 0 1 0 42H91" />
        <path class="mug" d="M25 38h66v76a8 8 0 0 1-8 8H33a8 8 0 0 1-8-8z" />
        <path class="foam" d="M24 43c-9-7-6-20 5-23 2-12 17-17 26-9 9-10 26-5 28 7 13-4 23 9 16 20-4 6-11 8-20 8H34c-4 0-7-1-10-3z" />
        <path d="M39 62v43M53 62v43M67 62v43" fill="none" stroke="#f3dfb7" stroke-linecap="round" stroke-width="4" opacity=".55" />
      </svg>
    </header>
    <section class="details-wrap" aria-label="Bierdetails">
      <h2 class="section-heading">Auf einen Blick</h2>
      <dl>
{details}
      </dl>
    </section>
  </article>
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
