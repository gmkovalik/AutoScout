"""XPR: Expected Points Rating - a fourth prediction basis for AutoScout (web).

Why another rating
------------------
OPR and CAP are calculated from a team's most recent event. Once the current event has
been played, that *is* the current event, so they "predict" matches they were fitted on.
EPA forecasts honestly (it only knows the matches before each one) but starts every team
from the same place and learns in small steps, so it is slow to separate teams at a
5-6 match FTC event.

XPR combines what each does well:

1. A prior for every team from its EARLIER events this season (never the current one):
   a recency-weighted average of its non-penalty OPR (each event counts twice as much as
   the one before it), with a quarter of the season trend mixed in once it has 3+ events.
   Teams with no earlier event start at the average prior of the teams that have one.

2. This event's match scores, fitted like OPR (alliance score = sum of its teams), but as a
   ridge / Bayesian regression that is pulled towards each team's prior. The prior counts
   as k "pseudo-matches": 2 with one earlier event, 3 with two, 4 with three or more, and
   1 for teams with no history. Early in an event XPR is mostly the prior; after 5-6
   matches the event's own results dominate. That is the standard fix for OPR's main
   weakness at FTC events: too few matches for too many unknowns.

3. Every match is predicted with only the matches played BEFORE it (the fit is updated one
   match at a time), so the accuracy shown on the Analysis tab is a real forecast record,
   not hindsight.

4. Scouting still counts: Technical issues and Delta (Teams) adjust the value used for
   matches not yet played and for the alliance tables - never for matches already played,
   because you didn't know about the problem then.

A team's value is floored at 0 (a robot can't take points away from its alliance).
None of this is statistically proven better yet: it is designed to be, and the Analysis
tab now measures it fairly. Compare it with CAP/OPR/EPA over a few events before trusting it.

This module does not change AutoScout.py: `install(A)` adds XPR to the loaded engine.
"""
from __future__ import annotations

import math

NAME = "XPR"
LABEL = "XPR (prior + this event, forecasts honestly)"
PRIOR_K = {0: 1.0, 1: 2.0, 2: 3.0}        # earlier events -> pseudo-matches (3+ events: 4)
PRIOR_K_MAX = 4.0
PRIOR_K_MANUAL = 6.0                     # an OPR typed in by hand (Teams > data inputs) is trusted more
TREND_SHARE = 0.25                       # share of the season trend once a team has 3+ earlier events

EXPLAINER = """XPR: Expected Points Rating (new in the web version)

XPR estimates how many non-penalty points a team adds to its alliance, like OPR, but it is
built to forecast:

  Prior      from the team's EARLIER events this season only: a recency-weighted average
             of its non-penalty OPR (each event counts double the one before), plus a
             quarter of its season trend once it has 3+ events. No history: the average
             of the other teams' priors.
  This event the match scores so far, fitted like OPR (alliance score = sum of its teams),
             but pulled towards each team's prior. The prior counts as 2-4 matches
             (1 with no history), so XPR starts at the prior and moves to this event's
             results as matches are played.
  Forecasts  each match is predicted only from the matches before it, so "called
             correctly" on the Analysis tab is a fair forecast record for XPR.
  Scouting   Technical issues and Delta adjust matches not yet played and the alliance
             tables (not matches already played).

Alliance prediction = XPR(team 1) + XPR(team 2), as with the other ratings.
XPR is new and not yet proven on real events: compare its record with CAP, OPR and EPA
on the Analysis tab before relying on it.
"""


# --------------------------------------------------------------------------- #
# The calculation
# --------------------------------------------------------------------------- #
def _alliance_scores(m):
    """(red, blue) non-penalty scores when both are known, else totals, else None."""
    if getattr(m, "red_score_np", None) is not None and getattr(m, "blue_score_np", None) is not None:
        return float(m.red_score_np), float(m.blue_score_np)
    if getattr(m, "red_score", None) is not None and getattr(m, "blue_score", None) is not None:
        return float(m.red_score), float(m.blue_score)
    return None


def _priors(A, s, teams):
    """{team: (prior mean, prior weight, earlier events used)} from earlier events only."""
    cur_start = str((s.event_info or {}).get("start") or "")[:10]
    out, known = {}, []
    for n in teams:
        hist = [h for h in (s.history.get(n) or [])
                if (h.opr_np or h.qual_played) and h.event_code != s.event_code
                and not (cur_start and h.start_date and h.start_date >= cur_start)]
        hist.sort(key=lambda h: (h.start_date or "9999", h.event_code))
        mu = k = None
        if hist:
            ws = [2.0 ** i for i in range(len(hist))]
            mu = sum(w * h.opr_np for w, h in zip(ws, hist)) / sum(ws)
            if len(hist) >= 3:
                proj = A.projected_opr([h.opr_np for h in hist])
                if proj is not None:
                    mu = (1 - TREND_SHARE) * mu + TREND_SHARE * proj
            k = PRIOR_K.get(len(hist), PRIOR_K_MAX)
            known.append(mu)
        m = s.manual.get(n)
        if m is not None and getattr(m, "opr", None) is not None:
            mu, k = float(m.opr), PRIOR_K_MANUAL
        out[n] = [mu, k, len(hist)]
    if known:
        fill = sum(known) / len(known)
    else:                                   # nobody has history: half the event's average alliance score
        scores = [x for mt in s.matches if mt.played and _alliance_scores(mt) for x in _alliance_scores(mt)]
        fill = (sum(scores) / len(scores) / 2) if scores else 0.0
    for n, v in out.items():
        if v[0] is None:
            v[0], v[1] = fill, PRIOR_K[0]
    return {n: tuple(v) for n, v in out.items()}


