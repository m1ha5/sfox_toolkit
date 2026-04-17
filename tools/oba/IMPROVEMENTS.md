# sFOX Market Monitor — improvements backlog

This doc collects **security**, **performance**, **readability**, **UX/design**, and **operability** items for `sfox_mm.py` (**sfox-mm**) and related `tools/oba/` modules. It is not tied to a single release; version bumps stay in `sfox_mm.py` (`__version__`).

---

## Security

| Item | Notes |
|------|--------|
| **API keys** | Prefer `SFOX_API_KEY` in the environment; avoid `-k` in shell history. Never commit keys; rotate any key pasted into chat or logs. |
| **Cache directory** | Candle JSON under `--candle-cache-dir` / `SFOX_MARKETS_MONITOR_CACHE` holds market data, not secrets—still avoid world-writable dirs on shared machines. |
| **Debug logging** | `SFOX_WS_OB_TRACE=1` prints live prices to stderr; disable in shared terminals or when logging sessions. |
| **Future** | Optional **keychain / file path** (`--key-file`) to read the key without env vars (implementation TBD). |

---

## Performance / optimization

| Status | Item |
|--------|------|
| Done | **%Δ cache:** stale-while-revalidate + thread pool; **shorter TTL for cached `None`**; **interleaved prime tasks** across assets to reduce chartdata burst/rate limits. |
| Done | **Disk cache** for chartdata REST (`sfox_trader.lib.chartdata_cache`) with period-aware TTL and time buckets. |
| Todo | **REST backoff:** retry with jitter on HTTP 429 / transient errors inside `get_candlesticks` or `ChangesFetcher` (don’t cache failures as long-lived `None`). |
| Todo | **WS reconnect:** `sfox_trader.lib.sfox_ws` has minimal reconnect; consider exponential backoff and resubscribe guarantees under packet loss. |
| Todo | **Large pads:** full mode with many bps rungs + long asset names—profile `curses` refresh if terminals feel sluggish. |

---

## Code readability / structure

| Item | Notes |
|------|--------|
| **Split `sfox_mm.py`** | Single large file (~2k+ lines): extract **order-book math** (`fmt_px`, ladder rows, band volumes), **changes/%Δ** (`ChangesFetcher`), and **curses views** (summary / full / changes / help) into submodules under `tools/oba/` (e.g. `tools/oba/sfox_mm/` package) when a refactor pass is scheduled. |
| **Types** | Gradual `TypedDict` for theme keys and row-def dicts would help editors without a full rewrite. |
| **Tests** | Pure helpers (`fmt_px`, `fmt_vol`, `_resolve_orderbook_storage_pair`, `parse_candles_arg`, cache key logic) are good candidates for **pytest** in `tools/oba/tests/` or project `tests/`. |

---

## UX / design

| Item | Notes |
|------|--------|
| Done | **Focus + scroll:** arrow highlight, `j`/`k`, PgUp/Dn; theme **`mid_fg`**; feed speed line and clock. |
| Done | **Diff column** when same-base multi-quote; **fmt** tuned so nearby pairs don’t collapse to identical strings. |
| Done | **USD + USDC same base:** README + startup check — `sfox_mm.py` / `sfox_ws_debug.py` **exit** if `-a` lists both `<base>usd` and `<base>usdc` (sFOX crosses books; see README). |
| Todo | **Resize / narrow terminals:** hard minimum width already implied by layout; document minimum columns or degrade gracefully (truncate labels). |
| Todo | **Help screen:** keep in sync when new flags are added (`-V`, cache, trace env). |

---

## Operability / tooling

| Item | Notes |
|------|--------|
| Done | **`tools/oba/README.md`:** run modes, flags, theme, cache, hotkeys, `SFOX_WS_OB_TRACE`. |
| Done | **`sfox_ws_debug.py`:** raw WS for `-a`; `-s` max seconds; `-t N` stop after N order-book frames; `-r` REST book before/after for comparison. |
| Todo | **CHANGELOG** for `tools/oba/` (optional): high-level bullets per `__version__` bump for users who don’t read git log. |

---

## How to use this file

- Treat rows as **backlog**, not promises.
- When closing an item, move it to a short **“Done”** subsection with a version or date, or delete if noise.
- Bump `sfox_mm.py` `__version__` on every edit to that file (see `.cursor/rules/sfox-mm-version.mdc`).
