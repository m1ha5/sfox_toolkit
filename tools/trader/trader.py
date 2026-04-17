#!/usr/bin/env python3
# -*- coding: utf-8; py-indent-offset:4 -*-
# vim: set noexpandtab tabstop=4 shiftwidth=4:

"""
SFOX Trading Utility - Version 2.56
Updates:
- Restored help messages and technical documentation comments.
- Integrated 'daily_value' under --account to fetch portfolio valuation history.
- Parallel Grid Orders (--scale) with multiprocessing.
- Automated CSV Logging for trade responses.
"""

import argparse
import csv
import json
import logging
import locale
import pprint
import math
import os
import pickle
import requests
import sys
import time
from datetime import datetime
from decimal import Decimal
from multiprocessing import Pool, cpu_count

# Attempt to import tabulate for professional table output
try:
    from tabulate import tabulate
except ImportError:
    tabulate = None

# Logging setup: INFO to stdout, ERROR to stderr
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
log.propagate = False
log.handlers.clear()
h_stdout = logging.StreamHandler(sys.stdout)
h_stdout.setLevel(logging.INFO)
h_stdout.addFilter(lambda r: r.levelno < logging.ERROR)
h_stdout.setFormatter(logging.Formatter("%(message)s"))
h_stderr = logging.StreamHandler(sys.stderr)
h_stderr.setLevel(logging.ERROR)
h_stderr.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(h_stdout)
log.addHandler(h_stderr)

# Set USD locale for currency formatting (fallback to manual if locale unavailable)
try:
    locale.setlocale(locale.LC_MONETARY, ("en_US", "UTF-8"))
    _USE_LOCALE_CURRENCY = True
except (locale.Error, OSError):
    _USE_LOCALE_CURRENCY = False

from sfox_trader.lib.sfox_client import SFOXTrader


# --- trader.py ---

VERSION = "2.56"


def get_version():
    """Release version string (inline; not derived from git)."""
    return VERSION

def format_sig_figs(val, sig_figs=4):
    """Dynamically formats float precision to maintain significant digits without scientific notation."""
    try:
        val = float(val)
        if val == 0: return "0.000"
        magnitude = math.floor(math.log10(abs(val)))
        decimals = max(0, (sig_figs - 1) - magnitude)
        return f"{val:.{decimals}f}"
    except (ValueError, TypeError):
        return val

def format_usd(val):
    """Formats numeric value as USD with $ and comma separators (e.g. $300,000.00). Uses locale.currency when available."""
    try:
        val = float(val)
        if _USE_LOCALE_CURRENCY:
            return locale.currency(val, grouping=True)
        return f"${val:,.2f}"
    except (ValueError, TypeError):
        return val

def worker_place_order(args_tuple):
    """Worker function used by Multiprocessing Pool for concurrent order placement."""
    trader_obj, side, payload, cid_prefix = args_tuple
    return trader_obj._place_order(side, payload, cid_prefix)

def get_lhnp(args, price):

    low, high, num = map(str, args.scale.split(","))
    if "now" in args.scale:
    # we need to fix high and low here;
        bips = high
        if args.side == 'sell':
            low = round(price,3)
            high = round((1 + float(bips)/10000) * low,3)
        elif args.side == 'buy':  #args.side == 'buy':
            high = round(price,3)
            low = round((1 - float(bips)/10000) * high,3)
        log.info("low is %s high: %s", low, high)

    low = float(low)
    high = float(high)
    num = int(num)
    price_step = (high - low) / (num) if num > 1 else 0

    return low,high,num,price_step

def transform_params(old_params, new_api_key, new_cid_prefix):
    """
    Takes an existing list of order_params, flips the side,
    and re-initializes them for a new API instance.
    """
    new_trader = SFOXTrader(new_api_key)
    new_params = []

    for _, side, payload, _ in old_params:
        new_side = 'sell' if side == 'buy' else 'buy'
        new_payload = payload.copy()
        new_payload['_unique_tag'] = int(time.time() * 1000)
        new_params.append((new_trader, new_side, new_payload, new_cid_prefix))

    return new_params


