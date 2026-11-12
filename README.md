# FTC AutoScout on the web

AutoScout 4.1.0 as a website. It runs on a Mac, a Windows PC, an iPhone or an Android phone, in the browser, with nothing to install.

It is not a rewrite. The website runs the desktop file `AutoScout.py`, unchanged, inside the browser, using Pyodide (Python compiled to WebAssembly). Every number, table column, chart and piece of wording comes from that file. To update the website to a new AutoScout version, replace `AutoScout.py` and publish again. 

## Publish it on GitHub Pages (about 5 minutes)

1. On github.com, create a new public repository (for example `autoscout-web`), or use an existing one.
2. Upload everything in this folder to the top level of the repository: **Add file > Upload files**, then drag in all the files and the `py` and `assets` folders. Keep the hidden `.nojekyll` file too: if your file manager hides it, GitHub still works without it, it just builds a little slower.
3. Go to **Settings > Pages**. Under "Build and deployment", choose **Deploy from a branch**, branch **main**, folder **/ (root)**, then **Save**.
4. After a minute or two the site is live at `https://<your-github-name>.github.io/<repository-name>/`.

To put it inside an existing repository instead, copy the folder to `docs/` and choose the **/docs** folder in step 3.

## Updating to a new AutoScout version

Replace `AutoScout.py` with the new single file (same name) and commit. The site picks it up on the next visit.

The website reads parts of the desktop file by name. If a new version renames or removes any of these, `py/webbridge.py` needs a matching change:

- Engine: `Session`, `Settings`, `FTCScoutClient`, `ApiError`, `html_report`, `export_csv`, `printable_text`, the preferences functions (`get`, `save`), `default_cache_dir`.
- Desktop pages, whose `refresh()` code the website runs: `HomeTab`, `CompetitionDayTab`, `SetupTab`, `TeamsTab`, `TeamChartPanel`, `MatchesTab`, `MyTeamTab`, `StandingsTab`, `AlliancesTab`, `AllianceWinRateTab`, `AnalysisTab`, `ChartsTab`, `LiveScoutingTab`, `TeamResearchTab`, `SettingsTab`.
- Tables and charts: `TEAM_COLUMNS`, `MATCH_COLUMNS`, `MY_MATCH_COLUMNS`, `STANDING_COLUMNS`, `ALLIANCE_COLUMNS`, `PARTNER_COLUMNS`, `SEAT_COLUMNS`, `WRONG_COLUMNS`, `FLAG_COLUMNS`, `TEAM_VAR_LABELS`, `RATING_SERIES`, `PAIR_UNITS`, `PRED_VARS`, `ALLIANCE_VARS`, `EVENT_X_VARS`, `MATCH_X_VARS`, `ALL_MATCH_X_VARS`, `EVENT_Y_LABELS`, `MATCH_Y_LABELS`, `EXPLAIN_SHORT`, `EXPLAIN_LONG`, `OVERRIDE_FIELDS`, `NONE_LABEL`, `TDC_ISSUES`, `FOCUS_MODES`, `TRISTATE`, `CAP_EXPLAINER`, `RATING_EXPLAINER`, `CHARTS_GUIDE`.

Changes inside those (new columns, new formulas, new chart options, new text) need nothing: they flow through.

## How it works

| File | What it does |
|---|---|
| `AutoScout.py` | The desktop app, unchanged. |
| `worker.js` | Downloads Pyodide 0.29.5 from jsDelivr, loads `AutoScout.py` and runs it in a background thread (a Web Worker) so the page never freezes while an event loads. |
| `py/tkstub.py` | Stand-ins for tkinter, which browsers don't have, so the desktop file imports. |
| `py/webbridge.py` | Runs each desktop page's own `refresh()` against stand-in widgets that record what it writes (table rows, labels, chart series) and hands that to the page as JSON. Also connects the engine's FTCScout client to the browser. |
| `index.html`, `app.css`, `app.js` | The web interface: the same 14 workspaces, sortable tables, the charts (a port of the desktop chart drawing), dialogs. Plain HTML and JavaScript with no build step. |
| `sw.js`, `manifest.webmanifest`, `assets/` | Let the site work offline once visited and be added to a phone's home screen like an app. |

Storage: sessions are saved in the browser (IndexedDB), in exactly the JSON format the desktop app uses. **Setup > Download session file** and **Open session file** move a session between the website, the desktop app and other devices. The FTCScout cache is kept in the browser too.

Network: the first visit downloads the engine (about 7 MB) from jsDelivr. After that the site talks only to the FTCScout API (and YouTube if you use the video player). There is no server, account or tracking; scouting notes never leave the device unless you download the session file.

## Differences from the desktop app

- **Live Scouting** plays the YouTube stream inside the page, beside the notes. The desktop has to open a separate browser.
- **Changes save themselves** (notes, scores, overrides, settings) to the browser. "Save session" is still there.
- **Competition Day's refresh button** fetches new match results only (a few seconds). The desktop's "Refresh event data" reloads everything, which takes minutes at a big event. A full reload is still on Setup.
- **Save image** on a chart writes a PNG instead of PostScript.
- **Team search** shows the first 30 matches. FTCScout ignores the requested limit and can return thousands of teams for a word like "robot".
- The "Detach / video window" button is left out: the video is already in the page.

## Using it at an event

Load the event at home or at school on good Wi-Fi. Every team's season history is downloaded once and cached. At the venue, the site and the loaded session open even with no signal. Turn on **Settings > Offline** if the venue Wi-Fi is worse than nothing.

On iPhone, use **Safari > Share > Add to Home Screen**. Safari can clear storage for websites you haven't opened in a while, but not for home-screen apps. Download a session file as a backup before a big event.

## Trying it locally

Browsers won't run the worker from a `file://` page, so serve the folder:

```
cd AutoScout-Web
python3 -m http.server 8000
```

then open http://localhost:8000.

## What was tested

Measured in Chrome (desktop window and iPhone 12-size phone emulation) and in WebKit, the engine Safari uses, at iPhone 12 size, on a Mac:

- The engine starts in about 3 seconds after the first download.
- A 15-team league meet (USTXCLRM1, 2025) loads live from FTCScout in about 10 seconds.
- The 57-team 2025 World Championship Jackson division loads live, with every team's season history, in 87 seconds, and gives the same results as the desktop engine: 96 of 138 matches called correctly by CAP.
- With the network switched off after one visit, the site, the engine and the saved session all open from the browser's copy, and every page works.
- All 14 workspaces open with no script errors. Pages fit a phone screen with no sideways scrolling; wide tables scroll inside their own box.
- Tables sort, the team sheet's edits and data overrides apply, typed match scores apply and clear, the rating basis switches between CAP, OPR and EPA, scouting notes add and delete, team research and search work, and the session reopens after a reload.

Not tested yet: a real iPhone or Android phone (only the engines at phone size), Windows browsers, and Firefox.

## Credits

AutoScout is by LightSaders Robotics, FTC team 12928. The web version wraps the desktop app's own code; the engine and all of the statistics are unchanged.
