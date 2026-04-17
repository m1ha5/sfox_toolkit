#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared parsing for sFOX pair strings (base + quote suffix)."""

from __future__ import annotations

# Longest quote suffix first (e.g. usdt before usd).
QUOTE_SUFFIXES = (
	"usdc",
	"usdt",
	"usd",
	"eur",
	"gbp",
	"aud",
	"cad",
	"jpy",
	"chf",
	"try",
	"nzd",
	"sek",
	"czk",
	"pln",
	"hkd",
	"sgd",
	"inr",
	"brl",
	"mxn",
)


def pair_base(sym: str) -> str | None:
	"""Base asset id from a pair string (e.g. ethusd -> eth). None if quote unknown."""
	s = (sym or "").lower().strip().replace("/", "")
	if not s:
		return None
	for q in QUOTE_SUFFIXES:
		if len(s) > len(q) and s.endswith(q):
			return s[: -len(q)]
	return None


def pair_quote(sym: str) -> str | None:
	"""Quote currency suffix from a pair string (e.g. ethusdc -> usdc). None if unknown."""
	s = (sym or "").lower().strip().replace("/", "")
	if not s:
		return None
	for q in QUOTE_SUFFIXES:
		if len(s) > len(q) and s.endswith(q):
			return q
	return None


def usd_usdc_cross_book_error(assets: list[str]) -> str | None:
	"""
	If the list includes both ``<base>usd`` and ``<base>usdc`` for the same base, return
	an error message (sFOX crosses those net books; side-by-side columns are misleading).
	"""
	if len(assets) < 2:
		return None
	by_base: dict[str, list[str]] = {}
	for a in assets:
		b = pair_base(a)
		q = pair_quote(a)
		if b is None or q is None or q not in ("usd", "usdc"):
			continue
		by_base.setdefault(b, []).append(a)
	conflicts: list[str] = []
	for base, syms in sorted(by_base.items()):
		quotes = {pair_quote(x) for x in syms}
		if "usd" in quotes and "usdc" in quotes:
			conflicts.append(f"{base}: {', '.join(sorted(set(syms)))}")
	if not conflicts:
		return None
	return (
		"sFOX crosses net order books across USD- and USDC-quoted pairs for the same base "
		"(a resting order on one pair can be filled by a taker on the other). "
		"WS/REST snapshots for e.g. btcusd vs btcusdc can therefore match.\n"
		"Do not pass both quote styles for the same base. Conflicting groups:\n  "
		+ "\n  ".join(conflicts)
		+ "\n  (Remove one column per base, or compare e.g. *usd vs *usdt instead.)"
	)
