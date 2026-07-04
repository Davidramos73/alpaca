# Walk-Forward Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `walk_forward.py`: análisis de estabilidad de los parámetros óptimos semana a semana + torneo de estrategias (fija vs walk-forward) para decidir con datos si el auto-ajuste del bot se justifica.

**Architecture:** Se generaliza `simulate()` de `optimize.py` para aceptar/devolver estado de portfolio (encadenable). `walk_forward.py` importa esa función, parte el histórico de 1 minuto en semanas ISO, corre el grid por semana (estabilidad) y simula 4 estrategias con portfolio continuo (torneo). Salida: log legible + CSV semanal.

**Tech Stack:** Python 3, pandas 3.x, numpy, pytest 9. Sin red en tests (datos sintéticos); datos reales desde `cache_*.pkl` existente.

**Spec:** `docs/superpowers/specs/2026-07-04-walk-forward-design.md`

## Global Constraints

- Todos los archivos van en la raíz del repo `alpaca/` (el proyecto usa layout plano: `optimize.py`, `backtest.py`, `tradebot.py`).
- Los tests usan SOLO datos sintéticos — nunca llaman a Alpaca ni requieren `.env`.
- Grid fijo del spec: `BUY_DROP_RANGE`/`SELL_RISE_RANGE` = 1%…10% (paso 1pp), `MAX_BUYS = 10`, `STARTING_CASH = 100_000` — se importan de `optimize.py`, no se redefinen.
- Convención de submuestreo existente: el CALLER hace `df.iloc[::interval].reset_index(drop=True)` antes de llamar a `simulate()`; `interval_minutes` es solo metadata del resultado.
- Estilo de logs en español, mismo formato visual de separadores (`"=" * 80`, `"-" * 80`) que `optimize_*.log`.
- Comandos se corren desde `/home/david/Repos/David/Inversiones/invertirCarlos/alpaca`.
- Mensajes de commit terminan con `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `simulate()` con estado encadenable (+ fix de `buy_amount`)

`optimize.py::simulate()` hoy crea siempre un portfolio nuevo y (bug preexistente) ignora el parámetro `buy_amount` usando la constante `BUY_AMOUNT` en las líneas 37 y 56. Esta task agrega `state` (portfolio continuo entre llamadas) y corrige el bug. Con los defaults (`buy_amount=10000 == BUY_AMOUNT`) el comportamiento es idéntico al actual.

**Files:**
- Modify: `optimize.py` (función `simulate`, líneas 24–96)
- Create: `test_walk_forward.py`

**Interfaces:**
- Produces: `optimize.new_state(starting_cash: float = STARTING_CASH) -> dict` con claves `cash, purchases, profit_pool, total_buys, total_sells, total_fees`.
- Produces: `optimize.simulate(df, max_buys, buy_drop_pct, sell_rise_pct, fee_pct, use_pool=True, buy_amount=BUY_AMOUNT, interval_minutes=1, state=None) -> dict` — mismo dict de retorno actual MÁS la clave `"state"` (estado final, para encadenar). `state=None` ⇒ portfolio nuevo (compra inmediata en la primera vela, como hoy).

- [ ] **Step 1: Escribir los tests que fallan**

Crear `test_walk_forward.py`:

```python
import pandas as pd
import pytest

from optimize import simulate, new_state, MAX_BUYS


def make_df(prices, start="2026-01-05 14:30", freq="1min"):
    ts = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    return pd.DataFrame({"timestamp": ts, "close": [float(p) for p in prices]})


# Secuencia calculada a mano (drop=5%, rise=5%, fee=0, pool ON, buy=10000):
#   100 -> compra inicial (100 acciones, cash 90000)
#   94  -> <= 95 (target compra): compra 10000/94 acciones, cash 80000
#   99  -> >= 98.7 (target venta s/94): vende, revenue 10531.9148936..., pool 531.91...
#   106 -> >= 105 (target venta s/100): vende 100 acc., revenue 10600, pool 1131.9148936...
#   100 -> sin posiciones: compra con bonus pool/10 = 113.19..., qty 101.1319...
ZIGZAG = [100, 94, 99, 106, 100]


def test_simulate_retrocompatible_zigzag():
    df = make_df(ZIGZAG)
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert r["buys"] == 3
    assert r["sells"] == 2
    assert r["open_positions"] == 1
    assert r["total_equity"] == pytest.approx(101131.9148936, abs=1e-4)
    assert r["roi"] == pytest.approx(1.1319148936, abs=1e-6)
    assert "state" in r


def test_simulate_encadenado_equivale_a_corrida_unica():
    df = make_df(ZIGZAG)
    completo = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)

    df_a, df_b = df.iloc[:3].reset_index(drop=True), df.iloc[3:].reset_index(drop=True)
    r1 = simulate(df_a, MAX_BUYS, 0.05, 0.05, 0.0)
    r2 = simulate(df_b, MAX_BUYS, 0.05, 0.05, 0.0, state=r1["state"])

    assert r2["total_equity"] == pytest.approx(completo["total_equity"])
    assert r2["roi"] == pytest.approx(completo["roi"])
    assert r2["buys"] == completo["buys"]
    assert r2["sells"] == completo["sells"]
    assert r2["open_positions"] == completo["open_positions"]


