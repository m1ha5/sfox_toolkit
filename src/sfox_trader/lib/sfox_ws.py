#!/usr/bin/env python3
# -*- coding: utf-8; py-indent-offset:4 -*-
"""
WebSocket client for sFOX market and account data.

Feeds used here include:

- Net order books ``orderbook.net.<pair>``, tickers ``ticker.sfox.<pair>``
- ``private.user.open-orders`` — order snapshots and updates (see
  https://docs.sfox.com/websocket-api/orders-and-account-data/orders )
- ``private.user.trades`` — optional per-order trade enrichment
- ``private.user.balances`` — account (and optionally Web3) balances (see
  https://docs.sfox.com/websocket-api/orders-and-account-data/balances )
- ``private.user.post-trade-settlement`` — PTS equity

Reconnect resubscribes via ``_pending_feeds``. Set ``SFOX_WS_OB_TRACE=1`` or
``SFOX_WS_ORDERS_TRACE=1`` for stderr traces.
"""

import json
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Open-orders feed: https://docs.sfox.com/websocket-api/orders-and-account-data/orders
# Balances feed: https://docs.sfox.com/websocket-api/orders-and-account-data/balances

try:
	import websocket  # type: ignore
except ImportError:  # pragma: no cover
	websocket = None  # placeholder; real use requires websocket-client dependency

log = logging.getLogger(__name__)

# Set ``SFOX_WS_OB_TRACE=1`` to print one stderr line per net order book message:
# recipient, parsed suffix, payload.pair, resolved storage key, top bid/ask/qty.
_OB_TRACE = os.environ.get("SFOX_WS_OB_TRACE", "").strip().lower() in ("1", "true", "yes")
# Set ``SFOX_WS_ORDERS_TRACE=1`` to log each open-orders batch at INFO (can be verbose).
_ORDERS_TRACE = os.environ.get("SFOX_WS_ORDERS_TRACE", "").strip().lower() in ("1", "true", "yes")


def _trace_ob_line(
	recipient: str,
	suffix: str,
	payload_pair: str,
	store_key: str,
	bids: list,
	asks: list,
) -> None:
	if not _OB_TRACE:
		return
	bb = ba = qb = qa = None
	try:
		if bids:
			bb, qb = bids[0][0], bids[0][1]
		if asks:
			ba, qa = asks[0][0], asks[0][1]
	except (TypeError, ValueError, IndexError):
		pass
	print(
		"[sfox-ws ob]",
		f"recipient={recipient!r}",
		f"suffix={suffix!r}",
		f"payload.pair={payload_pair!r}",
		f"store={store_key!r}",
		f"bid={bb}@{qb}",
		f"ask={ba}@{qa}",
		file=sys.stderr,
		flush=True,
	)


def _trace_orders_line(
	sequence: Optional[int],
	recipient: str,
	orders: list,
) -> None:
	if not _ORDERS_TRACE:
		return
	try:
		preview = []
		for o in orders[:12]:
			preview.append(
				f"id={o.get('id')!s} {o.get('pair')!s} {o.get('status')!s} "
				f"filled={o.get('filled')!s}/{o.get('quantity')!s}"
			)
		extra = f" (+{len(orders) - 12} more)" if len(orders) > 12 else ""
		print(
			"[sfox-ws orders]",
			f"sequence={sequence}",
			f"recipient={recipient!r}",
			f"n={len(orders)}",
			"; ".join(preview) + extra,
			file=sys.stderr,
			flush=True,
		)
	except Exception:
		pass


_TERMINAL_ORDER_STATUSES = frozenset(
	{
		"done",
		"cancelled",
		"canceled",
		"rejected",
		"expired",
		"complete",
		"completed",
		"filled",
	}
)


def _order_is_terminal(order: dict) -> bool:
	"""Return True if this order should be dropped from the open-order cache."""
	try:
		st = str(order.get("status") or "").strip().lower()
		if st in _TERMINAL_ORDER_STATUSES:
			return True
	except Exception:
		pass
	try:
		qty = float(order.get("quantity") or 0.0)
		filled = float(order.get("filled") or 0.0)
		if qty > 0 and filled >= qty - 1e-12:
			return True
	except (TypeError, ValueError):
		pass
	return False


def _merge_order_preserving_trades(old: Optional[dict], new: dict) -> dict:
	"""WS open-orders payload replaces fields; keep optional ``trades`` list from prior merges."""
	out = dict(new)
	if old and isinstance(old.get("trades"), list) and "trades" not in new:
		out["trades"] = old["trades"]
	return out


