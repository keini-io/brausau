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
            label = escape(beer["Biername"] or beer[ID_HEADER])
            style = escape(beer["Bierstil"].strip())
            style_markup = (
                f'<span class="beer-style">{style}</span>' if style else ""
            )
            cards.append(
                f'<a class="beer-card" href="{escape(beer[ID_HEADER])}/" '
                f'aria-label="{escape(beer["Biername"] or beer[ID_HEADER])}" '
                f'style="--beer-color: {beer[COLOR_HEADER]}">'
                '<span class="cap-frame" aria-hidden="true"><span class="cap"></span></span>'
                f'<span class="beer-text"><span class="beer-name">{label}</span>'
                f"{style_markup}</span>"
                '<span class="beer-leader" aria-hidden="true"></span>'
                '<span class="beer-mark" aria-hidden="true">✦</span>'
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
    @font-face {{ font-family: Skranji; src: url("../assets/fonts/Skranji-Bold.ttf") format("truetype"); font-weight: 700; font-display: swap; }}
    @font-face {{ font-family: Skranji; src: url("../assets/fonts/Skranji-Regular.ttf") format("truetype"); font-weight: 400; font-display: swap; }}
    :root {{ color-scheme: light; --paper: #f2e7cd; --paper-hi: #fbf4e3; --green: #223a2e; --green-deep: #16261e; --brass: #a87c3e; --brass-light: #d9c08a; --ink: #332a1d; font-family: Georgia, "Palatino Linotype", "Book Antiqua", serif; color: var(--ink); background-color: #efe3c7; }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; }}
    body {{ min-height: 100vh; margin: 0; background-color: #efe3c7; background-image: radial-gradient(ellipse at 50% -10%, #fcf6e7, transparent 58%), radial-gradient(ellipse at 50% 112%, rgb(120 90 45 / 26%), transparent 62%), repeating-linear-gradient(0deg, rgb(120 90 45 / 4%) 0 1px, transparent 1px 4px), repeating-linear-gradient(90deg, rgb(120 90 45 / 3%) 0 1px, transparent 1px 5px); }}
    body::before {{ position: fixed; inset: 0; z-index: 0; content: ""; box-shadow: inset 0 0 190px rgb(80 55 20 / 30%); pointer-events: none; }}
    main {{ position: relative; z-index: 1; width: min(1040px, calc(100% - clamp(1.6rem, 5vw, 4rem))); margin: 0 auto; padding: clamp(1.5rem, 4vw, 2.8rem) 0 clamp(3rem, 7vw, 5rem); }}
    .top-nav {{ margin: 0 0 clamp(1rem, 2.5vw, 1.6rem) .2rem; }}
    .top-nav a {{ display: inline-flex; align-items: center; gap: .5rem; color: var(--green); font-size: .88rem; font-weight: 700; letter-spacing: .02em; text-decoration: none; }}
    .top-nav a::before {{ color: var(--brass); content: "❧"; font-size: 1.35rem; line-height: 0; transition: transform .2s ease; }}
    .top-nav a:hover::before {{ transform: translateX(-4px); }}
    .top-nav a:focus-visible {{ outline: 2px dashed var(--brass); outline-offset: 4px; }}
    .masthead {{ padding: clamp(.4rem, 1.5vw, .9rem) 0 clamp(1.1rem, 3vw, 1.8rem); text-align: center; }}
    .kicker {{ display: flex; align-items: center; justify-content: center; gap: clamp(.6rem, 2vw, 1rem); margin: 0 0 .55rem; color: #77613a; font-size: clamp(.62rem, 1.4vw, .72rem); font-weight: 700; letter-spacing: .3em; line-height: 1.7; text-transform: uppercase; }}
    .kicker::before, .kicker::after {{ flex: 0 1 clamp(26px, 8vw, 80px); height: 1px; content: ""; background: linear-gradient(90deg, transparent, var(--brass)); }}
    .kicker::after {{ transform: scaleX(-1); }}
    h1 {{ margin: 0; color: var(--green); font-family: Skranji, Georgia, serif; font-size: clamp(2.8rem, 9vw, 5.2rem); font-weight: 700; letter-spacing: .015em; line-height: .95; text-shadow: 0 1px 0 #fff9ea, 0 3px 0 rgb(120 90 45 / 20%), 0 12px 22px rgb(90 65 25 / 18%); }}
    .sub {{ margin: .7rem 0 0; color: #6b5838; font-size: clamp(.95rem, 2.2vw, 1.15rem); font-style: italic; letter-spacing: .03em; }}
    .sub::before, .sub::after {{ padding: 0 .75em; color: var(--brass); content: "✦"; font-size: .68em; font-style: normal; vertical-align: .2em; }}
    .masthead-rule {{ display: flex; align-items: center; justify-content: center; gap: .8rem; margin: clamp(.9rem, 2.4vw, 1.4rem) 0 0; color: var(--brass); }}
    .masthead-rule::before, .masthead-rule::after {{ flex: 0 1 clamp(50px, 18vw, 190px); height: 1px; content: ""; background: linear-gradient(90deg, transparent, var(--brass)); }}
    .masthead-rule::after {{ transform: scaleX(-1); }}
    .masthead-rule span {{ padding-left: .4em; font-size: 1.1rem; letter-spacing: .4em; }}
    .board-mat {{ position: relative; padding: clamp(7px, 1.2vw, 11px); border: 2px solid #6b5129; border-radius: 5px; background: linear-gradient(180deg, #2c4739 0%, #1d3227 55%, #14241b 100%); box-shadow: 0 24px 48px rgb(60 40 15 / 32%), inset 0 1px 0 rgb(255 240 204 / 18%); }}
    .menu-board {{ position: relative; padding: clamp(1.1rem, 3vw, 2rem) clamp(1.1rem, 3.4vw, 2.4rem) clamp(1rem, 2.6vw, 1.6rem); border: 3px double var(--brass); border-radius: 3px; background-color: var(--paper); background-image: radial-gradient(ellipse at 24% 0%, rgb(255 255 255 / 50%), transparent 55%), radial-gradient(ellipse at 80% 100%, rgb(150 112 55 / 12%), transparent 60%), repeating-linear-gradient(0deg, rgb(120 90 45 / 3.5%) 0 1px, transparent 1px 4px), repeating-linear-gradient(90deg, rgb(120 90 45 / 2.8%) 0 1px, transparent 1px 5px); box-shadow: inset 0 0 0 3px rgb(255 252 240 / 45%), inset 0 0 50px rgb(140 104 52 / 12%); }}
    .menu-corner {{ position: absolute; z-index: 2; color: var(--brass); font-size: clamp(1rem, 2vw, 1.35rem); line-height: 1; opacity: .85; pointer-events: none; }}
    .menu-corner.tl {{ top: 9px; left: 11px; }}
    .menu-corner.tr {{ top: 9px; right: 11px; transform: scaleX(-1); }}
    .menu-corner.bl {{ bottom: 9px; left: 11px; transform: rotate(180deg); }}
    .menu-corner.br {{ bottom: 9px; right: 11px; transform: rotate(180deg) scaleX(-1); }}
    .board-head {{ display: flex; align-items: center; gap: .9rem; margin: 0 0 clamp(.7rem, 2vw, 1.1rem); color: #77613a; font-size: clamp(.6rem, 1.4vw, .7rem); font-weight: 700; letter-spacing: .26em; text-transform: uppercase; }}
    .board-head::before, .board-head::after {{ flex: 1; height: 1px; content: ""; background: linear-gradient(90deg, transparent, rgb(168 124 62 / 60%)); }}
    .board-head::after {{ transform: scaleX(-1); }}
    .board-head span {{ flex: 0 0 auto; }}
    .beer-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 330px), 1fr)); column-gap: clamp(1.2rem, 4vw, 3rem); margin: 0; }}
    .beer-card {{ position: relative; isolation: isolate; min-width: 0; display: grid; grid-template-columns: auto auto minmax(1.4rem, 1fr) auto; align-items: center; gap: clamp(.7rem, 1.8vw, 1rem); padding: clamp(.62rem, 1.6vw, .85rem) .25rem; border-bottom: 1px solid rgb(168 124 62 / 36%); color: inherit; text-decoration: none; transform: translate(var(--shift-x, 0), var(--shift-y, 0)) rotate(var(--tilt, 0deg)); transition: transform .22s ease; }}
    .beer-card::before {{ position: absolute; inset: 3px -.35rem; z-index: -1; content: ""; border-radius: 3px; background: linear-gradient(90deg, rgb(168 124 62 / 16%), rgb(168 124 62 / 6%) 70%, transparent); opacity: 0; transition: opacity .22s ease; }}
    .beer-card:hover, .beer-card:focus-visible {{ transform: translate(var(--shift-x, 0), var(--shift-y, 0)) rotate(0deg) translateX(4px); }}
    .beer-card:hover::before, .beer-card:focus-visible::before {{ opacity: 1; }}
    .beer-card:focus-visible {{ outline: 2px dashed var(--brass); outline-offset: 4px; }}
    .cap-frame {{ width: clamp(50px, 8vw, 60px); aspect-ratio: 1; display: grid; place-items: center; border-radius: 50%; background: radial-gradient(circle at 34% 28%, #3d5b49, #1a2e24 72%); box-shadow: inset 0 0 0 2px rgb(217 192 138 / 55%), inset 0 0 12px rgb(0 0 0 / 35%), 0 3px 7px rgb(60 40 15 / 25%); }}
    .cap {{ width: 74%; aspect-ratio: 142.08 / 130.56; display: block; background-color: var(--beer-color); -webkit-mask: url("../assets/kronkorken.svg") center / contain no-repeat; mask: url("../assets/kronkorken.svg") center / contain no-repeat; filter: drop-shadow(0 2px 1.5px rgb(0 0 0 / 40%)); transform: rotate(var(--cap-rotate, 0deg)); transition: transform .25s ease; }}
    .beer-text {{ min-width: 0; display: flex; flex-direction: column; gap: .18rem; }}
    .beer-name {{ color: var(--green); font-family: Skranji, Georgia, serif; font-size: clamp(1rem, 2vw, 1.22rem); letter-spacing: .01em; line-height: 1.15; overflow-wrap: anywhere; }}
    .beer-style {{ color: #7d6745; font-size: clamp(.72rem, 1.5vw, .8rem); font-style: italic; letter-spacing: .035em; line-height: 1.3; overflow-wrap: anywhere; }}
    .beer-leader {{ align-self: center; height: 0; border-top: 1px dotted rgb(168 124 62 / 55%); margin-top: .15rem; transition: border-color .2s ease; }}
    .beer-mark {{ color: var(--brass); font-size: .82rem; line-height: 1; opacity: .65; transition: transform .3s ease, opacity .2s ease; }}
    .beer-card:hover .beer-name, .beer-card:focus-visible .beer-name {{ color: #172a20; }}
    .beer-card:hover .beer-leader, .beer-card:focus-visible .beer-leader {{ border-top-color: rgb(168 124 62 / 85%); }}
    .beer-card:hover .cap, .beer-card:focus-visible .cap {{ transform: rotate(0deg) scale(1.08); }}
    .beer-card:hover .beer-mark, .beer-card:focus-visible .beer-mark {{ opacity: 1; transform: rotate(90deg); }}
    .menu-foot {{ margin: clamp(1.1rem, 3vw, 1.7rem) 0 0; padding-top: clamp(.8rem, 2vw, 1rem); border-top: 3px double rgb(168 124 62 / 50%); color: #77613a; font-size: clamp(.6rem, 1.4vw, .7rem); font-weight: 700; letter-spacing: .2em; line-height: 1.8; text-align: center; text-transform: uppercase; }}
    .menu-foot::before, .menu-foot::after {{ padding: 0 .6em; color: var(--brass); content: "✦"; }}
    .empty {{ max-width: 460px; margin: clamp(1.4rem, 4vw, 2.2rem) auto; padding: clamp(1.5rem, 4vw, 2.2rem) 1.2rem; border: 3px double var(--brass); border-radius: 2px; background: linear-gradient(#fbf5e7, #f0e5cc); box-shadow: inset 0 0 0 3px #fffaf0; color: #5b4a2f; font-family: Skranji, Georgia, serif; text-align: center; }}
    .empty::before {{ display: block; margin-bottom: .6rem; color: var(--brass); content: "❧  ✦  ❧"; font-size: .85rem; letter-spacing: .3em; }}
    @media (max-width: 620px) {{ main {{ width: calc(100% - 1.2rem); padding-top: 1rem; }} .top-nav {{ margin-left: .15rem; }} .menu-board {{ padding: 1rem .8rem .9rem; }} .cap-frame {{ width: 46px; }} h1 {{ font-size: clamp(2.5rem, 13vw, 3.4rem); }} .kicker {{ letter-spacing: .22em; }} .menu-foot {{ letter-spacing: .14em; }} .menu-corner {{ display: none; }} .beer-grid {{ column-gap: 0; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation: none !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
<main>
  <nav class="top-nav" aria-label="Seitennavigation"><a href="../">Zur Schenke</a></nav>
  <header class="masthead">
    <p class="kicker">Brausau · Aus dem Fass</p>
    <h1>Bierkarte</h1>
    <p class="sub">Was trinkst du gerade?</p>
    <div class="masthead-rule" aria-hidden="true"><span>❦</span></div>
  </header>
  <section class="board-mat" aria-label="Bierliste">
    <div class="menu-board">
      <span class="menu-corner tl" aria-hidden="true">❧</span>
      <span class="menu-corner tr" aria-hidden="true">❧</span>
      <span class="menu-corner bl" aria-hidden="true">❧</span>
      <span class="menu-corner br" aria-hidden="true">❧</span>
      <p class="board-head"><span>Nach alter Brauart · Zürich</span></p>
      {content}
      <p class="menu-foot">Wohl bekomm's</p>
    </div>
  </section>
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
      card.style.setProperty("--tilt", `${{(Math.random() * 2.4 - 1.2).toFixed(1)}}deg`);
      card.style.setProperty("--shift-x", `${{(Math.random() * 6 - 3).toFixed(1)}}px`);
      card.style.setProperty("--shift-y", `${{(Math.random() * 4 - 2).toFixed(1)}}px`);
      card.style.setProperty("--cap-rotate", `${{(Math.random() * 12 - 6).toFixed(0)}}deg`);
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
    @font-face {{ font-family: Skranji; src: url("../../assets/fonts/Skranji-Regular.ttf") format("truetype"); font-weight: 400; font-display: swap; }}
    :root {{ --beer-title-color: #f1e8d5; --paper: #f2e7cf; --paper-deep: #e7d8b8; --brew-green: #233a2e; --brew-green-deep: #16261e; --brew-ink: #302b22; --brew-gold: #a47b3c; --brew-gold-light: #d9c08a; --seal: #7c2b25; color-scheme: light; font-family: Georgia, "Palatino Linotype", "Book Antiqua", serif; color: var(--brew-ink); background: #e8dcc1; font-synthesis: none; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; background-color: #e8dcc1; background-image: radial-gradient(ellipse at 50% -5%, #f8f1df 0, transparent 58%), radial-gradient(ellipse at 50% 112%, rgb(120 90 45 / 22%) 0, transparent 62%), repeating-linear-gradient(0deg, rgb(120 90 45 / 4%) 0 1px, transparent 1px 4px), repeating-linear-gradient(90deg, rgb(120 90 45 / 3%) 0 1px, transparent 1px 5px); }}
    body::before {{ position: fixed; inset: 0; z-index: 0; content: ""; box-shadow: inset 0 0 190px rgb(80 55 20 / 24%); pointer-events: none; }}
    main {{ width: min(1040px, calc(100% - 2.5rem)); margin: 0 auto; padding: 2rem 0 4rem; }}
    nav {{ margin-bottom: 1.5rem; }}
    nav a {{ display: inline-flex; align-items: center; gap: .55rem; color: var(--brew-green); text-decoration: none; font-size: .9rem; font-weight: 700; }}
    nav a::before {{ content: "❧"; color: var(--brew-gold); font-size: 1.2rem; transition: transform .2s ease; }}
    nav a::after {{ content: "✦"; color: var(--brew-gold); font-size: .72rem; }}
    nav a:hover::before {{ transform: translateX(-4px); }}
    nav a:focus-visible {{ outline: 2px dashed var(--brew-gold); outline-offset: 4px; }}
    .beer-profile {{ position: relative; overflow: hidden; border: 4px double #a48b5a; outline: 1px solid #6f5a35; outline-offset: 5px; border-radius: 2px; background-color: var(--paper); background-image: radial-gradient(ellipse at 24% 0%, rgb(255 255 255 / 45%), transparent 58%), radial-gradient(ellipse at 82% 100%, rgb(150 112 55 / 14%), transparent 62%), repeating-linear-gradient(0deg, rgb(120 90 45 / 3.5%) 0 1px, transparent 1px 4px), repeating-linear-gradient(90deg, rgb(120 90 45 / 3%) 0 1px, transparent 1px 5px); box-shadow: 0 18px 48px rgb(48 43 34 / 22%), inset 0 0 70px rgb(120 90 45 / 12%); }}
    .beer-profile::before {{ position: absolute; z-index: 3; inset: 8px; border: 1px solid rgb(164 139 90 / 48%); content: ""; pointer-events: none; }}
    .frame-corner {{ position: absolute; z-index: 4; color: var(--brew-gold); font-family: Georgia, "Palatino Linotype", serif; font-size: clamp(1.1rem, 2vw, 1.45rem); line-height: 1; opacity: .85; text-shadow: 0 1px 0 rgb(255 255 255 / 45%); pointer-events: none; }}
    .fc-tl {{ top: 12px; left: 14px; }}
    .fc-tr {{ top: 12px; right: 14px; transform: scaleX(-1); }}
    .fc-bl {{ bottom: 12px; left: 14px; transform: rotate(180deg); }}
    .fc-br {{ bottom: 12px; right: 14px; transform: rotate(180deg) scaleX(-1); }}
    .hero {{ position: relative; isolation: isolate; min-height: 420px; display: flex; align-items: center; justify-content: space-between; gap: clamp(1.5rem, 4vw, 3.5rem); padding: clamp(2.6rem, 7vw, 5rem); border-bottom: 4px double #b49a64; background: radial-gradient(ellipse at 50% 50%, rgb(164 123 60 / 13%), transparent 62%), repeating-linear-gradient(135deg, transparent 0 14px, rgb(255 255 255 / 2%) 14px 15px), var(--brew-green); box-shadow: inset 0 0 130px rgb(0 0 0 / 32%); }}
    .hero::before {{ position: absolute; inset: 15px; border: 1px solid rgb(211 189 141 / 58%); content: ""; pointer-events: none; }}
    .hero::after {{ position: absolute; top: 2px; left: 50%; padding: 0 .8rem; background: var(--brew-green); color: #d3bd8d; content: "✦  ❧  ✦"; font-size: .9rem; letter-spacing: .35em; transform: translateX(-50%); }}
    .hero-copy {{ position: relative; z-index: 1; flex: 1 1 auto; max-width: 62%; }}
    .hero-copy::before {{ position: absolute; top: -.55em; left: -.28em; color: rgb(217 192 138 / 9%); content: "❦"; font-size: clamp(7rem, 15vw, 12rem); line-height: 1; pointer-events: none; }}
    .glass-feature {{ position: relative; z-index: 1; flex: 0 0 min(38%, 360px); display: flex; flex-direction: column; align-items: center; gap: .85rem; }}
    .glass-feature::before, .glass-feature::after {{ position: absolute; left: 50%; color: #d3bd8d; font-size: 1rem; letter-spacing: .5em; white-space: nowrap; transform: translateX(-50%); }}
    .glass-feature::before {{ top: -.8rem; content: "❧  ✦  ❧"; }}
    .glass-feature::after {{ bottom: -1rem; content: "· · · ✦ · · ·"; font-size: .75rem; }}
    .glass-visual {{ position: relative; isolation: isolate; width: clamp(210px, 25vw, 300px); aspect-ratio: 1; display: grid; place-items: center; }}
    .glass-visual::before {{ position: absolute; inset: 3%; content: ""; border: 3px double #f1e8d5; border-radius: 50%; background: color-mix(in srgb, var(--beer-color) 34%, #d8c7a2); box-shadow: inset 0 0 0 7px rgb(241 232 213 / 16%), inset 0 0 42px rgb(35 58 46 / 22%), 0 0 0 5px rgb(180 154 100 / 35%), 0 0 34px rgb(217 192 138 / 28%); }}
    .glass-halo {{ position: absolute; inset: -8%; z-index: -1; border: 1px dashed rgb(217 192 138 / 55%); border-radius: 50%; box-shadow: 0 0 28px rgb(217 192 138 / 18%), inset 0 0 24px rgb(217 192 138 / 14%); animation: haloSpin 46s linear infinite; pointer-events: none; }}
    .glass-caption {{ position: relative; width: 100%; padding: .9rem 1rem; border: 3px double #9a8254; border-radius: 2px; background: linear-gradient(#f2e8d2, #e7dac0); box-shadow: inset 0 0 0 3px #f8f1e3; text-align: center; }}
    .wax-seal {{ position: absolute; z-index: 3; top: -20px; right: -14px; width: clamp(48px, 6vw, 60px); aspect-ratio: 1; display: grid; place-items: center; border-radius: 46% 54% 48% 52% / 52% 47% 53% 48%; background: radial-gradient(circle at 34% 28%, #a7463a, var(--seal) 56%, #4e1713 100%); box-shadow: 0 5px 12px rgb(0 0 0 / 35%), inset 0 0 0 2px rgb(255 214 170 / 18%), inset 0 0 14px rgb(0 0 0 / 40%); color: #f3dfc2; font-family: Skranji, Georgia, serif; font-size: clamp(1.05rem, 2.4vw, 1.4rem); line-height: 1; text-shadow: 0 -1px 0 rgb(0 0 0 / 40%); transform: rotate(-8deg); pointer-events: none; }}
    .wax-seal::before {{ position: absolute; inset: 16%; border: 1px solid rgb(255 214 170 / 32%); border-radius: 50%; content: ""; }}
    .recommendation-label {{ margin: 0 0 .4rem; color: #74603b; font-size: .64rem; font-weight: 800; letter-spacing: .17em; text-transform: uppercase; }}
    .recommendation-label::before, .recommendation-label::after {{ padding: 0 .45rem; color: #a47b3c; content: "❧"; }}
    .recommendation {{ margin: 0; color: #302b22; font-size: clamp(.82rem, 1vw, .95rem); line-height: 1.5; text-align: center; }}
    .recommendation strong {{ color: #23372d; font-weight: 750; }}
    .beer-style {{ display: flex; align-items: center; gap: .7rem; margin: .75rem 0 0; color: #e6d7b5; font-size: clamp(1rem, 1.6vw, 1.2rem); font-weight: 600; line-height: 1.35; }}
    .beer-style::before {{ flex: 0 0 auto; color: #c6a96e; content: "❧"; font-size: 1.15rem; }}
    .beer-style::after {{ flex: 0 0 auto; color: #c6a96e; content: "✦"; font-size: .75rem; }}
    .eyebrow {{ display: flex; align-items: center; gap: .7rem; margin: 0 0 1.2rem; color: #d3bd8d; font-size: .72rem; font-weight: 750; letter-spacing: .2em; text-transform: uppercase; }}
    .eyebrow::before, .eyebrow::after {{ flex: 0 0 auto; color: #c6a96e; content: "❧"; font-size: 1rem; }}
    h1 {{ max-width: 720px; margin: 0; color: var(--beer-title-color); font-family: Skranji, Georgia, serif; font-size: clamp(2.8rem, 8vw, 6.5rem); font-weight: 700; letter-spacing: -.025em; line-height: .98; text-shadow: 1px 2px 0 rgb(0 0 0 / 25%); text-wrap: balance; }}
    h1::after {{ display: block; margin-top: .55rem; color: #c6a96e; content: "✦  ❦  ✦"; font-family: Georgia, serif; font-size: .2em; letter-spacing: .42em; text-align: center; text-shadow: none; }}
    .hero-mark {{ position: relative; z-index: 1; width: 42%; max-height: 88%; height: auto; overflow: visible; transform: rotate(5deg); filter: drop-shadow(0 16px 20px rgb(0 0 0 / 35%)); animation: glassFloat 7.5s ease-in-out infinite; }}
    .hero-mark .mug {{ fill: color-mix(in srgb, var(--beer-color) 78%, #d99432); stroke: #e8d9b8; stroke-width: 5; }}
    .hero-mark .foam {{ fill: #fff6df; }}
    .hero-mark .handle {{ fill: none; stroke: #e8d9b8; stroke-width: 9; }}
    .details-wrap {{ position: relative; padding: clamp(1.25rem, 2.5vw, 1.8rem); }}
    .details-wrap::before {{ position: absolute; top: -.8rem; left: 50%; z-index: 1; padding: 0 .9rem; background: var(--paper); color: #a47b3c; content: "❧  ✦  ❧"; letter-spacing: .3em; transform: translateX(-50%); }}
    .section-heading {{ display: flex; align-items: center; gap: .7rem; margin: 0 0 .9rem; color: #66583e; font-size: .72rem; font-weight: 800; letter-spacing: .18em; text-transform: uppercase; }}
    .section-heading::before, .section-heading::after {{ height: 1px; flex: 1; content: ""; background: linear-gradient(90deg, transparent, #9f895a 35%, #9f895a 65%, transparent); }}
    dl {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 1.1rem; margin: 0; }}
    .detail {{ min-width: 0; min-height: 0; display: flex; align-items: baseline; justify-content: flex-start; gap: .55rem; padding: .65rem .7rem; border: 1px solid #d1c3a1; border-top: 2px solid #b49a64; border-radius: 2px; background: linear-gradient(135deg, #f7f0e2, #eee4cf); box-shadow: inset 0 0 0 2px rgb(255 255 255 / 48%); }}
    .detail:hover {{ background: #faf4e8; }}
    dt {{ flex: 0 1 auto; color: #756b58; font-size: .68rem; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }}
    dt::after {{ padding-left: .35rem; color: #aa8d56; content: "·"; }}
    dd {{ min-width: 0; margin: 0; color: #302b22; font-size: .95rem; font-weight: 650; line-height: 1.35; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .detail-info, .detail-zutaten {{ position: relative; grid-column: span 3; display: block; padding: .9rem 1.6rem; border: 3px double #b49a64; border-left: 5px solid var(--beer-color); border-radius: 2px; background: linear-gradient(135deg, #f6efdf, #ece1ca); box-shadow: inset 0 0 0 3px rgb(255 255 255 / 45%); }}
    .detail-info::before, .detail-zutaten::before {{ position: absolute; top: .6rem; right: .65rem; color: #ad925c; content: "✦"; font-size: .8rem; }}
    .detail-info::after, .detail-zutaten::after {{ position: absolute; right: .65rem; bottom: .35rem; color: #ad925c; content: "❧"; font-size: 1.1rem; }}
    .detail-info dt, .detail-zutaten dt {{ color: #66583e; }}
    .detail-info dd, .detail-zutaten dd {{ margin-top: .35rem; font-size: .95rem; font-weight: 450; text-align: left; }}
    .detail-info dt::before, .detail-zutaten dt::before {{ padding-right: .35rem; color: #a47b3c; content: "✦"; }}
    .colophon {{ margin: 1.2rem 0 0; padding-top: .9rem; border-top: 3px double rgb(168 124 62 / 45%); color: #77613a; font-size: .68rem; font-weight: 700; letter-spacing: .2em; line-height: 1.8; text-align: center; text-transform: uppercase; }}
    .colophon::before {{ display: block; margin-bottom: .55rem; color: var(--brew-gold); content: "❧  ✦  ❧"; font-size: .85rem; letter-spacing: .35em; }}
    @keyframes glassFloat {{ 0%, 100% {{ transform: rotate(5deg) translateY(0); }} 50% {{ transform: rotate(4deg) translateY(-7px); }} }}
    @keyframes haloSpin {{ to {{ transform: rotate(360deg); }} }}
    @media (max-width: 720px) {{ dl {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .detail-info, .detail-zutaten {{ grid-column: span 2; }} }}
    @media (max-width: 600px) {{ main {{ width: calc(100% - 1rem); max-width: 1040px; padding: .75rem 0 2rem; }} nav {{ margin: 0 0 1rem .25rem; }} .beer-profile {{ width: 100%; min-width: 0; border-radius: 2px; box-shadow: 0 10px 28px rgb(48 43 34 / 16%); }} .hero {{ min-width: 0; min-height: 0; flex-direction: column; align-items: stretch; gap: 1.25rem; padding: 2rem 1.1rem 1.25rem; }} .hero-copy {{ max-width: 100%; min-width: 0; }} .glass-feature {{ flex: 0 1 auto; width: min(100%, 300px); max-width: 100%; min-width: 0; margin: 0 auto; gap: .65rem; }} .glass-visual {{ width: clamp(180px, 52vw, 220px); max-width: 100%; }} .hero-mark {{ width: 40%; }} .glass-caption {{ min-width: 0; padding: .7rem .8rem; }} .recommendation {{ font-size: .88rem; overflow-wrap: anywhere; }} h1 {{ max-width: calc(100% - 1rem); font-size: clamp(2.6rem, 13vw, 4.2rem); }} .details-wrap {{ width: 100%; min-width: 0; padding: .8rem .75rem .7rem; }} .section-heading {{ margin-bottom: .35rem; }} dl {{ width: 100%; min-width: 0; grid-template-columns: minmax(0, 1fr); column-gap: 0; }} .detail, .detail:nth-child(3n + 1), .detail:nth-child(3n + 2) {{ min-width: 0; padding: .55rem .5rem; transform: none; }} dd {{ min-width: 0; font-size: .92rem; overflow-wrap: anywhere; }} .detail-info, .detail-zutaten {{ grid-column: auto; padding: .65rem .7rem; }} .wax-seal {{ top: -16px; right: -8px; width: 44px; }} .hero-copy::before {{ font-size: 6rem; }} .frame-corner {{ font-size: 1rem; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation: none !important; scroll-behavior: auto !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
<main style="--beer-color: {beer[COLOR_HEADER]}">
  <nav><a href="../">andere Farbe</a></nav>
  <article class="beer-profile">
    <span class="frame-corner fc-tl" aria-hidden="true">❧</span>
    <span class="frame-corner fc-tr" aria-hidden="true">❧</span>
    <span class="frame-corner fc-bl" aria-hidden="true">❧</span>
    <span class="frame-corner fc-br" aria-hidden="true">❧</span>
    <header class="hero">
      <div class="hero-copy">
        <p class="eyebrow">Brausau · Bierportrait</p>
        <h1>{escape(beer['Biername'])}</h1>
        {beer_style_markup}
      </div>
      <div class="glass-feature">
        <div class="glass-visual"><span class="glass-halo" aria-hidden="true"></span>{glass_artwork}</div>
        <div class="glass-caption">
          <span class="wax-seal" aria-hidden="true">B</span>
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
      <footer class="colophon">Gebraut in Zürich · Nach alter Brauart · Wohl bekomm's</footer>
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
