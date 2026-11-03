#!/usr/bin/env python3
"""
Cut static scenario packs from collected_stocks so VPA Arena runs with no server
(GitHub Pages, phone). Uses exactly the same round builders as vpa_arena_server.py.

    python build_packs.py [out_dir] [--per-mode N]

Output: <out_dir>/packs/index.json + <mode>_NNN.json (40 rounds per file). Dates are kept
(the game hides the year until the reveal). Prehistory is trimmed to keep files phone-sized.
"""
import argparse, json, os, random, sys
from datetime import date

sys.argv = sys.argv[:1] + [a for a in sys.argv[1:]]
import vpa_arena_server as V

# packs are for practising the kind of stocks you actually trade: no penny stocks, real liquidity
V.MIN_PRICE = 5.0
V.MIN_DOLLAR_VOL = 2_000_000

COUNTS = {"position": 450, "trigger": 600, "plan": 450, "trade": 300, "breakout": 350}
PRE_KEEP, PER_FILE = 240, 15


def pack_round(rd):
    pre = rd["prehistory"][-PRE_KEEP:]
    bars = pre + rd["context"] + rd["future"]
    d0 = date.fromisoformat(bars[0]["time"])
    dt = [(date.fromisoformat(b["time"]) - d0).days for b in bars]
    r4 = lambda x: float(f"{x:.5g}")
    spy = {x["time"]: x["close"] for x in rd.get("spy", [])}
    out = {k: v for k, v in rd.items() if k not in ("prehistory", "context", "future", "spy")}
    out.update({"d0": bars[0]["time"], "dt": dt, "o": [r4(b["open"]) for b in bars], "h": [r4(b["high"]) for b in bars],
                "l": [r4(b["low"]) for b in bars], "c": [r4(b["close"]) for b in bars],
                "v": [int(b["volume"]) for b in bars], "nPre": len(pre), "nCtx": len(rd["context"]),
                "spy": [r4(spy[b["time"]]) if b["time"] in spy else None for b in bars] if spy else None})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=".")
    ap.add_argument("--per-mode", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261103)
    a = ap.parse_args()
    random.seed(a.seed)
    outdir = os.path.join(a.out, "packs"); os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        if f.endswith(".json"): os.remove(os.path.join(outdir, f))
    syms = [t for t in V.TICKER_LIST if t != "SPY"]
    spy = V._spy_bars()
    index = {"built": date.today().isoformat(), "tickers": len(syms), "modes": {}}
    for mode, n in COUNTS.items():
        n = a.per_mode or n
        rounds, used, tries = [], {}, 0
        while len(rounds) < n and tries < n * 40:
            tries += 1
            sym = random.choice(syms)
            tup = V.load_bars(V.TICKERS[sym])
            if len(tup) < 400:
                continue
            rd = V.BUILDERS[mode](V.to_dicts(tup), sym, random.choice(["easy", "normal", "normal", "hard"]))
            if not rd:
                continue
            t = rd.get("decision_time") or rd.get("breakout_time")
            if any(abs((date.fromisoformat(t) - date.fromisoformat(u)).days) < 60 for u in used.get(sym, [])):
                continue
            used.setdefault(sym, []).append(t)
            rd["id"] = f"{sym}-{t}"
            first, last = rd["prehistory"][-PRE_KEEP:][0]["time"] if rd["prehistory"] else rd["context"][0]["time"], (rd["future"] or rd["context"])[-1]["time"]
            rd["spy"] = [{"time": tt, "close": c} for (tt, o, h, l, c, v) in spy if first <= tt <= last] if sym != "SPY" else []
            rounds.append(pack_round(rd))
        files = []
        for k in range(0, len(rounds), PER_FILE):
            fn = f"{mode}_{k // PER_FILE:03d}.json"
            with open(os.path.join(outdir, fn), "w") as f:
                json.dump(rounds[k:k + PER_FILE], f, separators=(",", ":"))
            files.append(fn)
        index["modes"][mode] = {"count": len(rounds), "files": files}
        print(f"{mode}: {len(rounds)} rounds in {len(files)} files")
    with open(os.path.join(outdir, "index.json"), "w") as f:
        json.dump(index, f)


if __name__ == "__main__":
    main()
