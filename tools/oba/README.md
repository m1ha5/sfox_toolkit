# sFOX Market Monitor (`sfox_mm.py`, CLI shorthand **sfox-mm**)

Terminal UI for multi-asset sFOX order books and a percentage-change grid. It uses **WebSocket** feeds for books and tickers and **REST** for candlestick chart data.

## Requirements

- Python 3
- `websocket-client`
- sFOX API key: `SFOX_API_KEY` or `-k` / `--key`

## Running

From the **repository root** (`sfox_trader/`):

```bash
export SFOX_API_KEY=your_key
python3 tools/oba/sfox_mm.py -m summary -a btcusd,ethusd
```

From **`tools/oba/`**:

```bash
export SFOX_API_KEY=your_key
python3 sfox_mm.py -m summary -a btcusd,ethusd
```

Show version:

```bash
python3 tools/oba/sfox_mm.py -V
```

**WebSocket order book trace** (stderr, one line per net book message): set `SFOX_WS_OB_TRACE=1` (or `true` / `yes`). Logs `recipient`, channel suffix, `payload.pair`, resolved storage key, and top bid/ask.

## Modes (`-m` / `--mode`)

| Mode        | Description |
|------------|-------------|
| `summary`  | One line per symbol: bid, mid, ask, spread, sizes. |
| `full`     | Ask / mid / bid reference rows, bps ladder, then an embedded % change block (candles + Day Open). Requires `-b` / `--bips`. |
| `orderbook`| Same ladder as full, without the % change block. Requires `-b`. |
| `changes`  | Standalone % grid: one column per asset; no sizes. |

Switch while running: **s** summary, **f** full, **o** orderbook, **c** changes.

## Command-line options

| Option | Description |
|--------|-------------|
| `-a`, `--assets` | Comma-separated pairs (e.g. `btcusd,ethusd,ethusdt`). **Required.** |
| `-m`, `--mode` | `summary`, `full`, `changes`, or `orderbook`. **Required.** |
| `-b`, `--bips` | Comma-separated bps rungs (e.g. `0,1,2,5,7,10`). **Required** for `full` and `orderbook`. For `summary` / `changes`, optional; **f** / **o** use a default ladder if omitted. `0` is the mid row (price = mid; size blank). |
| `-k`, `--key` | API key (default: `SFOX_API_KEY`). |
| `-d`, `--diff` | Start with diff on (≥2 assets, **same base**, e.g. `ethusd,ethusdt,ethusdc`). Toggle with **d**. |
| `--theme` | Path to a JSON theme file. If unset, `tools/oba/sfox_mm.colors.cfg` is used when present (legacy: `tools/oba/grid-ob.colors.cfg`). |
| `--candles` | Comma-separated % rows after Day Open. Default: `1M,5M,15M,1H,4H,6H,1D`. Tokens: **1M, 5M, 15M, 1H, 4H, 6H, 1D, 3D, 7D**. |
| `--candle-prime-workers` | Parallel REST fetches when warming the in-memory % cache at startup (1–32, default 8). |
| `--no-candle-disk-cache` | Disable on-disk caching of chartdata REST responses (see below). |
| `--candle-cache-dir` | Directory for chartdata JSON cache (overrides `SFOX_MARKETS_MONITOR_CACHE`). |

## Examples

```bash
# Summary for two pairs
python3 tools/oba/sfox_mm.py -m summary -a btcusd,ethusd

# Full ladder + diff (same base) + custom candle rows
python3 tools/oba/sfox_mm.py -m full -a ethusd,ethusdt,ethusdc -b 0,1,5,10 -d \
  --candles 1M,5M,1H,1D

# Changes grid only
python3 tools/oba/sfox_mm.py -m changes -a btcusd,ethusd

# Custom theme file
python3 tools/oba/sfox_mm.py -m summary -a btcusd --theme ./my-colors.cfg

# Custom candle cache directory
python3 tools/oba/sfox_mm.py -m changes -a btcusd --candle-cache-dir ~/.cache/sfox-candles

# No disk cache (always hit REST for candles)
python3 tools/oba/sfox_mm.py -m changes -a btcusd --no-candle-disk-cache
```

## Theme colors (`--theme` / `sfox_mm.colors.cfg`)

Theme files are JSON objects: keys are foreground color names, values are one of **`black`**, **`red`**, **`green`**, **`yellow`**, **`blue`**, **`magenta`**, **`cyan`**, **`white`** (and optionally **`-1`** for the terminal default, if supported).

Copy and edit the example:

```bash
cp tools/oba/sfox_mm.colors.cfg.example tools/oba/sfox_mm.colors.cfg
```

### `mid_fg` (mid price color)

**`mid_fg`** colors:

