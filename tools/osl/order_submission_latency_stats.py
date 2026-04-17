#!/usr/bin/env python3
"""
Read SQLite `orders` table and compute latency from client order submission time
and run_instance epoch (embedded in client_order_id) to date_added.

client_order_id example:
  s_f:ethusd:1774361033::9_1774361034906
  - 1774361033 = run_instance epoch (seconds)
  - 1774361034906 ms = order_submission (trailing _<ms>)
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

# ..._<ms_since_epoch> (order submission)
_ORDER_SUB_MS = re.compile(r"_(\d+)$")
# :<seconds>:: (run_instance, before the ::sequence_ms part)
_RUN_INSTANCE_S = re.compile(r":(\d+)::")


def parse_date_added(value: str | None) -> float:
    """Return unix seconds (float, ms preserved) from ISO-8601 date_added."""
    if not isinstance(value, str):
        raise TypeError("date_added must be a string")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_order_submission_ms(client_order_id: str | None) -> int | None:
    if not client_order_id:
        return None
    m = _ORDER_SUB_MS.search(client_order_id.strip())
    if not m:
        return None
    return int(m.group(1))


def parse_run_instance_s(client_order_id: str | None) -> int | None:
    """Run instance as unix seconds (epoch in client_order_id before ::)."""
    if not client_order_id:
        return None
    m = _RUN_INSTANCE_S.search(client_order_id.strip())
    if not m:
        return None
    return int(m.group(1))


def percentile_nearest_rank(sorted_vals: list[float], p: float) -> float:
    """p in (0, 100]. Nearest-rank method (common for latency reports)."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("empty")
    k = int(math.ceil(p / 100.0 * n)) - 1
    k = max(0, min(k, n - 1))
    return sorted_vals[k]


def print_latency_block(
    title: str,
    deltas_ms: list[float],
    quantiles: list[tuple[str, float]],
) -> None:
    print(title)
    if not deltas_ms:
        print("  (no rows)")
        return
    summary = summarize_latency(deltas_ms, quantiles)
    assert summary is not None
    mean, min_v, max_v, q_values = summary
    print(f"  n:    {len(deltas_ms)}")
    print(f"  mean: {mean:.6f}")
    print(f"  min:  {min_v:.6f}")
    print(f"  max:  {max_v:.6f}")
    for (label, _), qv in zip(quantiles, q_values):
        print(f"  p{label}:  {qv:.6f}")


def summarize_latency(
    deltas_ms: list[float],
    quantiles: list[tuple[str, float]],
) -> tuple[float, float, float, list[float]] | None:
    if not deltas_ms:
        return None
    s = sorted(deltas_ms)
    mean = statistics.fmean(deltas_ms)
    q_values = [percentile_nearest_rank(s, qv) for _, qv in quantiles]
    return mean, s[0], s[-1], q_values


def parse_quantiles_arg(raw_quantiles: str) -> list[tuple[str, float]]:
    parts = [item.strip() for item in raw_quantiles.split(",") if item.strip()]
    if not parts:
        raise ValueError("--quantiles must include at least one percentile")
    parsed: list[tuple[str, float]] = []
    for part in parts:
        try:
            value = float(part)
        except ValueError as e:
            raise ValueError(f"invalid quantile value: {part}") from e
        if value <= 0.0 or value > 100.0:
            raise ValueError(f"quantile out of range (0,100]: {part}")
        parsed.append((part, value))
    return parsed


def parse_tables_arg(raw_tables: str) -> list[str]:
    tables = [item.strip() for item in raw_tables.split(",") if item.strip()]
    if not tables:
        raise ValueError("--tables must include at least one table name")
    return tables


def read_rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    cur = conn.execute(f'SELECT date_added, client_order_id FROM "{table}"')
    return cur.fetchall()


