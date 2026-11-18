"""Glue between the AutoScout desktop file and the web page.

The web page never re-implements AutoScout's maths or its tables. It imports the
desktop file (AutoScout.py) unchanged, then runs each desktop page's own
`refresh()` against stand-in widgets that simply record what the page wrote:
table rows, labels, chart series. Those recordings are sent to the browser as
JSON and drawn with HTML. So when the desktop file changes, the website changes
with it: drop in a new AutoScout.py and the columns, wording and numbers follow.

Runs inside Pyodide, in a Web Worker. Network calls use synchronous
XMLHttpRequest (allowed in workers), so the engine's blocking FTCScout client
works as written.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
import time
import types
import urllib.error
import urllib.parse

import tkstub

tkstub.install()
if "ssl" not in sys.modules:          # the engine imports ssl but the browser does the HTTPS
    try:
        import ssl  # noqa: F401
    except ImportError:
        sys.modules["ssl"] = tkstub._StubModule("ssl")
if "subprocess" not in sys.modules:
    try:
        import subprocess  # noqa: F401
    except ImportError:
        sys.modules["subprocess"] = tkstub._StubModule("subprocess")

import AutoScout as A                  # noqa: E402  (the desktop file, unchanged)

try:
    import js                          # Pyodide: the worker's global scope
except ImportError:                    # plain Python (tests)
    js = None

HOME = os.path.expanduser("~")
SESSIONS_DIR = os.path.join(A.default_cache_dir(), "web_sessions")

# --------------------------------------------------------------------------- #
# Network: FTCScout over synchronous XHR, with a couple of retries
# --------------------------------------------------------------------------- #
_OPENED: list[str] = []                # URLs the engine asked webbrowser to open


def _xhr_get(url: str, timeout: float) -> str:
    last = None
    for attempt in range(3):
        req = js.XMLHttpRequest.new()
        req.open("GET", url, False)
        req.setRequestHeader("Accept", "application/json")
        try:
            req.send(None)
        except Exception as e:           # network down, DNS, CORS, timeout
            last = urllib.error.URLError(str(e))
            _pause(attempt)
            continue
        status = int(req.status or 0)
        if status == 0:
            last = urllib.error.URLError("no response (offline?)")
            _pause(attempt)
            continue
        body = str(req.responseText or "")
        if status >= 500 and attempt < 2:
            _pause(attempt)
            continue
        if status >= 400:
            raise urllib.error.HTTPError(url, status, str(req.statusText or ""), {},
                                         io.BytesIO(body.encode("utf-8")))
        return body
    raise last or urllib.error.URLError("request failed")


def _pause(attempt: int) -> None:
    """Wait before a retry. (A plain loop: there is no event loop to yield to in a sync call.)"""
    if attempt < 2:
        end = time.time() + 1.5 * (attempt + 1)
        while time.time() < end:
            pass


if js is not None:
    A.FTCScoutClient._http_get = classmethod(lambda cls, url, timeout: _xhr_get(url, timeout))

import webbrowser                      # noqa: E402


def _record_open(url, *a, **k):
    _OPENED.append(str(url))
    return True


webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = _record_open

# --------------------------------------------------------------------------- #
# Stand-in widgets
# --------------------------------------------------------------------------- #


class Rec:
    """Records whatever the desktop page code writes into a widget or variable."""

    def __init__(self, value=None):
        self.value = value
        self.text = ""
        self.rows = None
        self.inserted: list[list] = []
        self.listed: list[str] = []
        self.cfg: dict = {}
        self.headings: dict = {}
        self.columns = None
        self.chart = None
        self.sel = ""
        self.kids: dict = {}

    # tk.StringVar / BooleanVar, and Text.get
    def set(self, v):
        self.value = v

    def get(self, *a):
        if a:
            return self.text
        return "" if self.value is None else self.value

    # SortableTree
    def set_rows(self, rows):
        self.rows = [[iid, list(vals), list(tags)] for iid, vals, tags in rows]

    def show_columns(self, keys):
        self.columns = list(keys)

    def set_heading(self, key, text):
        self.headings[key] = text

    def selection(self):
        return self.sel

    def curselection(self):
        return ()

    # Treeview / Text / Listbox
    def insert(self, *a, **k):
        if "values" in k:
            self.inserted.append(list(k["values"]))
        elif len(a) >= 2 and a[0] == "1.0":
            self.text = str(a[1])
        elif len(a) >= 2 and a[0] == "end":
            self.listed.append(str(a[1]))

    def delete(self, *a):
        if a and a[0] in ("1.0",):
            self.text = ""
        elif a and a[0] == 0:
            self.listed = []
        elif not a or a:
            pass

    def get_children(self, *a):
        return ()

    def exists(self, *a):
        return True

    def configure(self, **k):
        self.cfg.update(k)

    config = configure

    def __getitem__(self, k):
        return self.kids.setdefault(k, Rec())

    def __setitem__(self, k, v):
        self.cfg[k] = v

    # ChartCanvas
    def set_data(self, title, x_label, y_label, series, z_label="", x_ticks=None, note=""):
        self.chart = {"title": title, "x_label": x_label, "y_label": y_label, "z_label": z_label,
                      "series": [_series(s) for s in series],
                      "x_ticks": [[float(v), str(lab)] for v, lab in (x_ticks or [])] or None,
                      "note": note}

    def clear(self, message="No data to chart yet."):
        self.chart = {"title": "", "x_label": "", "y_label": "", "z_label": "", "series": [],
                      "x_ticks": None, "note": message}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _noop

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return True


def _noop(*a, **k):
    return Rec()


def _series(s) -> dict:
    return {"name": s.name, "color": s.color, "kind": s.kind, "labels": bool(s.labels),
            "dashed": bool(s.dashed), "markers": bool(s.markers),
            "points": [{"x": p.x, "y": p.y, "label": p.label, "text": p.text, "z": p.z,
                        "key": list(p.key) if isinstance(p.key, tuple) else p.key, "color": p.color}
                       for p in s.points]}


class FakeApp:
    """What the desktop pages expect of the main window."""

    def __init__(self):
        self.session = A.Session(season=int(A.config.get("season", 2025) or 2025))
        self.status = Rec()
        self.errors: list = []
        self.font_bold = self.font_big = self.font_title = None
        self.title_family = self.body_family = "Roboto"
        self.progress = None            # callback(msg, frac) while loading
        self._client = None

    def make_client(self, offline=None):
        off = bool(A.config.get("offline", False)) if offline is None else bool(offline)
        if self._client is None or self._client.offline != off:
            self._client = A.FTCScoutClient(offline=off)
        return self._client

    def run_async(self, work, done, failed=None):
        try:
            result = work()
        except A.ApiError as e:
            self.errors.append(e.user_message)
            if failed:
                failed(e)
            else:
                raise
            return
        done(result)

    def post(self, fn):
        fn()

    def recompute(self):
        self.session.compute()

    def refresh_all(self):
        pass

    def show_api_error(self, err):
        self.errors.append(getattr(err, "user_message", str(err)))

    def select_team(self, *a, **k):
        pass

    def select_match(self, *a, **k):
        pass

    def do_save(self, *a, **k):
        _save_session()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Rec()


APP = FakeApp()


def probe(cls, **attrs):
    """An instance of a desktop page class whose missing widgets are recorders."""
    def _getattr(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        r = Rec()
        object.__setattr__(self, name, r)
        return r
    P = type("Probe" + cls.__name__, (cls,), {"__getattr__": _getattr})
    obj = object.__new__(P)
    obj.__dict__["app"] = APP
    for k, v in attrs.items():
        obj.__dict__[k] = v
    return obj


def recs(n: int, values=None) -> list:
    return [Rec(None if values is None else values[i]) for i in range(n)]


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #


def _clean(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    return str(x)


def _table(rec: Rec, columns) -> dict:
    cols = [{"key": k, "label": rec.headings.get(k, lab), "align": al} for k, lab, _w, al in columns]
    if rec.columns is not None:
        cols = [c for c in cols if c["key"] in rec.columns]
        keep = [i for i, (k, *_r) in enumerate(columns) if k in rec.columns]
        rows = [[iid, [vals[i] for i in keep if i < len(vals)], tags] for iid, vals, tags in (rec.rows or [])]
    else:
        rows = rec.rows or []
    return {"columns": cols, "rows": rows}


def _simple_table(headers, rows) -> dict:
    return {"columns": [{"key": h, "label": h, "align": "w"} for h in headers],
            "rows": [["", list(r), []] for r in rows]}


# --------------------------------------------------------------------------- #
# Sessions (kept in the browser's storage, in the engine's own JSON format)
# --------------------------------------------------------------------------- #


def _session_path(s) -> str:
    name = f"autoscout_{s.season}_{s.event_code or 'session'}.json"
    return os.path.join(SESSIONS_DIR, name)


def _save_session() -> str:
    s = APP.session
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = s.path if s.path and s.path.startswith(SESSIONS_DIR) else _session_path(s)
    s.save(path)
    A.config.save({"last_session": path, "season": s.season, "event_code": s.event_code,
                   "my_team": s.my_team})
    return path


def _autosave():
    if APP.session.loaded or APP.session.matches:
        _save_session()


def list_sessions() -> list:
    out = []
    if os.path.isdir(SESSIONS_DIR):
        for name in sorted(os.listdir(SESSIONS_DIR)):
            if not name.endswith(".json"):
                continue
            p = os.path.join(SESSIONS_DIR, name)
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                info = d.get("event_info") or {}
                out.append({"file": name, "event": info.get("name") or d.get("event_code", ""),
                            "code": d.get("event_code", ""), "season": d.get("season"),
                            "teams": len(d.get("team_numbers", [])), "my_team": d.get("my_team"),
                            "saved": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
            except (OSError, ValueError):
                continue
    return out


# --------------------------------------------------------------------------- #
# Page views
# --------------------------------------------------------------------------- #


def _head() -> dict:
    s = APP.session
    return {"loaded": s.loaded, "event": s.event_name, "code": s.event_code, "season": s.season,
            "my_team": s.my_team, "basis": s.settings.basis, "matches": len(s.matches),
            "played": sum(1 for m in s.matches if m.played), "teams": len(s.teams),
            "path": os.path.basename(s.path) if s.path else ""}


def view_overview(o):
    p = probe(A.HomeTab, home_metric_vars=recs(4))
    p.refresh()
    return {"metrics": [{"label": lab, "value": v.get()} for lab, v in
                        zip(["TEAMS TRACKED", "MATCHES LOADED", "MY TEAM", "SESSION STATUS"], p.home_metric_vars)]}


def view_competition(o):
    p = probe(A.CompetitionDayTab, metric_vars=recs(5))
    p.refresh()
    s = APP.session
    upcoming = next((m.label for m in s.matches if not m.played), None)
    matches_table = view_matches({})["table"]
    # Keep the full Matches-tab statistics in Schedule Data, not a reduced matchup list.
    # The browser adds an Open action to each row for the existing detailed match dialog.
    return {"head": _head(),
            "line": p.event_line.cfg.get("text", ""),
            "metrics": [{"label": lab, "value": v.get()} for lab, v in
                        zip(("EVENT TEAMS", "MATCHES", "PLAYED", "UP NEXT", "MY TEAM"), p.metric_vars)],
            "matches": matches_table, "upcoming": upcoming}


def view_setup(o):
    p = probe(A.SetupTab)
    p.season_var.set(str(A.config.get("season", 2025)))
    p.event_var.set(A.config.get("event_code", ""))
    p.team_var.set(str(A.config.get("my_team", 0) or ""))
    p.refresh()
    s = APP.session
    return {"season": p.season_var.get(), "event": p.event_var.get(), "team": p.team_var.get(),
            "extra": p.extra_var.get() if s.event_code or s.loaded else "",
            "schedule": s.pasted_schedule,
            "info": p.info_text.text.replace(SESSIONS_DIR + os.sep, "").replace("\nFile: ", "\nSaved in this browser as "),
            "offline": bool(A.config.get("offline", False)), "sessions": list_sessions()}


def _team_league_info(team: int, season: int) -> dict:
    """Return this season's league standings and every event participation for a team."""
    client = APP.make_client(offline=False)

    def rows_from(value, *keys):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in keys:
                candidate = value.get(key)
                if isinstance(candidate, list):
                    return candidate
        return []

    def nested(obj, *keys):
        for key in keys:
            value = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(value, dict):
                return value
        return {}

    leagues_raw, events_raw = [], []
    try:
        leagues_raw = rows_from(client.get_json(
            f"teams/{int(team)}/leagues?season={int(season)}", max_age=3600),
            "leagues", "results", "data")
    except Exception:
        pass
    try:
        events_raw = rows_from(client.get_json(
            f"teams/{int(team)}/events/{int(season)}", max_age=3600),
            "events", "participations", "results", "data")
    except Exception:
        pass

    leagues = []
    league_by_code = {}
    for item in leagues_raw:
        if not isinstance(item, dict):
            continue
        league_obj = nested(item, "league")
        code = str(item.get("leagueCode") or item.get("code") or
                   league_obj.get("leagueCode") or league_obj.get("code") or "").strip()
        region_obj = nested(item, "region") or nested(league_obj, "region")
        region_value = item.get("regionCode") or league_obj.get("regionCode") or ""
        if not region_value and isinstance(item.get("region"), str):
            region_value = item.get("region")
        if not region_value:
            region_value = region_obj.get("code") or region_obj.get("regionCode") or ""
        region = str(region_value).strip()
        name = str(item.get("name") or item.get("leagueName") or
                   league_obj.get("name") or league_obj.get("leagueName") or code or "League").strip()
        entry = {"name": name, "code": code, "region": region, "rank": None,
                 "team_rank": None, "rankings": [], "rankings_error": ""}
        leagues.append(entry)
        if code:
            league_by_code[code.casefold()] = entry
        league_by_code[name.casefold()] = entry

    # FTCScout exposes cumulative league rankings at this route. These are
    # League Meet rankings, not League Tournament rankings.
    for league in leagues:
        if not league["code"] or not league["region"]:
            league["rankings_error"] = "League region or code was not supplied by the API."
            continue
        try:
            ranking_raw = client.get_json(
                f"leagues/{int(season)}/{urllib.parse.quote(league['region'], safe='')}/"
                f"{urllib.parse.quote(league['code'], safe='')}/teams", max_age=1800)
            ranking_rows = rows_from(ranking_raw, "teams", "rankings", "results", "data")
            clean = []
            for row in ranking_rows:
                if not isinstance(row, dict):
                    continue
                team_obj = nested(row, "team")
                stats = nested(row, "stats")
                number = row.get("teamNumber") or row.get("number") or team_obj.get("teamNumber") or team_obj.get("number")
                try:
                    number = int(number)
                except (TypeError, ValueError):
                    number = 0
                rank = row.get("rank") or row.get("ranking") or row.get("position")
                record = {
                    "rank": rank,
                    "number": number,
                    "name": str(row.get("teamName") or row.get("name") or team_obj.get("name") or ""),
                    "wins": row.get("wins", stats.get("wins")),
                    "losses": row.get("losses", stats.get("losses")),
                    "ties": row.get("ties", stats.get("ties")),
                    "qual_average": row.get("qualAverage", stats.get("qualAverage")),
                    "matches_played": row.get("matchesPlayed", stats.get("matchesPlayed"))
                }
                clean.append(record)
            clean.sort(key=lambda row: (int(row["rank"]) if str(row["rank"] or "").isdigit() else 999999,
                                        row["number"]))
            league["rankings"] = clean
            mine = next((row for row in clean if row["number"] == int(team)), None)
            league["team_rank"] = mine
            league["rank"] = mine.get("rank") if mine else None
        except Exception as exc:
            league["rankings_error"] = str(exc)

    events = []
    seen = set()
    for item in events_raw:
        if not isinstance(item, dict):
            continue
        event = nested(item, "event") or item
        code = str(event.get("code") or event.get("eventCode") or
                   item.get("eventCode") or item.get("code") or "").strip()
        if not code or code.casefold() in seen:
            continue
        seen.add(code.casefold())
        league_obj = nested(item, "league") or nested(event, "league")
        league_code = str(item.get("leagueCode") or event.get("leagueCode") or league_obj.get("code") or "").strip()
        league_name = str(item.get("leagueName") or event.get("leagueName") or league_obj.get("name") or
                          (item.get("league") if isinstance(item.get("league"), str) else "") or "").strip()
        league = league_by_code.get(league_code.casefold()) or league_by_code.get(league_name.casefold())
        date = str(event.get("dateStart") or event.get("startDate") or item.get("dateStart") or "")
        date_end = str(event.get("dateEnd") or event.get("endDate") or item.get("dateEnd") or "")
        name = str(event.get("name") or event.get("eventName") or item.get("eventName") or code)
        division = str(event.get("divisionCode") or event.get("division") or item.get("divisionCode") or "").strip()
        city = str(event.get("city") or item.get("city") or "").strip()
        state = str(event.get("stateprov") or event.get("state") or item.get("stateprov") or "").strip()
        country = str(event.get("country") or item.get("country") or "").strip()
        if not date or not division or not league_code:
            try:
                official_event = client.event(int(season), code, max_age=3600)
                if isinstance(official_event, dict):
                    date = date or str(official_event.get("dateStart") or official_event.get("start") or "")
                    date_end = date_end or str(official_event.get("dateEnd") or official_event.get("end") or "")
                    name = str(official_event.get("name") or name)
                    division = division or str(official_event.get("divisionCode") or official_event.get("division") or "").strip()
                    league_code = league_code or str(official_event.get("leagueCode") or "").strip()
                    league_name = league_name or str(official_event.get("leagueName") or "").strip()
                    city = city or str(official_event.get("city") or "").strip()
                    state = state or str(official_event.get("stateprov") or official_event.get("state") or "").strip()
                    country = country or str(official_event.get("country") or "").strip()
                    league = league or league_by_code.get(league_code.casefold()) or league_by_code.get(league_name.casefold())
            except Exception:
                pass
        events.append({
            "name": name, "code": code, "season": int(season), "date": date, "date_end": date_end,
            "league": (league or {}).get("name", league_name),
            "league_code": (league or {}).get("code", league_code),
            "division": division, "city": city, "state": state, "country": country,
            "url": f"https://ftc-events.firstinspires.org/{int(season)}/{code}"
        })
    events.sort(key=lambda row: (row.get("date") or "9999", row["name"].casefold(), row["code"]))
    return {"season": int(season), "leagues": leagues, "events": events,
            "source": "FTCScout REST API", "official_url": "https://ftc-events.firstinspires.org/"}

