# Order Submission Latency Stats

`order_submission_latency_stats.py` reads an SQLite `orders` table and reports latency statistics in milliseconds for two values:

- `date_added - order submission time`, where order submission time is parsed from the trailing `_<ms>` portion of `client_order_id`
- `date_added - run instance time`, where run instance time is parsed from the `:<seconds>::` portion of `client_order_id`

This is useful for quickly checking how long it takes an order record to appear in the DB relative to client-side submission and bot run-instance timestamps.

## Requirements

- Python 3.9+
- SQLite database with an `orders` table (or custom table(s) via `--tables`) containing:
  - `date_added` (ISO-8601 timestamp)
  - `client_order_id` (string in the expected sFOX format)

## Usage

Run from anywhere by giving the script path:

```bash
python tools/osl/order_submission_latency_stats.py
```

Use a custom database path:

```bash
python tools/osl/order_submission_latency_stats.py --db path/to/orders.db
```

Short form:

```bash
python tools/osl/order_submission_latency_stats.py -d path/to/orders.db
```

Use a custom table name:

```bash
python tools/osl/order_submission_latency_stats.py --db path/to/orders.db --tables orders_archive
```

Use multiple tables (per-table + TOTAL aggregate):

```bash
python tools/osl/order_submission_latency_stats.py --db path/to/orders.db --tables t1,t2,t3
```

Short form:

```bash
python tools/osl/order_submission_latency_stats.py -d path/to/orders.db -t t1,t2,t3
```

Get CSV output (single summary row):

```bash
python tools/osl/order_submission_latency_stats.py --db path/to/orders.db --tables orders_archive --csv
```

Customize quantiles (defaults to `90,99`):

```bash
python tools/osl/order_submission_latency_stats.py --db path/to/orders.db --tables orders_archive --quantiles 75,90,95,99.99
```

Short form:

```bash
python tools/osl/order_submission_latency_stats.py -d path/to/orders.db -t orders_archive -q 75,90,95,99.99
```

## Output

The script prints:

- number of rows read
- number of rows where `date_added` failed to parse
- latency stats (`n`, `mean`, `min`, `max`, `p90`, `p99`) for:
- latency stats (`n`, `mean`, `min`, `max`, and configured quantiles via `--quantiles`) for:
  - `latency_ms_date_added_minus_submission`
  - `latency_ms_date_added_minus_run_instance`

When `--csv` is set, it prints:

- Header: `rows_read,date_added_parse_failed,mean,min,max,p90,p99`
- One data row for submission latency stats (`date_added - submission`)
- Quantile columns are dynamic and follow `--quantiles` (default: `p90,p99`)

When `--tables` is set:

- Normal output prints one section per table plus a final `TOTAL` section
- CSV output prints one row per table plus a final `TOTAL` row (with `table` column)
