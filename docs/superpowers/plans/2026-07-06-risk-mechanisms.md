# Benchmark Buy & Hold + Mecanismos Anti-Crash — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar benchmark buy & hold y drawdown máximo a las herramientas de análisis, y tres mecanismos anti-crash opcionales (cooldown, slots reservados, circuit breaker) medibles con un torneo nuevo (`risk_tournament.py`) sobre datos históricos cacheados.

**Architecture:** Todo el motor de simulación vive en `simulate()` (`optimize.py`); los mecanismos entran como parámetros opcionales default-apagados con la decisión de compra concentrada en un helper `can_buy()`. `backtest.py` se refactoriza a wrapper fino de `simulate()` vía un callback `on_trade`. `risk_tournament.py` corre la matriz de variantes por símbolo y reporta ΔROI/ΔmaxDD contra baseline.

**Tech Stack:** Python 3.12 (venv `.venv`), pandas, numpy, pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-07-06-risk-mechanisms-design.md`

## Global Constraints

- Directorio de trabajo: `/home/david/Repos/David/Inversiones/invertirCarlos/alpaca` (raíz del repo git). Branch: `feature/risk-mechanisms`.
- Correr tests con: `python3 -m pytest test_walk_forward.py -v` (si existe `.venv/bin/python3`, usarlo). Los tests NUNCA tocan red ni credenciales.
- Los mecanismos anti-crash tienen default APAGADO (`cooldown_minutes=0`, `reserved_slots=0`, `deep_drop_pct=0.0`, `breaker_dd_pct=0.0`). Con defaults, `simulate()` debe devolver exactamente los mismos ROI/compras/ventas que hoy.
- Las ventas NUNCA se bloquean; ningún mecanismo liquida posiciones.
- NO tocar `tradebot.py`, `tesla.py`, `test1.py` (fuera de alcance).
- Código y reportes en español, siguiendo el estilo existente (funciones module-level, sin clases; comentarios solo donde el código no se explica solo).
- Después de cada task: suite completa verde + commit.

---

### Task 1: Drawdown máximo en `simulate()` + claves nuevas en `new_state()`

**Files:**
- Modify: `optimize.py` (funciones `new_state()` y `simulate()`)
- Test: `test_walk_forward.py`

**Interfaces:**
- Produces: `simulate()` devuelve la clave nueva `"max_drawdown_pct"` (float, %, ≥0) y su `"state"` incluye `"equity_peak"` (float) y `"max_dd"` (fracción 0-1). `new_state()` inicializa `"equity_peak": starting_cash, "max_dd": 0.0`. Tasks 3, 6, 9 y 11 dependen de esto.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `test_walk_forward.py`:

```python
# ---------------------------------------------------------------------------
# Drawdown máximo (spec 2026-07-06-risk-mechanisms, fase 1.2)
# ---------------------------------------------------------------------------

def test_simulate_max_drawdown_basico():
    # drop=5%, rise=5%, buy=10000, pool ON, secuencia [100, 80, 90]:
    #   bar0: compra inicial @100 (qty 100, cash 90000) -> equity 100000
    #   bar1: 80 <= 95, compra grid @80 (qty 125, cash 80000)
    #         -> equity 80000 + 225*80 = 98000 -> dd = 2%
    #   bar2: 90 >= 84 (target venta s/80), vende qty 125 (revenue 11250,
    #         cash 91250) -> equity 91250 + 100*90 = 100250 -> pico nuevo
    df = make_df([100, 80, 90])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert r["max_drawdown_pct"] == pytest.approx(2.0)
    assert r["state"]["equity_peak"] == pytest.approx(100_250.0)
    assert r["state"]["max_dd"] == pytest.approx(0.02)


def test_simulate_max_drawdown_encadenado_persiste_pico():
    # [100, 80, 70]: bar2 compra @70 (target 76 s/80), equity final
    # 70000 + 367.857*70 = 95750 -> dd 4.25% respecto del pico 100000.
    # Si el pico no persistiera en state, la corrida encadenada mediría el
    # dd del segundo tramo contra su propio primer equity (95750) y daría ~0.
    df = make_df([100, 80, 70])
    completo = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert completo["max_drawdown_pct"] == pytest.approx(4.25)

    df_a = df.iloc[:2].reset_index(drop=True)
    df_b = df.iloc[2:].reset_index(drop=True)
    r1 = simulate(df_a, MAX_BUYS, 0.05, 0.05, 0.0)
    r2 = simulate(df_b, MAX_BUYS, 0.05, 0.05, 0.0, state=r1["state"])
    assert r2["max_drawdown_pct"] == pytest.approx(completo["max_drawdown_pct"])


def test_simulate_tolera_state_viejo_sin_claves_nuevas():
    # Un state serializado antes de este cambio no tiene equity_peak/max_dd:
    # simulate debe tolerarlo con defaults en vez de romper con KeyError.
    st = new_state()
    del st["equity_peak"]
    del st["max_dd"]
    r = simulate(make_df(ZIGZAG), MAX_BUYS, 0.05, 0.05, 0.0, state=st)
    assert r["roi"] == pytest.approx(1.1319148936, abs=1e-6)
    assert "max_drawdown_pct" in r
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -k max_drawdown -v`
Expected: FAIL con `KeyError: 'max_drawdown_pct'` (y el de state viejo con `KeyError: 'equity_peak'` en el `del`).

- [ ] **Step 3: Implementar en `optimize.py`**

`new_state()` queda:

```python
def new_state(starting_cash: float = STARTING_CASH) -> dict:
    return {
        "cash":        starting_cash,
        "purchases":   [],
        "profit_pool": 0.0,
        "total_buys":  0,
        "total_sells": 0,
        "total_fees":  0.0,
        "equity_peak": starting_cash,
        "max_dd":      0.0,
    }
```

`simulate()` se reestructura: el `continue` de la compra inicial se reemplaza por `if/else` para que el epílogo por vela (equity/drawdown) corra siempre. Cuerpo completo:

```python
def simulate(df: pd.DataFrame, max_buys: int, buy_drop_pct: float, sell_rise_pct: float, fee_pct: float, use_pool: bool = True, buy_amount: float = BUY_AMOUNT, interval_minutes: int = 1, state: dict | None = None) -> dict:
    if state is None:
        state = new_state()
    cash        = state["cash"]
    purchases   = list(state["purchases"])
    profit_pool = state["profit_pool"]
    total_buys  = state["total_buys"]
    total_sells = state["total_sells"]
    total_fees  = state["total_fees"]
    equity_peak = state.get("equity_peak", 0.0)
    max_dd      = state.get("max_dd", 0.0)

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

        # Epílogo por vela: equity, pico y drawdown máximo
        equity = cash + sum(p["qty"] for p in purchases) * price
        if equity > equity_peak:
            equity_peak = equity
        dd = (equity_peak - equity) / equity_peak if equity_peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

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
        "max_drawdown_pct": max_dd * 100,
        "state": {
            "cash":        cash,
            "purchases":   purchases,
            "profit_pool": profit_pool,
            "total_buys":  total_buys,
            "total_sells": total_sells,
            "total_fees":  total_fees,
            "equity_peak": equity_peak,
            "max_dd":      max_dd,
        },
    }
