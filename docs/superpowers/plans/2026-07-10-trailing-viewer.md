# Viewer para la estrategia trailing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React (Vite) viewer for the trailing-stop strategy, analogous to `strategies/vanilla/viewer/`, that always runs `strategies/trailing/optimize.py` with a 1-minute interval and shows vanilla-vs-trailing% comparisons.

**Architecture:** `strategies/trailing/optimize.py` gains `--out-dir` + a manifest that groups one vanilla JSON with N trailing JSONs per run. A new, independent Vite project at `strategies/trailing/viewer/` (same middleware pattern as vanilla's viewer) drives it from a form and renders a comparison table + two charts (trades, indexed equity) for whichever series (vanilla or one trailing %) the user selects.

**Tech Stack:** Python 3 (pandas, argparse) for the backend script; React 19 + Vite (no TypeScript) for the viewer, same stack as `strategies/vanilla/viewer/`.

## Global Constraints

- Interval is always 1 minute for this strategy — the viewer never sends an `intervals`/interval field; the middleware hardcodes `--intervals 1`.
- `max_buys` stays fixed at 10 in `strategies/trailing/optimize.py` (`MAX_BUYS` constant) — no `--max-buys` flag, no form field.
- Do not change `simulate()` / `simulate_trailing()` bodies — only wiring around them.
- Do not touch `strategies/vanilla/viewer/`.
- Run backend tests with the system `pytest` from the repo root (`pytest <path>`), not the project venv — the venv has pandas/alpaca but no pytest installed; `~/.local/bin/pytest` (system Python) already has pandas/alpaca/pytest and is what the existing `test_trailing.py`/`test_tradebot.py` use.
- `strategies/vanilla/optimize.py` and `strategies/trailing/optimize.py` are both literally named `optimize.py` with no `__init__.py` anywhere under `strategies/`. Any test that does `from optimize import ...` risks resolving to whichever of the two got imported first into `sys.modules['optimize']` in that pytest session (confirmed empirically — see Task 1). Never add a plain `from optimize import ...` test under `strategies/trailing/`; always load the module via `importlib.util.spec_from_file_location` using an explicit path, as shown in Task 1.

---

## Task 1: `regenerate_manifest()` in `strategies/trailing/optimize.py`

**Files:**
- Modify: `strategies/trailing/optimize.py`
- Create: `strategies/trailing/test_optimize.py`

**Interfaces:**
- Produces: `regenerate_manifest(out_dir: str) -> str` — scans `out_dir` recursively, groups equity JSON files by `(symbol, run_ts)` into runs of the shape `{"symbol": str, "run_ts": str, "date_start": str, "date_end": str, "base_file": str, "trail_files": [{"trail_pct": float, "file": str}, ...]}`, writes `out_dir/manifest.json` (a JSON array of those run dicts, sorted by `run_ts` descending, runs without a `base_file` dropped), and returns the manifest path. Task 3's `vite.config.js` and Task 7's `App.jsx` consume this shape.

- [ ] **Step 1: Write the failing tests**

Create `strategies/trailing/test_optimize.py`:

```python
import importlib.util
import json
import os


def _load_trailing_optimize():
    path = os.path.join(os.path.dirname(__file__), "optimize.py")
    spec = importlib.util.spec_from_file_location("trailing_optimize_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trailing_optimize = _load_trailing_optimize()
regenerate_manifest = trailing_optimize.regenerate_manifest


def _write_equity_json(path, **overrides):
    payload = {
        "symbol": "TSLA",
        "date_start": "2026-01-01",
        "date_end": "2026-06-28",
        "interval_minutes": 1,
        "starting_cash": 100000.0,
    }
    payload.update(overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_groups_base_and_trail_files_into_one_run(tmp_path):
    symbol_dir = tmp_path / "TSLA"
    symbol_dir.mkdir()
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_120000_equity.json")
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_120000_trail_0.5_equity.json")
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_120000_trail_1.0_equity.json")

    regenerate_manifest(str(tmp_path))

    with open(tmp_path / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert len(manifest) == 1
    run = manifest[0]
    assert run["symbol"] == "TSLA"
    assert run["run_ts"] == "20260710_120000"
    assert run["base_file"] == "TSLA/optimize_TSLA_20260710_120000_equity.json"
    assert [t["trail_pct"] for t in run["trail_files"]] == [0.5, 1.0]
    assert run["trail_files"][0]["file"] == "TSLA/optimize_TSLA_20260710_120000_trail_0.5_equity.json"


def test_run_without_base_file_is_dropped(tmp_path):
    symbol_dir = tmp_path / "TSLA"
    symbol_dir.mkdir()
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_130000_trail_0.5_equity.json")

    regenerate_manifest(str(tmp_path))

    with open(tmp_path / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest == []


def test_multiple_runs_sorted_by_run_ts_desc(tmp_path):
    symbol_dir = tmp_path / "TSLA"
    symbol_dir.mkdir()
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_090000_equity.json")
    _write_equity_json(symbol_dir / "optimize_TSLA_20260710_150000_equity.json")

    regenerate_manifest(str(tmp_path))

    with open(tmp_path / "manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert [r["run_ts"] for r in manifest] == ["20260710_150000", "20260710_090000"]
```

Note on the `importlib.util` loading: a plain `from optimize import regenerate_manifest` would be fragile here. `strategies/vanilla/optimize.py` and `strategies/trailing/optimize.py` are both named `optimize.py`, and neither `strategies/` nor `strategies/trailing/` has an `__init__.py`. pytest's default "prepend" import mode caches imported modules in `sys.modules` by their bare name — so if `test_trailing.py` (at the repo root, importing the root `optimize.py` shim which re-exports `strategies/vanilla/optimize.py`) runs first in the same pytest session, a later `from optimize import ...` anywhere else reuses that cached module instead of loading `strategies/trailing/optimize.py`. This was verified empirically while designing this plan: running both test files together made a probe under `strategies/trailing/` resolve `regenerate_manifest` to `strategies/vanilla/optimize.py`. Loading via `importlib.util.spec_from_file_location` with an explicit path and a unique module name sidesteps this entirely.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest strategies/trailing/test_optimize.py -v`
Expected: `AttributeError: module 'trailing_optimize_under_test' has no attribute 'regenerate_manifest'` (3 errors).

- [ ] **Step 3: Add `import re` and `regenerate_manifest()` to `strategies/trailing/optimize.py`**

Modify the top of the file — old:

```python
import os
import argparse
import itertools
```

new:

```python
import os
import re
import argparse
import itertools
```

Then insert the regex constants and the function right before `def main():` — old:

```python
    return [{"date": d.isoformat(), "value": v} for d, v in sorted(daily.items())]

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
```

new:

```python
    return [{"date": d.isoformat(), "value": v} for d, v in sorted(daily.items())]

BASE_EQUITY_RE = re.compile(r"^optimize_(?P<symbol>[^_]+)_(?P<run_ts>\d{8}_\d{6})_equity\.json$")
TRAIL_EQUITY_RE = re.compile(r"^optimize_(?P<symbol>[^_]+)_(?P<run_ts>\d{8}_\d{6})_trail_(?P<trail_pct>\d+(?:\.\d+)?)_equity\.json$")

def regenerate_manifest(out_dir: str) -> str:
    """Escanea out_dir (incluyendo subcarpetas por símbolo, ej. out_dir/TSLA/)
    agrupando cada corrida (símbolo + run_ts) con su JSON base (grid vanilla,
    usado como referencia) y sus JSON de trailing asociados, y regenera
    manifest.json para que el visor React sepa qué corridas puede listar en
    el dropdown. Un run sin JSON base (por ejemplo si quedó a medias) no se
    incluye."""
    runs: dict = {}

    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, out_dir).replace(os.sep, "/")

            m_trail = TRAIL_EQUITY_RE.match(name)
            if m_trail:
                key = (m_trail.group("symbol"), m_trail.group("run_ts"))
                run = runs.setdefault(key, {"trail_files": []})
                run["trail_files"].append({"trail_pct": float(m_trail.group("trail_pct")), "file": rel})
                continue

            m_base = BASE_EQUITY_RE.match(name)
            if not m_base:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            key = (m_base.group("symbol"), m_base.group("run_ts"))
            run = runs.setdefault(key, {"trail_files": []})
            run["symbol"]     = payload.get("symbol", m_base.group("symbol"))
            run["run_ts"]     = m_base.group("run_ts")
            run["date_start"] = payload.get("date_start")
            run["date_end"]   = payload.get("date_end")
            run["base_file"]  = rel

    entries = [run for run in runs.values() if "base_file" in run]
    for run in entries:
        run["trail_files"].sort(key=lambda t: t["trail_pct"])
    entries.sort(key=lambda run: run["run_ts"], reverse=True)

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    return manifest_path

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest strategies/trailing/test_optimize.py -v`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add strategies/trailing/optimize.py strategies/trailing/test_optimize.py
git commit -m "feat: regenerate_manifest() para agrupar corridas de trailing en strategies/trailing/optimize.py"
```

---

## Task 2: `--out-dir`, robust errors, and output wiring in `strategies/trailing/optimize.py`

**Files:**
- Modify: `strategies/trailing/optimize.py`

**Interfaces:**
- Consumes: `regenerate_manifest(out_dir)` from Task 1.
- Produces: CLI now accepts `--out-dir` (default `viewer/public/data`); on `--export-equity-json`, writes the base equity JSON and every `_trail_<pct>_equity.json` under `out_dir/<SYMBOL>/` (previously trail files landed in the cwd, ungrouped) and calls `regenerate_manifest(args.out_dir)` at the end; cache/log/csv files move under a `logs/` directory (mirrors `strategies/vanilla/optimize.py`); all error paths now `sys.exit(1)` instead of a bare `return` (a bare `return` exits with code 0, which the viewer's `run-optimize` middleware — built in Task 3 — treats as success even though nothing was generated); trade capture dicts (both the vanilla `best_trades` and the trailing `trades`) gain `time`/`buy_time` fields alongside `date`/`buy_date`, matching `strategies/vanilla/optimize.py`, so the frontend can spread same-day markers instead of stacking them.

This task has no automated test (it drives real Alpaca network calls / real filesystem paths through `main()`, same as `strategies/vanilla/optimize.py` — that file also has no dedicated test for this code path). Verify manually with cached data, per Step 8 below; the exact commands and expected output here were run against this exact diff while writing this plan.

- [ ] **Step 1: Add `import sys` and `LOGS_DIR`**

old:

```python
import os
import re
import argparse
import itertools
import json
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ---------------------------------------------------------------------------
# Rangos de búsqueda (máximo 10%)
# ---------------------------------------------------------------------------
MAX_BUYS           = 10                                 # fijo, no modificable
BUY_DROP_RANGE     = [r / 100 for r in range(1, 11)]   # 1% … 10%
SELL_RISE_RANGE    = [r / 100 for r in range(1, 11)]   # 1% … 10%

BUY_AMOUNT    = 10_000.0
STARTING_CASH = 100_000.0

# ---------------------------------------------------------------------------
# Simulación (misma lógica que backtest.py, sin I/O)
# ---------------------------------------------------------------------------
```

new:

```python
import os
import re
import sys
import argparse
import itertools
import json
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ---------------------------------------------------------------------------
# Rangos de búsqueda (máximo 10%)
# ---------------------------------------------------------------------------
MAX_BUYS           = 10                                 # fijo, no modificable
BUY_DROP_RANGE     = [r / 100 for r in range(1, 11)]   # 1% … 10%
SELL_RISE_RANGE    = [r / 100 for r in range(1, 11)]   # 1% … 10%

BUY_AMOUNT    = 10_000.0
STARTING_CASH = 100_000.0

LOGS_DIR = "logs"   # cache .pkl, .log y .csv de cada corrida

# ---------------------------------------------------------------------------
# Simulación (misma lógica que backtest.py, sin I/O)
# ---------------------------------------------------------------------------
```

- [ ] **Step 2: Add `--out-dir`, `sys.exit(1)` on errors, date/empty-data guards, and move cache path under `LOGS_DIR`**

old:

```python
    parser.add_argument("--trail-pcts", type=str, default=None, help="Lista de % de trailing stop a comparar contra la mejor combinación, separados por coma (ej. 0.5,1,1.5,2). Requiere un solo --intervals.")
    args = parser.parse_args()

    if args.export_equity_json and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --export-equity-json requiere un solo --intervals (no una lista).")
        return

    if args.trail_pcts and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --trail-pcts requiere un solo --intervals (no una lista).")
        return

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: credenciales no encontradas en .env")
        return

    # --- Descargar datos una sola vez (caché por símbolo y período) ---
    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    cache_path = f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}_1Min.pkl"

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

    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")
