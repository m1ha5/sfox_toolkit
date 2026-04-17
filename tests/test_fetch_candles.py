import importlib.util
from pathlib import Path


def _load_module():
    mod_path = Path(__file__).resolve().parents[1] / "tools" / "get_candles" / "fetch_candles.py"
    spec = importlib.util.spec_from_file_location("fetch_candles", mod_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_aggregate_candles_builds_4h_and_twap():
    mod = _load_module()
    base = []
    for i in range(60):
        epoch = 13 * 3600 + i * 60
        base.append(
            {
                "timestamp": epoch,
                "epoch": epoch,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 1.0,
                "trades": 1.0,
                "vwap": 100.25 + i,
                "ticker": "btcusd",
                "size": 60,
            }
        )
    rows = mod.aggregate_candles(base, 3600, "btcusd")
    assert len(rows) == 1
    row = rows[0]
    assert row["open"] == 100.0
    assert row["close"] == 159.5
    assert row["high"] == 160.0
    assert row["low"] == 99.0
    expected_twap = sum(((100.0 + i) + (101.0 + i) + (99.0 + i) + (100.5 + i)) / 4.0 for i in range(60)) / 60.0
    assert row["twap_1m"] == expected_twap


def test_merge_dedup_keeps_latest_epoch():
    mod = _load_module()
    existing = [{"epoch": 100, "close": 1.0}, {"epoch": 200, "close": 2.0}]
    incoming = [{"epoch": 200, "close": 22.0}, {"epoch": 300, "close": 3.0}]
    merged = mod.merge_dedup(existing, incoming)
    assert [x["epoch"] for x in merged] == [100, 200, 300]
    assert merged[1]["close"] == 22.0


def test_decimal_policy_from_first_ohlc():
    mod = _load_module()
    assert mod._decimals_from_first_ohlc({"open": 20, "high": 21, "low": 19, "close": 20}) == 2
    assert mod._decimals_from_first_ohlc({"open": 2, "high": 3, "low": 1, "close": 2}) == 3
    assert mod._decimals_from_first_ohlc({"open": 0.2, "high": 0.3, "low": 0.1, "close": 0.2}) == 5


def test_parse_iso_to_epoch_treats_naive_date_as_utc():
    mod = _load_module()
    assert mod.parse_iso_to_epoch("2026-04-01") == 1775001600