def test_simulate_no_muta_el_state_del_caller():
    df = make_df(ZIGZAG)
    st = new_state()
    simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, state=st)
    assert st == new_state()


def test_simulate_usa_buy_amount():
    df = make_df([100.0])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, buy_amount=5_000.0)
    assert r["state"]["cash"] == pytest.approx(95_000.0)
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: FAIL con `ImportError: cannot import name 'new_state'`.

- [ ] **Step 3: Implementar en `optimize.py`**

Reemplazar la función `simulate` completa (líneas 24–96) por:

```python
def new_state(starting_cash: float = STARTING_CASH) -> dict:
    return {
        "cash":        starting_cash,
        "purchases":   [],
        "profit_pool": 0.0,
        "total_buys":  0,
        "total_sells": 0,
        "total_fees":  0.0,
    }

def simulate(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, state: dict | None = None) -> dict:
    if state is None:
        state = new_state()
    cash        = state["cash"]
    purchases   = list(state["purchases"])
    profit_pool = state["profit_pool"]
    total_buys  = state["total_buys"]
    total_sells = state["total_sells"]
    total_fees  = state["total_fees"]

    for _, row in df.iterrows():
        price = float(row["close"])

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
            purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy})
            total_buys += 1
            continue

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
                purchases.append({"price": price, "qty": qty, "buy_fee": buy_fee, "effective_buy": effective_buy})
                total_buys += 1

        elif price >= sell_target:
            sold      = purchases.pop()
            revenue   = sold["qty"] * price
            sell_fee  = revenue * fee_pct
            cash     += revenue - sell_fee
            total_fees += sell_fee
            total_sells += 1
            profit = (revenue - sell_fee) - (sold["effective_buy"] + sold["buy_fee"])
            if use_pool and profit > 0:
                profit_pool += profit

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
        "roi":            roi,
        "profit":         profit,
        "total_equity":   total_equity,
        "total_fees":     total_fees,
        "buys":           total_buys,
        "sells":          total_sells,
        "open_positions": len(purchases),
        "state": {
            "cash":        cash,
            "purchases":   purchases,
            "profit_pool": profit_pool,
            "total_buys":  total_buys,
            "total_sells": total_sells,
            "total_fees":  total_fees,
        },
    }
```

Cambios vs original: (a) parámetro `state` + bloque inicial que lo desempaqueta, (b) `buy_amount` en lugar de `BUY_AMOUNT` en los dos bloques de compra (bugfix), (c) clave `"state"` en el retorno. La lógica de trading no cambia.

- [ ] **Step 4: Verificar que pasan (y que el grid sigue andando)**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 4 PASS.

Sanity extra (no descarga nada, usa caché):
Run: `python3 optimize.py --symbol TSLA --date-start 2026-01-01 --date-end 2026-01-31 --intervals 20 2>&1 | tail -5`
Expected: termina sin error e imprime rutas de log/CSV.

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "refactor: simulate() acepta/devuelve estado de portfolio y respeta buy_amount

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `load_bars()` en optimize.py y `split_weeks()` en walk_forward.py

**Files:**
- Modify: `optimize.py` (extraer la carga de datos de `main()`, líneas 119–141)
- Create: `walk_forward.py`
- Test: `test_walk_forward.py` (agregar tests)

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `optimize.load_bars(symbol: str, date_start: datetime, date_end: datetime, api_key: str, secret_key: str) -> pd.DataFrame` (mismo caché `cache_{symbol}_{YYYYMMDD}_{YYYYMMDD}_1Min.pkl`).
- Produces: `walk_forward.split_weeks(df_1min: pd.DataFrame) -> list[dict]`, cada dict: `{"label": "2026-W02", "start": Timestamp, "end": Timestamp, "df": DataFrame}` en orden cronológico; semanas sin velas no aparecen.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `test_walk_forward.py`:

```python
from walk_forward import split_weeks


def test_split_weeks_semanas_iso_y_huecos():
    # Vie 2026-01-09 (W02), Lun/Mar 2026-01-12/13 (W03), hueco, Mié 2026-01-21 (W04)
    partes = [
        make_df([100] * 10, start="2026-01-09 15:00"),
        make_df([100] * 20, start="2026-01-12 15:00"),
        make_df([100] * 15, start="2026-01-13 15:00"),
        make_df([100] * 5,  start="2026-01-21 15:00"),
    ]
    df = pd.concat(partes, ignore_index=True)
    weeks = split_weeks(df)

    assert [w["label"] for w in weeks] == ["2026-W02", "2026-W03", "2026-W04"]
    assert [len(w["df"]) for w in weeks] == [10, 35, 5]
    assert weeks[0]["start"] == df["timestamp"].iloc[0]
    assert weeks[2]["end"] == df["timestamp"].iloc[-1]
    # cada df semanal viene con índice reseteado
    assert list(weeks[1]["df"].index) == list(range(35))
```

- [ ] **Step 2: Verificar que falla**

Run: `python3 -m pytest test_walk_forward.py::test_split_weeks_semanas_iso_y_huecos -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'walk_forward'`.

- [ ] **Step 3: Implementar**

En `optimize.py`, agregar después de `simulate()` (y borrar el bloque equivalente de `main()`, líneas 119–141, reemplazándolo por `df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)`):

