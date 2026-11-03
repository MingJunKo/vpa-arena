#!/usr/bin/env python3
"""
VPA Arena - Volume Price Analysis Training Game (server)

Usage:
    python vpa_arena_server.py [path\\to\\collected_stocks]

Then open http://localhost:5052/ in your browser.

- Reads daily OHLCV CSVs from your collected_stocks folder (same files Big Movers uses)
- Generates random training rounds (Trade Manager, Call the Move, VPA Signal Drills, Breakout Judge)
- Persists your full gameplay history + progression to vpa_gameplay.json
"""

import csv
import os
import sys
import json
import random
import statistics
from functools import lru_cache
from flask import Flask, jsonify, request, Response

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=SCRIPT_DIR, static_url_path="")

# ---------------------------------------------------------------- config
STOCKS_DIRS = [
    os.path.join(SCRIPT_DIR, "collected_stocks"),
]
if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
    STOCKS_DIRS.insert(0, os.path.abspath(sys.argv[1]))

GAMEPLAY_FILE = os.path.join(SCRIPT_DIR, "vpa_gameplay.json")

MIN_PRICE = 1.0          # skip sub-$1 windows
MIN_DOLLAR_VOL = 300_000  # avg close*volume over last 50 visible bars
MAX_TRIES = 80            # random ticker attempts per round request

# ---------------------------------------------------------------- data loading

def _list_tickers():
    seen = {}
    for d in STOCKS_DIRS:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith(".csv"):
                sym = os.path.splitext(f)[0].upper()
                if sym not in seen:
                    seen[sym] = os.path.join(d, f)
    return seen

TICKERS = _list_tickers()
TICKER_LIST = list(TICKERS.keys())


