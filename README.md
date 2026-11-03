# Beer list site

A small static website generated from a private Google Sheet. GitHub Actions reads the sheet with read-only access, generates `index.html`, and publishes the generated page with GitHub Pages about once an hour.

The source spreadsheet remains private. **The generated page is public**, so only put information in the selected columns that you intend to show publicly.

## Configuration

The workflow is configured for:

- Spreadsheet ID: `Vid837zHDbtGlQy9gt4F6RzCQuFDb4-6EhgV3y-iazQ`
- Tab: `biere`
- Header labels in the first row: `Sud` and `Biername`

The script locates those columns by their header labels, so their order does not matter. It skips the header row and ignores rows where both selected cells are blank. If there are no data rows, the page says “No data available.” Values are HTML-escaped and rendered as plain text.

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

The generated page is `public/index.html`. The workflow uses `_site` as its output directory so it can publish a clean artifact without committing generated data. For local use, set `OUTPUT_DIR` to choose a different location. Never commit the service-account JSON key.
