# sfox_trader

Unofficial sFOX trading utilities for algorithmic trading and portfolio management.

- **`sfox_trader.lib.sfox_client`** – SFOX API client (`SFOXTrader`; editable install)
- **`tools/trader/trader.py`** – CLI: account data, order management, trading (run via **`./bin/trader`** symlink or `python3 tools/trader/trader.py`)
- **`tools/osl/order_submission_latency_stats.py`** – order-submission latency from SQLite (**`./bin/osl_stats`**)
- **`tools/oba/sfom_mm.py`** – watch multiple orderbooks concurrently (**`./bin/sfox_mm`**)

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
pip3 install -r requirements.txt
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

## Tools 