def handle_order_status(trader, args):
    """Handle --order_status: open, done, done_recent, or numeric order ID."""
    status = args.order_status.lower()
    pair = args.pair.lower() if args.pair else None
    action = args.side.lower() if args.side and args.side.lower() in ("buy", "sell") else None
    if status == 'open':
        data = trader.get_open_orders(currency_pair=pair)
    elif status == 'done':
        data = trader.get_done_orders(max_results=50, currency_pair=pair)
    elif status == 'done_recent':
        data = trader.get_done_orders(max_results=250, currency_pair=pair)
    else:
        data = None
    if data is not None:
        if action:
            data = [o for o in data if o.get("action", "").lower() == action]
        if tabulate and data:
            cols = ["id", "client_order_id", "pair", "action", "quantity","price", "filled", "quantity", "status"]
            fmt = [{k: format_sig_figs(v) if k in ["quantity", "filled"] else v for k, v in o.items() if k in cols} for o in data]
            log.info(tabulate(fmt, headers="keys", tablefmt="pretty"))
            log.info("Total Open Orders: %s", len(data))
        else:
            log.info(data)
    else:
        log.info(pprint.pformat(trader.get_order_by_id(args.order_status)))
    sys.exit(0)


def _pts_403(e):
    """Return True if HTTP 403 (PTS disabled); log and return False otherwise."""
    if e.response is not None and e.response.status_code == 403:
        try:
            err = e.response.json()
            print("foo")
        except (json.JSONDecodeError, ValueError):
            err = {}
        msg = err.get("error", getattr(e.response, "text", None) or str(e))
        log.error("Post-trade settlement disabled: %s", msg)
        return True
    return False