```

new:

```python
    parser.add_argument("--trail-pcts", type=str, default=None, help="Lista de % de trailing stop a comparar contra la mejor combinación, separados por coma (ej. 0.5,1,1.5,2). Requiere un solo --intervals.")
    parser.add_argument("--out-dir", type=str, default="viewer/public/data", help="Carpeta base donde escribir el JSON de equity para el visor React, organizado en out-dir/<símbolo>/ (default: viewer/public/data)")
    args = parser.parse_args()

    if args.export_equity_json and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --export-equity-json requiere un solo --intervals (no una lista).")
        sys.exit(1)

    if args.trail_pcts and len(set(v.strip() for v in args.intervals.split(","))) > 1:
        print("Error: --trail-pcts requiere un solo --intervals (no una lista).")
        sys.exit(1)

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: credenciales no encontradas en .env")
        sys.exit(1)

    # --- Descargar datos una sola vez (caché por símbolo y período) ---
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
              f"Verificá el símbolo y el rango de fechas (puede estar fuera del histórico disponible o no tener "
              f"días hábiles).")
        sys.exit(1)

    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")
```

- [ ] **Step 3: Move `log_path`/`csv_path` under `LOGS_DIR`**

old:

```python
    top_n     = 20
    run_ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = f"optimize_{symbol}_{run_ts}.log"
    csv_path  = f"optimize_{symbol}_{run_ts}.csv"
```

new:

```python
    top_n     = 20
    run_ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path  = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.log")
    csv_path  = os.path.join(LOGS_DIR, f"optimize_{symbol}_{run_ts}.csv")
```

- [ ] **Step 4: Add `time`/`buy_time` to the vanilla `best_trades` capture**

old:

```python
        def _capture_trade(ev):
            trade = {
                "type":     "SELL" if ev["type"] == "SELL" else "BUY",
                "date":     ev["timestamp"].strftime("%Y-%m-%d"),
                "price":    ev["price"],
                "order_id": ev["order_id"],
            }
            if ev["type"] == "SELL":
                trade["buy_price"] = ev["buy_price"]
                trade["profit"]    = ev["profit"]
                trade["buy_date"]  = ev["buy_timestamp"].strftime("%Y-%m-%d")
            best_trades.append(trade)
