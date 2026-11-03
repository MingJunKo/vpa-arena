#!/usr/bin/env python3
"""
VPA Arena — top up collected_stocks with the latest daily prices (Yahoo Finance, free, no key).

    python update_data.py                    # update every live ticker in collected_stocks
    python update_data.py --add NVDA PLTR    # also download full history for new tickers
    python update_data.py "D:\\path\\to\\collected_stocks"

What it does
  * Live tickers (data within ~3 weeks of the newest file) get the missing days appended.
  * Delisted tickers (data stops years ago) are left exactly as they are — they're what keeps the
    game free of survivorship bias, so they're never touched.
  * If a stock split or re-adjustment since your last update makes the old and new prices disagree,
    that ticker's full history is re-downloaded so the chart has no fake gap.
  * Every file it changes is first copied to collected_stocks_backup\\<date>\\ (nothing is deleted).
  * Updated files are written as DateTime,Open,High,Low,Close,Volume — a layout the server reads.

Needs:  pip install yfinance pandas
Then restart vpa_arena_server.py so it picks up the new data.
"""
import argparse
import datetime as dt
import os
import shutil
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def read_csv(path):
    """Same tolerant layouts as the server: [idx,date,o,h,l,c,v] / [DateTime,o,h,l,c,v] / [date,c,o,h,l,v]."""
    df = pd.read_csv(path, dtype=str)
    cols = [c.strip().lower() for c in df.columns]
    if len(cols) >= 2 and "date" in cols[1]:
        df = df.iloc[:, 1:7]; df.columns = ["date", "open", "high", "low", "close", "volume"]
    elif "date" in cols[0]:
        df = df.iloc[:, 0:6]; df.columns = ["date", "open", "high", "low", "close", "volume"]
    else:
        df = df.iloc[:, 0:6]; df.columns = ["date", "close", "open", "high", "low", "volume"]
    df["date"] = pd.to_datetime(df["date"].str.strip(), errors="coerce", format="mixed")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    return df[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def write_csv(df, path):
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.columns = ["DateTime", "Open", "High", "Low", "Close", "Volume"]
    out["Volume"] = out["Volume"].fillna(0).round().astype("int64")
    out.round(4).to_csv(path, index=False)


def yahoo(tickers, **kw):
    import yfinance as yf
    sym = {t: t.replace(".", "-") for t in tickers}
    for attempt in range(4):
        try:
            raw = yf.download(list(sym.values()), interval="1d", auto_adjust=True, group_by="ticker",
                              threads=True, progress=False, **kw)
            break
        except Exception as e:
            print(f"  retry {attempt + 1}: {e}"); time.sleep(10 * (attempt + 1))
    else:
        return {}
    out = {}
    for t, s in sym.items():
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if len(sub):
            d = sub.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
            d.columns = ["date", "open", "high", "low", "close", "volume"]
            d["date"] = pd.to_datetime(d["date"]).dt.tz_localize(None).dt.normalize()
            out[t] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=os.path.join(HERE, "collected_stocks"))
    ap.add_argument("--add", nargs="*", default=[], help="new tickers to download in full")
    a = ap.parse_args()
    folder = a.folder
    if not os.path.isdir(folder):
        sys.exit(f"Folder not found: {folder}")
    backup = os.path.join(os.path.dirname(folder), "collected_stocks_backup", dt.date.today().isoformat())

    files = {os.path.splitext(f)[0].upper(): os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".csv")}
    data, bad = {}, []
    for t, p in files.items():
        try:
            data[t] = read_csv(p)
        except Exception:
            bad.append(t)
    newest = max(d["date"].iloc[-1] for d in data.values() if len(d))
    live = {t: d for t, d in data.items() if len(d) and d["date"].iloc[-1] >= newest - pd.Timedelta(days=21)}
    print(f"{len(files)} files · newest data {newest.date()} · {len(live)} live tickers to update · "
          f"{len(data) - len(live)} delisted/old left untouched" + (f" · {len(bad)} unreadable: {', '.join(bad)}" if bad else ""))

    def save(t, df):
        p = files.get(t, os.path.join(folder, f"{t}.csv"))
        if os.path.exists(p):
            os.makedirs(backup, exist_ok=True)
            if not os.path.exists(os.path.join(backup, os.path.basename(p))):
                shutil.copy2(p, backup)
        write_csv(df, p)

    start = (newest - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    names = sorted(live)
    updated, refetched, nodata = 0, [], []
    for k in range(0, len(names), 100):
        part = names[k:k + 100]
        got = yahoo(part, start=start)
        full_needed = []
        for t in part:
            new, old = got.get(t), live[t]
            if new is None or new.empty:
                nodata.append(t); continue
            overlap = old.merge(new, on="date", suffixes=("_o", "_n"))
            if len(overlap):
                ratio = (overlap.close_n / overlap.close_o).median()
                if abs(ratio - 1) > 0.02:                       # split / re-adjustment since last update
                    full_needed.append(t); continue
            add = new[new.date > old.date.iloc[-1]]
            if len(add):
                save(t, pd.concat([old, add], ignore_index=True)); updated += 1
        if full_needed:
            for t, d in yahoo(full_needed, period="max").items():
                save(t, d); refetched.append(t)
        print(f"  {min(k + 100, len(names))}/{len(names)} checked")
        time.sleep(1)

    if a.add:
        got = yahoo([t.upper() for t in a.add], period="max")
        for t, d in got.items():
            save(t, d)
        print(f"added {len(got)} new tickers: {', '.join(got)}"
              + (f" · not found: {', '.join(set(t.upper() for t in a.add) - set(got))}" if len(got) < len(a.add) else ""))

    print(f"\nDone. {updated} tickers topped up · {len(refetched)} re-downloaded after a split"
          + (f" ({', '.join(refetched[:15])})" if refetched else "")
          + (f" · no Yahoo data for {len(nodata)} (likely delisted since): {', '.join(nodata[:20])}" if nodata else ""))
    print(f"Backups of changed files: {backup}" if os.path.isdir(backup) else "No files changed.")
    print("Restart vpa_arena_server.py to load the new data.")


if __name__ == "__main__":
    main()
