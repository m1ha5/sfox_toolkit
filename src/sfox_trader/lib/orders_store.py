#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


def _default_orders_db_path() -> str:
	"""Resolve ``db/orders.db`` relative to the repository root (parent of ``src/``)."""
	repo_root = Path(__file__).resolve().parents[3]
	return str(repo_root / "db" / "orders.db")


class OrdersStore:
	"""
	Thin wrapper around the SQLite orders database at db/orders.db.

	Assumes the orders table has already been created via db/create_order_table.sql.
	"""

	def __init__(self, db_path: Optional[str] = None) -> None:
		if db_path is None:
			db_path = _default_orders_db_path()
		self._conn = sqlite3.connect(db_path)

	def close(self) -> None:
		try:
			self._conn.close()
		except Exception:
			pass

	def record_grid_volume(
		self,
		run_id: str,
		asset: str,
		side_mode: str,
		*,
		buy_count: int,
		sell_count: int,
		buy_spread: float,
		sell_spread: float,
		full_spread: float,
		avg_price: float,
		current_volume: float,
		total_volume: float,
		total_orders: int,
		count_per_side: int,
		split: int,
		x: int,
		spread_m: int,
		dry_run: bool,
	) -> None:
		"""
		Record a single grid-volume snapshot for a batch, keyed by run_id.
		"""
		self._conn.execute(
			"""
			INSERT INTO grid_volume (
				run_id, asset, side_mode,
				buy_count, sell_count,
				buy_spread, sell_spread, full_spread,
				avg_price, current_volume, total_volume, total_orders,
				count_per_side, split, x, spread_m, dry_run
			) VALUES (
				?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
			)
			""",
			(
				run_id,
				asset,
				side_mode,
				buy_count,
				sell_count,
				buy_spread,
				sell_spread,
				full_spread,
				avg_price,
				current_volume,
				total_volume,
				total_orders,
				count_per_side,
				split,
				x,
				spread_m,
				1 if dry_run else 0,
			),
		)
		self._conn.commit()

	def record_batch(
		self,
		run_id: str,
		asset: str,
		order_params: List[Tuple[str, Dict[str, Any], str]],
		responses: List[Dict[str, Any]],
	) -> None:
		"""
		Persist a batch of responses corresponding 1:1 with order_params.
		"""
		if not order_params or not responses:
			return
		cur = self._conn.cursor()

		for (side, _payload, _cid_prefix), resp in zip(order_params, responses):
			# Normalize response shape
			r = resp or {}
			is_error_only = isinstance(r, dict) and "error" in r and len(r) == 1

			if is_error_only:
				error = r.get("error")
				row_vals = (
					run_id,
					asset,
					side,
					None,  # side_id
					None,  # action
					None,  # algorithm_id
					None,  # algorithm
					None,  # type
					None,  # pair
					None,  # quantity
					None,  # price
					None,  # amount
					None,  # net_market_amount
					None,  # filled
					None,  # vwap
					None,  # filled_amount
					None,  # fees
					None,  # net_proceeds
					None,  # status
					None,  # status_code
					None,  # routing_option
					None,  # routing_type
					None,  # time_in_force
					None,  # expires
					None,  # dateupdated
					None,  # date_added
					None,  # client_order_id
					None,  # user_tx_id
					None,  # o_action
					None,  # algo_id
					None,  # destination
					None,  # order_id
					error,
				)
			else:
				error = None
				row_vals = (
					run_id,
					asset,
					side,
					r.get("side_id"),
					r.get("action"),
					r.get("algorithm_id"),
					r.get("algorithm"),
					r.get("type"),
					r.get("pair"),
					r.get("quantity"),
					r.get("price"),
					r.get("amount"),
					r.get("net_market_amount"),
					r.get("filled"),
					r.get("vwap"),
					r.get("filled_amount"),
					r.get("fees"),
					r.get("net_proceeds"),
					r.get("status"),
					r.get("status_code"),
					r.get("routing_option"),
					r.get("routing_type"),
					r.get("time_in_force"),
					r.get("expires"),
					r.get("dateupdated"),
					r.get("date_added"),
					r.get("client_order_id"),
					r.get("user_tx_id"),
					r.get("o_action"),
					r.get("algo_id"),
					r.get("destination"),
					r.get("id"),  # order_id
					error,
				)

			# 1) insert into orders
			cur.execute(
				"""
				INSERT INTO orders (
					run_id, asset, side, side_id, action, algorithm_id, algorithm, type, pair,
					quantity, price, amount, net_market_amount, filled, vwap, filled_amount,
					fees, net_proceeds, status, status_code, routing_option, routing_type,
					time_in_force, expires, dateupdated, date_added, client_order_id,
					user_tx_id, o_action, algo_id, destination, order_id, error
				) VALUES (
					?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
				)
				""",
				row_vals,
			)
			order_row_id = cur.lastrowid

			# 2) insert raw response JSON into order_responses
			cur.execute(
				"""
				INSERT INTO order_responses (order_row_id, response_json)
				VALUES (?, ?)
				""",
				(order_row_id, json.dumps(r)),
			)

		self._conn.commit()

