#!/usr/bin/env python3
# -*- coding: utf-8; py-indent-offset:4 -*-

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd

currentdir = os.path.dirname(os.path.realpath(__file__))
grandparentdir = os.path.dirname(os.path.dirname(currentdir))
sys.path.append(os.path.join(grandparentdir, "src"))

from sfox_trader.lib.sfox_client import SFOXTrader

API_PERIODS = [60, 300, 900, 3600, 21600, 86400]
MAX_CANDLES_PER_REQUEST = 500


def epoch_to_utc_str(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_args(pargs=None):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    pgroup = parser.add_argument_group(title="Candle Arguments")
    pgroup.add_argument("--size", "-s", default=60, type=int, help="candle size in seconds")
    pgroup.add_argument(
        "--ticker",
        "-t",
        default="btcusd",
        type=str,
        help="single ticker, comma list, or one of: all, majors, usd, usdt, usdc",
    )
    pgroup.add_argument("--append", "-a", default=False, action="store_true", help="append mode")
    pgroup.add_argument("--sdir", "-d", default="./size", type=str, help="base output directory")
    pgroup.add_argument("--date_start", default=None, type=str, help="start date like 2025-01-01T00:00:00Z")
    pgroup.add_argument(
        "--api_key_env",
        default=None,
        type=str,
        help="optional env var name for API key (not required for public candles)",
    )
    pgroup.add_argument(
        "--twap",
        default=False,
        action="store_true",
        help="include twap_1m column generation",
    )
    return parser.parse_args(pargs)


def parse_iso_to_epoch(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp())


def default_start_for_size(size: int) -> str:
    if size in [60, 300, 900]:
        return "2025-01-01T00:00:00Z"
    if size == 3600:
        return "2023-10-01T00:00:00Z"
    if size in [21600, 86400]:
        return "2021-10-01T00:00:00Z"
    return "2024-01-01T00:00:00Z"


def make_dirs(size: int, sdir: str) -> str:
    path = os.path.join(sdir, str(size))
    os.makedirs(path, exist_ok=True)
    return path


def normalize_ticker(pair: str) -> str:
    return pair.lower().strip().replace("/", "")


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _normalize_api_candles(rows: Iterable[dict], ticker: str, size: int) -> List[dict]:
    candles = []
    for row in rows:
        epoch = _safe_int(row.get("start_time"))
        candles.append(
            {
                "timestamp": epoch_to_utc_str(epoch),
                "close": _safe_float(row.get("close_price")),
                "high": _safe_float(row.get("high_price")),
                "low": _safe_float(row.get("low_price")),
                "open": _safe_float(row.get("open_price")),
                "size": size,
                "ticker": ticker,
                "epoch": epoch,
                "trades": _safe_float(row.get("num_trades")),
                "volume": _safe_float(row.get("volume")),
                "vwap": _safe_float(row.get("vwap")),
            }
        )
    candles.sort(key=lambda x: x["epoch"])
    dedup = {}
    for candle in candles:
        dedup[candle["epoch"]] = candle
    return [dedup[k] for k in sorted(dedup.keys())]


def fetch_range(client: SFOXTrader, pair: str, period: int, start_ts: int, end_ts: int) -> List[dict]:
    all_rows = []
    step = period * MAX_CANDLES_PER_REQUEST
    for start in range(start_ts, end_ts + 1, step):
        end = min(start + period * (MAX_CANDLES_PER_REQUEST - 1), end_ts)
        cur = client.get_candlesticks(pair=pair, start_time=start, end_time=end, period=period)
        all_rows.extend(cur)
    return _normalize_api_candles(all_rows, pair, period)


def aggregate_candles(base: List[dict], target_size: int, ticker: str) -> List[dict]:
    if not base:
        return []

    df = pd.DataFrame(base).copy()
    df["bucket_epoch"] = (df["epoch"] // target_size) * target_size
    df.sort_values(["bucket_epoch", "epoch"], inplace=True)
    df["tp"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    df["vwap_x_vol"] = df["vwap"] * df["volume"]

    grouped = df.groupby("bucket_epoch", sort=True)
    agg = grouped.agg(
        open=("open", "first"),
        close=("close", "last"),
        high=("high", "max"),
        low=("low", "min"),
        volume=("volume", "sum"),
        trades=("trades", "sum"),
        twap_1m=("tp", "mean"),
        vwap_x_vol=("vwap_x_vol", "sum"),
        close_mean=("close", "mean"),
    )
    agg["vwap"] = agg["vwap_x_vol"] / agg["volume"]
    agg["vwap"] = agg["vwap"].where(agg["volume"] > 0, agg["close_mean"])
    agg.reset_index(inplace=True)

    result = []
    for row in agg.to_dict(orient="records"):
        epoch = int(row["bucket_epoch"])
        result.append(
            {
                "timestamp": epoch_to_utc_str(epoch),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "open": float(row["open"]),
                "size": target_size,
                "ticker": ticker,
                "epoch": epoch,
                "trades": float(row["trades"]),
                "volume": float(row["volume"]),
                "vwap": float(row["vwap"]),
                "twap_1m": float(row["twap_1m"]),
            }
        )
    return result


def read_existing(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    rows: List[dict] = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "timestamp": row.get("timestamp") or epoch_to_utc_str(_safe_int(row.get("epoch", 0))),
                    "close": _safe_float(row.get("close")),
                    "high": _safe_float(row.get("high")),
                    "low": _safe_float(row.get("low")),
                    "open": _safe_float(row.get("open")),
                    "size": _safe_int(row.get("size")),
                    "ticker": row.get("ticker"),
                    "epoch": _safe_int(row.get("epoch", row.get("timestamp", 0))),
                    "trades": _safe_float(row.get("trades")),
                    "volume": _safe_float(row.get("volume")),
                    "vwap": _safe_float(row.get("vwap")),
                    "twap_1m": _safe_float(row.get("twap_1m"), default=float("nan")),
                }
            )
    return rows


def merge_dedup(existing: List[dict], incoming: List[dict]) -> List[dict]:
    merged = {}
    for row in existing + incoming:
        if not row.get("timestamp"):
            row["timestamp"] = epoch_to_utc_str(row["epoch"])
        merged[row["epoch"]] = row
    return [merged[k] for k in sorted(merged.keys())]


def _decimals_from_first_ohlc(first_row: dict) -> int:
    first_ohlc = max(
        _safe_float(first_row.get("open")),
        _safe_float(first_row.get("high")),
        _safe_float(first_row.get("low")),
        _safe_float(first_row.get("close")),
    )
    if first_ohlc > 10:
        return 2
    if first_ohlc >= 1:
        return 3
    return 5


def _round_decimals(value: float, decimals: int) -> float:
    if value is None:
        return value
    try:
        fval = float(value)
    except Exception:
        return value
    if fval != fval or fval in (float("inf"), float("-inf")):
        return value
    if fval == 0:
        return 0.0
    return round(fval, decimals)


def write_csv(path: str, rows: List[dict], include_twap: bool):
    fields = ["timestamp", "close", "high", "low", "open", "size", "ticker", "epoch", "trades", "volume", "vwap"]
    if include_twap:
        fields.append("twap_1m")
    decimals = _decimals_from_first_ohlc(rows[0]) if rows else 3
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in fields}
            out["volume"] = _round_decimals(out.get("volume"), decimals)
            out["vwap"] = _round_decimals(out.get("vwap"), decimals)
            if include_twap:
                out["twap_1m"] = _round_decimals(out.get("twap_1m"), decimals)
            writer.writerow(out)


def resolve_tickers(client: SFOXTrader, ticker_arg: str) -> List[str]:
    raw = ticker_arg.strip().lower()
    if "," in raw:
        return [normalize_ticker(x) for x in raw.split(",") if x.strip()]
    if raw not in {"all", "majors", "usd", "usdt", "usdc"}:
        return [normalize_ticker(raw)]

    pairs = client.get_currency_pairs()
    symbols = [normalize_ticker(x.get("pair", "")) for x in pairs if x.get("pair")]
    if raw == "all":
        return sorted(set(symbols))
    if raw == "majors":
        majors = {"btcusd", "ethusd", "solusd", "ltcusd"}
        return sorted([p for p in set(symbols) if p in majors])
    suffix = raw
    return sorted([p for p in set(symbols) if p.endswith(suffix)])


def fetch_for_pair(
    client: SFOXTrader,
    pair: str,
    target_size: int,
    append: bool,
    out_dir: str,
    date_start: Optional[str],
    with_twap: bool,
):
    path = make_dirs(target_size, out_dir)
    ofile = os.path.join(path, f"{pair}.csv")
    existing = read_existing(ofile) if append else []
    now = int(datetime.now(timezone.utc).timestamp())
    end_ts = now + target_size * 2

    if append and existing:
        start_ts = max(0, existing[-1]["epoch"] - max(60, target_size * 2))
    else:
        begin_date = date_start or default_start_for_size(target_size)
        start_ts = parse_iso_to_epoch(begin_date)

    if target_size in API_PERIODS:
        candles = fetch_range(client, pair, target_size, start_ts, end_ts)
    else:
        if target_size % 60 != 0:
            raise ValueError(f"Unsupported custom size {target_size}: must be multiple of 60")
        minute = fetch_range(client, pair, 60, start_ts, end_ts)
        candles = aggregate_candles(minute, target_size, pair)

    if with_twap and target_size in API_PERIODS and target_size > 60 and target_size % 60 == 0:
        minute = fetch_range(client, pair, 60, start_ts, end_ts)
        twap_rows = aggregate_candles(minute, target_size, pair)
        twap_map = {r["epoch"]: r.get("twap_1m") for r in twap_rows}
        for row in candles:
            row["twap_1m"] = twap_map.get(row["epoch"])
    elif with_twap and target_size == 60:
        for row in candles:
            row["twap_1m"] = (row["open"] + row["high"] + row["low"] + row["close"]) / 4.0

    merged = merge_dedup(existing, candles) if append else candles
    write_csv(ofile, merged, include_twap=with_twap)
    mode = "appended" if append else "saved"
    print(f"{mode}: {ofile} ({len(merged)} rows)")


def run(args=None):
    args = parse_args(args)
    api_key = os.getenv(args.api_key_env) if args.api_key_env else ""
    client = SFOXTrader(api_key or "")
    tickers = resolve_tickers(client, args.ticker)
    if not tickers:
        sys.exit("No tickers resolved.")
    for pair in tickers:
        fetch_for_pair(
            client=client,
            pair=pair,
            target_size=args.size,
            append=args.append,
            out_dir=args.sdir,
            date_start=args.date_start,
            with_twap=args.twap,
        )
        time.sleep(0.1)


if __name__ == "__main__":
    run()
