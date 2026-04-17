#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sFOX Market Monitor (``sfox_mm.py``) — multi-asset order book and %%-change grid
(WebSocket + REST for candles).

  summary: one line per symbol (bid, mid, ask, spread, sizes).
  full:    same ladder as orderbook, then a %%-change block (candles + Day Open from ticker) after the bid ladder.
  orderbook: ask/mid/bid and bps ladder only (no %% block); hotkey o.
  changes: standalone %%-change grid (one column per asset, no sizes); rows are %% change from
           candle rows: (current−ref)/ref with ref = last completed bar close for that timeframe; current = ticker last (else mid).
           Day Open row (GMT session open→last) from the ticker WS feed.
           Default candle rows: 1M,5M,15M,1H,4H,6H,1D (override with --candles).
           Candle %% cells are warmed at startup with parallel REST (see --candle-prime-workers);
           Day Open still uses the ticker WebSocket.

Usage (from ``tools/oba/``):
  export SFOX_API_KEY=...
  python3 sfox_mm.py -m summary -a btcusd,ethusd
  python3 sfox_mm.py -m full -a ethusd,ethusdt,ethusdc -b 0,1,5,10 -d
  python3 sfox_mm.py -m changes -a btcusd,ethusd
  python3 sfox_mm.py -m full -a btcusd -b 0,1,5 --candles 1M,5M,1H,1D
  python3 sfox_mm.py ... --theme ./sfox_mm.colors.cfg

From repository root:
  python3 tools/oba/sfox_mm.py -m summary -a btcusd,ethusd

While running: s/f/o/c/d/v/h as below; idle redraw ~0.25s, ~0.1s after a key; summary and full/orderbook use arrow keys to move a highlighted row/column (reverse video); full/orderbook ladder scrolls with j/k and PgUp/Dn; resize supported.

Pairs are base+quote (e.g. btcusd, btcusdt, ethusdc). Listing **both** ``*usd`` and ``*usdc`` for the **same base** (e.g. btcusd + btcusdc) is **not allowed** — sFOX crosses those books; see README. Diff (≥2 assets) requires the same base across pairs (e.g. ethusd, ethusdt, ethusdc); mixed bases like btcusd,ethusd,solusd are rejected.