```python
def load_bars(symbol: str, date_start: datetime, date_end: datetime, api_key: str, secret_key: str) -> pd.DataFrame:
    cache_path = f"cache_{symbol}_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}_1Min.pkl"
    if os.path.exists(cache_path):
        print(f"Cargando datos desde caché ({cache_path})…")
        return pd.read_pickle(cache_path)

    print(f"Descargando datos históricos (1 minuto) de Alpaca para {symbol}…")
    client = StockHistoricalDataClient(api_key, secret_key)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=date_start,
        end=date_end,
    )
    df = client.get_stock_bars(req).df.reset_index()
    df.to_pickle(cache_path)
    print(f"Datos guardados en caché ({cache_path})")
    return df
```

Crear `walk_forward.py`:

```python
import argparse
import itertools
import os
from datetime import datetime

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from optimize import (
    BUY_DROP_RANGE,
    MAX_BUYS,
    SELL_RISE_RANGE,
    STARTING_CASH,
    load_bars,
    new_state,
    simulate,
)


def split_weeks(df_1min: pd.DataFrame) -> list[dict]:
    iso = df_1min["timestamp"].dt.isocalendar()
    weeks = []
    for (year, week), g in df_1min.groupby([iso["year"], iso["week"]], sort=True):
        g = g.reset_index(drop=True)
        weeks.append({
            "label": f"{year}-W{week:02d}",
            "start": g["timestamp"].iloc[0],
            "end":   g["timestamp"].iloc[-1],
            "df":    g,
        })
    return weeks
```

- [ ] **Step 4: Verificar que pasan todos**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 5 PASS.

Sanity de `load_bars` con caché real:
Run: `python3 -c "from datetime import datetime; from optimize import load_bars; df = load_bars('TSLA', datetime(2026,1,1), datetime(2026,7,3), 'x', 'x'); print(len(df))"`
Expected: `Cargando datos desde caché…` y `117862` (no toca la red porque el pkl existe).

- [ ] **Step 5: Commit**

```bash
git add optimize.py walk_forward.py test_walk_forward.py
git commit -m "feat: extraer load_bars() y particionar histórico en semanas ISO

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Grid semanal y selección pico / meseta

**Files:**
- Modify: `walk_forward.py`
- Test: `test_walk_forward.py` (agregar tests)

**Interfaces:**
- Consumes: `simulate`, `BUY_DROP_RANGE`, `SELL_RISE_RANGE`, `MAX_BUYS` (Task 1/2).
- Produces: `run_grid(df: pd.DataFrame, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]` (lista de resultados de `simulate`, uno por combo×intervalo).
- Produces: `select_peak(results: list[dict]) -> dict` (resultado con mayor `roi`).
- Produces: `select_plateau(results: list[dict]) -> tuple[dict, float]` (resultado con mejor promedio de ROI en su vecindario 3×3 de drop±1pp y rise±1pp con el mismo intervalo, y ese score).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `test_walk_forward.py`:

```python
from walk_forward import run_grid, select_peak, select_plateau


def _combo(drop_pp, rise_pp, roi, interval=20):
    return {
        "interval_minutes": interval,
        "buy_drop_pct":  drop_pp / 100,
        "sell_rise_pct": rise_pp / 100,
        "roi": roi,
    }


def test_select_plateau_prefiere_meseta_sobre_pico_aislado():
    # Grid 10x10: base 0, meseta de 5.0 en drop/rise 2..4, pico aislado de 10.0 en (9,9)
    results = []
    for d in range(1, 11):
        for r in range(1, 11):
            if 2 <= d <= 4 and 2 <= r <= 4:
                roi = 5.0
            elif d == 9 and r == 9:
                roi = 10.0
            else:
                roi = 0.0
            results.append(_combo(d, r, roi))

    assert select_peak(results)["buy_drop_pct"] == pytest.approx(0.09)

    plateau, score = select_plateau(results)
    assert plateau["buy_drop_pct"] == pytest.approx(0.03)
    assert plateau["sell_rise_pct"] == pytest.approx(0.03)
    assert score == pytest.approx(5.0)   # los 9 vecinos de (3,3) valen 5.0


def test_select_plateau_no_mezcla_intervalos():
    # Mismo drop/rise, distinto intervalo: los vecindarios son independientes
    results = [_combo(3, 3, 5.0, interval=20), _combo(3, 3, 9.0, interval=60)]
    plateau, score = select_plateau(results)
    assert plateau["interval_minutes"] == 60
    assert score == pytest.approx(9.0)


def test_run_grid_dimensiones_y_mejor_roi():
    # 60 velas de random walk determinístico
    rng = np.random.default_rng(42)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 60))
    df = make_df(prices)

    results = run_grid(df, [1, 5], 0.0, True, 10_000.0)
    assert len(results) == 100 * 2          # 10 drops x 10 rises x 2 intervalos
    peak = select_peak(results)
    assert peak["roi"] == max(r["roi"] for r in results)


