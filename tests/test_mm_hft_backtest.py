"""Tests for top-of-book HFT maker backtester."""

import random
from datetime import datetime, timedelta

from sfox_trader.strats.mm_hft.backtest import BacktestConfig, backtest_top_of_book_strategy


def _sample_candles(n: int = 600):
    ts = datetime(2025, 2, 1)
    rng = random.Random(7)
    price = 3200.0
    candles = []
    for _ in range(n):
        ret = rng.uniform(-0.0012, 0.0012)
        price = price * (1.0 + ret)
        high = price * (1 + rng.uniform(0.0001, 0.0012))
        low = price * (1 - rng.uniform(0.0001, 0.0012))
        volume = rng.uniform(15, 400)
        candles.append(
            {
                "timestamp": ts,
                "close": price,
                "high": high,
                "low": low,
                "volume": volume,
            }
        )
        ts += timedelta(minutes=1)
    return candles


def test_min_order_size_is_enforced():
    candles = _sample_candles(350)
    cfg = BacktestConfig(order_notional_usd=10.0, min_order_notional_usd=50.0)
    res = backtest_top_of_book_strategy(candles, "qsm", cfg, rng_seed=1)
    assert res["avg_order_notional_usd"] >= 50.0


def test_inventory_limits_respected():
    candles = _sample_candles(500)
    cfg = BacktestConfig(
        order_notional_usd=500.0,
        max_long_notional_usd=900.0,
        max_short_notional_usd=500.0,
    )
    res = backtest_top_of_book_strategy(candles, "irm", cfg, rng_seed=2)
    assert res["max_long_notional_usd"] <= 900.0 + 1e-6
    assert res["max_short_notional_usd"] <= 500.0 + 1e-6


def test_rebates_positive_when_volume_is_positive():
    candles = _sample_candles(450)
    cfg = BacktestConfig(order_notional_usd=100.0, maker_rebate_bps=2.0)
    res = backtest_top_of_book_strategy(candles, "qsm", cfg, rng_seed=3)
    assert res["gross_volume_usd"] > 0
    assert res["rebates_usd"] > 0


def test_cross_trend_override_changes_qsm_behavior():
    candles = _sample_candles(420)
    cfg = BacktestConfig(order_notional_usd=120.0)
    ts_map = {c["timestamp"]: 0.01 for c in candles}
    res_self = backtest_top_of_book_strategy(candles, "qsm", cfg, rng_seed=9)
    res_cross = backtest_top_of_book_strategy(
        candles,
        "qsm",
        cfg,
        rng_seed=9,
        trend_override=ts_map,
    )
    assert res_self["net_pnl_usd"] != res_cross["net_pnl_usd"]


def test_exposure_equity_limits_formula_behavior():
    candles = _sample_candles(500)
    cfg = BacktestConfig(
        order_notional_usd=500.0,
        equity_usd=4000.0,
        exposure_usd=5000.0,
        max_long_notional_usd=9000.0,  # equity + exposure
        max_short_notional_usd=5000.0,  # exposure
    )
    res = backtest_top_of_book_strategy(candles, "qsm", cfg, rng_seed=13)
    assert res["max_long_notional_usd"] <= 9000.0 + 1e-6
    assert res["max_short_notional_usd"] <= 5000.0 + 1e-6
