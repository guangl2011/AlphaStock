"""Generate the correlation fixtures — deterministic, seeded, offline.

Run from the repo's ``pipelines`` directory:

    python tests/fixtures/correlation/generate.py

Writes, in this directory:

* ``closes.csv`` — EOD closes for the 8 factor ETF proxies and 6 synthetic
  stocks over 120 trading days (2026-06-01 onward). Same canonical columns
  as the index fixture (``ticker,universe,date,close,adjClose``).
* ``index_levels.csv`` — synthetic AS-100 / AS-MC100 level series over the
  same days, in the canonical index pipeline format.
* ``signal_events.csv`` — five V1–V4 signal events inside the event-study
  window (enough estimation history before each, three trading days after).

The generator is a fixed-seed LCG — no randomness, no clock reads — so
re-running it reproduces these files byte-for-byte.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

SEED = 20261114
N_DAYS = 120
START = date(2026, 6, 1)

PROXIES = ("SPY", "IWM", "IWC", "RSP", "XLU", "URA", "GRID", "ROBO")
STOCK_MODELS = {
    # ticker: (beta on MKT, weight on thematic, thematic factor, noise scale)
    "AAOI": (0.7, 0.3, "GRID", 0.005),
    "AEP": (0.5, 0.6, "UTIL", 0.004),
    "AMSC": (1.2, 0.4, "GRID", 0.008),
    "BE": (1.1, 0.5, "NUKE", 0.007),
    "FLNC": (0.9, 0.5, "GRID", 0.009),
    "NNE": (1.3, 0.6, "NUKE", 0.006),
}

SIGNALS = [
    ("SIG-2026-0915-A", "V2_interconnection", "positive", 4,
     "2026-09-15T20:00:00+00:00", "AAOI|AMSC"),
    ("SIG-2026-1001-B", "V1_news", "negative", 3,
     "2026-10-01T14:30:00+00:00", "AEP"),
    ("SIG-2026-1014-C", "V4_permit", "positive", 5,
     "2026-10-14T18:00:00+00:00", "BE|FLNC"),
    ("SIG-2026-1030-D", "V3_queue", "neutral", 2,
     "2026-10-30T15:00:00+00:00", "NNE"),
    ("SIG-2026-1110-E", "V2_interconnection", "positive", 3,
     "2026-11-10T20:00:00+00:00", "AAOI"),
]


class Lcg:
    """Fixed-seed 64-bit LCG — the only 'randomness' allowed here."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def next(self) -> float:
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % 2**64
        return self.state / 2**64

    def noise(self, scale: float) -> float:
        return scale * (self.next() - 0.5)


def trading_days(count: int) -> list[date]:
    out: list[date] = []
    day = START
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def main() -> None:
    rng = Lcg(SEED)
    days = trading_days(N_DAYS)

    mkt = [0.0004 + 0.011 * (rng.next() - 0.5) for _ in range(N_DAYS - 1)]
    proxy_returns: dict[str, list[float]] = {"SPY": mkt}
    for proxy in ("IWM", "IWC", "RSP", "XLU", "URA", "GRID", "ROBO"):
        base = mkt if proxy == "IWM" else [0.0002 + 0.010 * (rng.next() - 0.5) for _ in mkt]
        proxy_returns[proxy] = [r + rng.noise(0.004) for r in base]

    # Stock returns are exact linear combinations of the factor returns plus
    # seeded noise — the relationships the model is supposed to recover.
    theme_proxy = {"UTIL": "XLU", "NUKE": "URA", "GRID": "GRID", "ROBO": "ROBO"}
    stock_returns: dict[str, list[float]] = {}
    for ticker, (beta, weight, theme, noise) in STOCK_MODELS.items():
        theme_series = proxy_returns[theme_proxy[theme]]
        stock_returns[ticker] = [
            beta * m + weight * t + rng.noise(noise)
            for m, t in zip(mkt, theme_series, strict=False)
        ]

    closes: dict[str, list[float]] = {}
    for ticker, series in [("SPY", mkt), *[(p, proxy_returns[p]) for p in PROXIES if p != "SPY"],
                           *stock_returns.items()]:
        levels = [300.0 if ticker in PROXIES else 40.0]
        for r in series:
            levels.append(levels[-1] * (1.0 + r))
        closes[ticker] = levels

    with (HERE / "closes.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
        writer.writerow(["ticker", "universe", "date", "close", "adjClose"])
        for ticker, levels in sorted(closes.items()):
            universe = "proxy" if ticker in PROXIES else "Mixed"
            for day, close in zip(days, levels, strict=False):
                text = f"{close:.4f}"
                writer.writerow([ticker, universe, day.isoformat(), text, text])

    # Synthetic index levels: AS-100 tracks 0.8*MKT + 0.2*GRID; AS-MC100 is
    # more small-cap sensitive (0.9*MKT + 0.4*SIZE spread).
    with (HERE / "index_levels.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
        writer.writerow(["index_id", "date", "level", "constituents"])
        for index_id, beta, weight, theme in (
            ("AS-100", 0.8, 0.2, "GRID"),
            ("AS-MC100", 0.9, 0.4, "IWM"),
        ):
            theme_series = proxy_returns[theme]
            level = 1000.0
            writer.writerow([index_id, days[0].isoformat(), f"{level:.6f}", 100])
            for day_index, (m, t) in enumerate(zip(mkt, theme_series, strict=False)):
                level *= 1.0 + beta * m + weight * t + rng.noise(0.002)
                writer.writerow([index_id, days[day_index + 1].isoformat(), f"{level:.6f}", 100])

    day_set = set(days)
    for signal_id, _, _, _, published, _ in SIGNALS:
        event_day = date.fromisoformat(published[:10])
        position = days.index(event_day)
        assert event_day in day_set and 64 <= position <= N_DAYS - 4, (
            f"{signal_id}: event day {event_day} at position {position} violates "
            "the estimation/window bounds"
        )

    with (HERE / "signal_events.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
        writer.writerow(["signal_id", "category", "direction", "strength",
                         "published_at", "tickers"])
        writer.writerows(SIGNALS)

    print(f"wrote closes.csv, index_levels.csv, signal_events.csv to {HERE}")


if __name__ == "__main__":
    main()