def _coerce_epoch_ms(v) -> Optional[int]:
	"""Coerce WebSocket timestamp to integer epoch milliseconds."""
	if v is None:
		return None
	try:
		x = int(float(v))
	except (TypeError, ValueError):
		return None
	# Heuristic: sub-second epoch seconds (e.g. 1.7e9) vs ms (1.7e12)
	if x < 10**11:
		x = int(x * 1000)
	return x


def _normalize_ws_pair(s: str) -> str:
	return str(s or "").lower().strip().replace("/", "").split("?", 1)[0].strip()


def _orderbook_payload_times(payload: dict) -> Tuple[Optional[int], Optional[int]]:
	"""``lastPublished`` / ``lastUpdated`` as ms epoch from order book payload (field names vary)."""
	lp = (
		payload.get("lastPublished")
		or payload.get("last_published")
		or payload.get("lastpublished")
	)
	lu = (
		payload.get("lastUpdated")
		or payload.get("last_updated")
		or payload.get("lastupdated")
	)
	return _coerce_epoch_ms(lp), _coerce_epoch_ms(lu)


def _resolve_orderbook_storage_pair(
	recipient_pair: str,
	payload_pair: str,
	subscribed: Set[str],
) -> str:
	"""
	Pick dict key for ``_order_book_snapshots``.

	sFOX sometimes tags messages so ``recipient`` and ``payload.pair`` disagree, or the
	channel suffix is the shorter stablecoin name (e.g. ``btcusd``) while the book is
	``btcusdc``. Prefer the subscribed pair that matches, and when both match and one
	is a prefix of the other, keep the **longer** id so ``btcusdc`` does not overwrite
	``btcusd`` (and vice versa).
	"""
	r = _normalize_ws_pair(recipient_pair)
	p = _normalize_ws_pair(payload_pair)
	r_in = r in subscribed
	p_in = p in subscribed
	if r_in and p_in:
		if r == p:
			return r
		if r.startswith(p) or p.startswith(r):
			return r if len(r) >= len(p) else p
		log.warning(
			"Order book pair mismatch: recipient suffix %r vs payload.pair %r; using recipient",
			r,
			p,
		)
		return r
	if r_in:
		return r
	if p_in:
		return p
	return r or p