import numpy as np
```

(La línea `import numpy as np` va arriba del archivo junto a los otros imports, no al final — se muestra acá para explicitar que el test la necesita.)

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: los 3 tests nuevos FAIL con `ImportError: cannot import name 'run_grid'`.

- [ ] **Step 3: Implementar en `walk_forward.py`**

```python
def run_grid(df: pd.DataFrame, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]:
    results = []
    for interval in intervals:
        sub = df.iloc[::interval].reset_index(drop=True)
        for drop, rise in itertools.product(BUY_DROP_RANGE, SELL_RISE_RANGE):
            results.append(simulate(sub, MAX_BUYS, drop, rise, fee_pct, use_pool, buy_amount, interval))
    return results


def select_peak(results: list[dict]) -> dict:
    return max(results, key=lambda r: r["roi"])


def select_plateau(results: list[dict]) -> tuple[dict, float]:
    tabla = {
        (r["interval_minutes"], round(r["buy_drop_pct"] * 100), round(r["sell_rise_pct"] * 100)): r
        for r in results
    }
    mejor, mejor_score = None, float("-inf")
    for (m, d, s), r in tabla.items():
        vecinos = [
            tabla[(m, d + dd, s + ds)]["roi"]
            for dd in (-1, 0, 1)
            for ds in (-1, 0, 1)
            if (m, d + dd, s + ds) in tabla
        ]
        score = sum(vecinos) / len(vecinos)
        if score > mejor_score:
            mejor, mejor_score = r, score
    return mejor, mejor_score
```

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: grid semanal con selección por pico y por meseta robusta

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Métricas de estabilidad (dispersión, autocorrelación, regret) y mediana expansiva

**Files:**
- Modify: `walk_forward.py`
- Test: `test_walk_forward.py` (agregar tests)

**Interfaces:**
- Consumes: `run_grid`, `select_peak`, `select_plateau` (Task 3); `simulate`, `MAX_BUYS` (Task 1).
- Consumes: estructura `weekly: list[dict]` con claves `{"wk": <dict de split_weeks>, "results": list, "peak": dict, "plateau": dict, "plateau_score": float}` (la arma `run_analysis` en Task 6; los tests la construyen a mano).
- Produces: `lag1_corr(values: list[float]) -> float` (Pearson lag-1; `nan` si n < 3 o varianza cero).
- Produces: `median_params(past_peaks: list[dict]) -> tuple[float, float, int]` → `(drop, rise, interval)`: medianas de drop/rise redondeadas al grid de 1pp, intervalo = moda.
- Produces: `regret_series(weekly: list[dict], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]`, una fila por semana desde la 2ª: `{"label", "own_roi", "applied_roi", "regret"}` con `regret = own_roi - applied_roi`, ambos con portfolio fresco sobre la semana aislada.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `test_walk_forward.py`:

```python
from walk_forward import lag1_corr, median_params, regret_series


def test_lag1_corr():
    assert lag1_corr([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert np.isnan(lag1_corr([5, 5, 5, 5]))      # varianza cero
    assert np.isnan(lag1_corr([1.0, 2.0]))        # muestra insuficiente


def test_median_params():
    peaks = [
        _combo(3, 2, 1.0, interval=20),
        _combo(5, 6, 1.0, interval=20),
        _combo(4, 10, 1.0, interval=5),
    ]
    drop, rise, interval = median_params(peaks)
    assert drop == pytest.approx(0.04)
    assert rise == pytest.approx(0.06)
    assert interval == 20


def _weekly_sintetico(n_semanas=2, bars=80):
    rng = np.random.default_rng(7)
    weekly = []
    lunes = pd.date_range("2026-01-05", periods=n_semanas, freq="7D")
    for i in range(n_semanas):
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, bars))
        df = make_df(prices, start=lunes[i].strftime("%Y-%m-%d 15:00"))
        results = run_grid(df, [1], 0.0, True, 10_000.0)
        plateau, score = select_plateau(results)
        weekly.append({
            "wk": {"label": f"2026-W{2 + i:02d}", "start": df["timestamp"].iloc[0],
                   "end": df["timestamp"].iloc[-1], "df": df},
            "results": results,
            "peak": select_peak(results),
            "plateau": plateau,
            "plateau_score": score,
        })
    return weekly


def test_regret_nunca_negativo():
    # El combo aplicado sale del mismo grid que define el óptimo propio,
    # así que own_roi >= applied_roi siempre.
    weekly = _weekly_sintetico()
    rows = regret_series(weekly, 0.0, True, 10_000.0)
    assert len(rows) == 1
    assert rows[0]["label"] == "2026-W03"
    assert rows[0]["own_roi"] == pytest.approx(weekly[1]["peak"]["roi"])
    assert rows[0]["regret"] >= -1e-9
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: los 3 tests nuevos FAIL con `ImportError: cannot import name 'lag1_corr'`.

- [ ] **Step 3: Implementar en `walk_forward.py`**

