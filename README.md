# Beer list site

A small static website with a home page and a beer list generated from a private Google Sheet. GitHub Actions reads the sheet with read-only access and publishes the site with GitHub Pages about once an hour.

The source spreadsheet remains private. **The generated page is public**, so only put information in the selected columns that you intend to show publicly.

## Configuration

The workflow is configured for:

- Spreadsheet ID: `1Vid837zHDbtGlQy9gt4F6RzCQuFDb4-6EhgV3y-iazQ`
- Tab: `biere`
- Header labels in the first row: `ID`, `Sud`, `Biername`, `Bierstil`, `Gebraut am`, `Alc`, `Stammwürze`, `IBU`, `Trinktemperatur`, `Best before`, `Passt gut zu`, `Info`, `Zutaten`, `Farbe`, `Glas`, and `Leer`

The script locates the beer columns by their header labels, so their order does not matter. Give each beer a unique, stable `ID` made of letters, numbers, hyphens, or underscores (for example, `beer-001`). IDs are used in detail-page URLs and should not change when a beer's name changes. In the beer tab, `Farbe` can contain a color name selected from the `Kronkorken` tab. That tab must have `Farbe` and `Hex` headers; each color name must be unique and each hex value must be six-digit format such as `#D64B32`. The generator resolves the selected name to its hex value, which tints the beer's crown-cap icon. Direct six-digit hex values in the beer tab's `Farbe` column are also accepted. The detail-page glass artwork uses this color for its liquid fill, with the illustration and text colors kept separate for legibility. The icons are shuffled into a new layout on each page load. `Leer` is used as a visibility flag and is not displayed: only rows where `Leer` is `0` are shown; rows with `1`, an empty cell, or any other value are excluded. Every visible row must have a valid, unique ID and color. Rows where all displayed cells are blank are also ignored. If there are no visible data rows, the page says “No data available.” Values are HTML-escaped and rendered as plain text.

The `glaeser` tab defines the available glass illustrations and must have `Name` and `File` headers. In the beer tab, `Glas` selects a `Name` from that tab; `File` must be a filename present in `site/assets/glasses/` (for example, `weizen.svg`). The generator embeds the selected SVG on the beer detail page and sets its `currentColor` liquid fill to the beer's resolved `Farbe` hex value. The glass outline and beer title share the `--beer-title-color` CSS variable in `scripts/generate_site.py`, so their color can be adjusted in one place. A blank `Glas` cell uses the built-in beer-mug illustration.

Units are hardcoded in the generator: `Alc` gets ` Vol. % Alc`, `Stammwürze` gets `°P`, and `Trinktemperatur` gets `°C`. Keep the sheet values numeric so units are not duplicated; empty cells stay `—` with no unit. The detail-page hero shows `Bierstil` as a subtitle and groups the selected glass with a serving recommendation using `Trinktemperatur` and the glass name; neither Bierstil nor temperature is repeated in the details grid. The generator accepts either `Trinktemperatur` or `Optimale Trinktemperatur` as the sheet header.

## Site files

- `site/index.html` is the home page template.
- `site/assets/logo_v2.svg` is loaded as the home-page logo image.
- `site/assets/favicon.svg` adapts between a black mark in light mode and a white mark in dark mode; `apple-touch-icon.png` is the square PNG fallback.
- `site/assets/fonts/` contains the Skranji font files and license.
- The workflow generates a randomly arranged crown-cap index at `/beers/` and detail pages at `/beers/<ID>/`, and copies the home page and assets into the published output.

## One-time Google setup

1. In the [Google Cloud Console](https://console.cloud.google.com/), create or select a project and enable the **Google Sheets API**.
2. Create a service account. It does not need a role on the whole project to read a sheet shared directly with it.
3. Create a JSON key for that service account and download it. Treat this file like a password; do not add it to this repository.
4. Copy the service account's `client_email` from the JSON file.
5. Share the spreadsheet with that email address as a **Viewer**. Do not enable link sharing on the spreadsheet.

The script requests only the `spreadsheets.readonly` OAuth scope.

## GitHub setup and publishing

1. Push this repository to GitHub on the `main` branch.
2. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `GOOGLE_SERVICE_ACCOUNT_JSON`
   - Value: the complete contents of the downloaded service-account JSON key file.
3. Open **Settings → Pages** and set the build/deployment source to **GitHub Actions**.
4. In **Actions**, select **Update and publish site**, then choose **Run workflow** to test the first build. A successful run deploys the page.
5. Later runs happen on pushes to `main`, when triggered manually, and on the hourly schedule. Scheduled runs may be delayed by GitHub, so the refresh is approximate rather than exact.

GitHub Pages provides a `github.io` URL initially. To use a custom domain later, set it under **Settings → Pages → Custom domain**, then add the DNS records GitHub specifies at your domain provider. Email DNS records can remain configured with your email host.

## Run locally (optional)

Requires Python 3.10 or newer. From the repository root, install dependencies:

```sh
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Set `GOOGLE_SERVICE_ACCOUNT_JSON` to the complete JSON key contents in your shell, then run:

```sh
python scripts/generate_site.py
```

The generated pages are `public/index.html` and `public/beers/index.html`. The workflow uses `_site` as its output directory so it can publish a clean artifact without committing generated data. For local use, set `OUTPUT_DIR` to choose a different location. Never commit the service-account JSON key.

In IntelliJ, run **Build and Preview Beer Site** to regenerate `public/` from the Google Sheet and start a local preview at `http://localhost:8000/`. Stop the run configuration to shut down the preview server. The Google credential is deliberately not stored in the run configuration: it must be available as `GOOGLE_SERVICE_ACCOUNT_JSON` in IntelliJ's inherited environment. If you set it in a PowerShell session, start IntelliJ from that same session; otherwise set it as a Windows user environment variable and restart IntelliJ. Live GitHub Pages publishing still happens through the GitHub Actions workflow.
