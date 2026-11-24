"""Generate a public static page from a private Google Sheet."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT_DIR / "site"
GLASS_ASSET_DIR = SOURCE_DIR / "assets" / "glasses"
CSS_DIR = SOURCE_DIR / "assets" / "css"

SHEET_ID = os.environ.get(
    "GOOGLE_SHEET_ID", "1Vid837zHDbtGlQy9gt4F6RzCQuFDb4-6EhgV3y-iazQ"
)
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "biere")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "public"))
# Public URL of the site, e.g. https://keini-io.github.io/brausau (set by the
# workflow). Used for absolute link-preview URLs and the 404 page's links.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

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
BEER_HEADERS = (ID_HEADER, *DISPLAY_HEADERS, COLOR_HEADER, VISIBILITY_HEADER)
HEADER_ALIASES = {
    "Trinktemperatur": ("Optimale Trinktemperatur",),
}
UNIT_SUFFIXES = {
    "Alc": " Vol. % Alc",
    "Stammwürze": "°P",
    "Trinktemperatur": "°C",
}

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
GLASS_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.svg$")
GENUS_ARTICLES = {"m": "einem", "f": "einer", "n": "einem"}

Row = dict[str, str]


class Glass(NamedTuple):
    file: str
    article: str


class Style(NamedTuple):
    display_name: str
    glass_name: str
    glass: Glass


@dataclass(frozen=True)
class Beer:
    id: str
    color: str
    values: dict[str, str]
    glass_name: str = ""
    glass_file: str = ""
    glass_article: str = "einem"

    @property
    def name(self) -> str:
        """Display name; falls back to the ID so a page never has an empty title."""
        return self.values.get("Biername", "").strip() or self.id


# --- Google Sheets ---------------------------------------------------------


def sheet_range(tab_name: str) -> str:
    escaped_tab = tab_name.replace("'", "''")
    return f"'{escaped_tab}'!A:ZZ"


def sheets_session():
    """Return an authorized session that retries transient Google API errors."""
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2 import service_account
    from requests import Session
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON to the service-account JSON.")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(service_account_json), scopes=SCOPES
    )

    retry = Retry(
        total=5,
        backoff_factor=2,  # waits roughly 2, 4, 8, 16 s between attempts
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),  # POST is the token request; it's idempotent
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    def mount(session: Session) -> Session:
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    token_session = mount(Session())
    return mount(AuthorizedSession(credentials, auth_request=Request(token_session)))


def fetch_tabs(tab_names: list[str]) -> dict[str, list[list[str]]]:
    """Read several sheet tabs in a single API request."""
    session = sheets_session()
    url = (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        f"{quote(SHEET_ID, safe='')}/values:batchGet"
    )
    params = [("ranges", sheet_range(tab)) for tab in tab_names]
    params.append(("valueRenderOption", "FORMATTED_VALUE"))
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    value_ranges = response.json().get("valueRanges", [])
    if len(value_ranges) != len(tab_names):
        raise RuntimeError(
            f"Expected {len(tab_names)} ranges from Google Sheets, got {len(value_ranges)}."
        )
    return {
        tab: value_range.get("values", [])
        for tab, value_range in zip(tab_names, value_ranges)
    }


# --- Sheet parsing ---------------------------------------------------------


def read_table(
    rows: list[list[str]],
    tab: str,
    headers: tuple[str, ...],
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[tuple[int, Row]]:
    """Return (sheet row number, {header: raw cell}) for every data row.

    Columns are located by header label, so their order in the sheet does not
    matter. Missing trailing cells become empty strings.
    """
    if not rows:
        raise ValueError(
            f"The {tab!r} tab is empty; its first row must contain the headers: "
            + ", ".join(headers)
        )
    header_row = [str(cell).strip() for cell in rows[0]]
    aliases = aliases or {}
    indexes: dict[str, int] = {}
    missing = []
    for name in headers:
        for candidate in (name, *aliases.get(name, ())):
            if candidate in header_row:
                indexes[name] = header_row.index(candidate)
                break
        else:
            missing.append(name)
    if missing:
        raise ValueError(
            f"Could not find expected header(s) in the {tab!r} tab: " + ", ".join(missing)
        )
    return [
        (
            row_number,
            {name: str(row[i]) if i < len(row) else "" for name, i in indexes.items()},
        )
        for row_number, row in enumerate(rows[1:], start=2)  # Row 1 is the header.
    ]


def color_lookup(rows: list[list[str]]) -> dict[str, str]:
    """Map casefolded color names from the Kronkorken tab to hex values."""
    colors: dict[str, str] = {}
    for row_number, row in read_table(rows, COLOR_TAB, (COLOR_NAME_HEADER, COLOR_HEX_HEADER)):
        name = row[COLOR_NAME_HEADER].strip()
        hex_color = row[COLOR_HEX_HEADER].strip()
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


def glass_lookup(rows: list[list[str]]) -> dict[str, Glass]:
    """Map casefolded glass names from the glaeser tab to their SVG and article."""
    glasses: dict[str, Glass] = {}
    headers = (GLASS_NAME_HEADER, GLASS_FILE_HEADER, GLASS_GENUS_HEADER)
    for row_number, row in read_table(rows, GLASS_TAB, headers):
        name = row[GLASS_NAME_HEADER].strip()
        filename = row[GLASS_FILE_HEADER].strip()
        genus = row[GLASS_GENUS_HEADER].strip().casefold()
        if not name and not filename and not genus:
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
        if genus not in GENUS_ARTICLES:
            raise ValueError(
                f"Invalid or missing Genus for glass {name!r} in {GLASS_TAB!r} "
                f"tab row {row_number}: {genus!r}. Use m, f, or n."
            )
        key = name.casefold()
        if key in glasses:
            raise ValueError(f"Duplicate glass Name {name!r} in {GLASS_TAB!r} tab.")
        glasses[key] = Glass(local_filename, GENUS_ARTICLES[genus])
    return glasses


def style_lookup(rows: list[list[str]], glasses: dict[str, Glass]) -> dict[str, Style]:
    """Map casefolded beer-style references to display names and glasses."""
    styles: dict[str, Style] = {}
    headers = (STYLE_NAME_HEADER, STYLE_REFERENCE_HEADER, STYLE_GLASS_HEADER)
    for row_number, row in read_table(rows, STYLE_TAB, headers):
        display_name = row[STYLE_NAME_HEADER].strip()
        reference = row[STYLE_REFERENCE_HEADER].strip()
        glass_name = row[STYLE_GLASS_HEADER].strip()
        if not display_name and not reference and not glass_name:
            continue
        if not display_name:
            raise ValueError(f"Missing Name in {STYLE_TAB!r} tab row {row_number}.")
        if not reference or not glass_name:
            # An unused style can lack a reference or glass. If a beer selects
            # it, the missing relationship is reported for that beer row.
            continue
        glass = glasses.get(glass_name.casefold())
        if glass is None:
            raise ValueError(
                f"Unknown Bierglas {glass_name!r} in {STYLE_TAB!r} tab row "
                f"{row_number}; add a matching Name to the {GLASS_TAB!r} tab."
            )
        resolved = Style(display_name, glass_name, glass)
        key = reference.casefold()
        if key in styles and styles[key] != resolved:
            raise ValueError(
                f"Conflicting mappings for style name {reference!r} in the {STYLE_TAB!r} tab."
            )
        styles[key] = resolved
    return styles


def visible_beers(
    rows: list[list[str]],
    colors: dict[str, str],
    styles: dict[str, Style],
) -> list[Beer]:
    """Return the beers to publish: rows where Leer is 0, validated."""
    beers = []
    seen_ids: dict[str, int] = {}
    for row_number, row in read_table(rows, SHEET_TAB, BEER_HEADERS, HEADER_ALIASES):
        if row[VISIBILITY_HEADER].strip() != "0":
            continue
        beer_id = row[ID_HEADER].strip()
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

        color_choice = row[COLOR_HEADER].strip()
        if COLOR_PATTERN.fullmatch(color_choice):
            color = color_choice
        elif color_choice.casefold() in colors:
            color = colors[color_choice.casefold()]
        else:
            raise ValueError(
                f"Unknown or invalid Farbe value on sheet row {row_number}: "
                f"{color_choice!r}. Select a color Name defined in the {COLOR_TAB!r} tab "
                f"or use a six-digit hex color such as #D64B32."
            )

        values = {name: row[name] for name in DISPLAY_HEADERS}
        if not any(value.strip() for value in values.values()):
            continue

        glass_details = {}
        style_name = values["Bierstil"].strip()
        if style_name:
            style = styles.get(style_name.casefold())
            if style is None:
                raise ValueError(
                    f"Could not resolve Bierstil {style_name!r} on sheet row "
                    f"{row_number} through the {STYLE_TAB!r} and {GLASS_TAB!r} tabs."
                )
            values["Bierstil"] = style.display_name
            glass_details = {
                "glass_name": style.glass_name,
                "glass_file": style.glass.file,
                "glass_article": style.glass.article,
            }
        beers.append(Beer(id=beer_id, color=color, values=values, **glass_details))
    return beers


# --- Rendering -------------------------------------------------------------


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def base_path(site_url: str = SITE_URL) -> str:
    """URL path the site is served under, e.g. '/brausau/' for a project page."""
    path = urlparse(site_url).path.strip("/")
    return f"/{path}/" if path else "/"


def asset_version(path: Path) -> str:
    """Short content hash so browsers fetch a stylesheet again after it changes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def stylesheet(prefix: str, name: str) -> str:
    version = asset_version(CSS_DIR / name)
    return f'<link rel="stylesheet" href="{prefix}assets/css/{name}?v={version}">'