```python
def lag1_corr(values: list[float]) -> float:
    s = pd.Series(values, dtype=float)
    if len(s) < 3 or s.iloc[:-1].std() == 0 or s.iloc[1:].std() == 0:
        return float("nan")
    return float(s.corr(s.shift(1)))


def median_params(past_peaks: list[dict]) -> tuple[float, float, int]:
    drop = round(float(np.median([p["buy_drop_pct"] for p in past_peaks])) * 100) / 100
    rise = round(float(np.median([p["sell_rise_pct"] for p in past_peaks])) * 100) / 100
    interval = int(pd.Series([p["interval_minutes"] for p in past_peaks]).mode().iloc[0])
    return drop, rise, interval


def regret_series(weekly: list[dict], fee_pct: float, use_pool: bool, buy_amount: float) -> list[dict]:
    rows = []
    for i in range(1, len(weekly)):
        prev = weekly[i - 1]["peak"]
        d, r, m = prev["buy_drop_pct"], prev["sell_rise_pct"], prev["interval_minutes"]
        sub = weekly[i]["wk"]["df"].iloc[::m].reset_index(drop=True)
        applied = simulate(sub, MAX_BUYS, d, r, fee_pct, use_pool, buy_amount, m)
        own = weekly[i]["peak"]["roi"]
        rows.append({
            "label":       weekly[i]["wk"]["label"],
            "own_roi":     own,
            "applied_roi": applied["roi"],
            "regret":      own - applied["roi"],
        })
    return rows
```

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: métricas de estabilidad: autocorrelación lag-1, mediana expansiva y regret

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `simulate_adaptive()` y torneo de estrategias

**Files:**
- Modify: `walk_forward.py`
- Test: `test_walk_forward.py` (agregar tests)

**Interfaces:**
- Consumes: `simulate`, `new_state` (Task 1); `run_grid`, `select_peak`, `select_plateau` (Task 3); `median_params` (Task 4); estructura `weekly` (Task 4).
- Produces: `simulate_adaptive(week_dfs: list[pd.DataFrame], params_per_week: list[tuple[float, float, int]], fee_pct: float, use_pool: bool, buy_amount: float) -> dict` — portfolio continuo, cambia `(drop, rise, interval)` en cada frontera; submuestrea `iloc[::interval]` POR SEMANA (la fase se reinicia cada lunes); devuelve el dict del último `simulate` (ROI/equity valuados al último precio).
- Produces: `tournament(weekly: list[dict], train_weeks: int, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> tuple[dict, dict]` → `(resultados, planes)`:
  - `resultados`: `{"fija-mediana": dict, "wf-pico": dict, "wf-meseta": dict, "oraculo": dict}` (retorno de `simulate_adaptive` por estrategia).
  - `planes`: mismo keys → `list[tuple[float, float, int]]` (parámetros usados en cada semana de aplicación, para el reporte).
  - Las 4 estrategias operan solo sobre las semanas `weekly[train_weeks:]`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `test_walk_forward.py`:

```python
from walk_forward import simulate_adaptive, tournament


def test_simulate_adaptive_params_constantes_equivale_a_simulate():
    rng = np.random.default_rng(3)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 120))
    df = make_df(prices)
    semanas = [df.iloc[:40].reset_index(drop=True),
               df.iloc[40:90].reset_index(drop=True),
               df.iloc[90:].reset_index(drop=True)]

    completo = simulate(df, MAX_BUYS, 0.03, 0.02, 0.0)
    adaptado = simulate_adaptive(semanas, [(0.03, 0.02, 1)] * 3, 0.0, True, 10_000.0)

    assert adaptado["total_equity"] == pytest.approx(completo["total_equity"])
    assert adaptado["buys"] == completo["buys"]
    assert adaptado["sells"] == completo["sells"]


def test_tournament_estructura_y_semanas_de_aplicacion():
    weekly = _weekly_sintetico(n_semanas=3)
    resultados, planes = tournament(weekly, 1, [1], 0.0, True, 10_000.0)

    assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}
    # 3 semanas, train_weeks=1 -> 2 semanas de aplicación por estrategia
    assert all(len(p) == 2 for p in planes.values())
    # con train_weeks=1, wf-pico usa el pico de la semana anterior
    esperado = (weekly[0]["peak"]["buy_drop_pct"],
                weekly[0]["peak"]["sell_rise_pct"],
                weekly[0]["peak"]["interval_minutes"])
    assert planes["wf-pico"][0] == esperado
    # el oráculo usa el pico de la propia semana
    assert planes["oraculo"][1][0] == weekly[2]["peak"]["buy_drop_pct"]
    for r in resultados.values():
        assert "roi" in r and "total_equity" in r
```

- [ ] **Step 2: Verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: los 2 tests nuevos FAIL con `ImportError: cannot import name 'simulate_adaptive'`.

- [ ] **Step 3: Implementar en `walk_forward.py`**

