# Estrategia double trailing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `strategies/double_trailing/` — a grid strategy with trailing on BOTH ends (buy waits for a rebound from the valley, sell waits for a pullback from the peak), a 4D-grid optimizer, and a React viewer showing the top-20 combos vs. a vanilla reference row.

**Architecture:** `strategies/double_trailing/optimize.py` starts as a copy of `strategies/trailing/optimize.py`, gains `simulate_double_trailing()` (trailing-buy state machine mirroring the existing trailing-sell one), and replaces `main()` with a single-phase 4D grid (drop 1-10% × rise 1-10% × trail_buy × trail_sell). One JSON per run carries daily price + full series for the top-20 combos + a vanilla reference. The viewer clones the `strategies/trailing/viewer/` pattern with a selectable top-20 table.

**Tech Stack:** Python 3 (pandas, argparse); React 19 + Vite (no TypeScript), same as the two existing viewers.

## Global Constraints

- Interval is always 1 minute — no `--intervals` flag in the new CLI, no interval field in the viewer form.
- `MAX_BUYS = 10` fixed — no `--max-buys` flag, no form field.
- First buy is immediate (BUY_INIT on the first bar); trailing-buy applies only to grid buys.
- Do NOT modify anything under `strategies/vanilla/` or `strategies/trailing/` — copy from them only.
- Run backend tests with the system `pytest` from the repo root (the venv lacks pytest; `~/.local/bin/pytest` has pandas/alpaca/pytest).
- Never `from optimize import ...` in tests under `strategies/` (module-name collision across the three same-named `optimize.py` files — verified empirically in the trailing plan). Always load via `importlib.util.spec_from_file_location` with a unique module name.
- `trail_buy_pct` / `trail_sell_pct` are fractions internally (0.01 = 1%); CLI flags take percent values (`0.5,1,1.5`).

---

## Task 1: `simulate_double_trailing()` (TDD)

**Files:**
- Create: `strategies/double_trailing/optimize.py` (starts as a copy of `strategies/trailing/optimize.py`)
- Create: `strategies/double_trailing/test_optimize.py`

**Interfaces:**
- Produces: `simulate_double_trailing(df, max_buys, buy_drop_pct, sell_rise_pct, fee_pct, use_pool=True, buy_amount=BUY_AMOUNT, interval_minutes=1, trail_buy_pct=0.0, trail_sell_pct=0.0, on_trade=None, on_bar=None) -> dict`. The returned dict has every key `simulate_trailing()` returns plus `trail_buy_pct`, `trail_sell_pct`, `buy_capture_total`, `trailing_buys` (and renames nothing). Task 2's grid loop and export consume this exact signature.
- `on_trade` events: `BUY_INIT` / `BUY_GRID` (grid buys carry `"buy_capture"`) / `SELL` (carries `"trailing_capture"`), same field names as `simulate_trailing()`.

- [ ] **Step 1: Seed the module as a copy of trailing's**

```bash
cd /home/david/Repos/David/Inversiones/invertirCarlos/alpaca
mkdir -p strategies/double_trailing
cp strategies/trailing/optimize.py strategies/double_trailing/optimize.py
python3 -m py_compile strategies/double_trailing/optimize.py
```

(The copy brings `simulate()`, `simulate_trailing()`, `daily_last()`, constants, and a `main()`/manifest that Task 2 replaces. Do not edit the copied functions.)

- [ ] **Step 2: Write the failing tests**

Create `strategies/double_trailing/test_optimize.py`:

```python
import importlib.util
import os

import pandas as pd


def _load_module():
    path = os.path.join(os.path.dirname(__file__), "optimize.py")
    spec = importlib.util.spec_from_file_location("double_trailing_optimize_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
simulate_double_trailing = mod.simulate_double_trailing


def _df(prices):
    ts = pd.date_range("2026-01-05 09:30", periods=len(prices), freq="1min")
    return pd.DataFrame({"timestamp": ts, "close": [float(p) for p in prices]})


def _run(prices, **kw):
    args = dict(max_buys=10, buy_drop_pct=0.02, sell_rise_pct=0.07, fee_pct=0.0,
                use_pool=True, buy_amount=10_000.0, interval_minutes=1,
                trail_buy_pct=0.01, trail_sell_pct=0.01)
    args.update(kw)
    return simulate_double_trailing(_df(prices), **args)


def test_caida_sin_rebote_no_compra():
    # BUY_INIT a 100; cae a 98 (arma trailing de compra), sigue a 97 y 96 sin
    # rebotar 1% desde el mínimo: no debe comprar de nuevo.
    r = _run([100, 98, 97, 96])
    assert r["buys"] == 1
    assert r["open_positions"] == 1


def test_caida_con_rebote_compra_al_precio_del_rebote():
    # Arma a 98, valley baja a 96, rebota a 97 (>= 96*1.01 = 96.96): compra a 97.
    trades = []
    df = _df([100, 98, 96, 97, 97])
    simulate_double_trailing(df, 10, 0.02, 0.07, 0.0, True, 10_000.0, 1,
                             trail_buy_pct=0.01, trail_sell_pct=0.01,
                             on_trade=trades.append)
    grid_buys = [t for t in trades if t["type"] == "BUY_GRID"]
    assert len(grid_buys) == 1
    assert grid_buys[0]["price"] == 97.0
    # buy_capture = qty * (precio_de_armado 98 - precio_pagado 97) > 0
    qty = grid_buys[0]["qty"]
    assert abs(grid_buys[0]["buy_capture"] - qty * (98.0 - 97.0)) < 1e-9


def test_buy_capture_total_acumula():
    r = _run([100, 98, 96, 97, 97])
    assert r["buys"] == 2
    assert r["trailing_buys"] == 1
    qty = 10_000.0 / 97.0
    assert abs(r["buy_capture_total"] - qty * 1.0) < 1e-6


def test_venta_trailing_heredada():
    # BUY_INIT a 100; sube a 107 (arma venta), pico 110, stop 108.9, vende a 108.
    r = _run([100, 107, 110, 108])
    assert r["sells"] == 1
    assert r["trailing_sells"] == 1
    assert r["open_positions"] == 0


def test_ciclo_completo_contabilidad():
    # Compra init 100, arma compra a 98, valley 96, compra a 97;
    # luego sube: target de venta = 97*1.07 = 103.79 → arma a 104, pico 106,
    # stop 104.94, vende a 104.5 (LIFO: la posición de 97).
    r = _run([100, 98, 96, 97, 104, 106, 104.5])
    assert r["buys"] == 2 and r["sells"] == 1
    qty2 = 10_000.0 / 97.0
    expected_profit_trade = qty2 * 104.5 - 10_000.0
    # equity final = 100000 - 10000(init) - 10000(grid) + qty2*104.5 + qty1*104.5
    qty1 = 10_000.0 / 100.0
    expected_equity = 100_000.0 - 20_000.0 + qty2 * 104.5 + qty1 * 104.5
    assert abs(r["total_equity"] - expected_equity) < 1e-6
    assert expected_profit_trade > 0


def test_trailing_compra_armado_al_final_no_compra():
    r = _run([100, 98, 97, 96, 95])
    assert r["buys"] == 1
    assert r["open_positions"] == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest strategies/double_trailing/test_optimize.py -v`
Expected: `AttributeError: module ... has no attribute 'simulate_double_trailing'` (6 errors).

- [ ] **Step 4: Add `simulate_double_trailing()`**

Insert into `strategies/double_trailing/optimize.py` immediately after `simulate_trailing()` (i.e. right before `def daily_last(...)`):

