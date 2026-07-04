# Walk-Forward Optimization — Diseño

**Fecha:** 2026-07-04
**Proyecto:** `invertirCarlos/alpaca`
**Objetivo:** Validar con datos históricos si auto-ajustar `buy_drop_pct`, `sell_rise_pct` e intervalo de consulta semana a semana (idea del bot adaptativo) le gana a usar parámetros fijos, antes de construir el sistema en vivo.

## Contexto

La estrategia grid actual (`tradebot.py` / `backtest.py` / `optimize.py`): compra `BUY_AMOUNT` cuando el precio cae `buy_drop_pct` desde la última compra, vende la última posición cuando sube `sell_rise_pct`, máximo 10 posiciones, con pool de reinversión de ganancias. `optimize.py` hace grid search de drop (1–10%) × rise (1–10%) × intervalos sobre velas de 1 minuto cacheadas.

El problema: el óptimo varía mes a mes / semana a semana. La hipótesis a validar: "el óptimo de la semana anterior sirve para configurar la semana siguiente" (persistencia). Si los óptimos fluctúan al azar alrededor de un centro estable (sin autocorrelación), el auto-ajuste no suma y conviene un parámetro fijo robusto.

## Entregable

Un script nuevo `walk_forward.py` (mismo estilo CLI que `optimize.py`) + test `test_walk_forward.py`.

## CLI

Flags heredados de `optimize.py` con los mismos defaults: `--symbol` (TSLA), `--date-start` (2026-01-01), `--date-end` (2026-06-28), `--buy-amount` (10000), `--fee-pct` (0.0), `--no-profit-pool`, `--intervals` (default `20`).

Nuevos:

- `--train-weeks N` (default `1`): cuántas semanas previas concatenadas se usan para optimizar antes de aplicar a la semana siguiente. Default 1 = test puro de persistencia semana-a-semana.

Usa el mismo caché `cache_{symbol}_{start}_{end}_1Min.pkl` y la misma descarga de Alpaca si no existe.

## Refactor mínimo de `optimize.py`

`simulate()` se generaliza para aceptar y devolver estado de portfolio:

```python
def simulate(df, max_buys, buy_drop_pct, sell_rise_pct, fee_pct,
             use_pool=True, buy_amount=BUY_AMOUNT, interval_minutes=1,
             state=None) -> dict
```

- `state=None`: comportamiento idéntico al actual (portfolio nuevo, compra inicial inmediata).
- `state={cash, purchases, profit_pool, ...}`: continúa un portfolio existente.
- El dict de retorno agrega la clave `state` con el estado final, para encadenar.
- La llamada existente en `optimize.py` no cambia de semántica: mismo resultado que hoy con los mismos argumentos.

`walk_forward.py` importa `simulate` (y el loader de caché, extraído a función `load_bars()`) desde `optimize.py`.

## Partición en semanas

- Semanas calendario ISO (lunes 00:00 como frontera) sobre la columna `timestamp` del df de 1 minuto.
- Semanas sin datos (feriados largos) se omiten. Semanas parciales al inicio/fin del rango se incluyen tal cual, con su cantidad de velas anotada en el reporte.
- Las primeras `train_weeks` semanas son solo warmup: ninguna estrategia del torneo opera en ellas, así todas compiten sobre el mismo período de aplicación.

## Parte 1 — Análisis de estabilidad

Para cada semana `w`, correr el grid completo (100 combos drop×rise × intervalos) con portfolio fresco sobre esa semana aislada. Guardar por semana:

- Combo #1 (pico): drop, rise, intervalo, ROI.
- Combo meseta (ver selección robusta abajo) y su ROI.

Métricas sobre la serie de óptimos semanales:

- **Dispersión:** mediana, desvío estándar y rango intercuartil de drop y rise; distribución de intervalos óptimos (conteo por valor).
- **Persistencia:** correlación de Pearson lag-1 de la serie de drops óptimos y de rises óptimos (semana N vs N-1). Reportar junto al n de semanas y la advertencia de muestra chica (~26 semanas).
- **Regret:** para cada semana N (desde la segunda), `ROI(mejor combo propio de N) − ROI(combo #1 de N-1 aplicado a N)`, ambos con portfolio fresco sobre la semana N aislada. Reportar regret promedio, mediana y peor caso. Regret chico y estable ⇒ hay persistencia explotable (Mundo B); regret grande y errático ⇒ ruido (Mundo A).

