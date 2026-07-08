# Trailing Stop ("mantener y cabalgar") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `simulate_trailing()` to `optimize.py` — a variant of the grid strategy that, instead of selling immediately at `sell_rise_pct`, arms a trailing stop that follows the price's peak and only sells when the price pulls back `trail_pct` from that peak — plus a `--trail-pcts` CLI flag on `optimize.py` that prints a comparison table (vanilla ROI vs. trailing ROI) for the best combination found by the existing grid search.

**Architecture:** A new, self-contained function `simulate_trailing()` lives next to `simulate()` in `optimize.py`. It is a two-state machine (NORMAL / TRAILING) that walks every 1-minute bar: grid buy/sell decisions are only evaluated at checkpoints (every `interval_minutes`-th bar, same alignment as vanilla's `df.iloc[::interval_minutes]`), but once a trailing stop is armed it is checked on every 1-minute bar regardless of checkpoint alignment, using the `close` price. `simulate()` is never modified — it stays the baseline for comparison.

**Tech Stack:** Python, pandas, pytest (existing project stack — no new dependencies).

## Global Constraints

- Branch: `feature/trailing-stop` (already created from `main`, spec committed there as `docs/superpowers/specs/2026-07-08-trailing-stop-design.md`). All commits in this plan happen on that branch.
- `simulate()` in `optimize.py` must NOT change — it's the baseline every comparison is measured against.
- Only `close` price of each 1-minute bar is used to update/check the trailing stop (no high/low).
- All accounting (cash, profit, `profit_pool`) uses the **real execution price** — never the `sell_target` that would have triggered a vanilla sale. `trailing_capture` is a reporting-only metric (`qty × (exec_price − sell_target_at_arm_time)`), it does not affect cash/profit/`profit_pool`.
- While a trailing stop is armed, no buy or sell decision is evaluated for any other lot (grid is fully suspended until the trailing stop resolves).
- Test file: `test_trailing.py` at repo root (flat, matching `test_tradebot.py`/`test1.py` convention — no `tests/` subdirectory in this repo).
- No changes to `tradebot.py`, `backtest.py`, or the equity JSON/plot feature in this plan (out of scope per spec).

---

### Task 1: `simulate_trailing()` core implementation + primary scenario test

**Files:**
- Modify: `optimize.py` (insert new function after `simulate()` ends, before `daily_last()`)
- Test: `test_trailing.py` (new file)

**Interfaces:**
- Produces: `simulate_trailing(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, trail_pct: float = 0.0, on_trade=None, on_bar=None) -> dict`. Returns a dict with all the keys `simulate()` returns (`interval_minutes`, `max_buys`, `buy_drop_pct`, `sell_rise_pct`, `fee_pct`, `roi`, `profit`, `total_equity`, `total_fees`, `buys`, `sells`, `open_positions`) plus `trail_pct`, `trailing_capture_total` (float), `trailing_sells` (int), `trailing_captures` (list of float, one per trailing sale, in order). `df` must be the **full, unresampled** 1-minute bars with `timestamp` and `close` columns — `simulate_trailing` does its own checkpoint gating internally via `interval_minutes`, unlike `simulate()` which expects an already-resampled df.
- `on_trade` callback receives the same event shapes as `simulate()`'s (`BUY_INIT`, `BUY_GRID`, `SELL` with `price`, `qty`, `fee`, `cash`, `pool`, `timestamp`, `open_positions`, and for `SELL` also `buy_price`, `profit`, `buy_timestamp`, `order_id`), plus a `trailing_capture` key on every `SELL` event.

- [ ] **Step 1: Write the failing test**

Create `test_trailing.py`:

```python
import pytest
import pandas as pd

from optimize import simulate_trailing


def make_df(prices):
    idx = pd.date_range("2026-01-01 09:30", periods=len(prices), freq="1min")
    return pd.DataFrame({"timestamp": idx, "close": prices})


def test_trailing_rides_and_sells_on_pullback():
    """Compra en 100. Al checkpoint siguiente el precio llegó a 105 (rise 5%),
    en vez de vender arma el trailing. Sube a 108 y 110 (el stop sube con
    el pico), y al retroceder a 107 vende ahí — por encima de los 105 que
    hubiera vendido la versión vanilla."""
    df = make_df([100, 101, 105, 108, 110, 107])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=2, trail_pct=0.02,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "SELL"]
    sell = trades[1]
    assert sell["price"] == pytest.approx(107.0)
    assert sell["buy_price"] == pytest.approx(100.0)
    assert sell["profit"] == pytest.approx(700.0)
    assert sell["trailing_capture"] == pytest.approx(200.0)

    assert result["trailing_sells"] == 1
    assert result["trailing_capture_total"] == pytest.approx(200.0)
    assert result["roi"] == pytest.approx(0.7)
    assert result["open_positions"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_trailing.py -v`
Expected: FAIL with `ImportError: cannot import name 'simulate_trailing'`

- [ ] **Step 3: Write the implementation**

In `optimize.py`, insert this new function immediately after `simulate()` ends (right before the `def daily_last(...)` line):

```python
def simulate_trailing(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, trail_pct: float = 0.0, on_trade=None, on_bar=None) -> dict:
    """Como simulate(), pero al llegar a sell_rise_pct no vende: arma un
    trailing stop que sigue el pico del precio (vela a vela, no solo en
    checkpoints) y vende recién cuando el precio retrocede trail_pct desde
    ese pico. Mientras el trailing está armado no se evalúan compras ni
    ventas del grid. Usa precio real de ejecución en toda la contabilidad;
    trailing_capture (por venta y total) es una métrica de reporte que
    compara contra el sell_target que hubiera vendido la versión vanilla.
    df debe ser el histórico de 1 minuto completo, sin resamplear."""
    cash        = STARTING_CASH
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0
    trailing_capture_total = 0.0
    trailing_sells = 0
    trailing_captures = []

    trailing = None  # {"peak", "stop", "sell_target_ref"} cuando está armado

    for i, row in enumerate(df.itertuples(index=False)):
        price     = float(row.close)
        timestamp = row.timestamp

        if trailing is not None:
            if price > trailing["peak"]:
                trailing["peak"] = price
                trailing["stop"] = trailing["peak"] * (1.0 - trail_pct)
            if price <= trailing["stop"]:
                sold      = purchases.pop()
                revenue   = sold["qty"] * price
                sell_fee  = revenue * fee_pct
                cash     += revenue - sell_fee
                total_fees += sell_fee
                total_sells += 1
                profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
                if use_pool and profit > 0:
                    profit_pool += profit
                capture = sold["qty"] * (price - trailing["sell_target_ref"])
                trailing_capture_total += capture
                trailing_captures.append(capture)
                trailing_sells += 1
                if on_trade:
                    on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                              "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                              "open_positions": len(purchases),
                              "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                              "order_id": sold["order_id"], "trailing_capture": capture})
                trailing = None
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if i % interval_minutes != 0:
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if len(purchases) == 0:
            free_slots    = max_buys - len(purchases)
            bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
            effective_buy = buy_amount + bonus
            qty     = effective_buy / price
            buy_fee = effective_buy * fee_pct
            cash   -= effective_buy + buy_fee
            if use_pool:
                profit_pool -= bonus
            total_fees += buy_fee
            total_buys += 1
            order_id = total_buys
            purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                               "timestamp": timestamp, "order_id": order_id})
            if on_trade:
                on_trade({"type": "BUY_INIT", "price": price, "qty": qty, "fee": buy_fee,
                          "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                          "open_positions": len(purchases), "order_id": order_id})
        else:
            last_price  = purchases[-1]["price"]
            buy_target  = last_price * (1.0 - buy_drop_pct)
            sell_target = last_price * (1.0 + sell_rise_pct)

            if price <= buy_target:
                if len(purchases) < max_buys:
                    free_slots    = max_buys - len(purchases)
                    bonus         = (profit_pool / free_slots) if (use_pool and free_slots > 0) else 0.0
                    effective_buy = buy_amount + bonus
                    qty     = effective_buy / price
                    buy_fee = effective_buy * fee_pct
                    cash   -= effective_buy + buy_fee
                    if use_pool:
                        profit_pool -= bonus
                    total_fees += buy_fee
                    total_buys += 1
                    order_id = total_buys
                    purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy,
                                       "timestamp": timestamp, "order_id": order_id})
                    if on_trade:
                        on_trade({"type": "BUY_GRID", "price": price, "qty": qty, "fee": buy_fee,
                                  "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                                  "open_positions": len(purchases), "order_id": order_id})

            elif price >= sell_target:
                trailing = {"peak": price, "stop": price * (1.0 - trail_pct), "sell_target_ref": sell_target}

        if on_bar:
            on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)

    # Fin de datos con trailing activo: liquidar al último close disponible.
    if trailing is not None and purchases:
        price     = float(df.iloc[-1]["close"])
        timestamp = df.iloc[-1]["timestamp"]
        sold      = purchases.pop()
        revenue   = sold["qty"] * price
        sell_fee  = revenue * fee_pct
        cash     += revenue - sell_fee
        total_fees += sell_fee
        total_sells += 1
        profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
        if use_pool and profit > 0:
            profit_pool += profit
        capture = sold["qty"] * (price - trailing["sell_target_ref"])
        trailing_capture_total += capture
        trailing_captures.append(capture)
        trailing_sells += 1
        if on_trade:
            on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                      "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                      "open_positions": len(purchases),
                      "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                      "order_id": sold["order_id"], "trailing_capture": capture})

    final_price    = float(df.iloc[-1]["close"])
    holdings_value = sum(p["qty"] for p in purchases) * final_price
    total_equity   = cash + holdings_value
    profit         = total_equity - STARTING_CASH
    roi            = (profit / STARTING_CASH) * 100

    return {
        "interval_minutes": interval_minutes,
        "max_buys":       max_buys,
        "buy_drop_pct":   buy_drop_pct,
        "sell_rise_pct":  sell_rise_pct,
        "fee_pct":        fee_pct,
        "trail_pct":      trail_pct,
        "roi":            roi,
        "profit":         profit,
        "total_equity":   total_equity,
        "total_fees":     total_fees,
        "buys":           total_buys,
        "sells":          total_sells,
        "open_positions": len(purchases),
        "trailing_capture_total": trailing_capture_total,
        "trailing_sells":         trailing_sells,
        "trailing_captures":      trailing_captures,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_trailing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_trailing.py
git commit -m "feat: simulate_trailing() — trailing stop con seguimiento sobre el grid"
```

---

### Task 2: Return-shape test

**Files:**
- Test: `test_trailing.py` (append)

**Interfaces:**
- Consumes: `simulate_trailing` from Task 1 (unchanged signature/return keys).

- [ ] **Step 1: Write the test**

Append to `test_trailing.py`:

```python
def test_returns_same_shape_as_simulate_plus_trailing_fields():
    df = make_df([100, 101])
    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=True, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
    )
    expected_keys = {
        "interval_minutes", "max_buys", "buy_drop_pct", "sell_rise_pct", "fee_pct", "trail_pct",
        "roi", "profit", "total_equity", "total_fees", "buys", "sells", "open_positions",
        "trailing_capture_total", "trailing_sells", "trailing_captures",
    }
    assert set(result.keys()) == expected_keys
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m pytest test_trailing.py -v`
Expected: PASS (2 tests total)

- [ ] **Step 3: Commit**

```bash
git add test_trailing.py
git commit -m "test: verificar forma del dict devuelto por simulate_trailing"
```

---

### Task 3: Capture negativo (retroceso inmediato tras armar)

**Files:**
- Test: `test_trailing.py` (append)

**Interfaces:**
- Consumes: `simulate_trailing` from Task 1.

- [ ] **Step 1: Write the test**

Append to `test_trailing.py`:

```python
def test_trailing_capture_is_negative_on_immediate_pullback():
    """Arma el trailing en 105 (rise 5% sobre compra en 100) y en la vela
    siguiente el precio ya cayó a 103, por debajo del stop (103.95) — vende
    ahí. La ganancia real sigue siendo positiva (103 > 100), pero el
    trailing_capture es negativo porque vendió por debajo de los 105 que
    hubiera vendido la versión vanilla."""
    df = make_df([100, 105, 103])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    sell = trades[1]
    assert sell["price"] == pytest.approx(103.0)
    assert sell["profit"] == pytest.approx(300.0)
    assert sell["trailing_capture"] == pytest.approx(-200.0)
    assert result["trailing_capture_total"] == pytest.approx(-200.0)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m pytest test_trailing.py -v`
Expected: PASS (3 tests total). If it fails, re-check the `trailing["stop"]` computation in `simulate_trailing` before proceeding — do not change the test to make it pass.

- [ ] **Step 3: Commit**

```bash
git add test_trailing.py
git commit -m "test: trailing_capture negativo cuando el precio retrocede apenas se arma"
```

---

### Task 4: Nunca dispara — liquidar al último close

**Files:**
- Test: `test_trailing.py` (append)

**Interfaces:**
- Consumes: `simulate_trailing` from Task 1.

- [ ] **Step 1: Write the test**

Append to `test_trailing.py`:

```python
def test_trailing_liquidates_at_last_close_if_never_triggered():
    """El precio sube monótono hasta el final de los datos sin retroceder
    lo suficiente para disparar el stop — se liquida al último close
    disponible en vez de quedar con una posición fantasma."""
    df = make_df([100, 105, 108, 112])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "SELL"]
    sell = trades[1]
    assert sell["price"] == pytest.approx(112.0)
    assert result["trailing_sells"] == 1
    assert result["trailing_capture_total"] == pytest.approx(700.0)
    assert result["open_positions"] == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m pytest test_trailing.py -v`
Expected: PASS (4 tests total)

- [ ] **Step 3: Commit**

```bash
git add test_trailing.py
git commit -m "test: liquidar al último close si el trailing nunca dispara"
```

---

### Task 5: Recompra tras vender el único lote

**Files:**
- Test: `test_trailing.py` (append)

**Interfaces:**
- Consumes: `simulate_trailing` from Task 1.

- [ ] **Step 1: Write the test**

Append to `test_trailing.py`:

```python
def test_restarts_with_new_buy_after_trailing_empties_the_stack():
    """Tras vender el único lote por el trailing, el próximo checkpoint
    arranca un ciclo nuevo con una compra inicial — el grid no queda
    trabado con la pila vacía."""
    df = make_df([100, 105, 103, 90])
    trades = []

    simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "SELL", "BUY_INIT"]
    assert trades[2]["price"] == pytest.approx(90.0)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m pytest test_trailing.py -v`
Expected: PASS (5 tests total)

- [ ] **Step 3: Commit**

```bash
git add test_trailing.py
git commit -m "test: recompra tras vaciar la pila por el trailing"
```

---

### Task 6: Mantener varios lotes sin vender el de abajo (capstone)

**Files:**
- Test: `test_trailing.py` (append)

**Interfaces:**
- Consumes: `simulate_trailing` from Task 1.

- [ ] **Step 1: Write the test**

Append to `test_trailing.py`:

```python
def test_holds_lower_lot_untouched_and_reverts_reference_after_trailing_sell():
    """Dos lotes abiertos (100 y 95, tope=95). El tope arma trailing al
    llegar a 99.75 (rise 5%), sube a 105 sin vender NADA (ni el tope
    -todavía en trailing- ni el lote de abajo), y al retroceder a 103
    vende el tope (95) — no el de 100. Después, la referencia para la
    siguiente decisión vuelve correctamente al lote restante (100), no al
    precio de venta (103) ni al sell_target que armó el trailing (99.75):
    la compra grid siguiente se dispara en 94 porque 94 <= 100*(1-0.05)=95."""
    df = make_df([100, 95, 99.75, 105, 103, 94])
    trades = []

    result = simulate_trailing(
        df, max_buys=10, buy_drop_pct=0.05, sell_rise_pct=0.05, fee_pct=0.0,
        use_pool=False, buy_amount=10000.0, interval_minutes=1, trail_pct=0.01,
        on_trade=trades.append,
    )

    assert [t["type"] for t in trades] == ["BUY_INIT", "BUY_GRID", "SELL", "BUY_GRID"]
    assert trades[0]["price"] == pytest.approx(100.0)
    assert trades[1]["price"] == pytest.approx(95.0)

    sell = trades[2]
    assert sell["price"] == pytest.approx(103.0)
    assert sell["buy_price"] == pytest.approx(95.0)  # vendió el TOPE (95), no el de 100

    assert trades[3]["price"] == pytest.approx(94.0)  # referencia volvió al lote de 100

    assert result["open_positions"] == 2  # quedan el de 100 y el nuevo de 94
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python3 -m pytest test_trailing.py -v`
Expected: PASS (6 tests total). This is the most important test in the suite — if it fails, the bug is almost certainly that the NORMAL-state block (buy/sell evaluation) is running on a bar where `trailing is not None`, which would show up as extra trade events between `BUY_GRID@95` and `SELL@103`.

- [ ] **Step 3: Commit**

```bash
git add test_trailing.py
git commit -m "test: mantener lote de abajo intacto durante el trailing y revertir referencia tras vender"
```

---

### Task 7: `--trail-pcts` CLI flag + tabla comparativa en `optimize.py`

**Files:**
- Modify: `optimize.py` (argparse block, post-`best`/`best_trades` computation, end of `main()`)

**Interfaces:**
- Consumes: `simulate_trailing` from Task 1, `best` (dict from the existing grid search result, already computed in `main()`), `df_1min` (full unresampled bars, already loaded in `main()`).

- [ ] **Step 1: Add the CLI flag**

In `optimize.py`, find this line in `main()`:

```python
    parser.add_argument("--export-equity-json", action="store_true", help="Exportar la curva de equity diaria (al cierre) de cada combinación drop/rise a un JSON, para graficar después")
    args = parser.parse_args()
```

Replace with:

```python
    parser.add_argument("--export-equity-json", action="store_true", help="Exportar la curva de equity diaria (al cierre) de cada combinación drop/rise a un JSON, para graficar después")
    parser.add_argument("--trail-pcts", type=str, default=None, help="Lista de % de trailing stop a comparar contra la mejor combinación, separados por coma (ej. 0.5,1,1.5,2). Requiere un solo --intervals.")
    args = parser.parse_args()
```

- [ ] **Step 2: Validate `--trail-pcts` requires a single interval**

Find:

```python
    if args.export_equity_json and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --export-equity-json requiere un solo --intervals (no una lista).")
        return
```

Replace with:

```python
    if args.export_equity_json and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --export-equity-json requiere un solo --intervals (no una lista).")
        return

    if args.trail_pcts and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --trail-pcts requiere un solo --intervals (no una lista).")
        return
```

- [ ] **Step 3: Run simulate_trailing for each requested trail_pct on the best combo**

Find:

```python
        simulate(best_df, MAX_BUYS, best["buy_drop_pct"], best["sell_rise_pct"], fee_pct, use_pool, buy_amount,
                 best["interval_minutes"], on_trade=_capture_trade)

    best_by_interval = {}
```

Replace with:

```python
        simulate(best_df, MAX_BUYS, best["buy_drop_pct"], best["sell_rise_pct"], fee_pct, use_pool, buy_amount,
                 best["interval_minutes"], on_trade=_capture_trade)

    trail_pcts = []
    trailing_results = []
    if args.trail_pcts:
        trail_pcts = sorted({float(v.strip()) / 100 for v in args.trail_pcts.split(",") if v.strip()})
        for trail_pct in trail_pcts:
            trailing_results.append(
                simulate_trailing(df_1min, MAX_BUYS, best["buy_drop_pct"], best["sell_rise_pct"], fee_pct,
                                   use_pool, buy_amount, best["interval_minutes"], trail_pct=trail_pct)
            )

    best_by_interval = {}
```

- [ ] **Step 4: Print and log the comparison table**

Find the end of `main()`:

```python
    if args.export_equity_json:
        price_daily = daily_last(zip(df_1min["timestamp"], df_1min["close"].astype(float)))
        equity_json_path = f"optimize_{symbol}_{run_ts}_equity.json"
        payload = {
            "symbol":       symbol,
            "date_start":   periodo_start,
            "date_end":     periodo_end,
            "interval_minutes": intervals[0],
            "starting_cash": STARTING_CASH,
            "price":        [{"date": p["date"], "close": p["value"]} for p in price_daily],
            "series":       equity_series,
            "best_combo":   {"drop_pct": round(best["buy_drop_pct"] * 100), "rise_pct": round(best["sell_rise_pct"] * 100)},
            "best_trades":  best_trades,
        }
        with open(equity_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"JSON de equity   : {equity_json_path}")

if __name__ == "__main__":
    main()
```

Replace with:

```python
    if args.export_equity_json:
        price_daily = daily_last(zip(df_1min["timestamp"], df_1min["close"].astype(float)))
        equity_json_path = f"optimize_{symbol}_{run_ts}_equity.json"
        payload = {
            "symbol":       symbol,
            "date_start":   periodo_start,
            "date_end":     periodo_end,
            "interval_minutes": intervals[0],
            "starting_cash": STARTING_CASH,
            "price":        [{"date": p["date"], "close": p["value"]} for p in price_daily],
            "series":       equity_series,
            "best_combo":   {"drop_pct": round(best["buy_drop_pct"] * 100), "rise_pct": round(best["sell_rise_pct"] * 100)},
            "best_trades":  best_trades,
        }
        with open(equity_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"JSON de equity   : {equity_json_path}")

    if trailing_results:
        trail_sep = "-" * 80
        trail_lines = [
            "",
            f"  COMPARACIÓN TRAILING STOP (mejor combo: drop {best['buy_drop_pct']*100:.0f}% / "
            f"rise {best['sell_rise_pct']*100:.0f}% / interval {best['interval_minutes']} min)",
            trail_sep,
            f"  {'ESTRATEGIA':<16}  {'ROI':>9}  {'Compras':>7}  {'Ventas':>6}  {'Trailing capture':>17}",
            trail_sep,
            f"  {'vanilla':<16}  {best['roi']:>+8.2f}%  {best['buys']:>7}  {best['sells']:>6}  {'—':>17}",
        ]
        for trail_pct, r in zip(trail_pcts, trailing_results):
            label = f"trail {trail_pct*100:.1f}%"
            capture_str = "$" + format(r["trailing_capture_total"], "+,.0f")
            trail_lines.append(
                f"  {label:<16}  {r['roi']:>+8.2f}%  {r['buys']:>7}  {r['sells']:>6}  {capture_str:>17}"
            )
        trail_lines.append(trail_sep)
        trail_content = "\n".join(trail_lines)
        print(trail_content)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(trail_content + "\n")

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Syntax-check the file**

Run: `python3 -c "import ast; ast.parse(open('optimize.py').read()); print('sintaxis OK')"`
Expected: `sintaxis OK`

- [ ] **Step 6: Run the full test suite**

Run: `python3 -m pytest test_trailing.py test_tradebot.py -v`
Expected: PASS (all tests, no regressions in `test_tradebot.py`)

- [ ] **Step 7: Commit**

```bash
git add optimize.py
git commit -m "feat: --trail-pcts en optimize.py — tabla comparativa vanilla vs. trailing stop"
```

---

### Task 8: Verificación manual contra datos reales

**Files:** none (verification only, no code changes)

**Interfaces:** none.

- [ ] **Step 1: Run against cached TSLA data**

There's a cached 1-minute bars pickle for TSLA at the default date range on this machine (previously generated in `runs/TSLA/cache_TSLA_20260101_20260628_1Min.pkl` on another branch). Copy it to the flat path this branch's `optimize.py` expects, then run:

```bash
cd /home/david/Repos/David/Inversiones/invertirCarlos/alpaca
cp runs/TSLA/cache_TSLA_20260101_20260628_1Min.pkl ./cache_TSLA_20260101_20260628_1Min.pkl 2>/dev/null || true
python3 optimize.py --symbol TSLA --intervals 20 --trail-pcts "0.5,1,1.5,2"
```

Expected: the normal grid search output, followed by:

```
  COMPARACIÓN TRAILING STOP (mejor combo: drop 2% / rise 5% / interval 20 min)
--------------------------------------------------------------------------------
  ESTRATEGIA        ROI        Compras  Ventas  Trailing capture
  vanilla           +9.06%     38       29      —
  trail 0.5%        ...
  trail 1.0%        ...
  trail 1.5%        ...
  trail 2.0%        ...
--------------------------------------------------------------------------------
```

The `vanilla` row must show `+9.06%` / `38` / `29` — matching the already-known grid search result for TSLA at these defaults (confirms `simulate()` truly wasn't touched). The `trail X%` rows can be any ROI (that's literally what we're measuring) but `buys`/`sells` should be reasonably close in magnitude (not wildly different, e.g. not 10x).

- [ ] **Step 2: Clean up generated artifacts**

```bash
cd /home/david/Repos/David/Inversiones/invertirCarlos/alpaca
rm -f cache_TSLA_20260101_20260628_1Min.pkl optimize_TSLA_*.log optimize_TSLA_*.csv
git status --short
```

Expected: clean working tree (no untracked/modified files left over from the test run).

- [ ] **Step 3: Report the numbers back to the user**

No commit for this task — it's a verification checkpoint. Summarize the vanilla vs. trailing ROI numbers for the user so they can judge whether any `trail_pct` value is worth pursuing further (e.g. porting to `tradebot.py`, which stays explicitly out of scope for this plan).