```python
def simulate_double_trailing(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, trail_buy_pct: float = 0.0, trail_sell_pct: float = 0.0, on_trade=None, on_bar=None) -> dict:
    """Trailing en ambas puntas. Compra inicial inmediata. Compra de grid: al
    caer buy_drop_pct desde la última compra se arma un trailing de compra que
    sigue el mínimo vela a vela y compra recién cuando el precio rebota
    trail_buy_pct desde ese mínimo. Venta: idéntica a simulate_trailing()
    (pico + retroceso trail_sell_pct). Mientras cualquiera de los dos
    trailings está armado no se evalúan los gatillos del grid.
    buy_capture (por compra y total) compara el precio pagado contra el
    precio al que se armó el trailing (lo que hubiera pagado la versión sin
    trailing de compra). df debe ser histórico de 1 minuto completo."""
    cash        = STARTING_CASH
    purchases   = []
    profit_pool = 0.0
    total_buys  = total_sells = 0
    total_fees  = 0.0
    trailing_capture_total = 0.0
    trailing_sells = 0
    buy_capture_total = 0.0
    trailing_buys = 0

    trailing_sell = None  # {"peak", "stop", "sell_target_ref"}
    trailing_buy  = None  # {"valley", "arm", "arm_ref"}

    def _do_buy(price, timestamp, ev_type, buy_capture=None):
        nonlocal cash, profit_pool, total_fees, total_buys, buy_capture_total, trailing_buys
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
        ev = {"type": ev_type, "price": price, "qty": qty, "fee": buy_fee,
              "cash": cash, "pool": profit_pool, "timestamp": timestamp,
              "open_positions": len(purchases), "order_id": order_id}
        if buy_capture is not None:
            capture = qty * buy_capture
            buy_capture_total += capture
            trailing_buys += 1
            ev["buy_capture"] = capture
        if on_trade:
            on_trade(ev)

    for i, row in enumerate(df.itertuples(index=False)):
        price     = float(row.close)
        timestamp = row.timestamp

        if trailing_sell is not None:
            if price > trailing_sell["peak"]:
                trailing_sell["peak"] = price
                trailing_sell["stop"] = trailing_sell["peak"] * (1.0 - trail_sell_pct)
            if price <= trailing_sell["stop"]:
                sold      = purchases.pop()
                revenue   = sold["qty"] * price
                sell_fee  = revenue * fee_pct
                cash     += revenue - sell_fee
                total_fees += sell_fee
                total_sells += 1
                profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
                if use_pool and profit > 0:
                    profit_pool += profit
                capture = sold["qty"] * (price - trailing_sell["sell_target_ref"])
                trailing_capture_total += capture
                trailing_sells += 1
                if on_trade:
                    on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                              "cash": cash, "pool": profit_pool, "timestamp": timestamp,
                              "open_positions": len(purchases),
                              "buy_price": sold["price"], "profit": profit, "buy_timestamp": sold["timestamp"],
                              "order_id": sold["order_id"], "trailing_capture": capture})
                trailing_sell = None
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if trailing_buy is not None:
            if price < trailing_buy["valley"]:
                trailing_buy["valley"] = price
                trailing_buy["arm"]    = trailing_buy["valley"] * (1.0 + trail_buy_pct)
            if price >= trailing_buy["arm"]:
                if len(purchases) < max_buys:
                    _do_buy(price, timestamp, "BUY_GRID",
                            buy_capture=trailing_buy["arm_ref"] - price)
                trailing_buy = None
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if i % interval_minutes != 0:
            if on_bar:
                on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)
            continue

        if len(purchases) == 0:
            _do_buy(price, timestamp, "BUY_INIT")
        else:
            last_price  = purchases[-1]["price"]
            buy_target  = last_price * (1.0 - buy_drop_pct)
            sell_target = last_price * (1.0 + sell_rise_pct)

            if price <= buy_target:
                if len(purchases) < max_buys:
                    trailing_buy = {"valley": price, "arm": price * (1.0 + trail_buy_pct), "arm_ref": price}
            elif price >= sell_target:
                trailing_sell = {"peak": price, "stop": price * (1.0 - trail_sell_pct), "sell_target_ref": sell_target}

        if on_bar:
            on_bar(timestamp, cash + sum(p["qty"] for p in purchases) * price)

    # Fin de datos con trailing de venta activo: liquidar al último close.
    # (Con trailing de compra activo simplemente no se compra.)
    if trailing_sell is not None and purchases:
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
        capture = sold["qty"] * (price - trailing_sell["sell_target_ref"])
        trailing_capture_total += capture
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
        "max_buys":        max_buys,
        "buy_drop_pct":    buy_drop_pct,
        "sell_rise_pct":   sell_rise_pct,
        "fee_pct":         fee_pct,
        "trail_buy_pct":   trail_buy_pct,
        "trail_sell_pct":  trail_sell_pct,
        "roi":             roi,
        "profit":          profit,
        "total_equity":    total_equity,
        "total_fees":      total_fees,
        "buys":            total_buys,
        "sells":           total_sells,
        "open_positions":  len(purchases),
        "trailing_capture_total": trailing_capture_total,
        "trailing_sells":         trailing_sells,
        "buy_capture_total":      buy_capture_total,
        "trailing_buys":          trailing_buys,
    }
```

Note: `_do_buy()` deliberately centralizes the buy block that `simulate()`/`simulate_trailing()` duplicate inline — new code, new chance to be DRY without touching the copied originals.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest strategies/double_trailing/test_optimize.py -v`
Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add strategies/double_trailing/optimize.py strategies/double_trailing/test_optimize.py
git commit -m "feat: simulate_double_trailing() — trailing de compra y venta en strategies/double_trailing"
```

---

## Task 2: 4D-grid `main()`, export JSON and manifest

**Files:**
- Modify: `strategies/double_trailing/optimize.py` (replace `main()`, manifest regex/function; keep everything above `daily_last()` untouched)
- Modify: `strategies/double_trailing/test_optimize.py` (add manifest tests)

**Interfaces:**
- Consumes: `simulate_double_trailing()` (Task 1), `simulate()`/`daily_last()` (copied verbatim).
- Produces: CLI `python3 optimize.py --symbol X --date-start --date-end --buy-amount --fee-pct [--no-profit-pool] --trail-buy-pcts 0.5,1,1.5 --trail-sell-pcts 0.5,1,1.5 [--export-equity-json] [--out-dir viewer/public/data]`. One JSON per run at `out_dir/<SYMBOL>/optimize_<SYMBOL>_<run_ts>_equity.json` with shape:

  ```json
  {
    "symbol": "TSLA", "date_start": "...", "date_end": "...", "starting_cash": 100000.0,
    "price": [{"date", "close"}],
    "vanilla": {"drop_pct": 9, "rise_pct": 6, "roi", "profit", "buys", "sells",
                 "equity": [{"date", "equity"}], "trades": [...]},
    "combos": [{"drop_pct": 2, "rise_pct": 7, "trail_buy_pct": 1.0, "trail_sell_pct": 0.5,
                 "roi", "profit", "buys", "sells", "open_positions",
                 "buy_capture_total", "trailing_capture_total",
                 "equity": [{"date", "equity"}], "trades": [...]}]
  }
  ```

  `combos` = top 20 by ROI, sorted desc. `trades` entries match the viewer contract (`type`, `date`, `time`, `price`, `order_id`, and for SELL: `buy_price`, `profit`, `buy_date`, `buy_time`, `trailing_capture`; for BUY_GRID: `buy_capture`). `regenerate_manifest(out_dir)` writes `out_dir/manifest.json` as `[{"symbol", "run_ts", "date_start", "date_end", "file"}]` sorted by `run_ts` desc. Tasks 3 & 6 consume both shapes.

- [ ] **Step 1: Add failing manifest tests**

Append to `strategies/double_trailing/test_optimize.py`:

```python
import json

regenerate_manifest = mod.regenerate_manifest


def _write_run_json(path, **overrides):
    payload = {"symbol": "TSLA", "date_start": "2026-01-01", "date_end": "2026-06-28"}
    payload.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_manifest_una_entrada_por_corrida(tmp_path):
    d = tmp_path / "TSLA"
    d.mkdir()
    _write_run_json(d / "optimize_TSLA_20260711_120000_equity.json")
    _write_run_json(d / "optimize_TSLA_20260711_150000_equity.json")
    regenerate_manifest(str(tmp_path))
    manifest = json.load(open(tmp_path / "manifest.json"))
    assert [e["run_ts"] for e in manifest] == ["20260711_150000", "20260711_120000"]
    assert manifest[0]["file"] == "TSLA/optimize_TSLA_20260711_150000_equity.json"
    assert manifest[0]["symbol"] == "TSLA"


def test_manifest_ignora_archivos_ajenos(tmp_path):
    d = tmp_path / "TSLA"
    d.mkdir()
    _write_run_json(d / "optimize_TSLA_20260711_120000_equity.json")
    (d / "otracosa.json").write_text("{}")
    regenerate_manifest(str(tmp_path))
    manifest = json.load(open(tmp_path / "manifest.json"))
    assert len(manifest) == 1
```

