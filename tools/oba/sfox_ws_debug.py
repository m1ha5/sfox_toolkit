#!/usr/bin/env python3
"""
Debug: subscribe to sFOX WebSocket feeds for given assets and print each message.

From this directory (``tools/oba/``):

  export SFOX_API_KEY=...
  python3 sfox_ws_debug.py -a btcusd,btcusdt,ethusd

  # Compare REST order book vs WebSocket (REST printed before / after run)
  python3 sfox_ws_debug.py -a ethusd,ethusdc -r

  # Stop after first order book frame (sequence-limited), or 30s max
  python3 sfox_ws_debug.py -a ethusd -t 1 -s 30

From repo root:

  python3 tools/oba/sfox_ws_debug.py -a ethusd,ethusdc

Requires: pip install websocket-client requests (``requests`` is used only with ``-r``)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

try:
	import websocket  # type: ignore
except ImportError:
	print("Install websocket-client:  python3 -m pip install websocket-client", file=sys.stderr)
	sys.exit(1)

WS_URL = "wss://ws.sfox.com/ws"

from sfox_trader.lib.pair_utils import usd_usdc_cross_book_error


def _print_rest_block(label: str, api_key: str, assets: list[str], pretty: bool) -> None:
	from sfox_trader.lib.sfox_client import SFOXTrader  # lazy import so ``-r`` not required

	tr = SFOXTrader(api_key)
	print(f"=== REST {label} ===", flush=True)
	for sym in assets:
		try:
			data = tr.get_current_ob(sym)
		except Exception as e:
			print(json.dumps({"source": "REST", "pair": sym, "error": str(e)}), flush=True)
			continue
		wrap = {"source": "REST", "pair": sym, "path": f"GET /v1/markets/orderbook/{sym}", "data": data}
		if pretty:
			print(json.dumps(wrap, indent=2), flush=True)
		else:
			print(json.dumps(wrap, separators=(",", ":")), flush=True)
	print(f"=== end REST {label} ===", flush=True)


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Print raw sFOX WS messages; optional REST order books for comparison.",
	)
	parser.add_argument(
		"-a",
		"--assets",
		required=True,
		help="Comma-separated pairs, e.g. ethusd,ethusdc,ethusdt",
	)
	parser.add_argument("-k", "--key", default=None, help="API key (else env SFOX_API_KEY)")
	parser.add_argument(
		"-s",
		"--seconds",
		type=float,
		default=2.0,
		help="Max seconds to stay connected on WebSocket (default: 2). Stops when this elapses or -t is satisfied.",
	)
	parser.add_argument(
		"-t",
		"--max-sequences",
		type=int,
		default=None,
		metavar="N",
		help="Stop after N WebSocket order book messages (recipient orderbook.*). Omit for no sequence cap.",
	)
	parser.add_argument(
		"-r",
		"--rest",
		action="store_true",
		help="Fetch REST /markets/orderbook/<pair> (NetPrice) before and after the WS run for comparison.",
	)
	parser.add_argument(
		"--pretty",
		action="store_true",
		help="Pretty-print JSON (one message can be many lines)",
	)
	args = parser.parse_args()

	api_key = args.key or os.environ.get("SFOX_API_KEY")
	if not api_key:
		parser.error("Set SFOX_API_KEY or pass --key")

	assets = [x.strip().lower().replace("/", "") for x in args.assets.split(",") if x.strip()]
	if not assets:
		parser.error("No assets after parsing -a")

	xc = usd_usdc_cross_book_error(assets)
	if xc:
		parser.error(xc)

	if args.rest:
		_print_rest_block("before WebSocket", api_key, assets, args.pretty)

	feeds: list[str] = []
	for sym in assets:
		feeds.append(f"orderbook.net.{sym}")
		feeds.append(f"ticker.sfox.{sym}")

	ob_msg_count = [0]
	done = threading.Event()

	def on_message(_ws, message: str) -> None:
		obj = None
		if args.pretty:
			try:
				obj = json.loads(message)
				print(json.dumps(obj, indent=2), flush=True)
			except json.JSONDecodeError:
				print(message, flush=True)
		else:
			print(message, flush=True)
			try:
				obj = json.loads(message)
			except json.JSONDecodeError:
				obj = None

		if args.max_sequences is None or obj is None:
			return
		rec = obj.get("recipient")
		if isinstance(rec, str) and rec.lower().startswith("orderbook."):
			ob_msg_count[0] += 1
			if ob_msg_count[0] >= args.max_sequences:
				done.set()
				try:
					_ws.close()
				except Exception:
					pass

	def on_open(ws) -> None:
		ws.send(json.dumps({"type": "authenticate", "apiKey": api_key}))
		ws.send(json.dumps({"type": "subscribe", "feeds": feeds}))

	def on_error(_ws, error) -> None:
		print("WS error:", error, file=sys.stderr, flush=True)

	app = websocket.WebSocketApp(
		WS_URL,
		on_open=on_open,
		on_message=on_message,
		on_error=on_error,
	)

	def run_timer() -> None:
		try:
			time.sleep(max(0.05, float(args.seconds)))
		finally:
			if not done.is_set():
				done.set()
				try:
					app.close()
				except Exception:
					pass

	th = threading.Thread(
		target=app.run_forever,
		kwargs={"ping_interval": 20, "ping_timeout": 10},
		daemon=True,
	)
	timer_th = threading.Thread(target=run_timer, daemon=True)
	th.start()
	timer_th.start()
	th.join(timeout=max(float(args.seconds) + 15.0, 20.0))
	if not done.is_set():
		try:
			app.close()
		except Exception:
			pass
	th.join(timeout=5.0)

	if args.rest:
		_print_rest_block("after WebSocket", api_key, assets, args.pretty)


if __name__ == "__main__":
	main()