def _team_phase_chart(team: int, season: int, event_code: str, scope: str) -> dict:
    """Chart actual alliance phase totals for the team's matches when FTCScout supplies them."""
    try:
        client = APP.make_client(offline=False)
        payload = client.team_matches(int(team), int(season))
    except Exception as exc:
        return {"title": "Auto / Teleop / Endgame by match",
                "series": [], "note": f"Match phase data could not be loaded: {exc}"}
    if not isinstance(payload, list):
        payload = []
    rows = []
    current = str(event_code or "").strip().casefold()
    for item in payload:
        if not isinstance(item, dict):
            continue
        match = item.get("match") if isinstance(item.get("match"), dict) else item
        event = item.get("event") if isinstance(item.get("event"), dict) else {}
        code = str(item.get("eventCode") or match.get("eventCode") or event.get("code") or "").strip()
        if scope == "event" and code.casefold() != current:
            continue
        alliance = str(item.get("alliance") or item.get("color") or "").strip().casefold()
        if alliance not in ("red", "blue"):
            for participant in match.get("teams", []) if isinstance(match.get("teams"), list) else []:
                if isinstance(participant, dict) and int(participant.get("teamNumber") or 0) == int(team):
                    alliance = str(participant.get("alliance") or "").strip().casefold()
                    break
        if alliance not in ("red", "blue"):
            continue
        scores = match.get("scores") or item.get("scores") or {}
        side = scores.get(alliance) if isinstance(scores, dict) else None
        if not isinstance(side, dict):
            side = scores if isinstance(scores, dict) else {}
        def phase(*keys):
            for key in keys:
                value = side.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
            return None
        auto = phase("autoPoints", "autonomousPoints")
        teleop = phase("teleopPoints", "dcPoints", "driverControlledPoints")
        endgame = phase("endgamePoints")
        if all(value is None for value in (auto, teleop, endgame)):
            continue
        label = str(match.get("label") or match.get("description") or item.get("matchId") or item.get("matchNumber") or "Match")
        try:
            order = float(match.get("matchNumber") or item.get("matchNumber") or len(rows) + 1)
        except (TypeError, ValueError):
            order = float(len(rows) + 1)
        event_date = str(event.get("dateStart") or event.get("startDate") or item.get("dateStart") or "")
        rows.append((code, label, order, auto, teleop, endgame, event_date))
    rows.sort(key=lambda row: (row[6], row[2], row[1]))
    if not rows:
        return {"title": "Auto / Teleop / Endgame by match", "series": [],
                "note": "FTCScout did not return phase-by-phase scores for these matches. Overall match scores are not substituted for phase data."}
    ticks = []
    for i, row in enumerate(rows, 1):
        ticks.append([float(i), (row[0] + " " if scope == "season" else "") + row[1]])
    series = []
    for name, col, color in (("Auto", 3, "#2f6fdb"), ("Teleop", 4, "#b92e4b"), ("Endgame", 5, "#5d081f")):
        points = [{"x": float(i + 1), "y": row[col], "key": row[1],
                   "text": f"{row[0]} {row[1]}\\n{name}: {row[col]:.1f}"}
                  for i, row in enumerate(rows) if row[col] is not None]
        series.append({"name": name, "color": color, "kind": "line", "points": points})
    return {"title": f"{'Season' if scope == 'season' else 'Event'} match phase scores for team {team}",
            "x_label": "Matches in played order", "y_label": "Alliance points",
            "series": series, "x_ticks": ticks,
            "note": "Alliance phase totals for matches this team played, not individual robot contributions."}