def compute_latency(rows: list[sqlite3.Row]) -> tuple[list[float], list[float], int]:
    deltas_sub_ms: list[float] = []
    deltas_run_ms: list[float] = []
    date_parse_failed = 0

    for row in rows:
        try:
            added_ts = parse_date_added(row["date_added"])
        except (TypeError, ValueError):
            date_parse_failed += 1
            continue
        added_ms = added_ts * 1000.0
        cid = row["client_order_id"]

        sub_ms = parse_order_submission_ms(cid)
        if sub_ms is not None:
            deltas_sub_ms.append(added_ms - float(sub_ms))

        run_s = parse_run_instance_s(cid)
        if run_s is not None:
            deltas_run_ms.append(added_ms - float(run_s) * 1000.0)

    return deltas_sub_ms, deltas_run_ms, date_parse_failed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stats for (date_added - submission) and (date_added - run_instance) from orders."
    )
    ap.add_argument(
        "-d",
        "--db",
        dest="db_path",
        default="orders.db",
        help="Path to SQLite database (default: ./orders.db)",
    )
    ap.add_argument(
        "-t",
        "--tables",
        default="orders",
        help="Comma-separated table names (default: orders), e.g. orders or t1,t2,t3",
    )
    ap.add_argument(
        "--csv",
        action="store_true",
        help="Print CSV output instead of the multi-line report",
    )
    ap.add_argument(
        "-q",
        "--quantiles",
        default="90,99",
        help="Comma-separated percentiles for latency stats (default: 90,99)",
    )
    args = ap.parse_args()

    try:
        tables = parse_tables_arg(args.tables)
        quantiles = parse_quantiles_arg(args.quantiles)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        per_table_rows: dict[str, list[sqlite3.Row]] = {}
        for table in tables:
            per_table_rows[table] = read_rows(conn, table)
    except sqlite3.Error as e:
        print(f"SQLite error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    per_table_metrics: dict[str, tuple[int, int, list[float], list[float]]] = {}
    total_rows_read = 0
    total_date_parse_failed = 0
    total_deltas_sub_ms: list[float] = []
    total_deltas_run_ms: list[float] = []

    for table in tables:
        rows = per_table_rows[table]
        deltas_sub_ms, deltas_run_ms, date_parse_failed = compute_latency(rows)
        per_table_metrics[table] = (len(rows), date_parse_failed, deltas_sub_ms, deltas_run_ms)
        total_rows_read += len(rows)
        total_date_parse_failed += date_parse_failed
        total_deltas_sub_ms.extend(deltas_sub_ms)
        total_deltas_run_ms.extend(deltas_run_ms)

    emit_multi_table = len(tables) > 1

    if args.csv:
        q_header = ",".join(f"p{label}" for label, _ in quantiles)
        if emit_multi_table:
            print(f"table,rows_read,date_added_parse_failed,mean,min,max,{q_header}")
            for table in tables:
                rows_read, date_parse_failed, deltas_sub_ms, _ = per_table_metrics[table]
                summary = summarize_latency(deltas_sub_ms, quantiles)
                if summary is None:
                    print(
                        f"{table},{rows_read},{date_parse_failed},"
                        + ",".join(["", "", ""] + ([""] * len(quantiles)))
                    )
                else:
                    mean, min_v, max_v, q_values = summary
                    q_csv = ",".join(f"{value:.6f}" for value in q_values)
                    print(
                        f"{table},{rows_read},{date_parse_failed},{mean:.6f},{min_v:.6f},{max_v:.6f},{q_csv}"
                    )
            total_summary = summarize_latency(total_deltas_sub_ms, quantiles)
            if total_summary is None:
                print(
                    f"TOTAL,{total_rows_read},{total_date_parse_failed},"
                    + ",".join(["", "", ""] + ([""] * len(quantiles)))
                )
            else:
                mean, min_v, max_v, q_values = total_summary
                q_csv = ",".join(f"{value:.6f}" for value in q_values)
                print(
                    f"TOTAL,{total_rows_read},{total_date_parse_failed},{mean:.6f},{min_v:.6f},{max_v:.6f},{q_csv}"
                )
        else:
            rows_read, date_parse_failed, deltas_sub_ms, _ = per_table_metrics[tables[0]]
            print(f"rows_read,date_added_parse_failed,mean,min,max,{q_header}")
            summary = summarize_latency(deltas_sub_ms, quantiles)
            if summary is None:
                print(
                    f"{rows_read},{date_parse_failed},"
                    + ",".join(["", "", ""] + ([""] * len(quantiles)))
                )
            else:
                mean, min_v, max_v, q_values = summary
                q_csv = ",".join(f"{value:.6f}" for value in q_values)
                print(
                    f"{rows_read},{date_parse_failed},{mean:.6f},{min_v:.6f},{max_v:.6f},{q_csv}"
                )
        return 0

    if emit_multi_table:
        for table in tables:
            rows_read, date_parse_failed, deltas_sub_ms, deltas_run_ms = per_table_metrics[table]
            print(f"table: {table}")
            print(f"rows_read: {rows_read}")
            print(f"date_added_parse_failed: {date_parse_failed}")
            print()
            print_latency_block(
                "latency_ms_date_added_minus_submission:",
                deltas_sub_ms,
                quantiles,
            )
            print()
            print_latency_block(
                "latency_ms_date_added_minus_run_instance:",
                deltas_run_ms,
                quantiles,
            )
            print()

        print("table: TOTAL")
        print(f"rows_read: {total_rows_read}")
        print(f"date_added_parse_failed: {total_date_parse_failed}")
        print()
        print_latency_block(
            "latency_ms_date_added_minus_submission:",
            total_deltas_sub_ms,
            quantiles,
        )
        print()
        print_latency_block(
            "latency_ms_date_added_minus_run_instance:",
            total_deltas_run_ms,
            quantiles,
        )
    else:
        rows_read, date_parse_failed, deltas_sub_ms, deltas_run_ms = per_table_metrics[tables[0]]
        print(f"rows_read: {rows_read}")
        print(f"date_added_parse_failed: {date_parse_failed}")
        print()
        print_latency_block(
            "latency_ms_date_added_minus_submission:",
            deltas_sub_ms,
            quantiles,
        )
        print()
        print_latency_block(
            "latency_ms_date_added_minus_run_instance:",
            deltas_run_ms,
            quantiles,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
