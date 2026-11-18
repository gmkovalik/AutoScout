"""Synthetic backtest: how well do CAP, OPR, EPA and XPR forecast a simulated FTC event?

Simulates seasons of teams with hidden true strengths (that grow from event to event),
noisy alliance scores and occasional breakdowns: four earlier events, then a "current"
event. AutoScout's own engine then predicts the current event under each rating.

  fair       each rating only knows what was known before the match (real forecasting)
  hindsight  OPR/CAP built from the current event's own results - what the app shows on a
             finished event's Analysis tab. Not a forecast; shown to explain the gap.

Run from the repository root:   python3 tools/rating_simulation.py 200
Variants:  MODE=nogrowth (teams don't improve)   MODE=synergy (strong pairs score extra)
This is a simulation, not real FTC data: it tests the methods, not the real world.
"""
import sys, random, math, statistics as st, time
import os as _os
_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
sys.path.insert(0, _os.path.join(_root, "py")); sys.path.insert(0, _root)
sys.dont_write_bytecode = True
import tkstub; tkstub.install()
import AutoScout as A
import xpr; xpr.install(A)
import os; MODE = os.environ.get("MODE", "")

def opr_fit(matches, teams):
    # least squares alliance score = sum(team), tiny ridge for stability
    idx = {t: i for i, t in enumerate(teams)}; n = len(teams)
    M = [[0.0] * n for _ in range(n)]; b = [0.0] * n
    for red, blue, rs, bs in matches:
        for al, y in ((red, rs), (blue, bs)):
            for i in al:
                b[idx[i]] += y
                for j in al: M[idx[i]][idx[j]] += 1
    for i in range(n): M[i][i] += 1e-3
    # gaussian elimination
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c])); M[c], M[p] = M[p], M[c]; b[c], b[p] = b[p], b[c]
        for r in range(c + 1, n):
            f = M[r][c] / M[c][c]
            if f:
                for k in range(c, n): M[r][k] -= f * M[c][k]
                b[r] -= f * b[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (b[r] - sum(M[r][k] * x[k] for k in range(r + 1, n))) / M[r][r]
    return {t: x[idx[t]] for t in teams}

def schedule(teams, per_team, rng):
    out, pool = [], []
    need = len(teams) * per_team // 4
    for _ in range(need):
        if len(pool) < 4: pool += rng.sample(teams, len(teams))
        four = []
        while len(four) < 4:
            t = pool.pop()
            if t not in four: four.append(t)
        out.append((tuple(four[:2]), tuple(four[2:])))
    return out

def play(red, blue, ability, rng):
    def side(al):
        s = 0.0
        for t in al:
            a = ability[t]
            if rng.random() < 0.07: a *= rng.uniform(0.0, 0.4)      # breakdown / disconnect
            s += a
        if MODE == "synergy":                     # two strong robots together score more than the sum
            a1, a2 = ability[al[0]], ability[al[1]]
            s += 0.006 * a1 * a2
        return max(0.0, s + rng.gauss(0, 12 + 0.12 * s))
    return round(side(red)), round(side(blue))

def season(seed):
    rng = random.Random(seed)
    teams = list(range(1001, 1001 + 70))
    base = {t: max(8.0, rng.lognormvariate(math.log(45), 0.45)) for t in teams}
    growth = {t: (rng.gauss(5, 6) if MODE != "nogrowth" else 0.0) for t in teams}                       # points gained per event
    events = [f"E{i}" for i in range(4)]
    history = {t: [] for t in teams}
    epa_events = []
    attended = {t: 0 for t in teams}
    for ei, code in enumerate(events):
        at = rng.sample(teams, 24)
        ab = {t: max(3.0, base[t] + growth[t] * attended[t] + rng.gauss(0, 4)) for t in at}
        sched = schedule(at, 6, rng)
        played = [(r, b, *play(r, b, ab, rng)) for r, b in sched]
        opr = opr_fit(played, at)
        start = f"2026-0{1 + ei // 2}-{10 + (ei % 2) * 14}"
        for t in at:
            o = max(0.0, opr[t])
            history[t].append(A.EventStat(team=t, event_code=code, start_date=start, opr_np=o, opr_total=o,
                                          auto=0.3 * o, dc=0.7 * o, endgame=0.2 * o, tb1=0.3 * o, tb2=0.2 * o, qual_played=6))
            attended[t] += 1
        ms = []
        for k, (r, b, rs, bs) in enumerate(played, 1):
            ms.append(A.Match(label=f"Q-{k}", red=r, blue=b, number=k, played=True, red_score=rs, blue_score=bs, red_score_np=rs, blue_score_np=bs))
        epa_events.append(A.EpaEvent(code=code, start=start, matches=ms))
    cur = rng.sample(teams, 32)
    ab = {t: max(3.0, base[t] + growth[t] * attended[t] + rng.gauss(0, 4)) for t in cur}
    sched = schedule(cur, 6, rng)
    cur_matches, cur_played = [], []
    for k, (r, b) in enumerate(sched, 1):
        rs, bs = play(r, b, ab, rng)
        cur_played.append((r, b, rs, bs))
        cur_matches.append(A.Match(label=f"Q-{k}", red=r, blue=b, number=k, played=True, red_score=rs, blue_score=bs, red_score_np=rs, blue_score_np=bs))
    cur_opr = opr_fit(cur_played, cur)
    return cur, history, epa_events, cur_matches, cur_opr

def evaluate(sess):
    c = d = 0; brier = 0.0; mae = 0.0; n = 0
    for p in sess.predictions:
        m = p.match
        if m.red_score == m.blue_score: continue
        red_won = m.red_score > m.blue_score
        d += 1; c += int((p.red_pts > p.blue_pts) == red_won)
        brier += (p.red_cow - (1.0 if red_won else 0.0)) ** 2
        mae += abs((m.red_score - m.blue_score) - (p.red_pts - p.blue_pts)); n += 1
    return c, d, brier, mae, n

def run(seasons):
    tot = {}
    for sd in range(seasons):
        cur, history, epa_events, cur_matches, cur_opr = season(sd)
        for scen in ("fair", "hindsight"):
            hist = {t: list(history[t]) for t in cur}
            if scen == "hindsight":           # what the app does once the event is over: its own stats are the latest
                for t in cur:
                    o = max(0.0, cur_opr[t])
                    hist[t].append(A.EventStat(team=t, event_code="CUR", start_date="2026-03-01", opr_np=o, opr_total=o,
                                               auto=0.3 * o, dc=0.7 * o, endgame=0.2 * o, tb1=0.3 * o, tb2=0.2 * o, qual_played=6))
            for basis in ("CAP", "OPR", "EPA", "XPR"):
                if scen == "hindsight" and basis in ("EPA", "XPR"): continue   # unaffected: they never use the current event's stats
                s = A.Session(season=2025, event_code="CUR", settings=A.Settings(rating=basis))
                s.event_info = {"name": "Sim", "start": "2026-03-01"}
                s.team_numbers = list(cur); s.history = hist; s.epa_events = epa_events
                s.api_matches = [A.Match.from_dict(m.to_dict()) for m in cur_matches]
                s.compute()
                c, d, b, mae, n = evaluate(s)
                k = (basis, scen)
                a = tot.setdefault(k, [0, 0, 0.0, 0.0, 0]); a[0] += c; a[1] += d; a[2] += b; a[3] += mae; a[4] += n
    return tot

if __name__ == "__main__":
    t0 = time.time(); N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    tot = run(N)
    print(f"{N} simulated seasons, {time.time()-t0:.0f}s")
    print(f"{'rating':6} {'scenario':10} {'correct':>14} {'accuracy':>9} {'95% CI':>13} {'Brier':>7} {'margin MAE':>11}")
    for (basis, scen), (c, d, b, mae, n) in sorted(tot.items(), key=lambda kv: (kv[0][1], -kv[1][0] / kv[1][1])):
        p = c / d; ci = 1.96 * math.sqrt(p * (1 - p) / d)
        print(f"{basis:6} {scen:10} {c:6}/{d:<7} {p:9.1%} {('±%.1f pts' % (ci*100)):>13} {b/d:7.3f} {mae/n:11.1f}")
