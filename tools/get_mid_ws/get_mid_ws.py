#!/usr/bin/env python3
# -*- coding: utf-8; py-indent-offset:4 -*-
"""
Simple tester for SFOXWebSocketClient mid-price updates.

Usage:
  ./bin/get_mid_ws --pair btcusd
  python3 tools/get_mid_ws/get_mid_ws.py --pair btcusd

Requires:
  - SFOX_API_KEY env var or --key argument.
  - websocket-client installed (python -m pip install websocket-client).
"""

import argparse
import logging
import os
import sys
import time

from sfox_trader.lib.sfox_ws import SFOXWebSocketClient


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Stream mid-price via SFOX WebSocket order book feed")
	parser.add_argument(
		"--pair",
		required=True,
		help="Currency pair symbol, e.g. btcusd, ethusd",
	)
	parser.add_argument(
		"-k",
		"--key",
		default=None,
		help="SFOX API Key (defaults to SFOX_API_KEY env var)",
	)
	parser.add_argument(
		"--interval",
		type=float,
		default=1.0,
		help="Seconds between prints (default: 1.0)",
	)
	return parser


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(message)s")
	parser = build_parser()
	args = parser.parse_args()

	api_key = args.key or os.environ.get("SFOX_API_KEY")
	if not api_key:
		parser.error("SFOX API key must be provided via --key or SFOX_API_KEY env var")

	pair = args.pair.lower()

	ws_client = SFOXWebSocketClient(api_key)
	ws_client.start()
	ws_client.subscribe_order_book(pair)

	try:
		while True:
			mid = ws_client.get_mid_price(pair)
			if mid is not None:
				print(f"{pair} mid={mid:.8f}")
			else:
				print(f"{pair} mid=NA (waiting for snapshot)")
			time.sleep(args.interval)
	except KeyboardInterrupt:
		print("Stopping...")
	finally:
		ws_client.stop()


if __name__ == "__main__":
	main()

