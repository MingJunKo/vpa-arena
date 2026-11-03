# VPA Arena — Volume-Price Analysis Training Game

**Play anywhere:** https://mingjunko.github.io/vpa-arena/ (phone or desktop, no install). Progress is saved
in each browser; use ⬇ / ⬆ in the top bar to move it between phone and PC. The site rebuilds itself on the
3rd of every month with the latest prices (Actions → *Build & deploy VPA Arena* → Run workflow to do it now).
**Play locally:** double-click `Launch VPA Arena.bat` (uses your local `collected_stocks`, saves to `vpa_gameplay.json`).

A gamified chart-reading trainer built on the same foundation as Big Movers: TradingView
Lightweight Charts, a small local Python server, and your own folder of daily OHLCV CSVs.
No API keys, no data purchase, fully offline (after the first load of the chart library).

---

## Quick start

1. Install requirements (one time):

       pip install flask

2. Put `vpa_arena_server.py` and `vpa_arena.html` in the same folder.

3. Point it at your data — either:
   - copy/rename your Big Movers stock folder so it sits next to the server as `collected_stocks\`, **or**
   - pass the path when starting:

         python vpa_arena_server.py "D:\BigMovers\collected_stocks"

4. Run and open **http://localhost:5052/** in your browser.

A small demo `collected_stocks` folder (SPY + 5 synthetic tickers) is included so you can
try the game immediately — swap in your real folder for proper training.

---

## Getting the CSV data

**You already have it.** The `collected_stocks` folder that powers Big Movers (the one in
your screenshot — AAL.csv, AAPL.csv, …) is exactly what VPA Arena reads. Same file
formats, same tolerant parser. Just point the server at that folder (step 3 above).
The more tickers in the folder, the better the game — 12,000 tickers including delisted
names means effectively unlimited, survivorship-bias-free rounds.

### Keeping prices up to date

The CSVs are a snapshot (they stop at 20 Mar 2026); the server never downloads anything. To top them up:

    pip install yfinance pandas        (one time)
    python update_data.py              (from this folder; ~3–5 minutes)
    python update_data.py --add PLTR HOOD   (also pull full history for new tickers)

Live tickers get the missing days appended; delisted names are never touched (they keep the game
free of survivorship bias); a stock that split since the last update is re-downloaded in full so the
chart has no fake gap; every changed file is first copied to `collected_stocks_backup\<date>\`.
Restart the server afterwards. Monthly is plenty — the game trains on history, not today's tape.

If you ever need to rebuild or extend the dataset:

- **Big Movers shared folder** — the Google Drive folder linked in the Big Movers README
  contains the packaged data.
- **Stooq** (free, no key): bulk historical daily data downloads for US stocks at
  https://stooq.com/db/h/ — extract and convert to `SYMBOL.csv` with columns
  `DateTime,Open,High,Low,Close,Volume`.
- **yfinance** (free Python library): `pip install yfinance`, then for each symbol
  `yf.download(sym, period="max")` and save with the same columns.

Accepted CSV layouts (auto-detected, same as Big Movers):

    DateTime,Open,High,Low,Close,Volume
    idx,date,open,high,low,close,volume
    date,close,open,high,low,volume

---

## The game

**Daily Workout** — 10 mixed rounds (2 Position Trader, 2 Trade Manager, 3 Signal Drill,
2 Breakout Judge, 1 Call the Move). Completing 10 rounds in a day meets your **daily goal** and extends
your **streak**.

**Position Trader** (new in 1.6). About a year of context, then 10–50 candles to trade (by difficulty).
Buy/sell in units (1 unit ≈ 10% of a $100k account), add or cut every candle, or buy ATM calls/puts
(contracts sized to ~1 unit, Black-Scholes priced from recent volatility, 30-bar expiry, so time decay is
real). Orders fill at the next open; one stop protects the whole position (set from last low/high, 1 ATR,
or a click on the chart). Rounds start from a deliberate mix — breakout setups, breakdowns, random dates —
so about half are chop and "always long" doesn't win. Graded by fixed rules (no randomness):
**Direction 30%** (right side during the candles that moved; in chop, flat is right),
**Pressing 25%** (biggest size in the strongest candles, adds to winners; averaging down is penalised —
in chop this becomes **Patience**: fewer trades), **Risk 25%** (worst trade, drawdown, stop on), and
**P&L 20%** (vs what the chart offered). The reveal shows a coach's notes with candle numbers and
"what the chart said at the start" (trend template, 52-week high, RS line vs SPY, tightness, volume
dry-up, base). Stats show your score by chart type and habit counters. Keys: B/S buy/sell 1 unit
(Shift = 3), C/P call/put, X close all, L/A/T stop, Space next candle.

**Trade Manager** (the core mode). The tape replays to a decision point. Go long or
short, then **set your stop by clicking the chart** (or quick presets: 1×/1.5×/2× ATR,
prior bar). You enter at the next bar's open — then manage candle by candle: advance
bars with Space, trail your stop behind prior bars, move to breakeven, or exit. Gaps
through your stop fill at the open, like real life. Results are scored in
**R-multiples**: a clean 1R loss with an honored stop still earns XP (that's a good
trade), while **widening a stop earns zero and a warning** — the game rewards process,
not just outcome. The stats screen tracks your win rate, avg win/loss and expectancy
in R per trade.

**Call the Move (Quick).** Fast reps: at the decision point call Long / Stand-aside /
Short with 1–3× conviction. A call only scores if the move beats an ATR-based noise
band — so "stand aside" in chop is a winning answer.

**VPA Signal Drill.** The server scans for real occurrences of classic VPA signatures —
climactic volume, stopping volume, no demand, no supply, effort-vs-result absorption —
highlights the bar, and quizzes you. Wrong answers make that signature appear more often
(weak-skill targeting), and every answer ends with a short lesson.

**Breakout Judge.** Qullamaggie-style setups: tight base near highs, prior uptrend,
volume expansion through the pivot. You judge follow-through (+10% before −5%) vs
failure, then watch the outcome with entry/target/stop lines drawn on the chart.

**Progression.** XP and levels, combo multiplier for consecutive wins (up to ×2), daily
streak with earned streak-freezes (1 per 7-day streak), per-skill mastery ratings
(Bronze → Silver → Gold → Diamond) across 9 skills, and a stats screen with 14-day XP
history and your full round log.

**Chart.** Full TradingView-style pan/zoom, log/linear scale, and configurable EMA/SMA
overlays (click a chip to toggle, ✕ to remove, "+ MA" to add — settings persist).

---

## Your gameplay file

Every round is appended to `vpa_gameplay.json` next to the server — profile, XP, streak,
skills, and a full log of every simulation you've run (timestamp, mode, symbol, your
answer, the truth, XP earned). The ⬇ / ⬆ buttons in the top bar export/import the same
JSON so you can back it up or move machines. If the server is offline the game falls
back to browser localStorage and re-syncs when the server returns.

**Learn (📖).** A built-in VPA field guide: every drilled signature (climactic volume,
stopping volume, no demand, no supply, effort-vs-result absorption) with an illustrated
what-to-look-for / what-it-means / what-follows breakdown, plus breakout anatomy and
R-multiple risk management. Drill verdicts link straight to the relevant chapter.

## Keyboard

Space play/pause replay or next bar in a trade · 1/2/3 answer · E exit trade ·
N or Enter next round.

## Notes

- Symbols are hidden ("?????") during a round and revealed after you answer, so you
  can't be biased by knowing the stock.
- Rounds are filtered for liquidity (median price ≥ $1, avg dollar volume ≥ $300k) to
  avoid junk charts; adjust `MIN_PRICE` / `MIN_DOLLAR_VOL` at the top of the server file.
- Difficulty changes context length and prediction horizon in Bar Replay.
- The chart library loads from unpkg the first time; to run fully offline, download
  `lightweight-charts.standalone.production.js` (v3.8.0) into the same folder.