def head_links(prefix: str) -> str:
    return (
        f'<link rel="icon" type="image/svg+xml" href="{prefix}assets/favicon.svg?v=2">\n'
        f'  <link rel="apple-touch-icon" href="{prefix}assets/apple-touch-icon.png">'
    )


def link_preview(title: str, description: str, page_path: str) -> str:
    """Open Graph tags for link previews in messengers and social media."""
    tags = [
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Brausau">',
        '<meta property="og:locale" content="de_CH">',
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:description" content="{escape(description)}">',
    ]
    if SITE_URL:
        tags.append(f'<meta property="og:url" content="{escape(SITE_URL + "/" + page_path)}">')
        tags.append(
            f'<meta property="og:image" content="{escape(SITE_URL)}/assets/apple-touch-icon.png">'
        )
    return "\n  ".join(tags)


def render_beer_index(beers: list[Beer]) -> str:
    if beers:
        cards = "\n".join(
            f'<a class="beer-card" href="{escape(beer.id)}/" '
            f'aria-label="{escape(beer.name)}" '
            f'style="--beer-color: {beer.color}">'
            '<span class="cap" aria-hidden="true"></span>'
            "</a>"
            for beer in beers
        )
        content = f'<div class="beer-grid" id="beer-grid">{cards}</div>'
    else:
        content = '<p class="empty">Gerade ist kein Bier verfügbar.</p>'

    title = "Was trinkst du gerade?"
    description = "Die Brausau-Bierliste."
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
    :root {{ color-scheme: dark; --ink: #0d0d0d; --paper: #f4efdf; --acid: #d5ff00; --hot: #ff2f92; --ice: #6ff7ff; font-family: system-ui, sans-serif; color: var(--ink); background: var(--ink); }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; overflow-x: hidden; background-color: var(--ink); background-image: radial-gradient(circle at 8% 2%, rgb(255 47 146 / .38), transparent 34%), radial-gradient(circle at 96% 28%, rgb(213 255 0 / .26), transparent 38%), radial-gradient(circle at 50% 120%, rgb(111 247 255 / .2), transparent 45%), radial-gradient(rgb(244 239 223 / .1) 1px, transparent 1.2px); background-size: 140% 140%, 150% 150%, 160% 160%, 9px 9px; background-position: 0 0, 100% 0, 50% 100%, 0 0; animation: bgWash 15s ease-in-out infinite alternate; }}
    body::before {{ position: fixed; inset: -20vmax; z-index: 0; content: ""; background-image: radial-gradient(var(--acid) 1px, transparent 1.2px); background-size: 11px 11px; opacity: .18; animation: drift 20s linear infinite, hue 11s ease-in-out infinite alternate; pointer-events: none; }}
    body::after {{ position: fixed; inset: 0; z-index: 0; content: ""; background: repeating-linear-gradient(0deg, rgb(0 0 0 / .2) 0 2px, transparent 2px 6px), radial-gradient(circle at 50% 45%, transparent 52%, rgb(0 0 0 / .6) 100%); mix-blend-mode: multiply; opacity: .4; animation: scan 6s linear infinite; pointer-events: none; }}
    .chaos-tag {{ position: fixed; z-index: 1; padding: .3rem .58rem .25rem; background: var(--hot); border: 3px solid var(--ink); box-shadow: 4px 4px 0 var(--acid); color: var(--ink); font-family: Skranji, Georgia, serif; font-size: .82rem; letter-spacing: .14em; line-height: 1; text-transform: uppercase; pointer-events: none; }}
    .tag-a {{ top: 12%; left: 2.5%; transform: rotate(-8deg); animation: tagBob 6s ease-in-out infinite, hue 9s ease-in-out infinite alternate; }}
    .tag-b {{ right: 2%; bottom: 27%; background: var(--ice); transform: rotate(6deg); animation: tagBob 7.5s ease-in-out infinite reverse; }}
    .tag-c {{ left: 3%; bottom: 13%; background: var(--acid); transform: rotate(5deg); animation: tagSpin 14s linear infinite; }}
    main {{ position: relative; isolation: isolate; width: min(980px, calc(100% - 2rem)); margin: 0 auto; padding: clamp(2rem, 5vw, 4rem) 0 clamp(6rem, 11vw, 8rem); }}
    main::before {{ position: absolute; top: -.1em; right: -.05em; z-index: -1; content: "★"; color: transparent; font-family: Skranji, Georgia, serif; font-size: clamp(9rem, 26vw, 22rem); line-height: 1; -webkit-text-stroke: 3px rgb(213 255 0 / .2); transform: rotate(12deg); animation: spin 32s linear infinite, ghostPulse 5.5s ease-in-out infinite; pointer-events: none; }}
    main::after {{ position: absolute; bottom: -1.6rem; left: -.08em; z-index: -1; content: "BIER"; color: transparent; font-family: Skranji, Georgia, serif; font-size: clamp(5rem, 16vw, 13rem); line-height: 1; -webkit-text-stroke: 2px rgb(255 47 146 / .22); transform: rotate(-6deg); animation: ghostSway 9s ease-in-out infinite; pointer-events: none; }}
    .masthead {{ position: relative; margin-bottom: clamp(1.5rem, 4vw, 2.6rem); padding-bottom: 1.4rem; border-bottom: 14px solid transparent; border-image: repeating-linear-gradient(-45deg, var(--ink) 0 14px, var(--acid) 14px 28px) 14; }}
    .eyebrow {{ display: inline-block; margin: 0 0 1.1rem; padding: .36rem .8rem .3rem; background: var(--hot); color: var(--ink); border: 2px solid var(--ink); box-shadow: 4px 4px 0 var(--acid); font-size: .72rem; font-weight: 900; letter-spacing: .2em; text-transform: uppercase; transform: rotate(-2deg); animation: eyebrowWiggle 5s ease-in-out infinite, hue 8s ease-in-out infinite alternate; }}
    h1 {{ max-width: 10ch; margin: 0; color: var(--acid); font-family: Skranji, Georgia, serif; font-size: clamp(3.3rem, 11.5vw, 8.4rem); font-weight: 700; letter-spacing: -.055em; line-height: .84; text-transform: uppercase; text-shadow: .035em .035em 0 var(--hot), .07em .07em 0 var(--ink), .095em .095em 0 var(--paper); transform: rotate(-1.5deg); text-wrap: balance; animation: titleGlitch 7s steps(1, end) infinite; }}
    h1 .accent {{ display: inline-block; padding: .02em .1em .08em; background: var(--hot); color: var(--paper); box-shadow: .055em .055em 0 var(--ink); transform: rotate(1.4deg); animation: accentWobble 3.4s ease-in-out infinite; }}
    .sub {{ margin: 1.2rem 0 0; color: var(--paper); font-size: clamp(.72rem, 1.6vw, .86rem); font-weight: 750; letter-spacing: .16em; text-transform: uppercase; }}
    .sub::before {{ content: "↳"; margin-right: .5em; color: var(--acid); animation: arrowNudge 1.4s ease-in-out infinite; display: inline-block; }}
    .beer-grid {{ position: relative; isolation: isolate; display: grid; grid-template-columns: repeat(auto-fill, minmax(clamp(106px, 13vw, 140px), 1fr)); gap: clamp(1.3rem, 4vw, 2.8rem) clamp(.7rem, 2.4vw, 1.8rem); padding: clamp(1.2rem, 4vw, 2.6rem); border: 3px solid var(--paper); background: #171717; background-image: radial-gradient(rgb(255 255 255 / .09) 1px, transparent 1.1px); background-size: 10px 10px; box-shadow: 10px 10px 0 var(--hot), 18px 18px 0 var(--acid), 0 0 90px rgb(213 255 0 / .12) inset; animation: boardPulse 5s ease-in-out infinite; }}
    .beer-grid::before {{ position: absolute; inset: -13px; z-index: -1; content: ""; background: conic-gradient(from 0deg, var(--acid), var(--hot), var(--ice), var(--hot), var(--acid)); filter: blur(3px); opacity: .4; animation: spin 9s linear infinite; }}
    .beer-card {{ position: relative; min-width: 0; aspect-ratio: 1; display: grid; place-items: center; color: inherit; text-decoration: none; transform: translate(var(--shift-x, 0), var(--shift-y, 0)) rotate(var(--tilt, 0deg)) scale(var(--card-scale, 1)); transition: transform .24s cubic-bezier(.2, .8, .2, 1), filter .24s ease; will-change: transform; }}
    .beer-card::before {{ position: absolute; inset: 12%; content: ""; border-radius: 50%; background: radial-gradient(circle at 38% 30%, #363636, #141414 62%); border: 3px solid var(--paper); box-shadow: 0 0 0 4px var(--ink), 7px 7px 0 var(--hot), 0 0 32px color-mix(in srgb, var(--beer-color) 48%, transparent); transform: rotate(-4deg); animation: capGlow var(--glow-duration, 4.5s) ease-in-out infinite; animation-delay: var(--glow-delay, 0s); }}
    .beer-card::after {{ position: absolute; top: 1%; left: 50%; z-index: 2; width: 48%; height: 10%; content: ""; background: rgb(213 255 0 / .72); border: 1px solid rgb(0 0 0 / .35); box-shadow: 0 2px 3px rgb(0 0 0 / .25); transform: translateX(-50%) rotate(-6deg); animation: tapeWiggle 5.5s ease-in-out infinite; animation-delay: var(--glow-delay, 0s); }}
    .cap {{ position: relative; z-index: 1; width: 74%; aspect-ratio: 142.08 / 130.56; display: block; background-color: var(--beer-color); -webkit-mask: url("../assets/kronkorken.svg") center / contain no-repeat; mask: url("../assets/kronkorken.svg") center / contain no-repeat; filter: drop-shadow(0 10px 6px rgb(0 0 0 / .55)); transform: rotate(var(--cap-rotate, 0deg)); transition: transform .28s cubic-bezier(.2, .8, .2, 1); animation: capWobble var(--wobble-duration, 5s) ease-in-out infinite alternate; animation-delay: var(--wobble-delay, 0s); }}
    .beer-card:hover, .beer-card:focus-visible {{ transform: translate(var(--shift-x, 0), var(--shift-y, 0)) rotate(0deg) scale(calc(var(--card-scale, 1) * 1.1)); outline: none; z-index: 3; }}
    .beer-card:hover .cap, .beer-card:focus-visible .cap {{ animation: none; transform: rotate(0deg) scale(1.1); }}
    .beer-card:hover::before, .beer-card:focus-visible::before {{ animation: none; box-shadow: 0 0 0 4px var(--ink), 7px 7px 0 var(--hot), 0 0 55px var(--beer-color); }}
    .beer-card:focus-visible::after {{ background: var(--paper); }}
    .beer-card:focus-visible {{ outline: 3px dashed var(--acid); outline-offset: 7px; }}
    .empty {{ padding: 1.4rem; border: 3px solid var(--ink); background: var(--paper); box-shadow: 8px 8px 0 var(--hot); font-family: Skranji, Georgia, serif; transform: rotate(-1deg); animation: emptyWiggle 4s ease-in-out infinite; }}
    .ticker {{ position: fixed; z-index: 4; right: 0; bottom: 0; left: 0; overflow: hidden; border-top: 3px solid var(--ink); background: var(--acid); color: var(--ink); }}
    .ticker-track {{ display: flex; width: max-content; animation: marquee 18s linear infinite; }}
    .ticker-track.alt {{ color: var(--paper); background: var(--ink); animation: marquee 27s linear infinite; }}
    .ticker-track.alt span {{ text-shadow: 2px 2px 0 var(--hot); }}
    .ticker span {{ display: block; padding: .38rem 0; font-family: Skranji, Georgia, serif; font-size: clamp(.76rem, 1.6vw, .96rem); letter-spacing: .2em; text-transform: uppercase; white-space: nowrap; }}
    .ticker:hover .ticker-track {{ animation-play-state: paused; }}
    @keyframes bgWash {{ 0% {{ background-position: 0 0, 100% 0, 50% 100%, 0 0; }} 100% {{ background-position: 12% 8%, 88% 12%, 44% 88%, 18px 9px; }} }}
    @keyframes drift {{ to {{ transform: translate3d(18px, 18px, 0) rotate(4deg); }} }}
    @keyframes hue {{ to {{ filter: hue-rotate(65deg) saturate(1.6); }} }}
    @keyframes scan {{ to {{ background-position: 0 22px, center; }} }}
    @keyframes tagBob {{ 0%, 100% {{ translate: 0 0; }} 50% {{ translate: 0 -7px; }} }}
    @keyframes tagSpin {{ to {{ rotate: 360deg; }} }}
    @keyframes ghostPulse {{ 0%, 100% {{ opacity: .75; }} 50% {{ opacity: 1; }} }}
    @keyframes ghostSway {{ 0%, 100% {{ translate: 0 0; }} 50% {{ translate: 10px -8px; }} }}
    @keyframes eyebrowWiggle {{ 0%, 100% {{ transform: rotate(-2deg) translateY(0); }} 50% {{ transform: rotate(2deg) translateY(-3px); }} }}
    @keyframes titleGlitch {{ 0%, 88%, 100% {{ text-shadow: .035em .035em 0 var(--hot), .07em .07em 0 var(--ink), .095em .095em 0 var(--paper); translate: 0 0; }} 90% {{ text-shadow: -.02em .02em 0 var(--ice), .06em -.015em 0 var(--hot), .095em .095em 0 var(--paper); translate: -2px 0; }} 93% {{ text-shadow: .04em -.02em 0 var(--hot), -.03em .03em 0 var(--ice), .095em .095em 0 var(--paper); translate: 2px -1px; }} 96% {{ text-shadow: .02em .04em 0 var(--acid), .07em .07em 0 var(--hot), .11em .11em 0 var(--ink); translate: -1px 1px; }} }}
    @keyframes accentWobble {{ 0%, 100% {{ transform: rotate(1.4deg) scale(1); }} 50% {{ transform: rotate(-2.6deg) scale(1.04); }} }}
    @keyframes arrowNudge {{ 0%, 100% {{ transform: translateX(0); }} 50% {{ transform: translateX(6px); }} }}
    @keyframes boardPulse {{ 0%, 100% {{ box-shadow: 10px 10px 0 var(--hot), 18px 18px 0 var(--acid), 0 0 90px rgb(213 255 0 / .12) inset; }} 50% {{ box-shadow: 14px 14px 0 var(--hot), 25px 25px 0 var(--acid), 0 0 120px rgb(255 47 146 / .22) inset, 0 0 70px rgb(213 255 0 / .22); }} }}
    @keyframes capGlow {{ 0%, 100% {{ box-shadow: 0 0 0 4px var(--ink), 7px 7px 0 var(--hot), 0 0 22px color-mix(in srgb, var(--beer-color) 48%, transparent); }} 50% {{ box-shadow: 0 0 0 4px var(--ink), 7px 7px 0 var(--hot), 0 0 46px var(--beer-color); }} }}
    @keyframes tapeWiggle {{ 0%, 100% {{ transform: translateX(-50%) rotate(-6deg) translateY(0); }} 50% {{ transform: translateX(-50%) rotate(3deg) translateY(-2px); }} }}
    @keyframes capWobble {{ 0% {{ transform: rotate(var(--cap-rotate, 0deg)); }} 100% {{ transform: rotate(calc(var(--cap-rotate, 0deg) + var(--wobble, 12deg))); }} }}
    @keyframes emptyWiggle {{ 0%, 100% {{ transform: rotate(-1deg) translateY(0); }} 50% {{ transform: rotate(1.4deg) translateY(-4px); }} }}
    @keyframes marquee {{ to {{ transform: translateX(-50%); }} }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    @media (max-width: 1180px) {{ .chaos-tag {{ display: none; }} }}
    @media (max-width: 620px) {{ main {{ width: calc(100% - 1.4rem); padding-top: 1.6rem; }} h1 {{ font-size: clamp(2.6rem, 13vw, 4.4rem); }} .beer-grid {{ gap: 1.6rem .6rem; padding: 1.2rem .8rem; box-shadow: 6px 6px 0 var(--hot), 11px 11px 0 var(--acid), 0 0 60px rgb(213 255 0 / .12) inset; animation: none; }} .beer-grid::before {{ inset: -7px; }} .beer-card::after {{ height: 9%; }} .ticker-track.alt {{ display: none; }} .empty {{ animation: none; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation: none !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
<span class="chaos-tag tag-a" aria-hidden="true">Süffig!</span>
<span class="chaos-tag tag-b" aria-hidden="true">Prost ★</span>
<span class="chaos-tag tag-c" aria-hidden="true">Glug</span>
<main>
  <header class="masthead">
    <p class="eyebrow" aria-hidden="true">Brausau · Sortiment</p>
    <h1>Was trinkst du <span class="accent">gerade?</span></h1>
    <p class="sub" aria-hidden="true">Kronkorken statt Krawatte · jeden Besuch neu durchgeschüttelt</p>
  </header>
  {content}
</main>
<div class="ticker" aria-hidden="true">
  <div class="ticker-track">
    <span>Was trinkst du gerade? ★ Brausau ★ Bier ★ Was trinkst du gerade? ★ Brausau ★ Bier ★&nbsp;</span>
    <span>Was trinkst du gerade? ★ Brausau ★ Bier ★ Was trinkst du gerade? ★ Brausau ★ Bier ★&nbsp;</span>
  </div>
  <div class="ticker-track alt">
    <span>Kronkorken statt Krawatte ★ Kein Benehmen ★ Kronkorken statt Krawatte ★ Kein Benehmen ★&nbsp;</span>
    <span>Kronkorken statt Krawatte ★ Kein Benehmen ★ Kronkorken statt Krawatte ★ Kein Benehmen ★&nbsp;</span>
    <span>Kronkorken statt Krawatte ★ Kein Benehmen ★ Kronkorken statt Krawatte ★ Kein Benehmen ★&nbsp;</span>
    <span>Kronkorken statt Krawatte ★ Kein Benehmen ★ Kronkorken statt Krawatte ★ Kein Benehmen ★&nbsp;</span>
  </div>
</div>
<script>
  const grid = document.getElementById("beer-grid");
  if (grid) {{
    const cards = Array.from(grid.children);
    const chaos = window.matchMedia("(max-width: 620px)").matches ? 0.55 : 1;
    for (let i = cards.length - 1; i > 0; i--) {{
      const j = Math.floor(Math.random() * (i + 1));
      [cards[i], cards[j]] = [cards[j], cards[i]];
    }}
    cards.forEach((card) => {{
      card.style.setProperty("--tilt", `${{((Math.random() * 30 - 15) * chaos).toFixed(1)}}deg`);
      card.style.setProperty("--shift-x", `${{((Math.random() * 40 - 20) * chaos).toFixed(1)}}px`);
      card.style.setProperty("--shift-y", `${{((Math.random() * 36 - 18) * chaos).toFixed(1)}}px`);
      card.style.setProperty("--card-scale", `${{(1 + (Math.random() * 0.42 - 0.21) * chaos).toFixed(2)}}`);
      card.style.setProperty("--cap-rotate", `${{(Math.random() * 360).toFixed(0)}}deg`);
      card.style.setProperty("--wobble", `${{((Math.random() * 26 + 8) * (Math.random() < 0.5 ? -1 : 1)).toFixed(0)}}deg`);
      card.style.setProperty("--wobble-duration", `${{(2.8 + Math.random() * 3.8).toFixed(2)}}s`);
      card.style.setProperty("--wobble-delay", `${{(-Math.random() * 6).toFixed(2)}}s`);
      card.style.setProperty("--glow-duration", `${{(3.2 + Math.random() * 3).toFixed(2)}}s`);
      card.style.setProperty("--glow-delay", `${{(-Math.random() * 5).toFixed(2)}}s`);
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


DEFAULT_MUG_SVG = """<svg class="hero-mark" viewBox="0 0 120 140" aria-hidden="true" focusable="false">
        <path class="handle" d="M91 48h10a14 14 0 0 1 0 42H91" />
        <path class="mug" d="M25 38h66v76a8 8 0 0 1-8 8H33a8 8 0 0 1-8-8z" />
        <path class="foam" d="M24 43c-9-7-6-20 5-23 2-12 17-17 26-9 9-10 26-5 28 7 13-4 23 9 16 20-4 6-11 8-20 8H34c-4 0-7-1-10-3z" />
        <path d="M39 62v43M53 62v43M67 62v43" fill="none" stroke="#f3dfb7" stroke-linecap="round" stroke-width="4" opacity=".55" />
      </svg>"""


def render_glass(beer: Beer) -> str:
    """Inline the beer's glass SVG, tinting its currentColor liquid with the beer color."""
    if not beer.glass_file:
        return DEFAULT_MUG_SVG
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    root = ET.parse(GLASS_ASSET_DIR / beer.glass_file).getroot()
    root.set("class", "hero-mark")
    root.set("color", beer.color)
    root.set("aria-hidden", "true")
    root.set("focusable", "false")
    return ET.tostring(root, encoding="unicode")


def render_beer_detail(beer: Beer) -> str:
    name = escape(beer.name)
    beer_style = escape(beer.values["Bierstil"].strip())
    beer_style_markup = f'<p class="beer-style">{beer_style}</p>' if beer_style else ""
    recommendation = (
        '<p class="recommendation">Am besten auf '
        f"<strong>{escape(detail_value('Trinktemperatur', beer.values['Trinktemperatur']))}</strong> "
        f"gekühlt aus {escape(beer.glass_article)} "
        f"<strong>{escape(beer.glass_name or 'Bierglas')}</strong> geniessen.</p>"
    )
    details = "\n".join(
        f'<div class="detail detail-{escape(header.lower().replace(" ", "-"))}">'
        f"<dt>{escape(header)}</dt>"
        f"<dd>{escape(detail_value(header, beer.values[header]))}</dd></div>"
        for header in DISPLAY_HEADERS
        if header not in ("Biername", "Bierstil", "Trinktemperatur")
    )
    description = f"{beer.name}: {beer.values['Bierstil'].strip() or 'Bier'} aus der Brausau."
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
    :root {{ --beer-title-color: #f5efdf; --paper: #f4efdf; --ink: #101010; --punk-pink: #ff2f92; --acid: #d5ff00; --ice: #6ff7ff; color-scheme: dark; font-family: system-ui, sans-serif; color: var(--ink); background: var(--ink); font-synthesis: none; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; overflow-x: hidden; background-color: var(--ink); background-image: radial-gradient(circle at 14% 0%, rgb(255 47 146 / .3), transparent 35%), radial-gradient(circle at 92% 40%, rgb(213 255 0 / .18), transparent 40%), radial-gradient(circle at 50% 110%, rgb(111 247 255 / .16), transparent 45%), radial-gradient(rgb(244 239 223 / .11) 1px, transparent 1.2px); background-size: 140% 140%, 150% 150%, 160% 160%, 9px 9px; background-position: 0 0, 100% 0, 50% 100%, 0 0; animation: bgWash 16s ease-in-out infinite alternate; }}
    body::after {{ position: fixed; inset: 0; z-index: 0; content: ""; background: repeating-linear-gradient(0deg, rgb(0 0 0 / .18) 0 2px, transparent 2px 6px), radial-gradient(circle at 50% 45%, transparent 55%, rgb(0 0 0 / .55) 100%); mix-blend-mode: multiply; opacity: .34; animation: scan 6.5s linear infinite; pointer-events: none; }}
    main {{ position: relative; isolation: isolate; width: min(1120px, calc(100% - 2rem)); margin: 0 auto; padding: clamp(1.4rem, 4vw, 3rem) 0 clamp(6.5rem, 12vw, 9rem); }}
    main::before {{ position: absolute; top: .2em; left: -.06em; z-index: -1; content: "PROST"; color: transparent; font-family: Skranji, Georgia, serif; font-size: clamp(7rem, 20vw, 19rem); line-height: 1; -webkit-text-stroke: 2px rgb(213 255 0 / .18); transform: rotate(-8deg); animation: ghostSway 10s ease-in-out infinite, ghostPulse 5s ease-in-out infinite; pointer-events: none; }}
    main::after {{ position: absolute; right: -.1em; bottom: 4rem; z-index: -1; content: "GLUG"; color: transparent; font-family: Skranji, Georgia, serif; font-size: clamp(4rem, 12vw, 10rem); line-height: 1; -webkit-text-stroke: 2px rgb(255 47 146 / .24); transform: rotate(9deg); animation: ghostSway 8s ease-in-out infinite reverse; pointer-events: none; }}
    nav {{ margin: 0 0 1.7rem; padding-left: .3rem; }}
    nav a {{ position: relative; display: inline-flex; align-items: center; gap: .55rem; padding: .45rem .8rem .4rem; border: 3px solid var(--ink); background: var(--paper); color: var(--ink); box-shadow: 5px 5px 0 var(--punk-pink); font-family: Skranji, Georgia, serif; font-size: .92rem; letter-spacing: .04em; text-decoration: none; text-transform: uppercase; transform: rotate(-1.5deg); transition: transform .18s ease, box-shadow .18s ease, background-color .18s ease; }}
    nav a::before {{ content: "←"; color: #d60065; font-size: 1.2rem; transition: transform .2s ease; }}
    nav a:hover, nav a:focus-visible {{ background: var(--acid); transform: rotate(1.5deg) translate(-2px, -2px); box-shadow: 8px 8px 0 var(--punk-pink); animation: navShake .32s linear infinite; }}
    nav a:focus-visible {{ outline: 3px dashed var(--ink); outline-offset: 4px; }}
    nav a:hover::before {{ animation: arrowNudge .5s ease-in-out infinite; }}
    .beer-profile {{ position: relative; border: 4px solid var(--ink); background: var(--paper); box-shadow: 16px 16px 0 var(--ink), 24px 24px 0 var(--punk-pink), 32px 32px 0 var(--acid); transform: rotate(-.35deg); animation: profilePulse 6s ease-in-out infinite; }}
    .beer-profile::before, .beer-profile::after {{ position: absolute; z-index: 5; width: clamp(70px, 12vw, 120px); height: 32px; content: ""; background: rgb(244 239 223 / .78); border: 1px solid rgb(16 16 16 / .3); box-shadow: 0 2px 4px rgb(0 0 0 / .18); }}
    .beer-profile::before {{ top: -17px; left: 8%; transform: rotate(-6deg); animation: tapeWiggle 5s ease-in-out infinite; }}
    .beer-profile::after {{ right: 6%; bottom: -17px; background: rgb(213 255 0 / .74); transform: rotate(5deg); animation: tapeWiggle 6.5s ease-in-out infinite reverse; }}
    .hero {{ position: relative; isolation: isolate; min-height: 420px; display: flex; align-items: center; justify-content: space-between; gap: clamp(1.5rem, 4vw, 3rem); padding: clamp(2rem, 6vw, 4.5rem); border-bottom: 4px solid var(--ink); background: radial-gradient(circle at 78% 22%, color-mix(in srgb, var(--beer-color) 42%, transparent), transparent 37%), radial-gradient(circle at 10% 90%, rgb(255 47 146 / .34), transparent 36%), #0e0e0e; background-size: 130% 130%, 140% 140%, auto; animation: heroWash 11s ease-in-out infinite alternate; overflow: hidden; }}
    .hero::before {{ position: absolute; inset: -20%; z-index: -1; content: ""; background-image: radial-gradient(rgb(244 239 223 / .2) 1px, transparent 1.2px); background-size: 7px 7px; opacity: .55; mix-blend-mode: screen; animation: drift 14s linear infinite; }}
    .hero::after {{ position: absolute; right: -.1em; bottom: -.34em; z-index: -1; content: "SAU"; color: transparent; font-family: Skranji, Georgia, serif; font-size: clamp(7rem, 18vw, 15rem); line-height: 1; -webkit-text-stroke: 2px rgb(213 255 0 / .24); transform: rotate(-8deg); animation: ghostSway 9s ease-in-out infinite; pointer-events: none; }}
    .hero-halo {{ position: absolute; top: 50%; right: 4%; z-index: 0; width: clamp(240px, 34vw, 420px); aspect-ratio: 1; border-radius: 50%; background: conic-gradient(from 0deg, transparent, color-mix(in srgb, var(--beer-color) 70%, transparent), transparent 42%, rgb(255 47 146 / .5), transparent 75%); filter: blur(24px); opacity: .55; transform: translateY(-50%); animation: spin 13s linear infinite; pointer-events: none; }}
    .hero-sticker {{ position: absolute; z-index: 3; display: grid; place-items: center; padding: .4rem .55rem .35rem; background: var(--acid); border: 3px solid var(--ink); border-radius: 50%; color: var(--ink); font-family: Skranji, Georgia, serif; font-size: clamp(.7rem, 1.3vw, .9rem); line-height: .95; text-align: center; text-transform: uppercase; box-shadow: 4px 4px 0 var(--punk-pink); pointer-events: none; }}
    .hs-a {{ top: 1.1rem; right: 1.2rem; width: clamp(58px, 7vw, 78px); aspect-ratio: 1; transform: rotate(9deg); animation: stickerBob 4.2s ease-in-out infinite; }}
    .hs-b {{ bottom: 1.2rem; left: 1.1rem; width: clamp(54px, 6.5vw, 72px); aspect-ratio: 1; background: var(--ice); transform: rotate(-11deg); animation: stickerBob 5.4s ease-in-out infinite reverse; }}
    .hero-copy {{ position: relative; z-index: 1; flex: 1 1 auto; max-width: 62%; }}
    .eyebrow {{ display: inline-flex; align-items: center; gap: .7rem; margin: 0 0 1.1rem; padding: .4rem .7rem .35rem; background: var(--acid); color: var(--ink); border: 3px solid var(--ink); box-shadow: 5px 5px 0 var(--punk-pink); font-size: .7rem; font-weight: 900; letter-spacing: .2em; text-transform: uppercase; transform: rotate(-2.5deg); animation: eyebrowWiggle 5s ease-in-out infinite; }}
    .eyebrow::before {{ width: 1.5rem; height: 3px; content: ""; background: var(--ink); }}
    h1 {{ max-width: 760px; margin: 0; color: var(--beer-title-color); font-family: Skranji, Georgia, serif; font-size: clamp(3rem, 8.5vw, 7rem); font-weight: 700; letter-spacing: -.04em; line-height: .9; text-shadow: .04em .04em 0 var(--punk-pink), .08em .08em 0 #000, 0 0 30px rgb(0 0 0 / .5); text-wrap: balance; transform: rotate(-1.2deg); animation: titleGlitch 6.5s steps(1, end) infinite; }}
    h1::after {{ display: block; width: 38%; height: 6px; margin-top: .18em; content: ""; background: repeating-linear-gradient(90deg, var(--acid) 0 14px, transparent 14px 22px); transform: rotate(-1deg); animation: stripeSlide 1.2s linear infinite; }}
    .beer-style {{ display: inline-flex; align-items: center; gap: .7rem; margin: 1rem 0 0; padding: .38rem .75rem .32rem; border: 3px solid var(--beer-title-color); background: color-mix(in srgb, var(--beer-color) 24%, #101010); color: var(--beer-title-color); box-shadow: 5px 5px 0 var(--punk-pink); font-family: Skranji, Georgia, serif; font-size: clamp(.95rem, 1.6vw, 1.15rem); letter-spacing: .04em; line-height: 1.2; text-transform: uppercase; transform: rotate(1.2deg); animation: styleWobble 4.6s ease-in-out infinite; }}
    .beer-style::before {{ width: 1.4rem; height: 2px; flex: 0 0 auto; content: ""; background: var(--acid); animation: arrowNudge 1.3s ease-in-out infinite; }}
    .glass-feature {{ position: relative; z-index: 1; flex: 0 0 min(40%, 390px); display: flex; flex-direction: column; align-items: center; gap: 1rem; }}
    .glass-visual {{ position: relative; width: clamp(220px, 27vw, 330px); aspect-ratio: 1; display: grid; place-items: center; }}
    .glass-visual::before {{ position: absolute; inset: 2%; content: ""; border: 4px solid var(--ink); border-radius: 50%; background: radial-gradient(circle at 35% 30%, color-mix(in srgb, var(--beer-color) 65%, #fff) 0 8%, transparent 58%), radial-gradient(circle at 50% 55%, color-mix(in srgb, var(--beer-color) 45%, #101010), #101010 72%); box-shadow: 0 0 0 5px var(--acid), 10px 10px 0 var(--punk-pink), 0 0 70px color-mix(in srgb, var(--beer-color) 45%, transparent); transform: rotate(5deg); animation: glassPulse 3.6s ease-in-out infinite; }}
    .glass-visual::after {{ position: absolute; inset: -5%; content: ""; border: 3px dashed var(--acid); border-radius: 50%; opacity: .8; animation: spin 9s linear infinite; }}
    .orbit {{ position: absolute; inset: -9%; border-radius: 50%; animation: spin 6.5s linear infinite; pointer-events: none; }}
    .orbit::before, .orbit::after {{ position: absolute; width: 13px; aspect-ratio: 1; content: ""; border: 2px solid var(--ink); border-radius: 50%; background: var(--acid); box-shadow: 0 0 16px var(--acid); }}
    .orbit::before {{ top: -6px; left: 50%; }}
    .orbit::after {{ right: 18%; bottom: -6px; background: var(--punk-pink); box-shadow: 0 0 16px var(--punk-pink); }}
    .glass-caption {{ position: relative; width: 100%; padding: .95rem 1rem .9rem; border: 3px solid var(--ink); background: var(--acid); color: var(--ink); box-shadow: 8px 8px 0 var(--punk-pink); text-align: center; transform: rotate(-1.6deg); animation: captionSway 5s ease-in-out infinite; }}
    .glass-caption::before {{ position: absolute; top: -14px; left: 50%; width: 84px; height: 26px; content: ""; background: rgb(244 239 223 / .8); border: 1px solid rgb(16 16 16 / .3); transform: translateX(-50%) rotate(3deg); animation: tapeWiggle 4.6s ease-in-out infinite; }}
    .recommendation-label {{ margin: 0 0 .35rem; color: var(--ink); font-size: .64rem; font-weight: 900; letter-spacing: .17em; text-transform: uppercase; animation: labelBlink 4.2s steps(1, end) infinite; }}
    .recommendation {{ margin: 0; color: var(--ink); font-size: clamp(.82rem, 1vw, .95rem); font-weight: 550; line-height: 1.5; text-align: center; }}
    .recommendation strong {{ padding: 0 .14em; background: var(--punk-pink); color: var(--ink); font-weight: 850; }}
    .hero-mark {{ position: relative; z-index: 1; width: 44%; max-height: 88%; height: auto; overflow: visible; transform: rotate(6deg); filter: drop-shadow(0 18px 18px rgb(0 0 0 / .55)); animation: float 3.8s ease-in-out infinite, beerGlow 2.8s ease-in-out infinite; }}
    .hero-mark .mug {{ fill: color-mix(in srgb, var(--beer-color) 78%, #d99432); stroke: var(--beer-title-color); stroke-width: 5; }}
    .hero-mark .foam {{ fill: #fff6df; }}
    .hero-mark .handle {{ fill: none; stroke: var(--beer-title-color); stroke-width: 9; }}
    .details-wrap {{ position: relative; padding: clamp(1.4rem, 4vw, 2.4rem); }}
    .section-heading {{ position: relative; display: inline-flex; align-items: center; gap: .7rem; margin: 0 0 1.2rem; padding: .45rem .8rem .38rem; background: var(--ink); color: var(--acid); box-shadow: 6px 6px 0 var(--punk-pink); font-family: Skranji, Georgia, serif; font-size: clamp(.9rem, 2vw, 1.05rem); letter-spacing: .12em; text-transform: uppercase; transform: rotate(-1.4deg); animation: headingFloat 4.2s ease-in-out infinite; }}
    .section-heading::before {{ content: "✖"; color: var(--acid); font-size: .8em; animation: spin 5s linear infinite; }}
    .section-heading::after {{ width: 1.2rem; height: 3px; content: ""; background: var(--punk-pink); animation: arrowNudge 1.1s ease-in-out infinite; }}
    dl {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem .9rem; margin: 0; align-items: start; }}
    .detail {{ position: relative; min-width: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: .45rem .6rem; padding: .7rem .75rem; border: 3px solid var(--ink); background: #fff9e8; box-shadow: 5px 5px 0 color-mix(in srgb, var(--beer-color) 28%, var(--punk-pink)); transition: transform .18s ease, box-shadow .18s ease, background-color .18s ease; animation: detailBob 4.4s ease-in-out infinite; }}
    .detail:nth-child(3n + 1) {{ transform: rotate(-.9deg); animation-delay: -.4s; }}
    .detail:nth-child(3n + 2) {{ transform: rotate(.7deg); animation-delay: -1.5s; }}
    .detail:nth-child(3n) {{ transform: rotate(-.3deg); animation-delay: -2.6s; }}
    .detail:hover {{ background: #fff; transform: translate(-2px, -2px) rotate(0deg); box-shadow: 8px 8px 0 color-mix(in srgb, var(--beer-color) 28%, var(--punk-pink)), 0 0 26px color-mix(in srgb, var(--beer-color) 45%, transparent); animation-play-state: paused; }}
    dt {{ flex: 0 1 auto; padding: .16rem .3rem; background: var(--acid); color: var(--ink); box-shadow: 2px 2px 0 var(--ink); font-size: .64rem; font-weight: 900; letter-spacing: .05em; text-transform: uppercase; }}
    dd {{ min-width: 0; margin: 0; color: var(--ink); font-size: .98rem; font-weight: 750; line-height: 1.25; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .detail-info, .detail-zutaten {{ grid-column: 1 / -1; display: block; padding: 1rem 1.1rem; border: 3px solid var(--ink); border-left: 10px solid var(--beer-color); background: #fff9e8; box-shadow: 7px 7px 0 var(--ink); animation: infoPulse 4.8s ease-in-out infinite, detailBob 5.5s ease-in-out infinite; overflow: visible; }}
    .detail-info::before, .detail-zutaten::before {{ position: absolute; top: -1.05rem; right: 1rem; content: "★"; color: var(--punk-pink); font-size: 1.4rem; -webkit-text-stroke: 1px var(--ink); animation: spin 6s linear infinite; }}
    .detail-info dt, .detail-zutaten dt {{ display: inline-block; transform: rotate(-1deg); }}
    .detail-info dd, .detail-zutaten dd {{ margin-top: .5rem; font-size: .98rem; font-weight: 500; line-height: 1.45; text-align: left; }}
    .ticker {{ position: fixed; z-index: 7; right: 0; bottom: 0; left: 0; overflow: hidden; border-top: 3px solid var(--ink); background: var(--acid); color: var(--ink); }}
    .ticker-track {{ display: flex; width: max-content; animation: marquee 19s linear infinite; }}
    .ticker-track.alt {{ color: var(--paper); background: var(--ink); animation: marquee 29s linear infinite; }}
    .ticker-track.alt span {{ text-shadow: 2px 2px 0 var(--punk-pink); }}
    .ticker span {{ display: block; padding: .38rem 0; font-family: Skranji, Georgia, serif; font-size: clamp(.76rem, 1.6vw, .96rem); letter-spacing: .2em; text-transform: uppercase; white-space: nowrap; }}
    .ticker:hover .ticker-track {{ animation-play-state: paused; }}
    @keyframes spin {{ to {{ transform: rotate(365deg); }} }}
    @keyframes float {{ 0%, 100% {{ transform: rotate(6deg) translateY(0); }} 50% {{ transform: rotate(6deg) translateY(-10px); }} }}
    @keyframes bgWash {{ 0% {{ background-position: 0 0, 100% 0, 50% 100%, 0 0; }} 100% {{ background-position: 12% 8%, 88% 14%, 44% 86%, 18px 9px; }} }}
    @keyframes scan {{ to {{ background-position: 0 22px, center; }} }}
    @keyframes ghostSway {{ 0%, 100% {{ translate: 0 0; }} 50% {{ translate: 12px -10px; }} }}
    @keyframes ghostPulse {{ 0%, 100% {{ opacity: .7; }} 50% {{ opacity: 1; }} }}
    @keyframes heroWash {{ 0% {{ background-position: 0 0, 100% 100%, 0 0; }} 100% {{ background-position: 16% 12%, 84% 88%, 0 0; }} }}
    @keyframes drift {{ to {{ transform: translate3d(16px, 16px, 0) rotate(4deg); }} }}
    @keyframes stickerBob {{ 0%, 100% {{ translate: 0 0; rotate: 0deg; }} 50% {{ translate: 4px -7px; rotate: -12deg; }} }}
    @keyframes eyebrowWiggle {{ 0%, 100% {{ transform: rotate(-2.5deg) translateY(0); }} 50% {{ transform: rotate(1.5deg) translateY(-3px); }} }}
    @keyframes titleGlitch {{ 0%, 89%, 100% {{ text-shadow: .04em .04em 0 var(--punk-pink), .08em .08em 0 #000, 0 0 30px rgb(0 0 0 / .5); translate: 0 0; }} 91% {{ text-shadow: -.025em .02em 0 var(--ice), .06em -.02em 0 var(--acid), .08em .08em 0 #000; translate: -2px 0; }} 94% {{ text-shadow: .05em -.02em 0 var(--punk-pink), -.03em .03em 0 var(--ice), .08em .08em 0 #000; translate: 2px -1px; }} 97% {{ text-shadow: .02em .05em 0 var(--acid), .07em .07em 0 var(--punk-pink), .11em .11em 0 #000; translate: -1px 1px; }} }}
    @keyframes stripeSlide {{ to {{ background-position: 22px 0; }} }}
    @keyframes styleWobble {{ 0%, 100% {{ transform: rotate(1.2deg) translateY(0); }} 50% {{ transform: rotate(-1.4deg) translateY(-4px); }} }}
    @keyframes arrowNudge {{ 0%, 100% {{ transform: translateX(0); }} 50% {{ transform: translateX(6px); }} }}
    @keyframes glassPulse {{ 0%, 100% {{ transform: rotate(5deg) scale(1); box-shadow: 0 0 0 5px var(--acid), 10px 10px 0 var(--punk-pink), 0 0 55px color-mix(in srgb, var(--beer-color) 45%, transparent); }} 50% {{ transform: rotate(9deg) scale(1.03); box-shadow: 0 0 0 5px var(--acid), 14px 14px 0 var(--punk-pink), 0 0 100px color-mix(in srgb, var(--beer-color) 75%, transparent); }} }}
    @keyframes captionSway {{ 0%, 100% {{ transform: rotate(-1.6deg) translateY(0); }} 50% {{ transform: rotate(1deg) translateY(-4px); }} }}
    @keyframes tapeWiggle {{ 0%, 100% {{ transform: translateX(-50%) rotate(3deg); }} 50% {{ transform: translateX(-50%) rotate(-3deg) translateY(-2px); }} }}
    @keyframes labelBlink {{ 0%, 90%, 95%, 100% {{ opacity: 1; }} 92%, 94% {{ opacity: .45; }} }}
    @keyframes beerGlow {{ 0%, 100% {{ filter: drop-shadow(0 18px 18px rgb(0 0 0 / .55)); }} 50% {{ filter: drop-shadow(0 18px 18px rgb(0 0 0 / .55)) drop-shadow(0 0 22px color-mix(in srgb, var(--beer-color) 70%, transparent)); }} }}
    @keyframes navShake {{ 0%, 100% {{ translate: 0 0; }} 25% {{ translate: -1px 0; }} 75% {{ translate: 1px 0; }} }}
    @keyframes profilePulse {{ 0%, 100% {{ box-shadow: 16px 16px 0 var(--ink), 24px 24px 0 var(--punk-pink), 32px 32px 0 var(--acid); }} 50% {{ box-shadow: 19px 19px 0 var(--ink), 28px 28px 0 var(--punk-pink), 38px 38px 0 var(--acid), 0 0 60px color-mix(in srgb, var(--beer-color) 35%, transparent); }} }}
    @keyframes detailBob {{ 0%, 100% {{ translate: 0 0; }} 50% {{ translate: 0 -4px; }} }}
    @keyframes infoPulse {{ 0%, 100% {{ box-shadow: 7px 7px 0 var(--ink); }} 50% {{ box-shadow: 10px 10px 0 var(--ink), 0 0 40px color-mix(in srgb, var(--beer-color) 35%, transparent); }} }}
    @keyframes headingFloat {{ 0%, 100% {{ translate: 0 0; }} 50% {{ translate: 0 -5px; }} }}
    @keyframes marquee {{ to {{ transform: translateX(-50%); }} }}
    @media (max-width: 760px) {{ dl {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .detail-info, .detail-zutaten {{ grid-column: 1 / -1; }} .hero {{ flex-direction: column; align-items: stretch; gap: 2rem; padding: 2.5rem 1.3rem 2rem; }} .hero-copy {{ max-width: 100%; }} .glass-feature {{ width: min(100%, 340px); margin: 0 auto; }} .glass-visual {{ width: clamp(200px, 56vw, 260px); }} h1 {{ font-size: clamp(2.8rem, 13vw, 4.6rem); }} .beer-profile {{ transform: none; box-shadow: 8px 8px 0 var(--ink), 14px 14px 0 var(--punk-pink), 20px 20px 0 var(--acid); animation: none; }} .hero-sticker {{ display: none; }} .hero-halo {{ right: 50%; top: 78%; width: 58vw; transform: translate(50%, -50%); }} }}
    @media (max-width: 520px) {{ main {{ width: calc(100% - 1.4rem); padding-top: 1.2rem; }} nav {{ margin-left: .2rem; }} dl {{ grid-template-columns: minmax(0, 1fr); gap: 1.1rem; }} .detail, .detail:nth-child(3n + 1), .detail:nth-child(3n + 2), .detail:nth-child(3n) {{ transform: none; animation: none; }} .detail-info, .detail-zutaten {{ grid-column: auto; animation: none; }} h1 {{ font-size: clamp(2.6rem, 14vw, 4rem); }} .glass-visual {{ width: clamp(190px, 58vw, 240px); }} .glass-caption {{ padding: .8rem .8rem .75rem; }} .recommendation {{ font-size: .88rem; overflow-wrap: anywhere; }} .hero::after {{ font-size: 7rem; }} .ticker-track.alt {{ display: none; }} main::after {{ display: none; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation: none !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
<main style="--beer-color: {beer[COLOR_HEADER]}; --punk-pink: color-mix(in srgb, var(--beer-color) 18%, #ff2f92); --acid: color-mix(in srgb, var(--beer-color) 18%, #d5ff00)">
  <nav><a href="../">andere Farbe</a></nav>
  <article class="beer-profile">
    <header class="hero">
      <span class="hero-halo" aria-hidden="true"></span>
      <span class="hero-sticker hs-a" aria-hidden="true">Prost!</span>
      <span class="hero-sticker hs-b" aria-hidden="true">Süffig</span>
      <div class="hero-copy">
        <p class="eyebrow">Brausau · Bierportrait</p>
        <h1>{name}</h1>
        {beer_style_markup}
      </div>
      <div class="glass-feature">
        <div class="glass-visual"><span class="orbit" aria-hidden="true"></span>{render_glass(beer)}</div>
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
<div class="ticker" aria-hidden="true">
  <div class="ticker-track">
    <span>Brausau ★ Bier ★ Brausau ★ Bier ★ Brausau ★ Bier ★&nbsp;</span>
    <span>Brausau ★ Bier ★ Brausau ★ Bier ★ Brausau ★ Bier ★&nbsp;</span>
  </div>
  <div class="ticker-track alt">
    <span>Prost du! ★ Kein Benehmen ★ Prost du! ★ Kein Benehmen ★ Prost du! ★&nbsp;</span>
    <span>Prost du! ★ Kein Benehmen ★ Prost du! ★ Kein Benehmen ★ Prost du! ★&nbsp;</span>
    <span>Prost du! ★ Kein Benehmen ★ Prost du! ★ Kein Benehmen ★ Prost du! ★&nbsp;</span>
    <span>Prost du! ★ Kein Benehmen ★ Prost du! ★ Kein Benehmen ★ Prost du! ★&nbsp;</span>
  </div>
</div>
</body>
</html>
"""


def render_not_found() -> str:
    """404 page. GitHub Pages serves it for any missing path, so links are absolute."""
    prefix = base_path()
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>Nicht gefunden | Brausau</title>
  {head_links(prefix)}
  {stylesheet(prefix, "beer-list.css")}
</head>
<body>
<main>
  <nav><a href="{prefix}">← Startseite</a></nav>
  <h1>Hier ist nichts mehr im Glas.</h1>
  <p class="empty">Diese Seite gibt es nicht (mehr). Vielleicht ist das Bier schon ausgetrunken.
  <a href="{prefix}beers/">Zur Bierliste</a></p>
</main>
</body>
</html>
"""


# --- Main ------------------------------------------------------------------


def main() -> None:
    if not (SOURCE_DIR / "index.html").is_file():
        raise FileNotFoundError("Missing site/index.html home page template.")

    tabs = fetch_tabs([SHEET_TAB, COLOR_TAB, STYLE_TAB, GLASS_TAB])
    glasses = glass_lookup(tabs[GLASS_TAB])
    beers = visible_beers(
        tabs[SHEET_TAB],
        color_lookup(tabs[COLOR_TAB]),
        style_lookup(tabs[STYLE_TAB], glasses),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_DIR, OUTPUT_DIR, dirs_exist_ok=True)
    (OUTPUT_DIR / "404.html").write_text(render_not_found(), encoding="utf-8")

    beers_dir = OUTPUT_DIR / "beers"
    if beers_dir.exists():
        shutil.rmtree(beers_dir)
    beers_dir.mkdir(parents=True)
    index_path = beers_dir / "index.html"
    index_path.write_text(render_beer_index(beers), encoding="utf-8")
    for beer in beers:
        detail_dir = beers_dir / beer.id
        detail_dir.mkdir()
        (detail_dir / "index.html").write_text(render_beer_detail(beer), encoding="utf-8")
    print(
        f"Copied home page to {OUTPUT_DIR / 'index.html'}, generated {index_path}, "
        f"{OUTPUT_DIR / '404.html'}, and {len(beers)} beer detail page(s)."
    )


if __name__ == "__main__":
    main()
