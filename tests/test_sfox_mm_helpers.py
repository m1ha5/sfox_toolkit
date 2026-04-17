"""Unit tests for sfox_mm pure helpers (no curses / WebSocket)."""

import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SFOX_MM = os.path.join(_ROOT, "tools", "oba", "sfox_mm.py")


def _load_sfox_mm():
	spec = importlib.util.spec_from_file_location("sfox_mm_mod", _SFOX_MM)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["sfox_mm_mod"] = mod
	assert spec.loader is not None
	spec.loader.exec_module(mod)
	return mod


go = _load_sfox_mm()


def test_pair_base_common_pairs():
	assert go.pair_base("ethusd") == "eth"
	assert go.pair_base("ethusdt") == "eth"
	assert go.pair_base("btcusdc") == "btc"
	assert go.pair_base("BTCUSD") == "btc"


def test_pair_base_unknown():
	assert go.pair_base("xyz") is None
	assert go.pair_base("") is None


def test_diff_base_uniform_error_ok():
	assert go.diff_base_uniform_error(["ethusd", "ethusdt"]) is None
	assert go.diff_base_uniform_error(["ethusd"]) is None


def test_diff_base_uniform_error_mixed():
	msg = go.diff_base_uniform_error(["btcusd", "ethusd"])
	assert msg is not None
	assert "same base" in msg.lower() or "mixed" in msg.lower()


def test_fmt_diff_px():
	assert "+0.0000" in go.fmt_diff_px(1.0, 1.0, 10)
	assert go.fmt_diff_px(None, 1.0, 10).strip() == "—" or "—" in go.fmt_diff_px(None, 1.0, 10)


def test_full_body_line_count():
	fr = go.build_full_rows([1, 5, -1])
	h, has_ask, last_bid = go.full_body_line_count(fr)
	assert has_ask is True
	assert last_bid is not None
	assert h == 3 + 1 + len(fr) + 1  # ASK/MID/BID + ASKS + ladder + BIDS


def test_mirror_bid_bips_if_needed():
	assert -5 in go.mirror_bid_bips_if_needed([1, 5])


def test_build_full_rows_order():
	rows = go.build_full_rows([0, 10, 1, -1])
	kinds = [r[0] for r in rows]
	assert "ask" in kinds
	assert "zero" in kinds
	assert "bid" in kinds