def handle_account(trader, args, parser):
    """Handle --account: balance, pairs, fees, transactions, shorting, short_positions, pts, daily_value."""
    action = args.account[0]
    if action == 'daily_value':
        months = 1
        if len(args.account) > 1:
            try:
                months = int(args.account[1].upper().replace('M', ''))
            except ValueError:
                parser.error("Invalid month format. Use e.g., 2M")
        res = trader.get_portfolio_valuation(months)
        log.info("timestamp,epoch,usd_value")
        for entry in res.get('data', []):
            ms = entry['timestamp']
            human_time = datetime.fromtimestamp(ms/1000).strftime('%Y-%m-%d %H:%M:%S')
            log.info("%s,%s,%s", human_time, ms, entry['usd_value'])
    elif action == 'balance':
        data = trader.get_balances()
        data = [b for b in data if float(b.get('available', 0)) != 0 or float(b.get('balance', 0)) != 0]
        fmt = [{"currency": b['currency'], "available": format_sig_figs(b['available']), "balance": format_sig_figs(b['balance'])} for b in data]
        if tabulate:
            log.info(tabulate(fmt, headers="keys", tablefmt="pretty"))
        else:
            log.info("%s", fmt)
    elif action == 'transactions':
        data = trader.get_all_transactions()
        if tabulate:
            cols = ["day", "action", "currency", "amount", "price", "status"]
            fmt = [{k: format_sig_figs(v) if k in ["amount", "price"] else v for k, v in t.items() if k in cols} for t in data]
            log.info(tabulate(fmt, headers="keys", tablefmt="pretty"))
        else:
            log.info("%s", data)
    elif action == 'fees':
        log.info("%s", trader.get_fees())
    elif action == 'pairs':
        log.info("%s", trader.get_currency_pairs())
    elif action == 'shorting':
        try:
            risk = trader.get_margin_account()
            fmt = [{"metric": k, "value": format_sig_figs(v) if isinstance(v, (int, float)) else v} for k, v in risk.items()]
            if tabulate:
                log.info("Shorting enabled\n")
                log.info(tabulate(fmt, headers="keys", tablefmt="pretty", showindex=False))
            else:
                log.info("Shorting enabled")
                log.info("%s", pprint.pformat(risk))
            positions = trader.get_margin_positions()
            if positions.get("data"):
                log.info("\nActive positions:")
                if tabulate:
                    cols = ["id", "pair", "margin_type", "status", "current_loan_qty", "proceeds", "vwap", "interest_rate"]
                    fmt_pos = [{k: format_sig_figs(v) if k in ["current_loan_qty", "proceeds", "vwap", "interest_rate", "interest_qty"] else v
                               for k, v in p.items() if k in cols} for p in positions["data"]]
                    log.info(tabulate(fmt_pos, headers="keys", tablefmt="pretty"))
                else:
                    log.info("%s", pprint.pformat(positions["data"]))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                try:
                    err = e.response.json()
                except (json.JSONDecodeError, ValueError):
                    err = {}
                msg = err.get("error", getattr(e.response, "text", None) or str(e))
                log.error("Shorting not enabled: %s", msg)
            else:
                raise
    elif action == 'pts':
        try:
            sub = args.account[1].lower() if len(args.account) > 1 else None
            if sub is None:
                data = trader.get_pts_account()
                data["equity_level"] = round(data["equity"]/data["exposure"] * 100,1)
                data["equity_level"] = str(data["equity_level"]) + "%"
                data["liquidation_level"] = str(round(data["liquidation_level"]*100,1)) + "%"
                fmt = [{"metric": k, "value": format_usd(v) if isinstance(v, (int, float)) else v} for k, v in data.items()]
                if tabulate:
                    log.info("Post-Trade Settlement\n")
                    log.info(tabulate(fmt, headers="keys", tablefmt="pretty", showindex=False, colalign=("left", "right")))
                else:
                    log.info("%s", pprint.pformat(data))
            elif sub == 'positions':
                params = {}
                if len(args.account) > 2:
                    params["pair"] = args.account[2].lower()
                res = trader.get_pts_positions(**params)
                if isinstance(res, list):
                    data = res
                elif isinstance(res, dict) and "data" in res:
                    data = res["data"]
                elif isinstance(res, dict) and "id" in res:
                    data = [res]
                else:
                    data = []
                if not isinstance(data, list):
                    data = [data] if data else []
                if tabulate and data:
                    cols = ["id", "pair", "status", "margin_type", "loan_currency_symbol", "current_loan_qty",
                            "proceeds", "vwap", "interest_rate", "order_id_open", "date_added"]
                    fmt = [{k: format_sig_figs(v) if k in ["current_loan_qty", "proceeds", "vwap",
                            "interest_rate", "interest_qty"] else v for k, v in p.items() if k in cols} for p in data]
                    log.info(tabulate(fmt, headers="keys", tablefmt="pretty"))
                    log.info("Total PTS Positions: %s", len(data))
                else:
                    log.info("%s", data)
            elif sub == 'funding':
                res = trader.get_pts_funding()
                data = res.get("data", [])
                if tabulate and data:
                    cols = ["id", "currency", "amount", "amount_usd", "date_added", "position_id"]
                    fmt = [{k: format_sig_figs(v) if k in ["amount", "amount_usd"] else v
                           for k, v in t.items() if k in cols} for t in data]
                    log.info(tabulate(fmt, headers="keys", tablefmt="pretty"))
                    log.info("Total: %s", len(data))
                else:
                    log.info("%s", res)
            elif sub == 'rates':
                res = trader.get_pts_funding_rates()
                data = []
                for cur, vals in res.items():
                    if isinstance(vals, dict):
                        rate = vals.get("interest_rate")
                        apr = (rate * 100) if rate is not None else None
                        row = {"currency": cur, "APR": apr,
                               "interest_frequency_hours": vals.get("interest_frequency_minutes", 0) / 60,
                               "interest_grace_period_hours": vals.get("interest_grace_period_minutes", 0) / 60}
                        data.append(row)
                if tabulate and data:
                    cols = ["currency", "APR", "interest_frequency_hours", "interest_grace_period_hours"]
                    fmt = [{k: (f"{v:.2f}%" if k == "APR" and v is not None else v) for k, v in row.items() if k in cols} for row in data]
                    log.info(tabulate(fmt, headers="keys", tablefmt="pretty", colalign=("left", "right", "right", "right")))
                    log.info("Total: %s", len(data))
                else:
                    log.info("%s", res)
            elif sub == 'cr':
                res = trader.get_pts_risk_modes()
                data = res.get("data", [])
                pair = args.account[2].lower() if len(args.account) > 2 else None
                if pair:
                    data = [r for r in data if r.get("currency_pair", "").lower() == pair]
                    if not data:
                        log.info("Risk mode for %s: Not found", pair)
                    elif tabulate:
                        log.info(tabulate(data, headers="keys", tablefmt="pretty"))
                        log.info("Risk mode: %s", data[0].get('risk_mode', 'N/A'))
                    else:
                        log.info("%s", data)
                elif tabulate and data:
                    log.info(tabulate(data, headers="keys", tablefmt="pretty"))
                    log.info("Total: %s", len(data))
                else:
                    log.info("%s", data)
            else:
                parser.error(f"Unknown pts sub-command: {sub}. Use: positions, funding, rates, cr [pair]")
        except requests.HTTPError as e:
            if not _pts_403(e):
                raise
    elif action == 'short_positions':
        try:
            res = trader.get_margin_positions(status="active")
            data = res.get("data", [])
            if tabulate and data:
                cols = ["id", "pair", "status", "margin_type", "loan_currency", "current_loan_qty",
                        "original_loan_qty", "proceeds", "vwap", "interest_rate", "order_id", "date_added"]
                fmt = [{k: format_sig_figs(v) if k in ["current_loan_qty", "original_loan_qty", "proceeds",
                        "vwap", "interest_rate", "interest_qty"] else v for k, v in p.items() if k in cols} for p in data]
                log.info(tabulate(fmt, headers="keys", tablefmt="pretty"))
                log.info("Total Short Positions: %s", len(data))
            else:
                log.info("%s", data)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                try:
                    err = e.response.json()
                except (json.JSONDecodeError, ValueError):
                    err = {}
                msg = err.get("error", getattr(e.response, "text", None) or str(e))
                log.error("Shorting not enabled: %s", msg)
            else:
                raise
    else:
        parser.error(f"Unkown command: {action}.  Usage: balance,pts,fees, pairs, short_positions,")
    sys.exit(0)


