# Benchmark buy & hold y mecanismos anti-crash — Diseño

**Fecha:** 2026-07-06
**Proyecto:** `invertirCarlos/alpaca`
**Branch:** `feature/risk-mechanisms`
**Objetivo:** (1) agregar la referencia de buy & hold y la métrica de drawdown máximo a las herramientas de análisis, para saber si el grid le gana a "no hacer nada"; (2) diseñar y medir con datos históricos tres mecanismos que frenen la acumulación de compras durante un crash (el riesgo dominante identificado en la bitácora: SPCX y MSFT perdieron plata con cualquier parámetro del grid — ver `docs/walk-forward-log.md`, conclusión #2), antes de tocar el bot en vivo.

## Contexto

- La estrategia es **a largo plazo**: la compra inicial al arrancar el grid actúa como pivot. La preocupación del usuario son los crashes: el bot gasta los 10 slots de compra en una caída corta y el precio sigue cayendo sin munición ni freno.
- `simulate()` en `optimize.py` es el motor compartido (lo usan `optimize.py` y `walk_forward.py`); `backtest.py` duplica la misma lógica línea por línea con I/O de log agregado.
- El ROI de `simulate()` ya valúa las posiciones abiertas al precio final (`total_equity = cash + holdings`), pero **no** trackea el drawdown intermedio — sin eso no se puede medir cuánto protege un freno.
- Criterio de éxito acordado: **proteger sin costo excesivo** — gana el mecanismo que más reduce la pérdida/drawdown en SPCX/MSFT sin sacrificar más que unos pocos pp de ROI en TSLA/NVDA. Decisión balanceada mirando ROI y maxDD en los 4 símbolos.
- Restricción de diseño: **nunca liquidar posiciones automáticamente** — los mecanismos solo bloquean compras; las ventas del grid siguen operando siempre.

## Alcance

Dos fases dentro de este spec:

- **Fase 1 — Medición:** `buy_hold_roi()`, drawdown máximo en `simulate()`, fila B&H y columna maxDD en el torneo de `walk_forward.py`, línea B&H en los reportes de `backtest.py` y `optimize.py`.
- **Fase 2 — Mecanismos anti-crash:** tres mecanismos opcionales en `simulate()` (cooldown temporal, slots reservados por profundidad, circuit breaker congelador), script nuevo `risk_tournament.py` que los compara por símbolo, y refactor de `backtest.py` para eliminar la lógica duplicada.

**Fuera de alcance (explícito):** llevar el mecanismo ganador a `tradebot.py`. Eso es un ciclo propio (spec + plan) una vez que el torneo diga cuál mecanismo y con qué parámetros; incluye estado persistente nuevo y variables de `.env`.

---

## Fase 1 — Benchmark buy & hold y drawdown máximo

### 1.1 `buy_hold_roi(df, starting_cash=STARTING_CASH)` en `optimize.py`

Invierte todo el capital al primer `close` del DataFrame recibido y valúa al último. Devuelve un dict con el mismo formato de claves que `simulate()` para poder mezclarlo en tablas: `{"roi", "profit", "total_equity", "max_drawdown_pct"}`. El maxDD del B&H se calcula sobre la misma serie de closes (pico de equity vs valle posterior). No cobra fees (es una referencia teórica; con fee 0 default en todo el proyecto, es consistente).

### 1.2 Drawdown máximo en `simulate()`

En cada vela, después de procesar la acción del grid, se calcula `equity = cash + sum(qty) * price`, se actualiza el pico histórico y el drawdown máximo:

```
equity_peak = max(equity_peak, equity)
max_dd      = max(max_dd, (equity_peak - equity) / equity_peak)
```

- Se agrega `"max_drawdown_pct"` (en %, positivo) al dict de resultado.
- **Continuidad en `simulate_adaptive`:** `equity_peak` y `max_dd` se guardan en el dict `state` (los inicializa `new_state()`), así el drawdown se acumula correctamente a través de períodos encadenados en el torneo del walk-forward. Estados viejos sin esas claves se toleran con `.get()` y defaults (peak = equity actual, dd = 0).
- Costo: `sum()` sobre ≤10 posiciones por vela — despreciable.
- Sin mecanismos activados, ROI/compras/ventas devuelven **exactamente** lo mismo que hoy (test de regresión obligatorio).

### 1.3 `walk_forward.py`: fila B&H, columna maxDD y veredicto

- El torneo suma la fila **`buy-hold`**, calculada **solo sobre los períodos de aplicación** (concatenación de los `app_dfs`): primer close del primer período de aplicación → último close del último. Mismo rango que las otras estrategias; comparable.
- La tabla del torneo gana la columna **`maxDD%`** para las 5 filas.
- El veredicto menciona explícitamente la comparación contra B&H. Si la mejor estrategia realista pierde contra B&H, el veredicto lo dice sin vueltas ("el grid no agrega valor sobre buy & hold en este símbolo/rango").
- El CSV de salida no cambia de esquema (es el historial de óptimos por período, no del torneo).

### 1.4 Reportes de `backtest.py` y `optimize.py`

Ambos resúmenes agregan una línea de referencia B&H calculada sobre el rango completo analizado, junto al precio inicial/final que ya imprimen. `backtest.py` además muestra el maxDD de la corrida.

---

## Fase 2 — Mecanismos anti-crash

### 2.1 Parámetros nuevos de `simulate()`

```python
def simulate(df, max_buys, buy_drop_pct, sell_rise_pct, fee_pct,
             use_pool=True, buy_amount=BUY_AMOUNT, interval_minutes=1,
             state=None,
             cooldown_minutes=0,      # 0 = apagado
             reserved_slots=0,        # 0 = apagado
             deep_drop_pct=0.0,       #   (umbral de los slots reservados)
             breaker_dd_pct=0.0,      # 0 = apagado
             on_trade=None) -> dict
```

Todos los mecanismos apagados por default → comportamiento idéntico al actual. La decisión de compra se concentra en un helper module-level testeable:

```python
def can_buy(purchases, max_buys, price, state, cooldown_minutes,
            reserved_slots, deep_drop_pct, breaker_dd_pct) -> bool
```

que aplica los tres chequeos (cualquiera que bloquee, bloquea). Las **ventas nunca se bloquean**.

### 2.2 Cooldown temporal (`cooldown_minutes`)

Después de cada compra **de grid** (no la inicial que abre el grid y fija el pivot), no se permite otra compra hasta que pasen N minutos *de mercado*.

- Implementación: contador `cooldown_remaining_min` en `state`. Cada vela procesada lo decrementa en `interval_minutes`; una compra de grid lo resetea a `cooldown_minutes`. Compra de grid permitida solo si `<= 0`. Como las velas solo existen en horario de mercado, noches y findes no consumen cooldown — y el contador en minutos (no en velas) sobrevive cambios de intervalo entre períodos encadenados.
- La compra inicial (pila vacía, fija el pivot) **ni consulta ni activa** el cooldown: si se vendió todo durante un cooldown vigente, el re-pivot puede ejecutarse igual.
- Valores del torneo: **390 (1 día de mercado), 780 (2 días), 1950 (1 semana)**.

### 2.3 Slots reservados por profundidad (`reserved_slots` + `deep_drop_pct`)

Los últimos `reserved_slots` de los `max_buys` solo se habilitan si el precio actual cayó al menos `deep_drop_pct` respecto del **pivot** (el precio de la primera compra de la pila actual, `purchases[0]["price"]`).

- Con `len(purchases) < max_buys - reserved_slots`: compra normal.
- Con `len(purchases) >= max_buys - reserved_slots`: se exige además `price <= pivot * (1 - deep_drop_pct)`.
- Si la pila se vacía (todo vendido), la próxima compra inicial fija un pivot nuevo.
- Valores del torneo: **N ∈ {2, 3} × X ∈ {20%, 30%}** (4 combinaciones).

### 2.4 Circuit breaker congelador (`breaker_dd_pct`)

Si el drawdown del equity desde su pico supera el umbral T, se congelan **todas** las compras (incluida la inicial/re-pivot si la pila quedó vacía). Las ventas siguen operando.

- Histéresis para no titilar en el borde: congelado se pasa a `frozen=True` cuando `dd > T`; se descongela recién cuando `dd < T/2`.
- `frozen` vive en `state` (persiste entre períodos encadenados).
- Orden dentro de la vela: primero se decide/ejecuta la acción del grid con el estado `frozen` vigente, después se recalcula equity/drawdown y se actualiza `frozen` — es decir, un cruce del umbral surte efecto a partir de la vela siguiente. Evita ambigüedad sobre la vela exacta del cruce.
- Usa el mismo tracking de equity/pico de la fase 1 — el breaker depende de 1.2.
- Valores del torneo: **T ∈ {15%, 25%}**.

### 2.5 Trade-off explícito

Los tres mecanismos comparten el mismo trade-off: en un crash el bot compra menos veces durante el desplome (protege), pero también compra menos barato si la caída era la oportunidad (cuesta ROI en símbolos que rebotan). El torneo existe para medir cuál mecanismo paga mejor ese trade-off en datos reales — no se asume la respuesta.

### 2.6 `risk_tournament.py` (script nuevo)

```bash
python risk_tournament.py --symbol MSFT --date-start 2024-07-01 --date-end 2026-07-01 \
  --buy-drop-pct 0.01 --sell-rise-pct 0.03 --interval 20
```

- **CLI:** `--symbol`, `--date-start`, `--date-end`, `--buy-drop-pct` (default 0.01), `--sell-rise-pct` (default 0.03), `--interval` (default 20, un solo valor), `--buy-amount`, `--fee-pct`, `--no-profit-pool` — mismos nombres y defaults que los otros scripts donde aplique.
- **Datos:** `load_bars()` (reutiliza los cachés `.pkl` existentes de TSLA/NVDA/MSFT 2 años y SPCX; sin red si el caché existe).
- **Matriz de variantes (11 simulaciones por símbolo):**

| Variante | Configuraciones |
|---|---|
| Baseline (sin freno) | 1 |
| Cooldown | 390 / 780 / 1950 min → 3 |
| Slots reservados | {2,3} × {20%,30%} → 4 |
| Circuit breaker | 15% / 25% → 2 |
| Buy & hold (referencia) | 1 |

- **Reporte (`risktournament_<symbol>_<timestamp>.log` + `.csv`):** tabla con variante, parámetros, ROI%, maxDD%, compras/ventas/abiertas/fees; y una sección comparativa con **ΔROI y ΔmaxDD contra el baseline** por variante — el insumo directo del criterio "proteger sin costo excesivo".
- **Sin veredicto automático:** el criterio es multi-símbolo (hay que mirar TSLA+NVDA+MSFT+SPCX juntos); la interpretación va a `docs/walk-forward-log.md`, como todas las corridas.
- **Sin combinaciones de mecanismos** en esta versión: primero se mide cada uno aislado; si dos ayudan, la combinación es una iteración posterior.

### 2.7 Refactor de `backtest.py`

`backtest.py` pasa a ser un wrapper fino de `simulate()`:

- Parsea flags (sin cambios de CLI), carga datos con `load_bars()` (elimina su copia de la descarga/caché), llama a `simulate()` una vez y arma el resumen final desde el dict de resultado.
- Para conservar el log detallado por operación, `simulate()` acepta un callback opcional `on_trade(evento)` donde `evento` es un dict `{"type": "BUY_INIT"|"BUY_GRID"|"SELL", "price", "qty", "fee", "cash", "profit" (solo SELL), "timestamp", "open_positions", "pool"}`. `backtest.py` lo usa para escribir sus líneas de log; `optimize.py` y `walk_forward.py` no lo pasan y no cambian.
- `simulate()` lee `row["timestamp"]` además de `row["close"]` (la columna ya existe en todos los DataFrames del proyecto) para poblar los eventos.
- `backtest.py` gana los flags de los mecanismos anti-crash (`--cooldown-minutes`, `--reserved-slots`, `--deep-drop-pct`, `--breaker-dd-pct`, defaults apagados), lo que permite inspeccionar trade por trade el comportamiento de un mecanismo.
- **Verificación de equivalencia del refactor:** correr `backtest.py` sobre TSLA con parámetros idénticos antes y después; ROI, capital final, compras y ventas deben coincidir exactamente.

---

## Testing (`test_walk_forward.py`, datos sintéticos, sin red)

**Fase 1:**
- `buy_hold_roi`: serie que sube X% → ROI = X%; serie con caída conocida → maxDD esperado.
- `simulate()` maxDD: serie sintética con pico y valle conocidos → maxDD correcto; encadenado vía `state` acumula el drawdown entre llamadas.
- Regresión: `simulate()` con mecanismos apagados devuelve mismos ROI/compras/ventas que antes del cambio (se fija con un caso sintético con valores esperados hardcodeados).
- Estados viejos sin las claves nuevas no rompen (`.get()` con defaults).

**Fase 2:**
- `can_buy`: testeable directo, casos por mecanismo y combinados.
- Cooldown: caída rápida → compra solo al vencer el cooldown; el contador persiste entre llamadas encadenadas; la compra inicial no lo activa.
- Slots reservados: caída que no llega al umbral → la pila se frena en `max_buys - N`; caída profunda → usa los 10; pila vaciada → pivot nuevo.
- Breaker: congela al cruzar T; no compra congelado (tampoco compra inicial); ventas funcionan congelado; se rearma recién bajo T/2 (histéresis, no a T).
- `on_trade`: recibe los eventos esperados en orden para una serie sintética conocida.

## Documentación

- `README.md`: `risk_tournament.py` en la estructura de archivos, flags nuevos de `backtest.py`, sección de hallazgos cuando haya resultados del torneo.
- `docs/walk-forward-log.md`: entrada nueva con los resultados del torneo en los 4 símbolos (TSLA, NVDA, MSFT 2 años; SPCX el rango disponible) y la interpretación según el criterio acordado.
- Artefactos generados (`risktournament_*.log/.csv`) se agregan al `.gitignore` si el patrón existente no los cubre.

## Orden de implementación sugerido

1. Fase 1 completa (es autocontenida y ya da valor: re-leer los hallazgos existentes contra B&H).
2. Refactor de `backtest.py` (con verificación de equivalencia) — antes de los mecanismos, para que nazcan en un solo lugar.
3. Mecanismos en `simulate()` + `can_buy()` + tests.
4. `risk_tournament.py` + corridas en los 4 símbolos + entrada en la bitácora.
