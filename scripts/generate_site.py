"""Generate a public static page from a private Google Sheet."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account


SHEET_ID = os.environ.get(
    "GOOGLE_SHEET_ID", "1Vid837zHDbtGlQy9gt4F6RzCQuFDb4-6EhgV3y-iazQ"
)
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "biere")
COLOR_TAB = "Kronkorken"
COLOR_NAME_HEADER = "Farbe"
COLOR_HEX_HEADER = "Hex"
STYLE_TAB = "bierstile"
STYLE_NAME_HEADER = "Name"
STYLE_REFERENCE_HEADER = "style name"
STYLE_GLASS_HEADER = "Bierglas"
GLASS_TAB = "glaeser"
GLASS_NAME_HEADER = "Name"
GLASS_FILE_HEADER = "File"
GLASS_GENUS_HEADER = "Genus"
GLASS_NAME_KEY = "_glass_name"
GLASS_ARTICLE_KEY = "_glass_article"
GLASS_ASSET_DIR = Path(__file__).resolve().parent.parent / "site" / "assets" / "glasses"
GLASS_FILE_ALIASES = {
    "flut.svg": "flute.svg",
    "tumblr.svg": "tumbler.svg",
}
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
GLASS_HEADER = "Glas"
REQUIRED_HEADERS = (
    ID_HEADER,
    *DISPLAY_HEADERS,
    COLOR_HEADER,
    VISIBILITY_HEADER,
)
UNIT_SUFFIXES = {
    "Alc": " Vol. % Alc",
    "Stammwürze": "°P",
    "Trinktemperatur": "°C",
}
HEADER_ALIASES = {
    "Trinktemperatur": ("Optimale Trinktemperatur",),
}
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "public"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
GLASS_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.svg$")


def sheet_values(tab_name: str = SHEET_TAB) -> list[list[str]]:
    """Read the sheet using a service-account key supplied through the environment."""
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON to the service-account JSON.")

    credentials_info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=SCOPES
    )
    session = AuthorizedSession(credentials)
    escaped_tab = tab_name.replace("'", "''")
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


def color_lookup(rows: list[list[str]]) -> dict[str, str]:
    if not rows:
        raise ValueError(
            f"The {COLOR_TAB!r} tab is empty; it must contain Farbe and Hex headers."
        )

    header_row = rows[0]
    missing = [name for name in (COLOR_NAME_HEADER, COLOR_HEX_HEADER) if name not in header_row]
    if missing:
        raise ValueError(
            f"Could not find expected header(s) in the {COLOR_TAB!r} tab: "
            + ", ".join(missing)
        )

    name_index = header_row.index(COLOR_NAME_HEADER)
    hex_index = header_row.index(COLOR_HEX_HEADER)
    colors: dict[str, str] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        name = row[name_index].strip() if name_index < len(row) else ""
        hex_color = row[hex_index].strip() if hex_index < len(row) else ""
        if not name and not hex_color:
            continue
        if not name:
            raise ValueError(f"Missing color name in {COLOR_TAB!r} tab row {row_number}.")
        if not COLOR_PATTERN.fullmatch(hex_color):
            raise ValueError(
                f"Invalid or missing Hex value in {COLOR_TAB!r} tab row {row_number}: "
                f"{hex_color!r}. Use a six-digit hex color such as #D64B32."
            )
        key = name.casefold()
        if key in colors:
            raise ValueError(f"Duplicate color Name {name!r} in {COLOR_TAB!r} tab.")
        colors[key] = hex_color
    return colors


def glass_lookup(rows: list[list[str]]) -> dict[str, str]:
    if not rows:
        raise ValueError(f"The {GLASS_TAB!r} tab is empty; it must contain Name and File headers.")

    header_row = rows[0]
    missing = [name for name in (GLASS_NAME_HEADER, GLASS_FILE_HEADER) if name not in header_row]
    if missing:
        raise ValueError(
            f"Could not find expected header(s) in the {GLASS_TAB!r} tab: "
            + ", ".join(missing)
        )

    name_index = header_row.index(GLASS_NAME_HEADER)
    file_index = header_row.index(GLASS_FILE_HEADER)
    glasses: dict[str, str] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        name = row[name_index].strip() if name_index < len(row) else ""
        filename = row[file_index].strip() if file_index < len(row) else ""
        if not name and not filename:
            continue
        if not name:
            raise ValueError(f"Missing glass Name in {GLASS_TAB!r} tab row {row_number}.")
        if not GLASS_FILE_PATTERN.fullmatch(filename):
            raise ValueError(
                f"Invalid or missing SVG File in {GLASS_TAB!r} tab row {row_number}: "
                f"{filename!r}. Use a local .svg filename such as weizen.svg."
            )
        local_filename = GLASS_FILE_ALIASES.get(filename.casefold(), filename)
        if not (GLASS_ASSET_DIR / local_filename).is_file():
            raise ValueError(
                f"Glass file {filename!r} from {GLASS_TAB!r} tab row {row_number} "
                f"does not exist in site/assets/glasses/."
            )
        key = name.casefold()
        if key in glasses:
            raise ValueError(f"Duplicate glass Name {name!r} in {GLASS_TAB!r} tab.")
        glasses[key] = local_filename
    return glasses


def glass_article_lookup(rows: list[list[str]]) -> dict[str, str]:
    if not rows:
        raise ValueError(
            f"The {GLASS_TAB!r} tab is empty; it must contain "
            f"{GLASS_NAME_HEADER} and {GLASS_GENUS_HEADER} headers."
        )

    header_row = rows[0]
    missing = [
        name
        for name in (GLASS_NAME_HEADER, GLASS_GENUS_HEADER)
        if name not in header_row
    ]
    if missing:
        raise ValueError(
            f"Could not find expected header(s) in the {GLASS_TAB!r} tab: "
            + ", ".join(missing)
        )

    name_index = header_row.index(GLASS_NAME_HEADER)
    genus_index = header_row.index(GLASS_GENUS_HEADER)
    articles: dict[str, str] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        name = row[name_index].strip() if name_index < len(row) else ""
        genus = row[genus_index].strip().casefold() if genus_index < len(row) else ""
        if not name and not genus:
            continue
        if not name:
            raise ValueError(f"Missing glass Name in {GLASS_TAB!r} tab row {row_number}.")
        if genus not in {"m", "f", "n"}:
            raise ValueError(
                f"Invalid or missing Genus for glass {name!r} in {GLASS_TAB!r} "
                f"tab row {row_number}: {genus!r}. Use m, f, or n."
            )
        key = name.casefold()
        article = "einer" if genus == "f" else "einem"
        if key in articles and articles[key] != article:
            raise ValueError(f"Conflicting Genus values for glass {name!r} in {GLASS_TAB!r} tab.")
        articles[key] = article
    return articles


def style_glass_lookup(
    rows: list[list[str]],
    glasses: dict[str, str],
    articles: dict[str, str],
) -> dict[str, tuple[str, str, str, str]]:
    """Resolve English beer-style references to display names and glass data."""
    if not rows:
        raise ValueError(
            f"The {STYLE_TAB!r} tab is empty; it must contain "
            f"{STYLE_NAME_HEADER}, {STYLE_REFERENCE_HEADER}, and "
            f"{STYLE_GLASS_HEADER} headers."
        )

    header_row = rows[0]
    missing = [
        name
        for name in (
            STYLE_NAME_HEADER,
            STYLE_REFERENCE_HEADER,
            STYLE_GLASS_HEADER,
        )
        if name not in header_row
    ]
    if missing:
        raise ValueError(
            f"Could not find expected header(s) in the {STYLE_TAB!r} tab: "
            + ", ".join(missing)
        )

    name_index = header_row.index(STYLE_NAME_HEADER)
    reference_index = header_row.index(STYLE_REFERENCE_HEADER)
    glass_index = header_row.index(STYLE_GLASS_HEADER)
    styles: dict[str, tuple[str, str, str, str]] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        display_name = row[name_index].strip() if name_index < len(row) else ""
        style_reference = (
            row[reference_index].strip() if reference_index < len(row) else ""
        )
        glass_name = row[glass_index].strip() if glass_index < len(row) else ""
        if not display_name and not style_reference and not glass_name:
            continue
        if not display_name:
            raise ValueError(
                f"Missing Name in {STYLE_TAB!r} tab row {row_number}."
            )
        if not style_reference:
            continue
        if not glass_name:
            # An unused style can lack a glass. If a beer selects it, the
            # missing relationship is reported at the beer row below.
            continue

        key = style_reference.casefold()
        glass_key = glass_name.casefold()
        if glass_key not in glasses:
            raise ValueError(
                f"Unknown Bierglas {glass_name!r} in {STYLE_TAB!r} tab row "
                f"{row_number}; add a matching Name to the {GLASS_TAB!r} tab."
            )
        article = articles.get(glass_key)
        if article is None:
            raise ValueError(
                f"Missing Genus for glass {glass_name!r} referenced in "
                f"{STYLE_TAB!r} tab row {row_number}."
            )
        resolved = (display_name, glass_name, glasses[glass_key], article)
        if key in styles and styles[key] != resolved:
            raise ValueError(
                f"Conflicting mappings for style name {style_reference!r} "
                f"in the {STYLE_TAB!r} tab."
            )
        styles[key] = resolved
    return styles


def visible_beers(
    rows: list[list[str]],
    colors: dict[str, str] | None = None,
    glasses: dict[str, str] | None = None,
    styles: dict[str, tuple[str, str, str, str]] | None = None,
) -> list[dict[str, str]]:
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
    glass_index = header_index(header_row, GLASS_HEADER)
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
        color_choice = row[color_index].strip() if color_index < len(row) else ""
        if COLOR_PATTERN.fullmatch(color_choice):
            color = color_choice
        elif colors and color_choice.casefold() in colors:
            color = colors[color_choice.casefold()]
        else:
            raise ValueError(
                f"Unknown or invalid Farbe value on sheet row {row_number}: "
                f"{color_choice!r}. Select a color Name defined in the {COLOR_TAB!r} tab "
                f"or use a six-digit hex color such as #D64B32."
            )
        glass_choice = ""
        glass_file = ""
        glass_article = "einem"
        display_style_name = ""
        style_index = column_indexes["Bierstil"]
        style_name = row[style_index].strip() if style_index < len(row) else ""
        if styles is not None and style_name:
            style_match = styles.get(style_name.casefold())
            if style_match is None:
                raise ValueError(
                    f"Could not resolve Bierstil {style_name!r} on sheet row "
                    f"{row_number} through the {STYLE_TAB!r} and {GLASS_TAB!r} tabs."
                )
            display_style_name, glass_choice, glass_file, glass_article = style_match
        elif glass_index is not None:
            legacy_glass = row[glass_index].strip() if glass_index < len(row) else ""
            if legacy_glass and glasses and legacy_glass.casefold() in glasses:
                glass_choice = legacy_glass
                glass_file = glasses[legacy_glass.casefold()]
            elif legacy_glass:
                raise ValueError(
                    f"Unknown Glas value on sheet row {row_number}: {legacy_glass!r}. "
                    f"Select a glass Name defined in the {GLASS_TAB!r} tab."
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
        if display_style_name:
            values["Bierstil"] = display_style_name
        if any(value.strip() for value in values.values()):
            beers.append(
                {
                    ID_HEADER: beer_id,
                    COLOR_HEADER: color,
                    GLASS_HEADER: glass_file,
                    GLASS_NAME_KEY: glass_choice,
                    GLASS_ARTICLE_KEY: glass_article,
                    **values,
                }
            )
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


def render_glass(beer: dict[str, str]) -> str:
    filename = beer.get(GLASS_HEADER, "")
    if not filename:
        return '''<svg class="hero-mark" viewBox="0 0 120 140" aria-hidden="true" focusable="false">
        <path class="handle" d="M91 48h10a14 14 0 0 1 0 42H91" />
        <path class="mug" d="M25 38h66v76a8 8 0 0 1-8 8H33a8 8 0 0 1-8-8z" />
        <path class="foam" d="M24 43c-9-7-6-20 5-23 2-12 17-17 26-9 9-10 26-5 28 7 13-4 23 9 16 20-4 6-11 8-20 8H34c-4 0-7-1-10-3z" />
        <path d="M39 62v43M53 62v43M67 62v43" fill="none" stroke="#f3dfb7" stroke-linecap="round" stroke-width="4" opacity=".55" />
      </svg>'''

    svg_path = GLASS_ASSET_DIR / filename
    root = ET.parse(svg_path).getroot()
    root.set("class", "hero-mark")
    root.set("color", beer[COLOR_HEADER])
    root.set("aria-hidden", "true")
    root.set("focusable", "false")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")


def render_beer_detail(beer: dict[str, str]) -> str:
    glass_artwork = render_glass(beer)
    beer_style = escape(beer["Bierstil"].strip())
    beer_style_markup = (
        f'<p class="beer-style">{beer_style}</p>' if beer_style else ""
    )
    recommendation = (
        '<p class="recommendation">Am besten auf '
        f"<strong>{escape(detail_value('Trinktemperatur', beer['Trinktemperatur']))}</strong> "
        f"gekühlt aus {escape(beer.get(GLASS_ARTICLE_KEY) or 'einem')} "
        f"<strong>{escape(beer.get(GLASS_NAME_KEY) or 'Bierglas')}</strong> geniessen.</p>"
    )
    details = "\n".join(
        f'<div class="detail detail-{escape(name.lower().replace(" ", "-"))}">'
        f'<dt>{escape(name)}</dt>'
        f'<dd>{escape(detail_value(name, beer[name]))}</dd></div>'
        for name in DISPLAY_HEADERS
        if name not in ("Biername", "Bierstil", "Trinktemperatur")
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
    :root {{ --beer-title-color: #f5efdf; --paper: #f4efdf; --punk-pink: #ff2f92; --acid: #d5ff00; color-scheme: light; font-family: system-ui, sans-serif; color: #171717; background: #d5ff00; font-synthesis: none; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; background-color: var(--acid); background-image: radial-gradient(rgb(23 23 23 / 17%) .8px, transparent .8px); background-size: 8px 8px; }}
    main {{ width: min(1040px, calc(100% - 2.5rem)); margin: 0 auto; padding: 2rem 0 4rem; }}
    nav {{ margin-bottom: 1.5rem; }}
    nav a {{ display: inline-flex; align-items: center; gap: .55rem; padding: .35rem .65rem; border: 2px solid #171717; background: var(--paper); color: #171717; text-decoration: none; font-size: .9rem; font-weight: 800; box-shadow: 3px 3px 0 var(--punk-pink); transform: rotate(-1deg); }}
    nav a::before {{ content: "←"; color: #d60065; font-size: 1.2rem; transition: transform .2s ease; }}
    nav a:hover::before {{ transform: translateX(-4px); }}
    .beer-profile {{ overflow: hidden; border: 3px solid #171717; border-radius: 0; background: var(--paper); box-shadow: 12px 12px 0 #171717; }}
    .hero {{ position: relative; isolation: isolate; min-height: 390px; display: flex; align-items: center; justify-content: space-between; gap: clamp(1.5rem, 4vw, 3.5rem); padding: clamp(2rem, 7vw, 5rem); border-bottom: 4px solid #171717; background: #171717; }}
    .hero-copy {{ position: relative; z-index: 1; flex: 1 1 auto; max-width: 62%; }}
    .glass-feature {{ position: relative; z-index: 1; flex: 0 0 min(38%, 360px); display: flex; flex-direction: column; align-items: center; gap: .85rem; }}
    .glass-visual {{ position: relative; width: clamp(210px, 25vw, 300px); aspect-ratio: 1; display: grid; place-items: center; }}
    .glass-visual::before {{ position: absolute; inset: 3%; content: ""; border: 4px solid #171717; border-radius: 50%; background: var(--beer-color); box-shadow: 9px 9px 0 var(--punk-pink); transform: rotate(5deg); }}
    .glass-caption {{ width: 100%; padding: .85rem 1rem; border: 3px solid #171717; border-radius: 0; background: var(--acid); box-shadow: 7px 7px 0 var(--punk-pink); text-align: center; transform: rotate(-1deg); }}
    .recommendation-label {{ margin: 0 0 .35rem; color: #171717; font-size: .64rem; font-weight: 900; letter-spacing: .17em; text-transform: uppercase; }}
    .recommendation {{ margin: 0; color: #171717; font-size: clamp(.82rem, 1vw, .95rem); font-weight: 550; line-height: 1.5; text-align: center; }}
    .recommendation strong {{ padding: 0 .12em; background: var(--punk-pink); color: #171717; font-weight: 850; }}
    .beer-style {{ display: flex; align-items: center; gap: .7rem; margin: .75rem 0 0; color: var(--acid); font-size: clamp(1rem, 1.6vw, 1.2rem); font-weight: 700; line-height: 1.35; }}
    .beer-style::before {{ width: 1.5rem; height: 2px; flex: 0 0 auto; content: ""; background: var(--beer-color); }}
    .eyebrow {{ display: inline-flex; align-items: center; gap: .7rem; margin: 0 0 1rem; padding: .35rem .6rem; background: var(--punk-pink); color: #171717; font-size: .72rem; font-weight: 900; letter-spacing: .2em; text-transform: uppercase; transform: rotate(-2deg); }}
    .eyebrow::before {{ width: 1.6rem; height: 3px; flex: 0 0 auto; content: ""; background: #171717; }}
    h1 {{ max-width: 720px; margin: 0; color: var(--beer-title-color); font-family: Skranji, Georgia, serif; font-size: clamp(2.8rem, 8vw, 6.5rem); font-weight: 700; letter-spacing: -.045em; line-height: .98; text-shadow: 4px 4px 0 var(--punk-pink); text-wrap: balance; transform: rotate(-1deg); }}
    .hero-mark {{ position: relative; z-index: 1; width: 42%; max-height: 88%; height: auto; overflow: visible; transform: rotate(5deg); filter: drop-shadow(0 16px 20px rgb(0 0 0 / 35%)); }}
    .hero-mark .mug {{ fill: color-mix(in srgb, var(--beer-color) 78%, #d99432); stroke: var(--beer-title-color); stroke-width: 5; }}
    .hero-mark .foam {{ fill: #fff6df; }}
    .hero-mark .handle {{ fill: none; stroke: var(--beer-title-color); stroke-width: 9; }}
    .details-wrap {{ padding: clamp(.9rem, 2.5vw, 1.5rem); }}
    .section-heading {{ display: inline-flex; align-items: center; gap: .6rem; margin: 0 0 .8rem; padding: .35rem .6rem; background: #171717; color: var(--acid); font-size: .72rem; font-weight: 900; letter-spacing: .18em; text-transform: uppercase; transform: rotate(-1deg); }}
    .section-heading::after {{ width: 1.1rem; height: 3px; content: ""; background: var(--punk-pink); }}
    dl {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 1.1rem; margin: 0; }}
    .detail {{ min-width: 0; min-height: 0; display: flex; align-items: baseline; justify-content: flex-start; gap: .55rem; padding: .65rem .7rem; border: 2px solid #171717; border-radius: 0; background: #fff9e8; box-shadow: 4px 4px 0 var(--punk-pink); }}
    .detail:nth-child(3n + 1) {{ transform: rotate(-.65deg); }}
    .detail:nth-child(3n + 2) {{ transform: rotate(.55deg); }}
    .detail:hover {{ background: #fff; }}
    dt {{ flex: 0 1 auto; padding: .12rem .25rem; background: var(--acid); color: #171717; font-size: .67rem; font-weight: 900; letter-spacing: .045em; text-transform: uppercase; }}
    dd {{ min-width: 0; margin: 0; color: #171717; font-size: .95rem; font-weight: 750; line-height: 1.3; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .detail-info, .detail-zutaten {{ grid-column: span 3; display: block; padding: .8rem .9rem; border: 3px solid #171717; border-left: 8px solid var(--beer-color); border-radius: 0; background: #fff9e8; box-shadow: 6px 6px 0 #171717; }}
    .detail-info dt, .detail-zutaten dt {{ display: inline-block; transform: rotate(-1deg); }}
    .detail-info dd, .detail-zutaten dd {{ margin-top: .4rem; font-size: .95rem; font-weight: 500; text-align: left; }}
    @media (max-width: 720px) {{ dl {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .detail-info, .detail-zutaten {{ grid-column: span 2; }} }}
    @media (max-width: 600px) {{ main {{ width: calc(100% - 1rem); max-width: 1040px; padding: .75rem 0 2rem; }} nav {{ margin: 0 0 1rem .25rem; }} .beer-profile {{ width: 100%; min-width: 0; border-radius: 0; box-shadow: 6px 6px 0 #171717; }} .hero {{ min-width: 0; min-height: 0; flex-direction: column; align-items: stretch; gap: 1.25rem; padding: 2rem 1.1rem 1.25rem; }} .hero-copy {{ max-width: 100%; min-width: 0; }} .glass-feature {{ flex: 0 1 auto; width: min(100%, 300px); max-width: 100%; min-width: 0; margin: 0 auto; gap: .65rem; }} .glass-visual {{ width: clamp(180px, 52vw, 220px); max-width: 100%; }} .hero-mark {{ width: 40%; }} .glass-caption {{ min-width: 0; padding: .7rem .8rem; }} .recommendation {{ font-size: .88rem; overflow-wrap: anywhere; }} h1 {{ max-width: calc(100% - 1rem); font-size: clamp(2.6rem, 13vw, 4.2rem); }} .details-wrap {{ width: 100%; min-width: 0; padding: .8rem .75rem .7rem; }} .section-heading {{ margin-bottom: .35rem; }} dl {{ width: 100%; min-width: 0; grid-template-columns: minmax(0, 1fr); column-gap: 0; }} .detail, .detail:nth-child(3n + 1), .detail:nth-child(3n + 2) {{ min-width: 0; padding: .55rem .5rem; transform: none; }} dd {{ min-width: 0; font-size: .92rem; overflow-wrap: anywhere; }} .detail-info, .detail-zutaten {{ grid-column: auto; padding: .65rem .7rem; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
<main style="--beer-color: {beer[COLOR_HEADER]}; --punk-pink: color-mix(in srgb, var(--beer-color) 12%, #ff2f92); --acid: color-mix(in srgb, var(--beer-color) 12%, #d5ff00)">
  <nav><a href="../">andere Farbe</a></nav>
  <article class="beer-profile">
    <header class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Brausau · Bierportrait</p>
        <h1>{escape(beer['Biername'])}</h1>
        {beer_style_markup}
      </div>
      <div class="glass-feature">
        <div class="glass-visual">{glass_artwork}</div>
        <div class="glass-caption">
          <p class="recommendation-label">Die Sau empfiehlt</p>
          {recommendation}
        </div>
      </div>
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

    glass_rows = sheet_values(GLASS_TAB)
    glass_files = glass_lookup(glass_rows)
    glass_articles = glass_article_lookup(glass_rows)
    style_glasses = style_glass_lookup(
        sheet_values(STYLE_TAB), glass_files, glass_articles
    )
    beers = visible_beers(
        sheet_values(),
        color_lookup(sheet_values(COLOR_TAB)),
        glass_files,
        style_glasses,
    )
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