```

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos (los 24 existentes + 3 nuevos). En particular `test_simulate_retrocompatible_zigzag` y `test_simulate_no_muta_el_state_del_caller` deben seguir verdes — prueban que la reestructura no cambió la lógica.

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "feat: trackear drawdown máximo del equity en simulate()"
```

---

### Task 2: `buy_hold_roi()` en `optimize.py`

**Files:**
- Modify: `optimize.py` (función nueva después de `simulate()`)
- Test: `test_walk_forward.py`

**Interfaces:**
- Produces: `buy_hold_roi(df: pd.DataFrame, starting_cash: float = STARTING_CASH) -> dict` con claves `{"roi", "profit", "total_equity", "max_drawdown_pct"}` (mismos nombres que `simulate()`). Tasks 3, 4, 6 y 11 la importan.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `test_walk_forward.py` (y sumar `buy_hold_roi` al import de `optimize` al tope del archivo):

```python
# ---------------------------------------------------------------------------
# Buy & hold de referencia (spec fase 1.1)
# ---------------------------------------------------------------------------

def test_buy_hold_roi_serie_que_sube():
    df = make_df([100, 110, 120])
    r = buy_hold_roi(df)
    assert r["roi"] == pytest.approx(20.0)
    assert r["profit"] == pytest.approx(20_000.0)
    assert r["total_equity"] == pytest.approx(120_000.0)
    assert r["max_drawdown_pct"] == pytest.approx(0.0)


def test_buy_hold_roi_con_drawdown():
    # 100 -> 80 (dd 20%) -> 110 (cierra +10%)
    df = make_df([100, 80, 110])
    r = buy_hold_roi(df)
    assert r["roi"] == pytest.approx(10.0)
    assert r["max_drawdown_pct"] == pytest.approx(20.0)


def test_buy_hold_roi_respeta_starting_cash():
    df = make_df([100, 150])
    r = buy_hold_roi(df, starting_cash=10_000.0)
    assert r["total_equity"] == pytest.approx(15_000.0)
    assert r["roi"] == pytest.approx(50.0)
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -k buy_hold -v`
Expected: FAIL con `ImportError: cannot import name 'buy_hold_roi'`.

- [ ] **Step 3: Implementar en `optimize.py`** (debajo de `simulate()`)

```python
def buy_hold_roi(df: pd.DataFrame, starting_cash: float = STARTING_CASH) -> dict:
    """Referencia buy & hold: invierte todo el capital al primer close del
    rango y valúa al último. Sin fees (referencia teórica). Devuelve las
    mismas claves de métricas que simulate() para poder mezclar en tablas."""
    closes = df["close"].astype(float)
    qty    = starting_cash / closes.iloc[0]
    equity = qty * closes
    peak   = equity.cummax()
    max_dd = float(((peak - equity) / peak).max())

    total_equity = float(equity.iloc[-1])
    profit       = total_equity - starting_cash
    return {
        "roi":              (profit / starting_cash) * 100,
        "profit":           profit,
        "total_equity":     total_equity,
        "max_drawdown_pct": max_dd * 100,
    }
```

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "feat: buy_hold_roi() como referencia de benchmark"
```

---

### Task 3: `walk_forward.py` — fila buy-hold, columna maxDD y veredicto vs B&H

**Files:**
- Modify: `walk_forward.py` (`tournament()`, `run_analysis()`, `build_report()`, import)
- Test: `test_walk_forward.py` (tests nuevos + actualizar 5 aserciones existentes)

**Interfaces:**
- Consumes: `buy_hold_roi()` (Task 2), `"max_drawdown_pct"` en resultados de `simulate()` (Task 1).
- Produces: `tournament()` devuelve `resultados` con la clave extra `"buy-hold"` (dict de `buy_hold_roi`); `planes` NO cambia (sigue con 4 estrategias). El veredicto de `run_analysis()` siempre menciona "buy & hold".

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `test_walk_forward.py`:

```python
def test_tournament_incluye_buy_hold_sobre_periodos_de_aplicacion():
    weekly = _weekly_sintetico(n_semanas=3)
    resultados, planes = tournament(weekly, 1, [1], 0.0, True, 10_000.0)

    assert "buy-hold" in resultados
    # B&H se calcula SOLO sobre los períodos de aplicación (train_periods=1
    # -> semanas 1 y 2), no sobre el rango completo.
    app_df = pd.concat([weekly[1]["wk"]["df"], weekly[2]["wk"]["df"]], ignore_index=True)
    first = float(app_df["close"].iloc[0])
    last  = float(app_df["close"].iloc[-1])
    assert resultados["buy-hold"]["roi"] == pytest.approx((last - first) / first * 100)
    # buy-hold no participa de la tabla de parámetros por período
    assert set(planes) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo"}


def test_veredicto_siempre_menciona_buy_hold():
    rng = np.random.default_rng(11)
    partes = []
    lunes = pd.date_range("2026-01-05", periods=4, freq="7D")
    for i in range(4):
        prices = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
        partes.append(make_df(prices, start=lunes[i].strftime("%Y-%m-%d 15:00")))
    df = pd.concat(partes, ignore_index=True)

    out = run_analysis(df, intervals=[1], train_periods=1, fee_pct=0.0, use_pool=True, buy_amount=10_000.0)
    assert "buy & hold" in out["veredicto"].lower()
    # Consistencia dirección/texto: si el mejor ROI realista supera al B&H
    # el texto dice que el grid le gana; si no, advierte con "OJO".
    bh = out["torneo"]["buy-hold"]["roi"]
    mejor_real = max(out["torneo"]["fija-mediana"]["roi"],
                     out["torneo"]["wf-pico"]["roi"],
                     out["torneo"]["wf-meseta"]["roi"])
    if mejor_real > bh:
        assert "le gana" in out["veredicto"]
    else:
        assert "OJO" in out["veredicto"]
```

Y actualizar las 5 aserciones existentes sobre las claves del torneo (`"buy-hold"` se suma al set en `resultados`/`out["torneo"]`):

1. En `test_tournament_estructura_y_semanas_de_aplicacion`:
   `assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}`
2. En `test_tournament_train_weeks_mayor_a_uno_usa_ventana_correcta`:
   `assert set(resultados) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}`
3. En `test_run_analysis_pipeline_completo`:
   `assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}`
4. En `test_run_analysis_veredicto_no_se_justifica_cuando_fija_empata_adaptativas`:
   `assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}`
5. En `test_run_analysis_con_period_month_pipeline_completo`:
   `assert set(out["torneo"]) == {"fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"}`

Nota: en `test_tournament_estructura_y_semanas_de_aplicacion` el loop final `for r in resultados.values(): assert "roi" in r and "total_equity" in r` sigue válido — `buy_hold_roi` devuelve ambas claves.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -k "buy_hold or tournament or run_analysis or veredicto" -v`
Expected: FAIL los nuevos (`KeyError: 'buy-hold'`) y los 5 actualizados (set distinto).

- [ ] **Step 3: Implementar en `walk_forward.py`**

3a. Sumar `buy_hold_roi` al import de `optimize`:

```python
from optimize import (
    BUY_DROP_RANGE,
    MAX_BUYS,
    SELL_RISE_RANGE,
    STARTING_CASH,
    buy_hold_roi,
    load_bars,
    new_state,
    simulate,
)
```

3b. En `tournament()`, después de construir `resultados` y antes del `return`:

```python
    resultados = {
        nombre: simulate_adaptive(app_dfs, plan, fee_pct, use_pool, buy_amount)
        for nombre, plan in planes.items()
    }
    resultados["buy-hold"] = buy_hold_roi(pd.concat(app_dfs, ignore_index=True))
    return resultados, planes
```

3c. En `run_analysis()`, extender el veredicto (después del `if/else` que lo arma):

```python
    bh = torneo["buy-hold"]["roi"]
    mejor_real = max(fija, adaptativas["WF-pico"], adaptativas["WF-meseta"])
    if mejor_real > bh:
        veredicto += f" Buy & hold de referencia: {bh:+.2f}% — el grid le gana en este rango."
    else:
        veredicto += (
            f" OJO: buy & hold ({bh:+.2f}%) supera a la mejor estrategia del grid "
            f"({mejor_real:+.2f}%) — el grid no agrega valor sobre comprar y sostener en este símbolo/rango."
        )
```

3d. En `build_report()`, sección del torneo — header y filas ganan `maxDD%` y la fila `buy-hold`:

```python
        f"  {'estrategia':<14}  {'ROI%':>8}  {'maxDD%':>7}  {'Ganancia':>12}  {'Capital':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Fees':>10}",
        SEP2,
    ]
    for nombre in ("fija-mediana", "wf-pico", "wf-meseta", "oraculo", "buy-hold"):
        r = torneo[nombre]
        lines.append(
            f"  {nombre:<14}  {r['roi']:>+8.2f}  {r.get('max_drawdown_pct', 0.0):>7.2f}  ${r['profit']:>+11,.0f}  ${r['total_equity']:>11,.0f}  "
            f"{r.get('buys', 0):>7}  {r.get('sells', 0):>6}  {r.get('open_positions', 0):>5}  ${r.get('total_fees', 0.0):>9,.0f}"
        )
```

(La fila buy-hold muestra 0 en compras/ventas/open/fees vía `.get` — no opera.)

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 5: Commit**

```bash
git add walk_forward.py test_walk_forward.py
git commit -m "feat: fila buy & hold y columna maxDD en el torneo walk-forward"
```

---

### Task 4: Línea B&H en el reporte de `optimize.py`

**Files:**
- Modify: `optimize.py` (solo `main()`)

**Interfaces:**
- Consumes: `buy_hold_roi()` (Task 2).
- Produces: nada consumido por otras tasks (cambio de reporte).

- [ ] **Step 1: Implementar**

En `main()` de `optimize.py`, después de calcular `precio_fin` y antes de armar `lines`, agregar:

```python
    bh = buy_hold_roi(df_1min)
```

y en la cabecera de `lines`, inmediatamente después de la línea `f"  Precio {symbol} inicio: ..."`:

```python
        f"  Buy & Hold referencia: {bh['roi']:+.2f}%  (maxDD {bh['max_drawdown_pct']:.2f}%)",
```

- [ ] **Step 2: Verificación manual con caché existente**

Run: `ls cache_SPCX_*.pkl && python3 optimize.py --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03 --intervals 20`
Expected: el reporte muestra la línea `Buy & Hold referencia: ...%` con un valor coherente con los precios inicio/fin impresos arriba (SPCX cerró por debajo de donde empezó según la bitácora, así que debería dar un ROI B&H acorde a esos precios). Si el caché no existe, usar cualquier `cache_*.pkl` presente ajustando símbolo/fechas al nombre del archivo.

- [ ] **Step 3: Correr la suite completa (regresión)**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 4: Commit**

```bash
git add optimize.py
git commit -m "feat: línea de referencia buy & hold en el reporte de optimize"
```

---

### Task 5: Callback `on_trade` en `simulate()`

**Files:**
- Modify: `optimize.py` (`simulate()`)
- Test: `test_walk_forward.py`

**Interfaces:**
- Produces: `simulate(..., on_trade=None)`. Si se pasa, se invoca `on_trade(evento)` por cada operación, donde `evento` es un dict:
  - Compras: `{"type": "BUY_INIT"|"BUY_GRID", "price", "qty", "fee", "cash", "pool", "timestamp", "open_positions"}`
  - Ventas: lo mismo con `"type": "SELL"` más `"buy_price"` y `"profit"`.
  - `timestamp` es el valor crudo de `row["timestamp"]`; `cash`/`pool`/`open_positions` son el estado DESPUÉS de la operación.
- Task 6 (backtest) consume este callback.

- [ ] **Step 1: Escribir el test que falla**

```python
# ---------------------------------------------------------------------------
# Callback on_trade (spec fase 2.7)
# ---------------------------------------------------------------------------

def test_on_trade_emite_eventos_en_orden():
    # Con ZIGZAG la secuencia de operaciones es conocida (ver comentario
    # arriba de ZIGZAG): init@100, grid@94, sell@99, sell@106, init@100.
    df = make_df(ZIGZAG)
    eventos = []
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, on_trade=eventos.append)

    assert [e["type"] for e in eventos] == ["BUY_INIT", "BUY_GRID", "SELL", "SELL", "BUY_INIT"]
    assert [e["price"] for e in eventos] == [100.0, 94.0, 99.0, 106.0, 100.0]

    venta = eventos[2]
    assert venta["buy_price"] == pytest.approx(94.0)
    assert venta["profit"] == pytest.approx(531.9148936, abs=1e-4)
    assert venta["timestamp"] == df["timestamp"].iloc[2]
    assert venta["open_positions"] == 1          # quedó solo el lote de 100

    assert eventos[-1]["open_positions"] == 1
    assert eventos[-1]["pool"] == pytest.approx(1131.9148936 * 0.9, abs=1e-4)  # pool tras restar bonus pool/10

    # El callback no altera el resultado numérico
    r2 = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    assert r["roi"] == pytest.approx(r2["roi"])
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python3 -m pytest test_walk_forward.py -k on_trade -v`
Expected: FAIL con `TypeError: simulate() got an unexpected keyword argument 'on_trade'`.

- [ ] **Step 3: Implementar**

Firma: agregar `on_trade=None` al final de los parámetros de `simulate()`. En el loop, tras cada operación, emitir el evento (solo si hay callback). Las tres inserciones:

Compra inicial — después de `total_buys += 1`:

```python
            if on_trade:
                on_trade({"type": "BUY_INIT", "price": price, "qty": qty, "fee": buy_fee,
                          "cash": cash, "pool": profit_pool, "timestamp": row["timestamp"],
                          "open_positions": len(purchases)})
```

Compra grid — después de su `total_buys += 1` (idéntico pero `"type": "BUY_GRID"`).

Venta — después del bloque del pool:

```python
                if on_trade:
                    on_trade({"type": "SELL", "price": price, "qty": sold["qty"], "fee": sell_fee,
                              "cash": cash, "pool": profit_pool, "timestamp": row["timestamp"],
                              "open_positions": len(purchases),
                              "buy_price": sold["price"], "profit": profit})
```

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "feat: callback on_trade en simulate() para log por operación"
```

---

### Task 6: Refactor de `backtest.py` como wrapper de `simulate()`

**Files:**
- Modify: `backtest.py` (reescritura completa)
- No hay test automatizado nuevo: la red de seguridad es la verificación de equivalencia numérica (Steps 1 y 4).

**Interfaces:**
- Consumes: `simulate()` con `on_trade` (Task 5), `buy_hold_roi()` (Task 2), `load_bars()` (existente en `optimize.py`).
- Produces: nada consumido por otras tasks. La CLI de `backtest.py` no cambia en esta task.

- [ ] **Step 1: Capturar el baseline ANTES de tocar nada**

```bash
ls cache_*.pkl
```

Elegir un caché de TSLA (ej. `cache_TSLA_20250101_20260701_1Min.pkl` → fechas `2025-01-01` / `2026-07-01`). Correr y guardar:

```bash
python3 backtest.py --symbol TSLA --date-start 2025-01-01 --date-end 2026-07-01 \
  --buy-drop-pct 0.01 --sell-rise-pct 0.03 --interval-minutes 20 \
  | tee /tmp/claude-1000/-home-david-Repos-David-Inversiones/0ea25107-db8a-4b1e-bae2-03b3a94c6a4b/scratchpad/backtest_baseline.txt
```

Anotar del output: `Retorno (ROI)`, `Capital Final Total`, `Total de Compras`, `Total de Ventas`, `Compras Activas`. Si no existe ningún caché de TSLA, usar el símbolo/fechas de cualquier `cache_*.pkl` disponible (mismos flags) — lo que importa es comparar el mismo comando antes y después.

- [ ] **Step 2: Reescribir `backtest.py`**

Contenido completo del archivo nuevo:

```python
import os
import argparse
from datetime import datetime
from dotenv import load_dotenv

from optimize import STARTING_CASH, buy_hold_roi, load_bars, simulate