```

new:

```python
        def _capture_trade(ev):
            trade = {
                "type":     "SELL" if ev["type"] == "SELL" else "BUY",
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
            best_trades.append(trade)
```

- [ ] **Step 5: Write the base equity JSON under `out_dir/<symbol>/`**

old:

```python
    price_daily = None

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

        # --- Generar JSONs para trailing (si se solicita) ---
```

new:

```python
    price_daily = None
    symbol_dir  = None

    if args.export_equity_json:
        symbol_dir = os.path.join(args.out_dir, symbol)
        os.makedirs(symbol_dir, exist_ok=True)
        price_daily = daily_last(zip(df_1min["timestamp"], df_1min["close"].astype(float)))
        equity_json_name = f"optimize_{symbol}_{run_ts}_equity.json"
        equity_json_path = os.path.join(symbol_dir, equity_json_name)
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

        # --- Generar JSONs para trailing (si se solicita) ---
```

- [ ] **Step 6: Add `time`/`buy_time` to the trailing trade capture**

old:

```python
            def on_trade(ev):
                trade = {
                    "type":     ev["type"],   # "BUY_INIT", "BUY_GRID", "SELL"
                    "date":     ev["timestamp"].strftime("%Y-%m-%d"),
                    "price":    ev["price"],
                    "order_id": ev["order_id"],
                }
                if ev["type"] == "SELL":
                    trade["buy_price"] = ev["buy_price"]
                    trade["profit"]    = ev["profit"]
                    trade["buy_date"]  = ev["buy_timestamp"].strftime("%Y-%m-%d")
                    if "trailing_capture" in ev:
                        trade["trailing_capture"] = ev["trailing_capture"]
                    
                trades_trail.append(trade)
                # Si quieres registrar compras, puedes añadirlas aquí (opcional)
```

new:

```python
            def on_trade(ev):
                trade = {
                    "type":     ev["type"],   # "BUY_INIT", "BUY_GRID", "SELL"
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

                trades_trail.append(trade)
```

- [ ] **Step 7: Write trail JSONs under `symbol_dir` and call `regenerate_manifest` after**

old:

```python
            trail_json_path = f"optimize_{symbol}_{run_ts}_trail_{trail_pct*100:.1f}_equity.json"
            with open(trail_json_path, "w", encoding="utf-8") as f:
                json.dump(trail_payload, f)
            print(f"JSON de equity con trailing {trail_pct*100:.1f}%: {trail_json_path}")

    if trailing_results:
```

new:

```python
            trail_json_name = f"optimize_{symbol}_{run_ts}_trail_{trail_pct*100:.1f}_equity.json"
            trail_json_path = os.path.join(symbol_dir, trail_json_name)
            with open(trail_json_path, "w", encoding="utf-8") as f:
                json.dump(trail_payload, f)
            print(f"JSON de equity con trailing {trail_pct*100:.1f}%: {trail_json_path}")

    if args.export_equity_json:
        manifest_path = regenerate_manifest(args.out_dir)
        print(f"Manifest visor   : {manifest_path}")

    if trailing_results:
```

- [ ] **Step 8: Manually verify with cached data (no network needed)**

```bash
cd strategies/trailing
python3 -m py_compile optimize.py
mkdir -p logs
cp ../vanilla/logs/cache_TSLA_20260601_20260610_1Min.pkl logs/
python3 optimize.py --symbol TSLA --date-start 2026-06-01 --date-end 2026-06-10 \
  --buy-amount 10000 --fee-pct 0 --intervals 1 --export-equity-json \
  --trail-pcts 0.5,1 --out-dir viewer/public/data
```

Expected tail of output (run_ts will differ):

```
Log guardado en  : logs/optimize_TSLA_<run_ts>.log
CSV guardado en  : logs/optimize_TSLA_<run_ts>.csv
JSON de equity   : viewer/public/data/TSLA/optimize_TSLA_<run_ts>_equity.json
JSON de equity con trailing 0.5%: viewer/public/data/TSLA/optimize_TSLA_<run_ts>_trail_0.5_equity.json
JSON de equity con trailing 1.0%: viewer/public/data/TSLA/optimize_TSLA_<run_ts>_trail_1.0_equity.json
Manifest visor   : viewer/public/data/manifest.json

  COMPARACIÓN TRAILING STOP (mejor combo: drop 9% / rise 6% / interval 1 min)
--------------------------------------------------------------------------------
  ESTRATEGIA              ROI  Compras  Ventas   Trailing capture
--------------------------------------------------------------------------------
  vanilla              -0.12%        3       1                  —
  trail 0.5%           -0.17%        3       1               $-52
  trail 1.0%           -0.23%        3       1              $-108
--------------------------------------------------------------------------------
```

Then confirm the manifest groups correctly and the trade captures carry `time`:

```bash
cat viewer/public/data/manifest.json
python3 -c "
import json, glob
base = [f for f in glob.glob('viewer/public/data/TSLA/*_equity.json') if '_trail_' not in f][0]
print(json.load(open(base))['best_trades'][:2])
"
```

Expected: `manifest.json` has one entry with `base_file` + a `trail_files` array of 2 items (`trail_pct` 0.5 and 1.0), and each `best_trades` entry has a `time` key (e.g. `{'type': 'BUY', 'date': '2026-06-01', 'time': '08:00', ...}`).

Also confirm the empty-data guard exits non-zero (needed so the viewer's middleware surfaces the error instead of reporting success):

```bash
cp ../vanilla/logs/cache_TSLA_20260606_20260608_1Min.pkl logs/
python3 optimize.py --symbol TSLA --date-start 2026-06-06 --date-end 2026-06-08 \
  --buy-amount 10000 --fee-pct 0 --intervals 1 --export-equity-json --trail-pcts 0.5 \
  --out-dir viewer/public/data
echo "exit code: $?"
```

Expected: prints `Error: no se encontraron velas de 1 minuto...` and `exit code: 1`.

Clean up the manual-test artifacts before committing (they're gitignored but no need to leave them around):

```bash
rm -rf logs viewer
cd ../..
```

- [ ] **Step 9: Commit**

```bash
git add strategies/trailing/optimize.py
git commit -m "feat: --out-dir, manejo de errores y agrupado de salidas en strategies/trailing/optimize.py"
```

---

## Task 3: Scaffold `strategies/trailing/viewer/` (Vite project + backend middlewares)

**Files:**
- Create: `strategies/trailing/viewer/package.json`
- Create: `strategies/trailing/viewer/.oxlintrc.json`
- Create: `strategies/trailing/viewer/.gitignore`
- Create: `strategies/trailing/viewer/index.html`
- Create: `strategies/trailing/viewer/vite.config.js`
- Create: `strategies/trailing/viewer/public/data/.gitkeep`
- Create: `strategies/trailing/viewer/public/data/.gitignore`
- Create: `strategies/trailing/viewer/src/main.jsx` (placeholder `App` — real one lands in Task 7)

**Interfaces:**
- Consumes: `regenerate_manifest` (Task 1), `--out-dir`/`--trail-pcts` (Task 2), via child-process spawn.
- Produces: `POST /api/run-optimize` (body `{symbol, date_start, date_end, buy_amount, fee_pct, trail_pcts}`), `POST /api/delete-run` (body `{run_ts, symbol}`), and static serving of `public/data/manifest.json` + `public/data/<symbol>/*.json` — all consumed by `App.jsx` in Task 7.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "trailing-viewer",
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

- [ ] **Step 2: Create `.oxlintrc.json`**

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "oxc"],
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

- [ ] **Step 3: Create `.gitignore`**

```
# Logs
logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*
lerna-debug.log*

node_modules
dist
dist-ssr
*.local

# Editor directories and files
.vscode/*
!.vscode/extensions.json
.idea
.DS_Store
*.suo
*.ntvs*
*.njsproj
*.sln
*.sw?
```

- [ ] **Step 4: Create `index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Trailing Stop Viewer</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `public/data/.gitkeep` and `public/data/.gitignore`**

`public/data/.gitkeep` is an empty file. `public/data/.gitignore`:

```
*
!.gitignore
!.gitkeep
```

Run: `mkdir -p strategies/trailing/viewer/public/data && touch strategies/trailing/viewer/public/data/.gitkeep`

- [ ] **Step 6: Create `vite.config.js`**

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const TRAILING_DIR = path.resolve(__dirname, '..')
const VENV_PYTHON = path.resolve(__dirname, '../../../../.venv/bin/python')
const DATA_DIR = path.resolve(__dirname, 'public/data')

function regenerateManifest() {
  return new Promise((resolve, reject) => {
    const child = spawn(
      VENV_PYTHON,
      ['-c', "from optimize import regenerate_manifest; regenerate_manifest('viewer/public/data')"],
      { cwd: TRAILING_DIR }
    )
    let stderr = ''
    child.stderr.on('data', (chunk) => (stderr += chunk))
    child.on('error', reject)
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(stderr || `exit ${code}`))))
  })
}

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

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch (err) {
        reject(err)
      }
    })
    req.on('error', reject)
  })
}

function runOptimizeMiddleware() {
  return {
    name: 'run-optimize-middleware',
    configureServer(server) {
      server.middlewares.use('/api/run-optimize', (req, res, next) => {
        if (req.method !== 'POST') return next()

        readJsonBody(req)
          .then((body) => {
            const error = validateParams(body)
            if (error) {
              res.statusCode = 400
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error }))
              return
            }

            const symbol = body.symbol.toUpperCase()
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

            const child = spawn(VENV_PYTHON, args, { cwd: TRAILING_DIR })
            let stdout = ''
            let stderr = ''
            child.stdout.on('data', (chunk) => (stdout += chunk))
            child.stderr.on('data', (chunk) => (stderr += chunk))
            child.on('error', (err) => {
              res.statusCode = 500
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: `No se pudo iniciar optimize.py: ${err.message}` }))
            })
            child.on('close', (code) => {
              res.setHeader('Content-Type', 'application/json')
              if (code !== 0) {
                const errorLine = stdout.split('\n').reverse().find((line) => line.startsWith('Error:'))
                res.statusCode = 500
                res.end(JSON.stringify({ error: errorLine || stderr || `optimize.py salió con código ${code}`, stdout, stderr }))
                return
              }
              res.statusCode = 200
              res.end(JSON.stringify({ ok: true, stdout }))
            })
          })
          .catch((err) => {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ error: `Body inválido: ${err.message}` }))
          })
      })
    },
  }
}

function deleteRunMiddleware() {
  return {
    name: 'delete-run-middleware',
    configureServer(server) {
      server.middlewares.use('/api/delete-run', (req, res, next) => {
        if (req.method !== 'POST') return next()

        readJsonBody(req)
          .then(async (body) => {
            res.setHeader('Content-Type', 'application/json')
            const { run_ts, symbol } = body
            if (typeof run_ts !== 'string' || typeof symbol !== 'string') {
              res.statusCode = 400
              res.end(JSON.stringify({ error: 'run_ts y symbol son requeridos' }))
              return
            }

            const manifestPath = path.join(DATA_DIR, 'manifest.json')
            let manifest
            try {
              manifest = JSON.parse(await fs.readFile(manifestPath, 'utf-8'))
            } catch (err) {
              res.statusCode = 500
              res.end(JSON.stringify({ error: `No se pudo leer manifest.json: ${err.message}` }))
              return
            }

            const run = manifest.find((r) => r.run_ts === run_ts && r.symbol === symbol)
            if (!run) {
              res.statusCode = 404
              res.end(JSON.stringify({ error: 'run no encontrado en el manifest' }))
              return
            }

            const files = [run.base_file, ...run.trail_files.map((t) => t.file)]
            for (const file of files) {
              const target = path.resolve(DATA_DIR, file)
              if (target !== path.normalize(target) || !target.startsWith(DATA_DIR + path.sep)) {
                res.statusCode = 400
                res.end(JSON.stringify({ error: `file fuera de la carpeta de datos: ${file}` }))
                return
              }
            }

            try {
              await Promise.all(files.map((file) => fs.unlink(path.resolve(DATA_DIR, file))))
              await regenerateManifest()
              res.statusCode = 200
              res.end(JSON.stringify({ ok: true }))
            } catch (err) {
              res.statusCode = 500
              res.end(JSON.stringify({ error: err.message }))
            }
          })
          .catch((err) => {
            res.statusCode = 400
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ error: `Body inválido: ${err.message}` }))
          })
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), runOptimizeMiddleware(), deleteRunMiddleware()],
})
```

- [ ] **Step 7: Create a placeholder `src/main.jsx` + `src/App.jsx` so the dev server has something to render**

`src/main.jsx`:

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

`src/App.jsx` (temporary — replaced in Task 7):

```jsx
export default function App() {
  return <div>Trailing viewer scaffold OK</div>
}
```

- [ ] **Step 8: Install dependencies and verify the dev server starts**

```bash
cd strategies/trailing/viewer
npm install
npm run dev -- --port 5180 &
sleep 2
curl -s http://localhost:5180/ | grep -o '<title>[^<]*</title>'
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5180/data/manifest.json
kill %1
cd ../../..
```

Expected: `<title>Trailing Stop Viewer</title>` and a `404` for `manifest.json` (nothing generated yet — that's expected at this point).

- [ ] **Step 9: Commit**

```bash
git add strategies/trailing/viewer
git commit -m "feat: scaffold del proyecto Vite para el viewer de trailing"
```

---

## Task 4: Shared chart utilities (copied verbatim) + CSS additions

**Files:**
- Create: `strategies/trailing/viewer/src/lib/chartMath.js` (copy)
- Create: `strategies/trailing/viewer/src/hooks/useZoom.js` (copy)
- Create: `strategies/trailing/viewer/src/hooks/useTooltip.js` (copy)
- Create: `strategies/trailing/viewer/src/components/ChartFrame.jsx` (copy)
- Create: `strategies/trailing/viewer/src/components/Tooltip.jsx` (copy)
- Create: `strategies/trailing/viewer/src/index.css` (copy + 2 new CSS vars/classes)
- Modify: `strategies/trailing/viewer/src/main.jsx` (import the CSS)

**Interfaces:**
- Produces: `xForIndex`, `nearestIndex`, `svgLocalX`, `fmtMoney`, `indexed`, `paddedDomain`, `dayFraction`, `dateTicksForDomain` (from `chartMath.js`); `useZoom({svgRef, n, marginLeft, plotWidth})`; `useTooltip()`; `<ChartFrame>`; `<Tooltip>` — all consumed unchanged by Tasks 5–7, same signatures as in `strategies/vanilla/viewer/`.

These five files are byte-for-byte identical to their vanilla counterparts — no adaptation needed, so this step copies rather than retypes them.

- [ ] **Step 1: Copy the four verbatim files**

```bash
cd /home/david/Repos/David/Inversiones/invertirCarlos/alpaca
mkdir -p strategies/trailing/viewer/src/lib strategies/trailing/viewer/src/hooks
cp strategies/vanilla/viewer/src/lib/chartMath.js strategies/trailing/viewer/src/lib/chartMath.js
cp strategies/vanilla/viewer/src/hooks/useZoom.js strategies/trailing/viewer/src/hooks/useZoom.js
cp strategies/vanilla/viewer/src/hooks/useTooltip.js strategies/trailing/viewer/src/hooks/useTooltip.js
cp strategies/vanilla/viewer/src/components/ChartFrame.jsx strategies/trailing/viewer/src/components/ChartFrame.jsx
cp strategies/vanilla/viewer/src/components/Tooltip.jsx strategies/trailing/viewer/src/components/Tooltip.jsx
```

- [ ] **Step 2: Copy `index.css`, then add the trailing-vs-vanilla indexed-line styles**

```bash
cp strategies/vanilla/viewer/src/index.css strategies/trailing/viewer/src/index.css
```

Then modify `strategies/trailing/viewer/src/index.css` — old:

```css
:root {
  --rise-1: #86b6ef; --rise-2: #6da7ec; --rise-3: #5598e7; --rise-4: #3987e5; --rise-5: #2a78d6;
  --rise-6: #256abf; --rise-7: #1c5cab; --rise-8: #184f95; --rise-9: #104281; --rise-10: #0d366b;
  --cat-1: #2a78d6; --cat-2: #1baf7a; --cat-3: #eda100; --cat-4: #008300; --cat-5: #4a3aa7;

  --surface-1: #fcfcfb; --page-plane: #f9f9f7; --text-primary: #0b0b0b;
  --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9; --baseline: #c3c2b7;
  --price-line: #2a78d6; --marker-buy: #008300; --marker-sell: #e34948;
  color-scheme: light dark;
}

@media (prefers-color-scheme: dark) {
  :root {
    --rise-1: #9ec5f4; --rise-2: #86b6ef; --rise-3: #6da7ec; --rise-4: #5598e7; --rise-5: #3987e5;
    --rise-6: #2a78d6; --rise-7: #256abf; --rise-8: #1c5cab; --rise-9: #184f95; --rise-10: #104281;
    --cat-1: #3987e5; --cat-2: #199e70; --cat-3: #c98500; --cat-4: #008300; --cat-5: #9085e9;

    --surface-1: #1a1a19; --page-plane: #0d0d0d; --text-primary: #ffffff;
    --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a; --baseline: #383835;
    --price-line: #3987e5; --marker-buy: #008300; --marker-sell: #e66767;
  }
}
```

new:

```css
:root {
  --rise-1: #86b6ef; --rise-2: #6da7ec; --rise-3: #5598e7; --rise-4: #3987e5; --rise-5: #2a78d6;
  --rise-6: #256abf; --rise-7: #1c5cab; --rise-8: #184f95; --rise-9: #104281; --rise-10: #0d366b;
  --cat-1: #2a78d6; --cat-2: #1baf7a; --cat-3: #eda100; --cat-4: #008300; --cat-5: #4a3aa7;

  --surface-1: #fcfcfb; --page-plane: #f9f9f7; --text-primary: #0b0b0b;
  --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9; --baseline: #c3c2b7;
  --price-line: #2a78d6; --equity-line: #1baf7a; --marker-buy: #008300; --marker-sell: #e34948;
  color-scheme: light dark;
}

@media (prefers-color-scheme: dark) {
  :root {
    --rise-1: #9ec5f4; --rise-2: #86b6ef; --rise-3: #6da7ec; --rise-4: #5598e7; --rise-5: #3987e5;
    --rise-6: #2a78d6; --rise-7: #256abf; --rise-8: #1c5cab; --rise-9: #184f95; --rise-10: #104281;
    --cat-1: #3987e5; --cat-2: #199e70; --cat-3: #c98500; --cat-4: #008300; --cat-5: #9085e9;

    --surface-1: #1a1a19; --page-plane: #0d0d0d; --text-primary: #ffffff;
    --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a; --baseline: #383835;
    --price-line: #3987e5; --equity-line: #199e70; --marker-buy: #008300; --marker-sell: #e66767;
  }
}
```

Then add the indexed-line classes right after the existing `.t5-line*` rules — old:

```css
.price-line { fill: none; stroke: var(--price-line); stroke-width: 2; }
.t5-line { fill: none; stroke-width: 2; transition: opacity 0.15s, stroke-width 0.15s; }
.t5-line.ref-line { stroke: var(--muted); stroke-width: 2; stroke-dasharray: 5 4; }
.t5-line.dimmed { opacity: 0.15; }
.t5-line.emphasized { stroke-width: 3; }
```

new:

```css
.price-line { fill: none; stroke: var(--price-line); stroke-width: 2; }
.t5-line { fill: none; stroke-width: 2; transition: opacity 0.15s, stroke-width 0.15s; }
.t5-line.ref-line { stroke: var(--muted); stroke-width: 2; stroke-dasharray: 5 4; }
.t5-line.dimmed { opacity: 0.15; }
.t5-line.emphasized { stroke-width: 3; }
.idx-line { fill: none; stroke-width: 2; }
.idx-line.ref-line { stroke: var(--muted); stroke-width: 2; stroke-dasharray: 5 4; }
.idx-line.vanilla { stroke: var(--price-line); }
.idx-line.trailing { stroke: var(--equity-line); }
```

- [ ] **Step 3: Import the CSS from `main.jsx`**

old:

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
```

new:

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
```

- [ ] **Step 4: Verify the dev server still starts and now has styling**

```bash
cd strategies/trailing/viewer
npm run dev -- --port 5180 &
sleep 2
curl -s http://localhost:5180/src/index.css | grep -c "idx-line"
kill %1
cd ../../..
```

Expected: a number greater than `0` (the new rules are being served).

- [ ] **Step 5: Commit**

```bash
git add strategies/trailing/viewer
git commit -m "feat: utilidades de charts compartidas + estilos para el comparador vanilla/trailing"
```

---

## Task 5: `RunForm.jsx` + `ComparisonTable.jsx`

**Files:**
- Create: `strategies/trailing/viewer/src/components/RunForm.jsx`
- Create: `strategies/trailing/viewer/src/components/ComparisonTable.jsx`

**Interfaces:**
- Consumes: `fmtMoney` from `chartMath.js` (Task 4).
- Produces: `<RunForm onRunComplete={() => Promise}>` — posts to `/api/run-optimize` (Task 3) with `{symbol, date_start, date_end, buy_amount, fee_pct, trail_pcts}`, no interval/max_buys fields. `<ComparisonTable rows={[{key, label, roi, profit, buys, sells, trailingCapture}]}>` — pure/presentational, consumed by `App.jsx` in Task 7 with `trailingCapture: null` for the vanilla row.

- [ ] **Step 1: Create `RunForm.jsx`**

```jsx
import { useState } from "react";

const DEFAULTS = {
  symbol: "TSLA",
  date_start: "2026-01-01",
  date_end: "2026-06-28",
  buy_amount: 10000,
  fee_pct: 0,
  trail_pcts: "0.5,1,1.5,2",
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
    if (!TRAIL_PCTS_RE.test(form.trail_pcts.trim())) {
      setError("Trailing % inválido — usá números positivos separados por coma (ej. 0.5,1,1.5,2)");
      return;
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
          <input
            type="text"
            value={form.symbol}
            onChange={(e) => update("symbol", e.target.value.toUpperCase())}
            disabled={running}
            required
          />
        </label>
        <label>
          Desde
          <input
            type="date"
            value={form.date_start}
            onChange={(e) => update("date_start", e.target.value)}
            disabled={running}
            required
          />
        </label>
        <label>
          Hasta
          <input
            type="date"
            value={form.date_end}
            onChange={(e) => update("date_end", e.target.value)}
            disabled={running}
            required
          />
        </label>
        <label>
          Monto por compra ($)
          <input
            type="number"
            min="1"
            step="1"
            value={form.buy_amount}
            onChange={(e) => update("buy_amount", e.target.value)}
            disabled={running}
            required
          />
        </label>
        <label>
          Fee (%)
          <input
            type="number"
            min="0"
            step="0.01"
            value={form.fee_pct * 100}
            onChange={(e) => update("fee_pct", Number(e.target.value) / 100)}
            disabled={running}
            required
          />
        </label>
        <label>
          Trailing % a comparar
          <input
            type="text"
            value={form.trail_pcts}
            onChange={(e) => update("trail_pcts", e.target.value)}
            disabled={running}
            placeholder="0.5,1,1.5,2"
            required
          />
        </label>
      </div>
      <button type="submit" disabled={running}>
        {running ? "Ejecutando…" : "Generar"}
      </button>
      {running && (
        <span className="run-status">
          Corriendo optimize.py con intervalo fijo de 1 minuto, puede tardar un rato…
        </span>
      )}
      {error && <div className="run-error">{error}</div>}
    </form>
  );
}
```

Note there is no interval field and no max_buys field — per this project's design, trailing always runs at 1-minute resolution (sent by the middleware, not the form) and `max_buys` is fixed at 10 server-side.

- [ ] **Step 2: Create `ComparisonTable.jsx`**

```jsx
import { fmtMoney } from "../lib/chartMath";

export default function ComparisonTable({ rows }) {
  const sorted = [...rows].sort((a, b) => b.roi - a.roi);
  return (
    <div className="panel">
      <h2>Comparación vanilla vs. trailing stop</h2>
      <table>
        <thead>
          <tr>
            <th>Estrategia</th>
            <th>ROI</th>
            <th>Ganancia</th>
            <th>Compras</th>
            <th>Ventas</th>
            <th>Trailing capture</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.key}>
              <th>{r.label}</th>
              <td>
                {r.roi >= 0 ? "+" : ""}
                {r.roi.toFixed(2)}%
              </td>
              <td>
                {r.profit >= 0 ? "+" : "-"}
                {fmtMoney(Math.abs(r.profit))}
              </td>
              <td>{r.buys}</td>
              <td>{r.sells}</td>
              <td>
                {r.trailingCapture == null
                  ? "—"
                  : `${r.trailingCapture >= 0 ? "+" : "-"}${fmtMoney(Math.abs(r.trailingCapture))}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Sanity-check both files for syntax/import errors**

`App.jsx` still exports the Task 3 placeholder and doesn't import these two yet, so a `vite build` from the entry point wouldn't actually traverse them — use oxlint directly on the two files instead, since it parses whatever paths it's given regardless of the import graph:

```bash
cd strategies/trailing/viewer
npx oxlint src/components/RunForm.jsx src/components/ComparisonTable.jsx
cd ../../..
```

Expected: no parse errors (oxlint may print style warnings — only syntax/unresolved-import errors matter here).

- [ ] **Step 4: Commit**

```bash
git add strategies/trailing/viewer/src/components/RunForm.jsx strategies/trailing/viewer/src/components/ComparisonTable.jsx
git commit -m "feat: RunForm y ComparisonTable del viewer de trailing"
```

---

## Task 6: `TrailingTradesChart.jsx` + `IndexedEquityChart.jsx`

**Files:**
- Create: `strategies/trailing/viewer/src/components/TrailingTradesChart.jsx`
- Create: `strategies/trailing/viewer/src/components/IndexedEquityChart.jsx`

**Interfaces:**
- Consumes: `useZoom`, `xForIndex`/`nearestIndex`/`dateTicksForDomain`/`paddedDomain`/`dayFraction`/`indexed` from `chartMath.js`, `<ChartFrame>` (all Task 4).
- Produces: `<TrailingTradesChart id title price trades showTooltip hideTooltip>` — `trades` entries may have `type` of `"BUY"`, `"BUY_INIT"`, `"BUY_GRID"`, or `"SELL"` (all three buy variants render as a buy marker); a `SELL` entry's tooltip additionally shows `trailing_capture` when present. `<IndexedEquityChart id price vanillaEquity trailingEquity trailingLabel showTooltip hideTooltip>` — `price` is `[{date, close}]`, `vanillaEquity`/`trailingEquity` are `[{date, equity}]` (need not have exactly the same dates as `price`; values are forward-filled by date). Both consumed by `App.jsx` in Task 7.

- [ ] **Step 1: Create `TrailingTradesChart.jsx`**

```jsx
import { useMemo, useRef, useState } from "react";
import { useZoom } from "../hooks/useZoom";
import { xForIndex, nearestIndex, dateTicksForDomain, paddedDomain, dayFraction } from "../lib/chartMath";
import ChartFrame from "./ChartFrame";

const WIDTH = 900;
const HEIGHT = 220;
const MARGIN = { top: 10, right: 16, bottom: 24, left: 60 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;

const DAY_SLOT_WIDTH = 0.9;

function isBuyType(type) {
  return type === "BUY" || type === "BUY_INIT" || type === "BUY_GRID";
}

export default function TrailingTradesChart({ id, title, price, trades, showTooltip, hideTooltip }) {
  const svgRef = useRef(null);
  const n = price.length;
  const dates = useMemo(() => price.map((p) => p.date), [price]);
  const values = useMemo(() => price.map((p) => p.close), [price]);
  const [yMin, yMax] = useMemo(() => paddedDomain(values), [values]);
  const [linkedOrderId, setLinkedOrderId] = useState(null);

  const { domain, dragRect, handlers } = useZoom({ svgRef, n, marginLeft: MARGIN.left, plotWidth: PLOT_W });

  const x = (i) => xForIndex(i, { marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
  const y = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * (HEIGHT - MARGIN.top - MARGIN.bottom);

  const points = useMemo(
    () => values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [values, domain]
  );

  const dateToIdx = useMemo(() => new Map(dates.map((d, i) => [d, i])), [dates]);

  const markers = useMemo(
    () =>
      trades
        .map((t) => ({ ...t, idx: dateToIdx.get(t.date) }))
        .filter((t) => t.idx !== undefined)
        .map((t) => ({ ...t, xIdx: t.idx + dayFraction(t.time) * DAY_SLOT_WIDTH })),
    [trades, dateToIdx]
  );

  const [crosshairX, setCrosshairX] = useState(null);

  function onHitPointerMove(e) {
    const i = nearestIndex(svgRef.current, e.clientX, { n, marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
    setCrosshairX(x(i));
    showTooltip(
      e.clientX,
      e.clientY,
      <>
        <div className="date">{dates[i]}</div>
        <div className="tooltip-row">
          <span className="key">Precio</span>
          <span className="val">${values[i].toFixed(2)}</span>
        </div>
      </>
    );
  }

  function onHitPointerLeave() {
    setCrosshairX(null);
    hideTooltip();
  }

  function markerCenter(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function onMarkerEnter(e, marker) {
    e.stopPropagation();
    setLinkedOrderId(marker.order_id ?? null);

    const allEls = Array.from(svgRef.current.querySelectorAll(".trade-marker"));
    const center = markerCenter(e.currentTarget);
    const nearby = allEls
      .map((el, k) => ({ el, m: markers[k], c: markerCenter(el) }))
      .filter(({ c }) => Math.hypot(c.x - center.x, c.y - center.y) <= 12)
      .map(({ m }) => m)
      .sort((a, b) => a.date.localeCompare(b.date));

    const group = nearby.length ? nearby : [marker];
    showTooltip(
      e.clientX,
      e.clientY,
      <>
        {group.length > 1 && <div className="date">{group.length} operaciones</div>}
        {group.map((m, k) => (
          <TradeRow key={k} trade={m} />
        ))}
      </>
    );
  }

  function onMarkerLeave() {
    setLinkedOrderId(null);
    hideTooltip();
  }

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const dateTickIdx = dateTicksForDomain(domain, n);

  const gridContent = (
    <>
      {yTicks.map((v, i) => (
        <g key={i}>
          <line className="grid" x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(v)} y2={y(v)} />
          <text className="tick" x={MARGIN.left - 6} y={y(v) + 3} textAnchor="end">
            ${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </text>
        </g>
      ))}
    </>
  );

  const xLabels = (
    <>
      {dateTickIdx.map((i) => (
        <text key={i} className="tick x-tick" x={x(i)} y={HEIGHT - 6} textAnchor="middle">
          {dates[i]}
        </text>
      ))}
    </>
  );

  return (
    <div className="panel">
      <h2>{title}</h2>
      <div className="legend">
        <span className="legend-item">
          <span className="marker-swatch buy"></span>Compra
        </span>
        <span className="legend-item">
          <span className="marker-swatch sell"></span>Venta
        </span>
      </div>
      <ChartFrame
        id={id}
        svgRef={svgRef}
        width={WIDTH}
        height={HEIGHT}
        margin={MARGIN}
        dragRect={dragRect}
        zoomHandlers={handlers}
        onHitPointerMove={onHitPointerMove}
        onHitPointerLeave={onHitPointerLeave}
        crosshairX={crosshairX}
        gridContent={gridContent}
        xLabels={xLabels}
      >
        <polyline className="price-line" points={points} />
        {markers.map((m, k) => {
          if (m.idx < domain[0] || m.idx > domain[1]) return null;
          const cx = x(m.xIdx);
          const cy = y(m.price);
          const isLinked = m.order_id != null && m.order_id === linkedOrderId;
          const isBuy = isBuyType(m.type);
          const pts = isBuy
            ? `${cx},${cy - 5} ${cx - 5},${cy + 4} ${cx + 5},${cy + 4}`
            : `${cx},${cy + 5} ${cx - 5},${cy - 4} ${cx + 5},${cy - 4}`;
          const colorVar = isBuy ? "var(--marker-buy)" : "var(--marker-sell)";
          return (
            <g
              key={k}
              className={"trade-marker" + (isLinked ? " linked" : "")}
              onPointerEnter={(e) => onMarkerEnter(e, m)}
              onPointerMove={(e) => onMarkerEnter(e, m)}
              onPointerLeave={onMarkerLeave}
            >
              <circle cx={cx} cy={cy} r={10} fill="transparent" />
              <polygon points={pts} style={{ fill: colorVar }} stroke="var(--surface-1)" strokeWidth={2} />
            </g>
          );
        })}
      </ChartFrame>
    </div>
  );
}

function TradeRow({ trade }) {
  const isBuy = isBuyType(trade.type);
  const type = isBuy ? "Compra" : "Venta";
  const colorVar = isBuy ? "var(--marker-buy)" : "var(--marker-sell)";
  const orderTag = trade.order_id ? ` · orden #${trade.order_id}` : "";
  const whenStr = trade.time ? `${trade.date} ${trade.time}` : trade.date;

  if (!isBuy) {
    const profit = trade.profit;
    const sign = profit >= 0 ? "+" : "-";
    const profitColor = profit >= 0 ? "var(--marker-buy)" : "var(--marker-sell)";
    const priceDiff = trade.price - trade.buy_price;
    const diffSign = priceDiff >= 0 ? "+" : "-";
    const diffColor = priceDiff >= 0 ? "var(--marker-buy)" : "var(--marker-sell)";
    const diffPct = trade.buy_price ? (priceDiff / trade.buy_price) * 100 : 0;
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
        <div className="tooltip-row">
          <span className="key">
            ↳ abierta {trade.buy_date || "?"}
            {trade.buy_time ? ` ${trade.buy_time}` : ""}
          </span>
          <span className="val">${trade.buy_price.toFixed(2)}</span>
        </div>
        <div className="tooltip-row">
          <span className="key">↳ venta − compra</span>
          <span className="val" style={{ color: diffColor }}>
            {diffSign}${Math.abs(priceDiff).toFixed(2)}{" "}
            <span className="roi">
              ({diffSign}
              {Math.abs(diffPct).toFixed(2)}%)
            </span>
          </span>
        </div>
        <div className="tooltip-row">
          <span className="key">↳ ganancia acumulada</span>
          <span className="val" style={{ color: profitColor }}>
            {sign}${Math.abs(profit).toFixed(2)}
          </span>
        </div>
        {trade.trailing_capture != null && (
          <div className="tooltip-row">
            <span className="key">↳ trailing capture</span>
            <span
              className="val"
              style={{ color: trade.trailing_capture >= 0 ? "var(--marker-buy)" : "var(--marker-sell)" }}
            >
              {trade.trailing_capture >= 0 ? "+" : "-"}${Math.abs(trade.trailing_capture).toFixed(2)}
            </span>
          </div>
        )}
      </>
    );
  }

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
}
```

- [ ] **Step 2: Create `IndexedEquityChart.jsx`**

```jsx
import { useMemo, useRef, useState } from "react";
import { useZoom } from "../hooks/useZoom";
import { xForIndex, nearestIndex, dateTicksForDomain, paddedDomain, indexed } from "../lib/chartMath";
import ChartFrame from "./ChartFrame";

const WIDTH = 900;
const HEIGHT = 260;
const MARGIN = { top: 10, right: 16, bottom: 24, left: 50 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;

// Forward-fills equity points onto `dates` by date, so vanillaEquity/
// trailingEquity don't need to share exact dates/length with price — a
// missing date just carries the last known value forward.
function alignByDate(dates, points) {
  const byDate = new Map(points.map((p) => [p.date, p.equity]));
  let last = points.length ? points[0].equity : 0;
  return dates.map((d) => {
    if (byDate.has(d)) last = byDate.get(d);
    return last;
  });
}

export default function IndexedEquityChart({
  id,
  price,
  vanillaEquity,
  trailingEquity,
  trailingLabel,
  showTooltip,
  hideTooltip,
}) {
  const svgRef = useRef(null);
  const n = price.length;
  const dates = useMemo(() => price.map((p) => p.date), [price]);
  const priceVals = useMemo(() => price.map((p) => p.close), [price]);
  const priceIndexed = useMemo(() => indexed(priceVals), [priceVals]);

  const vanillaVals = useMemo(() => alignByDate(dates, vanillaEquity), [dates, vanillaEquity]);
  const trailingVals = useMemo(() => alignByDate(dates, trailingEquity), [dates, trailingEquity]);
  const vanillaIndexed = useMemo(() => indexed(vanillaVals), [vanillaVals]);
  const trailingIndexed = useMemo(() => indexed(trailingVals), [trailingVals]);

  const [yMin, yMax] = useMemo(
    () => paddedDomain([...priceIndexed, ...vanillaIndexed, ...trailingIndexed]),
    [priceIndexed, vanillaIndexed, trailingIndexed]
  );

  const { domain, dragRect, handlers } = useZoom({ svgRef, n, marginLeft: MARGIN.left, plotWidth: PLOT_W });

  const x = (i) => xForIndex(i, { marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
  const y = (v) => MARGIN.top + (1 - (v - yMin) / (yMax - yMin)) * (HEIGHT - MARGIN.top - MARGIN.bottom);

  const pricePath = useMemo(
    () => priceIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [priceIndexed, domain]
  );
  const vanillaPath = useMemo(
    () => vanillaIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [vanillaIndexed, domain]
  );
  const trailingPath = useMemo(
    () => trailingIndexed.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "),
    [trailingIndexed, domain]
  );

  const [crosshairX, setCrosshairX] = useState(null);

  function onHitPointerMove(e) {
    const i = nearestIndex(svgRef.current, e.clientX, { n, marginLeft: MARGIN.left, plotWidth: PLOT_W, domain });
    setCrosshairX(x(i));
    showTooltip(
      e.clientX,
      e.clientY,
      <>
        <div className="date">{dates[i]}</div>
        <div className="tooltip-row">
          <span className="key">
            <span
              className="key-line"
              style={{ background: "var(--muted)", borderTop: "2px dashed var(--muted)" }}
            ></span>
            Precio
          </span>
          <span className="val">${priceVals[i].toFixed(2)}</span>
        </div>
        <div className="tooltip-row">
          <span className="key">
            <span className="key-line" style={{ background: "var(--price-line)" }}></span>
            Vanilla
          </span>
          <span className="val">{vanillaIndexed[i].toFixed(1)}</span>
        </div>
        <div className="tooltip-row">
          <span className="key">
            <span className="key-line" style={{ background: "var(--equity-line)" }}></span>
            {trailingLabel}
          </span>
          <span className="val">{trailingIndexed[i].toFixed(1)}</span>
        </div>
      </>
    );
  }

  function onHitPointerLeave() {
    setCrosshairX(null);
    hideTooltip();
  }

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const dateTickIdx = dateTicksForDomain(domain, n);

  const gridContent = (
    <>
      {yTicks.map((v, i) => (
        <g key={i}>
          <line className="grid" x1={MARGIN.left} x2={WIDTH - MARGIN.right} y1={y(v)} y2={y(v)} />
          <text className="tick" x={MARGIN.left - 6} y={y(v) + 3} textAnchor="end">
            {v.toFixed(0)}
          </text>
        </g>
      ))}
    </>
  );

  const xLabels = (
    <>
      {dateTickIdx.map((i) => (
        <text key={i} className="tick x-tick" x={x(i)} y={HEIGHT - 6} textAnchor="middle">
          {dates[i]}
        </text>
      ))}
    </>
  );

  return (
    <div className="panel">
      <h2>
        Equity vanilla vs. {trailingLabel} (indexado a 100 al inicio)
      </h2>
      <div className="legend">
        <span className="legend-item">
          <span className="swatch swatch-dashed"></span>Precio (referencia)
        </span>
        <span className="legend-item">
          <span className="swatch" style={{ background: "var(--price-line)" }}></span>Vanilla
        </span>
        <span className="legend-item">
          <span className="swatch" style={{ background: "var(--equity-line)" }}></span>
          {trailingLabel}
        </span>
      </div>
      <ChartFrame
        id={id}
        svgRef={svgRef}
        width={WIDTH}
        height={HEIGHT}
        margin={MARGIN}
        dragRect={dragRect}
        zoomHandlers={handlers}
        onHitPointerMove={onHitPointerMove}
        onHitPointerLeave={onHitPointerLeave}
        crosshairX={crosshairX}
        gridContent={gridContent}
        xLabels={xLabels}
      >
        <polyline className="idx-line ref-line" points={pricePath} />
        <polyline className="idx-line vanilla" points={vanillaPath} />
        <polyline className="idx-line trailing" points={trailingPath} />
      </ChartFrame>
    </div>
  );
}
```

- [ ] **Step 3: Sanity-check both files for syntax/import errors**

Same reasoning as Task 5 Step 3 — use oxlint directly rather than `vite build`, since `App.jsx` doesn't import either file until Task 7:

```bash
cd strategies/trailing/viewer
npx oxlint src/components/TrailingTradesChart.jsx src/components/IndexedEquityChart.jsx
cd ../../..
```

Expected: no parse errors.

- [ ] **Step 4: Commit**

```bash
git add strategies/trailing/viewer/src/components/TrailingTradesChart.jsx strategies/trailing/viewer/src/components/IndexedEquityChart.jsx
git commit -m "feat: TrailingTradesChart e IndexedEquityChart del viewer de trailing"
```

---

## Task 7: Wire up `App.jsx` and verify end-to-end in the browser

**Files:**
- Modify: `strategies/trailing/viewer/src/App.jsx` (replace the Task 3 placeholder)

**Interfaces:**
- Consumes: `ComparisonTable` (Task 5), `TrailingTradesChart`/`IndexedEquityChart` (Task 6), `Tooltip`/`useTooltip` (Task 4), `RunForm` (Task 5), `/data/manifest.json` + `/data/<file>` + `/api/delete-run` (Task 3).

- [ ] **Step 1: Replace `src/App.jsx`**

```jsx
import { useCallback, useEffect, useMemo, useState } from "react";
import ComparisonTable from "./components/ComparisonTable";
import TrailingTradesChart from "./components/TrailingTradesChart";
import IndexedEquityChart from "./components/IndexedEquityChart";
import RunForm from "./components/RunForm";
import Tooltip from "./components/Tooltip";
import { useTooltip } from "./hooks/useTooltip";

function runLabel(entry) {
  return `${entry.symbol} · ${entry.date_start} → ${entry.date_end} · corrida ${entry.run_ts}`;
}

function countTrades(trades, type) {
  return trades.filter((t) => t.type === type).length;
}

export default function App() {
  const [manifest, setManifest] = useState(null);
  const [manifestError, setManifestError] = useState(null);
  const [selectedRunKey, setSelectedRunKey] = useState(null);
  const [baseData, setBaseData] = useState(null);
  const [trailDatas, setTrailDatas] = useState(null); // [{trail_pct, data}]
  const [dataError, setDataError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [selectedSeriesKey, setSelectedSeriesKey] = useState(null); // "vanilla" | "trail-<pct>"
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
          setBaseData(null);
          setTrailDatas(null);
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

  useEffect(() => {
    if (!selectedRun) return;
    setBaseData(null);
    setTrailDatas(null);
    setDataError(null);
    setSelectedSeriesKey(null);

    Promise.all([
      fetch(`/data/${selectedRun.base_file}`, { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status} en ${selectedRun.base_file}`);
        return r.json();
      }),
      Promise.all(
        selectedRun.trail_files.map((t) =>
          fetch(`/data/${t.file}`, { cache: "no-store" })
            .then((r) => {
              if (!r.ok) throw new Error(`HTTP ${r.status} en ${t.file}`);
              return r.json();
            })
            .then((data) => ({ trail_pct: t.trail_pct, data }))
        )
      ),
    ])
      .then(([base, trails]) => {
        setBaseData(base);
        setTrailDatas(trails);
      })
      .catch((err) => setDataError(err.message));
  }, [selectedRun]);

  const vanillaSeriesForEquity = useMemo(() => {
    if (!baseData) return null;
    return baseData.series.find(
      (s) => s.drop_pct === baseData.best_combo.drop_pct && s.rise_pct === baseData.best_combo.rise_pct
    );
  }, [baseData]);

  const rows = useMemo(() => {
    if (!baseData || !trailDatas || !vanillaSeriesForEquity) return [];
    const finalEquity = vanillaSeriesForEquity.points[vanillaSeriesForEquity.points.length - 1].equity;
    const profit = finalEquity - baseData.starting_cash;
    const roi = (profit / baseData.starting_cash) * 100;
    const vanillaRow = {
      key: "vanilla",
      label: `Vanilla (drop ${baseData.best_combo.drop_pct}% / rise ${baseData.best_combo.rise_pct}%)`,
      roi,
      profit,
      buys: countTrades(baseData.best_trades, "BUY"),
      sells: countTrades(baseData.best_trades, "SELL"),
      trailingCapture: null,
    };
    const trailRows = trailDatas.map(({ trail_pct, data }) => ({
      key: `trail-${trail_pct}`,
      label: `Trailing ${trail_pct.toFixed(1)}%`,
      roi: data.roi,
      profit: data.profit,
      buys: data.buys,
      sells: data.sells,
      trailingCapture: data.trailing_capture_total,
    }));
    return [vanillaRow, ...trailRows];
  }, [baseData, trailDatas, vanillaSeriesForEquity]);

  const bestTrailKey = useMemo(() => {
    const trailRows = rows.filter((r) => r.key !== "vanilla");
    if (trailRows.length === 0) return null;
    return trailRows.reduce((best, r) => (r.roi > best.roi ? r : best), trailRows[0]).key;
  }, [rows]);

  const activeSeriesKey = selectedSeriesKey ?? bestTrailKey ?? "vanilla";
  const activeTrail = useMemo(
    () => trailDatas?.find((t) => `trail-${t.trail_pct}` === activeSeriesKey) ?? null,
    [trailDatas, activeSeriesKey]
  );

  return (
    <div className="app">
      <div className="app-header">
        <h1>Trailing Stop Viewer</h1>
        {manifest && manifest.length > 0 && (
          <div className="run-picker">
            <select value={selectedRunKey ?? ""} onChange={(e) => setSelectedRunKey(e.target.value)}>
              {manifest.map((entry) => (
                <option key={`${entry.symbol}|${entry.run_ts}`} value={`${entry.symbol}|${entry.run_ts}`}>
                  {runLabel(entry)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="delete-run-btn"
              onClick={handleDelete}
              disabled={deleting || !selectedRun}
              title="Borrar esta corrida"
            >
              {deleting ? "Borrando…" : "Borrar"}
            </button>
          </div>
        )}
      </div>

      {deleteError && <div className="panel error">No se pudo borrar la corrida: {deleteError}</div>}

      <RunForm onRunComplete={() => fetchManifest(true)} />

      {manifestError && (
        <div className="panel error">
          No se pudo cargar data/manifest.json ({manifestError}). Corré optimize.py con --export-equity-json
          --trail-pcts apuntando a esta carpeta.
        </div>
      )}
      {manifest && manifest.length === 0 && (
        <div className="panel error">No hay corridas en data/. Generá una desde el form de arriba.</div>
      )}
      {dataError && <div className="panel error">No se pudo cargar la corrida: {dataError}</div>}

      {baseData && trailDatas && rows.length > 0 && (
        <>
          <div className="subtitle">
            {baseData.date_start} → {baseData.date_end} · intervalo 1 min (fijo) · equity diaria al cierre de
            mercado
          </div>

          <ComparisonTable rows={rows} />

          <div className="legend">
            {rows.map((r) => (
              <span
                key={r.key}
                className={"legend-item legend-clickable" + (activeSeriesKey === r.key ? " active" : "")}
                tabIndex={0}
                onClick={() => setSelectedSeriesKey(r.key)}
                onKeyDown={(e) =>
                  (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setSelectedSeriesKey(r.key))
                }
              >
                {r.label}
              </span>
            ))}
          </div>

          {activeSeriesKey === "vanilla" ? (
            <TrailingTradesChart
              id="trades"
              title={`Precio ${baseData.symbol} + operaciones — Vanilla`}
              price={baseData.price}
              trades={baseData.best_trades}
              showTooltip={show}
              hideTooltip={hide}
            />
          ) : (
            activeTrail && (
              <TrailingTradesChart
                id="trades"
                title={`Precio ${baseData.symbol} + operaciones — Trailing ${activeTrail.trail_pct.toFixed(1)}%`}
                price={activeTrail.data.price}
                trades={activeTrail.data.trades}
                showTooltip={show}
                hideTooltip={hide}
              />
            )
          )}

          {activeSeriesKey !== "vanilla" && activeTrail ? (
            <IndexedEquityChart
              id="equity-compare"
              price={baseData.price}
              vanillaEquity={vanillaSeriesForEquity.points}
              trailingEquity={activeTrail.data.equity}
              trailingLabel={`Trailing ${activeTrail.trail_pct.toFixed(1)}%`}
              showTooltip={show}
              hideTooltip={hide}
            />
          ) : (
            <div className="panel">
              <p className="subtitle" style={{ margin: 0 }}>
                Elegí un % de trailing arriba para comparar su curva de equity contra vanilla.
              </p>
            </div>
          )}
        </>
      )}

      <Tooltip tooltip={tooltip} />
    </div>
  );
}
```

- [ ] **Step 2: Seed fixture data and start the dev server**

The fixtures below are the exact files produced in Task 2 Step 8 against the small cached TSLA range — reuse that same manual run instead of re-downloading:

```bash
cd strategies/trailing
mkdir -p logs
cp ../vanilla/logs/cache_TSLA_20260601_20260610_1Min.pkl logs/
python3 optimize.py --symbol TSLA --date-start 2026-06-01 --date-end 2026-06-10 \
  --buy-amount 10000 --fee-pct 0 --intervals 1 --export-equity-json \
  --trail-pcts 0.5,1 --out-dir viewer/public/data
cd viewer
npm run dev -- --port 5180 &
sleep 2
```

- [ ] **Step 3: Drive it in the browser and confirm the golden path**

Load `http://localhost:5180/` and verify, in order:

1. The run picker shows one entry (`TSLA · 2026-06-01 → 2026-06-09 · corrida <run_ts>`).
2. The comparison table shows 3 rows (`Vanilla`, `Trailing 0.5%`, `Trailing 1.0%`) sorted by ROI descending.
3. The legend below the table has 3 clickable items; the trades chart title reflects whichever is active by default (the best-ROI trailing option, since at least one trail row exists).
4. Clicking the `Vanilla` legend item switches the trades chart to `— Vanilla` and replaces the equity-comparison chart with the "Elegí un % de trailing…" note.
5. Clicking a `Trailing X%` legend item brings back the `IndexedEquityChart` with two lines (vanilla vs that %) plus the dashed price reference.
6. Hovering the trades chart shows the crosshair + price tooltip; hovering a marker shows the trade tooltip (a `SELL` marker shows the `trailing_capture` row when the active series is a trailing one).
7. Submitting the "Nueva corrida" form with default values (`trail_pcts` pre-filled `0.5,1,1.5,2`) succeeds and the new run appears at the top of the dropdown (this will hit the network / real Alpaca credentials in `.env` since there's no cache for the form's default date range — confirm it at least reaches the backend and either succeeds or surfaces a clear `Error: ...` in the `run-error` div, not a silent failure).
8. Deleting the freshly-created run removes it from the dropdown and its files from `viewer/public/data/`.

- [ ] **Step 4: Stop the dev server and clean up manual-test artifacts**

```bash
kill %1
cd ../..
rm -rf strategies/trailing/logs strategies/trailing/viewer/public/data/TSLA
cd ../..
```

(`viewer/public/data/manifest.json` and the `.gitkeep`/`.gitignore` under `public/data/` are the only things that should remain tracked — the per-symbol run folders are gitignored, so deleting them just tidies the working tree, it does not touch git state.)

- [ ] **Step 5: Commit**

```bash
git add strategies/trailing/viewer/src/App.jsx
git commit -m "feat: App.jsx del viewer de trailing — tabla comparativa + charts"
```

---

## Task 8: Remove the superseded scripts and update the README

**Files:**
- Delete: `plot_trailing.py` (root shim)
- Delete: `run_trail.sh` (root shim)
- Delete: `strategies/trailing/plot_trailing.py`
- Delete: `strategies/trailing/run_trail.sh`
- Modify: `README.md`

**Interfaces:** none — this task only removes now-superseded files and updates documentation; nothing downstream depends on it.

- [ ] **Step 1: Confirm nothing else references the files being removed**

```bash
grep -rn "plot_trailing\|run_trail" --include="*.py" --include="*.sh" --include="*.md" . | grep -v "^./README.md"
```

Expected: no output (only `README.md` mentions them, handled in Step 3).

- [ ] **Step 2: Delete the four files**

```bash
git rm plot_trailing.py run_trail.sh strategies/trailing/plot_trailing.py strategies/trailing/run_trail.sh
```

- [ ] **Step 3: Update `README.md`**

Modify the "Estructura de Archivos" section — old:

```markdown
* `strategies/trailing/`: `plot_trailing.py` y `run_trail.sh` para la estrategia trailing.
```

new:

```markdown
* `strategies/trailing/`: `tradebot.py`/`trade_trailing_bot.py`, `backtest.py` y `optimize.py` para la estrategia trailing (grid drop/rise + trailing stop sobre el mejor combo), más `viewer/` — app React (Vite) que compara vanilla vs. distintos % de trailing: lee los JSON de `viewer/public/data/<símbolo>/` generados por `optimize.py --export-equity-json --trail-pcts` (siempre a intervalo de 1 minuto) y permite lanzar nuevas corridas desde un form en la UI (`npm run dev` dentro de `viewer/`).
```

- [ ] **Step 4: Verify the removal doesn't break anything importable**

```bash
python3 -c "import strategies.trailing.optimize" && echo OK
pytest strategies/trailing/test_optimize.py test_trailing.py -q
```

Expected: `OK`, then all tests passing.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "chore: borrar plot_trailing.py/run_trail.sh, reemplazados por strategies/trailing/viewer"
```

---

## Post-plan check

After Task 8, confirm the full picture with `git log --oneline -10` and `git status`, and run the whole backend test suite once more from the repo root:

```bash
pytest -q
```

Expected: all tests pass (the pre-existing `test_trailing.py`/`test_tradebot.py` plus the new `strategies/trailing/test_optimize.py`).