def _sheet_notes() -> dict:
    """The "Note" typed on each team's sheet (the Technical Issues editor), by team."""
    out = {}
    for team, m in (getattr(APP.session, "manual", None) or {}).items():
        text = str(getattr(m, "note", "") or "").strip()
        if text:
            out[str(team)] = text
    return out


def _team_notes_for(team: str) -> list:
    rows = APP.session.team_notes.get(str(team), [])
    return [{"team": str(team), "time": str(n.get("time") or ""), "text": str(n.get("text") or ""),
             "photo": str(n.get("photo") or ""), "index": i} for i, n in enumerate(rows)]


def view_team_notes(o):
    """The team notes for one team, for the team sheet."""
    team = str(o.get("team") or "")
    return {"team": team, "notes": _team_notes_for(team), "sheet_note": _sheet_notes().get(team, "")}


def _note_index() -> dict:
    """Every scouting note in the session, without photos: enough to count notes per team."""
    s = APP.session
    team_notes = [{"team": str(team), "time": str(n.get("time") or ""), "text": str(n.get("text") or "")}
                  for team, rows in s.team_notes.items() for n in rows]
    match_notes = [{"match": str(label), "time": str(n.get("time") or ""), "team": str(n.get("team") or "General"),
                    "phase": str(n.get("phase") or "Overall"), "text": str(n.get("text") or "")}
                   for label, rows in s.live_notes.items() for n in rows]
    return {"team_notes": team_notes, "match_notes": match_notes, "sheet_notes": _sheet_notes()}


