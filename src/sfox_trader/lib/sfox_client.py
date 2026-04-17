#!/usr/bin/env python3
# -*- coding: utf-8; py-indent-offset:4 -*-

"""
SFOX API client for REST API communication.
Uses Basic Authentication with API key as username.
"""

import json
import time
import requests

REQUEST_TIMEOUT = 30
DEBUG = False

# Market chart data (separate host from api.sfox.com); see
# https://docs.sfox.com/rest-api/market-data/get-candlesticks
CHARTDATA_CANDLESTICKS_URL = "https://chartdata.sfox.com/candlesticks"

class SFOXTrader:
	"""
	SFOXTrader handles all REST API communication with SFOX endpoints.
	Utilizes Basic Authentication with API Key as the username.
	"""
	def __init__(self, api_key):
		self.api_key = api_key
		self.base_url = "https://api.sfox.com/v1"
		self.auth = (self.api_key, "")

	def _request(self, method, url, **kwargs):
		"""Execute HTTP request with timeout, status check, and JSON parsing."""
		kwargs.setdefault("timeout", REQUEST_TIMEOUT)
		response = method(url, auth=self.auth, **kwargs)
		response.raise_for_status()
		try:
			return response.json()
		except json.JSONDecodeError as e:
			raise ValueError(f"Invalid JSON response from {url}: {e}") from e

	def _place_order(self, side, payload, client_id_prefix):
		"""
		Internal helper to submit POST requests for order placement.
		Generates a unique client_order_id for idempotency.
		"""
		url = f"{self.base_url}/orders/{side.lower()}"
		unique_suffix = payload.pop('_unique_tag', int(time.time()*1000))
		payload["client_order_id"] = f"{client_id_prefix}_{unique_suffix}"
		payload["routing_type"] = "NetPrice"

		try:
			response = requests.post(url, auth=self.auth, data=payload, timeout=REQUEST_TIMEOUT)
			response.raise_for_status()
			try:
				return response.json()
			except json.JSONDecodeError as e:
				return {"error": f"Invalid JSON response: {e}", "client_order_id": payload.get("client_order_id")}
		except requests.RequestException as e:
			return {"error": str(e), "client_order_id": payload.get("client_order_id")}

	# --- Account & Market Management Methods ---

	def get_balances(self):
		"""Retrieves total and available balances for all account currencies."""
		return self._request(requests.get, f"{self.base_url}/user/balance")

	def get_currencies(self):
		"""Lists all currencies supported by the SFOX account."""
		return self._request(requests.get, f"{self.base_url}/currency")

	def get_currency_pairs(self):
		"""Lists all active trading pairs (e.g., btcusd, ethusd)."""
		return self._request(requests.get, f"{self.base_url}/currency-pairs")

	def get_fees(self):
		"""Retrieves taker, maker, and volume-based fee rates."""
		return self._request(requests.get, f"{self.base_url}/account/fee-rates")

	def get_all_transactions(self, limit=50):
		"""Fetches recent ledger history including trades and transfers."""
		params = {"limit": limit}
		return self._request(requests.get, f"{self.base_url}/account/transactions", params=params)

	def get_margin_account(self):
		"""
		Retrieves account risk metrics for margin/shorting.
		Raises on 403 if account is not enabled for margin trading.
		"""
		return self._request(requests.get, f"{self.base_url}/margin/account")

	def get_margin_positions(self, status="active", **kwargs):
		"""Retrieves margin/loan positions. status: 'active' or 'closed'."""
		params = {"status": status, **kwargs}
		return self._request(requests.get, f"{self.base_url}/margin/loans", params=params)

	def get_pts_account(self):
		"""Retrieves Post-Trade Settlement account risk metrics. Raises on 403 if PTS is disabled."""
		return self._request(requests.get, f"{self.base_url}/post-trade-settlement")

	def get_pts_positions(self, status="active", **kwargs):
		"""Retrieves PTS positions. status: 'active' or 'closed'."""
		params = {"status": status, **kwargs}
		return self._request(requests.get, f"{self.base_url}/post-trade-settlement/positions", params=params)

	def get_pts_funding(self, **kwargs):
		"""Retrieves PTS funding/interest transactions."""
		return self._request(requests.get, f"{self.base_url}/post-trade-settlement/interest/history", params=kwargs)

	def get_pts_funding_rates(self):
		"""Retrieves PTS funding rates and terms per currency."""
		return self._request(requests.get, f"{self.base_url}/post-trade-settlement/interest")

	def get_pts_risk_modes(self):
		"""Retrieves risk mode for each currency pair."""
		return self._request(requests.get, f"{self.base_url}/post-trade-settlement/risk-modes")

	def get_portfolio_valuation(self, months_back=1):
		"""Fetches daily historical USD valuation of the entire portfolio."""
		url = f"{self.base_url}/account/balance/history"
		end_date = int(time.time() * 1000)
		start_date = end_date - (months_back * 30 * 24 * 60 * 60 * 1000)
		params = {
			"start_date": start_date,
			"end_date": end_date,
			"interval": 86400
		}
		return self._request(requests.get, url, params=params)

	def amend_order(self, order_id, **fields):
		"""
		Amend an existing order's quantity/price/stop fields without cancelling.

		See https://docs.sfox.com/rest-api/orders/amend-order
		Docs use JSON body; form-encoded PATCH often yields 400.
		"""
		return self._request(
			requests.patch,
			f"{self.base_url}/orders/{order_id}",
			json=fields,
		)

	# --- Order Status & Cancellation Methods ---

	def get_open_orders(self, limit=50, currency_pair=None):
		"""
		Retrieves all currently active/started orders.
		Paginates through results (API max 50 per request).
		Optionally filter by currency_pair (e.g. btcusd). Action (buy/sell) must be filtered client-side.
		"""
		all_orders = []
		params = {"limit": limit}
		if currency_pair:
			params["currency_pair"] = currency_pair.lower()
		while True:
			batch = self._request(requests.get, f"{self.base_url}/orders", params=params)
			if not batch:
				break
			all_orders.extend(batch)
			if len(batch) < limit:
				break
			params = {"limit": limit, "before": min(o["id"] for o in batch)}
			if currency_pair:
				params["currency_pair"] = currency_pair.lower()
		return all_orders

	def get_done_orders(self, max_results=50, currency_pair=None):
		"""
		Retrieves done orders. max_results=50: single request; max_results=250: 5 requests with rate limiting.
		Optionally filter by currency_pair. Action (buy/sell) must be filtered client-side.
		"""
		PAGE_SIZE = 50
		all_orders = []
		params = {"limit": PAGE_SIZE}
		if currency_pair:
			params["currency_pair"] = currency_pair.lower()
		PAGINATION_DELAY = 2.1
		while len(all_orders) < max_results:
			batch = self._request(requests.get, f"{self.base_url}/orders/done", params=params)
			if not batch:
				break
			all_orders.extend(batch[:max_results - len(all_orders)])
			if len(batch) < PAGE_SIZE:
				break
			if len(all_orders) >= max_results:
				break
			time.sleep(PAGINATION_DELAY)
			params = {"limit": PAGE_SIZE, "before": min(o["id"] for o in batch)}
			if currency_pair:
				params["currency_pair"] = currency_pair.lower()
		return all_orders

	def get_order_by_id(self, order_id):
		"""Retrieves granular details for a specific 7+ digit order ID."""
		return self._request(requests.get, f"{self.base_url}/orders/{order_id}")

	def cancel_order(self, order_id):
		"""Cancels a single specific order."""
		return self._request(requests.delete, f"{self.base_url}/orders/{order_id}")

	def cancel_multiple_orders(self, order_ids):
		"""Cancels a list of order IDs via comma-separated query."""
		params = {"ids": ",".join(map(str, order_ids))}
		return self._request(requests.delete, f"{self.base_url}/orders", params=params)

	def cancel_all_orders(self):
		"""Global cancellation of all open orders."""
		return self._request(requests.delete, f"{self.base_url}/orders/open")

	# --- Marketbook Methods ---

	def get_current_quote(self, ticker, side, quantity=10):
		if ticker in ["btcusd", "ethusd"]:
			quantity = 0.1
		elif ticker in ["dogeusd", "sushiusd"]:
			quantity = 100
		params = {"pair": ticker, "quantity": quantity, "routing_type": "NetPrice"}
		return self._request(requests.get, f"{self.base_url}/offer/{side}", params=params)

	def get_current_ob(self, ticker):
		params = {"pair": ticker, "routing_type": "NetPrice"}
		return self._request(requests.get, f"{self.base_url}/markets/orderbook/{ticker}", params=params)

	def get_best_bid(self, ticker):
		params = {"pair": ticker, "routing_type": "NetPrice"}
		res = self._request(requests.get, f"{self.base_url}/markets/orderbook/{ticker}", params=params)
		return res["market_making"]["bids"][-1]

	def get_best_ask(self, ticker):
		params = {"pair": ticker, "routing_type": "NetPrice"}
		res = self._request(requests.get, f"{self.base_url}/markets/orderbook/{ticker}", params=params)
		return res["market_making"]["asks"][-1]

	def get_ob_mid_mm(self, ticker):
		params = {"pair": ticker, "routing_type": "NetPrice"}
		res = self._request(requests.get, f"{self.base_url}/markets/orderbook/{ticker}", params=params)
		bids = res.get("market_making", {}).get("bids", [])
		asks = res.get("market_making", {}).get("asks", [])
		if not bids:
			print(res["market_making"])
			raise ValueError(f"Order book empty: no bids for {ticker}")
		if not asks:
			print(res["market_making"])
			raise ValueError(f"Order book empty: no asks for {ticker}")
		best_bid = bids[-1][0]
		best_ask = asks[-1][0]

		if DEBUG:
			print("mm best_bid: " + str(best_bid) + " | mm best_ask: " + str(best_ask))
		return round((best_bid + best_ask) / 2, 8)

	def get_candlesticks(
		self,
		pair: str,
		start_time: int,
		end_time: int,
		period: int = 60,
	):
		"""
		Historical OHLCV candles. ``start_time`` / ``end_time`` are Unix **seconds**.
		``period`` is bar length in seconds (60, 300, 900, 3600, 21600, 86400 per API).
		Max 500 candles per request.

		See https://docs.sfox.com/rest-api/market-data/get-candlesticks
		"""
		pair = str(pair).lower().strip().replace("/", "")
		params = {
			"pair": pair,
			"startTime": int(start_time),
			"endTime": int(end_time),
			"period": int(period),
		}
		headers = {"Authorization": f"Bearer {self.api_key}"}
		r = requests.get(
			CHARTDATA_CANDLESTICKS_URL,
			params=params,
			headers=headers,
			timeout=REQUEST_TIMEOUT,
		)
		# Docs show Bearer; some environments accept the same key as REST Basic.
		if r.status_code == 401:
			r = requests.get(
				CHARTDATA_CANDLESTICKS_URL,
				params=params,
				auth=self.auth,
				timeout=REQUEST_TIMEOUT,
			)
		r.raise_for_status()
		try:
			data = r.json()
		except json.JSONDecodeError as e:
			raise ValueError(f"Invalid JSON from candlesticks: {e}") from e
		if not isinstance(data, list):
			raise ValueError("candlesticks response is not a list")
		return data

	def get_ob_mid_non_mm(self, ticker):
		params = {"pair": ticker, "routing_type": "NetPrice"}
		res = self._request(requests.get, f"{self.base_url}/markets/orderbook/{ticker}", params=params)
		bids = res.get("bids", [])
		asks = res.get("asks", [])
		if not bids:
			print(res["market_making"])
			raise ValueError(f"Order book empty: no bids for {ticker}")
		if not asks:
			print(res["market_making"])
			raise ValueError(f"Order book empty: no asks for {ticker}")
		best_bid = bids[0][0]
		best_ask = asks[0][0]
		if DEBUG:
			print("best_bid: " + str(best_bid) + " | best_ask: " + str(best_ask))
		return round((best_bid + best_ask) / 2, 8)