class SFOXWebSocketClient:
	def __init__(self, api_key: str) -> None:
		self.api_key = api_key
		self._ws_url = "wss://ws.sfox.com/ws"
		self._ws: Optional["websocket.WebSocketApp"] = None
		self._thread: Optional[threading.Thread] = None
		self._stop = threading.Event()

		self._mid_prices: Dict[str, float] = {}
		self._best_bids: Dict[str, float] = {}
		self._best_asks: Dict[str, float] = {}
		# Last full net order book per pair (raw lists from WS payload) for depth UIs.
		self._order_book_snapshots: Dict[str, Dict[str, list]] = {}
		# Per-pair feed metadata: lastPublished / lastUpdated (epoch ms) from WS payload.
		self._order_book_feed_meta: Dict[str, Dict[str, Optional[int]]] = {}
		# Last ticker payload per pair from ``ticker.sfox.<pair>`` (see sFOX ticker docs).
		self._tickers: Dict[str, dict] = {}
		self._orders: Dict[str, dict] = {}
		self._equity: Optional[float] = None
		# Last account balances payload from ``private.user.balances`` (list of per-currency dicts).
		self._balances: List[dict] = []
		self._pending_feeds: Set[str] = set()
		self._connected = False
		self._ws_lock = threading.Lock()
		self._state_lock = threading.Lock()
		# First ``private.user.open-orders`` message after connect is a full snapshot (per sFOX docs).
		self._awaiting_open_orders_snapshot = True
		self._on_open_orders: Optional[Callable[[Dict[str, Any]], None]] = None
		self._on_balances: Optional[Callable[[Dict[str, Any]], None]] = None
		# Pairs subscribed as ``orderbook.net.<pair>`` — used to disambiguate e.g. btcusd vs btcusdc
		# when recipient and payload.pair disagree or one is a prefix of the other.
		self._subscribed_net_pairs: Set[str] = set()

	def start(self) -> None:
		"""
		Start background WebSocket loop. If websocket-client is not installed,
		log a warning and act as a no-op client.
		"""
		if websocket is None:
			log.warning("websocket-client not installed; SFOXWebSocketClient is a no-op")
			return

		if self._thread and self._thread.is_alive():
			return

		self._stop.clear()

		def _run():
			while not self._stop.is_set():
				try:
					self._connect_and_loop()
				except Exception as e:  # pragma: no cover
					log.error("WebSocket loop error: %s", e)
				if self._stop.is_set():
					break
				time.sleep(3)

		self._thread = threading.Thread(target=_run, name="SFOXWebSocketClient", daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()
		with self._ws_lock:
			self._connected = False
		if self._ws is not None:
			try:
				self._ws.close()
			except Exception:
				pass
		if self._thread and self._thread.is_alive():
			self._thread.join(timeout=5)

	def _connect_and_loop(self) -> None:
		"""
		Establish a WebSocket connection and process messages until closed.
		"""
		assert websocket is not None

		def on_open(ws):
			log.info("sFOX WebSocket connected")
			with self._ws_lock:
				self._connected = True
			with self._state_lock:
				self._awaiting_open_orders_snapshot = True
			# Authenticate
			auth_msg = {"type": "authenticate", "apiKey": self.api_key}
			ws.send(json.dumps(auth_msg))
			# After auth, subscribe to any pending feeds
			if self._pending_feeds:
				msg = {"type": "subscribe", "feeds": sorted(self._pending_feeds)}
				ws.send(json.dumps(msg))

		def on_message(ws, message):
			self._handle_message(message)

		def on_error(ws, error):
			log.error("sFOX WebSocket error: %s", error)

		def on_close(ws, status_code, msg):
			with self._ws_lock:
				self._connected = False
			log.info("sFOX WebSocket closed: %s %s", status_code, msg)

		self._ws = websocket.WebSocketApp(
			self._ws_url,
			on_open=on_open,
			on_message=on_message,
			on_error=on_error,
			on_close=on_close,
		)
		self._ws.run_forever()

	def _handle_message(self, message: str) -> None:
		"""
		Handle market and account messages based on 'recipient' field.
		"""
		try:
			data = json.loads(message)
		except json.JSONDecodeError:
			log.error("Invalid WebSocket JSON: %s", message)
			return

		recipient = data.get("recipient")
		payload = data.get("payload")

		if not recipient or payload is None:
			return

		# Order book snapshots: recipient like "orderbook.net.btcusd"
		if isinstance(recipient, str) and recipient.lower().startswith("orderbook."):
			if not isinstance(payload, dict):
				return
			pair_from_payload = _normalize_ws_pair(str(payload.get("pair", "")))
			# Full suffix after orderbook.net. (not just the third dot segment) so we match
			# the feed id the client subscribed to; case-insensitive recipient.
			pair_from_recipient = ""
			rlow = recipient.lower().split("?", 1)[0].strip()
			net_prefix = "orderbook.net."
			if rlow.startswith(net_prefix):
				pair_from_recipient = _normalize_ws_pair(rlow[len(net_prefix) :])
			subscribed = set(self._subscribed_net_pairs)
			pair = _resolve_orderbook_storage_pair(pair_from_recipient, pair_from_payload, subscribed)
			# bids/asks: list of [price, quantity, venue?, ...]
			bids = payload.get("bids") or []
			asks = payload.get("asks") or []
			_trace_ob_line(
				recipient,
				pair_from_recipient,
				pair_from_payload,
				pair,
				bids,
				asks,
			)
			if pair:
				self._order_book_snapshots[pair] = {"bids": list(bids), "asks": list(asks)}
				pub_ms, upd_ms = _orderbook_payload_times(payload)
				self._order_book_feed_meta[pair] = {
					"lastPublished": pub_ms,
					"lastUpdated": upd_ms,
				}
			if not bids or not asks:
				return
			try:
				# Match primary REST book (get_ob_mid_non_mm / get_best_*): best at index 0.
				# Do not use market_making side (often empty); WS net book aligns with top-level bids/asks.
				best_bid = float(bids[0][0])
				best_ask = float(asks[0][0])
				mid = (best_bid + best_ask) / 2.0
				if pair:
					self._best_bids[pair] = best_bid
					self._best_asks[pair] = best_ask
					self._mid_prices[pair] = mid
			except (TypeError, ValueError, IndexError):
				return
			return

		# Ticker: recipient like "ticker.sfox.btcusd"
		if isinstance(recipient, str) and recipient.lower().startswith("ticker.sfox."):
			if not isinstance(payload, dict):
				return
			rlow = recipient.lower()
			pfx = "ticker.sfox."
			pair = rlow[len(pfx) :].strip() if rlow.startswith(pfx) else ""
			pp = str(payload.get("pair", "")).lower().strip()
			if pp:
				pair = pp
			if pair:
				self._tickers[pair] = dict(payload)
			return

		# Open orders feed: "private.user.open-orders"
		# https://docs.sfox.com/websocket-api/orders-and-account-data/orders
		if recipient == "private.user.open-orders":
			if not isinstance(payload, list):
				return
			self._process_open_orders_message(data, payload)
			return

		# Account balances: "private.user.balances"
		# https://docs.sfox.com/websocket-api/orders-and-account-data/balances
		if recipient == "private.user.balances":
			self._process_balances_message(data, payload)
			return

		# Trades feed: "private.user.trades" (optional enrichment)
		if recipient == "private.user.trades":
			if not isinstance(payload, list):
				return
			with self._state_lock:
				for trade in payload:
					try:
						order_id = str(trade.get("order_id"))
						if not order_id:
							continue
						entry = self._orders.get(order_id, {})
						entry.setdefault("trades", []).append(trade)
						self._orders[order_id] = entry
					except Exception:
						continue
			return

		# PTS / equity: "private.user.post-trade-settlement"
		if recipient == "private.user.post-trade-settlement":
			if not isinstance(payload, dict):
				return
			eq = payload.get("equity")
			try:
				if eq is not None:
					with self._state_lock:
						self._equity = float(eq)
			except (TypeError, ValueError):
				return

	def _process_open_orders_message(self, data: dict, orders_list: list) -> None:
		sequence = data.get("sequence")
		ts = data.get("timestamp")
		_trace_orders_line(sequence, "private.user.open-orders", orders_list)

		with self._state_lock:
			if self._awaiting_open_orders_snapshot:
				self._orders.clear()
				self._awaiting_open_orders_snapshot = False
				for order in orders_list:
					if not isinstance(order, dict):
						continue
					oid = str(order.get("id") or "")
					if not oid or _order_is_terminal(order):
						continue
					self._orders[oid] = dict(order)
			else:
				for order in orders_list:
					if not isinstance(order, dict):
						continue
					oid = str(order.get("id") or "")
					if not oid:
						continue
					if _order_is_terminal(order):
						self._orders.pop(oid, None)
						continue
					old = self._orders.get(oid)
					self._orders[oid] = _merge_order_preserving_trades(old, dict(order))

		envelope: Dict[str, Any] = {
			"sequence": sequence,
			"timestamp": ts,
			"recipient": "private.user.open-orders",
			"orders": list(orders_list),
		}
		cb = self._on_open_orders
		if cb is not None:
			try:
				cb(envelope)
			except Exception as e:
				log.error("on_open_orders callback error: %s", e)

	def _process_balances_message(self, data: dict, payload) -> None:
		sequence = data.get("sequence")
		ts = data.get("timestamp")
		# Web3 wallet messages are a single dict with type "web3" (see sFOX balances docs).
		if isinstance(payload, dict) and str(payload.get("type") or "").lower() == "web3":
			envelope: Dict[str, Any] = {
				"sequence": sequence,
				"timestamp": ts,
				"recipient": "private.user.balances",
				"payload": dict(payload),
			}
			cb = self._on_balances
			if cb is not None:
				try:
					cb(envelope)
				except Exception as e:
					log.error("on_balances callback error: %s", e)
			return

		if not isinstance(payload, list):
			return
		with self._state_lock:
			self._balances = [dict(x) for x in payload if isinstance(x, dict)]
			snap = [dict(x) for x in self._balances]

		envelope = {
			"sequence": sequence,
			"timestamp": ts,
			"recipient": "private.user.balances",
			"balances": snap,
		}
		cb = self._on_balances
		if cb is not None:
			try:
				cb(envelope)
			except Exception as e:
				log.error("on_balances callback error: %s", e)

	def set_on_open_orders(self, cb: Optional[Callable[[Dict[str, Any]], None]]) -> None:
		"""Register a callback for each ``private.user.open-orders`` message (runs on WS thread)."""
		self._on_open_orders = cb

	def set_on_balances(self, cb: Optional[Callable[[Dict[str, Any]], None]]) -> None:
		"""Register a callback for each ``private.user.balances`` message (runs on WS thread)."""
		self._on_balances = cb

	def _send_subscribe(self, feeds) -> None:
		# Track requested feeds so they can be re-sent after reconnect.
		for f in feeds:
			self._pending_feeds.add(f)

		# Do not send before on_open — avoids "socket is already closed" when subscribe
		# runs immediately after start() before the handshake completes.
		with self._ws_lock:
			if not self._connected or self._ws is None:
				return
		try:
			msg = {"type": "subscribe", "feeds": feeds}
			self._ws.send(json.dumps(msg))
		except Exception as e:
			log.error("Failed to send subscribe: %s", e)

	def subscribe_order_book(self, symbol: str) -> None:
		"""
		Subscribe to net order book snapshots for a symbol (e.g. btcusd).
		"""
		symbol = _normalize_ws_pair(symbol)
		self._subscribed_net_pairs.add(symbol)
		feed = f"orderbook.net.{symbol}"
		log.info("Subscribing to WS order book feed %s", feed)
		self._send_subscribe([feed])

	def subscribe_tickers(self, symbols: list[str]) -> None:
		"""
		Subscribe to aggregated ticker feeds ``ticker.sfox.<pair>`` (24h OHLCV + last, ~3s updates).

		See https://docs.sfox.com/websocket-api/market-data/ticker
		"""
		feeds = [
			f"ticker.sfox.{str(s).lower().strip().replace('/', '')}"
			for s in symbols
			if s
		]
		if not feeds:
			return
		log.info("Subscribing to WS ticker feeds %s", feeds)
		self._send_subscribe(feeds)

	def subscribe_order_books(self, symbols: list[str]) -> None:
		"""
		Subscribe to multiple net order books in one WebSocket message.

		Some servers replace the subscription list on each subscribe; sending one feed
		at a time can leave only the last pair receiving updates (stale or wrong column
		in multi-asset UIs). Prefer this when subscribing to several pairs at once.
		"""
		feeds = []
		for s in symbols:
			if not s:
				continue
			n = _normalize_ws_pair(s)
			self._subscribed_net_pairs.add(n)
			feeds.append(f"orderbook.net.{n}")
		if not feeds:
			return
		log.info("Subscribing to WS order book feeds %s", feeds)
		self._send_subscribe(feeds)

	def subscribe_orders(self) -> None:
		log.info("Subscribing to WS open-orders feed")
		self._send_subscribe(["private.user.open-orders"])

	def subscribe_trades(self) -> None:
		log.info("Subscribing to WS trades feed")
		self._send_subscribe(["private.user.trades"])

	def subscribe_pts(self) -> None:
		log.info("Subscribing to WS post-trade-settlement feed")
		self._send_subscribe(["private.user.post-trade-settlement"])

	def subscribe_balances(self) -> None:
		log.info("Subscribing to WS balances feed")
		self._send_subscribe(["private.user.balances"])

	# --- State accessors (used by pairs.py) ---

	def get_mid_price(self, symbol: str) -> Optional[float]:
		return self._mid_prices.get(symbol.lower())

	def get_best_bid_ask(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
		s = symbol.lower()
		return self._best_bids.get(s), self._best_asks.get(s)

	def get_ticker(self, symbol: str) -> Optional[dict]:
		"""Copy of last ticker payload for ``symbol``, or ``None`` if none yet."""
		s = symbol.lower()
		t = self._tickers.get(s)
		return dict(t) if t else None

	def get_order_book_snapshot(self, symbol: str) -> Optional[Dict[str, list]]:
		"""Last net order book for symbol: ``{"bids": [[price, size], ...], "asks": ...}`` (WS payload shape)."""
		return self._order_book_snapshots.get(symbol.lower())

	def get_order_book_feed_meta(self, symbol: str) -> Optional[Dict[str, Optional[int]]]:
		"""
		Per-feed timestamps from the last order book message (epoch **milliseconds**):

		- ``lastPublished`` — server publish time
		- ``lastUpdated`` — last update time

		Returns ``None`` if no snapshot has been received for this symbol.
		"""
		s = symbol.lower()
		m = self._order_book_feed_meta.get(s)
		if m is None:
			return None
		return dict(m)

	def get_order_status(self, order_id: str) -> Optional[dict]:
		with self._state_lock:
			o = self._orders.get(str(order_id))
			return dict(o) if o else None

	def get_open_orders_snapshot(self) -> Dict[str, dict]:
		"""Shallow copy of cached open orders keyed by order id (WS ``private.user.open-orders``)."""
		with self._state_lock:
			return {k: dict(v) for k, v in self._orders.items()}

	def get_balances_snapshot(self) -> List[dict]:
		"""Last account balances list from ``private.user.balances``, or empty if none yet."""
		with self._state_lock:
			return [dict(x) for x in self._balances]

	def get_equity(self) -> Optional[float]:
		with self._state_lock:
			return self._equity