def view_pick(o):
    """Team Page > Pick List: the team stats table and, for my team, the engine's
    prediction with every possible partner (the Alliances tab's partner table)."""
    s = APP.session
    teams = probe(A.TeamsTab, _editing=0, _panel_added=False)
    teams.refresh()
    alli = probe(A.AlliancesTab, only_var=Rec(False))
    alli.refresh()
    return {"loaded": bool(s.teams), "event": s.event_name, "my_team": s.my_team, "basis": s.settings.basis,
            "teams": _table(teams.tree, A.TEAM_COLUMNS),
            "partners": _table(alli.partner_tree, A.PARTNER_COLUMNS),
            "partners_status": alli.status_var.get(), "bar": alli.bar_var.get(),
            "notes": _note_index()}


def view_teams(o):
    p = probe(A.TeamsTab, _editing=0, _panel_added=False)
    p.refresh()
    out = {"table": _table(p.tree, A.TEAM_COLUMNS), "notes": _note_index()}
    team = int(o.get("team") or 0)
    if team and team in APP.session.teams:
        p._on_select(str(team))
        out["editor"] = {"team": team, "title": p.sel_var.get(), "tdc": p.tdc_var.get(),
                         "delta": p.delta_var.get(), "focus": p.focus_var.get(), "note": p.note_var.get()}
        out["panel"] = _team_panel(team, o)
        out["league_info"] = _team_league_info(team, APP.session.season)
        out["phase_charts"] = {
            "event": _team_phase_chart(team, APP.session.season, APP.session.event_code, "event"),
            "season": _team_phase_chart(team, APP.session.season, APP.session.event_code, "season")}
    return out


def view_notes(o):
    s = APP.session
    matches = []
    for match in s.matches:
        raw_notes = list(enumerate(s.live_notes.get(match.label, [])))
        raw_notes.sort(key=lambda pair: (str(pair[1].get("time") or ""), pair[0]))
        notes = [{"time": str(note.get("time") or ""),
                  "team": str(note.get("team") or "General"),
                  "phase": str(note.get("phase") or "Overall"),
                  "text": str(note.get("text") or ""),
                  "photo": str(note.get("photo") or ""),
                  "index": index}
                 for index, note in raw_notes]
        score = ""
        if match.red_score is not None and match.blue_score is not None:
            score = f"{match.red_score:g} - {match.blue_score:g}"
        matches.append({
            "label": match.label,
            "red": list(match.red),
            "blue": list(match.blue),
            "played": bool(getattr(match, "played", False)),
            "score": score,
            "notes": notes
        })
    team_notes = []
    for team, rows in sorted(s.team_notes.items(), key=lambda pair: (int(pair[0]) if str(pair[0]).isdigit() else 0)):
        for index, note in enumerate(rows):
            team_notes.append({"team": str(team), "time": str(note.get("time") or ""),
                               "text": str(note.get("text") or ""),
                               "photo": str(note.get("photo") or ""), "index": index})
    team_notes.sort(key=lambda note: (note["time"], note["team"]))
    return {"loaded": bool(s.matches), "event": s.event_name, "season": s.season,
            "matches": matches, "note_count": sum(len(row["notes"]) for row in matches),
            "team_options": [str(n) for n in sorted(set(s.team_numbers) | set(s.extra_teams))],
            "team_notes": team_notes, "sheet_notes": _sheet_notes()}


def _team_panel(team: int, o: dict) -> dict:
    domain = o.get("domain") or A.TeamChartPanel.DOMAINS[0]
    kind = {A.TeamChartPanel.DOMAINS[1]: "matches", A.TeamChartPanel.DOMAINS[2]: "all_matches"}.get(domain, "events")
    x_vars = {"events": A.EVENT_X_VARS, "matches": A.MATCH_X_VARS, "all_matches": A.ALL_MATCH_X_VARS}[kind]
    y_labels = A.EVENT_Y_LABELS if kind == "events" else A.MATCH_Y_LABELS
    defaults = (["OPR (non-penalty)", "CAP (as of that event)", "EPA (after that event)"] if kind == "events"
                else ["EPA before the match", "Pred. points, my alliance", "Actual points, my alliance (np)"])
    xs = [lab for lab, _ in x_vars]
    x = o.get("x") if o.get("x") in xs else xs[0]
    ys = list(o.get("y") or defaults)[:3]
    ys = [y if (y in y_labels or (i and y == A.NONE_LABEL)) else defaults[i] for i, y in enumerate(ys + defaults[len(ys):])]
    p = probe(A.TeamChartPanel, team=team, _editing=0, team_var=None, editor=Rec(),
              domain_var=Rec(domain), x_var=Rec(x), y_vars=recs(3, ys),
              override_vars={k: Rec() for k, _ in A.OVERRIDE_FIELDS},
              current_vars={k: Rec() for k, _ in A.OVERRIDE_FIELDS})
    p.refresh()
    return {"team": team, "head": p.head_var.get(), "chart": p.chart.chart, "explain": p.explain_var.get(),
            "explain_long": A.EXPLAIN_LONG.get(kind, ""), "domains": list(A.TeamChartPanel.DOMAINS),
            "domain": domain, "x_options": xs, "x": x, "y_options": y_labels, "y": ys,
            "none": A.NONE_LABEL,
            "overrides": [{"key": k, "label": lab, "value": p.override_vars[k].get(),
                           "current": p.current_vars[k].get()} for k, lab in A.OVERRIDE_FIELDS]}