def handle_cancellations(trader, args):
    """Handle --cancel_all or --cancel_ids."""
    if args.cancel_all:
        log.info("%s", trader.cancel_all_orders())
        sys.exit(0)
    if args.cancel_ids:
        ids = args.cancel_ids.split(",")
        log.info("%s", trader.cancel_multiple_orders(ids) if len(ids) > 1 else trader.cancel_order(ids[0]))
        sys.exit(0)


def handle_create_order(trader, args, parser):
    """Handle --create_order: manual, scaled, or both-side grid orders."""
    algo_map = {'market': 100, 'smart': 200, 'limit': 201, 'gorilla': 301, 'turtle': 302, 'hare': 303,
                'polar': 305, 'sniper': 306, 'twap': 307, 'trailing_stop': 308}
    if not all([args.pair, args.algo, args.side, args.client_order_id]):
        parser.error("--create_order requires --pair, --algo, --side, and --client_order_id")
    if args.side == 'both':
        if args.quantity is None:
            parser.error("--create_order with --side=both requires --quantity")
        if args.quantity <= 0:
            parser.error("--quantity must be positive")
    elif args.scale:
        if args.quantity is None:
            parser.error("--create_order with --scale requires --quantity")
        if args.quantity <= 0:
            parser.error("--quantity must be positive")
    elif args.side != 'both':
        if args.algo == 'market':
            if args.amount is None:
                parser.error("--create_order with --algo=market requires --amount")
            if args.amount <= 0:
                parser.error("--amount must be positive")
        else:
            if args.quantity is None:
                parser.error("--create_order with --algo=%s requires --quantity" % args.algo)
            if args.quantity <= 0:
                parser.error("--quantity must be positive")

    order_params = []
    algo_id = algo_map[args.algo]

    if args.side == 'both':
        log.info("%s", args.count)
        cur_mid = trader.get_ob_mid_non_mm(args.pair)
        print("cur mid: " + str(cur_mid))

        if args.split > 1:
            args.quantity = args.quantity / args.split
        for inc in range(1, (args.count + 1) * args.split):
            buy_price = round(cur_mid - (args.spread * inc), 8)
            sell_price = round(cur_mid + (args.spread * inc), 8)
            if buy_price == sell_price:
                sell_price = buy_price + args.spread

            x = round(.0002 * sell_price * args.quantity + .0002 * buy_price * args.quantity - (buy_price - sell_price), 5)
            y = round(sell_price - buy_price)
            z = round(x - y, 5)
            log.info("%s Rev: %s Cost: %s Profit: %s| buy_price: %s sell_price: %s", inc, x, y, z, buy_price, sell_price)
            payload = {"algorithm_id": algo_id, "currency_pair": args.pair, "quantity": args.quantity, "price": format_sig_figs(buy_price, 8), "destination": args.dest}
            order_params.append((trader, "buy", payload, "b_" + args.client_order_id))
            payload = {"algorithm_id": algo_id, "currency_pair": args.pair, "quantity": args.quantity, "price": format_sig_figs(sell_price, 8), "destination": args.dest}
            order_params.append((trader, "sell", payload, "s_" + args.client_order_id))

    if args.scale:
        cur_quote = trader.get_current_quote(args.pair, args.side)
        price = (cur_quote.get("price") or cur_quote.get("buy_price") or cur_quote.get("sell_price")
                 or (cur_quote.get("data") or {}).get("price"))
        if price is None:
            parser.error(f"Could not extract price from quote response. Keys: {list(cur_quote.keys())}")
        low, high, num, price_step = get_lhnp(args, price=price)
        qty = format_sig_figs(args.quantity / num, 4) if args.sq else args.quantity
        for i in range(num):
            price = low + (price_step * i)
            payload = {"algorithm_id": algo_id, "currency_pair": args.pair, "quantity": qty, "price": format_sig_figs(price, 8), "_unique_tag": f"{int(time.time()*1000)}_{i}"}
            payload["destination"] = args.dest
            order_params.append((trader, args.side, payload, args.client_order_id))
    elif args.side != 'both':
        payload = {"algorithm_id": algo_id, "currency_pair": args.pair}
        payload["destination"] = args.dest
        if args.algo == 'market':
            payload["amount"] = args.amount
        else:
            payload.update({"quantity": args.quantity, "price": args.limit_price})
        order_params.append((trader, args.side, payload, args.client_order_id))

    log.info("Submitting %s orders using %s cores...", len(order_params), cpu_count())
    fv = {}
    base_dir = f"orders/{args.pair}/{args.side}"
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)

    if args.save_orders:
        sfile = f"{base_dir}/s_{int(time.time())}.pkl"
        with open(sfile, 'wb') as f:
            pickle.dump(order_params, f)
        log.info("%s", sfile)
        if args.rev:
            new_params = transform_params(order_params, args.rev_key, "R:" + args.client_order_id)
            sfile = f"{base_dir}/r_{int(time.time())}.pkl"
            with open(sfile, 'wb') as f:
                pickle.dump(new_params, f)
            log.info("%s", sfile)

    if args.dry_run:
        log.info("%s", order_params)
        sys.exit("Exiting due to dry_run")

    with Pool(cpu_count()) as p:
        responses = p.map(worker_place_order, order_params)

    fkeys = ["id", "action", "algorithm", "pair", "quantity", "price", "status"]
    for res in responses:
        for k in fkeys:
            if k in res:
                fv[k] = res[k]
        log.info("%s", fv)

    csv_fn = f"{base_dir}/{int(time.time())}.csv"
    if responses:
        with open(csv_fn, 'w', newline='\n') as f:
            dw = csv.DictWriter(f, fieldnames=responses[0].keys())
            dw.writeheader()
            dw.writerows(responses)
        log.info("Responses saved to %s", csv_fn)