```python
def simulate_adaptive(week_dfs: list[pd.DataFrame], params_per_week: list[tuple[float, float, int]], fee_pct: float, use_pool: bool, buy_amount: float) -> dict:
    state = None
    result = None
    for df_week, (drop, rise, interval) in zip(week_dfs, params_per_week, strict=True):
        sub = df_week.iloc[::interval].reset_index(drop=True)
        result = simulate(sub, MAX_BUYS, drop, rise, fee_pct, use_pool, buy_amount, interval, state=state)
        state = result["state"]
    return result


def tournament(weekly: list[dict], train_weeks: int, intervals: list[int], fee_pct: float, use_pool: bool, buy_amount: float) -> tuple[dict, dict]:
    def params_of(r: dict) -> tuple[float, float, int]:
        return (r["buy_drop_pct"], r["sell_rise_pct"], r["interval_minutes"])

    app = range(train_weeks, len(weekly))
    app_dfs = [weekly[i]["wk"]["df"] for i in app]

    planes = {"fija-mediana": [], "wf-pico": [], "wf-meseta": [], "oraculo": []}
    for i in app:
        planes["fija-mediana"].append(median_params([weekly[j]["peak"] for j in range(i)]))

        if train_weeks == 1:
            train_results = weekly[i - 1]["results"]
        else:
            train_df = pd.concat(
                [weekly[j]["wk"]["df"] for j in range(i - train_weeks, i)],
                ignore_index=True,
            )
            train_results = run_grid(train_df, intervals, fee_pct, use_pool, buy_amount)

        planes["wf-pico"].append(params_of(select_peak(train_results)))
        planes["wf-meseta"].append(params_of(select_plateau(train_results)[0]))
        planes["oraculo"].append(params_of(weekly[i]["peak"]))

    resultados = {
        nombre: simulate_adaptive(app_dfs, plan, fee_pct, use_pool, buy_amount)
        for nombre, plan in planes.items()
    }
    return resultados, planes
```

- [ ] **Step 4: Verificar que pasan**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 13 PASS.

- [ ] **Step 5: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: simulate_adaptive con portfolio continuo y torneo de 4 estrategias

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `run_analysis()`, reporte (log + CSV), CLI y corrida real

**Files:**
- Modify: `walk_forward.py`
- Test: `test_walk_forward.py` (agregar test)

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: `run_analysis(df_1min: pd.DataFrame, intervals: list[int], train_weeks: int, fee_pct: float, use_pool: bool, buy_amount: float) -> dict` con claves:
  - `"weekly"`: la lista de Task 4 (una entrada por semana),
  - `"stats"`: `{"median_drop", "median_rise", "std_drop", "std_rise", "iqr_drop", "iqr_rise", "corr_drop", "corr_rise", "interval_counts": dict}`,
  - `"regret"`: salida de `regret_series`,
  - `"torneo"`: resultados del torneo, `"planes"`: planes del torneo,
  - `"veredicto"`: string ya redactado.
- Produces: CLI `python3 walk_forward.py --symbol --date-start --date-end --buy-amount --fee-pct --no-profit-pool --intervals --train-weeks` que escribe `walkforward_{symbol}_{ts}.log` y `walkforward_{symbol}_{ts}.csv`.
- CSV: una fila por semana, columnas `week_label, week_start, week_end, bars, best_drop, best_rise, best_interval, best_roi, plateau_drop, plateau_rise, plateau_interval, plateau_roi`.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `test_walk_forward.py`:

```python
from walk_forward import run_analysis


def test_run_analysis_pipeline_completo():
    rng = np.random.default_rng(11)
    partes = []
    lunes = pd.date_range("2026-01-05", periods=4, freq="7D")
    for i in range(4):
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
        partes.append(make_df(prices, start=lunes[i].strftime("%Y-%m-%d 15:00")))
    df = pd.concat(partes, ignore_index=True)

    out = run_analysis(df, intervals=[1], train_weeks=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)

    assert len(out["weekly"]) == 4
    assert len(out["regret"]) == 3
    assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}
    assert 0.01 <= out["stats"]["median_drop"] <= 0.10
    assert isinstance(out["veredicto"], str) and len(out["veredicto"]) > 0
```

- [ ] **Step 2: Verificar que falla**

Run: `python3 -m pytest test_walk_forward.py::test_run_analysis_pipeline_completo -v`
Expected: FAIL con `ImportError: cannot import name 'run_analysis'`.

- [ ] **Step 3: Implementar en `walk_forward.py`**

```python
def run_analysis(df_1min: pd.DataFrame, intervals: list[int], train_weeks: int, fee_pct: float, use_pool: bool, buy_amount: float) -> dict:
    weeks = split_weeks(df_1min)
    if len(weeks) <= train_weeks + 1:
        raise SystemExit(f"Error: {len(weeks)} semana(s) de datos; se necesitan al menos {train_weeks + 2}.")

    weekly = []
    for n, wk in enumerate(weeks, 1):
        print(f"  Grid semana {n}/{len(weeks)} ({wk['label']})", end="\r")
        results = run_grid(wk["df"], intervals, fee_pct, use_pool, buy_amount)
        plateau, plateau_score = select_plateau(results)
        weekly.append({
            "wk": wk,
            "results": results,
            "peak": select_peak(results),
            "plateau": plateau,
            "plateau_score": plateau_score,
        })
    print()

    drops = [w["peak"]["buy_drop_pct"] for w in weekly]
    rises = [w["peak"]["sell_rise_pct"] for w in weekly]
    ints  = [w["peak"]["interval_minutes"] for w in weekly]
    q75d, q25d = np.percentile(drops, [75, 25])
    q75r, q25r = np.percentile(rises, [75, 25])
    stats = {
        "median_drop": float(np.median(drops)),
        "median_rise": float(np.median(rises)),
        "std_drop":    float(np.std(drops)),
        "std_rise":    float(np.std(rises)),
        "iqr_drop":    float(q75d - q25d),
        "iqr_rise":    float(q75r - q25r),
        "corr_drop":   lag1_corr(drops),
        "corr_rise":   lag1_corr(rises),
        "interval_counts": dict(pd.Series(ints).value_counts().sort_index()),
    }

    regret = regret_series(weekly, fee_pct, use_pool, buy_amount)
    torneo, planes = tournament(weekly, train_weeks, intervals, fee_pct, use_pool, buy_amount)

    fija = torneo["fija-mediana"]["roi"]
    adaptativas = {"WF-pico": torneo["wf-pico"]["roi"], "WF-meseta": torneo["wf-meseta"]["roi"]}
    mejor_adapt = max(adaptativas, key=adaptativas.get)
    if adaptativas[mejor_adapt] > fija:
        veredicto = (
            f"EL AUTO-AJUSTE SE JUSTIFICA: {mejor_adapt} ({adaptativas[mejor_adapt]:+.2f}%) supera a "
            f"Fija-mediana ({fija:+.2f}%). Techo teórico (Oráculo): {torneo['oraculo']['roi']:+.2f}%."
        )
    else:
        d, r, m = median_params([w["peak"] for w in weekly])
        veredicto = (
            f"EL AUTO-AJUSTE NO SE JUSTIFICA: Fija-mediana ({fija:+.2f}%) le gana a "
            f"WF-pico ({adaptativas['WF-pico']:+.2f}%) y WF-meseta ({adaptativas['WF-meseta']:+.2f}%). "
            f"Recomendación: parámetros fijos drop={d*100:.0f}% rise={r*100:.0f}% intervalo={m} min."
        )

    return {"weekly": weekly, "stats": stats, "regret": regret,
            "torneo": torneo, "planes": planes, "veredicto": veredicto}
```

