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
ASSET_DIR = SOURCE_DIR / "assets"

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


def asset_url(prefix: str, path: str) -> str:
    """URL of a file in site/assets/, versioned by its content."""
    return f"{prefix}assets/{path}?v={asset_version(ASSET_DIR / path)}"


def stylesheet(prefix: str, name: str) -> str:
    """Link the shared effects stylesheet plus the page's own stylesheet."""
    return (
        f'<link rel="stylesheet" href="{asset_url(prefix, "css/fx.css")}">\n'
        f'  <link rel="stylesheet" href="{asset_url(prefix, f"css/{name}")}">'
    )


def effects_script(prefix: str) -> str:
    return f'<script src="{asset_url(prefix, "js/fx.js")}" defer></script>'


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
  <meta name="description" content="{description}">
  <title>{title} | Brausau</title>
  {link_preview(title, description, "beers/")}
  {head_links("../")}
  <link rel="preload" href="../assets/fonts/Skranji-Bold.woff2" as="font" type="font/woff2" crossorigin>
  {stylesheet("../", "beer-list.css")}
</head>
<body>
<span class="chaos-tag tag-a" aria-hidden="true">Süffig!</span>
<span class="chaos-tag tag-b" aria-hidden="true">Prost ★</span>
<span class="chaos-tag tag-c" aria-hidden="true">Glug</span>
<main>
  <nav><a href="../">Startseite</a></nav>
  <header class="masthead">
    <p class="eyebrow" aria-hidden="true">Brausau · Sortiment</p>
    <h1>Was trinkst du <span class="accent">gerade?</span></h1>
    <p class="sub" aria-hidden="true">Kronkorken statt Krawatte · jeden Besuch neu durchgeschüttelt</p>
    <button class="shake" id="shake" type="button" hidden>Schütteln!</button>
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
{effects_script("../")}
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
  <meta name="description" content="Details zu {name}.">
  <title>{name} | Brausau</title>
  {link_preview(beer.name, description, f"beers/{beer.id}/")}
  {head_links("../../")}
  <link rel="preload" href="../../assets/fonts/Skranji-Bold.woff2" as="font" type="font/woff2" crossorigin>
  {stylesheet("../../", "beer-detail.css")}
</head>
<body>
<main style="--beer-color: {beer.color}">
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
      <div class="stamp" aria-hidden="true"><small>Von der Sau</small>geprüft</div>
      <h2 class="section-heading">Auf einen Blick</h2>
      <dl>
{details}
      </dl>
    </section>
  </article>
</main>
{effects_script("../../")}
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
  <nav><a href="{prefix}">Startseite</a></nav>
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
