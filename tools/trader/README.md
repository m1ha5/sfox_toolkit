# sfox_trader

trader.py is a general sfox cli for trade only functionality of an sFOX account. While I have variations of this personally for the last several years with over tens of thousands of orders. Generally works well for mid-frequency ( > 1min ) trading for execution. 

### API Key

Set your SFOX API key as an environment variable:

```bash
export SFOX_API_KEY=your_api_key_here
```

Or pass it inline:

```bash
SFOX_API_KEY=your_key ./bin/trader --account balance
```

## Notes:

* no twap, trailing stop, or TSO support
* default destination is 'ox', set --dest=smart for smart routing 
* mid is from the ask/bid mid not market_making as market_making maybe missing from the ob 

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
For accounts with shorting support but not PTS

```bash
./bin/trader --account shorting
./bin/trader --account short_positions
```

### Post-Trade Settlement (PTS)
Shorting positions show up here

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

## Tests

```bash
pip3 install -e ".[dev]"
python3 -m pytest tests/ -v
```

## Version

**Trader** report fixed versions baked into `tools/trader/trader.py` (not `git describe`). Check:

```bash
./bin/trader --version
```

Other entrypoints may still use git-based or script-local version strings—see each tool’s source.

## DB

```bash
sqlite3 orders.db < db/create_order_table.sql
```
