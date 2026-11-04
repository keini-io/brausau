# Beer list site

A small static website with a home page and a beer list generated from a private Google Sheet. GitHub Actions reads the sheet with read-only access and publishes the site with GitHub Pages about once an hour.

The source spreadsheet remains private. **The generated page is public**, so only put information in the selected columns that you intend to show publicly.

## Configuration

The workflow is configured for:

- Spreadsheet ID: `1Vid837zHDbtGlQy9gt4F6RzCQuFDb4-6EhgV3y-iazQ`
- Tab: `biere`
- Header labels in the first row: `ID`, `Sud`, `Biername`, `Bierstil`, `Gebraut am`, `Alc`, `Stammwürze`, `IBU`, `Trinktemperatur`, `Best before`, `Passt gut zu`, `Info`, `Zutaten`, `Farbe`, and `Leer`

The script locates those columns by their header labels, so their order does not matter. Give each beer a unique, stable `ID` made of letters, numbers, hyphens, or underscores (for example, `beer-001`). IDs are used in detail-page URLs and should not change when a beer's name changes. `Farbe` must be a six-digit hex color such as `#D64B32`; it tints that beer's crown-cap icon on the beer index. The icons are shuffled into a new layout on each page load. It skips the header row. `Leer` is used as a visibility flag and is not displayed: only rows where `Leer` is `0` are shown; rows with `1`, an empty cell, or any other value are excluded. Every visible row must have a valid, unique ID and color. Rows where all displayed cells are blank are also ignored. If there are no visible data rows, the page says “No data available.” Values are HTML-escaped and rendered as plain text.

## Site files

- `site/index.html` is the home page template.
- `site/assets/logo_v2.svg` is loaded as the home-page logo image.
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

In IntelliJ, the shared run configurations **Generate Beer Site** and **Preview Beer Site** run these two commands using `.venv/Scripts/python.exe`. Generate the site first; then start the preview configuration and open `http://localhost:8000/`. The Google credential is deliberately not stored in the run configuration: it must be available as `GOOGLE_SERVICE_ACCOUNT_JSON` in IntelliJ's inherited environment. If you set it in a PowerShell session, start IntelliJ from that same session; otherwise set it as a Windows user environment variable and restart IntelliJ.
