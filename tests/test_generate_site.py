"""Tests for the sheet parsing and rendering in scripts/generate_site.py.

These run without Google access: the parsing functions take plain lists of rows
exactly as the Sheets API returns them (ragged, all strings).
"""

import pytest

import generate_site as gs
from generate_site import Beer, Glass, Style

BEER_HEADER = ["ID", *gs.DISPLAY_HEADERS, "Farbe", "Leer"]


def beer_row(**overrides: str) -> list[str]:
    values = {
        "ID": "beer-001",
        "Biername": "Saufrisch",
        "Bierstil": "",
        "Trinktemperatur": "8",
        "Farbe": "Rot",
        "Leer": "0",
    }
    values.update(overrides)
    return [values.get(header, "") for header in BEER_HEADER]


COLORS = {"rot": "#D64B32"}
WEIZEN = Style("Weizenbier", "Weizenglas", Glass("weizen.svg", "einem"))
STYLES = {"weissbier": WEIZEN}


# --- read_table -------------------------------------------------------------


def test_read_table_finds_columns_by_header_and_pads_short_rows():
    rows = [["b", "a"], ["2"]]
    assert gs.read_table(rows, "t", ("a", "b")) == [(2, {"a": "", "b": "2"})]


def test_read_table_accepts_header_aliases():
    rows = [["Optimale Trinktemperatur"], ["7"]]
    table = gs.read_table(rows, "t", ("Trinktemperatur",), gs.HEADER_ALIASES)
    assert table == [(2, {"Trinktemperatur": "7"})]


def test_read_table_reports_missing_headers_and_empty_tabs():
    with pytest.raises(ValueError, match="b, c"):
        gs.read_table([["a"]], "t", ("a", "b", "c"))
    with pytest.raises(ValueError, match="empty"):
        gs.read_table([], "t", ("a",))


# --- lookups ----------------------------------------------------------------


def test_color_lookup_is_case_insensitive_and_validates():
    assert gs.color_lookup([["Farbe", "Hex"], ["Rot", "#D64B32"], []]) == COLORS
    with pytest.raises(ValueError, match="Invalid or missing Hex"):
        gs.color_lookup([["Farbe", "Hex"], ["Rot", "red"]])
    with pytest.raises(ValueError, match="Duplicate"):
        gs.color_lookup([["Farbe", "Hex"], ["Rot", "#000000"], ["rot", "#111111"]])


def test_glass_lookup_resolves_aliases_and_articles():
    rows = [
        ["Name", "File", "Genus"],
        ["Flöte", "flut.svg", "f"],
        ["Weizenglas", "weizen.svg", "n"],
    ]
    assert gs.glass_lookup(rows) == {
        "flöte": Glass("flute.svg", "einer"),
        "weizenglas": Glass("weizen.svg", "einem"),
    }


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (["Glas", "missing.svg", "n"], "does not exist"),
        (["Glas", "../weizen.svg", "n"], "Invalid or missing SVG"),
        (["Glas", "weizen.svg", "x"], "Genus"),
        (["", "weizen.svg", "n"], "Missing glass Name"),
    ],
)
def test_glass_lookup_rejects_bad_rows(row, message):
    with pytest.raises(ValueError, match=message):
        gs.glass_lookup([["Name", "File", "Genus"], row])


def test_style_lookup_resolves_and_skips_unused_styles():
    glasses = {"weizenglas": Glass("weizen.svg", "einem")}
    rows = [
        ["Name", "style name", "Bierglas"],
        ["Weizenbier", "Weissbier", "Weizenglas"],
        ["Weizenbier", "weissbier", "Weizenglas"],  # same mapping again: allowed
        ["Ohne Glas", "Lonely", ""],
    ]
    assert gs.style_lookup(rows, glasses) == {
        "weissbier": Style("Weizenbier", "Weizenglas", glasses["weizenglas"])
    }


def test_style_lookup_rejects_unknown_glass_and_conflicts():
    glasses = {"a": Glass("a.svg", "einem"), "b": Glass("b.svg", "einer")}
    header = ["Name", "style name", "Bierglas"]
    with pytest.raises(ValueError, match="Unknown Bierglas"):
        gs.style_lookup([header, ["X", "x", "c"]], glasses)
    with pytest.raises(ValueError, match="Conflicting"):
        gs.style_lookup([header, ["X", "x", "a"], ["X", "x", "b"]], glasses)