Run: `pytest strategies/double_trailing/test_optimize.py -v` — the two new tests must FAIL (the copied `regenerate_manifest` expects trailing's base+trail_files grouping, so `run["file"]` KeyErrors / assertions fail). The 6 Task 1 tests must still pass.

- [ ] **Step 2: Replace the manifest regexes and `regenerate_manifest()`**

In `strategies/double_trailing/optimize.py`, delete the copied `BASE_EQUITY_RE`, `TRAIL_EQUITY_RE` and `regenerate_manifest()` and replace with:

```python
RUN_EQUITY_RE = re.compile(r"^optimize_(?P<symbol>[^_]+)_(?P<run_ts>\d{8}_\d{6})_equity\.json$")

def regenerate_manifest(out_dir: str) -> str:
    """Escanea out_dir/<símbolo>/ y regenera manifest.json con una entrada
    por corrida (a diferencia de trailing, acá cada corrida es un solo JSON)."""
    entries = []
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            m = RUN_EQUITY_RE.match(name)
            if not m:
                continue
            path = os.path.join(root, name)
            rel  = os.path.relpath(path, out_dir).replace(os.sep, "/")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            entries.append({
                "symbol":     payload.get("symbol", m.group("symbol")),
                "run_ts":     m.group("run_ts"),
                "date_start": payload.get("date_start"),
                "date_end":   payload.get("date_end"),
                "file":       rel,
            })
    entries.sort(key=lambda e: e["run_ts"], reverse=True)
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    return manifest_path
```

Run: `pytest strategies/double_trailing/test_optimize.py -v` — expected `8 passed`.

- [ ] **Step 3: Replace `main()`**

Delete the copied `main()` entirely (from `def main():` to the line before `if __name__ == "__main__":`) and replace with:

```python
def _capture_callbacks(trades_out, bars_out):
    """Devuelve (on_trade, on_bar) que registran trades en formato del viewer
    y equity por barra para reducir a diario."""
    def on_bar(ts, eq):
        bars_out.append((ts, eq))

    def on_trade(ev):
        trade = {
            "type":     ev["type"],
            "date":     ev["timestamp"].strftime("%Y-%m-%d"),
            "time":     ev["timestamp"].strftime("%H:%M"),
            "price":    ev["price"],
            "order_id": ev["order_id"],
        }
        if ev["type"] == "SELL":
            trade["buy_price"] = ev["buy_price"]
            trade["profit"]    = ev["profit"]
            trade["buy_date"]  = ev["buy_timestamp"].strftime("%Y-%m-%d")
            trade["buy_time"]  = ev["buy_timestamp"].strftime("%H:%M")
            if "trailing_capture" in ev:
                trade["trailing_capture"] = ev["trailing_capture"]
        if "buy_capture" in ev:
            trade["buy_capture"] = ev["buy_capture"]
        trades_out.append(trade)

    return on_trade, on_bar

def main():
    parser = argparse.ArgumentParser(description="Optimizador double trailing (compra y venta con trailing)")
    parser.add_argument("--symbol",     type=str,   default="TSLA",       help="Símbolo a analizar (default: TSLA)")
    parser.add_argument("--date-start", type=str,   default="2026-01-01", help="Fecha inicio YYYY-MM-DD (default: 2026-01-01)")
    parser.add_argument("--date-end",   type=str,   default="2026-06-28", help="Fecha fin YYYY-MM-DD (default: 2026-06-28)")
    parser.add_argument("--buy-amount", type=float, default=10_000.0,     help="Monto base por compra en USD (default: 10000)")
    parser.add_argument("--fee-pct",    type=float, default=0.0,          help="Fee por operación sobre el monto (default: 0.0). Ej: 0.001 = 0.1%%")
    parser.add_argument("--no-profit-pool", action="store_true",          help="Desactivar reinversión de ganancias (modo clásico)")
    parser.add_argument("--trail-buy-pcts",  type=str, default="0.5,1,1.5", help="Lista de %% de rebote para el trailing de compra, separados por coma (default: 0.5,1,1.5)")
    parser.add_argument("--trail-sell-pcts", type=str, default="0.5,1,1.5", help="Lista de %% de retroceso para el trailing de venta, separados por coma (default: 0.5,1,1.5)")
    parser.add_argument("--export-equity-json", action="store_true", help="Exportar JSON con top combos + referencia vanilla para el visor React")
    parser.add_argument("--out-dir", type=str, default="viewer/public/data", help="Carpeta base del JSON para el visor, organizado en out-dir/<símbolo>/ (default: viewer/public/data)")
    args = parser.parse_args()

    def _parse_pcts(raw, flag):
        try:
            vals = sorted({float(v.strip()) / 100 for v in raw.split(",") if v.strip()})
        except ValueError:
            print(f"Error: {flag} inválido ({raw}); usá números separados por coma, ej. 0.5,1,1.5")
            sys.exit(1)
        if not vals or any(v <= 0 for v in vals):
            print(f"Error: {flag} requiere valores positivos, ej. 0.5,1,1.5")
            sys.exit(1)
        return vals

    trail_buy_pcts  = _parse_pcts(args.trail_buy_pcts,  "--trail-buy-pcts")
    trail_sell_pcts = _parse_pcts(args.trail_sell_pcts, "--trail-sell-pcts")

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: credenciales no encontradas en .env")
        sys.exit(1)

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    if date_start >= date_end:
        print(f"Error: date-start ({args.date_start}) debe ser anterior a date-end ({args.date_end}).")
        sys.exit(1)
    os.makedirs(LOGS_DIR, exist_ok=True)
    cache_path = os.path.join(LOGS_DIR, f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}_1Min.pkl")

    if os.path.exists(cache_path):
        print(f"Cargando datos desde caché ({cache_path})…")
        df_1min = pd.read_pickle(cache_path)
    else:
        print(f"Descargando datos históricos (1 minuto) de Alpaca para {symbol}…")
        client = StockHistoricalDataClient(api_key, secret_key)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=date_start,
            end=date_end,
        )
        bars    = client.get_stock_bars(req)
        df_1min = bars.df.reset_index()
        df_1min.to_pickle(cache_path)
        print(f"Datos guardados en caché ({cache_path})")

    if len(df_1min) == 0:
        print(f"Error: no se encontraron velas de 1 minuto para {symbol} entre {args.date_start} y {args.date_end}. "
              f"Verificá el símbolo y el rango de fechas.")
        sys.exit(1)

    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    fee_pct    = args.fee_pct
    buy_amount = args.buy_amount
    use_pool   = not args.no_profit_pool

    # --- Referencia vanilla: grilla 2D drop x rise a intervalo 1 min ---
    vanilla_combos = list(itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE))
    print(f"Fase vanilla: {len(vanilla_combos)} combinaciones drop/rise (referencia)…")
    vanilla_results = []
    for buy_drop, sell_rise in vanilla_combos:
        vanilla_results.append(simulate(df_1min, MAX_BUYS, buy_drop, sell_rise, fee_pct, use_pool, buy_amount, 1))
    vanilla_results.sort(key=lambda r: r["roi"], reverse=True)
    vanilla_best = vanilla_results[0]

    # --- Grilla 4D double trailing ---
    combos = list(itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE, trail_buy_pcts, trail_sell_pcts))
    total  = len(combos)
    print(f"Grilla double trailing: {total} combinaciones "
          f"(drop x rise x trail_buy {[p*100 for p in trail_buy_pcts]} x trail_sell {[p*100 for p in trail_sell_pcts]}; "
          f"max_buys fijo = {MAX_BUYS}, buy_amount = ${buy_amount:,.0f}, fee = {fee_pct*100:.3f}%, pool = {'ON' if use_pool else 'OFF'})…\n")

    results = []
    for done, (buy_drop, sell_rise, tb, tsell) in enumerate(combos, 1):
        if done % 10 == 0 or done == total:
            print(f"  {done}/{total}", end="\r")
        results.append(simulate_double_trailing(df_1min, MAX_BUYS, buy_drop, sell_rise, fee_pct,
                                                use_pool, buy_amount, 1,
                                                trail_buy_pct=tb, trail_sell_pct=tsell))
    results.sort(key=lambda r: r["roi"], reverse=True)

    top_n    = 20
    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.log")
    csv_path = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.csv")

    periodo_start = df_1min.iloc[0]["timestamp"].strftime("%Y-%m-%d")
    periodo_end   = df_1min.iloc[-1]["timestamp"].strftime("%Y-%m-%d")
    best  = results[0]
    worst = results[-1]

    sep  = "=" * 96
    sep2 = "-" * 96
    header_row = (
        f"{'#':>3}  {'drop%':>6}  {'rise%':>6}  {'t.buy%':>7}  {'t.sell%':>8}  {'ROI%':>8}  "
        f"{'Ganancia':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'BuyCapt':>10}  {'SellCapt':>10}"
    )

    def fmt_row(rank, r):
        return (
            f"{rank:>3}  "
            f"{r['buy_drop_pct']*100:>5.0f}%  "
            f"{r['sell_rise_pct']*100:>5.0f}%  "
            f"{r['trail_buy_pct']*100:>6.1f}%  "
            f"{r['trail_sell_pct']*100:>7.1f}%  "
            f"{r['roi']:>+8.2f}%  "
            f"${r['profit']:>+11,.0f}  "
            f"{r['buys']:>7}  "
            f"{r['sells']:>6}  "
            f"{r['open_positions']:>5}  "
            f"${r['buy_capture_total']:>+9,.0f}  "
            f"${r['trailing_capture_total']:>+9,.0f}"
        )

    lines = [
        sep,
        f"  OPTIMIZE DOUBLE TRAILING {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        f"  Período analizado:  {periodo_start}  →  {periodo_end}",
        f"  Velas de 1 minuto:  {len(df_1min)}   |   Intervalo fijo: 1 min",
        f"  Capital inicial:    ${STARTING_CASH:,.2f}   |   Monto por compra: ${buy_amount:,.2f}",
        f"  max_buys (fijo):    {MAX_BUYS}",
        f"  Combinaciones evaluadas: {total} (double trailing) + {len(vanilla_combos)} (vanilla referencia)",
        sep2,
        f"  REFERENCIA VANILLA (mejor drop/rise sin trailing)",
        f"  drop {vanilla_best['buy_drop_pct']*100:.0f}% / rise {vanilla_best['sell_rise_pct']*100:.0f}%  "
        f"ROI {vanilla_best['roi']:+.2f}%  Ganancia ${vanilla_best['profit']:+,.0f}  "
        f"Compras {vanilla_best['buys']}  Ventas {vanilla_best['sells']}",
        sep2,
        "",
        f"  TOP {top_n} COMBINACIONES DOUBLE TRAILING (ordenadas por ROI)",
        sep2,
        header_row,
        sep2,
    ]
    for rank, r in enumerate(results[:top_n], 1):
        lines.append(fmt_row(rank, r))

    lines += [
        sep2,
        "",
        "  PEOR COMBINACIÓN",
        sep2,
        fmt_row(total, worst),
        "",
        "  TODAS LAS COMBINACIONES (ordenadas por ROI)",
        sep2,
        header_row,
        sep2,
    ]
    for rank, r in enumerate(results, 1):
        lines.append(fmt_row(rank, r))
    lines += [sep, f"  CSV completo: {csv_path}", sep]

    log_content = "\n".join(lines)
    console_lines = lines[:lines.index("  TODAS LAS COMBINACIONES (ordenadas por ROI)")]
    print("\n" + "\n".join(console_lines))
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log_content + "\n")
    pd.DataFrame(results).to_csv(csv_path, index=False)
    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")

    if args.export_equity_json:
        symbol_dir = os.path.join(args.out_dir, symbol)
        os.makedirs(symbol_dir, exist_ok=True)
        price_daily = daily_last(zip(df_1min["timestamp"], df_1min["close"].astype(float)))

        # Series de la referencia vanilla (re-corre el mejor combo con callbacks)
        v_trades, v_bars = [], []
        on_trade, on_bar = _capture_callbacks(v_trades, v_bars)
        simulate(df_1min, MAX_BUYS, vanilla_best["buy_drop_pct"], vanilla_best["sell_rise_pct"],
                 fee_pct, use_pool, buy_amount, 1, on_trade=on_trade, on_bar=on_bar)
        vanilla_payload = {
            "drop_pct": round(vanilla_best["buy_drop_pct"] * 100),
            "rise_pct": round(vanilla_best["sell_rise_pct"] * 100),
            "roi":      vanilla_best["roi"],
            "profit":   vanilla_best["profit"],
            "buys":     vanilla_best["buys"],
            "sells":    vanilla_best["sells"],
            "equity":   [{"date": p["date"], "equity": p["value"]} for p in daily_last(v_bars)],
            "trades":   v_trades,
        }

        # Series de cada combo del top N (re-corre con callbacks)
        combos_payload = []
        print(f"Generando series del top {top_n}…")
        for r in results[:top_n]:
            c_trades, c_bars = [], []
            on_trade, on_bar = _capture_callbacks(c_trades, c_bars)
            simulate_double_trailing(df_1min, MAX_BUYS, r["buy_drop_pct"], r["sell_rise_pct"], fee_pct,
                                     use_pool, buy_amount, 1,
                                     trail_buy_pct=r["trail_buy_pct"], trail_sell_pct=r["trail_sell_pct"],
                                     on_trade=on_trade, on_bar=on_bar)
            combos_payload.append({
                "drop_pct":       round(r["buy_drop_pct"] * 100),
                "rise_pct":       round(r["sell_rise_pct"] * 100),
                "trail_buy_pct":  r["trail_buy_pct"] * 100,
                "trail_sell_pct": r["trail_sell_pct"] * 100,
                "roi":            r["roi"],
                "profit":         r["profit"],
                "buys":           r["buys"],
                "sells":          r["sells"],
                "open_positions": r["open_positions"],
                "buy_capture_total":      r["buy_capture_total"],
                "trailing_capture_total": r["trailing_capture_total"],
                "equity": [{"date": p["date"], "equity": p["value"]} for p in daily_last(c_bars)],
                "trades": c_trades,
            })

        payload = {
            "symbol":        symbol,
            "date_start":    periodo_start,
            "date_end":      periodo_end,
            "starting_cash": STARTING_CASH,
            "price":         [{"date": p["date"], "close": p["value"]} for p in price_daily],
            "vanilla":       vanilla_payload,
            "combos":        combos_payload,
        }
        equity_json_path = os.path.join(symbol_dir, f"optimize_{symbol}_{run_ts}_equity.json")
        with open(equity_json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"JSON del visor   : {equity_json_path}")
        manifest_path = regenerate_manifest(args.out_dir)
        print(f"Manifest visor   : {manifest_path}")
```

Run `python3 -m py_compile strategies/double_trailing/optimize.py` and `pytest strategies/double_trailing/test_optimize.py -v` (expected `8 passed`).

- [ ] **Step 4: Manual verification with cached data**

```bash
cd strategies/double_trailing
mkdir -p logs
cp ../vanilla/logs/cache_TSLA_20260601_20260610_1Min.pkl logs/
python3 optimize.py --symbol TSLA --date-start 2026-06-01 --date-end 2026-06-10 \
  --buy-amount 10000 --fee-pct 0 --trail-buy-pcts 0.5,1 --trail-sell-pcts 0.5,1 \
  --export-equity-json --out-dir viewer/public/data
python3 - <<'EOF'
import json, glob
p = glob.glob("viewer/public/data/TSLA/*_equity.json")[0]
d = json.load(open(p))
assert set(d) == {"symbol","date_start","date_end","starting_cash","price","vanilla","combos"}, set(d)
assert len(d["combos"]) == 20 and "equity" in d["combos"][0] and "trades" in d["combos"][0]
assert "equity" in d["vanilla"] and "trades" in d["vanilla"]
m = json.load(open("viewer/public/data/manifest.json"))
assert len(m) == 1 and m[0]["file"].startswith("TSLA/")
print("EXPORT OK — top combo:", {k: d["combos"][0][k] for k in ("drop_pct","rise_pct","trail_buy_pct","trail_sell_pct","roi")})
EOF
rm -rf logs viewer
cd ../..
```

Expected: grid runs 400 combos (10×10×2×2) + 100 vanilla, prints the top-20 table with `t.buy%`/`t.sell%`/`BuyCapt`/`SellCapt` columns, and the inline check prints `EXPORT OK — ...`.

- [ ] **Step 5: Commit**

```bash
git add strategies/double_trailing/optimize.py strategies/double_trailing/test_optimize.py
git commit -m "feat: grilla 4D, export JSON y manifest en strategies/double_trailing/optimize.py"
```

---

## Task 3: Viewer scaffold (Vite + middlewares)

**Files:**
- Create: `strategies/double_trailing/viewer/` — `package.json`, `.oxlintrc.json`, `.gitignore`, `index.html`, `vite.config.js`, `public/data/.gitkeep`, `public/data/.gitignore`, `src/main.jsx`, `src/App.jsx` (placeholder)

**Interfaces:**
- Consumes: CLI + `regenerate_manifest` from Task 2 via child-process spawn.
- Produces: `POST /api/run-optimize` (body `{symbol, date_start, date_end, buy_amount, fee_pct, trail_buy_pcts, trail_sell_pcts}`), `POST /api/delete-run` (body `{run_ts, symbol}` — deletes the run's single `file` from the manifest), static `/data/*`. Consumed by Task 6's `App.jsx`.

- [ ] **Step 1: Copy the identical scaffold files from the trailing viewer**

```bash
cd /home/david/Repos/David/Inversiones/invertirCarlos/alpaca
mkdir -p strategies/double_trailing/viewer/public/data strategies/double_trailing/viewer/src
cp strategies/trailing/viewer/.oxlintrc.json strategies/double_trailing/viewer/
cp strategies/trailing/viewer/.gitignore strategies/double_trailing/viewer/
cp strategies/trailing/viewer/public/data/.gitignore strategies/double_trailing/viewer/public/data/
touch strategies/double_trailing/viewer/public/data/.gitkeep
cp strategies/trailing/viewer/src/main.jsx strategies/double_trailing/viewer/src/main.jsx
```

- [ ] **Step 2: Create `package.json`** (same as trailing's but renamed)

```json
{
  "name": "double-trailing-viewer",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "oxlint",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.2.7",
    "react-dom": "^19.2.7"
  },
  "devDependencies": {
    "@types/react": "^19.2.17",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.3",
    "oxlint": "^1.71.0",
    "vite": "^8.1.1"
  }
}
```

- [ ] **Step 3: Create `index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Double Trailing Viewer</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Create `vite.config.js`**

Copy `strategies/trailing/viewer/vite.config.js` to `strategies/double_trailing/viewer/vite.config.js`, then apply these exact edits:

Edit A — validation: old

```js
const SYMBOL_RE = /^[A-Z.]{1,10}$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TRAIL_PCTS_RE = /^\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*$/

function validateParams(body) {
  const { symbol, date_start, date_end, buy_amount, fee_pct, trail_pcts } = body
  if (typeof symbol !== 'string' || !SYMBOL_RE.test(symbol.toUpperCase())) {
    return 'symbol inválido'
  }
  if (typeof date_start !== 'string' || !DATE_RE.test(date_start)) return 'date_start inválido'
  if (typeof date_end !== 'string' || !DATE_RE.test(date_end)) return 'date_end inválido'
  const amount = Number(buy_amount)
  if (!Number.isFinite(amount) || amount <= 0) return 'buy_amount inválido'
  const fee = Number(fee_pct)
  if (!Number.isFinite(fee) || fee < 0) return 'fee_pct inválido'
  if (typeof trail_pcts !== 'string' || !TRAIL_PCTS_RE.test(trail_pcts.trim())) return 'trail_pcts inválido'
  return null
}
```

new

```js
const SYMBOL_RE = /^[A-Z.]{1,10}$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TRAIL_PCTS_RE = /^\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*$/

function validateParams(body) {
  const { symbol, date_start, date_end, buy_amount, fee_pct, trail_buy_pcts, trail_sell_pcts } = body
  if (typeof symbol !== 'string' || !SYMBOL_RE.test(symbol.toUpperCase())) {
    return 'symbol inválido'
  }
  if (typeof date_start !== 'string' || !DATE_RE.test(date_start)) return 'date_start inválido'
  if (typeof date_end !== 'string' || !DATE_RE.test(date_end)) return 'date_end inválido'
  const amount = Number(buy_amount)
  if (!Number.isFinite(amount) || amount <= 0) return 'buy_amount inválido'
  const fee = Number(fee_pct)
  if (!Number.isFinite(fee) || fee < 0) return 'fee_pct inválido'
  if (typeof trail_buy_pcts !== 'string' || !TRAIL_PCTS_RE.test(trail_buy_pcts.trim())) return 'trail_buy_pcts inválido'
  if (typeof trail_sell_pcts !== 'string' || !TRAIL_PCTS_RE.test(trail_sell_pcts.trim())) return 'trail_sell_pcts inválido'
  return null
}
```

Edit B — spawn args: old

```js
            const args = [
              'optimize.py',
              '--symbol', symbol,
              '--date-start', body.date_start,
              '--date-end', body.date_end,
              '--buy-amount', String(Number(body.buy_amount)),
              '--fee-pct', String(Number(body.fee_pct)),
              '--intervals', '1',
              '--export-equity-json',
              '--trail-pcts', body.trail_pcts.trim(),
              '--out-dir', 'viewer/public/data',
            ]
```

new

```js
            const args = [
              'optimize.py',
              '--symbol', symbol,
              '--date-start', body.date_start,
              '--date-end', body.date_end,
              '--buy-amount', String(Number(body.buy_amount)),
              '--fee-pct', String(Number(body.fee_pct)),
              '--export-equity-json',
              '--trail-buy-pcts', body.trail_buy_pcts.trim(),
              '--trail-sell-pcts', body.trail_sell_pcts.trim(),
              '--out-dir', 'viewer/public/data',
            ]
```

Edit C — the directory constant: old

```js
const TRAILING_DIR = path.resolve(__dirname, '..')
```

new

```js
const STRATEGY_DIR = path.resolve(__dirname, '..')
```

and replace the two `cwd: TRAILING_DIR` occurrences with `cwd: STRATEGY_DIR`.

Edit D — delete-run file list (single file per run): old

```js
            const files = [run.base_file, ...run.trail_files.map((t) => t.file)]
```

new

```js
            const files = [run.file]
```

- [ ] **Step 5: Create placeholder `src/App.jsx`**

```jsx
export default function App() {
  return <div>Double trailing viewer scaffold OK</div>
}
```

- [ ] **Step 6: Install and verify the dev server**

```bash
cd strategies/double_trailing/viewer
npm install --prefer-offline --no-audit --fund=false
(npm run dev -- --port 5181 --strictPort &) && sleep 3
curl -s http://localhost:5181/ | grep -o '<title>[^<]*</title>'
pkill -f "vite --port 5181"
cd ../../..
```

Expected: `<title>Double Trailing Viewer</title>`. (Use `--prefer-offline`: the npm cache already holds every dependency from the trailing viewer install.)

- [ ] **Step 7: Commit**

```bash
git add strategies/double_trailing/viewer
git commit -m "feat: scaffold del proyecto Vite para el viewer de double trailing"
```

---

## Task 4: Shared chart utilities + charts (copied from trailing viewer)

**Files:**
- Create (verbatim copies from `strategies/trailing/viewer/src/`): `src/lib/chartMath.js`, `src/hooks/useZoom.js`, `src/hooks/useTooltip.js`, `src/components/ChartFrame.jsx`, `src/components/Tooltip.jsx`, `src/components/TrailingTradesChart.jsx`, `src/components/IndexedEquityChart.jsx`, `src/index.css`
- Modify: `src/main.jsx` (ensure `import './index.css'` — already present in the copied main.jsx)

**Interfaces:**
- Produces (same signatures as trailing viewer): `<TrailingTradesChart id title price trades showTooltip hideTooltip>` (handles BUY/BUY_INIT/BUY_GRID/SELL; includes the post-zoom marker-index fix already committed in trailing) and `<IndexedEquityChart id price vanillaEquity trailingEquity trailingLabel showTooltip hideTooltip>`. Consumed by Task 6.

- [ ] **Step 1: Copy the files**

```bash
cd /home/david/Repos/David/Inversiones/invertirCarlos/alpaca
mkdir -p strategies/double_trailing/viewer/src/lib strategies/double_trailing/viewer/src/hooks strategies/double_trailing/viewer/src/components
for f in lib/chartMath.js hooks/useZoom.js hooks/useTooltip.js components/ChartFrame.jsx components/Tooltip.jsx components/TrailingTradesChart.jsx components/IndexedEquityChart.jsx index.css; do
  cp "strategies/trailing/viewer/src/$f" "strategies/double_trailing/viewer/src/$f"
done
for f in lib/chartMath.js hooks/useZoom.js hooks/useTooltip.js components/ChartFrame.jsx components/Tooltip.jsx components/TrailingTradesChart.jsx components/IndexedEquityChart.jsx index.css; do
  diff -q "strategies/trailing/viewer/src/$f" "strategies/double_trailing/viewer/src/$f"
done
```

Expected: no `diff` output (byte-identical copies).

- [ ] **Step 2: Add the buy_capture tooltip row to the copied `TrailingTradesChart.jsx`**

The only adaptation: BUY rows can now carry `buy_capture`. In the copied file's `TradeRow`, old:

```jsx
  return (
    <div className="tooltip-row">
      <span className="key">
        <span className="key-line" style={{ background: colorVar }}></span>
        {type} {whenStr}
        {orderTag}
      </span>
      <span className="val">${trade.price.toFixed(2)}</span>
    </div>
  );
```

new:

```jsx
  return (
    <>
      <div className="tooltip-row">
        <span className="key">
          <span className="key-line" style={{ background: colorVar }}></span>
          {type} {whenStr}
          {orderTag}
        </span>
        <span className="val">${trade.price.toFixed(2)}</span>
      </div>
      {trade.buy_capture != null && (
        <div className="tooltip-row">
          <span className="key">↳ buy capture</span>
          <span
            className="val"
            style={{ color: trade.buy_capture >= 0 ? "var(--marker-buy)" : "var(--marker-sell)" }}
          >
            {trade.buy_capture >= 0 ? "+" : "-"}${Math.abs(trade.buy_capture).toFixed(2)}
          </span>
        </div>
      )}
    </>
  );
```

- [ ] **Step 3: Lint and commit**

```bash
cd strategies/double_trailing/viewer
npx oxlint src
cd ../../..
git add strategies/double_trailing/viewer/src
git commit -m "feat: charts y utilidades compartidas para el viewer de double trailing"
```

Expected oxlint: no parse errors (the known exhaustive-deps warnings inherited from trailing are fine).

---

## Task 5: `RunForm.jsx` + `TopTable.jsx`

**Files:**
- Create: `strategies/double_trailing/viewer/src/components/RunForm.jsx`
- Create: `strategies/double_trailing/viewer/src/components/TopTable.jsx`

**Interfaces:**
- Consumes: `fmtMoney` from `../lib/chartMath` (Task 4).
- Produces: `<RunForm onRunComplete>` posting `{symbol, date_start, date_end, buy_amount, fee_pct, trail_buy_pcts, trail_sell_pcts}` to `/api/run-optimize`. `<TopTable rows selectedKey onSelect>` where `rows = [{key, label, dropPct, risePct, trailBuyPct, trailSellPct, roi, profit, buys, sells, buyCapture, sellCapture}]` — `trailBuyPct`/`trailSellPct`/`buyCapture`/`sellCapture` are `null` for the vanilla row; rows render in the order given (App pre-sorts); clicking a row calls `onSelect(key)`. Consumed by Task 6.

- [ ] **Step 1: Create `RunForm.jsx`**

```jsx
import { useState } from "react";

const DEFAULTS = {
  symbol: "TSLA",
  date_start: "2026-01-01",
  date_end: "2026-06-28",
  buy_amount: 10000,
  fee_pct: 0,
  trail_buy_pcts: "0.5,1,1.5",
  trail_sell_pcts: "0.5,1,1.5",
};

const TRAIL_PCTS_RE = /^\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*$/;

export default function RunForm({ onRunComplete }) {
  const [form, setForm] = useState(DEFAULTS);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    for (const field of ["trail_buy_pcts", "trail_sell_pcts"]) {
      if (!TRAIL_PCTS_RE.test(form[field].trim())) {
        setError(`${field === "trail_buy_pcts" ? "Trailing compra" : "Trailing venta"} inválido — usá números positivos separados por coma (ej. 0.5,1,1.5)`);
        return;
      }
    }
    setRunning(true);
    setError(null);
    try {
      const res = await fetch("/api/run-optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      await onRunComplete();
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <form className="panel run-form" onSubmit={handleSubmit}>
      <h2>Nueva corrida</h2>
      <div className="run-form-grid">
        <label>
          Símbolo
          <input type="text" value={form.symbol}
                 onChange={(e) => update("symbol", e.target.value.toUpperCase())}
                 disabled={running} required />
        </label>
        <label>
          Desde
          <input type="date" value={form.date_start}
                 onChange={(e) => update("date_start", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Hasta
          <input type="date" value={form.date_end}
                 onChange={(e) => update("date_end", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Monto por compra ($)
          <input type="number" min="1" step="1" value={form.buy_amount}
                 onChange={(e) => update("buy_amount", e.target.value)}
                 disabled={running} required />
        </label>
        <label>
          Fee (%)
          <input type="number" min="0" step="0.01" value={form.fee_pct * 100}
                 onChange={(e) => update("fee_pct", Number(e.target.value) / 100)}
                 disabled={running} required />
        </label>
        <label>
          Trailing compra % (rebote)
          <input type="text" value={form.trail_buy_pcts}
                 onChange={(e) => update("trail_buy_pcts", e.target.value)}
                 disabled={running} placeholder="0.5,1,1.5" required />
        </label>
        <label>
          Trailing venta % (retroceso)
          <input type="text" value={form.trail_sell_pcts}
                 onChange={(e) => update("trail_sell_pcts", e.target.value)}
                 disabled={running} placeholder="0.5,1,1.5" required />
        </label>
      </div>
      <button type="submit" disabled={running}>
        {running ? "Ejecutando…" : "Generar"}
      </button>
      {running && (
        <span className="run-status">
          Corriendo la grilla 4D con intervalo fijo de 1 minuto — puede tardar varios minutos…
        </span>
      )}
      {error && <div className="run-error">{error}</div>}
    </form>
  );
}
```

- [ ] **Step 2: Create `TopTable.jsx`**

```jsx
import { fmtMoney } from "../lib/chartMath";

function pct(v) {
  return v == null ? "—" : `${v}%`;
}

function money(v) {
  return v == null ? "—" : `${v >= 0 ? "+" : "-"}${fmtMoney(Math.abs(v))}`;
}

export default function TopTable({ rows, selectedKey, onSelect }) {
  return (
    <div className="panel">
      <h2>Top combinaciones vs. referencia vanilla</h2>
      <table>
        <thead>
          <tr>
            <th>Estrategia</th>
            <th>Drop</th>
            <th>Rise</th>
            <th>T.compra</th>
            <th>T.venta</th>
            <th>ROI</th>
            <th>Ganancia</th>
            <th>Compras</th>
            <th>Ventas</th>
            <th>Buy capture</th>
            <th>Sell capture</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.key}
              className={"row-clickable" + (selectedKey === r.key ? " active" : "")}
              onClick={() => onSelect(r.key)}
            >
              <th>{r.label}</th>
              <td>{pct(r.dropPct)}</td>
              <td>{pct(r.risePct)}</td>
              <td>{r.trailBuyPct == null ? "—" : `${r.trailBuyPct}%`}</td>
              <td>{r.trailSellPct == null ? "—" : `${r.trailSellPct}%`}</td>
              <td>{r.roi >= 0 ? "+" : ""}{r.roi.toFixed(2)}%</td>
              <td>{money(r.profit)}</td>
              <td>{r.buys}</td>
              <td>{r.sells}</td>
              <td>{money(r.buyCapture)}</td>
              <td>{money(r.sellCapture)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Add the clickable-row styles to `src/index.css`**

Append at the end of the file:

```css
tr.row-clickable { cursor: pointer; }
tr.row-clickable:hover td, tr.row-clickable:hover th { background: color-mix(in srgb, var(--price-line) 8%, transparent); }
tr.row-clickable.active td, tr.row-clickable.active th { background: color-mix(in srgb, var(--price-line) 16%, transparent); }
```

- [ ] **Step 4: Lint and commit**

```bash
cd strategies/double_trailing/viewer
npx oxlint src/components/RunForm.jsx src/components/TopTable.jsx
cd ../../..
git add strategies/double_trailing/viewer/src
git commit -m "feat: RunForm y TopTable del viewer de double trailing"
```

---

## Task 6: `App.jsx` wiring + end-to-end browser verification

**Files:**
- Modify: `strategies/double_trailing/viewer/src/App.jsx` (replace placeholder)

**Interfaces:**
- Consumes: everything from Tasks 2-5 (manifest/run JSON shapes, middlewares, all components).

- [ ] **Step 1: Replace `src/App.jsx`**

```jsx
import { useCallback, useEffect, useMemo, useState } from "react";
import TopTable from "./components/TopTable";
import TrailingTradesChart from "./components/TrailingTradesChart";
import IndexedEquityChart from "./components/IndexedEquityChart";
import RunForm from "./components/RunForm";
import Tooltip from "./components/Tooltip";
import { useTooltip } from "./hooks/useTooltip";

function runLabel(entry) {
  return `${entry.symbol} · ${entry.date_start} → ${entry.date_end} · corrida ${entry.run_ts}`;
}

function comboKey(c, i) {
  return `combo-${i}-${c.drop_pct}-${c.rise_pct}-${c.trail_buy_pct}-${c.trail_sell_pct}`;
}

export default function App() {
  const [manifest, setManifest] = useState(null);
  const [manifestError, setManifestError] = useState(null);
  const [selectedRunKey, setSelectedRunKey] = useState(null);
  const [runData, setRunData] = useState(null);
  const [dataError, setDataError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [selectedRowKey, setSelectedRowKey] = useState(null);
  const { tooltip, show, hide } = useTooltip();

  const fetchManifest = useCallback(async (selectNewest) => {
    try {
      const r = await fetch("/data/manifest.json", { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const entries = await r.json();
      setManifest(entries);
      setManifestError(null);
      if (selectNewest) {
        if (entries.length > 0) setSelectedRunKey(`${entries[0].symbol}|${entries[0].run_ts}`);
        else {
          setSelectedRunKey(null);
          setRunData(null);
        }
      }
    } catch (err) {
      setManifestError(err.message);
    }
  }, []);

  useEffect(() => {
    fetchManifest(true);
  }, [fetchManifest]);

  const selectedRun = useMemo(
    () => manifest?.find((r) => `${r.symbol}|${r.run_ts}` === selectedRunKey) ?? null,
    [manifest, selectedRunKey]
  );

  useEffect(() => {
    if (!selectedRun) return;
    setRunData(null);
    setDataError(null);
    setSelectedRowKey(null);
    fetch(`/data/${selectedRun.file}`, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${selectedRun.file}`);
        return r.json();
      })
      .then(setRunData)
      .catch((err) => setDataError(err.message));
  }, [selectedRun]);

  async function handleDelete() {
    if (!selectedRun) return;
    if (!window.confirm(`¿Borrar esta corrida?\n\n${runLabel(selectedRun)}`)) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await fetch("/api/delete-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_ts: selectedRun.run_ts, symbol: selectedRun.symbol }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      await fetchManifest(true);
    } catch (err) {
      setDeleteError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  const rows = useMemo(() => {
    if (!runData) return [];
    const v = runData.vanilla;
    const vanillaRow = {
      key: "vanilla",
      label: `Vanilla (drop ${v.drop_pct}% / rise ${v.rise_pct}%)`,
      dropPct: v.drop_pct,
      risePct: v.rise_pct,
      trailBuyPct: null,
      trailSellPct: null,
      roi: v.roi,
      profit: v.profit,
      buys: v.buys,
      sells: v.sells,
      buyCapture: null,
      sellCapture: null,
    };
    const comboRows = runData.combos.map((c, i) => ({
      key: comboKey(c, i),
      label: `#${i + 1}`,
      dropPct: c.drop_pct,
      risePct: c.rise_pct,
      trailBuyPct: c.trail_buy_pct,
      trailSellPct: c.trail_sell_pct,
      roi: c.roi,
      profit: c.profit,
      buys: c.buys,
      sells: c.sells,
      buyCapture: c.buy_capture_total,
      sellCapture: c.trailing_capture_total,
    }));
    return [...comboRows, vanillaRow].sort((a, b) => b.roi - a.roi);
  }, [runData]);

  const activeRowKey = selectedRowKey ?? rows[0]?.key ?? null;
  const activeCombo = useMemo(() => {
    if (!runData || activeRowKey == null || activeRowKey === "vanilla") return null;
    const i = runData.combos.findIndex((c, idx) => comboKey(c, idx) === activeRowKey);
    return i >= 0 ? runData.combos[i] : null;
  }, [runData, activeRowKey]);

  return (
    <div className="app">
      <div className="app-header">
        <h1>Double Trailing Viewer</h1>
        {manifest && manifest.length > 0 && (
          <div className="run-picker">
            <select value={selectedRunKey ?? ""} onChange={(e) => setSelectedRunKey(e.target.value)}>
              {manifest.map((entry) => (
                <option key={`${entry.symbol}|${entry.run_ts}`} value={`${entry.symbol}|${entry.run_ts}`}>
                  {runLabel(entry)}
                </option>
              ))}
            </select>
            <button type="button" className="delete-run-btn" onClick={handleDelete}
                    disabled={deleting || !selectedRun} title="Borrar esta corrida">
              {deleting ? "Borrando…" : "Borrar"}
            </button>
          </div>
        )}
      </div>

      {deleteError && <div className="panel error">No se pudo borrar la corrida: {deleteError}</div>}

      <RunForm onRunComplete={() => fetchManifest(true)} />

      {manifestError && (
        <div className="panel error">
          No se pudo cargar data/manifest.json ({manifestError}). Generá una corrida desde el form.
        </div>
      )}
      {manifest && manifest.length === 0 && (
        <div className="panel error">No hay corridas en data/. Generá una desde el form de arriba.</div>
      )}
      {dataError && <div className="panel error">No se pudo cargar la corrida: {dataError}</div>}

      {runData && rows.length > 0 && (
        <>
          <div className="subtitle">
            {runData.date_start} → {runData.date_end} · intervalo 1 min (fijo) · equity diaria al cierre
          </div>

          <TopTable rows={rows} selectedKey={activeRowKey} onSelect={setSelectedRowKey} />

          {activeRowKey === "vanilla" ? (
            <>
              <TrailingTradesChart
                id="trades"
                title={`Precio ${runData.symbol} + operaciones — Vanilla`}
                price={runData.price}
                trades={runData.vanilla.trades}
                showTooltip={show}
                hideTooltip={hide}
              />
              <div className="panel">
                <p className="subtitle" style={{ margin: 0 }}>
                  Elegí una fila double-trailing para comparar su equity contra vanilla.
                </p>
              </div>
            </>
          ) : (
            activeCombo && (
              <>
                <TrailingTradesChart
                  id="trades"
                  title={`Precio ${runData.symbol} + operaciones — drop ${activeCombo.drop_pct}% / rise ${activeCombo.rise_pct}% / t.compra ${activeCombo.trail_buy_pct}% / t.venta ${activeCombo.trail_sell_pct}%`}
                  price={runData.price}
                  trades={activeCombo.trades}
                  showTooltip={show}
                  hideTooltip={hide}
                />
                <IndexedEquityChart
                  id="equity-compare"
                  price={runData.price}
                  vanillaEquity={runData.vanilla.equity}
                  trailingEquity={activeCombo.equity}
                  trailingLabel={`DT ${activeCombo.trail_buy_pct}%/${activeCombo.trail_sell_pct}%`}
                  showTooltip={show}
                  hideTooltip={hide}
                />
              </>
            )
          )}
        </>
      )}

      <Tooltip tooltip={tooltip} />
    </div>
  );
}
```

- [ ] **Step 2: Seed fixture data and start the dev server**

```bash
cd strategies/double_trailing
mkdir -p logs
cp ../vanilla/logs/cache_TSLA_20260601_20260610_1Min.pkl logs/
python3 optimize.py --symbol TSLA --date-start 2026-06-01 --date-end 2026-06-10 \
  --buy-amount 10000 --fee-pct 0 --trail-buy-pcts 0.5,1 --trail-sell-pcts 0.5,1 \
  --export-equity-json --out-dir viewer/public/data
cd viewer
npm run dev -- --port 5181 --strictPort &
sleep 3
```

- [ ] **Step 3: Drive it in a real browser (Chrome tools) and verify the golden path**

Load `http://localhost:5181/` and verify, in order:

1. Run picker shows one entry (`TSLA · 2026-06-01 → 2026-06-09 · corrida <run_ts>`).
2. TopTable shows 21 rows (20 combos + vanilla), sorted by ROI desc, vanilla row shows `—` in the four trailing/capture columns.
3. The best-ROI row is selected by default and the trades chart title reflects its params.
4. Clicking the vanilla row switches the trades chart to `— Vanilla` and replaces the equity chart with the "Elegí una fila…" note.
5. Clicking any combo row shows both charts; the `IndexedEquityChart` has 3 lines (dashed price, vanilla, DT).
6. Hovering a BUY_GRID marker shows the `buy capture` tooltip row; hovering a SELL marker shows `trailing capture`. Zoom in (drag) past the first marker, hover a visible marker, confirm the tooltip matches the marker under the cursor (regression check for the marker-index fix).
7. Submitting the form with defaults reaches the backend (real Alpaca call; slow is OK) and either succeeds (new run at top of dropdown) or surfaces a clear error in `run-error`.
8. Deleting the fresh run removes it from the dropdown and its file from `public/data/`. (Note: `window.confirm` blocks the Chrome extension's injected scripts — override `window.confirm = () => true` via the JS tool before clicking Borrar, as discovered in the trailing viewer's verification.)

- [ ] **Step 4: Stop the server, clean up, commit**

```bash
pkill -f "vite --port 5181"
cd ..
rm -rf logs viewer/public/data/TSLA
cd ../..
git add strategies/double_trailing/viewer/src/App.jsx
git commit -m "feat: App.jsx del viewer de double trailing — top-20 seleccionable + charts"
```

---

## Task 7: README + full-suite check

**Files:**
- Modify: `README.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Add the double_trailing bullet to README's "Estructura de Archivos"**

Insert immediately after the `strategies/trailing/` bullet — old:

```markdown
* `strategies/trailing/`: `trade_trailing_bot.py` (bot de producción) y `optimize.py` (incluye la lógica de backtest, sin archivo separado) para la estrategia trailing (grid drop/rise + trailing stop sobre el mejor combo), más `viewer/` — app React (Vite) que compara vanilla vs. distintos % de trailing: lee los JSON de `viewer/public/data/<símbolo>/` generados por `optimize.py --export-equity-json --trail-pcts` (siempre a intervalo de 1 minuto) y permite lanzar nuevas corridas desde un form en la UI (`npm run dev` dentro de `viewer/`).
```

new:

```markdown
* `strategies/trailing/`: `trade_trailing_bot.py` (bot de producción) y `optimize.py` (incluye la lógica de backtest, sin archivo separado) para la estrategia trailing (grid drop/rise + trailing stop sobre el mejor combo), más `viewer/` — app React (Vite) que compara vanilla vs. distintos % de trailing: lee los JSON de `viewer/public/data/<símbolo>/` generados por `optimize.py --export-equity-json --trail-pcts` (siempre a intervalo de 1 minuto) y permite lanzar nuevas corridas desde un form en la UI (`npm run dev` dentro de `viewer/`).
* `strategies/double_trailing/`: `optimize.py` con `simulate_double_trailing()` — trailing en ambas puntas (la compra espera un rebote `trail_buy%` desde el mínimo tras caer `drop%`; la venta espera un retroceso `trail_sell%` desde el pico tras subir `rise%`) — y grilla 4D drop × rise × trail_buy × trail_sell (`--trail-buy-pcts`/`--trail-sell-pcts`, siempre a 1 minuto), más `viewer/` — app React (Vite) con tabla top-20 seleccionable vs. referencia vanilla (`npm run dev` dentro de `viewer/`).
```

- [ ] **Step 2: Full-suite check and commit**

```bash
pytest -q
```

Expected: all tests pass (the pre-existing suite + the 8 new `strategies/double_trailing/test_optimize.py` tests → 20 total).

```bash
git add README.md
git commit -m "docs: strategies/double_trailing en el README"
```

---

## Post-plan check

`git log --oneline -8`, `git status` (clean), `pytest -q` (all green).