def view_match_detail(o):
    s = APP.session
    label = str(o.get("match") or "")
    match = next((m for m in s.matches if m.label == label), None)
    if match is None:
        return {"title": label or "Match details", "text": "This match is not in the loaded schedule.", "teams": []}
    p = probe(A.CompetitionDayTab, metric_vars=recs(5))
    p._show_match(label)
    def side(color, numbers):
        out = []
        for number in numbers:
            team = s.teams.get(number)
            out.append({"number": number, "name": (getattr(team, "name", "") or s.team_names.get(number, "")) if team else s.team_names.get(number, ""),
                        "stats": (f"CAP {team.cap:.1f} · OPR {team.opr:.1f} · EPA {team.epa:.1f}"
                                  if team and getattr(team, "epa", None) is not None else
                                  f"CAP {team.cap:.1f} · OPR {team.opr:.1f}" if team else "")})
        return {"color": color, "label": "Red alliance" if color == "red" else "Blue alliance", "teams": out}
    return {"title": p.detail_title.get() or label, "text": p.detail_text.text,
            "teams": [side("red", match.red), side("blue", match.blue)]}


def view_matches(o):
    p = probe(A.MatchesTab)
    p.refresh()
    return {"summary": p.acc_var.get(), "table": _table(p.tree, A.MATCH_COLUMNS)}


def view_myteam(o):
    keys = ("opr", "auto", "teleop", "endgame", "cap")
    p = probe(A.MyTeamTab, stat_vars={k: Rec() for k in keys})
    p.refresh()
    labels = {"opr": "OPR", "auto": "Auto", "teleop": "Teleop", "endgame": "Endgame", "cap": "CAP"}
    return {"name": p.name_var.get(), "team": p.team_var.get(),
            "stats": [{"label": labels[k], "value": p.stat_vars[k].get()} for k in keys],
            "change": p.change_var.get(), "proj": p.proj_var.get(),
            "table": _table(p.tree, A.MY_MATCH_COLUMNS)}


def view_standings(o):
    p = probe(A.StandingsTab)
    p.refresh()
    return {"head": p.head_var.get(), "table": _table(p.tree, A.STANDING_COLUMNS)}


def view_alliances(o):
    p = probe(A.AlliancesTab, only_var=Rec(bool(o.get("only"))))
    p.refresh()
    return {"head": p.head_var.get(), "bar": p.bar_var.get(), "team": p.team_var.get(),
            "seats": _table(p.seat_tree, A.SEAT_COLUMNS),
            "alliances": _table(p.tree, A.ALLIANCE_COLUMNS),
            "partners_title": p.partner_var.get(), "partners": _table(p.partner_tree, A.PARTNER_COLUMNS),
            "status": p.status_var.get(), "only": bool(o.get("only"))}


def view_winrate(o):
    p = probe(A.AllianceWinRateTab)
    p.refresh()
    return {"heading": p.heading.get(), "summary": p.summary.get(), "chart": p.chart.chart,
            "equation": p.equation.get()}


def view_analysis(o):
    p = probe(A.AnalysisTab)
    p.refresh()
    return {"summary": p.summary.text, "wrong": _table(p.wrong_tree, A.WRONG_COLUMNS),
            "flags": _table(p.flag_tree, A.FLAG_COLUMNS)}


def view_charts(o):
    s = APP.session
    page = o.get("page") or "team"
    out = {"page": page, "loaded": bool(s.teams)}
    if page == "team":
        team = int(o.get("team") or 0) or (s.my_team if s.my_team in s.teams else 0)
        out["panel"] = _team_panel(team, o)
        return out
    if not s.teams:
        out["chart"] = {"series": [], "note": "Load a meet on the Setup tab first."}
        return out
    if page == "all":
        views = ["Scatter", "Compare ratings (where they converge)"]
        view = o.get("view") if o.get("view") in views else views[0]
        labs = A.TEAM_VAR_LABELS
        ax = o.get("x") if o.get("x") in labs else "OPR (non-penalty)"
        ay = o.get("y") if o.get("y") in labs else "CAP"
        az = o.get("z") if o.get("z") in [A.NONE_LABEL] + labs else "EPA"
        ticked = o.get("ratings") or ["opr", "cap", "epa"]
        p = probe(A.ChartsTab, view_var=Rec(view), ax_var=Rec(ax), ay_var=Rec(ay), az_var=Rec(az),
                  label_var=Rec(bool(o.get("labels", True))),
                  cmp_vars={key: Rec(key in ticked) for _lab, key in A.RATING_SERIES},
                  spread_var=Rec(bool(o.get("spread", True))))
        p._refresh_all()
        compare = view.startswith("Compare")
        out.update({"views": views, "view": view, "options": labs, "x": ax, "y": ay, "z": az,
                    "none": A.NONE_LABEL, "labels": bool(o.get("labels", True)),
                    "ratings": [{"key": k, "label": lab, "on": k in ticked} for lab, k in A.RATING_SERIES],
                    "spread": bool(o.get("spread", True)),
                    "chart": p.all_chart.chart, "status": p.all_status.get(),
                    "explain": A.EXPLAIN_SHORT["compare" if compare else "scatter"],
                    "explain_long": A.EXPLAIN_LONG["compare" if compare else "scatter"]})
        return out
    # matches & alliances
    units = list(A.PAIR_UNITS)
    unit = o.get("unit") if o.get("unit") in units else units[0]
    p = probe(A.ChartsTab, unit_var=Rec(unit), px_var=Rec(), py_var=Rec(), pz_var=Rec(),
              plabel_var=Rec(bool(o.get("labels", False))), top_var=Rec(str(o.get("top") or "80")))
    p._unit_changed(refresh=False)               # sets the default axes for this unit
    labs = A._labels(A.PRED_VARS if unit == units[0] else A.ALLIANCE_VARS)
    if o.get("unit") == unit:                    # keep the user's axes when the unit did not change
        if o.get("x") in labs:
            p.px_var.set(o["x"])
        if o.get("y") in labs:
            p.py_var.set(o["y"])
        if o.get("z") in [A.NONE_LABEL] + labs:
            p.pz_var.set(o["z"])
    p._refresh_pairs()
    key = "pred" if unit == units[0] else "alliance" if unit == units[1] else "partners"
    out.update({"units": units, "unit": unit, "options": labs, "x": p.px_var.get(), "y": p.py_var.get(),
                "z": p.pz_var.get(), "none": A.NONE_LABEL, "labels": bool(o.get("labels", False)),
                "top": str(o.get("top") or "80"), "tops": ["20", "40", "80", "150", "300", "all"],
                "chart": p.pair_chart.chart, "status": p.pair_status.get(),
                "explain": A.EXPLAIN_SHORT[key], "explain_long": A.EXPLAIN_LONG.get(key, "")})
    return out