# --- visible_beers ----------------------------------------------------------


def test_visible_beers_filters_on_leer_and_resolves_color_and_style():
    rows = [
        BEER_HEADER,
        beer_row(Bierstil="weissbier"),
        beer_row(ID="hidden", Leer="1"),
        beer_row(ID="empty-flag", Leer=""),
        beer_row(ID="hex", Farbe="#123456"),
    ]
    beers = gs.visible_beers(rows, COLORS, STYLES)
    assert [beer.id for beer in beers] == ["beer-001", "hex"]
    first, second = beers
    assert first.color == "#D64B32"
    assert first.values["Bierstil"] == "Weizenbier"
    assert (first.glass_name, first.glass_file) == ("Weizenglas", "weizen.svg")
    assert second.color == "#123456"
    assert second.glass_file == ""  # blank Bierstil: default mug


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (beer_row(ID=""), "has no ID"),
        (beer_row(ID="no spaces"), "is invalid"),
        (beer_row(Farbe="Lila"), "Unknown or invalid Farbe"),
        (beer_row(Bierstil="Unbekannt"), "Could not resolve Bierstil"),
    ],
)
def test_visible_beers_rejects_bad_rows(row, message):
    with pytest.raises(ValueError, match=message):
        gs.visible_beers([BEER_HEADER, row], COLORS, STYLES)


def test_visible_beers_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="Duplicate visible beer ID"):
        gs.visible_beers([BEER_HEADER, beer_row(), beer_row()], COLORS, STYLES)


def test_visible_beers_skips_rows_without_displayed_values():
    row = [{"ID": "x", "Farbe": "Rot", "Leer": "0"}.get(h, "") for h in BEER_HEADER]
    assert gs.visible_beers([BEER_HEADER, row], COLORS, STYLES) == []


# --- rendering --------------------------------------------------------------


def make_beer(**values: str) -> Beer:
    defaults = {header: "" for header in gs.DISPLAY_HEADERS}
    defaults.update(values)
    return Beer(id="beer-001", color="#D64B32", values=defaults)


def test_detail_page_escapes_sheet_values():
    page = gs.render_beer_detail(make_beer(Biername="<script>x</script>", Info="a & b"))
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page
    assert "a &amp; b" in page


def test_detail_page_falls_back_to_id_without_name():
    page = gs.render_beer_detail(make_beer())
    assert "<title>beer-001 | Brausau</title>" in page
    assert "<h1>beer-001</h1>" in page


def test_detail_page_adds_units_and_glass_recommendation():
    beer = Beer(
        id="w",
        color="#D64B32",
        values={**make_beer().values, "Alc": "5.2", "Trinktemperatur": "7"},
        glass_name="Weizenglas",
        glass_file="weizen.svg",
    )
    page = gs.render_beer_detail(beer)
    assert "5.2 Vol. % Alc" in page
    assert "<strong>7°C</strong>" in page
    assert "einem <strong>Weizenglas</strong>" in page
    assert 'color="#D64B32"' in page  # the glass SVG is tinted


def test_index_page_links_beers_and_handles_empty_list():
    page = gs.render_beer_index([make_beer(Biername="Saufrisch")])
    assert 'href="beer-001/"' in page
    assert 'aria-label="Saufrisch"' in page
    assert 'href="../"' in page  # back to the home page
    assert "kein Bier" in gs.render_beer_index([])


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", "/"),
        ("https://keini-io.github.io/brausau", "/brausau/"),
        ("https://keini-io.github.io/brausau/", "/brausau/"),
        ("https://brausau.ch", "/"),
    ],
)
def test_base_path(url, expected):
    assert gs.base_path(url) == expected


def test_sheet_range_quotes_tab_names():
    assert gs.sheet_range("it's") == "'it''s'!A:ZZ"


def test_link_preview_omits_absolute_urls_without_site_url(monkeypatch):
    monkeypatch.setattr(gs, "SITE_URL", "")
    assert "og:url" not in gs.link_preview("T", "D", "beers/")
    monkeypatch.setattr(gs, "SITE_URL", "https://example.org/b")
    tags = gs.link_preview("T", "D", "beers/")
    assert 'content="https://example.org/b/beers/"' in tags
    assert 'content="https://example.org/b/assets/apple-touch-icon.png"' in tags