Después agregar el reporte y el `main()`:

```python
SEP  = "=" * 80
SEP2 = "-" * 80


def build_report(symbol: str, out: dict, train_weeks: int, intervals: list[int], buy_amount: float, fee_pct: float, use_pool: bool) -> list[str]:
    weekly, stats, regret = out["weekly"], out["stats"], out["regret"]
    torneo, planes = out["torneo"], out["planes"]

    lines = [
        SEP,
        f"  WALK-FORWARD {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        SEP,
        f"  Semanas: {len(weekly)}  ({weekly[0]['wk']['label']} → {weekly[-1]['wk']['label']})"
        f"   |   train_weeks: {train_weeks}   |   intervalos: {intervals}",
        f"  Monto por compra: ${buy_amount:,.0f}   |   fee: {fee_pct*100:.3f}%   |   pool: {'ON' if use_pool else 'OFF'}",
        SEP,
        "",
        "  1) ESTABILIDAD DE LOS ÓPTIMOS SEMANALES",
        SEP2,
        f"  {'semana':<10}  {'velas':>6}  {'drop':>5}  {'rise':>5}  {'min':>4}  {'ROI%':>8}   |  {'meseta d/r/min':>15}  {'ROI%':>8}",
        SEP2,
    ]
    for w in weekly:
        p, q = w["peak"], w["plateau"]
        lines.append(
            f"  {w['wk']['label']:<10}  {len(w['wk']['df']):>6}  "
            f"{p['buy_drop_pct']*100:>4.0f}%  {p['sell_rise_pct']*100:>4.0f}%  {p['interval_minutes']:>4}  {p['roi']:>+8.2f}   |  "
            f"{q['buy_drop_pct']*100:>4.0f}/{q['sell_rise_pct']*100:>3.0f}/{q['interval_minutes']:>4}  {q['roi']:>+8.2f}"
        )
    lines += [
        SEP2,
        f"  drop óptimo : mediana {stats['median_drop']*100:.1f}%  desvío {stats['std_drop']*100:.2f}pp  IQR {stats['iqr_drop']*100:.1f}pp  autocorr lag-1 {stats['corr_drop']:+.2f}",
        f"  rise óptimo : mediana {stats['median_rise']*100:.1f}%  desvío {stats['std_rise']*100:.2f}pp  IQR {stats['iqr_rise']*100:.1f}pp  autocorr lag-1 {stats['corr_rise']:+.2f}",
        f"  intervalos ganadores: {stats['interval_counts']}",
        f"  (n = {len(weekly)} semanas: muestra chica, interpretar la autocorrelación con cautela)",
        "",
        "  REGRET (usar el óptimo de la semana anterior vs el propio, semanas aisladas)",
        SEP2,
        f"  {'semana':<10}  {'ROI propio':>10}  {'ROI aplicado':>12}  {'regret':>8}",
        SEP2,
    ]
    for row in regret:
        lines.append(f"  {row['label']:<10}  {row['own_roi']:>+10.2f}  {row['applied_roi']:>+12.2f}  {row['regret']:>8.2f}")
    regrets = [row["regret"] for row in regret]
    lines += [
        SEP2,
        f"  regret promedio {np.mean(regrets):.2f}pp  |  mediana {np.median(regrets):.2f}pp  |  peor {np.max(regrets):.2f}pp",
        "",
        "  2) TORNEO DE ESTRATEGIAS (portfolio continuo, semanas de aplicación: "
        f"{len(weekly) - train_weeks})",
        SEP2,
        f"  {'estrategia':<14}  {'ROI%':>8}  {'Ganancia':>12}  {'Capital':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Fees':>10}",
        SEP2,
    ]
    for nombre in ("fija-mediana", "wf-pico", "wf-meseta", "oraculo"):
        r = torneo[nombre]
        lines.append(
            f"  {nombre:<14}  {r['roi']:>+8.2f}  ${r['profit']:>+11,.0f}  ${r['total_equity']:>11,.0f}  "
            f"{r['buys']:>7}  {r['sells']:>6}  {r['open_positions']:>5}  ${r['total_fees']:>9,.0f}"
        )
    lines += [
        SEP2,
        "",
        "  PARÁMETROS USADOS POR SEMANA (drop%/rise%/min)",
        SEP2,
        "  " + f"{'semana':<10}" + "".join(f"  {n:>14}" for n in planes),
        SEP2,
    ]
    app_weeks = weekly[train_weeks:]
    for k, w in enumerate(app_weeks):
        celdas = "".join(
            f"  {planes[n][k][0]*100:>4.0f}/{planes[n][k][1]*100:>3.0f}/{planes[n][k][2]:>4}"
            for n in planes
        )
        lines.append(f"  {w['wk']['label']:<10}{celdas}")
    lines += [SEP2, "", "  3) VEREDICTO", SEP2, f"  {out['veredicto']}", SEP]
    return lines


def main():
    parser = argparse.ArgumentParser(description="Walk-forward: estabilidad de óptimos semanales y torneo de estrategias")
    parser.add_argument("--symbol",      type=str,   default="TSLA")
    parser.add_argument("--date-start",  type=str,   default="2026-01-01")
    parser.add_argument("--date-end",    type=str,   default="2026-06-28")
    parser.add_argument("--buy-amount",  type=float, default=10_000.0)
    parser.add_argument("--fee-pct",     type=float, default=0.0)
    parser.add_argument("--no-profit-pool", action="store_true")
    parser.add_argument("--intervals",   type=str,   default="20", help="Intervalos de revisión en minutos, separados por coma")
    parser.add_argument("--train-weeks", type=int,   default=1, help="Semanas previas usadas para optimizar (default: 1)")
    args = parser.parse_args()

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    intervals  = sorted({max(1, int(v.strip())) for v in args.intervals.split(",") if v.strip()})
    use_pool   = not args.no_profit_pool

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    print(f"Velas de 1 minuto cargadas: {len(df_1min)}\n")

    out = run_analysis(df_1min, intervals, args.train_weeks, args.fee_pct, use_pool, args.buy_amount)

    lines = build_report(symbol, out, args.train_weeks, intervals, args.buy_amount, args.fee_pct, use_pool)
    print("\n".join(lines))

    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"walkforward_{symbol}_{run_ts}.log"
    csv_path = f"walkforward_{symbol}_{run_ts}.csv"

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    filas = [{
        "week_label":       w["wk"]["label"],
        "week_start":       w["wk"]["start"],
        "week_end":         w["wk"]["end"],
        "bars":             len(w["wk"]["df"]),
        "best_drop":        w["peak"]["buy_drop_pct"],
        "best_rise":        w["peak"]["sell_rise_pct"],
        "best_interval":    w["peak"]["interval_minutes"],
        "best_roi":         w["peak"]["roi"],
        "plateau_drop":     w["plateau"]["buy_drop_pct"],
        "plateau_rise":     w["plateau"]["sell_rise_pct"],
        "plateau_interval": w["plateau"]["interval_minutes"],
        "plateau_roi":      w["plateau"]["roi"],
    } for w in out["weekly"]]
    pd.DataFrame(filas).to_csv(csv_path, index=False)

    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")


if __name__ == "__main__":
    main()
```