def handle_rev_load(args):
    """Handle --rev_load: load and log reverse order params from file."""
    with open(args.rev_load, 'rb') as f:
        order_params = pickle.load(f)
    log.info("%s", order_params)


def main():
    ENV_VAR = 'SFOX_API_KEY'
    version = get_version()
    parser = argparse.ArgumentParser(description=f"SFOX Algorithmic Trading Utility v{version}")

    # Key Management
    parser.add_argument("--key", default=os.environ.get(ENV_VAR),
                        required=ENV_VAR not in os.environ,
                        help=f"SFOX API Key (Defaults to {ENV_VAR} env var)")
    # Rev Order Key
    parser.add_argument("--rev_key", default=os.environ.get('REV_KEY'),
                        help="SFOX API Key (Defaults to $REV_KEY env var)")

    # Account Reporting
    parser.add_argument("-a", "--account", nargs="+",
                        help="Fetch account data: balance, pairs, fees, transactions, shorting, short_positions, pts [positions|funding|rates|cr [pair]], or 'daily_value [months]M'")

    # Order Tracking
    parser.add_argument("-o", "--order_status",
                        help="Check status: 'open', 'done' (50 most recent), 'done_recent' (250 most recent), or numeric order ID")

    # Trading Parameters
    parser.add_argument("--create_order", action="store_true", help="Flag to initiate order execution")
    parser.add_argument("--client_order_id", help="Required prefix for trading idempotency")
    parser.add_argument("-p", "--pair", help="Asset pair (e.g., btcusd, ethusd). Filters order_status open/done when used with --order_status")
    parser.add_argument("--side", choices=['buy', 'sell', 'both'], help="Trade direction. Filters order_status open/done when buy or sell")
    parser.add_argument("--algo", choices=['market', 'sniper', 'twap', 'limit', 'gorilla', 'trailing_stop', 'smart', 'polar', 'turtle', 'hare'],
                        help="Execution algorithm to use")
    parser.add_argument("--count", type=int, choices=[1,2,3,4,5,6,7,8,9,10,11,12], default=1, help="number of orders")
    parser.add_argument("--spread", type=float, default=0.005, help="default spread")
    parser.add_argument("--split", type=int, default=1, help="splits into more orders for --both")

    # Volume and Price
    parser.add_argument("--quantity", type=float, help="Size of order (Base asset quantity)")
    parser.add_argument("--amount", type=float, help="USD amount to spend (Market orders only)")
    parser.add_argument("--limit_price", type=float, help="Limit price for the order")

    # Grid/Scaled Orders
    parser.add_argument("--scale", help="Grid placement: 'lower_bound,upper_bound,num_orders'")
    parser.add_argument("--sq", action="store_true", help="Scale Quantity: splits --quantity across grid slices")

    # Destination
    parser.add_argument("--dest", default="ox",choices=['darkpool', 'smart', 'ox'], help="Routing Destination, ox is the default")

    # Cancellations
    parser.add_argument("--cancel_all", action="store_true", help="Instantly cancel all open orders")
    parser.add_argument("--cancel_ids", help="Comma-separated list of numeric IDs to cancel")

    # Dry Run
    parser.add_argument("--dry_run", action="store_true", help="dont execute orders, just print")
    parser.add_argument("--save_orders", action="store_true", help="save orders to file")
    parser.add_argument("--rev", action="store_true", help="save a reverse order_pair")
    parser.add_argument("--rev_load", default=None, type=str, help="reverse order file to load")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version}")

    args = parser.parse_args()
    trader = SFOXTrader(args.key)

    try:
        # 1. Order Status Logic
        if args.order_status:
            handle_order_status(trader, args)

        # 2. Account Information Logic
        if args.account:
            handle_account(trader, args, parser)

        # 3. Cancellation Logic
        if args.cancel_all or args.cancel_ids:
            handle_cancellations(trader, args)

        # 4. Trading Operations (Manual & Scaled)
        if args.create_order:
            handle_create_order(trader, args, parser)

        # 5. Reverse Order Load
        if args.rev_load is not None:
            handle_rev_load(args)
    except ValueError as e:
        log.error("%s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