def view_live(o):
    s = APP.session
    labels = [m.label for m in s.matches]
    match = o.get("match") if o.get("match") in labels else (labels[0] if labels else "")
    default_query = " ".join(x for x in (str(s.season), s.event_name, s.event_code, "FTC robotics") if x)
    notes = s.live_notes.get(match, []) if match else []
    return {"matches": labels, "match": match, "teams": ["- alliance / general -"] + [str(n) for n in sorted(s.teams)],
            "phases": ["Overall", "Autonomous", "TeleOp", "Endgame", "Between matches"],
            "notes": notes, "stream": s.live_stream_url, "query": default_query,
            "note_counts": {k: len(v) for k, v in s.live_notes.items() if v},
            "match_teams": next(([list(m.red), list(m.blue)] for m in s.matches if m.label == match), None)}


def view_research(o):
    return {"season": str(max(2022, int(time.strftime("%Y")))), "hint": "Enter a team number (fastest) or part of a team name."}


def view_settings(o):
    quirks = ["stats_from_max_opr_event", "auto_endgame_from_tiebreakers", "proj_opr_sorted_by_value"]
    p = probe(A.SettingsTab, _loading=0, overrides={k: Rec() for k in quirks})
    p.refresh()
    try:
        n_cache = APP.make_client().cache.count()
    except Exception:
        n_cache = None
    return {"rating": p.rating_var.get(), "epa_live": bool(p.epa_live_var.get()),
            "epa_future": bool(p.epa_future_var.get()), "k0": p.k0_var.get(), "kmin": p.kmin_var.get(),
            "epa_info": p.epa_info_var.get(), "faithful": bool(p.faithful_var.get()),
            "quirks": [{"key": k, "label": lab, "value": p.overrides[k].get()} for k, lab in (
                ("stats_from_max_opr_event", "Stats from the team's highest-OPR event"),
                ("auto_endgame_from_tiebreakers", "Auto / Endgame taken from TB1 / TB2"),
                ("proj_opr_sorted_by_value", "Projected OPR fits the history sorted by value"))],
            "tristate": list(A.TRISTATE), "mpt": p.mpt_var.get(), "margin": p.margin_var.get(),
            "poa": bool(p.poa_var.get()), "cache_count": n_cache,
            "offline": bool(A.config.get("offline", False)),
            "about": {"title": f"{A.APP_FULL_TITLE} {A.__version__} - {A.APP_TAGLINE}",
                      "blurb": A.APP_BLURB, "org": A.APP_ORG_FULL, "website": A.APP_WEBSITE}}


VIEWS = {"overview": view_overview, "competition": view_competition, "setup": view_setup,
         "teams": view_teams, "notes": view_notes, "pick": view_pick, "team_notes": view_team_notes, "matches": view_matches, "match_detail": view_match_detail, "myteam": view_myteam,
         "standings": view_standings, "alliances": view_alliances, "winrate": view_winrate,
         "analysis": view_analysis, "charts": view_charts, "live": view_live,
         "research": view_research, "settings": view_settings}


# --------------------------------------------------------------------------- #
# Actions (each mirrors a desktop button)
# --------------------------------------------------------------------------- #


def act_events_search(a):
    """Search FTCScout REST event-search endpoint."""
    query = str(a.get("query", "")).strip()
    season = A.as_int(str(a.get("season", "")), APP.session.season)
    if len(query) < 2:
        return {"ok": False, "message": "Enter at least two characters to search for a meet."}
    try:
        client = APP.make_client(offline=False)
        path = (f"events/search/{int(season)}?searchText="
                f"{urllib.parse.quote(query, safe='')}&limit=30")
        rows = client.get_json(path, max_age=3600) or []
        # FTCScout fuzzy-searches event names; also support exact event-code lookup.
        if not rows and " " not in query:
            try:
                exact = client.get_json(f"events/{int(season)}/{urllib.parse.quote(query.upper(), safe='')}", max_age=3600)
                if isinstance(exact, dict):
                    rows = [exact]
            except A.ApiError:
                pass
        seen = set()
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            location = ", ".join(str(row.get(k)).strip() for k in ("city", "stateprov", "country") if row.get(k))
            dates = " – ".join(str(row.get(k)).strip() for k in ("dateStart", "dateEnd") if row.get(k))
            meta = " · ".join(x for x in (code, location, dates) if x)
            results.append({"name": str(row.get("name") or code), "code": code, "season": int(season), "meta": meta})
        return {"ok": True, "results": results, "message": f"Found {len(results)} matching meet(s)." if results else "No matching meets found for this season."}
    except A.ApiError as e:
        return {"ok": False, "message": getattr(e, "user_message", str(e))}
    except Exception as e:
        return {"ok": False, "message": f"Could not search FTCScout: {e}"}


def act_load(a):
    """Setup → Load event data (the desktop's SetupTab.do_load, run in this worker)."""
    p = probe(A.SetupTab, season_var=Rec(str(a.get("season", ""))), event_var=Rec(a.get("event", "")),
              team_var=Rec(str(a.get("team", ""))), extra_var=Rec(a.get("extra", "")),
              offline_var=Rec(bool(a.get("offline"))), history_var=Rec(bool(a.get("history", True))),
              status_var=Rec(), progress_var=Rec(0.0), load_btn=Rec(), schedule_text=Rec())
    p.schedule_text.text = a.get("schedule", "") or ""
    if not (p.event_var.get() or "").strip():
        return {"ok": False, "message": "Enter an event code first (e.g. FTCCMP1JACK)."}
    APP.errors = []
    A.config.save({"offline": bool(a.get("offline"))})
    old = APP.session
    code = (a.get("event") or "").strip().upper()
    season = A.as_int(str(a.get("season", "")), old.season)
    if old.event_code and (old.event_code != code or old.season != season):
        APP.session = A.Session(season=season, settings=A.Settings.from_dict(old.settings.to_dict()))

    def post(fn):
        fn()
        if APP.progress:
            try:
                APP.progress(str(p.status_var.get()), float(p.progress_var.get() or 0))
            except Exception:
                pass
    APP.post = post
    try:
        APP.session.path = ""
        p.do_load()
    finally:
        APP.post = lambda fn: fn()
    if APP.errors:
        return {"ok": False, "message": APP.errors[-1]}
    _save_session()
    return {"ok": True, "message": p.status_var.get()}