Nota: si `load_bars` necesita descargar (sin caché) y no hay credenciales, el `StockHistoricalDataClient` fallará solo; con el caché presente las credenciales no se usan.

- [ ] **Step 4: Verificar que pasa la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: 14 PASS.

- [ ] **Step 5: Corrida real sobre el caché de TSLA (criterio de éxito del spec)**

Run: `python3 walk_forward.py --symbol TSLA --date-start 2026-01-01 --date-end 2026-07-03 --intervals 20 --train-weeks 1`

Expected: usa `cache_TSLA_20260101_20260703_1Min.pkl` (sin red), imprime las 3 secciones (estabilidad con ~27 semanas, torneo con 4 estrategias, veredicto) y guarda `walkforward_TSLA_*.log` y `walkforward_TSLA_*.csv`. Verificar a ojo: ningún `nan` inesperado fuera de la autocorrelación, ROIs con valores plausibles (una cifra por estrategia), CSV con una fila por semana.

- [ ] **Step 6: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: walk_forward.py con reporte de estabilidad, torneo y veredicto

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Verificación final contra el spec

- `simulate()` con estado + retrocompatibilidad → Task 1.
- `load_bars()` compartido y caché → Task 2.
- Partición semanal ISO con huecos → Task 2.
- Grid semanal, pico y meseta 3×3 → Task 3.
- Dispersión, autocorrelación lag-1, regret, mediana expansiva → Task 4.
- Portfolio continuo entre semanas y 4 estrategias → Task 5.
- Reporte log + CSV historial semanal + veredicto + corrida real TSLA → Task 6.
- Fuera de alcance respetado: no se toca `tradebot.py` ni `backtest.py`.
