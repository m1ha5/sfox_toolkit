# get_candles

Utilities for fetching and building SFOX candle files.

## Main Tool

Use `fetch_candles.py` as the canonical command.

It supports:

- Fetching native API candle sizes
- Building custom candle sizes from 1-minute candles (for example `10m`, `4h`, `8h`)
- Multiple assets in one run
- Safe append + dedupe by `epoch`
- Optional `twap_1m` generation with `--twap`
- UTC/GMT handling for `--date_start`

## Usage

Run from the repository root:

```bash
python tools/get_candles/fetch_candles.py [options]
```

### Options

- `--size`, `-s`  
  Candle size in seconds.
- `--ticker`, `-t`  
  Single ticker (`btcusd`), comma list (`btcusd,ethusd`), or group (`all`, `majors`, `usd`, `usdt`, `usdc`).
- `--append`, `-a`  
  Append mode. Merges with existing file and dedupes by `epoch`.
- `--sdir`, `-d`  
  Output base directory. Default: `./size`.
- `--date_start`  
  Start date (UTC/GMT). Examples: `2026-04-01`, `2026-04-01T00:00:00Z`.
- `--twap`  
  Include `twap_1m` column.
- `--api_key_env`  
  Optional env var name for API key. Not required for public candle fetching.

## Examples

### 1) Fetch hourly BTCUSD candles

```bash
python tools/get_candles/fetch_candles.py -s 3600 -t btcusd
```

### 2) Append latest data to existing file

```bash
python tools/get_candles/fetch_candles.py -s 3600 -t btcusd --append
```

### 3) Fetch multiple assets at once

```bash
python tools/get_candles/fetch_candles.py -s 3600 -t btcusd,ethusd,solusd
```

### 4) Fetch a group (all USD pairs)

```bash
python tools/get_candles/fetch_candles.py -s 3600 -t usd
```

### 5) Build custom 10-minute candles

```bash
python tools/get_candles/fetch_candles.py -s 600 -t btcusd
```

### 6) Build custom 4-hour candles with TWAP

```bash
python tools/get_candles/fetch_candles.py -s 14400 -t btcusd --twap
```

### 7) Start from a specific UTC date

```bash
python tools/get_candles/fetch_candles.py -s 3600 -t btcusd --date_start 2026-04-01
```

## Output Layout

Files are written to:

```text
<working_dir>/size/<candle_size>/<ticker>.csv
```

Example:

```text
tools/get_candles/size/3600/btcusd.csv
```

## Output Columns

Base columns:

- `timestamp` (UTC string, `YYYY-MM-DD HH:MM:SS`)
- `close`
- `high`
- `low`
- `open`
- `size`
- `ticker`
- `epoch` (unix seconds, dedupe key)
- `trades`
- `volume`
- `vwap`

Optional:

- `twap_1m` (included only with `--twap`)

## Precision Policy

When writing `volume`, `vwap`, and `twap_1m`, decimal precision is chosen from the first row OHLC:

- if OHLC `> 10`: 2 decimal places
- if OHLC `>= 1` and `<= 10`: 3 decimal places
- if OHLC `< 1`: 5 decimal places

## Notes

- `--date_start` is always interpreted as UTC/GMT, never local timezone.
- `fetch_candles.py` replaces the old `combine.sh` append workflow with internal merge+dedupe logic.