Requires: websocket-client
"""

from __future__ import annotations

__version__ = "1.54"  # bump minor on each change — see .cursor/rules/sfox-mm-version.mdc

# Shown in curses title bars and help (CLI: ``python3 sfox_mm.py``).
_APP_DISPLAY_TITLE = "sFOX Market Monitor"

import argparse
import curses
import json
import math
import os
import sys
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

_OBA_DIR = os.path.dirname(os.path.abspath(__file__))

from sfox_trader.lib.chartdata_cache import default_cache_root, get_candlesticks_cached
from sfox_trader.lib.pair_utils import pair_base, usd_usdc_cross_book_error
from sfox_trader.lib.sfox_client import SFOXTrader
from sfox_trader.lib.sfox_ws import SFOXWebSocketClient

# ---------------------------------------------------------------------------
# Order book helpers (raw WS shape: best bid / best ask at index 0 per sFOX)
# ---------------------------------------------------------------------------


def _ob_price(line):
	try:
		return float(line[0])
	except (TypeError, ValueError, IndexError):
		return None


def _ob_qty(line):
	if line is None or len(line) < 2:
		return None
	try:
		return float(line[1])
	except (TypeError, ValueError):
		return None


def best_bid_ask(snap):
	bids = snap.get("bids") or []
	asks = snap.get("asks") or []
	if not bids or not asks:
		return None, None, None, None
	bb = _ob_price(bids[0])
	ba = _ob_price(asks[0])
	if bb is None or ba is None:
		return None, None, None, None
	qb = _ob_qty(bids[0]) or 0.0
	qa = _ob_qty(asks[0]) or 0.0
	return bb, ba, qb, qa


def mid_from_snap(snap):
	bb, ba, _, _ = best_bid_ask(snap)
	if bb is None or ba is None:
		return None
	return (bb + ba) / 2.0


def fmt_vol(v, width=10):
	"""Size/volume: three digits after the decimal (e.g. ``130.000``, ``2.061K``) for clearer multi-pair ladders."""
	try:
		x = float(v)
	except (TypeError, ValueError):
		return f"{'?':>{width}}"[:width]
	ax = abs(x)
	sgn = "-" if x < 0 else ""
	if ax == 0:
		s = f"{sgn}0.000"
	elif ax >= 1000:
		s = f"{sgn}{ax / 1000.0:.3f}K"
	else:
		s = f"{sgn}{ax:.3f}"
	if len(s) > width:
		s = s[:width]
	return s.rjust(width)


def _round_sig_figs(x: float, sig: int) -> float:
	"""Round x to ``sig`` significant figures (x != 0)."""
	ax = abs(x)
	e = math.floor(math.log10(ax))
	return math.copysign(round(ax, int(sig - e - 1)), x)


def fmt_px(p, width=12):
	"""
	Format a price for display.

	- ``abs(price) >= 10``: keep the integer part; round only the **fractional**
	  part to 3 significant figures so nearby pairs (e.g. btcusd vs btcusdc) do not
	  collapse to the same string when prices differ by a few dollars.
	- ``abs(price) < 10``: round the **entire** value to 4 significant figures.
	"""
	if p is None:
		return ("?" * min(width, 3))[:width].rjust(width)
	try:
		x = float(p)
	except (TypeError, ValueError):
		return ("?" * min(width, 3))[:width].rjust(width)
	if not math.isfinite(x):
		return ("?" * min(width, 3))[:width].rjust(width)
	if x == 0:
		return "0"[:width].rjust(width)
	ax = abs(x)
	sig = 3 if ax >= 10 else 4
	sign = 1.0 if x >= 0 else -1.0

	if ax < 10:
		val = _round_sig_figs(x, sig)
	else:
		whole = math.floor(ax)
		frac = ax - whole
		if frac < 1e-12:
			val = sign * whole
		else:
			rfrac = _round_sig_figs(frac, sig)
			if rfrac >= 1.0 - 1e-12:
				whole += 1
				rfrac = 0.0
			val = sign * (whole + rfrac)

	s = format(val, ".15g")
	if "e" in s or "E" in s:
		s = format(val, ".8e")
	if s in ("-0", "-0.0"):
		s = "0"
	if "." in s:
		s = s.rstrip("0").rstrip(".")
	return s[:width].rjust(width)


def ask_band_volume(asks, lo_px, hi_px):
	t = 0.0
	for line in asks or []:
		p = _ob_price(line)
		q = _ob_qty(line)
		if p is None or q is None:
			continue
		if lo_px < p <= hi_px:
			t += q
	return t


def bid_band_volume(bids, lo_px, hi_px):
	t = 0.0
	for line in bids or []:
		p = _ob_price(line)
		q = _ob_qty(line)
		if p is None or q is None:
			continue
		if lo_px <= p < hi_px:
			t += q
	return t


def mirror_bid_bips_if_needed(bips_list: list[int]) -> list[int]:
	"""
	If the user gave positive rungs but no negative ones, mirror to the bid side
	(e.g. 1,5,25 -> also -1,-5,-25) so full mode shows a full book.
	"""
	pos = [x for x in bips_list if x > 0]
	neg = [x for x in bips_list if x < 0]
	if pos and not neg:
		return sorted(set(bips_list) | {-p for p in pos})
	return bips_list


def build_full_rows(bips_list):
	"""Return ordered row specs: ('+N'|'-N'|'0', kind, ...) where kind is ask_band|zero|bid_band."""
	pos = sorted({x for x in bips_list if x > 0})
	neg = sorted({x for x in bips_list if x < 0}, key=lambda x: abs(x))  # -1, -5, -10
	has_zero = 0 in bips_list
	rows = []
	# Asks: outer → inner (large +bps first)
	for p in sorted(pos, reverse=True):
		inner = max((x for x in pos if x < p), default=0)
		rows.append(("ask", p, inner))
	if has_zero:
		rows.append(("zero", 0, 0))
	# Bids: inner → outer (-1 then -5 then -10)
	prev_mag = 0
	for n in neg:
		mag = abs(n)
		rows.append(("bid", mag, prev_mag))
		prev_mag = mag
	return rows


def cell_volume_for_row(snap, row, mid):
	"""Volume string only (second line of ladder cell)."""
	if snap is None or mid is None or mid <= 0:
		return "—"
	kind = row[0]
	if kind == "zero":
		return ""
	if kind == "ask":
		p, inner = row[1], row[2]
		asks = snap.get("asks") or []
		lo = mid * (1.0 + inner / 10000.0)
		hi = mid * (1.0 + p / 10000.0)
		v = ask_band_volume(asks, lo, hi)
		return fmt_vol(v, 10)
	if kind == "bid":
		mag, prev_mag = row[1], row[2]
		bids = snap.get("bids") or []
		hi = mid * (1.0 - prev_mag / 10000.0)
		lo = mid * (1.0 - mag / 10000.0)
		v = bid_band_volume(bids, lo, hi)
		return fmt_vol(v, 10)
	return "—"


def cell_price_for_row(snap, row, mid, width: int):
	"""Band-edge price, or mid (bb+ba)/2 on the mid ladder row."""
	if snap is None or mid is None or mid <= 0:
		return "—".rjust(width)[:width]
	kind = row[0]
	if kind == "zero":
		# Mid rung: px = (bb+ba)/2; sz column left blank (sizes belong on bid/ask rows).
		return fmt_px(mid, width)
	if kind == "ask":
		p = row[1]
		hi = mid * (1.0 + p / 10000.0)
		return fmt_px(hi, width)
	if kind == "bid":
		mag = row[1]
		lo = mid * (1.0 - mag / 10000.0)
		return fmt_px(lo, width)
	return "—".rjust(width)[:width]


def cell_price_numeric(snap, row, mid):
	"""Same reference price as ``cell_price_for_row``, as a float (or None)."""
	if snap is None or mid is None or mid <= 0:
		return None
	kind = row[0]
	if kind == "zero":
		return float(mid)
	if kind == "ask":
		p = row[1]
		return mid * (1.0 + p / 10000.0)
	if kind == "bid":
		mag = row[1]
		return mid * (1.0 - mag / 10000.0)
	return None


def fmt_diff_px(v, v0, width=10):
	"""``v - v0`` with four decimal places (first asset column is 0 vs itself)."""
	if v is None or v0 is None:
		return "—".rjust(width)[:width]
	d = float(v) - float(v0)
	if not math.isfinite(d):
		return "—".rjust(width)[:width]
	s = f"{d:+.4f}"
	return s[:width].rjust(width)


def diff_attr_for_delta(v, v0, theme_pairs):
	"""Green for positive diff, bright red for negative, neutral for zero or missing."""
	if v is None or v0 is None:
		return theme_pairs["cell_fg"]
	try:
		d = float(v) - float(v0)
	except (TypeError, ValueError):
		return theme_pairs["cell_fg"]
	if not math.isfinite(d):
		return theme_pairs["cell_fg"]
	if d > 0:
		return theme_pairs["diff_pos_fg"]
	if d < 0:
		return theme_pairs["diff_neg_fg"] | curses.A_BOLD
	return theme_pairs["cell_fg"]


def attr_for_pct(v, theme_pairs):
	"""Color for a signed percent (positive green, negative bold red)."""
	if v is None:
		return theme_pairs["cell_fg"]
	try:
		x = float(v)
	except (TypeError, ValueError):
		return theme_pairs["cell_fg"]
	if not math.isfinite(x):
		return theme_pairs["cell_fg"]
	if x > 0:
		return theme_pairs["diff_pos_fg"]
	if x < 0:
		return theme_pairs["diff_neg_fg"] | curses.A_BOLD
	return theme_pairs["cell_fg"]


def row_label(row):
	kind = row[0]
	if kind == "zero":
		return "MID"
	if kind == "ask":
		return f"+{row[1]}"
	if kind == "bid":
		return f"-{row[1]}"
	return "?"


def full_body_line_count(full_rows: list) -> tuple[int, bool, int | None]:
	"""Lines in full-mode scrollable block: ASK/MID/BID + ASKS + ladder + optional BIDS."""
	has_ask = any(rw[0] == "ask" for rw in full_rows)
	_bidx = [i for i, rw in enumerate(full_rows) if rw[0] == "bid"]
	last_bid_i = _bidx[-1] if _bidx else None
	n = 3 + len(full_rows)
	if has_ask:
		n += 1
	if last_bid_i is not None:
		n += 1
	return n, has_ask, last_bid_i


def ladder_total_body_lines(full_rows: list, with_changes_tail: bool, n_change_rows: int) -> int:
	"""Total scrollable body lines including optional %% block (blank + %% header + rows)."""
	body_h, _, _ = full_body_line_count(full_rows)
	tail = (2 + n_change_rows) if with_changes_tail else 0
	return body_h + tail


def ladder_scroll_metrics(
	max_y: int,
	show_feed_speed: bool,
	full_rows: list,
	with_changes_tail: bool,
	n_change_rows: int,
) -> tuple[int, int, int]:
	"""``(max_scroll, vheight, total_body_h)`` for full/orderbook pad scrolling + focus."""
	fr = _footer_rows_for(max_y, show_feed_speed)
	total_body_h = ladder_total_body_lines(full_rows, with_changes_tail, n_change_rows)
	vheight = max(1, max_y - fr - 3)
	max_scroll = max(0, total_body_h - vheight)
	return max_scroll, vheight, total_body_h


def _focus_hl(attr: int, on: bool) -> int:
	return attr | (curses.A_REVERSE if on else 0)


def _summary_field_specs(diff_rest: bool, w_diff: int) -> list[tuple[int, int]]:
	"""``(x, width)`` for each logical field: sym, bid, mid, ask, sprd, bidSz, askSz [, diff]."""
	x = 0
	out: list[tuple[int, int]] = []
	out.append((x, 10))
	x += 10 + 1
	out.append((x, 14))
	x += 14 + 1
	out.append((x, 14))
	x += 14 + 1
	out.append((x, 14))
	x += 14 + 1
	out.append((x, 10))
	x += 10 + 1
	out.append((x, 10))
	x += 10 + 1
	out.append((x, 10))
	x += 10 + 1
	if diff_rest:
		out.append((x, w_diff))
	return out


def _ref_cell_ask(snap, w: int) -> tuple[str, float | None]:
	"""One ``best_bid_ask`` call: formatted ask px and numeric value for diff."""
	if not snap:
		return ("—".rjust(w)[:w], None)
	bba = best_bid_ask(snap)
	if not bba or bba[1] is None:
		return ("—".rjust(w)[:w], None)
	ba = bba[1]
	return (fmt_px(ba, w), float(ba))


def _ref_cell_mid(snap, w: int) -> tuple[str, float | None]:
	if not snap:
		return ("—".rjust(w)[:w], None)
	m = mid_from_snap(snap)
	if m is None:
		return ("—".rjust(w)[:w], None)
	return (fmt_px(m, w), float(m))


def _ref_cell_bid(snap, w: int) -> tuple[str, float | None]:
	if not snap:
		return ("—".rjust(w)[:w], None)
	bba = best_bid_ask(snap)
	if not bba or bba[0] is None:
		return ("—".rjust(w)[:w], None)
	bb = bba[0]
	return (fmt_px(bb, w), float(bb))


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_COLOR_NAMES = {
	"black": curses.COLOR_BLACK,
	"red": curses.COLOR_RED,
	"green": curses.COLOR_GREEN,
	"yellow": curses.COLOR_YELLOW,
	"blue": curses.COLOR_BLUE,
	"magenta": curses.COLOR_MAGENTA,
	"cyan": curses.COLOR_CYAN,
	"white": curses.COLOR_WHITE,
}

_DEFAULT_THEME = {
	"header_fg": "cyan",
	"title_fg": "white",
	"label_fg": "yellow",
	"ask_fg": "red",
	"bid_fg": "yellow",
	"mid_fg": "cyan",
	"zero_row_fg": "white",
	"cell_fg": "white",
	"hint_fg": "blue",
	"diff_pos_fg": "green",
	"diff_neg_fg": "red",
}


def load_theme(path: str | None) -> dict:
	base = dict(_DEFAULT_THEME)
	candidates = []
	if path:
		candidates.append(path)
	candidates.append(os.path.join(_OBA_DIR, "sfox_mm.colors.cfg"))
	candidates.append(os.path.join(_OBA_DIR, "grid-ob.colors.cfg"))  # legacy filename
	for p in candidates:
		if p and os.path.isfile(p):
			try:
				with open(p, "r", encoding="utf-8") as f:
					raw = json.load(f)
				_skip_meta = frozenset({"comment", "grid_ob_version", "sfox_mm_version"})
				for k, v in raw.items():
					if k in _skip_meta or not isinstance(v, str):
						continue
					base[k] = v.lower()
			except (OSError, json.JSONDecodeError):
				pass
			break
	return base


def init_theme_colors(theme: dict):
	"""Map theme keys to curses color_pair(1..). Returns dict attr_name -> pair int."""
	curses.start_color()
	try:
		curses.use_default_colors()
		bg = -1
	except curses.error:
		bg = curses.COLOR_BLACK

	pairs = {}
	idx = 1
	for key in (
		"header_fg",
		"title_fg",
		"label_fg",
		"ask_fg",
		"bid_fg",
		"mid_fg",
		"zero_row_fg",
		"cell_fg",
		"hint_fg",
		"diff_pos_fg",
		"diff_neg_fg",
	):
		fg = _COLOR_NAMES.get(str(theme.get(key, "white")).lower(), curses.COLOR_WHITE)
		curses.init_pair(idx, fg, bg)
		pairs[key] = curses.color_pair(idx)
		idx += 1
	return pairs


# ---------------------------------------------------------------------------
# Draw
# ---------------------------------------------------------------------------

# Footer fragment reused on summary and full views (single place to edit hotkey hints).
_HINT_KEYS = "q quit | s summary | f full | o orderbook | c changes | d diff | v feed speed | h help"
_FOCUS_HINT_SUMMARY = " | arrows move cell focus"
_FOCUS_HINT_LADDER = " | arrows move focus (j/k PgUp/Dn scroll)"


def _title_bar(mode: str, max_x: int, max_y: int) -> str:
	"""Window title: app name, version, terminal size, view mode (``mode`` e.g. FULL, HELP)."""
	return f"{_APP_DISPLAY_TITLE}  v{__version__}  |  Terminal Size: {max_x}x{max_y}  |  {mode}"


def _footer_rows(max_y: int) -> int:
	"""Reserved bottom rows: feed speed line, optional GMT clock, hints."""
	if max_y >= 5:
		return 3
	if max_y >= 4:
		return 2
	return 1


def _footer_rows_for(max_y: int, show_feed_speed: bool) -> int:
	"""Footer height: when feed speed is off, omit that row (more space for body)."""
	if show_feed_speed:
		return _footer_rows(max_y)
	if max_y >= 4:
		return 2
	return 1


def _ordinal_day(n: int) -> str:
	if 10 <= (n % 100) <= 13:
		return f"{n}th"
	return f"{n}{['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]}"


def gmt_clock_line(max_len=None) -> str:
	"""e.g. ``Friday, April 3rd, 13:33:01 GMT`` (UTC)."""
	dt = datetime.now(timezone.utc)
	s = f"{dt.strftime('%A, %B')} {_ordinal_day(dt.day)}, {dt.strftime('%H:%M:%S')} GMT"
	if max_len is not None:
		s = s[: max(0, max_len)]
	return s


def feed_latency_footer_text(ws, assets, max_len: int) -> str:
	"""
	One-line ``feed speed:`` summary: per-asset |clock offset| in ms between local wall
	time and ``lastPublished`` (p) / ``lastUpdated`` (u) from the order-book WS meta.

	Uses ``abs(now - ts)`` so small NTP skew (server slightly ahead of client) does not
	show as misleading ``p0 u0``; the value is still a useful “how far apart are these
	clocks / how fresh is this stamp” scale.
	"""
	now_ms = int(time.time() * 1000)
	chunks = []
	for sym in assets:
		meta = ws.get_order_book_feed_meta(sym)
		if not meta:
			chunks.append(f"{sym.upper()} —")
			continue
		pub = meta.get("lastPublished")
		upd = meta.get("lastUpdated")
		d_pub = None
		d_upd = None
		if pub is not None:
			try:
				d_pub = abs(now_ms - int(pub))
			except (TypeError, ValueError):
				pass
		if upd is not None:
			try:
				d_upd = abs(now_ms - int(upd))
			except (TypeError, ValueError):
				pass
		if d_pub is not None and d_upd is not None:
			chunks.append(f"{sym.upper()} p{d_pub} u{d_upd}")
		elif d_pub is not None:
			chunks.append(f"{sym.upper()} p{d_pub}")
		elif d_upd is not None:
			chunks.append(f"{sym.upper()} u{d_upd}")
		else:
			chunks.append(f"{sym.upper()} —")
	s = "feed speed: " + " | ".join(chunks)
	return s[: max(0, max_len)]


_HELP_SECTION_TITLES = frozenset(
	{
		"  " + _APP_DISPLAY_TITLE,
		"Hotkeys",
		"Change table (% rows)",
		"Full / orderbook scroll (when taller than the window):",
	}
)


def draw_help(stdscr, theme_pairs):
	"""Full-screen hotkey reference (toggle with h or ?)."""
	max_y, max_x = stdscr.getmaxyx()
	stdscr.erase()
	stdscr.addstr(
		0,
		0,
		_title_bar("HELP", max_x, max_y)[: max_x - 1],
		theme_pairs["title_fg"] | curses.A_BOLD,
	)
	lines = [
		"",
		f"  {_APP_DISPLAY_TITLE}",
		"",
		"Hotkeys",
		"",
		"  q        Quit",
		"  s        Summary view (bid, mid, ask, spread, sizes per symbol)",
		"  f        Full ladder + % change table after bids (scroll if needed)",
		"  o        Order book ladder only (no %% table)",
		"  c        Changes-only grid (same % table as full, without ladder)",
		"  d        Toggle diff vs first asset (needs ≥2 symbols, same base)",
		"  v        Toggle feed speed line (order-book p/u ms; off by default)",
		"  h  or  ?   This help (press again to close)",
		"  Esc      Close help",
		"",
		"Change table (% rows)",
		"",
		"  Day Open — WebSocket ticker only (not chartdata). Formula:",
		"    (last - open) / open × 100.  open = session open at 00:00 GMT;",
		"    last = latest aggregated trade (sFOX ticker.sfox.<pair>).",
		"",
		"  All other rows — REST candles (chartdata.sfox.com) plus live price:",
		"    (current - ref) / ref × 100.",
		"    current = ticker last if available, else WebSocket order-book mid.",
		"    ref = a prior bar close, defined per row below.",
		"",
		"  1M, 5M, 15M, 1H, 6H, 1D — ref = close of the last *completed* candle at that",
		"    period (second-to-last bar; newest may still be open). 6H = 6-hour bars",
		"    (API 21600s). Default row list: 1M,5M,15M,1H,4H,6H,1D.",
		"",
		"  --candles  Override candle rows (after Day Open), e.g. --candles 1M,5M,1H,1D.",
		"    Supported tokens: 1M, 5M, 15M, 1H, 4H, 6H, 1D, 3D, 7D (sFOX chartdata).",
		"",
		"  4H — No native 4h period on the API. ref = hourly close from *four*",
		"    hours before the last completed hour (1h bars; rolling, not a fixed",
		"    UTC session grid).",
		"",
		"  3D, 7D — Optional (via --candles). Same daily logic: ref = close N completed",
		"    dailies before the latest completed daily (N=3 or 7).",
		"",
		"  Gaps — If an interval had no trades, that candle is omitted; \"days\"",
		"    are steps along returned dailies, not always exact calendar spans.",
		"",
		"Full / orderbook scroll (when taller than the window):",
		"",
		"  j / k            Scroll one line",
		"  PgUp / PgDn      Scroll one page",
		"  Arrow keys       Move highlight (summary: row=asset, col=field; full/orderbook: row/ col=asset)",
		"",
		"  Resizing the terminal is picked up on the next frame (KEY_RESIZE).",
		"",
		"Diff compares each column to the first; with -d or d, mixed bases",
		"(e.g. btcusd,ethusd,solusd) are rejected at startup or when enabling diff.",
	]
	y = 1
	hd = theme_pairs["header_fg"] | curses.A_BOLD
	for i, line in enumerate(lines):
		if y >= max_y - 1:
			break
		attr = hd if line in _HELP_SECTION_TITLES else theme_pairs["cell_fg"]
		stdscr.addstr(y, 0, line[: max_x - 1], attr)
		y += 1
	stdscr.addstr(
		max_y - 1,
		0,
		"h or ? or Esc close help | q quit"[: max_x - 1],
		theme_pairs["hint_fg"],
	)
	stdscr.refresh()


def draw_summary(
	stdscr,
	assets,
	ws,
	theme_pairs,
	show_diff=False,
	footer_error=None,
	show_feed_speed=False,
	focus_asset_i: int = 0,
	focus_field_i: int = 0,
):
	max_y, max_x = stdscr.getmaxyx()
	diff_rest = show_diff and len(assets) >= 2
	w_diff = 10 if diff_rest else 0
	n_summary_fields = len(_summary_field_specs(diff_rest, w_diff))
	stdscr.erase()
	title = _title_bar("SUMMARY", max_x, max_y)
	stdscr.addstr(0, 0, title[: max_x - 1], theme_pairs["title_fg"] | curses.A_BOLD)
	hd = theme_pairs["header_fg"] | curses.A_BOLD
	xh = 0
	stdscr.addstr(1, xh, f"{'sym':<10}"[: max_x - 1], hd)
	xh += 10
	stdscr.addstr(1, xh, " ", hd)
	xh += 1
	stdscr.addstr(1, xh, f"{'bid':>14}"[: max_x - xh - 1], theme_pairs["bid_fg"] | curses.A_BOLD)
	xh += 14
	stdscr.addstr(1, xh, " ", hd)
	xh += 1
	stdscr.addstr(1, xh, f"{'mid':>14}"[: max_x - xh - 1], theme_pairs["mid_fg"] | curses.A_BOLD)
	xh += 14
	stdscr.addstr(1, xh, " ", hd)
	xh += 1
	stdscr.addstr(1, xh, f"{'ask':>14}"[: max_x - xh - 1], theme_pairs["ask_fg"] | curses.A_BOLD)
	xh += 14
	stdscr.addstr(1, xh, " ", hd)
	xh += 1
	stdscr.addstr(1, xh, f"{'sprd':>10}"[: max_x - xh - 1], hd)
	xh += 10
	stdscr.addstr(1, xh, " ", hd)
	xh += 1
	stdscr.addstr(1, xh, f"{'bidSz':>10}"[: max_x - xh - 1], hd)
	xh += 10
	stdscr.addstr(1, xh, " ", hd)
	xh += 1
	stdscr.addstr(1, xh, f"{'askSz':>10}"[: max_x - xh - 1], hd)
	xh += 10
	if diff_rest:
		stdscr.addstr(1, xh, " ", hd)
		xh += 1
		stdscr.addstr(1, xh, f"{'diff':>{w_diff}}"[: max_x - xh - 1], theme_pairs["hint_fg"])
	row = 2
	bid_px_a = theme_pairs["bid_fg"] | curses.A_BOLD
	ask_px_a = theme_pairs["ask_fg"]
	mid_px_a = theme_pairs["mid_fg"] | curses.A_BOLD
	fr = _footer_rows_for(max_y, show_feed_speed)
	snap0 = ws.get_order_book_snapshot(assets[0]) if diff_rest and assets else None
	m0_mid = mid_from_snap(snap0) if snap0 else None

	def _sf(i: int, fidx: int, attr: int) -> int:
		return _focus_hl(attr, i == focus_asset_i and fidx == focus_field_i and n_summary_fields > 0)

	fi_sym, fi_bid, fi_mid, fi_ask, fi_sprd, fi_bsz, fi_asz = 0, 1, 2, 3, 4, 5, 6
	fi_diff = 7
	for i, sym in enumerate(assets):
		if row >= max_y - fr:
			break
		snap = ws.get_order_book_snapshot(sym)
		bb, ba, qb, qa = best_bid_ask(snap) if snap else (None, None, None, None)
		if bb is None:
			xc = 0
			stdscr.addstr(row, xc, f"{sym:<10}"[: max_x - xc - 1], _sf(i, fi_sym, theme_pairs["hint_fg"]))
			xc += 10
			stdscr.addstr(row, xc, " ", theme_pairs["hint_fg"])
			xc += 1
			for fidx in (fi_bid, fi_mid, fi_ask):
				stdscr.addstr(row, xc, f"{'…':>14}"[: max_x - xc - 1], _sf(i, fidx, theme_pairs["hint_fg"]))
				xc += 14
				stdscr.addstr(row, xc, " ", theme_pairs["hint_fg"])
				xc += 1
			for fidx in (fi_sprd, fi_bsz, fi_asz):
				stdscr.addstr(row, xc, f"{'…':>10}"[: max_x - xc - 1], _sf(i, fidx, theme_pairs["hint_fg"]))
				xc += 10
				stdscr.addstr(row, xc, " ", theme_pairs["hint_fg"])
				xc += 1
			if diff_rest:
				stdscr.addstr(
					row,
					xc,
					(f"{'…':>{w_diff}}" if i > 0 else f"{' ':>{w_diff}}")[: max_x - xc - 1],
					_sf(i, fi_diff, theme_pairs["hint_fg"]),
				)
		else:
			sp = ba - bb
			mid_px = mid_from_snap(snap)
			xc = 0
			stdscr.addstr(row, xc, f"{sym:<10}"[: max_x - 1], _sf(i, fi_sym, theme_pairs["cell_fg"]))
			xc += 10
			stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
			xc += 1
			stdscr.addstr(row, xc, fmt_px(bb, 14)[: max_x - xc - 1], _sf(i, fi_bid, bid_px_a))
			xc += 14
			stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
			xc += 1
			if mid_px is None:
				stdscr.addstr(row, xc, f"{'…':>14}"[: max_x - xc - 1], _sf(i, fi_mid, theme_pairs["hint_fg"]))
			else:
				stdscr.addstr(row, xc, fmt_px(mid_px, 14)[: max_x - xc - 1], _sf(i, fi_mid, mid_px_a))
			xc += 14
			stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
			xc += 1
			stdscr.addstr(row, xc, fmt_px(ba, 14)[: max_x - xc - 1], _sf(i, fi_ask, ask_px_a))
			xc += 14
			stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
			xc += 1
			stdscr.addstr(row, xc, f"{sp:>10.4g}"[: max_x - xc - 1], _sf(i, fi_sprd, theme_pairs["cell_fg"]))
			xc += 10
			stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
			xc += 1
			stdscr.addstr(row, xc, fmt_vol(qb, 10)[: max_x - xc - 1], _sf(i, fi_bsz, theme_pairs["cell_fg"]))
			xc += 10
			stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
			xc += 1
			stdscr.addstr(row, xc, fmt_vol(qa, 10)[: max_x - xc - 1], _sf(i, fi_asz, theme_pairs["cell_fg"]))
			xc += 10
			if diff_rest:
				stdscr.addstr(row, xc, " ", theme_pairs["cell_fg"])
				xc += 1
				if i == 0:
					bl = "".rjust(w_diff)[: max_x - xc - 1]
					stdscr.addstr(row, xc, bl, _sf(i, fi_diff, theme_pairs["cell_fg"]))
				else:
					mid = mid_from_snap(snap)
					stdscr.addstr(
						row,
						xc,
						fmt_diff_px(mid, m0_mid, w_diff)[: max_x - xc - 1],
						_sf(i, fi_diff, diff_attr_for_delta(mid, m0_mid, theme_pairs)),
					)
		row += 1
	if max_y >= 5:
		if show_feed_speed:
			stdscr.addstr(
				max_y - 3,
				0,
				feed_latency_footer_text(ws, assets, max_x - 1)[: max_x - 1],
				theme_pairs["hint_fg"],
			)
		stdscr.addstr(
			max_y - 2,
			0,
			gmt_clock_line(max_x - 1),
			theme_pairs["hint_fg"],
		)
	elif max_y >= 4:
		if show_feed_speed:
			stdscr.addstr(
				max_y - 2,
				0,
				feed_latency_footer_text(ws, assets, max_x - 1)[: max_x - 1],
				theme_pairs["hint_fg"],
			)
		else:
			stdscr.addstr(
				max_y - 2,
				0,
				gmt_clock_line(max_x - 1),
				theme_pairs["hint_fg"],
			)
	err_a = theme_pairs["diff_neg_fg"] | curses.A_BOLD
	if footer_error:
		stdscr.addstr(
			max_y - 1,
			0,
			f"diff: {footer_error}"[: max_x - 1],
			err_a,
		)
	else:
		hx = _HINT_KEYS + _FOCUS_HINT_SUMMARY
		if show_feed_speed:
			hx += " | feed speed p/u = |ms − WS stamp| (pub / upd)"
		stdscr.addstr(max_y - 1, 0, hx[: max_x - 1], theme_pairs["hint_fg"])
	stdscr.refresh()


def _paint_ladder_changes_tail(
	win,
	r: int,
	row_lim: int | None,
	col0: int,
	w_px: int,
	w_vol: int,
	gap: int,
	assets: list,
	fetcher,
	theme_pairs: dict,
	max_x: int,
	diff_rest: bool,
	w_diff: int,
	changes_row_defs: list[dict],
	body_br: int,
	focus_asset_i: int,
	focus_body_i: int,
) -> tuple[int, int]:
	"""
	One blank pad row (skipped by leading ``r += 1``), then %%Δ header row, then change rows.
	``body_br`` = body index of that blank separator (= ladder line count before tail).
	Returns ``(r, next_body_br)``.
	"""
	w_block = w_px + gap + w_vol

	def _hl(i: int, row_idx: int, attr: int) -> int:
		return _focus_hl(attr, i == focus_asset_i and row_idx == focus_body_i and len(assets) > 0)

	if row_lim is not None and r >= row_lim:
		return r, body_br
	r += 1
	if row_lim is not None and r >= row_lim:
		return r, body_br
	br = body_br + 1
	win.addstr(r, 0, f"{'':<{col0}}"[: max_x - 1], theme_pairs["hint_fg"])
	xc = col0
	for i, _ in enumerate(assets):
		if xc >= max_x - 1:
			break
		win.addstr(r, xc, f"{'%Δ':>{w_block}}"[: max_x - xc - 1], _hl(i, br, theme_pairs["hint_fg"]))
		xc += w_block
		if diff_rest and w_diff and i > 0:
			if xc >= max_x - 1:
				break
			xc += gap
			win.addstr(r, xc, " " * min(w_diff, max_x - xc - 1), _hl(i, br, theme_pairs["hint_fg"]))
			xc += w_diff
	r += 1
	br += 1
	for rd in changes_row_defs:
		if row_lim is not None and r >= row_lim:
			break
		win.addstr(r, 0, f"{rd['label']:<{col0}}"[: max_x - 1], theme_pairs["label_fg"])
		xc = col0
		for i, sym in enumerate(assets):
			if xc >= max_x - 1:
				break
			pv = fetcher.pct(sym, rd)
			txt = _fmt_pct_cell(pv, w_block)
			win.addstr(r, xc, txt[: max_x - xc - 1], _hl(i, br, attr_for_pct(pv, theme_pairs)))
			xc += w_block
			if diff_rest and w_diff and i > 0:
				if xc >= max_x - 1:
					break
				xc += gap
				win.addstr(r, xc, " " * min(w_diff, max_x - xc - 1), _hl(i, br, theme_pairs["cell_fg"]))
				xc += w_diff
		r += 1
		br += 1
	return r, br


def _draw_ladder_view(
	stdscr,
	assets,
	ws,
	full_rows,
	theme_pairs,
	show_diff=False,
	footer_error=None,
	scroll_y: int = 0,
	title: str = "FULL",
	fetcher=None,
	changes_row_defs: list[dict] | None = None,
	show_feed_speed: bool = False,
	focus_asset_i: int = 0,
	focus_body_i: int = 0,
) -> int:
	"""Reference ask/mid/bid, then ladder rows; optional %% block after bids (full mode). Returns clamped scroll offset."""
	max_y, max_x = stdscr.getmaxyx()
	fr = _footer_rows_for(max_y, show_feed_speed)
	stdscr.erase()
	stdscr.addstr(
		0,
		0,
		_title_bar(title, max_x, max_y)[: max_x - 1],
		theme_pairs["title_fg"] | curses.A_BOLD,
	)
	col0 = 8
	w_vol = 10
	w_px = max(13, max((len(s) for s in assets), default=6) + 3)
	gap = 1
	diff_rest = show_diff and len(assets) >= 2
	w_diff = 10 if diff_rest else 0

	def _asset_block_w(i: int) -> int:
		w = w_px + gap + w_vol
		if diff_rest and i > 0:
			w += gap + w_diff
		return w

	head_sym = f"{'bps':<{col0}}"
	for i, sym in enumerate(assets):
		bw = _asset_block_w(i)
		label = sym.upper()
		if len(label) > bw:
			label = label[:bw]
		else:
			label = label.center(bw)
		head_sym += label
	stdscr.addstr(1, 0, head_sym[: max_x - 1], theme_pairs["header_fg"] | curses.A_BOLD)

	head_sub = f"{'':<{col0}}"
	for i, _ in enumerate(assets):
		head_sub += f"{'px':>{w_px}}{' ' * gap}{'sz':>{w_vol}}"
		if diff_rest and i > 0:
			head_sub += f"{' ' * gap}{'diff':>{w_diff}}"
	stdscr.addstr(2, 0, head_sub[: max_x - 1], theme_pairs["hint_fg"])

	body_h, has_ask, last_bid_i = full_body_line_count(full_rows)
	_cr = changes_row_defs if fetcher is not None else None
	changes_tail = (2 + len(_cr)) if _cr is not None else 0
	total_body_h = body_h + changes_tail
	vheight = max(1, max_y - fr - 3)
	max_scroll = max(0, total_body_h - vheight)
	scy = max(0, min(scroll_y, max_scroll))

	snap0 = ws.get_order_book_snapshot(assets[0]) if diff_rest and assets else None
	s0 = snap0
	m0 = mid_from_snap(s0) if s0 else None

	ask_a = theme_pairs["ask_fg"]
	ask_ab = theme_pairs["ask_fg"] | curses.A_BOLD
	bid_a = theme_pairs["bid_fg"]
	bid_ab = theme_pairs["bid_fg"] | curses.A_BOLD
	mid_a = theme_pairs["mid_fg"]
	mid_ab = theme_pairs["mid_fg"] | curses.A_BOLD
	n_assets = len(assets)

	def _col_hl(i: int, body_row: int, attr: int) -> int:
		return _focus_hl(
			attr,
			n_assets > 0 and i == focus_asset_i and body_row == focus_body_i,
		)

	def draw_ref_line(
		win,
		y,
		label,
		attr_label,
		cell_attr,
		ref_cell,
		left_align_label=False,
		row_lim=None,
		body_row_idx: int = 0,
	):
		if row_lim is not None and y >= row_lim:
			return y
		if left_align_label:
			win.addstr(y, 0, f"{label:<{col0}}"[: max_x - 1], attr_label)
		else:
			win.addstr(y, 0, f"{label:>{col0}}"[: max_x - 1], attr_label)
		v0 = ref_cell(snap0, w_px)[1] if snap0 else None
		xc = col0
		for i, sym in enumerate(assets):
			if xc >= max_x - 1:
				break
			snap = ws.get_order_book_snapshot(sym)
			px_text, v = ref_cell(snap, w_px)
			win.addstr(y, xc, px_text[: max_x - xc - 1], _col_hl(i, body_row_idx, cell_attr))
			xc += w_px
			if xc >= max_x - 1:
				break
			xc += gap
			if xc >= max_x - 1:
				break
			blanks = " " * min(w_vol, max(0, max_x - xc - 1))
			win.addstr(y, xc, blanks, _col_hl(i, body_row_idx, cell_attr))
			xc += w_vol
			if diff_rest and w_diff and i > 0:
				if xc >= max_x - 1:
					break
				xc += gap
				ds = fmt_diff_px(v, v0, w_diff)
				da = diff_attr_for_delta(v, v0, theme_pairs)
				win.addstr(y, xc, ds[: max_x - xc - 1], _col_hl(i, body_row_idx, da))
				xc += w_diff
		return y + 1

	def paint_body(win, r, row_lim):
		br = 0
		r = draw_ref_line(win, r, "ASK", ask_ab, ask_a, _ref_cell_ask, False, row_lim, br)
		br += 1
		r = draw_ref_line(
			win,
			r,
			"MID",
			mid_ab,
			mid_a,
			_ref_cell_mid,
			True,
			row_lim,
			br,
		)
		br += 1
		r = draw_ref_line(win, r, "BID", bid_ab, bid_ab, _ref_cell_bid, False, row_lim, br)
		br += 1
		if has_ask and (row_lim is None or r < row_lim):
			win.addstr(r, 0, "ASKS", ask_ab)
			r += 1
			br += 1
		for idx, row in enumerate(full_rows):
			if row_lim is not None and r >= row_lim:
				break
			kind = row[0]
			lbl = row_label(row)
			lattr = theme_pairs["label_fg"]
			px_attr = theme_pairs["cell_fg"]
			if kind == "ask":
				lattr = ask_ab
				px_attr = ask_a
			elif kind == "bid":
				lattr = bid_ab
				px_attr = bid_ab
			elif kind == "zero":
				lattr = theme_pairs["zero_row_fg"] | curses.A_BOLD
			if kind == "zero":
				win.addstr(r, 0, f"{lbl:<{col0}}"[: max_x - 1], lattr)
			else:
				win.addstr(r, 0, f"{lbl:>6}", lattr)
			v0 = cell_price_numeric(s0, row, m0)
			xc = col0
			for i, sym in enumerate(assets):
				if xc >= max_x - 1:
					break
				snap = ws.get_order_book_snapshot(sym)
				mid = mid_from_snap(snap) if snap else None
				px_cell = cell_price_for_row(snap, row, mid, w_px)
				win.addstr(r, xc, px_cell[: max_x - xc - 1], _col_hl(i, br, px_attr))
				xc += w_px
				if xc >= max_x - 1:
					break
				xc += gap
				if xc >= max_x - 1:
					break
				vol_raw = cell_volume_for_row(snap, row, mid)
				vol_show = (vol_raw[:w_vol] if len(vol_raw) > w_vol else vol_raw).rjust(w_vol)
				win.addstr(r, xc, vol_show[: max_x - xc - 1], _col_hl(i, br, theme_pairs["cell_fg"]))
				xc += w_vol
				if diff_rest and w_diff and i > 0:
					if xc >= max_x - 1:
						break
					xc += gap
					v = cell_price_numeric(snap, row, mid)
					win.addstr(
						r,
						xc,
						fmt_diff_px(v, v0, w_diff)[: max_x - xc - 1],
						_col_hl(i, br, diff_attr_for_delta(v, v0, theme_pairs)),
					)
					xc += w_diff
			r += 1
			br += 1
			if last_bid_i is not None and idx == last_bid_i and (row_lim is None or r < row_lim):
				win.addstr(r, 0, "BIDS", bid_ab)
				r += 1
				br += 1
		if fetcher is not None and changes_row_defs is not None and (row_lim is None or r < row_lim):
			r, _ = _paint_ladder_changes_tail(
				win,
				r,
				row_lim,
				col0,
				w_px,
				w_vol,
				gap,
				assets,
				fetcher,
				theme_pairs,
				max_x,
				diff_rest,
				w_diff,
				changes_row_defs,
				br,
				focus_asset_i,
				focus_body_i,
			)
		return r

	if total_body_h <= vheight:
		paint_body(stdscr, 3, max_y - fr)
	else:
		try:
			body_pad = curses.newpad(max(total_body_h, 1), max(max_x, 1))
		except curses.error:
			paint_body(stdscr, 3, max_y - fr)
		else:
			paint_body(body_pad, 0, None)
			try:
				body_pad.refresh(scy, 0, 3, 0, max_y - fr - 1, max_x - 1)
			except curses.error:
				pass

	if max_y >= 5:
		if show_feed_speed:
			stdscr.addstr(
				max_y - 3,
				0,
				feed_latency_footer_text(ws, assets, max_x - 1)[: max_x - 1],
				theme_pairs["hint_fg"],
			)
		stdscr.addstr(
			max_y - 2,
			0,
			gmt_clock_line(max_x - 1),
			theme_pairs["hint_fg"],
		)
	elif max_y >= 4:
		if show_feed_speed:
			stdscr.addstr(
				max_y - 2,
				0,
				feed_latency_footer_text(ws, assets, max_x - 1)[: max_x - 1],
				theme_pairs["hint_fg"],
			)
		else:
			stdscr.addstr(
				max_y - 2,
				0,
				gmt_clock_line(max_x - 1),
				theme_pairs["hint_fg"],
			)
	err_a = theme_pairs["diff_neg_fg"] | curses.A_BOLD
	if footer_error:
		stdscr.addstr(max_y - 1, 0, f"diff: {footer_error}"[: max_x - 1], err_a)
	else:
		scroll_hint = " | j/k PgUp/Dn scroll" if max_scroll > 0 else ""
		if fetcher is not None:
			hint = (
				f"{_HINT_KEYS}{_FOCUS_HINT_LADDER}{scroll_hint} | ladder then %%chg after bids | px=outer-edge or mid; "
				"sz=band qty (mid: sz blank) | diff=px−px₀ (cols 2+)"
			)
		else:
			hint = (
				f"{_HINT_KEYS}{_FOCUS_HINT_LADDER}{scroll_hint} | px=outer-edge or mid; sz=band qty (mid row: sz blank) | "
				"diff=px−px₀ (cols 2+)"
			)
		if show_feed_speed:
			hint += " | feed speed p/u"
		stdscr.addstr(max_y - 1, 0, hint[: max_x - 1], theme_pairs["hint_fg"])
	stdscr.refresh()
	return scy


def draw_orderbook(
	stdscr,
	assets,
	ws,
	full_rows,
	theme_pairs,
	show_diff=False,
	footer_error=None,
	scroll_y: int = 0,
	show_feed_speed: bool = False,
	focus_asset_i: int = 0,
	focus_body_i: int = 0,
) -> int:
	"""Ladder only (same as former full view without the embedded %% table)."""
	return _draw_ladder_view(
		stdscr,
		assets,
		ws,
		full_rows,
		theme_pairs,
		show_diff,
		footer_error,
		scroll_y,
		title="ORDERBOOK",
		fetcher=None,
		changes_row_defs=None,
		show_feed_speed=show_feed_speed,
		focus_asset_i=focus_asset_i,
		focus_body_i=focus_body_i,
	)


def draw_full(
	stdscr,
	assets,
	ws,
	full_rows,
	theme_pairs,
	fetcher,
	changes_row_defs: list[dict],
	show_diff=False,
	footer_error=None,
	scroll_y: int = 0,
	show_feed_speed: bool = False,
	focus_asset_i: int = 0,
	focus_body_i: int = 0,
) -> int:
	"""Ladder plus %% change block after the bid section (scroll to see it if the window is short)."""
	return _draw_ladder_view(
		stdscr,
		assets,
		ws,
		full_rows,
		theme_pairs,
		show_diff,
		footer_error,
		scroll_y,
		title="FULL",
		fetcher=fetcher,
		changes_row_defs=changes_row_defs,
		show_feed_speed=show_feed_speed,
		focus_asset_i=focus_asset_i,
		focus_body_i=focus_body_i,
	)


# --- changes mode: REST candles + WS ticker (Day Open = GMT session open→last;
#    other rows = (current − last completed X close) / that close, current = ticker last or mid) ---

_CHANGES_TTL_S = {
	"Day Open": 2.0,
	"1M": 15.0,
	"5M": 30.0,
	"15M": 40.0,
	"1H": 60.0,
	"4H": 120.0,
	"6H": 180.0,
	"1D": 300.0,
	"3D": 600.0,
	"7D": 900.0,
}

# Cached ``None`` (REST error, empty bars, rate limit) must retry soon — not full candle TTL.
_NONE_PCT_CACHE_TTL_S = 15.0


def _cache_entry_ttl(label: str, cached_val: float | None) -> float:
	"""TTL for treating a cache entry as fresh; shorten when value is still missing."""
	base = _CHANGES_TTL_S.get(label, 60.0)
	if cached_val is None:
		return min(base, _NONE_PCT_CACHE_TTL_S)
	return base


def _sort_candles(raw: list) -> list:
	if not raw:
		return []

	def _st(c):
		if not isinstance(c, dict):
			return 0
		st = c.get("start_time")
		if st is None:
			st = c.get("startTime")
		try:
			return int(st)
		except (TypeError, ValueError):
			return 0

	return sorted(raw, key=_st)


def _candle_close_float(c) -> float | None:
	try:
		cl = float(c["close_price"])
	except (KeyError, TypeError, ValueError):
		return None
	if cl == 0 or not math.isfinite(cl):
		return None
	return cl


class ChangesFetcher:
	def __init__(
		self,
		rest: SFOXTrader,
		ws: SFOXWebSocketClient,
		candle_cache_root: Path | None = None,
	) -> None:
		self.rest = rest
		self.ws = ws
		self._candle_cache = candle_cache_root
		self._cache: dict[tuple[str, str], tuple[float | None, float]] = {}
		self._cache_lock = threading.Lock()
		# Stale-while-revalidate: expired TTL returns last value; REST runs off UI thread.
		self._pct_executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="sfox-pct")
		self._inflight_refresh: set[tuple[str, str]] = set()
		self._inflight_lock = threading.Lock()

	def close(self) -> None:
		"""Stop background %% refresh workers (call before exit)."""
		self._pct_executor.shutdown(wait=False, cancel_futures=True)

	def _get_candlesticks(self, pair: str, start: int, end: int, period: int) -> list:
		return get_candlesticks_cached(
			self.rest.get_candlesticks,
			pair,
			start,
			end,
			period,
			self._candle_cache,
		)

	def _current_price(self, pair: str) -> float | None:
		"""Best live price: ticker ``last``, else WebSocket mid."""
		t = self.ws.get_ticker(pair)
		if t:
			try:
				v = float(t["last"])
				if math.isfinite(v) and v > 0:
					return v
			except (KeyError, TypeError, ValueError):
				pass
		mp = self.ws.get_mid_price(pair)
		if mp is not None:
			try:
				v = float(mp)
				if math.isfinite(v) and v > 0:
					return v
			except (TypeError, ValueError):
				pass
		return None

	def _pct_current_vs_close(self, pair: str, ref_close: float | None) -> float | None:
		if ref_close is None:
			return None
		cur = self._current_price(pair)
		if cur is None:
			return None
		return (cur - ref_close) / ref_close * 100.0

	def _async_pct_refresh(self, key: tuple[str, str], pair: str, row: dict) -> None:
		try:
			v = self._compute(pair, row)
			now_m = time.monotonic()
			with self._cache_lock:
				self._cache[key] = (v, now_m)
		finally:
			with self._inflight_lock:
				self._inflight_refresh.discard(key)

	def pct(self, pair: str, row: dict) -> float | None:
		pl = pair.lower()
		label = row["label"]
		key = (pl, label)
		now_m = time.monotonic()
		with self._cache_lock:
			ent = self._cache.get(key)
			if ent is not None:
				val, t0 = ent
				if now_m - t0 < _cache_entry_ttl(label, val):
					return val
				stale_val = val
			else:
				stale_val = None
		if ent is not None:
			with self._inflight_lock:
				if key not in self._inflight_refresh:
					self._inflight_refresh.add(key)
					row_copy = dict(row)
					self._pct_executor.submit(self._async_pct_refresh, key, pl, row_copy)
			return stale_val
		v = self._compute(pl, row)
		with self._cache_lock:
			self._cache[key] = (v, now_m)
		return v

	def _compute(self, pair: str, row: dict) -> float | None:
		kind = row["kind"]
		try:
			if kind == "ticker":
				t = self.ws.get_ticker(pair)
				if not t:
					return None
				o = float(t["open"])
				last = float(t["last"])
				if o == 0:
					return None
				return (last - o) / o * 100.0
			if kind == "candle":
				return self._candle_period_pct(pair, int(row["period"]))
			if kind == "candle_4h":
				return self._candle_4h_pct(pair)
			if kind == "span_days":
				return self._span_days_pct(pair, int(row["days"]))
		except Exception:
			return None
		return None

	def _candle_period_pct(self, pair: str, period: int) -> float | None:
		# ref = close of last *completed* bar of this period; current = live last/mid.
		now = int(time.time())
		span = min(499 * period, max(period * 6, period * 3))
		raw = self._get_candlesticks(pair, now - span, now, period)
		chs = _sort_candles(raw)
		ref = None
		if len(chs) >= 2:
			ref = _candle_close_float(chs[-2])
		elif len(chs) == 1:
			ref = _candle_close_float(chs[-1])
		return self._pct_current_vs_close(pair, ref)

	def _candle_4h_pct(self, pair: str) -> float | None:
		# No native 4h: hourly bars; ref = close 4 hours before last completed hour (chs[-6] vs chs[-2]).
		now = int(time.time())
		raw = self._get_candlesticks(pair, now - 8 * 3600, now, 3600)
		chs = _sort_candles(raw)
		if len(chs) < 6:
			return None
		ref = _candle_close_float(chs[-6])
		return self._pct_current_vs_close(pair, ref)

	def _span_days_pct(self, pair: str, days: int) -> float | None:
		# ref = daily close ``days`` completed bars before the latest completed daily (chs[-2]).
		now = int(time.time())
		span = (days + 10) * 86400
		raw = self._get_candlesticks(pair, now - span, now, 86400)
		chs = _sort_candles(raw)
		need_len = 2 + days
		if len(chs) < need_len:
			return None
		ref = _candle_close_float(chs[-(2 + days)])
		return self._pct_current_vs_close(pair, ref)


def prime_changes_cache_async(
	fetcher: ChangesFetcher,
	assets: list[str],
	row_defs: list[dict],
	max_workers: int,
) -> threading.Thread:
	"""
	Start a daemon thread that warms REST-backed %% cells in parallel via ``pct``.
	Skips ``kind == "ticker"`` (Day Open — WebSocket only). Safe with the UI loop:
	``ChangesFetcher`` uses a lock around cache updates.
	"""
	# Interleave by timeframe so we do not burst chartdata with all rows for asset[0] first
	# (avoids 429 / empty cache that then freezes ``None`` behind long TTLs).
	_rows = [r for r in row_defs if r.get("kind") != "ticker"]
	tasks = [(s, _rows[i]) for i in range(len(_rows)) for s in assets]

	def _run() -> None:
		if not tasks:
			return
		nw = max(1, min(max_workers, len(tasks)))

		def _one(pair_row: tuple[str, dict]) -> None:
			pair, row = pair_row
			try:
				fetcher.pct(pair, row)
			except Exception:
				pass

		try:
			with ThreadPoolExecutor(max_workers=nw) as ex:
				ex.map(_one, tasks)
		except Exception:
			pass

	th = threading.Thread(target=_run, name="sFOX-candle-prime", daemon=True)
	th.start()
	return th


def _fmt_pct_cell(v: float | None, width: int) -> str:
	if v is None:
		return "—".rjust(width)[:width]
	if not math.isfinite(v):
		return "—".rjust(width)[:width]
	s = f"{v:+.2f}%"
	if len(s) > width:
		s = f"{v:+.1f}%"
	return s[:width].rjust(width)


def draw_changes(
	stdscr,
	assets,
	fetcher: ChangesFetcher,
	ws,
	theme_pairs,
	changes_row_defs: list[dict],
	footer_error=None,
	show_feed_speed: bool = False,
):
	max_y, max_x = stdscr.getmaxyx()
	fr = _footer_rows_for(max_y, show_feed_speed)
	stdscr.erase()
	stdscr.addstr(
		0,
		0,
		_title_bar("CHANGES", max_x, max_y)[: max_x - 1],
		theme_pairs["title_fg"] | curses.A_BOLD,
	)
	col0 = 8
	w_cell = max(11, max((len(s) for s in assets), default=6) + 3)
	gap = 1
	head_sym = f"{'':<{col0}}"
	for i, sym in enumerate(assets):
		lbl = sym.upper()
		if len(lbl) > w_cell:
			lbl = lbl[:w_cell]
		else:
			lbl = lbl.center(w_cell)
		head_sym += lbl
		if i < len(assets) - 1:
			head_sym += " " * gap
	stdscr.addstr(1, 0, head_sym[: max_x - 1], theme_pairs["header_fg"] | curses.A_BOLD)
	head_sub = f"{'':<{col0}}"
	for i, _ in enumerate(assets):
		head_sub += f"{'%Δ':>{w_cell}}"
		if i < len(assets) - 1:
			head_sub += " " * gap
	stdscr.addstr(2, 0, head_sub[: max_x - 1], theme_pairs["hint_fg"])
	row = 3
	for rd in changes_row_defs:
		if row >= max_y - fr:
			break
		stdscr.addstr(row, 0, f"{rd['label']:<{col0}}"[: max_x - 1], theme_pairs["label_fg"])
		xc = col0
		for sym in assets:
			if xc >= max_x - 1:
				break
			pv = fetcher.pct(sym, rd)
			txt = _fmt_pct_cell(pv, w_cell)
			stdscr.addstr(row, xc, txt[: max_x - xc - 1], attr_for_pct(pv, theme_pairs))
			xc += w_cell + gap
		row += 1
	if max_y >= 5:
		if show_feed_speed:
			stdscr.addstr(
				max_y - 3,
				0,
				feed_latency_footer_text(ws, assets, max_x - 1)[: max_x - 1],
				theme_pairs["hint_fg"],
			)
		stdscr.addstr(
			max_y - 2,
			0,
			gmt_clock_line(max_x - 1),
			theme_pairs["hint_fg"],
		)
	elif max_y >= 4:
		if show_feed_speed:
			stdscr.addstr(
				max_y - 2,
				0,
				feed_latency_footer_text(ws, assets, max_x - 1)[: max_x - 1],
				theme_pairs["hint_fg"],
			)
		else:
			stdscr.addstr(
				max_y - 2,
				0,
				gmt_clock_line(max_x - 1),
				theme_pairs["hint_fg"],
			)
	err_a = theme_pairs["diff_neg_fg"] | curses.A_BOLD
	if footer_error:
		stdscr.addstr(max_y - 1, 0, f"diff: {footer_error}"[: max_x - 1], err_a)
	else:
		hx = f"{_HINT_KEYS} | Day Open=GMT session; rows=(last/mid−prior close)/prior close; --candles"
		if show_feed_speed:
			hx += " | feed speed p/u"
		stdscr.addstr(max_y - 1, 0, hx[: max_x - 1], theme_pairs["hint_fg"])
	stdscr.refresh()


def parse_assets(s: str) -> list[str]:
	out = []
	for p in s.split(","):
		p = p.strip().lower().replace("/", "")
		if p:
			out.append(p)
	return out


def diff_base_uniform_error(assets: list[str]) -> str | None:
	"""
	Return an error message if diff is not meaningful: all pairs must share the same base.
	None if OK or fewer than two assets (diff is unused).
	"""
	if len(assets) < 2:
		return None
	bases = [pair_base(a) for a in assets]
	if any(b is None for b in bases):
		return "cannot resolve base/quote for one or more pairs (unknown quote suffix)"
	if len(set(bases)) != 1:
		return "diff needs the same base across pairs (e.g. ethusd, ethusdt, ethusdc); mixed bases disabled"
	return None


def parse_bips(s: str) -> list[int]:
	out = []
	for p in s.split(","):
		p = p.strip()
		if not p:
			continue
		out.append(int(p, 10))
	return sorted(set(out))


# Default %% rows after Day Open (sFOX chartdata periods only for minute bars).
DEFAULT_CANDLES_STR = "1M,5M,15M,1H,4H,6H,1D"

# Normalized token (lower case, no spaces) -> row spec (display label + kind).
_CANDLE_TOKEN_MAP: dict[str, dict] = {
	"1m": {"label": "1M", "kind": "candle", "period": 60},
	"5m": {"label": "5M", "kind": "candle", "period": 300},
	"15m": {"label": "15M", "kind": "candle", "period": 900},
	"1h": {"label": "1H", "kind": "candle", "period": 3600},
	"4h": {"label": "4H", "kind": "candle_4h"},
	"6h": {"label": "6H", "kind": "candle", "period": 21600},
	# Back-compat: old typo label mapped to 6-hour bars
	"6m": {"label": "6H", "kind": "candle", "period": 21600},
	"1d": {"label": "1D", "kind": "candle", "period": 86400},
	"3d": {"label": "3D", "kind": "span_days", "days": 3},
	"7d": {"label": "7D", "kind": "span_days", "days": 7},
}

_SUPPORTED_CANDLE_TOKENS_HELP = "1M, 5M, 15M, 1H, 4H, 6H, 1D, 3D, 7D"


def parse_candles_arg(s: str | None) -> list[dict]:
	"""
	Parse --candles into row dicts (no Day Open). Tokens must match sFOX chartdata
	(60, 300, 900, 3600, 21600, 86400) or 4H (synthetic) / 3D / 7D spans.
	"""
	src = (s or "").strip() or DEFAULT_CANDLES_STR
	out: list[dict] = []
	seen_labels: set[str] = set()
	for raw in src.split(","):
		tok = raw.strip().lower().replace(" ", "")
		if not tok:
			continue
		spec = _CANDLE_TOKEN_MAP.get(tok)
		if spec is None:
			raise ValueError(
				f"unknown candle token {raw.strip()!r}; supported: {_SUPPORTED_CANDLE_TOKENS_HELP} "
				"(minute bars must be 1M, 5M, or 15M per sFOX API)"
			)
		row = {k: v for k, v in spec.items()}
		lb = row["label"]
		if lb in seen_labels:
			continue
		seen_labels.add(lb)
		out.append(row)
	if not out:
		raise ValueError("empty --candles after parsing (need at least one token)")
	return out


def build_changes_row_defs(candle_rows: list[dict]) -> list[dict]:
	"""Day Open first, then candle rows from ``parse_candles_arg``."""
	day = {"label": "Day Open", "kind": "ticker"}
	return [day] + [{k: v for k, v in r.items()} for r in candle_rows]


# Default ladder when switching to full mode without -b/--bips (summary-only launch).
_DEFAULT_TOGGLE_BIPS = "0,1,2,5,7,10"

# Main loop: slower redraw when idle, faster after a keypress.
_SLEEP_IDLE_S = 0.25
_SLEEP_ACTIVE_S = 0.08
# After one or more keys this frame, sleep briefly so key-repeat / drain feels snappy (not 80ms+ per step).
_SLEEP_AFTER_KEYS_S = 0.012
_ACTIVE_AFTER_INPUT_S = 0.5
_KEY_RESIZE = getattr(curses, "KEY_RESIZE", None)


def curses_main(stdscr, args):
	curses.curs_set(0)
	stdscr.nodelay(True)
	theme = load_theme(args.theme)
	tpairs = init_theme_colors(theme)

	rest = SFOXTrader(args.key)
	ws = SFOXWebSocketClient(args.key)
	ws.start()
	ws.subscribe_order_books(args.assets)
	ws.subscribe_tickers(args.assets)
	cache_root = getattr(args, "candle_cache_path", None)
	if cache_root is not None:
		cache_root.mkdir(parents=True, exist_ok=True)
	fetcher = ChangesFetcher(rest, ws, cache_root)
	prime_changes_cache_async(
		fetcher,
		args.assets,
		args.changes_row_defs,
		getattr(args, "candle_prime_workers", 8),
	)

	# Prime: order books for summary / ladder modes; tickers for changes and full (Day Open row).
	if args.mode == "changes":
		for _ in range(120):
			if all(ws.get_ticker(s) for s in args.assets):
				break
			time.sleep(0.05)
	else:
		for _ in range(80):
			if all(ws.get_order_book_snapshot(s) for s in args.assets):
				break
			time.sleep(0.05)
	if args.mode == "full":
		for _ in range(80):
			if all(ws.get_ticker(s) for s in args.assets):
				break
			time.sleep(0.05)

	full_rows = None
	if args.bips_list:
		full_rows = build_full_rows(args.bips_list)
		if not full_rows:
			raise SystemExit("full mode needs at least one bps in -b/--bips")
	elif args.mode in ("full", "orderbook"):
		raise SystemExit("internal: ladder mode startup without bips_list")

	mode = args.mode
	show_diff = bool(args.diff) and mode != "changes"
	diff_err = None
	show_help = False
	show_feed_speed = False

	def ensure_full_rows():
		nonlocal full_rows
		if full_rows is not None:
			return full_rows
		bl = args.bips_list if args.bips_list else mirror_bid_bips_if_needed(parse_bips(_DEFAULT_TOGGLE_BIPS))
		full_rows = build_full_rows(bl)
		if not full_rows:
			raise SystemExit("could not build ladder rows for full/orderbook mode")
		return full_rows

	full_scroll_y = 0
	active_until = 0.0
	focus_asset_i = 0
	focus_field_i = 0
	focus_body_i = 0

	while True:
		keys: list[int] = []
		_ch = stdscr.getch()
		while _ch != -1:
			active_until = time.time() + _ACTIVE_AFTER_INPUT_S
			keys.append(_ch)
			_ch = stdscr.getch()
		keys_received = len(keys) > 0

		# Toggle keys: apply at most once per frame. Key-repeat + input drain can queue many
		# identical codes; toggling per code flips an even number of times (no net change).
		if any(c in (ord("v"), ord("V")) for c in keys):
			show_feed_speed = not show_feed_speed
			if mode in ("full", "orderbook"):
				full_scroll_y = 0
		if 27 in keys:
			show_help = False
		elif any(c in (ord("h"), ord("H"), ord("?")) for c in keys):
			show_help = not show_help

		quit_app = False
		for ch in keys:
			if ch in (ord("q"), ord("Q")):
				quit_app = True
				break
			if not show_help:
				if _KEY_RESIZE is not None and ch == _KEY_RESIZE:
					try:
						curses.update_lines_cols()
					except AttributeError:
						pass
					full_scroll_y = 0
					if mode in ("full", "orderbook"):
						_rsz_fr = ensure_full_rows()
						_rsz_y, _ = stdscr.getmaxyx()
						_rsz_ch = mode == "full"
						_rsz_ncr = len(args.changes_row_defs) if _rsz_ch else 0
						_, _, _rsz_tot = ladder_scroll_metrics(
							_rsz_y, show_feed_speed, _rsz_fr, _rsz_ch, _rsz_ncr
						)
						focus_body_i = min(focus_body_i, max(0, _rsz_tot - 1))
					focus_asset_i = min(focus_asset_i, max(0, len(args.assets) - 1))
				elif ch in (ord("s"), ord("S")):
					mode = "summary"
					focus_asset_i = min(focus_asset_i, max(0, len(args.assets) - 1))
					dr = show_diff and len(args.assets) >= 2
					nf = len(_summary_field_specs(dr, 10 if dr else 0))
					focus_field_i = min(focus_field_i, max(0, nf - 1))
				elif ch in (ord("f"), ord("F")):
					mode = "full"
					ensure_full_rows()
				elif ch in (ord("o"), ord("O")):
					mode = "orderbook"
					ensure_full_rows()
				elif ch in (ord("c"), ord("C")):
					mode = "changes"
					show_diff = False
					diff_err = None
				elif ch in (ord("d"), ord("D")):
					if mode == "changes":
						pass
					elif diff_err:
						diff_err = None
					elif show_diff:
						show_diff = False
					else:
						e = diff_base_uniform_error(args.assets)
						if e:
							diff_err = e
						else:
							show_diff = True
				elif mode == "summary":
					_kl = getattr(curses, "KEY_LEFT", None)
					_kr = getattr(curses, "KEY_RIGHT", None)
					_ku = getattr(curses, "KEY_UP", None)
					_kd = getattr(curses, "KEY_DOWN", None)
					_na = len(args.assets)
					_dr = show_diff and _na >= 2
					_wd = 10 if _dr else 0
					_nf = len(_summary_field_specs(_dr, _wd)) if _na else 0
					if _nf:
						if _ku is not None and ch == _ku:
							focus_asset_i = max(0, focus_asset_i - 1)
						elif _kd is not None and ch == _kd:
							focus_asset_i = min(_na - 1, focus_asset_i + 1)
						if _kl is not None and ch == _kl:
							focus_field_i = max(0, focus_field_i - 1)
						elif _kr is not None and ch == _kr:
							focus_field_i = min(_nf - 1, focus_field_i + 1)
				elif mode in ("full", "orderbook"):
					kpp = getattr(curses, "KEY_PPAGE", None)
					knp = getattr(curses, "KEY_NPAGE", None)
					_, my = stdscr.getmaxyx()
					page = max(3, my // 3)
					_fr = ensure_full_rows()
					_with_ch = mode == "full"
					_ncr = len(args.changes_row_defs) if _with_ch else 0
					_max_sc, _vh, _tot = ladder_scroll_metrics(
						my, show_feed_speed, _fr, _with_ch, _ncr
					)
					if ch == ord("k"):
						full_scroll_y -= 1
					elif ch == ord("j"):
						full_scroll_y += 1
					elif kpp is not None and ch == kpp:
						full_scroll_y -= page
					elif knp is not None and ch == knp:
						full_scroll_y += 1
					_kl = getattr(curses, "KEY_LEFT", None)
					_kr = getattr(curses, "KEY_RIGHT", None)
					_ku = getattr(curses, "KEY_UP", None)
					_kd = getattr(curses, "KEY_DOWN", None)
					_nas = len(args.assets)
					_focus_moved_v = False
					if _nas and _tot:
						if _kl is not None and ch == _kl:
							focus_asset_i = max(0, focus_asset_i - 1)
						elif _kr is not None and ch == _kr:
							focus_asset_i = min(_nas - 1, focus_asset_i + 1)
						if _ku is not None and ch == _ku:
							focus_body_i = max(0, focus_body_i - 1)
							_focus_moved_v = True
						elif _kd is not None and ch == _kd:
							focus_body_i = min(_tot - 1, focus_body_i + 1)
							_focus_moved_v = True
						if _focus_moved_v:
							if focus_body_i < full_scroll_y:
								full_scroll_y = focus_body_i
							elif _vh and focus_body_i >= full_scroll_y + _vh:
								full_scroll_y = focus_body_i - _vh + 1
					full_scroll_y = max(0, min(full_scroll_y, _max_sc))

		if quit_app:
			break
		if show_help:
			draw_help(stdscr, tpairs)
		elif mode == "changes":
			draw_changes(
				stdscr,
				args.assets,
				fetcher,
				ws,
				tpairs,
				args.changes_row_defs,
				diff_err,
				show_feed_speed,
			)
		elif mode == "summary":
			_sdr = show_diff and len(args.assets) >= 2
			_swd = 10 if _sdr else 0
			_snf = len(_summary_field_specs(_sdr, _swd))
			focus_asset_i = min(focus_asset_i, max(0, len(args.assets) - 1))
			focus_field_i = min(focus_field_i, max(0, _snf - 1))
			draw_summary(
				stdscr,
				args.assets,
				ws,
				tpairs,
				show_diff,
				diff_err,
				show_feed_speed,
				focus_asset_i=focus_asset_i,
				focus_field_i=focus_field_i,
			)
		elif mode == "orderbook":
			_ofr = ensure_full_rows()
			_oy, _ = stdscr.getmaxyx()
			_, _, _otot = ladder_scroll_metrics(_oy, show_feed_speed, _ofr, False, 0)
			focus_body_i = min(focus_body_i, max(0, _otot - 1))
			full_scroll_y = draw_orderbook(
				stdscr,
				args.assets,
				ws,
				full_rows,
				tpairs,
				show_diff,
				diff_err,
				full_scroll_y,
				show_feed_speed,
				focus_asset_i=focus_asset_i,
				focus_body_i=focus_body_i,
			)
		else:
			_ffr = ensure_full_rows()
			_fy, _ = stdscr.getmaxyx()
			_, _, _ftot = ladder_scroll_metrics(
				_fy, show_feed_speed, _ffr, True, len(args.changes_row_defs)
			)
			focus_body_i = min(focus_body_i, max(0, _ftot - 1))
			full_scroll_y = draw_full(
				stdscr,
				args.assets,
				ws,
				full_rows,
				tpairs,
				fetcher,
				args.changes_row_defs,
				show_diff,
				diff_err,
				full_scroll_y,
				show_feed_speed,
				focus_asset_i=focus_asset_i,
				focus_body_i=focus_body_i,
			)
		if keys_received:
			delay = _SLEEP_AFTER_KEYS_S
		elif time.time() < active_until:
			delay = _SLEEP_ACTIVE_S
		else:
			delay = _SLEEP_IDLE_S
		time.sleep(delay)

	fetcher.close()
	ws.stop()


def main():
	ap = argparse.ArgumentParser(description="sFOX Market Monitor (sfox-mm) — order book and change grid")
	ap.add_argument(
		"-V",
		"--version",
		action="version",
		version=f"%(prog)s {__version__}",
	)
	ap.add_argument(
		"-a",
		"--assets",
		required=True,
		help="Comma-separated pairs, e.g. btcusd,ethusd,solusd",
	)
	ap.add_argument(
		"-b",
		"--bips",
		default="",
		help='Comma-separated bps ladder for full / orderbook, e.g. "0,1,2,5,7,10". '
		"Required with -m full or orderbook. Optional with -m summary or changes (used when you press f or o). "
		"If you start in summary or changes without -b, pressing f or o uses a default ladder. "
		"0 = mid row (px=mid only; sz blank). Bid side mirrors +rungs unless you add negatives.",
	)
	ap.add_argument(
		"-m",
		"--mode",
		choices=("summary", "full", "changes", "orderbook"),
		required=True,
		help="Starting view: summary; full (ladder + %%chg after bids); orderbook (ladder only); changes. Press s/f/o/c to switch.",
	)
	ap.add_argument(
		"-k",
		"--key",
		default=None,
		help="sFOX API key (default: env SFOX_API_KEY)",
	)
	ap.add_argument(
		"--theme",
		default=None,
		help="JSON theme file (default: tools/oba/sfox_mm.colors.cfg if present; grid-ob.colors.cfg still accepted)",
	)
	ap.add_argument(
		"-d",
		"--diff",
		action="store_true",
		help="Start with diff on (≥2 assets, same base). Press d to toggle. Mixed bases (e.g. btcusd,ethusd) are rejected.",
	)
	ap.add_argument(
		"--candles",
		default=None,
		help="Comma-separated %% rows after Day Open. Default 1M,5M,15M,1H,4H,6H,1D. "
		f"Tokens: {_SUPPORTED_CANDLE_TOKENS_HELP}. Example: --candles 1M,5M,1H,1D",
	)
	ap.add_argument(
		"--candle-prime-workers",
		type=int,
		default=8,
		help="Parallel REST chartdata fetches when warming the %% candle cache at startup (1–32, default 8).",
	)
	ap.add_argument(
		"--no-candle-disk-cache",
		action="store_true",
		help="Disable on-disk chartdata cache. Default cache dir: .cache/sfox-markets-monitor/candles "
		"(cwd); override with --candle-cache-dir or env SFOX_MARKETS_MONITOR_CACHE.",
	)
	ap.add_argument(
		"--candle-cache-dir",
		default=None,
		help="Directory for chartdata JSON cache (overrides env SFOX_MARKETS_MONITOR_CACHE).",
	)
	ns = ap.parse_args()
	ns.assets = parse_assets(ns.assets)
	if not ns.assets:
		ap.error("no assets parsed from -a/--assets")
	ns.key = ns.key or os.environ.get("SFOX_API_KEY")
	if not ns.key:
		ap.error("set SFOX_API_KEY or pass -k/--key")

	xc = usd_usdc_cross_book_error(ns.assets)
	if xc:
		ap.error(xc)

	try:
		ns.changes_row_defs = build_changes_row_defs(parse_candles_arg(ns.candles))
	except ValueError as e:
		ap.error(str(e))

	pw = int(ns.candle_prime_workers)
	if pw < 1:
		ap.error("--candle-prime-workers must be >= 1")
	if pw > 32:
		ap.error("--candle-prime-workers must be <= 32")
	ns.candle_prime_workers = pw

	if ns.no_candle_disk_cache:
		ns.candle_cache_path = None
	elif ns.candle_cache_dir:
		ns.candle_cache_path = Path(os.path.expanduser(ns.candle_cache_dir))
	else:
		env = os.environ.get("SFOX_MARKETS_MONITOR_CACHE", "").strip()
		ns.candle_cache_path = Path(os.path.expanduser(env)) if env else default_cache_root()

	if ns.diff and len(ns.assets) >= 2:
		de = diff_base_uniform_error(ns.assets)
		if de:
			ap.error(f"diff not enabled: {de}")

	if ns.mode in ("full", "orderbook"):
		if not ns.bips.strip():
			ap.error("full and orderbook modes require -b/--bips")
		ns.bips_list = mirror_bid_bips_if_needed(parse_bips(ns.bips))
		if not ns.bips_list:
			ap.error("no bips parsed from -b/--bips")
	elif ns.mode == "changes":
		if ns.bips.strip():
			ns.bips_list = mirror_bid_bips_if_needed(parse_bips(ns.bips))
			if not ns.bips_list:
				ap.error("no bips parsed from -b/--bips")
		else:
			ns.bips_list = []
	else:
		if ns.bips.strip():
			ns.bips_list = mirror_bid_bips_if_needed(parse_bips(ns.bips))
			if not ns.bips_list:
				ap.error("no bips parsed from -b/--bips")
		else:
			ns.bips_list = []

	curses.wrapper(curses_main, ns)


if __name__ == "__main__":
	main()