def main():
    parser = argparse.ArgumentParser(description="Backtest de estrategia grid")
    parser.add_argument("--symbol",     type=str,   default="TSLA",       help="Símbolo a analizar (default: TSLA)")
    parser.add_argument("--date-start", type=str,   default="2026-01-01", help="Fecha inicio YYYY-MM-DD (default: 2026-01-01)")
    parser.add_argument("--date-end",   type=str,   default="2026-06-28", help="Fecha fin YYYY-MM-DD (default: 2026-06-28)")
    parser.add_argument("--buy-amount", type=float, default=10000.0,      help="Monto base por compra en USD (default: 10000)")
    parser.add_argument("--max-buys", type=int, default=10, help="Máximo de compras activas simultáneas (default: 10)")
    parser.add_argument("--buy-drop-pct", type=float, default=0.05, help="Porcentaje de caída para nueva compra (default: 0.05 = 5%%)")
    parser.add_argument("--sell-rise-pct", type=float, default=0.04, help="Porcentaje de subida para venta (default: 0.04 = 4%%)")
    parser.add_argument("--fee-pct", type=float, default=0.0, help="Fee por operación sobre el monto (default: 0.0). Ej: 0.001 = 0.1%%")
    parser.add_argument("--no-profit-pool", action="store_true", help="Desactivar reinversión de ganancias (modo clásico)")
    parser.add_argument("--interval-minutes", type=int, default=20, help="Cada cuántos minutos 'revisa' el precio el bot simulado, igual que el parámetro --interval del bot real (default: 20)")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("Error: No se encontraron las credenciales en el archivo .env")
        return

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")

    print(f"Iniciando simulación (Backtest) de {symbol}…")
    print(f"Rango: {date_start.strftime('%Y-%m-%d')} al {date_end.strftime('%Y-%m-%d')}")

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    print(f"Datos descargados: {len(df_1min)} velas de 1 minuto.")

    interval_minutes = max(1, args.interval_minutes)
    df = df_1min.iloc[::interval_minutes].reset_index(drop=True)
    print(f"Simulando con intervalo de revisión de {interval_minutes} minuto(s): {len(df)} precios evaluados.")

    buy_amount = args.buy_amount
    fee_pct    = args.fee_pct
    use_pool   = not args.no_profit_pool

    run_ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = f"backtest_{symbol}_{run_ts}.log"
    trades_log    = []

    with open(log_file_path, "w", encoding="utf-8") as lf:
        lf.write(f"=== INICIO DE SIMULACIÓN HISTÓRICA {symbol} ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n")
        lf.write(f"Capital Inicial: ${STARTING_CASH:,.2f} | Buy Amount: ${buy_amount:,.2f} | Fee: {fee_pct*100:.3f}% | Pool de ganancias: {'ON' if use_pool else 'OFF'}\n")
        lf.write("=================================================================================\n\n")

        def on_trade(ev):
            trades_log.append(ev)
            t = ev["timestamp"].strftime("%Y-%m-%d %H:%M")
            if ev["type"] in ("BUY_INIT", "BUY_GRID"):
                etiqueta = "COMPRA INICIAL" if ev["type"] == "BUY_INIT" else "COMPRA GRID"
                lf.write(f"[{t}] {etiqueta}: {ev['qty']:.6f} acciones a ${ev['price']:.2f} | Fee: ${ev['fee']:.2f} | Pool: ${ev['pool']:.2f} | Efectivo: ${ev['cash']:,.2f} | Activas: {ev['open_positions']}/{args.max_buys}\n")
            else:
                lf.write(f"[{t}] VENTA LOTE: {ev['qty']:.6f} acciones a ${ev['price']:.2f} (Compra: ${ev['buy_price']:.2f}) | Fee: ${ev['fee']:.2f} | Ganancia neta: ${ev['profit']:+,.2f} | Pool: ${ev['pool']:.2f} | Efectivo: ${ev['cash']:,.2f} | Activas: {ev['open_positions']}/{args.max_buys}\n")

        r = simulate(df, args.max_buys, args.buy_drop_pct, args.sell_rise_pct, fee_pct,
                     use_pool=use_pool, buy_amount=buy_amount,
                     interval_minutes=interval_minutes, on_trade=on_trade)

        bh               = buy_hold_roi(df_1min)
        final_price      = float(df.iloc[-1]["close"])
        remaining_shares = sum(p["qty"] for p in r["state"]["purchases"])
        holdings_value   = remaining_shares * final_price
        cash             = r["state"]["cash"]

        resumen = [
            "",
            "=================================================================================",
            "                             RESULTADOS FINALES                                  ",
            "=================================================================================",
            f"Período:             {df.iloc[0]['timestamp'].strftime('%Y-%m-%d')} a {df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}",
            f"Precio Inicial {symbol}: ${df.iloc[0]['close']:.2f}",
            f"Precio Final {symbol}:   ${final_price:.2f}",
            f"Capital Inicial:     ${STARTING_CASH:,.2f}",
            f"Efectivo Final:      ${cash:,.2f}",
            f"Valor de Acciones:   ${holdings_value:,.2f} ({remaining_shares:.6f} acciones)",
            f"Capital Final Total: ${r['total_equity']:,.2f}",
            f"Ganancia/Pérdida:    ${r['profit']:+,.2f}",
            f"Retorno (ROI):       {r['roi']:+.2f}%",
            f"Drawdown máximo:     {r['max_drawdown_pct']:.2f}%",
            f"Buy & Hold ref.:     {bh['roi']:+.2f}%  (maxDD {bh['max_drawdown_pct']:.2f}%)",
            f"Fee por operación:   {fee_pct*100:.3f}%",
            f"Total Fees Pagados:  ${r['total_fees']:,.2f}",
            f"Total de Compras:    {r['buys']}",
            f"Total de Ventas:     {r['sells']}",
            f"Compras Activas:     {r['open_positions']}/{args.max_buys}",
            f"Pool de ganancias:   ${r['state']['profit_pool']:,.2f} ({'ON' if use_pool else 'OFF'})",
            "=================================================================================",
        ]
        lf.write("\n".join(resumen) + "\n")

    print("\n".join(resumen))
    print(f"Detalle completo guardado en: {log_file_path}")

    print("\nÚltimas 10 transacciones registradas:")
    for t in trades_log[-10:]:
        date_str = t["timestamp"].strftime("%Y-%m-%d %H:%M")
        if t["type"] in ("BUY_INIT", "BUY_GRID"):
            print(f"[{date_str}] COMPRA - Precio: ${t['price']:.2f} | Acciones: {t['qty']:.4f} | Efectivo: ${t['cash']:,.2f}")
        else:
            print(f"[{date_str}] VENTA  - Compra: ${t['buy_price']:.2f} -> Venta: ${t['price']:.2f} | Ganancia: ${t['profit']:+,.2f} | Efectivo: ${t['cash']:,.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Correr la suite (regresión)**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS (backtest no tiene tests, pero la suite confirma que `optimize.py` no se rompió).

- [ ] **Step 4: Verificación de equivalencia**

Repetir EXACTAMENTE el comando del Step 1 y comparar contra `backtest_baseline.txt`: `Retorno (ROI)`, `Capital Final Total`, `Total de Compras`, `Total de Ventas` y `Compras Activas` deben coincidir dígito a dígito. (Las líneas nuevas `Drawdown máximo` y `Buy & Hold ref.` son agregados esperados.) Si algún número difiere, NO commitear: investigar la divergencia (la lógica de `simulate()` y la del viejo loop deberían ser idénticas).

- [ ] **Step 5: Commit**

```bash
git add backtest.py
git commit -m "refactor: backtest.py como wrapper de simulate() con on_trade"
```

---

### Task 7: `can_buy()` + mecanismo de cooldown temporal

**Files:**
- Modify: `optimize.py` (`can_buy()` nueva, `new_state()`, `simulate()`)
- Test: `test_walk_forward.py`

**Interfaces:**
- Produces:
  - `can_buy(purchases, max_buys, price, *, frozen=False, cooldown_remaining_min=0.0, reserved_slots=0, deep_drop_pct=0.0) -> bool` (module-level en `optimize.py`; Tasks 8 y 9 la extienden — la firma completa se define ya).
  - `simulate(..., cooldown_minutes=0.0)`; `new_state()` gana `"cooldown_remaining_min": 0.0`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `test_walk_forward.py` (y sumar `can_buy` al import de `optimize`):

```python
# ---------------------------------------------------------------------------
# Mecanismo anti-crash: cooldown temporal (spec fase 2.2)
# ---------------------------------------------------------------------------

def test_can_buy_cooldown_y_pila():
    compras = [{"price": 100.0, "qty": 1.0}]
    assert can_buy(compras, 10, 90.0)
    assert not can_buy(compras, 10, 90.0, cooldown_remaining_min=1.0)
    assert can_buy([], 10, 90.0, cooldown_remaining_min=5.0)   # compra inicial ignora cooldown
    assert not can_buy(compras * 10, 10, 90.0)                 # pila llena


def test_cooldown_bloquea_compras_hasta_vencer():
    # interval=1, cooldown=2 min, drop 5% rise 5%, [100, 94, 88, 83, 78]:
    #   sin cooldown compra en cada vela (5 compras).
    #   con cooldown: init@100, grid@94 (arranca cooldown=2),
    #   88 bloqueada (resta 1), 83 compra (resta 0), 78 bloqueada (resta 1).
    df = make_df([100, 94, 88, 83, 78])
    sin = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0)
    con = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2)
    assert sin["buys"] == 5
    assert con["buys"] == 3
    assert [p["price"] for p in con["state"]["purchases"]] == [100.0, 94.0, 83.0]


def test_cooldown_persiste_entre_llamadas_encadenadas():
    df = make_df([100, 94, 88, 83, 78])
    a = df.iloc[:3].reset_index(drop=True)
    b = df.iloc[3:].reset_index(drop=True)
    completo = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2)
    r1 = simulate(a, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2)
    r2 = simulate(b, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=2, state=r1["state"])
    assert r2["buys"] == completo["buys"] == 3
    assert r2["total_equity"] == pytest.approx(completo["total_equity"])


def test_cooldown_no_afecta_compra_inicial_ni_ventas():
    # init@100, grid@94 (cooldown=10 arranca), 99>=98.7 vende lote de 94,
    # 105.1>=105 vende lote de 100 (pila vacía), re-pivot @100 pese al
    # cooldown vigente. Ventas nunca bloqueadas, re-pivot tampoco.
    df = make_df([100, 94, 99, 105.1, 100])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, cooldown_minutes=10)
    assert r["buys"] == 3
    assert r["sells"] == 2
    assert r["open_positions"] == 1
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -k cooldown -v`
Expected: FAIL (`ImportError: cannot import name 'can_buy'` / `TypeError` por kwarg desconocido).

- [ ] **Step 3: Implementar en `optimize.py`**

3a. Función nueva arriba de `simulate()` (firma completa ya con los parámetros de Tasks 8-9; los chequeos de `frozen` y slots reservados quedan implementados ahora — son triviales y sus tests llegan en las tasks siguientes):

```python
def can_buy(purchases: list, max_buys: int, price: float, *,
            frozen: bool = False,
            cooldown_remaining_min: float = 0.0,
            reserved_slots: int = 0,
            deep_drop_pct: float = 0.0) -> bool:
    """Decide si una compra está permitida dados los mecanismos anti-crash.
    Con todos los mecanismos apagados replica las reglas actuales del grid.
    La compra inicial (pila vacía) solo puede bloquearla el breaker."""
    if frozen:
        return False
    if len(purchases) == 0:
        return True
    if len(purchases) >= max_buys:
        return False
    if cooldown_remaining_min > 0:
        return False
    if reserved_slots > 0 and len(purchases) >= max_buys - reserved_slots:
        pivot = purchases[0]["price"]
        if price > pivot * (1.0 - deep_drop_pct):
            return False
    return True
```

3b. `new_state()` gana la clave `"cooldown_remaining_min": 0.0` (y el dict `state` devuelto por `simulate()` también).

3c. `simulate()`: firma gana `cooldown_minutes: float = 0.0`. Al inicio: `cooldown_remaining = state.get("cooldown_remaining_min", 0.0)`. En el loop:

- Primera línea del cuerpo del loop (antes de decidir acciones):

```python
        if cooldown_remaining > 0:
            cooldown_remaining = max(0.0, cooldown_remaining - interval_minutes)
```

- El guard de la compra grid `if len(purchases) < max_buys:` se reemplaza por:

```python
            if price <= buy_target:
                if can_buy(purchases, max_buys, price,
                           cooldown_remaining_min=cooldown_remaining):
```

  y dentro del bloque de compra grid, al final (tras `total_buys += 1` y el evento `on_trade`):

```python
                    if cooldown_minutes > 0:
                        cooldown_remaining = float(cooldown_minutes)
```

- La compra inicial NO consulta ni setea el cooldown (queda como está).

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos (incluidos regresión ZIGZAG y `no_muta_el_state` — `new_state()` cambió, pero el test compara contra `new_state()` fresco, sigue válido).

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "feat: can_buy() y mecanismo de cooldown temporal en simulate()"
```

---

### Task 8: Mecanismo de slots reservados por profundidad

**Files:**
- Modify: `optimize.py` (`simulate()`: firma y llamada a `can_buy()`)
- Test: `test_walk_forward.py`

**Interfaces:**
- Consumes: `can_buy()` con `reserved_slots`/`deep_drop_pct` (ya implementado en Task 7).
- Produces: `simulate(..., reserved_slots=0, deep_drop_pct=0.0)`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# ---------------------------------------------------------------------------
# Mecanismo anti-crash: slots reservados por profundidad (spec fase 2.3)
# ---------------------------------------------------------------------------

def test_can_buy_slots_reservados():
    # pivot=100, max_buys=3, reserved=1: el 3er slot exige caída >= 20%
    compras = [{"price": 100.0, "qty": 1.0}, {"price": 95.0, "qty": 1.0}]
    assert not can_buy(compras, 3, 85.0, reserved_slots=1, deep_drop_pct=0.20)  # 85 > 80
    assert can_buy(compras, 3, 79.0, reserved_slots=1, deep_drop_pct=0.20)      # 79 <= 80
    # con un solo lote (slot no reservado) compra normal
    assert can_buy(compras[:1], 3, 94.0, reserved_slots=1, deep_drop_pct=0.20)


def test_slots_reservados_frenan_sin_caida_profunda():
    # max_buys=3, reserved=1, deep=20% (umbral: pivot 100 -> 80),
    # drop 5%, rise 99% (sin ventas), [100, 95, 90, 85, 79]:
    #   sin mecanismo: compra 100, 95, 90 (pila llena en 3).
    #   con mecanismo: compra 100, 95; 90 y 85 bloqueadas (> 80); 79 compra.
    df = make_df([100, 95, 90, 85, 79])
    sin = simulate(df, 3, 0.05, 0.99, 0.0)
    con = simulate(df, 3, 0.05, 0.99, 0.0, reserved_slots=1, deep_drop_pct=0.20)
    assert [p["price"] for p in sin["state"]["purchases"]] == [100.0, 95.0, 90.0]
    assert [p["price"] for p in con["state"]["purchases"]] == [100.0, 95.0, 79.0]


def test_slots_reservados_pivot_se_renueva_tras_vaciar_pila():
    # [100, 95, 105, 105.5, 100, 95, 90, 79], drop 5% rise 5%,
    # max_buys=3, reserved=1, deep=20%:
    #   init@100, grid@95, 105>=99.75 vende lote 95, 105.5>=105 vende lote
    #   100 (pila vacía), re-pivot init@100, grid@95, 90 bloqueada
    #   (nuevo pivot 100 -> umbral 80), 79 compra (slot reservado).
    df = make_df([100, 95, 105, 105.5, 100, 95, 90, 79])
    r = simulate(df, 3, 0.05, 0.05, 0.0, reserved_slots=1, deep_drop_pct=0.20)
    assert r["buys"] == 5
    assert r["sells"] == 2
    assert [p["price"] for p in r["state"]["purchases"]] == [100.0, 95.0, 79.0]
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -k reservados -v`
Expected: `test_can_buy_slots_reservados` PASA (la lógica ya está en `can_buy` desde Task 7); los dos de `simulate` FALLAN con `TypeError: simulate() got an unexpected keyword argument 'reserved_slots'`.

- [ ] **Step 3: Implementar**

`simulate()`: firma gana `reserved_slots: int = 0, deep_drop_pct: float = 0.0`, y la llamada a `can_buy()` en la compra grid pasa a:

```python
                if can_buy(purchases, max_buys, price,
                           cooldown_remaining_min=cooldown_remaining,
                           reserved_slots=reserved_slots,
                           deep_drop_pct=deep_drop_pct):
```

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "feat: mecanismo de slots reservados por profundidad"
```

---

### Task 9: Mecanismo de circuit breaker congelador

**Files:**
- Modify: `optimize.py` (`new_state()`, `simulate()`)
- Test: `test_walk_forward.py`

**Interfaces:**
- Consumes: tracking de equity/pico de Task 1; `can_buy(frozen=...)` de Task 7.
- Produces: `simulate(..., breaker_dd_pct=0.0)`; `new_state()` y el state devuelto ganan `"frozen": False`.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# ---------------------------------------------------------------------------
# Mecanismo anti-crash: circuit breaker congelador (spec fase 2.4)
# ---------------------------------------------------------------------------

def _estado_cargado():
    # 9 lotes @100 (900 acciones) + 10k cash; pico previo de equity 100k.
    st = new_state()
    st["cash"] = 10_000.0
    st["purchases"] = [
        {"price": 100.0, "qty": 100.0, "buy_fee": 0.0, "effective_buy": 10_000.0}
        for _ in range(9)
    ]
    st["total_buys"] = 9
    st["equity_peak"] = 100_000.0
    return st


def test_breaker_congela_y_descongela_con_histeresis():
    # T=15% (rearme < 7.5%), max_buys=20, drop 5% rise 5%,
    # precios [80, 76, 84, 95, 94] partiendo de _estado_cargado():
    #   bar0 @80: compra grid @80 (cash 0, 1025 acc) -> equity 82000,
    #        dd 18% > 15% -> congela AL FINAL de la vela.
    #   bar1 @76: 76<=76 (target s/80) pero CONGELADO -> bloqueada.
    #        equity 77900 -> max_dd 22.1%.
    #   bar2 @84: venta lote 80 (84>=84) -- las ventas siguen congelado.
    #        equity 10500+900*84=86100, dd 13.9% > 7.5% -> sigue congelado.
    #   bar3 @95: 95<=95 (target s/100) pero sigue congelado en esta vela;
    #        equity 96000, dd 4% < 7.5% -> descongela al final (histéresis:
    #        el cruce surte efecto en la vela siguiente).
    #   bar4 @94: 94<=95 -> compra permitida.
    df = make_df([80, 76, 84, 95, 94])
    r = simulate(df, 20, 0.05, 0.05, 0.0, state=_estado_cargado(), breaker_dd_pct=0.15)
    assert r["buys"] == 11          # 9 previas + @80 + @94
    assert r["sells"] == 1
    assert r["state"]["frozen"] is False
    assert r["max_drawdown_pct"] == pytest.approx(22.1)

    # Sin breaker, la compra de bar1 @76 también entra
    sin = simulate(df, 20, 0.05, 0.05, 0.0, state=_estado_cargado())
    assert sin["buys"] == 12


def test_breaker_bloquea_compra_inicial_congelado():
    # Pila vacía + congelado con dd 50% (>> T/2): el re-pivot también se
    # bloquea mientras el breaker esté activo.
    st = new_state()
    st["cash"] = 50_000.0
    st["equity_peak"] = 100_000.0
    st["frozen"] = True
    df = make_df([100, 100, 100])
    r = simulate(df, MAX_BUYS, 0.05, 0.05, 0.0, state=st, breaker_dd_pct=0.15)
    assert r["buys"] == 0
    assert r["state"]["frozen"] is True
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `python3 -m pytest test_walk_forward.py -k breaker -v`
Expected: FAIL con `TypeError: simulate() got an unexpected keyword argument 'breaker_dd_pct'`.

- [ ] **Step 3: Implementar en `optimize.py`**

3a. `new_state()` gana `"frozen": False` (y el state devuelto por `simulate()` también).

3b. `simulate()`: firma gana `breaker_dd_pct: float = 0.0`. Al inicio:

```python
    frozen = state.get("frozen", False) if breaker_dd_pct > 0 else False
```

(con el breaker apagado, un `frozen` heredado se ignora y se resetea).

3c. La compra inicial queda condicionada al breaker:

```python
        if len(purchases) == 0:
            if not frozen:
                ...bloque de compra inicial sin cambios...
```

3d. La llamada a `can_buy()` de la compra grid suma `frozen=frozen`.

3e. El epílogo por vela (después de actualizar `max_dd`) suma las transiciones:

```python
        if breaker_dd_pct > 0:
            if not frozen and dd > breaker_dd_pct:
                frozen = True
            elif frozen and dd < breaker_dd_pct / 2:
                frozen = False
```

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 5: Commit**

```bash
git add optimize.py test_walk_forward.py
git commit -m "feat: circuit breaker congelador con histéresis en simulate()"
```

---

### Task 10: Flags de mecanismos anti-crash en `backtest.py`

**Files:**
- Modify: `backtest.py` (argparse + llamada a `simulate()`)

**Interfaces:**
- Consumes: `simulate(..., cooldown_minutes, reserved_slots, deep_drop_pct, breaker_dd_pct)` (Tasks 7-9).

- [ ] **Step 1: Implementar**

En el argparse de `backtest.py`, después de `--interval-minutes`:

```python
    parser.add_argument("--cooldown-minutes", type=float, default=0.0, help="Minutos de mercado sin comprar tras cada compra de grid (default: 0 = apagado)")
    parser.add_argument("--reserved-slots",   type=int,   default=0,   help="Slots finales reservados para caídas profundas (default: 0 = apagado)")
    parser.add_argument("--deep-drop-pct",    type=float, default=0.0, help="Caída mínima desde el pivot para usar los slots reservados. Ej: 0.25 = 25%%")
    parser.add_argument("--breaker-dd-pct",   type=float, default=0.0, help="Umbral de drawdown del equity que congela compras (default: 0 = apagado). Ej: 0.15 = 15%%")
```

y la llamada a `simulate()` suma:

```python
                     cooldown_minutes=args.cooldown_minutes,
                     reserved_slots=args.reserved_slots,
                     deep_drop_pct=args.deep_drop_pct,
                     breaker_dd_pct=args.breaker_dd_pct,
```

- [ ] **Step 2: Verificación manual**

Con el mismo caché de la Task 6:

```bash
python3 backtest.py --symbol TSLA --date-start 2025-01-01 --date-end 2026-07-01 \
  --buy-drop-pct 0.01 --sell-rise-pct 0.03 --interval-minutes 20 --breaker-dd-pct 0.15
```

Expected: corre sin errores y el número de compras es ≤ que el de la corrida sin breaker (Task 6 Step 4); sin flags, sigue devolviendo los números del baseline.

- [ ] **Step 3: Correr la suite (regresión)**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 4: Commit**

```bash
git add backtest.py
git commit -m "feat: flags de mecanismos anti-crash en backtest.py"
```

---

### Task 11: `risk_tournament.py`

**Files:**
- Create: `risk_tournament.py`
- Test: `test_walk_forward.py`

**Interfaces:**
- Consumes: `simulate()` con mecanismos (Tasks 7-9), `buy_hold_roi()` (Task 2), `load_bars()`, `MAX_BUYS`.
- Produces: `build_variants() -> list[dict]` con `{"name": str, "params": dict}` (10 variantes, baseline primero). Artefactos: `risktournament_<symbol>_<ts>.log` / `.csv`.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `test_walk_forward.py` (import nuevo: `from risk_tournament import build_variants`):

```python
# ---------------------------------------------------------------------------
# risk_tournament (spec fase 2.6)
# ---------------------------------------------------------------------------

def test_build_variants_matriz_completa():
    vs = build_variants()
    assert len(vs) == 10                       # 1 baseline + 3 cooldown + 4 reserva + 2 breaker
    assert vs[0] == {"name": "baseline", "params": {}}
    names = [v["name"] for v in vs]
    assert len(set(names)) == 10
    params = [v["params"] for v in vs]
    assert {"cooldown_minutes": 390} in params
    assert {"cooldown_minutes": 1950} in params
    assert {"reserved_slots": 2, "deep_drop_pct": 0.20} in params
    assert {"reserved_slots": 3, "deep_drop_pct": 0.30} in params
    assert {"breaker_dd_pct": 0.15} in params
    assert {"breaker_dd_pct": 0.25} in params
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `python3 -m pytest test_walk_forward.py -k build_variants -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'risk_tournament'`.

- [ ] **Step 3: Crear `risk_tournament.py`**

Contenido completo:

```python
import argparse
import os
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

from optimize import MAX_BUYS, buy_hold_roi, load_bars, simulate

SEP  = "=" * 96
SEP2 = "-" * 96


def build_variants() -> list[dict]:
    """Matriz de variantes del torneo (spec 2026-07-06): baseline + cada
    mecanismo aislado. Sin combinaciones en esta versión."""
    variants = [{"name": "baseline", "params": {}}]
    for cd in (390, 780, 1950):
        variants.append({"name": f"cooldown-{cd}min", "params": {"cooldown_minutes": cd}})
    for n in (2, 3):
        for x in (0.20, 0.30):
            variants.append({
                "name": f"reserva-{n}slots-{int(x * 100)}pct",
                "params": {"reserved_slots": n, "deep_drop_pct": x},
            })
    for t in (0.15, 0.25):
        variants.append({"name": f"breaker-{int(t * 100)}pct", "params": {"breaker_dd_pct": t}})
    return variants


def main():
    parser = argparse.ArgumentParser(description="Torneo de mecanismos anti-crash sobre datos históricos")
    parser.add_argument("--symbol",        type=str,   default="TSLA")
    parser.add_argument("--date-start",    type=str,   default="2024-07-01")
    parser.add_argument("--date-end",      type=str,   default="2026-07-01")
    parser.add_argument("--buy-drop-pct",  type=float, default=0.01)
    parser.add_argument("--sell-rise-pct", type=float, default=0.03)
    parser.add_argument("--interval",      type=int,   default=20, help="Intervalo de revisión en minutos (un solo valor)")
    parser.add_argument("--buy-amount",    type=float, default=10_000.0)
    parser.add_argument("--fee-pct",       type=float, default=0.0)
    parser.add_argument("--no-profit-pool", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    symbol     = args.symbol.upper()
    date_start = datetime.strptime(args.date_start, "%Y-%m-%d")
    date_end   = datetime.strptime(args.date_end,   "%Y-%m-%d")
    interval   = max(1, args.interval)
    use_pool   = not args.no_profit_pool

    df_1min = load_bars(symbol, date_start, date_end, api_key, secret_key)
    df = df_1min.iloc[::interval].reset_index(drop=True)
    print(f"Velas de 1 minuto: {len(df_1min)} | evaluadas al intervalo de {interval} min: {len(df)}\n")

    filas = []
    for v in build_variants():
        r = simulate(df, MAX_BUYS, args.buy_drop_pct, args.sell_rise_pct, args.fee_pct,
                     use_pool=use_pool, buy_amount=args.buy_amount,
                     interval_minutes=interval, **v["params"])
        filas.append({"name": v["name"], "params": v["params"], **{
            k: r[k] for k in ("roi", "max_drawdown_pct", "profit", "total_equity",
                              "buys", "sells", "open_positions", "total_fees")
        }})

    bh = buy_hold_roi(df_1min)
    baseline = filas[0]

    lines = [
        SEP,
        f"  RISK TOURNAMENT {symbol} — Ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        SEP,
        f"  Rango: {df_1min.iloc[0]['timestamp'].strftime('%Y-%m-%d')} → {df_1min.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}"
        f"   |   intervalo: {interval} min   |   drop {args.buy_drop_pct*100:.0f}% / rise {args.sell_rise_pct*100:.0f}%",
        f"  Monto por compra: ${args.buy_amount:,.0f}   |   fee: {args.fee_pct*100:.3f}%   |   pool: {'ON' if use_pool else 'OFF'}   |   max_buys: {MAX_BUYS}",
        SEP,
        "",
        "  RESULTADOS POR VARIANTE",
        SEP2,
        f"  {'variante':<24}  {'ROI%':>8}  {'maxDD%':>7}  {'Ganancia':>12}  {'Compras':>7}  {'Ventas':>6}  {'Open':>5}  {'Fees':>10}",
        SEP2,
    ]
    for f in filas:
        lines.append(
            f"  {f['name']:<24}  {f['roi']:>+8.2f}  {f['max_drawdown_pct']:>7.2f}  ${f['profit']:>+11,.0f}  "
            f"{f['buys']:>7}  {f['sells']:>6}  {f['open_positions']:>5}  ${f['total_fees']:>9,.0f}"
        )
    lines.append(
        f"  {'buy-hold (ref.)':<24}  {bh['roi']:>+8.2f}  {bh['max_drawdown_pct']:>7.2f}  ${bh['profit']:>+11,.0f}  "
        f"{'—':>7}  {'—':>6}  {'—':>5}  {'—':>10}"
    )
    lines += [
        SEP2,
        "",
        "  DELTAS vs BASELINE (ΔmaxDD positivo = protege; ΔROI positivo = gana más)",
        SEP2,
        f"  {'variante':<24}  {'ΔROI(pp)':>9}  {'ΔmaxDD(pp)':>11}",
        SEP2,
    ]
    for f in filas[1:]:
        d_roi = f["roi"] - baseline["roi"]
        d_dd  = baseline["max_drawdown_pct"] - f["max_drawdown_pct"]
        lines.append(f"  {f['name']:<24}  {d_roi:>+9.2f}  {d_dd:>+11.2f}")
    lines += [SEP2, "", f"  Baseline: ROI {baseline['roi']:+.2f}% / maxDD {baseline['max_drawdown_pct']:.2f}%"
              f"   |   Buy & hold: ROI {bh['roi']:+.2f}% / maxDD {bh['max_drawdown_pct']:.2f}%", SEP]

    print("\n".join(lines))

    run_ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"risktournament_{symbol}_{run_ts}.log"
    csv_path = f"risktournament_{symbol}_{run_ts}.csv"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    pd.DataFrame(filas).to_csv(csv_path, index=False)
    print(f"\nLog guardado en  : {log_path}")
    print(f"CSV guardado en  : {csv_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr la suite completa**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 5: Smoke test con caché existente**

```bash
python3 risk_tournament.py --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03
```

Expected: tabla con 11 filas (10 variantes + buy-hold), sección de deltas, y archivos `risktournament_SPCX_*.log/.csv` generados. Verificar a ojo: `baseline` reproduce un ROI negativo (SPCX perdió plata según la bitácora) y al menos alguna variante muestra ΔmaxDD > 0.

- [ ] **Step 6: Commit**

```bash
git add risk_tournament.py test_walk_forward.py
git commit -m "feat: risk_tournament.py — torneo de mecanismos anti-crash"
```

---

### Task 12: Corridas en 4 símbolos, bitácora, README y .gitignore

**Files:**
- Modify: `docs/walk-forward-log.md`, `README.md`, `.gitignore`

**Interfaces:**
- Consumes: `risk_tournament.py` (Task 11) y los cachés `.pkl` existentes.

- [ ] **Step 1: Correr el torneo en los 4 símbolos**

```bash
python3 risk_tournament.py --symbol TSLA --date-start 2024-07-01 --date-end 2026-07-01
python3 risk_tournament.py --symbol NVDA --date-start 2024-07-01 --date-end 2026-07-01
python3 risk_tournament.py --symbol MSFT --date-start 2024-07-01 --date-end 2026-07-01
python3 risk_tournament.py --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03
```

(Todos con cachés existentes; si alguno falta, `load_bars()` descarga con las credenciales de `.env`.) Guardar los nombres de los artefactos generados.

- [ ] **Step 2: Escribir la entrada en `docs/walk-forward-log.md`**

Agregar la entrada `### 9. Torneo de mecanismos anti-crash (risk_tournament.py) — 4 símbolos` después de la entrada #8, con: tabla por símbolo (baseline / mejor variante por ΔmaxDD / buy-hold, con ROI y maxDD **reales de las corridas**), y la interpretación según el criterio acordado: *¿qué mecanismo reduce más el maxDD en SPCX/MSFT costando pocos pp de ROI en TSLA/NVDA?* Cerrar con el veredicto provisorio (qué variante candidatea para `tradebot.py`, o si ninguna convence) y agregar las filas correspondientes en la tabla "Historial de corridas". Actualizar también la sección "Próximos pasos sugeridos": marcar hecho el ítem del mecanismo de freno y agregar el próximo paso que corresponda según los resultados.

- [ ] **Step 3: Actualizar `README.md`**

- En "Estructura de Archivos", después de la entrada de `walk_forward.py`, agregar:

```markdown
* **`risk_tournament.py`** — Torneo de mecanismos anti-crash: compara sobre datos históricos el grid sin frenos contra tres mecanismos que limitan compras durante desplomes (cooldown temporal, slots reservados por profundidad, circuit breaker que congela compras), con buy & hold como referencia. Reporta ROI, drawdown máximo y los deltas contra baseline por variante. Ver `docs/superpowers/specs/2026-07-06-risk-mechanisms-design.md` (diseño) y `docs/walk-forward-log.md` (resultados).
```

- En la sección "Cómo Ejecutar Cada Script", agregar un bloque para `risk_tournament.py` con el comando de ejemplo del Step 1 y una línea explicando los flags de mecanismos nuevos de `backtest.py` (`--cooldown-minutes`, `--reserved-slots`, `--deep-drop-pct`, `--breaker-dd-pct`).
- En "Hallazgos hasta ahora", agregar un bullet con el resultado del torneo (con los números reales) y actualizar la lista de archivos generados en runtime para incluir `risktournament_*.csv`/`.log`.

- [ ] **Step 4: Actualizar `.gitignore`**

Agregar al final:

```
walkforward_*.csv
risktournament_*.csv
__pycache__/
```

(los `.log` ya los cubre `*.log`).

- [ ] **Step 5: Correr la suite completa una última vez**

Run: `python3 -m pytest test_walk_forward.py -v`
Expected: PASS todos.

- [ ] **Step 6: Commit**

```bash
git add docs/walk-forward-log.md README.md .gitignore
git commit -m "docs: resultados del torneo anti-crash en 4 símbolos + README"
```