@lru_cache(maxsize=256)
def load_bars(path):
    """Tolerant CSV loader (same layouts Big Movers supports)."""
    bars = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return ()
            if len(header) >= 2 and "date" in (header[1] or "").lower():
                fmt = "new"       # [idx, date, o, h, l, c, v]
            elif len(header) >= 1 and "date" in (header[0] or "").lower():
                fmt = "noindex"   # [DateTime, O, H, L, C, V]
            else:
                fmt = "old"       # [date, close, open, high, low, volume]
            for row in reader:
                try:
                    if fmt == "new":
                        if len(row) < 7:
                            continue
                        t, o, h, l, c, v = row[1].strip(), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6])
                    elif fmt == "noindex":
                        if len(row) < 6:
                            continue
                        raw = row[0].strip()
                        if len(raw) == 10 and raw[2] == "/":
                            p = raw.split("/")
                            t = f"{p[2]}-{p[0]:>02}-{p[1]:>02}"
                        else:
                            t = raw
                        o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                    else:
                        if len(row) < 6:
                            continue
                        t, c, o, h, l, v = row[0].strip(), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
                    if len(t) >= 10 and t[2] == "/":            # mm/dd/yyyy in any layout -> ISO
                        mm, dd, yy = t[:10].split("/")
                        t = f"{yy}-{mm:>02}-{dd:>02}"
                    if c <= 0 or h < l or len(t) < 10 or t[4] != "-":
                        continue
                    bars.append({"time": t[:10], "open": o, "high": h, "low": l, "close": c, "volume": v})
                except (ValueError, IndexError):
                    continue
    except Exception:
        return ()
    bars.sort(key=lambda x: x["time"])
    # Drop duplicate dates (split/merged/bad CSVs). Charts require strictly ascending,
    # unique timestamps -- a single duplicate makes the client chart throw and breaks the
    # round (no render, entry won't proceed, timeframe/RS buttons look dead).
    dedup = []
    for b in bars:
        if dedup and dedup[-1]["time"] == b["time"]:
            dedup[-1] = b          # keep the latest row for that date
        else:
            dedup.append(b)
    bars = dedup
    return tuple((b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars)


def to_dicts(tup):
    return [{"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v} for (t, o, h, l, c, v) in tup]


# ---------------------------------------------------------------- indicators

def atr_pct(bars, i, period=14):
    """ATR as % of close at index i (bars = list of dicts)."""
    if i < period + 1:
        return 0.03
    trs = []
    for j in range(i - period + 1, i + 1):
        b, pb = bars[j], bars[j - 1]
        tr = max(b["high"] - b["low"], abs(b["high"] - pb["close"]), abs(b["low"] - pb["close"]))
        trs.append(tr)
    c = bars[i]["close"]
    return (sum(trs) / len(trs)) / c if c > 0 else 0.03


def avg_vol(bars, i, period=20):
    lo = max(0, i - period)
    vols = [b["volume"] for b in bars[lo:i]]
    return (sum(vols) / len(vols)) if vols else 0.0


def window_ok(bars, i):
    """Liquidity/price sanity on the 50 bars ending at i."""
    lo = max(0, i - 49)
    win = bars[lo:i + 1]
    if not win:
        return False
    closes = [b["close"] for b in win]
    if statistics.median(closes) < MIN_PRICE:
        return False
    dv = [b["close"] * b["volume"] for b in win]
    if (sum(dv) / len(dv)) < MIN_DOLLAR_VOL:
        return False
    return True


def outcome_label(bars, i, horizon):
    """Ground truth for 'what happens next': long/short/flat, noise-adjusted by ATR."""
    if i + horizon >= len(bars):
        horizon = len(bars) - 1 - i
    if horizon < 3:
        return None
    entry = bars[i]["close"]
    exitc = bars[i + horizon]["close"]
    move = (exitc - entry) / entry
    noise = max(0.8 * atr_pct(bars, i), 0.01)
    if move > noise:
        lab = "long"
    elif move < -noise:
        lab = "short"
    else:
        lab = "flat"
    return {"label": lab, "move_pct": round(move * 100, 2), "noise_pct": round(noise * 100, 2), "horizon": horizon}


# ---------------------------------------------------------------- VPA event detectors

VPA_CONCEPTS = {
    "climax": "Climactic volume",
    "stopping": "Stopping volume",
    "no_demand": "No demand",
    "no_supply": "No supply",
    "absorption": "Effort vs result (absorption)",
}


def detect_vpa_events(bars):
    """Scan for classic VPA signatures. Returns list of (index, concept_key)."""
    events = []
    n = len(bars)
    for i in range(60, n - 20):
        b = bars[i]
        rng = b["high"] - b["low"]
        if rng <= 0:
            continue
        av = avg_vol(bars, i)
        if av <= 0:
            continue
        vr = b["volume"] / av                       # volume ratio vs 20-bar avg
        a = atr_pct(bars, i) * b["close"]           # ATR in price terms
        if a <= 0:
            continue
        rr = rng / a                                # range ratio vs ATR
        close_pos = (b["close"] - b["low"]) / rng   # close position in bar 0..1
        up_move = b["close"] > bars[i - 12]["close"] * 1.10
        down_move = b["close"] < bars[i - 12]["close"] * 0.92
        up_bar = b["close"] > b["open"]
        down_bar = b["close"] < b["open"]

        # Buying climax: extended up-move + huge volume + wide range
        if up_move and vr > 2.5 and rr > 1.7:
            events.append((i, "climax"))
        # Stopping volume: down-move, heavy volume, closes in upper part, undercuts prior low
        elif down_move and vr > 2.0 and close_pos > 0.6 and b["low"] < bars[i - 1]["low"]:
            events.append((i, "stopping"))
        # No demand: up bar, narrow range, volume below prior two bars, after up-move
        elif up_bar and rr < 0.6 and b["volume"] < bars[i - 1]["volume"] and b["volume"] < bars[i - 2]["volume"] and up_move:
            events.append((i, "no_demand"))
        # No supply: down bar, narrow range, low volume, within an uptrend pullback
        elif down_bar and rr < 0.6 and b["volume"] < bars[i - 1]["volume"] and b["volume"] < bars[i - 2]["volume"] and b["close"] > bars[i - 40]["close"]:
            events.append((i, "no_supply"))
        # Absorption: massive effort (volume) with no result (tiny range)
        elif vr > 2.5 and rr < 0.7:
            events.append((i, "absorption"))
    return events


def detect_breakouts(bars):
    """Qullamaggie-style: tight consolidation near highs -> breakout day."""
    setups = []
    n = len(bars)
    for i in range(80, n - 30):
        b = bars[i]
        if b["close"] < 2.0:
            continue
        prior_high = max(x["high"] for x in bars[i - 40:i])
        if b["close"] <= prior_high:
            continue
        base = bars[i - 20:i]
        base_hi = max(x["high"] for x in base)
        base_lo = min(x["low"] for x in base)
        if (base_hi - base_lo) / b["close"] > 0.28:
            continue
        if b["close"] <= bars[i - 60]["close"]:      # need prior uptrend
            continue
        av = avg_vol(bars, i)
        if av <= 0 or b["volume"] < 1.3 * av:        # breakout volume expansion
            continue
        setups.append(i)
    return setups


def breakout_outcome(bars, i, up=0.10, dn=0.05, look=25):
    entry = bars[i]["close"]
    tgt, stp = entry * (1 + up), entry * (1 - dn)
    for j in range(i + 1, min(i + 1 + look, len(bars))):
        hit_t = bars[j]["high"] >= tgt
        hit_s = bars[j]["low"] <= stp
        if hit_t and hit_s:
            # One daily bar spanned BOTH the target and the stop; with no intraday data we
            # can't know the order high/low were made. Resolve by where the bar CLOSED:
            # a bar that closes above the target is a genuine follow-through (a gap-down or
            # dip that got bought and ripped), even if it opened at/through the stop. Only a
            # close back through the stop is a real failure; anything in between falls back to
            # the opening gap direction.
            c = bars[j]["close"]
            if c >= tgt:
                return "follow"
            if c <= stp:
                return "fail"
            return "fail" if bars[j]["open"] <= stp else "follow"
        if hit_t:
            return "follow"
        if hit_s:
            return "fail"
    # neither hit: judge by final close
    endc = bars[min(i + look, len(bars) - 1)]["close"]
    return "follow" if endc > entry * 1.04 else "fail"


# ---------------------------------------------------------------- round builders

PRE_BARS = 360   # extra display-only history before the context window (enables weekly/monthly views)


def slice_round(bars, i, ctx, fut):
    start = max(0, i - ctx + 1)
    context = bars[start:i + 1]
    future = bars[i + 1:i + 1 + fut]
    prehistory = bars[max(0, start - PRE_BARS):start]
    return context, future, prehistory


def build_replay(bars, sym, difficulty, fut=None, mode="replay"):
    ctx = {"easy": 200, "normal": 150, "hard": 110}.get(difficulty, 150)
    hz = {"easy": 15, "normal": 10, "hard": 7}.get(difficulty, 10)
    fut = fut or (hz + 10)
    lo, hi = ctx + 10, len(bars) - fut - 2
    if hi <= lo:
        return None
    for _ in range(6):
        i = random.randint(lo, hi)
        if not window_ok(bars, i):
            continue
        out = outcome_label(bars, i, hz)
        if not out:
            continue
        context, future, prehistory = slice_round(bars, i, ctx, fut)
        return {
            "mode": mode, "prehistory": prehistory, "symbol": sym, "difficulty": difficulty,
            "context": context, "future": future,
            "decision_time": bars[i]["time"],
            "atr_pct": round(atr_pct(bars, i) * 100, 2),
            "truth": out,
        }
    return None


def build_trade(bars, sym, difficulty):
    """Same setup selection as replay, but a 50-bar future window for
    candle-by-candle trade management (entry, stop, trail, exit)."""
    return build_replay(bars, sym, difficulty, fut=50, mode="trade")


def build_drill(bars, sym, difficulty, focus=None):
    events = detect_vpa_events(bars)
    if not events:
        return None
    groups = {}
    for i, c in events:
        groups.setdefault(c, []).append(i)
    # pick the concept class first (uniform across classes) so rare signatures
    # like climaxes appear as often as common ones; honor a 'focus' concept
    concept = focus if (focus in groups) else random.choice(list(groups.keys()))
    idxs = groups[concept]
    for i in random.sample(idxs, min(8, len(idxs))):
        if not window_ok(bars, i):
            continue
        out = outcome_label(bars, i, 10)
        if not out:
            continue
        context, future, prehistory = slice_round(bars, i, 120, 20)
        keys = list(VPA_CONCEPTS.keys())
        keys.remove(concept)
        distractors = random.sample(keys, 3)
        options = distractors + [concept]
        random.shuffle(options)
        return {
            "mode": "drill", "prehistory": prehistory, "symbol": sym, "difficulty": difficulty,
            "context": context, "future": future,
            "event_time": bars[i]["time"],
            "concept": concept,
            "concept_label": VPA_CONCEPTS[concept],
            "options": [{"key": k, "label": VPA_CONCEPTS[k]} for k in options],
            "truth": out,
        }
    return None


def build_breakout(bars, sym, difficulty):
    setups = detect_breakouts(bars)
    random.shuffle(setups)
    for i in setups[:12]:
        if not window_ok(bars, i):
            continue
        context, future, prehistory = slice_round(bars, i, 150, 30)
        res = breakout_outcome(bars, i)
        return {
            "mode": "breakout", "prehistory": prehistory, "symbol": sym, "difficulty": difficulty,
            "context": context, "future": future,
            "breakout_time": bars[i]["time"],
            "entry": bars[i]["close"],
            "target_pct": 10, "stop_pct": 5,
            "truth": {"label": res},
        }
    return None


def detect_breakdowns(bars):
    """Mirror of detect_breakouts: tight base, then a close below the 40-bar low (short-side practice)."""
    setups = []
    for i in range(80, len(bars) - 30):
        c = bars[i]["close"]
        if c < 2.0 or c >= min(x["low"] for x in bars[i - 40:i]):
            continue
        base = bars[i - 20:i]
        if (max(x["high"] for x in base) - min(x["low"] for x in base)) / c > 0.30:
            continue
        setups.append(i)
    return setups


def move_character(bars, i, n):
    """Same rule the Position Trader grades with: up/down = net move >= max(2 ATR, 4%) that travelled
    reasonably directly (efficiency >= 0.2), or any move >= max(5 ATR, 12%) (capped at 25%); everything else is chop."""
    a, z = bars[i]["close"], bars[i + n]["close"]
    path = sum(abs(bars[j]["close"] - bars[j - 1]["close"]) for j in range(i + 1, i + n + 1))
    net, er, ap = z / a - 1, (abs(z - a) / path if path else 0), atr_pct(bars, i)
    big, strong = abs(net) >= max(2 * ap, 0.04), abs(net) >= min(max(5 * ap, 0.12), 0.25)
    kind = ("up" if net > 0 else "down") if big and (er >= 0.2 or strong) else "chop"
    return {"label": kind, "move_pct": round(net * 100, 2), "efficiency": round(er, 2), "horizon": n}


def build_position(bars, sym, difficulty):
    """Position Trader: ~1 year of context, then N candles to trade with sizing, adds, stops and options.
    Decision points are mixed on purpose — breakout setups, breakdowns and random dates — so that
    'always long' doesn't win and sitting out chop is a skill."""
    lo_n, hi_n = {"easy": (35, 50), "normal": (20, 40), "hard": (10, 25)}.get(difficulty, (20, 40))
    n = random.randint(lo_n, hi_n)
    ctx, fut = 260, n + 40
    r = random.random()
    if r < 0.35:
        origin, cands = "breakout setup", [i - random.randint(0, 5) for i in detect_breakouts(bars)]
    elif r < 0.50:
        origin, cands = "breakdown setup", [i - random.randint(0, 5) for i in detect_breakdowns(bars)]
    else:
        origin, cands = "random date", [random.randint(ctx + 10, max(ctx + 11, len(bars) - fut - 2)) for _ in range(8)]
    random.shuffle(cands)
    for i in cands[:12]:
        if i < ctx + 10 or i + fut + 1 >= len(bars) or not window_ok(bars, i):
            continue
        context, future, prehistory = slice_round(bars, i, ctx, fut)
        return {
            "mode": "position", "prehistory": prehistory, "symbol": sym, "difficulty": difficulty,
            "context": context, "future": future, "decision_time": bars[i]["time"],
            "atr_pct": round(atr_pct(bars, i) * 100, 2), "play_bars": n, "origin": origin,
            "truth": move_character(bars, i, n),
        }
    return None


def _setup_candidates(bars, mix=(0.45, 0.20)):
    """Breakout setups / breakdown setups / random dates, in roughly the proportions real scanning produces."""
    r = random.random()
    if r < mix[0]:
        return "breakout setup", detect_breakouts(bars)
    if r < mix[0] + mix[1]:
        return "breakdown setup", detect_breakdowns(bars)
    return "random date", [random.randint(300, max(301, len(bars) - 80)) for _ in range(8)]


def build_trigger(bars, sym, difficulty):
    """Pull the Trigger: the tape goes live 8-25 bars BEFORE a pivot (or at a random date); you decide
    bar by bar when — or whether — to enter. Window = lead + 15 bars; 35 more bars to manage/reveal."""
    origin, cands = _setup_candidates(bars)
    random.shuffle(cands)
    for p in cands[:12]:
        lead = random.randint(8, 25)
        s, win = p - lead, lead + 15
        if s < 280 or s + win + 36 >= len(bars) or not window_ok(bars, s):
            continue
        context, future, prehistory = slice_round(bars, s, 260, win + 35)
        return {"mode": "trigger", "prehistory": prehistory, "symbol": sym, "difficulty": difficulty,
                "context": context, "future": future, "decision_time": bars[s]["time"],
                "atr_pct": round(atr_pct(bars, s) * 100, 2), "window": win, "origin": origin,
                "pivot_time": bars[p]["time"] if origin != "random date" else None,
                "truth": {"label": origin}}
    return None


def build_plan(bars, sym, difficulty):
    """Plan the Trade: decision point just before a pivot (or random). You write the plan — direction,
    entry trigger, stop, exit rule — and it executes over the next 45 bars without intervention."""
    origin, cands = _setup_candidates(bars, (0.5, 0.2))
    random.shuffle(cands)
    for p in cands[:12]:
        d = p - random.randint(1, 4) if origin != "random date" else p
        if d < 280 or d + 46 >= len(bars) or not window_ok(bars, d):
            continue
        context, future, prehistory = slice_round(bars, d, 260, 45)
        return {"mode": "plan", "prehistory": prehistory, "symbol": sym, "difficulty": difficulty,
                "context": context, "future": future, "decision_time": bars[d]["time"],
                "atr_pct": round(atr_pct(bars, d) * 100, 2), "origin": origin, "truth": {"label": origin}}
    return None


BUILDERS = {"replay": build_replay, "drill": build_drill, "breakout": build_breakout, "trade": build_trade,
            "position": build_position, "trigger": build_trigger, "plan": build_plan}


_SPY_CACHE = None


def _spy_bars():
    global _SPY_CACHE
    if _SPY_CACHE is None:
        p = TICKERS.get("SPY")
        _SPY_CACHE = load_bars(p) if p else ()
    return _SPY_CACHE


# ---------------------------------------------------------------- routes

@app.route("/")
def index():
    p = os.path.join(SCRIPT_DIR, "vpa_arena.html")
    if not os.path.exists(p):
        return f"vpa_arena.html not found in {SCRIPT_DIR}", 404
    with open(p, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/api/health")
def api_health():
    return jsonify({
        "tickers": len(TICKER_LIST),
        "dirs": STOCKS_DIRS,
        "gameplay_file": GAMEPLAY_FILE,
    })


@app.route("/api/round")
def api_round():
    mode = (request.args.get("mode") or "replay").strip()
    difficulty = (request.args.get("difficulty") or "normal").strip()
    builder = BUILDERS.get(mode)
    if not builder:
        return jsonify({"error": f"unknown mode {mode}"}), 400
    if not TICKER_LIST:
        return jsonify({"error": "No CSVs found. Point the server at your collected_stocks folder."}), 404
    for _ in range(MAX_TRIES):
        sym = random.choice(TICKER_LIST)
        tup = load_bars(TICKERS[sym])
        if len(tup) < 260:
            continue
        bars = to_dicts(tup)
        if mode == "drill":
            rd = builder(bars, sym, difficulty, focus=(request.args.get("focus") or None))
        else:
            rd = builder(bars, sym, difficulty)
        if rd:
            rd["id"] = f"{sym}-{rd.get('decision_time') or rd.get('event_time') or rd.get('breakout_time')}"
            pre = rd.get("prehistory") or []
            first = (pre[0] if pre else rd["context"][0])["time"]
            last = (rd["future"][-1] if rd["future"] else rd["context"][-1])["time"]
            spy = [{"time": t, "close": c} for (t, o, h, l, c, v) in _spy_bars() if first <= t <= last]
            if spy and sym != "SPY":
                rd["spy"] = spy
            return jsonify(rd)
    return jsonify({"error": "Could not find a qualifying round — try again (or check data folder)."}), 503


@app.route("/api/gameplay", methods=["GET", "POST"])
def api_gameplay():
    if request.method == "GET":
        if not os.path.exists(GAMEPLAY_FILE):
            return jsonify({})
        try:
            with open(GAMEPLAY_FILE, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception:
            return jsonify({})
    try:
        data = request.get_json(silent=True) or {}
        with open(GAMEPLAY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print(f"VPA Arena: http://localhost:5052/   ({len(TICKER_LIST)} tickers loaded)")
    if not TICKER_LIST:
        print("WARNING: no CSVs found. Run as: python vpa_arena_server.py path\\to\\collected_stocks")
    app.run(host="127.0.0.1", port=5052, debug=False)
