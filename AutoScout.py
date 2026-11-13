#!/usr/bin/env python3
"""AutoScout 4.1.0 - competition-day workflow and classic AutoScout styling.
Lightsaders Robotics, Copyright 2026
Standard library only. Run it with any Python 3.11+:

    python autoscout.py                       # GUI (if this build has one)
    python autoscout.py predict --fixture DIR
    python autoscout.py load --season 2025 --event USTXCLRM1 --team 12928 --save meet.json
    python autoscout.py predict --open meet.json
    python autoscout.py predict --open meet.json --rating EPA     # CAP | OPR | EPA
"""

from __future__ import annotations

# The package modules referred to one another as objects (`core.Match`, `analysis.analyze`).
# In one file every one of those names is already a global, so the old module names become
# thin views onto this file's namespace. (Resolving lazily, through globals(), keeps this
# working whether the file is run as a script, imported, or frozen by PyInstaller.)
class _SelfModule:
    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name) from None

    def __dir__(self):
        return sorted(globals())


_analysis = config = core = _SelfModule()

# ==========================================================================
# autoscout/__init__.py
# ==========================================================================
"""AutoScout - FTC match prediction, ported from the AutoScout v1.3 spreadsheets."""
__version__ = "4.1.0"   # 2.2.3: per-meet alliance win-rate curve tab; 2.2.2: Alliance Strength (fitted win-rate curve, chart overlay + equation,
                         #        per-match stat); Chance of Winning / alliance win chance now a calibrated Gaussian CDF
                         #        instead of a point share. 2.2.1: readable selection everywhere.
                         #        2.2: charts (Teams side menu, Charts tab) + editable data inputs
APP_NAME = "AutoScout"

# --------------------------------------------------------------------------- #
# Branding - kept in sync with the FTC AutoScout site
# (https://sites.google.com/smca.com/autoscout/home)
# --------------------------------------------------------------------------- #
APP_TAGLINE = "Scouting Made Easy"
APP_TITLE_PREFIX = "FTC"                       # shown upright, before the wordmark
APP_FULL_TITLE = f"{APP_TITLE_PREFIX} {APP_NAME}"   # "FTC AutoScout" - the marketing wordmark
APP_ORG = "LightSaders Robotics"
APP_ORG_FULL = "Lightsaders Robotics 12928"
APP_WEBSITE = "https://sites.google.com/smca.com/autoscout/home"
APP_BLURB = (
    "AutoScout uses machine learning and statistically driven calculations to "
    "predict FTC match outcomes, with accuracy up to 97% when run before a "
    "meet. Its own statistic, Calculated Average Points (CAP), is on average "
    "5-10% more accurate than using OPR alone."
)

FONT_TITLE_CANDIDATES = ("Roboto Black", "Roboto")
FONT_BODY_CANDIDATES = ("Roboto",)

THEME_BG = "#5d081f"       # deep ink brand
THEME_ACCENT = "#5d081f"   # vivid crimson accent
THEME_ACCENT_TEXT = "#dbdce0"  # soft slate
THEME_LIGHT_TEXT = "#ffffff"

UI_PAGE_BG = "#eef1f6"          
UI_APP_BG = "#ffffff"           
UI_CARD_BG = UI_APP_BG
UI_BORDER = "#e3e7ee"
UI_TEXT = "#182236"
UI_MUTED = "#6b7280"

UI_NAVY = THEME_BG            
UI_NAVY_DARK = "#000000"
UI_GOLD = THEME_ACCENT          
UI_GOLD_DEEP = "#5d081f"

UI_ACCENT = UI_NAVY           
UI_ACCENT_HOVER = "#000000"
UI_ACCENT_PRESSED = "#5d081f"
UI_ACCENT_TEXT = "#ffffff"

UI_TAB_BG = UI_PAGE_BG
UI_TAB_FG = UI_MUTED
UI_TAB_ACTIVE_BG = UI_APP_BG
UI_TAB_ACTIVE_FG = UI_NAVY
UI_TAB_HOVER_BG = "#dbdce0"

UI_ROW_ALT_BG = "#f5f7fb"
UI_ROW_SELECTED_BG = "#dbdce0"

# Selection: the same look everywhere text can be selected (table rows, drop-down
# lists, text boxes, entries). Main-colour background, white bold text on top;
# a box that lost focus keeps its selection in the secondary colour with dark text.
UI_SELECT_BG = UI_ACCENT
UI_SELECT_FG = "#ffffff"
UI_SELECT_INACTIVE_BG = UI_ROW_SELECTED_BG
UI_SELECT_INACTIVE_FG = UI_TEXT

UI_NAV_BG = UI_NAVY
UI_NAV_FG = "#ffffff"
UI_NAV_MUTED = THEME_ACCENT_TEXT
UI_NAV_SELECTED_BG = "#2b2d43"
UI_NAV_INDICATOR = UI_GOLD

"""Calculation core: models and the AutoScout statistics.

Every formula here is a port of a spreadsheet cell; the comment on each one names
the sheet and column it came from (see analysis/PIPELINE_MAP.md). Pure Python,
no I/O. The `Settings.faithful` switch reproduces the spreadsheet quirks exactly;
the default is the corrected behaviour decided in STRATEGY.md section 8.
"""

import bisect
import itertools
import math
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Iterable

TDC_ISSUES = ("", "CAMERA", "INTAKE JAM", "OUT-TAKE", "BATTERY")
FOCUS_MODES = ("performance", "award")
#: The number a match prediction (and the alliance tables) are built from.
RATINGS = ("CAP", "OPR", "EPA")

@dataclass
class Settings:
    """Behaviour switches. `faithful=True` = do exactly what the sheets did."""
    faithful: bool = False
    # Individual overrides (None = follow `faithful`). Exposed for experiments.
    stats_from_max_opr_event: Optional[bool] = None   # sheets: VLOOKUP hit the max-NP-OPR event
    auto_endgame_from_tiebreakers: Optional[bool] = None  # sheets: AUTO:=tb1, ENDGAME:=tb2
    proj_opr_sorted_by_value: Optional[bool] = None   # sheets: OPR history sorted asc by value
    matches_per_team: Optional[int] = None            # sheets: hard-coded 6; None = count schedule
    margin_points: float = 10.0                       # Sheet18: trust the call only above this margin
    show_potential_of_accuracy: bool = False          # EasyReadInterface!K (precedence bug; experimental)
    # --- what the predictions are based on (new in 2.1) ---------------------------------
    rating: str = "CAP"        # "CAP" | "OPR" | "EPA": the per-team number that gets summed per alliance
    epa_live: bool = True      # EPA also learns from this event's played matches, match by match
    # --- EPA, per https://statcube.vercel.app/methodology ------------------------------
    epa_kmax_frac: float = 0.4   # k_max = this x |mu|   (page: 0.4)
    epa_kmin_frac: float = 0.2   # k_min = this x |mu|   (page: 0.2)
    epa_a: float = 2.1482        # Taylor's-law constants (page: DECODE fit) ...
    epa_b: float = 0.6543
    epa_mean: Optional[float] = None  # season mean alliance score S-bar. NOT on the page; None = learn from matches seen
    # The page does not pin these down. Defaults are the most plausible reading; epa_calibrate.py
    # can work out which one the site really uses from a few EPA values copied off it.
    epa_sigma: str = "std"       # "std": sigma = a*M**b   |  "var": sigma = sqrt(a*M**b)  (page writes V = a*M**b)
    epa_n_after: bool = False    # k-factor's n: False = matches played BEFORE this one, True = including it
    epa_score: str = "np"        # "np" non-penalty points  |  "total" points including penalties
    epa_mu_scale: float = 1.0    # mu in k_max/k_min = this x S-bar   (1.0 = S-bar, 0.5 = mean team score)
    epa_sigma_m_scale: float = 1.0  # M in sigma = a*M**b is this x the alliance's predicted score (0.5 = per-team scale)
    epa_future: bool = True      # also learn from events these teams played AFTER this one (the site's EPA is season-long).
                                 # Per-match predictions never see them; only the displayed final EPA does.

    @property
    def basis(self) -> str:
        """`rating`, normalised: anything unrecognised means CAP."""
        r = str(self.rating or "CAP").strip().upper()
        return r if r in RATINGS else "CAP"

    def _flag(self, v: Optional[bool]) -> bool:
        return self.faithful if v is None else v

    @property
    def use_max_opr_event(self) -> bool:
        return self._flag(self.stats_from_max_opr_event)

    @property
    def use_tiebreakers(self) -> bool:
        return self._flag(self.auto_endgame_from_tiebreakers)

    @property
    def sort_history_by_value(self) -> bool:
        return self._flag(self.proj_opr_sorted_by_value)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

@dataclass
class EventStat:
    team: int
    event_code: str
    event_name: str = ""
    start_date: str = ""          # ISO yyyy-mm-dd, "" if unknown
    opr_np: float = 0.0           # opr.totalPointsNp
    opr_total: float = 0.0        # opr.totalPoints
    auto: float = 0.0             # opr.autoPoints
    dc: float = 0.0               # opr.dcPoints (driver-controlled, may include endgame)
    endgame: float = 0.0          # season-specific endgame component of opr
    tb1: float = 0.0
    tb2: float = 0.0
    rank: int = 0
    rp: float = 0.0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    qual_played: int = 0

    @property
    def teleop(self) -> float:
        """Teleop excluding endgame; robust across seasons: np − auto − endgame."""
        return self.opr_np - self.auto - self.endgame

@dataclass
class TeamStats:
    number: int
    name: str = ""
    opr: float = 0.0          # STAT PAGE!F  (non-penalty OPR)
    auto: float = 0.0         # STAT PAGE!B  ("TBP 1" in AutoSheet)
    teleop: float = 0.0       # STAT PAGE!C
    endgame: float = 0.0      # STAT PAGE!D  ("TBP 2" in AutoSheet)
    proj_opr: Optional[float] = None   # PROJ. OPR!row 44 (None = "Insufficient Data")
    tdc_issue: str = ""       # Technical Issues
    tdc_delta: float = 0.0    # Technical Issues  manual "change in stats"
    focus: str = "performance"  # AutoSheet!O
    opr_history: list[float] = field(default_factory=list)   # chronological event OPRs
    source_event: str = ""    # which event the stats came from
    events_played: int = 0
    rank: int = 0             # rank at source event
    record: str = ""          # "W-L-T" at source event
    has_data: bool = True     # False when FTCScout had nothing for the team
    epa: Optional[float] = None   # Expected Points Added (AutoScout's own online rating)
    epa_matches: int = 0          # matches EPA has learned from for this team
    basis: str = "CAP"            # which rating `strength` reports (Settings.basis)
    edited: bool = False          # one or more inputs overridden by hand (Teams side menu)

    @property
    def avg(self) -> float:            # MAIN DATA!E = AUTO + TELEOP + ENDGAME
        return self.auto + self.teleop + self.endgame

    @property
    def new_avg(self) -> float:        # MAIN DATA!G = (AVG + OPR) / 2
        return (self.avg + self.opr) / 2

    @property
    def tdc_avg(self) -> float:        # Technical Issues
        return tdc_adjusted_avg(self.new_avg, self.auto, self.endgame, self.tdc_issue)

    @property
    def ftc_avg(self) -> float:        # Technical Issues = L + N   ("Team average" in AutoSheet)
        return self.tdc_avg + self.tdc_delta

    @property
    def rsp(self) -> float:            # AutoSheet!N = (OPR − TBP2) · 0.001
        return (self.opr - self.endgame) * 0.001

    @property
    def eopr(self) -> float:           # AutoSheet!K
        if self.proj_opr is None:
            return self.ftc_avg
        return self.proj_opr + self.rsp * 10

    @property
    def rs_adjust(self) -> float:      # AutoSheet!P  "RS − / +"
        if self.focus == "award" and self.endgame:
            return -((self.auto / self.endgame) / 2) / 10
        return 0.0

    @property
    def cap(self) -> float:            # AutoSheet!L  Calculated Average Points
        return cap(self.opr, self.auto, self.endgame, self.ftc_avg, self.eopr, self.rs_adjust)

    @property
    def cap_minus_opr(self) -> float:  # AutoSheet!V-ish: what the UI shows as "OPR → CAP" change
        return self.cap - self.opr

    @property
    def cap_change_pct(self) -> Optional[float]:   # AutoScout '1'!R9
        return (self.cap - self.opr) / self.opr if self.opr else None

    @property
    def alter_cap(self) -> float:      # AutoSheet!S = CAP + (FTC AVG − OPR)
        return self.cap + (self.ftc_avg - self.opr)

    @property
    def strength(self) -> float:
        """What one team is worth to an alliance under the chosen prediction basis."""
        if self.basis == "OPR":
            return self.opr
        if self.basis == "EPA" and self.epa is not None:
            return self.epa
        return self.cap

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("avg", "new_avg", "ftc_avg", "eopr", "cap", "strength"):
            d[k] = getattr(self, k)
        return d


def tdc_adjusted_avg(new_avg: float, tbp1: float, tbp2: float, issue: str) -> float:
    """Technical Issues - Technical Difficulty Calculator adjustment.

    CAMERA            → AVG − TBP1          (auto is lost)
    INTAKE JAM/OUT-TAKE → TBP1 + TBP2       (teleop scoring is lost)
    BATTERY           → 0
    """
    issue = (issue or "").strip().upper()
    if issue == "CAMERA":
        return new_avg - tbp1
    if issue in ("INTAKE JAM", "OUT-TAKE"):
        return new_avg - (new_avg - (tbp1 + tbp2))
    if issue == "BATTERY":
        return 0.0
    return new_avg


def cap(opr: float, tbp1: float, tbp2: float, ftc_avg: float, eopr: float, rs_adjust: float = 0.0) -> float:
    """AutoSheet!L, transcribed literally:

    (((((((((G+H)+I)+G)+G)*(N*0.5)) + ((H+I)+M)) / 3) * 1.5 + K + G) / 3 + M) / 2 + P*10
    G=OPR H=TBP1 I=TBP2 M=FTC AVG N=RSP=(G−I)·0.001 K=EOPR P=RS-/+
    """
    rsp = (opr - tbp2) * 0.001
    t1 = (3 * opr + tbp1 + tbp2) * (rsp * 0.5)
    t2 = t1 + (tbp1 + tbp2 + ftc_avg)
    t3 = (t2 / 3) * 1.5
    t4 = t3 + eopr + opr
    t5 = t4 / 3 + ftc_avg
    return t5 / 2 + rs_adjust * 10

def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


def projected_opr(history: Iterable[float], sorted_by_value: bool = False) -> Optional[float]:
    """PROJ. OPR!row 44.  x = 1..n event index, y = OPR at that event.
    Returns None when fewer than 2 usable points ("N/A" / "Insufficient Data").

    sorted_by_value=True reproduces the spreadsheet quirk of feeding the OPRs sorted
    ascending by value (AutoServer TeamData!M1) instead of chronologically.
    """
    ys = [float(y) for y in history if isinstance(y, (int, float)) and y >= 0.00001]
    if sorted_by_value:
        ys = sorted(ys)
    if len(ys) < 2:
        return None
    xs = [float(i) for i in range(1, len(ys) + 1)]
    slope, intercept = linear_fit(xs, ys)
    return max(0.0, slope * (max(xs) + 1) + intercept)


def accuracy_bars(n_points: int) -> str:
    """PROJ. OPR!J 'Accuracy Bar' (custom BARS() script, not exported): more data → more bars."""
    n = max(0, min(int(n_points), 5))
    return "▮" * n + "▯" * (5 - n)

def team_stats_from_history(number: int, history: list[EventStat], settings: Settings,
                            name: str = "", current_event: str = "") -> TeamStats:
    """Collapse a team's per-event stats into the single row CAP consumes.

    Sheets: the row is the one with the highest NP-OPR (AutoServer.TeamData sorted desc + VLOOKUP).
    Corrected: the most recent event (by start date, then by list order).
    """
    hist = [h for h in history if h.opr_np or h.qual_played]
    chrono = sorted(hist, key=lambda h: (h.start_date or "9999", ))
    t = TeamStats(number=number, name=name)
    if not chrono:
        t.has_data = False
        return t
    src = max(chrono, key=lambda h: h.opr_np) if settings.use_max_opr_event else chrono[-1]
    t.opr = src.opr_np
    if settings.use_tiebreakers:
        t.auto, t.endgame, t.teleop = src.tb1, src.tb2, src.dc
    else:
        t.auto, t.endgame, t.teleop = src.auto, src.endgame, src.teleop
    t.source_event = src.event_code
    t.events_played = len(chrono)
    t.rank = src.rank
    t.record = f"{src.wins}-{src.losses}-{src.ties}"
    t.opr_history = [h.opr_np for h in chrono]
    t.proj_opr = projected_opr(t.opr_history, settings.sort_history_by_value)
    return t

@dataclass
class Match:
    label: str                       # "Q-1"
    red: tuple[int, ...]
    blue: tuple[int, ...]
    number: int = 0
    level: str = "Quals"             # "Quals" | "DoubleElim" | ...
    surrogates: tuple[int, ...] = () # play (and score) but the match doesn't count for their ranking
    scheduled: str = ""              # ISO time if known
    played: bool = False
    red_score: Optional[float] = None       # total points
    blue_score: Optional[float] = None
    red_score_np: Optional[float] = None    # non-penalty
    blue_score_np: Optional[float] = None

    @property
    def teams(self) -> tuple[int, ...]:
        return self.red + self.blue

    @property
    def winner(self) -> Optional[str]:     # TED!B "POA" once known
        if self.red_score is None or self.blue_score is None:
            return None
        if self.red_score > self.blue_score:
            return "RED"
        if self.blue_score > self.red_score:
            return "BLUE"
        return "TIE"

    @property
    def np_winner(self) -> Optional[str]:  # Sheet18!L "NP Winner"
        if self.red_score_np is None or self.blue_score_np is None:
            return None
        return "RED" if self.red_score_np > self.blue_score_np else "BLUE" if self.blue_score_np > self.red_score_np else "TIE"

    def color_of(self, team: int) -> Optional[str]:
        if team in self.red:
            return "RED"
        if team in self.blue:
            return "BLUE"
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Match":
        d = dict(d)
        d["red"] = tuple(d.get("red", ()))
        d["blue"] = tuple(d.get("blue", ()))
        d["surrogates"] = tuple(d.get("surrogates", ()))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Prediction:
    match: Match
    red_pts: float
    blue_pts: float
    red_detail: tuple[float, ...] = ()
    blue_detail: tuple[float, ...] = ()
    red_strength: float = 0.0    # Alliance Strength (%) - this alliance's predicted points run
    blue_strength: float = 0.0   # through the loaded event's StrengthFit (set by Session.compute)

    @property
    def total(self) -> float:            # TED!W
        return self.red_pts + self.blue_pts

    @property
    def red_cow(self) -> float:
        """TED!F "Chance Of Winning". Was a raw point share (red_pts / total); that treats a
        150-90 alliance's 60-point edge the same as a 20-10 alliance's 10-point edge, and
        ignores that either alliance's actual score is noisy around its prediction. Now a
        calibrated Phi(margin / combined sigma), matching how EpaModel.observe scores its own
        margin signal: sigma(M) = a*M**b (Taylor's law - the same scoring spread the EPA model
        already fits), so a big alliance's points need to differ by more before it counts as
        confident, and a small alliance's smaller absolute spread is still weighed correctly."""
        spread = math.hypot(_cow_sigma(self.red_pts), _cow_sigma(self.blue_pts))
        if not spread:
            return 0.5
        return _phi(self.margin / spread)

    @property
    def blue_cow(self) -> float:         # TED!G
        return 1 - self.red_cow

    def cow_for(self, color: str) -> float:
        return self.red_cow if color == "RED" else self.blue_cow

    @property
    def winner(self) -> str:             # TED!C
        if self.red_pts < self.blue_pts:
            return "BLUE"
        if self.red_pts == self.blue_pts:
            return "TIE"
        return "RED"

    @property
    def margin(self) -> float:
        return self.red_pts - self.blue_pts

    @property
    def correct(self) -> Optional[bool]:  # AutoScout '1'!I
        w = self.match.winner
        if w in ("RED", "BLUE"):
            return self.winner == w
        return None

    def confident(self, points: float = 10.0) -> bool:
        """True when the predicted margin exceeds `points` (Settings.margin_points)."""
        return abs(self.margin) > points

    def margin_call(self, points: float = 10.0) -> str:
        """AutoScout.Sheet18!P: flip the call when the margin is within `points` (literal port)."""
        d = self.margin
        if d > 0:
            return "RED" if d > points else "BLUE"
        return "BLUE" if -d > points else "RED"

    @property
    def potential_of_accuracy(self) -> float:
        """EasyReadInterface!K, literal precedence as in the sheet (experimental)."""
        m, n = self.red_pts, self.blue_pts
        if m > n:
            return (m + n / m - n) * 0.01 if m else 0.0
        return (n + m / n - m) * 0.01 if n else 0.0


def match_rating(cow: float) -> int:
    """AutoScout '1'!AA - MR: 3 if >65%, 2 if >45%, else 1."""
    if cow > 0.65:
        return 3
    if cow > 0.45:
        return 2
    return 1


# --------------------------------------------------------------------------- #
# EPA - Expected Points Added. AutoScout's own online rating (FTCScout publishes none).
# --------------------------------------------------------------------------- #
@dataclass
class EpaEvent:
    """One finished event's played qualification matches. Only used to warm EPA up."""
    code: str
    start: str = ""                                   # ISO yyyy-mm-dd, "" if unknown
    matches: list = field(default_factory=list)       # list[Match]

    def to_dict(self) -> dict:
        return {"code": self.code, "start": self.start, "matches": [m.to_dict() for m in self.matches]}

    @classmethod
    def from_dict(cls, d: dict) -> "EpaEvent":
        return cls(code=d.get("code", ""), start=d.get("start", ""),
                   matches=[Match.from_dict(m) for m in d.get("matches", [])])


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _cow_sigma(m: float, a: float = 2.1482, b: float = 0.6543) -> float:
    """Taylor's-law scoring spread for one alliance's predicted points (statcube's a*M**b,
    "std" mode - see EpaModel.sigma) - used to turn a predicted margin into a calibrated win
    probability for *any* prediction basis (CAP/OPR/EPA), not only the EPA model itself."""
    return a * max(m, 1e-6) ** b


class EpaModel:
    """EPA as described on https://statcube.vercel.app/methodology.

    Alliance strength is the sum of its two teams' EPA. After each played match, with the
    ratings the teams had *before* it:

        sigma(M)   = a * M**b                       (M = predicted alliance score; see epa_sigma)
        z_margin   = ((S_red - S_blue) - (Sh_red - Sh_blue)) / sqrt(sigma_r**2 + sigma_b**2)
        k_margin   = Phi(z_margin) - 0.5            (sign flipped for blue)
        k_points   = Phi((S - Sh) / sigma) - 0.5    (own alliance, red and blue separately)
        EPA_new    = EPA_old + alpha(n) * (k_margin + k_points)

    alpha(n) is the team's k-factor:
        n <= 6 : k_max        7 <= n <= 12 : k_max - (n-6)*(k_max-k_min)/6        n > 12 : k_min
        k_max = 0.4*|mu|, k_min = 0.2*|mu|.

    A team with no EPA yet is valued at S-bar / 2. There is no floor at zero.
    The page does not state S-bar, which score type, what n counts, or how sigma is formed from
    V = a*M**b; those are the `epa_*` settings (see Settings) rather than hidden constants.
    """
    def __init__(self, k0: float = 0.4, kmin: float = 0.2, a: float = 2.1482, b: float = 0.6543,
                 mean: Optional[float] = None, sigma: str = "std", n_after: bool = False,
                 score: str = "np", mu_scale: float = 1.0, sigma_m_scale: float = 1.0):
        self.k0 = float(k0)            # k_max / |mu|
        self.kmin = float(kmin)        # k_min / |mu|
        self.a = float(a)
        self.b = float(b)
        self.fixed_mean = None if mean is None else float(mean)
        self.sigma_mode = "var" if str(sigma).lower() == "var" else "std"
        self.n_after = bool(n_after)
        self.score = "total" if str(score).lower() == "total" else "np"
        self.mu_scale = float(mu_scale)
        self.sigma_m_scale = float(sigma_m_scale)
        self.state: dict[int, list] = {}      # team -> [epa, matches learned from]
        self._sum = 0.0                       # running alliance-score total (used only if no fixed mean)
        self._n = 0

    # ---- season mean S-bar and what hangs off it ---------------------------------
    @property
    def mean(self) -> float:
        if self.fixed_mean is not None:
            return self.fixed_mean
        return self._sum / self._n if self._n else 0.0

    @property
    def prior(self) -> float:
        """EPA of a team with no record: S-bar / 2."""
        return self.mean / 2.0

    def sigma(self, m: float) -> float:
        v = self.a * max(m * self.sigma_m_scale, 1e-6) ** self.b
        return math.sqrt(v) if self.sigma_mode == "var" else v

    def k_factor(self, n: int, mu: float) -> float:
        mu = abs(mu) * self.mu_scale
        kmax, kmin = self.k0 * mu, self.kmin * mu
        if n <= 6:
            return kmax
        if n <= 12:
            return kmax - (n - 6) * (kmax - kmin) / 6.0
        return kmin

    def value(self, team: int) -> float:
        st = self.state.get(team)
        return st[0] if st else self.prior

    def games(self, team: int) -> int:
        st = self.state.get(team)
        return int(st[1]) if st else 0

    def observe(self, m: Match) -> None:
        """Learn from one played match."""
        r_raw, b_raw = ((m.red_score, m.blue_score) if self.score == "total"
                        else (m.red_score_np, m.blue_score_np))
        if r_raw is None or b_raw is None or not m.red or not m.blue:
            return
        s_r, s_b = float(r_raw), float(b_raw)
        seeded = False
        if self.fixed_mean is None and not self._n:
            # nothing seen yet and no fixed season mean: let this very first match seed S-bar
            self._sum, self._n, seeded = s_r + s_b, 2, True
        mu = self.mean
        prior = self.prior
        h_r = sum(self.value(t) for t in m.red)
        h_b = sum(self.value(t) for t in m.blue)
        sg_r, sg_b = self.sigma(h_r), self.sigma(h_b)
        k_margin = _phi(((s_r - s_b) - (h_r - h_b)) / math.hypot(sg_r, sg_b)) - 0.5
        k_red = _phi((s_r - h_r) / sg_r) - 0.5
        k_blue = _phi((s_b - h_b) / sg_b) - 0.5
        for tl, delta in ((m.red, k_margin + k_red), (m.blue, -k_margin + k_blue)):
            for t in tl:
                st = self.state.setdefault(t, [prior, 0])
                st[0] += self.k_factor(st[1] + (1 if self.n_after else 0), mu) * delta
                st[1] += 1
        if not seeded:
            self._sum += s_r + s_b
            self._n += 2


def new_epa_model(st: Optional[Settings] = None) -> EpaModel:
    st = st or Settings()
    return EpaModel(st.epa_kmax_frac, st.epa_kmin_frac, st.epa_a, st.epa_b, st.epa_mean,
                    st.epa_sigma, st.epa_n_after, st.epa_score, st.epa_mu_scale, st.epa_sigma_m_scale)


def build_epa(prior_events: Iterable[EpaEvent], matches: Iterable[Match],
              settings: Optional[Settings] = None,
              later_events: Iterable[EpaEvent] = ()) -> tuple[list[tuple[tuple, tuple]], EpaModel]:
    """Replay earlier events (oldest first), then walk this event's schedule.

    Returns one (red values, blue values) pair per match plus the finished model. Each
    match is valued with what EPA knew *before* it: if `settings.epa_live`, the played
    matches ahead of it have been learned from; otherwise EPA stays frozen at the value
    it had when the event started. Nothing a match itself reveals leaks into its own call.

    `later_events` (events these teams played after this one) are learned only once every
    per-match value has been recorded, so they change the finished model (the season-long
    EPA the site shows) but never a prediction.
    """
    st = settings or Settings()
    model = new_epa_model(st)
    for ev in sorted(prior_events, key=lambda e: (e.start or "9999", e.code)):
        for m in ev.matches:
            model.observe(m)
    values = []
    for m in matches:
        values.append((tuple(model.value(t) for t in m.red), tuple(model.value(t) for t in m.blue)))
        if st.epa_live and m.played:
            model.observe(m)
    for ev in sorted(later_events, key=lambda e: (e.start or "9999", e.code)):
        for m in ev.matches:
            model.observe(m)
    return values, model


def predict_from_values(matches: Iterable[Match], values: list[tuple[tuple, tuple]]) -> list[Prediction]:
    """Predictions from per-match, per-team values (what EPA-based calls use)."""
    return [Prediction(m, sum(r), sum(b), tuple(r), tuple(b)) for m, (r, b) in zip(matches, values)]


def build_epa_history(prior_events: Iterable[EpaEvent], matches: Iterable[Match], current_code: str = "",
                      settings: Optional[Settings] = None) -> list[tuple[str, Match, tuple, tuple]]:
    """Same walk as `build_epa`, but keeps a row for *every* match along the way - earlier
    events' matches as well as this event's - instead of only returning values for `matches`.

    Returns (event_code, match, red_values, blue_values) oldest first, each match valued with
    what EPA knew before it was played. This is what powers the "all matches this season" chart:
    it lets a team's whole season be walked match by match instead of resetting at each event.
    """
    st = settings or Settings()
    model = new_epa_model(st)
    out = []
    for ev in sorted(prior_events, key=lambda e: (e.start or "9999", e.code)):
        for m in ev.matches:
            out.append((ev.code, m, tuple(model.value(t) for t in m.red), tuple(model.value(t) for t in m.blue)))
            model.observe(m)
    for m in matches:
        out.append((current_code, m, tuple(model.value(t) for t in m.red), tuple(model.value(t) for t in m.blue)))
        if st.epa_live and m.played:
            model.observe(m)
    return out


def predict(matches: Iterable[Match], teams: dict[int, TeamStats],
            strength: Callable[[TeamStats], float] = lambda t: t.strength) -> list[Prediction]:
    """TED!D/E: alliance points = sum of each member's rating (0 for unknown teams).

    The rating is `TeamStats.strength`: CAP by default, or OPR when Settings.rating says so.
    (EPA changes during an event, so it goes through `build_epa` / `predict_from_values`.)"""
    def s(n: int) -> float:
        t = teams.get(n)
        return strength(t) if t is not None else 0.0
    out = []
    for m in matches:
        rd = tuple(s(n) for n in m.red)
        bd = tuple(s(n) for n in m.blue)
        out.append(Prediction(m, sum(rd), sum(bd), rd, bd))
    return out

@dataclass
class Standing:
    number: int
    name: str
    wins: int
    losses: int
    played: int                 # matches with a known or predicted result
    scheduled: int              # matches on the schedule
    tbp1: float
    tbp2: float
    rp: float = 0.0             # TED!I = 2·wins / matches_per_team
    rank: int = 0
    captain_rank: int = 0       # Sheet16 "Cpt. Rank" (top 6)


def standings(preds: Iterable[Prediction], teams: dict[int, TeamStats],
              matches_per_team: Optional[int] = None) -> list[Standing]:
    """Wins come from the actual result when known, else the predicted winner (TED!M,N).
    Ranking: RP desc, then TBP1 (AUTO), then TBP2 (ENDGAME)  (TED!Q / Sheet16)."""
    wins: dict[int, int] = {}
    losses: dict[int, int] = {}
    played: dict[int, int] = {}
    scheduled: dict[int, int] = {}
    for p in preds:
        m = p.match
        counted = [n for n in m.teams if n not in m.surrogates]
        for n in counted:
            scheduled[n] = scheduled.get(n, 0) + 1
        w = m.winner or p.winner
        if w == "TIE":
            for n in counted:
                played[n] = played.get(n, 0) + 1
            continue
        winners, losers = (m.red, m.blue) if w == "RED" else (m.blue, m.red)
        for n in winners:
            if n in m.surrogates:
                continue
            wins[n] = wins.get(n, 0) + 1
            played[n] = played.get(n, 0) + 1
        for n in losers:
            if n in m.surrogates:
                continue
            losses[n] = losses.get(n, 0) + 1
            played[n] = played.get(n, 0) + 1
    rows = []
    for n in set(teams) | set(scheduled):
        t = teams.get(n)
        denom = matches_per_team or scheduled.get(n, 0)
        rows.append(Standing(
            number=n, name=t.name if t else "", wins=wins.get(n, 0), losses=losses.get(n, 0),
            played=played.get(n, 0), scheduled=scheduled.get(n, 0),
            tbp1=t.auto if t else 0.0, tbp2=t.endgame if t else 0.0,
            rp=(2 * wins.get(n, 0) / denom) if denom else 0.0))
    rows.sort(key=lambda r: (r.rp, r.tbp1, r.tbp2), reverse=True)
    for i, r in enumerate(rows, 1):
        r.rank = i
        r.captain_rank = i if i <= 6 else 0
    return rows


def accuracy(preds: Iterable[Prediction]) -> tuple[int, int]:
    """AutoScout '1'!A7 - (correct, decided) over matches with a known non-tie result."""
    correct = decided = 0
    for p in preds:
        c = p.correct
        if c is not None:
            decided += 1
            correct += int(c)
    return correct, decided

@dataclass
class MyMatch:
    prediction: Prediction
    color: str                   # "RED" | "BLUE"
    partner: tuple[int, ...]
    opponents: tuple[int, ...]
    cow: float                   # chance of winning for my alliance
    rating: int                  # MR 1..3
    predicted_win: bool
    actual: Optional[str]        # "Win" | "Loss" | "Tie" | None


def my_team_matches(preds: Iterable[Prediction], team: int) -> list[MyMatch]:
    out = []
    for p in preds:
        color = p.match.color_of(team)
        if not color:
            continue
        mine = p.match.red if color == "RED" else p.match.blue
        opp = p.match.blue if color == "RED" else p.match.red
        cow = p.cow_for(color)
        w = p.match.winner
        actual = None if w is None else ("Tie" if w == "TIE" else "Win" if w == color else "Loss")
        out.append(MyMatch(p, color, tuple(n for n in mine if n != team), opp, cow, match_rating(cow),
                           p.winner == color, actual))
    return out


def old_rp(wins: int, matches: int) -> float:
    """AutoScout '1'!X6 - "Old RP (out of 2)" = 2 · wins / matches."""
    return round(2 * wins / matches, 2) if matches else 0.0

@dataclass
class Alliance:
    """One possible two-team alliance, scored against every alliance it could face.

    Interface rows 5+ are these rows for one team: the partner's CAP, number and
    name, and the "Total Score" the pair would put up (Interface!D5 = my CAP plus
    theirs). The opponent columns are this port's own - the sheet ranked partners
    but never asked which alliances could actually beat which.
    """
    teams: tuple[int, ...]
    names: tuple[str, ...]
    caps: tuple[float, ...]
    cap_sum: float                      # Interface!D  "Total Score"
    rank: int = 0
    beats: int = 0                      # legal opponents this alliance out-scores
    legal: int = 0                      # alliances sharing none of its teams
    closest: tuple[int, ...] = ()       # the strongest of those
    closest_cap_sum: float = 0.0
    alliance_strength: float = 0.0      # fitted win rate (%) from StrengthFit - smoothed
                                         # version of win_rate*100, set by alliance_report()

    @property
    def margin(self) -> float:
        """Points in hand against the strongest alliance that could line up opposite."""
        return self.cap_sum - self.closest_cap_sum

    @property
    def unbeatable(self) -> bool:
        """Out-scores every alliance it could legally face - "beats any scenario"."""
        return self.legal > 0 and self.beats == self.legal

    @property
    def win_rate(self) -> float:
        return self.beats / self.legal if self.legal else 0.0

    @property
    def cow(self) -> float:
        """Chance of winning against the closest matchup it could actually face. Now
        Phi(margin / combined sigma) - see Prediction.red_cow - instead of a point share."""
        spread = math.hypot(_cow_sigma(self.cap_sum), _cow_sigma(self.closest_cap_sum))
        return _phi((self.cap_sum - self.closest_cap_sum) / spread) if spread else 0.5

    @property
    def label(self) -> str:
        return " + ".join(str(n) for n in self.teams)

    def partner_of(self, team: int) -> int:
        return next((n for n in self.teams if n != team), 0)

    def partner_cap(self, team: int) -> float:      # Interface!A - the partner's CAP
        return sum(c for n, c in zip(self.teams, self.caps) if n != team)

    def partner_name(self, team: int) -> str:       # Interface!C
        return next((s for n, s in zip(self.teams, self.names) if n != team), "")


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (probit), Acklam's rational approximation
    (relative error ~1.15e-9) - used only to fit `StrengthFit`, not for `_phi`."""
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Ordinary least squares, closed form: slope, intercept, R²."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0), 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    sse = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    sst = sum((y - my) ** 2 for y in ys)
    return slope, intercept, (1 - sse / sst if sst else 1.0)


@dataclass
class StrengthFit:
    """A win-rate-vs-Total-CAP curve, fitted fresh from whichever alliances are loaded
    (`fit_alliance_strength`), rather than hard-coded. `win_rate` (beats/legal) is really a
    percentile rank, i.e. the CDF of Total CAP over every possible alliance, so it is fit as
    a shifted log-normal CDF: Win% = 100*Phi[(ln(CAP + shift) - mu) / sigma]. `shift` makes
    ln(CAP + shift) as close to normal as possible (found by a golden-section search); mu
    and sigma then fall out of one linear regression of probit(win_rate) on that log."""
    shift: float
    mu: float
    sigma: float
    r2: float
    n: int

    def predict(self, cap_sum: float) -> float:
        """Predicted win rate (0-100) for a given Total CAP, from the fitted curve."""
        x = cap_sum + self.shift
        if x <= 0 or self.sigma <= 0:
            return 0.0
        return 100.0 * _phi((math.log(x) - self.mu) / self.sigma)

    @property
    def equation(self) -> str:
        return f"Win rate % = 100 x Phi[(ln(Total rating + {self.shift:.2f}) - {self.mu:.3f}) / {self.sigma:.3f}]"


def fit_alliance_strength(table: list["Alliance"]) -> Optional[StrengthFit]:
    """Fits `StrengthFit` to the loaded alliance table's (cap_sum, win_rate) pairs. Returns
    None when there isn't enough data (too few alliances, or no spread in Total CAP) to fit."""
    pts = [(a.cap_sum, a.win_rate) for a in table if a.legal > 0]
    if len(pts) < 8:
        return None
    caps = [c for c, _ in pts]
    min_cap, max_cap = min(caps), max(caps)
    if max_cap - min_cap < 1e-9:
        return None
    eps = 0.5 / len(pts)

    def score(shift: float) -> tuple[float, float, float]:
        xs, ys = [], []
        for cap, w in pts:
            arg = cap + shift
            if arg <= 1e-6:
                return -1e18, 0.0, 1.0
            xs.append(math.log(arg))
            ys.append(_norm_ppf(min(max(w, eps), 1 - eps)))
        slope, intercept, r2 = _linreg(xs, ys)
        if slope <= 0:
            return -1e18, 0.0, 1.0
        sigma = 1.0 / slope
        return r2, -intercept * sigma, sigma

    lo, hi = -min_cap + 1e-3, -min_cap + (max_cap - min_cap) * 5 + 1000.0
    gr = (math.sqrt(5) - 1) / 2
    c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    fc, fd = score(c)[0], score(d)[0]
    for _ in range(60):
        if fc < fd:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = score(d)[0]
        else:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = score(c)[0]
    shift = (lo + hi) / 2
    r2, mu, sigma = score(shift)
    return StrengthFit(shift=shift, mu=mu, sigma=sigma, r2=r2, n=len(pts)) if sigma > 0 else None


def alliance_table(teams: dict[int, TeamStats],
                   strength: Callable[[TeamStats], float] = lambda t: t.strength) -> list[Alliance]:
    """Every possible two-team alliance, ranked by rating sum (CAP, OPR or EPA - Settings.rating).

    Alliance strength is CAP(a) + CAP(b) (TED!D/E), so "A beats B" is just the larger
    sum - and an alliance's opponents are the ones sharing none of its teams, because
    a team cannot play both sides. The whole table therefore falls out of one sort:
    everything below a row in the ranking is something it out-scores, less the handful
    that share a team with it. Comparing every pair directly would be O(alliances²)
    for the same answer; this is O(alliances × teams).
    """
    members = sorted(teams.values(), key=lambda t: t.number)
    caps = {t.number: strength(t) for t in members}
    table = [Alliance(teams=(a.number, b.number), names=(a.name, b.name),
                      caps=(caps[a.number], caps[b.number]),
                      cap_sum=caps[a.number] + caps[b.number])
             for a, b in itertools.combinations(members, 2)]
    table.sort(key=lambda x: -x.cap_sum)
    ladder = [-x.cap_sum for x in table]          # ascending, which is what bisect wants
    appears: dict[int, set[int]] = {}
    for i, x in enumerate(table):
        for n in x.teams:
            appears.setdefault(n, set()).add(i)
    for i, x in enumerate(table):
        x.rank = i + 1
        shares = appears[x.teams[0]] | appears[x.teams[1]]      # includes x itself
        x.legal = len(table) - len(shares)
        weaker = len(table) - bisect.bisect_right(ladder, -x.cap_sum)
        x.beats = weaker - sum(1 for j in shares if table[j].cap_sum < x.cap_sum)
        for j, y in enumerate(table):             # the first row it could actually face
            if j not in shares:
                x.closest, x.closest_cap_sum = y.teams, y.cap_sum
                break
    return table


def _rank_order(table: list[Standing], teams: dict[int, TeamStats],
                strength: Callable[[TeamStats], float]) -> list[int]:
    """Team numbers in seeding order (Sheet16's rank column). Before a schedule exists
    there are no standings, so fall back to CAP order and the tab still works."""
    order = [r.number for r in table if r.number in teams]
    return order or [t.number for t in sorted(teams.values(), key=lambda t: -strength(t))]


def _seat_bar(caps: dict[int, float], below: list[int]) -> tuple[float, float]:
    """Sheet1!N:S for one seat - the best CAP still on the board once one, two, three,
    four and five teams are gone (five overlapping MAXA windows), then their average
    with the largest counted twice (Sheet1!S = average(N:R, max(N:R))). That average
    is the seat's "pick must be above" bar (Sheet1!F1/F2).
    """
    windows = [max((caps[n] for n in below[k:]), default=0.0) for k in range(5)]
    return windows[0], (sum(windows) + max(windows)) / 6


@dataclass
class CaptainSeat:
    """One row of Sheet1 rows 3–8: a captain and what the draft leaves for them."""
    rank: int
    team: int
    name: str
    cap: float                      # Sheet1!G
    best_available: float           # Sheet1!D  "Likely 1st pick could score"
    bar: float                      # Sheet1!S  pick must be above this
    pick: int = 0                   # Sheet1!E  the team this seat is expected to take
    pick_cap: float = 0.0
    projected_total: float = 0.0    # Sheet1!H


def captain_seats(table: list[Standing], teams: dict[int, TeamStats], count: int = 6,
                  exclude: int = 0,
                  strength: Callable[[TeamStats], float] = lambda t: t.strength) -> list[CaptainSeat]:
    """Sheet1!K10:S15 - the six alliance captains and the picks the draft leaves them.

    Sheet1!U:Z tried to stop two captains landing on the same team with a chain of IF
    comparisons; that chain is broken in the source (its later rows reach into empty
    cells), so this ports its evident intent instead - walk the seats in rank order and
    give each one the best team no earlier seat has taken. `exclude` keeps your own team
    out of everyone else's pick, which is what Sheet1!AA was for.
    """
    order = _rank_order(table, teams, strength)
    caps = {n: strength(teams[n]) for n in order}
    seats: list[CaptainSeat] = []
    taken = {exclude} if exclude else set()
    for i, number in enumerate(order[:count]):
        below = order[i + 1:]
        best, bar = _seat_bar(caps, below)
        pick = next((n for n in below if n not in taken), 0)
        if pick:
            taken.add(pick)
        cap, pick_cap = caps[number], caps.get(pick, 0.0)
        seats.append(CaptainSeat(
            rank=i + 1, team=number, name=teams[number].name, cap=cap,
            best_available=best, bar=bar, pick=pick, pick_cap=pick_cap,
            # Sheet1!H, corrected: the sheet averaged (captain + best available) with
            # (pick + best available), which drops the captain out of the second term.
            projected_total=((cap + best) + (cap + pick_cap)) / 2))
    return seats


@dataclass
class AllianceReport:
    """The whole ALSO workbook for one session: Sheet1's captain block, and Interface's
    ranked list of partners for the team typed into the box."""
    table: list[Alliance] = field(default_factory=list)
    seats: list[CaptainSeat] = field(default_factory=list)
    my_team: int = 0
    my_rank: int = 0
    my_cap: float = 0.0
    bar: Optional[float] = None                              # Sheet1!F2
    partners: list[Alliance] = field(default_factory=list)   # Interface rows 5+
    strength_fit: Optional[StrengthFit] = None                # fitted win-rate-vs-CAP curve

    @property
    def unbeatable(self) -> list[Alliance]:
        return [a for a in self.table if a.unbeatable]

    @property
    def qualifying(self) -> list[Alliance]:
        """Interface!A52's FILTER - the partners whose CAP clears this seat's bar."""
        if self.bar is None:
            return list(self.partners)
        return [a for a in self.partners if a.partner_cap(self.my_team) > self.bar]

    @property
    def average_total(self) -> Optional[float]:
        """Sheet1!I3, corrected: the sheet averaged a hard-coded 49 rows of the
        qualifying list, silently truncating it; this averages all of them."""
        rows = self.qualifying
        return sum(a.cap_sum for a in rows) / len(rows) if rows else None


def alliance_report(teams: dict[int, TeamStats], table: list[Standing], my_team: int = 0,
                    captains: int = 6,
                    strength: Callable[[TeamStats], float] = lambda t: t.strength) -> AllianceReport:
    """Interface!B2 in, the whole sheet out: every alliance, every captain's seat, and
    the partners the team in the box could win with.

    The sheet's bar (Sheet1!F2) came from a VLOOKUP over the six captain rows, so a team
    seeded 7th or lower silently got the 6th seat's number. Here the same five windows
    are read at the team's own seat, which agrees with the sheet for the top six and
    means something for everyone else.
    """
    rep = AllianceReport(my_team=my_team)
    rep.table = alliance_table(teams, strength)
    rep.strength_fit = fit_alliance_strength(rep.table)
    if rep.strength_fit is not None:
        for a in rep.table:
            a.alliance_strength = rep.strength_fit.predict(a.cap_sum)
    rep.seats = captain_seats(table, teams, captains, exclude=my_team, strength=strength)
    t = teams.get(my_team)
    if t is None:
        return rep
    order = _rank_order(table, teams, strength)
    caps = {n: strength(teams[n]) for n in order}
    rep.my_cap = strength(t)
    rep.my_rank = next((r.rank for r in table if r.number == my_team), 0)
    if my_team in order:
        i = order.index(my_team)
        rep.my_rank = rep.my_rank or i + 1
        rep.bar = _seat_bar(caps, order[i + 1:])[1]
    rep.partners = sorted((a for a in rep.table if my_team in a.teams), key=lambda a: -a.cap_sum)
    return rep

"""FTCScout REST client with a disk cache and an offline mode, plus the
season adapter that turns raw payloads into core.EventStat / core.Match,
and a parser for pasted schedules (the no-connectivity fallback).

Verified endpoints (no API key):
  GET /rest/v1/teams/{n}
  GET /rest/v1/teams/{n}/events/{season}
  GET /rest/v1/teams/{n}/quick-stats?season=S
  GET /rest/v1/events/{season}/{code}
  GET /rest/v1/events/{season}/{code}/teams
  GET /rest/v1/events/{season}/{code}/matches
"""

import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional


BASE_URL = "https://api.ftcscout.org/rest/v1"
USER_AGENT = "AutoScout/2.0 (+ftc-winprob; team scouting tool)"

# Season → keys in the `opr` block whose sum is the endgame component.
SEASON_ENDGAME_KEYS = {
    2022: ["egPoints"],
    2023: ["egPoints"],
    2024: ["dcParkPoints"],       # INTO THE DEEP: park/ascent
    2025: ["dcBasePoints"],       # DECODE: base return
}
_ENDGAME_HINTS = ("egPoints", "dcParkPoints", "dcBasePoints", "dcAscentPoints", "dcClimbPoints", "dcHangPoints")


class ApiError(Exception):
    """Base class for client errors; `.user_message` is safe to show in the UI."""
    user_message = "FTCScout request failed."

    def __init__(self, msg: str = ""):
        super().__init__(msg or self.user_message)
        if msg:
            self.user_message = msg


class NotFound(ApiError):
    user_message = "Not found on FTCScout."


class SeasonUnavailable(ApiError):
    user_message = "FTCScout does not serve this season yet."


class OfflineMiss(ApiError):
    user_message = "Offline and not in the cache."


class NetworkError(ApiError):
    user_message = "Could not reach FTCScout (no internet?)."


def default_cache_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "autoscout")


class Cache:
    """Tiny URL → body cache in one sqlite file."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS http (url TEXT PRIMARY KEY, fetched REAL, body TEXT)")
        self.conn.commit()

    def get(self, url: str) -> Optional[tuple[float, str]]:
        row = self.conn.execute("SELECT fetched, body FROM http WHERE url=?", (url,)).fetchone()
        return (row[0], row[1]) if row else None

    def put(self, url: str, body: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO http VALUES (?,?,?)", (url, time.time(), body))
        self.conn.commit()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM http")
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM http").fetchone()[0]


class FTCScoutClient:
    """`max_age` seconds: serve from cache if younger; `offline`: never touch the network."""

    def __init__(self, cache_dir: Optional[str] = None, offline: bool = False,
                 timeout: float = 15.0, max_age: float = 300.0, base_url: str = BASE_URL,
                 opener: Optional[Callable[[str, float], str]] = None):
        self.cache = Cache(os.path.join(cache_dir or default_cache_dir(), "http_cache.sqlite"))
        self.offline = offline
        self.timeout = timeout
        self.max_age = max_age
        self.base_url = base_url.rstrip("/")
        self._opener = opener or self._http_get
        self.last_error: Optional[str] = None

    _ssl_ctx: Optional[ssl.SSLContext] = None

    @classmethod
    def _http_get(cls, url: str, timeout: float) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=cls._ssl_ctx) as r:
                return r.read().decode("utf-8")
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e) or cls._ssl_ctx is not None:
                raise
            cls._ssl_ctx = _fallback_ssl_context()      # python.org builds without certificates installed
            with urllib.request.urlopen(req, timeout=timeout, context=cls._ssl_ctx) as r:
                return r.read().decode("utf-8")

    def get_json(self, path: str, max_age: Optional[float] = None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        age = self.max_age if max_age is None else max_age
        hit = self.cache.get(url)
        if hit and (self.offline or time.time() - hit[0] < age):
            return self._decode(hit[1], url)
        if self.offline:
            raise OfflineMiss(f"Offline and '{path}' is not cached.")
        try:
            body = self._opener(url, self.timeout)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
            if e.code == 404 or "No event" in text or "No team" in text:
                raise NotFound(_clean(text) or f"{path}: not found")
            if "Invalid season" in text:
                raise SeasonUnavailable(_clean(text))
            raise NetworkError(f"HTTP {e.code} from FTCScout: {_clean(text)[:200]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if hit:                       # stale cache beats no data
                self.last_error = f"Using cached data; FTCScout unreachable ({e})."
                return self._decode(hit[1], url)
            raise NetworkError(f"Could not reach FTCScout: {e}")
        self.cache.put(url, body)
        return self._decode(body, url)

    @staticmethod
    def _decode(body: str, url: str):
        b = body.strip()
        if b.startswith("{") or b.startswith("["):
            return json.loads(b)
        # FTCScout returns plain-text errors with 200 in some cases
        if "Invalid season" in b:
            raise SeasonUnavailable(_clean(b))
        if b.startswith("No event") or b.startswith("No team") or "Cannot GET" in b:
            raise NotFound(_clean(b) or url)
        raise ApiError(f"Unexpected response from {url}: {b[:120]}")

    def team(self, number: int) -> dict:
        return self.get_json(f"teams/{int(number)}", max_age=7 * 86400)

    def team_events(self, number: int, season: int) -> list:
        return self.get_json(f"teams/{int(number)}/events/{int(season)}")

    def quick_stats(self, number: int, season: int) -> dict:
        return self.get_json(f"teams/{int(number)}/quick-stats?season={int(season)}")

    def team_awards(self, number: int, season: Optional[int] = None) -> list:
        suffix = f"?season={int(season)}" if season else ""
        return self.get_json(f"teams/{int(number)}/awards{suffix}", max_age=86400)

    def team_matches(self, number: int, season: Optional[int] = None) -> list:
        suffix = f"?season={int(season)}" if season else ""
        return self.get_json(f"teams/{int(number)}/matches{suffix}", max_age=3600)

    def search_teams(self, text: str, limit: int = 30) -> list:
        q = urllib.parse.quote(str(text).strip(), safe="")
        return self.get_json(f"teams/search?searchText={q}&limit={int(limit)}", max_age=3600)

    def event(self, season: int, code: str) -> dict:
        return self.get_json(f"events/{int(season)}/{code.strip().upper()}", max_age=86400)

    def event_teams(self, season: int, code: str) -> list:
        return self.get_json(f"events/{int(season)}/{code.strip().upper()}/teams")

    def event_matches(self, season: int, code: str, max_age: float = 60) -> list:
        return self.get_json(f"events/{int(season)}/{code.strip().upper()}/matches", max_age=max_age)


def _fallback_ssl_context() -> ssl.SSLContext:
    """Default verification failed: try the OS trust store (macOS), then the bundled CA file."""
    ctx = ssl.create_default_context()
    if sys.platform == "darwin":
        try:
            pem = subprocess.run(
                ["security", "find-certificate", "-a", "-p", "/System/Library/Keychains/SystemRootCertificates.keychain"],
                capture_output=True, text=True, timeout=20, check=True).stdout
            if "BEGIN CERTIFICATE" in pem:
                ctx.load_verify_locations(cadata=pem)
                return ctx
        except Exception:
            pass
    for base in (getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__))):
        if base:
            cafile = os.path.join(base, "cacert.pem")
            if os.path.exists(cafile):
                ctx.load_verify_locations(cafile=cafile)
                return ctx
    if CA_BUNDLE_PEM:
        ctx.load_verify_locations(cadata=CA_BUNDLE_PEM)
    return ctx


CA_BUNDLE_PEM = """\
-----BEGIN CERTIFICATE-----
MIIEkTCCA3mgAwIBAgIERWtQVDANBgkqhkiG9w0BAQUFADCBsDELMAkGA1UEBhMC
VVMxFjAUBgNVBAoTDUVudHJ1c3QsIEluYy4xOTA3BgNVBAsTMHd3dy5lbnRydXN0
Lm5ldC9DUFMgaXMgaW5jb3Jwb3JhdGVkIGJ5IHJlZmVyZW5jZTEfMB0GA1UECxMW
KGMpIDIwMDYgRW50cnVzdCwgSW5jLjEtMCsGA1UEAxMkRW50cnVzdCBSb290IENl
cnRpZmljYXRpb24gQXV0aG9yaXR5MB4XDTA2MTEyNzIwMjM0MloXDTI2MTEyNzIw
NTM0MlowgbAxCzAJBgNVBAYTAlVTMRYwFAYDVQQKEw1FbnRydXN0LCBJbmMuMTkw
NwYDVQQLEzB3d3cuZW50cnVzdC5uZXQvQ1BTIGlzIGluY29ycG9yYXRlZCBieSBy
ZWZlcmVuY2UxHzAdBgNVBAsTFihjKSAyMDA2IEVudHJ1c3QsIEluYy4xLTArBgNV
BAMTJEVudHJ1c3QgUm9vdCBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBALaVtkNC+sZtKm9I35RMOVcF7sN5EUFo
Nu3s/poBj6E4KPz3EEZmLk0eGrEaTsbRwJWIsMn/MYszA9u3g3s+IIRe7bJWKKf4
4LlAcTfFy0cOlypowCKVYhXbR9n10Cv/gkvJrT7eTNuQgFA/CYqEAOwwCj0Yzfv9
KlmaI5UXLEWeH25DeW0MXJj+SKfFI0dcXv1u5x609mhF0YaDW6KKjbHjKYD+JXGI
rb68j6xSlkuqUY3kEzEZ6E5Nn9uss2rVvDlUccp6en+Q3X0dgNmBu1kmwhH+5pPi
94DkZfs0Nw4pgHBNrziGLp5/V6+eF67rHMsoIV+2HNjnogQi+dPa2MsCAwEAAaOB
sDCBrTAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zArBgNVHRAEJDAi
gA8yMDA2MTEyNzIwMjM0MlqBDzIwMjYxMTI3MjA1MzQyWjAfBgNVHSMEGDAWgBRo
kORnpKZTgMeGZqTx90tD+4S9bTAdBgNVHQ4EFgQUaJDkZ6SmU4DHhmak8fdLQ/uE
vW0wHQYJKoZIhvZ9B0EABBAwDhsIVjcuMTo0LjADAgSQMA0GCSqGSIb3DQEBBQUA
A4IBAQCT1DCw1wMgKtD5Y+iRDAUgqV8ZyntyTtSx29CW+1RaGSwMCPeyvIWonX9t
O1KzKtvn1ISMY/YPyyYBkVBs9F8U4pN0wBOeMDpQ47RgxRzwIkSNcUesyBrJ6Zua
AGAT/3B+XxFNSRuzFVJ7yVTav52Vr2ua2J7p8eRDjeIRRDq/r72DQnNSi6q7pynP
9WQcCk3RvKqsnyrQ/39/2n3qse0wJcGE2jTSW3iDVuycNsMm4hH2Z0kdkquM++v/
eu6FSqdQgPCnXEqULl8FmTxSQeDNtGPPAUO6nIPcj2A781q0tHuu2guQOHXvgR1m
0vdXcDazv/wor3ElhVsT/h5/WrQ8
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFtzCCA5+gAwIBAgICBQkwDQYJKoZIhvcNAQEFBQAwRTELMAkGA1UEBhMCQk0x
GTAXBgNVBAoTEFF1b1ZhZGlzIExpbWl0ZWQxGzAZBgNVBAMTElF1b1ZhZGlzIFJv
b3QgQ0EgMjAeFw0wNjExMjQxODI3MDBaFw0zMTExMjQxODIzMzNaMEUxCzAJBgNV
BAYTAkJNMRkwFwYDVQQKExBRdW9WYWRpcyBMaW1pdGVkMRswGQYDVQQDExJRdW9W
YWRpcyBSb290IENBIDIwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCa
GMpLlA0ALa8DKYrwD4HIrkwZhR0In6spRIXzL4GtMh6QRr+jhiYaHv5+HBg6XJxg
Fyo6dIMzMH1hVBHL7avg5tKifvVrbxi3Cgst/ek+7wrGsxDp3MJGF/hd/aTa/55J
WpzmM+Yklvc/ulsrHHo1wtZn/qtmUIttKGAr79dgw8eTvI02kfN/+NsRE8Scd3bB
rrcCaoF6qUWD4gXmuVbBlDePSHFjIuwXZQeVikvfj8ZaCuWw419eaxGrDPmF60Tp
+ARz8un+XJiM9XOva7R+zdRcAitMOeGylZUtQofX1bOQQ7dsE/He3fbE+Ik/0XX1
ksOR1YqI0JDs3G3eicJlcZaLDQP9nL9bFqyS2+r+eXyt66/3FsvbzSUr5R/7mp/i
Ucw6UwxI5g69ybR2BlLmEROFcmMDBOAENisgGQLodKcftslWZvB1JdxnwQ5hYIiz
PtGo/KPaHbDRsSNU30R2be1B2MGyIrZTHN81Hdyhdyox5C315eXbyOD/5YDXC2Og
/zOhD7osFRXql7PSorW+8oyWHhqPHWykYTe5hnMz15eWniN9gqRMgeKh0bpnX5UH
oycR7hYQe7xFSkyyBNKr79X9DFHOUGoIMfmR2gyPZFwDwzqLID9ujWc9Otb+fVuI
yV77zGHcizN300QyNQliBJIWENieJ0f7OyHj+OsdWwIDAQABo4GwMIGtMA8GA1Ud
EwEB/wQFMAMBAf8wCwYDVR0PBAQDAgEGMB0GA1UdDgQWBBQahGK8SEwzJQTU7tD2
A8QZRtGUazBuBgNVHSMEZzBlgBQahGK8SEwzJQTU7tD2A8QZRtGUa6FJpEcwRTEL
MAkGA1UEBhMCQk0xGTAXBgNVBAoTEFF1b1ZhZGlzIExpbWl0ZWQxGzAZBgNVBAMT
ElF1b1ZhZGlzIFJvb3QgQ0EgMoICBQkwDQYJKoZIhvcNAQEFBQADggIBAD4KFk2f
BluornFdLwUvZ+YTRYPENvbzwCYMDbVHZF34tHLJRqUDGCdViXh9duqWNIAXINzn
g/iN/Ae42l9NLmeyhP3ZRPx3UIHmfLTJDQtyU/h2BwdBR5YM++CCJpNVjP4iH2Bl
fF/nJrP3MpCYUNQ3cVX2kiF495V5+vgtJodmVjB3pjd4M1IQWK4/YY7yarHvGH5K
WWPKjaJW1acvvFYfzznB4vsKqBUsfU16Y8Zsl0Q80m/DShcK+JDSV6IZUaUtl0Ha
B0+pUNqQjZRG4T7wlP0QADj1O+hA4bRuVhogzG9Yje0uRY/W6ZM/57Es3zrWIozc
hLsib9D45MY56QSIPMO661V6bYCZJPVsAfv4l7CUW+v90m/xd2gNNWQjrLhVoQPR
TUIZ3Ph1WVaj+ahJefivDrkRoHy3au000LYmYjgahwz46P0u05B/B5EqHdZ+XIWD
mbA4CD/pXvk1B+TJYm5Xf6dQlfe6yJvmjqIBxdZmv3lh8zwc4bmCXF2gw+nYSL0Z
ohEUGW6yhhtoPkg3Goi3XZZenMfvJ2II4pEZXNLxId26F0KCl3GBUzGpn/Z9Yr9y
4aOTHcyKJloJONDO1w2AFrR4pTqHTI2KpdVGl/IsELm8VCLAAVBpQ570su9t+Oza
8eOx79+Rj1QqCyXBJhnEUhAFZdWCEOrCMc0u
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIGnTCCBIWgAwIBAgICBcYwDQYJKoZIhvcNAQEFBQAwRTELMAkGA1UEBhMCQk0x
GTAXBgNVBAoTEFF1b1ZhZGlzIExpbWl0ZWQxGzAZBgNVBAMTElF1b1ZhZGlzIFJv
b3QgQ0EgMzAeFw0wNjExMjQxOTExMjNaFw0zMTExMjQxOTA2NDRaMEUxCzAJBgNV
BAYTAkJNMRkwFwYDVQQKExBRdW9WYWRpcyBMaW1pdGVkMRswGQYDVQQDExJRdW9W
YWRpcyBSb290IENBIDMwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDM
V0IWVJzmmNPTTe7+7cefQzlKZbPoFog02w1ZkXTPkrgEQK0CSzGrvI2RaNggDhoB
4hp7Thdd4oq3P5kazethq8Jlph+3t723j/z9cI8LoGe+AaJZz3HmDyl2/7FWeUUr
H556VOijKTVopAFPD6QuN+8bv+OPEKhyq1hX51SGyMnzW9os2l2ObjyjPtr7guXd
8lyyBTNvijbO0BNO/79KDDRMpsMhvVAEVeuxu537RR5kFd5VAYwCdrXLoT9Cabwv
vWhDFlaJKjdhkf2mrk7AyxRllDdLkgbvBNDInIjbC3uBr7E9KsRlOni27tyAsdLT
mZw67mtaa7ONt9XOnMK+pUsvFrGeaDsGb659n/je7Mwpp5ijJUMv7/FfJuGITfhe
btfZFG4ZM2mnO4SJk8RTVROhUXhA+LjJou57ulJCg54U7QVSWllWp5f8nT8KKdjc
T5EOE7zelaTfi5m+rJsziO+1ga8bxiJTyPbH7pcUsMV8eFLI8M5ud2CEpukqdiDt
WAEXMJPpGovgc2PZapKUSU60rUqFxKMiMPwJ7Wgic6aIDFUhWMXhOp8q3crhkODZ
c6tsgLjoC2SToJyMGf+z0gzskSaHirOi4XCPLArlzW1oUevaPwV/izLmE1xr/l9A
4iLItLRkT9a6fUg+qGkM17uGcclzuD87nSVL2v9A6wIDAQABo4IBlTCCAZEwDwYD
VR0TAQH/BAUwAwEB/zCB4QYDVR0gBIHZMIHWMIHTBgkrBgEEAb5YAAMwgcUwgZMG
CCsGAQUFBwICMIGGGoGDQW55IHVzZSBvZiB0aGlzIENlcnRpZmljYXRlIGNvbnN0
aXR1dGVzIGFjY2VwdGFuY2Ugb2YgdGhlIFF1b1ZhZGlzIFJvb3QgQ0EgMyBDZXJ0
aWZpY2F0ZSBQb2xpY3kgLyBDZXJ0aWZpY2F0aW9uIFByYWN0aWNlIFN0YXRlbWVu
dC4wLQYIKwYBBQUHAgEWIWh0dHA6Ly93d3cucXVvdmFkaXNnbG9iYWwuY29tL2Nw
czALBgNVHQ8EBAMCAQYwHQYDVR0OBBYEFPLAE+CCQz777i9nMpY1XNu4ywLQMG4G
A1UdIwRnMGWAFPLAE+CCQz777i9nMpY1XNu4ywLQoUmkRzBFMQswCQYDVQQGEwJC
TTEZMBcGA1UEChMQUXVvVmFkaXMgTGltaXRlZDEbMBkGA1UEAxMSUXVvVmFkaXMg
Um9vdCBDQSAzggIFxjANBgkqhkiG9w0BAQUFAAOCAgEAT62gLEz6wPJv92ZVqyM0
7ucp2sNbtrCD2dDQ4iH782CnO11gUyeim/YIIirnv6By5ZwkajGxkHon24QRiSem
d1o417+shvzuXYO8BsbRd2sPbSQvS3pspweWyuOEn62Iix2rFo1bZhfZFvSLgNLd
+LJ2w/w4E6oM3kJpK27zPOuAJ9v1pkQNn1pVWQvVDVJIxa6f8i+AxeoyUDUSly7B
4f/xI4hROJ/yZlZ25w9Rl6VSDE1JUZU2Pb+iSwwQHYaZTKrzchGT5Or2m9qoXadN
t54CrnMAyNojA+j56hl0YgCUyyIgvpSnWbWCar6ZeXqp8kokUvd0/bpO5qgdAm6x
DYBEwa7TIzdfu4V8K5Iu6H6li92Z4b8nby1dqnuH/grdS/yO9SbkbnBCbjPsMZ57
k8HkyWkaPcBrTiJt7qtYTcbQQcEr6k8Sh17rRdhs9ZgC06DYVYoGmRmioHfRMJ6s
zHXug/WwYjnPbFfiTNKRCw51KBuav/0aQ/HKd/s7j2G4aSgWQgRecCocIdiP4b0j
Wy10QJLZYxkNc91pvGJHvOB0K7Lrfb5BG7XARsWhIstfTsEokt4YutUqKLsRixeT
mJlglFwjz1onl14LBQaTNx47aTbrqZ5hHY8y2o4M1nQ+ewkk2gF3R8Q7zTSMmfXK
4SVhM7JZG+Ju1zdXtg2pEto=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDtzCCAp+gAwIBAgIQDOfg5RfYRv6P5WD8G/AwOTANBgkqhkiG9w0BAQUFADBl
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJv
b3QgQ0EwHhcNMDYxMTEwMDAwMDAwWhcNMzExMTEwMDAwMDAwWjBlMQswCQYDVQQG
EwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNl
cnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJvb3QgQ0EwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCtDhXO5EOAXLGH87dg+XESpa7c
JpSIqvTO9SA5KFhgDPiA2qkVlTJhPLWxKISKityfCgyDF3qPkKyK53lTXDGEKvYP
mDI2dsze3Tyoou9q+yHyUmHfnyDXH+Kx2f4YZNISW1/5WBg1vEfNoTb5a3/UsDg+
wRvDjDPZ2C8Y/igPs6eD1sNuRMBhNZYW/lmci3Zt1/GiSw0r/wty2p5g0I6QNcZ4
VYcgoc/lbQrISXwxmDNsIumH0DJaoroTghHtORedmTpyoeb6pNnVFzF1roV9Iq4/
AUaG9ih5yLHa5FcXxH4cDrC0kqZWs72yl+2qp/C3xag/lRbQ/6GW6whfGHdPAgMB
AAGjYzBhMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBRF66Kv9JLLgjEtUYunpyGd823IDzAfBgNVHSMEGDAWgBRF66Kv9JLLgjEtUYun
pyGd823IDzANBgkqhkiG9w0BAQUFAAOCAQEAog683+Lt8ONyc3pklL/3cmbYMuRC
dWKuh+vy1dneVrOfzM4UKLkNl2BcEkxY5NM9g0lFWJc1aRqoR+pWxnmrEthngYTf
fwk8lOa4JiwgvT2zKIn3X/8i4peEH+ll74fg38FnSbNd67IJKusm7Xi+fT8r87cm
NW1fiQG2SVufAQWbqz0lwcy2f8Lxb4bG+mRo64EtlOtCt/qMHt1i8b5QZ7dsvfPx
H2sMNgcWfzd8qVttevESRmCD1ycEvkvOl77DZypoEd+A5wwzZr8TDRRu838fYxAe
+o0bJW1sj6W3YQGx0qMmoRBxna3iw/nDmVG3KwcIzi7mULKn+gpFL6Lw8g==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDrzCCApegAwIBAgIQCDvgVpBCRrGhdWrJWZHHSjANBgkqhkiG9w0BAQUFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBD
QTAeFw0wNjExMTAwMDAwMDBaFw0zMTExMTAwMDAwMDBaMGExCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IENBMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4jvhEXLeqKTTo1eqUKKPC3eQyaKl7hLOllsB
CSDMAZOnTjC3U/dDxGkAV53ijSLdhwZAAIEJzs4bg7/fzTtxRuLWZscFs3YnFo97
nh6Vfe63SKMI2tavegw5BmV/Sl0fvBf4q77uKNd0f3p4mVmFaG5cIzJLv07A6Fpt
43C/dxC//AH2hdmoRBBYMql1GNXRor5H4idq9Joz+EkIYIvUX7Q6hL+hqkpMfT7P
T19sdl6gSzeRntwi5m3OFBqOasv+zbMUZBfHWymeMr/y7vrTC0LUq7dBMtoM1O/4
gdW7jVg/tRvoSSiicNoxBN33shbyTApOB6jtSj1etX+jkMOvJwIDAQABo2MwYTAO
BgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUA95QNVbR
TLtm8KPiGxvDl7I90VUwHwYDVR0jBBgwFoAUA95QNVbRTLtm8KPiGxvDl7I90VUw
DQYJKoZIhvcNAQEFBQADggEBAMucN6pIExIK+t1EnE9SsPTfrgT1eXkIoyQY/Esr
hMAtudXH/vTBH1jLuG2cenTnmCmrEbXjcKChzUyImZOMkXDiqw8cvpOp/2PV5Adg
06O/nVsJ8dWO41P0jmP6P6fbtGbfYmbW0W5BjfIttep3Sp+dWOIrWcBAI+0tKIJF
PnlUkiaY4IBIqDfv8NZ5YBberOgOzW6sRBc4L0na4UU+Krk2U886UAb3LujEV0ls
YSEY1QSteDwsOoBrp+uvFRTp2InBuThs4pFsiv9kuXclVzDAGySj4dzp30d8tbQk
CAUw7C29C79Fv1C5qfPrmAESrciIxpg0X40KPMbp1ZWVbd4=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDxTCCAq2gAwIBAgIQAqxcJmoLQJuPC3nyrkYldzANBgkqhkiG9w0BAQUFADBs
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSswKQYDVQQDEyJEaWdpQ2VydCBIaWdoIEFzc3VyYW5j
ZSBFViBSb290IENBMB4XDTA2MTExMDAwMDAwMFoXDTMxMTExMDAwMDAwMFowbDEL
MAkGA1UEBhMCVVMxFTATBgNVBAoTDERpZ2lDZXJ0IEluYzEZMBcGA1UECxMQd3d3
LmRpZ2ljZXJ0LmNvbTErMCkGA1UEAxMiRGlnaUNlcnQgSGlnaCBBc3N1cmFuY2Ug
RVYgUm9vdCBDQTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAMbM5XPm
+9S75S0tMqbf5YE/yc0lSbZxKsPVlDRnogocsF9ppkCxxLeyj9CYpKlBWTrT3JTW
PNt0OKRKzE0lgvdKpVMSOO7zSW1xkX5jtqumX8OkhPhPYlG++MXs2ziS4wblCJEM
xChBVfvLWokVfnHoNb9Ncgk9vjo4UFt3MRuNs8ckRZqnrG0AFFoEt7oT61EKmEFB
Ik5lYYeBQVCmeVyJ3hlKV9Uu5l0cUyx+mM0aBhakaHPQNAQTXKFx01p8VdteZOE3
hzBWBOURtCmAEvF5OYiiAhF8J2a3iLd48soKqDirCmTCv2ZdlYTBoSUeh10aUAsg
EsxBu24LUTi4S8sCAwEAAaNjMGEwDgYDVR0PAQH/BAQDAgGGMA8GA1UdEwEB/wQF
MAMBAf8wHQYDVR0OBBYEFLE+w2kD+L9HAdSYJhoIAu9jZCvDMB8GA1UdIwQYMBaA
FLE+w2kD+L9HAdSYJhoIAu9jZCvDMA0GCSqGSIb3DQEBBQUAA4IBAQAcGgaX3Nec
nzyIZgYIVyHbIUf4KmeqvxgydkAQV8GK83rZEWWONfqe/EW1ntlMMUu4kehDLI6z
eM7b41N5cdblIZQB2lWHmiRk9opmzN6cN82oNLFpmyPInngiK3BD41VHMWEZ71jF
hS9OMPagMRYjyOfiZRYzy78aG6A9+MpeizGLYAiJLQwGXFK3xPkKmNEVX58Svnw2
Yzi9RKR/5CYrCsSXaQ3pjOLAEFe4yHYSkVXySGnYvCoCWw9E1CAx2/S6cCZdkGCe
vEsXCS+0yx5DaMkHJ8HSXPfqIbloEpw8nL+e/IBcm2PN7EeqJSdnoDfzAIJ9VNep
+OkuE6N36B9K
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFujCCA6KgAwIBAgIJALtAHEP1Xk+wMA0GCSqGSIb3DQEBBQUAMEUxCzAJBgNV
BAYTAkNIMRUwEwYDVQQKEwxTd2lzc1NpZ24gQUcxHzAdBgNVBAMTFlN3aXNzU2ln
biBHb2xkIENBIC0gRzIwHhcNMDYxMDI1MDgzMDM1WhcNMzYxMDI1MDgzMDM1WjBF
MQswCQYDVQQGEwJDSDEVMBMGA1UEChMMU3dpc3NTaWduIEFHMR8wHQYDVQQDExZT
d2lzc1NpZ24gR29sZCBDQSAtIEcyMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIIC
CgKCAgEAr+TufoskDhJuqVAtFkQ7kpJcyrhdhJJCEyq8ZVeCQD5XJM1QiyUqt2/8
76LQwB8CJEoTlo8jE+YoWACjR8cGp4QjK7u9lit/VcyLwVcfDmJlD909Vopz2q5+
bbqBHH5CjCA12UNNhPqE21Is8w4ndwtrvxEvcnifLtg+5hg3Wipy+dpikJKVyh+c
6bM8K8vzARO/Ws/BtQpgvd21mWRTuKCWs2/iJneRjOBiEAKfNA+k1ZIzUd6+jbqE
emA8atufK+ze3gE/bk3lUIbLtK/tREDFylqM2tIrfKjuvqblCqoOpd8FUrdVxyJd
MmqXl2MT28nbeTZ7hTpKxVKJ+STnnXepgv9VHKVxaSvRAiTysybUa9oEVeXBCsdt
MDeQKuSeFDNeFhdVxVu1yzSJkvGdJo+hB9TGsnhQ2wwMC3wLjEHXuendjIj3o02y
MszYF9rNt85mndT9Xv+9lz4pded+p2JYryU0pUHHPbwNUMoDAw8IWh+Vc3hiv69y
FGkOpeUDDniOJihC8AcLYiAQZzlG+qkDzAQ4embvIIO1jEpWjpEA/I5cgt6IoMPi
aG59je883WX0XaxR7ySArqpWl2/5rX3aYT+YdzylkbYcjCbaZaIJbcHiVOO5ykxM
gI93e2CaHt+28kgeDrpOVG2Y4OGiGqJ3UM/EY5LsRxmd6+ZrzsECAwEAAaOBrDCB
qTAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUWyV7
lqRlUX64OfPAeGZe6Drn8O4wHwYDVR0jBBgwFoAUWyV7lqRlUX64OfPAeGZe6Drn
8O4wRgYDVR0gBD8wPTA7BglghXQBWQECAQEwLjAsBggrBgEFBQcCARYgaHR0cDov
L3JlcG9zaXRvcnkuc3dpc3NzaWduLmNvbS8wDQYJKoZIhvcNAQEFBQADggIBACe6
45R88a7A3hfm5djV9VSwg/S7zV4Fe0+fdWavPOhWfvxyeDgD2StiGwC5+OlgzczO
UYrHUDFu4Up+GC9pWbY9ZIEr44OE5iKHjn3g7gKZYbge9LgriBIWhMIxkziWMaa5
O1M/wySTVltpkuzFwbs4AOPsF6m43Md8AYOfMke6UiI0HTJ6CVanfCU2qT1L2sCC
bwq7EsiHSycR+R4tx5M/nttfJmtS2S6K8RTGRI0Vqbe/vd6mGu6uLftIdxf+u+yv
GPUqUfA5hJeVbG4bwyvEdGB5JbAKJ9/fXtI5z0V9QkvfsywexcZdylU6oJxpmo/a
77KwPJ+HbBIrZXAVUjEaJM9vMSNQH4xPjyPDdEFjHFWoFN0+4FFQz/EbMFYOkrCC
hdiDyyJkvC24JdVUorgG6q2SpCSgwYa1ShNqR88uC1aVVMvOmttqtKay20EIhid3
92qgQmwLOM7XdVAyksLfKzAiSNDVQTglXaTpXZ/GlHXQRf0wl0OPkKsKx4ZzYEpp
Ld6leNcG2mqeSz53OiATIgHQv2ieY2BrNU0LbbqhPcCT4H8js1WtciVORvnSFu+w
ZMEBnunKoGqYDs/YYPIvSbjkQuE4NRb0yG5P94FW6LqjviOvrv1vA+ACOzB2+htt
Qc8Bsem4yWb02ybzOqR08kkkW8mw0FfB+j564ZfJ
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDuDCCAqCgAwIBAgIQDPCOXAgWpa1Cf/DrJxhZ0DANBgkqhkiG9w0BAQUFADBI
MQswCQYDVQQGEwJVUzEgMB4GA1UEChMXU2VjdXJlVHJ1c3QgQ29ycG9yYXRpb24x
FzAVBgNVBAMTDlNlY3VyZVRydXN0IENBMB4XDTA2MTEwNzE5MzExOFoXDTI5MTIz
MTE5NDA1NVowSDELMAkGA1UEBhMCVVMxIDAeBgNVBAoTF1NlY3VyZVRydXN0IENv
cnBvcmF0aW9uMRcwFQYDVQQDEw5TZWN1cmVUcnVzdCBDQTCCASIwDQYJKoZIhvcN
AQEBBQADggEPADCCAQoCggEBAKukgeWVzfX2FI7CT8rU4niVWJxB4Q2ZQCQXOZEz
Zum+4YOvYlyJ0fwkW2Gz4BERQRwdbvC4u/jep4G6pkjGnx29vo6pQT64lO0pGtSO
0gMdA+9tDWccV9cGrcrI9f4Or2YlSASWC12juhbDCE/RRvgUXPLIXgGZbf2IzIao
wW8xQmxSPmjL8xk037uHGFaAJsTQ3MBv396gwpEWoGQRS0S8Hvbn+mPeZqx2pHGj
7DaUaHp3pLHnDi+BeuK1cobvomuL8A/b01k/unK8RCSc43Oz969XL0Imnal0ugBS
8kvNU3xHCzaFDmapCJcWNFfBZveA4+1wVMeT4C4oFVmHursCAwEAAaOBnTCBmjAT
BgkrBgEEAYI3FAIEBh4EAEMAQTALBgNVHQ8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB
/zAdBgNVHQ4EFgQUQjK2FvoE/f5dS3rD/fdMQB1aQ68wNAYDVR0fBC0wKzApoCeg
JYYjaHR0cDovL2NybC5zZWN1cmV0cnVzdC5jb20vU1RDQS5jcmwwEAYJKwYBBAGC
NxUBBAMCAQAwDQYJKoZIhvcNAQEFBQADggEBADDtT0rhWDpSclu1pqNlGKa7UTt3
6Z3q059c4EVlew3KW+JwULKUBRSuSceNQQcSc5R+DCMh/bwQf2AQWnL1mA6s7Ll/
3XpvXdMc9P+IBWlCqQVxyLesJugutIxq/3HcuLHfmbx8IVQr5Fiiu1cprp6poxkm
D5kuCLDv/WnPmRoJjeOnnyvJNjR7JLN4TJUXpAYmHrZkUjZfYGfZnMUFdAvnZyPS
CPyI6a6Lf+Ew9Dd+/cYy2i2eRDAwbO4H3tI0/NL/QPZL9GZGBlSm8jIKYyYwa5vR
3ItHuuG51WLQoqD0ZwV4KWMabwTW+MZMo5qxN7SN5ShLHZ4swrhovO0C7jE=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDvDCCAqSgAwIBAgIQB1YipOjUiolN9BPI8PjqpTANBgkqhkiG9w0BAQUFADBK
MQswCQYDVQQGEwJVUzEgMB4GA1UEChMXU2VjdXJlVHJ1c3QgQ29ycG9yYXRpb24x
GTAXBgNVBAMTEFNlY3VyZSBHbG9iYWwgQ0EwHhcNMDYxMTA3MTk0MjI4WhcNMjkx
MjMxMTk1MjA2WjBKMQswCQYDVQQGEwJVUzEgMB4GA1UEChMXU2VjdXJlVHJ1c3Qg
Q29ycG9yYXRpb24xGTAXBgNVBAMTEFNlY3VyZSBHbG9iYWwgQ0EwggEiMA0GCSqG
SIb3DQEBAQUAA4IBDwAwggEKAoIBAQCvNS7YrGxVaQZx5RNoJLNP2MwhR/jxYDiJ
iQPpvepeRlMJ3Fz1Wuj3RSoC6zFh1ykzTM7HfAo3fg+6MpjhHZevj8fcyTiW89sa
/FHtaMbQbqR8JNGuQsiWUGMu4P51/pinX0kuleM5M2SOHqRfkNJnPLLZ/kG5VacJ
jnIFHovdRIWCQtBJwB1g8NEXLJXr9qXBkqPFwqcIYA1gBBCWeZ4WNOaptvolRTnI
HmX5k/Wq8VLcmZg9pYYaDDUz+kulBAYVHDGA76oYa8J719rO+TMg1fW9ajMtgQT7
sFzUnKPiXB3jqUJ1XnvUd+85VLrJChgbEplJL4hL/VBi0XPnj3pDAgMBAAGjgZ0w
gZowEwYJKwYBBAGCNxQCBAYeBABDAEEwCwYDVR0PBAQDAgGGMA8GA1UdEwEB/wQF
MAMBAf8wHQYDVR0OBBYEFK9EBMJBfkiD2045AuzshHrmzsmkMDQGA1UdHwQtMCsw
KaAnoCWGI2h0dHA6Ly9jcmwuc2VjdXJldHJ1c3QuY29tL1NHQ0EuY3JsMBAGCSsG
AQQBgjcVAQQDAgEAMA0GCSqGSIb3DQEBBQUAA4IBAQBjGghAfaReUw132HquHw0L
URYD7xh8yOOvaliTFGCRsoTciE6+OYo68+aCiV0BN7OrJKQVDpI1WkpEXk5X+nXO
H0jOZvQ8QCaSmGwb7iRGDBezUqXbpZGRzzfTb+cnCDpOGR86p1hcF895P4vkp9Mm
I50mD1hp/Ed+stCNi5O/KU9DaXR2Z0vPB4zmAve14bRDtUstFJ/53CYNv6ZHdAbY
iNE6KTCEztI5gGIbqMdXSbxqVVFnFUq+NQfk1XWYN3kwFNspnWzFacxHVaIw98xc
f8LDmBxrThaA63p4ZUWiABqvDA1VZDRIuJK58bRQKfJPIx/abKwfROHdI3hRW8cW
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEHTCCAwWgAwIBAgIQToEtioJl4AsC7j41AkblPTANBgkqhkiG9w0BAQUFADCB
gTELMAkGA1UEBhMCR0IxGzAZBgNVBAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4G
A1UEBxMHU2FsZm9yZDEaMBgGA1UEChMRQ09NT0RPIENBIExpbWl0ZWQxJzAlBgNV
BAMTHkNPTU9ETyBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTAeFw0wNjEyMDEwMDAw
MDBaFw0yOTEyMzEyMzU5NTlaMIGBMQswCQYDVQQGEwJHQjEbMBkGA1UECBMSR3Jl
YXRlciBNYW5jaGVzdGVyMRAwDgYDVQQHEwdTYWxmb3JkMRowGAYDVQQKExFDT01P
RE8gQ0EgTGltaXRlZDEnMCUGA1UEAxMeQ09NT0RPIENlcnRpZmljYXRpb24gQXV0
aG9yaXR5MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0ECLi3LjkRv3
UcEbVASY06m/weaKXTuH+7uIzg3jLz8GlvCiKVCZrts7oVewdFFxze1CkU1B/qnI
2GqGd0S7WWaXUF601CxwRM/aN5VCaTwwxHGzUvAhTaHYujl8HJ6jJJ3ygxaYqhZ8
Q5sVW7euNJH+1GImGEaaP+vB+fGQV+useg2L23IwambV4EajcNxo2f8ESIl33rXp
+2dtQem8Ob0y2WIC8bGoPW43nOIv4tOiJovGuFVDiOEjPqXSJDlqR6sA1KGzqSX+
DT+nHbrTUcELpNqsOO9VUCQFZUaTNE8tja3G1CEZ0o7KBWFxB3NH5YoZEr0ETc5O
nKVIrLsm9wIDAQABo4GOMIGLMB0GA1UdDgQWBBQLWOWLxkwVN6RAqTCpIb5HNlpW
/zAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zBJBgNVHR8EQjBAMD6g
PKA6hjhodHRwOi8vY3JsLmNvbW9kb2NhLmNvbS9DT01PRE9DZXJ0aWZpY2F0aW9u
QXV0aG9yaXR5LmNybDANBgkqhkiG9w0BAQUFAAOCAQEAPpiem/Yb6dc5t3iuHXIY
SdOH5EOC6z/JqvWote9VfCFSZfnVDeFs9D6Mk3ORLgLETgdxb8CPOGEIqB6BCsAv
IC9Bi5HcSEW88cbeunZrM8gALTFGTO3nnc+IlP8zwFboJIYmuNg4ON8qa90SzMc/
RxdMosIGlgnW2/4/PEZB31jiVg88O8EckzXZOFKs7sjsLjBOlDW0JB9LeGna8gI4
zJVSk/BwJVmcIGfE7vmLV2H0knZ9P4SNVbfo5azV8fUZVqZa+5Acr5Pr5RzUZ5dd
BA6+C4OmF4O5MBKgxTMVBbkN+8cFduPYSo38NBejxiEovjBFMR7HeL5YYTisO+IB
ZQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICiTCCAg+gAwIBAgIQH0evqmIAcFBUTAGem2OZKjAKBggqhkjOPQQDAzCBhTEL
MAkGA1UEBhMCR0IxGzAZBgNVBAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UE
BxMHU2FsZm9yZDEaMBgGA1UEChMRQ09NT0RPIENBIExpbWl0ZWQxKzApBgNVBAMT
IkNPTU9ETyBFQ0MgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMDgwMzA2MDAw
MDAwWhcNMzgwMTE4MjM1OTU5WjCBhTELMAkGA1UEBhMCR0IxGzAZBgNVBAgTEkdy
ZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEaMBgGA1UEChMRQ09N
T0RPIENBIExpbWl0ZWQxKzApBgNVBAMTIkNPTU9ETyBFQ0MgQ2VydGlmaWNhdGlv
biBBdXRob3JpdHkwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAAQDR3svdcmCFYX7deSR
FtSrYpn1PlILBs5BAH+X4QokPB0BBO490o0JlwzgdeT6+3eKKvUDYEs2ixYjFq0J
cfRK9ChQtP6IHG4/bC8vCVlbpVsLM5niwz2J+Wos77LTBumjQjBAMB0GA1UdDgQW
BBR1cacZSBm8nZ3qQUfflMRId5nTeTAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/
BAUwAwEB/zAKBggqhkjOPQQDAwNoADBlAjEA7wNbeqy3eApyt4jf/7VGFAkK+qDm
fQjGGoe9GKhzvSbKYAydzpmfz1wPMOG+FDHqAjAU9JM8SaczepBGR7NjfRObTrdv
GDeAU/7dIOA1mjbRxwG55tzd8/8dLDoWV9mSOdY=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDqDCCApCgAwIBAgIJAP7c4wEPyUj/MA0GCSqGSIb3DQEBBQUAMDQxCzAJBgNV
BAYTAkZSMRIwEAYDVQQKDAlEaGlteW90aXMxETAPBgNVBAMMCENlcnRpZ25hMB4X
DTA3MDYyOTE1MTMwNVoXDTI3MDYyOTE1MTMwNVowNDELMAkGA1UEBhMCRlIxEjAQ
BgNVBAoMCURoaW15b3RpczERMA8GA1UEAwwIQ2VydGlnbmEwggEiMA0GCSqGSIb3
DQEBAQUAA4IBDwAwggEKAoIBAQDIaPHJ1tazNHUmgh7stL7qXOEm7RFHYeGifBZ4
QCHkYJ5ayGPhxLGWkv8YbWkj4Sti993iNi+RB7lIzw7sebYs5zRLcAglozyHGxny
gQcPOJAZ0xH+hrTy0V4eHpbNgGzOOzGTtvKg0KmVEn2lmsxryIRWijOp5yIVUxbw
zBfsV1/pogqYCd7jX5xv3EjjhQsVWqa6n6xI4wmy9/Qy3l40vhx4XUJbzg4ij02Q
130yGLMLLGq/jj8UEYkgDncUtT2UCIf3JR7VsmAA7G8qKCVuKj4YYxclPz5EIBb2
JsglrgVKtOdjLPOMFlN+XPsRGgjBRmKfIrjxwo1p3Po6WAbfAgMBAAGjgbwwgbkw
DwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUGu3+QTmQtCRZvgHyUtVF9lo53BEw
ZAYDVR0jBF0wW4AUGu3+QTmQtCRZvgHyUtVF9lo53BGhOKQ2MDQxCzAJBgNVBAYT
AkZSMRIwEAYDVQQKDAlEaGlteW90aXMxETAPBgNVBAMMCENlcnRpZ25hggkA/tzj
AQ/JSP8wDgYDVR0PAQH/BAQDAgEGMBEGCWCGSAGG+EIBAQQEAwIABzANBgkqhkiG
9w0BAQUFAAOCAQEAhQMeknH2Qq/ho2Ge6/PAD/Kl1NqV5ta+aDY9fm4fTIrv0Q8h
bV6lUmPOEvjvKtpv6zf+EwLHyzs+ImvaYS5/1HI93TDhHkxAGYwP15zRgzB7mFnc
fca5DClMoTOi62c6ZYTTluLtdkVwj7Ur3vkj1kluPBS1xp81HlDQwY9qcEQCYsuu
HWhBp6pX6FOqB9IG9tUUBguRA3UsbHK1YZWaDYu5Def131TN3ubY1gkIl2PlwS6w
t0QmwCbAr1UwnjvVNioZBPRcHv/PLLf/0P2HQBHVESO7SMAhqaQoLf0V+LBOK/Qw
WyH8EZE0vkHve52Xdf+XlcCWWC/qu0bXu+TZLg==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFsDCCA5igAwIBAgIQFci9ZUdcr7iXAF7kBtK8nTANBgkqhkiG9w0BAQUFADBe
MQswCQYDVQQGEwJUVzEjMCEGA1UECgwaQ2h1bmdod2EgVGVsZWNvbSBDby4sIEx0
ZC4xKjAoBgNVBAsMIWVQS0kgUm9vdCBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTAe
Fw0wNDEyMjAwMjMxMjdaFw0zNDEyMjAwMjMxMjdaMF4xCzAJBgNVBAYTAlRXMSMw
IQYDVQQKDBpDaHVuZ2h3YSBUZWxlY29tIENvLiwgTHRkLjEqMCgGA1UECwwhZVBL
SSBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5MIICIjANBgkqhkiG9w0BAQEF
AAOCAg8AMIICCgKCAgEA4SUP7o3biDN1Z82tH306Tm2d0y8U82N0ywEhajfqhFAH
SyZbCUNsIZ5qyNUD9WBpj8zwIuQf5/dqIjG3LBXy4P4AakP/h2XGtRrBp0xtInAh
ijHyl3SJCRImHJ7K2RKilTza6We/CKBk49ZCt0Xvl/T29de1ShUCWH2YWEtgvM3X
DZoTM1PRYfl61dd4s5oz9wCGzh1NlDivqOx4UXCKXBCDUSH3ET00hl7lSM2XgYI1
TBnsZfZrxQWh7kcT1rMhJ5QQCtkkO7q+RBNGMD+XPNjX12ruOzjjK9SXDrkb5wdJ
fzcq+Xd4z1TtW0ado4AOkUPB1ltfFLqfpo0kR0BZv3I4sjZsN/+Z0V0OWQqraffA
sgRFelQArr5T9rXn4fg8ozHSqf4hUmTFpmfwdQcGlBSBVcYn5AGPF8Fqcde+S/uU
WH1+ETOxQvdibBjWzwloPn9s9h6PYq2lY9sJpx8iQkEeb5mKPtf5P0B6ebClAZLS
nT0IFaUQAS2zMnaolQ2zepr7BxB4EW/hj8e6DyUadCrlHJhBmd8hh+iVBmoKs2pH
dmX2Os+PYhcZewoozRrSgx4hxyy/vv9haLdnG7t4TY3OZ+XkwY63I2binZB1NJip
NiuKmpS5nezMirH4JYlcWrYvjB9teSSnUmjDhDXiZo1jDiVN1Rmy5nk3pyKdVDEC
AwEAAaNqMGgwHQYDVR0OBBYEFB4M97Zn8uGSJglFwFU5Lnc/QkqiMAwGA1UdEwQF
MAMBAf8wOQYEZyoHAAQxMC8wLQIBADAJBgUrDgMCGgUAMAcGBWcqAwAABBRFsMLH
ClZ87lt4DJX5GFPBphzYEDANBgkqhkiG9w0BAQUFAAOCAgEACbODU1kBPpVJufGB
uvl2ICO1J2B01GqZNF5sAFPZn/KmsSQHRGoqxqWOeBLoR9lYGxMqXnmbnwoqZ6Yl
PwZpVnPDimZI+ymBV3QGypzqKOg4ZyYr8dW1P2WT+DZdjo2NQCCHGervJ8A9tDkP
JXtoUHRVnAxZfVo9QZQlUgjgRywVMRnVvwdVxrsStZf0X4OFunHB2WyBEXYKCrC/
gpf36j36+uwtqSiUO1bd0lEursC9CBWMd1I0ltabrNMdjmEPNXubrjlpC2JgQCA2
j6/7Nu4tCEoduL+bXPjqpRugc6bY+G7gMwRfaKonh+3ZwZCc7b3jajWvY9+rGNm6
5ulK6lCKD2GTHuItGeIwlDWSXQ62B68ZgI9HkFFLLk3dheLSClIKF5r8GrBQAuUB
o2M3IUxExJtRmREOc5wGj1QupyheRDmHVi03vYVElOEMSyycw5KFNGHLD7ibSkNS
/jQ6fbjpKdx2qcgw+BRxgMYeNkh0IkFch4LoGHGLQYlE535YW6i4jRPpp2zDR+2z
Gp1iro2C6pSe3VkQw63d4k3jMdXH7OjysP6SHhYKGvzZ8/gntsm+HbRsZJB/9OTE
W9c3rkIO3aQab3yIVMUWbuF6aC74Or8NpDyJO3inTmODBCEIZ43ygknQW/2xzQ+D
hNQ+IIX3Sj0rnP0qCglN6oH4EZw=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDODCCAiCgAwIBAgIGIAYFFnACMA0GCSqGSIb3DQEBBQUAMDsxCzAJBgNVBAYT
AlJPMREwDwYDVQQKEwhjZXJ0U0lHTjEZMBcGA1UECxMQY2VydFNJR04gUk9PVCBD
QTAeFw0wNjA3MDQxNzIwMDRaFw0zMTA3MDQxNzIwMDRaMDsxCzAJBgNVBAYTAlJP
MREwDwYDVQQKEwhjZXJ0U0lHTjEZMBcGA1UECxMQY2VydFNJR04gUk9PVCBDQTCC
ASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALczuX7IJUqOtdu0KBuqV5Do
0SLTZLrTk+jUrIZhQGpgV2hUhE28alQCBf/fm5oqrl0Hj0rDKH/v+yv6efHHrfAQ
UySQi2bJqIirr1qjAOm+ukbuW3N7LBeCgV5iLKECZbO9xSsAfsT8AzNXDe3i+s5d
RdY4zTW2ssHQnIFKquSyAVwdj1+ZxLGt24gh65AIgoDzMKND5pCCrlUoSe1b16kQ
OA7+j0xbm0bqQfWwCHTD0IgztnzXdN/chNFDDnU5oSVAKOp4yw4sLjmdjItuFhwv
JoIQ4uNllAoEwF73XVv4EOLQunpL+943AAAaWyjj0pxzPjKHmKHJUS/X3qwzs08C
AwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAcYwHQYDVR0O
BBYEFOCMm9slSbPxfIbWskKHC9BroNnkMA0GCSqGSIb3DQEBBQUAA4IBAQA+0hyJ
LjX8+HXd5n9liPRyTMks1zJO890ZeUe9jjtbkw9QSSQTaxQGcu8J06Gh40CEyecY
MnQ8SG4Pn0vU9x7Tk4ZkVJdjclDVVc/6IJMCopvDI5NOFlV2oHB5bc0hH88vLbwZ
44gx+FkagQnIl6Z0x2DEW8xXjrJ1/RsCCdtZb3KTafcxQdaIOL+Hsr0Wefmq5L6I
Jd1hJyMctTEHBDa0GpC9oHRxUIltvBTjD4au8as+x6AJzKNI0eDbZOeStc+vckNw
i/nDhDwTqn6Sm1dTk/pwwpEOMfmbZ13pljheX7NzTogVZ96edhBiIL5VaZVDADlN
9u6wWk5JRFRYX0KD
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEFTCCAv2gAwIBAgIGSUEs5AAQMA0GCSqGSIb3DQEBCwUAMIGnMQswCQYDVQQG
EwJIVTERMA8GA1UEBwwIQnVkYXBlc3QxFTATBgNVBAoMDE5ldExvY2sgS2Z0LjE3
MDUGA1UECwwuVGFuw7pzw610dsOhbnlraWFkw7NrIChDZXJ0aWZpY2F0aW9uIFNl
cnZpY2VzKTE1MDMGA1UEAwwsTmV0TG9jayBBcmFueSAoQ2xhc3MgR29sZCkgRsWR
dGFuw7pzw610dsOhbnkwHhcNMDgxMjExMTUwODIxWhcNMjgxMjA2MTUwODIxWjCB
pzELMAkGA1UEBhMCSFUxETAPBgNVBAcMCEJ1ZGFwZXN0MRUwEwYDVQQKDAxOZXRM
b2NrIEtmdC4xNzA1BgNVBAsMLlRhbsO6c8OtdHbDoW55a2lhZMOzayAoQ2VydGlm
aWNhdGlvbiBTZXJ2aWNlcykxNTAzBgNVBAMMLE5ldExvY2sgQXJhbnkgKENsYXNz
IEdvbGQpIEbFkXRhbsO6c8OtdHbDoW55MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A
MIIBCgKCAQEAxCRec75LbRTDofTjl5Bu0jBFHjzuZ9lk4BqKf8owyoPjIMHj9DrT
lF8afFttvzBPhCf2nx9JvMaZCpDyD/V/Q4Q3Y1GLeqVw/HpYzY6b7cNGbIRwXdrz
AZAj/E4wqX7hJ2Pn7WQ8oLjJM2P+FpD/sLj916jAwJRDC7bVWaaeVtAkH3B5r9s5
VA1lddkVQZQBr17s9o3x/61k/iCa11zr/qYfCGSji3ZVrR47KGAuhyXoqq8fxmRG
ILdwfzzeSNuWU7c5d+Qa4scWhHaXWy+7GRWF+GmF9ZmnqfI0p6m2pgP8b4Y9VHx2
BJtr+UBdADTHLpl1neWIA6pN+APSQnbAGwIDAKiLo0UwQzASBgNVHRMBAf8ECDAG
AQH/AgEEMA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUzPpnk/C2uNClwB7zU/2M
U9+D15YwDQYJKoZIhvcNAQELBQADggEBAKt/7hwWqZw8UQCgwBEIBaeZ5m8BiFRh
bvG5GK1Krf6BQCOUL/t1fC8oS2IkgYIL9WHxHG64YTjrgfpioTtaYtOUZcTh5m2C
+C8lcLIhJsFyUR+MLMOEkMNaj7rP9KdlpeuY0fsFskZ1FSNqb4VjMIDw1Z4fKRzC
bLBQWV2QWzuoDTDPv31/zvGdg73JRm4gpvlhUbohL3u+pRVjodSVh/GeufOJ8z2F
uLjbvrW5KfnaNwUASZQDhETnv0Mxz3WLJdH0pmT1kvarBes96aULNmLazAZfNou2
XjG4Kvte9nHfRCaexOYNkbQudZWAUWpLMKawYqGT8ZvYzsRjdT9ZR7E=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIECjCCAvKgAwIBAgIJAMJ+QwRORz8ZMA0GCSqGSIb3DQEBCwUAMIGCMQswCQYD
VQQGEwJIVTERMA8GA1UEBwwIQnVkYXBlc3QxFjAUBgNVBAoMDU1pY3Jvc2VjIEx0
ZC4xJzAlBgNVBAMMHk1pY3Jvc2VjIGUtU3ppZ25vIFJvb3QgQ0EgMjAwOTEfMB0G
CSqGSIb3DQEJARYQaW5mb0BlLXN6aWduby5odTAeFw0wOTA2MTYxMTMwMThaFw0y
OTEyMzAxMTMwMThaMIGCMQswCQYDVQQGEwJIVTERMA8GA1UEBwwIQnVkYXBlc3Qx
FjAUBgNVBAoMDU1pY3Jvc2VjIEx0ZC4xJzAlBgNVBAMMHk1pY3Jvc2VjIGUtU3pp
Z25vIFJvb3QgQ0EgMjAwOTEfMB0GCSqGSIb3DQEJARYQaW5mb0BlLXN6aWduby5o
dTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAOn4j/NjrdqG2KfgQvvP
kd6mJviZpWNwrZuuyjNAfW2WbqEORO7hE52UQlKavXWFdCyoDh2Tthi3jCyoz/tc
cbna7P7ofo/kLx2yqHWH2Leh5TvPmUpG0IMZfcChEhyVbUr02MelTTMuhTlAdX4U
fIASmFDHQWe4oIBhVKZsTh/gnQ4H6cm6M+f+wFUoLAKApxn1ntxVUwOXewdI/5n7
N4okxFnMUBBjjqqpGrCEGob5X7uxUG6k0QrM1XF+H6cbfPVTbiJfyyvm1HxdrtbC
xkzlBQHZ7Vf8wSN5/PrIJIOV87VqUQHQd9bpEqH5GoP7ghu5sJf0dgYzQ0mg/wu1
+rUCAwEAAaOBgDB+MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMB0G
A1UdDgQWBBTLD8bfQkPMPcu1SCOhGnqmKrs0aDAfBgNVHSMEGDAWgBTLD8bfQkPM
Pcu1SCOhGnqmKrs0aDAbBgNVHREEFDASgRBpbmZvQGUtc3ppZ25vLmh1MA0GCSqG
SIb3DQEBCwUAA4IBAQDJ0Q5eLtXMs3w+y/w9/w0olZMEyL/azXm4Q5DwpL7v8u8h
mLzU1F0G9u5C7DBsoKqpyvGvivo/C3NqPuouQH4frlRheesuCDfXI/OMn74dseGk
ddug4lQUsbocKaQY9hK6ohQU4zE1yED/t+AFdlfBHFny+L/k7SViXITwfn4fs775
tyERzAMBVnCnEJIeGzSBHq2cGsMEPO0CYdYeBvNfOofyK/FFh+U9rNHHV4S9a67c
2Pm2G2JwCz02yULyMtd6YebS2z3PyKnJm9zbWETXbzivf3jTo60adbocwTZ8jx5t
HMN1Rq41Bab2XD0h7lbwyYIiLXpUq3DDfSJlgnCW
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDXzCCAkegAwIBAgILBAAAAAABIVhTCKIwDQYJKoZIhvcNAQELBQAwTDEgMB4G
A1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjMxEzARBgNVBAoTCkdsb2JhbFNp
Z24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMDkwMzE4MTAwMDAwWhcNMjkwMzE4
MTAwMDAwWjBMMSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSMzETMBEG
A1UEChMKR2xvYmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAMwldpB5BngiFvXAg7aEyiie/QV2EcWtiHL8
RgJDx7KKnQRfJMsuS+FggkbhUqsMgUdwbN1k0ev1LKMPgj0MK66X17YUhhB5uzsT
gHeMCOFJ0mpiLx9e+pZo34knlTifBtc+ycsmWQ1z3rDI6SYOgxXG71uL0gRgykmm
KPZpO/bLyCiR5Z2KYVc3rHQU3HTgOu5yLy6c+9C7v/U9AOEGM+iCK65TpjoWc4zd
QQ4gOsC0p6Hpsk+QLjJg6VfLuQSSaGjlOCZgdbKfd/+RFO+uIEn8rUAVSNECMWEZ
XriX7613t2Saer9fwRPvm2L7DWzgVGkWqQPabumDk3F2xmmFghcCAwEAAaNCMEAw
DgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFI/wS3+o
LkUkrk1Q+mOai97i3Ru8MA0GCSqGSIb3DQEBCwUAA4IBAQBLQNvAUKr+yAzv95ZU
RUm7lgAJQayzE4aGKAczymvmdLm6AC2upArT9fHxD4q/c2dKg8dEe3jgr25sbwMp
jjM5RcOO5LlXbKr8EpbsU8Yt5CRsuZRj+9xTaGdWPoO4zzUhw8lo/s7awlOqzJCK
6fBdRoyV3XpYKBovHd7NADdBj+1EbddTKJd+82cEHhXXipa0095MJ6RMG3NzdvQX
mcIfeg7jLQitChws/zyrVQ4PkX4268NXSb7hLi18YIvDQVETI53O9zJrlAGomecs
Mx86OyXShkDOOyyGeMlhLxS67ttVb9+E7gUJTb0o2HLO02JQZR7rkpeDMdmztcpH
WD9f
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF8TCCA9mgAwIBAgIQALC3WhZIX7/hy/WL1xnmfTANBgkqhkiG9w0BAQsFADA4
MQswCQYDVQQGEwJFUzEUMBIGA1UECgwLSVpFTlBFIFMuQS4xEzARBgNVBAMMCkl6
ZW5wZS5jb20wHhcNMDcxMjEzMTMwODI4WhcNMzcxMjEzMDgyNzI1WjA4MQswCQYD
VQQGEwJFUzEUMBIGA1UECgwLSVpFTlBFIFMuQS4xEzARBgNVBAMMCkl6ZW5wZS5j
b20wggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDJ03rKDx6sp4boFmVq
scIbRTJxldn+EFvMr+eleQGPicPK8lVx93e+d5TzcqQsRNiekpsUOqHnJJAKClaO
xdgmlOHZSOEtPtoKct2jmRXagaKH9HtuJneJWK3W6wyyQXpzbm3benhB6QiIEn6H
LmYRY2xU+zydcsC8Lv/Ct90NduM61/e0aL6i9eOBbsFGb12N4E3GVFWJGjMxCrFX
uaOKmMPsOzTFlUFpfnXCPCDFYbpRR6AgkJOhkEvzTnyFRVSa0QUmQbC1TR0zvsQD
yCV8wXDbO/QJLVQnSKwv4cSsPsjLkkxTOTcj7NMB+eAJRE1NZMDhDVqHIrytG6P+
JrUV86f8hBnp7KGItERphIPzidF0BqnMC9bC3ieFUCbKF7jJeodWLBoBHmy+E60Q
rLUk9TiRodZL2vG70t5HtfG8gfZZa88ZU+mNFctKy6lvROUbQc/hhqfK0GqfvEyN
BjNaooXlkDWgYlwWTvDjovoDGrQscbNYLN57C9saD+veIR8GdwYDsMnvmfzAuU8L
hij+0rnq49qlw0dpEuDb8PYZi+17cNcC1u2HGCgsBCRMd+RIihrGO5rUD8r6ddIB
QFqNeb+Lz0vPqhbBleStTIo+F5HUsWLlguWABKQDfo2/2n+iD5dPDNMN+9fR5XJ+
HMh3/1uaD7euBUbl8agW7EekFwIDAQABo4H2MIHzMIGwBgNVHREEgagwgaWBD2lu
Zm9AaXplbnBlLmNvbaSBkTCBjjFHMEUGA1UECgw+SVpFTlBFIFMuQS4gLSBDSUYg
QTAxMzM3MjYwLVJNZXJjLlZpdG9yaWEtR2FzdGVpeiBUMTA1NSBGNjIgUzgxQzBB
BgNVBAkMOkF2ZGEgZGVsIE1lZGl0ZXJyYW5lbyBFdG9yYmlkZWEgMTQgLSAwMTAx
MCBWaXRvcmlhLUdhc3RlaXowDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AQYwHQYDVR0OBBYEFB0cZQ6o8iV7tJHP5LGx5r1VdGwFMA0GCSqGSIb3DQEBCwUA
A4ICAQB4pgwWSp9MiDrAyw6lFn2fuUhfGI8NYjb2zRlrrKvV9pF9rnHzP7MOeIWb
laQnIUdCSnxIOvVFfLMMjlF4rJUT3sb9fbgakEyrkgPH7UIBzg/YsfqikuFgba56
awmqxinuaElnMIAkejEWOVt+8Rwu3WwJrfIxwYJOubv5vr8qhT/AQKM6WfxZSzwo
JNu0FXWuDYi6LnPAvViH5ULy617uHjAimcs30cQhbIHsvm0m5hzkQiCeR7Csg1lw
LDXWrzY0tM07+DKo7+N4ifuNRSzanLh+QBxh5z6ikixL8s36mLYp//Pye6kfLqCT
VyvehQP5aTfLnnhqBbTFMXiJ7HqnheG5ezzevh55hM6fcA5ZwjUukCox2eRFekGk
LhObNA5me0mrZJfQRsN5nXJQY6aYWwa9SG3YOYNw6DXwBdGqvOPbyALqfP2C2sJb
UjWumDqtujWTI6cfSN01RpiyEGjkpTHCClguGYEQyVB1/OpaFs4R1+7vUIgtYf8/
QnMFlEPVjjxOAToZpR9GTnfQXeWBIiGH/pR9hNiTrdZoQ0iy2+tzJOeRf1SktoA+
naM8THLCV8Sg1Mw4J87VBp6iSNnpn86CcDaTmjvfliHjWbcM2pE38P1ZWrOZyGls
QyYBNWNgVYkDOnXYukrZVP/u3oDYLdE41V4tC5h9Pmzb/CaIxw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDxTCCAq2gAwIBAgIBADANBgkqhkiG9w0BAQsFADCBgzELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxGjAYBgNVBAoT
EUdvRGFkZHkuY29tLCBJbmMuMTEwLwYDVQQDEyhHbyBEYWRkeSBSb290IENlcnRp
ZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5MDkwMTAwMDAwMFoXDTM3MTIzMTIz
NTk1OVowgYMxCzAJBgNVBAYTAlVTMRAwDgYDVQQIEwdBcml6b25hMRMwEQYDVQQH
EwpTY290dHNkYWxlMRowGAYDVQQKExFHb0RhZGR5LmNvbSwgSW5jLjExMC8GA1UE
AxMoR28gRGFkZHkgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgLSBHMjCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAL9xYgjx+lk09xvJGKP3gElY6SKD
E6bFIEMBO4Tx5oVJnyfq9oQbTqC023CYxzIBsQU+B07u9PpPL1kwIuerGVZr4oAH
/PMWdYA5UXvl+TW2dE6pjYIT5LY/qQOD+qK+ihVqf94Lw7YZFAXK6sOoBJQ7Rnwy
DfMAZiLIjWltNowRGLfTshxgtDj6AozO091GB94KPutdfMh8+7ArU6SSYmlRJQVh
GkSBjCypQ5Yj36w6gZoOKcUcqeldHraenjAKOc7xiID7S13MMuyFYkMlNAJWJwGR
tDtwKj9useiciAF9n9T521NtYJ2/LOdYq7hfRvzOxBsDPAnrSTFcaUaz4EcCAwEA
AaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYE
FDqahQcQZyi27/a9BUFuIMGU2g/eMA0GCSqGSIb3DQEBCwUAA4IBAQCZ21151fmX
WWcDYfF+OwYxdS2hII5PZYe096acvNjpL9DbWu7PdIxztDhC2gV7+AJ1uP2lsdeu
9tfeE8tTEH6KRtGX+rcuKxGrkLAngPnon1rpN5+r5N9ss4UXnT3ZJE95kTXWXwTr
gIOrmgIttRD02JDHBHNA7XIloKmf7J6raBKZV8aPEjoJpL1E/QYVN8Gb5DKj7Tjo
2GTzLH4U/ALqn83/B2gX2yKQOC16jdFU8WnjXzPKej17CuPKf1855eJ1usV2GDPO
LPAvTK33sefOT6jEm0pUBsV/fdUID+Ic/n4XuKxe9tQWskMJDE32p2u0mYRlynqI
4uJEvlz36hz1
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIID3TCCAsWgAwIBAgIBADANBgkqhkiG9w0BAQsFADCBjzELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxJTAjBgNVBAoT
HFN0YXJmaWVsZCBUZWNobm9sb2dpZXMsIEluYy4xMjAwBgNVBAMTKVN0YXJmaWVs
ZCBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5MDkwMTAwMDAw
MFoXDTM3MTIzMTIzNTk1OVowgY8xCzAJBgNVBAYTAlVTMRAwDgYDVQQIEwdBcml6
b25hMRMwEQYDVQQHEwpTY290dHNkYWxlMSUwIwYDVQQKExxTdGFyZmllbGQgVGVj
aG5vbG9naWVzLCBJbmMuMTIwMAYDVQQDEylTdGFyZmllbGQgUm9vdCBDZXJ0aWZp
Y2F0ZSBBdXRob3JpdHkgLSBHMjCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoC
ggEBAL3twQP89o/8ArFvW59I2Z154qK3A2FWGMNHttfKPTUuiUP3oWmb3ooa/RMg
nLRJdzIpVv257IzdIvpy3Cdhl+72WoTsbhm5iSzchFvVdPtrX8WJpRBSiUZV9Lh1
HOZ/5FSuS/hVclcCGfgXcVnrHigHdMWdSL5stPSksPNkN3mSwOxGXn/hbVNMYq/N
Hwtjuzqd+/x5AJhhdM8mgkBj87JyahkNmcrUDnXMN/uLicFZ8WJ/X7NfZTD4p7dN
dloedl40wOiWVpmKs/B/pM293DIxfJHP4F8R+GuqSVzRmZTRouNjWwl2tVZi4Ut0
HZbUJtQIBFnQmA4O5t78w+wfkPECAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAO
BgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFHwMMh+n2TB/xH1oo2Kooc6rB1snMA0G
CSqGSIb3DQEBCwUAA4IBAQARWfolTwNvlJk7mh+ChTnUdgWUXuEok21iXQnCoKjU
sHU48TRqneSfioYmUeYs0cYtbpUgSpIB7LiKZ3sx4mcujJUDJi5DnUox9g61DLu3
4jd/IroAow57UvtruzvE03lRTs2Q9GcHGcg8RnoNAX3FWOdt5oUwF5okxBDgBPfg
8n/Uqgr/Qh037ZTlZFkSIHc40zI+OIF1lnP6aI+xy84fxez6nH7PfrHxBy22/L/K
pL/QlwVKvOoYKAKQvVR4CSFx09F9HdkWsKlhPdAKACL8x3vLCWRFCztAgfd9fDL1
mMpYjn0q7pBZc2T5NnReJaH1ZgUufzkVqSr7UIuOhWn0
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIID7zCCAtegAwIBAgIBADANBgkqhkiG9w0BAQsFADCBmDELMAkGA1UEBhMCVVMx
EDAOBgNVBAgTB0FyaXpvbmExEzARBgNVBAcTClNjb3R0c2RhbGUxJTAjBgNVBAoT
HFN0YXJmaWVsZCBUZWNobm9sb2dpZXMsIEluYy4xOzA5BgNVBAMTMlN0YXJmaWVs
ZCBTZXJ2aWNlcyBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eSAtIEcyMB4XDTA5
MDkwMTAwMDAwMFoXDTM3MTIzMTIzNTk1OVowgZgxCzAJBgNVBAYTAlVTMRAwDgYD
VQQIEwdBcml6b25hMRMwEQYDVQQHEwpTY290dHNkYWxlMSUwIwYDVQQKExxTdGFy
ZmllbGQgVGVjaG5vbG9naWVzLCBJbmMuMTswOQYDVQQDEzJTdGFyZmllbGQgU2Vy
dmljZXMgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgLSBHMjCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBANUMOsQq+U7i9b4Zl1+OiFOxHz/Lz58gE20p
OsgPfTz3a3Y4Y9k2YKibXlwAgLIvWX/2h/klQ4bnaRtSmpDhcePYLQ1Ob/bISdm2
8xpWriu2dBTrz/sm4xq6HZYuajtYlIlHVv8loJNwU4PahHQUw2eeBGg6345AWh1K
Ts9DkTvnVtYAcMtS7nt9rjrnvDH5RfbCYM8TWQIrgMw0R9+53pBlbQLPLJGmpufe
hRhJfGZOozptqbXuNC66DQO4M99H67FrjSXZm86B0UVGMpZwh94CDklDhbZsc7tk
6mFBrMnUVN+HL8cisibMn1lUaJ/8viovxFUcdUBgF4UCVTmLfwUCAwEAAaNCMEAw
DwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFJxfAN+q
AdcwKziIorhtSpzyEZGDMA0GCSqGSIb3DQEBCwUAA4IBAQBLNqaEd2ndOxmfZyMI
bw5hyf2E3F/YNoHN2BtBLZ9g3ccaaNnRbobhiCPPE95Dz+I0swSdHynVv/heyNXB
ve6SbzJ08pGCL72CQnqtKrcgfU28elUSwhXqvfdqlS5sdJ/PHLTyxQGjhdByPq1z
qwubdQxtRbeOlKyWN7Wg0I8VRw7j6IPdj/3vQQF3zCepYoUz8jcI73HPdwbeyBkd
iEDPfUYd/x7H4c7/I9vG+o1VTqkC50cRRj70/b17KSa7qWFiNyi2LSr2EIZkyXCn
0q23KXB56jzaYyWf/Wi3MOxw+3WKt21gZ7IeyLnp2KhvAotnDU0mV3HaIPzBSlCN
sSi6
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDTDCCAjSgAwIBAgIId3cGJyapsXwwDQYJKoZIhvcNAQELBQAwRDELMAkGA1UE
BhMCVVMxFDASBgNVBAoMC0FmZmlybVRydXN0MR8wHQYDVQQDDBZBZmZpcm1UcnVz
dCBDb21tZXJjaWFsMB4XDTEwMDEyOTE0MDYwNloXDTMwMTIzMTE0MDYwNlowRDEL
MAkGA1UEBhMCVVMxFDASBgNVBAoMC0FmZmlybVRydXN0MR8wHQYDVQQDDBZBZmZp
cm1UcnVzdCBDb21tZXJjaWFsMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKC
AQEA9htPZwcroRX1BiLLHwGy43NFBkRJLLtJJRTWzsO3qyxPxkEylFf6EqdbDuKP
Hx6GGaeqtS25Xw2Kwq+FNXkyLbscYjfysVtKPcrNcV/pQr6U6Mje+SJIZMblq8Yr
ba0F8PrVC8+a5fBQpIs7R6UjW3p6+DM/uO+Zl+MgwdYoic+U+7lF7eNAFxHUdPAL
MeIrJmqbTFeurCA+ukV6BfO9m2kVrn1OIGPENXY6BwLJN/3HR+7o8XYdcxXyl6S1
yHp52UKqK39c/s4mT6NmgTWvRLpUHhwwMmWd5jyTXlBOeuM61G7MGvv50jeuJCqr
VwMiKA1JdX+3KNp1v47j3A55MQIDAQABo0IwQDAdBgNVHQ4EFgQUnZPGU4teyq8/
nx4P5ZmVvCT2lI8wDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwDQYJ
KoZIhvcNAQELBQADggEBAFis9AQOzcAN/wr91LoWXym9e2iZWEnStB03TX8nfUYG
XUPGhi4+c7ImfU+TqbbEKpqrIZcUsd6M06uJFdhrJNTxFq7YpFzUf1GO7RgBsZNj
vbz4YYCanrHOQnDiqX0GJX0nof5v7LMeJNrjS1UaADs1tDvZ110w/YETifLCBivt
Z8SOyUOyXGsViQK8YvxO8rUzqrJv0wqiUOP2O+guRMLbZjipM1ZI8W0bM40NjD9g
N53Tym1+NH4Nn3J2ixufcv1SNUFFApYvHLKac0khsUlHRUe072o0EclNmsxZt9YC
nlpOZbWUrhvfKbAW8b8Angc6F2S1BLUjIZkKlTuXfO8=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDTDCCAjSgAwIBAgIIfE8EORzUmS0wDQYJKoZIhvcNAQEFBQAwRDELMAkGA1UE
BhMCVVMxFDASBgNVBAoMC0FmZmlybVRydXN0MR8wHQYDVQQDDBZBZmZpcm1UcnVz
dCBOZXR3b3JraW5nMB4XDTEwMDEyOTE0MDgyNFoXDTMwMTIzMTE0MDgyNFowRDEL
MAkGA1UEBhMCVVMxFDASBgNVBAoMC0FmZmlybVRydXN0MR8wHQYDVQQDDBZBZmZp
cm1UcnVzdCBOZXR3b3JraW5nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKC
AQEAtITMMxcua5Rsa2FSoOujz3mUTOWUgJnLVWREZY9nZOIG41w3SfYvm4SEHi3y
YJ0wTsyEheIszx6e/jarM3c1RNg1lho9Nuh6DtjVR6FqaYvZ/Ls6rnla1fTWcbua
kCNrmreIdIcMHl+5ni36q1Mr3Lt2PpNMCAiMHqIjHNRqrSK6mQEubWXLviRmVSRL
QESxG9fhwoXA3hA/Pe24/PHxI1Pcv2WXb9n5QHGNfb2V1M6+oF4nI979ptAmDgAp
6zxG8D1gvz9Q0twmQVGeFDdCBKNwV6gbh+0t+nvujArjqWaJGctB+d1ENmHP4ndG
yH329JKBNv3bNPFyfvMMFr20FQIDAQABo0IwQDAdBgNVHQ4EFgQUBx/S55zawm6i
QLSwelAQUHTEyL0wDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwDQYJ
KoZIhvcNAQEFBQADggEBAIlXshZ6qML91tmbmzTCnLQyFE2npN/svqe++EPbkTfO
tDIuUFUaNU52Q3Eg75N3ThVwLofDwR1t3Mu1J9QsVtFSUzpE0nPIxBsFZVpikpzu
QY0x2+c06lkh1QF612S4ZDnNye2v7UsDSKegmQGA3GWjNq5lWUhPgkvIZfFXHeVZ
Lgo/bNjR9eUJtGxUAArgFU2HdW23WJZa3W3SAKD0m0i+wzekujbgfIeFlxoVot4u
olu9rxj5kFDNcFn4J2dHy8egBzp90SxdbBk6ZrV9/ZFvgrG+CJPbFEfxojfHRZ48
x3evZKiT3/Zpg4Jg8klCNO1aAFSFHBY2kgxc+qatv9s=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFRjCCAy6gAwIBAgIIbYwURrGmCu4wDQYJKoZIhvcNAQEMBQAwQTELMAkGA1UE
BhMCVVMxFDASBgNVBAoMC0FmZmlybVRydXN0MRwwGgYDVQQDDBNBZmZpcm1UcnVz
dCBQcmVtaXVtMB4XDTEwMDEyOTE0MTAzNloXDTQwMTIzMTE0MTAzNlowQTELMAkG
A1UEBhMCVVMxFDASBgNVBAoMC0FmZmlybVRydXN0MRwwGgYDVQQDDBNBZmZpcm1U
cnVzdCBQcmVtaXVtMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAxBLf
qV/+Qd3d9Z+K4/as4Tx4mrzY8H96oDMq3I0gW64tb+eT2TZwamjPjlGjhVtnBKAQ
JG9dKILBl1fYSCkTtuG+kU3fhQxTGJoeJKJPj/CihQvL9Cl/0qRY7iZNyaqoe5rZ
+jjeRFcV5fiMyNlI4g0WJx0eyIOFJbe6qlVBzAMiSy2RjYvmia9mx+n/K+k8rNrS
s8PhaJyJ+HoAVt70VZVs+7pk3WKL3wt3MutizCaam7uqYoNMtAZ6MMgpv+0GTZe5
HMQxK9VfvFMSF5yZVylmd2EhMQcuJUmdGPLu8ytxjLW6OQdJd/zvLpKQBY0tL3d7
70O/Nbua2Plzpyzy0FfuKE4mX4+QaAkvuPjcBukumj5Rp9EixAqnOEhss/n/fauG
V+O61oV4d7pD6kh/9ti+I20ev9E2bFhc8e6kGVQa9QPSdubhjL08s9NIS+LI+H+S
qHZGnEJlPqQewQcDWkYtuJfzt9WyVSHvutxMAJf7FJUnM7/oQ0dG0giZFmA7mn7S
5u046uwBHjxIVkkJx0w3AJ6IDsBz4W9m6XJHMD4Q5QsDyZpCAGzFlH5hxIrff4Ia
C1nEWTJ3s7xgaVY5/bQGeyzWZDbZvUjthB9+pSKPKrhC9IK31FOQeE4tGv2Bb0TX
OwF0lkLgAOIua+rF7nKsu7/+6qqo+Nz2snmKtmcCAwEAAaNCMEAwHQYDVR0OBBYE
FJ3AZ6YMItkm9UWrpmVSESfYRaxjMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/
BAQDAgEGMA0GCSqGSIb3DQEBDAUAA4ICAQCzV00QYk465KzquByvMiPIs0laUZx2
KI15qldGF9X1Uva3ROgIRL8YhNILgM3FEv0AVQVhh0HctSSePMTYyPtwni94loMg
Nt58D2kTiKV1NpgIpsbfrM7jWNa3Pt668+s0QNiigfV4Py/VpfzZotReBA4Xrf5B
8OWycvpEgjNC6C1Y91aMYj+6QrCcDFx+LmUmXFNPALJ4fqENmS2NuB2OosSw/WDQ
MKSOyARiqcTtNd56l+0OOF6SL5Nwpamcb6d9Ex1+xghIsV5n61EIJenmJWtSKZGc
0jlzCFfemQa0W50QBuHCAKi4HEoCChTQwUHK+4w1IX2COPKpVJEZNZOUbWo6xbLQ
u4mGk+ibyQ86p3q4ofB4Rvr8Ny/lioTz3/4E2aFooC8k4gmVBtWVyuEklut89pMF
u+1z6S3RdTnX5yTb2E5fQ4+e0BQ5v1VwSJlXMbSc7kqYA5YwH2AG7hsj/oFgIxpH
YoWlzBk0gG+zrBrjn/B7SK3VAdlntqlyk+otZrWyuOQ9PLLvTIzq6we/qzWaVYa8
GKa1qF60g2xraUDTn9zxw2lrueFtCfTxqlB2Cnp9ehehVZZCmTEJ3WARjQUwfuaO
RtGdFNrHF+QFlozEJLUbzxQHskD4o55BhrwE0GuWyCqANP2/7waj3VjFhT0+j/6e
KeC2uAloGRwYQw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB/jCCAYWgAwIBAgIIdJclisc/elQwCgYIKoZIzj0EAwMwRTELMAkGA1UEBhMC
VVMxFDASBgNVBAoMC0FmZmlybVRydXN0MSAwHgYDVQQDDBdBZmZpcm1UcnVzdCBQ
cmVtaXVtIEVDQzAeFw0xMDAxMjkxNDIwMjRaFw00MDEyMzExNDIwMjRaMEUxCzAJ
BgNVBAYTAlVTMRQwEgYDVQQKDAtBZmZpcm1UcnVzdDEgMB4GA1UEAwwXQWZmaXJt
VHJ1c3QgUHJlbWl1bSBFQ0MwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAAQNMF4bFZ0D
0KF5Nbc6PJJ6yhUczWLznCZcBz3lVPqj1swS6vQUX+iOGasvLkjmrBhDeKzQN8O9
ss0s5kfiGuZjuD0uL3jET9v0D6RoTFVya5UdThhClXjMNzyR4ptlKymjQjBAMB0G
A1UdDgQWBBSaryl6wBE1NSZRMADDav5A1a7WPDAPBgNVHRMBAf8EBTADAQH/MA4G
A1UdDwEB/wQEAwIBBjAKBggqhkjOPQQDAwNnADBkAjAXCfOHiFBar8jAQr9HX/Vs
aobgxCd05DhT1wV/GzTjxi+zygk8N53X57hG8f2h4nECMEJZh0PUUd+60wkyWs6I
flc9nF9Ca/UHLbXwgpP5WW+uZPpY5Yse42O+tYHNbwKMeQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDuzCCAqOgAwIBAgIDBETAMA0GCSqGSIb3DQEBBQUAMH4xCzAJBgNVBAYTAlBM
MSIwIAYDVQQKExlVbml6ZXRvIFRlY2hub2xvZ2llcyBTLkEuMScwJQYDVQQLEx5D
ZXJ0dW0gQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkxIjAgBgNVBAMTGUNlcnR1bSBU
cnVzdGVkIE5ldHdvcmsgQ0EwHhcNMDgxMDIyMTIwNzM3WhcNMjkxMjMxMTIwNzM3
WjB+MQswCQYDVQQGEwJQTDEiMCAGA1UEChMZVW5pemV0byBUZWNobm9sb2dpZXMg
Uy5BLjEnMCUGA1UECxMeQ2VydHVtIENlcnRpZmljYXRpb24gQXV0aG9yaXR5MSIw
IAYDVQQDExlDZXJ0dW0gVHJ1c3RlZCBOZXR3b3JrIENBMIIBIjANBgkqhkiG9w0B
AQEFAAOCAQ8AMIIBCgKCAQEA4/t9o3K6wvDJFIf1awFO4W5AB7ptJ11/91sts1rH
UV+rpDKmYYe2bg+G0jACl/jXaVehGDldamR5xgFZrDwxSjh80gTSSyjoIF87B6LM
TXPb865Px1bVWqeWifrzq2jUI4ZZJ88JJ7ysbnKDHDBy3+Ci6dLhdHUZvSqeexVU
BBvXQzmtVSjF4hq79MDkrjhJM8x2hZ85RdKknvISjFH4fOQtf/WsX+sWn7Et0brM
kUJ3TCXJkDhv2/DM+44el1k+1WBO5gUo7Ul5E0u6SNsv+XLTOcr+H9g0cvW0QM8x
AcPs3hEtF10fuFDRXhmnad4HMyjKUJX5p1TLVIZQRan5SQIDAQABo0IwQDAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBQIds3LB/8k9sXN7buQvOKEN0Z19zAOBgNV
HQ8BAf8EBAMCAQYwDQYJKoZIhvcNAQEFBQADggEBAKaorSLOAT2mo/9i0Eidi15y
sHhE49wcrwn9I0j6vSrEuVUEtRCjjSfeC4Jj0O7eDDd5QVsisrCaQVymcODU0HfL
I9MA4GxWL+FpDQ3Zqr8hgVDZBqWo/5U30Kr+4rP1mS1FhIrlQgnXdAIv94nYmem8
J9RHjboNRhx3zxSkHLmkMcScKHQDNP8zGSal6Q10tz6XxnboJ5ajZt3hrvJBW8qY
VoNzcOSGGtIxQbovvi0TWnZvTuhOgQ4/WwMioBK+ZlgRSssDxLQqKi2WF+A5VLxI
03YnnZotBqbJ7DnSq9ufmgsnAjUpsUCV5/nonFWIGUbWtzT1fs45mtk48VH3Tyw=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDezCCAmOgAwIBAgIBATANBgkqhkiG9w0BAQUFADBfMQswCQYDVQQGEwJUVzES
MBAGA1UECgwJVEFJV0FOLUNBMRAwDgYDVQQLDAdSb290IENBMSowKAYDVQQDDCFU
V0NBIFJvb3QgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMDgwODI4MDcyNDMz
WhcNMzAxMjMxMTU1OTU5WjBfMQswCQYDVQQGEwJUVzESMBAGA1UECgwJVEFJV0FO
LUNBMRAwDgYDVQQLDAdSb290IENBMSowKAYDVQQDDCFUV0NBIFJvb3QgQ2VydGlm
aWNhdGlvbiBBdXRob3JpdHkwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIB
AQCwfnK4pAOU5qfeCTiRShFAh6d8WWQUe7UREN3+v9XAu1bihSX0NXIP+FPQQeFE
AcK0HMMxQhZHhTMidrIKbw/lJVBPhYa+v5guEGcevhEFhgWQxFnQfHgQsIBct+HH
K3XLfJ+utdGdIzdjp9xCoi2SBBtQwXu4PhvJVgSLL1KbralW6cH/ralYhzC2gfeX
RfwZVzsrb+RH9JlF/h3x+JejiB03HFyP4HYlmlD4oFT/RJB2I9IyxsOrBr/8+7/z
rX2SYgJbKdM1o5OaQ2RgXbL6Mv87BK9NQGr5x+PvI/1ry+UPizgN7gr8/g+YnzAx
3WxSZfmLgb4i4RxYA7qRG4kHAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBRqOFsmjd6LWvJPelSDGRjjCDWmujANBgkq
hkiG9w0BAQUFAAOCAQEAPNV3PdrfibqHDAhUaiBQkr6wQT25JmSDCi/oQMCXKCeC
MErJk/9q56YAf4lCmtYR5VPOL8zy2gXE/uJQxDqGfczafhAJO5I1KlOy/usrBdls
XebQ79NqZp4VKIV66IIArB6nCWlWQtNoURi+VJq/REG6Sb4gumlc7rh3zc5sH62D
lhh9DrUUOYTxKOkto557HnpyWoOzeW/vtPzQCqVYT0bf+215WfKEIlKuD8z7fDvn
aspHYcN6+NOSBB+4IIThNlQWx0DeO4pz3N/GCUzf7Nr/1FNCocnyYh0igzyXxfkZ
YiesZSLX0zzG5Y6yU8xJzrww/nsOM5D77dIUkR8Hrw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDdzCCAl+gAwIBAgIBADANBgkqhkiG9w0BAQsFADBdMQswCQYDVQQGEwJKUDEl
MCMGA1UEChMcU0VDT00gVHJ1c3QgU3lzdGVtcyBDTy4sTFRELjEnMCUGA1UECxMe
U2VjdXJpdHkgQ29tbXVuaWNhdGlvbiBSb290Q0EyMB4XDTA5MDUyOTA1MDAzOVoX
DTI5MDUyOTA1MDAzOVowXTELMAkGA1UEBhMCSlAxJTAjBgNVBAoTHFNFQ09NIFRy
dXN0IFN5c3RlbXMgQ08uLExURC4xJzAlBgNVBAsTHlNlY3VyaXR5IENvbW11bmlj
YXRpb24gUm9vdENBMjCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBANAV
OVKxUrO6xVmCxF1SrjpDZYBLx/KWvNs2l9amZIyoXvDjChz335c9S672XewhtUGr
zbl+dp+++T42NKA7wfYxEUV0kz1XgMX5iZnK5atq1LXaQZAQwdbWQonCv/Q4EpVM
VAX3NuRFg3sUZdbcDE3R3n4MqzvEFb46VqZab3ZpUql6ucjrappdUtAtCms1FgkQ
hNBqyjoGADdH5H5XTz+L62e4iKrFvlNVspHEfbmwhRkGeC7bYRr6hfVKkaHnFtWO
ojnflLhwHyg/i/xAXmODPIMqGplrz95Zajv8bxbXH/1KEOtOghY6rCcMU/Gt1SSw
awNQwS08Ft1ENCcadfsCAwEAAaNCMEAwHQYDVR0OBBYEFAqFqXdlBZh8QIH4D5cs
OPEK7DzPMA4GA1UdDwEB/wQEAwIBBjAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3
DQEBCwUAA4IBAQBMOqNErLlFsceTfsgLCkLfZOoc7llsCLqJX2rKSpWeeo8HxdpF
coJxDjrSzG+ntKEju/Ykn8sX/oymzsLS28yN/HH8AynBbF0zX2S2ZTuJbxh2ePXc
okgfGT+Ok+vx+hfuzU7jBBJV1uXk3fs+BXziHV7Gp7yXT2g69ekuCkO2r1dcYmh8
t/2jioSgrGK+KwmHNPBqAbubKVY8/gA3zyNs8U6qtnRGEmyR7jTV7JqR50S+kDFy
1UkC9gLl9B/rfNmWVan/7Ir5mUf/NVoCqgTLiluHcSmRvaS0eg29mvVXIwAHIRc/
SjnRBUkLp7Y3gaVdjKozXoEofKd9J+sAro03
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFuzCCA6OgAwIBAgIIVwoRl0LE48wwDQYJKoZIhvcNAQELBQAwazELMAkGA1UE
BhMCSVQxDjAMBgNVBAcMBU1pbGFuMSMwIQYDVQQKDBpBY3RhbGlzIFMucC5BLi8w
MzM1ODUyMDk2NzEnMCUGA1UEAwweQWN0YWxpcyBBdXRoZW50aWNhdGlvbiBSb290
IENBMB4XDTExMDkyMjExMjIwMloXDTMwMDkyMjExMjIwMlowazELMAkGA1UEBhMC
SVQxDjAMBgNVBAcMBU1pbGFuMSMwIQYDVQQKDBpBY3RhbGlzIFMucC5BLi8wMzM1
ODUyMDk2NzEnMCUGA1UEAwweQWN0YWxpcyBBdXRoZW50aWNhdGlvbiBSb290IENB
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAp8bEpSmkLO/lGMWwUKNv
UTufClrJwkg4CsIcoBh/kbWHuUA/3R1oHwiD1S0eiKD4j1aPbZkCkpAW1V8IbInX
4ay8IMKx4INRimlNAJZaby/ARH6jDuSRzVju3PvHHkVH3Se5CAGfpiEd9UEtL0z9
KK3giq0itFZljoZUj5NDKd45RnijMCO6zfB9E1fAXdKDa0hMxKufgFpbOr3JpyI/
gCczWw63igxdBzcIy2zSekciRDXFzMwujt0q7bd9Zg1fYVEiVRvjRuPjPdA1Yprb
rxTIW6HMiRvhMCb8oJsfgadHHwTrozmSBp+Z07/T6k9QnBn+locePGX2oxgkg4YQ
51Q+qDp2JE+BIcXjDwL4k5RHILv+1A7TaLndxHqEguNTVHnd25zS8gebLra8Pu2F
be8lEfKXGkJh90qX6IuxEAf6ZYGyojnP9zz/GPvG8VqLWeICrHuS0E4UT1lF9gxe
KF+w6D9Fz8+vm2/7hNN3WpVvrJSEnu68wEqPSpP4RCHiMUVhUE4Q2OM1fEwZtN4F
v6MGn8i1zeQf1xcGDXqVdFUNaBr8EBtiZJ1t4JWgw5QHVw0U5r0F+7if5t+L4sbn
fpb2U8WANFAoWPASUHEXMLrmeGO89LKtmyuy/uE5jF66CyCU3nuDuP/jVo23Eek7
jPKxwV2dpAtMK9myGPW1n0sCAwEAAaNjMGEwHQYDVR0OBBYEFFLYiDrIn3hm7Ynz
ezhwlMkCAjbQMA8GA1UdEwEB/wQFMAMBAf8wHwYDVR0jBBgwFoAUUtiIOsifeGbt
ifN7OHCUyQICNtAwDgYDVR0PAQH/BAQDAgEGMA0GCSqGSIb3DQEBCwUAA4ICAQAL
e3KHwGCmSUyIWOYdiPcUZEim2FgKDk8TNd81HdTtBjHIgT5q1d07GjLukD0R0i70
jsNjLiNmsGe+b7bAEzlgqqI0JZN1Ut6nna0Oh4lScWoWPBkdg/iaKWW+9D+a2fDz
WochcYBNy+A4mz+7+uAwTc+G02UQGRjRlwKxK3JCaKygvU5a2hi/a5iB0P2avl4V
SM0RFbnAKVy06Ij3Pjaut2L9HmLecHgQHEhb2rykOLpn7VU+Xlff1ANATIGk0k9j
pwlCCRT8AKnCgHNPLsBA2RF7SOp6AsDT6ygBJlh0wcBzIm2Tlf05fbsq4/aC4yyX
X04fkZT6/iyj2HYauE2yOE+b+h1IYHkm4vP9qdCa6HCPSXrW5b0KDtst842/6+Ok
fcvHlXHo2qN8xcL4dJIEG4aspCJTQLas/kx2z/uUMsA1n3Y/buWQbqCmJqK4LL7R
K4X9p2jIugErsWx0Hbhzlefut8cl8ABMALJ+tguLHPPAUJ4lueAI3jZm/zel0btU
ZCzJJ7VLkn5l/9Mt4blOvH+kQSGQQXemOR/qnuOf0GZvBeyqdn6/axag67XH/JJU
LysRJyU3eExRarDzzFhdFPFqSBX/wge2sY0PjlxQRrM9vwGYT7JZVEc+NHt4bVaT
LnPqZih4zR0Uv6CPLy64Lo7yFIrM6bV8+2ydDKXhlg==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFWTCCA0GgAwIBAgIBAjANBgkqhkiG9w0BAQsFADBOMQswCQYDVQQGEwJOTzEd
MBsGA1UECgwUQnV5cGFzcyBBUy05ODMxNjMzMjcxIDAeBgNVBAMMF0J1eXBhc3Mg
Q2xhc3MgMiBSb290IENBMB4XDTEwMTAyNjA4MzgwM1oXDTQwMTAyNjA4MzgwM1ow
TjELMAkGA1UEBhMCTk8xHTAbBgNVBAoMFEJ1eXBhc3MgQVMtOTgzMTYzMzI3MSAw
HgYDVQQDDBdCdXlwYXNzIENsYXNzIDIgUm9vdCBDQTCCAiIwDQYJKoZIhvcNAQEB
BQADggIPADCCAgoCggIBANfHXvfBB9R3+0Mh9PT1aeTuMgHbo4Yf5FkNuud1g1Lr
6hxhFUi7HQfKjK6w3Jad6sNgkoaCKHOcVgb/S2TwDCo3SbXlzwx87vFKu3MwZfPV
L4O2fuPn9Z6rYPnT8Z2SdIrkHJasW4DptfQxh6NR/Md+oW+OU3fUl8FVM5I+GC91
1K2GScuVr1QGbNgGE41b/+EmGVnAJLqBcXmQRFBoJJRfuLMR8SlBYaNByyM21cHx
MlAQTn/0hpPshNOOvEu/XAFOBz3cFIqUCqTqc/sLUegTBxj6DvEr0VQVfTzh97QZ
QmdiXnfgolXsttlpF9U6r0TtSsWe5HonfOV116rLJeffawrbD02TTqigzXsu8lkB
arcNuAeBfos4GzjmCleZPe4h6KP1DBbdi+w0jpwqHAAVF41og9JwnxgIzRFo1clr
Us3ERo/ctfPYV3Me6ZQ5BL/T3jjetFPsaRyifsSP5BtwrfKi+fv3FmRmaZ9JUaLi
FRhnBkp/1Wy1TbMz4GHrXb7pmA8y1x1LPC5aAVKRCfLf6o3YBkBjqhHk/sM3nhRS
P/TizPJhk9H9Z2vXUq6/aKtAQ6BXNVN48FP4YUIHZMbXb5tMOA1jrGKvNouicwoN
9SG9dKpN6nIDSdvHXx1iY8f93ZHsM+71bbRuMGjeyNYmsHVee7QHIJihdjK4TWxP
AgMBAAGjQjBAMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFMmAd+BikoL1Rpzz
uvdMw964o605MA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAgEAU18h
9bqwOlI5LJKwbADJ784g7wbylp7ppHR/ehb8t/W2+xUbP6umwHJdELFx7rxP462s
A20ucS6vxOOto70MEae0/0qyexAQH6dXQbLArvQsWdZHEIjzIVEpMMpghq9Gqx3t
OluwlN5E40EIosHsHdb9T7bWR9AUC8rmyrV7d35BH16Dx7aMOZawP5aBQW9gkOLo
+fsicdl9sz1Gv7SEr5AcD48Saq/v7h56rgJKihcrdv6sVIkkLE8/trKnToyokZf7
KcZ7XC25y2a2t6hbElGFtQl+Ynhw/qlqYLYdDnkM/crqJIByw5c/8nerQyIKx+u2
DISCLIBrQYoIwOula9+ZEsuK1V6ADJHgJgg2SMX6OBE1/yWDLfJ6v9r9jv6ly0Us
H8SIU653DtmadsWOLB2jutXsMq7Aqqz30XpN69QH4kj3Io6wpJ9qzo6ysmD0oyLQ
I+uUWnpp3Q+/QFesa1lQ2aOZ4W7+jQF5JyMV3pKdewlNWudLSDBaGOYKbeaP4NK7
5t98biGCwWg5TbSYWGZizEqQXsP6JwSxeRV0mcy+rSDeJmAc61ZRpqPq5KM/p/9h
3PFaTWwyI0PurKju7koSCTxdccK+efrCh2gdC/1cacwG0Jp9VJkqyTkaGa9LKkPz
Y11aWOIv4x3kqdbQCtCev9eBCfHJxyYNrJgWVqA=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFWTCCA0GgAwIBAgIBAjANBgkqhkiG9w0BAQsFADBOMQswCQYDVQQGEwJOTzEd
MBsGA1UECgwUQnV5cGFzcyBBUy05ODMxNjMzMjcxIDAeBgNVBAMMF0J1eXBhc3Mg
Q2xhc3MgMyBSb290IENBMB4XDTEwMTAyNjA4Mjg1OFoXDTQwMTAyNjA4Mjg1OFow
TjELMAkGA1UEBhMCTk8xHTAbBgNVBAoMFEJ1eXBhc3MgQVMtOTgzMTYzMzI3MSAw
HgYDVQQDDBdCdXlwYXNzIENsYXNzIDMgUm9vdCBDQTCCAiIwDQYJKoZIhvcNAQEB
BQADggIPADCCAgoCggIBAKXaCpUWUOOV8l6ddjEGMnqb8RB2uACatVI2zSRHsJ8Y
ZLya9vrVediQYkwiL944PdbgqOkcLNt4EemOaFEVcsfzM4fkoF0LXOBXByow9c3E
N3coTRiR5r/VUv1xLXA+58bEiuPwKAv0dpihi4dVsjoT/Lc+JzeOIuOoTyrvYLs9
tznDDgFHmV0ST9tD+leh7fmdvhFHJlsTmKtdFoqwNxxXnUX/iJY2v7vKB3tvh2PX
0DJq1l1sDPGzbjniazEuOQAnFN44wOwZZoYS6J1yFhNkUsepNxz9gjDthBgd9K5c
/3ATAOux9TN6S9ZV+AWNS2mw9bMoNlwUxFFzTWsL8TQH2xc519woe2v1n/MuwU8X
KhDzzMro6/1rqy6any2CbgTUUgGTLT2G/H783+9CHaZr77kgxve9oKeV/afmiSTY
zIw0bOIjL9kSGiG5VZFvC5F5GQytQIgLcOJ60g7YaEi7ghM5EFjp2CoHxhLbWNvS
O1UQRwUVZ2J+GGOmRj8JDlQyXr8NYnon74Do29lLBlo3WiXQCBJ31G8JUJc9yB3D
34xFMFbG02SrZvPAXpacw8Tvw3xrizp5f7NJzz3iiZ+gMEuFuZyUJHmPfWupRWgP
K9Dx2hzLabjKSWJtyNBjYt1gD1iqj6G8BaVmos8bdrKEZLFMOVLAMLrwjEsCsLa3
AgMBAAGjQjBAMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFEe4zf/lb+74suwv
Tg75JbCOPGvDMA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAgEAACAj
QTUEkMJAYmDv4jVM1z+s4jSQuKFvdvoWFqRINyzpkMLyPPgKn9iB5btb2iUspKdV
cSQy9sgL8rxq+JOssgfCX5/bzMiKqr5qb+FJEMwx14C7u8jYog5kV+qi9cKpMRXS
IGrs/CIBKM+GuIAeqcwRpTzyFrNHnfzSgCHEy9BHcEGhyoMZCCxt8l13nIoUE9Q2
HJLw5QY33KbmkJs4j1xrG0aGQ0JfPgEHU1RdZX33inOhmlRaHylDFCfChQ+1iHsa
O5S3HWCntZznKWlXWpuTekMwGwPXYshApqr8ZORK15FTAaggiG6cX0S5y2CBNOxv
033aSF/rtJC8LakcC6wc1aJoIIAE1vyxjy+7SjENSoYc6+I2KSb12tjE8nVhz36u
dmNKekBlk4f4HoCMhuWG1o8O/FMsYOgWYRqiPkN7zTlgVGr18okmAWiDSKIz6MkE
kbIRNBE+6tBDGR8Dk5AM/1E9V/RBbuHLoL7ryWPNbczk+DaqaJ3tvV2XcEQNtg41
3OEMXbugUZTLfhbrES+jkkXITHHZvMmZUldGL1DPvTVp9D0VzgalLA8+9oG6lLvD
u79leNKGef9JOxqDDPDeeOzI8k1MGt6CKfjBWtrt7uYnXuhF0J0cUahoq0Tj0Itq
4/g7u9xN12TyUb7mqqta6THuBrxzvxNiCp/HuZc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDwzCCAqugAwIBAgIBATANBgkqhkiG9w0BAQsFADCBgjELMAkGA1UEBhMCREUx
KzApBgNVBAoMIlQtU3lzdGVtcyBFbnRlcnByaXNlIFNlcnZpY2VzIEdtYkgxHzAd
BgNVBAsMFlQtU3lzdGVtcyBUcnVzdCBDZW50ZXIxJTAjBgNVBAMMHFQtVGVsZVNl
YyBHbG9iYWxSb290IENsYXNzIDMwHhcNMDgxMDAxMTAyOTU2WhcNMzMxMDAxMjM1
OTU5WjCBgjELMAkGA1UEBhMCREUxKzApBgNVBAoMIlQtU3lzdGVtcyBFbnRlcnBy
aXNlIFNlcnZpY2VzIEdtYkgxHzAdBgNVBAsMFlQtU3lzdGVtcyBUcnVzdCBDZW50
ZXIxJTAjBgNVBAMMHFQtVGVsZVNlYyBHbG9iYWxSb290IENsYXNzIDMwggEiMA0G
CSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQC9dZPwYiJvJK7genasfb3ZJNW4t/zN
8ELg63iIVl6bmlQdTQyK9tPPcPRStdiTBONGhnFBSivwKixVA9ZIw+A5OO3yXDw/
RLyTPWGrTs0NvvAgJ1gORH8EGoel15YUNpDQSXuhdfsaa3Ox+M6pCSzyU9XDFES4
hqX2iys52qMzVNn6chr3IhUciJFrf2blw2qAsCTz34ZFiP0Zf3WHHx+xGwpzJFu5
ZeAsVMhg02YXP+HMVDNzkQI6pn97djmiH5a2OK61yJN0HZ65tOVgnS9W0eDrXltM
EnAMbEQgqxHY9Bn20pxSN+f6tsIxO0rUFJmtxxr1XV/6B7h8DR/Wgx6zAgMBAAGj
QjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMB0GA1UdDgQWBBS1
A/d2O2GCahKqGFPrAyGUv/7OyjANBgkqhkiG9w0BAQsFAAOCAQEAVj3vlNW92nOy
WL6ukK2YJ5f+AbGwUgC4TeQbIXQbfsDuXmkqJa9c1h3a0nnJ85cp4IaH3gRZD/FZ
1GSFS5mvJQQeyUapl96Cshtwn5z2r3Ex3XsFpSzTucpH9sry9uetuUg/vBa3wW30
6gmv7PO15wWeph6KU1HWk4HMdJP2udqmJQV0eVp+QD6CSyYRMG7hP0HHRwA11fXT
91Q+gT3aSWqas+8QPebrb9HIIkfLzM8BMZLZGOMivgkeGj5asuRrDFR6fUNOuIml
e9eiPZaGzPImNC1qkp2aGtAw4l1OBLBfiyB+d8E9lYLRRpo7PHi4b6HQDWSieB4p
TpPDpFQUWw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEMzCCAxugAwIBAgIDCYPzMA0GCSqGSIb3DQEBCwUAME0xCzAJBgNVBAYTAkRF
MRUwEwYDVQQKDAxELVRydXN0IEdtYkgxJzAlBgNVBAMMHkQtVFJVU1QgUm9vdCBD
bGFzcyAzIENBIDIgMjAwOTAeFw0wOTExMDUwODM1NThaFw0yOTExMDUwODM1NTha
ME0xCzAJBgNVBAYTAkRFMRUwEwYDVQQKDAxELVRydXN0IEdtYkgxJzAlBgNVBAMM
HkQtVFJVU1QgUm9vdCBDbGFzcyAzIENBIDIgMjAwOTCCASIwDQYJKoZIhvcNAQEB
BQADggEPADCCAQoCggEBANOySs96R+91myP6Oi/WUEWJNTrGa9v+2wBoqOADER03
UAifTUpolDWzU9GUY6cgVq/eUXjsKj3zSEhQPgrfRlWLJ23DEE0NkVJD2IfgXU42
tSHKXzlABF9bfsyjxiupQB7ZNoTWSPOSHjRGICTBpFGOShrvUD9pXRl/RcPHAY9R
ySPocq60vFYJfxLLHLGvKZAKyVXMD9O0Gu1HNVpK7ZxzBCHQqr0ME7UAyiZsxGsM
lFqVlNpQmvH/pStmMaTJOKDfHR+4CS7zp+hnUquVH+BGPtikw8paxTGA6Eian5Rp
/hnd2HN8gcqW3o7tszIFZYQ05ub9VxC1X3a/L7AQDcUCAwEAAaOCARowggEWMA8G
A1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFP3aFMSfMN4hvR5COfyrYyNJ4PGEMA4G
A1UdDwEB/wQEAwIBBjCB0wYDVR0fBIHLMIHIMIGAoH6gfIZ6bGRhcDovL2RpcmVj
dG9yeS5kLXRydXN0Lm5ldC9DTj1ELVRSVVNUJTIwUm9vdCUyMENsYXNzJTIwMyUy
MENBJTIwMiUyMDIwMDksTz1ELVRydXN0JTIwR21iSCxDPURFP2NlcnRpZmljYXRl
cmV2b2NhdGlvbmxpc3QwQ6BBoD+GPWh0dHA6Ly93d3cuZC10cnVzdC5uZXQvY3Js
L2QtdHJ1c3Rfcm9vdF9jbGFzc18zX2NhXzJfMjAwOS5jcmwwDQYJKoZIhvcNAQEL
BQADggEBAH+X2zDI36ScfSF6gHDOFBJpiBSVYEQBrLLpME+bUMJm2H6NMLVwMeni
acfzcNsgFYbQDfC+rAF1hM5+n02/t2A7nPPKHeJeaNijnZflQGDSNiH+0LS4F9p0
o3/U37CYAqxva2ssJSRyoWXuJVrl5jLn8t+rSfrzkGkj2wTZ51xY/GXUl77M/C4K
zCUqNQT4YJEVdT1B/yMfGchs64JTBKbkTCJNjYy6zltz7GRUUG3RnFX7acM2w4y8
PIWmawomDeCTmGCufsYkl4phX5GOZpIJhzbNi5stPvZR1FDUWSi9g/LMKHtThm3Y
Johw1+qRzT65ysCQblrGXnRl11z+o+I=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEQzCCAyugAwIBAgIDCYP0MA0GCSqGSIb3DQEBCwUAMFAxCzAJBgNVBAYTAkRF
MRUwEwYDVQQKDAxELVRydXN0IEdtYkgxKjAoBgNVBAMMIUQtVFJVU1QgUm9vdCBD
bGFzcyAzIENBIDIgRVYgMjAwOTAeFw0wOTExMDUwODUwNDZaFw0yOTExMDUwODUw
NDZaMFAxCzAJBgNVBAYTAkRFMRUwEwYDVQQKDAxELVRydXN0IEdtYkgxKjAoBgNV
BAMMIUQtVFJVU1QgUm9vdCBDbGFzcyAzIENBIDIgRVYgMjAwOTCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAJnxhDRwui+3MKCOvXwEz75ivJn9gpfSegpn
ljgJ9hBOlSJzmY3aFS3nBfwZcyK3jpgAvDw9rKFs+9Z5JUut8Mxk2og+KbgPCdM0
3TP1YtHhzRnp7hhPTFiu4h7WDFsVWtg6uMQYZB7jM7K1iXdODL/ZlGsTl28So/6Z
qQTMFexgaDbtCHu39b+T7WYxg4zGcTSHThfqr4uRjRxWQa4iN1438h3Z0S0NL2lR
p75mpoo6Kr3HGrHhFPC+Oh25z1uxav60sUYgovseO3Dvk5h9jHOW8sXvhXCtKSb8
HgQ+HKDYD8tSg2J87otTlZCpV6LqYQXY+U3EJ/pure3511H3a6UCAwEAAaOCASQw
ggEgMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFNOUikxiEyoZLsyvcop9Ntea
HNxnMA4GA1UdDwEB/wQEAwIBBjCB3QYDVR0fBIHVMIHSMIGHoIGEoIGBhn9sZGFw
Oi8vZGlyZWN0b3J5LmQtdHJ1c3QubmV0L0NOPUQtVFJVU1QlMjBSb290JTIwQ2xh
c3MlMjAzJTIwQ0ElMjAyJTIwRVYlMjAyMDA5LE89RC1UcnVzdCUyMEdtYkgsQz1E
RT9jZXJ0aWZpY2F0ZXJldm9jYXRpb25saXN0MEagRKBChkBodHRwOi8vd3d3LmQt
dHJ1c3QubmV0L2NybC9kLXRydXN0X3Jvb3RfY2xhc3NfM19jYV8yX2V2XzIwMDku
Y3JsMA0GCSqGSIb3DQEBCwUAA4IBAQA07XtaPKSUiO8aEXUHL7P+PPoeUSbrh/Yp
3uDx1MYkCenBz1UbtDDZzhr+BlGmFaQt77JLvyAoJUnRpjZ3NOhk31KxEcdzes05
nsKtjHEh8lprr988TlWvsoRlFIm5d8sqMb7Po23Pb0iUMkZv53GMoKaEGTcH8gNF
CSuGdXzfX2lXANtu2KZyIktQ1HWYVt+3GP9DQ1CuekR78HlR10M9p9OB0/DJT7na
xpeG0ILD5EJt/rDiZE4OJudANCa1CInXCGNjOCd1HjPqbqjdn5lPdE2BiYBL3ZqX
KVwvvoFBuYz/6n1gBp7N1z3TLqMVvKjmJuVvw9y4AyHqnxbxLFS1
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFaTCCA1GgAwIBAgIJAJK4iNuwisFjMA0GCSqGSIb3DQEBCwUAMFIxCzAJBgNV
BAYTAlNLMRMwEQYDVQQHEwpCcmF0aXNsYXZhMRMwEQYDVQQKEwpEaXNpZyBhLnMu
MRkwFwYDVQQDExBDQSBEaXNpZyBSb290IFIyMB4XDTEyMDcxOTA5MTUzMFoXDTQy
MDcxOTA5MTUzMFowUjELMAkGA1UEBhMCU0sxEzARBgNVBAcTCkJyYXRpc2xhdmEx
EzARBgNVBAoTCkRpc2lnIGEucy4xGTAXBgNVBAMTEENBIERpc2lnIFJvb3QgUjIw
ggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCio8QACdaFXS1tFPbCw3Oe
NcJxVX6B+6tGUODBfEl45qt5WDza/3wcn9iXAng+a0EE6UG9vgMsRfYvZNSrXaNH
PWSb6WiaxswbP7q+sos0Ai6YVRn8jG+qX9pMzk0DIaPY0jSTVpbLTAwAFjxfGs3I
x2ymrdMxp7zo5eFm1tL7A7RBZckQrg4FY8aAamkw/dLukO8NJ9+flXP04SXabBbe
QTg06ov80egEFGEtQX6sx3dOy1FU+16SGBsEWmjGycT6txOgmLcRK7fWV8x8nhfR
yyX+hk4kLlYMeE2eARKmK6cBZW58Yh2EhN/qwGu1pSqVg8NTEQxzHQuyRpDRQjrO
QG6Vrf/GlK1ul4SOfW+eioANSW1z4nuSHsPzwfPrLgVv2RvPN3YEyLRa5Beny912
H9AZdugsBbPWnDTYltxhh5EF5EQIM8HauQhl1K6yNg3ruji6DOWbnuuNZt2Zz9aJ
QfYEkoopKW1rOhzndX0CcQ7zwOe9yxndnWCywmZgtrEE7snmhrmaZkCo5xHtgUUD
i/ZnWejBBhG93c+AAk9lQHhcR1DIm+YfgXvkRKhbhZri3lrVx/k6RGZL5DJUfORs
nLMOPReisjQS1n6yqEm70XooQL6iFh/f5DcfEXP7kAplQ6INfPgGAVUzfbANuPT1
rqVCV3w2EYx7XsQDnYx5nQIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1Ud
DwEB/wQEAwIBBjAdBgNVHQ4EFgQUtZn4r7CU9eMg1gqtzk5WpC5uQu0wDQYJKoZI
hvcNAQELBQADggIBACYGXnDnZTPIgm7ZnBc6G3pmsgH2eDtpXi/q/075KMOYKmFM
tCQSin1tERT3nLXK5ryeJ45MGcipvXrA1zYObYVybqjGom32+nNjf7xueQgcnYqf
GopTpti72TVVsRHFqQOzVju5hJMiXn7B9hJSi+osZ7z+Nkz1uM/Rs0mSO9MpDpkb
lvdhuDvEK7Z4bLQjb/D907JedR+Zlais9trhxTF7+9FGs9K8Z7RiVLoJ92Owk6Ka
+elSLotgEqv89WBW7xBci8QaQtyDW2QOy7W81k/BfDxujRNt+3vrMNDcTa/F1bal
TFtxyegxvug4BkihGuLq0t4SOVga/4AOgnXmt8kHbA7v/zjxmHHEt38OFdAlab0i
nSvtBfZGR6ztwPDUO+Ls7pZbkBNOHlY667DvlruWIxG68kOGdGSVyCh13x01utI3
gzhTODY7z2zp+WsO0PsE6E9312UBeIYMej4hYvF/Y3EMyZ9E26gnonW+boE+18Dr
G5gPcFw0sorMwIUY6256s/daoQe/qUKS82Ail+QUoQebTnbAjn39pCXHR+3/H3Os
zMOl6W8KjptlwlCFtaOgUxLMVYdh84GuEEZhvUQhuMI9dM9+JDX6HAcOmz0iyu8x
L4ysEr3vQCj8KWefshNPZiTEUxnpHikV7+ZtsH8tZ/3zbBt1RqPlShfppNcL
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIH0zCCBbugAwIBAgIIXsO3pkN/pOAwDQYJKoZIhvcNAQEFBQAwQjESMBAGA1UE
AwwJQUNDVlJBSVoxMRAwDgYDVQQLDAdQS0lBQ0NWMQ0wCwYDVQQKDARBQ0NWMQsw
CQYDVQQGEwJFUzAeFw0xMTA1MDUwOTM3MzdaFw0zMDEyMzEwOTM3MzdaMEIxEjAQ
BgNVBAMMCUFDQ1ZSQUlaMTEQMA4GA1UECwwHUEtJQUNDVjENMAsGA1UECgwEQUND
VjELMAkGA1UEBhMCRVMwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCb
qau/YUqXry+XZpp0X9DZlv3P4uRm7x8fRzPCRKPfmt4ftVTdFXxpNRFvu8gMjmoY
HtiP2Ra8EEg2XPBjs5BaXCQ316PWywlxufEBcoSwfdtNgM3802/J+Nq2DoLSRYWo
G2ioPej0RGy9ocLLA76MPhMAhN9KSMDjIgro6TenGEyxCQ0jVn8ETdkXhBilyNpA
lHPrzg5XPAOBOp0KoVdDaaxXbXmQeOW1tDvYvEyNKKGno6e6Ak4l0Squ7a4DIrhr
IA8wKFSVf+DuzgpmndFALW4ir50awQUZ0m/A8p/4e7MCQvtQqR0tkw8jq8bBD5L/
0KIV9VMJcRz/RROE5iZe+OCIHAr8Fraocwa48GOEAqDGWuzndN9wrqODJerWx5eH
k6fGioozl2A3ED6XPm4pFdahD9GILBKfb6qkxkLrQaLjlUPTAYVtjrs78yM2x/47
4KElB0iryYl0/wiPgL/AlmXz7uxLaL2diMMxs0Dx6M/2OLuc5NF/1OVYm3z61PMO
m3WR5LpSLhl+0fXNWhn8ugb2+1KoS5kE3fj5tItQo05iifCHJPqDQsGH+tUtKSpa
cXpkatcnYGMN285J9Y0fkIkyF/hzQ7jSWpOGYdbhdQrqeWZ2iE9x6wQl1gpaepPl
uUsXQA+xtrn13k/c4LOsOxFwYIRKQ26ZIMApcQrAZQIDAQABo4ICyzCCAscwfQYI
KwYBBQUHAQEEcTBvMEwGCCsGAQUFBzAChkBodHRwOi8vd3d3LmFjY3YuZXMvZmls
ZWFkbWluL0FyY2hpdm9zL2NlcnRpZmljYWRvcy9yYWl6YWNjdjEuY3J0MB8GCCsG
AQUFBzABhhNodHRwOi8vb2NzcC5hY2N2LmVzMB0GA1UdDgQWBBTSh7Tj3zcnk1X2
VuqB5TbMjB4/vTAPBgNVHRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFNKHtOPfNyeT
VfZW6oHlNsyMHj+9MIIBcwYDVR0gBIIBajCCAWYwggFiBgRVHSAAMIIBWDCCASIG
CCsGAQUFBwICMIIBFB6CARAAQQB1AHQAbwByAGkAZABhAGQAIABkAGUAIABDAGUA
cgB0AGkAZgBpAGMAYQBjAGkA8wBuACAAUgBhAO0AegAgAGQAZQAgAGwAYQAgAEEA
QwBDAFYAIAAoAEEAZwBlAG4AYwBpAGEAIABkAGUAIABUAGUAYwBuAG8AbABvAGcA
7QBhACAAeQAgAEMAZQByAHQAaQBmAGkAYwBhAGMAaQDzAG4AIABFAGwAZQBjAHQA
cgDzAG4AaQBjAGEALAAgAEMASQBGACAAUQA0ADYAMAAxADEANQA2AEUAKQAuACAA
QwBQAFMAIABlAG4AIABoAHQAdABwADoALwAvAHcAdwB3AC4AYQBjAGMAdgAuAGUA
czAwBggrBgEFBQcCARYkaHR0cDovL3d3dy5hY2N2LmVzL2xlZ2lzbGFjaW9uX2Mu
aHRtMFUGA1UdHwROMEwwSqBIoEaGRGh0dHA6Ly93d3cuYWNjdi5lcy9maWxlYWRt
aW4vQXJjaGl2b3MvY2VydGlmaWNhZG9zL3JhaXphY2N2MV9kZXIuY3JsMA4GA1Ud
DwEB/wQEAwIBBjAXBgNVHREEEDAOgQxhY2N2QGFjY3YuZXMwDQYJKoZIhvcNAQEF
BQADggIBAJcxAp/n/UNnSEQU5CmH7UwoZtCPNdpNYbdKl02125DgBS4OxnnQ8pdp
D70ER9m+27Up2pvZrqmZ1dM8MJP1jaGo/AaNRPTKFpV8M9xii6g3+CfYCS0b78gU
JyCpZET/LtZ1qmxNYEAZSUNUY9rizLpm5U9EelvZaoErQNV/+QEnWCzI7UiRfD+m
AM/EKXMRNt6GGT6d7hmKG9Ww7Y49nCrADdg9ZuM8Db3VlFzi4qc1GwQA9j9ajepD
vV+JHanBsMyZ4k0ACtrJJ1vnE5Bc5PUzolVt3OAJTS+xJlsndQAJxGJ3KQhfnlms
tn6tn1QwIgPBHnFk/vk4CpYY3QIUrCPLBhwepH2NDd4nQeit2hW3sCPdK6jT2iWH
7ehVRE2I9DZ+hJp4rPcOVkkO1jMl1oRQQmwgEh0q1b688nCBpHBgvgW1m54ERL5h
I6zppSSMEYCUWqKiuUnSwdzRp+0xESyeGabu4VXhwOrPDYTkF7eifKXeVSUG7szA
h1xA2syVP1XgNce4hL60Xc16gwFy7ofmXx2utYXGJt/mwZrpHgJHnyqobalbz+xF
d3+YJ5oyXSrjhO7FmGYvliAd3djDJ9ew+f7Zfc3Qn48LFFhRny+Lwzgt3uiP1o2H
pPVWQxaZLPSkVrQ0uGE3ycJYgBugl6H8WY3pEfbRD0tVNEYqi4Y7
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFQTCCAymgAwIBAgICDL4wDQYJKoZIhvcNAQELBQAwUTELMAkGA1UEBhMCVFcx
EjAQBgNVBAoTCVRBSVdBTi1DQTEQMA4GA1UECxMHUm9vdCBDQTEcMBoGA1UEAxMT
VFdDQSBHbG9iYWwgUm9vdCBDQTAeFw0xMjA2MjcwNjI4MzNaFw0zMDEyMzExNTU5
NTlaMFExCzAJBgNVBAYTAlRXMRIwEAYDVQQKEwlUQUlXQU4tQ0ExEDAOBgNVBAsT
B1Jvb3QgQ0ExHDAaBgNVBAMTE1RXQ0EgR2xvYmFsIFJvb3QgQ0EwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQCwBdvI64zEbooh745NnHEKH1Jw7W2CnJfF
10xORUnLQEK1EjRsGcJ0pDFfhQKX7EMzClPSnIyOt7h52yvVavKOZsTuKwEHktSz
0ALfUPZVr2YOy+BHYC8rMjk1Ujoog/h7FsYYuGLWRyWRzvAZEk2tY/XTP3VfKfCh
MBwqoJimFb3u/Rk28OKRQ4/6ytYQJ0lM793B8YVwm8rqqFpD/G2Gb3PpN0Wp8DbH
zIh1HrtsBv+baz4X7GGqcXzGHaL3SekVtTzWoWH1EfcFbx39Eb7QMAfCKbAJTibc
46KokWofwpFFiFzlmLhxpRUZyXx1EcxwdE8tmx2RRP1WKKD+u4ZqyPpcC1jcxkt2
yKsi2XMPpfRaAok/T54igu6idFMqPVMnaR1sjjIsZAAmY2E2TqNGtz99sy2sbZCi
laLOz9qC5wc0GZbpuCGqKX6mOL6OKUohZnkfs8O1CWfe1tQHRvMq2uYiN2DLgbYP
oA/pyJV/v1WRBXrPPRXAb94JlAGD1zQbzECl8LibZ9WYkTunhHiVJqRaCPgrdLQA
BDzfuBSO6N+pjWxnkjMdwLfS7JLIvgm/LCkFbwJrnu+8vyq8W8BQj0FwcYeyTbcE
qYSjMq+u7msXi7Kx/mzhkIyIqJdIzshNy/MGz19qCkKxHh53L46g5pIOBvwFItIm
4TFRfTLcDwIDAQABoyMwITAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB
/zANBgkqhkiG9w0BAQsFAAOCAgEAXzSBdu+WHdXltdkCY4QWwa6gcFGn90xHNcgL
1yg9iXHZqjNB6hQbbCEAwGxCGX6faVsgQt+i0trEfJdLjbDorMjupWkEmQqSpqsn
LhpNgb+E1HAerUf+/UqdM+DyucRFCCEK2mlpc3INvjT+lIutwx4116KD7+U4x6WF
H6vPNOw/KP4M8VeGTslV9xzU2KV9Bnpv1d8Q34FOIWWxtuEXeZVFBs5fzNxGiWNo
RI2T9GRwoD2dKAXDOXC4Ynsg/eTb6QihuJ49CcdP+yz4k3ZB3lLg4VfSnQO8d57+
nile98FRYB/e2guyLXW3Q0iT5/Z5xoRdgFlglPx4mI88k1HtQJAH32RjJMtOcQWh
15QaiDLxInQirqWm2BJpTGCjAu4r7NRjkgtevi92a6O2JryPA9gK8kxkRr05YuWW
6zRjESjMlfGt7+/cgFhI6Uu46mWs6fyAtbXIRfmswZ/ZuepiiI7E8UuDEq3mi4TW
nsLrgxifarsbJGAzcMzs9zLzXNl5fe+epP7JI8Mk7hWSsT2RTyaGvWZzJBPqpK5j
wa19hAM8EHiGG3njxPPyBJUgriOCxLM6AGK/5jYk4Ve6xx6QddVfP5VhK8E7zeWz
aGHQRiapIVJpLesux+t3zqY6tQMzT3bR51xUAV3LePTJDL/PEo4XLSNolOer/qmy
KwbQBM0=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFODCCAyCgAwIBAgIRAJW+FqD3LkbxezmCcvqLzZYwDQYJKoZIhvcNAQEFBQAw
NzEUMBIGA1UECgwLVGVsaWFTb25lcmExHzAdBgNVBAMMFlRlbGlhU29uZXJhIFJv
b3QgQ0EgdjEwHhcNMDcxMDE4MTIwMDUwWhcNMzIxMDE4MTIwMDUwWjA3MRQwEgYD
VQQKDAtUZWxpYVNvbmVyYTEfMB0GA1UEAwwWVGVsaWFTb25lcmEgUm9vdCBDQSB2
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAMK+6yfwIaPzaSZVfp3F
VRaRXP3vIb9TgHot0pGMYzHw7CTww6XScnwQbfQ3t+XmfHnqjLWCi65ItqwA3GV1
7CpNX8GH9SBlK4GoRz6JI5UwFpB/6FcHSOcZrr9FZ7E3GwYq/t75rH2D+1665I+X
Z75Ljo1kB1c4VWk0Nj0TSO9P4tNmHqTPGrdeNjPUtAa9GAH9d4RQAEX1jF3oI7x+
/jXh7VB7qTCNGdMJjmhnXb88lxhTuylixcpecsHHltTbLaC0H2kD7OriUPEMPPCs
81Mt8Bz17Ww5OXOAFshSsCPN4D7c3TxHoLs1iuKYaIu+5b9y7tL6pe0S7fyYGKkm
dtwoSxAgHNN/Fnct7W+A90m7UwW7XWjH1Mh1Fj+JWov3F0fUTPHSiXk+TT2YqGHe
Oh7S+F4D4MHJHIzTjU3TlTazN19jY5szFPAtJmtTfImMMsJu7D0hADnJoWjiUIMu
sDor8zagrC/kb2HCUQk5PotTubtn2txTuXZZNp1D5SDgPTJghSJRt8czu90VL6R4
pgd7gUY2BIbdeTXHlSw7sKMXNeVzH7RcWe/a6hBle3rQf5+ztCo3O3CLm1u5K7fs
slESl1MpWtTwEhDcTwK7EpIvYtQ/aUN8Ddb8WHUBiJ1YFkveupD/RwGJBmr2X7KQ
arMCpgKIv7NHfirZ1fpoeDVNAgMBAAGjPzA9MA8GA1UdEwEB/wQFMAMBAf8wCwYD
VR0PBAQDAgEGMB0GA1UdDgQWBBTwj1k4ALP1j5qWDNXr+nuqF+gTEjANBgkqhkiG
9w0BAQUFAAOCAgEAvuRcYk4k9AwI//DTDGjkk0kiP0Qnb7tt3oNmzqjMDfz1mgbl
dxSR651Be5kqhOX//CHBXfDkH1e3damhXwIm/9fH907eT/j3HEbAek9ALCI18Bmx
0GtnLLCo4MBANzX2hFxc469CeP6nyQ1Q6g2EdvZR74NTxnr/DlZJLo961gzmJ1Tj
TQpgcmLNkQfWpb/ImWvtxBnmq0wROMVvMeJuScg/doAmAyYp4Db29iBT4xdwNBed
Y2gea+zDTYa4EzAvXUYNR0PVG6pZDrlcjQZIrXSHX8f8MVRBE+LHIQ6e4B4N4cB7
Q4WQxYpYxmUKeFfyxiMPAdkgS94P+5KFdSpcc41teyWRyu5FrgZLAMzTsVlQ2jqI
OylDRl6XK1TOU2+NSueW+r9xDkKLfP0ooNBIytrEgUy7onOTJsjrDNYmiLbAJM+7
vVvrdX3pCI6GMyx5dwlppYn8s3CQh3aP0yK7Qs69cwsgJirQmz1wHiRszYd2qReW
t88NkvuOGKmYSdGe/mBEciG5Ge3C9THxOUiIkCR1VBatzvT4aRRkOfujuLpwQMcn
HL/EVlP6Y2XQ8xwOFvVrhlhNGNTkDY6lnVuR3HYkUD/GKvvZt5y11ubQ2egZixVx
SK236thZiNSQvxaz2emsWWFUyBy6ysHK4bkgTI86k4mloMy/0/Z1pHWWbVY=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDwzCCAqugAwIBAgIBATANBgkqhkiG9w0BAQsFADCBgjELMAkGA1UEBhMCREUx
KzApBgNVBAoMIlQtU3lzdGVtcyBFbnRlcnByaXNlIFNlcnZpY2VzIEdtYkgxHzAd
BgNVBAsMFlQtU3lzdGVtcyBUcnVzdCBDZW50ZXIxJTAjBgNVBAMMHFQtVGVsZVNl
YyBHbG9iYWxSb290IENsYXNzIDIwHhcNMDgxMDAxMTA0MDE0WhcNMzMxMDAxMjM1
OTU5WjCBgjELMAkGA1UEBhMCREUxKzApBgNVBAoMIlQtU3lzdGVtcyBFbnRlcnBy
aXNlIFNlcnZpY2VzIEdtYkgxHzAdBgNVBAsMFlQtU3lzdGVtcyBUcnVzdCBDZW50
ZXIxJTAjBgNVBAMMHFQtVGVsZVNlYyBHbG9iYWxSb290IENsYXNzIDIwggEiMA0G
CSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCqX9obX+hzkeXaXPSi5kfl82hVYAUd
AqSzm1nzHoqvNK38DcLZSBnuaY/JIPwhqgcZ7bBcrGXHX+0CfHt8LRvWurmAwhiC
FoT6ZrAIxlQjgeTNuUk/9k9uN0goOA/FvudocP05l03Sx5iRUKrERLMjfTlH6VJi
1hKTXrcxlkIF+3anHqP1wvzpesVsqXFP6st4vGCvx9702cu+fjOlbpSD8DT6Iavq
jnKgP6TeMFvvhk1qlVtDRKgQFRzlAVfFmPHmBiiRqiDFt1MmUUOyCxGVWOHAD3bZ
wI18gfNycJ5v/hqO2V81xrJvNHy+SE/iWjnX2J14np+GPgNeGYtEotXHAgMBAAGj
QjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMB0GA1UdDgQWBBS/
WSA2AHmgoCJrjNXyYdK4LMuCSjANBgkqhkiG9w0BAQsFAAOCAQEAMQOiYQsfdOhy
NsZt+U2e+iKo4YFWz827n+qrkRk4r6p8FU3ztqONpfSO9kSpp+ghla0+AGIWiPAC
uvxhI+YzmzB6azZie60EI4RYZeLbK4rnJVM3YlNfvNoBYimipidx5joifsFvHZVw
IEoHNN/q/xWA5brXethbdXwFeilHfkCoMRN3zUA7tFFHei4R40cR3p1m0IvVVGb6
g1XqfMIpiRvpb7PO4gWEyS8+eIVibslfwXhjdFjASBgMmTnrpMwatXlajRWc2BQN
9noHV8cigwUtPJslJj0Ys6lDfMjIq2SPDqO/nBudMNva0Bkuqjzx+zOAduTNrRlP
BSeOE6Fuwg==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDdzCCAl+gAwIBAgIIXDPLYixfszIwDQYJKoZIhvcNAQELBQAwPDEeMBwGA1UE
AwwVQXRvcyBUcnVzdGVkUm9vdCAyMDExMQ0wCwYDVQQKDARBdG9zMQswCQYDVQQG
EwJERTAeFw0xMTA3MDcxNDU4MzBaFw0zMDEyMzEyMzU5NTlaMDwxHjAcBgNVBAMM
FUF0b3MgVHJ1c3RlZFJvb3QgMjAxMTENMAsGA1UECgwEQXRvczELMAkGA1UEBhMC
REUwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCVhTuXbyo7LjvPpvMp
Nb7PGKw+qtn4TaA+Gke5vJrf8v7MPkfoepbCJI419KkM/IL9bcFyYie96mvr54rM
VD6QUM+A1JX76LWC1BTFtqlVJVfbsVD2sGBkWXppzwO3bw2+yj5vdHLqqjAqc2K+
SZFhyBH+DgMq92og3AIVDV4VavzjgsG1xZ1kCWyjWZgHJ8cblithdHFsQ/H3NYkQ
4J7sVaE3IqKHBAUsR320HLliKWYoyrfhk/WklAOZuXCFteZI6o1Q/NnezG8HDt0L
cp2AMBYHlT8oDv3FdU9T1nSatCQujgKRz3bFmx5VdJx4IbHwLfELn8LVlhgf8FQi
eowHAgMBAAGjfTB7MB0GA1UdDgQWBBSnpQaxLKYJYO7Rl+lwrrw7GWzbITAPBgNV
HRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFKelBrEspglg7tGX6XCuvDsZbNshMBgG
A1UdIAQRMA8wDQYLKwYBBAGwLQMEAQEwDgYDVR0PAQH/BAQDAgGGMA0GCSqGSIb3
DQEBCwUAA4IBAQAmdzTblEiGKkGdLD4GkGDEjKwLVLgfuXvTBznk+j57sj1O7Z8j
vZfza1zv7v1Apt+hk6EKhqzvINB5Ab149xnYJDE0BAGmuhWawyfc2E8PzBhj/5kP
DpFrdRbhIfzYJsdHt6bPWHJxfrrhTZVHO8mvbaG0weyJ9rQPOLXiZNwlz6bb65pc
maHFCN795trV1lpFDMS3wrUU77QR/w4VtfX128a961qn8FYiqTxlVMYVqL2Gns2D
lmh6cYGJ4Qvh6hEbaAjMaZ7snkGeRDImeuKHCnE96+RapNLbxc3G3mB/ufNPRJLv
KrcYPqcZ2Qt9sTdBQrC6YB3y/gkRsPCHe6ed
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFYDCCA0igAwIBAgIUeFhfLq0sGUvjNwc1NBMotZbUZZMwDQYJKoZIhvcNAQEL
BQAwSDELMAkGA1UEBhMCQk0xGTAXBgNVBAoTEFF1b1ZhZGlzIExpbWl0ZWQxHjAc
BgNVBAMTFVF1b1ZhZGlzIFJvb3QgQ0EgMSBHMzAeFw0xMjAxMTIxNzI3NDRaFw00
MjAxMTIxNzI3NDRaMEgxCzAJBgNVBAYTAkJNMRkwFwYDVQQKExBRdW9WYWRpcyBM
aW1pdGVkMR4wHAYDVQQDExVRdW9WYWRpcyBSb290IENBIDEgRzMwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQCgvlAQjunybEC0BJyFuTHK3C3kEakEPBtV
wedYMB0ktMPvhd6MLOHBPd+C5k+tR4ds7FtJwUrVu4/sh6x/gpqG7D0DmVIB0jWe
rNrwU8lmPNSsAgHaJNM7qAJGr6Qc4/hzWHa39g6QDbXwz8z6+cZM5cOGMAqNF341
68Xfuw6cwI2H44g4hWf6Pser4BOcBRiYz5P1sZK0/CPTz9XEJ0ngnjybCKOLXSoh
4Pw5qlPafX7PGglTvF0FBM+hSo+LdoINofjSxxR3W5A2B4GbPgb6Ul5jxaYA/qXp
UhtStZI5cgMJYr2wYBZupt0lwgNm3fME0UDiTouG9G/lg6AnhF4EwfWQvTA9xO+o
abw4m6SkltFi2mnAAZauy8RRNOoMqv8hjlmPSlzkYZqn0ukqeI1RPToV7qJZjqlc
3sX5kCLliEVx3ZGZbHqfPT2YfF72vhZooF6uCyP8Wg+qInYtyaEQHeTTRCOQiJ/G
KubX9ZqzWB4vMIkIG1SitZgj7Ah3HJVdYdHLiZxfokqRmu8hqkkWCKi9YSgxyXSt
hfbZxbGL0eUQMk1fiyA6PEkfM4VZDdvLCXVDaXP7a3F98N/ETH3Goy7IlXnLc6KO
Tk0k+17kBL5yG6YnLUlamXrXXAkgt3+UuU/xDRxeiEIbEbfnkduebPRq34wGmAOt
zCjvpUfzUwIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIB
BjAdBgNVHQ4EFgQUo5fW816iEOGrRZ88F2Q87gFwnMwwDQYJKoZIhvcNAQELBQAD
ggIBABj6W3X8PnrHX3fHyt/PX8MSxEBd1DKquGrX1RUVRpgjpeaQWxiZTOOtQqOC
MTaIzen7xASWSIsBx40Bz1szBpZGZnQdT+3Btrm0DWHMY37XLneMlhwqI2hrhVd2
cDMT/uFPpiN3GPoajOi9ZcnPP/TJF9zrx7zABC4tRi9pZsMbj/7sPtPKlL92CiUN
qXsCHKnQO18LwIE6PWThv6ctTr1NxNgpxiIY0MWscgKCP6o6ojoilzHdCGPDdRS5
YCgtW2jgFqlmgiNR9etT2DGbe+m3nUvriBbP+V04ikkwj+3x6xn0dxoxGE1nVGwv
b2X52z3sIexe9PSLymBlVNFxZPT5pqOBMzYzcfCkeF9OrYMh3jRJjehZrJ3ydlo2
8hP0r+AJx2EqbPfgna67hkooby7utHnNkDPDs3b69fBsnQGQ+p6Q9pxyz0fawx/k
NSBT8lTR32GDpgLiJTjehTItXnOQUl1CxM49S+H5GYQd1aJQzEH7QRTDvdbJWqNj
ZgKAvQU6O0ec7AAmTPWIUb+oI38YB7AL7YsmoWTTYUrrXJ/es69nA7Mf3W1daWhp
q1467HxpvMc7hU6eFbm0FU/DlXpY18ls6Wy58yljXrQs8C097Vpl4KlbQMJImYFt
nh8GKjwStIsPm6Ik8KaN1nrgS7ZklmOVhMJKzRwuJIczYOXD
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFYDCCA0igAwIBAgIURFc0JFuBiZs18s64KztbpybwdSgwDQYJKoZIhvcNAQEL
BQAwSDELMAkGA1UEBhMCQk0xGTAXBgNVBAoTEFF1b1ZhZGlzIExpbWl0ZWQxHjAc
BgNVBAMTFVF1b1ZhZGlzIFJvb3QgQ0EgMiBHMzAeFw0xMjAxMTIxODU5MzJaFw00
MjAxMTIxODU5MzJaMEgxCzAJBgNVBAYTAkJNMRkwFwYDVQQKExBRdW9WYWRpcyBM
aW1pdGVkMR4wHAYDVQQDExVRdW9WYWRpcyBSb290IENBIDIgRzMwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQChriWyARjcV4g/Ruv5r+LrI3HimtFhZiFf
qq8nUeVuGxbULX1QsFN3vXg6YOJkApt8hpvWGo6t/x8Vf9WVHhLL5hSEBMHfNrMW
n4rjyduYNM7YMxcoRvynyfDStNVNCXJJ+fKH46nafaF9a7I6JaltUkSs+L5u+9ym
c5GQYaYDFCDy54ejiK2toIz/pgslUiXnFgHVy7g1gQyjO/Dh4fxaXc6AcW34Sas+
O7q414AB+6XrW7PFXmAqMaCvN+ggOp+oMiwMzAkd056OXbxMmO7FGmh77FOm6RQ1
o9/NgJ8MSPsc9PG/Srj61YxxSscfrf5BmrODXfKEVu+lV0POKa2Mq1W/xPtbAd0j
IaFYAI7D0GoT7RPjEiuA3GfmlbLNHiJuKvhB1PLKFAeNilUSxmn1uIZoL1NesNKq
IcGY5jDjZ1XHm26sGahVpkUG0CM62+tlXSoREfA7T8pt9DTEceT/AFr2XK4jYIVz
8eQQsSWu1ZK7E8EM4DnatDlXtas1qnIhO4M15zHfeiFuuDIIfR0ykRVKYnLP43eh
vNURG3YBZwjgQQvD6xVu+KQZ2aKrr+InUlYrAoosFCT5v0ICvybIxo/gbjh9Uy3l
7ZizlWNof/k19N+IxWA1ksB8aRxhlRbQ694Lrz4EEEVlWFA4r0jyWbYW8jwNkALG
cC4BrTwV1wIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIB
BjAdBgNVHQ4EFgQU7edvdlq/YOxJW8ald7tyFnGbxD0wDQYJKoZIhvcNAQELBQAD
ggIBAJHfgD9DCX5xwvfrs4iP4VGyvD11+ShdyLyZm3tdquXK4Qr36LLTn91nMX66
AarHakE7kNQIXLJgapDwyM4DYvmL7ftuKtwGTTwpD4kWilhMSA/ohGHqPHKmd+RC
roijQ1h5fq7KpVMNqT1wvSAZYaRsOPxDMuHBR//47PERIjKWnML2W2mWeyAMQ0Ga
W/ZZGYjeVYg3UQt4XAoeo0L9x52ID8DyeAIkVJOviYeIyUqAHerQbj5hLja7NQ4n
lv1mNDthcnPxFlxHBlRJAHpYErAK74X9sbgzdWqTHBLmYF5vHX/JHyPLhGGfHoJE
+V+tYlUkmlKY7VHnoX6XOuYvHxHaU4AshZ6rNRDbIl9qxV6XU/IyAgkwo1jwDQHV
csaxfGl7w/U2Rcxhbl5MlMVerugOXou/983g7aEOGzPuVBj+D77vfoRrQ+NwmNtd
dbINWQeFFSM51vHfqSYP1kjHs6Yi9TM3WpVHn3u6GBVv/9YUZINJ0gpnIdsPNWNg
KCLjsZWDzYWm3S8P52dSbrsvhXz1SnPnxT7AvSESBT/8twNJAlvIJebiVDj1eYeM
HVOyToV7BjjHLPj4sHKNJeV3UvQDHEimUF+IIDBu8oJDqz2XhOdT+yHBTw8imoa4
WSr2Rz0ZiC3oheGe7IUIarFsNMkd7EgrO3jtZsSOeWmD3n+M
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFYDCCA0igAwIBAgIULvWbAiin23r/1aOp7r0DoM8Sah0wDQYJKoZIhvcNAQEL
BQAwSDELMAkGA1UEBhMCQk0xGTAXBgNVBAoTEFF1b1ZhZGlzIExpbWl0ZWQxHjAc
BgNVBAMTFVF1b1ZhZGlzIFJvb3QgQ0EgMyBHMzAeFw0xMjAxMTIyMDI2MzJaFw00
MjAxMTIyMDI2MzJaMEgxCzAJBgNVBAYTAkJNMRkwFwYDVQQKExBRdW9WYWRpcyBM
aW1pdGVkMR4wHAYDVQQDExVRdW9WYWRpcyBSb290IENBIDMgRzMwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQCzyw4QZ47qFJenMioKVjZ/aEzHs286IxSR
/xl/pcqs7rN2nXrpixurazHb+gtTTK/FpRp5PIpM/6zfJd5O2YIyC0TeytuMrKNu
FoM7pmRLMon7FhY4futD4tN0SsJiCnMK3UmzV9KwCoWdcTzeo8vAMvMBOSBDGzXR
U7Ox7sWTaYI+FrUoRqHe6okJ7UO4BUaKhvVZR74bbwEhELn9qdIoyhA5CcoTNs+c
ra1AdHkrAj80//ogaX3T7mH1urPnMNA3I4ZyYUUpSFlob3emLoG+B01vr87ERROR
FHAGjx+f+IdpsQ7vw4kZ6+ocYfx6bIrc1gMLnia6Et3UVDmrJqMz6nWB2i3ND0/k
A9HvFZcba5DFApCTZgIhsUfei5pKgLlVj7WiL8DWM2fafsSntARE60f75li59wzw
eyuxwHApw0BiLTtIadwjPEjrewl5qW3aqDCYz4ByA4imW0aucnl8CAMhZa634Ryl
sSqiMd5mBPfAdOhx3v89WcyWJhKLhZVXGqtrdQtEPREoPHtht+KPZ0/l7DxMYIBp
VzgeAVuNVejH38DMdyM0SXV89pgR6y3e7UEuFAUCf+D+IOs15xGsIs5XPd7JMG0Q
A4XN8f+MFrXBsj6IbGB/kE+V9/YtrQE5BwT6dYB9v0lQ7e/JxHwc64B+27bQ3RP+
ydOc17KXqQIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIB
BjAdBgNVHQ4EFgQUxhfQvKjqAkPyGwaZXSuQILnXnOQwDQYJKoZIhvcNAQELBQAD
ggIBADRh2Va1EodVTd2jNTFGu6QHcrxfYWLopfsLN7E8trP6KZ1/AvWkyaiTt3px
KGmPc+FSkNrVvjrlt3ZqVoAh313m6Tqe5T72omnHKgqwGEfcIHB9UqM+WXzBusnI
FUBhynLWcKzSt/Ac5IYp8M7vaGPQtSCKFWGafoaYtMnCdvvMujAWzKNhxnQT5Wvv
oxXqA/4Ti2Tk08HS6IT7SdEQTXlm66r99I0xHnAUrdzeZxNMgRVhvLfZkXdxGYFg
u/BYpbWcC/ePIlUnwEsBbTuZDdQdm2NnL9DuDcpmvJRPpq3t/O5jrFc/ZSXPsoaP
0Aj/uHYUbt7lJ+yreLVTubY/6CD50qi+YUbKh4yE8/nxoGibIh6BJpsQBJFxwAYf
3KDTuVan45gtf4Od34wrnDKOMpTwATwiKp9Dwi7DmDkHOHv8XgBCH/MyJnmDhPbl
8MFREsALHgQjDFSlTC9JxUrRtm5gDWv8a4uFJGS3iQ6rJUdbPM9+Sb3H6QrG2vd+
DhcI00iX0HGS8A85PjRqHH3Y8iKuu2n0M7SmSFXRDw4m6Oy2Cy2nhTXN/VnIn9HN
PlopNLk9hM6xZdRZkZFWdSHBd575euFgndOtBBj0fOtek49TSiIp+EgrPk2GrFt/
ywaZWWDYWGWVjUTR939+J399roD1B0y2PpxxVJkES/1Y+Zj0
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDljCCAn6gAwIBAgIQC5McOtY5Z+pnI7/Dr5r0SzANBgkqhkiG9w0BAQsFADBl
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJv
b3QgRzIwHhcNMTMwODAxMTIwMDAwWhcNMzgwMTE1MTIwMDAwWjBlMQswCQYDVQQG
EwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNl
cnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJvb3QgRzIwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDZ5ygvUj82ckmIkzTz+GoeMVSA
n61UQbVH35ao1K+ALbkKz3X9iaV9JPrjIgwrvJUXCzO/GU1BBpAAvQxNEP4Htecc
biJVMWWXvdMX0h5i89vqbFCMP4QMls+3ywPgym2hFEwbid3tALBSfK+RbLE4E9Hp
EgjAALAcKxHad3A2m67OeYfcgnDmCXRwVWmvo2ifv922ebPynXApVfSr/5Vh88lA
bx3RvpO704gqu52/clpWcTs/1PPRCv4o76Pu2ZmvA9OPYLfykqGxvYmJHzDNw6Yu
YjOuFgJ3RFrngQo8p0Quebg/BLxcoIfhG69Rjs3sLPr4/m3wOnyqi+RnlTGNAgMB
AAGjQjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQW
BBTOw0q5mVXyuNtgv6l+vVa1lzan1jANBgkqhkiG9w0BAQsFAAOCAQEAyqVVjOPI
QW5pJ6d1Ee88hjZv0p3GeDgdaZaikmkuOGybfQTUiaWxMTeKySHMq2zNixya1r9I
0jJmwYrA8y8678Dj1JGG0VDjA9tzd29KOVPt3ibHtX2vK0LRdWLjSisCx1BL4Gni
lmwORGYQRI+tBev4eaymG+g3NJ1TyWGqolKvSnAWhsI6yLETcDbYz+70CjTVW0z9
B5yiutkBclzzTcHdDrEcDcRjvq30FPuJ7KJBDkzMyFdA0G4Dqs0MjomZmWzwPDCv
ON9vvKO+KSAnq3T/EyJ43pdSVR6DtVQgA+6uwE9W3jfMw3+qBCe703e4YtsXfJwo
IhNzbM8m9Yop5w==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICRjCCAc2gAwIBAgIQC6Fa+h3foLVJRK/NJKBs7DAKBggqhkjOPQQDAzBlMQsw
CQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cu
ZGlnaWNlcnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJvb3Qg
RzMwHhcNMTMwODAxMTIwMDAwWhcNMzgwMTE1MTIwMDAwWjBlMQswCQYDVQQGEwJV
UzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNlcnQu
Y29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJvb3QgRzMwdjAQBgcq
hkjOPQIBBgUrgQQAIgNiAAQZ57ysRGXtzbg/WPuNsVepRC0FFfLvC/8QdJ+1YlJf
Zn4f5dwbRXkLzMZTCp2NXQLZqVneAlr2lSoOjThKiknGvMYDOAdfVdp+CW7if17Q
RSAPWXYQ1qAk8C3eNvJsKTmjQjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/
BAQDAgGGMB0GA1UdDgQWBBTL0L2p4ZgFUaFNN6KDec6NHSrkhDAKBggqhkjOPQQD
AwNnADBkAjAlpIFFAmsSS3V0T8gj43DydXLefInwz5FyYZ5eEJJZVrmDxxDnOOlY
JjZ91eQ0hjkCMHw2U/Aw5WJjOpnitqM7mzT6HtoQknFekROn3aRukswy1vUhZscv
6pZjamVFkpUBtA==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDjjCCAnagAwIBAgIQAzrx5qcRqaC7KGSxHQn65TANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xIDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEcyMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuzfNNNx7a8myaJCtSnX/RrohCgiN9RlUyfuI
2/Ou8jqJkTx65qsGGmvPrC3oXgkkRLpimn7Wo6h+4FR1IAWsULecYxpsMNzaHxmx
1x7e/dfgy5SDN67sH0NO3Xss0r0upS/kqbitOtSZpLYl6ZtrAGCSYP9PIUkY92eQ
q2EGnI/yuum06ZIya7XzV+hdG82MHauVBJVJ8zUtluNJbd134/tJS7SsVQepj5Wz
tCO7TG1F8PapspUwtP1MVYwnSlcUfIKdzXOS0xZKBgyMUNGPHgm+F6HmIcr9g+UQ
vIOlCsRnKPZzFBQ9RnbDhxSJITRNrw9FDKZJobq7nMWxM4MphQIDAQABo0IwQDAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAdBgNVHQ4EFgQUTiJUIBiV
5uNu5g/6+rkS7QYXjzkwDQYJKoZIhvcNAQELBQADggEBAGBnKJRvDkhj6zHd6mcY
1Yl9PMWLSn/pvtsrF9+wX3N3KjITOYFnQoQj8kVnNeyIv/iPsGEMNKSuIEyExtv4
NeF22d+mQrvHRAiGfzZ0JFrabA0UWTW98kndth/Jsw1HKj2ZL7tcu7XUIOGZX1NG
Fdtom/DzMNU+MeKNhJ7jitralj41E6Vf8PlwUHBHQRFXGU7Aj64GxJUTFy8bJZ91
8rGOmaFvE7FBcf6IKshPECBV1/MUReXgRPTqh5Uykw7+U0b6LJ3/iyK5S9kJRaTe
pLiaWN0bfVKfjllDiIGknibVb63dDcY3fe0Dkhvld1927jyNxF1WW6LZZm6zNTfl
MrY=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICPzCCAcWgAwIBAgIQBVVWvPJepDU1w6QP1atFcjAKBggqhkjOPQQDAzBhMQsw
CQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cu
ZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBHMzAe
Fw0xMzA4MDExMjAwMDBaFw0zODAxMTUxMjAwMDBaMGExCzAJBgNVBAYTAlVTMRUw
EwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5jb20x
IDAeBgNVBAMTF0RpZ2lDZXJ0IEdsb2JhbCBSb290IEczMHYwEAYHKoZIzj0CAQYF
K4EEACIDYgAE3afZu4q4C/sLfyHS8L6+c/MzXRq8NOrexpu80JX28MzQC7phW1FG
fp4tn+6OYwwX7Adw9c+ELkCDnOg/QW07rdOkFFk2eJ0DQ+4QE2xy3q6Ip6FrtUPO
Z9wj/wMco+I+o0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBhjAd
BgNVHQ4EFgQUs9tIpPmhxdiuNkHMEWNpYim8S8YwCgYIKoZIzj0EAwMDaAAwZQIx
AK288mw/EkrRLTnDCgmXc/SINoyIJ7vmiI1Qhadj+Z4y3maTD/HMsQmP3Wyr+mt/
oAIwOWZbwmSNuJ5Q3KjVSaLtx9zRSX8XAbjIho9OjIgrqJqpisXRAL34VOKa5Vt8
sycX
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFkDCCA3igAwIBAgIQBZsbV56OITLiOQe9p3d1XDANBgkqhkiG9w0BAQwFADBi
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSEwHwYDVQQDExhEaWdpQ2VydCBUcnVzdGVkIFJvb3Qg
RzQwHhcNMTMwODAxMTIwMDAwWhcNMzgwMTE1MTIwMDAwWjBiMQswCQYDVQQGEwJV
UzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNlcnQu
Y29tMSEwHwYDVQQDExhEaWdpQ2VydCBUcnVzdGVkIFJvb3QgRzQwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQC/5pBzaN675F1KPDAiMGkz7MKnJS7JIT3y
ithZwuEppz1Yq3aaza57G4QNxDAf8xukOBbrVsaXbR2rsnnyyhHS5F/WBTxSD1If
xp4VpX6+n6lXFllVcq9ok3DCsrp1mWpzMpTREEQQLt+C8weE5nQ7bXHiLQwb7iDV
ySAdYyktzuxeTsiT+CFhmzTrBcZe7FsavOvJz82sNEBfsXpm7nfISKhmV1efVFiO
DCu3T6cw2Vbuyntd463JT17lNecxy9qTXtyOj4DatpGYQJB5w3jHtrHEtWoYOAMQ
jdjUN6QuBX2I9YI+EJFwq1WCQTLX2wRzKm6RAXwhTNS8rhsDdV14Ztk6MUSaM0C/
CNdaSaTC5qmgZ92kJ7yhTzm1EVgX9yRcRo9k98FpiHaYdj1ZXUJ2h4mXaXpI8OCi
EhtmmnTK3kse5w5jrubU75KSOp493ADkRSWJtppEGSt+wJS00mFt6zPZxd9LBADM
fRyVw4/3IbKyEbe7f/LVjHAsQWCqsWMYRJUadmJ+9oCw++hkpjPRiQfhvbfmQ6QY
uKZ3AeEPlAwhHbJUKSWJbOUOUlFHdL4mrLZBdd56rF+NP8m800ERElvlEFDrMcXK
chYiCd98THU/Y+whX8QgUWtvsauGi0/C1kVfnSD8oR7FwI+isX4KJpn15GkvmB0t
9dmpsh3lGwIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIB
hjAdBgNVHQ4EFgQU7NfjgtJxXWRM3y5nP+e6mK4cD08wDQYJKoZIhvcNAQEMBQAD
ggIBALth2X2pbL4XxJEbw6GiAI3jZGgPVs93rnD5/ZpKmbnJeFwMDF/k5hQpVgs2
SV1EY+CtnJYYZhsjDT156W1r1lT40jzBQ0CuHVD1UvyQO7uYmWlrx8GnqGikJ9yd
+SeuMIW59mdNOj6PWTkiU0TryF0Dyu1Qen1iIQqAyHNm0aAFYF/opbSnr6j3bTWc
fFqK1qI4mfN4i/RN0iAL3gTujJtHgXINwBQy7zBZLq7gcfJW5GqXb5JQbZaNaHqa
sjYUegbyJLkJEVDXCLG4iXqEI2FCKeWjzaIgQdfRnGTZ6iahixTXTBmyUEFxPT9N
cCOGDErcgdLMMpSEDQgJlxxPwO5rIHQw0uA5NBCFIRUBCOhVMt5xSdkoF1BN5r5N
0XWs0Mr7QbhDparTwwVETyw2m+L64kW4I1NsBm9nVX9GtUw/bihaeSbSpKhil9Ie
4u1Ki7wb/UdKDd9nZn6yW0HQO+T0O/QEY+nvwlQAUaCKKsnOeMzV6ocEGLPOr0mI
r/OSmbaz5mEP0oUA51Aa5BuVnRmhuZyxm7EAHu/QD09CbMkKvO5D+jpxpchNJqU1
/YldvIViHTLSoCtU7ZpXwdv6EM8Zt4tKG48BtieVU+i2iW1bvGjUI+iLUaJW+fCm
gKDWHrO8Dw9TdSmq6hN35N6MgSGtBxBHEa2HPQfRdbzP82Z+
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF2DCCA8CgAwIBAgIQTKr5yttjb+Af907YWwOGnTANBgkqhkiG9w0BAQwFADCB
hTELMAkGA1UEBhMCR0IxGzAZBgNVBAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4G
A1UEBxMHU2FsZm9yZDEaMBgGA1UEChMRQ09NT0RPIENBIExpbWl0ZWQxKzApBgNV
BAMTIkNPTU9ETyBSU0EgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTAwMTE5
MDAwMDAwWhcNMzgwMTE4MjM1OTU5WjCBhTELMAkGA1UEBhMCR0IxGzAZBgNVBAgT
EkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEaMBgGA1UEChMR
Q09NT0RPIENBIExpbWl0ZWQxKzApBgNVBAMTIkNPTU9ETyBSU0EgQ2VydGlmaWNh
dGlvbiBBdXRob3JpdHkwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCR
6FSS0gpWsawNJN3Fz0RndJkrN6N9I3AAcbxT38T6KhKPS38QVr2fcHK3YX/JSw8X
pz3jsARh7v8Rl8f0hj4K+j5c+ZPmNHrZFGvnnLOFoIJ6dq9xkNfs/Q36nGz637CC
9BR++b7Epi9Pf5l/tfxnQ3K9DADWietrLNPtj5gcFKt+5eNu/Nio5JIk2kNrYrhV
/erBvGy2i/MOjZrkm2xpmfh4SDBF1a3hDTxFYPwyllEnvGfDyi62a+pGx8cgoLEf
Zd5ICLqkTqnyg0Y3hOvozIFIQ2dOciqbXL1MGyiKXCJ7tKuY2e7gUYPDCUZObT6Z
+pUX2nwzV0E8jVHtC7ZcryxjGt9XyD+86V3Em69FmeKjWiS0uqlWPc9vqv9JWL7w
qP/0uK3pN/u6uPQLOvnoQ0IeidiEyxPx2bvhiWC4jChWrBQdnArncevPDt09qZah
SL0896+1DSJMwBGB7FY79tOi4lu3sgQiUpWAk2nojkxl8ZEDLXB0AuqLZxUpaVIC
u9ffUGpVRr+goyhhf3DQw6KqLCGqR84onAZFdr+CGCe01a60y1Dma/RMhnEw6abf
Fobg2P9A3fvQQoh/ozM6LlweQRGBY84YcWsr7KaKtzFcOmpH4MN5WdYgGq/yapiq
crxXStJLnbsQ/LBMQeXtHT1eKJ2czL+zUdqnR+WEUwIDAQABo0IwQDAdBgNVHQ4E
FgQUu69+Aj36pvE8hI6t7jiY7NkyMtQwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB
/wQFMAMBAf8wDQYJKoZIhvcNAQEMBQADggIBAArx1UaEt65Ru2yyTUEUAJNMnMvl
wFTPoCWOAvn9sKIN9SCYPBMtrFaisNZ+EZLpLrqeLppysb0ZRGxhNaKatBYSaVqM
4dc+pBroLwP0rmEdEBsqpIt6xf4FpuHA1sj+nq6PK7o9mfjYcwlYRm6mnPTXJ9OV
2jeDchzTc+CiR5kDOF3VSXkAKRzH7JsgHAckaVd4sjn8OoSgtZx8jb8uk2Intzna
FxiuvTwJaP+EmzzV1gsD41eeFPfR60/IvYcjt7ZJQ3mFXLrrkguhxuhoqEwWsRqZ
CuhTLJK7oQkYdQxlqHvLI7cawiiFwxv/0Cti76R7CZGYZ4wUAc1oBmpjIXUDgIiK
boHGhfKppC3n9KUkEEeDys30jXlYsQab5xoq2Z0B15R97QNKyvDb6KkBPvVWmcke
jkk9u+UJueBPSZI9FoJAzMxZxuY67RIuaTxslbH9qh17f4a+Hg4yRvv7E491f0yL
S0Zj/gA0QHDBw7mh3aZw4gSzQbzpgJHqZJx64SIDqZxubw5lT2yHh17zbqD5daWb
QOhTsiedSrnAdyGN/4fy3ryM7xfft0kL0fJuMAsaDk527RH89elWsn2/x20Kk4yl
0MC2Hb46TpSi125sC8KKfPog88Tk5c0NqMuRkrF8hey1FGlmDoLnzc7ILaZRfyHB
NVOFBkpdn627G190
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF3jCCA8agAwIBAgIQAf1tMPyjylGoG7xkDjUDLTANBgkqhkiG9w0BAQwFADCB
iDELMAkGA1UEBhMCVVMxEzARBgNVBAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0pl
cnNleSBDaXR5MR4wHAYDVQQKExVUaGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNV
BAMTJVVTRVJUcnVzdCBSU0EgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTAw
MjAxMDAwMDAwWhcNMzgwMTE4MjM1OTU5WjCBiDELMAkGA1UEBhMCVVMxEzARBgNV
BAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0plcnNleSBDaXR5MR4wHAYDVQQKExVU
aGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNVBAMTJVVTRVJUcnVzdCBSU0EgQ2Vy
dGlmaWNhdGlvbiBBdXRob3JpdHkwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIK
AoICAQCAEmUXNg7D2wiz0KxXDXbtzSfTTK1Qg2HiqiBNCS1kCdzOiZ/MPans9s/B
3PHTsdZ7NygRK0faOca8Ohm0X6a9fZ2jY0K2dvKpOyuR+OJv0OwWIJAJPuLodMkY
tJHUYmTbf6MG8YgYapAiPLz+E/CHFHv25B+O1ORRxhFnRghRy4YUVD+8M/5+bJz/
Fp0YvVGONaanZshyZ9shZrHUm3gDwFA66Mzw3LyeTP6vBZY1H1dat//O+T23LLb2
VN3I5xI6Ta5MirdcmrS3ID3KfyI0rn47aGYBROcBTkZTmzNg95S+UzeQc0PzMsNT
79uq/nROacdrjGCT3sTHDN/hMq7MkztReJVni+49Vv4M0GkPGw/zJSZrM233bkf6
c0Plfg6lZrEpfDKEY1WJxA3Bk1QwGROs0303p+tdOmw1XNtB1xLaqUkL39iAigmT
Yo61Zs8liM2EuLE/pDkP2QKe6xJMlXzzawWpXhaDzLhn4ugTncxbgtNMs+1b/97l
c6wjOy0AvzVVdAlJ2ElYGn+SNuZRkg7zJn0cTRe8yexDJtC/QV9AqURE9JnnV4ee
UB9XVKg+/XRjL7FQZQnmWEIuQxpMtPAlR1n6BB6T1CZGSlCBst6+eLf8ZxXhyVeE
Hg9j1uliutZfVS7qXMYoCAQlObgOK6nyTJccBz8NUvXt7y+CDwIDAQABo0IwQDAd
BgNVHQ4EFgQUU3m/WqorSs9UgOHYm8Cd8rIDZsswDgYDVR0PAQH/BAQDAgEGMA8G
A1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEMBQADggIBAFzUfA3P9wF9QZllDHPF
Up/L+M+ZBn8b2kMVn54CVVeWFPFSPCeHlCjtHzoBN6J2/FNQwISbxmtOuowhT6KO
VWKR82kV2LyI48SqC/3vqOlLVSoGIG1VeCkZ7l8wXEskEVX/JJpuXior7gtNn3/3
ATiUFJVDBwn7YKnuHKsSjKCaXqeYalltiz8I+8jRRa8YFWSQEg9zKC7F4iRO/Fjs
8PRF/iKz6y+O0tlFYQXBl2+odnKPi4w2r78NBc5xjeambx9spnFixdjQg3IM8WcR
iQycE0xyNN+81XHfqnHd4blsjDwSXWXavVcStkNr/+XeTWYRUc+ZruwXtuhxkYze
Sf7dNXGiFSeUHM9h4ya7b6NnJSFd5t0dCy5oGzuCr+yDZ4XUmFF0sbmZgIn/f3gZ
XHlKYC6SQK5MNyosycdiyA5d9zZbyuAlJQG03RoHnHcAP9Dc1ew91Pq7P8yF1m9/
qS3fuQL39ZeatTXaw2ewh0qpKJ4jjv9cJ2vhsE/zB+4ALtRZh8tSQZXq9EfX7mRB
VXyNWQKV3WKdwrnuWih0hKWbt5DHDAff9Yk2dDLWKMGwsAvgnEzDHNb842m1R0aB
L6KCq9NjRHDEjf8tM7qtj3u1cIiuPhnPQCjY/MiQu12ZIvVS5ljFH4gxQ+6IHdfG
jjxDah2nGN59PRbxYvnKkKj9
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICjzCCAhWgAwIBAgIQXIuZxVqUxdJxVt7NiYDMJjAKBggqhkjOPQQDAzCBiDEL
MAkGA1UEBhMCVVMxEzARBgNVBAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0plcnNl
eSBDaXR5MR4wHAYDVQQKExVUaGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNVBAMT
JVVTRVJUcnVzdCBFQ0MgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTAwMjAx
MDAwMDAwWhcNMzgwMTE4MjM1OTU5WjCBiDELMAkGA1UEBhMCVVMxEzARBgNVBAgT
Ck5ldyBKZXJzZXkxFDASBgNVBAcTC0plcnNleSBDaXR5MR4wHAYDVQQKExVUaGUg
VVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNVBAMTJVVTRVJUcnVzdCBFQ0MgQ2VydGlm
aWNhdGlvbiBBdXRob3JpdHkwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAAQarFRaqflo
I+d61SRvU8Za2EurxtW20eZzca7dnNYMYf3boIkDuAUU7FfO7l0/4iGzzvfUinng
o4N+LZfQYcTxmdwlkWOrfzCjtHDix6EznPO/LlxTsV+zfTJ/ijTjeXmjQjBAMB0G
A1UdDgQWBBQ64QmG1M8ZwpZ2dEl23OA1xmNjmjAOBgNVHQ8BAf8EBAMCAQYwDwYD
VR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAwNoADBlAjA2Z6EWCNzklwBBHU6+4WMB
zzuqQhFkoJ2UOQIReVx7Hfpkue4WQrO/isIJxOzksU0CMQDpKmFHjFJKS04YcPbW
RNZu9YO6bVi9JNlWSOrvxKJGgYhqOkbRqZtNyWHa0V1Xahg=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICHjCCAaSgAwIBAgIRYFlJ4CYuu1X5CneKcflK2GwwCgYIKoZIzj0EAwMwUDEk
MCIGA1UECxMbR2xvYmFsU2lnbiBFQ0MgUm9vdCBDQSAtIFI1MRMwEQYDVQQKEwpH
bG9iYWxTaWduMRMwEQYDVQQDEwpHbG9iYWxTaWduMB4XDTEyMTExMzAwMDAwMFoX
DTM4MDExOTAzMTQwN1owUDEkMCIGA1UECxMbR2xvYmFsU2lnbiBFQ0MgUm9vdCBD
QSAtIFI1MRMwEQYDVQQKEwpHbG9iYWxTaWduMRMwEQYDVQQDEwpHbG9iYWxTaWdu
MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAER0UOlvt9Xb/pOdEh+J8LttV7HpI6SFkc
8GIxLcB6KP4ap1yztsyX50XUWPrRd21DosCHZTQKH3rd6zwzocWdTaRvQZU4f8ke
hOvRnkmSh5SHDDqFSmafnVmTTZdhBoZKo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYD
VR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUPeYpSJvqB8ohREom3m7e0oPQn1kwCgYI
KoZIzj0EAwMDaAAwZQIxAOVpEslu28YxuglB4Zf4+/2a4n0Sye18ZNPLBSWLVtmg
515dTguDnFt2KaAJJiFqYgIwcdK1j1zqO+F4CYWodZI7yFz9SO8NdCKoCOJuxUnO
xwy8p2Fp8fc74SrL+SvzZpA3
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFYDCCA0igAwIBAgIQCgFCgAAAAUUjyES1AAAAAjANBgkqhkiG9w0BAQsFADBK
MQswCQYDVQQGEwJVUzESMBAGA1UEChMJSWRlblRydXN0MScwJQYDVQQDEx5JZGVu
VHJ1c3QgQ29tbWVyY2lhbCBSb290IENBIDEwHhcNMTQwMTE2MTgxMjIzWhcNMzQw
MTE2MTgxMjIzWjBKMQswCQYDVQQGEwJVUzESMBAGA1UEChMJSWRlblRydXN0MScw
JQYDVQQDEx5JZGVuVHJ1c3QgQ29tbWVyY2lhbCBSb290IENBIDEwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQCnUBneP5k91DNG8W9RYYKyqU+PZ4ldhNlT
3Qwo2dfw/66VQ3KZ+bVdfIrBQuExUHTRgQ18zZshq0PirK1ehm7zCYofWjK9ouuU
+ehcCuz/mNKvcbO0U59Oh++SvL3sTzIwiEsXXlfEU8L2ApeN2WIrvyQfYo3fw7gp
S0l4PJNgiCL8mdo2yMKi1CxUAGc1bnO/AljwpN3lsKImesrgNqUZFvX9t++uP0D1
bVoE/c40yiTcdCMbXTMTEl3EASX2MN0CXZ/g1Ue9tOsbobtJSdifWwLziuQkkORi
T0/Br4sOdBeo0XKIanoBScy0RnnGF7HamB4HWfp1IYVl3ZBWzvurpWCdxJ35UrCL
vYf5jysjCiN2O/cz4ckA82n5S6LgTrx+kzmEB/dEcH7+B1rlsazRGMzyNeVJSQjK
Vsk9+w8YfYs7wRPCTY/JTw436R+hDmrfYi7LNQZReSzIJTj0+kuniVyc0uMNOYZK
dHzVWYfCP04MXFL0PfdSgvHqo6z9STQaKPNBiDoT7uje/5kdX7rL6B7yuVBgwDHT
c+XvvqDtMwt0viAgxGds8AgDelWAf0ZOlqf0Hj7h9tgJ4TNkK2PXMl6f+cB7D3hv
l7yTmvmcEpB4eoCHFddydJxVdHixuuFucAS6T6C6aMN7/zHwcz09lCqxC0EOoP5N
iGVreTO01wIDAQABo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB
/zAdBgNVHQ4EFgQU7UQZwNPwBovupHu+QucmVMiONnYwDQYJKoZIhvcNAQELBQAD
ggIBAA2ukDL2pkt8RHYZYR4nKM1eVO8lvOMIkPkp165oCOGUAFjvLi5+U1KMtlwH
6oi6mYtQlNeCgN9hCQCTrQ0U5s7B8jeUeLBfnLOic7iPBZM4zY0+sLj7wM+x8uwt
LRvM7Kqas6pgghstO8OEPVeKlh6cdbjTMM1gCIOQ045U8U1mwF10A0Cj7oV+wh93
nAbowacYXVKV7cndJZ5t+qntozo00Fl72u1Q8zW/7esUTTHHYPTa8Yec4kjixsU3
+wYQ+nVZZjFHKdp2mhzpgq7vmrlR94gjmmmVYjzlVYA211QC//G5Xc7UI2/YRYRK
W2XviQzdFKcgyxilJbQN+QHwotL0AMh0jqEqSI5l2xPE4iUXfeu+h1sXIFRRk0pT
AwvsXcoz7WL9RccvW9xYoIA55vrX/hMUpu09lEpCdNTDd1lzzY9GvlU47/rokTLq
l1gEIt44w8y8bckzOmoKaT+gyOpyj4xjhiO9bTyWnpXgSUyqorkqG5w2gXjtw+hG
4iZZRHUe2XWJUc0QhJ1hYMtd+ZciTY6Y5uN/9lu7rs3KSoFrXgvzUeF0K+l+J6fZ
mUlO+KWA2yUPHGNiiskzZ2s8EIPGrd6ozRaOjfAHN3Gf8qv8QfXBi+wAN10J5U6A
7/qxXDgGpRtK4dw4LTzcqx+QGtVKnO7RcGzM7vRX+Bi6hG6H
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFZjCCA06gAwIBAgIQCgFCgAAAAUUjz0Z8AAAAAjANBgkqhkiG9w0BAQsFADBN
MQswCQYDVQQGEwJVUzESMBAGA1UEChMJSWRlblRydXN0MSowKAYDVQQDEyFJZGVu
VHJ1c3QgUHVibGljIFNlY3RvciBSb290IENBIDEwHhcNMTQwMTE2MTc1MzMyWhcN
MzQwMTE2MTc1MzMyWjBNMQswCQYDVQQGEwJVUzESMBAGA1UEChMJSWRlblRydXN0
MSowKAYDVQQDEyFJZGVuVHJ1c3QgUHVibGljIFNlY3RvciBSb290IENBIDEwggIi
MA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQC2IpT8pEiv6EdrCvsnduTyP4o7
ekosMSqMjbCpwzFrqHd2hCa2rIFCDQjrVVi7evi8ZX3yoG2LqEfpYnYeEe4IFNGy
RBb06tD6Hi9e28tzQa68ALBKK0CyrOE7S8ItneShm+waOh7wCLPQ5CQ1B5+ctMlS
bdsHyo+1W/CD80/HLaXIrcuVIKQxKFdYWuSNG5qrng0M8gozOSI5Cpcu81N3uURF
/YTLNiCBWS2ab21ISGHKTN9T0a9SvESfqy9rg3LvdYDaBjMbXcjaY8ZNzaxmMc3R
3j6HEDbhuaR672BQssvKplbgN6+rNBM5Jeg5ZuSYeqoSmJxZZoY+rfGwyj4GD3vw
EUs3oERte8uojHH01bWRNszwFcYr3lEXsZdMUD2xlVl8BX0tIdUAvwFnol57plzy
9yLxkA2T26pEUWbMfXYD62qoKjgZl3YNa4ph+bz27nb9cCvdKTz4Ch5bQhyLVi9V
GxyhLrXHFub4qjySjmm2AcG1hp2JDws4lFTo6tyePSW8Uybt1as5qsVATFSrsrTZ
2fjXctscvG29ZV/viDUqZi/u9rNl8DONfJhBaUYPQxxp+pu10GFqzcpL2UyQRqsV
WaFHVCkugyhfHMKiq3IXAAaOReyL4jM9f9oZRORicsPfIsbyVtTdX5Vy7W1f90gD
W/3FKqD2cyOEEBsB5wIDAQABo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/
BAUwAwEB/zAdBgNVHQ4EFgQU43HgntinQtnbcZFrlJPrw6PRFKMwDQYJKoZIhvcN
AQELBQADggIBAEf63QqwEZE4rU1d9+UOl1QZgkiHVIyqZJnYWv6IAcVYpZmxI1Qj
t2odIFflAWJBF9MJ23XLblSQdf4an4EKwt3X9wnQW3IV5B4Jaj0z8yGa5hV+rVHV
DRDtfULAj+7AmgjVQdZcDiFpboBhDhXAuM/FSRJSzL46zNQuOAXeNf0fb7iAaJg9
TaDKQGXSc3z1i9kKlT/YPyNtGtEqJBnZhbMX73huqVjRI9PHE+1yJX9dsXNw0H8G
lwmEKYBhHfpe/3OsoOOJuBxxFcbeMX8S3OFtm6/n6J91eEyrRjuazr8FGF1NFTwW
mhlQBJqymm9li1JfPFgEKCXAZmExfrngdbkaqIHWchezxQMxNRF4eKLg6TCMf4Df
WN88uieW4oA0beOY02QnrEh+KHdcxiVhJfiFDGX6xDIvpZgF5PgLZxYWxoK4Mhn5
+bl53B/N66+rDt0b20XkeucC4pVd/GnwU2lhlXV5C15V5jgclKlZM57IcXR5f1GJ
tshquDDIajjDbp7hNxbqBWJMWxJH7ae0s1hWx0nzfxJoCTFx8G34Tkf71oXuxVhA
GaQdp/lLQzfcaFpPz+vCZHTetBXZ9FRUGi8c15dxVJCO2SCdUyt/q4/i6jC8UDfv
8Ue1fXwsBOxonbRJRBD0ckscZOf85muQ3Wl9af0AVqW3rLatt8o+Ae+c
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEPjCCAyagAwIBAgIESlOMKDANBgkqhkiG9w0BAQsFADCBvjELMAkGA1UEBhMC
VVMxFjAUBgNVBAoTDUVudHJ1c3QsIEluYy4xKDAmBgNVBAsTH1NlZSB3d3cuZW50
cnVzdC5uZXQvbGVnYWwtdGVybXMxOTA3BgNVBAsTMChjKSAyMDA5IEVudHJ1c3Qs
IEluYy4gLSBmb3IgYXV0aG9yaXplZCB1c2Ugb25seTEyMDAGA1UEAxMpRW50cnVz
dCBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IC0gRzIwHhcNMDkwNzA3MTcy
NTU0WhcNMzAxMjA3MTc1NTU0WjCBvjELMAkGA1UEBhMCVVMxFjAUBgNVBAoTDUVu
dHJ1c3QsIEluYy4xKDAmBgNVBAsTH1NlZSB3d3cuZW50cnVzdC5uZXQvbGVnYWwt
dGVybXMxOTA3BgNVBAsTMChjKSAyMDA5IEVudHJ1c3QsIEluYy4gLSBmb3IgYXV0
aG9yaXplZCB1c2Ugb25seTEyMDAGA1UEAxMpRW50cnVzdCBSb290IENlcnRpZmlj
YXRpb24gQXV0aG9yaXR5IC0gRzIwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK
AoIBAQC6hLZy254Ma+KZ6TABp3bqMriVQRrJ2mFOWHLP/vaCeb9zYQYKpSfYs1/T
RU4cctZOMvJyig/3gxnQaoCAAEUesMfnmr8SVycco2gvCoe9amsOXmXzHHfV1IWN
cCG0szLni6LVhjkCsbjSR87kyUnEO6fe+1R9V77w6G7CebI6C1XiUJgWMhNcL3hW
wcKUs/Ja5CeanyTXxuzQmyWC48zCxEXFjJd6BmsqEZ+pCm5IO2/b1BEZQvePB7/1
U1+cPvQXLOZprE4yTGJ36rfo5bs0vBmLrpxR57d+tVOxMyLlbc9wPBr64ptntoP0
jaWvYkxN4FisZDQSA/i2jZRjJKRxAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAP
BgNVHRMBAf8EBTADAQH/MB0GA1UdDgQWBBRqciZ60B7vfec7aVHUbI2fkBJmqzAN
BgkqhkiG9w0BAQsFAAOCAQEAeZ8dlsa2eT8ijYfThwMEYGprmi5ZiXMRrEPR9RP/
jTkrwPK9T3CMqS/qF8QLVJ7UG5aYMzyorWKiAHarWWluBh1+xLlEjZivEtRh2woZ
Rkfz6/djwUAFQKXSt/S1mja/qYh2iARVBCuch38aNzx+LaUa2NSJXsq9rD1s2G2v
1fN2D807iDginWyTmsQ9v4IbZT+mD12q/OWyFcq1rca8PdCE6OoGcrBNOTJ4vz4R
nAuknZoh8/CbCzB428Hch0P+vGOaysXCHMnHjf87ElgI5rY97HosTvuDls4MPGmH
VHOkc8KT/1EQrBVUAdj8BbGJoX90g5pJ19xOe4pIb4tF9g==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIC+TCCAoCgAwIBAgINAKaLeSkAAAAAUNCR+TAKBggqhkjOPQQDAzCBvzELMAkG
A1UEBhMCVVMxFjAUBgNVBAoTDUVudHJ1c3QsIEluYy4xKDAmBgNVBAsTH1NlZSB3
d3cuZW50cnVzdC5uZXQvbGVnYWwtdGVybXMxOTA3BgNVBAsTMChjKSAyMDEyIEVu
dHJ1c3QsIEluYy4gLSBmb3IgYXV0aG9yaXplZCB1c2Ugb25seTEzMDEGA1UEAxMq
RW50cnVzdCBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IC0gRUMxMB4XDTEy
MTIxODE1MjUzNloXDTM3MTIxODE1NTUzNlowgb8xCzAJBgNVBAYTAlVTMRYwFAYD
VQQKEw1FbnRydXN0LCBJbmMuMSgwJgYDVQQLEx9TZWUgd3d3LmVudHJ1c3QubmV0
L2xlZ2FsLXRlcm1zMTkwNwYDVQQLEzAoYykgMjAxMiBFbnRydXN0LCBJbmMuIC0g
Zm9yIGF1dGhvcml6ZWQgdXNlIG9ubHkxMzAxBgNVBAMTKkVudHJ1c3QgUm9vdCBD
ZXJ0aWZpY2F0aW9uIEF1dGhvcml0eSAtIEVDMTB2MBAGByqGSM49AgEGBSuBBAAi
A2IABIQTydC6bUF74mzQ61VfZgIaJPRbiWlH47jCffHyAsWfoPZb1YsGGYZPUxBt
ByQnoaD41UcZYUx9ypMn6nQM72+WCf5j7HBdNq1nd67JnXxVRDqiY1Ef9eNi1KlH
Bz7MIKNCMEAwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0O
BBYEFLdj5xrdjekIplWDpOBqUEFlEUJJMAoGCCqGSM49BAMDA2cAMGQCMGF52OVC
R98crlOZF7ZvHH3hvxGU0QOIdeSNiaSKd0bebWHvAvX7td/M/k7//qnmpwIwW5nX
hTcGtXsI/esni0qU+eH6p44mCOh8kmhtc9hvJqwhAriZtyZBWyVgrtBIGu4G
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFjTCCA3WgAwIBAgIEGErM1jANBgkqhkiG9w0BAQsFADBWMQswCQYDVQQGEwJD
TjEwMC4GA1UECgwnQ2hpbmEgRmluYW5jaWFsIENlcnRpZmljYXRpb24gQXV0aG9y
aXR5MRUwEwYDVQQDDAxDRkNBIEVWIFJPT1QwHhcNMTIwODA4MDMwNzAxWhcNMjkx
MjMxMDMwNzAxWjBWMQswCQYDVQQGEwJDTjEwMC4GA1UECgwnQ2hpbmEgRmluYW5j
aWFsIENlcnRpZmljYXRpb24gQXV0aG9yaXR5MRUwEwYDVQQDDAxDRkNBIEVWIFJP
T1QwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDXXWvNED8fBVnVBU03
sQ7smCuOFR36k0sXgiFxEFLXUWRwFsJVaU2OFW2fvwwbwuCjZ9YMrM8irq93VCpL
TIpTUnrD7i7es3ElweldPe6hL6P3KjzJIx1qqx2hp/Hz7KDVRM8Vz3IvHWOX6Jn5
/ZOkVIBMUtRSqy5J35DNuF++P96hyk0g1CXohClTt7GIH//62pCfCqktQT+x8Rgp
7hZZLDRJGqgG16iI0gNyejLi6mhNbiyWZXvKWfry4t3uMCz7zEasxGPrb382KzRz
EpR/38wmnvFyXVBlWY9ps4deMm/DGIq1lY+wejfeWkU7xzbh72fROdOXW3NiGUgt
hxwG+3SYIElz8AXSG7Ggo7cbcNOIabla1jj0Ytwli3i/+Oh+uFzJlU9fpy25IGvP
a931DfSCt/SyZi4QKPaXWnuWFo8BGS1sbn85WAZkgwGDg8NNkt0yxoekN+kWzqot
aK8KgWU6cMGbrU1tVMoqLUuFG7OA5nBFDWteNfB/O7ic5ARwiRIlk9oKmSJgamNg
TnYGmE69g60dWIolhdLHZR4tjsbftsbhf4oEIRUpdPA+nJCdDC7xij5aqgwJHsfV
PKPtl8MeNPo4+QgO48BdK4PRVmrJtqhUUy54Mmc9gn900PvhtgVguXDbjgv5E1hv
cWAQUhC5wUEJ73IfZzF4/5YFjQIDAQABo2MwYTAfBgNVHSMEGDAWgBTj/i39KNAL
tbq2osS/BqoFjJP7LzAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBBjAd
BgNVHQ4EFgQU4/4t/SjQC7W6tqLEvwaqBYyT+y8wDQYJKoZIhvcNAQELBQADggIB
ACXGumvrh8vegjmWPfBEp2uEcwPenStPuiB/vHiyz5ewG5zz13ku9Ui20vsXiObT
ej/tUxPQ4i9qecsAIyjmHjdXNYmEwnZPNDatZ8POQQaIxffu2Bq41gt/UP+TqhdL
jOztUmCypAbqTuv0axn96/Ua4CUqmtzHQTb3yHQFhDmVOdYLO6Qn+gjYXB74BGBS
ESgoA//vU2YApUo0FmZ8/Qmkrp5nGm9BC2sGE5uPhnEFtC+NiWYzKXZUmhH4J/qy
P5Hgzg0b8zAarb8iXRvTvyUFTeGSGn+ZnzxEk8rUQElsgIfXBDrDMlI1Dlb4pd19
xIsNER9Tyx6yF7Zod1rg1MvIB671Oi6ON7fQAUtDKXeMOZePglr4UeWJoBjnaH9d
Ci77o0cOPaYjesYBx4/IXr9tgFa+iiS6M+qf4TIRnvHST4D2G0CvOJ4RUHlzEhLN
5mydLIhyPDCBBpEi6lmt2hkuIsKNuYyH4Ga8cyNfIWRjgEj1oDwYPZTISEEdQLpe
/v5WOaHIz16eGWRGENoXkbcFgKyLmZJ956LYBws2J+dIeWCKw9cTXPhyQN9Ky8+Z
AAoACxGV2lZFA4gKn2fQ1XmxqI1AbQ3CekD6819kR5LLU7m7Wc5P/dAVUwHY3+vZ
5nbv0CO7O6l5s9UCKc2Jo5YPSjXnTkLAdc0Hz+Ys63su
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDtTCCAp2gAwIBAgIQdrEgUnTwhYdGs/gjGvbCwDANBgkqhkiG9w0BAQsFADBt
MQswCQYDVQQGEwJDSDEQMA4GA1UEChMHV0lTZUtleTEiMCAGA1UECxMZT0lTVEUg
Rm91bmRhdGlvbiBFbmRvcnNlZDEoMCYGA1UEAxMfT0lTVEUgV0lTZUtleSBHbG9i
YWwgUm9vdCBHQiBDQTAeFw0xNDEyMDExNTAwMzJaFw0zOTEyMDExNTEwMzFaMG0x
CzAJBgNVBAYTAkNIMRAwDgYDVQQKEwdXSVNlS2V5MSIwIAYDVQQLExlPSVNURSBG
b3VuZGF0aW9uIEVuZG9yc2VkMSgwJgYDVQQDEx9PSVNURSBXSVNlS2V5IEdsb2Jh
bCBSb290IEdCIENBMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2Be3
HEokKtaXscriHvt9OO+Y9bI5mE4nuBFde9IllIiCFSZqGzG7qFshISvYD06fWvGx
WuR51jIjK+FTzJlFXHtPrby/h0oLS5daqPZI7H17Dc0hBt+eFf1Biki3IPShehtX
1F1Q/7pn2COZH8g/497/b1t3sWtuuMlk9+HKQUYOKXHQuSP8yYFfTvdv37+ErXNk
u7dCjmn21HYdfp2nuFeKUWdy19SouJVUQHMD9ur06/4oQnc/nSMbsrY9gBQHTC5P
99UKFg29ZkM3fiNDecNAhvVMKdqOmq0NpQSHiB6F4+lT1ZvIiwNjeOvgGUpuuy9r
M2RYk61pv48b74JIxwIDAQABo1EwTzALBgNVHQ8EBAMCAYYwDwYDVR0TAQH/BAUw
AwEB/zAdBgNVHQ4EFgQUNQ/INmNe4qPs+TtmFc5RUuORmj0wEAYJKwYBBAGCNxUB
BAMCAQAwDQYJKoZIhvcNAQELBQADggEBAEBM+4eymYGQfp3FsLAmzYh7KzKNbrgh
cViXfa43FK8+5/ea4n32cZiZBKpDdHij40lhPnOMTZTg+XHEthYOU3gf1qKHLwI5
gSk8rxWYITD+KJAAjNHhy/peyP34EEY7onhCkRd0VQreUGdNZtGn//3ZwLWoo4rO
ZvUPQ82nK1d7Y0Zqqi5S2PTt4W2tKZB4SLrhI6qjiey1q5bAtEuiHZeeevJuQHHf
aPFlTc58Bd9TZaml8LGXBHAVRgOY1NK/VLSgWH1Sb9pWJmLU2NuJMW8c8CLC02Ic
Nc1MaRVUGpCY3useX8p3x8uOPUNpnJpY0CQ73xtAln41rYHHTnG6iBM=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDcjCCAlqgAwIBAgIUPopdB+xV0jLVt+O2XwHrLdzk1uQwDQYJKoZIhvcNAQEL
BQAwUTELMAkGA1UEBhMCUEwxKDAmBgNVBAoMH0tyYWpvd2EgSXpiYSBSb3psaWN6
ZW5pb3dhIFMuQS4xGDAWBgNVBAMMD1NaQUZJUiBST09UIENBMjAeFw0xNTEwMTkw
NzQzMzBaFw0zNTEwMTkwNzQzMzBaMFExCzAJBgNVBAYTAlBMMSgwJgYDVQQKDB9L
cmFqb3dhIEl6YmEgUm96bGljemVuaW93YSBTLkEuMRgwFgYDVQQDDA9TWkFGSVIg
Uk9PVCBDQTIwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQC3vD5QqEvN
QLXOYeeWyrSh2gwisPq1e3YAd4wLz32ohswmUeQgPYUM1ljj5/QqGJ3a0a4m7utT
3PSQ1hNKDJA8w/Ta0o4NkjrcsbH/ON7Dui1fgLkCvUqdGw+0w8LBZwPd3BucPbOw
3gAeqDRHu5rr/gsUvTaE2g0gv/pby6kWIK05YO4vdbbnl5z5Pv1+TW9NL++IDWr6
3fE9biCloBK0TXC5ztdyO4mTp4CEHCdJckm1/zuVnsHMyAHs6A6KCpbns6aH5db5
BSsNl0BwPLqsdVqc1U2dAgrSS5tmS0YHF2Wtn2yIANwiieDhZNRnvDF5YTy7ykHN
XGoAyDw4jlivAgMBAAGjQjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQD
AgEGMB0GA1UdDgQWBBQuFqlKGLXLzPVvUPMjX/hd56zwyDANBgkqhkiG9w0BAQsF
AAOCAQEAtXP4A9xZWx126aMqe5Aosk3AM0+qmrHUuOQn/6mWmc5G4G18TKI4pAZw
8PRBEew/R40/cof5O/2kbytTAOD/OblqBw7rHRz2onKQy4I9EYKL0rufKq8h5mOG
nXkZ7/e7DDWQw4rtTw/1zBLZpD67oPwglV9PJi8RI4NOdQcPv5vRtB3pEAT+ymCP
oky4rc/hkA/NrgrHXXu3UNLUYfrVFdvXn4dRVOul4+vJhaAlIDf7js4MNIThPIGy
d05DpYhfhmehPea0XGG2Ptv+tyjFogeutcrKjSoS75ftwjCkySp6+/NNIxuZMzSg
LvWpCz/UXeHPhJ/iGcJfitYgHuNztw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF0jCCA7qgAwIBAgIQIdbQSk8lD8kyN/yqXhKN6TANBgkqhkiG9w0BAQ0FADCB
gDELMAkGA1UEBhMCUEwxIjAgBgNVBAoTGVVuaXpldG8gVGVjaG5vbG9naWVzIFMu
QS4xJzAlBgNVBAsTHkNlcnR1bSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTEkMCIG
A1UEAxMbQ2VydHVtIFRydXN0ZWQgTmV0d29yayBDQSAyMCIYDzIwMTExMDA2MDgz
OTU2WhgPMjA0NjEwMDYwODM5NTZaMIGAMQswCQYDVQQGEwJQTDEiMCAGA1UEChMZ
VW5pemV0byBUZWNobm9sb2dpZXMgUy5BLjEnMCUGA1UECxMeQ2VydHVtIENlcnRp
ZmljYXRpb24gQXV0aG9yaXR5MSQwIgYDVQQDExtDZXJ0dW0gVHJ1c3RlZCBOZXR3
b3JrIENBIDIwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQC9+Xj45tWA
DGSdhhuWZGc/IjoedQF97/tcZ4zJzFxrqZHmuULlIEub2pt7uZld2ZuAS9eEQCsn
0+i6MLs+CRqnSZXvK0AkwpfHp+6bJe+oCgCXhVqqndwpyeI1B+twTUrWwbNWuKFB
OJvR+zF/j+Bf4bE/D44WSWDXBo0Y+aomEKsq09DRZ40bRr5HMNUuctHFY9rnY3lE
fktjJImGLjQ/KUxSiyqnwOKRKIm5wFv5HdnnJ63/mgKXwcZQkpsCLL2puTRZCr+E
Sv/f/rOf69me4Jgj7KZrdxYq28ytOxykh9xGc14ZYmhFV+SQgkK7QtbwYeDBoz1m
o130GO6IyY0XRSmZMnUCMe4pJshrAua1YkV/NxVaI2iJ1D7eTiew8EAMvE0Xy02i
sx7QBlrd9pPPV3WZ9fqGGmd4s7+W/jTcvedSVuWz5XV710GRBdxdaeOVDUO5/IOW
OZV7bIBaTxNyxtd9KXpEulKkKtVBRgkg/iKgtlswjbyJDNXXcPiHUv3a76xRLgez
Tv7QCdpw75j6VuZt27VXS9zlLCUVyJ4ueE742pyehizKV/Ma5ciSixqClnrDvFAS
adgOWkaLOusm+iPJtrCBvkIApPjW/jAux9JG9uWOdf3yzLnQh1vMBhBgu4M1t15n
3kfsmUjxpKEV/q2MYo45VU85FrmxY53/twIDAQABo0IwQDAPBgNVHRMBAf8EBTAD
AQH/MB0GA1UdDgQWBBS2oVQ5AsOgP46KvPrU+Bym0ToO/TAOBgNVHQ8BAf8EBAMC
AQYwDQYJKoZIhvcNAQENBQADggIBAHGlDs7k6b8/ONWJWsQCYftMxRQXLYtPU2sQ
F/xlhMcQSZDe28cmk4gmb3DWAl45oPePq5a1pRNcgRRtDoGCERuKTsZPpd1iHkTf
CVn0W3cLN+mLIMb4Ck4uWBzrM9DPhmDJ2vuAL55MYIR4PSFk1vtBHxgP58l1cb29
XN40hz5BsA72udY/CROWFC/emh1auVbONTqwX3BNXuMp8SMoclm2q8KMZiYcdywm
djWLKKdpoPk79SPdhRB0yZADVpHnr7pH1BKXESLjokmUbOe3lEu6LaTaM4tMpkT/
WjzGHWTYtTHkpjx6qFcL2+1hGsvxznN3Y6SHb0xRONbkX8eftoEq5IVIeVheO/jb
AoJnwTnbw3RLPTYe+SmTiGhbqEQZIfCn6IENLOiTNrQ3ssqwGyZ6miUfmpqAnksq
P/ujmv5zMnHCnsZy4YpoJ/HkD7TETKVhk/iXEAcqMCWpuchxuO9ozC1+9eB+D4Ko
b7a6bINDd82Kkhehnlt4Fj1F4jNy3eFmypnTycUm/Q1oBEauttmbjL4ZvrHG8hnj
XALKLNhvSgfZyTXaQHXyxKcZb55CEJh15pWLYLztxRLXis7VmFxWlgPF7ncGNf/P
5O4/E2Hu29othfDNrp2yGAlFw5Khchf8R7agCyzxxN5DaAhqXzvwdmP7zAYspsbi
DrW5viSP
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIGCzCCA/OgAwIBAgIBADANBgkqhkiG9w0BAQsFADCBpjELMAkGA1UEBhMCR1Ix
DzANBgNVBAcTBkF0aGVuczFEMEIGA1UEChM7SGVsbGVuaWMgQWNhZGVtaWMgYW5k
IFJlc2VhcmNoIEluc3RpdHV0aW9ucyBDZXJ0LiBBdXRob3JpdHkxQDA+BgNVBAMT
N0hlbGxlbmljIEFjYWRlbWljIGFuZCBSZXNlYXJjaCBJbnN0aXR1dGlvbnMgUm9v
dENBIDIwMTUwHhcNMTUwNzA3MTAxMTIxWhcNNDAwNjMwMTAxMTIxWjCBpjELMAkG
A1UEBhMCR1IxDzANBgNVBAcTBkF0aGVuczFEMEIGA1UEChM7SGVsbGVuaWMgQWNh
ZGVtaWMgYW5kIFJlc2VhcmNoIEluc3RpdHV0aW9ucyBDZXJ0LiBBdXRob3JpdHkx
QDA+BgNVBAMTN0hlbGxlbmljIEFjYWRlbWljIGFuZCBSZXNlYXJjaCBJbnN0aXR1
dGlvbnMgUm9vdENBIDIwMTUwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoIC
AQDC+Kk/G4n8PDwEXT2QNrCROnk8ZlrvbTkBSRq0t89/TSNTt5AA4xMqKKYx8ZEA
4yjsriFBzh/a/X0SWwGDD7mwX5nh8hKDgE0GPt+sr+ehiGsxr/CL0BgzuNtFajT0
AoAkKAoCFZVedioNmToUW/bLy1O8E00BiDeUJRtCvCLYjqOWXjrZMts+6PAQZe10
4S+nfK8nNLspfZu2zwnI5dMK/IhlZXQK3HMcXM1AsRzUtoSMTFDPaI6oWa7CJ06C
ojXdFPQf/7J31Ycvqm59JCfnxssm5uX+Zwdj2EUN3TpZZTlYepKZcj2chF6IIbjV
9Cz82XBST3i4vTwri5WY9bPRaM8gFH5MXF/ni+X1NYEZN9cRCLdmvtNKzoNXADrD
gfgXy5I2XdGj2HUb4Ysn6npIQf1FGQatJ5lOwXBH3bWfgVMS5bGMSF0xQxfjjMZ6
Y5ZLKTBOhE5iGV48zpeQpX8B653g+IuJ3SWYPZK2fu/Z8VFRfS0myGlZYeCsargq
NhEEelC9MoS+L9xy1dcdFkfkR2YgP/SWxa+OAXqlD3pk9Q0Yh9muiNX6hME6wGko
LfINaFGq46V3xqSQDqE3izEjR8EJCOtu93ib14L8hCCZSRm2Ekax+0VVFqmjZayc
Bw/qa9wfLgZy7IaIEuQt218FL+TwA9MmM+eAws1CoRc0CwIDAQABo0IwQDAPBgNV
HRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUcRVnyMjJvXVd
ctA4GGqd83EkVAswDQYJKoZIhvcNAQELBQADggIBAHW7bVRLqhBYRjTyYtcWNl0I
XtVsyIe9tC5G8jH4fOpCtZMWVdyhDBKg2mF+D1hYc2Ryx+hFjtyp8iY/xnmMsVMI
M4GwVhO+5lFc2JsKT0ucVlMC6U/2DWDqTUJV6HwbISHTGzrMd/K4kPFox/la/vot
9L/J9UUbzjgQKjeKeaO04wlshYaT/4mWJ3iBj2fjRnRUjtkNaeJK9E10A/+yd+2V
Z5fkscWrv2oj6NSU4kQoYsRL4vDY4ilrGnB+JGGTe08DMiUNRSQrlrRGar9KC/ea
j8GsGsVn82800vpzY4zvFrCopEYq+OsS7HK07/grfoxSwIuEVPkvPuNVqNxmsdnh
X9izjFk0WaSrT2y7HxjbdavYy5LNlDhhDgcGH0tGEPEVvo2FXDtKK4F5D7Rpn0lQ
l033DlZdwJVqwjbDG2jJ9SrcR5q+ss7FJej6A7na+RZukYT1HCjI/CbM1xyQVqdf
bzoEvM14iQuODy+jqk+iGxI9FghAD/FGTNeqewjBCvVtJ94Cj8rDtSvK6evIIVM4
pcw72Hc3MKJP2W/R8kCtQXoXxdZKNYm3QdV8hn9VTYNKpXMgwDqvkPGaJI7ZjnHK
e7iG2rKPmT4dEw0SEe7Uq/DpFXYC5ODfqiAeW2GFZECpkJcNrVPSWh2HagCXZWK0
vm9qp/UsQu0yrbYhnr68
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICwzCCAkqgAwIBAgIBADAKBggqhkjOPQQDAjCBqjELMAkGA1UEBhMCR1IxDzAN
BgNVBAcTBkF0aGVuczFEMEIGA1UEChM7SGVsbGVuaWMgQWNhZGVtaWMgYW5kIFJl
c2VhcmNoIEluc3RpdHV0aW9ucyBDZXJ0LiBBdXRob3JpdHkxRDBCBgNVBAMTO0hl
bGxlbmljIEFjYWRlbWljIGFuZCBSZXNlYXJjaCBJbnN0aXR1dGlvbnMgRUNDIFJv
b3RDQSAyMDE1MB4XDTE1MDcwNzEwMzcxMloXDTQwMDYzMDEwMzcxMlowgaoxCzAJ
BgNVBAYTAkdSMQ8wDQYDVQQHEwZBdGhlbnMxRDBCBgNVBAoTO0hlbGxlbmljIEFj
YWRlbWljIGFuZCBSZXNlYXJjaCBJbnN0aXR1dGlvbnMgQ2VydC4gQXV0aG9yaXR5
MUQwQgYDVQQDEztIZWxsZW5pYyBBY2FkZW1pYyBhbmQgUmVzZWFyY2ggSW5zdGl0
dXRpb25zIEVDQyBSb290Q0EgMjAxNTB2MBAGByqGSM49AgEGBSuBBAAiA2IABJKg
QehLgoRc4vgxEZmGZE4JJS+dQS8KrjVPdJWyUWRrjWvmP3CV8AVER6ZyOFB2lQJa
jq4onvktTpnvLEhvTCUp6NFxW98dwXU3tNf6e3pCnGoKVlp8aQuqgAkkbH7BRqNC
MEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFLQi
C4KZJAEOnLvkDv2/+5cgk5kqMAoGCCqGSM49BAMCA2cAMGQCMGfOFmI4oqxiRaep
lSTAGiecMjvAwNW6qef4BENThe5SId6d9SWDPp5YSy/XZxMOIQIwBeF1Ad5o7Sof
TUwJCA3sS61kFyjndc5FZXIhF8siQQ6ME5g4mlRtm8rifOoCWCKR
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFgzCCA2ugAwIBAgIPXZONMGc2yAYdGsdUhGkHMA0GCSqGSIb3DQEBCwUAMDsx
CzAJBgNVBAYTAkVTMREwDwYDVQQKDAhGTk1ULVJDTTEZMBcGA1UECwwQQUMgUkFJ
WiBGTk1ULVJDTTAeFw0wODEwMjkxNTU5NTZaFw0zMDAxMDEwMDAwMDBaMDsxCzAJ
BgNVBAYTAkVTMREwDwYDVQQKDAhGTk1ULVJDTTEZMBcGA1UECwwQQUMgUkFJWiBG
Tk1ULVJDTTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBALpxgHpMhm5/
yBNtwMZ9HACXjywMI7sQmkCpGreHiPibVmr75nuOi5KOpyVdWRHbNi63URcfqQgf
BBckWKo3Shjf5TnUV/3XwSyRAZHiItQDwFj8d0fsjz50Q7qsNI1NOHZnjrDIbzAz
WHFctPVrbtQBULgTfmxKo0nRIBnuvMApGGWn3v7v3QqQIecaZ5JCEJhfTzC8PhxF
tBDXaEAUwED653cXeuYLj2VbPNmaUtu1vZ5Gzz3rkQUCwJaydkxNEJY7kvqcfw+Z
374jNUUeAlz+taibmSXaXvMiwzn15Cou08YfxGyqxRxqAQVKL9LFwag0Jl1mpdIC
IfkYtwb1TplvqKtMUejPUBjFd8g5CSxJkjKZqLsXF3mwWsXmo8RZZUc1g16p6DUL
mbvkzSDGm0oGObVo/CK67lWMK07q87Hj/LaZmtVC+nFNCM+HHmpxffnTtOmlcYF7
wk5HlqX2doWjKI/pgG6BU6VtX7hI+cL5NqYuSf+4lsKMB7ObiFj86xsc3i1w4peS
MKGJ47xVqCfWS+2QrYv6YyVZLag13cqXM7zlzced0ezvXg5KkAYmY6252TUtB7p2
ZSysV4999AeU14ECll2jB0nVetBX+RvnU0Z1qrB5QstocQjpYL05ac70r8NWQMet
UqIJ5G+GR4of6ygnXYMgrwTJbFaai0b1AgMBAAGjgYMwgYAwDwYDVR0TAQH/BAUw
AwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFPd9xf3E6Jobd2Sn9R2gzL+H
YJptMD4GA1UdIAQ3MDUwMwYEVR0gADArMCkGCCsGAQUFBwIBFh1odHRwOi8vd3d3
LmNlcnQuZm5tdC5lcy9kcGNzLzANBgkqhkiG9w0BAQsFAAOCAgEAB5BK3/MjTvDD
nFFlm5wioooMhfNzKWtN/gHiqQxjAb8EZ6WdmF/9ARP67Jpi6Yb+tmLSbkyU+8B1
RXxlDPiyN8+sD8+Nb/kZ94/sHvJwnvDKuO+3/3Y3dlv2bojzr2IyIpMNOmqOFGYM
LVN0V2Ue1bLdI4E7pWYjJ2cJj+F3qkPNZVEI7VFY/uY5+ctHhKQV8Xa7pO6kO8Rf
77IzlhEYt8llvhjho6Tc+hj507wTmzl6NLrTQfv6MooqtyuGC2mDOL7Nii4LcK2N
JpLuHvUBKwrZ1pebbuCoGRw6IYsMHkCtA+fdZn71uSANA+iW+YJF1DngoABd15jm
fZ5nc8OaKveri6E6FO80vFIOiZiaBECEHX5FaZNXzuvO+FB8TxxuBEOb+dY7Ixjp
6o7RTUaN8Tvkasq6+yO3m/qZASlaWFot4/nUbQ4mrcFuNLwy+AwF+mWj2zs3gyLp
1txyM/1d8iC9djwj2ij3+RvrWWTV3F9yfiD8zYm1kGdNYno/Tq0dwzn+evQoFt9B
9kiABdcPUXmsEKvU7ANm5mqwujGSQkBqvjrTcuFqN1W8rB2Vt2lh8kORdOag0wok
RqEIr9baRRmW1FMdW4R58MD3R++Lj8UGrp1MYp3/RgT408m2ECVAdf4WqslKYIYv
uu8wd+RU4riEmViAqhOLUTpPSPaLtrM=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDQTCCAimgAwIBAgITBmyfz5m/jAo54vB4ikPmljZbyjANBgkqhkiG9w0BAQsF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAxMB4XDTE1MDUyNjAwMDAwMFoXDTM4MDExNzAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALJ4gHHKeNXj
ca9HgFB0fW7Y14h29Jlo91ghYPl0hAEvrAIthtOgQ3pOsqTQNroBvo3bSMgHFzZM
9O6II8c+6zf1tRn4SWiw3te5djgdYZ6k/oI2peVKVuRF4fn9tBb6dNqcmzU5L/qw
IFAGbHrQgLKm+a/sRxmPUDgH3KKHOVj4utWp+UhnMJbulHheb4mjUcAwhmahRWa6
VOujw5H5SNz/0egwLX0tdHA114gk957EWW67c4cX8jJGKLhD+rcdqsq08p8kDi1L
93FcXmn/6pUCyziKrlA4b9v7LWIbxcceVOF34GfID5yHI9Y/QCB/IIDEgEw+OyQm
jgSubJrIqg0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AYYwHQYDVR0OBBYEFIQYzIU07LwMlJQuCFmcx7IQTgoIMA0GCSqGSIb3DQEBCwUA
A4IBAQCY8jdaQZChGsV2USggNiMOruYou6r4lK5IpDB/G/wkjUu0yKGX9rbxenDI
U5PMCCjjmCXPI6T53iHTfIUJrU6adTrCC2qJeHZERxhlbI1Bjjt/msv0tadQ1wUs
N+gDS63pYaACbvXy8MWy7Vu33PqUXHeeE6V/Uq2V8viTO96LXFvKWlJbYK8U90vv
o/ufQJVtMVT8QtPHRh8jrdkPSHCa2XV4cdFyQzR1bldZwgJcJmApzyMZFo6IQ6XU
5MsI+yMRQ+hDKXJioaldXgjUkK642M4UwtBV8ob2xJNDd2ZhwLnoQdeXeGADbkpy
rqXRfboQnoZsG4q5WTP468SQvvG5
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFQTCCAymgAwIBAgITBmyf0pY1hp8KD+WGePhbJruKNzANBgkqhkiG9w0BAQwF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAyMB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMjCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK2Wny2cSkxK
gXlRmeyKy2tgURO8TW0G/LAIjd0ZEGrHJgw12MBvIITplLGbhQPDW9tK6Mj4kHbZ
W0/jTOgGNk3Mmqw9DJArktQGGWCsN0R5hYGCrVo34A3MnaZMUnbqQ523BNFQ9lXg
1dKmSYXpN+nKfq5clU1Imj+uIFptiJXZNLhSGkOQsL9sBbm2eLfq0OQ6PBJTYv9K
8nu+NQWpEjTj82R0Yiw9AElaKP4yRLuH3WUnAnE72kr3H9rN9yFVkE8P7K6C4Z9r
2UXTu/Bfh+08LDmG2j/e7HJV63mjrdvdfLC6HM783k81ds8P+HgfajZRRidhW+me
z/CiVX18JYpvL7TFz4QuK/0NURBs+18bvBt+xa47mAExkv8LV/SasrlX6avvDXbR
8O70zoan4G7ptGmh32n2M8ZpLpcTnqWHsFcQgTfJU7O7f/aS0ZzQGPSSbtqDT6Zj
mUyl+17vIWR6IF9sZIUVyzfpYgwLKhbcAS4y2j5L9Z469hdAlO+ekQiG+r5jqFoz
7Mt0Q5X5bGlSNscpb/xVA1wf+5+9R+vnSUeVC06JIglJ4PVhHvG/LopyboBZ/1c6
+XUyo05f7O0oYtlNc/LMgRdg7c3r3NunysV+Ar3yVAhU/bQtCSwXVEqY0VThUWcI
0u1ufm8/0i2BWSlmy5A5lREedCf+3euvAgMBAAGjQjBAMA8GA1UdEwEB/wQFMAMB
Af8wDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQWBBSwDPBMMPQFWAJI/TPlUq9LhONm
UjANBgkqhkiG9w0BAQwFAAOCAgEAqqiAjw54o+Ci1M3m9Zh6O+oAA7CXDpO8Wqj2
LIxyh6mx/H9z/WNxeKWHWc8w4Q0QshNabYL1auaAn6AFC2jkR2vHat+2/XcycuUY
+gn0oJMsXdKMdYV2ZZAMA3m3MSNjrXiDCYZohMr/+c8mmpJ5581LxedhpxfL86kS
k5Nrp+gvU5LEYFiwzAJRGFuFjWJZY7attN6a+yb3ACfAXVU3dJnJUH/jWS5E4ywl
7uxMMne0nxrpS10gxdr9HIcWxkPo1LsmmkVwXqkLN1PiRnsn/eBG8om3zEK2yygm
btmlyTrIQRNg91CMFa6ybRoVGld45pIq2WWQgj9sAq+uEjonljYE1x2igGOpm/Hl
urR8FLBOybEfdF849lHqm/osohHUqS0nGkWxr7JOcQ3AWEbWaQbLU8uz/mtBzUF+
fUwPfHJ5elnNXkoOrJupmHN5fLT0zLm4BwyydFy4x2+IoZCn9Kr5v2c69BoVYh63
n749sSmvZ6ES8lgQGVMDMBu4Gon2nL2XA46jCfMdiyHxtN/kHNGfZQIG6lzWE7OE
76KlXIx3KadowGuuQNKotOrN8I1LOJwZmhsoVLiJkO/KdYE+HvJkJMcYr07/R54H
9jVlpNMKVv/1F2Rs76giJUmTtt8AF9pYfl3uxRuw0dFfIRDH+fO6AgonB8Xx1sfT
4PsJYGw=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIBtjCCAVugAwIBAgITBmyf1XSXNmY/Owua2eiedgPySjAKBggqhkjOPQQDAjA5
MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6b24g
Um9vdCBDQSAzMB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTELMAkG
A1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJvb3Qg
Q0EgMzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABCmXp8ZBf8ANm+gBG1bG8lKl
ui2yEujSLtf6ycXYqm0fc4E7O5hrOXwzpcVOho6AF2hiRVd9RFgdszflZwjrZt6j
QjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQWBBSr
ttvXBp43rDCGB5Fwx5zEGbF4wDAKBggqhkjOPQQDAgNJADBGAiEA4IWSoxe3jfkr
BqWTrBqYaGFy+uGh0PsceGCmQ5nFuMQCIQCcAu/xlJyzlvnrxir4tiz+OpAUFteM
YyRIHN8wfdVoOw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB8jCCAXigAwIBAgITBmyf18G7EEwpQ+Vxe3ssyBrBDjAKBggqhkjOPQQDAzA5
MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6b24g
Um9vdCBDQSA0MB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTELMAkG
A1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJvb3Qg
Q0EgNDB2MBAGByqGSM49AgEGBSuBBAAiA2IABNKrijdPo1MN/sGKe0uoe0ZLY7Bi
9i0b2whxIdIA6GO9mif78DluXeo9pcmBqqNbIJhFXRbb/egQbeOc4OO9X4Ri83Bk
M6DLJC9wuoihKqB1+IGuYgbEgds5bimwHvouXKNCMEAwDwYDVR0TAQH/BAUwAwEB
/zAOBgNVHQ8BAf8EBAMCAYYwHQYDVR0OBBYEFNPsxzplbszh2naaVvuc84ZtV+WB
MAoGCCqGSM49BAMDA2gAMGUCMDqLIfG9fhGt0O9Yli/W651+kI0rz2ZVwyzjKKlw
CkcO8DdZEv8tmZQoTipPNU0zWgIxAOp1AE47xDqUEpHJWEadIRNyp4iciuRMStuW
1KyLa2tJElMzrdfkviT8tQp21KW8EA==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIEYzCCA0ugAwIBAgIBATANBgkqhkiG9w0BAQsFADCB0jELMAkGA1UEBhMCVFIx
GDAWBgNVBAcTD0dlYnplIC0gS29jYWVsaTFCMEAGA1UEChM5VHVya2l5ZSBCaWxp
bXNlbCB2ZSBUZWtub2xvamlrIEFyYXN0aXJtYSBLdXJ1bXUgLSBUVUJJVEFLMS0w
KwYDVQQLEyRLYW11IFNlcnRpZmlrYXN5b24gTWVya2V6aSAtIEthbXUgU00xNjA0
BgNVBAMTLVRVQklUQUsgS2FtdSBTTSBTU0wgS29rIFNlcnRpZmlrYXNpIC0gU3Vy
dW0gMTAeFw0xMzExMjUwODI1NTVaFw00MzEwMjUwODI1NTVaMIHSMQswCQYDVQQG
EwJUUjEYMBYGA1UEBxMPR2ViemUgLSBLb2NhZWxpMUIwQAYDVQQKEzlUdXJraXll
IEJpbGltc2VsIHZlIFRla25vbG9qaWsgQXJhc3Rpcm1hIEt1cnVtdSAtIFRVQklU
QUsxLTArBgNVBAsTJEthbXUgU2VydGlmaWthc3lvbiBNZXJrZXppIC0gS2FtdSBT
TTE2MDQGA1UEAxMtVFVCSVRBSyBLYW11IFNNIFNTTCBLb2sgU2VydGlmaWthc2kg
LSBTdXJ1bSAxMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAr3UwM6q7
a9OZLBI3hNmNe5eA027n/5tQlT6QlVZC1xl8JoSNkvoBHToP4mQ4t4y86Ij5iySr
LqP1N+RAjhgleYN1Hzv/bKjFxlb4tO2KRKOrbEz8HdDc72i9z+SqzvBV96I01INr
N3wcwv61A+xXzry0tcXtAA9TNypN9E8Mg/uGz8v+jE69h/mniyFXnHrfA2eJLJ2X
YacQuFWQfw4tJzh03+f92k4S400VIgLI4OD8D62K18lUUMw7D8oWgITQUVbDjlZ/
iSIzL+aFCr2lqBs23tPcLG07xxO9WSMs5uWk99gL7eqQQESolbuT1dCANLZGeA4f
AJNG4e7p+exPFwIDAQABo0IwQDAdBgNVHQ4EFgQUZT/HiobGPN08VFw1+DrtUgxH
V8gwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL
BQADggEBACo/4fEyjq7hmFxLXs9rHmoJ0iKpEsdeV31zVmSAhHqT5Am5EM2fKifh
AHe+SMg1qIGf5LgsyX8OsNJLN13qudULXjS99HMpw+0mFZx+CFOKWI3QSyjfwbPf
IPP54+M638yclNhOT8NrF7f3cuitZjO1JVOr4PhMqZ398g26rrnZqsZr+ZO7rqu4
lzwDGrpDxpa5RXI4s6ehlj2Re37AIVNMh+3yC1SVUZPVIqUNivGTDj5UDrDYyU7c
8jEyVupk+eq1nRZmQnLzf9OxMUP8pI4X8W0jq5Rm+K37DwhuJi1/FwcJsoz7UMCf
lo3Ptv0AnVoUmr8CRPXBwp8iXqIPoeM=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFiDCCA3CgAwIBAgIIfQmX/vBH6nowDQYJKoZIhvcNAQELBQAwYjELMAkGA1UE
BhMCQ04xMjAwBgNVBAoMKUdVQU5HIERPTkcgQ0VSVElGSUNBVEUgQVVUSE9SSVRZ
IENPLixMVEQuMR8wHQYDVQQDDBZHRENBIFRydXN0QVVUSCBSNSBST09UMB4XDTE0
MTEyNjA1MTMxNVoXDTQwMTIzMTE1NTk1OVowYjELMAkGA1UEBhMCQ04xMjAwBgNV
BAoMKUdVQU5HIERPTkcgQ0VSVElGSUNBVEUgQVVUSE9SSVRZIENPLixMVEQuMR8w
HQYDVQQDDBZHRENBIFRydXN0QVVUSCBSNSBST09UMIICIjANBgkqhkiG9w0BAQEF
AAOCAg8AMIICCgKCAgEA2aMW8Mh0dHeb7zMNOwZ+Vfy1YI92hhJCfVZmPoiC7XJj
Dp6L3TQsAlFRwxn9WVSEyfFrs0yw6ehGXTjGoqcuEVe6ghWinI9tsJlKCvLriXBj
TnnEt1u9ol2x8kECK62pOqPseQrsXzrj/e+APK00mxqriCZ7VqKChh/rNYmDf1+u
KU49tm7srsHwJ5uu4/Ts765/94Y9cnrrpftZTqfrlYwiOXnhLQiPzLyRuEH3FMEj
qcOtmkVEs7LXLM3GKeJQEK5cy4KOFxg2fZfmiJqwTTQJ9Cy5WmYqsBebnh52nUpm
MUHfP/vFBu8btn4aRjb3ZGM74zkYI+dndRTVdVeSN72+ahsmUPI2JgaQxXABZG12
ZuGR224HwGGALrIuL4xwp9E7PLOR5G62xDtw8mySlwnNR30YwPO7ng/Wi64HtloP
zgsMR6flPri9fcebNaBhlzpBdRfMK5Z3KpIhHtmVdiBnaM8Nvd/WHwlqmuLMc3Gk
L30SgLdTMEZeS1SZD2fJpcjyIMGC7J0R38IC+xo70e0gmu9lZJIQDSri3nDxGGeC
jGHeuLzRL5z7D9Ar7Rt2ueQ5Vfj4oR24qoAATILnsn8JuLwwoC8N9VKejveSswoA
HQBUlwbgsQfZxw9cZX08bVlX5O2ljelAU58VS6Bx9hoh49pwBiFYFIeFd3mqgnkC
AwEAAaNCMEAwHQYDVR0OBBYEFOLJQJ9NzuiaoXzPDj9lxSmIahlRMA8GA1UdEwEB
/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgGGMA0GCSqGSIb3DQEBCwUAA4ICAQDRSVfg
p8xoWLoBDysZzY2wYUWsEe1jUGn4H3++Fo/9nesLqjJHdtJnJO29fDMylyrHBYZm
DRd9FBUb1Ov9H5r2XpdptxolpAqzkT9fNqyL7FeoPueBihhXOYV0GkLH6VsTX4/5
COmSdI31R9KrO9b7eGZONn356ZLpBN79SWP8bfsUcZNnL0dKt7n/HipzcEYwv1ry
L3ml4Y0M2fmyYzeMN2WFcGpcWwlyua1jPLHd+PwyvzeG5LuOmCd+uh8W4XAR8gPf
JWIyJyYYMoSf/wA6E7qaTfRPuBRwIrHKK5DOKcFw9C+df/KQHtZa37dG/OaG+svg
IHZ6uqbL9XzeYqWxi+7egmaKTjowHz+Ay60nugxe19CxVsp3cbK1daFQqUBDF8Io
2c9Si1vIY9RCPqAzekYu9wogRlR+ak8x8YF+QnQ4ZXMn7sZ8uI7XpTrXmKGcjBBV
09tL7ECQ8s1uV9JiDnxXk7Gnbc2dg7sq5+W2O3FYrf3RRbxake5TFW/TRQl1brqQ
XR4EzzffHqhmsYzmIGrv/EhOdJhCrylvLmrH+33RZjEizIYAfmaDDEL0vTSSwxrq
T8p+ck0LcIymSLumoRT2+1hEmRSuqguTaaApJUqlyyvdimYHFngVV3Eb7PVHhPOe
MTd61X8kreS8/f3MboPoDKi3QWwH3b08hpcv0g==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF3TCCA8WgAwIBAgIIeyyb0xaAMpkwDQYJKoZIhvcNAQELBQAwfDELMAkGA1UE
BhMCVVMxDjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMRgwFgYDVQQK
DA9TU0wgQ29ycG9yYXRpb24xMTAvBgNVBAMMKFNTTC5jb20gUm9vdCBDZXJ0aWZp
Y2F0aW9uIEF1dGhvcml0eSBSU0EwHhcNMTYwMjEyMTczOTM5WhcNNDEwMjEyMTcz
OTM5WjB8MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hv
dXN0b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjExMC8GA1UEAwwoU1NMLmNv
bSBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IFJTQTCCAiIwDQYJKoZIhvcN
AQEBBQADggIPADCCAgoCggIBAPkP3aMrfcvQKv7sZ4Wm5y4bunfh4/WvpOz6Sl2R
xFdHaxh3a3by/ZPkPQ/CFp4LZsNWlJ4Xg4XOVu/yFv0AYvUiCVToZRdOQbngT0aX
qhvIuG5iXmmxX9sqAn78bMrzQdjt0Oj8P2FI7bADFB0QDksZ4LtO7IZl/zbzXmcC
C52GVWH9ejjt/uIZALdvoVBidXQ8oPrIJZK0bnoix/geoeOy3ZExqysdBP+lSgQ3
6YWkMyv94tZVNHwZpEpox7Ko07fKoZOI68GXvIz5HdkihCR0xwQ9aqkpk8zruFvh
/l8lqjRYyMEjVJ0bmBHDOJx+PYZspQ9AhnwC9FwCTyjLrnGfDzrIM/4RJTXq/LrF
YD3ZfBjVsqnTdXgDciLKOsMf7yzlLqn6niy2UUb9rwPW6mBo6oUWNmuF6R7As93E
JNyAKoFBbZQ+yODJgUEAnl6/f8UImKIYLEJAs/lvOCdLToD0PYFH4Ih86hzOtXVc
US4cK38acijnALXRdMbX5J+tB5O2UzU1/Dfkw/ZdFr4hc96SCvigY2q8lpJqPvi8
ZVWb3vUNiSYE/CUapiVpy8JtynziWV+XrOvvLsi81xtZPCvM8hnIk2snYxnP/Okm
+Mpxm3+T/jRnhE6Z6/yzeAkzcLpmpnbtG3PrGqUNxCITIJRWCk4sbE6x/c+cCbqi
M+2HAgMBAAGjYzBhMB0GA1UdDgQWBBTdBAkHovV6fVJTEpKV7jiAJQ2mWTAPBgNV
HRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFN0ECQei9Xp9UlMSkpXuOIAlDaZZMA4G
A1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsFAAOCAgEAIBgRlCn7Jp0cHh5wYfGV
cpNxJK1ok1iOMq8bs3AD/CUrdIWQPXhq9LmLpZc7tRiRux6n+UBbkflVma8eEdBc
Hadm47GUBwwyOabqG7B52B2ccETjit3E+ZUfijhDPwGFpUenPUayvOUiaPd7nNgs
PgohyC0zrL/FgZkxdMF1ccW+sfAjRfSda/wZY52jvATGGAslu1OJD7OAUN5F7kR/
q5R4ZJjT9ijdh9hwZXT7DrkT66cPYakylszeu+1jTBi7qUD3oFRuIIhxdRjqerQ0
cuAjJ3dctpDqhiVAq+8zD8ufgr6iIPv2tS0a5sKFsXQP+8hlAqRSAUfdSSLBv9jr
a6x+3uxjMxW3IwiPxg+NQVrdjsW5j+VFP3jbutIbQLH+cU0/4IGiul607BXgk90I
H37hVZkLId6Tngr75qNJvTYw/ud3sqB1l7UtgYgXZSD32pAAn8lSzDLKNXz1PQ/Y
K9f1JmzJBjSWFupwWRoyeXkLtoh/D1JIPb9s2KJELtFOt3JY04kTlf5Eq/jXixtu
nLwsoFvVagCvXzfh1foQC5ichucmj87w7G6KVwuA406ywKBjYZC6VWg3dGq2ktuf
oYYitmUnDuy2n0Jg5GfCtdpBC8TTi2EbvPofkSvXRAdeuims2cXp71NIWuuA8ShY
Ic2wBlX7Jz9TkHCpBB5XJ7k=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICjTCCAhSgAwIBAgIIdebfy8FoW6gwCgYIKoZIzj0EAwIwfDELMAkGA1UEBhMC
VVMxDjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMRgwFgYDVQQKDA9T
U0wgQ29ycG9yYXRpb24xMTAvBgNVBAMMKFNTTC5jb20gUm9vdCBDZXJ0aWZpY2F0
aW9uIEF1dGhvcml0eSBFQ0MwHhcNMTYwMjEyMTgxNDAzWhcNNDEwMjEyMTgxNDAz
WjB8MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0
b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjExMC8GA1UEAwwoU1NMLmNvbSBS
b290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IEVDQzB2MBAGByqGSM49AgEGBSuB
BAAiA2IABEVuqVDEpiM2nl8ojRfLliJkP9x6jh3MCLOicSS6jkm5BBtHllirLZXI
7Z4INcgn64mMU1jrYor+8FsPazFSY0E7ic3s7LaNGdM0B9y7xgZ/wkWV7Mt/qCPg
CemB+vNH06NjMGEwHQYDVR0OBBYEFILRhXMw5zUE044CkvvlpNHEIejNMA8GA1Ud
EwEB/wQFMAMBAf8wHwYDVR0jBBgwFoAUgtGFczDnNQTTjgKS++Wk0cQh6M0wDgYD
VR0PAQH/BAQDAgGGMAoGCCqGSM49BAMCA2cAMGQCMG/n61kRpGDPYbCWe+0F+S8T
kdzt5fxQaxFGRrMcIQBiu77D5+jNB5n5DQtdcj7EqgIwH7y6C+IwJPt8bYBVCpk+
gA0z5Wajs6O7pdWLjwkspl1+4vAHCGht0nxpbl/f5Wpl
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF6zCCA9OgAwIBAgIIVrYpzTS8ePYwDQYJKoZIhvcNAQELBQAwgYIxCzAJBgNV
BAYTAlVTMQ4wDAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjEYMBYGA1UE
CgwPU1NMIENvcnBvcmF0aW9uMTcwNQYDVQQDDC5TU0wuY29tIEVWIFJvb3QgQ2Vy
dGlmaWNhdGlvbiBBdXRob3JpdHkgUlNBIFIyMB4XDTE3MDUzMTE4MTQzN1oXDTQy
MDUzMDE4MTQzN1owgYIxCzAJBgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhhczEQMA4G
A1UEBwwHSG91c3RvbjEYMBYGA1UECgwPU1NMIENvcnBvcmF0aW9uMTcwNQYDVQQD
DC5TU0wuY29tIEVWIFJvb3QgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkgUlNBIFIy
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAjzZlQOHWTcDXtOlG2mvq
M0fNTPl9fb69LT3w23jhhqXZuglXaO1XPqDQCEGD5yhBJB/jchXQARr7XnAjssuf
OePPxU7Gkm0mxnu7s9onnQqG6YE3Bf7wcXHswxzpY6IXFJ3vG2fThVUCAtZJycxa
4bH3bzKfydQ7iEGonL3Lq9ttewkfokxykNorCPzPPFTOZw+oz12WGQvE43LrrdF9
HSfvkusQv1vrO6/PgN3B0pYEW3p+pKk8OHakYo6gOV7qd89dAFmPZiw+B6KjBSYR
aZfqhbcPlgtLyEDhULouisv3D5oi53+aNxPN8k0TayHRwMwi8qFG9kRpnMphNQcA
b9ZhCBHqurj26bNg5U257J8UZslXWNvNh2n4ioYSA0e/ZhN2rHd9NCSFg83XqpyQ
Gp8hLH94t2S42Oim9HizVcuE0jLEeK6jj2HdzghTreyI/BXkmg3mnxp3zkyPuBQV
PWKchjgGAGYS5Fl2WlPAApiiECtoRHuOec4zSnaqW4EWG7WK2NAAe15itAnWhmMO
pgWVSbooi4iTsjQc2KRVbrcc0N6ZVTsj9CLg+SlmJuwgUHfbSguPvuUCYHBBXtSu
UDkiFCbLsjtzdFVHB3mBOagwE0TlBIqulhMlQg+5U8Sb/M3kHN48+qvWBkofZ6aY
MBzdLNvcGJVXZsb/XItW9XcCAwEAAaNjMGEwDwYDVR0TAQH/BAUwAwEB/zAfBgNV
HSMEGDAWgBT5YLvU49U09rj1BoAlp3PbRmmonjAdBgNVHQ4EFgQU+WC71OPVNPa4
9QaAJadz20ZpqJ4wDgYDVR0PAQH/BAQDAgGGMA0GCSqGSIb3DQEBCwUAA4ICAQBW
s47LCp1Jjr+kxJG7ZhcFUZh1++VQLHqe8RT6q9OKPv+RKY9ji9i0qVQBDb6Thi/5
Sm3HXvVX+cpVHBK+Rw82xd9qt9t1wkclf7nxY/hoLVUE0fKNsKTPvDxeH3jnpaAg
cLAExbf3cqfeIg29MyVGjGSSJuM+LmOW2puMPfgYCdcDzH2GguDKBAdRUNf/ktUM
79qGn5nX67evaOI5JpS6aLe/g9Pqemc9YmeuJeVy6OLk7K4S9ksrPJ/psEDzOFSz
/bdoyNrGj1E8svuR3Bznm53htw1yj+KkxKl4+esUrMZDBcJlOSgYAsOCsp0FvmXt
ll9ldDz7CTUue5wT/RsPXcdtgTpWD8w74a8CLyKsRspGPKAcTNZEtF4uXBVmCeEm
Kf7GUmG6sXP/wwyc5WxqlD8UykAWlYTzWamsX0xhk23RO8yilQwipmdnRC652dKK
QbNmC1r7fSOl8hqw/96bg5Qu0T/fkreRrwU7ZcegbLHNYhLDkBvjJc40vG93drEQ
w/cFGsDWr3RiSBd3kmmQYRzelYB0VI8YHMPzA9C/pEN1hlMYegouCRw2n5H9gooi
S9EOUCXdywMMF8mDAAhONU2Ki+3wApRmLER/y5UnlhetCTCstnEXbosX9hwJ1C07
mKVx01QT2WDz9UtmT/rx7iASjbSsV7FFY6GsdqnC+w==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIClDCCAhqgAwIBAgIILCmcWxbtBZUwCgYIKoZIzj0EAwIwfzELMAkGA1UEBhMC
VVMxDjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMRgwFgYDVQQKDA9T
U0wgQ29ycG9yYXRpb24xNDAyBgNVBAMMK1NTTC5jb20gRVYgUm9vdCBDZXJ0aWZp
Y2F0aW9uIEF1dGhvcml0eSBFQ0MwHhcNMTYwMjEyMTgxNTIzWhcNNDEwMjEyMTgx
NTIzWjB/MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hv
dXN0b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjE0MDIGA1UEAwwrU1NMLmNv
bSBFViBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IEVDQzB2MBAGByqGSM49
AgEGBSuBBAAiA2IABKoSR5CYG/vvw0AHgyBO8TCCogbR8pKGYfL2IWjKAMTH6kMA
VIbc/R/fALhBYlzccBYy3h+Z1MzFB8gIH2EWB1E9fVwHU+M1OIzfzZ/ZLg1Kthku
WnBaBu2+8KGwytAJKaNjMGEwHQYDVR0OBBYEFFvKXuXe0oGqzagtZFG22XKbl+ZP
MA8GA1UdEwEB/wQFMAMBAf8wHwYDVR0jBBgwFoAUW8pe5d7SgarNqC1kUbbZcpuX
5k8wDgYDVR0PAQH/BAQDAgGGMAoGCCqGSM49BAMCA2gAMGUCMQCK5kCJN+vp1RPZ
ytRrJPOwPYdGWBrssd9v+1a6cGvHOMzosYxPD/fxZ3YOg9AeUY8CMD32IygmTMZg
h5Mmm7I1HrrW9zzRHM76JTymGoEVW/MSD2zuZYrJh6j5B+BimoxcSg==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFgzCCA2ugAwIBAgIORea7A4Mzw4VlSOb/RVEwDQYJKoZIhvcNAQEMBQAwTDEg
MB4GA1UECxMXR2xvYmFsU2lnbiBSb290IENBIC0gUjYxEzARBgNVBAoTCkdsb2Jh
bFNpZ24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMTQxMjEwMDAwMDAwWhcNMzQx
MjEwMDAwMDAwWjBMMSAwHgYDVQQLExdHbG9iYWxTaWduIFJvb3QgQ0EgLSBSNjET
MBEGA1UEChMKR2xvYmFsU2lnbjETMBEGA1UEAxMKR2xvYmFsU2lnbjCCAiIwDQYJ
KoZIhvcNAQEBBQADggIPADCCAgoCggIBAJUH6HPKZvnsFMp7PPcNCPG0RQssgrRI
xutbPK6DuEGSMxSkb3/pKszGsIhrxbaJ0cay/xTOURQh7ErdG1rG1ofuTToVBu1k
ZguSgMpE3nOUTvOniX9PeGMIyBJQbUJmL025eShNUhqKGoC3GYEOfsSKvGRMIRxD
aNc9PIrFsmbVkJq3MQbFvuJtMgamHvm566qjuL++gmNQ0PAYid/kD3n16qIfKtJw
LnvnvJO7bVPiSHyMEAc4/2ayd2F+4OqMPKq0pPbzlUoSB239jLKJz9CgYXfIWHSw
1CM69106yqLbnQneXUQtkPGBzVeS+n68UARjNN9rkxi+azayOeSsJDa38O+2HBNX
k7besvjihbdzorg1qkXy4J02oW9UivFyVm4uiMVRQkQVlO6jxTiWm05OWgtH8wY2
SXcwvHE35absIQh1/OZhFj931dmRl4QKbNQCTXTAFO39OfuD8l4UoQSwC+n+7o/h
bguyCLNhZglqsQY6ZZZZwPA1/cnaKI0aEYdwgQqomnUdnjqGBQCe24DWJfncBZ4n
WUx2OVvq+aWh2IMP0f/fMBH5hc8zSPXKbWQULHpYT9NLCEnFlWQaYw55PfWzjMpY
rZxCRXluDocZXFSxZba/jJvcE+kNb7gu3GduyYsRtYQUigAZcIN5kZeR1Bonvzce
MgfYFGM8KEyvAgMBAAGjYzBhMA4GA1UdDwEB/wQEAwIBBjAPBgNVHRMBAf8EBTAD
AQH/MB0GA1UdDgQWBBSubAWjkxPioufi1xzWx/B/yGdToDAfBgNVHSMEGDAWgBSu
bAWjkxPioufi1xzWx/B/yGdToDANBgkqhkiG9w0BAQwFAAOCAgEAgyXt6NH9lVLN
nsAEoJFp5lzQhN7craJP6Ed41mWYqVuoPId8AorRbrcWc+ZfwFSY1XS+wc3iEZGt
Ixg93eFyRJa0lV7Ae46ZeBZDE1ZXs6KzO7V33EByrKPrmzU+sQghoefEQzd5Mr61
55wsTLxDKZmOMNOsIeDjHfrYBzN2VAAiKrlNIC5waNrlU/yDXNOd8v9EDERm8tLj
vUYAGm0CuiVdjaExUd1URhxN25mW7xocBFymFe944Hn+Xds+qkxV/ZoVqW/hpvvf
cDDpw+5CRu3CkwWJ+n1jez/QcYF8AOiYrg54NMMl+68KnyBr3TsTjxKM4kEaSHpz
oHdpx7Zcf4LIHv5YGygrqGytXm3ABdJ7t+uA/iU3/gKbaKxCXcPu9czc8FB10jZp
nOZ7BN9uBmm23goJSFmH63sUYHpkqmlD75HHTOwY3WzvUy2MmeFe8nI+z1TIvWfs
pA9MRf/TuTAjB0yPEL+GltmZWrSZVxykzLsViVO6LAUP5MSeGbEYNNVMnbrt9x+v
JJUEeKgDu+6B5dpffItKoZB0JaezPkvILFa9x8jvOOJckvB595yEunQtYQEgfn7R
8k8HWV+LLUNS60YMlOH1Zkd5d9VUWx+tJDfLRVpOoERIyNiwmcUVhAn21klJwGW4
5hpxbqCo8YLoRT5s1gLXCmeDBVrJpBA=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICaTCCAe+gAwIBAgIQISpWDK7aDKtARb8roi066jAKBggqhkjOPQQDAzBtMQsw
CQYDVQQGEwJDSDEQMA4GA1UEChMHV0lTZUtleTEiMCAGA1UECxMZT0lTVEUgRm91
bmRhdGlvbiBFbmRvcnNlZDEoMCYGA1UEAxMfT0lTVEUgV0lTZUtleSBHbG9iYWwg
Um9vdCBHQyBDQTAeFw0xNzA1MDkwOTQ4MzRaFw00MjA1MDkwOTU4MzNaMG0xCzAJ
BgNVBAYTAkNIMRAwDgYDVQQKEwdXSVNlS2V5MSIwIAYDVQQLExlPSVNURSBGb3Vu
ZGF0aW9uIEVuZG9yc2VkMSgwJgYDVQQDEx9PSVNURSBXSVNlS2V5IEdsb2JhbCBS
b290IEdDIENBMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAETOlQwMYPchi82PG6s4ni
eUqjFqdrVCTbUf/q9Akkwwsin8tqJ4KBDdLArzHkdIJuyiXZjHWd8dvQmqJLIX4W
p2OQ0jnUsYd4XxiWD1AbNTcPasbc2RNNpI6QN+a9WzGRo1QwUjAOBgNVHQ8BAf8E
BAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUSIcUrOPDnpBgOtfKie7T
rYy0UGYwEAYJKwYBBAGCNxUBBAMCAQAwCgYIKoZIzj0EAwMDaAAwZQIwJsdpW9zV
57LnyAyMjMPdeYwbY9XJUpROTYJKcx6ygISpJcBMWm1JKWB4E+J+SOtkAjEA2zQg
Mgj/mkkCtojeFK9dbJlxjRo/i9fgojaGHAeCOnZT/cKi7e97sIBPWA9LUzm9
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFRjCCAy6gAwIBAgIQXd+x2lqj7V2+WmUgZQOQ7zANBgkqhkiG9w0BAQsFADA9
MQswCQYDVQQGEwJDTjERMA8GA1UECgwIVW5pVHJ1c3QxGzAZBgNVBAMMElVDQSBH
bG9iYWwgRzIgUm9vdDAeFw0xNjAzMTEwMDAwMDBaFw00MDEyMzEwMDAwMDBaMD0x
CzAJBgNVBAYTAkNOMREwDwYDVQQKDAhVbmlUcnVzdDEbMBkGA1UEAwwSVUNBIEds
b2JhbCBHMiBSb290MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAxeYr
b3zvJgUno4Ek2m/LAfmZmqkywiKHYUGRO8vDaBsGxUypK8FnFyIdK+35KYmToni9
kmugow2ifsqTs6bRjDXVdfkX9s9FxeV67HeToI8jrg4aA3++1NDtLnurRiNb/yzm
VHqUwCoV8MmNsHo7JOHXaOIxPAYzRrZUEaalLyJUKlgNAQLx+hVRZ2zA+te2G3/R
VogvGjqNO7uCEeBHANBSh6v7hn4PJGtAnTRnvI3HLYZveT6OqTwXS3+wmeOwcWDc
C/Vkw85DvG1xudLeJ1uK6NjGruFZfc8oLTW4lVYa8bJYS7cSN8h8s+1LgOGN+jIj
tm+3SJUIsUROhYw6AlQgL9+/V087OpAh18EmNVQg7Mc/R+zvWr9LesGtOxdQXGLY
D0tK3Cv6brxzks3sx1DoQZbXqX5t2Okdj4q1uViSukqSKwxW/YDrCPBeKW4bHAyv
j5OJrdu9o54hyokZ7N+1wxrrFv54NkzWbtA+FxyQF2smuvt6L78RHBgOLXMDj6Dl
NaBa4kx1HXHhOThTeEDMg5PXCp6dW4+K5OXgSORIskfNTip1KnvyIvbJvgmRlld6
iIis7nCs+dwp4wwcOxJORNanTrAmyPPZGpeRaOrvjUYG0lZFWJo8DA+DuAUlwznP
O6Q0ibd5Ei9Hxeepl2n8pndntd978XplFeRhVmUCAwEAAaNCMEAwDgYDVR0PAQH/
BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFIHEjMz15DD/pQwIX4wV
ZyF0Ad/fMA0GCSqGSIb3DQEBCwUAA4ICAQATZSL1jiutROTL/7lo5sOASD0Ee/oj
L3rtNtqyzm325p7lX1iPyzcyochltq44PTUbPrw7tgTQvPlJ9Zv3hcU2tsu8+Mg5
1eRfB70VVJd0ysrtT7q6ZHafgbiERUlMjW+i67HM0cOU2kTC5uLqGOiiHycFutfl
1qnN3e92mI0ADs0b+gO3joBYDic/UvuUospeZcnWhNq5NXHzJsBPd+aBJ9J3O5oU
b3n09tDh05S60FdRvScFDcH9yBIw7m+NESsIndTUv4BFFJqIRNow6rSn4+7vW4LV
PtateJLbXDzz2K36uGt/xDYotgIVilQsnLAXc47QN6MUPJiVAAwpBVueSUmxX8fj
y88nZY41F7dXyDDZQVu5FLbowg+UMaeUmMxq67XhJ/UQqAHojhJi6IjMtX9Gl8Cb
EGY4GjZGXyJoPd/JxhMnq1MGrKI8hgZlb7F+sSlEmqO6SWkoaY/X5V+tBIZkbxqg
DMUIYs6Ao9Dz7GjevjPHF1t/gMRMTLGmhIrDO7gJzRSBuhjjVFc2/tsvfEehOjPI
+Vg7RE+xygKJBJYoaMVLuCaJu9YzL1DV/pqJuhgyklTGW+Cd+V7lDSKb9triyCGy
YiGqhkCyLmTTX8jjfhFnRR8F/uOi77Oos/N9j/gMHyIfLXC0uAE0djAA5SN4p1bX
UB+K+wb1whnw0A==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFWjCCA0KgAwIBAgIQT9Irj/VkyDOeTzRYZiNwYDANBgkqhkiG9w0BAQsFADBH
MQswCQYDVQQGEwJDTjERMA8GA1UECgwIVW5pVHJ1c3QxJTAjBgNVBAMMHFVDQSBF
eHRlbmRlZCBWYWxpZGF0aW9uIFJvb3QwHhcNMTUwMzEzMDAwMDAwWhcNMzgxMjMx
MDAwMDAwWjBHMQswCQYDVQQGEwJDTjERMA8GA1UECgwIVW5pVHJ1c3QxJTAjBgNV
BAMMHFVDQSBFeHRlbmRlZCBWYWxpZGF0aW9uIFJvb3QwggIiMA0GCSqGSIb3DQEB
AQUAA4ICDwAwggIKAoICAQCpCQcoEwKwmeBkqh5DFnpzsZGgdT6o+uM4AHrsiWog
D4vFsJszA1qGxliG1cGFu0/GnEBNyr7uaZa4rYEwmnySBesFK5pI0Lh2PpbIILvS
sPGP2KxFRv+qZ2C0d35qHzwaUnoEPQc8hQ2E0B92CvdqFN9y4zR8V05WAT558aop
O2z6+I9tTcg1367r3CTueUWnhbYFiN6IXSV8l2RnCdm/WhUFhvMJHuxYMjMR83dk
sHYf5BA1FxvyDrFspCqjc/wJHx4yGVMR59mzLC52LqGj3n5qiAno8geK+LLNEOfi
c0CTuwjRP+H8C5SzJe98ptfRr5//lpr1kXuYC3fUfugH0mK1lTnj8/FtDw5lhIpj
VMWAtuCeS31HJqcBCF3RiJ7XwzJE+oJKCmhUfzhTA8ykADNkUVkLo4KRel7sFsLz
KuZi2irbWWIQJUoqgQtHB0MGcIfS+pMRKXpITeuUx3BNr2fVUbGAIAEBtHoIppB/
TuDvB0GHr2qlXov7z1CymlSvw4m6WC31MJixNnI5fkkE/SmnTHnkBVfblLkWU41G
sx2VYVdWf6/wFlthWG82UBEL2KwrlRYaDh8IzTY0ZRBiZtWAXxQgXy0MoHgKaNYs
1+lvK9JKBZP8nm9rZ/+I8U6laUpSNwXqxhaN0sSZ0YIrO7o1dfdRUVjzyAfd5LQD
fwIDAQABo0IwQDAdBgNVHQ4EFgQU2XQ65DA9DfcS3H5aBZ8eNJr34RQwDwYDVR0T
AQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAYYwDQYJKoZIhvcNAQELBQADggIBADaN
l8xCFWQpN5smLNb7rhVpLGsaGvdftvkHTFnq88nIua7Mui563MD1sC3AO6+fcAUR
ap8lTwEpcOPlDOHqWnzcSbvBHiqB9RZLcpHIojG5qtr8nR/zXUACE/xOHAbKsxSQ
VBcZEhrxH9cMaVr2cXj0lH2RC47skFSOvG+hTKv8dGT9cZr4QQehzZHkPJrgmzI5
c6sq1WnIeJEmMX3ixzDx/BR4dxIOE/TdFpS/S2d7cFOFyrC78zhNLJA5wA3CXWvp
4uXViI3WLL+rG761KIcSF3Ru/H38j9CHJrAb+7lsq+KePRXBOy5nAliRn+/4Qh8s
t2j1da3Ptfb/EX3C8CSlrdP6oDyp+l3cpaDvRKS+1ujl5BOWF3sGPjLtx7dCvHaj
2GU4Kzg1USEODm8uNBNA4StnDG1KQTAYI1oyVZnJF+A83vbsea0rWBmirSwiGpWO
vpaQXUJXxPkUAzUrHC1RVwinOt4/5Mi0A3PCwSaAuwtCH60NryZy2sy+s6ODWA2C
xR9GUeOcGMyNm43sSet1UNWMKFnKdDTajAshqx7qG+XH/RU+wBeq+yNuJkbL+vmx
cmtpzyKEC2IPrNkZAJSidjzULZrtBJ4tBmIQN1IchXIbJ+XMxjHsN+xjWZsLHXbM
fjKaiJUINlK73nZfdklJrX+9ZSCyycErdhh2n1ax
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIGWzCCBEOgAwIBAgIRAMrpG4nxVQMNo+ZBbcTjpuEwDQYJKoZIhvcNAQELBQAw
WjELMAkGA1UEBhMCRlIxEjAQBgNVBAoMCURoaW15b3RpczEcMBoGA1UECwwTMDAw
MiA0ODE0NjMwODEwMDAzNjEZMBcGA1UEAwwQQ2VydGlnbmEgUm9vdCBDQTAeFw0x
MzEwMDEwODMyMjdaFw0zMzEwMDEwODMyMjdaMFoxCzAJBgNVBAYTAkZSMRIwEAYD
VQQKDAlEaGlteW90aXMxHDAaBgNVBAsMEzAwMDIgNDgxNDYzMDgxMDAwMzYxGTAX
BgNVBAMMEENlcnRpZ25hIFJvb3QgQ0EwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAw
ggIKAoICAQDNGDllGlmx6mQWDoyUJJV8g9PFOSbcDO8WV43X2KyjQn+Cyu3NW9sO
ty3tRQgXstmzy9YXUnIo245Onoq2C/mehJpNdt4iKVzSs9IGPjA5qXSjklYcoW9M
CiBtnyN6tMbaLOQdLNyzKNAT8kxOAkmhVECe5uUFoC2EyP+YbNDrihqECB63aCPu
I9Vwzm1RaRDuoXrC0SIxwoKF0vJVdlB8JXrJhFwLrN1CTivngqIkicuQstDuI7pm
TLtipPlTWmR7fJj6o0ieD5Wupxj0auwuA0Wv8HT4Ks16XdG+RCYyKfHx9WzMfgIh
C59vpD++nVPiz32pLHxYGpfhPTc3GGYo0kDFUYqMwy3OU4gkWGQwFsWq4NYKpkDf
ePb1BHxpE4S80dGnBs8B92jAqFe7OmGtBIyT46388NtEbVncSVmurJqZNjBBe3Yz
IoejwpKGbvlw7q6Hh5UbxHq9MfPU0uWZ/75I7HX1eBYdpnDBfzwboZL7z8g81sWT
Co/1VTp2lc5ZmIoJlXcymoO6LAQ6l73UL77XbJuiyn1tJslV1c/DeVIICZkHJC1k
JWumIWmbat10TWuXekG9qxf5kBdIjzb5LdXF2+6qhUVB+s06RbFo5jZMm5BX7CO5
hwjCxAnxl4YqKE3idMDaxIzb3+KhF1nOJFl0Mdp//TBt2dzhauH8XwIDAQABo4IB
GjCCARYwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYE
FBiHVuBud+4kNTxOc5of1uHieX4rMB8GA1UdIwQYMBaAFBiHVuBud+4kNTxOc5of
1uHieX4rMEQGA1UdIAQ9MDswOQYEVR0gADAxMC8GCCsGAQUFBwIBFiNodHRwczov
L3d3d3cuY2VydGlnbmEuZnIvYXV0b3JpdGVzLzBtBgNVHR8EZjBkMC+gLaArhilo
dHRwOi8vY3JsLmNlcnRpZ25hLmZyL2NlcnRpZ25hcm9vdGNhLmNybDAxoC+gLYYr
aHR0cDovL2NybC5kaGlteW90aXMuY29tL2NlcnRpZ25hcm9vdGNhLmNybDANBgkq
hkiG9w0BAQsFAAOCAgEAlLieT/DjlQgi581oQfccVdV8AOItOoldaDgvUSILSo3L
6btdPrtcPbEo/uRTVRPPoZAbAh1fZkYJMyjhDSSXcNMQH+pkV5a7XdrnxIxPTGRG
HVyH41neQtGbqH6mid2PHMkwgu07nM3A6RngatgCdTer9zQoKJHyBApPNeNgJgH6
0BGM+RFq7q89w1DTj18zeTyGqHNFkIwgtnJzFyO+B2XleJINugHA64wcZr+shncB
lA2c5uk5jR+mUYyZDDl34bSb+hxnV29qao6pK0xXeXpXIs/NX2NGjVxZOob4Mkdi
o2cNGJHc+6Zr9UhhcyNZjgKnvETq9Emd8VRY+WCv2hikLyhF3HqgiIZd8zvn/yk1
gPxkQ5Tm4xxvvq0OKmOZK8l+hfZx6AYDlf7ej0gcWtSS6Cvu5zHbugRqh5jnxV/v
faci9wHYTfmJ0A6aBVmknpjZbyvKcL5kwlWj9Omvw5Ip3IgWJJk8jSaYtlu3zM63
Nwf9JtmYhST/WSMDmu2dnajkXjjO11INb9I/bbEFa0nOipFGc/T2L/Coc3cOZayh
jWZSaX5LaAzHHjcng6WMxwLkFM1JAbBzs/3GkDpv0mztO+7skb6iQ12LAEpmJURw
3kAP+HwV96LOPNdeE4yBFxgX0b3xdxA61GU5wSesVywlVP+i2k+KYTlerj1KjL0=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDlDCCAnygAwIBAgIKMfXkYgxsWO3W2DANBgkqhkiG9w0BAQsFADBnMQswCQYD
VQQGEwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBU
ZWNobm9sb2dpZXMgTGltaXRlZDEcMBoGA1UEAxMTZW1TaWduIFJvb3QgQ0EgLSBH
MTAeFw0xODAyMTgxODMwMDBaFw00MzAyMTgxODMwMDBaMGcxCzAJBgNVBAYTAklO
MRMwEQYDVQQLEwplbVNpZ24gUEtJMSUwIwYDVQQKExxlTXVkaHJhIFRlY2hub2xv
Z2llcyBMaW1pdGVkMRwwGgYDVQQDExNlbVNpZ24gUm9vdCBDQSAtIEcxMIIBIjAN
BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAk0u76WaK7p1b1TST0Bsew+eeuGQz
f2N4aLTNLnF115sgxk0pvLZoYIr3IZpWNVrzdr3YzZr/k1ZLpVkGoZM0Kd0WNHVO
8oG0x5ZOrRkVUkr+PHB1cM2vK6sVmjM8qrOLqs1D/fXqcP/tzxE7lM5OMhbTI0Aq
d7OvPAEsbO2ZLIvZTmmYsvePQbAyeGHWDV/D+qJAkh1cF+ZwPjXnorfCYuKrpDhM
tTk1b+oDafo6VGiFbdbyL0NVHpENDtjVaqSW0RM8LHhQ6DqS0hdW5TUaQBw+jSzt
Od9C4INBdN+jzcKGYEho42kLVACL5HZpIQ15TjQIXhTCzLG3rdd8cIrHhQIDAQAB
o0IwQDAdBgNVHQ4EFgQU++8Nhp6w492pufEhF38+/PB3KxowDgYDVR0PAQH/BAQD
AgEGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQELBQADggEBAFn/8oz1h31x
PaOfG1vR2vjTnGs2vZupYeveFix0PZ7mddrXuqe8QhfnPZHr5X3dPpzxz5KsbEjM
wiI/aTvFthUvozXGaCocV685743QNcMYDHsAVhzNixl03r4PEuDQqqE/AjSxcM6d
GNYIAwlG7mDgfrbESQRRfXBgvKqy/3lyeqYdPV8q+Mri/Tm3R7nrft8EI6/6nAYH
6ftjk4BAtcZsCjEozgyfz7MjNYBBjWzEN3uBL4ChQEKF6dk4jeihU80Bv2noWgby
RQuQ+q7hv53yrlc8pa6yVvSLZUDp/TGBLPQ5Cdjua6e0ph0VpZj3AYHYhX3zUVxx
iN66zB+Afko=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICTjCCAdOgAwIBAgIKPPYHqWhwDtqLhDAKBggqhkjOPQQDAzBrMQswCQYDVQQG
EwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNo
bm9sb2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0g
RzMwHhcNMTgwMjE4MTgzMDAwWhcNNDMwMjE4MTgzMDAwWjBrMQswCQYDVQQGEwJJ
TjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNobm9s
b2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0gRzMw
djAQBgcqhkjOPQIBBgUrgQQAIgNiAAQjpQy4LRL1KPOxst3iAhKAnjlfSU2fySU0
WXTsuwYc58Byr+iuL+FBVIcUqEqy6HyC5ltqtdyzdc6LBtCGI79G1Y4PPwT01xyS
fvalY8L1X44uT6EYGQIrMgqCZH0Wk9GjQjBAMB0GA1UdDgQWBBR8XQKEE9TMipuB
zhccLikenEhjQjAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAKBggq
hkjOPQQDAwNpADBmAjEAvvNhzwIQHWSVB7gYboiFBS+DCBeQyh+KTOgNG3qxrdWB
CUfvO6wIBHxcmbHtRwfSAjEAnbpV/KlK6O3t5nYBQnvI+GDZjVGLVTv7jHvrZQnD
+JbNR6iC8hZVdyR+EhCVBCyj
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDczCCAlugAwIBAgILAK7PALrEzzL4Q7IwDQYJKoZIhvcNAQELBQAwVjELMAkG
A1UEBhMCVVMxEzARBgNVBAsTCmVtU2lnbiBQS0kxFDASBgNVBAoTC2VNdWRocmEg
SW5jMRwwGgYDVQQDExNlbVNpZ24gUm9vdCBDQSAtIEMxMB4XDTE4MDIxODE4MzAw
MFoXDTQzMDIxODE4MzAwMFowVjELMAkGA1UEBhMCVVMxEzARBgNVBAsTCmVtU2ln
biBQS0kxFDASBgNVBAoTC2VNdWRocmEgSW5jMRwwGgYDVQQDExNlbVNpZ24gUm9v
dCBDQSAtIEMxMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAz+upufGZ
BczYKCFK83M0UYRWEPWgTywS4/oTmifQz/l5GnRfHXk5/Fv4cI7gklL35CX5VIPZ
HdPIWoU/Xse2B+4+wM6ar6xWQio5JXDWv7V7Nq2s9nPczdcdioOl+yuQFTdrHCZH
3DspVpNqs8FqOp099cGXOFgFixwR4+S0uF2FHYP+eF8LRWgYSKVGczQ7/g/IdrvH
GPMF0Ybzhe3nudkyrVWIzqa2kbBPrH4VI5b2P/AgNBbeCsbEBEV5f6f9vtKppa+c
xSMq9zwhbL2vj07FOrLzNBL834AaSaTUqZX3noleoomslMuoaJuvimUnzYnu3Yy1
aylwQ6BpC+S5DwIDAQABo0IwQDAdBgNVHQ4EFgQU/qHgcB4qAzlSWkK+XJGFehiq
TbUwDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL
BQADggEBAMJKVvoVIXsoounlHfv4LcQ5lkFMOycsxGwYFYDGrK9HWS8mC+M2sO87
/kOXSTKZEhVb3xEp/6tT+LvBeA+snFOvV71ojD1pM/CjoCNjO2RnIkSt1XHLVip4
kqNPEjE2NuLe/gDEo2APJ62gsIq1NnpSob0n9CAnYuhNlCQT5AoE6TyrLshDCUrG
YQTlSTR+08TI9Q/Aqum6VF7zYytPT1DU/rl7mYw9wC68AivTxEDkigcxHpvOJpkT
+xHqmiIMERnHXhuBUDDIlhJu58tBf5E7oke3VIAb3ADMmpDqw8NQBmIMMMAVSKeo
WXzhriKi4gp6D/piq1JM4fHfyr6DDUI=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICKzCCAbGgAwIBAgIKe3G2gla4EnycqDAKBggqhkjOPQQDAzBaMQswCQYDVQQG
EwJVUzETMBEGA1UECxMKZW1TaWduIFBLSTEUMBIGA1UEChMLZU11ZGhyYSBJbmMx
IDAeBgNVBAMTF2VtU2lnbiBFQ0MgUm9vdCBDQSAtIEMzMB4XDTE4MDIxODE4MzAw
MFoXDTQzMDIxODE4MzAwMFowWjELMAkGA1UEBhMCVVMxEzARBgNVBAsTCmVtU2ln
biBQS0kxFDASBgNVBAoTC2VNdWRocmEgSW5jMSAwHgYDVQQDExdlbVNpZ24gRUND
IFJvb3QgQ0EgLSBDMzB2MBAGByqGSM49AgEGBSuBBAAiA2IABP2lYa57JhAd6bci
MK4G9IGzsUJxlTm801Ljr6/58pc1kjZGDoeVjbk5Wum739D+yAdBPLtVb4Ojavti
sIGJAnB9SMVK4+kiVCJNk7tCDK93nCOmfddhEc5lx/h//vXyqaNCMEAwHQYDVR0O
BBYEFPtaSNCAIEDyqOkAB2kZd6fmw/TPMA4GA1UdDwEB/wQEAwIBBjAPBgNVHRMB
Af8EBTADAQH/MAoGCCqGSM49BAMDA2gAMGUCMQC02C8Cif22TGK6Q04ThHK1rt0c
3ta13FaPWEBaLd4gTCKDypOofu4SQMfWh0/434UCMBwUZOR8loMRnLDRWmFLpg9J
0wD8ofzkpf9/rdcw0Md3f76BB1UwUCAU9Vc4CqgxUQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFzzCCA7egAwIBAgIUCBZfikyl7ADJk0DfxMauI7gcWqQwDQYJKoZIhvcNAQEL
BQAwbzELMAkGA1UEBhMCSEsxEjAQBgNVBAgTCUhvbmcgS29uZzESMBAGA1UEBxMJ
SG9uZyBLb25nMRYwFAYDVQQKEw1Ib25na29uZyBQb3N0MSAwHgYDVQQDExdIb25n
a29uZyBQb3N0IFJvb3QgQ0EgMzAeFw0xNzA2MDMwMjI5NDZaFw00MjA2MDMwMjI5
NDZaMG8xCzAJBgNVBAYTAkhLMRIwEAYDVQQIEwlIb25nIEtvbmcxEjAQBgNVBAcT
CUhvbmcgS29uZzEWMBQGA1UEChMNSG9uZ2tvbmcgUG9zdDEgMB4GA1UEAxMXSG9u
Z2tvbmcgUG9zdCBSb290IENBIDMwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIK
AoICAQCziNfqzg8gTr7m1gNt7ln8wlffKWihgw4+aMdoWJwcYEuJQwy51BWy7sFO
dem1p+/l6TWZ5Mwc50tfjTMwIDNT2aa71T4Tjukfh0mtUC1Qyhi+AViiE3CWu4mI
VoBc+L0sPOFMV4i707mV78vH9toxdCim5lSJ9UExyuUmGs2C4HDaOym71QP1mbpV
9WTRYA6ziUm4ii8F0oRFKHyPaFASePwLtVPLwpgchKOesL4jpNrcyCse2m5FHomY
2vkALgbpDDtw1VAliJnLzXNg99X/NWfFobxeq81KuEXryGgeDQ0URhLj0mRiikKY
vLTGCAj4/ahMZJx2Ab0vqWwzD9g/KLg8aQFChn5pwckGyuV6RmXpwtZQQS4/t+Tt
bNe/JgERohYpSms0BpDsE9K2+2p20jzt8NYt3eEV7KObLyzJPivkaTv/ciWxNoZb
x39ri1UbSsUgYT2uy1DhCDq+sI9jQVMwCFk8mB13umOResoQUGC/8Ne8lYePl8X+
l2oBlKN8W4UdKjk60FSh0Tlxnf0h+bV78OLgAo9uliQlLKAeLKjEiafv7ZkGL7YK
TE/bosw3Gq9HhS2KX8Q0NEwA/RiTZxPRN+ZItIsGxVd7GYYKecsAyVKvQv83j+Gj
Hno9UKtjBucVtT+2RTeUN7F+8kjDf8V1/peNRY8apxpyKBpADwIDAQABo2MwYTAP
BgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQEAwIBBjAfBgNVHSMEGDAWgBQXnc0e
i9Y5K3DTXNSguB+wAPzFYTAdBgNVHQ4EFgQUF53NHovWOStw01zUoLgfsAD8xWEw
DQYJKoZIhvcNAQELBQADggIBAFbVe27mIgHSQpsY1Q7XZiNc4/6gx5LS6ZStS6LG
7BJ8dNVI0lkUmcDrudHr9EgwW62nV3OZqdPlt9EuWSRY3GguLmLYauRwCy0gUCCk
MpXRAJi70/33MvJJrsZ64Ee+bs7Lo3I6LWldy8joRTnU+kLBEUx3XZL7av9YROXr
gZ6voJmtvqkBZss4HTzfQx/0TW60uhdG/H39h4F5ag0zD/ov+BS5gLNdTaqX4fnk
GMX41TiMJjz98iji7lpJiCzfeT2OnpA8vUFKOt1b9pq0zj8lMH8yfaIDlNDceqFS
3m6TjRgm/VWsvY+b0s+v54Ysyx8Jb6NvqYTUc79NoXQbTiNg8swOqn+knEwlqLJm
Ozj/2ZQw9nKEvmhVEA/GcywWaZMH/rFF7buiVWqw2rVKAiUnhde3t4ZEFolsgCs+
l6mc1X5VTMbeRRAc6uk7nwNT7u56AQIWeNTowr5GdogTPyK7SBIdUgC0An4hGh6c
JfTzPV4e0hz5sy229zdcxsshTrD3mUcYhcErulWuBurQB7Lcq9CClnXO0lD+mefP
L5/ndtFhKvshuzHQqp9HpLIiyhY6UFfEW0NnxWViA0kB60PZ2Pierc+xYw5F9KBa
LJstxabArahH9CdMOA0uG0k7UvToiIMrVCjU8jVStDKDYmlkDJGcn5fqdBb9HxEG
mpv0
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICWTCCAd+gAwIBAgIQZvI9r4fei7FK6gxXMQHC7DAKBggqhkjOPQQDAzBlMQsw
CQYDVQQGEwJVUzEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMTYwNAYD
VQQDEy1NaWNyb3NvZnQgRUNDIFJvb3QgQ2VydGlmaWNhdGUgQXV0aG9yaXR5IDIw
MTcwHhcNMTkxMjE4MjMwNjQ1WhcNNDIwNzE4MjMxNjA0WjBlMQswCQYDVQQGEwJV
UzEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMTYwNAYDVQQDEy1NaWNy
b3NvZnQgRUNDIFJvb3QgQ2VydGlmaWNhdGUgQXV0aG9yaXR5IDIwMTcwdjAQBgcq
hkjOPQIBBgUrgQQAIgNiAATUvD0CQnVBEyPNgASGAlEvaqiBYgtlzPbKnR5vSmZR
ogPZnZH6thaxjG7efM3beaYvzrvOcS/lpaso7GMEZpn4+vKTEAXhgShC48Zo9OYb
hGBKia/teQ87zvH2RPUBeMCjVDBSMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8E
BTADAQH/MB0GA1UdDgQWBBTIy5lycFIM+Oa+sgRXKSrPQhDtNTAQBgkrBgEEAYI3
FQEEAwIBADAKBggqhkjOPQQDAwNoADBlAjBY8k3qDPlfXu5gKcs68tvWMoQZP3zV
L8KxzJOuULsJMsbG7X7JNpQS5GiFBqIb0C8CMQCZ6Ra0DvpWSNSkMBaReNtUjGUB
iudQZsIxtzm6uBoiB078a1QWIP8rtedMDE2mT3M=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFqDCCA5CgAwIBAgIQHtOXCV/YtLNHcB6qvn9FszANBgkqhkiG9w0BAQwFADBl
MQswCQYDVQQGEwJVUzEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMTYw
NAYDVQQDEy1NaWNyb3NvZnQgUlNBIFJvb3QgQ2VydGlmaWNhdGUgQXV0aG9yaXR5
IDIwMTcwHhcNMTkxMjE4MjI1MTIyWhcNNDIwNzE4MjMwMDIzWjBlMQswCQYDVQQG
EwJVUzEeMBwGA1UEChMVTWljcm9zb2Z0IENvcnBvcmF0aW9uMTYwNAYDVQQDEy1N
aWNyb3NvZnQgUlNBIFJvb3QgQ2VydGlmaWNhdGUgQXV0aG9yaXR5IDIwMTcwggIi
MA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDKW76UM4wplZEWCpW9R2LBifOZ
Nt9GkMml7Xhqb0eRaPgnZ1AzHaGm++DlQ6OEAlcBXZxIQIJTELy/xztokLaCLeX0
ZdDMbRnMlfl7rEqUrQ7eS0MdhweSE5CAg2Q1OQT85elss7YfUJQ4ZVBcF0a5toW1
HLUX6NZFndiyJrDKxHBKrmCk3bPZ7Pw71VdyvD/IybLeS2v4I2wDwAW9lcfNcztm
gGTjGqwu+UcF8ga2m3P1eDNbx6H7JyqhtJqRjJHTOoI+dkC0zVJhUXAoP8XFWvLJ
jEm7FFtNyP9nTUwSlq31/niol4fX/V4ggNyhSyL71Imtus5Hl0dVe49FyGcohJUc
aDDv70ngNXtk55iwlNpNhTs+VcQor1fznhPbRiefHqJeRIOkpcrVE7NLP8TjwuaG
YaRSMLl6IE9vDzhTyzMMEyuP1pq9KsgtsRx9S1HKR9FIJ3Jdh+vVReZIZZ2vUpC6
W6IYZVcSn2i51BVrlMRpIpj0M+Dt+VGOQVDJNE92kKz8OMHY4Xu54+OU4UZpyw4K
UGsTuqwPN1q3ErWQgR5WrlcihtnJ0tHXUeOrO8ZV/R4O03QK0dqq6mm4lyiPSMQH
+FJDOvTKVTUssKZqwJz58oHhEmrARdlns87/I6KJClTUFLkqqNfs+avNJVgyeY+Q
W5g5xAgGwax/Dj0ApQIDAQABo1QwUjAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/
BAUwAwEB/zAdBgNVHQ4EFgQUCctZf4aycI8awznjwNnpv7tNsiMwEAYJKwYBBAGC
NxUBBAMCAQAwDQYJKoZIhvcNAQEMBQADggIBAKyvPl3CEZaJjqPnktaXFbgToqZC
LgLNFgVZJ8og6Lq46BrsTaiXVq5lQ7GPAJtSzVXNUzltYkyLDVt8LkS/gxCP81OC
gMNPOsduET/m4xaRhPtthH80dK2Jp86519efhGSSvpWhrQlTM93uCupKUY5vVau6
tZRGrox/2KJQJWVggEbbMwSubLWYdFQl3JPk+ONVFT24bcMKpBLBaYVu32TxU5nh
SnUgnZUP5NbcA/FZGOhHibJXWpS2qdgXKxdJ5XbLwVaZOjex/2kskZGT4d9Mozd2
TaGf+G0eHdP67Pv0RR0Tbc/3WeUiJ3IrhvNXuzDtJE3cfVa7o7P4NHmJweDyAmH3
pvwPuxwXC65B2Xy9J6P9LjrRk5Sxcx0ki69bIImtt2dmefU6xqaWM/5TkshGsRGR
xpl/j8nWZjEgQRCHLQzWwa80mMpkg/sTV9HB8Dx6jKXB/ZUhoHHBk2dxEuqPiApp
GWSZI1b7rCoucL5mxAyE7+WL85MB+GqQk2dLsmijtWKP6T+MejteD+eMuMZ87zf9
dOLITzNy4ZQ5bb0Sr74MTnB8G2+NszKTc0QWbej09+CVgI+WXTik9KveCjCHk9hN
AHFiRSdLOkKEW39lt2c0Ui2cFmuqqNh7o0JMcccMyj6D5KbvtwEwXlGjefVwaaZB
RA+GsCyRxj3qrg+E
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICQDCCAeWgAwIBAgIMAVRI7yH9l1kN9QQKMAoGCCqGSM49BAMCMHExCzAJBgNV
BAYTAkhVMREwDwYDVQQHDAhCdWRhcGVzdDEWMBQGA1UECgwNTWljcm9zZWMgTHRk
LjEXMBUGA1UEYQwOVkFUSFUtMjM1ODQ0OTcxHjAcBgNVBAMMFWUtU3ppZ25vIFJv
b3QgQ0EgMjAxNzAeFw0xNzA4MjIxMjA3MDZaFw00MjA4MjIxMjA3MDZaMHExCzAJ
BgNVBAYTAkhVMREwDwYDVQQHDAhCdWRhcGVzdDEWMBQGA1UECgwNTWljcm9zZWMg
THRkLjEXMBUGA1UEYQwOVkFUSFUtMjM1ODQ0OTcxHjAcBgNVBAMMFWUtU3ppZ25v
IFJvb3QgQ0EgMjAxNzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABJbcPYrYsHtv
xie+RJCxs1YVe45DJH0ahFnuY2iyxl6H0BVIHqiQrb1TotreOpCmYF9oMrWGQd+H
Wyx7xf58etqjYzBhMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMB0G
A1UdDgQWBBSHERUI0arBeAyxr87GyZDvvzAEwDAfBgNVHSMEGDAWgBSHERUI0arB
eAyxr87GyZDvvzAEwDAKBggqhkjOPQQDAgNJADBGAiEAtVfd14pVCzbhhkT61Nlo
jbjcI4qKDdQvfepz7L9NbKgCIQDLpbQS+ue16M9+k/zzNY9vTlp8tLxOsvxyqltZ
+efcMQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFRzCCAy+gAwIBAgIJEQA0tk7GNi02MA0GCSqGSIb3DQEBCwUAMEExCzAJBgNV
BAYTAlJPMRQwEgYDVQQKEwtDRVJUU0lHTiBTQTEcMBoGA1UECxMTY2VydFNJR04g
Uk9PVCBDQSBHMjAeFw0xNzAyMDYwOTI3MzVaFw00MjAyMDYwOTI3MzVaMEExCzAJ
BgNVBAYTAlJPMRQwEgYDVQQKEwtDRVJUU0lHTiBTQTEcMBoGA1UECxMTY2VydFNJ
R04gUk9PVCBDQSBHMjCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAMDF
dRmRfUR0dIf+DjuW3NgBFszuY5HnC2/OOwppGnzC46+CjobXXo9X69MhWf05N0Iw
vlDqtg+piNguLWkh59E3GE59kdUWX2tbAMI5Qw02hVK5U2UPHULlj88F0+7cDBrZ
uIt4ImfkabBoxTzkbFpG583H+u/E7Eu9aqSs/cwoUe+StCmrqzWaTOTECMYmzPhp
n+Sc8CnTXPnGFiWeI8MgwT0PPzhAsP6CRDiqWhqKa2NYOLQV07YRaXseVO6MGiKs
cpc/I1mbySKEwQdPzH/iV8oScLumZfNpdWO9lfsbl83kqK/20U6o2YpxJM02PbyW
xPFsqa7lzw1uKA2wDrXKUXt4FMMgL3/7FFXhEZn91QqhngLjYl/rNUssuHLoPj1P
rCy7Lobio3aP5ZMqz6WryFyNSwb/EkaseMsUBzXgqd+L6a8VTxaJW732jcZZroiF
DsGJ6x9nxUWO/203Nit4ZoORUSs9/1F3dmKh7Gc+PoGD4FapUB8fepmrY7+EF3fx
DTvf95xhszWYijqy7DwaNz9+j5LP2RIUZNoQAhVB/0/E6xyjyfqZ90bp4RjZsbgy
LcsUDFDYg2WD7rlcz8sFWkz6GZdr1l0T08JcVLwyc6B49fFtHsufpaafItzRUZ6C
eWRgKRM+o/1Pcmqr4tTluCRVLERLiohEnMqE0yo7AgMBAAGjQjBAMA8GA1UdEwEB
/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMB0GA1UdDgQWBBSCIS1mxteg4BXrzkwJ
d8RgnlRuAzANBgkqhkiG9w0BAQsFAAOCAgEAYN4auOfyYILVAzOBywaK8SJJ6ejq
kX/GM15oGQOGO0MBzwdw5AgeZYWR5hEit/UCI46uuR59H35s5r0l1ZUa8gWmr4UC
b6741jH/JclKyMeKqdmfS0mbEVeZkkMR3rYzpMzXjWR91M08KCy0mpbqTfXERMQl
qiCA2ClV9+BB/AYm/7k29UMUA2Z44RGx2iBfRgB4ACGlHgAoYXhvqAEBj500mv/0
OJD7uNGzcgbJceaBxXntC6Z58hMLnPddDnskk7RI24Zf3lCGeOdA5jGokHZwYa+c
NywRtYK3qq4kNFtyDGkNzVmf9nGvnAvRCjj5BiKDUyUM/FHE5r7iOZULJK2v0ZXk
ltd0ZGtxTgI8qoXzIKNDOXZbbFD+mpwUHmUUihW9o4JFWklWatKcsWMy5WHgUyIO
pwpJ6st+H6jiYoD2EEVSmAYY3qXNL3+q1Ok+CHLsIwMCPKaq2LxndD0UF/tUSxfj
03k9bWtJySgOLnRQvwzZRjoQhsmnP+mg7H/rpXdYaXHmgwo38oZJar55CJD2AhZk
PuXaTH4MNMn5X7azKFGnpyuqSfqNZSlO42sTp5SjLVFteAxEy9/eCG/Oo2Sr05WE
1LlSVHJ7liXMvGnjSG4N0MedJ5qq+BOS3R7fY581qRY27Iy4g/Q9iY/NtBde17MX
QRBdJ3NghVdJIgc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF2jCCA8KgAwIBAgIMBfcOhtpJ80Y1LrqyMA0GCSqGSIb3DQEBCwUAMIGIMQsw
CQYDVQQGEwJVUzERMA8GA1UECAwISWxsaW5vaXMxEDAOBgNVBAcMB0NoaWNhZ28x
ITAfBgNVBAoMGFRydXN0d2F2ZSBIb2xkaW5ncywgSW5jLjExMC8GA1UEAwwoVHJ1
c3R3YXZlIEdsb2JhbCBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTAeFw0xNzA4MjMx
OTM0MTJaFw00MjA4MjMxOTM0MTJaMIGIMQswCQYDVQQGEwJVUzERMA8GA1UECAwI
SWxsaW5vaXMxEDAOBgNVBAcMB0NoaWNhZ28xITAfBgNVBAoMGFRydXN0d2F2ZSBI
b2xkaW5ncywgSW5jLjExMC8GA1UEAwwoVHJ1c3R3YXZlIEdsb2JhbCBDZXJ0aWZp
Y2F0aW9uIEF1dGhvcml0eTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIB
ALldUShLPDeS0YLOvR29zd24q88KPuFd5dyqCblXAj7mY2Hf8g+CY66j96xz0Xzn
swuvCAAJWX/NKSqIk4cXGIDtiLK0thAfLdZfVaITXdHG6wZWiYj+rDKd/VzDBcdu
7oaJuogDnXIhhpCujwOl3J+IKMujkkkP7NAP4m1ET4BqstTnoApTAbqOl5F2brz8
1Ws25kCI1nsvXwXoLG0R8+eyvpJETNKXpP7ScoFDB5zpET71ixpZfR9oWN0EACyW
80OzfpgZdNmcc9kYvkHHNHnZ9GLCQ7mzJ7Aiy/k9UscwR7PJPrhq4ufogXBeQotP
JqX+OsIgbrv4Fo7NDKm0G2x2EOFYeUY+VM6AqFcJNykbmROPDMjWLBz7BegIlT1l
RtzuzWniTY+HKE40Cz7PFNm73bZQmq131BnW2hqIyE4bJ3XYsgjxroMwuREOzYfw
hI0Vcnyh78zyiGG69Gm7DIwLdVcEuE4qFC49DxweMqZiNu5m4iK4BUBjECLzMx10
coos9TkpoNPnG4CELcU9402x/RpvumUHO1jsQkUm+9jaJXLE9gCxInm943xZYkqc
BW89zubWR2OZxiRvchLIrH+QtAuRcOi35hYQcRfO3gZPSEF9NUqjifLJS3tBEW1n
twiYTOURGa5CgNz7kAXU+FDKvuStx8KU1xad5hePrzb7AgMBAAGjQjBAMA8GA1Ud
EwEB/wQFMAMBAf8wHQYDVR0OBBYEFJngGWcNYtt2s9o9uFvo/ULSMQ6HMA4GA1Ud
DwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAgEAmHNw4rDT7TnsTGDZqRKGFx6W
0OhUKDtkLSGm+J1WE2pIPU/HPinbbViDVD2HfSMF1OQc3Og4ZYbFdada2zUFvXfe
uyk3QAUHw5RSn8pk3fEbK9xGChACMf1KaA0HZJDmHvUqoai7PF35owgLEQzxPy0Q
lG/+4jSHg9bP5Rs1bdID4bANqKCqRieCNqcVtgimQlRXtpla4gt5kNdXElE1GYhB
aCXUNxeEFfsBctyV3lImIJgm4nb1J2/6ADtKYdkNy1GTKv0WBpanI5ojSP5RvbbE
sLFUzt5sQa0WZ37b/TjNuThOssFgy50X31ieemKyJo90lZvkWx3SD92YHJtZuSPT
MaCm/zjdzyBP6VhWOmfD0faZmZ26NraAL4hHT4a/RDqA5Dccprrql5gR0IRiR2Qe
qu5AvzSxnI9O4fKSTx+O856X3vOmeWqJcU9LJxdI/uz0UA9PSX3MReO9ekDFQdxh
VicGaeVyQYHTtgGJoC86cnn+OjC/QezHYj6RS8fZMXZC+fc8Y+wmjHMMfRod6qh8
h6jCJ3zhM0EPz8/8AKAigJ5Kp28AsEFFtyLKaEjFQqKu3R3y4G5OBVixwJAWKqQ9
EEC+j2Jjg6mcgn0tAumDMHzLJ8n9HmYAsC7TIS+OMxZsmO0QqAfWzJPP29FpHOTK
yeC2nOnOcXHebD8WpHk=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICYDCCAgegAwIBAgIMDWpfCD8oXD5Rld9dMAoGCCqGSM49BAMCMIGRMQswCQYD
VQQGEwJVUzERMA8GA1UECBMISWxsaW5vaXMxEDAOBgNVBAcTB0NoaWNhZ28xITAf
BgNVBAoTGFRydXN0d2F2ZSBIb2xkaW5ncywgSW5jLjE6MDgGA1UEAxMxVHJ1c3R3
YXZlIEdsb2JhbCBFQ0MgUDI1NiBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTAeFw0x
NzA4MjMxOTM1MTBaFw00MjA4MjMxOTM1MTBaMIGRMQswCQYDVQQGEwJVUzERMA8G
A1UECBMISWxsaW5vaXMxEDAOBgNVBAcTB0NoaWNhZ28xITAfBgNVBAoTGFRydXN0
d2F2ZSBIb2xkaW5ncywgSW5jLjE6MDgGA1UEAxMxVHJ1c3R3YXZlIEdsb2JhbCBF
Q0MgUDI1NiBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTBZMBMGByqGSM49AgEGCCqG
SM49AwEHA0IABH77bOYj43MyCMpg5lOcunSNGLB4kFKA3TjASh3RqMyTpJcGOMoN
FWLGjgEqZZ2q3zSRLoHB5DOSMcT9CTqmP62jQzBBMA8GA1UdEwEB/wQFMAMBAf8w
DwYDVR0PAQH/BAUDAwcGADAdBgNVHQ4EFgQUo0EGrJBt0UrrdaVKEJmzsaGLSvcw
CgYIKoZIzj0EAwIDRwAwRAIgB+ZU2g6gWrKuEZ+Hxbb/ad4lvvigtwjzRM4q3wgh
DDcCIC0mA6AFvWvR9lz4ZcyGbbOcNEhjhAnFjXca4syc4XR7
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICnTCCAiSgAwIBAgIMCL2Fl2yZJ6SAaEc7MAoGCCqGSM49BAMDMIGRMQswCQYD
VQQGEwJVUzERMA8GA1UECBMISWxsaW5vaXMxEDAOBgNVBAcTB0NoaWNhZ28xITAf
BgNVBAoTGFRydXN0d2F2ZSBIb2xkaW5ncywgSW5jLjE6MDgGA1UEAxMxVHJ1c3R3
YXZlIEdsb2JhbCBFQ0MgUDM4NCBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTAeFw0x
NzA4MjMxOTM2NDNaFw00MjA4MjMxOTM2NDNaMIGRMQswCQYDVQQGEwJVUzERMA8G
A1UECBMISWxsaW5vaXMxEDAOBgNVBAcTB0NoaWNhZ28xITAfBgNVBAoTGFRydXN0
d2F2ZSBIb2xkaW5ncywgSW5jLjE6MDgGA1UEAxMxVHJ1c3R3YXZlIEdsb2JhbCBF
Q0MgUDM4NCBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTB2MBAGByqGSM49AgEGBSuB
BAAiA2IABGvaDXU1CDFHBa5FmVXxERMuSvgQMSOjfoPTfygIOiYaOs+Xgh+AtycJ
j9GOMMQKmw6sWASr9zZ9lCOkmwqKi6vr/TklZvFe/oyujUF5nQlgziip04pt89ZF
1PKYhDhloKNDMEEwDwYDVR0TAQH/BAUwAwEB/zAPBgNVHQ8BAf8EBQMDBwYAMB0G
A1UdDgQWBBRVqYSJ0sEyvRjLbKYHTsjnnb6CkDAKBggqhkjOPQQDAwNnADBkAjA3
AZKXRRJ+oPM+rRk6ct30UJMDEr5E0k9BpIycnR+j9sKS50gU/k6bpZFXrsY3crsC
MGclCrEMXu6pY5Jv5ZAL/mYiykf9ijH3g/56vxC+GCsej/YpHpRZ744hN8tRmKVu
Sw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFojCCA4qgAwIBAgIUAZQwHqIL3fXFMyqxQ0Rx+NZQTQ0wDQYJKoZIhvcNAQEM
BQAwaTELMAkGA1UEBhMCS1IxJjAkBgNVBAoMHU5BVkVSIEJVU0lORVNTIFBMQVRG
T1JNIENvcnAuMTIwMAYDVQQDDClOQVZFUiBHbG9iYWwgUm9vdCBDZXJ0aWZpY2F0
aW9uIEF1dGhvcml0eTAeFw0xNzA4MTgwODU4NDJaFw0zNzA4MTgyMzU5NTlaMGkx
CzAJBgNVBAYTAktSMSYwJAYDVQQKDB1OQVZFUiBCVVNJTkVTUyBQTEFURk9STSBD
b3JwLjEyMDAGA1UEAwwpTkFWRVIgR2xvYmFsIFJvb3QgQ2VydGlmaWNhdGlvbiBB
dXRob3JpdHkwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQC21PGTXLVA
iQqrDZBbUGOukJR0F0Vy1ntlWilLp1agS7gvQnXp2XskWjFlqxcX0TM62RHcQDaH
38dq6SZeWYp34+hInDEW+j6RscrJo+KfziFTowI2MMtSAuXaMl3Dxeb57hHHi8lE
HoSTGEq0n+USZGnQJoViAbbJAh2+g1G7XNr4rRVqmfeSVPc0W+m/6imBEtRTkZaz
kVrd/pBzKPswRrXKCAfHcXLJZtM0l/aM9BhK4dA9WkW2aacp+yPOiNgSnABIqKYP
szuSjXEOdMWLyEz59JuOuDxp7W87UC9Y7cSw0BwbagzivESq2M0UXZR4Yb8Obtoq
vC8MC3GmsxY/nOb5zJ9TNeIDoKAYv7vxvvTWjIcNQvcGufFt7QSUqP620wbGQGHf
nZ3zVHbOUzoBppJB7ASjjw2i1QnK1sua8e9DXcCrpUHPXFNwcMmIpi3Ua2FzUCaG
YQ5fG8Ir4ozVu53BA0K6lNpfqbDKzE0K70dpAy8i+/Eozr9dUGWokG2zdLAIx6yo
0es+nPxdGoMuK8u180SdOqcXYZaicdNwlhVNt0xz7hlcxVs+Qf6sdWA7G2POAN3a
CJBitOUt7kinaxeZVL6HSuOpXgRM6xBtVNbv8ejyYhbLgGvtPe31HzClrkvJE+2K
AQHJuFFYwGY6sWZLxNUxAmLpdIQM201GLQIDAQABo0IwQDAdBgNVHQ4EFgQU0p+I
36HNLL3s9TsBAZMzJ7LrYEswDgYDVR0PAQH/BAQDAgEGMA8GA1UdEwEB/wQFMAMB
Af8wDQYJKoZIhvcNAQEMBQADggIBADLKgLOdPVQG3dLSLvCkASELZ0jKbY7gyKoN
qo0hV4/GPnrK21HUUrPUloSlWGB/5QuOH/XcChWB5Tu2tyIvCZwTFrFsDDUIbatj
cu3cvuzHV+YwIHHW1xDBE1UBjCpD5EHxzzp6U5LOogMFDTjfArsQLtk70pt6wKGm
+LUx5vR1yblTmXVHIloUFcd4G7ad6Qz4G3bxhYTeodoS76TiEJd6eN4MUZeoIUCL
hr0N8F5OSza7OyAfikJW4Qsav3vQIkMsRIz75Sq0bBwcupTgE34h5prCy8VCZLQe
lHsIJchxzIdFV4XTnyliIoNRlwAYl3dqmJLJfGBs32x9SuRwTMKeuB330DTHD8z7
p/8Dvq1wkNoL3chtl1+afwkyQf3NosxabUzyqkn+Zvjp2DXrDige7kgvOtB5CTh8
piKCk5XQA76+AqAF3SAi428diDRgxuYKuQl1C/AH6GmWNcf7I4GOODm4RStDeKLR
LBT/DShycpWbXgnbiUSYqqFJu3FS8r/2/yehNq+4tneI3TqkbZs0kNwUXTC/t+sX
5Ie3cdCh13cV1ELX8vMxmV2b3RZtP+oGI/hGoiLtk/bdmuYqh7GYVPEi92tF4+KO
dh2ajcQGjTa3FPOdVGm3jjzVpG2Tgbet9r1ke8LJaDmgkpzNNIaRkPpkUZ3+/uul
9XXeifdy
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICbjCCAfOgAwIBAgIQYvYybOXE42hcG2LdnC6dlTAKBggqhkjOPQQDAzB4MQsw
CQYDVQQGEwJFUzERMA8GA1UECgwIRk5NVC1SQ00xDjAMBgNVBAsMBUNlcmVzMRgw
FgYDVQRhDA9WQVRFUy1RMjgyNjAwNEoxLDAqBgNVBAMMI0FDIFJBSVogRk5NVC1S
Q00gU0VSVklET1JFUyBTRUdVUk9TMB4XDTE4MTIyMDA5MzczM1oXDTQzMTIyMDA5
MzczM1oweDELMAkGA1UEBhMCRVMxETAPBgNVBAoMCEZOTVQtUkNNMQ4wDAYDVQQL
DAVDZXJlczEYMBYGA1UEYQwPVkFURVMtUTI4MjYwMDRKMSwwKgYDVQQDDCNBQyBS
QUlaIEZOTVQtUkNNIFNFUlZJRE9SRVMgU0VHVVJPUzB2MBAGByqGSM49AgEGBSuB
BAAiA2IABPa6V1PIyqvfNkpSIeSX0oNnnvBlUdBeh8dHsVnyV0ebAAKTRBdp20LH
sbI6GA60XYyzZl2hNPk2LEnb80b8s0RpRBNm/dfF/a82Tc4DTQdxz69qBdKiQ1oK
Um8BA06Oi6NCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwHQYD
VR0OBBYEFAG5L++/EYZg8k/QQW6rcx/n0m5JMAoGCCqGSM49BAMDA2kAMGYCMQCu
SuMrQMN0EfKVrRYj3k4MGuZdpSRea0R7/DjiT8ucRRcRTBQnJlU5dUoDzBOQn5IC
MQD6SmxgiHPz7riYYqnOK8LZiqZwMR2vsJRM60/G49HzYqc8/5MuB1xJAWdpEgJy
v+c=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFWjCCA0KgAwIBAgISEdK7udcjGJ5AXwqdLdDfJWfRMA0GCSqGSIb3DQEBDAUA
MEYxCzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWduIG52LXNhMRwwGgYD
VQQDExNHbG9iYWxTaWduIFJvb3QgUjQ2MB4XDTE5MDMyMDAwMDAwMFoXDTQ2MDMy
MDAwMDAwMFowRjELMAkGA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYt
c2ExHDAaBgNVBAMTE0dsb2JhbFNpZ24gUm9vdCBSNDYwggIiMA0GCSqGSIb3DQEB
AQUAA4ICDwAwggIKAoICAQCsrHQy6LNl5brtQyYdpokNRbopiLKkHWPd08EsCVeJ
OaFV6Wc0dwxu5FUdUiXSE2te4R2pt32JMl8Nnp8semNgQB+msLZ4j5lUlghYruQG
vGIFAha/r6gjA7aUD7xubMLL1aa7DOn2wQL7Id5m3RerdELv8HQvJfTqa1VbkNud
316HCkD7rRlr+/fKYIje2sGP1q7Vf9Q8g+7XFkyDRTNrJ9CG0Bwta/OrffGFqfUo
0q3v84RLHIf8E6M6cqJaESvWJ3En7YEtbWaBkoe0G1h6zD8K+kZPTXhc+CtI4wSE
y132tGqzZfxCnlEmIyDLPRT5ge1lFgBPGmSXZgjPjHvjK8Cd+RTyG/FWaha/LIWF
zXg4mutCagI0GIMXTpRW+LaCtfOW3T3zvn8gdz57GSNrLNRyc0NXfeD412lPFzYE
+cCQYDdF3uYM2HSNrpyibXRdQr4G9dlkbgIQrImwTDsHTUB+JMWKmIJ5jqSngiCN
I/onccnfxkF0oE32kRbcRoxfKWMxWXEM2G/CtjJ9++ZdU6Z+Ffy7dXxd7Pj2Fxzs
x2sZy/N78CsHpdlseVR2bJ0cpm4O6XkMqCNqo98bMDGfsVR7/mrLZqrcZdCinkqa
ByFrgY/bxFn63iLABJzjqls2k+g9vXqhnQt2sQvHnf3PmKgGwvgqo6GDoLclcqUC
4wIDAQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNV
HQ4EFgQUA1yrc4GHqMywptWU4jaWSf8FmSwwDQYJKoZIhvcNAQEMBQADggIBAHx4
7PYCLLtbfpIrXTncvtgdokIzTfnvpCo7RGkerNlFo048p9gkUbJUHJNOxO97k4Vg
JuoJSOD1u8fpaNK7ajFxzHmuEajwmf3lH7wvqMxX63bEIaZHU1VNaL8FpO7XJqti
2kM3S+LGteWygxk6x9PbTZ4IevPuzz5i+6zoYMzRx6Fcg0XERczzF2sUyQQCPtIk
pnnpHs6i58FZFZ8d4kuaPp92CC1r2LpXFNqD6v6MVenQTqnMdzGxRBF6XLE+0xRF
FRhiJBPSy03OXIPBNvIQtQ6IbbjhVp+J3pZmOUdkLG5NrmJ7v2B0GbhWrJKsFjLt
rWhV/pi60zTe9Mlhww6G9kuEYO4Ne7UyWHmRVSyBQ7N0H3qqJZ4d16GLuc1CLgSk
ZoNNiTW2bKg2SnkheCLQQrzRQDGQob4Ez8pn7fXwgNNgyYMqIgXQBztSvwyeqiv5
u+YfjyW6hY0XHgL+XVAEV8/+LbzvXMAaq7afJMbfc2hIkCwU9D9SGuTSyxTDYWnP
4vkYxboznxSjBF25cfe1lNj2M8FawTSLfJvdkzrnE6JwYZ+vj+vYxXX4M2bUdGc6
N3ec592kD3ZDZopD8p/7DEJ4Y9HiD2971KE9dJeFt0g5QdYg/NA6s/rob8SKunE3
vouXsXgxT7PntgMTzlSdriVZzH81Xwj3QEUxeCp6
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCzCCAZGgAwIBAgISEdK7ujNu1LzmJGjFDYQdmOhDMAoGCCqGSM49BAMDMEYx
CzAJBgNVBAYTAkJFMRkwFwYDVQQKExBHbG9iYWxTaWduIG52LXNhMRwwGgYDVQQD
ExNHbG9iYWxTaWduIFJvb3QgRTQ2MB4XDTE5MDMyMDAwMDAwMFoXDTQ2MDMyMDAw
MDAwMFowRjELMAkGA1UEBhMCQkUxGTAXBgNVBAoTEEdsb2JhbFNpZ24gbnYtc2Ex
HDAaBgNVBAMTE0dsb2JhbFNpZ24gUm9vdCBFNDYwdjAQBgcqhkjOPQIBBgUrgQQA
IgNiAAScDrHPt+ieUnd1NPqlRqetMhkytAepJ8qUuwzSChDH2omwlwxwEwkBjtjq
R+q+soArzfwoDdusvKSGN+1wCAB16pMLey5SnCNoIwZD7JIvU4Tb+0cUB+hflGdd
yXqBPCCjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1Ud
DgQWBBQxCpCPtsad0kRLgLWi5h+xEk8blTAKBggqhkjOPQQDAwNoADBlAjEA31SQ
7Zvvi5QCkxeCmb6zniz2C5GMn0oUsfZkvLtoURMMA/cVi4RguYv/Uo7njLwcAjA8
+RHUjE7AwWHCFUyqqx0LMV87HOIAl0Qx5v5zli/altP+CAezNIm8BZ/3Hobui3A=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFgjCCA2qgAwIBAgILWku9WvtPilv6ZeUwDQYJKoZIhvcNAQELBQAwTTELMAkG
A1UEBhMCQVQxIzAhBgNVBAoTGmUtY29tbWVyY2UgbW9uaXRvcmluZyBHbWJIMRkw
FwYDVQQDExBHTE9CQUxUUlVTVCAyMDIwMB4XDTIwMDIxMDAwMDAwMFoXDTQwMDYx
MDAwMDAwMFowTTELMAkGA1UEBhMCQVQxIzAhBgNVBAoTGmUtY29tbWVyY2UgbW9u
aXRvcmluZyBHbWJIMRkwFwYDVQQDExBHTE9CQUxUUlVTVCAyMDIwMIICIjANBgkq
hkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAri5WrRsc7/aVj6B3GyvTY4+ETUWiD59b
RatZe1E0+eyLinjF3WuvvcTfk0Uev5E4C64OFudBc/jbu9G4UeDLgztzOG53ig9Z
YybNpyrOVPu44sB8R85gfD+yc/LAGbaKkoc1DZAoouQVBGM+uq/ufF7MpotQsjj3
QWPKzv9pj2gOlTblzLmMCcpL3TGQlsjMH/1WljTbjhzqLL6FLmPdqqmV0/0plRPw
yJiT2S0WR5ARg6I6IqIoV6Lr/sCMKKCmfecqQjuCgGOlYx8ZzHyyZqjC0203b+J+
BlHZRYQfEs4kUmSFC0iAToexIiIwquuuvuAC4EDosEKAA1GqtH6qRNdDYfOiaxaJ
SaSjpCuKAsR49GiKweR6NrFvG5Ybd0mN1MkGco/PU+PcF4UgStyYJ9ORJitHHmkH
r96i5OTUawuzXnzUJIBHKWk7buis/UDr2O1xcSvy6Fgd60GXIsUf1DnQJ4+H4xj0
4KlGDfV0OoIu0G4skaMxXDtG6nsEEFZegB31pWXogvziB4xiRfUg3kZwhqG8k9Me
dKZssCz3AwyIDMvUclOGvGBG85hqwvG/Q/lwIHfKN0F5VVJjjVsSn8VoxIidrPIw
q7ejMZdnrY8XD2zHc+0klGvIg5rQmjdJBKuxFshsSUktq6HQjJLyQUp5ISXbY9e2
nKd+Qmn7OmMCAwEAAaNjMGEwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AQYwHQYDVR0OBBYEFNwuH9FhN3nkq9XVsxJxaD1qaJwiMB8GA1UdIwQYMBaAFNwu
H9FhN3nkq9XVsxJxaD1qaJwiMA0GCSqGSIb3DQEBCwUAA4ICAQCR8EICaEDuw2jA
VC/f7GLDw56KoDEoqoOOpFaWEhCGVrqXctJUMHytGdUdaG/7FELYjQ7ztdGl4wJC
XtzoRlgHNQIw4Lx0SsFDKv/bGtCwr2zD/cuz9X9tAy5ZVp0tLTWMstZDFyySCstd
6IwPS3BD0IL/qMy/pJTAvoe9iuOTe8aPmxadJ2W8esVCgmxcB9CpwYhgROmYhRZf
+I/KARDOJcP5YBugxZfD0yyIMaK9MOzQ0MAS8cE54+X1+NZK3TTN+2/BT+MAi1bi
kvcoskJ3ciNnxz8RFbLEAwW+uxF7Cr+obuf/WEPPm2eggAe2HcqtbepBEX4tdJP7
wry+UUTF72glJ4DjyKDUEuzZpTcdN3y0kcra1LGWge9oXHYQSa9+pTeAsRxSvTOB
TI/53WXZFM2KJVj04sWDpQmQ1GwUY7VA3+vA/MRYfg0UFodUJ25W5HCEuGwyEn6C
MUO+1918oa2u1qsgEu8KwxCMSZY13At1XrFP1U80DhEgB3VDRemjEdqso5nCtnkn
4rnvyOL2NSl6dPrFf4IFYqYK6miyeUcGbvJXqBUzxvd4Sj1Ce2t+/vdG6tHrju+I
aFvowdlxfv1k7/9nR4hYJS8+hge9+6jlgqispdNpQ80xiEmEU5LAsTkbOYMBMMTy
qfrQA71yN2BWHzZ8vTmR9W0Nv3vXkg==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIF7zCCA9egAwIBAgIIDdPjvGz5a7EwDQYJKoZIhvcNAQELBQAwgYQxEjAQBgNV
BAUTCUc2MzI4NzUxMDELMAkGA1UEBhMCRVMxJzAlBgNVBAoTHkFORiBBdXRvcmlk
YWQgZGUgQ2VydGlmaWNhY2lvbjEUMBIGA1UECxMLQU5GIENBIFJhaXoxIjAgBgNV
BAMTGUFORiBTZWN1cmUgU2VydmVyIFJvb3QgQ0EwHhcNMTkwOTA0MTAwMDM4WhcN
MzkwODMwMTAwMDM4WjCBhDESMBAGA1UEBRMJRzYzMjg3NTEwMQswCQYDVQQGEwJF
UzEnMCUGA1UEChMeQU5GIEF1dG9yaWRhZCBkZSBDZXJ0aWZpY2FjaW9uMRQwEgYD
VQQLEwtBTkYgQ0EgUmFpejEiMCAGA1UEAxMZQU5GIFNlY3VyZSBTZXJ2ZXIgUm9v
dCBDQTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBANvrayvmZFSVgpCj
cqQZAZ2cC4Ffc0m6p6zzBE57lgvsEeBbphzOG9INgxwruJ4dfkUyYA8H6XdYfp9q
yGFOtibBTI3/TO80sh9l2Ll49a2pcbnvT1gdpd50IJeh7WhM3pIXS7yr/2WanvtH
2Vdy8wmhrnZEE26cLUQ5vPnHO6RYPUG9tMJJo8gN0pcvB2VSAKduyK9o7PQUlrZX
H1bDOZ8rbeTzPvY1ZNoMHKGESy9LS+IsJJ1tk0DrtSOOMspvRdOoiXsezx76W0OL
zc2oD2rKDF65nkeP8Nm2CgtYZRczuSPkdxl9y0oukntPLxB3sY0vaJxizOBQ+OyR
p1RMVwnVdmPF6GUe7m1qzwmd+nxPrWAI/VaZDxUse6mAq4xhj0oHdkLePfTdsiQz
W7i1o0TJrH93PB0j7IKppuLIBkwC/qxcmZkLLxCKpvR/1Yd0DVlJRfbwcVw5Kda/
SiOL9V8BY9KHcyi1Swr1+KuCLH5zJTIdC2MKF4EA/7Z2Xue0sUDKIbvVgFHlSFJn
LNJhiQcND85Cd8BEc5xEUKDbEAotlRyBr+Qc5RQe8TZBAQIvfXOn3kLMTOmJDVb3
n5HUA8ZsyY/b2BzgQJhdZpmYgG4t/wHFzstGH6wCxkPmrqKEPMVOHj1tyRRM4y5B
u8o5vzY8KhmqQYdOpc5LMnndkEl/AgMBAAGjYzBhMB8GA1UdIwQYMBaAFJxf0Gxj
o1+TypOYCK2Mh6UsXME3MB0GA1UdDgQWBBScX9BsY6Nfk8qTmAitjIelLFzBNzAO
BgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOC
AgEATh65isagmD9uw2nAalxJUqzLK114OMHVVISfk/CHGT0sZonrDUL8zPB1hT+L
9IBdeeUXZ701guLyPI59WzbLWoAAKfLOKyzxj6ptBZNscsdW699QIyjlRRA96Gej
rw5VD5AJYu9LWaL2U/HANeQvwSS9eS9OICI7/RogsKQOLHDtdD+4E5UGUcjohybK
pFtqFiGS3XNgnhAY3jyB6ugYw3yJ8otQPr0R4hUDqDZ9MwFsSBXXiJCZBMXM5gf0
vPSQ7RPi6ovDj6MzD8EpTBNO2hVWcXNyglD2mjN8orGoGjR0ZVzO0eurU+AagNjq
OknkJjCb5RyKqKkVMoaZkgoQI1YS4PbOTOK7vtuNknMBZi9iPrJyJ0U27U1W45eZ
/zo1PqVUSlJZS2Db7v54EX9K3BR5YLZrZAPbFYPhor72I5dQ8AkzNqdxliXzuUJ9
2zg/LFis6ELhDtjTO0wugumDLmsx2d1Hhk9tl5EuT+IocTUW0fJz/iUrB0ckYyfI
+PbZa/wSMVYIwFNCr5zQM378BvAxRAMU8Vjq8moNqRGyg77FGr8H6lnco4g175x2
MjxNBiLOFeXdntiP2t7SxDnlF4HPOEfrf4htWRvfn0IUrn7PqLBmZdo3r5+qPeoo
tt7VMVgWglvquxl1AnMaykgaIZOQCo6ThKd9OyMYkomgjaw=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICZTCCAeugAwIBAgIQeI8nXIESUiClBNAt3bpz9DAKBggqhkjOPQQDAzB0MQsw
CQYDVQQGEwJQTDEhMB8GA1UEChMYQXNzZWNvIERhdGEgU3lzdGVtcyBTLkEuMScw
JQYDVQQLEx5DZXJ0dW0gQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkxGTAXBgNVBAMT
EENlcnR1bSBFQy0zODQgQ0EwHhcNMTgwMzI2MDcyNDU0WhcNNDMwMzI2MDcyNDU0
WjB0MQswCQYDVQQGEwJQTDEhMB8GA1UEChMYQXNzZWNvIERhdGEgU3lzdGVtcyBT
LkEuMScwJQYDVQQLEx5DZXJ0dW0gQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkxGTAX
BgNVBAMTEENlcnR1bSBFQy0zODQgQ0EwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAATE
KI6rGFtqvm5kN2PkzeyrOvfMobgOgknXhimfoZTy42B4mIF4Bk3y7JoOV2CDn7Tm
Fy8as10CW4kjPMIRBSqniBMY81CE1700LCeJVf/OTOffph8oxPBUw7l8t1Ot68Kj
QjBAMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFI0GZnQkdjrzife81r1HfS+8
EF9LMA4GA1UdDwEB/wQEAwIBBjAKBggqhkjOPQQDAwNoADBlAjADVS2m5hjEfO/J
UG7BJw+ch69u1RsIGL2SKcHvlJF40jocVYli5RsJHrpka/F2tNQCMQC0QoSZ/6vn
nvuRlydd3LBbMHHOXjgaatkl5+r3YZJW+OraNsKHZZYuciUvf9/DE8k=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFwDCCA6igAwIBAgIQHr9ZULjJgDdMBvfrVU+17TANBgkqhkiG9w0BAQ0FADB6
MQswCQYDVQQGEwJQTDEhMB8GA1UEChMYQXNzZWNvIERhdGEgU3lzdGVtcyBTLkEu
MScwJQYDVQQLEx5DZXJ0dW0gQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkxHzAdBgNV
BAMTFkNlcnR1bSBUcnVzdGVkIFJvb3QgQ0EwHhcNMTgwMzE2MTIxMDEzWhcNNDMw
MzE2MTIxMDEzWjB6MQswCQYDVQQGEwJQTDEhMB8GA1UEChMYQXNzZWNvIERhdGEg
U3lzdGVtcyBTLkEuMScwJQYDVQQLEx5DZXJ0dW0gQ2VydGlmaWNhdGlvbiBBdXRo
b3JpdHkxHzAdBgNVBAMTFkNlcnR1bSBUcnVzdGVkIFJvb3QgQ0EwggIiMA0GCSqG
SIb3DQEBAQUAA4ICDwAwggIKAoICAQDRLY67tzbqbTeRn06TpwXkKQMlzhyC93yZ
n0EGze2jusDbCSzBfN8pfktlL5On1AFrAygYo9idBcEq2EXxkd7fO9CAAozPOA/q
p1x4EaTByIVcJdPTsuclzxFUl6s1wB52HO8AU5853BSlLCIls3Jy/I2z5T4IHhQq
NwuIPMqw9MjCoa68wb4pZ1Xi/K1ZXP69VyywkI3C7Te2fJmItdUDmj0VDT06qKhF
8JVOJVkdzZhpu9PMMsmN74H+rX2Ju7pgE8pllWeg8xn2A1bUatMn4qGtg/BKEiJ3
HAVz4hlxQsDsdUaakFjgao4rpUYwBI4Zshfjvqm6f1bxJAPXsiEodg42MEx51UGa
mqi4NboMOvJEGyCI98Ul1z3G4z5D3Yf+xOr1Uz5MZf87Sst4WmsXXw3Hw09Omiqi
7VdNIuJGmj8PkTQkfVXjjJU30xrwCSss0smNtA0Aq2cpKNgB9RkEth2+dv5yXMSF
ytKAQd8FqKPVhJBPC/PgP5sZ0jeJP/J7UhyM9uH3PAeXjA6iWYEMspA90+NZRu0P
qafegGtaqge2Gcu8V/OXIXoMsSt0Puvap2ctTMSYnjYJdmZm/Bo/6khUHL4wvYBQ
v3y1zgD2DGHZ5yQD4OMBgQ692IU0iL2yNqh7XAjlRICMb/gv1SHKHRzQ+8S1h9E6
Tsd2tTVItQIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQWBBSM+xx1
vALTn04uSNn5YFSqxLNP+jAOBgNVHQ8BAf8EBAMCAQYwDQYJKoZIhvcNAQENBQAD
ggIBAEii1QALLtA/vBzVtVRJHlpr9OTy4EA34MwUe7nJ+jW1dReTagVphZzNTxl4
WxmB82M+w85bj/UvXgF2Ez8sALnNllI5SW0ETsXpD4YN4fqzX4IS8TrOZgYkNCvo
zMrnadyHncI013nR03e4qllY/p0m+jiGPp2Kh2RX5Rc64vmNueMzeMGQ2Ljdt4NR
5MTMI9UGfOZR0800McD2RrsLrfw9EAUqO0qRJe6M1ISHgCq8CYyqOhNf6DR5UMEQ
GfnTKB7U0VEwKbOukGfWHwpjscWpxkIxYxeU72nLL/qMFH3EQxiJ2fAyQOaA4kZf
5ePBAFmo+eggvIksDkc0C+pXwlM2/KfUrzHN/gLldfq5Jwn58/U7yn2fqSLLiMmq
0Uc9NneoWWRrJ8/vJ8HjJLWG965+Mk2weWjROeiQWMODvA8s1pfrzgzhIMfatz7D
P78v3DSk+yshzWePS/Tj6tQ/50+6uaWTRRxmHyH6ZF5v4HaUMst19W7l9o/HuKTM
qJZ9ZPskWkoDbGs4xugDQ5r3V7mzKWmTOPQD8rv7gmsHINFSH5pkAnuYZttcTVoP
0ISVoDwUQwbKytu4QTbaakRnh6+v40URFWkIsr4WOZckbxJF0WddCajJFdr60qZf
E2Efv4WstK2tBZQIgx51F9NxO5NQI1mg7TyRVJ12AMXDuDjb
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFszCCA5ugAwIBAgIUEwLV4kBMkkaGFmddtLu7sms+/BMwDQYJKoZIhvcNAQEL
BQAwYTELMAkGA1UEBhMCVE4xNzA1BgNVBAoMLkFnZW5jZSBOYXRpb25hbGUgZGUg
Q2VydGlmaWNhdGlvbiBFbGVjdHJvbmlxdWUxGTAXBgNVBAMMEFR1blRydXN0IFJv
b3QgQ0EwHhcNMTkwNDI2MDg1NzU2WhcNNDQwNDI2MDg1NzU2WjBhMQswCQYDVQQG
EwJUTjE3MDUGA1UECgwuQWdlbmNlIE5hdGlvbmFsZSBkZSBDZXJ0aWZpY2F0aW9u
IEVsZWN0cm9uaXF1ZTEZMBcGA1UEAwwQVHVuVHJ1c3QgUm9vdCBDQTCCAiIwDQYJ
KoZIhvcNAQEBBQADggIPADCCAgoCggIBAMPN0/y9BFPdDCA61YguBUtB9YOCfvdZ
n56eY+hz2vYGqU8ftPkLHzmMmiDQfgbU7DTZhrx1W4eI8NLZ1KMKsmwb60ksPqxd
2JQDoOw05TDENX37Jk0bbjBU2PWARZw5rZzJJQRNmpA+TkBuimvNKWfGzC3gdOgF
VwpIUPp6Q9p+7FuaDmJ2/uqdHYVy7BG7NegfJ7/Boce7SBbdVtfMTqDhuazb1YMZ
GoXRlJfXyqNlC/M4+QKu3fZnz8k/9YosRxqZbwUN/dAdgjH8KcwAWJeRTIAAHDOF
li/LQcKLEITDCSSJH7UP2dl3RxiSlGBcx5kDPP73lad9UKGAwqmDrViWVSHbhlnU
r8a83YFuB9tgYv7sEG7aaAH0gxupPqJbI9dkxt/con3YS7qC0lH4Zr8GRuR5KiY2
eY8fTpkdso8MDhz/yV3A/ZAQprE38806JG60hZC/gLkMjNWb1sjxVj8agIl6qeIb
MlEsPvLfe/ZdeikZjuXIvTZxi11Mwh0/rViizz1wTaZQmCXcI/m4WEEIcb9PuISg
jwBUFfyRbVinljvrS5YnzWuioYasDXxU5mZMZl+QviGaAkYt5IPCgLnPSz7ofzwB
7I9ezX/SKEIBlYrilz0QIX32nRzFNKHsLA4KUiwSVXAkPcvCFDVDXSdOvsC9qnyW
5/yeYa1E0wCXAgMBAAGjYzBhMB0GA1UdDgQWBBQGmpsfU33x9aTI04Y+oXNZtPdE
ITAPBgNVHRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFAaamx9TffH1pMjThj6hc1m0
90QhMA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAgEAqgVutt0Vyb+z
xiD2BkewhpMl0425yAA/l/VSJ4hxyXT968pk21vvHl26v9Hr7lxpuhbI87mP0zYu
QEkHDVneixCwSQXi/5E/S7fdAo74gShczNxtr18UnH1YeA32gAm56Q6XKRm4t+v4
FstVEuTGfbvE7Pi1HE4+Z7/FXxttbUcoqgRYYdZ2vyJ/0Adqp2RT8JeNnYA/u8EH
22Wv5psymsNUk8QcCMNE+3tjEUPRahphanltkE8pjkcFwRJpadbGNjHh/PqAulxP
xOu3Mqz4dWEX1xAZufHSCe96Qp1bWgvUxpVOKs7/B9dPfhgGiPEZtdmYu65xxBzn
dFlY7wyJz4sfdZMaBBSSSFCp61cpABbjNhzI+L/wM9VBD8TMPN3pM0MBkRArHtG5
Xc0yGYuPjCB31yLEQtyEFpslbei0VXF/sHyz03FJuc9SpAQ/3D2gu68zngowYI7b
nV2UqL1g52KAdoGDDIzMMEZJ4gzSqK/rYXHv5yJiqfdcZGyfFoxnNidF9Ql7v/YQ
CvGwjVRDjAS6oz/v4jXH+XTgbzRB0L9zZVcg+ZtnemZoJE6AZb0QmQZZ8mWvuMZH
u/2QeItBcy6vVR/cO5JyboTT0GFMDcx2V+IthSIVNg3rAZ3r2OvEhJn7wAzMMujj
d9qDRIueVSjAi1jTkD5OGwDxFa2DK5o=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFpDCCA4ygAwIBAgIQOcqTHO9D88aOk8f0ZIk4fjANBgkqhkiG9w0BAQsFADBs
MQswCQYDVQQGEwJHUjE3MDUGA1UECgwuSGVsbGVuaWMgQWNhZGVtaWMgYW5kIFJl
c2VhcmNoIEluc3RpdHV0aW9ucyBDQTEkMCIGA1UEAwwbSEFSSUNBIFRMUyBSU0Eg
Um9vdCBDQSAyMDIxMB4XDTIxMDIxOTEwNTUzOFoXDTQ1MDIxMzEwNTUzN1owbDEL
MAkGA1UEBhMCR1IxNzA1BgNVBAoMLkhlbGxlbmljIEFjYWRlbWljIGFuZCBSZXNl
YXJjaCBJbnN0aXR1dGlvbnMgQ0ExJDAiBgNVBAMMG0hBUklDQSBUTFMgUlNBIFJv
b3QgQ0EgMjAyMTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAIvC569l
mwVnlskNJLnQDmT8zuIkGCyEf3dRywQRNrhe7Wlxp57kJQmXZ8FHws+RFjZiPTgE
4VGC/6zStGndLuwRo0Xua2s7TL+MjaQenRG56Tj5eg4MmOIjHdFOY9TnuEFE+2uv
a9of08WRiFukiZLRgeaMOVig1mlDqa2YUlhu2wr7a89o+uOkXjpFc5gH6l8Cct4M
pbOfrqkdtx2z/IpZ525yZa31MJQjB/OCFks1mJxTuy/K5FrZx40d/JiZ+yykgmvw
Kh+OC19xXFyuQnspiYHLA6OZyoieC0AJQTPb5lh6/a6ZcMBaD9YThnEvdmn8kN3b
LW7R8pv1GmuebxWMevBLKKAiOIAkbDakO/IwkfN4E8/BPzWr8R0RI7VDIp4BkrcY
AuUR0YLbFQDMYTfBKnya4dC6s1BG7oKsnTH4+yPiAwBIcKMJJnkVU2DzOFytOOqB
AGMUuTNe3QvboEUHGjMJ+E20pwKmafTCWQWIZYVWrkvL4N48fS0ayOn7H6NhStYq
E613TBoYm5EPWNgGVMWX+Ko/IIqmhaZ39qb8HOLubpQzKoNQhArlT4b4UEV4AIHr
W2jjJo3Me1xR9BQsQL4aYB16cmEdH2MtiKrOokWQCPxrvrNQKlr9qEgYRtaQQJKQ
CoReaDH46+0N0x3GfZkYVVYnZS6NRcUk7M7jAgMBAAGjQjBAMA8GA1UdEwEB/wQF
MAMBAf8wHQYDVR0OBBYEFApII6ZgpJIKM+qTW8VX6iVNvRLuMA4GA1UdDwEB/wQE
AwIBhjANBgkqhkiG9w0BAQsFAAOCAgEAPpBIqm5iFSVmewzVjIuJndftTgfvnNAU
X15QvWiWkKQUEapobQk1OUAJ2vQJLDSle1mESSmXdMgHHkdt8s4cUCbjnj1AUz/3
f5Z2EMVGpdAgS1D0NTsY9FVqQRtHBmg8uwkIYtlfVUKqrFOFrJVWNlar5AWMxaja
H6NpvVMPxP/cyuN+8kyIhkdGGvMA9YCRotxDQpSbIPDRzbLrLFPCU3hKTwSUQZqP
JzLB5UkZv/HywouoCjkxKLR9YjYsTewfM7Z+d21+UPCfDtcRj88YxeMn/ibvBZ3P
zzfF0HvaO7AWhAw6k9a+F9sPPg4ZeAnHqQJyIkv3N3a6dcSFA1pj1bF1BcK5vZSt
jBWZp5N99sXzqnTPBIWUmAD04vnKJGW/4GKvyMX6ssmeVkjaef2WdhW+o45WxLM0
/L5H9MG0qPzVMIho7suuyWPEdr6sOBjhXlzPrjoiUevRi7PzKzMHVIf6tLITe7pT
BGIBnfHAT+7hOtSLIBD6Alfm78ELt5BGnBkpjNxvoEppaZS3JGWg/6w/zgH7IS79
aPib8qXPMThcFarmlwDB31qlpzmq6YR/PFGoOtmUW4y/Twhx5duoXNTSpv4Ao8YW
xw/ogM4cKGR0GQjTQuPOAF1/sdwTsOEFy9EgqoZ0njnnkf3/W9b3raYvAwtt41dU
63ZTGI0RmLo=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICVDCCAdugAwIBAgIQZ3SdjXfYO2rbIvT/WeK/zjAKBggqhkjOPQQDAzBsMQsw
CQYDVQQGEwJHUjE3MDUGA1UECgwuSGVsbGVuaWMgQWNhZGVtaWMgYW5kIFJlc2Vh
cmNoIEluc3RpdHV0aW9ucyBDQTEkMCIGA1UEAwwbSEFSSUNBIFRMUyBFQ0MgUm9v
dCBDQSAyMDIxMB4XDTIxMDIxOTExMDExMFoXDTQ1MDIxMzExMDEwOVowbDELMAkG
A1UEBhMCR1IxNzA1BgNVBAoMLkhlbGxlbmljIEFjYWRlbWljIGFuZCBSZXNlYXJj
aCBJbnN0aXR1dGlvbnMgQ0ExJDAiBgNVBAMMG0hBUklDQSBUTFMgRUNDIFJvb3Qg
Q0EgMjAyMTB2MBAGByqGSM49AgEGBSuBBAAiA2IABDgI/rGgltJ6rK9JOtDA4MM7
KKrxcm1lAEeIhPyaJmuqS7psBAqIXhfyVYf8MLA04jRYVxqEU+kw2anylnTDUR9Y
STHMmE5gEYd103KUkE+bECUqqHgtvpBBWJAVcqeht6NCMEAwDwYDVR0TAQH/BAUw
AwEB/zAdBgNVHQ4EFgQUyRtTgRL+BNUW0aq8mm+3oJUZbsowDgYDVR0PAQH/BAQD
AgGGMAoGCCqGSM49BAMDA2cAMGQCMBHervjcToiwqfAircJRQO9gcS3ujwLEXQNw
SaSS6sUUiHCm0w2wqsosQJz76YJumgIwK0eaB8bRwoF8yguWGEEbo/QwCZ61IygN
nxS2PFOiTAZpffpskcYqSUXm7LcT4Tps
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIGFDCCA/ygAwIBAgIIG3Dp0v+ubHEwDQYJKoZIhvcNAQELBQAwUTELMAkGA1UE
BhMCRVMxQjBABgNVBAMMOUF1dG9yaWRhZCBkZSBDZXJ0aWZpY2FjaW9uIEZpcm1h
cHJvZmVzaW9uYWwgQ0lGIEE2MjYzNDA2ODAeFw0xNDA5MjMxNTIyMDdaFw0zNjA1
MDUxNTIyMDdaMFExCzAJBgNVBAYTAkVTMUIwQAYDVQQDDDlBdXRvcmlkYWQgZGUg
Q2VydGlmaWNhY2lvbiBGaXJtYXByb2Zlc2lvbmFsIENJRiBBNjI2MzQwNjgwggIi
MA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDKlmuO6vj78aI14H9M2uDDUtd9
thDIAl6zQyrET2qyyhxdKJp4ERppWVevtSBC5IsP5t9bpgOSL/UR5GLXMnE42QQM
cas9UX4PB99jBVzpv5RvwSmCwLTaUbDBPLutN0pcyvFLNg4kq7/DhHf9qFD0sefG
L9ItWY16Ck6WaVICqjaY7Pz6FIMMNx/Jkjd/14Et5cS54D40/mf0PmbR0/RAz15i
NA9wBj4gGFrO93IbJWyTdBSTo3OxDqqHECNZXyAFGUftaI6SEspd/NYrspI8IM/h
X68gvqB2f3bl7BqGYTM+53u0P6APjqK5am+5hyZvQWyIplD9amML9ZMWGxmPsu2b
m8mQ9QEM3xk9Dz44I8kvjwzRAv4bVdZO0I08r0+k8/6vKtMFnXkIoctXMbScyJCy
Z/QYFpM6/EfY0XiWMR+6KwxfXZmtY4laJCB22N/9q06mIqqdXuYnin1oKaPnirja
EbsXLZmdEyRG98Xi2J+Of8ePdG1asuhy9azuJBCtLxTa/y2aRnFHvkLfuwHb9H/T
KI8xWVvTyQKmtFLKbpf7Q8UIJm+K9Lv9nyiqDdVF8xM6HdjAeI9BZzwelGSuewvF
6NkBiDkal4ZkQdU7hwxu+g/GvUgUvzlN1J5Bto+WHWOWk9mVBngxaJ43BjuAiUVh
OSPHG0SjFeUc+JIwuwIDAQABo4HvMIHsMB0GA1UdDgQWBBRlzeurNR4APn7VdMAc
tHNHDhpkLzASBgNVHRMBAf8ECDAGAQH/AgEBMIGmBgNVHSAEgZ4wgZswgZgGBFUd
IAAwgY8wLwYIKwYBBQUHAgEWI2h0dHA6Ly93d3cuZmlybWFwcm9mZXNpb25hbC5j
b20vY3BzMFwGCCsGAQUFBwICMFAeTgBQAGEAcwBlAG8AIABkAGUAIABsAGEAIABC
AG8AbgBhAG4AbwB2AGEAIAA0ADcAIABCAGEAcgBjAGUAbABvAG4AYQAgADAAOAAw
ADEANzAOBgNVHQ8BAf8EBAMCAQYwDQYJKoZIhvcNAQELBQADggIBAHSHKAIrdx9m
iWTtj3QuRhy7qPj4Cx2Dtjqn6EWKB7fgPiDL4QjbEwj4KKE1soCzC1HA01aajTNF
Sa9J8OA9B3pFE1r/yJfY0xgsfZb43aJlQ3CTkBW6kN/oGbDbLIpgD7dvlAceHabJ
hfa9NPhAeGIQcDq+fUs5gakQ1JZBu/hfHAsdCPKxsIl68veg4MSPi3i1O1ilI45P
Vf42O+AMt8oqMEEgtIDNrvx2ZnOorm7hfNoD6JQg5iKj0B+QXSBTFCZX2lSX3xZE
EAEeiGaPcjiT3SC3NL7X8e5jjkd5KAb881lFJWAiMxujX6i6KtoaPc1A6ozuBRWV
1aUsIC+nmCjuRfzxuIgALI9C2lHVnOUTaHFFQ4ueCyE8S1wF3BqfmI7avSKecs2t
CsvMo2ebKHTEm9caPARYpoKdrcd7b/+Alun4jWq9GJAd/0kakFI3ky88Al2CdgtR
5xbHV/g4+afNmyJU72OwFW1TZQNKXkqgsqeOSQBZONXH9IBk9W6VULgRfhVwOEqw
f9DEMnDAGf/JOC0ULGb0QkTmVXYbgBVX/8Cnp6o5qtjTcNAuuuuUavpfNIbnYrX9
ivAwhZTJryQCL2/W3Wf+47BVTwSYT6RBVuKT0Gro1vP7ZeDOdcQxWQzugsgMYDNK
GbqEZycPvEJdvSRUDewdcAZfpLz6IHxV
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICDzCCAZWgAwIBAgIUbmq8WapTvpg5Z6LSa6Q75m0c1towCgYIKoZIzj0EAwMw
RzELMAkGA1UEBhMCQ04xHDAaBgNVBAoTE2lUcnVzQ2hpbmEgQ28uLEx0ZC4xGjAY
BgNVBAMTEXZUcnVzIEVDQyBSb290IENBMB4XDTE4MDczMTA3MjY0NFoXDTQzMDcz
MTA3MjY0NFowRzELMAkGA1UEBhMCQ04xHDAaBgNVBAoTE2lUcnVzQ2hpbmEgQ28u
LEx0ZC4xGjAYBgNVBAMTEXZUcnVzIEVDQyBSb290IENBMHYwEAYHKoZIzj0CAQYF
K4EEACIDYgAEZVBKrox5lkqqHAjDo6LN/llWQXf9JpRCux3NCNtzslt188+cToL0
v/hhJoVs1oVbcnDS/dtitN9Ti72xRFhiQgnH+n9bEOf+QP3A2MMrMudwpremIFUd
e4BdS49nTPEQo0IwQDAdBgNVHQ4EFgQUmDnNvtiyjPeyq+GtJK97fKHbH88wDwYD
VR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAQYwCgYIKoZIzj0EAwMDaAAwZQIw
V53dVvHH4+m4SVBrm2nDb+zDfSXkV5UTQJtS0zvzQBm8JsctBp61ezaf9SXUY2sA
AjEA6dPGnlaaKsyh2j/IZivTWJwghfqrkYpwcBE4YGQLYgmRWAD5Tfs0aNoJrSEG
GJTO
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFVjCCAz6gAwIBAgIUQ+NxE9izWRRdt86M/TX9b7wFjUUwDQYJKoZIhvcNAQEL
BQAwQzELMAkGA1UEBhMCQ04xHDAaBgNVBAoTE2lUcnVzQ2hpbmEgQ28uLEx0ZC4x
FjAUBgNVBAMTDXZUcnVzIFJvb3QgQ0EwHhcNMTgwNzMxMDcyNDA1WhcNNDMwNzMx
MDcyNDA1WjBDMQswCQYDVQQGEwJDTjEcMBoGA1UEChMTaVRydXNDaGluYSBDby4s
THRkLjEWMBQGA1UEAxMNdlRydXMgUm9vdCBDQTCCAiIwDQYJKoZIhvcNAQEBBQAD
ggIPADCCAgoCggIBAL1VfGHTuB0EYgWgrmy3cLRB6ksDXhA/kFocizuwZotsSKYc
IrrVQJLuM7IjWcmOvFjai57QGfIvWcaMY1q6n6MLsLOaXLoRuBLpDLvPbmyAhykU
AyyNJJrIZIO1aqwTLDPxn9wsYTwaP3BVm60AUn/PBLn+NvqcwBauYv6WTEN+VRS+
GrPSbcKvdmaVayqwlHeFXgQPYh1jdfdr58tbmnDsPmcF8P4HCIDPKNsFxhQnL4Z9
8Cfe/+Z+M0jnCx5Y0ScrUw5XSmXX+6KAYPxMvDVTAWqXcoKv8R1w6Jz1717CbMdH
flqUhSZNO7rrTOiwCcJlwp2dCZtOtZcFrPUGoPc2BX70kLJrxLT5ZOrpGgrIDajt
J8nU57O5q4IikCc9Kuh8kO+8T/3iCiSn3mUkpF3qwHYw03dQ+A0Em5Q2AXPKBlim
0zvc+gRGE1WKyURHuFE5Gi7oNOJ5y1lKCn+8pu8fA2dqWSslYpPZUxlmPCdiKYZN
pGvu/9ROutW04o5IWgAZCfEF2c6Rsffr6TlP9m8EQ5pV9T4FFL2/s1m02I4zhKOQ
UqqzApVg+QxMaPnu1RcN+HFXtSXkKe5lXa/R7jwXC1pDxaWG6iSe4gUH3DRCEpHW
OXSuTEGC2/KmSNGzm/MzqvOmwMVO9fSddmPmAsYiS8GVP1BkLFTltvA8Kc9XAgMB
AAGjQjBAMB0GA1UdDgQWBBRUYnBj8XWEQ1iO0RYgscasGrz2iTAPBgNVHRMBAf8E
BTADAQH/MA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQsFAAOCAgEAKbqSSaet
8PFww+SX8J+pJdVrnjT+5hpk9jprUrIQeBqfTNqK2uwcN1LgQkv7bHbKJAs5EhWd
nxEt/Hlk3ODg9d3gV8mlsnZwUKT+twpw1aA08XXXTUm6EdGz2OyC/+sOxL9kLX1j
bhd47F18iMjrjld22VkE+rxSH0Ws8HqA7Oxvdq6R2xCOBNyS36D25q5J08FsEhvM
Kar5CKXiNxTKsbhm7xqC5PD48acWabfbqWE8n/Uxy+QARsIvdLGx14HuqCaVvIiv
TDUHKgLKeBRtRytAVunLKmChZwOgzoy8sHJnxDHO2zTlJQNgJXtxmOTAGytfdELS
S8VZCAeHvsXDf+eW2eHcKJfWjwXj9ZtOyh1QRwVTsMo554WgicEFOwE30z9J4nfr
I8iIZjs9OXYhRvHsXyO466JmdXTBQPfYaJqT4i2pLr0cox7IdMakLXogqzu4sEb9
b91fUlV1YvCXoHzXOP0l382gmxDPi7g4Xl7FtKYCNqEeXxzP4padKar9mK5S4fNB
UvupLnKWnyfjqnN9+BojZns7q2WwMgFLFT49ok8MKzWixtlnEjUwzXYuFrOZnk1P
Ti07NEPhmg4NpGaXutIcSkwsKouLgU9xGqndXHt7CMUADTdA43x7VF8vhV929ven
sBxXVsFy6K2ir40zSbofitzmdHxghm+Hl3s=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICGzCCAaGgAwIBAgIQQdKd0XLq7qeAwSxs6S+HUjAKBggqhkjOPQQDAzBPMQsw
CQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJuZXQgU2VjdXJpdHkgUmVzZWFyY2gg
R3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBYMjAeFw0yMDA5MDQwMDAwMDBaFw00
MDA5MTcxNjAwMDBaME8xCzAJBgNVBAYTAlVTMSkwJwYDVQQKEyBJbnRlcm5ldCBT
ZWN1cml0eSBSZXNlYXJjaCBHcm91cDEVMBMGA1UEAxMMSVNSRyBSb290IFgyMHYw
EAYHKoZIzj0CAQYFK4EEACIDYgAEzZvVn4CDCuwJSvMWSj5cz3es3mcFDR0HttwW
+1qLFNvicWDEukWVEYmO6gbf9yoWHKS5xcUy4APgHoIYOIvXRdgKam7mAHf7AlF9
ItgKbppbd9/w+kHsOdx1ymgHDB/qo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0T
AQH/BAUwAwEB/zAdBgNVHQ4EFgQUfEKWrt5LSDv6kviejM9ti6lyN5UwCgYIKoZI
zj0EAwMDaAAwZQIwe3lORlCEwkSHRhtFcP9Ymd70/aTSVaYgLXTWNLxBo1BfASdW
tL4ndQavEi51mI38AjEAi/V3bNTIZargCyzuFJ0nN6T5U6VR5CmD1/iQMVtCnwr1
/q4AaOeMSQ+2b1tbFfLn
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFajCCA1KgAwIBAgIQLd2szmKXlKFD6LDNdmpeYDANBgkqhkiG9w0BAQsFADBP
MQswCQYDVQQGEwJUVzEjMCEGA1UECgwaQ2h1bmdod2EgVGVsZWNvbSBDby4sIEx0
ZC4xGzAZBgNVBAMMEkhpUEtJIFJvb3QgQ0EgLSBHMTAeFw0xOTAyMjIwOTQ2MDRa
Fw0zNzEyMzExNTU5NTlaME8xCzAJBgNVBAYTAlRXMSMwIQYDVQQKDBpDaHVuZ2h3
YSBUZWxlY29tIENvLiwgTHRkLjEbMBkGA1UEAwwSSGlQS0kgUm9vdCBDQSAtIEcx
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA9B5/UnMyDHPkvRN0o9Qw
qNCuS9i233VHZvR85zkEHmpwINJaR3JnVfSl6J3VHiGh8Ge6zCFovkRTv4354twv
Vcg3Px+kwJyz5HdcoEb+d/oaoDjq7Zpy3iu9lFc6uux55199QmQ5eiY29yTw1S+6
lZgRZq2XNdZ1AYDgr/SEYYwNHl98h5ZeQa/rh+r4XfEuiAU+TCK72h8q3VJGZDnz
Qs7ZngyzsHeXZJzA9KMuH5UHsBffMNsAGJZMoYFL3QRtU6M9/Aes1MU3guvklQgZ
KILSQjqj2FPseYlgSGDIcpJQ3AOPgz+yQlda22rpEZfdhSi8MEyr48KxRURHH+CK
FgeW0iEPU8DtqX7UTuybCeyvQqww1r/REEXgphaypcXTT3OUM3ECoWqj1jOXTyFj
HluP2cFeRXF3D4FdXyGarYPM+l7WjSNfGz1BryB1ZlpK9p/7qxj3ccC2HTHsOyDr
y+K49a6SsvfhhEvyovKTmiKe0xRvNlS9H15ZFblzqMF8b3ti6RZsR1pl8w4Rm0bZ
/W3c1pzAtH2lsN0/Vm+h+fbkEkj9Bn8SV7apI09bA8PgcSojt/ewsTu8mL3WmKgM
a/aOEmem8rJY5AIJEzypuxC00jBF8ez3ABHfZfjcK0NVvxaXxA/VLGGEqnKG/uY6
fsI/fe78LxQ+5oXdUG+3Se0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAdBgNV
HQ4EFgQU8ncX+l6o/vY9cdVouslGDDjYr7AwDgYDVR0PAQH/BAQDAgGGMA0GCSqG
SIb3DQEBCwUAA4ICAQBQUfB13HAE4/+qddRxosuej6ip0691x1TPOhwEmSKsxBHi
7zNKpiMdDg1H2DfHb680f0+BazVP6XKlMeJ45/dOlBhbQH3PayFUhuaVevvGyuqc
SE5XCV0vrPSltJczWNWseanMX/mF+lLFjfiRFOs6DRfQUsJ748JzjkZ4Bjgs6Fza
ZsT0pPBWGTMpWmWSBUdGSquEwx4noR8RkpkndZMPvDY7l1ePJlsMu5wP1G4wB9Tc
XzZoZjmDlicmisjEOf6aIW/Vcobpf2Lll07QJNBAsNB1CI69aO4I1258EHBGG3zg
iLKecoaZAeO/n0kZtCW+VmWuF2PlHt/o/0elv+EmBYTksMCv5wiZqAxeJoBF1Pho
L5aPruJKHJwWDBNvOIf2u8g0X5IDUXlwpt/L9ZlNec1OvFefQ05rLisY+GpzjLrF
Ne85akEez3GoorKGB1s6yeHvP2UEgEcyRHCVTjFnanRbEEV16rCf0OY1/k6fi8wr
kkVbbiVghUbN0aqwdmaTd5a+g744tiROJgvM7XpWGuDpWsZkrUx6AEhEL7lAuxM+
vhV4nYWBSipX3tUZQ9rbyltHhoMLP7YNdnhzeSJesYAfz77RP1YQmCuVh6EfnWQU
YDksswBVLuT1sw5XxJFBAJw/6KXf6vb/yPCtbVKoF6ubYfwSUTXkJf2vqmqGOQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB3DCCAYOgAwIBAgINAgPlfvU/k/2lCSGypjAKBggqhkjOPQQDAjBQMSQwIgYD
VQQLExtHbG9iYWxTaWduIEVDQyBSb290IENBIC0gUjQxEzARBgNVBAoTCkdsb2Jh
bFNpZ24xEzARBgNVBAMTCkdsb2JhbFNpZ24wHhcNMTIxMTEzMDAwMDAwWhcNMzgw
MTE5MDMxNDA3WjBQMSQwIgYDVQQLExtHbG9iYWxTaWduIEVDQyBSb290IENBIC0g
UjQxEzARBgNVBAoTCkdsb2JhbFNpZ24xEzARBgNVBAMTCkdsb2JhbFNpZ24wWTAT
BgcqhkjOPQIBBggqhkjOPQMBBwNCAAS4xnnTj2wlDp8uORkcA6SumuU5BwkWymOx
uYb4ilfBV85C+nOh92VC/x7BALJucw7/xyHlGKSq2XE/qNS5zowdo0IwQDAOBgNV
HQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUVLB7rUW44kB/
+wpu+74zyTyjhNUwCgYIKoZIzj0EAwIDRwAwRAIgIk90crlgr/HmnKAWBVBfw147
bmF0774BxL4YSFlhgjICICadVGNA3jdgUM/I2O2dgq43mLyjj0xMqTQrbO/7lZsm
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjEwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreVp0A8of2C+X0yBoJx9vaMf/vo
27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4IyfLpLGcY9vXmX7w
Cl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/SCYe7zUjw
TcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl
qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaH
szVsrBhQf4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8
Y/kB+Xj9e1x3+naH+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmk
MiVOKvFlRNACzqrOSbTqn3yDsEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92
wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97PtbfkSIS5r762DL8EGMUUXLeXdYWk70p
aDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmxlbCluQ0WEdrHbEg8QOB+DVrN
VjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACYPTrIrnqYNxgFlQID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb
C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEe
QkEzCzc9zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuy
h6f88/qBVRRiClmpIgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM4
7HLwEXWdyzRSjeZ2axfG34arJ45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8J
ZgfIPxz88NtFMN9iiMG1D53Dn0reWVlHxYciNuaCp+0KueIHoI17eko8cdLiA6Ef
MgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/bal8xa5meLMFrUKTX5hgUvYU/
Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAbd03OIozUhfJFfbdT
6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLsQBqvFAnZ
0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm
2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bb
bP6MvPJwNQzcmRk13NfIRmPVNnGuV/u3gm3c
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlrsWNBCUaqxElqjANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjIwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjIwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQDO3v2m++zsFDQ8BwZabFn3GTXd98GdVarTzTukk3LvCvpt
nfbwhYBboUhSnznFt+4orO/LdmgUud+tAWyZH8QiHZ/+cnfgLFuv5AS/T3KgGjSY
6Dlo7JUle3ah5mm5hRm9iYz+re026nO8/4Piy33B0s5Ks40FnotJk9/BW9BuXvAu
MC6C/Pq8tBcKSOWIm8Wba96wyrQD8Nr0kLhlZPdcTK3ofmZemde4wj7I0BOdre7k
RXuJVfeKH2JShBKzwkCX44ofR5GmdFrS+LFjKBC4swm4VndAoiaYecb+3yXuPuWg
f9RhD1FLPD+M2uFwdNjCaKH5wQzpoeJ/u1U8dgbuak7MkogwTZq9TwtImoS1mKPV
+3PBV2HdKFZ1E66HjucMUQkQdYhMvI35ezzUIkgfKtzra7tEscszcTJGr61K8Yzo
dDqs5xoic4DSMPclQsciOzsSrZYuxsN2B6ogtzVJV+mSSeh2FnIxZyuWfoqjx5RW
Ir9qS34BIbIjMt/kmkRtWVtd9QCgHJvGeJeNkP+byKq0rxFROV7Z+2et1VsRnTKa
G73VululycslaVNVJ1zgyjbLiGH7HrfQy+4W+9OmTN6SpdTi3/UGVN4unUu0kzCq
gc7dGtxRcw1PcOnlthYhGXmy5okLdWTK1au8CcEYof/UVKGFPP0UJAOyh9OktwID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQUu//KjiOfT5nK2+JopqUVJxce2Q4wDQYJKoZIhvcNAQEMBQADggIBAB/Kzt3H
vqGf2SdMC9wXmBFqiN495nFWcrKeGk6c1SuYJF2ba3uwM4IJvd8lRuqYnrYb/oM8
0mJhwQTtzuDFycgTE1XnqGOtjHsB/ncw4c5omwX4Eu55MaBBRTUoCnGkJE+M3DyC
B19m3H0Q/gxhswWV7uGugQ+o+MePTagjAiZrHYNSVc61LwDKgEDg4XSsYPWHgJ2u
NmSRXbBoGOqKYcl3qJfEycel/FVL8/B/uWU9J2jQzGv6U53hkRrJXRqWbTKH7QMg
yALOWr7Z6v2yTcQvG99fevX4i8buMTolUVVnjWQye+mew4K6Ki3pHrTgSAai/Gev
HyICc/sgCq+dVEuhzf9gR7A/Xe8bVr2XIZYtCtFenTgCR2y59PYjJbigapordwj6
xLEokCZYCDzifqrXPW+6MYgKBesntaFJ7qBFVHvmJ2WZICGoo7z7GJa7Um8M7YNR
TOlZ4iBgxcJlkoKM8xAfDoqXvneCbT+PHV28SSe9zE8P4c52hgQjxcCMElv924Sg
JPFI/2R80L5cFtHvma3AH/vLrrw4IgYmZNralw4/KBVEqE8AyvCazM90arQ+POuV
7LXTWtiBmelDGDfrs7vRWGJB82bSj6p4lVQgw1oudCvV0b4YacCs1aTPObpRhANl
6WLAYv7YTVWW4tAR+kg0Eeye7QUd5MjWHYbL
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPluILrIPglJ209ZjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjMwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjMwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AAQfTzOHMymKoYTey8chWEGJ6ladK0uFxh1MJ7x/JlFyb+Kf1qPKzEUURout736G
jOyxfi//qXGdGIRFBEFVbivqJn+7kAHjSxm65FSWRQmx1WyRRK2EE46ajA2ADDL2
4CejQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBTB8Sa6oC2uhYHP0/EqEr24Cmf9vDAKBggqhkjOPQQDAwNpADBmAjEA9uEglRR7
VKOQFhG/hMjqb2sXnh5GmCCbn9MN2azTL818+FsuVbu/3ZL3pAzcMeGiAjEA/Jdm
ZuVDFhOD3cffL74UOO0BzrEXGhF16b0DjyZ+hOXJYKaV11RZt+cRLInUue4X
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFdDCCA1ygAwIBAgIPAWdfJ9b+euPkrL4JWwWeMA0GCSqGSIb3DQEBCwUAMEQx
CzAJBgNVBAYTAkZJMRowGAYDVQQKDBFUZWxpYSBGaW5sYW5kIE95ajEZMBcGA1UE
AwwQVGVsaWEgUm9vdCBDQSB2MjAeFw0xODExMjkxMTU1NTRaFw00MzExMjkxMTU1
NTRaMEQxCzAJBgNVBAYTAkZJMRowGAYDVQQKDBFUZWxpYSBGaW5sYW5kIE95ajEZ
MBcGA1UEAwwQVGVsaWEgUm9vdCBDQSB2MjCCAiIwDQYJKoZIhvcNAQEBBQADggIP
ADCCAgoCggIBALLQPwe84nvQa5n44ndp586dpAO8gm2h/oFlH0wnrI4AuhZ76zBq
AMCzdGh+sq/H1WKzej9Qyow2RCRj0jbpDIX2Q3bVTKFgcmfiKDOlyzG4OiIjNLh9
vVYiQJ3q9HsDrWj8soFPmNB06o3lfc1jw6P23pLCWBnglrvFxKk9pXSW/q/5iaq9
lRdU2HhE8Qx3FZLgmEKnpNaqIJLNwaCzlrI6hEKNfdWV5Nbb6WLEWLN5xYzTNTOD
n3WhUidhOPFZPY5Q4L15POdslv5e2QJltI5c0BE0312/UqeBAMN/mUWZFdUXyApT
7GPzmX3MaRKGwhfwAZ6/hLzRUssbkmbOpFPlob/E2wnW5olWK8jjfN7j/4nlNW4o
6GwLI1GpJQXrSPjdscr6bAhR77cYbETKJuFzxokGgeWKrLDiKca5JLNrRBH0pUPC
TEPlcDaMtjNXepUugqD0XBCzYYP2AgWGLnwtbNwDRm41k9V6lS/eINhbfpSQBGq6
WT0EBXWdN6IOLj3rwaRSg/7Qa9RmjtzG6RJOHSpXqhC8fF6CfaamyfItufUXJ63R
DolUK5X6wK0dmBR4M0KGCqlztft0DbcbMBnEWg4cJ7faGND/isgFuvGqHKI3t+ZI
pEYslOqodmJHixBTB0hXbOKSTbauBcvcwUpej6w9GU7C7WB1K9vBykLVAgMBAAGj
YzBhMB8GA1UdIwQYMBaAFHKs5DN5qkWH9v2sHZ7Wxy+G2CQ5MB0GA1UdDgQWBBRy
rOQzeapFh/b9rB2e1scvhtgkOTAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUw
AwEB/zANBgkqhkiG9w0BAQsFAAOCAgEAoDtZpwmUPjaE0n4vOaWWl/oRrfxn83EJ
8rKJhGdEr7nv7ZbsnGTbMjBvZ5qsfl+yqwE2foH65IRe0qw24GtixX1LDoJt0nZi
0f6X+J8wfBj5tFJ3gh1229MdqfDBmgC9bXXYfef6xzijnHDoRnkDry5023X4blMM
A8iZGok1GTzTyVR8qPAs5m4HeW9q4ebqkYJpCh3DflminmtGFZhb069GHWLIzoBS
SRE/yQQSwxN8PzuKlts8oB4KtItUsiRnDe+Cy748fdHif64W1lZYudogsYMVoe+K
TTJvQS8TUoKU1xrBeKJR3Stwbbca+few4GeXVtt8YVMJAygCQMez2P2ccGrGKMOF
6eLtGpOg3kuYooQ+BXcBlj37tCAPnHICehIv1aO6UXivKitEZU61/Qrowc15h2Er
3oBXRb9n8ZuRXqWk7FlIEA04x7D6w0RtBPV4UBySllva9bguulvP5fBqnUsvWHMt
Ty3EHD70sz+rFQ47GUGKpMFXEmZxTPpT41frYpUJnlTd0cI8Vzy9OK2YZLe4A5pT
VmBds9hCG1xLEooc6+t9xnppxyd/pPiL8uSUZodL6ZQHCRJ5irLrdATczvREWeAW
ysUsWNc8e89ihmpQfTU2Zqf7N+cox9jQraVplI/owd8k+BsHMYeB2F326CjYSlKA
rBPuUBQemMc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIC2zCCAmCgAwIBAgIQfMmPK4TX3+oPyWWa00tNljAKBggqhkjOPQQDAzBIMQsw
CQYDVQQGEwJERTEVMBMGA1UEChMMRC1UcnVzdCBHbWJIMSIwIAYDVQQDExlELVRS
VVNUIEJSIFJvb3QgQ0EgMSAyMDIwMB4XDTIwMDIxMTA5NDUwMFoXDTM1MDIxMTA5
NDQ1OVowSDELMAkGA1UEBhMCREUxFTATBgNVBAoTDEQtVHJ1c3QgR21iSDEiMCAG
A1UEAxMZRC1UUlVTVCBCUiBSb290IENBIDEgMjAyMDB2MBAGByqGSM49AgEGBSuB
BAAiA2IABMbLxyjR+4T1mu9CFCDhQ2tuda38KwOE1HaTJddZO0Flax7mNCq7dPYS
zuht56vkPE4/RAiLzRZxy7+SmfSk1zxQVFKQhYN4lGdnoxwJGT11NIXe7WB9xwy0
QVK5buXuQqOCAQ0wggEJMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFHOREKv/
VbNafAkl1bK6CKBrqx9tMA4GA1UdDwEB/wQEAwIBBjCBxgYDVR0fBIG+MIG7MD6g
PKA6hjhodHRwOi8vY3JsLmQtdHJ1c3QubmV0L2NybC9kLXRydXN0X2JyX3Jvb3Rf
Y2FfMV8yMDIwLmNybDB5oHegdYZzbGRhcDovL2RpcmVjdG9yeS5kLXRydXN0Lm5l
dC9DTj1ELVRSVVNUJTIwQlIlMjBSb290JTIwQ0ElMjAxJTIwMjAyMCxPPUQtVHJ1
c3QlMjBHbWJILEM9REU/Y2VydGlmaWNhdGVyZXZvY2F0aW9ubGlzdDAKBggqhkjO
PQQDAwNpADBmAjEAlJAtE/rhY/hhY+ithXhUkZy4kzg+GkHaQBZTQgjKL47xPoFW
wKrY7RjEsK70PvomAjEA8yjixtsrmfu3Ubgko6SUeho/5jbiA1czijDLgsfWFBHV
dWNbFJWcHwHP2NVypw87
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIC2zCCAmCgAwIBAgIQXwJB13qHfEwDo6yWjfv/0DAKBggqhkjOPQQDAzBIMQsw
CQYDVQQGEwJERTEVMBMGA1UEChMMRC1UcnVzdCBHbWJIMSIwIAYDVQQDExlELVRS
VVNUIEVWIFJvb3QgQ0EgMSAyMDIwMB4XDTIwMDIxMTEwMDAwMFoXDTM1MDIxMTA5
NTk1OVowSDELMAkGA1UEBhMCREUxFTATBgNVBAoTDEQtVHJ1c3QgR21iSDEiMCAG
A1UEAxMZRC1UUlVTVCBFViBSb290IENBIDEgMjAyMDB2MBAGByqGSM49AgEGBSuB
BAAiA2IABPEL3YZDIBnfl4XoIkqbz52Yv7QFJsnL46bSj8WeeHsxiamJrSc8ZRCC
/N/DnU7wMyPE0jL1HLDfMxddxfCxivnvubcUyilKwg+pf3VlSSowZ/Rk99Yad9rD
wpdhQntJraOCAQ0wggEJMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFH8QARY3
OqQo5FD4pPfsazK2/umLMA4GA1UdDwEB/wQEAwIBBjCBxgYDVR0fBIG+MIG7MD6g
PKA6hjhodHRwOi8vY3JsLmQtdHJ1c3QubmV0L2NybC9kLXRydXN0X2V2X3Jvb3Rf
Y2FfMV8yMDIwLmNybDB5oHegdYZzbGRhcDovL2RpcmVjdG9yeS5kLXRydXN0Lm5l
dC9DTj1ELVRSVVNUJTIwRVYlMjBSb290JTIwQ0ElMjAxJTIwMjAyMCxPPUQtVHJ1
c3QlMjBHbWJILEM9REU/Y2VydGlmaWNhdGVyZXZvY2F0aW9ubGlzdDAKBggqhkjO
PQQDAwNpADBmAjEAyjzGKnXCXnViOTYAYFqLwZOZzNnbQTs7h5kXO9XMT8oi96CA
y/m0sRtW9XLS/BnRAjEAkfcwkz8QRitxpNA7RJvAKQIFskF3UfN5Wp6OFKBOQtJb
gfM0agPnIjhQW+0ZT0MW
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICGTCCAZ+gAwIBAgIQCeCTZaz32ci5PhwLBCou8zAKBggqhkjOPQQDAzBOMQsw
CQYDVQQGEwJVUzEXMBUGA1UEChMORGlnaUNlcnQsIEluYy4xJjAkBgNVBAMTHURp
Z2lDZXJ0IFRMUyBFQ0MgUDM4NCBSb290IEc1MB4XDTIxMDExNTAwMDAwMFoXDTQ2
MDExNDIzNTk1OVowTjELMAkGA1UEBhMCVVMxFzAVBgNVBAoTDkRpZ2lDZXJ0LCBJ
bmMuMSYwJAYDVQQDEx1EaWdpQ2VydCBUTFMgRUNDIFAzODQgUm9vdCBHNTB2MBAG
ByqGSM49AgEGBSuBBAAiA2IABMFEoc8Rl1Ca3iOCNQfN0MsYndLxf3c1TzvdlHJS
7cI7+Oz6e2tYIOyZrsn8aLN1udsJ7MgT9U7GCh1mMEy7H0cKPGEQQil8pQgO4CLp
0zVozptjn4S1mU1YoI71VOeVyaNCMEAwHQYDVR0OBBYEFMFRRVBZqz7nLFr6ICIS
B4CIfBFqMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49
BAMDA2gAMGUCMQCJao1H5+z8blUD2WdsJk6Dxv3J+ysTvLd6jLRl0mlpYxNjOyZQ
LgGheQaRnUi/wr4CMEfDFXuxoJGZSZOoPHzoRgaLLPIxAJSdYsiJvRmEFOml+wG4
DXZDjC5Ty3zfDBeWUA==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFZjCCA06gAwIBAgIQCPm0eKj6ftpqMzeJ3nzPijANBgkqhkiG9w0BAQwFADBN
MQswCQYDVQQGEwJVUzEXMBUGA1UEChMORGlnaUNlcnQsIEluYy4xJTAjBgNVBAMT
HERpZ2lDZXJ0IFRMUyBSU0E0MDk2IFJvb3QgRzUwHhcNMjEwMTE1MDAwMDAwWhcN
NDYwMTE0MjM1OTU5WjBNMQswCQYDVQQGEwJVUzEXMBUGA1UEChMORGlnaUNlcnQs
IEluYy4xJTAjBgNVBAMTHERpZ2lDZXJ0IFRMUyBSU0E0MDk2IFJvb3QgRzUwggIi
MA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCz0PTJeRGd/fxmgefM1eS87IE+
ajWOLrfn3q/5B03PMJ3qCQuZvWxX2hhKuHisOjmopkisLnLlvevxGs3npAOpPxG0
2C+JFvuUAT27L/gTBaF4HI4o4EXgg/RZG5Wzrn4DReW+wkL+7vI8toUTmDKdFqgp
wgscONyfMXdcvyej/Cestyu9dJsXLfKB2l2w4SMXPohKEiPQ6s+d3gMXsUJKoBZM
pG2T6T867jp8nVid9E6P/DsjyG244gXazOvswzH016cpVIDPRFtMbzCe88zdH5RD
nU1/cHAN1DrRN/BsnZvAFJNY781BOHW8EwOVfH/jXOnVDdXifBBiqmvwPXbzP6Po
sMH976pXTayGpxi0KcEsDr9kvimM2AItzVwv8n/vFfQMFawKsPHTDU9qTXeXAaDx
Zre3zu/O7Oyldcqs4+Fj97ihBMi8ez9dLRYiVu1ISf6nL3kwJZu6ay0/nTvEF+cd
Lvvyz6b84xQslpghjLSR6Rlgg/IwKwZzUNWYOwbpx4oMYIwo+FKbbuH2TbsGJJvX
KyY//SovcfXWJL5/MZ4PbeiPT02jP/816t9JXkGPhvnxd3lLG7SjXi/7RgLQZhNe
XoVPzthwiHvOAbWWl9fNff2C+MIkwcoBOU+NosEUQB+cZtUMCUbW8tDRSHZWOkPL
tgoRObqME2wGtZ7P6wIDAQABo0IwQDAdBgNVHQ4EFgQUUTMc7TZArxfTJc1paPKv
TiM+s0EwDgYDVR0PAQH/BAQDAgGGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcN
AQEMBQADggIBAGCmr1tfV9qJ20tQqcQjNSH/0GEwhJG3PxDPJY7Jv0Y02cEhJhxw
GXIeo8mH/qlDZJY6yFMECrZBu8RHANmfGBg7sg7zNOok992vIGCukihfNudd5N7H
PNtQOa27PShNlnx2xlv0wdsUpasZYgcYQF+Xkdycx6u1UQ3maVNVzDl92sURVXLF
O4uJ+DQtpBflF+aZfTCIITfNMBc9uPK8qHWgQ9w+iUuQrm0D4ByjoJYJu32jtyoQ
REtGBzRj7TG5BO6jm5qu5jF49OokYTurWGT/u4cnYiWB39yhL/btp/96j1EuMPik
AdKFOV8BmZZvWltwGUb+hmA+rYAQCd05JS9Yf7vSdPD3Rh9GOUrYU9DzLjtxpdRv
/PNn5AeP3SYZ4Y1b+qOTEZvpyDrDVWiakuFSdjjo4bq9+0/V77PnSIMx8IIh47a+
p6tv75/fTM8BuGJqIz3nCU2AG3swpMPdB380vqQmsvZB6Akd4yCYqjdP//fx4ilw
MUc/dNAUFvohigLVigmUdy7yWSiLfFCSCmZ4OIN1xLVaqBHG5cGdZlXPU8Sv13WF
qUITVuwhd4GTWgzqltlJyqEI8pc7bZsEGCREjnwB8twl2F6GmrE52/WRMmrRpnCK
ovfepEWFJqgejF0pW8hL2JpqA15w8oVPbEtoL8pU9ozaMv7Da4M/OMZ+
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFRzCCAy+gAwIBAgIRAI4P+UuQcWhlM1T01EQ5t+AwDQYJKoZIhvcNAQELBQAw
PTELMAkGA1UEBhMCVVMxEjAQBgNVBAoTCUNlcnRhaW5seTEaMBgGA1UEAxMRQ2Vy
dGFpbmx5IFJvb3QgUjEwHhcNMjEwNDAxMDAwMDAwWhcNNDYwNDAxMDAwMDAwWjA9
MQswCQYDVQQGEwJVUzESMBAGA1UEChMJQ2VydGFpbmx5MRowGAYDVQQDExFDZXJ0
YWlubHkgUm9vdCBSMTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBANA2
1B/q3avk0bbm+yLA3RMNansiExyXPGhjZjKcA7WNpIGD2ngwEc/csiu+kr+O5MQT
vqRoTNoCaBZ0vrLdBORrKt03H2As2/X3oXyVtwxwhi7xOu9S98zTm/mLvg7fMbed
aFySpvXl8wo0tf97ouSHocavFwDvA5HtqRxOcT3Si2yJ9HiG5mpJoM610rCrm/b0
1C7jcvk2xusVtyWMOvwlDbMicyF0yEqWYZL1LwsYpfSt4u5BvQF5+paMjRcCMLT5
r3gajLQ2EBAHBXDQ9DGQilHFhiZ5shGIXsXwClTNSaa/ApzSRKft43jvRl5tcdF5
cBxGX1HpyTfcX35pe0HfNEXgO4T0oYoKNp43zGJS4YkNKPl6I7ENPT2a/Z2B7yyQ
wHtETrtJ4A5KVpK8y7XdeReJkd5hiXSSqOMyhb5OhaRLWcsrxXiOcVTQAjeZjOVJ
6uBUcqQRBi8LjMFbvrWhsFNunLhgkR9Za/kt9JQKl7XsxXYDVBtlUrpMklZRNaBA
2CnbrlJ2Oy0wQJuK0EJWtLeIAaSHO1OWzaMWj/Nmqhexx2DgwUMFDO6bW2BvBlyH
Wyf5QBGenDPBt+U1VwV/J84XIIwc/PH72jEpSe31C4SnT8H2TsIonPru4K8H+zMR
eiFPCyEQtkA6qyI6BJyLm4SGcprSp6XEtHWRqSsjAgMBAAGjQjBAMA4GA1UdDwEB
/wQEAwIBBjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQWBBTgqj8ljZ9EXME66C6u
d0yEPmcM9DANBgkqhkiG9w0BAQsFAAOCAgEAuVevuBLaV4OPaAszHQNTVfSVcOQr
PbA56/qJYv331hgELyE03fFo8NWWWt7CgKPBjcZq91l3rhVkz1t5BXdm6ozTaw3d
8VkswTOlMIAVRQdFGjEitpIAq5lNOo93r6kiyi9jyhXWx8bwPWz8HA2YEGGeEaIi
1wrykXprOQ4vMMM2SZ/g6Q8CRFA3lFV96p/2O7qUpUzpvD5RtOjKkjZUbVwlKNrd
rRT90+7iIgXr0PK3aBLXWopBGsaSpVo7Y0VPv+E6dyIvXL9G+VoDhRNCX8reU9di
taY1BMJH/5n9hN9czulegChB8n3nHpDYT3Y+gjwN/KUD+nsa2UUeYNrEjvn8K8l7
lcUq/6qJ34IxD3L/DCfXCh5WAFAeDJDBlrXYFIW7pw0WwfgHJBu6haEaBQmAupVj
yTrsJZ9/nbqkRxWbRHDxakvWOF5D8xh+UG7pWijmZeZ3Gzr9Hb4DJqPb1OG7fpYn
Kx3upPvaJVQTA945xsMfTZDsjxtK0hzthZU4UHlG1sGQUDGpXJpuHfUzVounmdLy
yCwzk5Iwx06MZTMQZBf9JBeW0Y3COmor6xOLRPIh80oat3df1+2IpHLlOR+Vnb5n
wXARPbv0+Em34yaXOp/SX3z7wJl8OSngex2/DaeP0ik0biQVy96QXr8axGbqwua6
OV+KmalBWQewLK8=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB9zCCAX2gAwIBAgIQBiUzsUcDMydc+Y2aub/M+DAKBggqhkjOPQQDAzA9MQsw
CQYDVQQGEwJVUzESMBAGA1UEChMJQ2VydGFpbmx5MRowGAYDVQQDExFDZXJ0YWlu
bHkgUm9vdCBFMTAeFw0yMTA0MDEwMDAwMDBaFw00NjA0MDEwMDAwMDBaMD0xCzAJ
BgNVBAYTAlVTMRIwEAYDVQQKEwlDZXJ0YWlubHkxGjAYBgNVBAMTEUNlcnRhaW5s
eSBSb290IEUxMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAE3m/4fxzf7flHh4axpMCK
+IKXgOqPyEpeKn2IaKcBYhSRJHpcnqMXfYqGITQYUBsQ3tA3SybHGWCA6TS9YBk2
QNYphwk8kXr2vBMj3VlOBF7PyAIcGFPBMdjaIOlEjeR2o0IwQDAOBgNVHQ8BAf8E
BAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQU8ygYy2R17ikq6+2uI1g4
hevIIgcwCgYIKoZIzj0EAwMDaAAwZQIxALGOWiDDshliTd6wT99u0nCK8Z9+aozm
ut6Dacpps6kFtZaSF4fC0urQe87YQVt8rgIwRt7qy12a7DLCZRawTDBcMPPaTnOG
BtjOiQRINzf43TNRnXCve1XYAS59BWQOhriR
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICODCCAb6gAwIBAgIJANZdm7N4gS7rMAoGCCqGSM49BAMDMGExCzAJBgNVBAYT
AkpQMSUwIwYDVQQKExxTRUNPTSBUcnVzdCBTeXN0ZW1zIENPLixMVEQuMSswKQYD
VQQDEyJTZWN1cml0eSBDb21tdW5pY2F0aW9uIEVDQyBSb290Q0ExMB4XDTE2MDYx
NjA1MTUyOFoXDTM4MDExODA1MTUyOFowYTELMAkGA1UEBhMCSlAxJTAjBgNVBAoT
HFNFQ09NIFRydXN0IFN5c3RlbXMgQ08uLExURC4xKzApBgNVBAMTIlNlY3VyaXR5
IENvbW11bmljYXRpb24gRUNDIFJvb3RDQTEwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AASkpW9gAwPDvTH00xecK4R1rOX9PVdu12O/5gSJko6BnOPpR27KkBLIE+Cnnfdl
dB9sELLo5OnvbYUymUSxXv3MdhDYW72ixvnWQuRXdtyQwjWpS4g8EkdtXP9JTxpK
ULGjQjBAMB0GA1UdDgQWBBSGHOf+LaVKiwj+KBH6vqNm+GBZLzAOBgNVHQ8BAf8E
BAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAwNoADBlAjAVXUI9/Lbu
9zuxNuie9sRGKEkz0FhDKmMpzE2xtHqiuQ04pV1IKv3LsnNdo4gIxwwCMQDAqy0O
be0YottT6SXbVQjgUMzfRGEWgqtJsLKB7HOHeLRMsmIbEvoWTSVLY70eN9k=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFdDCCA1ygAwIBAgIQVW9l47TZkGobCdFsPsBsIDANBgkqhkiG9w0BAQsFADBU
MQswCQYDVQQGEwJDTjEmMCQGA1UECgwdQkVJSklORyBDRVJUSUZJQ0FURSBBVVRI
T1JJVFkxHTAbBgNVBAMMFEJKQ0EgR2xvYmFsIFJvb3QgQ0ExMB4XDTE5MTIxOTAz
MTYxN1oXDTQ0MTIxMjAzMTYxN1owVDELMAkGA1UEBhMCQ04xJjAkBgNVBAoMHUJF
SUpJTkcgQ0VSVElGSUNBVEUgQVVUSE9SSVRZMR0wGwYDVQQDDBRCSkNBIEdsb2Jh
bCBSb290IENBMTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAPFmCL3Z
xRVhy4QEQaVpN3cdwbB7+sN3SJATcmTRuHyQNZ0YeYjjlwE8R4HyDqKYDZ4/N+AZ
spDyRhySsTphzvq3Rp4Dhtczbu33RYx2N95ulpH3134rhxfVizXuhJFyV9xgw8O5
58dnJCNPYwpj9mZ9S1WnP3hkSWkSl+BMDdMJoDIwOvqfwPKcxRIqLhy1BDPapDgR
at7GGPZHOiJBhyL8xIkoVNiMpTAK+BcWyqw3/XmnkRd4OJmtWO2y3syJfQOcs4ll
5+M7sSKGjwZteAf9kRJ/sGsciQ35uMt0WwfCyPQ10WRjeulumijWML3mG90Vr4Tq
nMfK9Q7q8l0ph49pczm+LiRvRSGsxdRpJQaDrXpIhRMsDQa4bHlW/KNnMoH1V6XK
V0Jp6VwkYe/iMBhORJhVb3rCk9gZtt58R4oRTklH2yiUAguUSiz5EtBP6DF+bHq/
pj+bOT0CFqMYs2esWz8sgytnOYFcuX6U1WTdno9uruh8W7TXakdI136z1C2OVnZO
z2nxbkRs1CTqjSShGL+9V/6pmTW12xB3uD1IutbB5/EjPtffhZ0nPNRAvQoMvfXn
jSXWgXSHRtQpdaJCbPdzied9v3pKH9MiyRVVz99vfFXQpIsHETdfg6YmV6YBW37+
WGgHqel62bno/1Afq8K0wM7o6v0PvY1NuLxxAgMBAAGjQjBAMB0GA1UdDgQWBBTF
7+3M2I0hxkjk49cULqcWk+WYATAPBgNVHRMBAf8EBTADAQH/MA4GA1UdDwEB/wQE
AwIBBjANBgkqhkiG9w0BAQsFAAOCAgEAUoKsITQfI/Ki2Pm4rzc2IInRNwPWaZ+4
YRC6ojGYWUfo0Q0lHhVBDOAqVdVXUsv45Mdpox1NcQJeXyFFYEhcCY5JEMEE3Kli
awLwQ8hOnThJdMkycFRtwUf8jrQ2ntScvd0g1lPJGKm1Vrl2i5VnZu69mP6u775u
+2D2/VnGKhs/I0qUJDAnyIm860Qkmss9vk/Ves6OF8tiwdneHg56/0OGNFK8YT88
X7vZdrRTvJez/opMEi4r89fO4aL/3Xtw+zuhTaRjAv04l5U/BXCga99igUOLtFkN
SoxUnMW7gZ/NfaXvCyUeOiDbHPwfmGcCCtRzRBPbUYQaVQNW4AB+dAb/OMRyHdOo
P2gxXdMJxy6MW2Pg6Nwe0uxhHvLe5e/2mXZgLR6UcnHGCyoyx5JO1UbXHfmpGQrI
+pXObSOYqgs4rZpWDW+N8TEAiMEXnM0ZNjX+VVOg4DwzX5Ze4jLp3zO7Bkqp2IRz
znfSxqxx4VyjHQy7Ct9f4qNx2No3WqB4K/TUfet27fJhcKVlmtOJNBir+3I+17Q9
eVzYH6Eze9mCUAyTF6ps3MKCuwJXNq+YJyo5UOGwifUll35HaBC07HPKs5fRJNz2
YqAo07WjuGS3iGJCz51TzZm+ZGiPTx4SSPfSKcOYKMryMguTjClPPGAyzQWWYezy
r/6zcCwupvI=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICJTCCAaugAwIBAgIQLBcIfWQqwP6FGFkGz7RK6zAKBggqhkjOPQQDAzBUMQsw
CQYDVQQGEwJDTjEmMCQGA1UECgwdQkVJSklORyBDRVJUSUZJQ0FURSBBVVRIT1JJ
VFkxHTAbBgNVBAMMFEJKQ0EgR2xvYmFsIFJvb3QgQ0EyMB4XDTE5MTIxOTAzMTgy
MVoXDTQ0MTIxMjAzMTgyMVowVDELMAkGA1UEBhMCQ04xJjAkBgNVBAoMHUJFSUpJ
TkcgQ0VSVElGSUNBVEUgQVVUSE9SSVRZMR0wGwYDVQQDDBRCSkNBIEdsb2JhbCBS
b290IENBMjB2MBAGByqGSM49AgEGBSuBBAAiA2IABJ3LgJGNU2e1uVCxA/jlSR9B
IgmwUVJY1is0j8USRhTFiy8shP8sbqjV8QnjAyEUxEM9fMEsxEtqSs3ph+B99iK+
+kpRuDCK/eHeGBIK9ke35xe/J4rUQUyWPGCWwf0VHKNCMEAwHQYDVR0OBBYEFNJK
sVF/BvDRgh9Obl+rg/xI1LCRMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQD
AgEGMAoGCCqGSM49BAMDA2gAMGUCMBq8W9f+qdJUDkpd0m2xQNz0Q9XSSpkZElaA
94M04TVOSG0ED1cxMDAtsaqdAzjbBgIxAMvMh1PLet8gUXOQwKhbYdDFUDn9hf7B
43j4ptZLvZuHjw/l1lOWqzzIQNph91Oj9w==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICOjCCAcGgAwIBAgIQQvLM2htpN0RfFf51KBC49DAKBggqhkjOPQQDAzBfMQsw
CQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1T
ZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBFNDYwHhcN
MjEwMzIyMDAwMDAwWhcNNDYwMzIxMjM1OTU5WjBfMQswCQYDVQQGEwJHQjEYMBYG
A1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1TZWN0aWdvIFB1YmxpYyBT
ZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBFNDYwdjAQBgcqhkjOPQIBBgUrgQQA
IgNiAAR2+pmpbiDt+dd34wc7qNs9Xzjoq1WmVk/WSOrsfy2qw7LFeeyZYX8QeccC
WvkEN/U0NSt3zn8gj1KjAIns1aeibVvjS5KToID1AZTc8GgHHs3u/iVStSBDHBv+
6xnOQ6OjQjBAMB0GA1UdDgQWBBTRItpMWfFLXyY4qp3W7usNw/upYTAOBgNVHQ8B
Af8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAwNnADBkAjAn7qRa
qCG76UeXlImldCBteU/IvZNeWBj7LRoAasm4PdCkT0RHlAFWovgzJQxC36oCMB3q
4S6ILuH5px0CMk7yn2xVdOOurvulGu7t0vzCAxHrRVxgED1cf5kDW21USAGKcw==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFijCCA3KgAwIBAgIQdY39i658BwD6qSWn4cetFDANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNNDYwMzIxMjM1OTU5WjBfMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYwggIiMA0GCSqGSIb3DQEB
AQUAA4ICDwAwggIKAoICAQCTvtU2UnXYASOgHEdCSe5jtrch/cSV1UgrJnwUUxDa
ef0rty2k1Cz66jLdScK5vQ9IPXtamFSvnl0xdE8H/FAh3aTPaE8bEmNtJZlMKpnz
SDBh+oF8HqcIStw+KxwfGExxqjWMrfhu6DtK2eWUAtaJhBOqbchPM8xQljeSM9xf
iOefVNlI8JhD1mb9nxc4Q8UBUQvX4yMPFF1bFOdLvt30yNoDN9HWOaEhUTCDsG3X
ME6WW5HwcCSrv0WBZEMNvSE6Lzzpng3LILVCJ8zab5vuZDCQOc2TZYEhMbUjUDM3
IuM47fgxMMxF/mL50V0yeUKH32rMVhlATc6qu/m1dkmU8Sf4kaWD5QazYw6A3OAS
VYCmO2a0OYctyPDQ0RTp5A1NDvZdV3LFOxxHVp3i1fuBYYzMTYCQNFu31xR13NgE
SJ/AwSiItOkcyqex8Va3e0lMWeUgFaiEAin6OJRpmkkGj80feRQXEgyDet4fsZfu
+Zd4KKTIRJLpfSYFplhym3kT2BFfrsU4YjRosoYwjviQYZ4ybPUHNs2iTG7sijbt
8uaZFURww3y8nDnAtOFr94MlI1fZEoDlSfB1D++N6xybVCi0ITz8fAr/73trdf+L
HaAZBav6+CuBQug4urv7qv094PPK306Xlynt8xhW6aWWrL3DkJiy4Pmi1KZHQ3xt
zwIDAQABo0IwQDAdBgNVHQ4EFgQUVnNYZJX5khqwEioEYnmhQBWIIUkwDgYDVR0P
AQH/BAQDAgGGMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEMBQADggIBAC9c
mTz8Bl6MlC5w6tIyMY208FHVvArzZJ8HXtXBc2hkeqK5Duj5XYUtqDdFqij0lgVQ
YKlJfp/imTYpE0RHap1VIDzYm/EDMrraQKFz6oOht0SmDpkBm+S8f74TlH7Kph52
gDY9hAaLMyZlbcp+nv4fjFg4exqDsQ+8FxG75gbMY/qB8oFM2gsQa6H61SilzwZA
Fv97fRheORKkU55+MkIQpiGRqRxOF3yEvJ+M0ejf5lG5Nkc/kLnHvALcWxxPDkjB
JYOcCj+esQMzEhonrPcibCTRAUH4WAP+JWgiH5paPHxsnnVI84HxZmduTILA7rpX
DhjvLpr3Etiga+kFpaHpaPi8TD8SHkXoUsCjvxInebnMMTzD9joiFgOgyY9mpFui
TdaBJQbpdqQACj7LzTWb4OE4y2BThihCQRxEV+ioratF4yUQvNs+ZUH7G6aXD+u5
dHn5HrwdVw1Hr8Mvn4dGp+smWg9WY7ViYG4A++MnESLn/pmPNPW56MORcr3Ywx65
LvKRRFHQV80MNNVIIb/bE/FmJUNS0nAiNs2fxBx1IK1jcmMGDw4nztJqDby1ORrp
0XZ60Vzk50lJLVU3aPAaOpg+VBeHVOmmJ1CJeyAvP/+/oYtKR5j/K3tJPsMpRmAY
QqszKbrAKbkTidOIijlBO8n9pu0f9GBj39ItVQGL
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFiTCCA3GgAwIBAgIQb77arXO9CEDii02+1PdbkTANBgkqhkiG9w0BAQsFADBO
MQswCQYDVQQGEwJVUzEYMBYGA1UECgwPU1NMIENvcnBvcmF0aW9uMSUwIwYDVQQD
DBxTU0wuY29tIFRMUyBSU0EgUm9vdCBDQSAyMDIyMB4XDTIyMDgyNTE2MzQyMloX
DTQ2MDgxOTE2MzQyMVowTjELMAkGA1UEBhMCVVMxGDAWBgNVBAoMD1NTTCBDb3Jw
b3JhdGlvbjElMCMGA1UEAwwcU1NMLmNvbSBUTFMgUlNBIFJvb3QgQ0EgMjAyMjCC
AiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBANCkCXJPQIgSYT41I57u9nTP
L3tYPc48DRAokC+X94xI2KDYJbFMsBFMF3NQ0CJKY7uB0ylu1bUJPiYYf7ISf5OY
t6/wNr/y7hienDtSxUcZXXTzZGbVXcdotL8bHAajvI9AI7YexoS9UcQbOcGV0ins
S657Lb85/bRi3pZ7QcacoOAGcvvwB5cJOYF0r/c0WRFXCsJbwST0MXMwgsadugL3
PnxEX4MN8/HdIGkWCVDi1FW24IBydm5MR7d1VVm0U3TZlMZBrViKMWYPHqIbKUBO
L9975hYsLfy/7PO0+r4Y9ptJ1O4Fbtk085zx7AGL0SDGD6C1vBdOSHtRwvzpXGk3
R2azaPgVKPC506QVzFpPulJwoxJF3ca6TvvC0PeoUidtbnm1jPx7jMEWTO6Af77w
dr5BUxIzrlo4QqvXDz5BjXYHMtWrifZOZ9mxQnUjbvPNQrL8VfVThxc7wDNY8VLS
+YCk8OjwO4s4zKTGkH8PnP2L0aPP2oOnaclQNtVcBdIKQXTbYxE3waWglksejBYS
d66UNHsef8JmAOSqg+qKkK3ONkRN0VHpvB/zagX9wHQfJRlAUW7qglFA35u5CCoG
AtUjHBPW6dvbxrB6y3snm/vg1UYk7RBLY0ulBY+6uB0rpvqR4pJSvezrZ5dtmi2f
gTIFZzL7SAg/2SW4BCUvAgMBAAGjYzBhMA8GA1UdEwEB/wQFMAMBAf8wHwYDVR0j
BBgwFoAU+y437uOEeicuzRk1sTN8/9REQrkwHQYDVR0OBBYEFPsuN+7jhHonLs0Z
NbEzfP/UREK5MA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsFAAOCAgEAjYlt
hEUY8U+zoO9opMAdrDC8Z2awms22qyIZZtM7QbUQnRC6cm4pJCAcAZli05bg4vsM
QtfhWsSWTVTNj8pDU/0quOr4ZcoBwq1gaAafORpR2eCNJvkLTqVTJXojpBzOCBvf
R4iyrT7gJ4eLSYwfqUdYe5byiB0YrrPRpgqU+tvT5TgKa3kSM/tKWTcWQA673vWJ
DPFs0/dRa1419dvAJuoSc06pkZCmF8NsLzjUo3KUQyxi4U5cMj29TH0ZR6LDSeeW
P4+a0zvkEdiLA9z2tmBVGKaBUfPhqBVq6+AL8BQx1rmMRTqoENjwuSfr98t67wVy
lrXEj5ZzxOhWc5y8aVFjvO9nHEMaX3cZHxj4HCUp+UmZKbaSPaKDN7EgkaibMOlq
bLQjk2UEqxHzDh1TJElTHaE/nUiSEeJ9DU/1172iWD54nR4fK/4huxoTtrEoZP2w
AgDHbICivRZQIA9ygV/MlP+7mea6kMvq+cYMwq7FGc4zoWtcu358NFcXrfA/rs3q
r5nsLFR+jM4uElZI7xc7P0peYNLcdDa8pUNjyw9bowJWCZ4kLOGGgYz+qxcs+sji
Mho6/4UIyYOf8kpIEFR3N+2ivEC+5BB09+Rbu7nzifmPQdjH5FCQNYA+HLhNkNPU
98OwoX6EyneSMSy4kLGCenROmxMmtNVQZlR4rmA=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICOjCCAcCgAwIBAgIQFAP1q/s3ixdAW+JDsqXRxDAKBggqhkjOPQQDAzBOMQsw
CQYDVQQGEwJVUzEYMBYGA1UECgwPU1NMIENvcnBvcmF0aW9uMSUwIwYDVQQDDBxT
U0wuY29tIFRMUyBFQ0MgUm9vdCBDQSAyMDIyMB4XDTIyMDgyNTE2MzM0OFoXDTQ2
MDgxOTE2MzM0N1owTjELMAkGA1UEBhMCVVMxGDAWBgNVBAoMD1NTTCBDb3Jwb3Jh
dGlvbjElMCMGA1UEAwwcU1NMLmNvbSBUTFMgRUNDIFJvb3QgQ0EgMjAyMjB2MBAG
ByqGSM49AgEGBSuBBAAiA2IABEUpNXP6wrgjzhR9qLFNoFs27iosU8NgCTWyJGYm
acCzldZdkkAZDsalE3D07xJRKF3nzL35PIXBz5SQySvOkkJYWWf9lCcQZIxPBLFN
SeR7T5v15wj4A4j3p8OSSxlUgaNjMGEwDwYDVR0TAQH/BAUwAwEB/zAfBgNVHSME
GDAWgBSJjy+j6CugFFR781a4Jl9nOAuc0DAdBgNVHQ4EFgQUiY8vo+groBRUe/NW
uCZfZzgLnNAwDgYDVR0PAQH/BAQDAgGGMAoGCCqGSM49BAMDA2gAMGUCMFXjIlbp
15IkWE8elDIPDAI2wv2sdDJO4fscgIijzPvX6yv/N33w7deedWo1dlJF4AIxAMeN
b0Igj762TVntd00pxCAgRWSGOlDGxK0tk/UYfXLtqc/ErFc2KAhl3zx5Zn6g6g==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICFTCCAZugAwIBAgIQPZg7pmY9kGP3fiZXOATvADAKBggqhkjOPQQDAzBMMS4w
LAYDVQQDDCVBdG9zIFRydXN0ZWRSb290IFJvb3QgQ0EgRUNDIFRMUyAyMDIxMQ0w
CwYDVQQKDARBdG9zMQswCQYDVQQGEwJERTAeFw0yMTA0MjIwOTI2MjNaFw00MTA0
MTcwOTI2MjJaMEwxLjAsBgNVBAMMJUF0b3MgVHJ1c3RlZFJvb3QgUm9vdCBDQSBF
Q0MgVExTIDIwMjExDTALBgNVBAoMBEF0b3MxCzAJBgNVBAYTAkRFMHYwEAYHKoZI
zj0CAQYFK4EEACIDYgAEloZYKDcKZ9Cg3iQZGeHkBQcfl+3oZIK59sRxUM6KDP/X
tXa7oWyTbIOiaG6l2b4siJVBzV3dscqDY4PMwL502eCdpO5KTlbgmClBk1IQ1SQ4
AjJn8ZQSb+/Xxd4u/RmAo0IwQDAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQWBBR2
KCXWfeBmmnoJsmo7jjPXNtNPojAOBgNVHQ8BAf8EBAMCAYYwCgYIKoZIzj0EAwMD
aAAwZQIwW5kp85wxtolrbNa9d+F851F+uDrNozZffPc8dz7kUK2o59JZDCaOMDtu
CCrCp1rIAjEAmeMM56PDr9NJLkaCI2ZdyQAUEv049OGYa3cpetskz2VAv9LcjBHo
9H1/IISpQuQo
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFZDCCA0ygAwIBAgIQU9XP5hmTC/srBRLYwiqipDANBgkqhkiG9w0BAQwFADBM
MS4wLAYDVQQDDCVBdG9zIFRydXN0ZWRSb290IFJvb3QgQ0EgUlNBIFRMUyAyMDIx
MQ0wCwYDVQQKDARBdG9zMQswCQYDVQQGEwJERTAeFw0yMTA0MjIwOTIxMTBaFw00
MTA0MTcwOTIxMDlaMEwxLjAsBgNVBAMMJUF0b3MgVHJ1c3RlZFJvb3QgUm9vdCBD
QSBSU0EgVExTIDIwMjExDTALBgNVBAoMBEF0b3MxCzAJBgNVBAYTAkRFMIICIjAN
BgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAtoAOxHm9BYx9sKOdTSJNy/BBl01Z
4NH+VoyX8te9j2y3I49f1cTYQcvyAh5x5en2XssIKl4w8i1mx4QbZFc4nXUtVsYv
Ye+W/CBGvevUez8/fEc4BKkbqlLfEzfTFRVOvV98r61jx3ncCHvVoOX3W3WsgFWZ
kmGbzSoXfduP9LVq6hdKZChmFSlsAvFr1bqjM9xaZ6cF4r9lthawEO3NUDPJcFDs
GY6wx/J0W2tExn2WuZgIWWbeKQGb9Cpt0xU6kGpn8bRrZtkh68rZYnxGEFzedUln
nkL5/nWpo63/dgpnQOPF943HhZpZnmKaau1Fh5hnstVKPNe0OwANwI8f4UDErmwh
3El+fsqyjW22v5MvoVw+j8rtgI5Y4dtXz4U2OLJxpAmMkokIiEjxQGMYsluMWuPD
0xeqqxmjLBvk1cbiZnrXghmmOxYsL3GHX0WelXOTwkKBIROW1527k2gV+p2kHYzy
geBYBr3JtuP2iV2J+axEoctr+hbxx1A9JNr3w+SH1VbxT5Aw+kUJWdo0zuATHAR8
ANSbhqRAvNncTFd+rrcztl524WWLZt+NyteYr842mIycg5kDcPOvdO3GDjbnvezB
c6eUWsuSZIKmAMFwoW4sKeFYV+xafJlrJaSQOoD0IJ2azsct+bJLKZWD6TWNp0lI
pw9MGZHQ9b8Q4HECAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQU
dEmZ0f+0emhFdcN+tNzMzjkz2ggwDgYDVR0PAQH/BAQDAgGGMA0GCSqGSIb3DQEB
DAUAA4ICAQAjQ1MkYlxt/T7Cz1UAbMVWiLkO3TriJQ2VSpfKgInuKs1l+NsW4AmS
4BjHeJi78+xCUvuppILXTdiK/ORO/auQxDh1MoSf/7OwKwIzNsAQkG8dnK/haZPs
o0UvFJ/1TCplQ3IM98P4lYsU84UgYt1UU90s3BiVaU+DR3BAM1h3Egyi61IxHkzJ
qM7F78PRreBrAwA0JrRUITWXAdxfG/F851X6LWh3e9NpzNMOa7pNdkTWwhWaJuyw
xfW70Xp0wmzNxbVe9kzmWy2B27O3Opee7c9GslA9hGCZcbUztVdF5kJHdWoOsAgM
rr3e97sPWD2PAzHoPYJQyi9eDF20l74gNAf0xBLh7tew2VktafcxBPTy+av5EzH4
AXcOPUIjJsyacmdRIXrMPIWo6iFqO9taPKU0nprALN+AnCng33eU0aKAQv9qTFsR
0PXNor6uzFFcw9VUewyu1rkGd4Di7wcaaMxZUa1+XGdrudviB0JbuAEFWDlN5LuY
o7Ey7Nmj1m+UI/87tyll5gfp77YZ6ufCOB0yiJA8EytuzO+rdwY0d4RPcuSBhPm5
dDTedk+SKlOxJTnbPP/lPqYO5Wue/9vsL3SD3460s6neFE3/MaNFcyT6lSnMEpcE
oji2jbDwN/zIIX8/syQbPYtuzE2wFg2WHYMfRsCbvUOZ58SWLs5fyQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFpTCCA42gAwIBAgIUZPYOZXdhaqs7tOqFhLuxibhxkw8wDQYJKoZIhvcNAQEM
BQAwWjELMAkGA1UEBhMCQ04xJTAjBgNVBAoMHFRydXN0QXNpYSBUZWNobm9sb2dp
ZXMsIEluYy4xJDAiBgNVBAMMG1RydXN0QXNpYSBHbG9iYWwgUm9vdCBDQSBHMzAe
Fw0yMTA1MjAwMjEwMTlaFw00NjA1MTkwMjEwMTlaMFoxCzAJBgNVBAYTAkNOMSUw
IwYDVQQKDBxUcnVzdEFzaWEgVGVjaG5vbG9naWVzLCBJbmMuMSQwIgYDVQQDDBtU
cnVzdEFzaWEgR2xvYmFsIFJvb3QgQ0EgRzMwggIiMA0GCSqGSIb3DQEBAQUAA4IC
DwAwggIKAoICAQDAMYJhkuSUGwoqZdC+BqmHO1ES6nBBruL7dOoKjbmzTNyPtxNS
T1QY4SxzlZHFZjtqz6xjbYdT8PfxObegQ2OwxANdV6nnRM7EoYNl9lA+sX4WuDqK
AtCWHwDNBSHvBm3dIZwZQ0WhxeiAysKtQGIXBsaqvPPW5vxQfmZCHzyLpnl5hkA1
nyDvP+uLRx+PjsXUjrYsyUQE49RDdT/VP68czH5GX6zfZBCK70bwkPAPLfSIC7Ep
qq+FqklYqL9joDiR5rPmd2jE+SoZhLsO4fWvieylL1AgdB4SQXMeJNnKziyhWTXA
yB1GJ2Faj/lN03J5Zh6fFZAhLf3ti1ZwA0pJPn9pMRJpxx5cynoTi+jm9WAPzJMs
hH/x/Gr8m0ed262IPfN2dTPXS6TIi/n1Q1hPy8gDVI+lhXgEGvNz8teHHUGf59gX
zhqcD0r83ERoVGjiQTz+LISGNzzNPy+i2+f3VANfWdP3kXjHi3dqFuVJhZBFcnAv
kV34PmVACxmZySYgWmjBNb9Pp1Hx2BErW+Canig7CjoKH8GB5S7wprlppYiU5msT
f9FkPz2ccEblooV7WIQn3MSAPmeamseaMQ4w7OYXQJXZRe0Blqq/DPNL0WP3E1jA
uPP6Z92bfW1K/zJMtSU7/xxnD4UiWQWRkUF3gdCFTIcQcf+eQxuulXUtgQIDAQAB
o2MwYTAPBgNVHRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFEDk5PIj7zjKsK5Xf/Ih
MBY027ySMB0GA1UdDgQWBBRA5OTyI+84yrCuV3/yITAWNNu8kjAOBgNVHQ8BAf8E
BAMCAQYwDQYJKoZIhvcNAQEMBQADggIBACY7UeFNOPMyGLS0XuFlXsSUT9SnYaP4
wM8zAQLpw6o1D/GUE3d3NZ4tVlFEbuHGLige/9rsR82XRBf34EzC4Xx8MnpmyFq2
XFNFV1pF1AWZLy4jVe5jaN/TG3inEpQGAHUNcoTpLrxaatXeL1nHo+zSh2bbt1S1
JKv0Q3jbSwTEb93mPmY+KfJLaHEih6D4sTNjduMNhXJEIlU/HHzp/LgV6FL6qj6j
ITk1dImmasI5+njPtqzn59ZW/yOSLlALqbUHM/Q4X6RJpstlcHboCoWASzY9M/eV
VHUl2qzEc4Jl6VL1XP04lQJqaTDFHApXB64ipCz5xUG3uOyfT0gA+QEEVcys+TIx
xHWVBqB/0Y0n3bOppHKH/lmLmnp0Ft0WpWIp6zqW3IunaFnT63eROfjXy9mPX1on
AX1daBli2MjN9LdyR75bl87yraKZk62Uy5P2EgmVtqvXO9A/EcswFi55gORngS1d
7XB4tmBZrOFdRWOPyN9yaFvqHbgB8X7754qz41SgOAngPN5C8sLtLpvzHzW2Ntjj
gKGLzZlkD8Kqq7HK9W+eQ42EVJmzbsASZthwEPEGNTNDqJwuuhQxzhB/HIbjj9LV
+Hfsm6vxL2PZQl/gZ4FkkfGXL/xuJvYz+NO1+MRiqzFRJQJ6+N1rZdVtTTDIZbpo
FGWsJwt0ivKH
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICVTCCAdygAwIBAgIUTyNkuI6XY57GU4HBdk7LKnQV1tcwCgYIKoZIzj0EAwMw
WjELMAkGA1UEBhMCQ04xJTAjBgNVBAoMHFRydXN0QXNpYSBUZWNobm9sb2dpZXMs
IEluYy4xJDAiBgNVBAMMG1RydXN0QXNpYSBHbG9iYWwgUm9vdCBDQSBHNDAeFw0y
MTA1MjAwMjEwMjJaFw00NjA1MTkwMjEwMjJaMFoxCzAJBgNVBAYTAkNOMSUwIwYD
VQQKDBxUcnVzdEFzaWEgVGVjaG5vbG9naWVzLCBJbmMuMSQwIgYDVQQDDBtUcnVz
dEFzaWEgR2xvYmFsIFJvb3QgQ0EgRzQwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAATx
s8045CVD5d4ZCbuBeaIVXxVjAd7Cq92zphtnS4CDr5nLrBfbK5bKfFJV4hrhPVbw
LxYI+hW8m7tH5j/uqOFMjPXTNvk4XatwmkcN4oFBButJ+bAp3TPsUKV/eSm4IJij
YzBhMA8GA1UdEwEB/wQFMAMBAf8wHwYDVR0jBBgwFoAUpbtKl86zK3+kMd6Xg1mD
pm9xy94wHQYDVR0OBBYEFKW7SpfOsyt/pDHel4NZg6ZvccveMA4GA1UdDwEB/wQE
AwIBBjAKBggqhkjOPQQDAwNnADBkAjBe8usGzEkxn0AAbbd+NvBNEU/zy4k6LHiR
UKNbwMp1JvK/kF0LgoxgKJ/GcJpo5PECMFxYDlZ2z1jD1xCMuo6u47xkdUfFVZDj
/bpV6wfEU6s3qe4hsiFbYI89MvHVI5TWWA==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICHTCCAaOgAwIBAgIUQ3CCd89NXTTxyq4yLzf39H91oJ4wCgYIKoZIzj0EAwMw
TjELMAkGA1UEBhMCVVMxEjAQBgNVBAoMCUNvbW1TY29wZTErMCkGA1UEAwwiQ29t
bVNjb3BlIFB1YmxpYyBUcnVzdCBFQ0MgUm9vdC0wMTAeFw0yMTA0MjgxNzM1NDNa
Fw00NjA0MjgxNzM1NDJaME4xCzAJBgNVBAYTAlVTMRIwEAYDVQQKDAlDb21tU2Nv
cGUxKzApBgNVBAMMIkNvbW1TY29wZSBQdWJsaWMgVHJ1c3QgRUNDIFJvb3QtMDEw
djAQBgcqhkjOPQIBBgUrgQQAIgNiAARLNumuV16ocNfQj3Rid8NeeqrltqLxeP0C
flfdkXmcbLlSiFS8LwS+uM32ENEp7LXQoMPwiXAZu1FlxUOcw5tjnSCDPgYLpkJE
hRGnSjot6dZoL0hOUysHP029uax3OVejQjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYD
VR0PAQH/BAQDAgEGMB0GA1UdDgQWBBSOB2LAUN3GGQYARnQE9/OufXVNMDAKBggq
hkjOPQQDAwNoADBlAjEAnDPfQeMjqEI2Jpc1XHvr20v4qotzVRVcrHgpD7oh2MSg
2NED3W3ROT3Ek2DS43KyAjB8xX6I01D1HiXo+k515liWpDVfG2XqYZpwI7UNo5uS
Um9poIyNStDuiw7LR47QjRE=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICHDCCAaOgAwIBAgIUKP2ZYEFHpgE6yhR7H+/5aAiDXX0wCgYIKoZIzj0EAwMw
TjELMAkGA1UEBhMCVVMxEjAQBgNVBAoMCUNvbW1TY29wZTErMCkGA1UEAwwiQ29t
bVNjb3BlIFB1YmxpYyBUcnVzdCBFQ0MgUm9vdC0wMjAeFw0yMTA0MjgxNzQ0NTRa
Fw00NjA0MjgxNzQ0NTNaME4xCzAJBgNVBAYTAlVTMRIwEAYDVQQKDAlDb21tU2Nv
cGUxKzApBgNVBAMMIkNvbW1TY29wZSBQdWJsaWMgVHJ1c3QgRUNDIFJvb3QtMDIw
djAQBgcqhkjOPQIBBgUrgQQAIgNiAAR4MIHoYx7l63FRD/cHB8o5mXxO1Q/MMDAL
j2aTPs+9xYa9+bG3tD60B8jzljHz7aRP+KNOjSkVWLjVb3/ubCK1sK9IRQq9qEmU
v4RDsNuESgMjGWdqb8FuvAY5N9GIIvejQjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYD
VR0PAQH/BAQDAgEGMB0GA1UdDgQWBBTmGHX/72DehKT1RsfeSlXjMjZ59TAKBggq
hkjOPQQDAwNnADBkAjAmc0l6tqvmSfR9Uj/UQQSugEODZXW5hYA4O9Zv5JOGq4/n
ich/m35rChJVYaoR4HkCMHfoMXGsPHED1oQmHhS48zs73u1Z/GtMMH9ZzkXpc2AV
mkzw5l4lIhVtwodZ0LKOag==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFbDCCA1SgAwIBAgIUPgNJgXUWdDGOTKvVxZAplsU5EN0wDQYJKoZIhvcNAQEL
BQAwTjELMAkGA1UEBhMCVVMxEjAQBgNVBAoMCUNvbW1TY29wZTErMCkGA1UEAwwi
Q29tbVNjb3BlIFB1YmxpYyBUcnVzdCBSU0EgUm9vdC0wMTAeFw0yMTA0MjgxNjQ1
NTRaFw00NjA0MjgxNjQ1NTNaME4xCzAJBgNVBAYTAlVTMRIwEAYDVQQKDAlDb21t
U2NvcGUxKzApBgNVBAMMIkNvbW1TY29wZSBQdWJsaWMgVHJ1c3QgUlNBIFJvb3Qt
MDEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCwSGWjDR1C45FtnYSk
YZYSwu3D2iM0GXb26v1VWvZVAVMP8syMl0+5UMuzAURWlv2bKOx7dAvnQmtVzslh
suitQDy6uUEKBU8bJoWPQ7VAtYXR1HHcg0Hz9kXHgKKEUJdGzqAMxGBWBB0HW0al
DrJLpA6lfO741GIDuZNqihS4cPgugkY4Iw50x2tBt9Apo52AsH53k2NC+zSDO3Oj
WiE260f6GBfZumbCk6SP/F2krfxQapWsvCQz0b2If4b19bJzKo98rwjyGpg/qYFl
P8GMicWWMJoKz/TUyDTtnS+8jTiGU+6Xn6myY5QXjQ/cZip8UlF1y5mO6D1cv547
KI2DAg+pn3LiLCuz3GaXAEDQpFSOm117RTYm1nJD68/A6g3czhLmfTifBSeolz7p
UcZsBSjBAg/pGG3svZwG1KdJ9FQFa2ww8esD1eo9anbCyxooSU1/ZOD6K9pzg4H/
kQO9lLvkuI6cMmPNn7togbGEW682v3fuHX/3SZtS7NJ3Wn2RnU3COS3kuoL4b/JO
Hg9O5j9ZpSPcPYeoKFgo0fEbNttPxP/hjFtyjMcmAyejOQoBqsCyMWCDIqFPEgkB
Ea801M/XrmLTBQe0MXXgDW1XT2mH+VepuhX2yFJtocucH+X8eKg1mp9BFM6ltM6U
CBwJrVbl2rZJmkrqYxhTnCwuwwIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4G
A1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUN12mmnQywsL5x6YVEFm45P3luG0wDQYJ
KoZIhvcNAQELBQADggIBAK+nz97/4L1CjU3lIpbfaOp9TSp90K09FlxD533Ahuh6
NWPxzIHIxgvoLlI1pKZJkGNRrDSsBTtXAOnTYtPZKdVUvhwQkZyybf5Z/Xn36lbQ
nmhUQo8mUuJM3y+Xpi/SB5io82BdS5pYV4jvguX6r2yBS5KPQJqTRlnLX3gWsWc+
QgvfKNmwrZggvkN80V4aCRckjXtdlemrwWCrWxhkgPut4AZ9HcpZuPN4KWfGVh2v
trV0KnahP/t1MJ+UXjulYPPLXAziDslg+MkfFoom3ecnf+slpoq9uC02EJqxWE2a
aE9gVOX2RhOOiKy8IUISrcZKiX2bwdgt6ZYD9KJ0DLwAHb/WNyVntHKLr4W96ioD
j8z7PEQkguIBpQtZtjSNMgsSDesnwv1B10A8ckYpwIzqug/xBpMu95yo9GA+o/E4
Xo4TwbM6l4c/ksp4qRyv0LAbJh6+cOx69TOY6lz/KwsETkPdY34Op054A5U+1C0w
lREQKC6/oAI+/15Z0wUOlV9TRe9rh9VIzRamloPh37MG88EU26fsHItdkJANclHn
YfkUyq+Dj7+vsQpZXdxc1+SWrVtgHdqul7I52Qb1dgAT+GhMIbA1xNxVssnBQVoc
icCMb3SgazNNtQEo/a2tiRc7ppqEvOuM6sRxJKi6KfkIsidWNTJf6jn7MZrVGczw
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFbDCCA1SgAwIBAgIUVBa/O345lXGN0aoApYYNK496BU4wDQYJKoZIhvcNAQEL
BQAwTjELMAkGA1UEBhMCVVMxEjAQBgNVBAoMCUNvbW1TY29wZTErMCkGA1UEAwwi
Q29tbVNjb3BlIFB1YmxpYyBUcnVzdCBSU0EgUm9vdC0wMjAeFw0yMTA0MjgxNzE2
NDNaFw00NjA0MjgxNzE2NDJaME4xCzAJBgNVBAYTAlVTMRIwEAYDVQQKDAlDb21t
U2NvcGUxKzApBgNVBAMMIkNvbW1TY29wZSBQdWJsaWMgVHJ1c3QgUlNBIFJvb3Qt
MDIwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDh+g77aAASyE3VrCLE
NQE7xVTlWXZjpX/rwcRqmL0yjReA61260WI9JSMZNRTpf4mnG2I81lDnNJUDMrG0
kyI9p+Kx7eZ7Ti6Hmw0zdQreqjXnfuU2mKKuJZ6VszKWpCtYHu8//mI0SFHRtI1C
rWDaSWqVcN3SAOLMV2MCe5bdSZdbkk6V0/nLKR8YSvgBKtJjCW4k6YnS5cciTNxz
hkcAqg2Ijq6FfUrpuzNPDlJwnZXjfG2WWy09X6GDRl224yW4fKcZgBzqZUPckXk2
LHR88mcGyYnJ27/aaL8j7dxrrSiDeS/sOKUNNwFnJ5rpM9kzXzehxfCrPfp4sOcs
n/Y+n2Dg70jpkEUeBVF4GiwSLFworA2iI540jwXmojPOEXcT1A6kHkIfhs1w/tku
FT0du7jyU1fbzMZ0KZwYszZ1OC4PVKH4kh+Jlk+71O6d6Ts2QrUKOyrUZHk2EOH5
kQMreyBUzQ0ZGshBMjTRsJnhkB4BQDa1t/qp5Xd1pCKBXbCL5CcSD1SIxtuFdOa3
wNemKfrb3vOTlycEVS8KbzfFPROvCgCpLIscgSjX74Yxqa7ybrjKaixUR9gqiC6v
wQcQeKwRoi9C8DfF8rhW3Q5iLc4tVn5V8qdE9isy9COoR+jUKgF4z2rDN6ieZdIs
5fq6M8EGRPbmz6UNp2YINIos8wIDAQABo0IwQDAPBgNVHRMBAf8EBTADAQH/MA4G
A1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUR9DnsSL/nSz12Vdgs7GxcJXvYXowDQYJ
KoZIhvcNAQELBQADggIBAIZpsU0v6Z9PIpNojuQhmaPORVMbc0RTAIFhzTHjCLqB
KCh6krm2qMhDnscTJk3C2OVVnJJdUNjCK9v+5qiXz1I6JMNlZFxHMaNlNRPDk7n3
+VGXu6TwYofF1gbTl4MgqX67tiHCpQ2EAOHyJxCDut0DgdXdaMNmEMjRdrSzbyme
APnCKfWxkxlSaRosTKCL4BWaMS/TiJVZbuXEs1DIFAhKm4sTg7GkcrI7djNB3Nyq
pgdvHSQSn8h2vS/ZjvQs7rfSOBAkNlEv41xdgSGn2rtO/+YHqP65DSdsu3BaVXoT
6fEqSWnHX4dXTEN5bTpl6TBcQe7rd6VzEojov32u5cSoHw2OHG1QAk8mGEPej1WF
sQs3BWDJVTkSBKEqz3EWnzZRSb9wO55nnPt7eck5HHisd5FUmrh1CoFSl+NmYWvt
PjgelmFV4ZFUjO2MJB+ByRCac5krFk5yAD9UG/iNuovnFNa2RU9g7Jauwy8CTl2d
lklyALKrdVwPaFsdZcJfMw8eD/A7hvWwTruc9+olBdytoptLFwG+Qt81IR2tq670
v64fG9PiO/yzcnMcmyiQiRM9HcEARwmWmjgb3bHPDcK0RPOWlc4yOo80nOAXx17O
rg3bhzjlP1v9mxnhMUF6cKojawHhRUzNlM47ni3niAIi9G7oyOzWPPO5std3eqx7
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICQjCCAcmgAwIBAgIQNjqWjMlcsljN0AFdxeVXADAKBggqhkjOPQQDAzBjMQsw
CQYDVQQGEwJERTEnMCUGA1UECgweRGV1dHNjaGUgVGVsZWtvbSBTZWN1cml0eSBH
bWJIMSswKQYDVQQDDCJUZWxla29tIFNlY3VyaXR5IFRMUyBFQ0MgUm9vdCAyMDIw
MB4XDTIwMDgyNTA3NDgyMFoXDTQ1MDgyNTIzNTk1OVowYzELMAkGA1UEBhMCREUx
JzAlBgNVBAoMHkRldXRzY2hlIFRlbGVrb20gU2VjdXJpdHkgR21iSDErMCkGA1UE
AwwiVGVsZWtvbSBTZWN1cml0eSBUTFMgRUNDIFJvb3QgMjAyMDB2MBAGByqGSM49
AgEGBSuBBAAiA2IABM6//leov9Wq9xCazbzREaK9Z0LMkOsVGJDZos0MKiXrPk/O
tdKPD/M12kOLAoC+b1EkHQ9rK8qfwm9QMuU3ILYg/4gND21Ju9sGpIeQkpT0CdDP
f8iAC8GXs7s1J8nCG6NCMEAwHQYDVR0OBBYEFONyzG6VmUex5rNhTNHLq+O6zd6f
MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMDA2cA
MGQCMHVSi7ekEE+uShCLsoRbQuHmKjYC2qBuGT8lv9pZMo7k+5Dck2TOrbRBR2Di
z6fLHgIwN0GMZt9Ba9aDAEH9L1r3ULRn0SyocddDypwnJJGDSA3PzfdUga/sf+Rn
27iQ7t0l
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFszCCA5ugAwIBAgIQIZxULej27HF3+k7ow3BXlzANBgkqhkiG9w0BAQwFADBj
MQswCQYDVQQGEwJERTEnMCUGA1UECgweRGV1dHNjaGUgVGVsZWtvbSBTZWN1cml0
eSBHbWJIMSswKQYDVQQDDCJUZWxla29tIFNlY3VyaXR5IFRMUyBSU0EgUm9vdCAy
MDIzMB4XDTIzMDMyODEyMTY0NVoXDTQ4MDMyNzIzNTk1OVowYzELMAkGA1UEBhMC
REUxJzAlBgNVBAoMHkRldXRzY2hlIFRlbGVrb20gU2VjdXJpdHkgR21iSDErMCkG
A1UEAwwiVGVsZWtvbSBTZWN1cml0eSBUTFMgUlNBIFJvb3QgMjAyMzCCAiIwDQYJ
KoZIhvcNAQEBBQADggIPADCCAgoCggIBAO01oYGA88tKaVvC+1GDrib94W7zgRJ9
cUD/h3VCKSHtgVIs3xLBGYSJwb3FKNXVS2xE1kzbB5ZKVXrKNoIENqil/Cf2SfHV
cp6R+SPWcHu79ZvB7JPPGeplfohwoHP89v+1VmLhc2o0mD6CuKyVU/QBoCcHcqMA
U6DksquDOFczJZSfvkgdmOGjup5czQRxUX11eKvzWarE4GC+j4NSuHUaQTXtvPM6
Y+mpFEXX5lLRbtLevOP1Czvm4MS9Q2QTps70mDdsipWol8hHD/BeEIvnHRz+sTug
BTNoBUGCwQMrAcjnj02r6LX2zWtEtefdi+zqJbQAIldNsLGyMcEWzv/9FIS3R/qy
8XDe24tsNlikfLMR0cN3f1+2JeANxdKz+bi4d9s3cXFH42AYTyS2dTd4uaNir73J
co4vzLuu2+QVUhkHM/tqty1LkCiCc/4YizWN26cEar7qwU02OxY2kTLvtkCJkUPg
8qKrBC7m8kwOFjQgrIfBLX7JZkcXFBGk8/ehJImr2BrIoVyxo/eMbcgByU/J7MT8
rFEz0ciD0cmfHdRHNCk+y7AO+oMLKFjlKdw/fKifybYKu6boRhYPluV75Gp6SG12
mAWl3G0eQh5C2hrgUve1g8Aae3g1LDj1H/1Joy7SWWO/gLCMk3PLNaaZlSJhZQNg
+y+TS/qanIA7AgMBAAGjYzBhMA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUtqeX
gj10hZv3PJ+TmpV5dVKMbUcwDwYDVR0TAQH/BAUwAwEB/zAfBgNVHSMEGDAWgBS2
p5eCPXSFm/c8n5OalXl1UoxtRzANBgkqhkiG9w0BAQwFAAOCAgEAqMxhpr51nhVQ
pGv7qHBFfLp+sVr8WyP6Cnf4mHGCDG3gXkaqk/QeoMPhk9tLrbKmXauw1GLLXrtm
9S3ul0A8Yute1hTWjOKWi0FpkzXmuZlrYrShF2Y0pmtjxrlO8iLpWA1WQdH6DErw
M807u20hOq6OcrXDSvvpfeWxm4bu4uB9tPcy/SKE8YXJN3nptT+/XOR0so8RYgDd
GGah2XsjX/GO1WfoVNpbOms2b/mBsTNHM3dA+VKq3dSDz4V4mZqTuXNnQkYRIer+
CqkbGmVps4+uFrb2S1ayLfmlyOw7YqPta9BO1UAJpB+Y1zqlklkg5LB9zVtzaL1t
xKITDmcZuI1CfmwMmm6gJC3VRRvcxAIU/oVbZZfKTpBQCHpCNfnqwmbU+AGuHrS+
w6jv/naaoqYfRvaE7fzbzsQCzndILIyy7MMAo+wsVRjBfhnu4S/yrYObnqsZ38aK
L4x35bcF7DvB7L6Gs4a8wPfc5+pbrrLMtTWGS9DiP7bY+A4A7l3j941Y/8+LN+lj
X273CXE2whJdV/LItM3z7gLfEdxquVeEHVlNjM7IDiPCtyaaEBRx/pOyiriA8A4Q
ntOoUAw3gi/q4Iqd4Sw5/7W0cwDk90imc6y/st53BIe0o82bNSQ3+pCTE4FCxpgm
dTdmQRCsu/WU48IxK63nI1bMNSWSs1A=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICejCCAgCgAwIBAgIQMZch7a+JQn81QYehZ1ZMbTAKBggqhkjOPQQDAzBuMQsw
CQYDVQQGEwJFUzEcMBoGA1UECgwTRmlybWFwcm9mZXNpb25hbCBTQTEYMBYGA1UE
YQwPVkFURVMtQTYyNjM0MDY4MScwJQYDVQQDDB5GSVJNQVBST0ZFU0lPTkFMIENB
IFJPT1QtQSBXRUIwHhcNMjIwNDA2MDkwMTM2WhcNNDcwMzMxMDkwMTM2WjBuMQsw
CQYDVQQGEwJFUzEcMBoGA1UECgwTRmlybWFwcm9mZXNpb25hbCBTQTEYMBYGA1UE
YQwPVkFURVMtQTYyNjM0MDY4MScwJQYDVQQDDB5GSVJNQVBST0ZFU0lPTkFMIENB
IFJPT1QtQSBXRUIwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAARHU+osEaR3xyrq89Zf
e9MEkVz6iMYiuYMQYneEMy3pA4jU4DP37XcsSmDq5G+tbbT4TIqk5B/K6k84Si6C
cyvHZpsKjECcfIr28jlgst7L7Ljkb+qbXbdTkBgyVcUgt5SjYzBhMA8GA1UdEwEB
/wQFMAMBAf8wHwYDVR0jBBgwFoAUk+FDY1w8ndYn81LsF7Kpryz3dvgwHQYDVR0O
BBYEFJPhQ2NcPJ3WJ/NS7Beyqa8s93b4MA4GA1UdDwEB/wQEAwIBBjAKBggqhkjO
PQQDAwNoADBlAjAdfKR7w4l1M+E7qUW/Runpod3JIha3RxEL2Jq68cgLcFBTApFw
hVmpHqTm6iMxoAACMQD94vizrxa5HnPEluPBMBnYfubDl94cT7iJLzPrSA8Z94dG
XSaQpYXFuXqUPoeovQA=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFjTCCA3WgAwIBAgIQQAE0jMIAAAAAAAAAATzyxjANBgkqhkiG9w0BAQwFADBQ
MQswCQYDVQQGEwJUVzESMBAGA1UEChMJVEFJV0FOLUNBMRAwDgYDVQQLEwdSb290
IENBMRswGQYDVQQDExJUV0NBIENZQkVSIFJvb3QgQ0EwHhcNMjIxMTIyMDY1NDI5
WhcNNDcxMTIyMTU1OTU5WjBQMQswCQYDVQQGEwJUVzESMBAGA1UEChMJVEFJV0FO
LUNBMRAwDgYDVQQLEwdSb290IENBMRswGQYDVQQDExJUV0NBIENZQkVSIFJvb3Qg
Q0EwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDG+Moe2Qkgfh1sTs6P
40czRJzHyWmqOlt47nDSkvgEs1JSHWdyKKHfi12VCv7qze33Kc7wb3+szT3vsxxF
avcokPFhV8UMxKNQXd7UtcsZyoC5dc4pztKFIuwCY8xEMCDa6pFbVuYdHNWdZsc/
34bKS1PE2Y2yHer43CdTo0fhYcx9tbD47nORxc5zb87uEB8aBs/pJ2DFTxnk684i
JkXXYJndzk834H/nY62wuFm40AZoNWDTNq5xQwTxaWV4fPMf88oon1oglWa0zbfu
j3ikRRjpJi+NmykosaS3Om251Bw4ckVYsV7r8Cibt4LK/c/WMw+f+5eesRycnupf
Xtuq3VTpMCEobY5583WSjCb+3MX2w7DfRFlDo7YDKPYIMKoNM+HvnKkHIuNZW0CP
2oi3aQiotyMuRAlZN1vH4xfyIutuOVLF3lSnmMlLIJXcRolftBL5hSmO68gnFSDA
S9TMfAxsNAwmmyYxpjyn9tnQS6Jk/zuZQXLB4HCX8SS7K8R0IrGsayIyJNN4KsDA
oS/xUgXJP+92ZuJF2A09rZXIx4kmyA+upwMu+8Ff+iDhcK2wZSA3M2Cw1a/XDBzC
kHDXShi8fgGwsOsVHkQGzaRP6AzRwyAQ4VRlnrZR0Bp2a0JaWHY06rc3Ga4udfmW
5cFZ95RXKSWNOkyrTZpB0F8mAwIDAQABo2MwYTAOBgNVHQ8BAf8EBAMCAQYwDwYD
VR0TAQH/BAUwAwEB/zAfBgNVHSMEGDAWgBSdhWEUfMFib5do5E83QOGt4A1WNzAd
BgNVHQ4EFgQUnYVhFHzBYm+XaORPN0DhreANVjcwDQYJKoZIhvcNAQEMBQADggIB
AGSPesRiDrWIzLjHhg6hShbNcAu3p4ULs3a2D6f/CIsLJc+o1IN1KriWiLb73y0t
tGlTITVX1olNc79pj3CjYcya2x6a4CD4bLubIp1dhDGaLIrdaqHXKGnK/nZVekZn
68xDiBaiA9a5F/gZbG0jAn/xX9AKKSM70aoK7akXJlQKTcKlTfjF/biBzysseKNn
TKkHmvPfXvt89YnNdJdhEGoHK4Fa0o635yDRIG4kqIQnoVesqlVYL9zZyvpoBJ7t
RCT5dEA7IzOrg1oYJkK2bVS1FmAwbLGg+LhBoF1JSdJlBTrq/p1hvIbZv97Tujqx
f36SNI7JAG7cmL3c7IAFrQI932XtCwP39xaEBDG6k5TY8hL4iuO/Qq+n1M0RFxbI
Qh0UqEL20kCGoE8jypZFVmAGzbdVAaYBlGX+bgUJurSkquLvWL69J1bY73NxW0Qz
8ppy6rBePm6pUlvscG21h483XjyMnM7k8M4MZ0HMzvaAq07MTFb1wWFZk7Q+ptq4
NxKfKjLji7gh7MMrZQzvIt6IKTtM1/r+t+FHvpw+PoP7UV31aPcuIYXcv/Fa4nzX
xeSDwWrruoBa3lwtcHb4yOWHh8qgnaHlIhInD0Q9HWzq1MKLL295q39QpsQZp6F6
t5b5wR9iWqJDB0BeJsas7a5wFsWqynKKTbDPAYsDP27X
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDcjCCAlqgAwIBAgIUZvnHwa/swlG07VOX5uaCwysckBYwDQYJKoZIhvcNAQEL
BQAwUTELMAkGA1UEBhMCSlAxIzAhBgNVBAoTGkN5YmVydHJ1c3QgSmFwYW4gQ28u
LCBMdGQuMR0wGwYDVQQDExRTZWN1cmVTaWduIFJvb3QgQ0ExMjAeFw0yMDA0MDgw
NTM2NDZaFw00MDA0MDgwNTM2NDZaMFExCzAJBgNVBAYTAkpQMSMwIQYDVQQKExpD
eWJlcnRydXN0IEphcGFuIENvLiwgTHRkLjEdMBsGA1UEAxMUU2VjdXJlU2lnbiBS
b290IENBMTIwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQC6OcE3emhF
KxS06+QT61d1I02PJC0W6K6OyX2kVzsqdiUzg2zqMoqUm048luT9Ub+ZyZN+v/mt
p7JIKwccJ/VMvHASd6SFVLX9kHrko+RRWAPNEHl57muTH2SOa2SroxPjcf59q5zd
J1M3s6oYwlkm7Fsf0uZlfO+TvdhYXAvA42VvPMfKWeP+bl+sg779XSVOKik71gur
FzJ4pOE+lEa+Ym6b3kaosRbnhW70CEBFEaCeVESE99g2zvVQR9wsMJvuwPWW0v4J
hscGWa5Pro4RmHvzC1KqYiaqId+OJTN5lxZJjfU+1UefNzFJM3IFTQy2VYzxV4+K
h9GtxRESOaCtAgMBAAGjQjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQD
AgEGMB0GA1UdDgQWBBRXNPN0zwRL1SXm8UC2LEzZLemgrTANBgkqhkiG9w0BAQsF
AAOCAQEAPrvbFxbS8hQBICw4g0utvsqFepq2m2um4fylOqyttCg6r9cBg0krY6Ld
mmQOmFxv3Y67ilQiLUoT865AQ9tPkbeGGuwAtEGBpE/6aouIs3YIcipJQMPTw4WJ
mBClnW8Zt7vPemVV2zfrPIpyMpcemik+rY3moxtt9XUa5rBouVui7mlHJzWhhpmA
8zNL4WukJsPvdFlseqJkth5Ew1DgDzk9qTPxpfPSvWKErI4cqc1avTc7bgoitPQV
55FYxTpE05Uo2cBl6XLK0A+9H7MV2anjpEcJnuDLN/v9vZfVvhgaaaI5gdka9at/
yOPiZwud9AzqVN/Ssq+xIvEg37xEHA==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFcjCCA1qgAwIBAgIUZNtaDCBO6Ncpd8hQJ6JaJ90t8sswDQYJKoZIhvcNAQEM
BQAwUTELMAkGA1UEBhMCSlAxIzAhBgNVBAoTGkN5YmVydHJ1c3QgSmFwYW4gQ28u
LCBMdGQuMR0wGwYDVQQDExRTZWN1cmVTaWduIFJvb3QgQ0ExNDAeFw0yMDA0MDgw
NzA2MTlaFw00NTA0MDgwNzA2MTlaMFExCzAJBgNVBAYTAkpQMSMwIQYDVQQKExpD
eWJlcnRydXN0IEphcGFuIENvLiwgTHRkLjEdMBsGA1UEAxMUU2VjdXJlU2lnbiBS
b290IENBMTQwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDF0nqh1oq/
FjHQmNE6lPxauG4iwWL3pwon71D2LrGeaBLwbCRjOfHw3xDG3rdSINVSW0KZnvOg
vlIfX8xnbacuUKLBl422+JX1sLrcneC+y9/3OPJH9aaakpUqYllQC6KxNedlsmGy
6pJxaeQp8E+BgQQ8sqVb1MWoWWd7VRxJq3qdwudzTe/NCcLEVxLbAQ4jeQkHO6Lo
/IrPj8BGJJw4J+CDnRugv3gVEOuGTgpa/d/aLIJ+7sr2KeH6caH3iGicnPCNvg9J
kdjqOvn90Ghx2+m1K06Ckm9mH+Dw3EzsytHqunQG+bOEkJTRX45zGRBdAuVwpcAQ
0BB8b8VYSbSwbprafZX1zNoCr7gsfXmPvkPx+SgojQlD+Ajda8iLLCSxjVIHvXib
y8posqTdDEx5YMaZ0ZPxMBoH064iwurO8YQJzOAUbn8/ftKChazcqRZOhaBgy/ac
18izju3Gm5h1DVXoX+WViwKkrkMpKBGk5hIwAUt1ax5mnXkvpXYvHUC0bcl9eQjs
0Wq2XSqypWa9a4X0dFbD9ed1Uigspf9mR6XU/v6eVL9lfgHWMI+lNpyiUBzuOIAB
SMbHdPTGrMNASRZhdCyvjG817XsYAFs2PJxQDcqSMxDxJklt33UkN4Ii1+iW/RVL
ApY+B3KVfqs9TC7XyvDf4Fg/LS8EmjijAQIDAQABo0IwQDAPBgNVHRMBAf8EBTAD
AQH/MA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUBpOjCl4oaTeqYR3r6/wtbyPk
86AwDQYJKoZIhvcNAQEMBQADggIBAJaAcgkGfpzMkwQWu6A6jZJOtxEaCnFxEM0E
rX+lRVAQZk5KQaID2RFPeje5S+LGjzJmdSX7684/AykmjbgWHfYfM25I5uj4V7Ib
ed87hwriZLoAymzvftAj63iP/2SbNDefNWWipAA9EiOWWF3KY4fGoweITedpdopT
zfFP7ELyk+OZpDc8h7hi2/DsHzc/N19DzFGdtfCXwreFamgLRB7lUe6TzktuhsHS
DCRZNhqfLJGP4xjblJUK7ZGqDpncllPjYYPGFrojutzdfhrGe0K22VoF3Jpf1d+4
2kd92jjbrDnVHmtsKheMYc2xbXIBw8MgAGJoFjHVdqqGuw6qnsb58Nn4DSEC5MUo
FlkRudlpcyqSeLiSV5sI8jrlL5WwWLdrIBRtFO8KvH7YVdiI2i/6GaX7i+B/OfVy
K4XELKzvGUWSTLNhB9xNH27SgRNcmvMSZ4PPmz+Ln52kuaiWA3rF7iDeM9ovnhp6
dB7h7sxaOgTdsxoEqBRjrLdHEoOabPXm6RUVkRqEGQ6UROcSjiVbgGcZ3GOTEAtl
Lor6CZpO2oYofaphNdgOpygau1LgePhsumywbrmHXumZNTfxPWQrqaA0k89jL9WB
365jJ6UeTo3cKXhZ+PmhIIynJkBugnLNeLLIjzwec+fBH7/PzqUqm9tEZDKgu39c
JRNItX+S
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICIzCCAamgAwIBAgIUFhXHw9hJp75pDIqI7fBw+d23PocwCgYIKoZIzj0EAwMw
UTELMAkGA1UEBhMCSlAxIzAhBgNVBAoTGkN5YmVydHJ1c3QgSmFwYW4gQ28uLCBM
dGQuMR0wGwYDVQQDExRTZWN1cmVTaWduIFJvb3QgQ0ExNTAeFw0yMDA0MDgwODMy
NTZaFw00NTA0MDgwODMyNTZaMFExCzAJBgNVBAYTAkpQMSMwIQYDVQQKExpDeWJl
cnRydXN0IEphcGFuIENvLiwgTHRkLjEdMBsGA1UEAxMUU2VjdXJlU2lnbiBSb290
IENBMTUwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAAQLUHSNZDKZmbPSYAi4Io5GdCx4
wCtELW1fHcmuS1Iggz24FG1Th2CeX2yF2wYUleDHKP+dX+Sq8bOLbe1PL0vJSpSR
ZHX+AezB2Ot6lHhWGENfa4HL9rzatAy2KZMIaY+jQjBAMA8GA1UdEwEB/wQFMAMB
Af8wDgYDVR0PAQH/BAQDAgEGMB0GA1UdDgQWBBTrQciu/NWeUUj1vYv0hyCTQSvT
9DAKBggqhkjOPQQDAwNoADBlAjEA2S6Jfl5OpBEHvVnCB96rMjhTKkZEBhd6zlHp
4P9mLQlO4E/0BdGF9jVg3PVys0Z9AjBEmEYagoUeYWmJSwdLZrWeqrqgHkHZAXQ6
bkU6iYAZezKYVWOr62Nuk22rGwlgMU4=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFqTCCA5GgAwIBAgIQczswBEhb2U14LnNLyaHcZjANBgkqhkiG9w0BAQ0FADBI
MQswCQYDVQQGEwJERTEVMBMGA1UEChMMRC1UcnVzdCBHbWJIMSIwIAYDVQQDExlE
LVRSVVNUIEJSIFJvb3QgQ0EgMiAyMDIzMB4XDTIzMDUwOTA4NTYzMVoXDTM4MDUw
OTA4NTYzMFowSDELMAkGA1UEBhMCREUxFTATBgNVBAoTDEQtVHJ1c3QgR21iSDEi
MCAGA1UEAxMZRC1UUlVTVCBCUiBSb290IENBIDIgMjAyMzCCAiIwDQYJKoZIhvcN
AQEBBQADggIPADCCAgoCggIBAK7/CVmRgApKaOYkP7in5Mg6CjoWzckjYaCTcfKr
i3OPoGdlYNJUa2NRb0kz4HIHE304zQaSBylSa053bATTlfrdTIzZXcFhfUvnKLNE
gXtRr90zsWh81k5M/itoucpmacTsXld/9w3HnDY25QdgrMBM6ghs7wZ8T1soegj8
k12b9py0i4a6Ibn08OhZWiihNIQaJZG2tY/vsvmA+vk9PBFy2OMvhnbFeSzBqZCT
Rphny4NqoFAjpzv2gTng7fC5v2Xx2Mt6++9zA84A9H3X4F07ZrjcjrqDy4d2A/wl
2ecjbwb9Z/Pg/4S8R7+1FhhGaRTMBffb00msa8yr5LULQyReS2tNZ9/WtT5PeB+U
cSTq3nD88ZP+npNa5JRal1QMNXtfbO4AHyTsA7oC9Xb0n9Sa7YUsOCIvx9gvdhFP
/Wxc6PWOJ4d/GUohR5AdeY0cW/jPSoXk7bNbjb7EZChdQcRurDhaTyN0dKkSw/bS
uREVMweR2Ds3OmMwBtHFIjYoYiMQ4EbMl6zWK11kJNXuHA7e+whadSr2Y23OC0K+
0bpwHJwh5Q8xaRfX/Aq03u2AnMuStIv13lmiWAmlY0cL4UEyNEHZmrHZqLAbWt4N
DfTisl01gLmB1IRpkQLLddCNxbU9CZEJjxShFHR5PtbJFR2kWVki3PaKRT08EtY+
XTIvAgMBAAGjgY4wgYswDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUZ5Dw1t61
GNVGKX5cq/ieCLxklRAwDgYDVR0PAQH/BAQDAgEGMEkGA1UdHwRCMEAwPqA8oDqG
OGh0dHA6Ly9jcmwuZC10cnVzdC5uZXQvY3JsL2QtdHJ1c3RfYnJfcm9vdF9jYV8y
XzIwMjMuY3JsMA0GCSqGSIb3DQEBDQUAA4ICAQA097N3U9swFrktpSHxQCF16+tI
FoE9c+CeJyrrd6kTpGoKWloUMz1oH4Guaf2Mn2VsNELZLdB/eBaxOqwjMa1ef67n
riv6uvw8l5VAk1/DLQOj7aRvU9f6QA4w9QAgLABMjDu0ox+2v5Eyq6+SmNMW5tTR
VFxDWy6u71cqqLRvpO8NVhTaIasgdp4D/Ca4nj8+AybmTNudX0KEPUUDAxxZiMrc
LmEkWqTqJwtzEr5SswrPMhfiHocaFpVIbVrg0M8JkiZmkdijYQ6qgYF/6FKC0ULn
4B0Y+qSFNueG4A3rvNTJ1jxD8V1Jbn6Bm2m1iWKPiFLY1/4nwSPFyysCu7Ff/vtD
hQNGvl3GyiEm/9cCnnRK3PgTFbGBVzbLZVzRHTF36SXDw7IyN9XxmAnkbWOACKsG
koHU6XCPpz+y7YaMgmo1yEJagtFSGkUPFaUA8JR7ZSdXOUPPfH/mvTWze/EZTN46
ls/pdu4D58JDUjxqgejBWoC9EV2Ta/vH5mQ/u2kc6d0li690yVRAysuTEwrt+2aS
Ecr1wPrYg1UDfNPFIkZ1cGt5SAYqgpq/5usWDiJFAbzdNpQ0qTUmiteXue4Icr80
knCDgKs4qllo3UCkGJCy89UDyibK79XH4I9TjvAA46jtn/mtd+ArY0+ew+43u3gJ
hJ65bvspmZDogNOfJA==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICMTCCAbegAwIBAgIUNnThTXxlE8msg1UloD5Sfi9QaMcwCgYIKoZIzj0EAwMw
WDELMAkGA1UEBhMCQ04xJTAjBgNVBAoTHFRydXN0QXNpYSBUZWNobm9sb2dpZXMs
IEluYy4xIjAgBgNVBAMTGVRydXN0QXNpYSBUTFMgRUNDIFJvb3QgQ0EwHhcNMjQw
NTE1MDU0MTU2WhcNNDQwNTE1MDU0MTU1WjBYMQswCQYDVQQGEwJDTjElMCMGA1UE
ChMcVHJ1c3RBc2lhIFRlY2hub2xvZ2llcywgSW5jLjEiMCAGA1UEAxMZVHJ1c3RB
c2lhIFRMUyBFQ0MgUm9vdCBDQTB2MBAGByqGSM49AgEGBSuBBAAiA2IABLh/pVs/
AT598IhtrimY4ZtcU5nb9wj/1WrgjstEpvDBjL1P1M7UiFPoXlfXTr4sP/MSpwDp
guMqWzJ8S5sUKZ74LYO1644xST0mYekdcouJtgq7nDM1D9rs3qlKH8kzsaNCMEAw
DwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQULIVTu7FDzTLqnqOH/qKYqKaT6RAw
DgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMDA2gAMGUCMFRH18MtYYZI9HlaVQ01
L18N9mdsd0AaRuf4aFtOJx24mH1/k78ITcTaRTChD15KeAIxAKORh/IRM4PDwYqR
OkwrULG9IpRdNYlzg8WbGf60oenUoWa2AaU2+dhoYSi3dOGiMQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFgDCCA2igAwIBAgIUHBjYz+VTPyI1RlNUJDxsR9FcSpwwDQYJKoZIhvcNAQEM
BQAwWDELMAkGA1UEBhMCQ04xJTAjBgNVBAoTHFRydXN0QXNpYSBUZWNobm9sb2dp
ZXMsIEluYy4xIjAgBgNVBAMTGVRydXN0QXNpYSBUTFMgUlNBIFJvb3QgQ0EwHhcN
MjQwNTE1MDU0MTU3WhcNNDQwNTE1MDU0MTU2WjBYMQswCQYDVQQGEwJDTjElMCMG
A1UEChMcVHJ1c3RBc2lhIFRlY2hub2xvZ2llcywgSW5jLjEiMCAGA1UEAxMZVHJ1
c3RBc2lhIFRMUyBSU0EgUm9vdCBDQTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCC
AgoCggIBAMMWuBtqpERz5dZO9LnPWwvB0ZqB9WOwj0PBuwhaGnrhB3YmH49pVr7+
NmDQDIPNlOrnxS1cLwUWAp4KqC/lYCZUlviYQB2srp10Zy9U+5RjmOMmSoPGlbYJ
Q1DNDX3eRA5gEk9bNb2/mThtfWza4mhzH/kxpRkQcwUqwzIZheo0qt1CHjCNP561
HmHVb70AcnKtEj+qpklz8oYVlQwQX1Fkzv93uMltrOXVmPGZLmzjyUT5tUMnCE32
ft5EebuyjBza00tsLtbDeLdM1aTk2tyKjg7/D8OmYCYozza/+lcK7Fs/6TAWe8Tb
xNRkoDD75f0dcZLdKY9BWN4ArTr9PXwaqLEX8E40eFgl1oUh63kd0Nyrz2I8sMeX
i9bQn9P+PN7F4/w6g3CEIR0JwqH8uyghZVNgepBtljhb//HXeltt08lwSUq6HTrQ
UNoyIBnkiz/r1RYmNzz7dZ6wB3C4FGB33PYPXFIKvF1tjVEK2sUYyJtt3LCDs3+j
TnhMmCWr8n4uIF6CFabW2I+s5c0yhsj55NqJ4js+k8UTav/H9xj8Z7XvGCxUq0DT
bE3txci3OE9kxJRMT6DNrqXGJyV1J23G2pyOsAWZ1SgRxSHUuPzHlqtKZFlhaxP8
S8ySpg+kUb8OWJDZgoM5pl+z+m6Ss80zDoWo8SnTq1mt1tve1CuBAgMBAAGjQjBA
MA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFLgHkXlcBvRG/XtZylomkadFK/hT
MA4GA1UdDwEB/wQEAwIBBjANBgkqhkiG9w0BAQwFAAOCAgEAIZtqBSBdGBanEqT3
Rz/NyjuujsCCztxIJXgXbODgcMTWltnZ9r96nBO7U5WS/8+S4PPFJzVXqDuiGev4
iqME3mmL5Dw8veWv0BIb5Ylrc5tvJQJLkIKvQMKtuppgJFqBTQUYo+IzeXoLH5Pt
7DlK9RME7I10nYEKqG/odv6LTytpEoYKNDbdgptvT+Bz3Ul/KD7JO6NXBNiT2Twp
2xIQaOHEibgGIOcberyxk2GaGUARtWqFVwHxtlotJnMnlvm5P1vQiJ3koP26TpUJ
g3933FEFlJ0gcXax7PqJtZwuhfG5WyRasQmr2soaB82G39tp27RIGAAtvKLEiUUj
pQ7hRGU+isFqMB3iYPg6qocJQrmBktwliJiJ8Xw18WLK7nn4GS/+X/jbh87qqA8M
pugLoDzga5SYnH+tBuYc6kIQX+ImFTw3OffXvO645e8D7r0i+yiGNFjEWn9hongP
XvPKnbwbPKfILfanIhHKA9jnZwqKDss1jjQ52MjqjZ9k4DewbNfFj8GQYSbbJIwe
SsCI3zWQzj8C9GRh3sfIB5XeMhg6j6JCQCTl1jNdfK7vsU1P1FeQNWrcrgSXSYk0
ly4wBOeY99sLAZDBHwo/+ML+TvrbmnNzFrwFuHnYWa8G5z9nODmxfKuU4CkUpijy
323imttUQ/hHWKNddBWcwauwxzQ=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFqTCCA5GgAwIBAgIQaSYJfoBLTKCnjHhiU19abzANBgkqhkiG9w0BAQ0FADBI
MQswCQYDVQQGEwJERTEVMBMGA1UEChMMRC1UcnVzdCBHbWJIMSIwIAYDVQQDExlE
LVRSVVNUIEVWIFJvb3QgQ0EgMiAyMDIzMB4XDTIzMDUwOTA5MTAzM1oXDTM4MDUw
OTA5MTAzMlowSDELMAkGA1UEBhMCREUxFTATBgNVBAoTDEQtVHJ1c3QgR21iSDEi
MCAGA1UEAxMZRC1UUlVTVCBFViBSb290IENBIDIgMjAyMzCCAiIwDQYJKoZIhvcN
AQEBBQADggIPADCCAgoCggIBANiOo4mAC7JXUtypU0w3uX9jFxPvp1sjW2l1sJkK
F8GLxNuo4MwxusLyzV3pt/gdr2rElYfXR8mV2IIEUD2BCP/kPbOx1sWy/YgJ25yE
7CUXFId/MHibaljJtnMoPDT3mfd/06b4HEV8rSyMlD/YZxBTfiLNTiVR8CUkNRFe
EMbsh2aJgWi6zCudR3Mfvc2RpHJqnKIbGKBv7FD0fUDCqDDPvXPIEysQEx6Lmqg6
lHPTGGkKSv/BAQP/eX+1SH977ugpbzZMlWGG2Pmic4ruri+W7mjNPU0oQvlFKzIb
RlUWaqZLKfm7lVa/Rh3sHZMdwGWyH6FDrlaeoLGPaxK3YG14C8qKXO0elg6DpkiV
jTujIcSuWMYAsoS0I6SWhjW42J7YrDRJmGOVxcttSEfi8i4YHtAxq9107PncjLgc
jmgjutDzUNzPZY9zOjLHfP7KgiJPvo5iR2blzYfi6NUPGJ/lBHJLRjwQ8kTCZFZx
TnXonMkmdMV9WdEKWw9t/p51HBjGGjp82A0EzM23RWV6sY+4roRIPrN6TagD4uJ+
ARZZaBhDM7DS3LAaQzXupdqpRlyuhoFBAUp0JuyfBr/CBTdkdXgpaP3F9ev+R/nk
hbDhezGdpn9yo7nELC7MmVcOIQxFAZRl62UJxmMiCzNJkkg8/M3OsD6Onov4/knF
NXJHAgMBAAGjgY4wgYswDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUqvyREBuH
kV8Wub9PS5FeAByxMoAwDgYDVR0PAQH/BAQDAgEGMEkGA1UdHwRCMEAwPqA8oDqG
OGh0dHA6Ly9jcmwuZC10cnVzdC5uZXQvY3JsL2QtdHJ1c3RfZXZfcm9vdF9jYV8y
XzIwMjMuY3JsMA0GCSqGSIb3DQEBDQUAA4ICAQCTy6UfmRHsmg1fLBWTxj++EI14
QvBukEdHjqOSMo1wj/Zbjb6JzkcBahsgIIlbyIIQbODnmaprxiqgYzWRaoUlrRc4
pZt+UPJ26oUFKidBK7GB0aL2QHWpDsvxVUjY7NHss+jOFKE17MJeNRqrphYBBo7q
3C+jisosketSjl8MmxfPy3MHGcRqwnNU73xDUmPBEcrCRbH0O1P1aa4846XerOhU
t7KR/aypH/KH5BfGSah82ApB9PI+53c0BFLd6IHyTS9URZ0V4U/M5d40VxDJI3IX
cI1QcB9WbMy5/zpaT2N6w25lBx2Eof+pDGOJbbJAiDnXH3dotfyc1dZnaVuodNv8
ifYbMvekJKZ2t0dT741Jj6m2g1qllpBFYfXeA08mD6iL8AOWsKwV0HFaanuU5nCT
2vFp4LJiTZ6P/4mdm13NRemUAiKN4DV/6PEEeXFsVIP4M7kFMhtYVRFP0OUnR3Hs
7dpn1mKmS00PaaLJvOwiS5THaJQXfuKOKD62xur1NGyfN4gHONuGcfrNlUhDbqNP
gofXNJhuS5N5YHVpD/Aa1VP6IQzCP+k/HxiMkl14p3ZnGbuy6n/pcAlWVqOwDAst
Nl7F6cTVg8uGF5csbBNvh1qvSaYd2804BC5f4ko1Di1L+KIkBI3Y4WNeApI02phh
XBxvWHZks/wCuPWdCg==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFkzCCA3ugAwIBAgIUQ/oMX04bgBhE79G0TzUfRPSA7cswDQYJKoZIhvcNAQEL
BQAwUTELMAkGA1UEBhMCQ0gxFTATBgNVBAoTDFN3aXNzU2lnbiBBRzErMCkGA1UE
AxMiU3dpc3NTaWduIFJTQSBUTFMgUm9vdCBDQSAyMDIyIC0gMTAeFw0yMjA2MDgx
MTA4MjJaFw00NzA2MDgxMTA4MjJaMFExCzAJBgNVBAYTAkNIMRUwEwYDVQQKEwxT
d2lzc1NpZ24gQUcxKzApBgNVBAMTIlN3aXNzU2lnbiBSU0EgVExTIFJvb3QgQ0Eg
MjAyMiAtIDEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDLKmjiC8NX
vDVjvHClO/OMPE5Xlm7DTjak9gLKHqquuN6orx122ro10JFwB9+zBvKK8i5VUXu7
LCTLf5ImgKO0lPaCoaTo+nUdWfMHamFk4saMla+ju45vVs9xzF6BYQ1t8qsCLqSX
5XH8irCRIFucdFJtrhUnWXjyCcplDn/L9Ovn3KlMd/YrFgSVrpxxpT8q2kFC5zyE
EPThPYxr4iuRR1VPuFa+Rd4iUU1OKNlfGUEGjw5NBuBwQCMBauTLE5tzrE0USJIt
/m2n+IdreXXhvhCxqohAWVTXz8TQm0SzOGlkjIHRI36qOTw7D59Ke4LKa2/KIj4x
0LDQKhySio/YGZxH5D4MucLNvkEM+KRHBdvBFzA4OmnczcNpI/2aDwLOEGrOyvi5
KaM2iYauC8BPY7kGWUleDsFpswrzd34unYyzJ5jSmY0lpx+Gs6ZUcDj8fV3oT4MM
0ZPlEuRU2j7yrTrePjxF8CgPBrnh25d7mUWe3f6VWQQvdT/TromZhqwUtKiE+shd
OxtYk8EXlFXIC+OCeYSf8wCENO7cMdWP8vpPlkwGqnj73mSiI80fPsWMvDdUDrta
clXvyFu1cvh43zcgTFeRc5JzrBh3Q4IgaezprClG5QtO+DdziZaKHG29777YtvTK
wP1H8K4LWCDFyB02rpeNUIMmJCn3nTsPBQIDAQABo2MwYTAPBgNVHRMBAf8EBTAD
AQH/MA4GA1UdDwEB/wQEAwIBBjAfBgNVHSMEGDAWgBRvjmKLk0Ow4UD2p8P98Q+4
DxU4pTAdBgNVHQ4EFgQUb45ii5NDsOFA9qfD/fEPuA8VOKUwDQYJKoZIhvcNAQEL
BQADggIBAKwsKUF9+lz1GpUYvyypiqkkVHX1uECry6gkUSsYP2OprphWKwVDIqO3
10aewCoSPY6WlkDfDDOLazeROpW7OSltwAJsipQLBwJNGD77+3v1dj2b9l4wBlgz
Hqp41eZUBDqyggmNzhYzWUUo8aWjlw5DI/0LIICQ/+Mmz7hkkeUFjxOgdg3XNwwQ
iJb0Pr6VvfHDffCjw3lHC1ySFWPtUnWK50Zpy1FVCypM9fJkT6lc/2cyjlUtMoIc
gC9qkfjLvH4YoiaoLqNTKIftV+Vlek4ASltOU8liNr3CjlvrzG4ngRhZi0Rjn9UM
ZfQpZX+RLOV/fuiJz48gy20HQhFRJjKKLjpHE7iNvUcNCfAWpO2Whi4Z2L6MOuhF
LhG6rlrnub+xzI/goP+4s9GFe3lmozm1O2bYQL7Pt2eLSMkZJVX8vY3PXtpOpvJp
zv1/THfQwUY1mFwjmwJFQ5Ra3bxHrSL+ul4vkSkphnsh3m5kt8sNjzdbowhq6/Td
Ao9QAwKxuDdollDruF/UKIqlIgyKhPBZLtU30WHlQnNYKoH3dtvi4k0NX/a3vgW0
rk4N3hY9A4GzJl5LuEsAz/+MF7psYC0nhzck5npgL7XTgwSqT0N1osGDsieYK7EO
gLrAhV5Cud+xYJHT6xh+cHiudoO+cVrQkOPKwRYlZ0rwtnu64ZzZ
-----END CERTIFICATE-----
"""   # 146 certificates from autoscout/cacert.pem   # the single-file build inlines autoscout/cacert.pem here


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()

def endgame_points(opr: dict, season: int) -> float:
    keys = SEASON_ENDGAME_KEYS.get(int(season))
    if keys is None:
        keys = [k for k in opr if k in _ENDGAME_HINTS] or [k for k in opr if re.search(r"(Park|Base|Ascent|Climb|Hang)Points$", k) and not k.endswith("Individual")]
    return float(sum(opr.get(k, 0.0) or 0.0 for k in keys))


def event_stat_from_json(team: int, season: int, event_code: str, stats: Optional[dict],
                         event_info: Optional[dict] = None) -> EventStat:
    """One entry of /teams/{n}/events/{season} or /events/{s}/{c}/teams → EventStat."""
    es = EventStat(team=int(team), event_code=event_code)
    if event_info:
        es.event_name = event_info.get("name", "") or ""
        es.start_date = (event_info.get("start") or "")[:10]
    if not stats:
        return es
    opr = stats.get("opr") or {}
    es.opr_np = float(opr.get("totalPointsNp", 0.0) or 0.0)
    es.opr_total = float(opr.get("totalPoints", 0.0) or 0.0)
    es.auto = float(opr.get("autoPoints", 0.0) or 0.0)
    es.dc = float(opr.get("dcPoints", 0.0) or 0.0)
    es.endgame = endgame_points(opr, season)
    es.tb1 = float(stats.get("tb1", 0.0) or 0.0)
    es.tb2 = float(stats.get("tb2", 0.0) or 0.0)
    es.rank = int(stats.get("rank", 0) or 0)
    es.rp = float(stats.get("rp", 0.0) or 0.0)
    es.wins = int(stats.get("wins", 0) or 0)
    es.losses = int(stats.get("losses", 0) or 0)
    es.ties = int(stats.get("ties", 0) or 0)
    es.qual_played = int(stats.get("qualMatchesPlayed", 0) or 0)
    return es


def matches_from_json(payload: list, quals_only: bool = True) -> list[Match]:
    """/events/{s}/{c}/matches → core.Match list (sorted by id)."""
    out = []
    for m in sorted(payload, key=lambda x: x.get("id", 0)):
        level = m.get("tournamentLevel", "Quals")
        if quals_only and level != "Quals":
            continue
        red = tuple(t["teamNumber"] for t in m.get("teams", []) if t.get("alliance") == "Red")
        blue = tuple(t["teamNumber"] for t in m.get("teams", []) if t.get("alliance") == "Blue")
        surrogates = tuple(t["teamNumber"] for t in m.get("teams", []) if t.get("surrogate"))
        if not red and not blue:
            continue
        mid = int(m.get("id", 0))
        prefix = "Q" if level == "Quals" else "E"
        num = mid if level == "Quals" else mid % 1000 or mid
        mt = Match(label=f"{prefix}-{num}", red=red, blue=blue, number=num, level=level,
                   surrogates=surrogates, scheduled=m.get("scheduledStartTime") or "")
        sc = m.get("scores") or {}
        if m.get("hasBeenPlayed") and sc.get("red") and sc.get("blue"):
            mt.played = True
            mt.red_score = float(sc["red"].get("totalPoints", 0))
            mt.blue_score = float(sc["blue"].get("totalPoints", 0))
            mt.red_score_np = float(sc["red"].get("totalPointsNp", mt.red_score))
            mt.blue_score_np = float(sc["blue"].get("totalPointsNp", mt.blue_score))
        out.append(mt)
    return out

_NUM = re.compile(r"\b\d{2,5}\b")
_LABEL = re.compile(r"\b(?:Q|Qual(?:ification)?|M|Match)\s*-?\s*(\d{1,3})\b", re.I)
_SCORE = re.compile(r"(?<!\d)(\d{1,3})\s*[-–]\s*(\d{1,3})(?!\d)")


def parse_schedule_text(text: str) -> list[Match]:
    """Parse a pasted schedule. Accepts one match per line in forms like:
        Q-1  11225 19013  19564 23213
        1    11225 19013 vs 19564 23213   104-66
        Q1   11225 (Chaotic Current) 19013 ... 19564 ... 23213 ...
    The first two team numbers are red, the next two blue; an optional trailing
    "red-blue" score marks the match as played. Team numbers are 2–5 digits;
    a leading bare 1–3 digit token is treated as the match number.
    """
    out = []
    n = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("match", "#", "time")):
            continue
        label_num = None
        m = _LABEL.search(line)
        if m:
            label_num = int(m.group(1))
            line = line[:m.start()] + " " + line[m.end():]
        score = None
        sm = _SCORE.search(line)
        if sm:
            score = (float(sm.group(1)), float(sm.group(2)))
            line = line[:sm.start()] + " " + line[sm.end():]
        nums = [int(x) for x in _NUM.findall(line)]
        if label_num is None and nums and nums[0] < 200 and len(nums) >= 5:
            label_num = nums.pop(0)
        nums = [x for x in nums if x >= 100 or len(nums) <= 4]
        if len(nums) < 4:
            continue
        n += 1
        num = label_num if label_num is not None else n
        mt = Match(label=f"Q-{num}", red=(nums[0], nums[1]), blue=(nums[2], nums[3]), number=num)
        if score:
            mt.played = True
            mt.red_score, mt.blue_score = score
            mt.red_score_np, mt.blue_score_np = score
        out.append(mt)
    return out


def parse_team_list(text: str) -> list[int]:
    return sorted({int(x) for x in _NUM.findall(text or "")})

@dataclass
class EventBundle:
    season: int
    code: str
    info: dict = field(default_factory=dict)
    team_numbers: list[int] = field(default_factory=list)
    team_names: dict[int, str] = field(default_factory=dict)
    current: dict[int, EventStat] = field(default_factory=dict)      # stats at this event
    history: dict[int, list[EventStat]] = field(default_factory=dict) # all events this season
    matches: list[Match] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    epa_events: list = field(default_factory=list)       # list[EpaEvent]: earlier events' results, for EPA


def load_event(client: FTCScoutClient, season: int, code: str,
               progress: Optional[Callable[[str, float], None]] = None,
               with_history: bool = True, extra_teams: Optional[list[int]] = None,
               epa_future: bool = False) -> EventBundle:
    """Fetch everything the session needs. `progress(message, fraction)` is optional."""
    def tick(msg: str, f: float):
        if progress:
            progress(msg, f)

    b = EventBundle(season=int(season), code=code.strip().upper())
    tick("Loading event…", 0.02)
    b.info = client.event(season, code)
    tick("Loading teams…", 0.08)
    for row in client.event_teams(season, code):
        n = int(row["teamNumber"])
        b.team_numbers.append(n)
        b.current[n] = event_stat_from_json(n, season, b.code, row.get("stats"), b.info)
    for n in extra_teams or []:
        if n not in b.team_numbers:
            b.team_numbers.append(n)
    b.team_numbers.sort()
    tick("Loading matches…", 0.15)
    try:
        b.matches = matches_from_json(client.event_matches(season, code))
    except NotFound:
        b.matches = []
        b.warnings.append("No match schedule published yet - paste one in Setup.")
    total = len(b.team_numbers)
    event_cache: dict[str, dict] = {b.code: b.info}
    for i, n in enumerate(b.team_numbers):
        tick(f"Team {n} ({i + 1}/{total})", 0.2 + 0.6 * (i / max(total, 1)))
        try:
            b.team_names[n] = client.team(n).get("name", "") or ""
        except ApiError:
            b.team_names[n] = ""
        hist: list[EventStat] = []
        if with_history:
            try:
                for e in client.team_events(n, season):
                    ec = e.get("eventCode", "")
                    if ec not in event_cache:
                        try:
                            event_cache[ec] = client.event(season, ec)
                        except ApiError:
                            event_cache[ec] = {}
                    hist.append(event_stat_from_json(n, season, ec, e.get("stats"), event_cache.get(ec)))
            except ApiError as e:
                b.warnings.append(f"Team {n}: {e.user_message}")
        if not hist and n in b.current:
            hist = [b.current[n]]
        b.history[n] = hist
    if with_history:
        b.epa_events, warns = fetch_epa_events(client, b.season, b.code, (b.info or {}).get("start") or "",
                                               b.history, lambda msg, f: tick(msg, 0.8 + 0.19 * f),
                                               include_later=epa_future)
        b.warnings.extend(warns)
    tick("Done", 1.0)
    return b


#: Finished events never change, so their results can sit in the cache for a week.
EPA_HISTORY_MAX_AGE = 7 * 86400


def fetch_epa_events(client: FTCScoutClient, season: int, current_code: str, current_start: str,
                     history: dict, progress: Optional[Callable[[str, float], None]] = None,
                     include_later: bool = False) -> tuple[list, list[str]]:
    """Results of every earlier event these teams played this season (what EPA warms up on).

    Found through the teams' own histories, so the cost is one cached request per distinct
    event. Events on or after the current one's start date are skipped, so replaying an old
    meet never lets EPA see the future.
    """
    cur = (current_start or "")[:10]
    found: dict[str, str] = {}
    for hist in history.values():
        for h in hist:
            if not h.event_code or h.event_code == current_code:
                continue
            if not include_later and cur and h.start_date and h.start_date >= cur:
                continue
            found.setdefault(h.event_code, h.start_date or "")
    order = sorted(found.items(), key=lambda kv: (kv[1] or "9999", kv[0]))
    out: list = []
    warns: list[str] = []
    for i, (code, start) in enumerate(order):
        if progress:
            progress(f"Match history {code} ({i + 1}/{len(order)})", i / max(len(order), 1))
        try:
            ms = [m for m in matches_from_json(client.event_matches(season, code, max_age=EPA_HISTORY_MAX_AGE))
                  if m.played]
        except NotFound:
            continue
        except (NetworkError, OfflineMiss):
            warns.append("Earlier events' match results could not be fetched (offline or not cached); "
                         "EPA will learn from this event only.")
            break
        except ApiError as e:
            warns.append(f"Match history {code}: {e.user_message}")
            continue
        if ms:
            out.append(EpaEvent(code=code, start=start, matches=ms))
    return out, warns

"""Post-mortem of the predictions - a port of AutoScout's `Analysis` and `Sheet18` tabs.

`Analysis` asks, for every match the app called wrong: by how many points was the CAP
off, and was it off because the CAP is wrong or because somebody took penalties?

  * column O / AJ  → the "Lower Blue ≈ 34 pts" suggestion (`WrongMatch.suggestion`)
  * columns K–N    → per-team verdict from how often the team shows up in wrong matches
  * columns Q–W    → per-match verdict from the verdicts of its four teams

`Sheet18` is the validation table: the prediction judged against the *non-penalty*
winner (columns J/K/L) and against the ±10-point margin heuristic (column P).

Pure computation over a `session.Session`; no I/O.
"""

import math
from dataclasses import dataclass, field
from typing import Optional


# Analysis!N - the sheet spells it "INNACURATE"; fixed here.
CAP_INACCURATE = "CAP INACCURATE"
LIKELY_PENALTY = "LIKELY PENALTY POINTS"
# Analysis!W - the per-match verdict uses title case in the sheet.
MATCH_CAP_INACCURATE = "Cap Inaccurate"
MATCH_LIKELY_PENALTY = "Likely Penalty Points"

#: Analysis!N8 - a team in at most this many wrong matches is a CAP problem, not penalties.
WRONG_APPEARANCE_THRESHOLD = 2


def _round(x: float) -> int:
    """Sheets ROUND(x, 0): half away from zero (Python's round() is half-to-even)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))

@dataclass
class WrongMatch:
    """One row of Analysis rows 8+ where the calculated outcome missed."""
    prediction: core.Prediction
    suggestion: str            # Analysis!O / AJ
    classification: str        # Analysis!W - MATCH_CAP_INACCURATE | MATCH_LIKELY_PENALTY

    @property
    def match(self) -> core.Match:
        return self.prediction.match


@dataclass
class TeamFlag:
    """One row of Analysis columns L–N, plus the record the UI shows beside it."""
    team: int
    name: str
    wrong_appearances: int     # Analysis!M = COUNTIF(E8:H1000, team)
    classification: str        # Analysis!N - CAP_INACCURATE | LIKELY_PENALTY
    predicted_wins: int
    actual_wins: int
    scheduled: int


@dataclass
class AnalysisReport:
    accuracy: tuple[int, int]          # AutoScout '1'!A7  (correct, decided)
    margin_accuracy: tuple[int, int]   # Sheet18!P vs the actual winner
    np_accuracy: tuple[int, int]       # Sheet18!L - prediction vs the non-penalty winner
    wrong: list[WrongMatch] = field(default_factory=list)
    team_flags: dict[int, TeamFlag] = field(default_factory=dict)
    summary_lines: list[str] = field(default_factory=list)
    
    @property
    def inaccuracy(self) -> Optional[float]:
        """Analysis!A4 = 100% − accuracy."""
        c, d = self.accuracy
        return 1 - c / d if d else None

    def flags_by_severity(self) -> list[TeamFlag]:
        """Worst offenders first (most wrong matches, then most-missed record)."""
        return sorted(self.team_flags.values(),
                      key=lambda f: (f.wrong_appearances, f.predicted_wins - f.actual_wins, -f.team),
                      reverse=True)


def suggestion_for(diff: float, actual_winner: str) -> str:
    """Analysis!O8, transcribed literally.

    `diff` = predicted red points − predicted blue points; `actual_winner` is RED or BLUE.
    Reads: the losing-by-our-numbers alliance actually won, so either the winner's CAP is
    too low or the loser's is too high; the sheet suggests both when the gap is big.
    """
    if actual_winner == "RED":
        if diff < 0:
            s = f"Lower Blue ≈ {_round(-diff + 8)} pts"
            if -diff > 20:
                s += f" + raise Red ≈ {_round(-diff / 4)} pts"
            return s
        return f"Raise Red ≈ {_round(diff + 10)} pts"
    if diff > 0:
        s = f"Lower Red ≈ {_round(diff + 8)} pts"
        if diff > 20:
            s += f" + raise Blue ≈ {_round(diff / 4)} pts"
        return s
    return f"Raise Blue ≈ {_round(-diff + 10)} pts"


def np_accuracy(preds) -> tuple[int, int]:
    """Sheet18!L: judge the call against the winner *before* penalties. Ties excluded."""
    correct = decided = 0
    for p in preds:
        w = p.match.np_winner
        if w in ("RED", "BLUE"):
            decided += 1
            correct += int(p.winner == w)
    return correct, decided


def margin_accuracy(preds, points: float = 10.0) -> tuple[int, int]:
    """Sheet18!P: the ±`points` heuristic (flip the call inside the margin). Ties excluded."""
    correct = decided = 0
    for p in preds:
        w = p.match.winner
        if w in ("RED", "BLUE"):
            decided += 1
            correct += int(p.margin_call(points) == w)
    return correct, decided


def analyze(session) -> AnalysisReport:
    """Build the whole Analysis/Sheet18 post-mortem for a session."""
    preds = list(session.predictions)
    settings = getattr(session, "settings", None) or core.Settings()
    # The verdict strings name whichever rating made the calls (CAP unless Settings.rating says otherwise).
    global CAP_INACCURATE, MATCH_CAP_INACCURATE
    _lab = settings.basis
    CAP_INACCURATE = f"{_lab} INACCURATE"
    MATCH_CAP_INACCURATE = "Cap Inaccurate" if _lab == "CAP" else f"{_lab} Inaccurate"

    rep = AnalysisReport(
        accuracy=core.accuracy(preds),
        margin_accuracy=margin_accuracy(preds, settings.margin_points),
        np_accuracy=np_accuracy(preds),
    )

    wrong_preds = [p for p in preds if p.correct is False]

    # --- Analysis!K,L,M: the teams that appear in wrong matches, and how often ---
    appearances: dict[int, int] = {}
    for p in wrong_preds:
        for n in p.match.teams:
            appearances[n] = appearances.get(n, 0) + 1

    # Records over the *whole* schedule, for context next to each flag.
    predicted_wins: dict[int, int] = {}
    actual_wins: dict[int, int] = {}
    scheduled: dict[int, int] = {}
    for p in preds:
        m = p.match
        for n in m.teams:
            if n in m.surrogates:
                continue
            scheduled[n] = scheduled.get(n, 0) + 1
            color = m.color_of(n)
            if p.winner == color:
                predicted_wins[n] = predicted_wins.get(n, 0) + 1
            if m.winner == color:
                actual_wins[n] = actual_wins.get(n, 0) + 1

    def team_name(n: int) -> str:
        t = session.teams.get(n) if getattr(session, "teams", None) else None
        if t is not None and t.name:
            return t.name
        return (getattr(session, "team_names", {}) or {}).get(n, "")

    for n, count in appearances.items():
        rep.team_flags[n] = TeamFlag(
            team=n, name=team_name(n), wrong_appearances=count,
            classification=CAP_INACCURATE if count <= WRONG_APPEARANCE_THRESHOLD else LIKELY_PENALTY,
            predicted_wins=predicted_wins.get(n, 0), actual_wins=actual_wins.get(n, 0),
            scheduled=scheduled.get(n, 0))

    # --- Analysis!O and Q–W, per wrong match ---
    for p in wrong_preds:
        diff = p.red_pts - p.blue_pts
        actual = p.match.winner or ""
        n_cap = n_pen = 0
        for n in p.match.teams:
            f = rep.team_flags.get(n)
            if f is None:
                continue
            if f.classification == CAP_INACCURATE:
                n_cap += 1
            else:
                n_pen += 1
        rep.wrong.append(WrongMatch(
            prediction=p,
            suggestion=suggestion_for(diff, actual),
            classification=MATCH_CAP_INACCURATE if n_cap > n_pen else MATCH_LIKELY_PENALTY))

    rep.summary_lines = _summary(session, rep)
    return rep


def _pct(pair: tuple[int, int]) -> str:
    c, d = pair
    return f"{c}/{d} = {c / d:.0%}" if d else f"{c}/{d} = n/a"


def _summary(session, rep: AnalysisReport) -> list[str]:
    """A short report a student can paste into their notebook."""
    name = getattr(session, "event_name", "") or getattr(session, "event_code", "") or "this event"
    lines = [f"Prediction report - {name} ({session.season}/{session.event_code})"]
    played = sum(1 for p in session.predictions if p.match.played)
    lines.append(f"{len(session.predictions)} matches on the schedule, {played} played.")
    if session.settings.faithful:
        lines.append("Sheet-faithful mode is ON (the app is reproducing the old spreadsheet's quirks).")
    lines.append(f"Accuracy: {_pct(rep.accuracy)} of decided matches called correctly.")
    lines.append(f"Ignoring penalties: {_pct(rep.np_accuracy)} "
                 f"(how often the call matched who scored more before penalties).")
    lines.append(f"Margin rule (±{session.settings.margin_points:g} pts): {_pct(rep.margin_accuracy)}.")

    if not rep.wrong:
        lines.append("No wrong calls yet - nothing to explain.")
    else:
        n_cap = sum(1 for w in rep.wrong if w.classification == MATCH_CAP_INACCURATE)
        lines.append(f"{len(rep.wrong)} wrong calls: {n_cap} look like inaccurate {session.settings.basis}s, "
                     f"{len(rep.wrong) - n_cap} look like penalty points.")
        flags = rep.flags_by_severity()
        top = [f for f in flags if f.classification == LIKELY_PENALTY][:5] or flags[:5]
        if top:
            lines.append("Teams to re-scout:")
            for f in top:
                lines.append(f"   {f.team} {f.name[:24]:24s} in {f.wrong_appearances} wrong "
                             f"{'match' if f.wrong_appearances == 1 else 'matches'} - {f.classification} "
                             f"(predicted {f.predicted_wins} wins, actual {f.actual_wins} of {f.scheduled})")

    if session.my_team:
        f = rep.team_flags.get(session.my_team)
        mine = session.my_matches()
        w = sum(1 for x in mine if x.actual == "Win")
        l = sum(1 for x in mine if x.actual == "Loss")
        t = sum(1 for x in mine if x.actual == "Tie")
        pw = sum(1 for x in mine if x.predicted_win)
        lines.append(f"My team {session.my_team}: {w}-{l}-{t} actual, {pw}/{len(mine)} predicted wins"
                     + (f"; appears in {f.wrong_appearances} wrong calls ({f.classification})" if f else ""))
    return lines


"""Exports - the sheets' "Printing instructions" tab, minus the printing instructions.

Three outputs, all from the same session:

  export_csv(session, path)      one CSV holding both tables (see below)
  export_html(session, path)     a self-contained printable page (inline CSS, no assets)
  printable_text(session)        the same report as monospace text

CSV layout: a single file with two labelled sections, because a meet report is one
artefact and students mail it around as one attachment. Section 1 is `## TEAMS`
(one row per team, ranked by the chosen rating); section 2 is `## MATCHES` (one row per match).
Each section has its own header row; a blank line separates them. Spreadsheets
import this fine - Excel/Sheets simply show the section markers as text rows.
"""

import csv
import datetime
import html
import io
import os
from typing import Optional



CSV_TEAM_COLUMNS = ["Rank", "Team", "Name", "OPR", "Auto", "Teleop", "Endgame", "AVG", "NEW AVG",
                    "Projected OPR", "Technical issue", "Technical issues adjustment", "Team average", "Focus", "CAP",
                    "CAP-OPR", "EPA", "Events", "Source event", "Record"]

CSV_MATCH_COLUMNS = ["Match", "Red 1", "Red 2", "Blue 1", "Blue 2", "Red pts", "Blue pts",
                     "Red win chance", "Blue win chance", "Red strength", "Blue strength",
                     "Predicted", "Actual", "Red score", "Blue score",
                     "Correct", "Surrogates"]

CSV_STANDING_COLUMNS = ["Rank", "Team", "Name", "Wins", "Losses", "Played", "Scheduled",
                        "Ranking points", "Auto tiebreaker points", "Endgame tiebreaker points", "Captain"]

CSV_ALLIANCE_COLUMNS = ["Rank", "Team 1", "Team 2", "Teams", "Total CAP", "Beats", "Opponents",
                        "Win rate", "Alliance Strength", "Closest opponent", "Margin",
                        "Chance of winning", "Verdict"]

CSV_CAPTAIN_COLUMNS = ["Cpt.", "Team", "Name", "CAP", "Best available", "Likely pick",
                       "Pick CAP", "Projected total", "Pick above"]


def _f(x: Optional[float], nd: int = 2) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def team_rows(session) -> list[list]:
    rows = []
    for i, t in enumerate(session.teams_by_strength(), 1):
        rows.append([i, t.number, t.name, _f(t.opr), _f(t.auto), _f(t.teleop), _f(t.endgame),
                     _f(t.avg), _f(t.new_avg), _f(t.proj_opr), t.tdc_issue, _f(t.tdc_delta),
                     _f(t.ftc_avg), t.focus, _f(t.cap), _f(t.cap_minus_opr), _f(t.epa),
                     t.events_played, t.source_event, t.record])
    return rows


def match_rows(session) -> list[list]:
    rows = []
    for p in session.predictions:
        m = p.match
        red = list(m.red) + ["", ""]
        blue = list(m.blue) + ["", ""]
        correct = "" if p.correct is None else ("YES" if p.correct else "NO")
        rows.append([m.label, red[0], red[1], blue[0], blue[1], _f(p.red_pts, 1), _f(p.blue_pts, 1),
                     f"{p.red_cow:.1%}", f"{p.blue_cow:.1%}",
                     f"{p.red_strength:.1f}%", f"{p.blue_strength:.1f}%", p.winner, m.winner or "",
                     _f(m.red_score, 0), _f(m.blue_score, 0), correct,
                     " ".join(str(n) for n in m.surrogates)])
    return rows


def standing_rows(session) -> list[list]:
    return [[r.rank, r.number, r.name, r.wins, r.losses, r.played, r.scheduled,
             f"{r.rp:.2f}", _f(r.tbp1), _f(r.tbp2), "captain" if r.captain_rank else ""]
            for r in session.table]


#: The printable page and the text report are read on paper, so they carry the top of
#: the alliance ranking; the CSV carries all of it, because a spreadsheet can scroll.
PRINTED_ALLIANCES = 40


def alliance_rows(report, limit: int = 0) -> list[list]:
    rows = []
    for a in (report.table[:limit] if limit else report.table):
        rows.append([a.rank, a.teams[0], a.teams[1], " / ".join(n for n in a.names if n),
                     _f(a.cap_sum), a.beats, a.legal, f"{a.win_rate:.1%}",
                     f"{a.alliance_strength:.1f}%",
                     " + ".join(str(n) for n in a.closest), _f(a.margin), f"{a.cow:.1%}",
                     "UNBEATABLE" if a.unbeatable else ""])
    return rows


def captain_rows(report) -> list[list]:
    return [[s.rank, s.team, s.name, _f(s.cap), _f(s.best_available), s.pick or "",
             _f(s.pick_cap), _f(s.projected_total), _f(s.bar)] for s in report.seats]


def _header_facts(session, rep) -> list[tuple[str, str]]:
    c, d = rep.accuracy
    facts = [("Event", f"{session.event_name}  ({session.season} / {session.event_code})"),
             ("Teams", str(len(session.teams))),
             ("Matches", str(len(session.predictions)))]
    if session.my_team:
        facts.append(("My team", str(session.my_team)))
    facts.append(("Accuracy", f"{c}/{d}" + (f"  ({c / d:.0%})" if d else "")))
    facts.append(("Mode", "sheet-faithful" if session.settings.faithful else "corrected"))
    facts.append(("Predictions based on", session.settings.basis))
    if session.loaded_at:
        facts.append(("Data loaded", session.loaded_at))
    facts.append(("Report made", datetime.datetime.now().isoformat(timespec="minutes")))
    return facts



def export_csv(session, path: str) -> str:
    """Write the teams section and the matches section into one CSV. Returns the path."""
    rep = _analysis.analyze(session)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([f"# {APP_FULL_TITLE} {__version__} export"])
        for k, v in _header_facts(session, rep):
            w.writerow([f"# {k}", v])
        w.writerow([])
        w.writerow(["## TEAMS"])
        w.writerow(CSV_TEAM_COLUMNS)
        w.writerows(team_rows(session))
        w.writerow([])
        w.writerow(["## MATCHES"])
        w.writerow(CSV_MATCH_COLUMNS)
        w.writerows(match_rows(session))
        w.writerow([])
        w.writerow(["## STANDINGS"])
        w.writerow(CSV_STANDING_COLUMNS)
        w.writerows(standing_rows(session))
        alli = session.alliance_report()
        w.writerow([])
        w.writerow(["## ALLIANCES"])
        w.writerow([f"# {len(alli.table)} possible two-team alliances; "
                    f"{len(alli.unbeatable)} beat every alliance they could face"])
        w.writerow([c.replace("CAP", session.settings.basis) for c in CSV_ALLIANCE_COLUMNS])
        w.writerows(alliance_rows(alli))
        w.writerow([])
        w.writerow(["## ALLIANCE CAPTAINS"])
        w.writerow([c.replace("CAP", session.settings.basis) for c in CSV_CAPTAIN_COLUMNS])
        w.writerows(captain_rows(alli))
        w.writerow([])
        w.writerow(["## ANALYSIS"])
        for line in rep.summary_lines:
            w.writerow([line])
        if rep.wrong:
            w.writerow([])
            w.writerow(["## WRONG CALLS"])
            w.writerow(["Match", "Predicted", "Actual", "Red pts", "Blue pts", "Suggestion", "Classification"])
            for x in rep.wrong:
                w.writerow([x.match.label, x.prediction.winner, x.match.winner,
                            _f(x.prediction.red_pts, 1), _f(x.prediction.blue_pts, 1),
                            x.suggestion, x.classification])
    return path


def _table(headers: list[str], rows: list[list], aligns: str = "") -> list[str]:
    cells = [[str(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in cells:
        for i, c in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(c))
    aligns = (aligns + "l" * len(headers))[:len(headers)]

    def line(vals: list[str]) -> str:
        out = []
        for i, v in enumerate(vals):
            out.append(v.rjust(widths[i]) if aligns[i] == "r" else v.ljust(widths[i]))
        return "  ".join(out).rstrip()

    return [line(headers), "  ".join("-" * w for w in widths)] + [line(r) for r in cells]


def printable_text(session) -> str:
    """Monospace version of the printable report."""
    rep = _analysis.analyze(session)
    out: list[str] = []
    title = f"{APP_FULL_TITLE} - {session.event_name}"
    out += [title, "=" * len(title), f"{APP_TAGLINE} · {APP_ORG_FULL}", ""]
    for k, v in _header_facts(session, rep):
        out.append(f"{k + ':':14s}{v}")
    out.append("")

    out += ["MATCHES", "-------"]
    out += _table(["Match", "Red", "Blue", "Red pts", "Blue pts", "Win chance", "Predicted", "Actual", "OK"],
                  [[p.match.label,
                    " ".join(str(n) for n in p.match.red),
                    " ".join(str(n) for n in p.match.blue),
                    f"{p.red_pts:.1f}", f"{p.blue_pts:.1f}",
                    f"{p.red_cow:.0%}/{p.blue_cow:.0%}",
                    p.winner, p.match.winner or "-",
                    "" if p.correct is None else ("OK" if p.correct else "X")]
                   for p in session.predictions],
                  aligns="lllrrl")
    out.append("")

    lab = session.settings.basis
    title = f"TEAMS (by {lab})"
    out += [title, "-" * len(title)]
    out += _table(["#", "Team", "Name", "OPR", "Auto", "Tele", "Endg", "ProjOPR", "CAP", "EPA", "Technical issue"],
                  [[i, t.number, t.name[:22], f"{t.opr:.1f}", f"{t.auto:.1f}", f"{t.teleop:.1f}",
                    f"{t.endgame:.1f}", _f(t.proj_opr, 1) or "n/a", f"{t.cap:.1f}", _f(t.epa, 1), t.tdc_issue]
                   for i, t in enumerate(session.teams_by_strength(), 1)],
                  aligns="rrlrrrrrrr")
    out.append("")

    out += ["PROJECTED STANDINGS", "-------------------"]
    out += _table(["Rank", "Team", "Name", "W-L", "Ranking points", "Auto tiebreaker points", "Endgame tiebreaker points", ""],
                  [[r.rank, r.number, r.name[:22], f"{r.wins}-{r.losses}", f"{r.rp:.2f}",
                    f"{r.tbp1:.1f}", f"{r.tbp2:.1f}", "captain" if r.captain_rank else ""]
                   for r in session.table],
                  aligns="rrlcrrr")
    out.append("")

    if session.my_team and session.my_team in session.teams:
        info = session.my_summary()
        out += [f"MY TEAM {session.my_team}", "-" * (8 + len(str(session.my_team)))]
        out += _table(["Match", "Color", "With", "Against", "Win chance", "Match rating", "Predicted", "Actual"],
                      [[x.prediction.match.label, x.color,
                        " ".join(str(n) for n in x.partner),
                        " ".join(str(n) for n in x.opponents),
                        f"{x.cow:.0%}", x.rating,
                        "Win" if x.predicted_win else "Loss", x.actual or "-"]
                       for x in info["matches"]],
                      aligns="lllrrr")
        out.append(f"Predicted wins {info['wins']}/{len(info['matches'])}, "
                   f"old RP {info['old_rp']}, projected rank {info['rank']}")
        out.append("")

    alli = session.alliance_report()
    if alli.table:
        out += ["BEST ALLIANCES", "--------------"]
        out.append(f"{len(alli.table)} possible alliances; {len(alli.unbeatable)} beat every "
                   f"alliance they could face (no shared team, so both could take the field).")
        if alli.bar is not None:
            out.append(f"Team {alli.my_team}: rank {alli.my_rank}, {lab} {alli.my_cap:.1f} - "
                       f"pick must be above {alli.bar:.1f}")
        out += _table(["#", "Alliance", "Total", "Beats", "Win rate", "Closest", "Margin", "Win chance", ""],
                      [[a.rank, a.label, f"{a.cap_sum:.1f}", f"{a.beats}/{a.legal}",
                        f"{a.win_rate:.1%}", " + ".join(str(n) for n in a.closest),
                        f"{a.margin:+.1f}", f"{a.cow:.0%}", "UNBEATABLE" if a.unbeatable else ""]
                       for a in alli.table[:PRINTED_ALLIANCES]],
                      aligns="rlrrrlrr")
        if len(alli.table) > PRINTED_ALLIANCES:
            out.append(f"… and {len(alli.table) - PRINTED_ALLIANCES} more; the CSV export has all of them.")
        out.append("")

        out += ["ALLIANCE CAPTAINS", "-----------------"]
        out += _table(["Cpt.", "Team", "Name", lab, "Best avail.", "Likely pick", "Proj. total", "Pick above"],
                      [[s.rank, s.team, s.name[:22], f"{s.cap:.1f}", f"{s.best_available:.1f}",
                        f"{s.pick or '-'} ({s.pick_cap:.1f})", f"{s.projected_total:.1f}", f"{s.bar:.1f}"]
                       for s in alli.seats],
                      aligns="rrlrrlrr")
        out.append("")

        if alli.partners:
            title = f"TEAMS {alli.my_team} COULD WIN WITH"
            out += [title, "-" * len(title)]
            out += _table(["Partner", "Name", f"Their {lab}", "Total", "Beats", "Win rate", "Win chance", ""],
                          [[a.partner_of(alli.my_team), a.partner_name(alli.my_team)[:22],
                            f"{a.partner_cap(alli.my_team):.1f}", f"{a.cap_sum:.1f}",
                            f"{a.beats}/{a.legal}", f"{a.win_rate:.1%}", f"{a.cow:.0%}",
                            "UNBEATABLE" if a.unbeatable else ""]
                           for a in alli.partners[:PRINTED_ALLIANCES]],
                          aligns="rlrrrrr")
            avg = alli.average_total
            if avg is not None:
                out.append(f"{len(alli.qualifying)} of {len(alli.partners)} partners clear the bar; "
                           f"they average {avg:.1f} total {lab}.")
            out.append("")

    out += ["ANALYSIS", "--------"]
    out += rep.summary_lines
    if rep.wrong:
        out.append("")
        out += _table(["Match", "Pred", "Actual", "Suggestion", "Looks like"],
                      [[x.match.label, x.prediction.winner, x.match.winner or "", x.suggestion,
                        x.classification] for x in rep.wrong])
    out.append("")
    return "\n".join(out)



_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 0 0 24px;
       font: 13px/1.45 "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;
       color: #14181d; background: #fff; }
h1, h2, .word { font-family: "Roboto", -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
.brandbar { display: flex; align-items: center; gap: 12px; background: #5d081f; color: #fff;
            padding: 14px 24px; margin: 0 0 20px; }
.brandbar .word { font-size: 18px; font-weight: 900; }
.brandbar .word .auto { font-style: italic; }
.brandbar .tagline { font-size: 12px; color: #dbdce0; font-weight: 600;
                      font-family: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace; }
.wrap { padding: 0 24px; }
h1 { font-size: 20px; margin: 0 0 2px; font-weight: 900; }
h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .08em; color: #5b6570;
     border-bottom: 1px solid #d5dae0; padding-bottom: 4px; margin: 26px 0 8px; }
.sub { color: #5b6570; margin: 0 0 14px; }
.facts { display: flex; flex-wrap: wrap; gap: 6px 22px; margin: 0 0 6px; padding: 10px 12px;
         background: #f4f6f8; border: 1px solid #e0e5ea; border-radius: 6px; }
.facts div { font-size: 12px; }
.facts b { display: block; font-weight: 600; color: #5b6570; font-size: 10px;
           text-transform: uppercase; letter-spacing: .06em; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { padding: 3px 6px; border-bottom: 1px solid #e6eaee; text-align: left; white-space: nowrap; }
th { background: #eef1f4; font-weight: 600; font-size: 11px; text-transform: uppercase;
     letter-spacing: .04em; color: #49525c; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tr:nth-child(even) td { background: #fafbfc; }
.red { color: #a4262c; font-weight: 600; }
.blue { color: #1c4f9c; font-weight: 600; }
.ok { color: #14713a; font-weight: 700; }
.bad { color: #a4262c; font-weight: 700; }
.mine td { background: #fff6da !important; }
.bar { display: inline-block; height: 8px; background: #b9c4d0; border-radius: 4px; vertical-align: middle; }
ul.summary { margin: 6px 0 0 0; padding-left: 18px; }
ul.summary li { margin: 2px 0; }
.wrap { max-width: 1100px; margin: 0 auto; }
footer { margin-top: 28px; color: #8a949e; font-size: 11px; border-top: 1px solid #e6eaee; padding-top: 8px; }
footer a { color: #1c4f9c; }
@media print {
  body { padding: 0; font-size: 11px; }
  h2 { margin-top: 16px; }
  table { font-size: 10px; page-break-inside: auto; }
  tr { page-break-inside: avoid; }
  .facts { background: none; }
}
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _color_span(c: Optional[str]) -> str:
    if c == "RED":
        return '<span class="red">RED</span>'
    if c == "BLUE":
        return '<span class="blue">BLUE</span>'
    return _e(c or "-")


def _html_table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset(),
                row_classes: Optional[list[str]] = None) -> str:
    buf = io.StringIO()
    buf.write("<table><thead><tr>")
    for i, h in enumerate(headers):
        buf.write(f'<th class="{"n" if i in numeric else ""}">{_e(h)}</th>')
    buf.write("</tr></thead><tbody>")
    for j, r in enumerate(rows):
        cls = row_classes[j] if row_classes else ""
        buf.write(f'<tr class="{cls}">' if cls else "<tr>")
        for i, c in enumerate(r):
            buf.write(f'<td class="{"n" if i in numeric else ""}">{c}</td>')
        buf.write("</tr>")
    buf.write("</tbody></table>")
    return buf.getvalue()


def html_report(session) -> str:
    """The printable page as a string (used by export_html; handy for tests)."""
    rep = _analysis.analyze(session)
    mine = session.my_team
    b = io.StringIO()
    b.write("<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>")
    b.write("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    b.write(f"<title>{_e(APP_FULL_TITLE)} - {_e(session.event_name)}</title>")
    b.write(f"<style>{_CSS}</style></head><body>")
    b.write(
        "<div class='brandbar'>"
        f"<img class='mark' width='34' height='34' alt='{_e(APP_FULL_TITLE)}' "
        f"src='data:image/png;base64,{_LOGO_PNG_B64_48}'>"
        f"<div><div class='word'>{_e(APP_TITLE_PREFIX)} <span class='auto'>{_e(APP_NAME)}</span></div>"
        f"<div class='tagline'>{_e(APP_TAGLINE)}</div></div>"
        "</div>")
    b.write("<div class='wrap'>")

    b.write(f"<h1>{_e(session.event_name)}</h1>")
    b.write(f"<p class='sub'>{_e(APP_FULL_TITLE)} {_e(__version__)} match predictions</p>")
    b.write("<div class='facts'>")
    for k, v in _header_facts(session, rep):
        b.write(f"<div><b>{_e(k)}</b>{_e(v)}</div>")
    b.write("</div>")

    b.write("<h2>Matches</h2>")
    rows, classes = [], []
    for p in session.predictions:
        m = p.match
        ok = "" if p.correct is None else (
            "<span class='ok'>&#10004;</span>" if p.correct else "<span class='bad'>&#10008;</span>")
        actual = _color_span(m.winner) if m.winner else "-"
        score = f"{m.red_score:.0f}–{m.blue_score:.0f}" if m.red_score is not None else "-"
        pct = p.red_cow * 100
        bar = (f"<span class='bar' style='width:{pct * 0.6:.0f}px'></span> "
               f"{p.red_cow:.0%} / {p.blue_cow:.0%}")
        rows.append([_e(m.label),
                     _e(" ".join(str(n) for n in m.red)),
                     _e(" ".join(str(n) for n in m.blue)),
                     f"{p.red_pts:.1f}", f"{p.blue_pts:.1f}", bar,
                     _color_span(p.winner), actual, _e(score), ok])
        classes.append("mine" if mine and mine in m.teams else "")
    b.write(_html_table(["Match", "Red", "Blue", "Red pts", "Blue pts", "Chance of winning",
                         "Predicted", "Actual", "Score", ""],
                        rows, numeric={3, 4}, row_classes=classes))

    lab = session.settings.basis
    b.write(f"<h2>Teams by {lab}</h2>")
    rows, classes = [], []
    for i, t in enumerate(session.teams_by_strength(), 1):
        rows.append([str(i), _e(t.number), _e(t.name), f"{t.opr:.1f}", f"{t.auto:.1f}",
                     f"{t.teleop:.1f}", f"{t.endgame:.1f}", f"{t.new_avg:.1f}",
                     (f"{t.proj_opr:.1f}" if t.proj_opr is not None else "n/a"),
                     f"<b>{t.cap:.1f}</b>", _f(t.epa, 1), _e(t.tdc_issue or ""),
                     _e(t.focus if t.focus != "performance" else "")])
        classes.append("mine" if mine == t.number else "")
    b.write(_html_table(["#", "Team", "Name", "OPR", "Auto", "Teleop", "Endgame", "NEW AVG",
                         "Projected OPR", "CAP", "EPA", "Technical issue", "Focus"],
                        rows, numeric={0, 1, 3, 4, 5, 6, 7, 8, 9, 10}, row_classes=classes))

    b.write("<h2>Projected standings</h2>")
    rows, classes = [], []
    for r in session.table:
        rows.append([str(r.rank), _e(r.number), _e(r.name), f"{r.wins}–{r.losses}",
                     str(r.scheduled), f"{r.rp:.2f}", f"{r.tbp1:.1f}", f"{r.tbp2:.1f}",
                     "captain" if r.captain_rank else ""])
        classes.append("mine" if mine == r.number else "")
    b.write(_html_table(["Rank", "Team", "Name", "W–L", "Matches", "Ranking points", "Auto tiebreaker points", "Endgame tiebreaker points", ""],
                        rows, numeric={0, 1, 4, 5, 6, 7}, row_classes=classes))

    if mine and mine in session.teams:
        info = session.my_summary()
        b.write(f"<h2>Team {_e(mine)}</h2>")
        rows = [[_e(x.prediction.match.label), _color_span(x.color),
                 _e(" ".join(str(n) for n in x.partner)),
                 _e(" ".join(str(n) for n in x.opponents)),
                 f"{x.cow:.0%}", str(x.rating),
                 "Win" if x.predicted_win else "Loss", _e(x.actual or "-")]
                for x in info["matches"]]
        b.write(_html_table(["Match", "Alliance", "With", "Against", "Chance of winning",
                             "Match rating", "Predicted", "Actual"], rows, numeric={4, 5}))
        b.write(f"<p class='sub'>Predicted wins {info['wins']}/{len(info['matches'])} &middot; "
                f"old RP {info['old_rp']} &middot; projected rank {info['rank']}</p>")

    alli = session.alliance_report()
    if alli.table:
        b.write("<h2>Best alliances</h2>")
        b.write(f"<p class='sub'>{len(alli.table)} possible two-team alliances &middot; "
                f"{len(alli.unbeatable)} beat every alliance they could face"
                + (f" &middot; team {_e(alli.my_team)} is rank {_e(alli.my_rank or '-')} "
                   f"with {lab} {alli.my_cap:.1f}, pick must be above {alli.bar:.1f}"
                   if alli.bar is not None else "") + "</p>")
        rows, classes = [], []
        for a in alli.table[:PRINTED_ALLIANCES]:
            rows.append([str(a.rank), _e(a.label), _e(" / ".join(n for n in a.names if n)),
                         f"{a.cap_sum:.1f}", f"{a.beats} / {a.legal}", f"{a.win_rate:.1%}",
                         _e(" + ".join(str(n) for n in a.closest)), f"{a.margin:+.1f}",
                         f"{a.cow:.0%}",
                         "<span class='ok'>UNBEATABLE</span>" if a.unbeatable else ""])
            classes.append("mine" if mine and mine in a.teams else "")
        b.write(_html_table(["#", "Alliance", "Teams", f"Total {lab}", "Beats", "Win rate",
                             "Closest opponent", "Margin", "Chance of winning", ""],
                            rows, numeric={0, 3, 4, 5, 7, 8}, row_classes=classes))

        b.write("<h2>Alliance captains</h2>")
        b.write(_html_table(["Cpt.", "Team", "Name", lab, "Best available", "Likely pick",
                             f"Pick {lab}", "Projected total", "Pick above"],
                            [[str(s.rank), _e(s.team), _e(s.name), f"{s.cap:.1f}",
                              f"{s.best_available:.1f}", _e(s.pick or "-"), f"{s.pick_cap:.1f}",
                              f"{s.projected_total:.1f}", f"{s.bar:.1f}"] for s in alli.seats],
                            numeric={0, 1, 3, 4, 5, 6, 7, 8},
                            row_classes=["mine" if mine == s.team else "" for s in alli.seats]))

        if alli.partners:
            b.write(f"<h2>Teams {_e(alli.my_team)} could win with</h2>")
            b.write(_html_table(["Partner", "Name", f"Their {lab}", f"Total {lab}", "Beats",
                                 "Win rate", "Chance of winning", "Margin", ""],
                                [[_e(a.partner_of(alli.my_team)), _e(a.partner_name(alli.my_team)),
                                  f"{a.partner_cap(alli.my_team):.1f}", f"{a.cap_sum:.1f}",
                                  f"{a.beats} / {a.legal}", f"{a.win_rate:.1%}", f"{a.cow:.0%}",
                                  f"{a.margin:+.1f}",
                                  "<span class='ok'>UNBEATABLE</span>" if a.unbeatable else ""]
                                 for a in alli.partners[:PRINTED_ALLIANCES]],
                                numeric={0, 2, 3, 4, 5, 6, 7}))

    b.write("<h2>Analysis</h2><ul class='summary'>")
    for line in rep.summary_lines:
        b.write(f"<li>{_e(line)}</li>")
    b.write("</ul>")
    if rep.wrong:
        b.write("<h2>Wrong calls</h2>")
        b.write(_html_table(["Match", "Predicted", "Actual", "Red pts", "Blue pts",
                             "How far off", "Looks like"],
                            [[_e(x.match.label), _color_span(x.prediction.winner),
                              _color_span(x.match.winner),
                              f"{x.prediction.red_pts:.1f}", f"{x.prediction.blue_pts:.1f}",
                              _e(x.suggestion), _e(x.classification)]
                             for x in rep.wrong], numeric={3, 4}))

    b.write(f"<footer>{_e(APP_FULL_TITLE)} {_e(__version__)} - {lab}, chance of winning and the "
            f"analysis suggestions are ported from the AutoScout v1.3 spreadsheets. "
            f"Chance of winning is a share of predicted points, not a calibrated probability."
            f"<br>{_e(APP_ORG_FULL)} · <a href='{_e(APP_WEBSITE)}'>{_e(APP_WEBSITE)}</a>"
            f"</footer></div></body></html>\n")
    return b.getvalue()


def export_html(session, path: str) -> str:
    """Write the self-contained printable page. Returns the path."""
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_report(session))
    return path


def export_text(session, path: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(printable_text(session))
    return path

"""A Session is one meet: what the user typed, what was fetched, and what was
computed. It serialises to a single JSON file so a meet can be prepared at home
and reopened at the venue with no connectivity.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable



@dataclass
class ManualTeam:
    """Per-team inputs the user controls (Technical Issues section + AutoSheet 'Team Focus')."""
    tdc_issue: str = ""
    tdc_delta: float = 0.0
    focus: str = "performance"
    note: str = ""
    # Data-input overrides (new in 2.2): None = use the value FTCScout gave.
    opr: Optional[float] = None
    auto: Optional[float] = None
    teleop: Optional[float] = None
    endgame: Optional[float] = None
    proj_opr: Optional[float] = None
    epa: Optional[float] = None


@dataclass
class Session:
    season: int = 2025
    event_code: str = ""
    my_team: int = 0
    settings: Settings = field(default_factory=Settings)
    manual: dict[int, ManualTeam] = field(default_factory=dict)
    pasted_schedule: str = ""                 # used when FTCScout has no schedule
    manual_results: dict[str, tuple] = field(default_factory=dict)   # label → (red, blue)
    extra_teams: list[int] = field(default_factory=list)
    # fetched
    event_info: dict = field(default_factory=dict)
    team_numbers: list[int] = field(default_factory=list)
    team_names: dict[int, str] = field(default_factory=dict)
    history: dict[int, list[EventStat]] = field(default_factory=dict)
    epa_events: list[EpaEvent] = field(default_factory=list)   # earlier events' results (EPA warm-up)
    api_matches: list[Match] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    live_stream_url: str = ""
    live_notes: dict[str, list[dict]] = field(default_factory=dict)
    loaded_at: str = ""
    path: str = ""
    # computed (not saved)
    teams: dict[int, TeamStats] = field(default_factory=dict, repr=False)
    matches: list[Match] = field(default_factory=list, repr=False)
    predictions: list[Prediction] = field(default_factory=list, repr=False)
    table: list[Standing] = field(default_factory=list, repr=False)
    strength_fit: Optional[StrengthFit] = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    @property
    def event_name(self) -> str:
        return self.event_info.get("name", "") or self.event_code

    @property
    def loaded(self) -> bool:
        return bool(self.team_numbers)

    def manual_for(self, team: int) -> ManualTeam:
        return self.manual.setdefault(int(team), ManualTeam())

    # ------------------------------------------------------------------ #
    def load(self, client: FTCScoutClient, progress: Optional[Callable[[str, float], None]] = None,
             with_history: bool = True) -> None:
        """Fetch (or re-fetch) everything from FTCScout, then compute."""
        b: EventBundle = load_event(client, self.season, self.event_code, progress, with_history,
                                    extra_teams=self.extra_teams, epa_future=self.settings.epa_future)
        self.event_code = b.code
        self.event_info = b.info
        self.team_numbers = b.team_numbers
        self.team_names = b.team_names
        self.history = b.history
        self.epa_events = b.epa_events
        self.api_matches = b.matches
        self.warnings = b.warnings
        import datetime
        self.loaded_at = datetime.datetime.now().isoformat(timespec="seconds")
        self.compute()

    def refresh_results(self, client: FTCScoutClient) -> int:
        """Re-fetch only the match list (cheap; for the live-meet loop). Returns matches played."""
        payload = client.event_matches(self.season, self.event_code)
        self.api_matches = matches_from_json(payload)
        self.compute()
        return sum(1 for m in self.matches if m.played)

    def compute(self) -> None:
        """Rebuild teams → matches → predictions → standings from current inputs."""
        s = self.settings
        self.teams = {}
        for n in self.team_numbers:
            t = team_stats_from_history(n, self.history.get(n, []), s, self.team_names.get(n, ""), self.event_code)
            m = self.manual.get(n)
            if m:
                t.tdc_issue, t.tdc_delta, t.focus = m.tdc_issue, m.tdc_delta, m.focus
                for key in ("opr", "auto", "teleop", "endgame", "proj_opr"):
                    v = getattr(m, key, None)
                    if v is not None:
                        setattr(t, key, float(v))
                        t.edited = True
            self.teams[n] = t
        # schedule: API first, pasted text as fallback; manual results override
        self.matches = [Match.from_dict(m.to_dict()) for m in self.api_matches] if self.api_matches \
            else parse_schedule_text(self.pasted_schedule)
        for mt in self.matches:
            r = self.manual_results.get(mt.label)
            if r:
                mt.played = True
                mt.red_score, mt.blue_score = float(r[0]), float(r[1])
                mt.red_score_np, mt.blue_score_np = mt.red_score, mt.blue_score
            for n in mt.teams:                      # teams on the schedule but not in the list
                if n not in self.teams:
                    self.teams[n] = TeamStats(number=n, name=self.team_names.get(n, ""), has_data=False)
        # EPA is always computed (the Teams tab shows it whatever the basis); the basis only
        # decides which rating the predictions and alliance tables are built from.
        cur_start = (self.event_info.get("start") or "")[:10]
        earlier = [e for e in self.epa_events
                   if e.code != self.event_code and not (cur_start and e.start and e.start >= cur_start)]
        later = ([e for e in self.epa_events
                  if e.code != self.event_code and cur_start and e.start and e.start >= cur_start]
                 if s.epa_future else [])
        epa_values, epa_model = build_epa(earlier, self.matches, s, later)
        basis = s.basis
        for n, t in self.teams.items():
            t.epa, t.epa_matches, t.basis = epa_model.value(n), epa_model.games(n), basis
        epa_over = {n: float(m.epa) for n, m in self.manual.items() if getattr(m, "epa", None) is not None}
        for n, v in epa_over.items():
            if n in self.teams:
                self.teams[n].epa, self.teams[n].edited = v, True
        if epa_over:
            epa_values = [(tuple(epa_over.get(a, r) for a, r in zip(mt.red, red)),
                           tuple(epa_over.get(b, bl) for b, bl in zip(mt.blue, blue)))
                          for mt, (red, blue) in zip(self.matches, epa_values)]
        self.epa_values = epa_values          # per-match, per-team EPA (the charts read these)
        self.epa_earlier = earlier
        self.epa_later = later
        self.predictions = (predict_from_values(self.matches, epa_values) if basis == "EPA"
                            else predict(self.matches, self.teams))
        self.table = standings(self.predictions, self.teams, s.matches_per_team)
        # Alliance Strength: every scheduled match's two alliances, run through the same
        # win-rate-vs-Total-CAP curve as the "All possible alliances" chart - fitted fresh
        # from this event's own alliance_table, not hard-coded (see fit_alliance_strength).
        self.strength_fit = fit_alliance_strength(alliance_table(self.teams))
        if self.strength_fit is not None:
            for p in self.predictions:
                p.red_strength = self.strength_fit.predict(p.red_pts)
                p.blue_strength = self.strength_fit.predict(p.blue_pts)

    def accuracy(self) -> tuple[int, int]:
        return accuracy(self.predictions)

    def my_matches(self) -> list[MyMatch]:
        return my_team_matches(self.predictions, self.my_team)

    def my_summary(self) -> dict:
        mm = self.my_matches()
        wins = sum(1 for x in mm if (x.actual == "Win") or (x.actual is None and x.predicted_win))
        t = self.teams.get(self.my_team)
        return {"team": self.my_team, "stats": t, "matches": mm, "wins": wins,
                "old_rp": old_rp(wins, len(mm)),
                "rank": next((r.rank for r in self.table if r.number == self.my_team), 0)}

    def alliance_report(self) -> AllianceReport:
        return alliance_report(self.teams, self.table, self.my_team)

    def teams_by_cap(self) -> list[TeamStats]:          # AutoSheet!C2 rankings
        return sorted(self.teams.values(), key=lambda t: t.cap, reverse=True)

    def teams_by_strength(self) -> list[TeamStats]:     # ranked by whichever rating drives the predictions
        return sorted(self.teams.values(), key=lambda t: t.strength, reverse=True)

    @property
    def basis(self) -> str:
        return self.settings.basis

    def epa_summary(self) -> str:
        """One line on what EPA has been fed (shown on the Settings tab)."""
        n_ev = len(self.epa_events)
        n_m = sum(len(e.matches) for e in self.epa_events)
        played = sum(1 for m in self.matches if m.played)
        if not n_ev and not played:
            return ("EPA has no match data yet: load a meet (with history) so it can warm up. "
                    "Until then every team is rated the same.")
        head = (f"EPA warmed up on {n_m} matches from {n_ev} earlier events"
                if n_ev else "EPA has no earlier-event history (load the meet with history to add it)")
        tail = (f"and is learning from the {played} matches played here." if self.settings.epa_live
                else f"and is frozen as of the start of this event ({played} matches played here are ignored).")
        return f"{head}, {tail}"

    def to_dict(self) -> dict:
        return {
            "app_version": __version__,
            "season": self.season, "event_code": self.event_code, "my_team": self.my_team,
            "settings": self.settings.to_dict(),
            "manual": {str(k): asdict(v) for k, v in self.manual.items()},
            "pasted_schedule": self.pasted_schedule,
            "manual_results": {k: list(v) for k, v in self.manual_results.items()},
            "extra_teams": self.extra_teams,
            "event_info": self.event_info,
            "team_numbers": self.team_numbers,
            "team_names": {str(k): v for k, v in self.team_names.items()},
            "history": {str(k): [asdict(e) for e in v] for k, v in self.history.items()},
            "api_matches": [m.to_dict() for m in self.api_matches],
            "epa_events": [e.to_dict() for e in self.epa_events],
            "warnings": self.warnings, "loaded_at": self.loaded_at,
            "live_stream_url": self.live_stream_url, "live_notes": self.live_notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        s = cls(season=int(d.get("season", 2025)), event_code=d.get("event_code", ""),
                my_team=int(d.get("my_team", 0) or 0))
        s.settings = Settings.from_dict(d.get("settings", {}))
        s.manual = {int(k): ManualTeam(**{kk: vv for kk, vv in v.items() if kk in ManualTeam.__dataclass_fields__})
                    for k, v in d.get("manual", {}).items()}
        s.pasted_schedule = d.get("pasted_schedule", "")
        s.manual_results = {k: tuple(v) for k, v in d.get("manual_results", {}).items()}
        s.extra_teams = [int(x) for x in d.get("extra_teams", [])]
        s.event_info = d.get("event_info", {})
        s.team_numbers = [int(x) for x in d.get("team_numbers", [])]
        s.team_names = {int(k): v for k, v in d.get("team_names", {}).items()}
        s.history = {int(k): [EventStat(**{kk: vv for kk, vv in e.items() if kk in EventStat.__dataclass_fields__}) for e in v]
                     for k, v in d.get("history", {}).items()}
        s.api_matches = [Match.from_dict(m) for m in d.get("api_matches", [])]
        s.epa_events = [EpaEvent.from_dict(e) for e in d.get("epa_events", [])]
        s.warnings = list(d.get("warnings", []))
        s.live_stream_url = str(d.get("live_stream_url", "") or "")
        s.live_notes = {str(k): [dict(n) for n in v if isinstance(n, dict)]
                        for k, v in (d.get("live_notes", {}) or {}).items() if isinstance(v, list)}
        s.loaded_at = d.get("loaded_at", "")
        s.compute()
        return s

    def save(self, path: Optional[str] = None) -> str:
        path = path or self.path or f"autoscout_{self.season}_{self.event_code or 'session'}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)
        self.path = path
        return path

    @classmethod
    def open(cls, path: str) -> "Session":
        with open(path, encoding="utf-8") as f:
            s = cls.from_dict(json.load(f))
        s.path = path
        return s


def load_fixture_session(fixture_dir: str, my_team: int = 12928, settings: Optional[Settings] = None) -> Session:
    """Build a Session from saved API payloads (tests, demos, offline smoke runs)."""
    pass
    with open(os.path.join(fixture_dir, "event.json")) as f:
        info = json.load(f)
    with open(os.path.join(fixture_dir, "teams.json")) as f:
        teams = json.load(f)
    with open(os.path.join(fixture_dir, "matches.json")) as f:
        matches = json.load(f)
    s = Session(season=int(info["season"]), event_code=info["code"], my_team=my_team,
                settings=settings or Settings())
    s.event_info = info
    for row in teams:
        n = int(row["teamNumber"])
        s.team_numbers.append(n)
        s.history[n] = [event_stat_from_json(n, s.season, s.event_code, row.get("stats"), info)]
    s.team_numbers.sort()
    s.api_matches = matches_from_json(matches)
    s.compute()
    return s

"""GUI preferences: a tiny JSON file next to the HTTP cache.

Holds only things the user would be annoyed to retype: the window geometry,
the last session file opened or saved, and the last Setup-tab entries. Never
holds scouting data - that lives in the session .json.
"""

import json
import os
from typing import Any


FILENAME = "gui_config.json"
_DEFAULTS: dict[str, Any] = {
    "geometry": "",
    "last_session": "",
    "last_dir": "",
    "season": 2025,
    "event_code": "",
    "my_team": 0,
    "offline": False,
}


def path() -> str:
    return os.path.join(default_cache_dir(), FILENAME)


def load() -> dict:
    """Read the preferences; never raises (a corrupt file is treated as empty)."""
    d = dict(_DEFAULTS)
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            d.update({k: v for k, v in data.items() if k in _DEFAULTS})
    except (OSError, ValueError):
        pass
    return d


def save(values: dict) -> None:
    """Merge `values` into the stored preferences; never raises."""
    d = load()
    d.update({k: v for k, v in values.items() if k in _DEFAULTS})
    try:
        p = path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


def get(key: str, default: Any = None) -> Any:
    return load().get(key, _DEFAULTS.get(key, default))

"""Tkinter desktop UI - the eight AutoScout spreadsheets as one window.

Each notebook tab reproduces one part of the original workbook set:

    Setup      AutoRegister form + Global_Var URLs + Meet Loading Data
    Teams      AutoSheet.Sheet1 + STAT PAGE.MAIN DATA + Technical Issues + PROJ. OPR
    Matches    Main-Calc.TED rows 48+ and AutoScout '1' rows 3..143
    My Team    AutoScout '1' scouting panel (R..AA)
    Standings  AutoScout.Sheet16
    Charts     one team over time, all teams (OPR vs CAP vs EPA), matches & alliances (2.2)
    Analysis   AutoScout '1'!A7 + Sheet18 + Analysis
    Settings   the faithful/corrected switches from STRATEGY.md section 8

Rules this file keeps to: standard library only, ttk widgets only (so a
PyInstaller --onefile --windowed build works on macOS, Windows and Linux with
stock Tk 8.6), no network call on the Tk thread, and no Tk call off it - worker
threads hand results back through `App.after`.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, Callable, Optional, Sequence


# Optional sibling modules (written separately); the GUI must run without them.
try:
    pass
except ImportError:          # pragma: no cover - depends on the other module
    analyze = None
try:
    pass
except ImportError:          # pragma: no cover
    export_csv = export_html = printable_text = None


NA = "n/a"
PUMP_MS = 50        # how often the Tk thread drains work posted by background threads


def f1(v: Optional[float]) -> str:
    """One decimal, or 'n/a'. Used for every points column."""
    return NA if v is None else f"{float(v):.1f}"


def pct(v: Optional[float]) -> str:
    """Whole-number percentage from a 0..1 fraction."""
    return "" if v is None else f"{float(v) * 100:.0f}%"


def sig(v: Optional[float]) -> str:
    """One decimal with an explicit sign (for CAP−OPR style deltas)."""
    return NA if v is None else f"{float(v):+.1f}"


def as_int(text: str, default: int = 0) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def as_float(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return default


def sort_key(value: str):
    """Sort a Treeview cell: numbers numerically, blanks/'n/a' last."""
    s = str(value).strip().replace("%", "").replace("+", "")
    if s in ("", NA, "-"):
        return (2, 0.0, "")
    try:
        return (0, float(s), "")
    except ValueError:
        return (1, 0.0, s.lower())


TRISTATE = ("Follow mode", "On", "Off")


def tristate_to_value(label: str) -> Optional[bool]:
    return {"On": True, "Off": False}.get(label)


def value_to_tristate(value: Optional[bool]) -> str:
    return "Follow mode" if value is None else ("On" if value else "Off")


CAP_EXPLAINER = """CAP: Calculated Average Points

Quick glossary: FTC means FIRST Tech Challenge; CAP means Calculated Average Points; OPR means Offensive Power Rating; EPA means Expected Points Added; RP means Ranking Points; MR means Match rating; EOPR means Event Offensive Power Rating; TBP1 means the autonomous tiebreaker points; TBP2 means the endgame tiebreaker points; AUTO means autonomous scoring; TELEOP means teleoperated scoring; ENDGAME means endgame scoring; AVG means average; PROJ means projected. Win chance means Chance of Winning.

CAP is AutoScout's single number for how many points a team is worth to its
alliance. It starts from the team's non-penalty OPR and nudges it with the
team's own scoring mix, so two teams with the same OPR are separated by how
they earn it.

The inputs (from the original scoring worksheet):
  OPR        Offensive Power Rating, adjusted to exclude penalties
  AUTO/ENDG  autonomous and endgame scoring components
  FTC AVG    (AUTO+TELEOP+ENDGAME averaged with OPR), adjusted for a known
             robot problem on the Technical Issues tab, plus your manual delta
  PROJ OPR   a straight-line extrapolation of the team's OPR across its events
  RS -/+     a small penalty when a team's focus is set to "award" rather than
             "performance"

A match prediction is just CAP(red 1) + CAP(red 2) against the same for blue,
and Win chance is the model's estimated probability that an alliance will win.
"""

CHARTS_EXPLAINER = """Charts (new in 2.2)

Teams tab → "Show charts ▸" opens a side menu for the selected team: its
statistics over the events it has played this season (OPR, CAP and EPA as
of each event, auto/teleop/endgame, rank, RP…) or over its matches at this
event (the EPA it carried into each match, predicted vs actual points,
chance of winning). Pick what goes on each axis; up to three Y series at
once. Underneath, "Edit this team's data inputs" lets you type your own
OPR, Auto, Teleop, Endgame, projected OPR or EPA - blank means FTCScout's
value. Overrides feed CAP, every prediction and every chart, are marked in
the table, and are saved with the session.

Charts tab:
  One team              the same chart with a team-number box.
  All teams             every team as a point. "Scatter": choose X, Y and an
                        optional Z (marker size and colour), e.g. OPR vs CAP
                        sized by EPA; the dashed x = y line is where the two
                        statistics agree. "Compare ratings": teams ordered by
                        one statistic with OPR, CAP and EPA drawn as lines -
                        where the lines sit together the methods converge and
                        the call is safer; the grey bars show the disagreement.
  Matches & alliances   the schedule's matches (predicted red vs blue points,
                        margin, chance of winning, actual scores, error), or
                        every possible two-team alliance / every partner for
                        your team (total rating, each member's rating, win
                        rate, margin over the closest opponent) - to see which
                        pairings are strongest and which are lopsided.

Hover a point for its details; click a point to open that team (or match).
"Save image…" writes the chart as a PostScript file.
"""

RATING_EXPLAINER = """Choosing what the predictions are based on (Settings tab)

  CAP   the AutoScout rating described above. The default.
  OPR   the team's plain non-penalty OPR from the same source event CAP starts from.
        Handy as a baseline: CAP-vs-OPR shows what the CAP formula adds.
  EPA   Expected Points Added, an online rating in points, calculated the way
        statcube.vercel.app/methodology does: each played match's score and margin
        "surprise" (a normal-CDF z-score, using Taylor's-law variances) nudges every team
        on the alliance, with big steps for a team's first matches and smaller ones
        later (k-factor 0.4 x season mean down to 0.2 x). It is warmed up on the earlier events
        this season's teams played, so it needs the meet loaded with history. With
        "keep learning" ticked it also updates through this event's own matches, and
        each match is called using only the matches played before it.

Whichever you pick is summed per alliance for every prediction, the accuracy count,
the projected standings and the alliance tables; the Teams tab shows all of them.
EPA is not an FTCScout statistic. The Taylor's-law constants (a, b) are StatCube's DECODE
fit and the season mean is learned from the matches seen unless Settings.epa_mean is set.
"""


class SortableTree(ttk.Frame):
    """A Treeview with scrollbars whose column headings sort the rows.

    `columns` is a list of (key, heading, width, anchor). Rows are given as
    (iid, values, tags) and remembered so a re-sort does not need the caller.
    """

    def __init__(self, master, columns: Sequence[tuple], height: int = 12, **kw):
        super().__init__(master, **kw)
        self.columns = list(columns)
        self.keys = [c[0] for c in self.columns]
        self._rows: list[tuple[str, tuple, tuple]] = []
        self._sort_col: Optional[str] = None
        self._sort_desc = False

        self.tree = ttk.Treeview(self, columns=self.keys, show="headings",
                                 height=height, selectmode="browse")
        for key, heading, width, anchor in self.columns:
            self.tree.heading(key, text=heading, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, minwidth=30, anchor=anchor, stretch=False)
        ysb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # Row colours. Tags define disjoint options so they can be combined.
        self.tree.tag_configure("ok", background="#dff2d8")
        self.tree.tag_configure("bad", background="#f9dcdc")
        self.tree.tag_configure("pending", background="")
        self.tree.tag_configure("mine", foreground="#12408f")
        self.tree.tag_configure("dim", foreground="#8a8a8a")
        self.tree.tag_configure("edited", foreground="#8a4b00")
        # The selected row is drawn bold (its colours come from the Treeview style map).
        import tkinter.font as _tkfont
        spec = ttk.Style(self).lookup("Treeview", "font") or "TkDefaultFont"
        self._selected_font = _tkfont.Font(self, font=spec)
        self._selected_font.configure(weight="bold")
        self.tree.tag_configure("selectedrow", font=self._selected_font)
        self.tree.bind("<<TreeviewSelect>>", self._sync_selected_tag, add="+")

    def _sync_selected_tag(self, _e=None) -> None:
        chosen = set(self.tree.selection())
        for iid in self.tree.get_children(""):
            tags = list(self.tree.item(iid, "tags"))
            has = "selectedrow" in tags
            if iid in chosen and not has:
                self.tree.item(iid, tags=tags + ["selectedrow"])
            elif has and iid not in chosen:
                self.tree.item(iid, tags=[t for t in tags if t != "selectedrow"])

    def set_rows(self, rows: Sequence[tuple[str, tuple, tuple]]) -> None:
        self._rows = [(str(i), tuple(v), tuple(t)) for i, v, t in rows]
        self._redraw()

    def _redraw(self) -> None:
        selected = self.selection()
        self.tree.delete(*self.tree.get_children())
        rows = list(self._rows)
        if self._sort_col in self.keys:
            idx = self.keys.index(self._sort_col)
            rows.sort(key=lambda r: sort_key(r[1][idx]), reverse=self._sort_desc)
        for iid, values, tags in rows:
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)
        stripe_treeview_rows(self.tree)
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def sort_by(self, key: str) -> None:
        self._sort_desc = not self._sort_desc if self._sort_col == key else False
        self._sort_col = key
        for k, heading, _, _ in self.columns:
            arrow = "" if k != key else ("  ▼" if self._sort_desc else "  ▲")
            self.tree.heading(k, text=heading + arrow)
        self._redraw()

    def show_columns(self, keys: Sequence[str]) -> None:
        self.tree.configure(displaycolumns=list(keys))

    def set_heading(self, key: str, text: str) -> None:
        """Rename a column heading (keeps the sort arrow if that column is the sort key)."""
        for i, c in enumerate(self.columns):
            if c[0] == key:
                self.columns[i] = (key, text) + tuple(c[2:])
                arrow = "" if key != self._sort_col else ("  ▼" if self._sort_desc else "  ▲")
                self.tree.heading(key, text=text + arrow)
                return

    def selection(self) -> str:
        sel = self.tree.selection()
        return sel[0] if sel else ""

    def select(self, iid: str) -> None:
        if iid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def bind_select(self, fn: Callable[[str], None]) -> None:
        self.tree.bind("<<TreeviewSelect>>", lambda _e: fn(self.selection()), add="+")

    def bind_double(self, fn: Callable[[str], None]) -> None:
        self.tree.bind("<Double-1>", lambda _e: fn(self.selection()))


class Tab(ttk.Frame):
    """Base class: every tab knows the App and can rebuild itself from the session."""

    title = "Tab"

    def __init__(self, app: "App"):
        super().__init__(app.nb, padding=8)
        self.app = app
        self.build()

    @property
    def session(self) -> Session:
        return self.app.session

    def build(self) -> None:          # pragma: no cover - overridden
        raise NotImplementedError

    def refresh(self) -> None:        # pragma: no cover - overridden
        pass



class CompetitionDayTab(Tab):
    """Fast, read-only event-floor dashboard with one-click scouting actions."""
    title = "Competition Day"

    def build(self) -> None:
        self.configure(padding=16)
        hero = tk.Frame(self, bg=THEME_BG, padx=20, pady=15)
        hero.pack(fill="x", pady=(0, 12))
        tk.Label(hero, text="COMPETITION DAY", bg=THEME_BG, fg=THEME_ACCENT_TEXT,
                 font=(self.app.title_family, 9, "bold")).pack(anchor="w")
        tk.Label(hero, text="Everything you need between matches.",
                 bg=THEME_BG, fg="#ffffff", font=(self.app.title_family, 20, "bold")).pack(anchor="w", pady=(3, 2))
        self.event_line = tk.Label(hero, text="Load an event to populate your match desk.",
                                   bg=THEME_BG, fg="#ffffff", font=(self.app.body_family, 10))
        self.event_line.pack(anchor="w")

        metrics = ttk.Frame(self); metrics.pack(fill="x", pady=(0, 12))
        self.metric_vars = [tk.StringVar(value="-") for _ in range(5)]
        labels = ("EVENT TEAMS", "MATCHES", "PLAYED", "UP NEXT", "MY TEAM")
        for i, (lab, var) in enumerate(zip(labels, self.metric_vars)):
            card = tk.Frame(metrics, bg="#ffffff", highlightbackground=UI_BORDER,
                            highlightthickness=1, padx=12, pady=10)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 7, 0))
            tk.Label(card, text=lab, bg="#ffffff", fg=UI_MUTED,
                     font=(self.app.title_family, 8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=var, bg="#ffffff", fg=UI_TEXT,
                     font=(self.app.title_family, 18, "bold")).pack(anchor="w", pady=(4, 0))
            metrics.columnconfigure(i, weight=1)

        actions = ttk.Frame(self); actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="Refresh event data", command=self._refresh_event).pack(side="left")
        ttk.Button(actions, text="Open live scouting", command=lambda: self._go("Live Scouting")).pack(side="left", padx=5)
        ttk.Button(actions, text="Alliance selection", command=lambda: self._go("Alliances")).pack(side="left")
        ttk.Button(actions, text="Team research", command=lambda: self._go("Team Research")).pack(side="left", padx=5)
        ttk.Button(actions, text="Save session", command=self.app.do_save).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal"); body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=(0, 0, 8, 0)); right = ttk.Frame(body, padding=(8, 0, 0, 0))
        body.add(left, weight=3); body.add(right, weight=2)
        lf = ttk.LabelFrame(left, text="Match desk", padding=8); lf.pack(fill="both", expand=True)
        self.match_tree = ttk.Treeview(lf, columns=("match","red","blue","forecast","status"),
                                       show="headings", height=16)
        for c, label, width in (("match","MATCH",90),("red","RED ALLIANCE",190),
                                ("blue","BLUE ALLIANCE",190),("forecast","WIN CHANCE",110),
                                ("status","STATUS",85)):
            self.match_tree.heading(c, text=label); self.match_tree.column(c, width=width, anchor="w")
        y = ttk.Scrollbar(lf, orient="vertical", command=self.match_tree.yview)
        self.match_tree.configure(yscrollcommand=y.set)
        self.match_tree.pack(side="left", fill="both", expand=True); y.pack(side="right", fill="y")
        self.match_tree.bind("<<TreeviewSelect>>", self._select_match)
        rf = ttk.LabelFrame(right, text="Selected match", padding=12); rf.pack(fill="both", expand=True)
        self.detail_title = tk.StringVar(value="Select a match")
        ttk.Label(rf, textvariable=self.detail_title, style="Card.TLabel",
                  font=(self.app.title_family, 15, "bold")).pack(anchor="w")
        self.detail_text = tk.Text(rf, height=12, wrap="word", relief="flat",
                                   bg="#f5f7fb", fg=UI_TEXT, padx=10, pady=10,
                                   font=(self.app.body_family, 10), state="disabled")
        self.detail_text.pack(fill="both", expand=True, pady=8)
        ttk.Button(rf, text="Scout this match", command=self._scout_selected).pack(anchor="e")
        self.refresh()

    def _go(self, name):
        self.app.nb.select_name(name)

    def _refresh_event(self):
        try:
            self.app.setup_tab.do_load()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not refresh event data.\n\n{e}", parent=self)

    def refresh(self) -> None:
        s = self.session
        matches = list(s.matches or [])
        predictions = {p.match.label: p for p in (s.predictions or [])}
        played = sum(bool(m.played) for m in matches)
        upcoming = next((m for m in matches if not m.played), None)
        self.event_line.configure(text=f"{s.event_name or 'No event loaded'}  •  Season {s.season}  •  {s.event_code or 'No event code'}")
        vals = (str(len(s.teams)), str(len(matches)), f"{played}/{len(matches)}",
                upcoming.label if upcoming else ("Complete" if matches else "-"),
                str(s.my_team or "Not set"))
        for v, value in zip(self.metric_vars, vals): v.set(value)
        self.match_tree.delete(*self.match_tree.get_children())
        for m in matches:
            p = predictions.get(m.label)
            red = " · ".join(str(t) for t in m.red)
            blue = " · ".join(str(t) for t in m.blue)
            if p:
                red_chance = p.red_cow * 100
                forecast = f"R {red_chance:.0f}% / B {100-red_chance:.0f}%"
            else: forecast = "-"
            status = "Played" if m.played else "Upcoming"
            self.match_tree.insert("", "end", iid=m.label, values=(m.label, red, blue, forecast, status))
        if matches and upcoming and self.match_tree.exists(upcoming.label):
            self.match_tree.selection_set(upcoming.label); self.match_tree.see(upcoming.label)
            self._show_match(upcoming.label)

    def _select_match(self, _event=None):
        selected = self.match_tree.selection()
        if selected: self._show_match(selected[0])

    def _show_match(self, label):
        s = self.session
        m = next((x for x in s.matches if x.label == label), None)
        if not m: return
        p = next((x for x in s.predictions if x.match.label == label), None)
        self.detail_title.set(label)
        def team_line(nums):
            return "\n".join(f"  {n}  {s.team_names.get(n, 'Team name unavailable')}" for n in nums)
        lines = [f"RED ALLIANCE\n{team_line(m.red)}", "", f"BLUE ALLIANCE\n{team_line(m.blue)}"]
        if p:
            lines += ["", f"Predicted score:  Red {p.red_pts:.1f}  -  Blue {p.blue_pts:.1f}",
                      f"Win probability:  Red {p.red_cow:.1%}  |  Blue {p.blue_cow:.1%}"]
        if m.played and m.red_score is not None and m.blue_score is not None:
            lines += ["", f"ACTUAL RESULT:  Red {m.red_score:g}  -  Blue {m.blue_score:g}"]
        notes = s.live_notes.get(label, [])
        lines += ["", f"SCOUTING NOTES: {len(notes)} saved"]
        self.detail_text.configure(state="normal"); self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines)); self.detail_text.configure(state="disabled")

    def _scout_selected(self):
        selected = self.match_tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a match first.", parent=self); return
        live = self.app.live_tab
        live.match_var.set(selected[0])
        live.refresh_notes()
        self._go("Live Scouting")


class HomeTab(Tab):
    """Competition command center: at-a-glance session health and quick actions."""
    title = "Overview"

    def build(self) -> None:
        self.configure(padding=18)
        hero = tk.Frame(self, bg=THEME_BG, padx=24, pady=20)
        hero.pack(fill="x", pady=(0, 14))
        tk.Label(hero, text="COMPETITION COMMAND CENTER", bg=THEME_BG, fg=THEME_ACCENT_TEXT,
                 font=(self.app.title_family, 9, "bold")).pack(anchor="w")
        tk.Label(hero, text="Scout smarter. Pick stronger alliances.", bg=THEME_BG, fg="#ffffff",
                 font=(self.app.title_family, 22, "bold")).pack(anchor="w", pady=(5, 3))
        tk.Label(hero, text="Your match data, team intelligence, and strategy tools - together in one workspace.",
                 bg=THEME_BG, fg=THEME_ACCENT_TEXT, font=(self.app.body_family, 10)).pack(anchor="w")
        metrics = ttk.Frame(self)
        metrics.pack(fill="x", pady=(0, 14))
        self.home_metric_vars = [tk.StringVar(value="-") for _ in range(4)]
        self.home_metric_labels = ["TEAMS TRACKED", "MATCHES LOADED", "MY TEAM", "SESSION STATUS"]
        for i, (label, var) in enumerate(zip(self.home_metric_labels, self.home_metric_vars)):
            card = tk.Frame(metrics, bg="#ffffff", highlightbackground=UI_BORDER, highlightthickness=1, padx=16, pady=13)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 9, 0))
            tk.Label(card, text=label, bg="#ffffff", fg=UI_MUTED, font=(self.app.title_family, 8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=var, bg="#ffffff", fg=UI_TEXT, font=(self.app.title_family, 20, "bold")).pack(anchor="w", pady=(5, 0))
            metrics.columnconfigure(i, weight=1)
        content = ttk.Frame(self)
        content.pack(fill="both", expand=True)
        left = ttk.Frame(content)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(content)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        actions = ttk.LabelFrame(left, text="QUICK ACTIONS", padding=14)
        actions.pack(fill="both", expand=True)
        self._action_button(actions, "01   Load an event", "Bring in teams, schedule, and match results.", self._go("Setup"), "Accent.TButton")
        self._action_button(actions, "02   Scout a match", "Review matchups and enter your observations.", self._go("Matches"))
        self._action_button(actions, "03   Research any team", "Explore public season data, records, and performance graphs.", self._go("Team Research"))
        self._action_button(actions, "04   Build an alliance", "Compare team strengths and identify promising combinations.", self._go("Alliances"))
        self._action_button(actions, "05   Open live scouting", "Keep match notes organized while following a stream.", self._go("Live Scouting"))
        info = ttk.LabelFrame(right, text="WORKFLOW", padding=14)
        info.pack(fill="both", expand=True)
        steps = [("01", "Set up your event", "Choose a season, event, and your team."),
                 ("02", "Build your dataset", "Load event data and add reliable pit or match observations."),
                 ("03", "Understand performance", "Use charts, CAP, EPA, and match analysis together."),
                 ("04", "Make a strategy call", "Compare alliances and validate predictions against real results.")]
        for n, title, desc in steps:
            row = tk.Frame(info, bg="#ffffff")
            row.pack(fill="x", pady=8)
            tk.Label(row, text=n, bg="#fbe9ee", fg="#b92e4b", font=(self.app.title_family, 10, "bold"), padx=10, pady=8).pack(side="left", padx=(0, 10))
            col = tk.Frame(row, bg="#ffffff")
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=title, bg="#ffffff", fg=UI_TEXT, font=(self.app.title_family, 10, "bold")).pack(anchor="w")
            tk.Label(col, text=desc, bg="#ffffff", fg=UI_MUTED, font=(self.app.body_family, 9), wraplength=390, justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Label(info, text="Tip: use Ctrl+K to jump to any workspace.", foreground=UI_MUTED).pack(anchor="w", pady=(12, 0))

    def _go(self, title):
        def jump():
            for idx, frame, name in self.app.nb._pages:
                if name == title:
                    self.app.nb.select(idx); break
        return jump

    def _action_button(self, parent, title, desc, command, style="TButton"):
        row = tk.Frame(parent, bg="#ffffff")
        row.pack(fill="x", pady=5)
        ttk.Button(row, text=title, command=command, style=style).pack(fill="x", anchor="w")
        ttk.Label(row, text=desc, foreground=UI_MUTED, wraplength=520).pack(anchor="w", padx=3, pady=(3, 0))

    def refresh(self) -> None:
        if not hasattr(self, "home_metric_vars"): return
        s = self.session
        vals = [str(len(s.teams)), str(len(s.matches)), str(s.my_team or "Not set"), "Loaded" if s.loaded else "Ready to start"]
        for var, val in zip(self.home_metric_vars, vals): var.set(val)


class SetupTab(Tab):
    """Replaces the AutoRegister Google Form and the sheet-URL plumbing.

    Everything the old system needed a registration round-trip for - season,
    meet code, your team, the team list - is typed here, and the pasted
    schedule box stands in for MEET LOADING DATA's IMPORTXML scrape.
    """

    title = "Setup"

    def build(self) -> None:
        self.season_var = tk.StringVar(value=str(config.get("season", 2025)))
        self.event_var = tk.StringVar(value=config.get("event_code", ""))
        self.team_var = tk.StringVar(value=str(config.get("my_team", 0) or ""))
        self.extra_var = tk.StringVar()
        self.offline_var = tk.BooleanVar(value=bool(config.get("offline", False)))
        self.history_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready.")
        self.progress_var = tk.DoubleVar(value=0.0)

        form = ttk.LabelFrame(self, text="Meet", padding=12)
        form.pack(fill="x")
        ttk.Label(form, foreground=UI_MUTED,
                  text="Enter your event details below, then press Load.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        # The three fields every user needs, always visible.
        rows = [("Season", self.season_var, 8), ("Event code", self.event_var, 18),
                ("My team number", self.team_var, 10)]
        entries = {}
        for i, (label, var, width) in enumerate(rows):
            r = i + 1
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=3)
            e = ttk.Entry(form, textvariable=var, width=width)
            e.grid(row=r, column=1, sticky="w", pady=3)
            entries[label] = e
        ttk.Label(form, text="e.g. FTCCMP1JACK or USTXCLRM1", foreground=UI_MUTED).grid(
            row=2, column=2, sticky="w", padx=8)
        # Changing "my team" here should repaint My Team / Teams straight away.
        team_entry = entries["My team number"]
        team_entry.bind("<Return>", lambda _e: self.apply_my_team())
        team_entry.bind("<FocusOut>", lambda _e: self.apply_my_team())

        # Everything else is optional and hidden by default - a first-time
        # user only needs the three fields above to get going.
        adv = Collapsible(form, text="Advanced options")
        adv.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(adv.body, text="Extra team numbers (optional)").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(adv.body, textvariable=self.extra_var, width=34).grid(row=0, column=1, sticky="w")
        offline_chk = ttk.Checkbutton(adv.body, text="Offline (cache only - never touch the network)",
                                       variable=self.offline_var)
        offline_chk.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        Tooltip(offline_chk, "Use only what's already saved locally. Nothing is\n"
                              "downloaded - good for spotty wifi at a meet.")
        history_chk = ttk.Checkbutton(adv.body, text="Fetch each team's season history (needed for projected OPR)",
                                       variable=self.history_var)
        history_chk.grid(row=2, column=0, columnspan=3, sticky="w")
        Tooltip(history_chk, "Downloads each team's past matches so AutoScout can\n"
                              "estimate OPR for teams with little or no data yet.\n"
                              "Leave this on unless the load is too slow.")

        paste = ttk.LabelFrame(self, text="Paste schedule (used when FTCScout has no schedule)", padding=12)
        paste.pack(fill="both", expand=True, pady=8)
        ttk.Label(paste, foreground=UI_MUTED, justify="left", text=(
            "One match per line - the first two team numbers are red, the next two blue.\n"
            "    Q-1  11225 19013  19564 23213\n"
            "    1    11225 19013 vs 19564 23213   104-66\n"
            "A trailing red-blue score marks the match as played.")
        ).pack(anchor="w")
        box = ttk.Frame(paste)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.schedule_text = tk.Text(box, height=8, wrap="none", undo=True)
        style_text_selection(self.schedule_text)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.schedule_text.yview)
        self.schedule_text.configure(yscrollcommand=sb.set)
        self.schedule_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        ttk.Button(paste, text="Use pasted schedule", command=self.apply_schedule).pack(anchor="w", pady=(4, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.load_btn = ttk.Button(bar, text="Load event data", style="Accent.TButton",
                                    command=self.do_load)
        self.load_btn.pack(side="left")
        ttk.Button(bar, text="Open session…", command=self.app.do_open).pack(side="left", padx=8)
        ttk.Button(bar, text="Save session…", command=self.app.do_save_as).pack(side="left")
        self.progress = ttk.Progressbar(bar, variable=self.progress_var, maximum=1.0, length=220)
        self.progress.pack(side="right")
        ttk.Label(self, textvariable=self.status_var, foreground=UI_MUTED).pack(anchor="w", pady=(6, 0))

        info = ttk.LabelFrame(self, text="Loaded event", padding=12)
        info.pack(fill="both", expand=True, pady=(8, 0))
        self.info_text = tk.Text(info, height=8, wrap="word", state="disabled")
        style_text_selection(self.info_text)
        isb = ttk.Scrollbar(info, orient="vertical", command=self.info_text.yview)
        self.info_text.configure(yscrollcommand=isb.set)
        self.info_text.pack(side="left", fill="both", expand=True)
        isb.pack(side="left", fill="y")

    def refresh(self) -> None:
        s = self.session
        # Only sync the entries from a real session; otherwise leave the remembered
        # values (and anything the user is halfway through typing) alone.
        if s.event_code or s.loaded:
            self.season_var.set(str(s.season))
            self.event_var.set(s.event_code)
            self.team_var.set(str(s.my_team) if s.my_team else "")
            self.extra_var.set(" ".join(str(n) for n in s.extra_teams))
        if s.pasted_schedule and s.pasted_schedule != self.schedule_text.get("1.0", "end-1c"):
            self.schedule_text.delete("1.0", "end")
            self.schedule_text.insert("1.0", s.pasted_schedule)
        self._write_info()

    def apply_my_team(self) -> None:
        n = as_int(self.team_var.get(), self.session.my_team)
        if n != self.session.my_team:
            self.session.my_team = n
            self.app.refresh_all()

    def _write_info(self) -> None:
        s = self.session
        i = s.event_info
        lines = []
        if s.loaded or i:
            lines.append(f"{s.event_name}  ({s.season}/{s.event_code})")
            where = " ".join(x for x in (i.get("venue") or "", i.get("city") or "", i.get("state") or "") if x)
            when = " → ".join(x for x in ((i.get("start") or "")[:10], (i.get("end") or "")[:10]) if x)
            if when or i.get("type"):
                lines.append(f"{when}    {i.get('type', '')}".strip())
            if where:
                lines.append(where)
            lines.append(f"{len(s.team_numbers)} teams, {len(s.matches)} matches "
                         f"({sum(1 for m in s.matches if m.played)} played)")
            if s.loaded_at:
                lines.append(f"Loaded {s.loaded_at.replace('T', ' ')}")
            if s.path:
                lines.append(f"File: {s.path}")
        else:
            lines.append("Nothing loaded yet. Fill in the meet above and press "
                         "“Load event data”, or open a saved session.")
        if s.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"  • {w}" for w in s.warnings)
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", "\n".join(lines))
        self.info_text.configure(state="disabled")

    def pull_into_session(self) -> None:
        """Copy the form fields into the session (without fetching)."""
        s = self.session
        s.season = as_int(self.season_var.get(), s.season)
        s.event_code = self.event_var.get().strip().upper() or s.event_code
        s.my_team = as_int(self.team_var.get(), s.my_team)
        s.extra_teams = parse_team_list(self.extra_var.get())
        s.pasted_schedule = self.schedule_text.get("1.0", "end-1c").strip()

    def apply_schedule(self) -> None:
        self.pull_into_session()
        self.session.api_matches = []          # pasted schedule wins once asked for
        self.app.recompute()
        n = len(self.session.matches)
        self.status_var.set(f"Parsed {n} matches from the pasted schedule."
                            if n else "No matches recognised in the pasted text.")

    def do_load(self) -> None:
        self.pull_into_session()
        s = self.session
        if not s.event_code:
            messagebox.showwarning(APP_NAME, "Enter an event code first (e.g. FTCCMP1JACK).", parent=self.app)
            return
        client = self.app.make_client(offline=self.offline_var.get())
        self.load_btn.state(["disabled"])
        self.progress_var.set(0.0)
        self.status_var.set("Contacting FTCScout…")
        with_history = self.history_var.get()

        def progress(msg: str, frac: float) -> None:      # called on the worker thread
            self.app.post(lambda m=msg, f=frac: (self.status_var.set(m), self.progress_var.set(f)))

        def work():
            s.load(client, progress=progress, with_history=with_history)
            return len(s.team_numbers), len(s.matches)

        def done(result) -> None:
            teams, matches = result
            self.load_btn.state(["!disabled"])
            self.progress_var.set(1.0)
            self.status_var.set(f"Loaded {teams} teams and {matches} matches.")
            config.save({"season": s.season, "event_code": s.event_code,
                         "my_team": s.my_team, "offline": self.offline_var.get()})
            self.app.refresh_all()

        def failed(err: ApiError) -> None:
            self.load_btn.state(["!disabled"])
            self.progress_var.set(0.0)
            self.status_var.set("Load failed.")
            self.app.show_api_error(err)

        self.app.run_async(work, done, failed)


TEAM_COLUMNS = [
    ("rank", "#", 40, "e"), ("team", "Team", 62, "e"), ("name", "Name", 190, "w"),
    ("opr", "OPR", 62, "e"), ("auto", "Auto", 58, "e"), ("teleop", "Teleop", 62, "e"),
    ("endgame", "Endgame", 68, "e"), ("avg", "AVG", 62, "e"), ("new_avg", "NEW AVG", 72, "e"),
    ("proj_opr", "Projected OPR", 72, "e"), ("events", "Events", 54, "e"),
    ("tdc", "Technical issue", 92, "w"), ("delta", "Delta", 58, "e"), ("focus", "Focus", 88, "w"),
    ("ftc_avg", "Team average", 72, "e"), ("cap", "CAP", 66, "e"), ("cap_opr", "CAP−OPR", 74, "e"),
    ("epa", "EPA", 62, "e"),
]


class TeamsTab(Tab):
    """AutoSheet.Sheet1 ranked by the chosen rating (CAP unless Settings says otherwise),
    with STAT PAGE's columns, an EPA column and the Technical Issues inputs.

    The table is AutoSheet's C2 ranking (teams sorted by CAP desc); the editor
    below it is the Technical Issues section - issue tag (K), manual delta (N) - plus
    AutoSheet!O "Team Focus".
    """

    title = "Teams"

    def build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Select a team, then open the side menu to chart its statistics and edit its inputs.",
                  foreground=UI_MUTED).pack(side="left")
        self.panel_btn = ttk.Button(bar, text="Show charts ▸", command=self.toggle_panel)
        self.panel_btn.pack(side="right")
        Tooltip(self.panel_btn, "Side menu: the selected team's statistics charted over its\n"
                                "events this season or its matches here, with pickable axes,\n"
                                "plus its data inputs (OPR, Auto, …) editable by hand.")
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True)
        left = ttk.Frame(self.paned)
        self.paned.add(left, weight=3)
        self.panel = TeamChartPanel(self.paned, self.app, show_team_entry=False, chart_height=250)
        self._panel_added = False

        self.tree = SortableTree(left, TEAM_COLUMNS, height=16)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind_select(self._on_select)

        ed = ttk.LabelFrame(left, text="Adjust the selected team (Technical Issues + Team Focus)", padding=8)
        ed.pack(fill="x", pady=(8, 0))
        self.sel_var = tk.StringVar(value="No team selected.")
        ttk.Label(ed, textvariable=self.sel_var, width=34).grid(row=0, column=0, columnspan=2, sticky="w")

        self.tdc_var = tk.StringVar()
        self.delta_var = tk.StringVar()
        self.focus_var = tk.StringVar()
        self.note_var = tk.StringVar()

        ttk.Label(ed, text="Technical issue").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.tdc_box = ttk.Combobox(ed, textvariable=self.tdc_var, values=list(TDC_ISSUES),
                                    state="readonly", width=14)
        self.tdc_box.grid(row=1, column=1, sticky="w")
        ttk.Label(ed, text="Delta").grid(row=1, column=2, sticky="w", padx=(12, 6))
        self.delta_entry = ttk.Entry(ed, textvariable=self.delta_var, width=8)
        self.delta_entry.grid(row=1, column=3, sticky="w")
        ttk.Label(ed, text="Focus").grid(row=1, column=4, sticky="w", padx=(12, 6))
        self.focus_box = ttk.Combobox(ed, textvariable=self.focus_var, values=list(FOCUS_MODES),
                                      state="readonly", width=12)
        self.focus_box.grid(row=1, column=5, sticky="w")
        ttk.Label(ed, text="Note").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(ed, textvariable=self.note_var, width=52).grid(
            row=2, column=1, columnspan=4, sticky="we", pady=(6, 0))
        ttk.Button(ed, text="Apply", command=self.apply_edit).grid(row=2, column=5, sticky="w", pady=(6, 0))
        ed.columnconfigure(4, weight=1)

        self.tdc_box.bind("<<ComboboxSelected>>", lambda _e: self.apply_edit())
        self.focus_box.bind("<<ComboboxSelected>>", lambda _e: self.apply_edit())
        self.delta_entry.bind("<Return>", lambda _e: self.apply_edit())
        self._editing = 0

    def toggle_panel(self, open_: Optional[bool] = None) -> None:
        """Open or close the chart side menu (new in 2.2)."""
        want = (not self._panel_added) if open_ is None else bool(open_)
        if want and not self._panel_added:
            self.paned.add(self.panel, weight=2)
            self._panel_added = True
            self.panel_btn.configure(text="◂ Hide charts")
            self.panel.set_team(as_int(self.tree.selection()))
        elif not want and self._panel_added:
            self.paned.forget(self.panel)
            self._panel_added = False
            self.panel_btn.configure(text="Show charts ▸")

    def refresh(self) -> None:
        s = self.session
        rows = []
        for i, t in enumerate(s.teams_by_strength(), 1):
            tags: list[str] = []
            if t.number == s.my_team:
                tags.append("mine")
            if not t.has_data:
                tags.append("dim")
            if getattr(t, "edited", False):
                tags.append("edited")
            rows.append((t.number, (
                i, t.number, t.name or "", f1(t.opr), f1(t.auto), f1(t.teleop), f1(t.endgame),
                f1(t.avg), f1(t.new_avg), f1(t.proj_opr) if t.proj_opr is not None else NA,
                t.events_played, t.tdc_issue or "", f1(t.tdc_delta), t.focus,
                f1(t.ftc_avg), f1(t.cap), sig(t.cap_minus_opr),
                f1(t.epa) if t.epa is not None else NA), tuple(tags)))
        self.tree.set_rows(rows)
        self._on_select(self.tree.selection())

    def _on_select(self, iid: str) -> None:
        if not iid:
            self.sel_var.set("No team selected.")
            return
        team = as_int(iid)
        t = self.session.teams.get(team)
        m = self.session.manual.get(team)
        self.sel_var.set(f"Team {team} {(t.name if t else '') or ''}".strip()
                         + (" (inputs edited)" if t is not None and getattr(t, "edited", False) else ""))
        self._editing += 1
        try:
            self.tdc_var.set((m.tdc_issue if m else "") or "")
            self.delta_var.set(f"{(m.tdc_delta if m else 0.0):g}")
            self.focus_var.set((m.focus if m else "performance") or "performance")
            self.note_var.set(m.note if m else "")
        finally:
            self._editing -= 1
        if self._panel_added and self.panel.team != team:
            self.panel.set_team(team)
        elif self._panel_added:
            self.panel.refresh()

    def apply_edit(self) -> None:
        iid = self.tree.selection()
        if not iid or self._editing:
            return
        team = as_int(iid)
        m = self.session.manual_for(team)
        m.tdc_issue = self.tdc_var.get().strip()
        m.tdc_delta = as_float(self.delta_var.get(), 0.0)
        m.focus = self.focus_var.get().strip() or "performance"
        m.note = self.note_var.get()
        self.app.recompute()
        self.tree.select(str(team))


MATCH_COLUMNS = [
    ("label", "Match", 62, "w"), ("r1", "Red 1", 62, "e"), ("r2", "Red 2", 62, "e"),
    ("b1", "Blue 1", 62, "e"), ("b2", "Blue 2", 62, "e"),
    ("rpts", "Pred. red", 72, "e"), ("bpts", "Pred. blue", 74, "e"),
    ("rcow", "Red win chance", 72, "e"), ("bcow", "Blue win chance", 74, "e"),
    ("rstr", "Red strength", 84, "e"), ("bstr", "Blue strength", 86, "e"),
    ("pred", "Pred.", 78, "w"), ("score", "Actual score", 96, "e"),
    ("actual", "Actual", 66, "w"), ("ok", "✔/✘", 44, "center"),
    ("poa", "PoA (exp.)", 78, "e"),
]


class MatchesTab(Tab):
    """Main-Calc.TED's schedule block and the AutoScout '1' correct/incorrect column.

    Predicted points are CAP sums (TED!D/E), Win chance is the estimated probability (TED!F/G),
    and the ✔/✘ column is AutoScout '1'!I once a real score exists. Double-click
    a row to type a score by hand - the sheets' "Live Outcome" column C.
    """

    title = "Matches"

    def build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.acc_var = tk.StringVar(value="No matches loaded.")
        ttk.Label(top, textvariable=self.acc_var, font=self.app.font_bold).pack(side="left")
        self.auto_var = tk.BooleanVar(value=False)
        self.every_var = tk.StringVar(value="2")
        # Right-packed widgets stack leftwards, so pack them right-to-left.
        ttk.Label(top, text=" min").pack(side="right")
        ttk.Spinbox(top, from_=1, to=60, width=4, textvariable=self.every_var,
                    command=self.reschedule).pack(side="right")
        ttk.Checkbutton(top, text="Auto-refresh every", variable=self.auto_var,
                        command=self.reschedule).pack(side="right")
        self.refresh_btn = ttk.Button(top, text="Refresh results", command=self.do_refresh)
        self.refresh_btn.pack(side="right", padx=8)

        self.tree = SortableTree(self, MATCH_COLUMNS, height=20)
        self.tree.pack(fill="both", expand=True, pady=(6, 0))
        self.tree.bind_double(self.edit_result)

        self.status_var = tk.StringVar(value="Double-click a match to enter a score by hand.")
        ttk.Label(self, textvariable=self.status_var, foreground="#666").pack(anchor="w", pady=(4, 0))
        self._after_id: Optional[str] = None

    def refresh(self) -> None:
        s = self.session
        my = s.my_team
        rows = []
        for p in s.predictions:
            m = p.match
            red = list(m.red) + ["", ""]
            blue = list(m.blue) + ["", ""]
            correct = p.correct
            tags = ["ok" if correct else "bad"] if correct is not None else ["pending"]
            if my and my in m.teams:
                tags.append("mine")
            score = f"{m.red_score:.0f} – {m.blue_score:.0f}" if m.winner else ""
            rows.append((m.label, (
                m.label, red[0], red[1], blue[0], blue[1],
                f1(p.red_pts), f1(p.blue_pts), pct(p.red_cow), pct(p.blue_cow),
                f"{p.red_strength:.1f}%", f"{p.blue_strength:.1f}%",
                p.winner, score, m.winner or "", "" if correct is None else ("✔" if correct else "✘"),
                f"{p.potential_of_accuracy:.2f}"), tuple(tags)))
        self.tree.set_rows(rows)
        keys = [c[0] for c in MATCH_COLUMNS]
        if not s.settings.show_potential_of_accuracy:
            keys = [k for k in keys if k != "poa"]
        self.tree.show_columns(keys)
        c, d = s.accuracy()
        played = sum(1 for m in s.matches if m.played)
        self.acc_var.set(f"{len(s.matches)} matches, {played} played   -   accuracy {c}/{d}"
                         + (f" = {c / d:.0%}" if d else "") + f"   -   predictions by {s.settings.basis}")

    def edit_result(self, iid: str) -> None:
        if not iid:
            return
        m = next((x for x in self.session.matches if x.label == iid), None)
        if m is None:
            return
        dlg = ResultDialog(self.app, m)
        self.app.wait_window(dlg)
        if dlg.result == "clear":
            self.session.manual_results.pop(m.label, None)
            self.app.recompute()
            self.status_var.set(f"Cleared the manual result for {m.label}.")
        elif dlg.result == "ok":
            self.session.manual_results[m.label] = (dlg.red, dlg.blue)
            self.app.recompute()
            self.status_var.set(f"{m.label}: {dlg.red:.0f} – {dlg.blue:.0f} entered by hand.")
        self.tree.select(iid)

    def do_refresh(self) -> None:
        s = self.session
        if not s.event_code:
            messagebox.showinfo(APP_NAME, "This session has no event code to refresh from.", parent=self.app)
            return
        client = self.app.make_client()
        self.refresh_btn.state(["disabled"])
        self.status_var.set("Refreshing results from FTCScout…")

        def work():
            return s.refresh_results(client)

        def done(played: int) -> None:
            self.refresh_btn.state(["!disabled"])
            self.status_var.set(f"Refreshed - {played} matches played.")
            self.app.refresh_all()

        def failed(err: ApiError) -> None:
            self.refresh_btn.state(["!disabled"])
            self.status_var.set("Refresh failed.")
            self.app.show_api_error(err)

        self.app.run_async(work, done, failed)

    def reschedule(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        if not self.auto_var.get():
            return
        minutes = max(1, as_int(self.every_var.get(), 2))
        self._after_id = self.after(minutes * 60_000, self._tick)

    def _tick(self) -> None:
        self._after_id = None
        if self.auto_var.get():
            self.do_refresh()
            self.reschedule()

    def stop(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None


class ResultDialog(tk.Toplevel):
    """The sheets' "Live Outcome" cell: type a final score for one match."""

    def __init__(self, app: "App", match):
        super().__init__(app)
        self.title(f"Result - {match.label}")
        self.transient(app)
        self.resizable(False, False)
        self.result: Optional[str] = None
        self.red = self.blue = 0.0
        red = "" if match.red_score is None else f"{match.red_score:.0f}"
        blue = "" if match.blue_score is None else f"{match.blue_score:.0f}"
        self.red_var = tk.StringVar(value=red)
        self.blue_var = tk.StringVar(value=blue)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Red {' + '.join(str(n) for n in match.red)}").grid(row=0, column=0, sticky="w")
        e = ttk.Entry(body, textvariable=self.red_var, width=8)
        e.grid(row=0, column=1, padx=6)
        ttk.Label(body, text=f"Blue {' + '.join(str(n) for n in match.blue)}").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(body, textvariable=self.blue_var, width=8).grid(row=1, column=1, padx=6, pady=(6, 0))
        bar = ttk.Frame(body)
        bar.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(bar, text="OK", command=self._ok).pack(side="left")
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        e.focus_set()
        try:
            self.grab_set()
        except tk.TclError:                    # pragma: no cover - window not viewable yet
            pass

    def _ok(self) -> None:
        self.red = as_float(self.red_var.get(), 0.0)
        self.blue = as_float(self.blue_var.get(), 0.0)
        self.result = "ok"
        self.destroy()

    def _clear(self) -> None:
        self.result = "clear"
        self.destroy()


MY_MATCH_COLUMNS = [
    ("label", "Match", 66, "w"), ("color", "Alliance", 74, "w"), ("partner", "Partner", 90, "w"),
    ("opp", "Opponents", 140, "w"), ("cow", "Chance of winning", 130, "e"),
    ("mr", "Match rating", 44, "center"), ("pred", "Pred.", 80, "w"), ("actual", "Actual", 70, "w"),
]


class MyTeamTab(Tab):
    """AutoScout '1' rows 13..18 plus the stat block at R3..X9.

    Shows one team's OPR/Auto/Teleop/Endgame/CAP, the CAP-vs-OPR percentage
    change (R9), its projected rank, predicted wins and "Old RP (out of 2)".
    """

    title = "My Team"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        self.name_var = tk.StringVar(value="No team selected.")
        ttk.Label(head, textvariable=self.name_var, font=self.app.font_title).pack(side="left")
        ttk.Label(head, text="Team ").pack(side="left", padx=(16, 0))
        self.team_var = tk.StringVar()
        entry = ttk.Entry(head, textvariable=self.team_var, width=8)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.change_team())
        ttk.Button(head, text="Show", command=self.change_team).pack(side="left", padx=4)

        stats = ttk.LabelFrame(self, text="Season stats", padding=8)
        stats.pack(fill="x", pady=(8, 0))
        self.stat_vars: dict[str, tk.StringVar] = {}
        blocks = [("OPR", "opr"), ("Auto", "auto"), ("Teleop", "teleop"),
                  ("Endgame", "endgame"), ("CAP", "cap")]
        for col, (label, key) in enumerate(blocks):
            cell = ttk.Frame(stats)
            cell.grid(row=0, column=col, padx=(0, 22), sticky="w")
            ttk.Label(cell, text=label, foreground="#666").pack(anchor="w")
            v = tk.StringVar(value=NA)
            self.stat_vars[key] = v
            ttk.Label(cell, textvariable=v, font=self.app.font_big).pack(anchor="w")
        self.change_var = tk.StringVar()
        ttk.Label(stats, textvariable=self.change_var, font=self.app.font_bold).grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.proj_var = tk.StringVar()
        ttk.Label(stats, textvariable=self.proj_var).grid(row=2, column=0, columnspan=5, sticky="w")

        ttk.Label(self, text="My matches", font=self.app.font_bold).pack(anchor="w", pady=(10, 2))
        self.tree = SortableTree(self, MY_MATCH_COLUMNS, height=14)
        self.tree.pack(fill="both", expand=True)

    def change_team(self) -> None:
        n = as_int(self.team_var.get(), 0)
        if n and n != self.session.my_team:
            self.session.my_team = n
            self.app.refresh_all()

    def refresh(self) -> None:
        s = self.session
        self.team_var.set(str(s.my_team) if s.my_team else "")
        info = s.my_summary()
        t = info["stats"]
        if t is None:
            self.name_var.set(f"Team {s.my_team} is not at this event." if s.my_team
                              else "Set your team number on the Setup tab.")
            for v in self.stat_vars.values():
                v.set(NA)
            self.change_var.set("")
            self.proj_var.set("")
            self.tree.set_rows([])
            return
        self.name_var.set(f"{t.number}  {t.name}".strip())
        for key in ("opr", "auto", "teleop", "endgame", "cap"):
            self.stat_vars[key].set(f1(getattr(t, key)))
        change = t.cap_change_pct
        if change is None:
            self.change_var.set("CAP has no OPR to compare against.")
        else:
            word = "DECREASE" if change < 0 else "INCREASE"
            size = abs(change) * 100
            # Whole percent everywhere else, but "0% INCREASE" would be a lie.
            shown = f"{size:.0f}" if size >= 0.5 or size == 0 else f"{size:.1f}"
            self.change_var.set(f"CAP is {shown}% {word} from OPR")
        matches = info["matches"]
        self.proj_var.set(
            f"Projected rank {info['rank'] or NA}     "
            f"predicted wins {info['wins']} / {len(matches)}     "
            f"old RP {info['old_rp']:.2f}     "
            f"Proj OPR {f1(t.proj_opr) if t.proj_opr is not None else NA}     "
            f"events played {t.events_played}")

        rows = []
        for x in matches:
            m = x.prediction.match
            tags = ["ok" if x.actual == "Win" else "bad"] if x.actual in ("Win", "Loss") else ["pending"]
            rows.append((m.label, (
                m.label, x.color.title(), " ".join(str(n) for n in x.partner),
                " ".join(str(n) for n in x.opponents), pct(x.cow), x.rating,
                "Win" if x.predicted_win else "Loss", x.actual or ""), tuple(tags)))
        self.tree.set_rows(rows)


STANDING_COLUMNS = [
    ("rank", "Rank", 54, "e"), ("team", "Team", 66, "e"), ("name", "Name", 200, "w"),
    ("wl", "W–L", 70, "center"), ("played", "Played", 86, "center"),
    ("rp", "Ranking points", 62, "e"), ("tbp1", "TBP1 (Auto)", 92, "e"), ("tbp2", "TBP2 (Endgame)", 106, "e"),
    ("captain", "Captain", 70, "center"),
]


class StandingsTab(Tab):
    """AutoScout.Sheet16 - projected standings sorted by RP, then TBP1, then TBP2.

    Wins come from the real result when a match has been played and from the
    prediction otherwise, so the table is a forecast that hardens as the meet runs.
    The top six carry the sheet's "Cpt. Rank" captain marker.
    """

    title = "Standings"

    def build(self) -> None:
        self.head_var = tk.StringVar(value="No standings yet.")
        ttk.Label(self, textvariable=self.head_var, font=self.app.font_bold).pack(anchor="w")
        self.tree = SortableTree(self, STANDING_COLUMNS, height=22)
        self.tree.pack(fill="both", expand=True, pady=(6, 0))

    def refresh(self) -> None:
        s = self.session
        rows = []
        for r in s.table:
            tags = ["mine"] if r.number == s.my_team else []
            rows.append((r.number, (
                r.rank, r.number, r.name or "", f"{r.wins}–{r.losses}",
                f"{r.played} / {r.scheduled}", f"{r.rp:.2f}", f1(r.tbp1), f1(r.tbp2),
                "★" if r.captain_rank else ""), tuple(tags)))
        self.tree.set_rows(rows)
        self.head_var.set(f"{len(rows)} teams - projected standings (RP, then AUTO, then ENDGAME); "
                          "top 6 are likely alliance captains")

ALLIANCE_COLUMNS = [
    ("rank", "#", 44, "e"), ("alliance", "Alliance", 120, "w"), ("names", "Teams", 230, "w"),
    ("total", "Total CAP", 82, "e"), ("beats", "Beats", 88, "e"), ("rate", "Win rate", 76, "e"),
    ("strength", "Alliance Strength", 108, "e"),
    ("closest", "Closest opponent", 124, "w"), ("margin", "Margin", 70, "e"),
    ("cow", "Chance of winning", 122, "e"), ("verdict", "Verdict", 100, "w"),
]

PARTNER_COLUMNS = [
    ("partner", "Partner", 70, "e"), ("name", "Name", 200, "w"), ("pcap", "Their CAP", 82, "e"),
    ("total", "Total CAP", 82, "e"), ("beats", "Beats", 88, "e"), ("rate", "Win rate", 76, "e"),
    ("strength", "Alliance Strength", 108, "e"),
    ("cow", "Chance of winning", 122, "e"), ("margin", "Margin", 70, "e"),
    ("verdict", "Verdict", 100, "w"),
]

SEAT_COLUMNS = [
    ("rank", "Cpt.", 48, "e"), ("team", "Team", 66, "e"), ("name", "Name", 180, "w"),
    ("cap", "CAP", 66, "e"), ("best", "Best available", 100, "e"),
    ("pick", "Likely pick", 86, "e"), ("pcap", "Pick CAP", 76, "e"),
    ("total", "Projected total", 104, "e"), ("bar", "Pick above", 86, "e"),
]

#: How much of the ranking the table shows. Every unbeatable alliance is near the top
#: of it by construction, so this only ever hides rows nobody would pick from.
ALLIANCE_ROWS = 250


class AlliancesTab(Tab):
    """FTC AutoScout ALSO - every two-team alliance, scored against every alliance it
    could line up against.

    Alliance strength is the CAP sum (TED!D/E) and an opponent may not share a team
    with the alliance being scored, so "beats" counts the alliances it out-scores among
    the ones it could actually face. An alliance that beats all of them is the sheet's
    answer to what could win any scenario. The captain block is Sheet1 rows 3–8 - the
    six seeds, the pick the draft leaves each of them, and the "pick must be above" bar
    for the team in the box; the bottom table is Interface rows 5+, every partner that
    team could win with.
    """

    title = "Alliances"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        self.head_var = tk.StringVar(value="No teams loaded.")
        ttk.Label(head, textvariable=self.head_var, font=self.app.font_bold).pack(side="left")
        ttk.Label(head, text="Team ").pack(side="left", padx=(16, 0))
        self.team_var = tk.StringVar()
        entry = ttk.Entry(head, textvariable=self.team_var, width=8)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.change_team())
        ttk.Button(head, text="Show", command=self.change_team).pack(side="left", padx=4)
        self.only_var = tk.BooleanVar(value=False)
        only = ttk.Checkbutton(head, text="Unbeatable only", variable=self.only_var,
                               command=self.refresh)
        only.pack(side="left", padx=(12, 0))
        Tooltip(only, "Show only the alliances that out-score every alliance\n"
                      "they could face - no shared team, so both could\n"
                      "take the field at the same time.")

        self.bar_var = tk.StringVar()
        ttk.Label(self, textvariable=self.bar_var, foreground=UI_MUTED).pack(anchor="w", pady=(2, 0))

        seats = ttk.LabelFrame(self, text="Alliance captains (what the draft leaves each seat)",
                               padding=8)
        seats.pack(fill="x", pady=(8, 0))
        self.seat_tree = SortableTree(seats, SEAT_COLUMNS, height=6)
        self.seat_tree.pack(fill="both", expand=True)

        ttk.Label(self, text="Best alliances", font=self.app.font_bold).pack(anchor="w", pady=(10, 2))
        self.tree = SortableTree(self, ALLIANCE_COLUMNS, height=12)
        self.tree.pack(fill="both", expand=True)

        self.partner_var = tk.StringVar(value="Partners")
        ttk.Label(self, textvariable=self.partner_var, font=self.app.font_bold).pack(
            anchor="w", pady=(10, 2))
        self.partner_tree = SortableTree(self, PARTNER_COLUMNS, height=10)
        self.partner_tree.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Type a team number to see who it could win with.")
        ttk.Label(self, textvariable=self.status_var, foreground=UI_MUTED).pack(anchor="w", pady=(4, 0))

    def change_team(self) -> None:
        n = as_int(self.team_var.get(), 0)
        if n and n != self.session.my_team:
            self.session.my_team = n
            self.app.refresh_all()

    def refresh(self) -> None:
        s = self.session
        lab = s.settings.basis
        self.seat_tree.set_heading("cap", lab)
        self.seat_tree.set_heading("pcap", f"Pick {lab}")
        self.tree.set_heading("total", f"Total {lab}")
        self.partner_tree.set_heading("pcap", f"Their {lab}")
        self.partner_tree.set_heading("total", f"Total {lab}")
        self.team_var.set(str(s.my_team) if s.my_team else "")
        if not s.teams:
            self.head_var.set("No teams loaded.")
            self.bar_var.set("Load a meet on the Setup tab first.")
            self.partner_var.set("Partners")
            for tree in (self.seat_tree, self.tree, self.partner_tree):
                tree.set_rows([])
            self.status_var.set("Type a team number to see who it could win with.")
            return

        rep = s.alliance_report()
        unbeaten = rep.unbeatable
        self.head_var.set(f"{len(s.teams)} teams → {len(rep.table)} possible alliances, "
                          f"{len(unbeaten)} unbeaten by anything they could face")
        if rep.bar is not None:
            self.bar_var.set(f"Team {rep.my_team} · rank {rep.my_rank or NA} · "
                             f"{lab} {f1(rep.my_cap)} · pick must be above {f1(rep.bar)}")
        else:
            self.bar_var.set("Set a team number to see its partners and the bar its pick must clear.")

        self.seat_tree.set_rows([
            (st.team, (st.rank, st.team, st.name or "", f1(st.cap), f1(st.best_available),
                       st.pick or NA, f1(st.pick_cap), f1(st.projected_total), f1(st.bar)),
             ("mine",) if st.team == s.my_team else ())
            for st in rep.seats])

        shown = unbeaten if self.only_var.get() else rep.table[:ALLIANCE_ROWS]
        rows = []
        for a in shown:
            tags: list[str] = []
            if a.unbeatable:
                tags.append("ok")
            if s.my_team and s.my_team in a.teams:
                tags.append("mine")
            rows.append((a.label, (
                a.rank, a.label, " · ".join(n for n in a.names if n),
                f1(a.cap_sum), f"{a.beats} / {a.legal}", f"{a.win_rate * 100:.1f}%",
                f"{a.alliance_strength:.1f}%",
                " + ".join(str(n) for n in a.closest) or NA, sig(a.margin), pct(a.cow),
                "UNBEATABLE" if a.unbeatable else ""), tuple(tags)))
        self.tree.set_rows(rows)

        self._fill_partners(rep)

    def _fill_partners(self, rep) -> None:
        """Interface rows 5+ - every partner the team in the box could win with, with
        the ones below its seat's bar dimmed rather than hidden."""
        if not rep.my_team or not rep.partners:
            self.partner_var.set("Partners")
            self.partner_tree.set_rows([])
            self.status_var.set("Set a team number above to see the partners it could win with."
                                if not rep.my_team else
                                f"Team {rep.my_team} is not at this event.")
            return
        self.partner_var.set(f"Teams {rep.my_team} could win with")
        rows = []
        for a in rep.partners:
            tags: list[str] = []
            if a.unbeatable:
                tags.append("ok")
            if rep.bar is not None and a.partner_cap(rep.my_team) <= rep.bar:
                tags.append("dim")
            partner = a.partner_of(rep.my_team)
            rows.append((partner, (
                partner, a.partner_name(rep.my_team), f1(a.partner_cap(rep.my_team)),
                f1(a.cap_sum), f"{a.beats} / {a.legal}", f"{a.win_rate * 100:.1f}%",
                f"{a.alliance_strength:.1f}%",
                pct(a.cow), sig(a.margin), "UNBEATABLE" if a.unbeatable else ""), tuple(tags)))
        self.partner_tree.set_rows(rows)
        avg = rep.average_total
        self.status_var.set(
            f"{len(rep.qualifying)} of {len(rep.partners)} partners clear the bar"
            + (f", averaging {f1(avg)} total {self.session.settings.basis}" if avg is not None else "")
            + ".  Chance of winning is a share of predicted points, not a probability.")


WRONG_COLUMNS = [
    ("label", "Match", 70, "w"), ("pred", "Pred.", 80, "w"), ("actual", "Actual", 70, "w"),
    ("pts", "Pred. pts", 110, "e"), ("score", "Actual score", 106, "e"),
    ("suggest", "Suggestion", 220, "w"), ("cls", "Classification", 170, "w"),
]
FLAG_COLUMNS = [
    ("team", "Team", 70, "e"), ("wrong", "In wrong matches", 130, "e"),
    ("cls", "Classification", 200, "w"), ("pw", "Predicted wins", 110, "e"),
    ("aw", "Actual wins", 100, "e"),
]


# --------------------------------------------------------------------------- #
# Charts (new in 2.2) - plain tk.Canvas plots, so the single-file / PyInstaller
# builds stay standard-library only. Three pieces:
#
#   ChartCanvas      a scatter / line chart with axes, legend, hover and click
#   *_VARS tables    what can go on an axis, and how to read it from the session
#   TeamChartPanel   one team's statistics over its events or its matches, with
#                    the "edit data inputs" block - used by the Teams side menu
#                    and by the Charts tab
#   ChartsTab        the Charts page: one team / all teams (OPR vs CAP vs EPA
#                    convergence) / matches & alliance pairings
# --------------------------------------------------------------------------- #
import math

CHART_COLORS = ["#5d081f", "#dbdce0", "#c0392b", "#2f6fdb", "#2e8b57", "#7d3c98", "#e67e22", "#16a085"]
CHART_GRID = "#e6e9f0"
CHART_AXIS = "#8a93a6"
CHART_TEXT = UI_TEXT
CHART_MUTED = UI_MUTED
NONE_LABEL = "- none -"


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round tick positions covering [lo, hi] (about `n` of them)."""
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0, 1.0]
    if hi <= lo:
        hi = lo + (abs(lo) * 0.1 or 1.0)
    span = hi - lo
    raw = span / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if span / step <= n + 0.5:
            break
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 0.5:
        ticks.append(round(t, 10))
        t += step
    return ticks


def _fmt_tick(v: float) -> str:
    if abs(v) >= 100 or v == int(v):
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _lerp_color(a: str, b: str, t: float) -> str:
    t = min(max(t, 0.0), 1.0)
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t))


@dataclass
class ChartPoint:
    x: float
    y: float
    label: str = ""                 # short text drawn next to the marker (optional)
    text: str = ""                  # hover text
    z: Optional[float] = None       # third variable → marker size and colour
    key: Any = None                 # what a click hands back (team number, match label…)
    color: Optional[str] = None     # overrides the series colour


@dataclass
class ChartSeries:
    name: str
    points: list                    # list[ChartPoint]
    color: str = CHART_COLORS[0]
    kind: str = "scatter"           # "scatter" | "line"
    labels: bool = False            # draw point labels
    dashed: bool = False
    markers: bool = True            # False for reference lines (x = y): no dots, no hover, no click


class ChartCanvas(tk.Canvas):
    """A chart. `set_data(...)` then it draws itself, and again on every resize.

    Hover shows the nearest point's text; a click hands its `key` to `on_click`.
    """

    PAD_L, PAD_R, PAD_T, PAD_B = 62, 18, 34, 48

    def __init__(self, master, height: int = 320, on_click: Optional[Callable[[Any], None]] = None,
                 title_font=None, **kw):
        super().__init__(master, height=height, bg="#ffffff", highlightthickness=1,
                         highlightbackground=UI_BORDER, **kw)
        self.on_click = on_click
        self.title_font = title_font or ("TkDefaultFont", 10, "bold")
        self.title = ""
        self.x_label = ""
        self.y_label = ""
        self.z_label = ""
        self.series: list[ChartSeries] = []
        self.x_ticks: Optional[list[tuple[float, str]]] = None   # categorical axis
        self.note = ""
        self._screen: list[tuple[float, float, ChartPoint, ChartSeries]] = []
        self._hover_items: list[int] = []
        self._redraw_id: Optional[str] = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._clear_hover())
        self.bind("<Button-1>", self._on_click)

    # -- data ---------------------------------------------------------------
    def set_data(self, title: str, x_label: str, y_label: str, series: Sequence[ChartSeries],
                 z_label: str = "", x_ticks: Optional[Sequence[tuple[float, str]]] = None,
                 note: str = "") -> None:
        self.title, self.x_label, self.y_label, self.z_label = title, x_label, y_label, z_label
        self.series = list(series)
        self.x_ticks = list(x_ticks) if x_ticks else None
        self.note = note
        self.redraw()

    def clear(self, message: str = "No data to chart yet.") -> None:
        self.series = []
        self.note = message
        self.redraw()

    # -- drawing --------------------------------------------------------------
    def _on_destroy(self, _e=None) -> None:
        if self._redraw_id is not None:
            try:
                self.after_cancel(self._redraw_id)
            except tk.TclError:
                pass
            self._redraw_id = None

    def _on_resize(self, _e=None) -> None:
        if self._redraw_id is not None:
            try:
                self.after_cancel(self._redraw_id)
            except tk.TclError:
                pass
        self._redraw_id = self.after(40, self.redraw)

    def redraw(self) -> None:
        self._redraw_id = None
        self.delete("all")
        self._screen = []
        self._hover_items = []
        w = max(self.winfo_width(), 200)
        h = max(self.winfo_height(), 120)
        pts = [(p, s) for s in self.series for p in s.points
               if p.x is not None and p.y is not None and math.isfinite(p.x) and math.isfinite(p.y)]
        zs = [p.z for p, _ in pts if p.z is not None and math.isfinite(p.z)]
        z_lo, z_hi = (min(zs), max(zs)) if zs else (0.0, 0.0)
        # legend: a row (or two) of swatches under the title, never over the data
        import tkinter.font as _tkfont
        small = _tkfont.Font(font=("TkDefaultFont", 8))
        entries = [(s.color, s.name) for s in self.series if s.name and s.kind != "vbar"]
        if zs:
            entries.append((None, f"size/colour = {(self.z_label or 'z')[:48]} ({_fmt_tick(z_lo)} … {_fmt_tick(z_hi)})"))
        legend_rows: list[list[tuple]] = []
        if len(entries) > 1 or zs:
            row, used = [], 0
            for color, name in entries:
                need = small.measure(name) + 34
                if row and used + need > w - self.PAD_L - self.PAD_R:
                    legend_rows.append(row)
                    row, used = [], 0
                row.append((color, name, need))
                used += need
            if row:
                legend_rows.append(row)
        rot = False
        pad_b = self.PAD_B
        if self.x_ticks:
            spacing = (w - self.PAD_L - self.PAD_R) / max(1, len(self.x_ticks))
            maxlen = max(len(lab) for _v, lab in self.x_ticks)
            rot = spacing < maxlen * 7 + 10
            if rot:
                pad_b = self.PAD_B + int(maxlen * 5.5)
        x0, y0, x1, y1 = self.PAD_L, self.PAD_T + 14 * len(legend_rows), w - self.PAD_R, h - pad_b
        if self.title:
            self.create_text((x0 + x1) / 2, 14, text=self.title, fill=UI_NAVY, font=self.title_font)
        ly = 28
        for row in legend_rows:
            lx = x0
            for color, name, need in row:
                if color:
                    self.create_rectangle(lx, ly, lx + 12, ly + 10, fill=color, outline="")
                self.create_text(lx + (16 if color else 0), ly + 5, text=name, anchor="w",
                                 fill=CHART_TEXT if color else CHART_MUTED, font=small)
                lx += need
            ly += 14
        if not pts:
            self.create_text(w / 2, h / 2, text=self.note or "No data to chart yet.", fill=CHART_MUTED)
            return
        xs = [p.x for p, _ in pts]
        ys = [p.y for p, _ in pts]
        lo_x, hi_x = min(xs), max(xs)
        lo_y, hi_y = min(ys), max(ys)
        if self.x_ticks:
            lo_x, hi_x = min(lo_x, self.x_ticks[0][0]), max(hi_x, self.x_ticks[-1][0])
            lo_x, hi_x = lo_x - 0.6, hi_x + 0.6
            xt = [(v, lab) for v, lab in self.x_ticks]          # every category gets its label
        else:
            padx = (hi_x - lo_x) * 0.05 or (abs(hi_x) * 0.05 or 1.0)
            lo_x, hi_x = lo_x - padx, hi_x + padx
            xt = [(v, _fmt_tick(v)) for v in nice_ticks(lo_x, hi_x, 6)]
            lo_x, hi_x = min(lo_x, xt[0][0]), max(hi_x, xt[-1][0])
        pady = (hi_y - lo_y) * 0.08 or (abs(hi_y) * 0.08 or 1.0)
        lo_y, hi_y = lo_y - pady, hi_y + pady
        yt = nice_ticks(lo_y, hi_y, 5)
        lo_y, hi_y = min(lo_y, yt[0]), max(hi_y, yt[-1])

        def sx(v: float) -> float:
            return x0 + (v - lo_x) / (hi_x - lo_x) * (x1 - x0)

        def sy(v: float) -> float:
            return y1 - (v - lo_y) / (hi_y - lo_y) * (y1 - y0)

        # grid + axes
        for v in yt:
            y = sy(v)
            self.create_line(x0, y, x1, y, fill=CHART_GRID)
            self.create_text(x0 - 6, y, text=_fmt_tick(v), anchor="e", fill=CHART_MUTED,
                             font=("TkDefaultFont", 8))
        for v, lab in xt:
            x = sx(v)
            self.create_line(x, y0, x, y1, fill=CHART_GRID)
            if rot:
                self.create_text(x + 3, y1 + 6, text=lab[:14], anchor="ne", angle=60, fill=CHART_MUTED,
                                 font=("TkDefaultFont", 8))
            else:
                self.create_text(x, y1 + 6, text=lab[:12], anchor="n", fill=CHART_MUTED,
                                 font=("TkDefaultFont", 8))
        self.create_line(x0, y1, x1, y1, fill=CHART_AXIS)
        self.create_line(x0, y0, x0, y1, fill=CHART_AXIS)
        if self.x_label:
            self.create_text((x0 + x1) / 2, h - 8, text=self.x_label, fill=CHART_TEXT,
                             font=("TkDefaultFont", 9))
        if self.y_label:
            self.create_text(14, (y0 + y1) / 2, text=self.y_label, angle=90, fill=CHART_TEXT,
                             font=("TkDefaultFont", 9))

        # z scaling (marker size + colour)
        def z_norm(z: Optional[float]) -> Optional[float]:
            if z is None or not zs:
                return None
            return 0.5 if z_hi == z_lo else (z - z_lo) / (z_hi - z_lo)

        # series ("vbar" = a vertical min–max line per x, drawn underneath the rest)
        for s in self.series:
            spts = [p for p in s.points if p.x is not None and p.y is not None
                    and math.isfinite(p.x) and math.isfinite(p.y)]
            if s.kind == "vbar":
                groups: dict[float, list[float]] = {}
                for p in spts:
                    groups.setdefault(p.x, []).append(p.y)
                for gx, gys in groups.items():
                    self.create_line(sx(gx), sy(min(gys)), sx(gx), sy(max(gys)), fill=s.color, width=4)
                continue
            if s.kind == "line" and len(spts) >= 2:
                ordered = sorted(spts, key=lambda p: p.x)
                coords = []
                for p in ordered:
                    coords += [sx(p.x), sy(p.y)]
                self.create_line(*coords, fill=s.color, width=2, smooth=False,
                                 dash=(6, 4) if s.dashed else None)
            if not s.markers:
                continue
            for p in spts:
                cx, cy = sx(p.x), sy(p.y)
                zn = z_norm(p.z)
                r = 4.5 if zn is None else 4 + 9 * zn
                color = p.color or (s.color if zn is None else _lerp_color("#dbdce0", "#5d081f", zn))
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="#ffffff", width=1)
                if s.labels and p.label:
                    self.create_text(cx + r + 2, cy - r - 1, text=p.label, anchor="sw",
                                     fill=CHART_TEXT, font=("TkDefaultFont", 8))
                self._screen.append((cx, cy, p, s))

        if self.note and pts:
            self.create_text(x0 + 4, y0 + 6, text=self.note, anchor="nw", fill=CHART_MUTED,
                             font=("TkDefaultFont", 8))

    # -- interaction ------------------------------------------------------------
    def _nearest(self, x: float, y: float, radius: float = 14.0):
        best, best_d = None, radius * radius
        for cx, cy, p, s in self._screen:
            d = (cx - x) ** 2 + (cy - y) ** 2
            if d <= best_d:
                best, best_d = (cx, cy, p, s), d
        return best

    def _clear_hover(self) -> None:
        for item in self._hover_items:
            self.delete(item)
        self._hover_items = []

    def _on_motion(self, e) -> None:
        self._clear_hover()
        hit = self._nearest(e.x, e.y)
        if not hit:
            return
        cx, cy, p, s = hit
        text = p.text or p.label or f"{p.x:g}, {p.y:g}"
        w = max(self.winfo_width(), 200)
        tx = cx + 12 if cx < w - 160 else cx - 12
        anchor = "w" if cx < w - 160 else "e"
        t = self.create_text(tx, cy, text=text, anchor=anchor, fill=CHART_TEXT, font=("TkDefaultFont", 9))
        bx0, by0, bx1, by1 = self.bbox(t)
        r = self.create_rectangle(bx0 - 4, by0 - 3, bx1 + 4, by1 + 3, fill="#fffbe6", outline=UI_BORDER)
        self.tag_raise(t, r)
        ring = self.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=UI_GOLD_DEEP, width=2)
        self._hover_items = [r, t, ring]

    def _on_click(self, e) -> None:
        hit = self._nearest(e.x, e.y)
        if hit and self.on_click and hit[2].key is not None:
            self.on_click(hit[2].key)

    def save_postscript(self, path: str) -> None:
        self.postscript(file=path, colormode="color")


# --------------------------------------------------------------------------- #
# What can go on an axis
# --------------------------------------------------------------------------- #
def _opt(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


#: Per-team variables: (key, label, getter(team, ctx)) - ctx has "standing" (number → Standing).
TEAM_VARS: list[tuple[str, str, Callable]] = [
    ("team", "Team number", lambda t, c: float(t.number)),
    ("opr", "OPR (non-penalty)", lambda t, c: t.opr),
    ("cap", "CAP", lambda t, c: t.cap),
    ("epa", "EPA", lambda t, c: _opt(t.epa)),
    ("strength", "Prediction rating (current basis)", lambda t, c: t.strength),
    ("auto", "Auto", lambda t, c: t.auto),
    ("teleop", "Teleop", lambda t, c: t.teleop),
    ("endgame", "Endgame", lambda t, c: t.endgame),
    ("avg", "AVG", lambda t, c: t.avg),
    ("new_avg", "NEW AVG", lambda t, c: t.new_avg),
    ("ftc_avg", "FTC AVG (after Technical Issues)", lambda t, c: t.ftc_avg),
    ("proj_opr", "Projected OPR", lambda t, c: _opt(t.proj_opr)),
    ("eopr", "Event OPR", lambda t, c: t.eopr),
    ("cap_opr", "CAP − OPR", lambda t, c: t.cap - t.opr),
    ("epa_opr", "EPA − OPR", lambda t, c: None if t.epa is None else t.epa - t.opr),
    ("epa_cap", "EPA − CAP", lambda t, c: None if t.epa is None else t.epa - t.cap),
    ("spread", "Rating spread (max − min of OPR/CAP/EPA)",
     lambda t, c: (max(v for v in (t.opr, t.cap, t.epa) if v is not None)
                   - min(v for v in (t.opr, t.cap, t.epa) if v is not None))),
    ("rank", "Projected rank", lambda t, c: _opt(c["standing"][t.number].rank) if t.number in c["standing"] else None),
    ("rp", "Projected RP", lambda t, c: c["standing"][t.number].rp if t.number in c["standing"] else None),
    ("wins", "Wins (actual + predicted)", lambda t, c: float(c["standing"][t.number].wins) if t.number in c["standing"] else None),
    ("events", "Events played this season", lambda t, c: float(t.events_played)),
    ("epa_matches", "Matches EPA learned from", lambda t, c: float(t.epa_matches)),
    ("src_rank", "Rank at source event", lambda t, c: float(t.rank) if t.rank else None),
    ("tdc_delta", "Technical issues adjustment", lambda t, c: t.tdc_delta),
]
TEAM_VAR_LABELS = [lab for _, lab, _ in TEAM_VARS]
TEAM_VAR_BY_LABEL = {lab: (k, fn) for k, lab, fn in TEAM_VARS}
RATING_SERIES = [("OPR (non-penalty)", "opr"), ("CAP", "cap"), ("EPA", "epa"),
                 ("NEW AVG", "new_avg"), ("Projected OPR", "proj_opr")]

#: One team over its events this season: (key, label, getter(EventStat, asof) ) where `asof`
#: is a TeamStats built from the history up to and including that event, and extra["epa"].
EVENT_X_VARS = [("Event (in order played)", "index"), ("Event date", "date")]
EVENT_Y_VARS: list[tuple[str, str, Callable]] = [
    ("opr", "OPR (non-penalty)", lambda e, asof, ex: e.opr_np),
    ("cap", "CAP (as of that event)", lambda e, asof, ex: asof.cap if asof else None),
    ("epa", "EPA (after that event)", lambda e, asof, ex: ex.get("epa")),
    ("auto", "Auto", lambda e, asof, ex: e.auto),
    ("teleop", "Teleop", lambda e, asof, ex: e.teleop),
    ("endgame", "Endgame", lambda e, asof, ex: e.endgame),
    ("total", "Total OPR (with penalties)", lambda e, asof, ex: e.opr_total),
    ("proj", "Projected OPR (as of that event)", lambda e, asof, ex: _opt(asof.proj_opr) if asof else None),
    ("tb1", "TBP 1", lambda e, asof, ex: e.tb1),
    ("tb2", "TBP 2", lambda e, asof, ex: e.tb2),
    ("rank", "Rank at event", lambda e, asof, ex: float(e.rank) if e.rank else None),
    ("rp", "RP at event", lambda e, asof, ex: e.rp),
    ("wins", "Wins at event", lambda e, asof, ex: float(e.wins)),
    ("losses", "Losses at event", lambda e, asof, ex: float(e.losses)),
    ("played", "Qual matches played", lambda e, asof, ex: float(e.qual_played)),
]
EVENT_Y_LABELS = [lab for _, lab, _ in EVENT_Y_VARS]
EVENT_Y_BY_LABEL = {lab: fn for _, lab, fn in EVENT_Y_VARS}

#: One team over its matches at this event: getter(MyMatch, epa_before, pred, match)
MATCH_X_VARS = [("Match (in order)", "index"), ("Match number", "number")]
#: One team over every match it has played this season (earlier events, then this one),
#: back to back in the order they were played. Uses the same Y getters as MATCH_Y_VARS.
ALL_MATCH_X_VARS = [("Match (in order played)", "index")]
MATCH_Y_VARS: list[tuple[str, str, Callable]] = [
    ("epa", "EPA before the match", lambda mm, epa, p, m: epa),
    ("pred_mine", "Pred. points, my alliance", lambda mm, epa, p, m: p.red_pts if mm.color == "RED" else p.blue_pts),
    ("pred_opp", "Pred. points, opponents", lambda mm, epa, p, m: p.blue_pts if mm.color == "RED" else p.red_pts),
    ("pred_margin", "Predicted margin", lambda mm, epa, p, m: (p.red_pts - p.blue_pts) * (1 if mm.color == "RED" else -1)),
    ("cow", "Chance of winning (%)", lambda mm, epa, p, m: mm.cow * 100),
    ("alliance_strength", "My alliance's Alliance Strength (%)",
     lambda mm, epa, p, m: p.red_strength if mm.color == "RED" else p.blue_strength),
    ("opp_strength", "Opponent alliance's Alliance Strength (%)",
     lambda mm, epa, p, m: p.blue_strength if mm.color == "RED" else p.red_strength),
    ("actual_mine", "Actual points, my alliance (np)", lambda mm, epa, p, m: _alliance_score(m, mm.color)),
    ("actual_opp", "Actual points, opponents (np)", lambda mm, epa, p, m: _alliance_score(m, "BLUE" if mm.color == "RED" else "RED")),
    ("actual_margin", "Actual margin", lambda mm, epa, p, m: _margin(m, mm.color)),
    ("error", "Prediction error (actual − predicted, my alliance)",
     lambda mm, epa, p, m: None if _alliance_score(m, mm.color) is None
     else _alliance_score(m, mm.color) - (p.red_pts if mm.color == "RED" else p.blue_pts)),
]
MATCH_Y_LABELS = [lab for _, lab, _ in MATCH_Y_VARS]
MATCH_Y_BY_LABEL = {lab: fn for _, lab, fn in MATCH_Y_VARS}


def _alliance_score(m: Match, color: str) -> Optional[float]:
    """Non-penalty score when both sides have one, otherwise the total for both (never mixed)."""
    if m.red_score_np is not None and m.blue_score_np is not None:
        return _opt(m.red_score_np if color == "RED" else m.blue_score_np)
    return _opt(m.red_score if color == "RED" else m.blue_score)


def _margin(m: Match, color: str) -> Optional[float]:
    a, b = _alliance_score(m, "RED"), _alliance_score(m, "BLUE")
    if a is None or b is None:
        return None
    return (a - b) if color == "RED" else (b - a)


#: Schedule matches: getter(Prediction)
PRED_VARS: list[tuple[str, str, Callable]] = [
    ("number", "Match number", lambda p: float(p.match.number or 0)),
    ("pred_red", "Predicted red points", lambda p: p.red_pts),
    ("pred_blue", "Predicted blue points", lambda p: p.blue_pts),
    ("pred_margin", "Predicted margin (red − blue)", lambda p: p.red_pts - p.blue_pts),
    ("pred_abs", "Predicted margin (absolute)", lambda p: abs(p.red_pts - p.blue_pts)),
    ("pred_total", "Predicted total points", lambda p: p.total),
    ("red_cow", "Red chance of winning (%)", lambda p: p.red_cow * 100),
    ("blue_cow", "Blue chance of winning (%)", lambda p: p.blue_cow * 100),
    ("red_strength", "Red Alliance Strength (fitted win rate %)", lambda p: p.red_strength),
    ("blue_strength", "Blue Alliance Strength (fitted win rate %)", lambda p: p.blue_strength),
    ("closeness", "Closeness (50 − |Win chance − 50|)", lambda p: 50 - abs(p.red_cow * 100 - 50)),
    ("actual_red", "Actual red points (np)", lambda p: _alliance_score(p.match, "RED")),
    ("actual_blue", "Actual blue points (np)", lambda p: _alliance_score(p.match, "BLUE")),
    ("actual_margin", "Actual margin (red − blue, np)", lambda p: _margin(p.match, "RED")),
    ("error", "Margin error (actual − predicted)",
     lambda p: None if _margin(p.match, "RED") is None else _margin(p.match, "RED") - (p.red_pts - p.blue_pts)),
    ("abs_error", "Margin error (absolute)",
     lambda p: None if _margin(p.match, "RED") is None else abs(_margin(p.match, "RED") - (p.red_pts - p.blue_pts))),
    ("strongest", "Strongest team's rating in the match", lambda p: max(p.red_detail + p.blue_detail, default=None)),
    ("weakest", "Weakest team's rating in the match", lambda p: min(p.red_detail + p.blue_detail, default=None)),
]

#: Alliance pairings (core.Alliance): getter(Alliance, my_team)
ALLIANCE_VARS: list[tuple[str, str, Callable]] = [
    ("rank", "Alliance rank", lambda a, me: float(a.rank)),
    ("total", "Total rating (pair)", lambda a, me: a.cap_sum),
    ("stronger", "Stronger member's rating", lambda a, me: max(a.caps)),
    ("weaker", "Weaker member's rating", lambda a, me: min(a.caps)),
    ("balance", "Balance (stronger − weaker)", lambda a, me: max(a.caps) - min(a.caps)),
    ("partner", "Partner's rating (partners view)", lambda a, me: a.partner_cap(me) if me in a.teams else None),
    ("partner_no", "Partner team number (partners view)", lambda a, me: float(a.partner_of(me)) if me in a.teams else None),
    ("beats", "Alliances it beats", lambda a, me: float(a.beats)),
    ("win_rate", "Win rate vs. legal opponents (%)", lambda a, me: a.win_rate * 100),
    ("alliance_strength", "Alliance Strength (fitted win rate %)", lambda a, me: a.alliance_strength),
    ("margin", "Margin over closest opponent", lambda a, me: a.margin),
    ("cow", "Chance of winning vs. closest (%)", lambda a, me: a.cow * 100),
]
PAIR_UNITS = ("Matches on the schedule", "All possible alliances", "Partners for my team")

#: One-paragraph explanations shown under each chart, and the longer text behind "Explain this chart".
EXPLAIN_SHORT = {
    "events": "Each point is one event this team played this season, oldest on the left. Each line is one statistic "
              "measured at that event, so you can see how the team has been trending and whether OPR, CAP and EPA "
              "tell the same story about it. Lines on top of each other = the ratings agree.",
    "matches": "Each point is one of this team's matches at this meet, in schedule order. Lines show what the team "
               "carried into each match (EPA) and how its alliance was predicted to score versus what it actually "
               "scored - the gap between predicted and actual is the error for that match.",
    "all_matches": "Each point is one match this team has played this season, oldest to newest, strung together "
                   "across every event - not just this one. The same per-match lines as the single-event view (EPA "
                   "carried in, predicted vs. actual score) now run the whole season without resetting at each "
                   "event's boundary. Needs the meet loaded \u201cwith history.\u201d",
    "scatter": "Every team at the meet is one dot, placed by the two statistics on the axes. The optional Z "
               "statistic sets the dot's size and colour (big and gold = high, small and navy = low). When both axes "
               "are ratings, the dashed x = y line is where the two agree; the further a dot is from it, the more the "
               "two methods disagree about that team.",
    "compare": "Every team at the meet is one column, lined up from weakest (left) to strongest (right) by the "
               "statistic in 'Order teams by'. Each coloured line is one rating (OPR, CAP, EPA…) for those same teams. "
               "Where the lines sit together, the methods agree and the prediction is on firm ground; where they "
               "split, at least one method is wrong about that team - the grey bar is the size of the disagreement. "
               "The axis labels are the team numbers (a team with no value for the ordering statistic is left out).",
    "pred": "Each point is one match on the schedule, placed by the two statistics on the axes. Green = AutoScout "
            "called it right, red = called it wrong, gold = your team's match, plain = not played yet. Choosing a Z "
            "statistic replaces those colours with a size-and-colour scale.",
    "alliance": "Each point is one possible two-team alliance from the teams at this meet, placed by the two "
                "statistics on the axes (only the strongest N by total rating are drawn - see 'Show top'). Gold = "
                "the pairing out-scores every alliance it could legally face; blue = it includes your team. Pick "
                "Total rating against Win rate (the default) to see the fitted curve and its equation.",
    "partners": "Each point is one possible partner for your team, placed by the two statistics on the axes. Use it "
                "to see which partners give the strongest pairing - top-right is best on the default axes. Gold = "
                "the pairing out-scores every alliance it could face.",
}

EXPLAIN_LONG = {
    "events": """ONE TEAM - EVENTS THIS SEASON

X axis: the events the team has played this season, oldest on the left ("Event (in order played)"),
or placed by calendar date ("Event date").

Y axis / Y2 / Y3: up to three statistics, each drawn as a line with one point per event:
  OPR (non-penalty)         the team's OPR at that event
  CAP (as of that event)    what CAP would have said with only the events played up to then
  EPA (after that event)    the EPA rating once that event's matches had been learned
  Auto / Teleop / Endgame   the OPR breakdown by phase
  Projected OPR             the straight-line projection at that point in the season
  TBP 1/2, Rank, RP, Wins, Losses, Qual matches played   the event's standings numbers

How to read it: a rising line is a team improving through the season. Lines on top of each other
mean the ratings agree about the team; a line that pulls away shows one method reacting to
something the others miss (EPA, for example, only moves once matches are played).

Hover a point for the event and its value; "Save image…" writes the chart to a file.""",
    "matches": """ONE TEAM - MATCHES AT THIS EVENT

X axis: the team's matches on this meet's schedule, in order (or by match number).

Y axis / Y2 / Y3: up to three statistics, one point per match:
  EPA before the match                 the rating the team carried into that match
  Predicted points, my alliance        what AutoScout expected the team's alliance to score
  Predicted points, opponents          the same for the other alliance
  Predicted margin                     my alliance minus opponents, as predicted
  Chance of winning (%)                my alliance's share of the predicted points
  Actual points, my alliance / opponents (np)   what happened, before penalties
  Actual margin                        my alliance minus opponents, actual
  Prediction error                     actual minus predicted for my alliance

How to read it: the vertical gap between "Predicted points" and "Actual points" is the error for
that match. A steady gap means the team is consistently under- or over-rated; a single spike is
usually one bad (or great) match. Matches not yet played have no actual values.""",
    "all_matches": """ONE TEAM - ALL MATCHES THIS SEASON

X axis: every match this team has played this season, oldest first - earlier events' matches,
then this event's, back to back as one continuous timeline instead of resetting at each event.

Y axis / Y2 / Y3: the same per-match statistics as "Matches at this event":
  EPA before the match                 the rating the team carried into that match
  Predicted points, my alliance        what AutoScout expected the team's alliance to score
  Predicted points, opponents          the same for the other alliance
  Predicted margin                     my alliance minus opponents, as predicted
  Chance of winning (%)                my alliance's share of the predicted points
  Actual points, my alliance / opponents (np)   what happened, before penalties
  Actual margin                        my alliance minus opponents, actual
  Prediction error                     actual minus predicted for my alliance

How to read it: this stitches every event's schedule into one timeline, so EPA and performance
can be watched match by match across the whole season. It needs the meet loaded "with history"
so earlier events' match results are available; without that, only this event's matches show up,
same as "Matches at this event". Hover a point for the event, match label and value.""",
    "scatter": """ALL TEAMS - SCATTER

Every team at the meet is one dot. X axis and Y axis can be any per-team statistic: OPR, CAP,
EPA, the prediction rating in use, Auto, Teleop, Endgame, AVG, NEW AVG, FTC AVG, Projected OPR,
EOPR, CAP − OPR, EPA − OPR, EPA − CAP, rating spread, projected rank and RP, wins, events played,
matches EPA has learned from, rank at the source event, Technical issues adjustment.

Z (size + colour): an optional third statistic. Bigger, more golden dots have a higher value;
small navy dots a lower one. "- none -" turns it off, and your own team is then drawn in gold.

The dashed x = y line appears when both axes are ratings: dots on the line are teams the two
statistics agree about; the further from the line, the bigger the disagreement.

Suggested views:
  X = OPR, Y = CAP, Z = EPA          do the three ratings converge?
  X = OPR, Y = EPA − OPR             who does EPA rate much higher or lower than OPR?
  X = Projected rank, Y = CAP        is the standings projection driven by rating or by schedule?
  X = Auto, Y = Teleop, Z = Endgame  what kind of robot each team is

"Label points" writes the team number beside each dot. Click a dot to open that team.""",
    "compare": """ALL TEAMS - COMPARE RATINGS (WHERE THEY CONVERGE)

What each part means:
  Columns (X axis)     one column per team at the meet, lined up from weakest on the left to
                       strongest on the right by the statistic chosen in "Order teams by" (OPR
                       unless you change it). The axis labels are the team numbers. A team with
                       no value for that statistic (e.g. no Projected OPR yet) is left out and
                       the title says how many.
  Coloured lines       one line per ticked rating (OPR, CAP, EPA, NEW AVG, Projected OPR),
                       giving that rating's value for each team in the same column order.
  Grey bars            the disagreement for each team: the gap between the highest and lowest
                       of the ticked ratings. "show disagreement bars" turns them off.
  Y axis               points (a team's rating is the points it is worth to its alliance).

How to read it:
  * Where the lines sit on top of each other, the methods agree about that team and a
    prediction involving it is on firm ground.
  * Where they split, at least one method is wrong about that team. Look at which line is the
    odd one out: EPA above the others means the team has been out-performing its OPR in real
    matches; EPA below means it has been scoring less than its OPR suggests (or its partners
    have been carrying it).
  * The status line under the chart gives the median and widest disagreement in points.
  * Big disagreements near the right-hand (strong) end matter most: those teams decide matches.

The Y axis and Z boxes do not apply in this view, which is why they are hidden. Click a point
to open that team on the Teams tab.""",
    "pred": """MATCHES & ALLIANCES - MATCHES ON THE SCHEDULE

Each point is one match. Axis choices: match number, predicted red / blue points, predicted
margin (signed or absolute), predicted total, red / blue chance of winning, closeness (how near
50/50 the call is), actual red / blue points, actual margin, margin error (actual minus
predicted, signed or absolute), strongest / weakest single rating in the match.

Colours: green = called correctly, red = called wrong, gold = a match with your team, plain =
not played yet. Choosing a Z statistic replaces the colours with the size-and-colour scale.

Actual points are non-penalty scores ("np"). Suggested views:
  X = Predicted red points, Y = Predicted blue points, Z = Predicted margin (absolute)
      dots near the diagonal are toss-ups; big gold dots are blow-outs
  X = Match number, Y = Closeness (50 − |Win chance − 50|)   when in the day the tight matches fall
  X = Predicted margin (red − blue), Y = Actual margin (red − blue, np)
      a straight diagonal is perfect prediction; dots in the wrong quadrant are the wrong calls
  X = Match number, Y = Margin error (absolute)   did the predictions improve as the meet went on?

Click a point to open that match on the Matches tab.""",
    "alliance": """MATCHES & ALLIANCES - ALL POSSIBLE ALLIANCES

Each point is one two-team pairing of teams at the meet. "Show top" limits the chart to the
strongest N pairings by total rating ("all" draws every one). Axis choices: alliance rank,
total rating of the pair, the stronger and the weaker member's rating, the balance between
them, how many other alliances it beats, its win rate against every alliance it could legally
face (no shared team), its margin over the closest of those, and its chance of winning against
that closest opponent.

Colours: gold = the pairing out-scores every alliance it could face ("unbeatable"); blue = it
includes your team. With a Z statistic chosen, size and colour show that statistic instead.

The default view plots Total rating against Win rate - win rate is really each alliance's
percentile rank by Total CAP, i.e. the CDF of Total CAP over every possible alliance here, so
the chart overlays a curve fitted fresh to this meet's own alliances (a shifted log-normal CDF:
Win% = 100*Phi[(ln(Total CAP + shift) - mu) / sigma]), with its equation and R\u00b2 printed on
the chart. "Alliance Strength" is that same fitted curve applied to a Total CAP value, i.e. a
smoothed version of Win rate that also extrapolates sensibly between alliances - it's the same
number shown for each scheduled match's red and blue alliance on the Matches tab and in the
Predicted-matches chart. Click a point to open the first team of the pair.""",
    "partners": """MATCHES & ALLIANCES - PARTNERS FOR MY TEAM

Each point is one possible partner for your team (set on the Setup tab). Axis choices are the
same as for all alliances, plus the partner's own rating and team number.

Default view: the partner's rating (X) against the pairing's win rate (Y), sized by the margin
over the closest opponent - the top-right dots are the partners to talk to first. Gold = the
pairing out-scores every alliance it could face. Click a point to open that partner.""",
}



def _labels(vars_: list) -> list[str]:
    return [lab for _, lab, _ in vars_]


def _by_label(vars_: list) -> dict:
    return {lab: fn for _, lab, fn in vars_}


def _key_by_label(vars_: list) -> dict:
    return {lab: k for k, lab, _ in vars_}


#: Axis-key pairs (either order) that plot Total CAP against a win-rate-shaped statistic -
#: when picked, the alliances/partners chart overlays the fitted StrengthFit curve.
STRENGTH_CURVE_KEYS = ({"total", "win_rate"}, {"total", "alliance_strength"})


# --------------------------------------------------------------------------- #
# Per-team series
# --------------------------------------------------------------------------- #
def team_event_rows(session: Session, team: int) -> list[tuple[EventStat, Optional[TeamStats], dict]]:
    """A team's events in the order played, each with the CAP/Proj-OPR "as of" that
    event and the EPA snapshot after it (from the earlier-events replay)."""
    hist = [h for h in session.history.get(team, []) if h.opr_np or h.qual_played]
    hist.sort(key=lambda h: (h.start_date or "9999",))
    st = session.settings
    manual = session.manual.get(team)
    # EPA after each earlier event, then after this event's played matches
    model = new_epa_model(st)
    snaps: dict[str, float] = {}
    for ev in sorted(getattr(session, "epa_earlier", []), key=lambda e: (e.start or "9999", e.code)):
        for m in ev.matches:
            model.observe(m)
        snaps[ev.code] = model.value(team)
    for m in session.matches:
        if st.epa_live and m.played:
            model.observe(m)
    if session.event_code:
        snaps[session.event_code] = model.value(team)
    rows = []
    current = session.teams.get(team)
    for i, e in enumerate(hist):
        asof = team_stats_from_history(team, hist[:i + 1], st, session.team_names.get(team, ""), session.event_code)
        if manual:
            asof.tdc_issue, asof.tdc_delta, asof.focus = manual.tdc_issue, manual.tdc_delta, manual.focus
        epa = snaps.get(e.event_code)
        if e.event_code == session.event_code and current is not None and getattr(current, "edited", False):
            # the point for *this* meet is what the heading and the predictions use: hand-edited
            # inputs apply here (earlier events keep their recorded values)
            for key in ("opr", "auto", "teleop", "endgame", "proj_opr"):
                v = getattr(manual, key, None) if manual else None
                if v is not None:
                    setattr(asof, key, float(v))
            if manual and manual.epa is not None:
                epa = float(manual.epa)
        rows.append((e, asof, {"epa": epa}))
    return rows


def team_match_rows(session: Session, team: int) -> list[tuple[MyMatch, Optional[float], Prediction, Match]]:
    """A team's matches at this event with the EPA it carried into each one."""
    values = getattr(session, "epa_values", None) or []
    out = []
    for i, p in enumerate(session.predictions):
        m = p.match
        color = m.color_of(team)
        if not color:
            continue
        mine = m.red if color == "RED" else m.blue
        opp = m.blue if color == "RED" else m.red
        cow = p.cow_for(color)
        w = m.winner
        actual = None if w is None else ("Tie" if w == "TIE" else "Win" if w == color else "Loss")
        mm = MyMatch(p, color, tuple(n for n in mine if n != team), opp, cow, match_rating(cow),
                     p.winner == color, actual)
        epa = None
        if i < len(values):
            side = values[i][0] if color == "RED" else values[i][1]
            members = m.red if color == "RED" else m.blue
            if team in members and len(side) == len(members):
                epa = _opt(side[members.index(team)])
        out.append((mm, epa, p, m))
    return out


def team_all_match_rows(session: Session, team: int) -> list[tuple[str, MyMatch, Optional[float], Prediction, Match]]:
    """A team's matches across every event loaded this season - earlier events, then this
    one - oldest first, each with the EPA it carried into that match. Needs the meet loaded
    "with history" (`session.epa_earlier`); without it, this is just this event's matches."""
    prior = getattr(session, "epa_earlier", None) or []
    rows = build_epa_history(prior, session.matches, session.event_code, session.settings)
    matches_all = [m for (_code, m, _r, _b) in rows]
    values_all = [(r, b) for (_code, m, r, b) in rows]
    preds_all = predict_from_values(matches_all, values_all)
    out = []
    for (code, m, red, blue), p in zip(rows, preds_all):
        color = m.color_of(team)
        if not color:
            continue
        mine = m.red if color == "RED" else m.blue
        opp = m.blue if color == "RED" else m.red
        cow = p.cow_for(color)
        w = m.winner
        actual = None if w is None else ("Tie" if w == "TIE" else "Win" if w == color else "Loss")
        mm = MyMatch(p, color, tuple(n for n in mine if n != team), opp, cow, match_rating(cow),
                     p.winner == color, actual)
        side = red if color == "RED" else blue
        members = m.red if color == "RED" else m.blue
        epa = _opt(side[members.index(team)]) if team in members and len(side) == len(members) else None
        out.append((code, mm, epa, p, m))
    return out


# --------------------------------------------------------------------------- #
# One team's chart + the "edit data inputs" block
# --------------------------------------------------------------------------- #
OVERRIDE_FIELDS = [("opr", "OPR"), ("auto", "Auto"), ("teleop", "Teleop"),
                   ("endgame", "Endgame"), ("proj_opr", "Projected OPR"), ("epa", "EPA")]


class TeamChartPanel(ttk.Frame):
    """One team's statistics over its events (this season), its matches (this event), or
    every match it has played this season back to back, with pickable axes, up to three Y
    series, and the inputs that feed CAP editable underneath. Used as the Teams tab's side
    menu and as the Charts tab's first page."""

    DOMAINS = ("Events this season", "Matches at this event", "All matches this season")

    def __init__(self, master, app: "App", show_team_entry: bool = True, chart_height: int = 300,
                 show_editor: bool = True):
        super().__init__(master, padding=6)
        self.app = app
        self.team: int = 0
        self._editing = 0

        head = ttk.Frame(self)
        head.pack(fill="x")
        self.head_var = tk.StringVar(value="No team selected.")
        ttk.Label(head, textvariable=self.head_var, font=app.font_bold).pack(side="left")
        if show_team_entry:
            ttk.Label(head, text="Team ").pack(side="left", padx=(12, 0))
            self.team_var = tk.StringVar()
            entry = ttk.Entry(head, textvariable=self.team_var, width=8)
            entry.pack(side="left")
            entry.bind("<Return>", lambda _e: self.set_team(as_int(self.team_var.get())))
            ttk.Button(head, text="Show", command=lambda: self.set_team(as_int(self.team_var.get()))).pack(
                side="left", padx=4)
        else:
            self.team_var = None

        ctl = ttk.LabelFrame(self, text="What to chart", padding=8)
        ctl.pack(fill="x", pady=(6, 2))
        self.domain_var = tk.StringVar(value=self.DOMAINS[0])
        self.x_var = tk.StringVar(value=EVENT_X_VARS[0][0])
        self.y_vars = [tk.StringVar(value="OPR (non-penalty)"), tk.StringVar(value="CAP (as of that event)"),
                       tk.StringVar(value="EPA (after that event)")]
        ttk.Label(ctl, text="Chart").grid(row=0, column=0, sticky="w")
        self.domain_box = ttk.Combobox(ctl, textvariable=self.domain_var, values=self.DOMAINS,
                                       state="readonly", width=22)
        self.domain_box.grid(row=0, column=1, sticky="w", padx=(4, 10))
        ttk.Label(ctl, text="X axis").grid(row=0, column=2, sticky="w")
        self.x_box = ttk.Combobox(ctl, textvariable=self.x_var, values=[l for l, _ in EVENT_X_VARS],
                                  state="readonly", width=20)
        self.x_box.grid(row=0, column=3, sticky="w", padx=(4, 0))
        self.y_boxes = []
        for i, var in enumerate(self.y_vars):
            ttk.Label(ctl, text="Y axis" if i == 0 else f"Y{i + 1} (optional)").grid(
                row=1 + i, column=0, columnspan=1, sticky="w", pady=(4, 0))
            box = ttk.Combobox(ctl, textvariable=var, values=EVENT_Y_LABELS if i == 0 else [NONE_LABEL] + EVENT_Y_LABELS,
                               state="readonly", width=34)
            box.grid(row=1 + i, column=1, columnspan=3, sticky="w", padx=(4, 0), pady=(4, 0))
            box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
            self.y_boxes.append(box)
        self.domain_box.bind("<<ComboboxSelected>>", lambda _e: self._domain_changed())
        self.x_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        btns = ttk.Frame(ctl)
        btns.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Button(btns, text="OPR vs CAP vs EPA", command=self.preset_ratings).pack(side="left")
        ttk.Button(btns, text="Auto / Teleop / Endgame", command=self.preset_phases).pack(side="left", padx=4)
        ttk.Button(btns, text="Save image…", command=self.save_image).pack(side="left", padx=4)

        self.chart = ChartCanvas(self, height=chart_height, title_font=app.font_bold)
        self.chart.pack(fill="both", expand=True, pady=(6, 0))
        exp = ttk.Frame(self)
        exp.pack(fill="x", pady=(4, 0))
        ttk.Button(exp, text="Explain this chart", command=self.explain).pack(side="right", padx=(8, 0))
        self.explain_var = tk.StringVar(value=EXPLAIN_SHORT["events"])
        ttk.Label(exp, textvariable=self.explain_var, foreground=UI_MUTED, justify="left",
                  wraplength=max(360, chart_height * 2)).pack(side="left", fill="x", expand=True)

        self.editor = None
        if show_editor:
            self._build_editor()

    # -- editor -------------------------------------------------------------
    def _build_editor(self) -> None:
        ed = ttk.LabelFrame(self, text="Edit this team's data inputs (blank = value from FTCScout)", padding=8)
        ed.pack(fill="x", pady=(8, 0))
        self.editor = ed
        self.override_vars: dict[str, tk.StringVar] = {}
        self.current_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(OVERRIDE_FIELDS):
            r, c = i // 3, (i % 3) * 3
            ttk.Label(ed, text=label).grid(row=r, column=c, sticky="w", padx=(0, 4), pady=2)
            var = tk.StringVar()
            e = ttk.Entry(ed, textvariable=var, width=8)
            e.grid(row=r, column=c + 1, sticky="w", pady=2)
            e.bind("<Return>", lambda _e: self.apply_overrides())
            cur = tk.StringVar()
            ttk.Label(ed, textvariable=cur, foreground=UI_MUTED, width=9).grid(row=r, column=c + 2, sticky="w", padx=(2, 10))
            self.override_vars[key] = var
            self.current_vars[key] = cur
        row = ttk.Frame(ed)
        row.grid(row=2, column=0, columnspan=9, sticky="we", pady=(6, 0))
        ttk.Button(row, text="Apply", command=self.apply_overrides).pack(side="left")
        ttk.Button(row, text="Reset to FTCScout", command=self.reset_overrides).pack(side="left", padx=6)
        self.edit_status = tk.StringVar(value="Overrides feed CAP, the predictions and the charts for this meet "
                                              "(earlier events keep their recorded values); saved with the session.")
        ttk.Label(row, textvariable=self.edit_status, foreground=UI_MUTED, wraplength=520, justify="left").pack(
            side="left", padx=(8, 0))

    def _fill_editor(self) -> None:
        if self.editor is None:
            return
        t = self.app.session.teams.get(self.team)
        m = self.app.session.manual.get(self.team)
        self._editing += 1
        try:
            for key, _ in OVERRIDE_FIELDS:
                ov = getattr(m, key, None) if m else None
                self.override_vars[key].set("" if ov is None else f"{ov:g}")
                cur = getattr(t, key, None) if t else None
                self.current_vars[key].set(f"({f1(cur)})" if cur is not None else "(n/a)")
        finally:
            self._editing -= 1

    def apply_overrides(self) -> None:
        if not self.team or self._editing:
            return
        m = self.app.session.manual_for(self.team)
        changed = []
        for key, label in OVERRIDE_FIELDS:
            text = self.override_vars[key].get().strip()
            val = None if text == "" else as_float(text, None)
            if text and val is None:
                self.edit_status.set(f"{label}: '{text}' is not a number.")
                return
            setattr(m, key, val)
            if val is not None:
                changed.append(label)
        self.edit_status.set("Using your values for " + ", ".join(changed) + "." if changed
                             else "No overrides - all values come from FTCScout.")
        self.app.recompute()
        self.app.select_team(self.team, source=self)

    def reset_overrides(self) -> None:
        if not self.team:
            return
        m = self.app.session.manual_for(self.team)
        for key, _ in OVERRIDE_FIELDS:
            setattr(m, key, None)
        self.edit_status.set("Reset: all values come from FTCScout again.")
        self.app.recompute()
        self.app.select_team(self.team, source=self)

    # -- controls -------------------------------------------------------------
    def _domain_kind(self) -> str:
        """'events' | 'matches' | 'all_matches', from the current Chart selection."""
        d = self.domain_var.get()
        if d == self.DOMAINS[1]:
            return "matches"
        if d == self.DOMAINS[2]:
            return "all_matches"
        return "events"

    def _domain_changed(self) -> None:
        kind = self._domain_kind()
        x_vars = {"events": EVENT_X_VARS, "matches": MATCH_X_VARS, "all_matches": ALL_MATCH_X_VARS}[kind]
        xs = [l for l, _ in x_vars]
        ys = EVENT_Y_LABELS if kind == "events" else MATCH_Y_LABELS
        self.x_box.configure(values=xs)
        self.x_var.set(xs[0])
        for i, box in enumerate(self.y_boxes):
            box.configure(values=ys if i == 0 else [NONE_LABEL] + ys)
        if kind == "events":
            self.preset_ratings(refresh=False)
        else:
            self.y_vars[0].set("EPA before the match")
            self.y_vars[1].set("Pred. points, my alliance")
            self.y_vars[2].set("Actual points, my alliance (np)")
        self.refresh()

    def preset_ratings(self, refresh: bool = True) -> None:
        self.domain_var.set(self.DOMAINS[0])
        self.x_box.configure(values=[l for l, _ in EVENT_X_VARS])
        for i, box in enumerate(self.y_boxes):
            box.configure(values=EVENT_Y_LABELS if i == 0 else [NONE_LABEL] + EVENT_Y_LABELS)
        self.y_vars[0].set("OPR (non-penalty)")
        self.y_vars[1].set("CAP (as of that event)")
        self.y_vars[2].set("EPA (after that event)")
        if refresh:
            self.refresh()

    def preset_phases(self) -> None:
        self.domain_var.set(self.DOMAINS[0])
        self.x_box.configure(values=[l for l, _ in EVENT_X_VARS])
        for i, box in enumerate(self.y_boxes):
            box.configure(values=EVENT_Y_LABELS if i == 0 else [NONE_LABEL] + EVENT_Y_LABELS)
        self.y_vars[0].set("Auto")
        self.y_vars[1].set("Teleop")
        self.y_vars[2].set("Endgame")
        self.refresh()

    def save_image(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save chart", defaultextension=".ps",
                                            initialfile=f"team_{self.team or 'chart'}.ps",
                                            filetypes=[("PostScript", "*.ps")])
        if path:
            try:
                self.chart.save_postscript(path)
            except tk.TclError as e:
                messagebox.showerror(APP_NAME, f"Could not save the chart.\n\n{e}", parent=self)

    def explain(self) -> None:
        messagebox.showinfo("What this chart shows", EXPLAIN_LONG[self._domain_kind()], parent=self)

    def set_team(self, team: int) -> None:
        self.team = int(team or 0)
        if self.team_var is not None:
            self.team_var.set(str(self.team) if self.team else "")
        self.refresh()

    # -- drawing --------------------------------------------------------------
    def refresh(self) -> None:
        s = self.app.session
        t = s.teams.get(self.team)
        if not self.team or t is None:
            self.head_var.set("No team selected." if not self.team else f"Team {self.team} is not at this event.")
            self.chart.clear("Pick a team to chart its statistics.")
            if self.editor is not None:
                self._editing += 1
                for v in list(self.override_vars.values()) + list(self.current_vars.values()):
                    v.set("")
                self._editing -= 1
            return
        edited = " · edited" if getattr(t, "edited", False) else ""
        self.head_var.set(f"Team {t.number} {t.name}".strip() + f" - OPR {f1(t.opr)} · CAP {f1(t.cap)} · EPA {f1(t.epa)}{edited}")
        self._fill_editor()
        kind = self._domain_kind()
        self.explain_var.set(EXPLAIN_SHORT[kind])
        chosen = [v.get() for v in self.y_vars if v.get() and v.get() != NONE_LABEL]
        series: list[ChartSeries] = []
        x_ticks = None
        if kind == "events":
            rows = team_event_rows(s, self.team)
            by_date = self.x_var.get() == "Event date"
            ticks = []
            for i, (e, asof, ex) in enumerate(rows):
                x = float(i + 1)
                if by_date and e.start_date:
                    try:
                        import datetime
                        x = float(datetime.date.fromisoformat(e.start_date[:10]).toordinal())
                    except ValueError:
                        x = float(i + 1)
                while any(abs(x - tx) < 0.05 for tx, _l in ticks):
                    x += 0.35                       # two events on one date: keep both visible
                ticks.append((x, e.start_date[5:10] if by_date and e.start_date else (e.event_code or f"#{i + 1}")))
            x_ticks = ticks
            for j, lab in enumerate(chosen):
                fn = EVENT_Y_BY_LABEL.get(lab)
                if not fn:
                    continue
                pts = []
                for (x, _lab), (e, asof, ex) in zip(ticks, rows):
                    y = _opt(fn(e, asof, ex))
                    if y is None:
                        continue
                    pts.append(ChartPoint(x, y, text=f"{e.event_code} {e.event_name[:28]}\n{lab}: {y:.1f}"
                                          + (f"\nrank {e.rank}, {e.wins}-{e.losses}" if e.rank else ""), key=e.event_code))
                series.append(ChartSeries(lab, pts, CHART_COLORS[j % len(CHART_COLORS)], kind="line"))
            title = f"Team {t.number} across {len(rows)} event{'s' if len(rows) != 1 else ''} this season"
            note = "" if rows else "No season history for this team (load the meet with history)."
        elif kind == "matches":
            rows = team_match_rows(s, self.team)
            by_number = self.x_var.get() == "Match number"
            ticks = [((float(m.number) if by_number and m.number else float(i + 1)), m.label)
                     for i, (mm, epa, p, m) in enumerate(rows)]
            x_ticks = ticks
            for j, lab in enumerate(chosen):
                fn = MATCH_Y_BY_LABEL.get(lab)
                if not fn:
                    continue
                pts = []
                for (x, _lab), (mm, epa, p, m) in zip(ticks, rows):
                    y = _opt(fn(mm, epa, p, m))
                    if y is None:
                        continue
                    pts.append(ChartPoint(x, y, key=m.label,
                                          text=f"{m.label} {mm.color} with {', '.join(map(str, mm.partner))} vs {', '.join(map(str, mm.opponents))}"
                                               f"\n{lab}: {y:.1f}" + (f"\nactual: {mm.actual}" if mm.actual else "")))
                series.append(ChartSeries(lab, pts, CHART_COLORS[j % len(CHART_COLORS)], kind="line"))
            title = f"Team {t.number} across its {len(rows)} matches at {s.event_code or 'this event'}"
            note = "" if rows else "This team has no matches on the schedule."
        else:  # all_matches
            rows = team_all_match_rows(s, self.team)
            ticks = [(float(i + 1), f"{code} {m.label}") for i, (code, mm, epa, p, m) in enumerate(rows)]
            x_ticks = ticks
            for j, lab in enumerate(chosen):
                fn = MATCH_Y_BY_LABEL.get(lab)
                if not fn:
                    continue
                pts = []
                for (x, _lab), (code, mm, epa, p, m) in zip(ticks, rows):
                    y = _opt(fn(mm, epa, p, m))
                    if y is None:
                        continue
                    pts.append(ChartPoint(x, y, key=m.label,
                                          text=f"{code} {m.label} {mm.color} with {', '.join(map(str, mm.partner))} vs {', '.join(map(str, mm.opponents))}"
                                               f"\n{lab}: {y:.1f}" + (f"\nactual: {mm.actual}" if mm.actual else "")))
                series.append(ChartSeries(lab, pts, CHART_COLORS[j % len(CHART_COLORS)], kind="line"))
            title = f"Team {t.number} across all {len(rows)} match{'es' if len(rows) != 1 else ''} played this season"
            note = "" if rows else "No cross-event match history for this team (load the meet with history)."
        self.chart.set_data(title, self.x_var.get(), chosen[0] if len(chosen) == 1 else "",
                            series, x_ticks=x_ticks, note=note)


# --------------------------------------------------------------------------- #
# The Charts tab
# --------------------------------------------------------------------------- #
class ChartsTab(Tab):
    """Charts: one team over time, every team at once (where OPR, CAP and EPA agree
    or disagree), and the schedule's matches or the possible alliance pairings -
    each with pickable axes and a third variable as marker size and colour."""

    title = "Charts"

    def build(self) -> None:
        self.nb = ttk.Notebook(self, style="Charts.TNotebook")
        self.nb.pack(fill="both", expand=True)
        self._build_team_page()
        self._build_all_page()
        self._build_pairs_page()
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self.refresh())

    # -- page 1: one team --------------------------------------------------------
    def _build_team_page(self) -> None:
        page = ttk.Frame(self.nb, padding=(4, 8, 4, 4))
        self.nb.add(page, text="  One team  ")
        self.team_panel = TeamChartPanel(page, self.app, show_team_entry=True, chart_height=340)
        self.team_panel.pack(fill="both", expand=True)

    # -- page 2: all teams ----------------------------------------------------------
    def _build_all_page(self) -> None:
        page = ttk.Frame(self.nb, padding=(4, 8, 4, 4))
        self.nb.add(page, text="  All teams  ")
        ctl = ttk.LabelFrame(page, text="What to chart", padding=8)
        ctl.pack(fill="x")
        self.view_var = tk.StringVar(value="Scatter")
        ttk.Label(ctl, text="View").grid(row=0, column=0, sticky="w")
        vb = ttk.Combobox(ctl, textvariable=self.view_var, state="readonly", width=30,
                          values=["Scatter", "Compare ratings (where they converge)"])
        vb.grid(row=0, column=1, sticky="w", padx=(4, 12))
        vb.bind("<<ComboboxSelected>>", lambda _e: self._view_changed())
        self.ax_var = tk.StringVar(value="OPR (non-penalty)")
        self.ay_var = tk.StringVar(value="CAP")
        self.az_var = tk.StringVar(value="EPA")
        self.ax_label = ttk.Label(ctl, text="X axis")
        self.ax_label.grid(row=0, column=2, sticky="w")
        self.ax_box = ttk.Combobox(ctl, textvariable=self.ax_var, values=TEAM_VAR_LABELS, state="readonly", width=30)
        self.ax_box.grid(row=0, column=3, sticky="w", padx=(4, 12))
        self.ay_label = ttk.Label(ctl, text="Y axis")
        self.ay_label.grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.ay_box = ttk.Combobox(ctl, textvariable=self.ay_var, values=TEAM_VAR_LABELS, state="readonly", width=30)
        self.ay_box.grid(row=1, column=3, sticky="w", padx=(4, 12), pady=(4, 0))
        self.az_label = ttk.Label(ctl, text="Z (size + colour)")
        self.az_label.grid(row=1, column=4, sticky="w", pady=(4, 0))
        self.az_box = ttk.Combobox(ctl, textvariable=self.az_var, values=[NONE_LABEL] + TEAM_VAR_LABELS,
                                   state="readonly", width=30)
        self.az_box.grid(row=1, column=5, sticky="w", padx=(4, 0), pady=(4, 0))
        for b in (self.ax_box, self.ay_box, self.az_box):
            b.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.label_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctl, text="Label points", variable=self.label_var, command=self.refresh).grid(
            row=0, column=4, columnspan=2, sticky="w")
        # compare-mode series pickers
        self.cmp_frame = ttk.Frame(ctl)
        self.cmp_frame.grid(row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Label(self.cmp_frame, text="Ratings to compare:").pack(side="left")
        self.cmp_vars: dict[str, tk.BooleanVar] = {}
        for label, key in RATING_SERIES:
            v = tk.BooleanVar(value=key in ("opr", "cap", "epa"))
            ttk.Checkbutton(self.cmp_frame, text=label, variable=v, command=self.refresh).pack(side="left", padx=(6, 0))
            self.cmp_vars[key] = v
        self.spread_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.cmp_frame, text="show disagreement bars", variable=self.spread_var,
                        command=self.refresh).pack(side="left", padx=(12, 0))
        ttk.Button(ctl, text="Save image…", command=lambda: self._save(self.all_chart, "all_teams")).grid(
            row=0, column=6, sticky="e", padx=(12, 0))
        ctl.columnconfigure(6, weight=1)
        self.all_chart = ChartCanvas(page, height=380, on_click=lambda key: self.app.select_team(key, source=self),
                                     title_font=self.app.font_bold)
        self.all_chart.pack(fill="both", expand=True, pady=(8, 0))
        self.all_status = tk.StringVar(value="Click a point to open that team on the Teams tab.")
        ttk.Label(page, textvariable=self.all_status, foreground=UI_NAVY, wraplength=1100, justify="left").pack(
            anchor="w", pady=(4, 0))
        exp = ttk.Frame(page)
        exp.pack(fill="x", pady=(2, 0))
        ttk.Button(exp, text="Explain this chart", command=lambda: messagebox.showinfo(
            "What this chart shows", EXPLAIN_LONG["compare" if self.view_var.get().startswith("Compare") else "scatter"],
            parent=self)).pack(side="right", padx=(8, 0))
        self.all_explain = tk.StringVar(value=EXPLAIN_SHORT["scatter"])
        ttk.Label(exp, textvariable=self.all_explain, foreground=UI_MUTED, wraplength=1100, justify="left").pack(
            side="left", fill="x", expand=True)
        self._view_changed(refresh=False)

    def _view_changed(self, refresh: bool = True) -> None:
        compare = self.view_var.get().startswith("Compare")
        if compare:
            self.ax_label.configure(text="Order teams by")
            for wdg in (self.ay_label, self.ay_box, self.az_label, self.az_box):
                wdg.grid_remove()                      # Y and Z do not apply: the lines are the ratings
            self.cmp_frame.grid()
            self.all_explain.set(EXPLAIN_SHORT["compare"])
        else:
            self.ax_label.configure(text="X axis")
            for wdg in (self.ay_label, self.ay_box, self.az_label, self.az_box):
                wdg.grid()
            self.cmp_frame.grid_remove()
            self.all_explain.set(EXPLAIN_SHORT["scatter"])
        if refresh:
            self.refresh()

    # -- page 3: matches & alliances ---------------------------------------------
    def _build_pairs_page(self) -> None:
        page = ttk.Frame(self.nb, padding=(4, 8, 4, 4))
        self.nb.add(page, text="  Matches & alliances  ")
        ctl = ttk.LabelFrame(page, text="What to chart", padding=8)
        ctl.pack(fill="x")
        self.unit_var = tk.StringVar(value=PAIR_UNITS[0])
        ttk.Label(ctl, text="Chart").grid(row=0, column=0, sticky="w")
        ub = ttk.Combobox(ctl, textvariable=self.unit_var, values=PAIR_UNITS, state="readonly", width=26)
        ub.grid(row=0, column=1, sticky="w", padx=(4, 12))
        ub.bind("<<ComboboxSelected>>", lambda _e: self._unit_changed())
        self.px_var = tk.StringVar()
        self.py_var = tk.StringVar()
        self.pz_var = tk.StringVar(value=NONE_LABEL)
        ttk.Label(ctl, text="X axis").grid(row=0, column=2, sticky="w")
        self.px_box = ttk.Combobox(ctl, textvariable=self.px_var, state="readonly", width=34)
        self.px_box.grid(row=0, column=3, sticky="w", padx=(4, 12))
        ttk.Label(ctl, text="Y axis").grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.py_box = ttk.Combobox(ctl, textvariable=self.py_var, state="readonly", width=34)
        self.py_box.grid(row=1, column=3, sticky="w", padx=(4, 12), pady=(4, 0))
        ttk.Label(ctl, text="Z (size + colour)").grid(row=1, column=4, sticky="w", pady=(4, 0))
        self.pz_box = ttk.Combobox(ctl, textvariable=self.pz_var, state="readonly", width=34)
        self.pz_box.grid(row=1, column=5, sticky="w", padx=(4, 0), pady=(4, 0))
        for b in (self.px_box, self.py_box, self.pz_box):
            b.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        self.plabel_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctl, text="Label points", variable=self.plabel_var, command=self.refresh).grid(
            row=0, column=4, columnspan=2, sticky="w")
        self.top_var = tk.StringVar(value="80")
        self.top_label = ttk.Label(ctl, text="Show top")
        self.top_label.grid(row=2, column=2, sticky="w", pady=(4, 0))
        tb = ttk.Combobox(ctl, textvariable=self.top_var, values=["20", "40", "80", "150", "300", "all"],
                          state="readonly", width=6)
        tb.grid(row=2, column=3, sticky="w", padx=(4, 0), pady=(4, 0))
        self.top_box = tb
        tb.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Button(ctl, text="Save image…", command=lambda: self._save(self.pair_chart, "matches")).grid(
            row=0, column=6, sticky="e", padx=(12, 0))
        ctl.columnconfigure(6, weight=1)
        self.pair_chart = ChartCanvas(page, height=380, on_click=self._pair_clicked, title_font=self.app.font_bold)
        self.pair_chart.pack(fill="both", expand=True, pady=(8, 0))
        self.pair_status = tk.StringVar()
        ttk.Label(page, textvariable=self.pair_status, foreground=UI_NAVY, wraplength=1100, justify="left").pack(
            anchor="w", pady=(4, 0))
        exp = ttk.Frame(page)
        exp.pack(fill="x", pady=(2, 0))
        ttk.Button(exp, text="Explain this chart", command=lambda: messagebox.showinfo(
            "What this chart shows", EXPLAIN_LONG[self._pair_key()], parent=self)).pack(side="right", padx=(8, 0))
        self.pair_explain = tk.StringVar(value=EXPLAIN_SHORT["pred"])
        ttk.Label(exp, textvariable=self.pair_explain, foreground=UI_MUTED, wraplength=1100, justify="left").pack(
            side="left", fill="x", expand=True)
        self._unit_changed(refresh=False)

    def _pair_key(self) -> str:
        unit = self.unit_var.get()
        return "pred" if unit == PAIR_UNITS[0] else "alliance" if unit == PAIR_UNITS[1] else "partners"

    def _unit_changed(self, refresh: bool = True) -> None:
        unit = self.unit_var.get()
        if unit == PAIR_UNITS[0]:
            vars_, dx, dy, dz = PRED_VARS, "Predicted red points", "Predicted blue points", "Predicted margin (absolute)"
        elif unit == PAIR_UNITS[1]:
            vars_, dx, dy, dz = ALLIANCE_VARS, "Total rating (pair)", "Win rate vs. legal opponents (%)", "Alliance Strength (fitted win rate %)"
        else:
            vars_, dx, dy, dz = ALLIANCE_VARS, "Partner's rating (partners view)", "Win rate vs. legal opponents (%)", "Margin over closest opponent"
        labs = _labels(vars_)
        self.pair_explain.set(EXPLAIN_SHORT[self._pair_key()])
        for wdg in (self.top_label, self.top_box):     # only alliances/partners are trimmed to the top N
            (wdg.grid if unit != PAIR_UNITS[0] else wdg.grid_remove)()
        self.px_box.configure(values=labs)
        self.py_box.configure(values=labs)
        self.pz_box.configure(values=[NONE_LABEL] + labs)
        self.px_var.set(dx)
        self.py_var.set(dy)
        self.pz_var.set(dz)
        if refresh:
            self.refresh()

    def _pair_clicked(self, key) -> None:
        if isinstance(key, int):
            self.app.select_team(key, source=self)
        elif isinstance(key, tuple) and key:
            self.app.select_team(key[0], source=self)
        elif isinstance(key, str):
            self.app.select_match(key)

    def _save(self, chart: ChartCanvas, stem: str) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save chart", defaultextension=".ps",
                                            initialfile=f"{stem}.ps", filetypes=[("PostScript", "*.ps")])
        if path:
            try:
                chart.save_postscript(path)
            except tk.TclError as e:
                messagebox.showerror(APP_NAME, f"Could not save the chart.\n\n{e}", parent=self)

    # -- refresh ----------------------------------------------------------------------
    def refresh(self) -> None:
        s = self.session
        if not s.teams:
            self.team_panel.refresh()
            self.all_chart.clear("Load a meet on the Setup tab first.")
            self.pair_chart.clear("Load a meet on the Setup tab first.")
            return
        if self.team_panel.team not in s.teams and s.my_team in s.teams:
            self.team_panel.set_team(s.my_team)
        else:
            self.team_panel.refresh()
        self._refresh_all()
        self._refresh_pairs()

    def _ctx(self) -> dict:
        return {"standing": {r.number: r for r in self.session.table}, "session": self.session}

    def _refresh_all(self) -> None:
        s = self.session
        ctx = self._ctx()
        teams = list(s.teams.values())
        if self.view_var.get().startswith("Compare"):
            xk, xf = TEAM_VAR_BY_LABEL[self.ax_var.get()]
            order = [(xf(t, ctx), t) for t in teams]
            order = sorted([(v, t) for v, t in order if v is not None], key=lambda vt: vt[0])
            ticks = [(float(i + 1), str(t.number)) for i, (_v, t) in enumerate(order)]
            series = []
            chosen = [(lab, key) for lab, key in RATING_SERIES if self.cmp_vars[key].get()]
            getters = {key: TEAM_VAR_BY_LABEL[lab][1] for lab, key in RATING_SERIES}
            bars = []                              # (x, lowest rating, highest rating, team)
            for i, (_v, t) in enumerate(order):
                vals = [_opt(getters[key](t, ctx)) for _lab, key in chosen]
                vals = [v for v in vals if v is not None]
                if len(vals) >= 2:
                    bars.append((float(i + 1), min(vals), max(vals), t.number))
            if bars and self.spread_var.get():
                pts = []
                for x, lo, hi, _n in bars:
                    pts.append(ChartPoint(x, lo))
                    pts.append(ChartPoint(x, hi))
                series.append(ChartSeries("disagreement", pts, "#d5d9e2", kind="vbar"))
            for j, (lab, key) in enumerate(chosen):
                pts = []
                for i, (_v, t) in enumerate(order):
                    y = _opt(getters[key](t, ctx))
                    if y is None:
                        continue
                    pts.append(ChartPoint(float(i + 1), y, key=t.number,
                                          text=f"Team {t.number} {t.name[:24]}\n{lab}: {y:.1f}\nOPR {t.opr:.1f} · CAP {t.cap:.1f} · EPA {f1(t.epa)}"))
                series.append(ChartSeries(lab, pts, CHART_COLORS[j % len(CHART_COLORS)], kind="line"))
            dropped = len(teams) - len(order)
            shown = (f"All {len(order)} teams" if not dropped
                     else f"{len(order)} of {len(teams)} teams ({dropped} have no {self.ax_var.get()})")
            self.all_chart.set_data(f"{shown}, weakest → strongest by {self.ax_var.get()} - "
                                    f"one line per rating; where the lines meet, the ratings agree",
                                    f"Teams (ordered by {self.ax_var.get()})", "Points", series, x_ticks=ticks)
            spreads = sorted((hi - lo, n) for _x, lo, hi, n in bars)
            if len(chosen) < 2:
                self.all_status.set("Tick at least two ratings to see where they converge.")
            elif spreads:
                med = spreads[len(spreads) // 2][0]
                worst = ", ".join(f"{n} ({d:.0f} pts)" for d, n in spreads[-3:][::-1])
                self.all_status.set(f"{shown} shown. Median disagreement between the ratings "
                                    f"{med:.1f} pts; biggest: {worst}. Click a point to open the team.")
            else:
                self.all_status.set(f"{shown} shown, but none has values for two of the ticked ratings.")
            return
        xk, xf = TEAM_VAR_BY_LABEL[self.ax_var.get()]
        yk, yf = TEAM_VAR_BY_LABEL[self.ay_var.get()]
        zf = TEAM_VAR_BY_LABEL.get(self.az_var.get(), (None, None))[1]
        pts = []
        for t in teams:
            x, y = _opt(xf(t, ctx)), _opt(yf(t, ctx))
            if x is None or y is None:
                continue
            z = _opt(zf(t, ctx)) if zf else None
            color = UI_GOLD_DEEP if t.number == s.my_team and not zf else None
            pts.append(ChartPoint(x, y, label=str(t.number), z=z, key=t.number, color=color,
                                  text=f"Team {t.number} {t.name[:24]}\n{self.ax_var.get()}: {x:.1f}\n{self.ay_var.get()}: {y:.1f}"
                                       + (f"\n{self.az_var.get()}: {z:.1f}" if z is not None else "")))
        series = [ChartSeries("", pts, CHART_COLORS[0], labels=self.label_var.get())]
        if xk in ("opr", "cap", "epa", "new_avg", "proj_opr", "strength", "ftc_avg", "eopr") and \
                yk in ("opr", "cap", "epa", "new_avg", "proj_opr", "strength", "ftc_avg", "eopr") and pts:
            lo = min(min(p.x for p in pts), min(p.y for p in pts))
            hi = max(max(p.x for p in pts), max(p.y for p in pts))
            series.append(ChartSeries("x = y (the two agree)", [ChartPoint(lo, lo), ChartPoint(hi, hi)],
                                      "#b8bfcc", kind="line", dashed=True, markers=False))
        self.all_chart.set_data(f"{len(pts)} teams - {self.ay_var.get()} against {self.ax_var.get()}",
                                self.ax_var.get(), self.ay_var.get(), series,
                                z_label=self.az_var.get() if zf else "")
        self.all_status.set("Points near the dashed x = y line are teams the two statistics agree on; the further "
                            "from it, the more the two methods disagree. Click a point to open that team.")

    def _refresh_pairs(self) -> None:
        s = self.session
        unit = self.unit_var.get()
        vars_ = PRED_VARS if unit == PAIR_UNITS[0] else ALLIANCE_VARS
        by = _by_label(vars_)
        xf, yf = by.get(self.px_var.get()), by.get(self.py_var.get())
        zf = by.get(self.pz_var.get())
        if xf is None or yf is None:
            self.pair_chart.clear("Pick the axes.")
            return
        top = self.top_var.get()
        limit = None if top == "all" else as_int(top, 80)
        pts = []
        title = ""
        if unit == PAIR_UNITS[0]:
            for p in s.predictions:
                x, y = _opt(xf(p)), _opt(yf(p))
                if x is None or y is None:
                    continue
                z = _opt(zf(p)) if zf else None
                m = p.match
                color = None
                if not zf:
                    color = "#2e8b57" if p.correct else "#c0392b" if p.correct is False else None
                    if s.my_team and s.my_team in m.teams:
                        color = UI_GOLD_DEEP
                pts.append(ChartPoint(x, y, label=m.label, z=z, key=m.label, color=color,
                                      text=f"{m.label}: red {m.red} vs blue {m.blue}\npred {p.winner} {p.red_pts:.0f}-{p.blue_pts:.0f} "
                                           f"(Win chance {p.red_cow:.0%})" + (f"\nactual {m.winner} {m.red_score:.0f}-{m.blue_score:.0f}" if m.winner else "")))
            title = f"{len(pts)} matches - {self.py_var.get()} against {self.px_var.get()}"
            self.pair_status.set("Green = called correctly, red = called wrong, gold = my team's match, plain = not played yet "
                                 "(colours give way to the Z variable when one is chosen). Click a point to open the match.")
        else:
            rep = s.alliance_report()
            rows = rep.partners if unit == PAIR_UNITS[2] else rep.table
            if limit:
                rows = rows[:limit]
            for a in rows:
                x, y = _opt(xf(a, s.my_team)), _opt(yf(a, s.my_team))
                if x is None or y is None:
                    continue
                z = _opt(zf(a, s.my_team)) if zf else None
                color = None
                if not zf:
                    color = UI_GOLD_DEEP if a.unbeatable else ("#2f6fdb" if s.my_team in a.teams else None)
                label = str(a.partner_of(s.my_team)) if unit == PAIR_UNITS[2] else a.label
                pts.append(ChartPoint(x, y, label=label, z=z, key=a.teams, color=color,
                                      text=f"{a.label}  (rank {a.rank})\n{' / '.join(n for n in a.names if n)[:40]}\n"
                                           f"total {a.cap_sum:.1f} · beats {a.beats}/{a.legal} ({a.win_rate:.0%}) · "
                                           f"strength {a.alliance_strength:.0f}% · "
                                           f"margin {a.margin:+.1f}" + ("\nUNBEATABLE" if a.unbeatable else "")))
            if unit == PAIR_UNITS[2]:
                title = (f"Partners for team {s.my_team}: {self.py_var.get()} against {self.px_var.get()}" if s.my_team
                         else "Set your team number (Setup tab) to chart its partners")
                self.pair_status.set("Each point is one possible partner for your team. Gold = the pair would beat every alliance "
                                     "it could face. Click a point to open the partner on the Teams tab.")
            else:
                title = f"Top {len(pts)} of {len(rep.table)} possible alliances - {self.py_var.get()} against {self.px_var.get()}"
                self.pair_status.set("Each point is one two-team alliance. Gold = beats every alliance it could face; blue = includes "
                                     "my team. Click a point to open its first team.")
        series = [ChartSeries("", pts, CHART_COLORS[0], labels=self.plabel_var.get())]
        note = ""
        if unit != PAIR_UNITS[0]:
            keyof = _key_by_label(vars_)
            xk, yk = keyof.get(self.px_var.get()), keyof.get(self.py_var.get())
            fit = rep.strength_fit
            if fit and {xk, yk} in STRENGTH_CURVE_KEYS:
                caps = [a.cap_sum for a in rep.table]
                lo, hi = min(caps), max(caps)
                curve = []
                for i in range(61):
                    cap = lo + (hi - lo) * i / 60
                    wr = fit.predict(cap)
                    cx, cy = (cap, wr) if xk == "total" else (wr, cap)
                    curve.append(ChartPoint(cx, cy))
                series.append(ChartSeries(f"Fitted curve (R\u00b2 {fit.r2:.3f})", curve,
                                          "#c0392b", kind="line", markers=False))
                note = f"Fit (n={fit.n}): {fit.equation}   R\u00b2 = {fit.r2:.3f}"
        self.pair_chart.set_data(title, self.px_var.get(), self.py_var.get(), series,
                                 z_label=self.pz_var.get() if zf else "", note=note)


class AllianceWinRateTab(Tab):
    """Per-meet alliance rating percentile curve, fitted from every legal pairing."""

    title = "Win Rate Curve"

    def build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 6))
        self.heading = tk.StringVar(value="Alliance win rate vs total rating")
        ttk.Label(head, textvariable=self.heading, font=self.app.font_bold,
                  foreground=UI_NAVY).pack(anchor="w")
        self.summary = tk.StringVar(value="Load a meet on the Setup tab to calculate its curve.")
        ttk.Label(self, textvariable=self.summary, wraplength=1100, justify="left").pack(
            anchor="w", pady=(0, 6))
        self.chart = ChartCanvas(self, height=480, title_font=self.app.font_bold)
        self.chart.pack(fill="both", expand=True)
        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(6, 0))
        self.equation = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.equation, foreground=UI_NAVY,
                  wraplength=1000, justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(foot, text="Save image…", command=self._save).pack(side="right", padx=(8, 0))

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save chart", defaultextension=".ps",
                                            initialfile="alliance_win_rate.ps",
                                            filetypes=[("PostScript", "*.ps")])
        if path:
            try:
                self.chart.save_postscript(path)
            except tk.TclError as e:
                messagebox.showerror(APP_NAME, f"Could not save the chart.\n\n{e}", parent=self)

    def refresh(self) -> None:
        s = self.session
        if not s.teams:
            self.heading.set("Alliance win rate vs total rating")
            self.summary.set("Load a meet on the Setup tab to calculate its curve.")
            self.equation.set("")
            self.chart.clear("Load a meet on the Setup tab first.")
            return

        rating = s.settings.basis
        alliances = alliance_table(s.teams)
        fit = s.strength_fit
        self.heading.set(f"{s.event_name} - alliance win rate vs total {rating}")
        if fit is None or len(alliances) < 8:
            self.summary.set(
                f"Rating: {rating}. Not enough distinct legal alliance data to fit a curve "
                "(at least 8 usable pairings with a spread in rating are required).")
            self.equation.set("No fitted function available for this meet.")
            self.chart.clear("Insufficient data to fit the shifted-lognormal curve.")
            return

        usable = [a for a in alliances if a.legal > 0]
        xlo = min(a.cap_sum for a in usable)
        xhi = max(a.cap_sum for a in usable)
        scatter = [ChartPoint(a.cap_sum, 100.0 * a.win_rate, label="",
                              text=(f"Alliance {a.label}\nTeams: {a.teams[0]} + {a.teams[1]}"
                                    f"\nTotal {rating}: {a.cap_sum:.2f}"
                                    f"\nBeats: {a.beats}/{a.legal}"
                                    f"\nWin rate: {100.0*a.win_rate:.2f}%"))
                   for a in usable]
        curve = []
        for i in range(121):
            x = xlo + (xhi - xlo) * i / 120
            curve.append(ChartPoint(x, fit.predict(x)))
        series = [
            ChartSeries(f"All legal alliances (n={len(usable)})", scatter, "#2f6fdb"),
            ChartSeries(f"Shifted-lognormal fit (R²={fit.r2:.3f})", curve, "#c0392b",
                        kind="line", markers=False),
        ]
        self.chart.set_data(
            f"{s.event_name}: alliance win rate vs total {rating}",
            f"Total {rating}", "Win rate (%)", series,
            note="Win rate is the fraction of legal two-team alliances this pairing out-scores "
                 "(beats ÷ legal opponents); the curve is fitted from this meet's alliance pool.")
        self.summary.set(
            f"Rating: {rating} · {len(s.teams)} teams · {len(usable)} legal pairings · "
            f"shifted-lognormal fit R² = {fit.r2:.4f}. The selected rating comes from Settings; "
            "changing it recalculates this graph and its equation.")
        self.equation.set(f"Meet-specific function: {fit.equation}    ·    R² = {fit.r2:.4f}")


CHARTS_GUIDE = """HOW TO USE THE CHARTS TAB

The Charts tab turns the numbers on the other tabs into pictures. Every chart has
the same controls: pick what goes on each axis from the drop-downs, hover a point
to read its details, click a point to jump to that team (or match), and press
"Save image…" to write the chart to a PostScript file you can open or print.
Nothing here changes the predictions - except the "Edit this team's data inputs"
block on the One team page, which does, on purpose.

Load a meet on the Setup tab first (with "fetch history" ticked, so each team's
earlier events are available). The charts fill in automatically and repaint
whenever the data changes: new results, a Technical issues adjustment, a settings change.


PAGE 1 - ONE TEAM

What it is for: seeing how one robot has been trending, and whether OPR, CAP and
EPA tell the same story about it.

1. Type a team number in the "Team" box and press Show (or Return). The heading
   confirms the team and shows its current OPR, CAP and EPA.
2. "Chart" chooses the time axis:
     Events this season      one point per event the team has played, oldest first.
     Matches at this event   one point per match on this meet's schedule.
     All matches this season one point per match the team has played this season -
                              every earlier event's matches, then this one, strung
                              together in the order they were played. (Needs the
                              meet loaded "with history"; otherwise it's the same
                              as "Matches at this event".)
3. "X axis":
     Event (in order played) / Event date   for the season view
     Match (in order) / Match number       for the match view
     Match (in order played)               for the all-matches view
4. "Y axis" is the main line; "Y2 (optional)" and "Y3 (optional)" add up to two
   more lines on the same chart (choose "- none -" to leave one out). Season-view
   choices include:
     OPR (non-penalty)             the team's OPR at that event
     CAP (as of that event)        what CAP would have said with only the events
                                   played up to then - so you can see whether CAP
                                   would have called the team right at the time
     EPA (after that event)        the EPA rating once that event's matches were
                                   learned
     Auto / Teleop / Endgame       the OPR breakdown by phase
     Projected OPR (as of that event), TBP 1 / TBP 2, Rank, RP, Wins, Losses,
     Qual matches played
   Match-view and all-matches-view choices (same list for both):
     EPA before the match          the rating the team carried into that match
     Predicted points, my alliance / opponents, Predicted margin
     Chance of winning (%)
     Actual points, my alliance / opponents (np), Actual margin
     Prediction error              actual minus predicted for the team's alliance
5. Two preset buttons set the axes in one click:
     "OPR vs CAP vs EPA"           the three ratings across the season
     "Auto / Teleop / Endgame"     where the points come from
6. Reading it: lines that sit on top of each other mean the ratings agree; a line
   that pulls away shows a method reacting to something the others miss. In the
   match view, the gap between "Predicted points" and "Actual points" is the
   error for each match - a steady gap means the team is consistently under- or
   over-rated, a one-off spike is usually one bad match.

Edit this team's data inputs (blank = value from FTCScout)
   Type a value in any box to replace what FTCScout reported: OPR, Auto, Teleop,
   Endgame, Proj OPR (projected OPR) or EPA. The number in brackets beside each
   box is the value currently in use. Blank means "use FTCScout's value".
   Press Apply (or Return). Everything downstream updates: CAP, every match
   prediction, chance of winning, standings, alliances, the Analysis tab and the
   charts (in the season view, the point for this meet; earlier events keep the
   values they were recorded with). Edited teams are marked in the Teams table
   and their heading says "edited". "Reset to FTCScout" clears every override for
   that team.
   Overrides are saved in the session file, so they survive closing the program.
   Use this when you know something FTCScout does not - a team that rebuilt its
   robot, a broken mechanism you saw in the pits, or a rookie with no data yet.

The same panel opens as a side menu on the Teams tab: select a row there and
press "Show charts ▸".


PAGE 2 - ALL TEAMS

What it is for: comparing every team at the meet at once, and finding the teams
where OPR, CAP and EPA disagree - those are the calls to double-check by eye.

"View" has two modes.

Scatter
   Every team is one dot. Choose the X axis and Y axis from any per-team number:
   OPR, CAP, EPA, the prediction rating in use, Auto, Teleop, Endgame, AVG, NEW
   AVG, FTC AVG, Projected OPR, EOPR, the differences CAP − OPR, EPA − OPR and
   EPA − CAP, the rating spread, projected rank and RP, wins, events played,
   matches EPA has learned from, rank at the source event, or the Technical issues adjustment.
   "Z (size + colour)" adds a third number: bigger, more golden dots have a
   higher value of it; small navy dots a lower one. "- none -" turns it off.
   "Label points" writes the team number beside each dot.
   When both axes are ratings (for example CAP against OPR) a dashed "x = y" line
   is drawn: dots on the line are teams the two statistics agree on; the further
   a dot sits from the line, the more the two methods disagree about that team.
   Your own team is drawn in gold when no Z variable is set.
   Suggested views:
     X = OPR, Y = CAP, Z = EPA        the default: do the three ratings converge?
     X = OPR, Y = EPA − OPR           who does EPA rate much higher or lower than OPR?
     X = Projected rank, Y = CAP      is the standings projection driven by rating
                                      or by schedule?
     X = Auto, Y = Teleop, Z = Endgame   what kind of robot each team is

Compare ratings (where they converge)
   Teams are lined up left to right, ordered by the statistic in "Order teams by"
   (OPR by default, weakest to strongest); a team with no value for that statistic
   (for example no Projected OPR yet) is left out, and the title says so. Each
   ticked rating is a line: OPR, CAP, EPA, NEW AVG, Projected OPR. Where the lines run together the methods agree
   and the prediction is on firm ground; where they split, at least one method is
   wrong about that team. The grey bars behind each team show the disagreement
   (highest minus lowest of the ticked ratings) - "show disagreement bars" turns
   them off. The status line under the chart gives the median and widest
   disagreement in points.

Clicking a dot (or a point on a line) opens that team on the Teams tab with the
side menu already showing it.


PAGE 3 - MATCHES & ALLIANCES

What it is for: seeing which matches are close and which are lopsided, checking
how the predictions did, and finding the strongest two-team pairings for alliance
selection.

"Chart" chooses what each point is:

Matches on the schedule
   One point per match. Axis choices: match number, predicted red / blue points,
   predicted margin (signed or absolute), predicted total, red / blue chance of
   winning, closeness (how near 50/50 the call is), actual red / blue points,
   actual margin, the margin error (actual minus predicted, signed or absolute),
   and the strongest / weakest single rating in the match.
   Colours: green = called correctly, red = called wrong, gold = a match with your
   team, plain = not played yet. Choosing a Z variable replaces the colours with
   the size-and-colour scale.
   Actual points are non-penalty scores ("np"), as on the One team page.
   Suggested views:
     X = Predicted red points, Y = Predicted blue points,
     Z = Predicted margin (absolute)
         the default - dots near the diagonal are toss-ups, big gold dots are
         blow-outs
     X = Match number, Y = Closeness (50 − |Win chance − 50|)
         when in the day the tight matches fall
     X = Predicted margin (red − blue), Y = Actual margin (red − blue, np)
         how good the predictions were: a straight diagonal is perfect, dots in
         the wrong quadrant are the wrong calls
     X = Match number, Y = Margin error (absolute)
         whether the predictions got better or worse as the meet went on
   Clicking a point opens that match on the Matches tab.

All possible alliances
   One point per two-team pairing of teams at the meet (the "Show top" box, shown
   for this view and the partners view, limits it to the strongest N by total
   rating; "all" shows every pairing). Axis
   choices: alliance rank, total rating of the pair, the stronger and the weaker
   member's rating, the balance between them, how many other alliances it beats,
   its win rate against every alliance it could legally face, its margin over the
   closest of those, and its chance of winning against that closest opponent.
   Gold = the pairing beats every alliance it could face; blue = includes your
   team. The default view (stronger member against weaker member, sized by win
   rate) shows at a glance whether a pairing's strength comes from one carry or
   from two solid robots. Clicking a point opens the first team of the pair.

Partners for my team
   Only the pairings that include your team (set it on the Setup tab). Default:
   each partner's own rating against the pair's win rate, sized by margin over
   the closest opponent - the top-right dots are the partners to talk to first.
   Clicking a point opens that partner on the Teams tab.


TIPS

  * The rating the predictions use (CAP, OPR or EPA - Settings tab) is what
    "Prediction rating (current basis)", the alliance totals and the match points
    are built from.
    Switch it on the Settings tab and every chart follows.
  * EPA needs match history: load the meet with "fetch history" ticked, and let
    it keep learning during the meet (Settings tab) so the match-view EPA line
    moves.
  * Sheet-faithful mode (Settings) changes CAP and the projected OPR, so the
    charts change with it - a quick way to see what the old spreadsheets would
    have said.
  * Charts repaint on every "Refresh results", so during a meet the Matches page
    shows the day's accuracy building up.
  * "Save image…" writes PostScript (.ps). macOS Preview opens it directly; on
    Windows and Linux use any PDF/PostScript viewer, or paste the numbers from the
    Teams tab into a spreadsheet if you need a different format.
"""


class AnalysisTab(Tab):
    """AutoScout.Analysis + Sheet18 - the post-mortem.

    Running accuracy, the ±margin variant of the call, a per-wrong-match
    "lower blue by ≈ N points" suggestion, and the per-team verdict of
    "CAP inaccurate" versus "likely penalty points". Export lives here too.
    """

    title = "Analysis"

    def build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Export CSV…", command=self.app.do_export_csv).pack(side="left")
        ttk.Button(bar, text="Export printable HTML…", command=self.app.do_export_html).pack(side="left", padx=6)
        ttk.Button(bar, text="Copy printable text", command=self.app.do_copy_text).pack(side="left")
        ttk.Button(bar, text="Re-analyse", command=self.refresh).pack(side="right")

        box = ttk.LabelFrame(self, text="Prediction report", padding=(8, 4))
        box.pack(fill="x", pady=(8, 0))
        # Explicit colours: the default tk.Text follows the desktop theme, which on a
        # dark-themed Linux desktop drew white text on a white box (an empty-looking block).
        self.summary = tk.Text(box, height=6, wrap="word", state="disabled",
                               background=UI_ROW_ALT_BG, foreground=UI_TEXT,
                               insertbackground=UI_TEXT, relief="flat", borderwidth=0,
                               highlightthickness=0, padx=8, pady=6,
                               font=(self.app.body_family, 10))
        style_text_selection(self.summary)
        ssb = ttk.Scrollbar(box, orient="vertical", command=self.summary.yview)
        self.summary.configure(yscrollcommand=ssb.set)
        self.summary.pack(side="left", fill="both", expand=True)
        ssb.pack(side="left", fill="y")

        ttk.Label(self, text="Wrong predictions", font=self.app.font_bold).pack(anchor="w", pady=(10, 2))
        self.wrong_tree = SortableTree(self, WRONG_COLUMNS, height=9)
        self.wrong_tree.pack(fill="both", expand=True)

        ttk.Label(self, text="Team flags", font=self.app.font_bold).pack(anchor="w", pady=(10, 2))
        self.flag_tree = SortableTree(self, FLAG_COLUMNS, height=8)
        self.flag_tree.pack(fill="both", expand=True)

    def _set_summary(self, lines) -> None:
        lines = list(lines)
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        # size the block to its content (up to 14 lines) so there is no dead space above the tables
        self.summary.configure(height=max(3, min(14, len(lines))), state="disabled")

    def refresh(self) -> None:
        s = self.session
        if analyze is None:
            c, d = s.accuracy()
            self._set_summary([
                "Analysis module not available.",
                "",
                "autoscout/analysis.py is missing, so only the running accuracy is shown here.",
                f"Accuracy: {c}/{d}" + (f" = {c / d:.0%}" if d else ""),
            ])
            self.wrong_tree.set_rows([])
            self.flag_tree.set_rows([])
            return
        try:
            report = analyze(s)
        except Exception as e:                        # a half-finished sibling module
            self._set_summary([f"Analysis failed: {e}"])
            self.wrong_tree.set_rows([])
            self.flag_tree.set_rows([])
            return

        lines = list(getattr(report, "summary_lines", []) or [])
        if not lines:
            c, d = getattr(report, "accuracy", (0, 0))
            mc, md = getattr(report, "margin_accuracy", (0, 0))
            lines = [f"Accuracy {c}/{d}" + (f" = {c / d:.0%}" if d else ""),
                     f"With the ±{s.settings.margin_points:.0f} point margin rule: {mc}/{md}"
                     + (f" = {mc / md:.0%}" if md else "")]
        self._set_summary(lines)

        rows = []
        for i, w in enumerate(getattr(report, "wrong", []) or []):
            p = getattr(w, "prediction", None)
            m = getattr(p, "match", None)
            label = getattr(m, "label", f"#{i + 1}")
            score = (f"{m.red_score:.0f} – {m.blue_score:.0f}"
                     if m is not None and m.red_score is not None and m.blue_score is not None else "")
            rows.append((label, (
                label, getattr(p, "winner", ""), getattr(m, "winner", "") or "",
                f"{f1(getattr(p, 'red_pts', None))} – {f1(getattr(p, 'blue_pts', None))}",
                score, getattr(w, "suggestion", ""), getattr(w, "classification", "")), ("bad",)))
        self.wrong_tree.set_rows(rows)

        flags = getattr(report, "team_flags", {}) or {}
        frows = []
        for number, flag in sorted(flags.items(), key=lambda kv: -getattr(kv[1], "wrong_appearances", 0)):
            tags = ("mine",) if number == s.my_team else ()
            frows.append((number, (
                getattr(flag, "team", number), getattr(flag, "wrong_appearances", 0),
                getattr(flag, "classification", ""), getattr(flag, "predicted_wins", ""),
                getattr(flag, "actual_wins", "")), tags))
        self.flag_tree.set_rows(frows)


class LiveScoutingTab(Tab):
    """Live meet video launcher plus durable, match-by-match human scouting notes.

    Tk's standard library has no embedded Chromium/video engine. This module therefore
    keeps AutoScout's scouting workspace native and opens YouTube in the user's browser
    (new tab/window); it never introduces a fragile mandatory browser dependency.
    """
    title = "Live Scouting"

    def build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="LIVE MEET SCOUTING", style="Card.TLabel").pack(anchor="w")
        ttk.Label(top, text=("Find a meet stream, keep it beside AutoScout, and record observations "
                             "against the exact match. Notes are saved in the session JSON."),
                  foreground=UI_MUTED, wraplength=1050).pack(anchor="w", pady=(2, 0))

        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True)
        video = ttk.Frame(split, padding=8)
        notes = ttk.Frame(split, padding=8)
        split.add(video, weight=1)
        split.add(notes, weight=2)

        vf = ttk.LabelFrame(video, text="Meet video", padding=10)
        vf.pack(fill="x", pady=(0, 8))
        self.stream_info = tk.StringVar(value="No stream selected")
        ttk.Label(vf, textvariable=self.stream_info, wraplength=360, foreground=UI_MUTED).pack(anchor="w", pady=(0, 8))
        ttk.Label(vf, text="Search YouTube for this meet").pack(anchor="w")
        self.query_var = tk.StringVar()
        ttk.Entry(vf, textvariable=self.query_var).pack(fill="x", pady=4)
        actions = ttk.Frame(vf); actions.pack(fill="x", pady=(2, 8))
        ttk.Button(actions, text="Search live streams", command=self.search_live).pack(side="left")
        ttk.Button(actions, text="Search all videos", command=self.search_all).pack(side="left", padx=5)
        ttk.Label(vf, text="Paste a YouTube stream or video URL").pack(anchor="w")
        self.url_var = tk.StringVar()
        ttk.Entry(vf, textvariable=self.url_var).pack(fill="x", pady=4)
        url_actions = ttk.Frame(vf); url_actions.pack(fill="x")
        ttk.Button(url_actions, text="Use this stream", command=self.use_stream).pack(side="left")
        ttk.Button(url_actions, text="Open video", command=self.open_stream).pack(side="left", padx=5)
        ttk.Button(url_actions, text="Detach / video window", command=self.video_window).pack(side="left")

        how = ttk.LabelFrame(video, text="Watch alongside AutoScout", padding=10)
        how.pack(fill="both", expand=True)
        ttk.Label(how, text=("AutoScout's desktop UI uses Tkinter, which does not include a web/video "
                             "engine. For reliable YouTube LIVE playback, the video opens in your "
                             "default browser. Keep the browser and AutoScout side by side, or use "
                             "your browser's picture-in-picture mode.\n\n"
                             "The scouting pane remains fully usable while the stream plays. "
                             "No browser add-on or API key is required."),
                  justify="left", wraplength=360).pack(anchor="w")

        mf = ttk.LabelFrame(notes, text="Match notes", padding=10)
        mf.pack(fill="both", expand=True)
        row = ttk.Frame(mf); row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Match").pack(side="left")
        self.match_var = tk.StringVar()
        self.match_box = ttk.Combobox(row, textvariable=self.match_var, state="readonly", width=18)
        self.match_box.pack(side="left", padx=(5, 14))
        self.match_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_notes())
        ttk.Label(row, text="Team").pack(side="left")
        self.team_var = tk.StringVar(value="- alliance / general -")
        self.team_box = ttk.Combobox(row, textvariable=self.team_var, state="readonly", width=22)
        self.team_box.pack(side="left", padx=5)
        ttk.Label(row, text="Phase").pack(side="left", padx=(8, 0))
        self.phase_var = tk.StringVar(value="Overall")
        ttk.Combobox(row, textvariable=self.phase_var, state="readonly", width=12,
                     values=("Overall", "Autonomous", "TeleOp", "Endgame", "Between matches")).pack(side="left", padx=5)

        ttk.Label(mf, text="Observation (what the robot/team did, outcome, reliability, issue, etc.)").pack(anchor="w")
        self.note_text = tk.Text(mf, height=4, wrap="word", relief="solid", borderwidth=1)
        self.note_text.pack(fill="x", pady=4)
        btns = ttk.Frame(mf); btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Add note", command=self.add_note).pack(side="left")
        ttk.Button(btns, text="Delete selected", command=self.delete_note).pack(side="left", padx=5)
        ttk.Button(btns, text="Save session", command=self.app.do_save).pack(side="right")

        self.notes_tree = ttk.Treeview(mf, columns=("time", "team", "phase", "note"), show="headings", height=14)
        for col, label, width in (("time", "Time", 135), ("team", "Team", 155), ("phase", "Phase", 100), ("note", "Observation", 430)):
            self.notes_tree.heading(col, text=label)
            self.notes_tree.column(col, width=width, anchor="w")
        ysb = ttk.Scrollbar(mf, orient="vertical", command=self.notes_tree.yview)
        self.notes_tree.configure(yscrollcommand=ysb.set)
        self.notes_tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.refresh()

    def _query(self) -> str:
        s = self.session
        typed = self.query_var.get().strip()
        if typed:
            return typed
        bits = [str(s.season), s.event_name, s.event_code]
        if s.my_team:
            bits.append(str(s.my_team))
        return " ".join(x for x in bits if x).strip() + " FTC robotics livestream"

    def search_live(self) -> None:
        import urllib.parse, webbrowser
        q = self._query() + " FTC robotics live"
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q) + "&sp=EgJAAQ%253D%253D"
        webbrowser.open_new_tab(url)
        self.stream_info.set("YouTube LIVE search opened in your browser. Choose the event stream, then paste its URL here.")

    def search_all(self) -> None:
        import urllib.parse, webbrowser
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(self._query())
        webbrowser.open_new_tab(url)

    def use_stream(self) -> None:
        url = self.url_var.get().strip()
        if not (url.startswith("https://www.youtube.com/") or url.startswith("https://youtube.com/") or
                url.startswith("https://youtu.be/") or url.startswith("https://www.youtube.com/live/")):
            messagebox.showwarning(APP_NAME, "Enter a valid YouTube video, live, or youtu.be URL.", parent=self)
            return
        self.session.live_stream_url = url
        self.stream_info.set("Selected stream: " + url)
        self.app.status.set("Live stream saved to this session.")

    def open_stream(self) -> None:
        import webbrowser
        url = self.url_var.get().strip() or self.session.live_stream_url
        if not url:
            messagebox.showinfo(APP_NAME, "Search for a live meet or paste a YouTube URL first.", parent=self)
            return
        self.url_var.set(url)
        self.use_stream()
        if self.session.live_stream_url:
            webbrowser.open_new_tab(self.session.live_stream_url)

    def video_window(self) -> None:
        import webbrowser
        url = self.url_var.get().strip() or self.session.live_stream_url
        if not url:
            self.search_live()
            return
        self.url_var.set(url)
        self.use_stream()
        win = tk.Toplevel(self)
        win.title("AutoScout - Live video")
        win.geometry("520x210")
        ttk.Label(win, text="YouTube live video", style="Card.TLabel").pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(win, text="Playback runs in your browser; this detachable controller keeps the stream link handy.",
                  wraplength=480).pack(anchor="w", padx=14, pady=4)
        ttk.Label(win, text=url, wraplength=480, foreground=UI_MUTED).pack(anchor="w", padx=14, pady=4)
        ttk.Button(win, text="Open / focus stream in browser", command=lambda: webbrowser.open_new_tab(url)).pack(anchor="w", padx=14, pady=8)

    def refresh(self) -> None:
        s = self.session
        if hasattr(self, "query_var"):
            if not self.query_var.get().strip():
                self.query_var.set(" ".join(x for x in (str(s.season), s.event_name, s.event_code, "FTC robotics") if x))
            if s.live_stream_url:
                self.url_var.set(s.live_stream_url)
                self.stream_info.set("Selected stream: " + s.live_stream_url)
            matches = [m.label for m in s.matches]
            current = self.match_var.get()
            self.match_box["values"] = matches
            if matches and current not in matches:
                self.match_var.set(matches[0])
            elif not matches:
                self.match_var.set("")
            teams = ["- alliance / general -"] + [str(n) for n in sorted(s.teams)]
            self.team_box["values"] = teams
            if self.team_var.get() not in teams:
                self.team_var.set(teams[0])
            self.refresh_notes()

    def refresh_notes(self) -> None:
        if not hasattr(self, "notes_tree"):
            return
        self.notes_tree.delete(*self.notes_tree.get_children())
        label = self.match_var.get()
        for i, n in enumerate(self.session.live_notes.get(label, [])):
            self.notes_tree.insert("", "end", iid=str(i), values=(n.get("time", ""), n.get("team", "General"),
                                  n.get("phase", "Overall"), n.get("text", "")))

    def add_note(self) -> None:
        label = self.match_var.get()
        content = self.note_text.get("1.0", "end-1c").strip()
        if not label:
            messagebox.showinfo(APP_NAME, "Load or paste a match schedule before adding match-specific notes.", parent=self)
            return
        if not content:
            messagebox.showinfo(APP_NAME, "Enter an observation first.", parent=self)
            return
        import datetime
        team = self.team_var.get()
        if team == "- alliance / general -":
            team = "General"
        note = {"time": datetime.datetime.now().strftime("%H:%M:%S"), "team": team,
                "phase": self.phase_var.get(), "text": content}
        self.session.live_notes.setdefault(label, []).append(note)
        if self.session.path:
            try:
                self.session.save(self.session.path)
            except OSError:
                pass
        self.note_text.delete("1.0", "end")
        self.refresh_notes()
        self.app.status.set(f"Added scouting note for {label}. Save the session to keep it permanently.")

    def delete_note(self) -> None:
        selected = self.notes_tree.selection()
        label = self.match_var.get()
        if not selected:
            return
        idx = int(selected[0])
        notes = self.session.live_notes.get(label, [])
        if 0 <= idx < len(notes):
            notes.pop(idx)
            if self.session.path:
                try:
                    self.session.save(self.session.path)
                except OSError:
                    pass
            self.refresh_notes()


class TeamResearchTab(Tab):
    """Public team intelligence for any FTC team, independent of the loaded event."""
    title = "Team Research"

    def build(self) -> None:
        self.result = None
        self._request_id = 0
        self._team_results = []
        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="TEAM INTELLIGENCE", style="Card.TLabel").pack(anchor="w")
        ttk.Label(head, text="Research any FTC team using FTCScout's public statistics. This workspace is separate from your loaded scouting session.",
                  foreground=UI_MUTED, wraplength=1100).pack(anchor="w", pady=(2, 0))

        search = ttk.LabelFrame(self, text="Find a team", padding=10)
        search.pack(fill="x", pady=(0, 8))
        self.query_var = tk.StringVar()
        self.season_var = tk.StringVar(value=str(max(2022, datetime.datetime.now().year)))
        ttk.Label(search, text="Team number or name").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(search, textvariable=self.query_var, width=28)
        entry.grid(row=1, column=0, sticky="we", padx=(0, 8), pady=(3, 0))
        entry.bind("<Return>", lambda _e: self.search())
        ttk.Label(search, text="Season (start year)").grid(row=0, column=1, sticky="w")
        ttk.Entry(search, textvariable=self.season_var, width=10).grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(3, 0))
        ttk.Button(search, text="Search teams", command=self.search).grid(row=1, column=2, padx=(0, 6), pady=(3, 0))
        ttk.Button(search, text="Load team", command=self.load_selected).grid(row=1, column=3, pady=(3, 0))
        search.columnconfigure(0, weight=1)
        self.search_status = tk.StringVar(value="Enter a team number (fastest) or part of a team name.")
        ttk.Label(search, textvariable=self.search_status, foreground=UI_MUTED).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=(0, 0, 6, 0))
        right = ttk.Frame(body, padding=(6, 0, 0, 0))
        body.add(left, weight=1); body.add(right, weight=3)
        listbox_frame = ttk.LabelFrame(left, text="Matching teams", padding=6)
        listbox_frame.pack(fill="both", expand=True)
        self.team_list = tk.Listbox(listbox_frame, height=12, activestyle="dotbox", exportselection=False)
        sb = ttk.Scrollbar(listbox_frame, orient="vertical", command=self.team_list.yview)
        self.team_list.configure(yscrollcommand=sb.set)
        self.team_list.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.team_list.bind("<Double-Button-1>", lambda _e: self.load_selected())
        ttk.Label(left, text="Tip: select a result and press Enter to open its profile.",
                  foreground=UI_MUTED, wraplength=240).pack(anchor="w", pady=(6, 0))
        self.team_list.bind("<Return>", lambda _e: self.load_selected())

        self.profile_title = tk.StringVar(value="No team loaded")
        ttk.Label(right, textvariable=self.profile_title, font=self.app.font_title).pack(anchor="w")
        self.profile_subtitle = tk.StringVar(value="Team profile, season performance, event history and awards")
        ttk.Label(right, textvariable=self.profile_subtitle, foreground=UI_MUTED).pack(anchor="w", pady=(1, 7))
        metrics = ttk.Frame(right)
        metrics.pack(fill="x", pady=(0, 8))
        self.metric_vars = [tk.StringVar(value="-") for _ in range(5)]
        self.metric_labels = ["Events", "Matches", "Win rate", "Avg OPR", "Awards"]
        self.metric_cards = []
        for i, (label, var) in enumerate(zip(self.metric_labels, self.metric_vars)):
            card = ttk.LabelFrame(metrics, text=label, padding=(8, 5))
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 4, 0))
            ttk.Label(card, textvariable=var, font=self.app.font_big).pack(anchor="w")
            metrics.columnconfigure(i, weight=1)
        self.chart = ChartCanvas(right, height=285, title_font=self.app.font_bold)
        self.chart.pack(fill="both", expand=True, pady=(0, 8))
        lower = ttk.Panedwindow(right, orient="horizontal")
        lower.pack(fill="both", expand=True)
        evf = ttk.LabelFrame(lower, text="Season event history", padding=6)
        details = ttk.Notebook(lower)
        lower.add(evf, weight=3); lower.add(details, weight=2)
        awf = ttk.Frame(details, padding=5)
        mf = ttk.Frame(details, padding=5)
        details.add(awf, text="Awards")
        details.add(mf, text="Match log")
        self.events_tree = ttk.Treeview(evf, columns=("event", "date", "record", "opr", "auto", "tele", "end"), show="headings", height=8)
        for col, label, width in (("event","Event",190),("date","Date",82),("record","W-L-T",65),("opr","OPR",62),("auto","Auto",58),("tele","TeleOp",62),("end","Endgame",68)):
            self.events_tree.heading(col, text=label); self.events_tree.column(col, width=width, anchor="w")
        self.events_tree.pack(fill="both", expand=True)
        self.awards_tree = ttk.Treeview(awf, columns=("award","event","season"), show="headings", height=8)
        for col, label, width in (("award","Award",170),("event","Event",140),("season","Season",55)):
            self.awards_tree.heading(col, text=label); self.awards_tree.column(col, width=width, anchor="w")
        self.awards_tree.pack(fill="both", expand=True)
        self.matches_tree = ttk.Treeview(mf, columns=("event","match","alliance","score","result"), show="headings", height=8)
        for col, label, width in (("event","Event",105),("match","Match",90),("alliance","Alliance",75),("score","Score",65),("result","Result",75)):
            self.matches_tree.heading(col, text=label); self.matches_tree.column(col, width=width, anchor="w")
        self.matches_tree.pack(fill="both", expand=True)
        self.profile_footer = tk.StringVar(value="Data provided by FTCScout. Public data may be incomplete or delayed.")
        ttk.Label(right, textvariable=self.profile_footer, foreground=UI_MUTED, wraplength=1000).pack(anchor="w", pady=(6, 0))

    def _client(self) -> FTCScoutClient:
        return FTCScoutClient(offline=bool(config.get("offline", False)), max_age=1800)

    def _season(self) -> int:
        try:
            y = int(self.season_var.get().strip())
            if y < 2000 or y > 2100: raise ValueError
            return y
        except ValueError:
            raise ApiError("Enter a valid season start year, such as 2026 for the 2026–27 season.")

    def search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            self.search_status.set("Enter a team number or name first."); return
        self.search_status.set("Searching public team records…")
        self.team_list.delete(0, "end")
        if query.isdigit():
            self._team_results = [{"teamNumber": int(query), "name": "Team " + query}]
            self.team_list.insert("end", "Team " + query)
            self.load_team(int(query)); return
        self.app.run_async(lambda: self._client().search_teams(query), self._search_done, self._failed)

    def _search_done(self, rows) -> None:
        self._team_results = rows if isinstance(rows, list) else []
        self.team_list.delete(0, "end")
        for team in self._team_results:
            number = team.get("teamNumber", team.get("number", "?"))
            name = team.get("name", "Unnamed team")
            location = ", ".join(x for x in (team.get("city"), team.get("state")) if x)
            self.team_list.insert("end", f"{number}  ·  {name}" + (f"  -  {location}" if location else ""))
        self.search_status.set(f"Found {len(self._team_results)} matching team(s). Select one and choose Load team.")
        if len(self._team_results) == 1:
            self.team_list.selection_set(0)

    def load_selected(self) -> None:
        selection = self.team_list.curselection()
        if not selection:
            self.search_status.set("Select a team from the results first."); return
        team = self._team_results[selection[0]]
        try: number = int(team.get("teamNumber", team.get("number")))
        except (TypeError, ValueError):
            self.search_status.set("That result did not include a valid team number."); return
        self.load_team(number)

    def load_team(self, number: int) -> None:
        try: season = self._season()
        except ApiError as e: self._failed(e); return
        self._request_id += 1
        req = self._request_id
        self.search_status.set(f"Loading team {number} · {season} season…")
        self.profile_title.set(f"Loading team {number}…")
        self.chart.clear("Fetching team history and performance data…")
        def work():
            c = self._client()
            team = c.team(number)
            events = c.team_events(number, season)
            if isinstance(events, list):
                for event_row in events:
                    if isinstance(event_row, dict):
                        code = event_row.get("eventCode", "")
                        if code:
                            try: event_row["_eventInfo"] = c.event(season, code)
                            except ApiError: event_row["_eventInfo"] = {}
            try: quick = c.quick_stats(number, season)
            except ApiError: quick = {}
            try: awards = c.team_awards(number, season)
            except ApiError: awards = []
            try: matches = c.team_matches(number, season)
            except ApiError: matches = []
            return {"number":number,"season":season,"team":team,"events":events,"quick":quick,"awards":awards,"matches":matches}
        self.app.run_async(work, lambda data: self._loaded(data, req), self._failed)

    def _failed(self, err) -> None:
        self.search_status.set(err.user_message if isinstance(err, ApiError) else str(err))
        self.profile_footer.set("Could not retrieve this profile. Check the team number, season, connection, or FTCScout availability.")

    @staticmethod
    def _val(obj, *keys, default=None):
        if not isinstance(obj, dict): return default
        for key in keys:
            if key in obj and obj[key] is not None: return obj[key]
        return default

    def _loaded(self, data, req) -> None:
        if req != self._request_id: return
        self.result = data
        team, events, awards = data["team"], data["events"], data["awards"]
        name = team.get("name") or f"Team {data['number']}"
        self.profile_title.set(f"{data['number']}  ·  {name}")
        location = ", ".join(x for x in (team.get("city"), team.get("state"), team.get("country")) if x)
        rookie = team.get("rookieYear") or team.get("rookieyear")
        self.profile_subtitle.set("  ·  ".join(x for x in (location, f"Rookie year {rookie}" if rookie else "", team.get("website") or "") if x) or f"FTC team profile · {data['season']} season")
        matches = data["matches"] if isinstance(data["matches"], list) else []
        wins = sum(int((e.get("stats") or {}).get("wins",0) or 0) for e in events if isinstance(e,dict))
        losses = sum(int((e.get("stats") or {}).get("losses",0) or 0) for e in events if isinstance(e,dict))
        ties = sum(int((e.get("stats") or {}).get("ties",0) or 0) for e in events if isinstance(e,dict))
        played = wins + losses + ties
        oprs = [float(((e.get("stats") or {}).get("opr") or {}).get("totalPointsNp",0) or 0) for e in events if isinstance(e,dict) and (e.get("stats") or {}).get("opr")]
        avg_opr = sum(oprs)/len(oprs) if oprs else None
        vals = [str(len(events)), str(len(matches) or played), f"{wins/played:.0%}" if played else "-", f"{avg_opr:.1f}" if avg_opr is not None else "-", str(len(awards))]
        for var, value in zip(self.metric_vars, vals): var.set(value)
        for tree in (self.events_tree, self.awards_tree, self.matches_tree):
            for item in tree.get_children(): tree.delete(item)
        parsed = []
        for e in events:
            if not isinstance(e,dict): continue
            stats = e.get("stats") or {}; opr = stats.get("opr") or {}
            ec = e.get("eventCode", e.get("event_code", ""))
            info = e.get("_eventInfo") or {}
            evname = e.get("eventName") or e.get("name") or info.get("name") or ec or "Event"
            date = (e.get("startDate") or e.get("start") or info.get("start") or e.get("date") or "")[:10]
            w,l,t = (int(stats.get(k,0) or 0) for k in ("wins","losses","ties"))
            total = float(opr.get("totalPointsNp",0) or 0)
            auto = float(opr.get("autoPoints",0) or 0)
            tele = float(opr.get("dcPoints",0) or 0)
            end = endgame_points(opr, data["season"])
            self.events_tree.insert("", "end", values=(evname,date,f"{w}-{l}-{t}",f"{total:.1f}",f"{auto:.1f}",f"{tele:.1f}",f"{end:.1f}"))
            parsed.append((date or ec or str(len(parsed)+1), total, auto, tele, end, evname))
        for m in matches:
            if not isinstance(m, dict): continue
            match_obj = m.get("match") if isinstance(m.get("match"), dict) else m
            event_code = m.get("eventCode") or match_obj.get("eventCode") or "-"
            match_label = match_obj.get("name") or match_obj.get("matchName") or match_obj.get("matchNumber") or match_obj.get("id") or "-"
            alliance = m.get("alliance") or m.get("color") or match_obj.get("alliance") or "-"
            score = m.get("score", match_obj.get("score", "-"))
            result = m.get("result") or ("Win" if m.get("isWinner") is True else "Loss" if m.get("isWinner") is False else "-")
            self.matches_tree.insert("", "end", values=(event_code, match_label, alliance, score, result))
        for a in awards:
            if not isinstance(a,dict): continue
            award = a.get("name") or a.get("awardName") or a.get("award") or "Award"
            event = a.get("eventName") or a.get("eventCode") or a.get("event") or "-"
            season = a.get("season", data["season"])
            self.awards_tree.insert("", "end", values=(award,event,season))
        parsed.sort(key=lambda x: x[0])
        if parsed:
            series = []
            for label, idx, color in (("OPR (non-penalty)",1,CHART_COLORS[0]),("Auto OPR",2,CHART_COLORS[1]),("TeleOp OPR",3,CHART_COLORS[2]),("Endgame OPR",4,CHART_COLORS[3])):
                pts = [ChartPoint(i+1,row[idx],label=row[0],text=f"{row[5]}\n{label}: {row[idx]:.1f}") for i,row in enumerate(parsed) if row[idx] is not None]
                series.append(ChartSeries(label,pts,color=color,kind="line"))
            ticks = [(i+1, (row[0] or row[5])[:10]) for i,row in enumerate(parsed)]
            self.chart.set_data(f"Team {data['number']} · {data['season']} season performance", "Event (chronological)", "OPR contribution", series, x_ticks=ticks,
                                note="Event OPR values are estimates from FTCScout, not your team's direct scouting observations.")
        else:
            self.chart.clear("No event performance data published for this team in this season yet.")
        self.search_status.set(f"Loaded team {data['number']} · {len(events)} events · {len(matches)} match records · cached public data.")
        quick = data.get("quick") or {}
        quick_bits = [f"{str(k).replace('_', ' ')}: {v}" for k, v in quick.items()
                      if isinstance(v, (str, int, float, bool))][:7] if isinstance(quick, dict) else []
        quick_line = ("FTCScout quick stats · " + "  |  ".join(quick_bits) + "\n") if quick_bits else ""
        self.profile_footer.set(quick_line + "Source: FTCScout public API · OPR and other calculated statistics are estimates, not guaranteed future match scores. Missing event data is not treated as zero.")

    def refresh(self) -> None:
        pass


class SettingsTab(Tab):
    """The faithful/corrected switches, plus cache housekeeping.

    "Sheet-faithful mode" flips all three quirks at once (max-OPR event for the
    stat row, TB1/TB2 as auto/endgame, OPR history sorted by value); each quirk
    can also be pinned on or off on its own for experiments.
    """

    title = "Settings"

    def build(self) -> None:
        bf = ttk.LabelFrame(self, text="Predictions are based on", padding=8)
        bf.pack(fill="x", pady=(0, 8))
        self.rating_var = tk.StringVar(value="CAP")
        row = ttk.Frame(bf)
        row.pack(fill="x")
        for key, text in (("CAP", "CAP (AutoScout's own rating)"),
                          ("OPR", "OPR (non-penalty)"),
                          ("EPA", "EPA (learns match by match)")):
            ttk.Radiobutton(row, text=text, value=key, variable=self.rating_var,
                            command=self.apply).pack(side="left", padx=(0, 16))
        ttk.Button(row, text="What are CAP, OPR and EPA?", command=self.app.show_cap_help).pack(side="right")
        ttk.Label(bf, foreground="#666", justify="left", wraplength=880, text=(
            "The per-team number that is added up for each alliance: it drives the match predictions, "
            "the accuracy count, projected standings and every alliance table.")
        ).pack(anchor="w", pady=(4, 4))
        erow = ttk.Frame(bf)
        erow.pack(fill="x")
        self.epa_live_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(erow, text="EPA keeps learning from this event's played matches",
                        variable=self.epa_live_var, command=self.apply).pack(side="left")
        self.k0_var = tk.StringVar()
        self.kmin_var = tk.StringVar()
        ttk.Label(erow, text="k max ×mean").pack(side="left", padx=(16, 4))
        ek0 = ttk.Entry(erow, textvariable=self.k0_var, width=6)
        ek0.pack(side="left")
        ttk.Label(erow, text="k min ×mean").pack(side="left", padx=(10, 4))
        ekm = ttk.Entry(erow, textvariable=self.kmin_var, width=6)
        ekm.pack(side="left")
        for e in (ek0, ekm):
            e.bind("<Return>", lambda _e: self.apply())
            e.bind("<FocusOut>", lambda _e: self.apply())
        self.epa_future_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bf, text="EPA also learns from events these teams played after this one "
                                 "(season-long EPA, like StatCube; reload the meet with history to fetch them)",
                        variable=self.epa_future_var, command=self.apply).pack(anchor="w", pady=(4, 0))
        self.epa_info_var = tk.StringVar()
        ttk.Label(bf, textvariable=self.epa_info_var, foreground="#666", justify="left",
                  wraplength=880).pack(anchor="w", pady=(4, 0))

        cols = ttk.Frame(self)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True)
        s = ttk.LabelFrame(left, text="Fidelity", padding=8)
        s.pack(fill="x")
        self.faithful_var = tk.BooleanVar()
        ttk.Checkbutton(s, text="Sheet-faithful mode (reproduce the original spreadsheets' quirks)",
                        variable=self.faithful_var, command=self.apply).pack(anchor="w")
        ttk.Label(s, foreground="#666", justify="left", text=(
            "Off (the default) uses the corrected behaviour: the latest event's stats, real\n"
            "auto/endgame OPR components, and the OPR history in date order.")
        ).pack(anchor="w", pady=(2, 6))

        grid = ttk.Frame(s)
        grid.pack(fill="x")
        self.overrides: dict[str, tk.StringVar] = {}
        quirks = [
            ("stats_from_max_opr_event", "Stats from the team's highest-OPR event"),
            ("auto_endgame_from_tiebreakers", "Auto / Endgame taken from TB1 / TB2"),
            ("proj_opr_sorted_by_value", "Projected OPR fits the history sorted by value"),
        ]
        for r, (key, label) in enumerate(quirks):
            ttk.Label(grid, text=label).grid(row=r, column=0, sticky="w", padx=(12, 8), pady=2)
            var = tk.StringVar(value=TRISTATE[0])
            self.overrides[key] = var
            cb = ttk.Combobox(grid, textvariable=var, values=list(TRISTATE), state="readonly", width=13)
            cb.grid(row=r, column=1, sticky="w", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda _e: self.apply())

        n = ttk.LabelFrame(left, text="Numbers", padding=8)
        n.pack(fill="x", pady=8)
        self.mpt_var = tk.StringVar()
        self.margin_var = tk.StringVar()
        ttk.Label(n, text="Matches per team (blank = count from the schedule)").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        e1 = ttk.Entry(n, textvariable=self.mpt_var, width=8)
        e1.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(n, text="Margin points (Sheet18: trust the call only above this)").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        e2 = ttk.Entry(n, textvariable=self.margin_var, width=8)
        e2.grid(row=1, column=1, sticky="w", pady=2)
        for e in (e1, e2):
            e.bind("<Return>", lambda _e: self.apply())
            e.bind("<FocusOut>", lambda _e: self.apply())
        self.poa_var = tk.BooleanVar()
        ttk.Checkbutton(n, text='Show the "potential of accuracy" column on Matches (experimental)',
                        variable=self.poa_var, command=self.apply).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(n, text="Apply", command=self.apply).grid(row=3, column=0, sticky="w", pady=(8, 0))

        c = ttk.LabelFrame(right, text="Cache", padding=8)
        c.pack(fill="x")
        self.cache_var = tk.StringVar(value=default_cache_dir())
        ttk.Label(c, textvariable=self.cache_var, foreground="#666").pack(anchor="w")
        row = ttk.Frame(c)
        row.pack(anchor="w", pady=(6, 0))
        ttk.Button(row, text="Clear cache", command=self.clear_cache).pack(side="left")
        self.cache_status = tk.StringVar()
        ttk.Label(row, textvariable=self.cache_status).pack(side="left", padx=8)

        a = ttk.LabelFrame(right, text="About", padding=8)
        a.pack(fill="x", pady=8)
        ttk.Label(a, text=f"{APP_FULL_TITLE} {__version__} - {APP_TAGLINE}",
                  font=self.app.font_bold).pack(anchor="w")
        ttk.Label(a, foreground="#666", justify="left", wraplength=430, text=(
            "A port of the eight AutoScout v1.3 spreadsheets. Data from the public "
            "FTCScout REST API; no account or API key needed. CAP is on average "
            "5-10% more accurate than using OPR alone.")).pack(anchor="w")
        ttk.Label(a, foreground="#666", wraplength=430, justify="left",
                  text=f"{APP_ORG_FULL}  ·  {APP_WEBSITE}").pack(anchor="w", pady=(4, 0))
        btns = ttk.Frame(a)
        btns.pack(anchor="w", pady=(6, 0))
        ttk.Button(btns, text="What is CAP?", command=self.app.show_cap_help).pack(side="left")
        ttk.Button(btns, text="Visit the AutoScout website", command=self.app._open_website).pack(side="left", padx=(8, 0))
        self._loading = 0

    def refresh(self) -> None:
        st = self.session.settings
        self._loading += 1
        try:
            self.faithful_var.set(bool(st.faithful))
            self.rating_var.set(st.basis)
            self.epa_live_var.set(bool(st.epa_live))
            self.epa_future_var.set(bool(st.epa_future))
            self.k0_var.set(f"{st.epa_kmax_frac:g}")
            self.kmin_var.set(f"{st.epa_kmin_frac:g}")
            self.epa_info_var.set(self.session.epa_summary())
            for key, var in self.overrides.items():
                var.set(value_to_tristate(getattr(st, key)))
            self.mpt_var.set("" if st.matches_per_team is None else str(st.matches_per_team))
            self.margin_var.set(f"{st.margin_points:g}")
            self.poa_var.set(bool(st.show_potential_of_accuracy))
        finally:
            self._loading -= 1

    def apply(self) -> None:
        if self._loading:
            return
        st: Settings = self.session.settings
        st.faithful = bool(self.faithful_var.get())
        st.rating = self.rating_var.get()
        st.epa_live = bool(self.epa_live_var.get())
        st.epa_future = bool(self.epa_future_var.get())
        st.epa_kmax_frac = min(max(as_float(self.k0_var.get(), 0.4), 0.0), 1.0)
        st.epa_kmin_frac = min(max(as_float(self.kmin_var.get(), 0.2), 0.0), 1.0)
        for key, var in self.overrides.items():
            setattr(st, key, tristate_to_value(var.get()))
        text = self.mpt_var.get().strip()
        st.matches_per_team = as_int(text, 0) or None if text else None
        st.margin_points = as_float(self.margin_var.get(), 10.0)
        st.show_potential_of_accuracy = bool(self.poa_var.get())
        self.app.recompute()

    def clear_cache(self) -> None:
        try:
            self.app.make_client().cache.clear()
            self.cache_status.set("Cache cleared.")
        except Exception as e:
            self.cache_status.set(f"Could not clear the cache: {e}")


_LOGO_PNG_B64_16 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAADEElEQVR42oWTQUgjZxzF3/dNmsQhjJlMiNgZCChoNKZSa6GY"
    "YDRtJdj24mJb7KF60tBjBO1tSw9e2ot4Ueipm4Okh4gUSS+BQgPJYQ/RwthYRAoqrtbETJ1kzPdNL65I6bL/23s8/rzD+wGv"
    "PwKAEkIeDNu2yb3//+FXPHpvaGioIMvy4uO88EhQADYhxL7XDgAcgOr3+5+pqvpkeXk5ZhjGcL1e/0SSJIdhGM8falBK4XK5"
    "VNM0Ozc3N6upVOqOcy4Eg8FMKpX6TJZlnJ2dMZ/PJ4iiiEwm86JQKLz1sq6Lc97j9Xq/m5mZ+X1xcfFzzvnbANjV1dVzQRBY"
    "uVy2CCFCqVRid3d3zLKsawDnAgAhFAp9PzAw8EMymeybnp5+A8CHiqJ8dXl5eRUMBmOdnZ3h/v5+srq6SiRJIvl8nsiyLFSr"
    "1b8pALmrq+vTdDrt7ujoEHO5HOLxuGdhYYF6PJ6Nubm5J5ZlIRaLUQAYHR0ljDF7YmJCDofDaQeAS9M0K8Vi8QPGGF9aWhI2"
    "NjbscDgM0zTtZrNJOefw+XxYX1+HYRjw+/32xcUFarXaiaAoytPJyckvbm5u6MrKihCJRGBZFtF1naiqimw2a4RCIZdhGIhG"
    "o9jd3bVlWSblcvm0VCrN0Z6enpmxsTGX2+1GIBCArutQFAWMMSQSCdJqtdyRSKR9eHjItre3IUkSHxkZYbe3t00AfzpOT09/"
    "q1Qq4Xa7Dc451tbWEAgEbK/XS/b29m4YY7/quv6xpmmQJKkNwFGpVNBsNv8B4BQajcbPtm2/r2la8OTkhCeTSZrP51lvby/J"
    "ZrOb19fXXx4cHKBQKPwRjUbfzeVy1Uwm86zRaDxttVp/OQYHB52EkEAikSDFYpHu7+9jamoKTqeTiKL4JgBiWdY3oij6t7a2"
    "xFqt9guAH+v1+sP2SV9f37fd3d1fDw8Pt8bHx907Ozv28fExPTo6mj0/P/8JgAtA69HsHQAYgJezhwhgUNO0rfn5eRtAGsA7"
    "/wUtHo87XgkbpRSapqmCIHw0OzvruceXvo71fwF6IEIHD6RB7gAAAABJRU5ErkJggg=="
)

_LOGO_PNG_B64_32 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAJ40lEQVR42r1Xa0xUZxp+vnOdYe4DM8NVqoNlYArjJdrGaNVF"
    "Cq2LrG3YmFq1tg3s1myjq1bFH6YXdTV2jf5yp6hZBdtAXV3XFYqR4q71kqYkYsYLLFKCTJkCDiDDGc7t2x8djGndZn/Yvsn5"
    "cfKdnPfyvN/7PC/Bz2MsAPLIuwaA4pc0QghYlv1FfTIAuB07djCiKFYBOABgF8dxWwoLC92Jqkx+9+SdMwyDyYfn+ab58+fT"
    "N954g3o8HurxeBYBAKWUmywQnmA0LCFEB7DVbrf/ZerUqbMVRUn1+XzKypUrpeTkZDUSiZT7fL5ZDMNcdzqdawkhdLIi5Kdg"
    "/EGA+mMaiSWEaISQ1SkpKX/lOA5DQ0OoqKjAs88+C0EQEA6H0djYiLa2Njp37lxy69atuNFonBEOh+8AYMj/UVtQSkEpfRzm"
    "uiAI03mev/7uu++KNptNHxoa4p5++mkqyzJhmIfx63fv3mX8fr9y9OhRvqWlpUmW5aWEEMI8JmseAPF4PG4A5bquv0gpXezz"
    "+dIS51yifCzDMFAU5a1nnnnGmJeXpxmNRm7atGmUUkqGh4fR0dEBTdMAgMnOzqaxWIx/6aWXdKPRWOr1emcB0LgfOKcAFACQ"
    "ZXmO2+0+7fP50N3djcHBwWOEkDUA1ERDQdd1kp2dfbStrW3FlStXsgoLC6miKOSTTz5BKBSC3W5HNBrF66+/jpycHMIwjH75"
    "8mVGUZROv9/fe/fu3YcVIABobm6uxWKxbKSUGqPRqCspKUnbvn17vKSkRB8cHPTqum4wGAy/t9lsOxiG0QBwvb29t1VVjUiS"
    "RJxOp15bW4tIJILz58+jra0N1dXV2LdvH8LhMEwmkz4wMABN066dO3cuMtljBABTX1/PEkL+YbVaKYCbHo9Hfvvtt+nhw4fp"
    "7t279cLCQgrgPxaLRXO73dTtdr+awHiazWbTP/74Y7pr1y7dZrPR3t5eSimlIyMjlFJKN23aRL1eL/3ss8/0LVu2UEJIOBAI"
    "PFVfX8+SBJaarus7A4FA9dq1aydaW1vFBQsWwOVyYXx8HCzLQhAEXLp0CSkpKXo8Hqf79+/vSk1N/VskElmWn5/ve//995md"
    "O3ciEAjgyJEjGB4eBsdxEAQBkUgEs2bNwubNm+FyuXDw4EF0dnbGHA7HNhYAdbvdT+m6fnLDhg16SkoKl5eXR0VRBMuyRFVV"
    "CIIAAMjOztYdDgeTkZHB9Pb2Jnd2ds5ftWqV65VXXiGUUpw+fRrr1q2D3++Hqqrfk4CmwW6349NPP0VycjIyMjLw/PPPq5FI"
    "xNDV1XWdA0ACgcBoa2tr29WrV2ctXbpUn5iYYCRJwiSeHMfh5ZdfRkFBAROPx6HrOkZGRighhGZlZcFkMjGxWAy6riMrKwuE"
    "EBBCYDaboWkaWJaF0+nE8PAwCCFQFIUwDEN1Xf9+fn/++edRWZYPhkIhCIKgx2IxvPfeeygoKMCZM2ewdetWHDp0CNeuXYPL"
    "5UJ3dzd6enqI1WolRqORmSQehmEwPj4OAOB5HlVVVdi9ezcAIB6Pg+d5AIDZbIYkSbqmaaMMAIXnecqy7AszZsyAzWbD8ePH"
    "UVRUhCNHjsDv96OyshLHjx9HbW0tBgYGkJubi9TUVMTjcdLU1IRwOExNJhMIIRgYGAClFJFIBMFgEIcOHcLo6Cj6+vqQlZUF"
    "RVFw6tQp5s6dO6zT6bzOuN1uD6V0n6ZpxV6vFx0dHWxXVxc+/PBDyLIMSZIwMjKC5cuXY8aMGWhubobH40FBQQFkWcalS5dQ"
    "W1tLzGYz0tPT0dzcDEIILBYL8vPzUV5ejosXLyIWiyEnJweRSAR1dXUagM1Op/NfTDQaXThlypSN77zzjisnJwdff/01mTlz"
    "JnJzczExMQGWZaFpGiileO6559DT0wNJklBWVkarqqogiuJgR0fH4I0bN7BixQra0NCAUCgEh8OBUCiE7du3480330R5eTmM"
    "RqPe1dUFg8Fw68GDB/tu3rw5xiiK4kxLS1MXLFig8jyP/v5++Hy+yUkHURThdDpBCIHX60UsFoOiKBAEAW63G2azeVSW5XB7"
    "ezumT59Oi4qKUFJSgmPHjqGurg5FRUXIy8vDokWLMD4+rnd2dkJRlLbELeEYAB6n00kURSG6roNSikkSEUURt2/fxkcfffQ9"
    "9bHsQ1LiOI4oiqJHo9Fp6enphYsWLUJfXx+zfPlyFBcXY8+ePdi7dy/mzJmDtWvX4sGDB5BlmZ03bx4EQfjNqlWrUgGonCAI"
    "0r1799hwOKxnZWXBbrdjYGAAhBAYDAYcOHAANTU1KC4uRk9PDywWCwRBQH9/P/3qq68Yg8EwFI/HxbGxMVNGRgZUVdVLS0uZ"
    "4uJiQgiBqqqQZZmazWZqMBgwNjZGKaWKz+ebAEAYn8/XEAqFmj/44AMyOjqKzMxM3LhxA5IkPRwiACAIAi5cuAC/3w9BEOiJ"
    "Eyf0Cxcu3PF6vX8YGhraf/LkSdLS0qIFg0G2u7ubxGIxRKNRaJqGb7/9lgSDQaa9vV05deoUk5SUtKO6ujr6UKYl7uf51atX"
    "04aGBtVut9OamhpKKaV9fX30+vXrNBgMUofDQWtqaujhw4dVr9dLLRbLiwBQWlpqNRqNd3mepxzHnRBFcXTjxo16XV2dXllZ"
    "Sc1mc5xhmJuCIFCr1VrDcdykcgYDQKiurmZ4nj/R2toKTdPo6tWrsX79ejQ2NkIQBNy+fRvr1q1DVVUVzGYz7ejoYL/55pto"
    "aWnpZQBMU1PTaCAQeGHmzJlFLMu+OjEx8c/+/n643e7xL774QlcU5Y/t7e1zp06d+rtYLPZWogH1h0xIKQUh5O8rVqz4dUlJ"
    "iUYpZa9cuYLGxkZYLBaMjY2hoqIChYWFkCQJqqrqe/bsUe/fv79ckqRzCbGpTsq0pKSkTVar9U+ZmZno6uoCz/NLvvvuuwuP"
    "U1xcQoSwDMMEXC4XFUWRyLJMFy9ejNmzZ5N4PA6HwzE5C6goisRisehWq1UYGhryPirRKioqSENDg/baa6/9ORgM/ru/v3+K"
    "yWSKLFmy5GpDQwNZuHAhe/HiRfWHEowlhGhWq7UiKSmpftu2bRPXrl0T/X4/MjMzEY/HQSmF2WymX375JUlLS1N6enq4mpqa"
    "e8uWLfOfOXNmLPEv+rjl5DFa8kcaEJOaQBTFepPJVHH//v0+i8ViLysrMy1evBiSJOHs2bNoaWlRjUYjl2C6F+/du9eUaCbt"
    "J9Yz+j/OfxQImTNnTrLJZPrT+vXr0wD8dvr06Wpzc7O0YcMGGUBHWVmZ32azHUxPT19DCMEj286Tt9TU1Hyn00nXrFlD582b"
    "R81m89lJnv85V6xJ2c1UVlbymZmZKwHsFUVxZ35+/q8STvmfNfMfAcmyj2b9xO2/yxBjdiTBiCoAAAAASUVORK5CYII="
)

_LOGO_PNG_B64_48 = (
    "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAASu0lEQVR42t1aaXBU15X+7r3vvd5b3RKSoIRAC2gN2JLAbDJC"
    "rIWhMJSQIAHsmNgoKit2xfYQ2ySmZI+pCSSmXAbMDgo4gySCMcYUNl4iHCYgg0BIRkYCZEDWgrZuqde33fkx6o5gcMb5Ezvz"
    "qrq63qvX751zz3e+c+53GvgXP8gPwAY2aAcfck2/5/z/78G+z5fn5OSILpfrUV3Xc4xGY4okSYmSJI1MT0/3dXZ2eocghPyQ"
    "IMsACAAwduzYdEEQOGOMS5LEB2HDnU7nF5xzMuR+AKA/iHwjJLyY7OjRozZKafeMGTPUN998U9mwYYOSkZGhUUpvDjrAAIBz"
    "Tr/NiX+WVxQAT0pKihkxYsSB2bNnP0Ip1X7zm99k6rou+Hw+Gh0dzZKSkpjdbieEkMgnn3xyHKVUS0xMXB0REXElISGhaDC5"
    "2fex8qSwsNAqSVINAG632/tjY2PLAchOp5MXFxfre/bs4fv27eM/+9nPdEIIB+B2OBz7GGM+ANxoNPqmT5+edW/ukn+C8ZRz"
    "DqvVesLn881dsmSJ/MEHH0jBYBAzZsxAQUEBbDYbAoEACCFgjKG1tRWffPIJPv/8c0RGRqK4uFh58803RUEQ6txu94RBB3UA"
    "/B9xQLjPNX3w8/dYTjMajS8FAoENK1asUBYvXizW1NRwt9utTZs2jSmKQhRFGZobMBqNoJTy+vp6zWQysQceeIBUVlaqFRUV"
    "Qk5OzjMXLlx4K/TsfzgChBBwzr8r7vXk5OT469evf5mbm2tZs2YN8Xg8xGQygTEGr9cLzjkYY5AkCYQQBINBqKoKSinMZjM0"
    "TYMsy9B1nb/22mvc5/O5S0tLs15//fWbAOh3SWJSWFjIzGbz45TStZzzUoPBsMbhcKxMSUkZfw8Uyb0EoWnaVAC2ESNGcMYY"
    "CRnp8XhAKYXVaoUgCGhpacH169chiiJsNhsAwOfzhYxHZGQkmTlzJne73c7y8vKpoXcIfw+7g98qgAhVVXcZDAbRZDIhGAxi"
    "YGAAgiB0V1ZWjikqKuoPhXRIW6ABoOPHjz/R3d394eHDh+clJSXpaWlpNBAIQBAECIKAjz/+GEePHkV/fz8AIDo6GsuWLcPE"
    "iRPh9/sBAJIkobW1lZ88eZI6HI7+hx566Hxrayu+Db4EAOjfgkNFUQSl9Mv8/Hxl7969wW3btsmTJ09WBUHo5ZybQ79Zv369"
    "Y/B3ZGgUZs+ePQpAYMqUKfrBgwf1t99+m+/atYvn5ORwAHzWrFn88OHD/NChQzw7O5sD4AUFBby8vJxv2bKFv/POO3zJkiUK"
    "AJ6Tk7NuaE7S+/F1ZmZmvM1m+2Ds2LHPMMb0pKSkGbquR9+5c4cxxkSHwyEMGzaMqqpqzc7OniUIAo+JiVmxadOm5tGjR79G"
    "KeWDEWEAWE1NzRRCiJSdna0rikLMZjM++ugjXLhwAb/97W/x8ccfo6CgAMuWLUNNTQ2WL1+OP/3pT7h06RKsViuCwSDS0tJA"
    "CEFbW5th0HgBAKH3rvyKFSvszc3Nx9xu9yMtLS2b7Xb7satXr34WGxsbnZ+fD0VRiN/vJ+np6cRms4kXL148ZjKZTvX09OwK"
    "BoPDWltbf52VlfWTQQipADSv1zvHbreTzMxMrus6vF4vjh49isWLF2Pt2rUYGBhAf38/3G43VFXFnj17kJqaivLy8nBSJyYm"
    "0qioKHi93kVms1kFEADA2T18zVeuXPmeLMu5q1evltvb24Wurq7UuXPnoqSkhI8ePZqEkio2Nha5ublwOByoq6tLMplM4iuv"
    "vKLV19eT27dv52/atGlnc3NzjN1uz+jr61sZHx8fn5eXB0mSyLFjx9DY2Ij9+/djxIgRUBQFgiCAMQZFUWC1WjFy5Ejs378f"
    "o0ePxqhRoyCKIrlz5w4aGxtjDAbD6MjIyLGlpaVfkaF8bbVaf+XxeP5jxYoVypIlS8Smpibe09Ojjxs3jt6Pr0VRhNFoREtL"
    "i6brOk1LSyN/+ctftLfeeovFx8dv7e3tzff5fBmcc8ybNw+rVq2C2+3GL3/5S0yePBl//vOf4fV6h+YbOOcQBAEejwcJCQnI"
    "zMxEaWkpAoEAOjs7sW/fPnR3d0NRFEycOPFhYRD32oQJE+LPnz//66lTp+rz588XOjs7ERsbS+Li4pjP5wPnHJIkQRRFEEIg"
    "y3L4ExcXxwCgp6cH2dnZLDs7m1+8ePFpAHpeXh6fP38+TCYTkWUZHo8Hsixj3rx5IIRA1/W7HCCEQFEUOJ1OpKWlob29HQCg"
    "qipiYmLw2muv4bPPPlN37doFRVG0EFVCVdV4SqmVEEI0TSOhcIZWyGq1wu/349KlSzh79iw8Hg9sNhsYYwgGg5BlGQBgMBgw"
    "adIkwjkH55ympqaShIQEIggCKKXo7e0FAIwaNQpDu1POOTRNg6ZpYaeSk5PR1dWFYDAISilUVYWqqtA0jTHGhAcffNAphPh6"
    "8+bNNY8//vjeM2fOrE5PT9enT59OPR5PGJvHjx9HZWXlXZS1ZMkSLFy4MPxSSZLQ1taGw4cPQ5IkyLIMxhgCgUC44rpcLgBA"
    "cnLyXZWdMQaLxQIA4ZrgdDrh9/vDlTl0P6WUaJqGc+fO+cKxy8/PV48dO/YMpbS/rq6OUko5IQSEEGzduhWVlZUoKChAdXU1"
    "vvjiCyxbtgzvvvsudu3aBUEQoOs6DAYDGhoa0NXVheTkZB0A7HY7NE0Lr7LRaAQAdHV1/U8zpevhKBYXF2Pjxo2QJAkAEAwG"
    "wRgLGz8YVQwMDIAQgqioKFkYksR03rx5M3Vdt2VlZemKolCz2Yzjx4+jtrYWv//97/Hcc8+FH3To0CGMHz8e69atQ2JiIh55"
    "5BH4/X5kZGRAkiR4vV4KALIsw2azhekwKioKhJCwA5qmwWg0Yvfu3di5cycAYNasWcjJycFXX32F2NhYGI1GeL1eGAwGmM1m"
    "3traqkuS5F66dOmtUAQUAGogEJhqNBpJWlqaxjmH1+vF+++/j/z8fDz33HMYGBjAwMAAPB4PBgYG8PLLL2PRokU4cuQIuru7"
    "QQhBbGwskpKS4Ha74XA4UF5eju3bt0OWZVBKIQgCOOfhXAg1ho2NjTAYDGCM4euvv4aiKKitrUVKSgoopRBFEU1NTXj99df5"
    "hQsXGKW0o6SkpJUCQExMzLjo6OjH3G73PKfTCavVykRRRHV1NQKBAF588UXouh7GKmMsHP5XXnkFwWAQZ86cgcFgAABMmjQJ"
    "brcbLpcLPT09+OSTT3D27FkIggCHwwGLxYIjR45gaMuSlZWFYDAITdPw0EMP4cMPP0QwGER2djYURYEkSTh9+jQuXryoORyO"
    "96OiojZomkYESilcLlc55zzLarUiNTUVkiTRQCCAkydPYsKECZg1axb8fj8E4W+9Xyg5MzMzkZKSgrNnz2LhwoWQZRnZ2dlo"
    "aWlBW1sbWlpaOKWUVFdXIz8/HxEREZgzZw6OHj2Kc+fOYeLEiejv78fy5ctx48YNJCUlISYmBs8++yyGDRuGtLQ0BINBmEwm"
    "3tvbSyIiIvrb29sfHdzUEFpcXOxUFGXEjBkz1DfeeENftmwZFEWBLMtwu92YMWMGGGPhRBx6hPCbkJAAl8sVjpLBYMAvfvEL"
    "FBUVQdM0PTIy8vP29na9oaGBa5qG2bNnw2Kx4Pnnnw/3/ZRSbNiwAU8++SRKSkpw48YNrFq1Kgyrjo4O/datWyCEnCsqKqIA"
    "RACcNjQ0WDnnZqfTKdhsNhJihb6+PhBCkJ6efl++DhkLAKmpqfD5fPD5fKCUQtd1eDwe+Hw+HQDLyMio5Zy7qquryeD2EqtW"
    "rcKZM2dQVFSEjo4OEELQ0tKCZ599Fvv27cPChQuRlZUFj8cDk8mEixcv8kAggHHjxr1bVVWl5eXlcQAQAoGAHYBJEASoqhpu"
    "pT0eDzjniI+Pv6vMD+XrgYEBAEBcXBx0XYcsy2GapJSCMUYJIfzGjRsLATimTJkCzjl8Ph8mT56Mvr4+VFRU4MiRI4iPj8et"
    "W7eg6zrmzJmDpUuXwu/3g1IKRVGQnJxMCCFobm6eRSndXV1drQOA0NzcHAVAHDly5F18bTKZAACdnZ3ha6FquGrVKlgsFrzx"
    "xhsAgDt37oAQAkmSwlxNCEFvby845+TmzZvJ48ePx7Rp0+DxeMAYg9/vx8KFC5GRkYFLly7B5/MhNzcX6enpiIuLQzAYDL8z"
    "EAggPT2d5eTkoK6ubvkLL7zw7xs3bvxy/fr1VIiIiJBdLpfe29urGwwGIcTXTqcThJCwA5qmwW63o6KiAgcPHsTgRgVLly7F"
    "hQsXYLVaYTabIcsyJEmCJEm4fv26JoqiYjabeXd3t3FwF0c45xBFEf39/YiPj8eYMWPCfZEsy+HKHaLcwXrCe3p6YDKZvImJ"
    "iS4AKCsrAy0tLa03GAx3Dhw4ILz66qu8r68PgiCES3xbW9tdfN3S0gJRFCEIAm7evAkAqKurw5gxYyCKIkRRRHNzM15++WV+"
    "9uxZZrfbAykpKSVtbW2kqqqKR0REQFVVuFwuPSIiApqmweVywe12Y2BgILzyZrMZXq8XwWAQUVFROH36tNbS0kLi4uJ2lJSU"
    "fDNYfHV26tQpJT4+vlWSpEBLS0u6qqosJycHgiDg8uXLuHLlCkpKSkAICTu2e/du6LqOzZs34/Lly9i5cyceffRRxMXFQRRF"
    "fPrpp6ipqeFxcXGVoijubWpq2h0ZGZnZ2Nj4o+TkZLWiogJVVVV0sJOFxWKBruthXchisaC+vl773e9+R7q7uxEREcG3b9/O"
    "nE5n16JFi35SU1PjD6VleCcmCAKMRuNxq9XKt2zZor7zzjv85z//OQfA//CHP3DOOe/t7eWKovA9e/bwAwcOcE3TeGZmJnc4"
    "HHznzp387bff5gcPHtSnTp3KGWN9QzRNUlhYONxoNLpCAq7dbr8KgFssFv7MM8/w/fv38x07dvBt27bxSZMm8UE1jg/yPTca"
    "jb6ZM2c+fK8kSgcfKKmqykaNGrXb4/Hof/3rX6GqKnJychAXF4e1a9fizp07cDqdCAaDWL16NVauXIni4mJ8+eWXeOKJJ8J8"
    "3dnZqdfV1cFisdSG9j2FhYW0qqqqY+zYsY9HR0d3paamHnK73eMnTZq0wOv1eq9cucIBcFEUcerUKX7u3DmekpJyZPr06dOc"
    "TmfDsGHDgikpKUs+/fTTz0PQuVfcVQBoc+bM+RyA78yZM0zTNM4Yw09/+lN0dHRg7ty5qKurgyzLaGhowFNPPYXdu3dj/vz5"
    "d/H11atXudfrRUJCQhUhRAfAq6qqNACkvr7+vT/+8Y9jr1279mNCSPD8+fMnAHQpiqLb7Xbd4XDo7e3tnFLadfXq1Z989NFH"
    "/7Vy5coFixcvHn/58uUPh0g3/2vExABoNpvthYGBgU1PP/20NnHiRObz+WA2m1FfX49t27bB7/cjMjIy3IgtWLAABQUFUBQl"
    "zBoul0srKytjiqJ85vf75wzRMcNqXUjwHdzl1ZvN5ozs7GwYDAbU1tbC7Xb3vfjii6PKysr8Qwym99OBQkMEzjlnhJCmxMTE"
    "xFdffZV7vV5KCIGmabBarejt7UVDQwP6+/sRFRWF5ORkxMTEwO/3hxNQ13U4HA4cPHiQnzhxghQVFU2prKw8W1hYyAajgCHC"
    "FwXAx4wZs+D27dv/FgwGkwFYCCFdI0eOrP7mm2+e0nWdACDr169HWVmZ/m2Cbehhut1ub+vq6kpqa2vTo6KioGkaTCYTPB4P"
    "zGYz8vLywq2CLMvwer1h5YxzHtoj86+//pozxjyiKLYCQFVV1dCX8yHCMK5du3acUnp8y5Yt1qamJktubm53UVGRNuReXlZW"
    "9n9OKRkAraCgIOvIkSM1GRkZZN26dbS7u5t0dXXpycnJhHNO/H7/XaoEAFitVty+fZszxkhSUhJOnjyp7tmzRxg+fPjGjo6O"
    "X90Pt/dRsO+dSpLvOqVkQzxljY2NbfHx8dq1a9dmR0ZGKidOnEBFRQVrb28nMTExcDqdd7XTJpMJtbW12saNG2lTUxN3Op36"
    "jh07BJPJdGvFihU/rqmpUb6DIfweOH9n4+87gFu/fr1ktVovA+CEEB4TE1NDCPEA4E888QQvLy/n27dv51u3bg1rm06n08sY"
    "4wC4zWaTc3Nz876vKSgFgClTpvwoIiLi/OjRo3dzzklpaWkapbQ7Ly+Pl5eX63v37uXLly/XAPCUlJTy559/PtHpdB6Oiopy"
    "P/zww3O/7xEuCUFkKFwIIV9kZWWpVVVV8nvvvacuWLBAJYT4XnrppWgAWLNmjfmxxx6L+6GMROmQGYHAOSc2m+1Dg8HAs7Oz"
    "+cyZM/nw4cO5xWJRBx2gP5Th+bc688ADDzw4atSo/zQajTUALtnt9tOpqambOedsSPLR7wUu39mTQVkkpEioqvov9b8KOuSf"
    "JUPPv9fjvwELF+kgIDf0+gAAAABJRU5ErkJggg=="
)

_LOGO_PNG_B64_56 = (
    "iVBORw0KGgoAAAANSUhEUgAAADgAAAA4CAYAAACohjseAAAXmUlEQVR42u1aeXRV1b3+9t7n3Dn33gwkEJMwBRKGEEyYLYMQ"
    "JdFCWywgxSqW1j6pFKmy6kKEx6ouS3mkS7RYhDIVEQ0VlC6BEAQlNCgBSiIZAZOYhAyEm+GO556z9/vDe85LLFrt63vatXrW"
    "Ouuedc++++7f/P0G4N/Xv/ZFvqHnorc4n/6sARD/6gR+SiX9lE4hRJ/Pf+WLEkLgcDieJoS8D+DPAP5MCHmNMbY7Pj5+xbJl"
    "yxwRwZB/BSHp6ihFPokQgthstssARExMjIiJiRE2m00wxoTVahWTJ08eH/kd66XK9PM2l74BxPHIDQBmxljI5XL9NRgMpq1c"
    "uVKNj4+XhBBi165d/OzZszQQCNh6ERcWQhBCCO+11y2N+eu4JAC8X79+dwwbNmzbL37xi+EAQvv37zdxzt2cc9bT0yOZzWYW"
    "FRUlORwOSimVoqOj0yilGmMsPHDgwP+MiYmpSE9Pnxchjn1T1JIBwG233ZYpy7IHgEhKSro6cuTIJZIkXQQgoqKi+LPPPiu2"
    "bdsmdu/eLZYsWcIBcMaYt3///i/Fx8evYIwJAMJutwdyc3Nvv5XQyNellhMmTBhcVlb2fjAYTEpJSVEaGhpMhHx6nLy8PHH3"
    "3XcTh8MBVVVBKQXnHMXFxTh9+jTq6+sBAFFRUfyOO+7gR48elWJiYqp+//vfZy1YsECJSFPgaxApAUDmz58vnz59+ngwGEyb"
    "N2+e+qMf/UiuqakRnHO+bNky5ObmUgDQNA060YQQjBgxAtOmTRMWi4X39PSIvLw8tnjxYnr16lX12rVr8eXl5aEbN26citAl"
    "/hEJki9gypcJwAyAZrPZnvb7/c9OmjQp/Nhjj8mBQABCCCiKAofDgUAgAEIICCHgnBvPmqaBMQaLxYJwOGzEyubmZrFmzRoe"
    "HR0dXLZsWcZzzz33sa4pX9XJCADq59ziSzBHGzNmTHwgEPhFYmIif+ihhyS/3w9VVSGEgMlkMogTQoBSCpvNBovFYkgRAPx+"
    "PzRNg6qqCIVCSE5OJnl5efB4PPa33377v4QQRpyUvoLkRHp6+oD6+vrHOOeSpml+i8WimkwmryRJHcOHDz9WXFzcrq/9HNvT"
    "wuFwOqU0hlLKOedEkiTj4DpSkWUZkiTB6/WiuroaVqsVAwcOhNVqhd/v76O2QggIIfCtb32LFBYW8oqKimydmV+GQClyWAJA"
    "9Xq9MxRFWa1z1+v1fqp3jMHj8ewihDwshGCRzWkv1dU/6eOPP17yzDPP7GxsbHx4586d2mOPPcY07dMlnHPY7Xa0tLTg8OHD"
    "KCkpgaqqAIDExETMmTMHkyZNQjgcNlSXUgpN07B//37BOWcZGRlnKKV6yNCkvyM1NfIsA2Aul6u+qalJnT17Ns/NzaVdXV2o"
    "q6tTd+7cKfv9ftv3v/99VlBQQAGohBAtIpHeEhU//elPw0KIHzscjszS0tKs6upqnpaWRv1+P2w2G0pKSrBr1y4EAgFMnjwZ"
    "eXl5aG9vx/bt27F161ZUV1fjgQcegBACmqYhKioK7777rigvL6fJyclNCxcufLysrIzoQV/6PE/HGOOJiYkLExISPiktLf2L"
    "/o4QIvl8PpGQkEBiY2NhsVgIpZR1d3dHHzx4UAOgLV682FlUVLQ5Li7uSkVFxbO9pAoAZPLkyeZQKGS1Wq1wuVxQVRVmsxlN"
    "TU3Yvn07KKV46aWX8OijjxqAe+XKlVi0aBFOnToFm82GRYsWobu7G5qmoV+/foIQQoPBYMfatWv7mAn9HE/HnU7nc42Njfur"
    "q6tPT5s27c6YmJgHa2pqXuecC5PJBEVR4PV6YTKZGKVUdHR0THW73TsmTZqU9dZbb+1rbW19qLq6+leZmZlPEEK0yL4yAFZf"
    "Xz9NVdURkyZN4klJSTQUCkGWZRw4cADhcBg7d+7Ez372M/h8PnR3d6OrqwuDBw/GW2+9hdTUVBw5cgRVVVWw2+0IBoNITU0l"
    "cXFxwuPxjMzNzR3WS+v+hkAJgJqYmPgfHo9ntSzLSk9PDz179uzxmzdv7jabzbctXryYfO973yOhUAiEEDidTixZsoQMHDjQ"
    "cuPGjYdLS0vPe73ee1NTU1Wz2azW1tb+V05Ozh0RCSoAVJ/PN4QQIlJSUjjnHBaLBXV1dbh48SJycnKwaNEidHV1gVIKSZIg"
    "yzK6urqQkJCAF198EYQQvPnmm9Bjpc1mI5mZmZqmaVI4HJ4bQTjhzwZ6BkBLSEi488aNGwUWi0V75plnJIvFgqqqKpqWlsaf"
    "fPJJjB07lnDOIYQwYtPQoUMxbdo0JCYmapcvX6Y2m02sXbuWxcXFibNnz1Kv1zvK6/Xufvnll+eYTKY7e3p6viuESP32t78N"
    "t9tNCCHYuXMnWltb8cILL2DYsGEIh8OGeuqOLBgMYtSoUXj//fdx7tw5jB8/Hm63G0IIWCwWcvr0afLxxx9PYIx9Lzk5eeLu"
    "3btPsd5hIDs721VXV3dMVVXX8uXLMXLkSDpkyBCSlZUlcnJyqNPpJD6fr49bJ4RAURQIITB48GA6ceJEMX36dGK1WpGSkkIv"
    "Xbokmpqakg4cOCCuX7/+ktfrnSOESI2JiUFeXh6x2WxoamrCa6+9hszMTPz617+Goih9iNMvzjlMJhPa2tpw/PhxpKSkYNiw"
    "YfD7/YiNjSVdXV1QVdUSCASSFEXJslqtr+hOhhFC1GvXrq0Kh8Mp9913n5qVlSV1dXWBMYbBgweTcDiMUChk4EJJkkApNdy4"
    "EAI+nw8ul4sAgKIokGUZCxYsIPn5+aK2tnZ1OBzm2dnZ4rvf/S61WCzUZrOBEAKPxwMAmDVrFkwmE4LBICRJumWGTwjB2LFj"
    "AQAdHR19mL1kyRKYTCb+3HPPicrKyq5AIBDW2cQjh50NgA8ePJjocQYAQqEQdLU0m82w2+1QFAUejweMMTgcDgNOqaoKVVXB"
    "GIPf78fYsWMxZswYoiiKJISQ09PTpfT0dOp0OsE5B6UUN2/eBAAkJSXdMl71NgnOOZKTk0EIQWtra59yRigUgqZpRFVVFhUV"
    "5czJyYnW2UQ45xg0aNC+mpqacdu2bdPWr18PHc3rqMHhcODy5cs4evQoKioqEAqFkJSUhJkzZ2Lq1KkGgfpBbDYbqqqqUFVV"
    "9T9eTJIMqKXv29LSAgBIT0/vA8l04qxWK1RVRTgchqqqcLlcsFqtuHnzJnoLojfEUxRF++ijjzQDbQghWEVFxW9TUlLyu7q6"
    "WFFRkWaxWIwNGGN4/fXX8fzzz+PixYsYO3YsFi1aBI/Hgz179iA/Px8+nw+SJIFzbjiFHTt2IBgMIiUlRURSHEPaBsCNSEHH"
    "nL2/t9lsuHr1Krq6umA2mw1o9rlgWQhwzhEKhZSPPvrI39uSiaZpJBAIXIgsFL05eODAARw+fBijRo3CiRMnUFJSgn379qGi"
    "ogIPPvggKisrsXnzZmiaBkppHy8ryzKcTqdBoKZpxnsAiI6OBgDU1NQYh9Q0DRaLBUeOHMHYsWMxY8YMNDc3Q5IkhEIhBINB"
    "2O32PniUEIJQKISOjg643W5t3rx54d71RyKEIG1tbT+VZRlTpkwhwWAQNpsNV65cQWFhIYYMGYITJ05g5syZ8Hq96O7uRmJi"
    "Inbv3o0f/vCHuHr1Ko4ePQq73Y5wOAyHw4HJkycjFApBURQKAC0tLbBYLDCZTAa3Y2NjAQCBQOBvnMpvf/tbBAIBVFZW4uDB"
    "g2CMoaysDBGTMpjJGIPdbofH49E6Ozs1p9NZvXTp0uu0VxoUjkhsRGxsLOLj42k4HIYsyzh27Bg459i4cSMSEhLQ2dkJxhgk"
    "SUIgEEAwGMSLL76I1NRUvPPOO2hqaoLFYkEoFEJGRgYopQiFQrDZbNi3bx9+9atfoaqqCroJ6CFBt0WdOE3TDEcmSRIaGhog"
    "hMCHH34IABg0aBA0TYMkSejo6MCGDRuwYcMGKVLPqSaECAoA06dPdyQlJU1PSUl5RNO02JSUFMiyTEwmE65du4Zz584hIyMD"
    "99xzD3w+H0wmU58AHAqF4HK5sGrVKoRCIZSUlMBqteq2h8TERNTX10NRFIRCIVRUVIg//elPEEJAVVUkJiaCMYaDBw8aIUIP"
    "RcOGDTNyv/Hjx4MQgsOHD4MxhiFDhiAYDCICRkR5eTnC4fD1hISEY1FRUXsMqHb+/Pl1zc3Npz755JOXhRAkISEBlFIwxnD6"
    "9GlwzvHLX/4SFovF8H598J0kQVVVzJw5EzabDefOnTPyNkmSMHv2bLjdbpjNZhBCIMsyqaurQ2VlJRhjGDBgAG6//XbU1NTg"
    "/fffh9VqhaZpCIfDWLt2LaZMmYKFCxdi4cKF2L59O8rLyzFx4kRER0cbiKerq0ujlIpZs2Ztb29vz21oaDgBAJQxBlVVx5lM"
    "Jv7QQw+Fly9fjlmzZiEYDEIIgStXrsBqtWLmzJkIh8Ng7G8rFrpxDxw4EIMGDcL169cRCoUMFZ44cSI2btyIBx98UAgh4HQ6"
    "a4UQ4sMPPzSYc8899wAA1q5dCyEEZFmG3+9Hamoqjh8/jv379+PKlSt46qmnYLFYMGfOHKMgpWkaqqurCeectLW1XeSc0+zs"
    "7E/BtqqqVFGUOJfLRXNyctiECRNgs9kghEA4HEZnZyeSk5MNbvV275/tG1BKERsbawRdfa2qqrBarTCbzTySvJ612Wznz549"
    "i6tXr3IhBFJTUzFjxgx88MEHWL16Nex2O1wuFxhjsNls6OjowP3334+Ojg7MmzcPycnJCAQCsFqtqK2t5ZWVlSwhIaFp/Pjx"
    "JwDw8+fPqwBAL1++LHHOLTry8Hq9hv53d3ejs7MTI0aMuKV66g6Cc27EvqysLAgh0NnZCUmS+oQLnRlOp9NKCFEURUFnZydk"
    "WUYgEMB9992H/v37Y8OGDVi4cCFOnDiB0tJSbN68GVOnTsXFixdx1113ITc3Fz09PYYZ3bx5Uwgh4HA4mrds2dLdJx/ctGmT"
    "ixDitlqtkGWZfBZFAIDL5fqb7g4hBDabzUAs+jvdAem/7b0+kviiqakpx+fzTR43bhwyMzNpIBAw9nnyySeRkZGBN954Azk5"
    "OZg0aRJWrFiB2tpa3Hvvvbj//vuNwhSlFMFgEKNHj2ZxcXG8oaEhe9q0aXcDEPPnz2cAIBUXF0cJIawpKSmGzTDGDNxJCEFj"
    "Y6Ohgjqhsixj1apVeOedd7B161ZMmTIFQgi0trYCAKxWq4GCdMlfv34dAFBXV+c2mUyYO3dun7CgKAqio6OxcuVKVFRU4MaN"
    "GxBCIDY2FgMHDkR0dDT0EqMuCE3T4HQ6MXfuXL5jxw7J4/Esp5QWFhQUfOoA4+Pj1StXrgiPxyNMJpNRl9Q0DQ6HA9HR0Whp"
    "aelj0A6HAyUlJcjPzwcAPP744ygpKQHnHH/9619BKYXT6TQIs1qtsNvtqK+vF5RSHh8fj/b2dtbU1ITBgwcjEAgYmYKesWRk"
    "ZBgOjXMORVHg8/kMJuvM01FMY2MjAMDhcNT3qgWBFhcXN9vt9iuXLl0izz//PD916hRkWTa4pJfvFEUxNiSEoKqqCowxMMbw"
    "8ccfo7u7Gx6PB7W1tbjttttgt9uhqipkWca5c+fwm9/8BmVlZcRqtbIxY8Y8rmlaz759+0RbW5swmUwwm80wm82Gunu9XvT0"
    "9KCnpwc+n68PiDebzbBaraCUwmq1oq6uThQVFRG32x1IT0/frKNMveGoRkVF7bLZbN2XLl0iO3fuFI2NjZBlGZRSJCcn45NP"
    "PsHFixf7gF0dRWiahpiYGMTExKC4uBiBQAAZGRnGWlVVsX//fnzwwQeQZdnrcDiqjh079sqAAQN+39PTQw4ePKi5XC4cOnQI"
    "GzduVCsrK7nD4TBwJqXUkBpjDG63G+Xl5SI/P583NzfDZDLh1VdfVQkhLDk5efv27dtr9LqSUbLYsWNHKaV0O4C21tbW2UII"
    "LTs7mwKA2WzG2bNnIUkS5s6da+SGKSkpqK2tRUNDA/Lz85GZmYklS5agubkZP/jBD+B0OnUC+fHjx4nZbG5et27d7bm5uRvH"
    "jRunFhYWFr/++usL6urq4hoaGtSioiLa3t7Ozpw5QxobG2G1WtG/f39DzfU06+DBg3zv3r20paWFNDU1iQsXLmjl5eXyoEGD"
    "PnrkkUeWFBYWKrfqExIAeOCBB+yyLHvcbrd4+eWX+bZt28TWrVtFYmKikGVZXLhwQXDOhcfjEYFAQHi9XlFVVSWEECI/P18A"
    "EFOmTBF79uwRv/vd78SuXbvEmjVrVEKIiImJOdHrvyQAGDVq1N1ms1kAEE6nUwwfPvy5mJiYvwDgkiTxTZs2iW3btolXXnlF"
    "vPDCCyIhIYEDEG632xsfH18TCQXC7XZ/PG/evIG3ap/1BtvSvn37fP3799/b2dmJkydPckmSYDabMW/ePITDYSxfvhyKosDl"
    "coFzDlmWkZaWhkOHDmHVqlVwuVxYuHChYa+EEJw48SldCQkJx3o1b9T58+ezioqKwrS0tBVpaWkXs7Ozl9XU1Dzt9XqnOJ3O"
    "P2uaRrq7uzW9Z3Hy5Ene2tqKzMzMd++6667spUuXTk1MTGxNTU2tnTp16l1vvvlmfSQ08M9tYUcM+CyAxy5cuCDuvfde+Hw+"
    "jBs3DhMnTsSZM2eQl5eHLVu2YMCAAWhra8OhQ4ewevVqcM7x4x//GC6XC36/3wg5VVVV1GQy8YkTJx6srKwU+gEKCgo0AKSs"
    "rGyzyWTaXF1dDQBmRVFChJBremvb7XYzSZLg8/k4IYSZzeYNBQUF1QDwhz/8IWPIkCGBO++807tu3Tq6fv167Yt69NrJkyel"
    "2bNnr2SM4Tvf+Q7VXbGiKHj44Ydht9vx7rvvYsSIEYiLizNKBlFRUXj00UeRmZkJn88Hxhg0TYPdbkd2djYvKipiR44ceQjA"
    "ms+07AQAqiiK3mPXImGlIxQK0YKCAl5eXq4xxlBTUwNCCAkEApbIWrp06dJ2XRPXr1/Pv2hORq+JzmltbX17woQJ2ooVK1h3"
    "d7eRUFJKYbFYcP78eZSVlaGrqwtutxtDhw7F6NGj4Xa74ff7+4ABSZLQ09Mj1qxZQyil3qeeeir16aefbo0MDnxeB0pkZmaO"
    "bGhoeM3j8WT0fhkbGxscM2bM2JMnT1bra3sx6gunLEikJhKr/0D3XnpY0DQNPp8PWVlZmDBhQp8qVzAYNIjTIZoeMxljIIQI"
    "SilRFOXvNVw5AHLp0qXLQojbhw8fPtrhcKQKIezNzc21qampbadOnbraa+2XnnQiALBu3TrXli1bPmxvbx/285//nGdlZdFQ"
    "KAS73Q5N0+D3+/tk4L1xpn7Z7XZwzuH1ehEdHY1du3aphYWFUr9+/ba1t7c/omvLl+lH/tOmHfTn9957LzBhwoSK5ubmxVVV"
    "VSInJ4cGAgEUFBRwv98vhg4dSmRZNnrn+q3jVlmW8fbbb4vm5maSlZWFCxcuiL1798Jut/vHjRt3/7Vr17r/nkp9hkgauVmv"
    "6SbxjxIoAEj19fVXBwwYENPa2jolFAqFi4uLyZkzZ2hpaSlpaGgQNpuNxMXF9ckHrVYrbty4gb1792qFhYW0rKxMREVFkT17"
    "9oQ553JSUtITpaWlx3sjjK/QMhe9pib+11IlAFhOTo7L4XA06Jv279//Snx8/Dn9zx555BEjmO/evVv85Cc/ERaLRQAQiYmJ"
    "XbIsCwAKY0ykpqa+FgHNX8uQDr0Fx1BUVNQ1YsSIB6KiopoTEhIqc3Nz81pbWyfecccdKwkhpKWlhespTmdnJ1599VUhhFCz"
    "s7M3LVu2bKTL5XorKipKHj58+M7a2tofappGv6Lk/n/mSJ944gn78uXLzfqX2dnZ6QDUGTNm8IKCAvHHP/5RbNy4UQUg4uLi"
    "/qI7n9zcXGdOTs49vZzR1zYRKH2B7tNNmzb5esdJs9nczRgTlZWV4syZM6rT6STXr1/XAAhCyM39+/ezBQsW0KNHj3YDeKcX"
    "Yd/YQc8+c5lvvPGGKT4+/oTJZBK9jF6YzWYxevTobZFwIUWmmdg3RhW/yqyMEMKUm5ub197ePqitrc1pNps9AwYM6GKMHX/v"
    "vfda/pkx7N/X/5XUp0+fLk2fPl2KqKP+/I0cLf5vdOjRmzbS1N8AAAAASUVORK5CYII="
)


_LOGO_IMG_CACHE: dict[int, tk.PhotoImage] = {}


def logo_image(size: int) -> tk.PhotoImage:
    """Return (and cache) the embedded logo as a PhotoImage at an exact size."""
    if size not in _LOGO_IMG_CACHE:
        data = {16: _LOGO_PNG_B64_16, 32: _LOGO_PNG_B64_32,
                48: _LOGO_PNG_B64_48, 56: _LOGO_PNG_B64_56}.get(size, _LOGO_PNG_B64_48)
        _LOGO_IMG_CACHE[size] = tk.PhotoImage(data=data)
    return _LOGO_IMG_CACHE[size]


def paint_logo(canvas: tk.Canvas, size: int = 56) -> None:
    """Draw the AutoScout gears mark onto an existing Canvas."""
    canvas.configure(width=size, height=size, bg=THEME_BG, highlightthickness=0)
    canvas.delete("all")
    img = logo_image(size if size in (16, 32, 48, 56) else 56)
    canvas.image = img   # keep a reference so Tk doesn't garbage-collect it
    canvas.create_image(size / 2, size / 2, image=img)


def build_icon(size: int = 32) -> tk.PhotoImage:
    """The embedded logo, for the window/taskbar icon."""
    return logo_image(size if size in (16, 32, 48, 56) else 32)


def resolve_font_family(candidates: Sequence[str], fallback: str) -> str:
    """Pick the first installed font family from `candidates`, else `fallback`.

    Requires a Tk root to already exist. Roboto/IBM Plex Mono are optional
    branding fonts - if the OS doesn't have them, this quietly falls back to
    Tk's default font rather than erroring.
    """
    try:
        import tkinter.font as tkfont
        available = {f.lower() for f in tkfont.families()}
    except Exception:
        return fallback
    for cand in candidates:
        if cand.lower() in available:
            return cand
    return fallback


class Tooltip:
    """A small hover hint - attach to any widget with a one-line explanation.

    Used sparingly, on the handful of controls whose purpose isn't obvious
    from the label alone (this is the low-learning-curve safety net, not a
    replacement for clear labels).
    """

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.widget.winfo_viewable():
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", background="#fffbe6",
                 foreground=UI_TEXT, relief="solid", borderwidth=1,
                 padx=8, pady=4, wraplength=320).pack()

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class Collapsible(ttk.Frame):
    """A section that starts collapsed - progressive disclosure for anything
    a first-time user doesn't need to see (advanced/optional settings)."""

    def __init__(self, master, text: str, start_open: bool = False, **kw):
        super().__init__(master, **kw)
        self._open = tk.BooleanVar(value=start_open)
        self._header = ttk.Checkbutton(self, text=text, variable=self._open,
                                        style="Collapsible.TCheckbutton",
                                        command=self._toggle)
        self._header.pack(anchor="w")
        self.body = ttk.Frame(self, padding=(20, 4, 0, 0))
        if start_open:
            self.body.pack(fill="x", anchor="w")

    def _toggle(self) -> None:
        if self._open.get():
            self.body.pack(fill="x", anchor="w")
        else:
            self.body.pack_forget()


class NavShell(ttk.Frame):
    """Horizontal, grouped navigation with a compact page header."""
    ICONS = {"Overview":"⌂", "Competition Day":"⚑", "Setup":"◈", "Teams":"▦",
             "Team Research":"⌕", "Matches":"⚡", "My Team":"★", "Standings":"☷",
             "Alliances":"◇", "Win Rate Curve":"⌁", "Analysis":"◉",
             "Charts":"▥", "Live Scouting":"●", "Settings":"⚙"}
    GROUPS = [("WORKSPACE", ["Overview", "Competition Day", "Setup", "Teams", "Team Research", "Matches", "My Team"]),
              ("STRATEGY", ["Standings", "Alliances", "Win Rate Curve", "Analysis"]),
              ("INSIGHTS", ["Charts", "Live Scouting"])]

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self._pages = []
        self._tabs = {}
        self._labels = {}
        self._current = None
        self.configure(style="TFrame")

        navwrap = tk.Frame(self, bg=UI_PAGE_BG, padx=12, pady=7)
        navwrap.pack(fill="x", side="top")
        navrow = tk.Frame(navwrap, bg=UI_PAGE_BG)
        navrow.pack(fill="x")
        self.nav_buttons = {}
        for group, names in self.GROUPS:
            group_frame = tk.Frame(navrow, bg=UI_PAGE_BG)
            group_frame.pack(side="left", padx=(0, 9), anchor="n")
            tk.Label(group_frame, text=group, bg=UI_PAGE_BG, fg=UI_MUTED,
                     font=(app.title_family, 7, "bold")).pack(anchor="w", padx=3, pady=(0, 3))
            buttons = tk.Frame(group_frame, bg=UI_PAGE_BG)
            buttons.pack(anchor="w")
            for name in names:
                self._make_nav(buttons, name)
        settings_frame = tk.Frame(navrow, bg=UI_PAGE_BG)
        settings_frame.pack(side="right", anchor="n")
        tk.Label(settings_frame, text="TOOLS", bg=UI_PAGE_BG, fg=UI_MUTED,
                 font=(app.title_family, 7, "bold")).pack(anchor="e", pady=(0, 3))
        self._make_nav(settings_frame, "Settings", compact=True)

        self.toolbar = tk.Frame(self, bg="#ffffff", height=54, padx=18,
                                highlightthickness=1, highlightbackground=UI_BORDER)
        self.toolbar.pack(fill="x")
        self.page_title = tk.Label(self.toolbar, text="Overview", bg="#ffffff",
                                   fg=UI_TEXT, font=(app.title_family, 15, "bold"))
        self.page_title.pack(side="left", pady=12)
        self.context = tk.Label(self.toolbar, text="Competition command center",
                                bg="#ffffff", fg=UI_MUTED, font=(app.body_family, 9))
        self.context.pack(side="left", padx=12, pady=14)
        ttk.Button(self.toolbar, text="Save session", command=app.do_save).pack(side="right", pady=8)
        ttk.Button(self.toolbar, text="⌕  Quick switch  Ctrl+K",
                   command=self.quick_switch).pack(side="right", padx=8, pady=8)
        self.content = tk.Frame(self, bg="#ffffff", highlightthickness=1,
                                highlightbackground=UI_BORDER)
        self.content.pack(side="top", fill="both", expand=True, padx=14, pady=(10, 12))
        app.bind_all("<Control-k>", lambda _e: self.quick_switch())
        app.bind_all("<Control-K>", lambda _e: self.quick_switch())

    def _make_nav(self, parent, name, compact=False):
        text = f"{self.ICONS.get(name, '•')} {name}"
        label = tk.Label(parent, text=text, anchor="center", bg=UI_PAGE_BG,
                         fg=UI_MUTED, font=(self.app.body_family, 8 if compact else 9, "bold"),
                         padx=7 if compact else 6, pady=7, cursor="hand2",
                         highlightthickness=1, highlightbackground=UI_BORDER)
        label.pack(side="left", padx=2)
        self.nav_buttons[name] = label
        label.bind("<Button-1>", lambda _e, n=name: self.select_name(n))
        label.bind("<Enter>", lambda _e, n=name: self._nav_hover(n, True))
        label.bind("<Leave>", lambda _e, n=name: self._nav_hover(n, False))

    def _nav_hover(self, name, on):
        if name == self._current_name(): return
        label = self.nav_buttons[name]
        label.configure(bg=UI_TAB_HOVER_BG if on else UI_PAGE_BG)

    def _current_name(self):
        for idx, _frame, name in self._pages:
            if idx == self._current: return name
        return None

    def add(self, frame: tk.Widget, text: str = "", pinned: bool = False, step: int = 0) -> str:
        idx = str(len(self._pages))
        self._pages.append((idx, frame, text))
        self._tabs[idx] = frame
        frame.place(in_=self.content, x=0, y=0, relwidth=1, relheight=1)
        frame.lower()
        if self._current is None: self.select(idx)
        return idx

    def tabs(self): return [i for i, _, _ in self._pages]

    def index_of(self, frame):
        for i, f, _ in self._pages:
            if f is frame: return i
        return None

    def select_name(self, name):
        for idx, _frame, title in self._pages:
            if title == name:
                self.select(idx); return

    def select(self, idx: str) -> None:
        if idx not in self._tabs: return
        prev = self._current
        self._current = idx
        frame = self._tabs[idx]
        name = next((n for i, _f, n in self._pages if i == idx), "AutoScout")
        self.page_title.configure(text=name)
        contexts = {"Overview":"Competition command center", "Competition Day":"Live event operations and match desk",
                    "Setup":"Event and session setup", "Teams":"Your event team database",
                    "Team Research":"Public FTC team intelligence", "Matches":"Match schedule and predictions",
                    "My Team":"Your team's performance", "Standings":"Event standings",
                    "Alliances":"Alliance selection workspace", "Win Rate Curve":"Alliance win-rate modeling",
                    "Charts":"Performance visualizations", "Analysis":"Prediction quality and insights",
                    "Live Scouting":"Live match observation workspace", "Settings":"Preferences and data controls"}
        self.context.configure(text=contexts.get(name, "FTC scouting workspace"))
        for nav_name, label in self.nav_buttons.items():
            active = nav_name == name
            label.configure(bg=UI_ACCENT if active else UI_PAGE_BG,
                            fg="#ffffff" if active else UI_MUTED,
                            highlightbackground=UI_ACCENT if active else UI_BORDER)
        frame.lift()
        if prev != idx and hasattr(frame, "refresh"):
            try: frame.refresh()
            except Exception: pass

    def quick_switch(self):
        win = tk.Toplevel(self)
        win.title("Quick switch")
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        win.configure(bg="#ffffff")
        win.geometry("360x430")
        tk.Label(win, text="GO TO WORKSPACE", bg="#ffffff", fg=UI_MUTED,
                 font=(self.app.title_family, 8, "bold")).pack(anchor="w", padx=14, pady=(14, 5))
        query = tk.StringVar()
        entry = ttk.Entry(win, textvariable=query)
        entry.pack(fill="x", padx=12, pady=(0, 8)); entry.focus_set()
        lb = tk.Listbox(win, activestyle="none", relief="flat", highlightthickness=0,
                        font=(self.app.body_family, 10), selectbackground="#dbdce0", selectforeground=UI_TEXT)
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        names = [n for _, _, n in self._pages]
        def populate(*_):
            term = query.get().lower(); lb.delete(0, "end")
            for n in names:
                if term in n.lower(): lb.insert("end", n)
            if lb.size(): lb.selection_set(0)
        def go(_event=None):
            sel = lb.curselection()
            if sel:
                self.select_name(lb.get(sel[0])); win.destroy()
        query.trace_add("write", populate)
        lb.bind("<Double-Button-1>", go); lb.bind("<Return>", go)
        entry.bind("<Return>", lambda _e: (populate(), go()))
        populate(); win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        x = self.winfo_toplevel().winfo_rootx() + (self.winfo_toplevel().winfo_width()-win.winfo_width())//2
        y = self.winfo_toplevel().winfo_rooty() + 100
        win.geometry(f"+{x}+{y}")



def apply_modern_style(root: tk.Tk, title_family: str, body_family: str) -> None:
    root.configure(bg=UI_PAGE_BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=UI_APP_BG, foreground=UI_TEXT,
                     fieldbackground="#ffffff", borderwidth=1)
    style.configure("TFrame", background=UI_APP_BG)
    style.configure("TLabel", background=UI_APP_BG, foreground=UI_TEXT)
    style.configure("TCheckbutton", background=UI_APP_BG, foreground=UI_TEXT)
    style.configure("TRadiobutton", background=UI_APP_BG, foreground=UI_TEXT)
    style.configure("Collapsible.TCheckbutton", background=UI_APP_BG,
                     foreground=UI_NAVY, font=(title_family, 10, "bold"))

    style.configure("TLabelframe", background=UI_CARD_BG, bordercolor=UI_BORDER,
                     relief="flat", borderwidth=0)
    style.configure("TLabelframe.Label", background=UI_CARD_BG, foreground=UI_NAVY,
                     font=(title_family, 11, "bold"))
    style.configure("Card.TFrame", background=UI_CARD_BG)
    style.configure("Card.TLabel", background=UI_CARD_BG, foreground=UI_TEXT)

    style.configure("TEntry", fieldbackground="#ffffff", bordercolor=UI_BORDER,
                     lightcolor=UI_BORDER, darkcolor=UI_BORDER, padding=6,
                     selectbackground=UI_SELECT_BG, selectforeground=UI_SELECT_FG)
    style.map("TEntry", bordercolor=[("focus", UI_GOLD_DEEP)],
              selectbackground=[("!focus", UI_SELECT_INACTIVE_BG)],
              selectforeground=[("!focus", UI_SELECT_INACTIVE_FG)])

    style.configure("TButton", background="#eef1f6", foreground=UI_NAVY,
                     bordercolor=UI_BORDER, padding=(16, 8),
                     font=(body_family, 10, "bold"))
    style.map("TButton", background=[("active", "#e2e7f0")])

    style.configure("Accent.TButton", background=UI_ACCENT, foreground=UI_ACCENT_TEXT,
                     bordercolor=UI_ACCENT, padding=(18, 9),
                     font=(title_family, 10, "bold"))
    style.map("Accent.TButton",
              background=[("pressed", UI_ACCENT_PRESSED), ("active", UI_ACCENT_HOVER)],
              foreground=[("disabled", UI_NAV_MUTED)])

    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                     foreground=UI_TEXT, rowheight=26, bordercolor=UI_BORDER,
                     font=(body_family, 10))
    style.configure("Treeview.Heading", background=UI_NAVY, foreground="#ffffff",
                     relief="flat", font=(title_family, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", UI_NAVY_DARK)])
    style.map("Treeview", background=[("selected", UI_SELECT_BG)],
              foreground=[("selected", UI_SELECT_FG)])

    style.configure("TProgressbar", background=UI_GOLD_DEEP, troughcolor="#e9ecf1")
    style.configure("Horizontal.TScale", background=UI_APP_BG)

    # Drop-downs and spinboxes: white fields with the same border as entries.
    style.configure("TCombobox", fieldbackground="#ffffff", background="#eef1f6",
                     bordercolor=UI_BORDER, lightcolor=UI_BORDER, darkcolor=UI_BORDER,
                     arrowcolor=UI_NAVY, padding=6,
                     selectbackground=UI_SELECT_BG, selectforeground=UI_SELECT_FG)
    style.map("TCombobox", fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f3f4f7")],
              bordercolor=[("focus", UI_GOLD_DEEP)], lightcolor=[("focus", UI_GOLD_DEEP)],
              darkcolor=[("focus", UI_GOLD_DEEP)], foreground=[("disabled", UI_MUTED)],
              selectbackground=[("readonly", "#ffffff"), ("!focus", UI_SELECT_INACTIVE_BG)],
              selectforeground=[("readonly", UI_TEXT), ("!focus", UI_SELECT_INACTIVE_FG)])
    style.configure("TSpinbox", fieldbackground="#ffffff", bordercolor=UI_BORDER, arrowcolor=UI_NAVY,
                     selectbackground=UI_SELECT_BG, selectforeground=UI_SELECT_FG)
    style.map("TSpinbox", selectbackground=[("!focus", UI_SELECT_INACTIVE_BG)],
              selectforeground=[("!focus", UI_SELECT_INACTIVE_FG)])
    root.option_add("*TCombobox*Listbox.background", "#ffffff")
    root.option_add("*TCombobox*Listbox.selectBackground", UI_SELECT_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", UI_SELECT_FG)
    # Plain tk widgets (Text, Entry, Listbox) follow the same selection colours.
    root.option_add("*selectBackground", UI_SELECT_BG)
    root.option_add("*selectForeground", UI_SELECT_FG)
    root.option_add("*Text.inactiveSelectBackground", UI_SELECT_INACTIVE_BG)

    # The Charts tab's inner page strip: the same folder tabs as the main navigation -
    # inactive on the page grey, the active one lifted to white with navy text.
    style.configure("Charts.TNotebook", background=UI_PAGE_BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("Charts.TNotebook.Tab", background=UI_TAB_BG, foreground=UI_TAB_FG,
                     padding=(14, 8), borderwidth=0, font=(title_family, 10, "bold"))
    style.map("Charts.TNotebook.Tab",
              background=[("selected", UI_TAB_ACTIVE_BG), ("active", UI_TAB_HOVER_BG)],
              foreground=[("selected", UI_TAB_ACTIVE_FG)],
              expand=[("selected", (0, 2, 0, 0))])
    style.layout("Charts.TNotebook.Tab", [("Notebook.tab", {"sticky": "nswe", "children": [
        ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
            ("Notebook.label", {"side": "top", "sticky": ""})]})]})])


def style_text_selection(text: tk.Text) -> None:
    """Selected text in a tk.Text box: main-colour background, white bold text."""
    import tkinter.font as _tkfont
    bold = _tkfont.Font(text, font=text.cget("font"))
    bold.configure(weight="bold")
    text._selection_font = bold  # keep a reference so Tk does not free it
    text.tag_configure("sel", background=UI_SELECT_BG, foreground=UI_SELECT_FG, font=bold)


def stripe_treeview_rows(tree: ttk.Treeview) -> None:

    tree.tag_configure("oddrow", background=UI_ROW_ALT_BG)
    tree.tag_configure("evenrow", background="#ffffff")
    semantic = {"ok", "bad"}
    for i, iid in enumerate(tree.get_children("")):
        tags = list(tree.item(iid, "tags"))
        if semantic.intersection(tags):
            continue
        tags = [t for t in tags if t not in ("oddrow", "evenrow")]
        tags.append("oddrow" if i % 2 else "evenrow")
        tree.item(iid, tags=tags)


class App(tk.Tk):
    """One window, one Session. Tabs read the session; edits go through `recompute`."""

    def __init__(self, open_path: Optional[str] = None, autostart: bool = True):
        super().__init__()
        self.title(f"{APP_FULL_TITLE} {__version__} - {APP_TAGLINE}")
        try:
            # kept as attributes so they aren't garbage-collected
            self._icon_imgs = [logo_image(s) for s in (16, 32, 48, 56)]
            self.iconphoto(True, *self._icon_imgs)
        except tk.TclError:
            pass
        self.session = Session()
        self._client: Optional[FTCScoutClient] = None
        self._offline = bool(config.get("offline", False))
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._pump_id: Optional[str] = None

        import tkinter.font as tkfont
        base = tkfont.nametofont("TkDefaultFont")

        # Typography: Roboto for titles/headings, IBM Plex Mono for body text.
        # Both fall back to the platform default if not installed.
        title_family = resolve_font_family(FONT_TITLE_CANDIDATES, base.cget("family"))
        body_family = resolve_font_family(FONT_BODY_CANDIDATES, base.cget("family"))
        self.title_family = title_family
        self.body_family = body_family

        # Re-point Tk's own named fonts at the body font so every default-styled
        # widget (labels, entries, buttons, treeviews, menus...) picks it up
        # without having to touch each widget individually.
        for fname in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                      "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont",
                      "TkFixedFont"):
            try:
                tkfont.nametofont(fname).configure(family=body_family)
            except tk.TclError:
                pass

        self.font_bold = tkfont.Font(family=title_family, size=base.cget("size"), weight="bold")
        self.font_title = tkfont.Font(family=title_family, size=base.cget("size") + 4, weight="bold")
        self.font_title_italic = tkfont.Font(family=title_family, size=base.cget("size") + 4,
                                              weight="bold", slant="italic")
        self.font_big = tkfont.Font(family=title_family, size=base.cget("size") + 6, weight="bold")
        self.font_tagline = tkfont.Font(family=body_family, size=base.cget("size"), weight="bold")

        # Section/tab headings ("titles" in this app's own vocabulary - see
        # the Tab.title attribute) also get the title font.
        apply_modern_style(self, title_family, body_family)

        geom = config.get("geometry", "")
        self.geometry(geom if geom else "1360x900")
        self.minsize(1120, 720)

        self._build_header()

        self.nb = NavShell(self, self)
        self.nb.pack(fill="both", expand=True)
        self.home_tab = HomeTab(self)
        self.competition_tab = CompetitionDayTab(self)
        self.setup_tab = SetupTab(self)
        self.teams_tab = TeamsTab(self)
        self.matches_tab = MatchesTab(self)
        self.myteam_tab = MyTeamTab(self)
        self.standings_tab = StandingsTab(self)
        self.alliances_tab = AlliancesTab(self)
        self.charts_tab = ChartsTab(self)
        self.winrate_tab = AllianceWinRateTab(self)
        self.analysis_tab = AnalysisTab(self)
        self.live_tab = LiveScoutingTab(self)
        self.research_tab = TeamResearchTab(self)
        self.settings_tab = SettingsTab(self)
        self.tabs: list[Tab] = [self.home_tab, self.competition_tab, self.setup_tab, self.teams_tab, self.research_tab, self.matches_tab, self.myteam_tab,
                                self.standings_tab, self.alliances_tab, self.charts_tab,
                                self.winrate_tab, self.analysis_tab, self.live_tab, self.settings_tab]
        # The first seven are the guided, in-order workflow (numbered so a new
        # user always knows what's next); Settings is a utility page, pinned
        # to the bottom and unnumbered like Windows' own Settings nav.
        for step, tab in enumerate(self.tabs[:-1], start=1):
            self.nb.add(tab, text=tab.title, step=step)
        self.nb.add(self.settings_tab, text=self.settings_tab.title, pinned=True)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, relief="flat", anchor="w",
                  background=UI_APP_BG, foreground=UI_MUTED,
                  padding=(10, 4)).pack(fill="x", side="bottom")

        self._build_menu()
        self.protocol("WM_DELETE_WINDOW", self.do_quit)
        self.refresh_all()
        self._pump_id = self.after(PUMP_MS, self._pump)
        if autostart:
            self.after(120, lambda: self._autostart(open_path))

    def _build_header(self) -> None:
     
        header = tk.Frame(self, bg=THEME_BG)
        header.pack(fill="x", side="top")

        logo = tk.Canvas(header, width=56, height=56, highlightthickness=0, bg=THEME_BG)
        logo.pack(side="left", padx=(14, 10), pady=6)
        paint_logo(logo, size=56)

        text_col = tk.Frame(header, bg=THEME_BG)
        text_col.pack(side="left", pady=8)

        title_row = tk.Frame(text_col, bg=THEME_BG)
        title_row.pack(anchor="w")
        tk.Label(title_row, text=f"{APP_TITLE_PREFIX} ", font=self.font_title,
                 fg=THEME_LIGHT_TEXT, bg=THEME_BG).pack(side="left")
        tk.Label(title_row, text=APP_NAME, font=self.font_title_italic,
                 fg=THEME_LIGHT_TEXT, bg=THEME_BG).pack(side="left")

        tk.Label(text_col, text=APP_TAGLINE, font=self.font_tagline,
                 fg=THEME_ACCENT_TEXT, bg=THEME_BG).pack(anchor="w")

        credit = tk.Label(header, text=APP_ORG_FULL,
                           fg=THEME_ACCENT_TEXT, bg=THEME_BG, cursor="hand2")
        credit.pack(side="right", padx=14)
        credit.bind("<Button-1>", lambda _e: self._open_website())

    def _open_website(self) -> None:
        import webbrowser
        webbrowser.open(APP_WEBSITE)

    def _build_menu(self) -> None:
        bar = tk.Menu(self)
        f = tk.Menu(bar, tearoff=0)
        f.add_command(label="New", command=self.do_new)
        f.add_command(label="Open…", command=self.do_open)
        f.add_command(label="Save", command=self.do_save)
        f.add_command(label="Save As…", command=self.do_save_as)
        f.add_separator()
        f.add_command(label="Export CSV…", command=self.do_export_csv)
        f.add_command(label="Export printable HTML…", command=self.do_export_html)
        f.add_separator()
        f.add_command(label="Quit", command=self.do_quit)
        bar.add_cascade(label="File", menu=f)

        d = tk.Menu(bar, tearoff=0)
        d.add_command(label="Load event data", command=self.setup_tab.do_load)
        d.add_command(label="Refresh results", command=self.matches_tab.do_refresh)
        bar.add_cascade(label="Data", menu=d)

        h = tk.Menu(bar, tearoff=0)
        h.add_command(label="About", command=self.show_about)
        h.add_command(label="What is CAP?", command=self.show_cap_help)
        h.add_command(label="What do the charts show?", command=self.show_charts_help)
        h.add_command(label="Charts guide (detailed)", command=self.show_charts_guide)
        h.add_separator()
        h.add_command(label="Visit the AutoScout website", command=self._open_website)
        bar.add_cascade(label="Help", menu=h)
        self.configure(menu=bar)

    def set_session(self, session: Session) -> None:
        self.session = session
        self.refresh_all()

    def recompute(self) -> None:
        """Re-run the whole calculation chain, then repaint every tab."""
        self.session.compute()
        self.refresh_all()

    def refresh_all(self) -> None:
        for tab in self.tabs:
            tab.refresh()
        s = self.session
        name = s.event_name if s.loaded or s.event_code else "no meet loaded"
        self.title(f"{APP_FULL_TITLE} {__version__} - {name}" + (f" - {os.path.basename(s.path)}" if s.path else ""))
        c, d = s.accuracy()
        self.status.set(f"{len(s.teams)} teams · {len(s.matches)} matches · accuracy {c}/{d}"
                        + (f" = {c / d:.0%}" if d else "") + f" · by {s.settings.basis}"
                        + (f" · my team {s.my_team}" if s.my_team else ""))

    def _autostart(self, open_path: Optional[str]) -> None:
        if open_path:
            self.open_path(open_path)
            return
        last = config.get("last_session", "")
        if last and os.path.exists(last) and messagebox.askyesno(
                APP_NAME, f"Reopen the last session?\n\n{last}", parent=self):
            self.open_path(last)

    def open_path(self, path: str) -> None:
        try:
            self.set_session(Session.open(path))
        except (OSError, ValueError, KeyError, TypeError) as e:
            messagebox.showerror(APP_NAME, f"Could not open that session file.\n\n{e}", parent=self)
            return
        config.save({"last_session": path, "last_dir": os.path.dirname(path)})
        self.status.set(f"Opened {path}")

    def do_new(self) -> None:
        if messagebox.askyesno(APP_NAME, "Start a new, empty session? Unsaved changes are lost.", parent=self):
            self.matches_tab.stop()
            self.set_session(Session())

    def do_open(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Open session", initialdir=config.get("last_dir", "") or None,
            filetypes=[("AutoScout session", "*.json"), ("All files", "*.*")])
        if path:
            self.open_path(path)

    def do_save(self) -> None:
        if not self.session.path:
            self.do_save_as()
            return
        self._save_to(self.session.path)

    def do_save_as(self) -> None:
        s = self.session
        default = os.path.basename(s.path) or f"autoscout_{s.season}_{s.event_code or 'session'}.json"
        path = filedialog.asksaveasfilename(
            parent=self, title="Save session", defaultextension=".json", initialfile=default,
            initialdir=config.get("last_dir", "") or None,
            filetypes=[("AutoScout session", "*.json")])
        if path:
            self._save_to(path)

    def _save_to(self, path: str) -> None:
        self.setup_tab.pull_into_session()
        try:
            self.session.save(path)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not save.\n\n{e}", parent=self)
            return
        config.save({"last_session": path, "last_dir": os.path.dirname(path)})
        self.status.set(f"Saved {path}")
        self.refresh_all()

    def _need_export(self) -> bool:
        if export_csv is None or export_html is None or printable_text is None:
            messagebox.showinfo(APP_NAME, "Export module not available in this build.", parent=self)
            return False
        return True

    def do_export_csv(self) -> None:
        if not self._need_export():
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export CSV", defaultextension=".csv",
            initialdir=config.get("last_dir", "") or None,
            initialfile=f"autoscout_{self.session.event_code or 'meet'}.csv",
            filetypes=[("CSV", "*.csv")])
        if path:
            self._run_export(export_csv, path)

    def do_export_html(self) -> None:
        if not self._need_export():
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export printable HTML", defaultextension=".html",
            initialdir=config.get("last_dir", "") or None,
            initialfile=f"autoscout_{self.session.event_code or 'meet'}.html",
            filetypes=[("HTML", "*.html")])
        if path:
            self._run_export(export_html, path)

    def _run_export(self, fn: Callable[[Session, str], Any], path: str) -> None:
        try:
            fn(self.session, path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Export failed.\n\n{e}", parent=self)
            return
        self.status.set(f"Exported {path}")

    def do_copy_text(self) -> None:
        if printable_text is None:
            messagebox.showinfo(APP_NAME, "Export module not available in this build.", parent=self)
            return
        try:
            text = printable_text(self.session)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not build the printable text.\n\n{e}", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.set("Printable summary copied to the clipboard.")

    def show_about(self) -> None:
        messagebox.showinfo(f"About {APP_FULL_TITLE}", (
            f"{APP_FULL_TITLE} {__version__} - {APP_TAGLINE}\n\n"
            f"{APP_BLURB}\n\n"
            "A port of the AutoScout v1.3 spreadsheets. Data from the public "
            "FTCScout REST API.\n\n"
            f"{APP_ORG_FULL}\n"
            f"{APP_WEBSITE}\n\n"
            f"Cache: {default_cache_dir()}"), parent=self)

    def show_cap_help(self) -> None:
        messagebox.showinfo("CAP, OPR and EPA", CAP_EXPLAINER + "\n" + RATING_EXPLAINER, parent=self)

    def show_charts_help(self) -> None:
        messagebox.showinfo("Charts", CHARTS_EXPLAINER, parent=self)

    def show_charts_guide(self) -> None:
        """The detailed Charts guide in its own scrollable window (too long for a messagebox)."""
        win = tk.Toplevel(self)
        win.title("How to use the Charts tab")
        win.geometry("880x720")
        win.configure(bg=UI_APP_BG)
        head = tk.Frame(win, bg=THEME_BG)
        head.pack(fill="x")
        tk.Label(head, text="How to use the Charts tab", font=self.font_title, fg=THEME_LIGHT_TEXT,
                 bg=THEME_BG, padx=14, pady=10).pack(side="left")
        body = ttk.Frame(win, padding=8)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", background="#ffffff", foreground=UI_TEXT, relief="flat",
                       padx=12, pady=10, font=(self.body_family, 10), highlightthickness=0)
        style_text_selection(text)
        sb = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        text.tag_configure("h1", font=(self.title_family, 13, "bold"), foreground=UI_NAVY, spacing1=10, spacing3=4)
        text.tag_configure("h2", font=(self.title_family, 11, "bold"), foreground=UI_NAVY, spacing1=8, spacing3=2)
        for line in CHARTS_GUIDE.strip("\n").splitlines():
            stripped = line.strip()
            if stripped and stripped == stripped.upper() and any(c.isalpha() for c in stripped) and not line.startswith(" "):
                text.insert("end", line + "\n", "h1" if line.startswith("HOW") or line.startswith("PAGE") else "h2")
            elif not line.startswith(" ") and stripped in (
                    "Scatter", "Compare ratings (where they converge)", "Matches on the schedule",
                    "All possible alliances", "Partners for my team",
                    "Edit this team's data inputs (blank = value from FTCScout)"):
                text.insert("end", line + "\n", "h2")
            else:
                text.insert("end", line + "\n")
        text.configure(state="disabled")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))

    def select_team(self, team: int, source: Any = None) -> None:
        """Show one team everywhere a single team is shown: the Teams row and its side
        menu, and the Charts tab's "One team" page. A click on a chart point lands here."""
        team = int(team or 0)
        if not team or team not in self.session.teams:
            return
        self.teams_tab.tree.select(str(team))
        if source is not self.charts_tab.team_panel:
            self.charts_tab.team_panel.set_team(team)
        if source is self.charts_tab:                   # a click on a chart: open the team
            self.teams_tab.toggle_panel(True)
            idx = self.nb.index_of(self.teams_tab)
            if idx is not None:
                self.nb.select(idx)
        if self.teams_tab._panel_added:
            self.teams_tab.panel.set_team(team)

    def select_match(self, label: str) -> None:
        """Jump to one match on the Matches tab (a click on a match point)."""
        self.matches_tab.tree.select(str(label))
        idx = self.nb.index_of(self.matches_tab)
        if idx is not None:
            self.nb.select(idx)


    def make_client(self, offline: Optional[bool] = None) -> FTCScoutClient:
        if offline is not None:
            self._offline = bool(offline)
        if self._client is None or self._client.offline != self._offline:
            self._client = FTCScoutClient(offline=self._offline)
        return self._client

    def post(self, fn: Callable[[], None]) -> None:
        """Ask for `fn` to run on the Tk thread. Safe to call from a worker.

        A plain `after()` would already be a Tk call, and Tk's C layer only
        tolerates that while the main thread sits in `mainloop`. A queue plus a
        main-thread pump keeps every Tk call on the one thread that owns them.
        """
        self._queue.put(fn)

    def _pump(self) -> None:
        self._pump_id = None
        try:
            while True:
                try:
                    fn = self._queue.get_nowait()
                except queue.Empty:
                    break
                fn()
        finally:
            try:
                self._pump_id = self.after(PUMP_MS, self._pump)
            except tk.TclError:                       # window already destroyed
                pass

    def run_async(self, work: Callable[[], Any], done: Callable[[Any], None],
                  failed: Callable[[ApiError], None]) -> None:
        """Run `work` off the Tk thread; `done`/`failed` come back on the Tk thread."""
        def runner() -> None:
            try:
                result = work()
            except ApiError as e:
                self.post(lambda e=e: failed(e))
            except Exception as e:                    # never leak a traceback into the UI
                self.post(lambda e=e: failed(ApiError(f"Unexpected error: {e}")))
            else:
                self.post(lambda r=result: done(r))

        threading.Thread(target=runner, daemon=True).start()

    def show_api_error(self, err: ApiError) -> None:
        messagebox.showerror(APP_NAME, err.user_message, parent=self)


    def do_quit(self) -> None:
        self.matches_tab.stop()
        if self._pump_id is not None:
            try:
                self.after_cancel(self._pump_id)
            except tk.TclError:                        # pragma: no cover
                pass
            self._pump_id = None
        try:
            config.save({"geometry": self.winfo_geometry()})
        except tk.TclError:                            # pragma: no cover
            pass
        self.destroy()


def run(open_path: str | None = None) -> int:
    """Start the GUI. Returns a process exit code (cli.py returns it directly)."""
    try:
        app = App(open_path)
    except tk.TclError as e:
        import sys
        # A --windowed build has no console: sys.stderr can be None.
        if sys.stderr is not None:
            print(f"AutoScout could not open a window: {e}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


def smoke_test(fixture_dir: str) -> None:
    """Build the window, load a fixture, paint every tab, and shut down.

    Used by CI and by hand: it exercises each tab's refresh path plus a manual
    edit on Teams and a manual result on Matches, without any network access.
    """
    app = App(autostart=False)
    if fixture_dir.endswith(".json"):
        app.set_session(Session.open(fixture_dir))
    else:
        app.set_session(load_fixture_session(fixture_dir, 12928))
    for _ in range(3):
        app.update()
    for tab_id in app.nb.tabs():
        app.nb.select(tab_id)
        app.update()

    # exercise the editing paths
    first = app.session.teams_by_cap()[0].number
    app.teams_tab.tree.select(str(first))
    app.update()
    app.teams_tab.tdc_var.set("CAMERA")
    app.teams_tab.delta_var.set("-5")
    app.teams_tab.focus_var.set("award")
    app.teams_tab.apply_edit()
    app.update()

    label = app.session.matches[0].label
    app.session.manual_results[label] = (99.0, 101.0)
    app.recompute()
    app.update()

    app.settings_tab.faithful_var.set(True)
    app.settings_tab.apply()
    app.settings_tab.poa_var.set(True)
    app.settings_tab.apply()
    app.update()
    for rating in RATINGS:                       # every prediction basis paints every tab
        app.settings_tab.rating_var.set(rating)
        app.settings_tab.apply()
        for tab_id in app.nb.tabs():
            app.nb.select(tab_id)
            app.update()

    # 2.2: the Teams side menu, data-input overrides, and every Charts page
    app.teams_tab.toggle_panel(True)
    app.teams_tab.tree.select(str(first))
    app.update()
    panel = app.teams_tab.panel
    assert panel.team == first, (panel.team, first)
    panel.override_vars["opr"].set("150")
    panel.override_vars["epa"].set("140")
    panel.apply_overrides()
    app.update()
    t = app.session.teams[first]
    assert t.opr == 150.0 and t.epa == 140.0 and t.edited, (t.opr, t.epa, t.edited)
    for domain in TeamChartPanel.DOMAINS:
        panel.domain_var.set(domain)
        panel._domain_changed()
        app.update()
    panel.x_var.set("Event date")
    panel.refresh()
    app.update()
    panel.reset_overrides()
    app.update()
    assert not app.session.teams[first].edited
    app.teams_tab.toggle_panel(False)
    app.update()

    ct = app.charts_tab
    app.nb.select(app.nb.index_of(ct))
    for page in ct.nb.tabs():
        ct.nb.select(page)
        app.update()
    ct.team_panel.set_team(first)
    app.update()
    ct.view_var.set("Compare ratings (where they converge)")
    ct._view_changed()
    app.update()
    assert ct.all_chart._screen, "compare view drew nothing"
    ct.view_var.set("Scatter")
    ct._view_changed()
    ct.az_var.set("EPA")
    ct.refresh()
    app.update()
    assert ct.all_chart._screen, "scatter drew nothing"
    for unit in PAIR_UNITS:
        ct.unit_var.set(unit)
        ct._unit_changed()
        app.update()
        assert ct.pair_chart._screen or not app.session.my_team, unit
    app.select_team(first, source=ct)
    app.update()
    assert app.teams_tab._panel_added
    app.select_match(app.session.matches[0].label)
    app.update()

    app.matches_tab.stop()
    if app._pump_id is not None:
        app.after_cancel(app._pump_id)
    app.destroy()

"""Command-line interface. `autoscout --help`.

Examples:
  autoscout load  --season 2025 --event FTCCMP1JACK --team 12928 --save meet.json
  autoscout predict --open meet.json
  autoscout myteam  --open meet.json
  autoscout standings --open meet.json --refresh
  autoscout alliances --open meet.json --team 12928
  autoscout predict --fixture tests/fixtures/FTCCMP1JACK_2025
"""

import argparse
import sys



def _apply_rating(a, settings: Settings) -> Settings:
    if getattr(a, "rating", None):
        settings.rating = a.rating
    if getattr(a, "epa_frozen", False):
        settings.epa_live = False
    return settings


def _client(a) -> FTCScoutClient:
    return FTCScoutClient(cache_dir=a.cache, offline=a.offline, max_age=0 if a.fresh else 300)


def _session(a) -> Session:
    if a.fixture:
        return load_fixture_session(a.fixture, a.team or 12928, _apply_rating(a, Settings(faithful=a.faithful)))
    if a.open:
        s = Session.open(a.open)
        if a.team:
            s.my_team = a.team
        if a.faithful:
            s.settings.faithful = True
        _apply_rating(a, s.settings)
        if a.faithful or a.rating or a.epa_frozen:
            s.compute()
        return s
    if not a.event:
        sys.exit("Give --event CODE (and --season), --open FILE, or --fixture DIR.")
    s = Session(season=a.season, event_code=a.event, my_team=a.team or 0, settings=_apply_rating(a, Settings(faithful=a.faithful)))
    s.load(_client(a), progress=lambda m, f: print(f"\r  {f:5.0%} {m:<40s}", end="", file=sys.stderr),
           with_history=not a.no_history)
    print(file=sys.stderr)
    return s


def fmt_team(t) -> str:
    return f"{t.number:6d} {t.name[:22]:22s} OPR {t.opr:6.1f} auto {t.auto:5.1f} tele {t.teleop:6.1f} eg {t.endgame:5.1f} " \
           f"projOPR {('%6.1f' % t.proj_opr) if t.proj_opr is not None else '   n/a'} CAP {t.cap:6.1f}" + \
           (f" EPA {t.epa:6.1f}" if t.epa is not None else "") + \
           (f"  [{t.tdc_issue}]" if t.tdc_issue else "")


def cmd_teams(s: Session, a):
    print(f"{s.event_name} ({s.season}/{s.event_code}) - {len(s.teams)} teams, ranked by {s.settings.basis}")
    for i, t in enumerate(s.teams_by_strength(), 1):
        print(f"{i:3d}. {fmt_team(t)}")


def cmd_predict(s: Session, a):
    c, d = s.accuracy()
    print(f"{s.event_name} - {len(s.predictions)} matches, predictions by {s.settings.basis}; accuracy so far {c}/{d}" + (f" = {c/d:.0%}" if d else ""))
    for p in s.predictions:
        m = p.match
        act = f"{m.winner:4s} {m.red_score:.0f}-{m.blue_score:.0f}" if m.winner else "-"
        ok = "" if p.correct is None else ("✔" if p.correct else "✘")
        print(f"{m.label:6s} R {m.red[0]:5d} {m.red[1] if len(m.red) > 1 else '':5} vs B {m.blue[0]:5d} {m.blue[1] if len(m.blue) > 1 else '':5}"
              f"  pred. {p.winner:4s} {p.red_pts:6.1f}-{p.blue_pts:6.1f} Win chance {p.red_cow:4.0%}/{p.blue_cow:4.0%}  actual {act} {ok}")


def cmd_standings(s: Session, a):
    print(f"{s.event_name} - projected standings (RP, then AUTO, then ENDGAME)")
    for r in s.table:
        cap = "  ← captain" if r.captain_rank else ""
        print(f"{r.rank:3d}. {r.number:6d} {r.name[:22]:22s} {r.wins}-{r.losses} of {r.scheduled}  RP {r.rp:.2f}  TBP1 {r.tbp1:6.1f} TBP2 {r.tbp2:5.1f}{cap}")


def cmd_myteam(s: Session, a):
    if not s.my_team:
        sys.exit("Give --team NUMBER.")
    info = s.my_summary()
    t = info["stats"]
    if t is None:
        sys.exit(f"Team {s.my_team} is not at this event.")
    print(f"Team {t.number} {t.name}  - {s.event_name}")
    print(f"  OPR {t.opr:.2f}  Auto {t.auto:.2f}  Teleop {t.teleop:.2f}  Endgame {t.endgame:.2f}")
    pct = t.cap_change_pct
    print(f"  CAP {t.cap:.2f}  ({abs(pct):.1%} {'DECREASE' if pct and pct < 0 else 'INCREASE'} from OPR)" if pct is not None else f"  CAP {t.cap:.2f}")
    if t.epa is not None:
        print(f"  EPA {t.epa:.2f}  (learned from {t.epa_matches} matches)")
    print(f"  projected rank {info['rank']}  predicted wins {info['wins']}/{len(info['matches'])}  old RP {info['old_rp']}")
    for x in info["matches"]:
        m = x.prediction.match
        print(f"  {m.label:6s} {x.color:4s} with {x.partner} vs {x.opponents}  Win chance {x.cow:4.0%}  MR {x.rating}  "
              f"pred {'Win' if x.predicted_win else 'Loss'}  actual {x.actual or '-'}")


def cmd_alliances(s: Session, a):
    rep = s.alliance_report()
    lab = s.settings.basis
    print(f"{s.event_name} - {len(s.teams)} teams, {len(rep.table)} possible alliances (by {lab}); "
          f"{len(rep.unbeatable)} beat every alliance they could face")
    if rep.bar is not None:
        print(f"Team {rep.my_team}: rank {rep.my_rank}, {lab} {rep.my_cap:.2f} - "
              f"pick must be above {rep.bar:.2f}")
    if rep.seats:
        print("  alliance captains")
        for st in rep.seats:
            print(f"  {st.rank:3d}. {st.team:6d} {st.name[:22]:22s} {lab} {st.cap:6.1f}  "
                  f"best avail. {st.best_available:6.1f}  likely pick {st.pick or 0:6d} "
                  f"({st.pick_cap:5.1f})  total {st.projected_total:6.1f}  pick above {st.bar:6.1f}")
    print("  best alliances")
    for x in rep.table[:PRINTED_ALLIANCES]:
        flag = "  ← unbeatable" if x.unbeatable else ""
        print(f"  {x.rank:3d}. {x.label:15s} {lab} {x.cap_sum:6.1f}  beats {x.beats:4d}/{x.legal:<4d} "
              f"{x.win_rate:6.1%}  closest {' + '.join(str(n) for n in x.closest):15s} "
              f"{x.margin:+6.1f}  Win chance {x.cow:4.0%}{flag}")
    if len(rep.table) > PRINTED_ALLIANCES:
        print(f"  … and {len(rep.table) - PRINTED_ALLIANCES} more")
    if rep.partners:
        print(f"  teams {rep.my_team} could win with")
        for x in rep.partners:
            flag = "  ← unbeatable" if x.unbeatable else ""
            print(f"  {x.partner_of(rep.my_team):6d} {x.partner_name(rep.my_team)[:22]:22s} "
                  f"their {lab} {x.partner_cap(rep.my_team):6.1f}  total {x.cap_sum:6.1f}  "
                  f"beats {x.beats:4d}/{x.legal:<4d} {x.win_rate:6.1%}  Win chance {x.cow:4.0%}{flag}")


def cmd_load(s: Session, a):
    path = s.save(a.save)
    print(f"Saved {path}: {len(s.teams)} teams, {len(s.matches)} matches")
    for w in s.warnings:
        print("  warning:", w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="autoscout",
        description=f"{APP_FULL_TITLE} {__version__} - {APP_TAGLINE}. FTC match predictions. {APP_WEBSITE}")
    ap.add_argument("command", choices=["load", "teams", "predict", "standings", "myteam", "alliances", "gui"],
                    nargs="?", default="gui")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--event", help="FTCScout/FIRST event code, e.g. FTCCMP1JACK")
    ap.add_argument("--team", type=int, help="your team number")
    ap.add_argument("--open", help="session .json to open")
    ap.add_argument("--save", help="where to save the session .json")
    ap.add_argument("--fixture", help="directory with event.json/teams.json/matches.json (offline demo)")
    ap.add_argument("--refresh", action="store_true", help="re-fetch match results before printing")
    ap.add_argument("--fresh", action="store_true", help="ignore the cache age (force re-download)")
    ap.add_argument("--offline", action="store_true", help="never touch the network")
    ap.add_argument("--no-history", action="store_true", help="skip per-team season history (faster; no projected OPR)")
    ap.add_argument("--faithful", action="store_true", help="sheet-faithful mode (reproduce spreadsheet quirks)")
    ap.add_argument("--rating", type=str.upper, choices=list(RATINGS),
                    help="what predictions are based on: CAP (default), OPR or EPA")
    ap.add_argument("--epa-frozen", action="store_true",
                    help="EPA stays as it was at the start of the event instead of learning from played matches")
    ap.add_argument("--cache", help="cache directory")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    if a.command == "gui":
        pass
        return run(a.open)
    try:
        s = _session(a)
        if a.refresh and not a.fixture:
            s.refresh_results(_client(a))
            if a.open:
                s.save()
        {"load": cmd_load, "teams": cmd_teams, "predict": cmd_predict,
         "standings": cmd_standings, "myteam": cmd_myteam,
         "alliances": cmd_alliances}[a.command](s, a)
    except ApiError as e:
        print("error:", e.user_message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