def _scouting(t, x):
    """Technical issues + Delta, the same adjustments CAP's Team average uses."""
    issue = (getattr(t, "tdc_issue", "") or "").strip().upper()
    if issue == "CAMERA":
        x = x - (t.auto or 0.0)
    elif issue in ("INTAKE JAM", "OUT-TAKE"):
        x = min(x, (t.auto or 0.0) + (t.endgame or 0.0))
    elif issue == "BATTERY":
        x = 0.0
    return x + (getattr(t, "tdc_delta", 0.0) or 0.0)


def compute(A, s):
    """Returns (final values {team: xpr}, per-match values [(red tuple, blue tuple)], priors).

    Ridge regression with a per-team prior, updated one played match at a time with the
    Sherman-Morrison formula, so each match is valued with what was known before it."""
    teams = sorted(set(s.teams) | {n for mt in s.matches for n in mt.teams})
    pri = _priors(A, s, teams)
    idx = {n: i for i, n in enumerate(teams)}
    N = len(teams)
    # M = K + sum(a a^T) ;  b = K mu + sum(y a) ;  x = M^-1 b
    Minv = [[0.0] * N for _ in range(N)]
    b = [0.0] * N
    for n, i in idx.items():
        mu, k, _ = pri[n]
        Minv[i][i] = 1.0 / k
        b[i] = k * mu

    def value(i):
        row = Minv[i]
        return sum(row[j] * b[j] for j in range(N))

    def observe(members, y):
        cols = [idx[n] for n in members if n in idx]
        if not cols:
            return
        u = [sum(Minv[r][c] for c in cols) for r in range(N)]       # M^-1 a
        denom = 1.0 + sum(u[c] for c in cols)                        # 1 + a^T M^-1 a
        for r in range(N):
            ur = u[r] / denom
            if ur:
                row = Minv[r]
                for c in range(N):
                    row[c] -= ur * u[c]
        for c in cols:
            b[c] += y

    def adjusted(n, raw, future):
        v = raw
        if future and n in s.teams:
            v = _scouting(s.teams[n], v)
        return max(0.0, v)

    per_match = []
    for mt in s.matches:
        sc = _alliance_scores(mt) if mt.played else None
        future = sc is None
        red = tuple(adjusted(n, value(idx[n]), future) for n in mt.red)
        blue = tuple(adjusted(n, value(idx[n]), future) for n in mt.blue)
        per_match.append((red, blue))
        if sc is not None:
            observe(mt.red, sc[0])
            observe(mt.blue, sc[1])
    final = {n: adjusted(n, value(i), True) for n, i in idx.items()}
    return final, per_match, pri


# --------------------------------------------------------------------------- #
# Plugging it into the engine
# --------------------------------------------------------------------------- #
def install(A):
    """Add XPR as a fourth prediction basis to the loaded AutoScout module."""
    if getattr(A, "_xpr_installed", False):
        return
    A._xpr_installed = True
    if NAME not in A.RATINGS:
        A.RATINGS = tuple(A.RATINGS) + (NAME,)           # Settings.basis reads this at run time

    old_strength = A.TeamStats.strength

    def strength(self):
        if self.basis == NAME:
            v = getattr(self, "xpr", None)
            return v if v is not None else self.cap
        return old_strength.fget(self)
    A.TeamStats.strength = property(strength, doc="Prediction rating under the chosen basis (CAP/OPR/EPA/XPR).")

    old_compute = A.Session.compute

    def compute_with_xpr(self):
        old_compute(self)
        try:
            final, per_match, pri = compute(A, self)
        except Exception as e:                           # never let the new rating break a session
            print("XPR could not be calculated:", e)
            return
        for n, t in self.teams.items():
            t.xpr = final.get(n)
            p = pri.get(n)
            t.xpr_prior = p[0] if p else None
            t.xpr_prior_events = p[2] if p else 0
        self.xpr_values = per_match
        if self.settings.basis == NAME:                  # redo predictions, standings and the strength fit
            self.predictions = A.predict_from_values(self.matches, per_match)
            self.table = A.standings(self.predictions, self.teams, self.settings.matches_per_team)
            self.strength_fit = A.fit_alliance_strength(A.alliance_table(self.teams))
            if self.strength_fit is not None:
                for p in self.predictions:
                    p.red_strength = self.strength_fit.predict(p.red_pts)
                    p.blue_strength = self.strength_fit.predict(p.blue_pts)
    A.Session.compute = compute_with_xpr

    # Charts: All teams (scatter / compare ratings) can plot XPR and its prior.
    def _get(attr):
        return lambda t, c: getattr(t, attr, None)
    extra = [("xpr", "XPR", _get("xpr")),
             ("xpr_prior", "XPR prior (earlier events)", _get("xpr_prior")),
             ("xpr_opr", "XPR − OPR", lambda t, c: None if getattr(t, "xpr", None) is None else t.xpr - t.opr),
             ("xpr_cap", "XPR − CAP", lambda t, c: None if getattr(t, "xpr", None) is None else t.xpr - t.cap)]
    at = next((i + 1 for i, (k, _l, _f) in enumerate(A.TEAM_VARS) if k == "epa"), len(A.TEAM_VARS))
    for off, (k, lab, fn) in enumerate(extra):
        A.TEAM_VARS.insert(at + off, (k, lab, fn))
        A.TEAM_VAR_LABELS.insert(at + off, lab)
        A.TEAM_VAR_BY_LABEL[lab] = (k, fn)
    if not any(k == "xpr" for _l, k in A.RATING_SERIES):
        A.RATING_SERIES.insert(3, ("XPR", "xpr"))
