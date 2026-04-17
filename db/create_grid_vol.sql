CREATE TABLE IF NOT EXISTS grid_volume (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          TEXT NOT NULL,      -- e.g. "ethusd:1773145653"
  asset           TEXT NOT NULL,
  side_mode       TEXT NOT NULL,      -- "buy" / "sell" / "both" (from --side)
  buy_count       INTEGER NOT NULL,
  sell_count      INTEGER NOT NULL,
  buy_spread      REAL NOT NULL,      -- max(buy) - min(buy)
  sell_spread     REAL NOT NULL,      -- max(sell) - min(sell)
  full_spread     REAL NOT NULL,      -- max(sell) - min(buy)
  avg_price       REAL NOT NULL,      -- mean of buy_prices
  current_volume  REAL NOT NULL,      -- this batch’s notional
  total_volume    REAL NOT NULL,      -- running total so far
  total_orders    INTEGER NOT NULL,   -- running total orders
  count_per_side  INTEGER NOT NULL,   -- your grid 
  split           INTEGER NOT NULL,   -- split
  x               INTEGER NOT NULL,   -- the x you logged
  spread_m        INTEGER NOT NULL,   -- spread_m you logged
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
