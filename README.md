# Afterglow

A personal movie finder with posters, date and theater filters, and estimated feature start, credits, and end times.

## Selected theaters

`public/data/theaters.json` controls both collection and display: 35 theaters in New York, NY (including Angelika New York), 25 in Philadelphia, PA, four in Miami, FL, and 11 in Rockville, MD. Philadelphia retains all 25 previously covered theaters. The Rockville label retains the original ZIP 20832 search center; commercial venues still require verified coordinates within the existing 30-mile radius.

The collector requests only configured Atom and Cinemark venue URLs. AMC and Alamo filter the selected theaters before requesting showtimes or presentation details. Screen Slate has a shared daily page, so its rows are filtered after reading that page. Removed venues are also excluded from retained snapshots.

Angelika Film Center New York is collected from Atom's public dated listings. Its View listing link opens https://angelikafilmcenter.com/nyc/now-playing. This does not use an authorized direct Angelika API.

## Run locally

Install Node.js 22+, Python 3.11+, and curl. From the project folder:

```sh
python -m pip install -r requirements.txt
npm run dev
```

Refresh with `python scripts/refresh.py`. UTF-8 is explicit for the collector's JSON and HTTP files, including on Windows. Optional direct AMC access reads `AMC_API_KEY` from the environment or a local `.env`; never upload `.env`. Alternatively use `python scripts/refresh.py --amc-key-prompt`.

## Refresh and publish

The existing GitHub Actions workflow refreshes Wednesday around 8:17 AM Eastern, with daylight saving handling. Each refresh requests 14 calendar dates, from Wednesday through the second Tuesday. Commercial sources cannot backfill past dates and may not have posted all future dates. Missing schedules remain empty; later changes wait until the next refresh or a manual run. Source failures and retained stale screenings are shown in Sources & coverage.

A push publishes the existing snapshot. To collect the expanded window immediately after installing this update, open Actions → Refresh listings and publish → Run workflow. No workflow changes are needed. The workflow's AMC_API_KEY repository secret is optional; AMC/Regal listings can also come through Atom. Direct Regal remains disabled by default because access was blocked during verification.

## Timing and checks

Listed showtime plus 15–20 minutes gives the estimated feature start. The published runtime includes credits: estimated end is feature start plus runtime, and credits begin five minutes before that end. Preferences are adjustable. Missing runtimes have no end estimate; introductions, Q&As and intermissions can change actual timing.

Run `npm test` and `npm run build`. The build copies only `public/` to `dist/`. Source markup can change, and duplicate screenings can appear when sources overlap. No booking or checkout occurs in this app.