def act_schedule(a):
    p = probe(A.SetupTab, season_var=Rec(str(a.get("season", ""))), event_var=Rec(a.get("event", "")),
              team_var=Rec(str(a.get("team", ""))), extra_var=Rec(a.get("extra", "")),
              status_var=Rec(), schedule_text=Rec())
    p.schedule_text.text = a.get("schedule", "") or ""
    p.apply_schedule()
    _autosave()
    return {"ok": bool(APP.session.matches), "message": p.status_var.get()}


def act_my_team(a):
    n = A.as_int(str(a.get("team", "")), APP.session.my_team)
    APP.session.my_team = n
    A.config.save({"my_team": n})
    _autosave()
    return {"ok": True}


def act_refresh(a):
    s = APP.session
    if not s.event_code:
        return {"ok": False, "message": "This session has no event code to refresh from."}
    client = APP.make_client()
    client.last_error = None
    try:
        played = s.refresh_live_data(client)
    except A.ApiError as e:
        return {"ok": False, "message": e.user_message}
    _autosave()
    if client.offline:
        return {"ok": True, "message": f"Offline: showing cached results ({played} matches played)."}
    if client.last_error:
        return {"ok": True, "message": f"Could not reach FTCScout, so these are the cached results ({played} matches played)."}
    return {"ok": True, "message": f"Refreshed - {played} matches played."}


def act_team_edit(a):
    team = int(a["team"])
    p = probe(A.TeamsTab, _editing=0, tree=Rec(), tdc_var=Rec(a.get("tdc", "")),
              delta_var=Rec(str(a.get("delta", "0"))), focus_var=Rec(a.get("focus", "performance")),
              note_var=Rec(a.get("note", "")))
    p.tree.sel = str(team)
    p.apply_edit()
    _autosave()
    return {"ok": True}


def act_overrides(a):
    team = int(a["team"])
    p = probe(A.TeamChartPanel, team=team, _editing=0, edit_status=Rec(),
              override_vars={k: Rec(str((a.get("values") or {}).get(k, ""))) for k, _ in A.OVERRIDE_FIELDS})
    if a.get("reset"):
        p.reset_overrides()
    else:
        p.apply_overrides()
    _autosave()
    return {"ok": True, "message": p.edit_status.get()}


def act_result(a):
    s = APP.session
    label = a["label"]
    if a.get("clear"):
        s.manual_results.pop(label, None)
        msg = f"Cleared the manual result for {label}."
    else:
        red, blue = A.as_float(str(a.get("red", "")), 0.0), A.as_float(str(a.get("blue", "")), 0.0)
        s.manual_results[label] = (red, blue)
        msg = f"{label}: {red:.0f} – {blue:.0f} entered by hand."
    s.compute()
    _autosave()
    return {"ok": True, "message": msg}


def act_settings(a):
    quirks = ["stats_from_max_opr_event", "auto_endgame_from_tiebreakers", "proj_opr_sorted_by_value"]
    q = a.get("quirks") or {}
    p = probe(A.SettingsTab, _loading=0, faithful_var=Rec(bool(a.get("faithful"))),
              rating_var=Rec(a.get("rating", "CAP")), epa_live_var=Rec(bool(a.get("epa_live", True))),
              epa_future_var=Rec(bool(a.get("epa_future", True))), k0_var=Rec(str(a.get("k0", "0.4"))),
              kmin_var=Rec(str(a.get("kmin", "0.2"))),
              overrides={k: Rec(q.get(k, A.TRISTATE[0])) for k in quirks},
              mpt_var=Rec(str(a.get("mpt", ""))), margin_var=Rec(str(a.get("margin", "10"))),
              poa_var=Rec(bool(a.get("poa"))))
    p.apply()
    _autosave()
    return {"ok": True}


def act_clear_cache(a):
    try:
        APP.make_client().cache.clear()
        return {"ok": True, "message": "Cache cleared."}
    except Exception as e:
        return {"ok": False, "message": f"Could not clear the cache: {e}"}


def act_offline(a):
    A.config.save({"offline": bool(a.get("offline"))})
    return {"ok": True}


def act_note_add(a):
    s = APP.session
    label = a.get("match", "")
    text = (a.get("text") or "").strip()
    if not label:
        return {"ok": False, "message": "Load or paste a match schedule before adding match-specific notes."}
    if not text:
        return {"ok": False, "message": "Enter an observation first."}
    team = a.get("team") or "General"
    if team == "- alliance / general -":
        team = "General"
    import datetime
    photo = str(a.get("photo") or "")
    if photo and (not photo.startswith("data:image/") or len(photo) > 5_500_000):
        return {"ok": False, "message": "Choose an image under 4 MB."}
    note = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "team": team, "phase": a.get("phase") or "Overall", "text": text, "photo": photo}
    s.live_notes.setdefault(label, []).append(note)
    _autosave()
    return {"ok": True, "message": f"Added scouting note for {label}.",
            "note": {k: v for k, v in note.items() if k != "photo"}}



def act_team_note_add(a):
    team = str(a.get("team") or "").strip()
    text = (a.get("text") or "").strip()
    photo = str(a.get("photo") or "")
    if not team.isdigit():
        return {"ok": False, "message": "Choose a team first."}
    if not text and not photo:
        return {"ok": False, "message": "Enter a note or attach a photo first."}
    if photo and (not photo.startswith("data:image/") or len(photo) > 5_500_000):
        return {"ok": False, "message": "Choose an image under 4 MB."}
    import datetime
    note = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "text": text, "photo": photo}
    APP.session.team_notes.setdefault(team, []).append(note)
    _autosave()
    return {"ok": True, "message": f"Saved team note for {team}.",
            "note": {"team": team, "time": note["time"], "text": text}}


def act_team_note_delete(a):
    team = str(a.get("team") or "")
    notes = APP.session.team_notes.get(team, [])
    i = int(a.get("index", -1))
    if 0 <= i < len(notes):
        notes.pop(i)
        if not notes:
            APP.session.team_notes.pop(team, None)
        _autosave()
    return {"ok": True}


def act_note_delete(a):
    notes = APP.session.live_notes.get(a.get("match", ""), [])
    i = int(a.get("index", -1))
    if 0 <= i < len(notes):
        notes.pop(i)
        _autosave()
    return {"ok": True}


def act_stream(a):
    url = (a.get("url") or "").strip()
    if url and not (url.startswith("https://www.youtube.com/") or url.startswith("https://youtube.com/")
                    or url.startswith("https://youtu.be/") or url.startswith("https://m.youtube.com/")):
        return {"ok": False, "message": "Enter a valid YouTube video, live, or youtu.be URL."}
    APP.session.live_stream_url = url
    _autosave()
    return {"ok": True, "message": "Live stream saved to this session." if url else "Stream cleared."}


def act_youtube(a):
    """The desktop's YouTube search buttons; the URL is handed to the browser."""
    p = probe(A.LiveScoutingTab, query_var=Rec(a.get("query") or ""), stream_info=Rec())
    _OPENED.clear()
    (p.search_live if a.get("live") else p.search_all)()
    return {"ok": True, "open": _OPENED[-1] if _OPENED else ""}