## Selección robusta por meseta

Para un grid de resultados de una ventana de entrenamiento, el score de cada combo (drop, rise, intervalo) = promedio del ROI del propio combo y sus vecinos existentes a drop±1pp y rise±1pp (mismo intervalo; en los bordes del grid se promedian solo los vecinos disponibles). Se elige el combo con mejor score. Esto prefiere centros de mesetas sobre picos aislados.

## Parte 2 — Torneo de estrategias

Realismo clave: el bot real **no liquida posiciones al cambiar parámetros**; las arrastra. Por eso cada estrategia se simula con **un único portfolio continuo** a lo largo de todo el período de aplicación, cambiando (drop, rise, intervalo) en cada frontera de semana:

```python
def simulate_adaptive(df_1min, weekly_params: list[(week_df, params)]) -> dict
# encadena simulate(week_df.iloc[::interval], ..., state=prev_state)
```

Nota de submuestreo: el intervalo se aplica por semana (`iloc[::interval]` dentro de cada semana), por lo que la fase del muestreo se reinicia cada lunes. Con intervalo 1 el encadenado es exactamente equivalente a una corrida completa.

Estrategias que compiten (todas operan solo desde la semana `train_weeks` en adelante):

| Estrategia | Parámetros para la semana N |
|---|---|
| **Fija-mediana** | Mediana expansiva (redondeada al grid de 1pp) de los drops y rises pico de las semanas `< N`; intervalo = moda de los intervalos pico pasados. Sin mirar el futuro. |
| **WF-pico** | Combo #1 del grid corrido sobre las `train_weeks` semanas anteriores concatenadas. La idea original del usuario. |
| **WF-meseta** | Combo con mejor score de meseta sobre la misma ventana de entrenamiento. |
| **Oráculo** | Combo #1 de la propia semana N (lookahead deliberado). Techo teórico, no implementable en vivo. |

Reporte del torneo: ROI final, ganancia, cantidad de compras/ventas, fees y posiciones abiertas al cierre de cada estrategia, más la valuación de posiciones abiertas al precio final (misma convención que `optimize.py`).

**Criterio de veredicto (impreso en el log):**
- Si WF-pico o WF-meseta superan a Fija-mediana ⇒ el auto-ajuste se justifica; la distancia al Oráculo indica el margen restante.
- Si no ⇒ recomendar parámetros fijos (los de Fija-mediana final) y no construir el sistema adaptativo en vivo.

## Salidas

- `walkforward_{symbol}_{ts}.log`: reporte legible con tres secciones (estabilidad, torneo, veredicto), mismo estilo visual que `optimize_*.log`.
- `walkforward_{symbol}_{ts}.csv`: una fila por semana con `week_start, week_end, bars, best_drop, best_rise, best_interval, best_roi, plateau_drop, plateau_rise, plateau_interval, plateau_roi`. Este CSV es el inicio del historial de óptimos que el usuario quiere acumular para el futuro bot adaptativo.

## Testing (`test_walk_forward.py`, pytest, datos sintéticos — sin red)

1. **Equivalencia de encadenado:** con parámetros constantes e intervalo 1, `simulate_adaptive()` sobre K semanas == `simulate()` en una sola corrida sobre el df completo (mismo cash, equity, compras, ventas).
2. **Retrocompatibilidad:** `simulate(state=None)` reproduce el resultado actual sobre una serie sintética conocida (zigzag con compras/ventas predecibles calculadas a mano).
3. **Meseta:** sobre un grid artificial con un pico aislado alto y una meseta más baja pero pareja, la selección robusta elige el centro de la meseta.
4. **Partición semanal:** timestamps sintéticos que cruzan un fin de semana y un feriado producen las semanas esperadas.

## Fuera de alcance (YAGNI)

- Modificar `tradebot.py` o el bot en vivo.
- Multi-símbolo en una corrida (se corre el script una vez por símbolo).
- Cadencias distintas de la semanal (la mensual se aproxima con `--train-weeks 4` si hiciera falta).
- Predicción estadística sobre el historial (primero hay que ver si hay señal).

## Criterio de éxito

El script corre sobre el caché existente `cache_TSLA_20260101_20260703_1Min.pkl` y produce un veredicto claro y numérico sobre si el auto-ajuste semanal le gana a parámetros fijos para TSLA en enero–julio 2026.
