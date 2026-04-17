# sfox_trader

SFOX trading utilities for algorithmic trading and portfolio management.

- **`sfox_trader.lib.sfox_client`** – SFOX API client (`SFOXTrader`; editable install)
- **`tools/trader/trader.py`** – CLI: account data, order management, trading (run via **`./bin/trader`** symlink or `python3 tools/trader/trader.py`)
- **`sfox_trader.strats.grid`** – grid order helpers; **`tools/grid/grid.py`** – grid runner (**`./bin/grid`**)
- **`sfox_trader.strats.long_short`** – pairs long/short trial (**`./bin/long_short_trial`**)
- **`sfox_trader.strats.new_pairs`** – pairs research / batch tools (**`./bin/pairs`**, **`./bin/batch_scanner`**)
- **`sfox_trader.strats.mm_hft`** – MM HFT live + backtest (**`./bin/mm_hft_live`**, **`./bin/mm_hft_backtest`**)
- **`tools/osl/order_submission_latency_stats.py`** – order-submission latency from SQLite (**`./bin/osl_stats`**)
- **`tools/grid/configs/`** – JSON asset params for the grid runner (`min_quantity`, `min_spread`, `zeros`). **Convention:** each tool or strategy keeps its own **`configs/`** (or example `*.json`) under **`tools/<name>/`** or **`src/sfox_trader/strats/<name>/`**—avoid a single shared top-level `configs/` unless multiple components read the same file.

## Setup

### Requirements

- **Python 3.9+** (matches `requires-python` in `pyproject.toml`)
- Core runtime: [requests](https://pypi.org/project/requests/), [tabulate](https://pypi.org/project/tabulate/)

For the grid runner: `jq` and `bc` (POSIX systems typically have these).

### Install (recommended)

From the repository root, use a virtual environment and **editable** install so `import sfox_trader` works everywhere:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip3 install -e ".[dev]"
```

Optional extras (see `pyproject.toml`):

```bash
pip3 install -e ".[dev,research]"    # new_pairs / pandas stack
pip3 install -e ".[dev,websocket]"   # websocket-client for WS tools and OBA
```

Legacy minimal file (no extras): `requirements.txt` — **`pip3 install -r requirements.txt`** only installs the base pins; it does **not** install the package in editable mode. Prefer **`pip3 install -e .`** for development.

### API Key

Set your SFOX API key as an environment variable:

```bash
export SFOX_API_KEY=your_api_key_here
```

Or pass it inline:

```bash
SFOX_API_KEY=your_key ./bin/trader --account balance
```

## Usage Examples

### Account & Balance

```bash
./bin/trader --account balance
./bin/trader --account pairs
./bin/trader --account fees
./bin/trader --account transactions
./bin/trader --account daily_value
./bin/trader --account daily_value 2M
```

### Orders

```bash
./bin/trader --order_status open
./bin/trader --order_status open --pair btcusd --side buy
./bin/trader --order_status done
./bin/trader --order_status done_recent --pair ethusd
./bin/trader --order_status 701968334
```

### Shorting

```bash
./bin/trader --account shorting
./bin/trader --account short_positions
```

### Post-Trade Settlement (PTS)

```bash
./bin/trader --account pts
./bin/trader --account pts positions
./bin/trader --account pts positions btcusd
./bin/trader --account pts funding
./bin/trader --account pts rates
./bin/trader --account pts cr
./bin/trader --account pts cr btcusd
```

### Cancellations

```bash
./bin/trader --cancel_all
./bin/trader --cancel_ids 1234567,1234568
```

### Trading (create_order)

#### Create a buy and sell order on ox with 1 cent spread from the top of the book
```bash
./bin/trader --create_order --pair btcusd --side both --quantity 0.01 \
  --client_order_id "my_prefix" --algo smart --dest ox  --spread 0.005
```
#### Create 10 layered sell orders (.05btc )from the current price to + 25 bip
```bash
./bin/trader --create_order --pair btcusd --side sell --scale now,25,10 \
 --client_order_id "my_prefix" --algo smart --dest ox --quabtity .5 
```

### Grid runner (`bin/grid`)

Continuous loop using `sfox_trader.strats.grid` and `bin/trader`-style API access for automated grid orders.

```bash
export SFOX_API_KEY=your_key
./bin/grid btcusd                     # interactive
./bin/grid -c btcusd                  # continuous (no pause)
./bin/grid -o btcusd                  # run once and exit
./bin/grid -d btcusd                  # debug (dry-run orders only)
./bin/grid -c -o btcusd               # continuous + run once
./bin/grid --version                  # show version
```

From repo root (explicit path):

```bash
export SFOX_API_KEY=your_key
python3 tools/grid/grid.py btcusd               # interactive
python3 tools/grid/grid.py -c btcusd            # continuous
python3 tools/grid/grid.py -o btcusd            # run once
python3 tools/grid/grid.py -d btcusd            # debug
python3 tools/grid/grid.py --config /path/to/config.json btcusd   # custom config
```

**Options:**

| Short | Long | Description |
|-------|------|-------------|
| `-c` | `--continuous` | Run without pausing between iterations |
| `-d` | `--debug` | Print DRY_RUN orders instead of submitting |
| `-o` | `--run_once` | Run once and exit (no loop) |
| `-n` | `--no_split` | Don't split orders |
| - | `--config` | Path to asset config JSON (default: `tools/grid/configs/default`) |
| `-v` | `--version` | Show version |

### asset_config – Asset Parameters

JSON file defining per-asset params. Default lookup is **`tools/grid/configs/default`**, or pass **`--config`**.

```json
{
  "btcusd": {"min_quantity": 0.005, "min_spread": 0.001, "zeros": "00"},
  "ethusd": {"min_quantity": 0.2, "min_spread": 0.001, "zeros": "00"},
  "dogeusd": {"min_quantity": 1000, "min_spread": 0.000001, "zeros": "00000000"}
}
```

- **min_quantity** – Base order size (matches `load_asset_config` in `tools/grid/grid.py`)
- **min_spread** – Base spread multiplier
- **zeros** – Decimal precision string for spread calculation

Unknown assets fall back to defaults (`min_quantity=5`, `min_spread=0.001`, `zeros="00"`).

## Tests

```bash
pip3 install -e ".[dev]"
python3 -m pytest tests/ -v
```

## Version

**Trader** and **grid** report fixed versions baked into `tools/trader/trader.py` and `tools/grid/grid.py` (not `git describe`). Check:

```bash
./bin/trader --version
./bin/grid --version
```

Other entrypoints may still use git-based or script-local version strings—see each tool’s source.

## DB

```bash
sqlite3 orders.db < db/create_order_table.sql
```