def act_research_search(a):
    query = (a.get("query") or "").strip()
    if not query:
        return {"ok": False, "message": "Enter a team number or name first."}
    if query.isdigit():
        return {"ok": True, "results": [{"number": int(query), "text": "Team " + query}], "direct": int(query)}
    try:
        rows = APP.make_client().search_teams(query)
    except A.ApiError as e:
        return {"ok": False, "message": e.user_message}
    rows = rows if isinstance(rows, list) else []
    total = len(rows)
    rows = rows[:30]                     # the desktop asks FTCScout for 30; FTCScout may send thousands
    out = []
    for team in rows:
        number = team.get("teamNumber", team.get("number", "?"))
        name = team.get("name", "Unnamed team")
        location = ", ".join(x for x in (team.get("city"), team.get("state")) if x)
        out.append({"number": number, "text": f"{number}  ·  {name}" + (f"  -  {location}" if location else "")})
    msg = (f"Found {total} matching teams; showing the first {len(out)}. Type more of the name to narrow it."
           if total > len(out) else f"Found {len(out)} matching team(s). Select one to load it.")
    return {"ok": True, "results": out, "message": msg}


def act_research_load(a):
    p = probe(A.TeamResearchTab, _request_id=0, season_var=Rec(str(a.get("season", ""))),
              metric_vars=recs(5), metric_labels=["Events", "Matches", "Win rate", "Avg OPR", "Awards"])
    APP.errors = []
    p.load_team(int(a["team"]))
    if APP.errors:
        return {"ok": False, "message": APP.errors[-1]}
    return {"ok": True, "title": p.profile_title.get(), "subtitle": p.profile_subtitle.get(),
            "metrics": [{"label": lab, "value": v.get()} for lab, v in zip(p.metric_labels, p.metric_vars)],
            "chart": p.chart.chart, "status": p.search_status.get(), "footer": p.profile_footer.get(),
            "events": _simple_table(["Event", "Date", "W-L-T", "OPR", "Auto", "TeleOp", "Endgame"], p.events_tree.inserted),
            "awards": _simple_table(["Award", "Event", "Season"], p.awards_tree.inserted),
            "matches": _simple_table(["Event", "Match", "Alliance", "Score", "Result"], p.matches_tree.inserted)}


def act_save(a):
    s = APP.session
    if not (s.loaded or s.matches or s.event_code):
        return {"ok": False, "message": "Nothing to save yet: load an event first."}
    path = _save_session()
    return {"ok": True, "message": f"Saved in this browser as {os.path.basename(path)}."}


def act_open_saved(a):
    path = os.path.join(SESSIONS_DIR, os.path.basename(a["file"]))
    APP.session = A.Session.open(path)
    A.config.save({"last_session": path, "season": APP.session.season,
                   "event_code": APP.session.event_code, "my_team": APP.session.my_team})
    return {"ok": True, "message": f"Opened {APP.session.event_name}."}


def act_delete_saved(a):
    path = os.path.join(SESSIONS_DIR, os.path.basename(a["file"]))
    try:
        os.remove(path)
    except OSError as e:
        return {"ok": False, "message": str(e)}
    if APP.session.path == path:
        APP.session.path = ""
    return {"ok": True, "message": "Deleted."}


def act_import(a):
    """Open a session .json (from the desktop app or another device)."""
    try:
        d = json.loads(a["text"])
        s = A.Session.from_dict(d)
    except Exception as e:
        return {"ok": False, "message": f"That file is not an AutoScout session ({e})."}
    APP.session = s
    s.path = ""
    _save_session()
    return {"ok": True, "message": f"Opened {s.event_name or 'session'}."}


def act_new(a):
    APP.session = A.Session(season=int(A.config.get("season", 2025) or 2025))
    A.config.save({"last_session": ""})
    return {"ok": True}


def act_export(a):
    s = APP.session
    kind = a.get("kind")
    stem = f"autoscout_{s.event_code or 'meet'}"
    if kind == "session":
        return {"ok": True, "name": f"autoscout_{s.season}_{s.event_code or 'session'}.json",
                "mime": "application/json", "text": json.dumps(s.to_dict(), indent=1)}
    if not s.predictions:
        return {"ok": False, "message": "Load a meet first: there is nothing to export yet."}
    if kind == "csv":
        tmp = os.path.join(HOME, "export.csv")
        A.export_csv(s, tmp)
        with open(tmp, encoding="utf-8") as f:
            text = f.read()
        return {"ok": True, "name": stem + ".csv", "mime": "text/csv", "text": text}
    if kind == "html":
        return {"ok": True, "name": stem + ".html", "mime": "text/html", "text": A.html_report(s)}
    if kind == "text":
        return {"ok": True, "name": stem + ".txt", "mime": "text/plain", "text": A.printable_text(s)}
    return {"ok": False, "message": "Unknown export."}


ACTIONS = {"load": act_load, "events_search": act_events_search, "schedule": act_schedule, "my_team": act_my_team, "refresh": act_refresh,
           "team_edit": act_team_edit, "overrides": act_overrides, "result": act_result,
           "settings": act_settings, "clear_cache": act_clear_cache, "offline": act_offline,
           "note_add": act_note_add, "note_delete": act_note_delete,
           "team_note_add": act_team_note_add, "team_note_delete": act_team_note_delete, "stream": act_stream,
           "youtube": act_youtube, "research_search": act_research_search,
           "research_load": act_research_load, "save": act_save, "open_saved": act_open_saved,
           "delete_saved": act_delete_saved, "import": act_import, "new": act_new, "export": act_export}


# --------------------------------------------------------------------------- #
# Entry points called from worker.js
# --------------------------------------------------------------------------- #


def boot() -> str:
    restored = ""
    last = A.config.get("last_session", "")
    if last and os.path.exists(last):
        try:
            APP.session = A.Session.open(last)
            restored = APP.session.event_name
        except Exception:
            restored = ""
    return json.dumps(_clean({
        "version": A.__version__, "name": A.APP_FULL_TITLE, "tagline": A.APP_TAGLINE, "org": A.APP_ORG_FULL,
        "website": A.APP_WEBSITE, "blurb": A.APP_BLURB, "restored": restored,
        "tdc_issues": list(A.TDC_ISSUES), "focus_modes": list(A.FOCUS_MODES),
        "cap_help": A.CAP_EXPLAINER + "\n" + A.RATING_EXPLAINER, "charts_guide": A.CHARTS_GUIDE,
        "head": _head()}))


def view(page: str, opts_json: str) -> str:
    opts = json.loads(opts_json or "{}")
    data = VIEWS[page](opts)
    data["head"] = _head()
    return json.dumps(_clean(data))


def action(name: str, args_json: str, progress=None) -> str:
    args = json.loads(args_json or "{}")
    APP.progress = progress
    try:
        out = ACTIONS[name](args)
    except A.ApiError as e:
        out = {"ok": False, "message": e.user_message}
    finally:
        APP.progress = None
    out["head"] = _head()
    return json.dumps(_clean(out))
