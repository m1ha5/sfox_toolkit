#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
On-disk cache for sFOX chartdata candlestick REST responses (JSON files).

Default directory: ``<cwd>/.cache/sfox-markets-monitor/candles/``.
Override with env ``SFOX_MARKETS_MONITOR_CACHE`` or ``--candle-cache-dir``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional


def _cache_quant_seconds(period: int) -> int:
	"""Bucket size (seconds) for aligning cache keys; smaller period → shorter buckets."""
	if period <= 60:
		return 30
	if period <= 300:
		return 60
	if period <= 900:
		return 120
	if period <= 3600:
		return 300
	if period <= 21600:
		return 600
	return 1800


def _cache_ttl_seconds(period: int) -> float:
	"""Max file age (mtime) before we ignore the cache and refetch."""
	q = _cache_quant_seconds(period)
	return min(7200.0, max(15.0, float(q) * 2))


def cache_bucket_end(end: int, period: int) -> int:
	"""Floor ``end`` into a time bucket so nearby wall times share one cache entry."""
	q = _cache_quant_seconds(period)
	return (int(end) // q) * q


def _cache_file_path(root: Path, pair: str, period: int, bucket_end: int) -> Path:
	pair_n = str(pair).lower().strip().replace("/", "")
	identity = f"{pair_n}|{period}|{bucket_end}"
	h = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:28]
	return root / f"{h}.json"


def cache_load(
	root: Path,
	pair: str,
	start: int,
	end: int,
	period: int,
) -> Optional[list]:
	"""Return cached candle list or None if missing/stale/invalid."""
	if not root:
		return None
	bucket = cache_bucket_end(end, period)
	path = _cache_file_path(root, pair, period, bucket)
	if not path.is_file():
		return None
	try:
		age = time.time() - path.stat().st_mtime
		if age > _cache_ttl_seconds(period):
			return None
		with open(path, "r", encoding="utf-8") as f:
			obj: Any = json.load(f)
	except (OSError, ValueError, json.JSONDecodeError):
		return None
	if not isinstance(obj, dict):
		return None
	if obj.get("pair") != pair.lower().strip().replace("/", ""):
		return None
	if int(obj.get("period", -1)) != int(period):
		return None
	if int(obj.get("bucket_end", -1)) != bucket:
		return None
	fs = int(obj.get("fetch_start", -1))
	fe = int(obj.get("fetch_end", -1))
	if fs < 0 or fe < 0 or fs > int(start) or fe < int(end):
		return None
	data = obj.get("data")
	if not isinstance(data, list):
		return None
	return data


def cache_save(
	root: Path,
	pair: str,
	start: int,
	end: int,
	period: int,
	data: list,
) -> None:
	if not root or not isinstance(data, list):
		return
	root.mkdir(parents=True, exist_ok=True)
	bucket = cache_bucket_end(end, period)
	path = _cache_file_path(root, pair, period, bucket)
	pair_n = pair.lower().strip().replace("/", "")
	obj = {
		"pair": pair_n,
		"period": int(period),
		"bucket_end": int(bucket),
		"fetch_start": int(start),
		"fetch_end": int(end),
		"fetched_wall": time.time(),
		"data": data,
	}
	fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(root))
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as f:
			json.dump(obj, f)
		os.replace(tmp, path)
	except OSError:
		try:
			os.unlink(tmp)
		except OSError:
			pass


def get_candlesticks_cached(
	get_candlesticks: Callable[..., list],
	pair: str,
	start: int,
	end: int,
	period: int,
	cache_root: Optional[Path],
) -> list:
	"""
	Wrap ``SFOXTrader.get_candlesticks``: disk cache when ``cache_root`` is set.
	Cache key includes quantized wall-time bucket of ``end``; fetch always uses
	the given ``start``/``end`` so the latest bars stay correct.
	"""
	if cache_root is None:
		return get_candlesticks(pair, start, end, period)
	hit = cache_load(cache_root, pair, start, end, period)
	if hit is not None:
		return hit
	raw = get_candlesticks(pair, start, end, period)
	if isinstance(raw, list):
		cache_save(cache_root, pair, start, end, period, raw)
	return raw


def default_cache_root() -> Path:
	"""Default candle cache directory under cwd (no env; main() applies env/CLI)."""
	return Path(os.getcwd()) / ".cache" / "sfox-markets-monitor" / "candles"