- Summary: **`mid`** column header and mid price.
- Full / orderbook: **MID** row label and mid prices in the ask / mid / bid header block.

Default in code: **`cyan`**. Example snippet:

```json
{
  "mid_fg": "magenta",
  "bid_fg": "yellow",
  "ask_fg": "red"
}
```

Other keys (see `sfox_mm.colors.cfg.example` for full descriptions): `header_fg`, `title_fg`, `label_fg`, `ask_fg`, `bid_fg`, `zero_row_fg`, `cell_fg`, `hint_fg`, `diff_pos_fg`, `diff_neg_fg`. A top-level **`"comment"`** key can hold documentation; it is ignored for styling.

## Candle disk cache

Chartdata candlestick REST responses can be cached as JSON files so **repeated runs** (and refreshes within TTL) reuse data when it is still considered relevant.

### Default location

```
<current working directory>/.cache/sfox-markets-monitor/candles/
```

The process creates this directory when caching is enabled.

### Overrides (precedence)

1. **`--no-candle-disk-cache`** — caching off.
2. Else **`--candle-cache-dir`** — use this path as the cache directory.
3. Else **`SFOX_MARKETS_MONITOR_CACHE`** — if set, use this path as the cache directory (expanded with `~`).
4. Else the default path above.

### Behavior (summary)

- Cache entries are keyed by pair, period, and a **time bucket** derived from the request end time.
- A cached file is used only if it is **fresh enough** (TTL depends on bar period; short periods expire sooner) and the stored **`fetch_start` / `fetch_end`** window **covers** the current request (so advancing wall time can trigger a refetch when the old response is no longer sufficient).
- **`--candle-prime-workers`** still warms the **in-memory** % cache at startup; disk cache reduces redundant **REST** traffic across runs.

### Examples

```bash
# Use default ~/.cache-style layout via env
export SFOX_MARKETS_MONITOR_CACHE="$HOME/.cache/sfox-markets-monitor/candles"
python3 tools/oba/sfox_mm.py -m changes -a btcusd,ethusd

# Explicit directory
python3 tools/oba/sfox_mm.py -m full -a btcusd -b 0,1,5 \
  --candle-cache-dir /tmp/sfox-candles
```

## While running (hotkeys)

| Key | Action |
|-----|--------|
| **q** | Quit |
| **s** / **f** / **o** / **c** | Summary / full / orderbook / changes |
| **d** | Toggle diff (when allowed) |
| **v** | Toggle feed speed line |
| **h** / **?** | Help; **Esc** closes help |
| **Arrow keys** | **Summary:** move highlighted cell (↑↓ = asset row, ←→ = field column). **Full / orderbook:** ↑↓ = body row (ASK/MID/BID, ladder, %% block); ←→ = asset column. Uses reverse-video highlight. |
| **j** / **k** | Scroll full / orderbook ladder one line |
| **PgUp** / **PgDn** | Page scroll (full / orderbook) |

Redraw is about **0.25s** when idle and **~0.1s** after a keypress; terminal resize is supported.

## Pairs and diff

Pairs are **base + quote** (e.g. `btcusd`, `btcusdt`, `ethusdc`). **Diff** needs at least two assets with the **same base**; mixed bases are rejected.

### Crossing books (USD vs USDC)

sFOX **crosses** net liquidity across certain quote currencies for the same base: a resting order on e.g. **BTCUSDC** can be filled by a taker on **BTCUSD** (and similarly **ETHUSD** vs **ETHUSDC**). Because of that, WebSocket `orderbook.net.*` snapshots and REST net books for `*usd` and `*usdc` pairs with the **same base** often **match** — that is expected, not a client bug.

**Do not pass both** `<base>usd` **and** `<base>usdc` in `-a` (e.g. `btcusd,btcusdc` or `ethusd,ethusdc,ethusdt` still conflicts for ETH). The monitor and `sfox_ws_debug.py` **refuse to start** and print a short explanation so you are not comparing two columns that show the same crossed book. Prefer e.g. `btcusd,btcusdt` or drop one of the USD/USDC legs.

## Related files

| File | Role |
|------|------|
| `IMPROVEMENTS.md` | Backlog: security, performance, readability, UX (ongoing) |
| `sfox_trader.lib.pair_utils` | Base/quote parsing; USD+USDC cross-book check |
| `sfox_mm.py` | Main TUI entrypoint (**sfox-mm**) |
| `sfox_trader.lib.chartdata_cache` | Disk cache for chartdata REST |
| `sfox_mm.colors.cfg.example` | Example theme (copy to `sfox_mm.colors.cfg`; `grid-ob.colors.cfg` still loaded if present) |
| `sfox_trader.lib.sfox_client`, `sfox_trader.lib.sfox_ws` | REST and WebSocket clients (editable install) |
